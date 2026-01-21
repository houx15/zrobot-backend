"""WebSocket message protocol definitions"""

from pydantic import BaseModel
from typing import Optional, Any, Literal
from datetime import datetime, timezone
from enum import Enum


class ClientMessageType(str, Enum):
    """Client to server message types"""
    AUDIO = "audio"
    TEXT = "text"
    IMAGE = "image"
    INTERRUPT = "interrupt"
    PING = "ping"


class ServerMessageType(str, Enum):
    """Server to client message types"""
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    REPLY_TEXT = "reply_text"
    REPLY_START = "reply_start"
    REPLY_END = "reply_end"
    ERROR = "error"
    PONG = "pong"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


# Client -> Server message data models
class AudioData(BaseModel):
    """Audio message data"""
    audio: str  # base64 encoded opus data
    is_final: bool = False


class TextData(BaseModel):
    """Text message data"""
    content: str


class ImageData(BaseModel):
    """Image message data"""
    image_url: str


class ClientMessage(BaseModel):
    """Generic client message structure"""
    type: ClientMessageType
    data: Optional[dict] = None
    timestamp: Optional[str] = None

    def get_audio_data(self) -> Optional[AudioData]:
        if self.type == ClientMessageType.AUDIO and self.data:
            return AudioData(**self.data)
        return None

    def get_text_data(self) -> Optional[TextData]:
        if self.type == ClientMessageType.TEXT and self.data:
            return TextData(**self.data)
        return None

    def get_image_data(self) -> Optional[ImageData]:
        if self.type == ClientMessageType.IMAGE and self.data:
            return ImageData(**self.data)
        return None


# Server -> Client message data models
class ServerAudioData(BaseModel):
    """Server audio response data"""
    audio: str  # base64 encoded opus data
    is_final: bool = False


class TranscriptData(BaseModel):
    """ASR transcript data"""
    content: str
    is_final: bool = False


class ReplyTextData(BaseModel):
    """LLM reply text data"""
    content: str
    is_final: bool = False


class ErrorData(BaseModel):
    """Error message data"""
    code: int
    message: str


class ServerMessage(BaseModel):
    """Generic server message structure"""
    type: ServerMessageType
    data: Optional[dict] = None
    timestamp: str = None

    def __init__(self, **kwargs):
        if "timestamp" not in kwargs or kwargs["timestamp"] is None:
            kwargs["timestamp"] = datetime.now(timezone.utc).isoformat()
        super().__init__(**kwargs)

    @classmethod
    def audio(cls, audio_data: str, is_final: bool = False) -> "ServerMessage":
        """Create audio message"""
        return cls(
            type=ServerMessageType.AUDIO,
            data={"audio": audio_data, "is_final": is_final},
        )

    @classmethod
    def transcript(cls, content: str, is_final: bool = False) -> "ServerMessage":
        """Create transcript message"""
        return cls(
            type=ServerMessageType.TRANSCRIPT,
            data={"content": content, "is_final": is_final},
        )

    @classmethod
    def reply_text(cls, content: str, is_final: bool = False) -> "ServerMessage":
        """Create reply text message"""
        return cls(
            type=ServerMessageType.REPLY_TEXT,
            data={"content": content, "is_final": is_final},
        )

    @classmethod
    def reply_start(cls) -> "ServerMessage":
        """Create reply start message"""
        return cls(type=ServerMessageType.REPLY_START, data={})

    @classmethod
    def reply_end(cls) -> "ServerMessage":
        """Create reply end message"""
        return cls(type=ServerMessageType.REPLY_END, data={})

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

    @classmethod
    def connected(cls, conversation_id: int) -> "ServerMessage":
        """Create connected message"""
        return cls(
            type=ServerMessageType.CONNECTED,
            data={"conversation_id": conversation_id},
        )

    @classmethod
    def disconnected(cls, reason: str = "") -> "ServerMessage":
        """Create disconnected message"""
        return cls(
            type=ServerMessageType.DISCONNECTED,
            data={"reason": reason},
        )
