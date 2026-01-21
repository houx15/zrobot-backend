"""WebSocket endpoint handler for AI conversation"""

from fastapi import WebSocket, WebSocketDisconnect, Query, Path
from datetime import datetime, timezone
import json
import asyncio
import base64
import logging
from typing import Optional, Dict
from collections import defaultdict

from app.websocket.manager import connection_manager
from app.websocket.protocol import (
    ClientMessage,
    ClientMessageType,
    ServerMessage,
)
from app.utils.security import decode_ws_token
from app.redis_client import redis_client
from app.services.agent import ai_agent

logger = logging.getLogger(__name__)

# Audio buffer for streaming ASR (conversation_id -> list of audio chunks)
audio_buffers: Dict[int, list] = defaultdict(list)


async def verify_connection(
    websocket: WebSocket,
    conversation_id: int,
    token: str,
) -> Optional[int]:
    """
    Verify WebSocket connection token.

    Returns user_id if valid, None otherwise.
    """
    # Decode token
    payload = decode_ws_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Invalid token")
        return None

    # Verify conversation_id matches
    if payload.get("conversation_id") != conversation_id:
        await websocket.close(code=4002, reason="Token does not match conversation")
        return None

    # Check if conversation exists in Redis
    session = await redis_client.hgetall(f"conv:session:{conversation_id}")
    if not session:
        await websocket.close(code=4003, reason="Conversation not found or expired")
        return None

    # Verify user_id matches
    session_user_id = int(session.get("user_id", 0))
    token_user_id = payload.get("user_id")
    if session_user_id != token_user_id:
        await websocket.close(code=4004, reason="User mismatch")
        return None

    # Check conversation status
    if session.get("status") != "active":
        await websocket.close(code=4005, reason="Conversation is not active")
        return None

    return token_user_id


async def update_last_active(conversation_id: int) -> None:
    """Update last active timestamp for conversation"""
    await redis_client.hset(
        f"conv:session:{conversation_id}",
        "last_active_at",
        datetime.now(timezone.utc).isoformat(),
    )


async def store_message(
    conversation_id: int,
    role: str,
    msg_type: str,
    content: str,
) -> None:
    """Store a message in Redis conversation history"""
    message = {
        "role": role,
        "type": msg_type,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.rpush(
        f"conv:messages:{conversation_id}",
        json.dumps(message, ensure_ascii=False),
    )


async def handle_ping(
    conversation_id: int,
) -> ServerMessage:
    """Handle ping message, return pong"""
    await update_last_active(conversation_id)
    return ServerMessage.pong()


async def handle_text_message(
    conversation_id: int,
    content: str,
) -> None:
    """
    Handle text message from client.

    Flow:
    1. Store the message
    2. Call LLM service for response
    3. Stream TTS audio response back
    """
    await update_last_active(conversation_id)

    # Send reply start
    await connection_manager.send_message(
        conversation_id,
        ServerMessage.reply_start(),
    )

    try:
        # Process through AI agent (LLM + TTS)
        async for response in ai_agent.process_text_input(
            conversation_id,
            content,
            on_reply_text=None,  # We'll handle text streaming below
            on_reply_audio=None,
        ):
            # Check for interrupt
            if await check_interrupt(conversation_id):
                logger.info(f"Text response interrupted for conversation {conversation_id}")
                break

            # Send text chunk
            if response.text:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.reply_text(response.text, is_final=response.is_final),
                )

            # Send audio chunk
            if response.audio_base64:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.audio(response.audio_base64, is_final=response.is_final),
                )

            if response.is_final:
                break

    except Exception as e:
        logger.error(f"Error processing text message: {e}")
        await connection_manager.send_message(
            conversation_id,
            ServerMessage.error(5001, f"处理消息时出错: {str(e)}"),
        )

    # Send reply end
    await connection_manager.send_message(
        conversation_id,
        ServerMessage.reply_end(),
    )


async def handle_audio_message(
    conversation_id: int,
    audio_data: str,
    is_final: bool,
) -> None:
    """
    Handle audio message from client.

    Flow:
    1. Buffer audio chunks
    2. When final, send to ASR for transcription
    3. Send transcription to LLM
    4. Stream TTS audio response back
    """
    await update_last_active(conversation_id)

    try:
        # Decode base64 audio data
        audio_bytes = base64.b64decode(audio_data)

        # Add to buffer
        audio_buffers[conversation_id].append(audio_bytes)

        if is_final:
            # Combine all buffered audio
            full_audio = b"".join(audio_buffers[conversation_id])
            audio_buffers[conversation_id].clear()

            if not full_audio:
                return

            # Transcribe audio
            transcribed_text = await ai_agent.asr.transcribe(full_audio)

            if not transcribed_text:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.transcript("(无法识别语音)", is_final=True),
                )
                return

            # Send transcript
            await connection_manager.send_message(
                conversation_id,
                ServerMessage.transcript(transcribed_text, is_final=True),
            )

            # Process transcribed text through LLM + TTS
            await handle_text_message(conversation_id, transcribed_text)

    except Exception as e:
        logger.error(f"Error processing audio message: {e}")
        # Clear buffer on error
        audio_buffers[conversation_id].clear()
        await connection_manager.send_message(
            conversation_id,
            ServerMessage.error(5001, f"音频处理出错: {str(e)}"),
        )


async def handle_image_message(
    conversation_id: int,
    image_url: str,
) -> None:
    """
    Handle image message from client.

    Store the image URL in conversation context for LLM reference.
    """
    await update_last_active(conversation_id)

    # Store image in context vars
    await redis_client.hset(
        f"conv:vars:{conversation_id}",
        "last_image_url",
        image_url,
    )

    # Store as message
    await store_message(conversation_id, "user", "image", image_url)


async def handle_interrupt(conversation_id: int) -> None:
    """
    Handle interrupt signal from client.

    This will:
    1. Set interrupt flag in Redis
    2. Clear audio buffer
    3. Cancel any ongoing TTS playback
    4. Cancel any pending LLM generation
    """
    logger.info(f"Interrupt received for conversation {conversation_id}")

    # Set interrupt flag
    await redis_client.set(
        f"conv:interrupt:{conversation_id}",
        "1",
        ex=10,  # Expire after 10 seconds
    )

    # Clear audio buffer
    audio_buffers[conversation_id].clear()

    # Update session state
    await redis_client.hset(
        f"conv:session:{conversation_id}",
        "tts_playing",
        "false",
    )

    # Send reply end to signal interruption complete
    await connection_manager.send_message(
        conversation_id,
        ServerMessage.reply_end(),
    )


async def check_interrupt(conversation_id: int) -> bool:
    """Check if there's an active interrupt signal"""
    flag = await redis_client.get(f"conv:interrupt:{conversation_id}")
    return flag == "1"


async def clear_interrupt(conversation_id: int) -> None:
    """Clear interrupt flag"""
    await redis_client.delete(f"conv:interrupt:{conversation_id}")


async def websocket_endpoint(
    websocket: WebSocket,
    conversation_id: int = Path(..., description="Conversation ID"),
    token: str = Query(..., description="WebSocket authentication token"),
):
    """
    WebSocket endpoint for AI conversation.

    URL: /ws/conversation/{conversation_id}?token={token}

    Message Protocol:
    - Client sends JSON: {"type": "audio|text|image|interrupt|ping", "data": {...}}
    - Server sends JSON: {"type": "audio|transcript|reply_text|reply_start|reply_end|error|pong", "data": {...}}
    """
    # Verify token and get user_id
    user_id = await verify_connection(websocket, conversation_id, token)
    if user_id is None:
        return

    # Accept connection and register
    await connection_manager.connect(websocket, conversation_id, user_id)

    # Send connected confirmation
    await connection_manager.send_message(
        conversation_id,
        ServerMessage.connected(conversation_id),
    )

    try:
        while True:
            # Receive message
            try:
                data = await websocket.receive_json()
            except json.JSONDecodeError:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.error(1001, "Invalid JSON format"),
                )
                continue

            # Parse message
            try:
                message = ClientMessage(**data)
            except Exception as e:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.error(1001, f"Invalid message format: {str(e)}"),
                )
                continue

            # Route message by type
            if message.type == ClientMessageType.PING:
                pong = await handle_ping(conversation_id)
                await connection_manager.send_message(conversation_id, pong)

            elif message.type == ClientMessageType.TEXT:
                text_data = message.get_text_data()
                if text_data:
                    # Run in background to not block other messages
                    asyncio.create_task(
                        handle_text_message(conversation_id, text_data.content)
                    )
                else:
                    await connection_manager.send_message(
                        conversation_id,
                        ServerMessage.error(1001, "Missing text content"),
                    )

            elif message.type == ClientMessageType.AUDIO:
                audio_data = message.get_audio_data()
                if audio_data:
                    asyncio.create_task(
                        handle_audio_message(
                            conversation_id,
                            audio_data.audio,
                            audio_data.is_final,
                        )
                    )
                else:
                    await connection_manager.send_message(
                        conversation_id,
                        ServerMessage.error(1001, "Missing audio data"),
                    )

            elif message.type == ClientMessageType.IMAGE:
                image_data = message.get_image_data()
                if image_data:
                    await handle_image_message(conversation_id, image_data.image_url)
                else:
                    await connection_manager.send_message(
                        conversation_id,
                        ServerMessage.error(1001, "Missing image URL"),
                    )

            elif message.type == ClientMessageType.INTERRUPT:
                await handle_interrupt(conversation_id)

    except WebSocketDisconnect:
        # Client disconnected
        logger.info(f"WebSocket disconnected for conversation {conversation_id}")
    except Exception as e:
        # Unexpected error
        logger.error(f"WebSocket error for conversation {conversation_id}: {e}")
        try:
            await connection_manager.send_message(
                conversation_id,
                ServerMessage.error(5001, f"Internal error: {str(e)}"),
            )
        except Exception:
            pass
    finally:
        # Clean up connection and audio buffer
        audio_buffers.pop(conversation_id, None)
        await connection_manager.disconnect(conversation_id)
