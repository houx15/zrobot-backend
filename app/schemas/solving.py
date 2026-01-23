from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class SolvingRequest(BaseModel):
    """Problem solving request"""
    image_url: str


class SolvingData(BaseModel):
    """Problem solving response data"""
    question_history_id: int
    image_url: Optional[str] = None
    question_text: Optional[str] = None


class SolvingHistoryItem(BaseModel):
    """Solving history list item"""
    id: int
    image_url: Optional[str] = None
    question_text: Optional[str] = None
    answer: Optional[str] = None
    course: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SolvingHistoryData(BaseModel):
    """Solving history response data"""
    total: int
    page: int
    page_size: int
    list: List[SolvingHistoryItem]
