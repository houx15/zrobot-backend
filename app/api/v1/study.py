from fastapi import APIRouter, Query
from sqlalchemy import select, func
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.api.deps import DbSession, CurrentUser
from app.models.study import StudyRecord
from app.schemas.base import BaseResponse
from app.schemas.study import (
    StudyRecordCreate,
    StudyRecordStartRequest,
    StudyRecordEndRequest,
    StudyRecordData,
    StudyRecordEndData,
    StudyRecordInfo,
    StudyRecordListData,
)
from app.utils.exceptions import NotFoundException

router = APIRouter()


@router.post("/record", response_model=BaseResponse[StudyRecordData])
async def create_study_record(
    request: StudyRecordCreate,
    current_user: CurrentUser,
    db: DbSession,
):
    """
    新增学习记录（简单模式）

    用户完成某个学习行为后，前端调用此接口记录时长
    适用于前端自行计时的场景
    """
    # Calculate start_time based on duration
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(seconds=request.duration)

    # Create study record
    record = StudyRecord(
        user_id=current_user.id,
        action=request.action,
        start_time=start_time,
        end_time=end_time,
        duration=request.duration,
        abstract=request.abstract,
        related_id=request.related_id,
        related_type=request.related_type,
        status=1,  # Completed
    )

    db.add(record)
    await db.commit()
    await db.refresh(record)

    return BaseResponse.success(
        data=StudyRecordData(record_id=record.id)
    )


@router.post("/start", response_model=BaseResponse[StudyRecordData])
async def start_study_record(
    request: StudyRecordStartRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """
    开始学习记录

    用户进入学习页面时调用，记录开始时间
    返回record_id，离开页面时调用/end接口
    """
    # Create study record with status=0 (in progress)
    record = StudyRecord(
        user_id=current_user.id,
        action=request.action,
        start_time=datetime.now(timezone.utc),
        status=0,  # In progress
    )

    db.add(record)
    await db.commit()
    await db.refresh(record)

    return BaseResponse.success(
        data=StudyRecordData(record_id=record.id)
    )


@router.post("/end", response_model=BaseResponse[StudyRecordEndData])
async def end_study_record(
    request: StudyRecordEndRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """
    结束学习记录

    用户离开学习页面时调用
    后端计算时长并更新记录
    """
    # Get the study record
    result = await db.execute(
        select(StudyRecord).where(
            StudyRecord.id == request.record_id,
            StudyRecord.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()

    if not record:
        raise NotFoundException("学习记录不存在")

    # Calculate duration
    end_time = datetime.now(timezone.utc)
    if record.start_time:
        # Ensure start_time is timezone aware
        start_time = record.start_time
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        duration = int((end_time - start_time).total_seconds())
    else:
        duration = 0

    # Update record
    record.end_time = end_time
    record.duration = duration
    record.abstract = request.abstract
    record.related_id = request.related_id
    record.related_type = request.related_type
    record.status = 1  # Completed

    await db.commit()

    return BaseResponse.success(
        data=StudyRecordEndData(
            record_id=record.id,
            duration=duration,
        )
    )


@router.get("/history", response_model=BaseResponse[StudyRecordListData])
async def get_study_history(
    current_user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    action: Optional[str] = Query(default=None),
):
    """
    获取学习记录历史

    支持按行为类型筛选
    """
    # Build query
    query = select(StudyRecord).where(
        StudyRecord.user_id == current_user.id,
    )

    # Filter by action if provided
    if action:
        query = query.where(StudyRecord.action == action)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination and ordering
    query = query.order_by(StudyRecord.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    # Execute query
    result = await db.execute(query)
    records = result.scalars().all()

    # Build response
    items = [
        StudyRecordInfo(
            id=r.id,
            action=r.action,
            start_time=r.start_time,
            end_time=r.end_time,
            duration=r.duration,
            abstract=r.abstract,
            related_id=r.related_id,
            related_type=r.related_type,
            status=r.status,
            created_at=r.created_at,
        )
        for r in records
    ]

    return BaseResponse.success(
        data=StudyRecordListData(
            total=total,
            page=page,
            page_size=page_size,
            list=items,
        )
    )
