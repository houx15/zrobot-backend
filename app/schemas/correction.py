from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class CorrectionSubmitRequest(BaseModel):
    """Correction submit request"""
    image_url: str


class QuestionResult(BaseModel):
    """Single question result"""
    question_index: int
    question_detail_id: int
    is_correct: Optional[bool] = None
    is_finish: Optional[bool] = None
    question_bbox: Optional[List[int]] = None
    answer_bbox: Optional[List[int]] = None
    user_answer: Optional[str] = None
    correct_answer: Optional[str] = None


class CorrectionSubmitData(BaseModel):
    """Correction submit response data"""
    correction_id: int
    processed_image_url: Optional[str] = None
    subject: Optional[str] = None
    total_questions: int
    correct_count: int
    wrong_count: int
    results: List[QuestionResult]


class CorrectionHistoryItem(BaseModel):
    """Correction history list item"""
    correction_id: int
    image_url: str
    processed_image_url: Optional[str] = None
    subject: Optional[str] = None
    total_questions: Optional[int] = None
    correct_count: Optional[int] = None
    wrong_count: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CorrectionHistoryData(BaseModel):
    """Correction history response data"""
    total: int
    page: int
    page_size: int
    list: List[CorrectionHistoryItem]
