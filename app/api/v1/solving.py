from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime, timezone
import json

from app.api.deps import DbSession, CurrentUser
from app.database import async_session_maker
from app.models.homework import QuestionHistory
from app.models.study import StudyRecord, KnowledgePointRecord
from app.schemas.base import BaseResponse
from app.schemas.solving import (
    SolvingRequest,
    SolvingData,
    SolvingHistoryData,
    SolvingHistoryItem,
)
from app.services.zhipu import zhipu_service
from app.utils.exceptions import ExternalAPIException

router = APIRouter()


async def create_study_record_for_solving(
    db,
    user_id: int,
    question_history_id: int,
):
    """Create study record for the solving activity"""
    record = StudyRecord(
        user_id=user_id,
        action="tutoring",
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        duration=30,  # Estimated duration
        abstract="拍照答疑",
        related_id=question_history_id,
        related_type="solving",
        status=1,
    )
    db.add(record)


@router.post("/submit", response_model=BaseResponse[SolvingData])
async def submit_solving(
    request: SolvingRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """
    提交拍照解题

    支持图片和/或文字输入，调用智谱API获取解答

    流程:
    1. 创建题目记录
    2. 调用智谱解题API
    3. 更新记录
    4. 记录学习时长
    """
    # 1. Create question history record
    question = QuestionHistory(
        user_id=current_user.id,
        source="solving",
        question_index=0,
        question_image_url=request.image_url,
    )
    db.add(question)
    await db.commit()
    await db.refresh(question)

    try:
        # 2. Call Zhipu problem solving API
        solving_response = await zhipu_service.solve_problem(
            image_url=request.image_url,
            text="请帮我解答这道题",
        )

        # 3. Update question record
        if solving_response.question_text:
            question.question_text = solving_response.question_text
        question.analysis = solving_response.analysis_text or solving_response.answer
        question.correct_answer = solving_response.final_answer
        question.subject = solving_response.course
        question.knowledge_points = solving_response.knowledge_points

        # 4. Update knowledge points and create study record
        await update_knowledge_points_for_solving(
            db,
            current_user.id,
            solving_response.course,
            solving_response.knowledge_points,
        )
        await create_study_record_for_solving(
            db,
            current_user.id,
            question.id,
        )
        await db.commit()

        return BaseResponse.success(
            data=SolvingData(
                question_history_id=question.id,
            )
        )

    except Exception as e:
        raise ExternalAPIException(f"解题失败: {str(e)}")


@router.post("/submit/stream")
async def submit_solving_stream(
    request: SolvingRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """
    提交拍照解题（流式响应）

    返回 Server-Sent Events 格式的流式响应
    """
    # Create question history record
    question = QuestionHistory(
        user_id=current_user.id,
        source="solving",
        question_index=0,
        question_image_url=request.image_url,
    )
    db.add(question)
    await db.commit()
    await db.refresh(question)

    async def generate():
        full_answer = ""
        try:
            async for chunk in zhipu_service.solve_problem_stream(
                image_url=request.image_url,
                text="请帮我解答这道题",
            ):
                full_answer += chunk
                yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"

            # Update question record with parsed answer
            sections = zhipu_service._parse_solution_sections(full_answer)
            knowledge_points = []
            for key in ("知识点总结", "知识点"):
                if key in sections:
                    knowledge_points = zhipu_service._parse_knowledge_points(sections[key])
                    break

            async with async_session_maker() as session:
                result = await session.execute(
                    select(QuestionHistory).where(QuestionHistory.id == question.id)
                )
                question_row = result.scalar_one_or_none()
                if question_row:
                    if sections.get("题目") and not question_row.question_text:
                        question_row.question_text = sections["题目"]
                    question_row.analysis = sections.get("解析") or full_answer
                    question_row.correct_answer = sections.get("答案")
                    question_row.knowledge_points = knowledge_points or question_row.knowledge_points
                    await update_knowledge_points_for_solving(
                        session,
                        current_user.id,
                        question_row.subject,
                        question_row.knowledge_points or [],
                    )
                    await create_study_record_for_solving(
                        session,
                        current_user.id,
                        question_row.id,
                    )
                    await session.commit()

            yield f"data: {json.dumps({'done': True, 'question_history_id': question.id}, ensure_ascii=False)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/history", response_model=BaseResponse[SolvingHistoryData])
async def get_solving_history(
    current_user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
):
    """
    获取答疑历史列表
    """
    # Build query
    query = select(QuestionHistory).where(
        QuestionHistory.user_id == current_user.id,
        QuestionHistory.source == "solving",
    )

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination and ordering
    query = query.order_by(QuestionHistory.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    # Execute query
    result = await db.execute(query)
    questions = result.scalars().all()

    # Build response
    items = [
        SolvingHistoryItem(
            id=q.id,
            image_url=q.question_image_url,
            question_text=q.question_text,
            answer=q.analysis,
            course=q.subject,
            created_at=q.created_at,
        )
        for q in questions
    ]

    return BaseResponse.success(
        data=SolvingHistoryData(
            total=total,
            page=page,
            page_size=page_size,
            list=items,
        )
    )
async def update_knowledge_points_for_solving(
    db,
    user_id: int,
    subject: Optional[str],
    knowledge_points: list,
):
    """Update knowledge point records based on solving results."""
    if not knowledge_points:
        return

    for point in knowledge_points:
        result = await db.execute(
            select(KnowledgePointRecord).where(
                KnowledgePointRecord.user_id == user_id,
                KnowledgePointRecord.topic_name == point,
            )
        )
        record = result.scalar_one_or_none()
        if record:
            record.question_count += 1
            if subject:
                record.subject = subject
        else:
            record = KnowledgePointRecord(
                user_id=user_id,
                topic_name=point,
                subject=subject,
                question_count=1,
            )
            db.add(record)
