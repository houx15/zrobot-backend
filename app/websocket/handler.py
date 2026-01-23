"""WebSocket endpoint handler for AI conversation"""

from fastapi import WebSocket, WebSocketDisconnect, Query, Path
from datetime import datetime, timezone, timedelta
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
    ConversationState,
)
from app.utils.security import decode_ws_token
from app.redis_client import redis_client
from app.services.agent import ai_agent

logger = logging.getLogger(__name__)

# Streaming ASR session state per conversation
asr_queues: Dict[int, asyncio.Queue] = {}
asr_tasks: Dict[int, asyncio.Task] = {}
interrupt_flags: Dict[int, bool] = defaultdict(bool)
listening_since: Dict[int, Optional[datetime]] = {}  # Track when entered LISTENING state
forced_end_until: Dict[int, datetime] = {}
IDLE_TIMEOUT_SECONDS = 60
LISTENING_TIMEOUT_SECONDS = 60  # End conversation if no audio for 1 minute in LISTENING state
PARTIAL_STABLE_SECONDS = 1.5
FORCED_END_GRACE_SECONDS = 2.0


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
        logger.warning(
            "[ws.verify] invalid token conv_id=%s token_prefix=%s",
            conversation_id,
            token[:12],
        )
        await websocket.close(code=4001, reason="Invalid token")
        return None

    # Verify conversation_id matches
    if payload.get("conversation_id") != conversation_id:
        logger.warning(
            "[ws.verify] token conv_id mismatch url=%s token=%s user_id=%s",
            conversation_id,
            payload.get("conversation_id"),
            payload.get("user_id"),
        )
        await websocket.close(code=4002, reason="Token does not match conversation")
        return None

    # Check if conversation exists in Redis
    session = await redis_client.hgetall(f"conv:session:{conversation_id}")
    if not session:
        logger.warning(
            "[ws.verify] session missing conv_id=%s user_id=%s",
            conversation_id,
            payload.get("user_id"),
        )
        await websocket.close(code=4003, reason="Conversation not found or expired")
        return None

    # Verify user_id matches
    session_user_id = int(session.get("user_id", 0))
    token_user_id = payload.get("user_id")
    if session_user_id != token_user_id:
        logger.warning(
            "[ws.verify] user mismatch conv_id=%s session_user_id=%s token_user_id=%s session=%s",
            conversation_id,
            session_user_id,
            token_user_id,
            session,
        )
        await websocket.close(code=4004, reason="User mismatch")
        return None

    # Check conversation status
    if session.get("status") != "active":
        logger.warning(
            "[ws.verify] session not active conv_id=%s status=%s session=%s",
            conversation_id,
            session.get("status"),
            session,
        )
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


async def send_state_change(conversation_id: int, state: ConversationState) -> None:
    """Send state change message to client"""
    await connection_manager.send_message(
        conversation_id,
        ServerMessage.state(state.value),
    )
    # Also update Redis session state
    await redis_client.hset(
        f"conv:session:{conversation_id}",
        "state",
        state.value,
    )
    # Track when we enter LISTENING state for timeout detection
    if state == ConversationState.LISTENING:
        listening_since[conversation_id] = datetime.now(timezone.utc)
    else:
        listening_since.pop(conversation_id, None)


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
    Handle text message from client using segment-based protocol.

    Flow:
    1. State: processing
    2. Call LLM service, parse [S]...[/S][B]...[/B] segments
    3. For each segment: generate TTS, send segment + audio, state: speaking
    4. State: idle
    """
    await update_last_active(conversation_id)

    # Send processing state
    await send_state_change(conversation_id, ConversationState.PROCESSING)

    segment_count = 0

    try:
        # Process through AI agent with segment-based pipeline
        async for segment in ai_agent.process_text_with_segments(
            conversation_id,
            content,
        ):
            # Check for interrupt
            if await check_interrupt(conversation_id):
                logger.info(f"Segment response interrupted for conversation {conversation_id}")
                break

            # Update state to speaking when sending first segment
            if segment_count == 0:
                await send_state_change(conversation_id, ConversationState.SPEAKING)

            # Send segment message
            await connection_manager.send_message(
                conversation_id,
                ServerMessage.segment(
                    segment_id=segment.segment_id,
                    speech=segment.speech,
                    board=segment.board,
                ),
            )

            if segment.audio_base64:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.audio(
                        audio_data=segment.audio_base64,
                        segment_id=segment.segment_id,
                        is_final=True,
                    ),
                )

            segment_count += 1

        # Send done marker
        await connection_manager.send_message(
            conversation_id,
            ServerMessage.done(total_segments=segment_count),
        )

    except Exception as e:
        logger.error(f"Error processing text message: {e}")
        await connection_manager.send_message(
            conversation_id,
            ServerMessage.error(5001, f"处理消息时出错: {str(e)}"),
        )

    # Send idle state
    await send_state_change(conversation_id, ConversationState.IDLE)


async def handle_audio_message(
    conversation_id: int,
    audio_data: str,
    sequence: int,
) -> None:
    """
    Handle audio message from client using segment-based protocol.

    Flow:
    1. State: listening (when receiving first chunk)
    2. Stream audio chunks to ASR
    3. Emit partial transcripts while speaking
    4. On end_speaking, finalize ASR and send to LLM
    5. State: idle
    """
    await update_last_active(conversation_id)

    try:
        until = forced_end_until.get(conversation_id)
        if until and datetime.now(timezone.utc) < until:
            return
        # Decode base64 audio data
        pcm_bytes = base64.b64decode(audio_data)
        queue = await ensure_asr_session(conversation_id)
        # Sequence is accepted for client ordering but ASR streaming uses arrival order.
        await queue.put(pcm_bytes)

    except Exception as e:
        logger.error(f"Error processing audio message: {e}")
        await stop_asr_session(conversation_id)
        await connection_manager.send_message(
            conversation_id,
            ServerMessage.error(5001, f"音频处理出错: {str(e)}"),
        )
        await send_state_change(conversation_id, ConversationState.IDLE)


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
        "context_image_url",
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
    5. Reset state to idle
    """
    logger.info(f"Interrupt received for conversation {conversation_id}")

    # Set interrupt flag
    await redis_client.set(
        f"conv:interrupt:{conversation_id}",
        "1",
        ex=10,  # Expire after 10 seconds
    )
    interrupt_flags[conversation_id] = True

    await stop_asr_session(conversation_id)

    # Update session state
    await redis_client.hset(
        f"conv:session:{conversation_id}",
        "tts_playing",
        "false",
    )

    # Reset to listening state so the user can continue speaking
    await send_state_change(conversation_id, ConversationState.LISTENING)


async def handle_end_speaking(conversation_id: int) -> None:
    """Handle end of speaking signal, finalize ASR stream."""
    await update_last_active(conversation_id)

    queue = asr_queues.get(conversation_id)
    if not queue:
        await send_state_change(conversation_id, ConversationState.IDLE)
        return
    await queue.put(None)


async def check_interrupt(conversation_id: int) -> bool:
    """Check if there's an active interrupt signal"""
    flag = await redis_client.get(f"conv:interrupt:{conversation_id}")
    return flag == "1"


async def clear_interrupt(conversation_id: int) -> None:
    """Clear interrupt flag"""
    await redis_client.delete(f"conv:interrupt:{conversation_id}")
    interrupt_flags[conversation_id] = False


async def ensure_asr_session(conversation_id: int) -> asyncio.Queue:
    """Ensure an ASR streaming session exists for this conversation."""
    task = asr_tasks.get(conversation_id)
    if task and not task.done():
        return asr_queues[conversation_id]

    await clear_interrupt(conversation_id)
    queue: asyncio.Queue = asyncio.Queue()
    asr_queues[conversation_id] = queue
    interrupt_flags[conversation_id] = False
    await send_state_change(conversation_id, ConversationState.LISTENING)

    async def audio_generator():
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk

    async def asr_worker():
        final_text = ""
        prev_text = ""
        last_change_at = datetime.now(timezone.utc)
        try:
            async for result in ai_agent.asr.transcribe_stream(
                audio_generator(),
                interrupt_check=lambda: interrupt_flags.get(conversation_id, False),
            ):
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.transcript(result.text, is_final=result.is_final),
                )
                if result.is_final:
                    final_text = result.text
                    break
                # If partial transcript is stable for a while, treat as end of speech.
                now = datetime.now(timezone.utc)
                if result.text != prev_text:
                    prev_text = result.text
                    last_change_at = now
                else:
                    elapsed = (now - last_change_at).total_seconds()
                    if prev_text and elapsed >= PARTIAL_STABLE_SECONDS:
                        final_text = prev_text
                        await connection_manager.send_message(
                            conversation_id,
                            ServerMessage.transcript(final_text, is_final=True),
                        )
                        forced_end_until[conversation_id] = now + timedelta(seconds=FORCED_END_GRACE_SECONDS)
                        queue.put_nowait(None)
                        break

            if final_text:
                await handle_text_message(conversation_id, final_text)
            else:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.transcript("(无法识别语音)", is_final=True),
                )
                await send_state_change(conversation_id, ConversationState.IDLE)
        except Exception as e:
            logger.error(f"ASR streaming error: {e}")
            await connection_manager.send_message(
                conversation_id,
                ServerMessage.error(5001, f"语音识别出错: {str(e)}"),
            )
            await send_state_change(conversation_id, ConversationState.IDLE)
        finally:
            current_task = asyncio.current_task()
            if asr_tasks.get(conversation_id) is current_task:
                asr_tasks.pop(conversation_id, None)
                asr_queues.pop(conversation_id, None)

    asr_tasks[conversation_id] = asyncio.create_task(asr_worker())
    return queue


async def stop_asr_session(conversation_id: int) -> None:
    """Stop any active ASR streaming session."""
    queue = asr_queues.get(conversation_id)
    if queue:
        await queue.put(None)
    task = asr_tasks.get(conversation_id)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def check_listening_timeout(conversation_id: int) -> bool:
    """
    Check if we've been in LISTENING state for too long without audio.

    Returns True if timeout exceeded, False otherwise.
    """
    started = listening_since.get(conversation_id)
    if started is None:
        return False

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return elapsed > LISTENING_TIMEOUT_SECONDS


async def websocket_endpoint(
    websocket: WebSocket,
    conversation_id: int = Path(..., description="Conversation ID"),
    token: str = Query(..., description="WebSocket authentication token"),
):
    """
    WebSocket endpoint for AI conversation.

    URL: /ws/conversation/{conversation_id}?token={token}

    Message Protocol:
    - Client sends JSON: {"type": "audio|end_speaking|image|interrupt|ping", "data": {...}}
    - Server sends JSON: {"type": "state|transcript|segment|audio|done|error|pong", "data": {...}}
    """
    # Verify token and get user_id
    user_id = await verify_connection(websocket, conversation_id, token)
    if user_id is None:
        return

    # Accept connection and register
    await connection_manager.connect(websocket, conversation_id, user_id)

    # Send initial idle state
    await send_state_change(conversation_id, ConversationState.IDLE)

    try:
        while True:
            # Receive message
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                await websocket.close(code=1000, reason="Idle timeout")
                return
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

            elif message.type == ClientMessageType.AUDIO:
                audio_data = message.get_audio_data()
                if audio_data:
                    asyncio.create_task(
                        handle_audio_message(
                            conversation_id,
                            audio_data.audio,
                            audio_data.sequence,
                        )
                    )
                else:
                    await connection_manager.send_message(
                        conversation_id,
                        ServerMessage.error(1001, "Missing audio data"),
                    )

            elif message.type == ClientMessageType.END_SPEAKING:
                asyncio.create_task(handle_end_speaking(conversation_id))

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

            # Check listening timeout (user interrupted but didn't speak for 1 minute)
            if check_listening_timeout(conversation_id):
                logger.info(f"Listening timeout for conversation {conversation_id}")
                await websocket.close(code=1000, reason="Listening timeout")
                return

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
        # Clean up connection, ASR session, and listening state
        await stop_asr_session(conversation_id)
        listening_since.pop(conversation_id, None)
        await connection_manager.disconnect(conversation_id)
