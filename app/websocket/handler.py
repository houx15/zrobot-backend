"""WebSocket endpoint handler for AI conversation"""

from fastapi import WebSocket, WebSocketDisconnect, Query, Path
from datetime import datetime, timezone
import json
import asyncio
import base64
import logging
from typing import Optional, Dict
from collections import defaultdict
from dataclasses import dataclass
import math

from app.websocket.manager import connection_manager
from app.websocket.protocol import WsEnvelope, ServerMessage, ConversationState
from app.utils.security import decode_ws_token
from app.redis_client import redis_client
from app.services.agent import ai_agent

logger = logging.getLogger(__name__)

# Streaming ASR session state per conversation
asr_queues: Dict[int, asyncio.Queue] = {}
asr_tasks: Dict[int, asyncio.Task] = {}
interrupt_flags: Dict[int, bool] = defaultdict(bool)
listening_since: Dict[int, Optional[datetime]] = {}  # Track when entered LISTENING state
conv_states: Dict[int, ConversationState] = {}
tts_last_chunk_sent_at: Dict[int, datetime] = {}
current_stream_id: Dict[int, str] = {}

@dataclass
class AudioConfig:
    format: str = "pcm_s16le"
    sample_rate: int = 16000
    channels: int = 1
    bits_per_sample: int = 16
    frame_ms: int = 20


@dataclass
class VADState:
    in_speech: bool = False
    silence_ms: float = 0.0
    noise_floor_db: float = -50.0
    barge_in_frames: int = 0
    last_frame_at: Optional[datetime] = None


audio_configs: Dict[int, AudioConfig] = {}
vad_states: Dict[int, VADState] = {}
IDLE_TIMEOUT_SECONDS = 60
LISTENING_TIMEOUT_SECONDS = 60  # End conversation if no audio for 1 minute in LISTENING state
END_SILENCE_MS = 1500
SPEECH_DB_ABOVE_NOISE = 10.0
BARGE_IN_DB_ABOVE_NOISE = 15.0
BARGE_IN_MIN_MS = 200
PLAYBACK_ECHO_WINDOW_MS = 1200


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
        ServerMessage.state(conversation_id, state.value),
    )
    conv_states[conversation_id] = state
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
) -> WsEnvelope:
    """Handle ping message, return pong"""
    await update_last_active(conversation_id)
    return ServerMessage.pong(conversation_id)


def _get_audio_config(conversation_id: int) -> AudioConfig:
    return audio_configs.get(conversation_id, AudioConfig())


def _get_vad_state(conversation_id: int) -> VADState:
    if conversation_id not in vad_states:
        vad_states[conversation_id] = VADState()
    return vad_states[conversation_id]


def _pcm_rms_db(pcm_bytes: bytes) -> float:
    if len(pcm_bytes) < 2:
        return -100.0
    sample_count = len(pcm_bytes) // 2
    sum_squares = 0
    for i in range(0, sample_count * 2, 2):
        sample = int.from_bytes(pcm_bytes[i : i + 2], "little", signed=True)
        sum_squares += sample * sample
    rms = math.sqrt(sum_squares / max(1, sample_count))
    if rms <= 0:
        return -100.0
    return 20.0 * math.log10(rms / 32768.0)


def _estimate_frame_ms(pcm_bytes: bytes, config: AudioConfig) -> float:
    bytes_per_sample = max(1, config.bits_per_sample // 8)
    bytes_per_sec = config.sample_rate * config.channels * bytes_per_sample
    if bytes_per_sec <= 0:
        return config.frame_ms
    return max(1.0, (len(pcm_bytes) / bytes_per_sec) * 1000.0)


async def handle_text_message(
    conversation_id: int,
    content: str,
) -> None:
    """
    Handle text message from client using segment-based protocol.

    Flow:
    1. State: processing
    2. Call LLM service, parse [S]...[/S][B]...[/B] segments
    3. For each segment:
       a. Send segment_start
       b. Stream TTS audio (audio_chunk) + ai_text_delta driven by TTS sentence events
       c. After audio_end: send board
    4. State: listening
    """
    await update_last_active(conversation_id)

    preview = content.strip().replace("\n", " ")
    if len(preview) > 200:
        preview = preview[:200] + "..."
    logger.info(
        "[ws.text] recv conv_id=%s len=%s text=%s",
        conversation_id,
        len(content),
        preview,
    )

    # Send processing state
    await send_state_change(conversation_id, ConversationState.PROCESSING)

    segment_count = 0
    interrupted = False

    try:
        async for segment in ai_agent.process_text_with_segments(
            conversation_id,
            content,
            with_tts=False,
        ):
            if await check_interrupt(conversation_id):
                logger.info(
                    "Segment response interrupted for conversation %s", conversation_id
                )
                interrupted = True
                break

            if segment_count == 0:
                await send_state_change(conversation_id, ConversationState.SPEAKING)

            logger.info(
                "[ws.segment] start conv_id=%s segment_id=%s speech_len=%s board_len=%s",
                conversation_id,
                segment.segment_id,
                len(segment.speech) if segment.speech else 0,
                len(segment.board) if segment.board else 0,
            )
            await connection_manager.send_message(
                conversation_id,
                ServerMessage.segment_start(
                    conv_id=conversation_id,
                    segment_id=segment.segment_id,
                    index=segment_count,
                ),
            )

            if segment.speech:
                audio_seq = 0
                text_seq = 0
                async for ev in ai_agent.tts.synthesize_stream_events(
                    segment.speech,
                    interrupt_check=lambda: interrupt_flags.get(conversation_id, False),
                ):
                    if await check_interrupt(conversation_id):
                        interrupted = True
                        break

                    if ev.name == "sentence_start":
                        if ev.text:
                            await connection_manager.send_message(
                                conversation_id,
                                ServerMessage.ai_text_delta(
                                    conv_id=conversation_id,
                                    segment_id=segment.segment_id,
                                    seq=text_seq,
                                    delta=ev.text,
                                ),
                            )
                            text_seq += 1
                    elif ev.name == "audio":
                        b64 = base64.b64encode(ev.audio or b"").decode("utf-8")
                        await connection_manager.send_message(
                            conversation_id,
                            ServerMessage.audio_chunk(
                                conv_id=conversation_id,
                                segment_id=segment.segment_id,
                                seq=audio_seq,
                                data_b64=b64,
                                format="pcm_s16le",
                                sample_rate=ai_agent.tts.sample_rate,
                                channels=1,
                                bits_per_sample=16,
                            ),
                        )
                        tts_last_chunk_sent_at[conversation_id] = datetime.now(
                            timezone.utc
                        )
                        audio_seq += 1
                    elif ev.name == "finished":
                        await connection_manager.send_message(
                            conversation_id,
                            ServerMessage.audio_end(
                                conv_id=conversation_id,
                                segment_id=segment.segment_id,
                                last_seq=max(0, audio_seq - 1),
                            ),
                        )
                        break

                if interrupted:
                    break

            if segment.board and not interrupted:
                logger.info(
                    "[ws.board] send conv_id=%s segment_id=%s board_len=%s",
                    conversation_id,
                    segment.segment_id,
                    len(segment.board),
                )
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.board(
                        conv_id=conversation_id,
                        segment_id=segment.segment_id,
                        content=segment.board,
                    ),
                )

            segment_count += 1

        await connection_manager.send_message(
            conversation_id,
            ServerMessage.done(
                conv_id=conversation_id,
                total_segments=segment_count,
                reason="interrupted" if interrupted else "completed",
            ),
        )

    except Exception as e:
        logger.error("Error processing text message: %s", e)
        await connection_manager.send_message(
            conversation_id,
            ServerMessage.error(conversation_id, 5001, f"处理消息时出错: {str(e)}"),
        )

    await clear_interrupt(conversation_id)
    await send_state_change(conversation_id, ConversationState.LISTENING)


async def handle_client_hello(conversation_id: int, payload: dict) -> None:
    await update_last_active(conversation_id)
    audio = payload.get("audio") or {}
    audio_configs[conversation_id] = AudioConfig(
        format=audio.get("format", "pcm_s16le"),
        sample_rate=int(audio.get("sample_rate", 16000)),
        channels=int(audio.get("channels", 1)),
        bits_per_sample=int(audio.get("bits_per_sample", 16)),
        frame_ms=int(audio.get("frame_ms", 20)),
    )


async def handle_mic_start(conversation_id: int, stream_id: str) -> None:
    await update_last_active(conversation_id)
    current_stream_id[conversation_id] = stream_id
    vad_states[conversation_id] = VADState()
    await ensure_asr_session(conversation_id, stream_id)


async def handle_user_audio_chunk(
    conversation_id: int,
    stream_id: str,
    seq: int,
    audio_b64: str,
    vad_hint: Optional[str] = None,
) -> None:
    await update_last_active(conversation_id)

    try:
        pcm_bytes = base64.b64decode(audio_b64)
        config = _get_audio_config(conversation_id)
        frame_ms = _estimate_frame_ms(pcm_bytes, config)
        rms_db = _pcm_rms_db(pcm_bytes)
        vad = _get_vad_state(conversation_id)

        if conv_states.get(conversation_id) == ConversationState.LISTENING:
            listening_since[conversation_id] = datetime.now(timezone.utc)

        # Update noise floor slowly when near-silence
        if rms_db < vad.noise_floor_db + 3:
            vad.noise_floor_db = 0.98 * vad.noise_floor_db + 0.02 * rms_db
        else:
            vad.noise_floor_db = 0.995 * vad.noise_floor_db + 0.005 * rms_db

        now = datetime.now(timezone.utc)
        speaking_state = conv_states.get(conversation_id, ConversationState.IDLE)
        last_tts = tts_last_chunk_sent_at.get(conversation_id)
        recent_tts = (
            last_tts is not None
            and (now - last_tts).total_seconds() * 1000 < PLAYBACK_ECHO_WINDOW_MS
        )
        speaking_risk = speaking_state == ConversationState.SPEAKING or recent_tts

        barge_in_triggered = False
        if speaking_risk:
            if rms_db > vad.noise_floor_db + BARGE_IN_DB_ABOVE_NOISE:
                vad.barge_in_frames += 1
            else:
                vad.barge_in_frames = 0
            if vad.barge_in_frames * frame_ms >= BARGE_IN_MIN_MS:
                barge_in_triggered = True
        else:
            vad.barge_in_frames = 0

        feed_asr = (not speaking_risk) or barge_in_triggered

        if barge_in_triggered and speaking_state == ConversationState.SPEAKING:
            logger.info("Barge-in detected conv_id=%s", conversation_id)
            await handle_interrupt(conversation_id, reason="barge_in")

        if not feed_asr:
            return

        queue = await ensure_asr_session(conversation_id, stream_id)
        await queue.put(pcm_bytes)

        # VAD endpointing (backend authoritative)
        if rms_db > vad.noise_floor_db + SPEECH_DB_ABOVE_NOISE:
            vad.in_speech = True
            vad.silence_ms = 0.0
        else:
            if vad.in_speech:
                vad.silence_ms += frame_ms
                if vad.silence_ms >= END_SILENCE_MS:
                    vad.in_speech = False
                    vad.silence_ms = 0.0
                    await queue.put(None)

    except Exception as e:
        logger.error("Error processing audio message: %s", e)
        await stop_asr_session(conversation_id)
        await connection_manager.send_message(
            conversation_id,
            ServerMessage.error(conversation_id, 5001, f"音频处理出错: {str(e)}"),
        )
        await send_state_change(conversation_id, ConversationState.LISTENING)


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


async def handle_interrupt(conversation_id: int, reason: str = "user_tap") -> None:
    """
    Handle interrupt signal from client.

    This will:
    1. Set interrupt flag in Redis
    2. Clear audio buffer
    3. Cancel any ongoing TTS playback
    4. Cancel any pending LLM generation
    5. Reset state to idle
    """
    logger.info("Interrupt received for conversation %s reason=%s", conversation_id, reason)

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


async def handle_mic_end(conversation_id: int, stream_id: str, last_seq: int) -> None:
    await update_last_active(conversation_id)
    if current_stream_id.get(conversation_id) != stream_id:
        return
    queue = asr_queues.get(conversation_id)
    if queue:
        await queue.put(None)


async def check_interrupt(conversation_id: int) -> bool:
    """Check if there's an active interrupt signal"""
    flag = await redis_client.get(f"conv:interrupt:{conversation_id}")
    return flag == "1"


async def clear_interrupt(conversation_id: int) -> None:
    """Clear interrupt flag"""
    await redis_client.delete(f"conv:interrupt:{conversation_id}")
    interrupt_flags[conversation_id] = False


async def ensure_asr_session(conversation_id: int, stream_id: str) -> asyncio.Queue:
    """Ensure an ASR streaming session exists for this conversation."""
    task = asr_tasks.get(conversation_id)
    if (
        task
        and not task.done()
        and current_stream_id.get(conversation_id) == stream_id
    ):
        return asr_queues[conversation_id]

    if task and not task.done():
        await stop_asr_session(conversation_id)

    current_stream_id[conversation_id] = stream_id
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
        last_partial = ""
        try:
            async for result in ai_agent.asr.transcribe_stream(
                audio_generator(),
                interrupt_check=lambda: interrupt_flags.get(conversation_id, False),
            ):
                text = (result.text or "").strip()
                if not text:
                    continue
                if result.is_final:
                    await connection_manager.send_message(
                        conversation_id,
                        ServerMessage.asr_final(conversation_id, stream_id, text),
                    )
                    final_text = text
                    break
                last_partial = text
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.asr_partial(conversation_id, stream_id, text),
                )

            if not final_text and last_partial:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.asr_final(conversation_id, stream_id, last_partial),
                )
                final_text = last_partial

            if final_text and not await check_interrupt(conversation_id):
                await handle_text_message(conversation_id, final_text)
            else:
                await send_state_change(conversation_id, ConversationState.LISTENING)
        except Exception as e:
            logger.error("ASR streaming error: %s", e)
            await connection_manager.send_message(
                conversation_id,
                ServerMessage.error(conversation_id, 5001, f"语音识别出错: {str(e)}"),
            )
            await send_state_change(conversation_id, ConversationState.LISTENING)
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
    current_stream_id.pop(conversation_id, None)


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

    Message Protocol (v2):
    - Client sends JSON envelope: {type, conv_id, msg_id, ts_ms, payload}
    - Server sends JSON envelope: {type, conv_id, msg_id, ts_ms, payload}
    """
    # Verify token and get user_id
    user_id = await verify_connection(websocket, conversation_id, token)
    if user_id is None:
        return

    # Accept connection and register
    await connection_manager.connect(websocket, conversation_id, user_id)

    # Send initial idle state
    await send_state_change(conversation_id, ConversationState.IDLE)
    # Kick off initial assistant response if configured
    try:
        initial_msg = await redis_client.hget(f"conv:session:{conversation_id}", "initial_user_message")
        if initial_msg:
            await redis_client.hdel(f"conv:session:{conversation_id}", "initial_user_message")
            asyncio.create_task(handle_text_message(conversation_id, initial_msg))
    except Exception as e:
        logger.error(f"Failed to send initial message for conversation {conversation_id}: {e}")

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
                    ServerMessage.error(conversation_id, 1001, "Invalid JSON format"),
                )
                continue

            # Parse message
            try:
                message = WsEnvelope(**data)
            except Exception as e:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.error(conversation_id, 1001, f"Invalid message format: {str(e)}"),
                )
                continue

            if message.conv_id != conversation_id:
                await connection_manager.send_message(
                    conversation_id,
                    ServerMessage.error(conversation_id, 1001, "Conversation ID mismatch"),
                )
                continue

            payload = message.payload or {}

            # Route message by type
            if message.type == "ping":
                pong = await handle_ping(conversation_id)
                await connection_manager.send_message(conversation_id, pong)

            elif message.type == "client_hello":
                await handle_client_hello(conversation_id, payload)

            elif message.type == "mic_start":
                stream_id = payload.get("stream_id")
                if stream_id:
                    await handle_mic_start(conversation_id, stream_id)
                else:
                    await connection_manager.send_message(
                        conversation_id,
                        ServerMessage.error(conversation_id, 1001, "Missing stream_id"),
                    )

            elif message.type == "user_audio_chunk":
                stream_id = payload.get("stream_id")
                audio_b64 = payload.get("data_b64")
                seq = payload.get("seq")
                if stream_id and audio_b64 is not None and seq is not None:
                    asyncio.create_task(
                        handle_user_audio_chunk(
                            conversation_id,
                            stream_id,
                            int(seq),
                            audio_b64,
                            payload.get("vad_hint"),
                        )
                    )
                else:
                    await connection_manager.send_message(
                        conversation_id,
                        ServerMessage.error(conversation_id, 1001, "Missing audio chunk data"),
                    )

            elif message.type == "mic_end":
                stream_id = payload.get("stream_id")
                last_seq = payload.get("last_seq", 0)
                if stream_id:
                    asyncio.create_task(
                        handle_mic_end(conversation_id, stream_id, int(last_seq))
                    )
                else:
                    await connection_manager.send_message(
                        conversation_id,
                        ServerMessage.error(conversation_id, 1001, "Missing stream_id"),
                    )

            elif message.type == "image":
                image_url = payload.get("image_url")
                if image_url:
                    await handle_image_message(conversation_id, image_url)
                else:
                    await connection_manager.send_message(
                        conversation_id,
                        ServerMessage.error(conversation_id, 1001, "Missing image URL"),
                    )

            elif message.type == "interrupt":
                await handle_interrupt(conversation_id)
            else:
                logger.warning(
                    "Unknown ws message type conv_id=%s type=%s",
                    conversation_id,
                    message.type,
                )

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
                ServerMessage.error(conversation_id, 5001, f"Internal error: {str(e)}"),
            )
        except Exception:
            pass
    finally:
        # Clean up connection, ASR session, and listening state
        await stop_asr_session(conversation_id)
        listening_since.pop(conversation_id, None)
        audio_configs.pop(conversation_id, None)
        vad_states.pop(conversation_id, None)
        conv_states.pop(conversation_id, None)
        tts_last_chunk_sent_at.pop(conversation_id, None)
        await connection_manager.disconnect(conversation_id)
