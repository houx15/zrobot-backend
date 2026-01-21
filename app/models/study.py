from sqlalchemy import BigInteger, String, Integer, SmallInteger, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from datetime import datetime
from typing import Optional

from app.database import Base


class StudyRecord(Base):
    """Study record - 学习记录"""
    __tablename__ = "study_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("student_user.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)  # correction/tutoring/chat/homework
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration: Mapped[Optional[int]] = mapped_column(Integer)  # seconds
    abstract: Mapped[Optional[str]] = mapped_column(String(500))
    related_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    related_type: Mapped[Optional[str]] = mapped_column(String(30))  # correction/solving/conversation
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 0=in_progress, 1=completed, 2=abnormal
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KnowledgePointRecord(Base):
    """Knowledge point record - 知识点记录"""
    __tablename__ = "knowledge_point_record"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("student_user.id"), nullable=False, index=True
    )
    topic_name: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(20))
    question_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "topic_name", name="uq_user_topic"),
    )
