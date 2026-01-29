"""WebSocket message protocol definitions (v2 envelope)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ConversationState(str, Enum):
    """Conversation state machine states."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"


def now_ms() -> int:
    """UTC timestamp in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def new_msg_id() -> str:
    return str(uuid.uuid4())


class WsEnvelope(BaseModel):
    """Unified message envelope."""

    type: str
    conv_id: int
    msg_id: str
    ts_ms: int
    payload: Dict[str, Any] = Field(default_factory=dict)


class ServerMessage:
    """Server message factory helpers."""

    @staticmethod
    def _make(conv_id: int, msg_type: str, payload: Optional[dict] = None) -> WsEnvelope:
        return WsEnvelope(
            type=msg_type,
            conv_id=conv_id,
            msg_id=new_msg_id(),
            ts_ms=now_ms(),
            payload=payload or {},
        )

    @classmethod
    def state(cls, conv_id: int, state: str, detail: Optional[str] = None) -> WsEnvelope:
        payload = {"state": state}
        if detail:
            payload["detail"] = detail
        return cls._make(conv_id, "state", payload)

    @classmethod
    def asr_partial(
        cls, conv_id: int, stream_id: str, text: str, stability: Optional[float] = None
    ) -> WsEnvelope:
        payload = {"stream_id": stream_id, "text": text}
        if stability is not None:
            payload["stability"] = stability
        return cls._make(conv_id, "asr_partial", payload)

    @classmethod
    def asr_final(cls, conv_id: int, stream_id: str, text: str) -> WsEnvelope:
        return cls._make(conv_id, "asr_final", {"stream_id": stream_id, "text": text})

    @classmethod
    def segment_start(cls, conv_id: int, segment_id: int, index: int) -> WsEnvelope:
        return cls._make(
            conv_id, "segment_start", {"segment_id": segment_id, "index": index}
        )

    @classmethod
    def ai_text_delta(
        cls, conv_id: int, segment_id: int, seq: int, delta: str
    ) -> WsEnvelope:
        return cls._make(
            conv_id,
            "ai_text_delta",
            {"segment_id": segment_id, "seq": seq, "delta": delta},
        )

    @classmethod
    def audio_chunk(
        cls,
        conv_id: int,
        segment_id: int,
        seq: int,
        data_b64: str,
        format: str,
        sample_rate: int,
        channels: int,
        bits_per_sample: int,
    ) -> WsEnvelope:
        return cls._make(
            conv_id,
            "audio_chunk",
            {
                "segment_id": segment_id,
                "seq": seq,
                "format": format,
                "sample_rate": sample_rate,
                "channels": channels,
                "bits_per_sample": bits_per_sample,
                "data_b64": data_b64,
            },
        )

    @classmethod
    def audio_end(cls, conv_id: int, segment_id: int, last_seq: int) -> WsEnvelope:
        return cls._make(
            conv_id, "audio_end", {"segment_id": segment_id, "last_seq": last_seq}
        )

    @classmethod
    def board(
        cls, conv_id: int, segment_id: int, content: str, format: str = "md"
    ) -> WsEnvelope:
        return cls._make(
            conv_id,
            "board",
            {"segment_id": segment_id, "format": format, "content": content},
        )

    @classmethod
    def done(
        cls, conv_id: int, total_segments: int, reason: str = "completed"
    ) -> WsEnvelope:
        return cls._make(
            conv_id, "done", {"total_segments": total_segments, "reason": reason}
        )

    @classmethod
    def error(
        cls, conv_id: int, code: int, message: str, retryable: bool = False
    ) -> WsEnvelope:
        return cls._make(
            conv_id,
            "error",
            {"code": code, "message": message, "retryable": retryable},
        )

    @classmethod
    def pong(cls, conv_id: int) -> WsEnvelope:
        return cls._make(conv_id, "pong", {})
