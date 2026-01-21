from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse
from sqlalchemy import select
import json

from app.api.deps import DbSession, CurrentUser
from app.models.homework import QuestionHistory, HomeworkCorrectionHistory
from app.database import async_session_maker
from app.schemas.base import BaseResponse
from app.schemas.question import QuestionDetailData
from app.services.zhipu import zhipu_service
from app.utils.exceptions import NotFoundException

router = APIRouter()


@router.get("/detail/{question_detail_id}", response_model=BaseResponse[QuestionDetailData])
async def get_question_detail(
    question_detail_id: int = Path(..., description="题目明细ID"),
    current_user: CurrentUser = None,
    db: DbSession = None,
):
    """
    获取题目详情

    根据题目明细ID获取完整信息
    """
    # Query question detail
    result = await db.execute(
        select(QuestionHistory).where(
            QuestionHistory.id == question_detail_id,
            QuestionHistory.user_id == current_user.id,
        )
    )
    question = result.scalar_one_or_none()

    if not question:
        raise NotFoundException("题目不存在")

    return BaseResponse.success(
        data=QuestionDetailData(
            id=question.id,
            correction_id=question.correction_id,
            source=question.source,
            subject=question.subject,
            question_index=question.question_index,
            question_text=question.question_text,
            question_image_url=question.question_image_url,
            user_answer=question.user_answer,
            correct_answer=question.correct_answer,
            is_correct=question.is_correct,
            analysis=question.analysis,
            knowledge_points=question.knowledge_points,
        )
    )


@router.get("/detail/{question_detail_id}/analysis/stream")
async def get_question_analysis_stream(
    question_detail_id: int = Path(..., description="题目明细ID"),
    current_user: CurrentUser = None,
    db: DbSession = None,
):
    """
    获取题目解析（流式响应）

    对于批改中的题目，调用智谱API获取详细解析
    返回 Server-Sent Events 格式的流式响应
    """
    # Query question detail
    result = await db.execute(
        select(QuestionHistory).where(
            QuestionHistory.id == question_detail_id,
            QuestionHistory.user_id == current_user.id,
        )
    )
    question = result.scalar_one_or_none()

    if not question:
        raise NotFoundException("题目不存在")

    # If analysis already exists, return it directly
    if question.analysis:
        async def generate_existing():
            yield f"data: {json.dumps({'text': question.analysis}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            generate_existing(),
            media_type="text/event-stream",
        )

    # For correction questions, get analysis from Zhipu
    if question.source == "correction" and question.correction_id:
        # Get correction info
        correction_result = await db.execute(
            select(HomeworkCorrectionHistory).where(
                HomeworkCorrectionHistory.id == question.correction_id,
            )
        )
        correction = correction_result.scalar_one_or_none()

        raw_response = (correction.raw_response or {}) if correction else {}
        image_id = raw_response.get("data", {}).get("image_id", "")
        trace_id = correction.api_trace_id if correction else None
        question_text = question.question_text or ""
        question_uuid = question.question_uuid or ""

        if correction and trace_id and image_id:
            async def generate_analysis():
                full_analysis = ""
                try:
                    async for chunk in zhipu_service.get_question_analysis(
                        question=question_text,
                        image_id=image_id,
                        uuid=question_uuid,
                        trace_id=trace_id,
                    ):
                        full_analysis += chunk
                        yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"

                    # Save analysis to database
                    async with async_session_maker() as session:
                        result = await session.execute(
                            select(QuestionHistory).where(QuestionHistory.id == question.id)
                        )
                        question_row = result.scalar_one_or_none()
                        if question_row:
                            question_row.analysis = full_analysis
                            await session.commit()

                    yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

            return StreamingResponse(
                generate_analysis(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

    # Fallback: no analysis available
    async def generate_empty():
        yield f"data: {json.dumps({'text': '暂无解析', 'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate_empty(),
        media_type="text/event-stream",
    )

