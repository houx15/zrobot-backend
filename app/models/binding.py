from sqlalchemy import BigInteger, String, SmallInteger, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from datetime import datetime
from typing import Optional

from app.database import Base


class ParentStudentBinding(Base):
    """Parent-Student binding model - 家长学生绑定关系"""
    __tablename__ = "parent_student_binding"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("parent_user.id"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("student_user.id"), nullable=False, index=True
    )
    relation: Mapped[Optional[str]] = mapped_column(String(20))  # father/mother/grandpa/grandma/other
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    unbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)  # 1=bound, 0=unbound
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("parent_id", "student_id", "status", name="uq_parent_student_status"),
    )
