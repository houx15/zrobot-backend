from fastapi import APIRouter, Query
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime, timezone

from app.api.deps import DbSession, CurrentUser
from app.models.homework import HomeworkCorrectionHistory, QuestionHistory
from app.models.study import StudyRecord, KnowledgePointRecord
from app.schemas.base import BaseResponse
from app.schemas.correction import (
    CorrectionSubmitRequest,
    CorrectionSubmitData,
    CorrectionHistoryData,
    CorrectionHistoryItem,
    QuestionResult,
)
from app.services.zhipu import zhipu_service
from app.utils.exceptions import ExternalAPIException
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


async def create_question_records(
    db,
    user_id: int,
    correction_id: int,
    correction_response,
):
    """Create QuestionHistory records for each question"""
    for result in correction_response.results:
        question = QuestionHistory(
            correction_id=correction_id,
            user_id=user_id,
            source="correction",
            subject=correction_response.subject,
            question_index=result.index,
            question_uuid=result.uuid,
            question_text=result.question_text,
            question_type=result.question_type,
            user_answer=result.user_answer,
            correct_answer=result.correct_answer,
            is_correct=result.is_correct,
            question_bbox=result.question_bbox,
            answer_bbox=result.answer_bbox,
            correct_source=result.correct_source,
            is_finish=result.is_finish,
        )
        db.add(question)

    await db.commit()


async def update_knowledge_points(
    db,
    user_id: int,
    subject: Optional[str],
    question_count: int,
):
    """Update knowledge point records"""
    if not subject:
        return

    # For now, just track subject-level stats
    # In production, you'd extract specific knowledge points from the questions
    result = await db.execute(
        select(KnowledgePointRecord).where(
            KnowledgePointRecord.user_id == user_id,
            KnowledgePointRecord.topic_name == subject,
        )
    )
    record = result.scalar_one_or_none()

    if record:
        record.question_count += question_count
        record.updated_at = datetime.now(timezone.utc)
    else:
        record = KnowledgePointRecord(
            user_id=user_id,
            topic_name=subject,
            subject=subject,
            question_count=question_count,
        )
        db.add(record)

async def create_study_record(
    db,
    user_id: int,
    correction_id: int,
    subject: Optional[str],
    correct_count: int,
    total: int,
):
    """Create study record for the correction activity"""
    accuracy = int(correct_count / total * 100) if total > 0 else 0
    abstract = f"批改{subject or ''}作业，正确率{accuracy}%"

    record = StudyRecord(
        user_id=user_id,
        action="correction",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        duration=10,  # Fixed duration for correction
        abstract=abstract,
        related_id=correction_id,
        related_type="correction",
        status=1,
    )
    db.add(record)


@router.post("/submit", response_model=BaseResponse[CorrectionSubmitData])
async def submit_correction(
    request: CorrectionSubmitRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """
    提交作业批改

    上传作业图片，调用智谱API进行批改

    流程:
    1. 创建批改记录
    2. 调用智谱作业批改API
    3. 解析响应，创建题目记录
    4. 更新知识点统计
    5. 记录学习时长
    """
    # 1. Create correction history record (status=0, processing)
    correction = HomeworkCorrectionHistory(
        user_id=current_user.id,
        image_url=request.image_url,
        status=0,  # Processing
    )
    db.add(correction)
    await db.commit()
    await db.refresh(correction)

    try:
        # 2. Call Zhipu homework correction API
        correction_response = await zhipu_service.correct_homework(request.image_url)

        # 3. Update correction record
        correction.subject = correction_response.subject
        correction.processed_image_url = correction_response.processed_image_url
        correction.total_questions = correction_response.total_questions
        correction.correct_count = correction_response.correct_count
        correction.wrong_count = correction_response.wrong_count
        correction.correcting_count = correction_response.correcting_count
        correction.api_trace_id = correction_response.trace_id
        correction.raw_response = correction_response.raw_response
        correction.status = 1  # Completed

        await db.commit()

        # 4. Create question records
        await create_question_records(
            db,
            current_user.id,
            correction.id,
            correction_response,
        )

        # 5. Get question detail IDs for response
        result = await db.execute(
            select(QuestionHistory).where(
                QuestionHistory.correction_id == correction.id,
            ).order_by(QuestionHistory.question_index)
        )
        questions = result.scalars().all()

        # 6. Build response
        results = [
            QuestionResult(
                question_index=q.question_index,
                question_detail_id=q.id,
                is_correct=q.is_correct or False,
                question_bbox=q.question_bbox,
                answer_bbox=q.answer_bbox,
                user_answer=q.user_answer,
                correct_answer=q.correct_answer if not q.is_correct else None,
            )
            for q in questions
        ]

        # 7. Update knowledge points and create study record
        await update_knowledge_points(
            db,
            current_user.id,
            correction_response.subject,
            correction_response.total_questions,
        )
        await create_study_record(
            db,
            current_user.id,
            correction.id,
            correction_response.subject,
            correction_response.correct_count,
            correction_response.total_questions,
        )
        await db.commit()

        return BaseResponse.success(
            data=CorrectionSubmitData(
                correction_id=correction.id,
                processed_image_url=correction.processed_image_url,
                subject=correction.subject,
                total_questions=correction.total_questions or 0,
                correct_count=correction.correct_count or 0,
                wrong_count=correction.wrong_count or 0,
                results=results,
            )
        )

    except Exception as e:
        logger.exception("Correction failed")
        # Update status to failed
        correction.status = 2
        await db.commit()
        raise ExternalAPIException(f"批改失败: {str(e)}")


@router.get("/history", response_model=BaseResponse[CorrectionHistoryData])
async def get_correction_history(
    current_user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    subject: Optional[str] = Query(default=None),
):
    """
    获取批改历史列表
    """
    # Build query
    query = select(HomeworkCorrectionHistory).where(
        HomeworkCorrectionHistory.user_id == current_user.id,
        HomeworkCorrectionHistory.is_deleted == False,
    )

    # Filter by subject if provided
    if subject:
        query = query.where(HomeworkCorrectionHistory.subject == subject)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination and ordering
    query = query.order_by(HomeworkCorrectionHistory.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    # Execute query
    result = await db.execute(query)
    corrections = result.scalars().all()

    # Build response
    items = [
        CorrectionHistoryItem(
            correction_id=c.id,
            image_url=c.image_url,
            processed_image_url=c.processed_image_url,
            subject=c.subject,
            total_questions=c.total_questions,
            correct_count=c.correct_count,
            wrong_count=c.wrong_count,
            created_at=c.created_at,
        )
        for c in corrections
    ]

    return BaseResponse.success(
        data=CorrectionHistoryData(
            total=total,
            page=page,
            page_size=page_size,
            list=items,
        )
    )
