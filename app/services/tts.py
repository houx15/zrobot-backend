"""
ByteDance/Volcano TTS (Text-to-Speech) Service

Based on Volcano Engine v3 WebSocket API for streaming speech synthesis.
"""

import asyncio
import json
import uuid
import base64
import struct
import gzip
import logging
from typing import AsyncGenerator, Optional, Callable
from dataclasses import dataclass
from enum import IntEnum

import websockets

from app.config import settings

logger = logging.getLogger(__name__)


def _detect_audio_format(data: bytes) -> Optional[str]:
    if len(data) < 4:
        return None
    if data.startswith(b"ID3") or data[:2] == b"\xFF\xFB" or data[:2] == b"\xFF\xF3" or data[:2] == b"\xFF\xF2":
        return "mp3"
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"RIFF"):
        return "wav"
    return None


def _wrap_wav(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_data


class MsgType(IntEnum):
    """TTS message types"""
    FullClientRequest = 0b0001
    AudioOnlyClient = 0b0010
    FullServerResponse = 0b1001
    AudioOnlyServer = 0b1011
    ErrorResponse = 0b1111


class EventType(IntEnum):
    """TTS event types"""
    SessionStarted = 1
    TaskStarted = 2
    SentenceStart = 3
    SentenceEnd = 4
    TaskFinished = 5
    SessionFinished = 6


class SerializationType(IntEnum):
    """Serialization types"""
    NoSerialization = 0b0000
    JSON = 0b0001


class CompressionType(IntEnum):
    """Compression types"""
    NoCompression = 0b0000
    GZIP = 0b0001


@dataclass
class TTSMessage:
    """TTS message structure"""
    type: MsgType
    event: int = 0
    payload: Optional[bytes] = None
    error_code: int = 0
    error_message: str = ""
    serialization: int = 0
    compression: int = 0


def build_tts_header(
    msg_type: MsgType,
    serialization: SerializationType = SerializationType.JSON,
    compression: CompressionType = CompressionType.GZIP,
) -> bytes:
    """Build TTS protocol header"""
    header = bytearray()
    # byte 0: version (4 bits) + header size (4 bits)
    header.append(0x11)  # version 1, header size 1 (4 bytes)
    # byte 1: message type (4 bits) + flags (4 bits)
    header.append((msg_type << 4) | 0x01)  # with sequence
    # byte 2: serialization (4 bits) + compression (4 bits)
    header.append((serialization << 4) | compression)
    # byte 3: reserved
    header.append(0x00)
    return bytes(header)


async def full_client_request(websocket, payload: bytes):
    """Send a full client request"""
    header = build_tts_header(MsgType.FullClientRequest)

    # Compress payload
    compressed = gzip.compress(payload)

    # Build message: header + sequence (4 bytes) + payload size (4 bytes) + payload
    msg = bytearray()
    msg.extend(header)
    msg.extend(struct.pack(">i", 1))  # sequence
    msg.extend(struct.pack(">I", len(compressed)))
    msg.extend(compressed)

    await websocket.send(bytes(msg))


def parse_tts_response(data: bytes) -> TTSMessage:
    """Parse TTS response message"""
    msg = TTSMessage(type=MsgType.FullServerResponse)

    if len(data) < 4:
        return msg

    # Parse header
    header_size = data[0] & 0x0F
    msg_type = (data[1] >> 4) & 0x0F
    flags = data[1] & 0x0F
    serialization = (data[2] >> 4) & 0x0F
    compression = data[2] & 0x0F

    msg.type = MsgType(msg_type)
    msg.serialization = serialization
    msg.compression = compression

    # Skip header
    offset = header_size * 4

    # Parse based on flags
    if flags & 0x01:  # has sequence
        offset += 4  # skip sequence

    if flags & 0x04:  # has event
        if offset + 4 <= len(data):
            msg.event = struct.unpack(">i", data[offset:offset+4])[0]
            offset += 4

    # Parse payload based on message type
    if msg.type == MsgType.FullServerResponse:
        if offset + 4 <= len(data):
            payload_size = struct.unpack(">I", data[offset:offset+4])[0]
            offset += 4
            if offset + payload_size <= len(data):
                payload = data[offset:offset+payload_size]
                if compression == CompressionType.GZIP:
                    try:
                        payload = gzip.decompress(payload)
                    except Exception:
                        pass
                msg.payload = payload

    elif msg.type == MsgType.AudioOnlyServer:
        if offset + 4 <= len(data):
            payload_size = struct.unpack(">I", data[offset:offset+4])[0]
            offset += 4
            if offset + payload_size <= len(data):
                msg.payload = data[offset:offset+payload_size]

    elif msg.type == MsgType.ErrorResponse:
        if offset + 4 <= len(data):
            msg.error_code = struct.unpack(">i", data[offset:offset+4])[0]
            offset += 4
        if offset + 4 <= len(data):
            error_size = struct.unpack(">I", data[offset:offset+4])[0]
            offset += 4
            if offset + error_size <= len(data):
                error_data = data[offset:offset+error_size]
                if compression == CompressionType.GZIP:
                    try:
                        error_data = gzip.decompress(error_data)
                    except Exception:
                        pass
                try:
                    msg.error_message = error_data.decode("utf-8")
                except Exception:
                    pass

    return msg


def _log_server_message(msg: TTSMessage) -> None:
    if not msg.payload:
        return
    try:
        text = msg.payload.decode("utf-8")
    except Exception:
        logger.info("TTS server response received (non-utf8 payload, %d bytes)", len(msg.payload))
        return
    try:
        payload = json.loads(text)
        logger.info("TTS server response: %s", payload)
    except Exception:
        logger.info("TTS server response (raw): %s", text)


async def receive_message(websocket) -> TTSMessage:
    """Receive and parse a TTS message"""
    data = await websocket.recv()
    if isinstance(data, bytes):
        return parse_tts_response(data)
    return TTSMessage(type=MsgType.ErrorResponse, error_message="Unexpected message type")


class TTSService:
    """ByteDance/Volcano TTS Service"""

    WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream"
    RESOURCE_ID = "seed-tts-2.0"

    def __init__(self):
        self.app_id = settings.volc_app_id
        self.access_token = settings.volc_access_token
        self.voice_type = settings.volc_tts_voice_type
        self.sample_rate = settings.volc_tts_sample_rate

    async def synthesize_stream(
        self,
        text: str,
        speed_ratio: float = 1.0,
        volume_ratio: float = 1.0,
        interrupt_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream synthesize speech from text.

        Args:
            text: Text to synthesize
            speed_ratio: Speech speed (0.8-2.0)
            volume_ratio: Volume (0.8-2.0)
            interrupt_check: Optional function to check if synthesis should be interrupted

        Yields:
            Audio data chunks (MP3 format)
        """
        if not self.app_id or not self.access_token:
            logger.error("TTS service not configured")
            return

        headers = {
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.RESOURCE_ID,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

        try:
            async with websockets.connect(
                self.WS_URL,
                extra_headers=headers,
                max_size=10 * 1024 * 1024
            ) as websocket:
                # Build request
                request = {
                    "user": {
                        "uid": str(uuid.uuid4()),
                    },
                    "req_params": {
                        "speaker": self.voice_type,
                        "audio_params": {
                            "format": "mp3",
                            "sample_rate": self.sample_rate,
                            "enable_timestamp": False,
                        },
                        "text": text,
                        "speed_ratio": speed_ratio,
                        "volume_ratio": volume_ratio,
                        "additions": json.dumps({
                            "disable_markdown_filter": False,
                        }),
                    },
                }

                # Send request
                await full_client_request(websocket, json.dumps(request).encode())

                # Receive audio stream
                while True:
                    # Check for interrupt
                    if interrupt_check and interrupt_check():
                        logger.info("TTS interrupted by user")
                        break

                    try:
                        msg = await asyncio.wait_for(receive_message(websocket), timeout=30.0)
                    except asyncio.TimeoutError:
                        logger.warning("TTS receive timeout")
                        break

                    if msg.type == MsgType.FullServerResponse:
                        _log_server_message(msg)
                        if msg.event == EventType.SessionFinished:
                            break
                    elif msg.type == MsgType.AudioOnlyServer:
                        if msg.payload:
                            yield msg.payload
                    elif msg.type == MsgType.ErrorResponse:
                        raise RuntimeError(
                            f"TTS error (code {msg.error_code}): {msg.error_message}"
                        )

        except Exception as e:
            logger.error(f"TTS stream error: {e}")
            raise

    async def synthesize(
        self,
        text: str,
        speed_ratio: float = 1.0,
        volume_ratio: float = 1.0,
    ) -> Optional[bytes]:
        """
        Synthesize complete audio from text.

        Args:
            text: Text to synthesize
            speed_ratio: Speech speed (0.8-2.0)
            volume_ratio: Volume (0.8-2.0)

        Returns:
            Complete audio data (MP3) or None on error
        """
        try:
            chunks = []
            async for chunk in self.synthesize_stream(text, speed_ratio, volume_ratio):
                chunks.append(chunk)

            if chunks:
                audio = b"".join(chunks)
                fmt = _detect_audio_format(audio)
                if not fmt:
                    logger.warning("TTS audio format unknown, wrapping as WAV")
                    return _wrap_wav(audio, sample_rate=self.sample_rate, channels=1, bits_per_sample=16)
                return audio
            return None

        except Exception as e:
            logger.error(f"TTS error: {e}")
            return None

    async def synthesize_base64(
        self,
        text: str,
        speed_ratio: float = 1.0,
        volume_ratio: float = 1.0,
    ) -> Optional[str]:
        """
        Synthesize audio and return as base64 string.

        Args:
            text: Text to synthesize
            speed_ratio: Speech speed (0.8-2.0)
            volume_ratio: Volume (0.8-2.0)

        Returns:
            Base64 encoded audio data or None on error
        """
        audio_data = await self.synthesize(text, speed_ratio, volume_ratio)
        if audio_data:
            return base64.b64encode(audio_data).decode("utf-8")
        return None


# Singleton instance
tts_service = TTSService()
