"""
ByteDance/Volcano ASR (Speech Recognition) Service

Based on Volcano Engine v3 WebSocket API for real-time speech recognition.
"""

import asyncio
import aiohttp
import json
import struct
import gzip
import uuid
import logging
from typing import AsyncGenerator, Optional, Callable
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


class ProtocolVersion:
    V1 = 0b0001


class MessageType:
    CLIENT_FULL_REQUEST = 0b0001
    CLIENT_AUDIO_ONLY_REQUEST = 0b0010
    SERVER_FULL_RESPONSE = 0b1001
    SERVER_ERROR_RESPONSE = 0b1111


class MessageTypeSpecificFlags:
    NO_SEQUENCE = 0b0000
    POS_SEQUENCE = 0b0001
    NEG_SEQUENCE = 0b0010
    NEG_WITH_SEQUENCE = 0b0011


class SerializationType:
    NO_SERIALIZATION = 0b0000
    JSON = 0b0001


class CompressionType:
    GZIP = 0b0001


class AsrRequestHeader:
    """ASR request header builder"""

    def __init__(self):
        self.message_type = MessageType.CLIENT_FULL_REQUEST
        self.message_type_specific_flags = MessageTypeSpecificFlags.POS_SEQUENCE
        self.serialization_type = SerializationType.JSON
        self.compression_type = CompressionType.GZIP
        self.reserved_data = bytes([0x00])

    def with_message_type(self, message_type: int) -> "AsrRequestHeader":
        self.message_type = message_type
        return self

    def with_message_type_specific_flags(self, flags: int) -> "AsrRequestHeader":
        self.message_type_specific_flags = flags
        return self

    def to_bytes(self) -> bytes:
        header = bytearray()
        header.append((ProtocolVersion.V1 << 4) | 1)
        header.append((self.message_type << 4) | self.message_type_specific_flags)
        header.append((self.serialization_type << 4) | self.compression_type)
        header.extend(self.reserved_data)
        return bytes(header)


@dataclass
class AsrResponse:
    """ASR response data structure"""
    code: int = 0
    event: int = 0
    is_last_package: bool = False
    payload_sequence: int = 0
    payload_size: int = 0
    payload_msg: Optional[dict] = None


@dataclass
class TranscriptionResult:
    """Transcription result"""
    text: str
    is_final: bool
    confidence: float = 0.0


class ASRService:
    """ByteDance/Volcano ASR Service"""

    WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    RESOURCE_ID = "volc.bigasr.sauc.duration"

    def __init__(self):
        self.app_id = settings.volc_app_id
        self.access_token = settings.volc_access_token

    def _build_auth_headers(self) -> dict:
        """Build authentication headers"""
        reqid = str(uuid.uuid4())
        return {
            "X-Api-Resource-Id": self.RESOURCE_ID,
            "X-Api-Request-Id": reqid,
            "X-Api-Access-Key": self.access_token,
            "X-Api-App-Key": self.app_id,
        }

    def _build_full_client_request(self, seq: int) -> bytes:
        """Build initial full client request"""
        header = AsrRequestHeader().with_message_type_specific_flags(
            MessageTypeSpecificFlags.POS_SEQUENCE
        )

        payload = {
            "user": {"uid": "learning_lamp_user"},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": False,
            },
        }

        payload_bytes = json.dumps(payload).encode("utf-8")
        compressed_payload = gzip.compress(payload_bytes)
        payload_size = len(compressed_payload)

        request = bytearray()
        request.extend(header.to_bytes())
        request.extend(struct.pack(">i", seq))
        request.extend(struct.pack(">I", payload_size))
        request.extend(compressed_payload)

        return bytes(request)

    def _build_audio_only_request(
        self, seq: int, segment: bytes, is_last: bool = False
    ) -> bytes:
        """Build audio-only request"""
        header = AsrRequestHeader()
        if is_last:
            header.with_message_type_specific_flags(
                MessageTypeSpecificFlags.NEG_WITH_SEQUENCE
            )
            seq = -seq
        else:
            header.with_message_type_specific_flags(
                MessageTypeSpecificFlags.POS_SEQUENCE
            )
        header.with_message_type(MessageType.CLIENT_AUDIO_ONLY_REQUEST)

        request = bytearray()
        request.extend(header.to_bytes())
        request.extend(struct.pack(">i", seq))

        compressed_segment = gzip.compress(segment)
        request.extend(struct.pack(">I", len(compressed_segment)))
        request.extend(compressed_segment)

        return bytes(request)

    def _parse_response(self, msg: bytes) -> AsrResponse:
        """Parse server response"""
        response = AsrResponse()

        header_size = msg[0] & 0x0F
        message_type = msg[1] >> 4
        message_type_specific_flags = msg[1] & 0x0F
        serialization_method = msg[2] >> 4
        message_compression = msg[2] & 0x0F

        payload = msg[header_size * 4:]

        # Parse flags
        if message_type_specific_flags & 0x01:
            response.payload_sequence = struct.unpack(">i", payload[:4])[0]
            payload = payload[4:]
        if message_type_specific_flags & 0x02:
            response.is_last_package = True
        if message_type_specific_flags & 0x04:
            response.event = struct.unpack(">i", payload[:4])[0]
            payload = payload[4:]

        # Parse message type
        if message_type == MessageType.SERVER_FULL_RESPONSE:
            response.payload_size = struct.unpack(">I", payload[:4])[0]
            payload = payload[4:]
        elif message_type == MessageType.SERVER_ERROR_RESPONSE:
            response.code = struct.unpack(">i", payload[:4])[0]
            response.payload_size = struct.unpack(">I", payload[4:8])[0]
            payload = payload[8:]

        if not payload:
            return response

        # Decompress
        if message_compression == CompressionType.GZIP:
            try:
                payload = gzip.decompress(payload)
            except Exception as e:
                logger.error(f"Failed to decompress: {e}")
                return response

        # Parse JSON
        try:
            if serialization_method == SerializationType.JSON:
                response.payload_msg = json.loads(payload.decode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to parse JSON: {e}")

        return response

    async def transcribe_stream(
        self,
        audio_chunks: AsyncGenerator[bytes, None],
        on_partial: Optional[Callable[[str], None]] = None,
        interrupt_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncGenerator[TranscriptionResult, None]:
        """
        Stream transcribe audio chunks.

        Args:
            audio_chunks: Async generator yielding audio data (16kHz, 16-bit, mono PCM)
            on_partial: Optional callback for partial results
            interrupt_check: Optional function to check if transcription should be interrupted

        Yields:
            TranscriptionResult: Transcription results (partial and final)
        """
        if not self.app_id or not self.access_token:
            logger.error("ASR service not configured")
            return

        session = aiohttp.ClientSession()
        headers = self._build_auth_headers()
        conn = None
        seq = 1

        try:
            conn = await session.ws_connect(self.WS_URL, headers=headers)
            logger.info("ASR WebSocket connected")

            # Send initial request
            full_request = self._build_full_client_request(seq)
            await conn.send_bytes(full_request)
            seq += 1

            # Receive initial response
            msg = await conn.receive()
            if msg.type == aiohttp.WSMsgType.BINARY:
                response = self._parse_response(msg.data)
                if response.code != 0:
                    raise RuntimeError(
                        f"ASR initialization failed: {response.payload_msg}"
                    )

            # Create receive task
            async def receive_task():
                async for msg in conn:
                    # Check for interrupt
                    if interrupt_check and interrupt_check():
                        logger.info("ASR interrupted by user")
                        break

                    if msg.type == aiohttp.WSMsgType.BINARY:
                        response = self._parse_response(msg.data)
                        if response.payload_msg:
                            result = response.payload_msg.get("result", {})
                            if result:
                                text = result.get("text", "")
                                is_final = result.get("is_final", False)

                                if on_partial and not is_final:
                                    on_partial(text)

                                yield TranscriptionResult(
                                    text=text,
                                    is_final=is_final,
                                )

                        if response.is_last_package or response.code != 0:
                            break
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error("WebSocket error")
                        break

            # Create send task
            async def send_task():
                nonlocal seq
                async for audio_data in audio_chunks:
                    if interrupt_check and interrupt_check():
                        break
                    request = self._build_audio_only_request(seq, audio_data, is_last=False)
                    await conn.send_bytes(request)
                    seq += 1

                # Send end packet
                request = self._build_audio_only_request(seq, bytes(0), is_last=True)
                await conn.send_bytes(request)

            # Run send and receive concurrently
            receiver = receive_task()
            sender_coroutine = send_task()
            sender_task = asyncio.create_task(sender_coroutine)

            try:
                async for result in receiver:
                    yield result
            finally:
                sender_task.cancel()
                try:
                    await sender_task
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            logger.error(f"ASR stream error: {e}")
            raise
        finally:
            if conn and not conn.closed:
                await conn.close()
            if session and not session.closed:
                await session.close()

    async def transcribe(self, audio_bytes: bytes) -> Optional[str]:
        """
        Transcribe complete audio data.

        Args:
            audio_bytes: Audio data (PCM format, 16kHz, 16-bit, mono)

        Returns:
            Transcribed text or None on error
        """
        try:
            async def audio_generator():
                yield audio_bytes

            final_text = ""
            async for result in self.transcribe_stream(audio_generator()):
                if result.is_final:
                    final_text = result.text

            return final_text

        except Exception as e:
            logger.error(f"ASR error: {e}")
            return None


# Singleton instance
asr_service = ASRService()
