from sqlalchemy import BigInteger, String, Text, Integer, SmallInteger, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from datetime import datetime
from typing import Optional

from app.database import Base


class HomeworkCorrectionHistory(Base):
    """Homework correction history - 批改历史主表"""
    __tablename__ = "homework_correction_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("student_user.id"), nullable=False, index=True
    )
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(20))
    processed_image_url: Mapped[Optional[str]] = mapped_column(Text)
    total_questions: Mapped[Optional[int]] = mapped_column(Integer)
    correct_count: Mapped[Optional[int]] = mapped_column(Integer)
    wrong_count: Mapped[Optional[int]] = mapped_column(Integer)
    correcting_count: Mapped[Optional[int]] = mapped_column(Integer)
    raw_response: Mapped[Optional[dict]] = mapped_column(JSONB)
    api_trace_id: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 0=processing, 1=done, 2=failed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class QuestionHistory(Base):
    """Question history - 题目历史(批改/答疑)"""
    __tablename__ = "question_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    correction_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("homework_correction_history.id"), index=True
    )
    conversation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("ai_conversation_history.id"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("student_user.id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # solving/correction
    subject: Mapped[Optional[str]] = mapped_column(String(20))
    question_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question_uuid: Mapped[Optional[str]] = mapped_column(String(100))
    question_text: Mapped[Optional[str]] = mapped_column(Text)
    question_image_url: Mapped[Optional[str]] = mapped_column(String(500))
    question_type: Mapped[Optional[int]] = mapped_column(SmallInteger)
    user_answer: Mapped[Optional[str]] = mapped_column(String(500))
    correct_answer: Mapped[Optional[str]] = mapped_column(String(500))
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean)
    analysis: Mapped[Optional[str]] = mapped_column(Text)
    knowledge_points: Mapped[Optional[list]] = mapped_column(JSONB)
    question_bbox: Mapped[Optional[list]] = mapped_column(JSONB)
    answer_bbox: Mapped[Optional[list]] = mapped_column(JSONB)
    correct_source: Mapped[Optional[int]] = mapped_column(SmallInteger)
    api_trace_id: Mapped[Optional[str]] = mapped_column(String(100))
    is_finish: Mapped[Optional[bool]] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
