from fastapi import APIRouter, Query, Path, Request
from sqlalchemy import select, func
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.api.deps import DbSession, Redis, CurrentUser
from app.models.conversation import AIConversationHistory
from app.models.homework import QuestionHistory
from app.models.study import StudyRecord
from app.schemas.base import BaseResponse
from app.schemas.conversation import (
    ConversationCreateRequest,
    ConversationCreateData,
    ConversationEndRequest,
    ConversationEndData,
    ConversationDetailData,
    ConversationHistoryData,
    ConversationHistoryItem,
    MessageInfo,
)
from app.utils.security import create_ws_token
from app.services.llm import llm_service, Message
from app.utils.exceptions import NotFoundException, ValidationException
from app.config import settings

router = APIRouter()

# WebSocket token expiration (2 hours)
WS_TOKEN_EXPIRE_SECONDS = 7200


async def finalize_conversation(
    conversation_id: int,
    user_id: int,
    db: DbSession,
    redis: Redis,
) -> Optional[ConversationEndData]:
    """Persist conversation data and clean up Redis."""
    result = await db.execute(
        select(AIConversationHistory).where(
            AIConversationHistory.id == conversation_id,
            AIConversationHistory.user_id == user_id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        return None

    # Collect messages from Redis
    messages_raw = await redis.lrange(f"conv:messages:{conversation.id}", 0, -1)
    messages = []
    for msg_str in messages_raw:
        try:
            import json
            messages.append(json.loads(msg_str))
        except Exception:
            continue

    # Session metadata
    session = await redis.hgetall(f"conv:session:{conversation.id}")
    started_at = session.get("started_at") if session else None

    from dateutil.parser import parse
    if started_at:
        try:
            start_time = parse(started_at)
        except Exception:
            start_time = conversation.started_at
    else:
        start_time = conversation.started_at

    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    else:
        start_time = start_time.astimezone(timezone.utc)

    duration = int((datetime.now(timezone.utc) - start_time).total_seconds())
    message_count = len(messages)

    # Summarize conversation topic using LLM
    topic = conversation.topic
    if not topic:
        topic = await summarize_conversation_topic(messages) or "AI对话"

    # Update conversation record
    conversation.content = {"messages": messages}
    conversation.message_count = message_count
    conversation.total_duration = duration
    conversation.topic = topic
    conversation.ended_at = datetime.now(timezone.utc)
    conversation.status = "ended"

    # Write study record
    action = "chat" if conversation.type == "chat" else "tutoring"
    record = StudyRecord(
        user_id=user_id,
        action=action,
        start_time=start_time,
        end_time=conversation.ended_at,
        duration=duration,
        abstract=f"AI对话：{topic}",
        related_id=conversation.id,
        related_type="conversation",
        status=1,
    )
    db.add(record)

    await db.commit()

    # Clean up Redis
    await redis.delete(f"conv:session:{conversation.id}")
    await redis.delete(f"conv:messages:{conversation.id}")
    await redis.delete(f"conv:context:{conversation.id}")
    await redis.delete(f"conv:vars:{conversation.id}")
    await redis.delete(f"conv:prompt:{conversation.id}")
    await redis.srem("conv:active_set", str(conversation.id))
    await redis.delete(f"user:active_conv:{user_id}")

    return ConversationEndData(
        duration=duration,
        message_count=message_count,
        topic=topic,
    )


async def summarize_conversation_topic(messages: list) -> Optional[str]:
    """Summarize conversation topic using LLM."""
    if not messages:
        return None

    # Build a compact transcript
    lines = []
    for msg in messages[-20:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if content:
            lines.append(f"{role}: {content}")

    transcript = "\n".join(lines).strip()
    if not transcript:
        return None

    system_prompt = (
        "你是一个对话摘要助手，请用10字以内概括对话主题。"
        "只输出主题短语，不要标点，不要解释。"
    )
    response = await llm_service.chat(
        messages=[
            Message(role="system", content=system_prompt),
            Message(role="user", content=transcript),
        ],
        temperature=0.3,
        max_tokens=32,
        top_p=0.7,
    )
    if not response:
        return None
    return response.content.strip() or None


@router.post("/create", response_model=BaseResponse[ConversationCreateData])
async def create_conversation(
    request: ConversationCreateRequest,
    http_request: Request,
    current_user: CurrentUser,
    db: DbSession,
    redis: Redis,
):
    """
    创建对话会话

    用户进入AI对话页面时调用，创建会话并获取 WebSocket 连接信息
    """
    # 1. Check if user has an active conversation
    active_conv_id = await redis.get(f"user:active_conv:{current_user.id}")
    if active_conv_id:
        old_conv_id = int(active_conv_id)
        await finalize_conversation(old_conv_id, current_user.id, db, redis)

    # 2. Create conversation record in database
    conversation = AIConversationHistory(
        user_id=current_user.id,
        type=request.type,
        status="active",
        started_at=datetime.now(timezone.utc),
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)

    # 3. Initialize Redis data for the conversation
    session_data = {
        "user_id": str(current_user.id),
        "type": request.type,
        "status": "active",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_active_at": datetime.now(timezone.utc).isoformat(),
        "tts_playing": "false",
    }
    await redis.hmset(f"conv:session:{conversation.id}", session_data)
    await redis.expire(f"conv:session:{conversation.id}", WS_TOKEN_EXPIRE_SECONDS)

    # Store context and profile variables
    context_vars = {}
    if request.question_history_id is not None:
        if request.type != "solving":
            raise ValidationException("question_history_id 仅适用于答疑类型")

        question_result = await db.execute(
            select(QuestionHistory).where(
                QuestionHistory.id == request.question_history_id,
                QuestionHistory.user_id == current_user.id,
            )
        )
        question = question_result.scalar_one_or_none()
        if not question:
            raise NotFoundException("题目不存在")

        context_vars["context_text"] = question.question_text or ""
        context_vars["context_image_url"] = question.question_image_url or ""
        if question.subject:
            context_vars["subject"] = question.subject
        if question.user_answer:
            context_vars["user_answer"] = question.user_answer
        if question.correct_answer:
            context_vars["correct_answer"] = question.correct_answer
        context_vars["question_history_id"] = str(question.id)

    if current_user.nickname:
        context_vars["student_name"] = current_user.nickname
    if current_user.grade:
        context_vars["grade"] = current_user.grade
    if context_vars:
        await redis.hmset(f"conv:vars:{conversation.id}", context_vars)
        await redis.expire(f"conv:vars:{conversation.id}", WS_TOKEN_EXPIRE_SECONDS)

    # Add to active set
    await redis.sadd("conv:active_set", str(conversation.id))

    # Set user's active conversation
    await redis.set(
        f"user:active_conv:{current_user.id}",
        str(conversation.id),
        ex=WS_TOKEN_EXPIRE_SECONDS,
    )

    # 4. Generate WebSocket token
    expire_at = datetime.now(timezone.utc) + timedelta(seconds=WS_TOKEN_EXPIRE_SECONDS)
    ws_token = create_ws_token(conversation.id, current_user.id)

    # 5. Build WebSocket URL based on request host
    host = http_request.headers.get("host", "localhost:8000")
    # Use wss for HTTPS, ws for HTTP
    ws_scheme = "wss" if http_request.url.scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{host}/ws/conversation/{conversation.id}"

    return BaseResponse.success(
        data=ConversationCreateData(
            conversation_id=conversation.id,
            ws_url=ws_url,
            token=ws_token,
            expire_at=expire_at,
        )
    )


@router.post("/end", response_model=BaseResponse[ConversationEndData])
async def end_conversation(
    request: ConversationEndRequest,
    current_user: CurrentUser,
    db: DbSession,
    redis: Redis,
):
    """
    结束对话会话

    用户点击「结束对话」或退出页面时调用
    """
    result = await db.execute(
        select(AIConversationHistory).where(
            AIConversationHistory.id == request.conversation_id,
            AIConversationHistory.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise NotFoundException("对话不存在")

    data = await finalize_conversation(
        request.conversation_id,
        current_user.id,
        db,
        redis,
    )
    if not data:
        raise NotFoundException("对话不存在")

    return BaseResponse.success(data=data)


@router.get("/history", response_model=BaseResponse[ConversationHistoryData])
async def get_conversation_history(
    current_user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    type: Optional[str] = Query(default=None),
):
    """
    获取对话历史列表
    """
    # Build query
    query = select(AIConversationHistory).where(
        AIConversationHistory.user_id == current_user.id,
        AIConversationHistory.is_deleted == False,
    )

    # Filter by type if provided
    if type:
        query = query.where(AIConversationHistory.type == type)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination and ordering
    query = query.order_by(AIConversationHistory.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    # Execute query
    result = await db.execute(query)
    conversations = result.scalars().all()

    # Build response
    items = [
        ConversationHistoryItem(
            conversation_id=c.id,
            type=c.type,
            topic=c.topic,
            message_count=c.message_count,
            duration=c.total_duration,
            started_at=c.started_at,
            ended_at=c.ended_at,
        )
        for c in conversations
    ]

    return BaseResponse.success(
        data=ConversationHistoryData(
            total=total,
            page=page,
            page_size=page_size,
            list=items,
        )
    )


@router.get("/detail/{conversation_id}", response_model=BaseResponse[ConversationDetailData])
async def get_conversation_detail(
    conversation_id: int = Path(..., description="对话会话ID"),
    current_user: CurrentUser = None,
    db: DbSession = None,
):
    """
    获取对话详情
    """
    # Query conversation
    result = await db.execute(
        select(AIConversationHistory).where(
            AIConversationHistory.id == conversation_id,
            AIConversationHistory.user_id == current_user.id,
            AIConversationHistory.is_deleted == False,
        )
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise NotFoundException("对话不存在")

    # Parse messages from content
    messages = []
    if conversation.content and "messages" in conversation.content:
        for msg in conversation.content["messages"]:
            messages.append(
                MessageInfo(
                    role=msg.get("role", "user"),
                    type=msg.get("type", "text"),
                    content=msg.get("content", ""),
                    timestamp=msg.get("timestamp", conversation.started_at),
                )
            )

    return BaseResponse.success(
        data=ConversationDetailData(
            conversation_id=conversation.id,
            type=conversation.type,
            topic=conversation.topic,
            duration=conversation.total_duration,
            started_at=conversation.started_at,
            ended_at=conversation.ended_at,
            messages=messages,
        )
    )
