from fastapi import APIRouter, Query
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime, timezone
import asyncio

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
from app.database import async_session_maker
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
            analysis=result.analysis,
            api_trace_id=correction_response.trace_id,
        )
        db.add(question)

    await db.commit()


def _parse_polling_results(polling_response: dict):
    try:
        if (
            polling_response.get("code") not in (200, 0)
            and polling_response.get("status") != "success"
        ):
            return None
        llm_result = polling_response["choices"][0]["messages"][0]["content"]["object"]
        raw_response = llm_result.get("image_results", [None])[0]
        if not raw_response:
            return None
        return {
            "raw_response": raw_response,
            "results": raw_response.get("results", []),
            "stat_result": raw_response.get("stat_result", {}),
        }
    except Exception:
        return None


async def _apply_polling_results(
    db, correction_id: int, questions: list, polling_results: dict
):
    if not polling_results:
        return
    raw_results = polling_results.get("results") or []
    if not raw_results:
        return
    questions_by_uuid = {q.question_uuid: q for q in questions if q.question_uuid}

    for item in raw_results:
        uuid = item.get("uuid")
        if not uuid or uuid not in questions_by_uuid:
            continue
        q = questions_by_uuid[uuid]
        user_answer = item.get("answers", [])
        if user_answer:
            user_answer = user_answer[0]
        else:
            user_answer = None
        is_correct = item.get("correct_result") == 1
        is_finish = item.get("is_finish") == 1
        q.question_text = item.get("text") or item.get("question") or q.question_text
        q.question_type = item.get("type") or q.question_type
        q.user_answer = user_answer.get("text") if user_answer else q.user_answer
        q.correct_answer = item.get("answer") or q.correct_answer
        q.is_correct = is_correct
        q.is_finish = is_finish
        q.question_bbox = item.get("bbox") or q.question_bbox
        q.answer_bbox = user_answer.get("bbox") if user_answer else q.answer_bbox
        q.correct_source = item.get("correct_source") or q.correct_source
        q.analysis = item.get("analysis") or q.analysis

    await db.commit()


async def _poll_and_update_async(
    correction_id: int,
    trace_id: str,
    image_id: str,
    questions: list,
):
    uuids = [q.question_uuid for q in questions if q.question_uuid]
    if not uuids:
        return
    async with async_session_maker() as session:
        try:
            polling_response = await zhipu_service.correct_homework_polling(
                trace_id=trace_id,
                image_id=image_id,
                uuids=uuids,
            )
            logger.info(
                "[correction.polling] correction_id=%s response=%s",
                correction_id,
                polling_response,
            )
            polling_results = _parse_polling_results(polling_response)
            await _apply_polling_results(
                session, correction_id, questions, polling_results
            )
        except Exception:
            logger.exception(
                "[correction.polling] failed correction_id=%s", correction_id
            )


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
    wrong_count: int,
):
    """Create study record for the correction activity"""
    subject_map = {
        "math": "数学",
        "mathematics": "数学",
        "english": "英语",
        "chinese": "语文",
        "physics": "物理",
        "chemistry": "化学",
        "biology": "生物",
        "history": "历史",
        "geography": "地理",
        "politics": "政治",
    }
    subject_label = subject_map.get((subject or "").lower(), subject or "")
    total = correct_count + wrong_count
    accuracy = int(correct_count / total * 100) if total > 0 else 0
    abstract = f"批改{subject_label}作业"

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
        correction.image_id = correction_response.image_id
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
            select(QuestionHistory)
            .where(
                QuestionHistory.correction_id == correction.id,
            )
            .order_by(QuestionHistory.question_index)
        )
        questions = result.scalars().all()

        # 6. Build response
        results = [
            QuestionResult(
                question_index=q.question_index,
                question_detail_id=q.id,
                is_correct=q.is_correct,
                is_finish=q.is_finish,
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
            correction_response.wrong_count,
        )
        await db.commit()

        if (
            correction.correcting_count
            and correction.api_trace_id
            and correction.image_id
        ):
            result = await db.execute(
                select(QuestionHistory).where(
                    QuestionHistory.correction_id == correction.id,
                    QuestionHistory.is_finish != True,
                )
            )
            pending_questions = result.scalars().all()
            if pending_questions:
                asyncio.create_task(
                    _poll_and_update_async(
                        correction_id=correction.id,
                        trace_id=correction.api_trace_id,
                        image_id=correction.image_id,
                        questions=pending_questions,
                    )
                )

        return BaseResponse.success(
            data=CorrectionSubmitData(
                correction_id=correction.id,
                image_url=correction.image_url,
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


@router.get("/detail", response_model=BaseResponse[CorrectionSubmitData])
async def get_correction_detail(
    correction_id: int = Query(..., ge=1),
    current_user: CurrentUser = None,
    db: DbSession = None,
):
    """
    获取批改详情（用于轮询未完成题目）
    """
    result = await db.execute(
        select(HomeworkCorrectionHistory).where(
            HomeworkCorrectionHistory.id == correction_id,
            HomeworkCorrectionHistory.user_id == current_user.id,
            HomeworkCorrectionHistory.is_deleted == False,
        )
    )
    correction = result.scalar_one_or_none()
    if not correction:
        return BaseResponse.error("批改记录不存在")

    # Reload questions for response
    result = await db.execute(
        select(QuestionHistory)
        .where(QuestionHistory.correction_id == correction.id)
        .order_by(QuestionHistory.question_index)
    )
    questions = result.scalars().all()

    correct_count = len([q for q in questions if q.is_finish and q.is_correct])
    wrong_count = len([q for q in questions if q.is_finish and q.is_correct is False])
    correcting_count = len([q for q in questions if not q.is_finish])

    correction.correct_count = correct_count
    correction.wrong_count = wrong_count
    correction.correcting_count = correcting_count
    if correcting_count == 0:
        correction.status = 1
    await db.commit()

    results = [
        QuestionResult(
            question_index=q.question_index,
            question_detail_id=q.id,
            is_correct=q.is_correct,
            is_finish=q.is_finish,
            question_bbox=q.question_bbox,
            answer_bbox=q.answer_bbox,
            user_answer=q.user_answer,
            correct_answer=q.correct_answer if not q.is_correct else None,
        )
        for q in questions
    ]

    return BaseResponse.success(
        data=CorrectionSubmitData(
            correction_id=correction.id,
            image_url=correction.image_url,
            processed_image_url=correction.processed_image_url,
            subject=correction.subject,
            total_questions=correction.total_questions or 0,
            correct_count=correction.correct_count or 0,
            wrong_count=correction.wrong_count or 0,
            results=results,
        )
    )


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
