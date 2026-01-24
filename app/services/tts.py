"""
ByteDance/Volcano TTS (Text-to-Speech) Service

Based on Volcano Engine v3 WebSocket API for streaming speech synthesis.
"""

import asyncio
import base64
import json
import logging
import struct
import uuid
from typing import AsyncGenerator, Callable, Optional

import websockets

from app.config import settings
from app.services.volc_tts_protocol import (
    EventType,
    MsgType,
    full_client_request,
    receive_message,
)

logger = logging.getLogger(__name__)


def _detect_audio_format(data: bytes) -> Optional[str]:
    if len(data) < 4:
        return None
    if (
        data.startswith(b"ID3")
        or data[:2] == b"\xff\xfb"
        or data[:2] == b"\xff\xf3"
        or data[:2] == b"\xff\xf2"
    ):
        return "mp3"
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"RIFF"):
        return "wav"
    return None


def _wrap_wav(
    pcm_data: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
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


def _log_server_message(msg) -> None:
    if not msg.payload:
        return
    try:
        text = msg.payload.decode("utf-8")
    except Exception:
        logger.info(
            "TTS server response received (non-utf8 payload, %d bytes)",
            len(msg.payload),
        )
        return
    try:
        payload = json.loads(text)
        logger.info("TTS server response: %s", payload)
    except Exception:
        logger.info("TTS server response (raw): %s", text)


class TTSService:
    """ByteDance/Volcano TTS Service"""

    WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream"

    def __init__(self):
        self.app_id = settings.volc_app_id
        self.access_token = settings.volc_access_token
        self.voice_type = settings.volc_tts_voice_type
        self.sample_rate = settings.volc_tts_sample_rate
        self.resource_id = settings.volc_tts_resource_id

    def _get_resource_id(self) -> str:
        if self.resource_id:
            return self.resource_id
        if self.voice_type.startswith("S_"):
            return "volc.megatts.default"
        return "volc.service_type.10029"

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
            "X-Api-Resource-Id": self._get_resource_id(),
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

        try:
            async with websockets.connect(
                self.WS_URL, extra_headers=headers, max_size=10 * 1024 * 1024
            ) as websocket:
                # Build request
                request = {
                    "user": {
                        "uid": str(uuid.uuid4()),
                    },
                    "req_params": {
                        "speaker": self.voice_type,
                        "audio_params": {
                            "format": "pcm",
                            "sample_rate": self.sample_rate,
                            "enable_timestamp": False,
                        },
                        "text": text,
                        "speed_ratio": speed_ratio,
                        "volume_ratio": volume_ratio,
                        "additions": json.dumps(
                            {
                                "disable_markdown_filter": False,
                            }
                        ),
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
                        msg = await asyncio.wait_for(
                            receive_message(websocket), timeout=30.0
                        )
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
                    elif msg.type == MsgType.Error:
                        err = ""
                        if msg.payload:
                            try:
                                err = msg.payload.decode("utf-8", "ignore")
                            except Exception:
                                err = ""
                        raise RuntimeError(f"TTS error (code {msg.error_code}): {err}")

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
                    return _wrap_wav(
                        audio,
                        sample_rate=self.sample_rate,
                        channels=1,
                        bits_per_sample=16,
                    )
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
