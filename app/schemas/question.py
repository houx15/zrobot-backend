from pydantic import BaseModel
from typing import Optional, List


class QuestionDetailData(BaseModel):
    """Question detail response data"""
    id: int
    correction_id: Optional[int] = None
    source: str
    subject: Optional[str] = None
    question_index: int
    question_text: Optional[str] = None
    question_image_url: Optional[str] = None
    user_answer: Optional[str] = None
    correct_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    analysis: Optional[str] = None
    knowledge_points: Optional[List[str]] = None

    class Config:
        from_attributes = True
