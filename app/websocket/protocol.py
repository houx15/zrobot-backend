"""WebSocket message protocol definitions"""

from pydantic import BaseModel
from typing import Optional, Any, Literal
from datetime import datetime, timezone
from enum import Enum


class ClientMessageType(str, Enum):
    """Client to server message types"""
    AUDIO = "audio"
    END_SPEAKING = "end_speaking"
    IMAGE = "image"
    INTERRUPT = "interrupt"
    PING = "ping"


class ServerMessageType(str, Enum):
    """Server to client message types"""
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    SEGMENT = "segment"  # New: speech + board segment
    STATE = "state"  # Conversation state update
    DONE = "done"
    ERROR = "error"
    PONG = "pong"


class ConversationState(str, Enum):
    """Conversation state machine states"""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"


# Client -> Server message data models
class AudioData(BaseModel):
    """Audio message data"""
    audio: str  # base64 encoded PCM s16le (16kHz, mono)
    sequence: int


class ClientMessage(BaseModel):
    """Generic client message structure"""
    type: ClientMessageType
    data: Optional[dict] = None
    timestamp: Optional[str] = None

    def get_audio_data(self) -> Optional[AudioData]:
        if self.type == ClientMessageType.AUDIO and self.data:
            return AudioData(**self.data)
        return None

    def get_image_data(self) -> Optional["ImageData"]:
        if self.type == ClientMessageType.IMAGE and self.data:
            return ImageData(**self.data)
        return None


class ImageData(BaseModel):
    """Image message data"""
    image_url: str

# Server -> Client message data models
class ServerAudioData(BaseModel):
    """Server audio response data"""
    audio: str  # base64 encoded audio (mp3)
    segment_id: int
    is_final: bool = False


class TranscriptData(BaseModel):
    """ASR transcript data"""
    text: str
    is_final: bool = False


class ReplyTextData(BaseModel):
    """LLM reply text data"""
    content: str
    is_final: bool = False


class SegmentData(BaseModel):
    """Speech + Board segment data"""
    segment_id: int
    speech: str  # Speech text for TTS
    board: str  # Board markup content
    # audio is sent separately via ServerMessage.audio


class StateChangeData(BaseModel):
    """Conversation state change data"""
    state: str  # idle, listening, processing, speaking


class ErrorData(BaseModel):
    """Error message data"""
    code: int
    message: str


class ServerMessage(BaseModel):
    """Generic server message structure"""
    type: ServerMessageType
    data: Optional[dict] = None
    timestamp: Optional[str] = None

    def __init__(self, **kwargs):
        if "timestamp" not in kwargs or kwargs["timestamp"] is None:
            kwargs["timestamp"] = datetime.now(timezone.utc).isoformat()
        super().__init__(**kwargs)

    @classmethod
    def audio(
        cls,
        audio_data: str,
        segment_id: int,
        is_final: bool = False,
    ) -> "ServerMessage":
        """Create audio message"""
        return cls(
            type=ServerMessageType.AUDIO,
            data={
                "audio": audio_data,
                "segment_id": segment_id,
                "is_final": is_final,
            },
        )

    @classmethod
    def transcript(cls, content: str, is_final: bool = False) -> "ServerMessage":
        """Create transcript message"""
        return cls(
            type=ServerMessageType.TRANSCRIPT,
            data={"text": content, "is_final": is_final},
        )

    @classmethod
    def segment(
        cls,
        segment_id: int,
        speech: str,
        board: str,
    ) -> "ServerMessage":
        """Create segment message with speech + board content"""
        return cls(
            type=ServerMessageType.SEGMENT,
            data={
                "segment_id": segment_id,
                "speech": speech,
                "board": board,
            },
        )

    @classmethod
    def state(cls, state: str) -> "ServerMessage":
        """Create state message"""
        return cls(
            type=ServerMessageType.STATE,
            data={"state": state},
        )

    @classmethod
    def done(cls, total_segments: int) -> "ServerMessage":
        """Create done message"""
        return cls(
            type=ServerMessageType.DONE,
            data={"total_segments": total_segments},
        )

    @classmethod
    def error(cls, code: int, message: str) -> "ServerMessage":
        """Create error message"""
        return cls(
            type=ServerMessageType.ERROR,
            data={"code": code, "message": message},
        )

    @classmethod
    def pong(cls) -> "ServerMessage":
        """Create pong message"""
        return cls(type=ServerMessageType.PONG, data={})
