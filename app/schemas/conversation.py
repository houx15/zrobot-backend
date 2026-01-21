from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class ConversationCreateRequest(BaseModel):
    """Create conversation request"""
    type: str  # solving/chat
    question_history_id: Optional[int] = None


class ConversationCreateData(BaseModel):
    """Create conversation response data"""
    conversation_id: int
    ws_url: str
    token: str
    expire_at: datetime


class ConversationEndRequest(BaseModel):
    """End conversation request"""
    conversation_id: int


class ConversationEndData(BaseModel):
    """End conversation response data"""
    duration: int
    message_count: int
    topic: Optional[str] = None


class MessageInfo(BaseModel):
    """Message info"""
    role: str  # user/assistant
    type: str  # text/image
    content: str
    timestamp: datetime


class ConversationDetailData(BaseModel):
    """Conversation detail response data"""
    conversation_id: int
    type: str
    topic: Optional[str] = None
    duration: Optional[int] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    messages: List[MessageInfo] = []


class ConversationHistoryItem(BaseModel):
    """Conversation history list item"""
    conversation_id: int
    type: str
    topic: Optional[str] = None
    message_count: Optional[int] = None
    duration: Optional[int] = None
    started_at: datetime
    ended_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ConversationHistoryData(BaseModel):
    """Conversation history response data"""
    total: int
    page: int
    page_size: int
    list: List[ConversationHistoryItem]
