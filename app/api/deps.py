from typing import Annotated, Optional
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.redis_client import get_redis, RedisClient
from app.models.user import StudentUser
from app.utils.security import decode_access_token
from app.utils.exceptions import AuthException


async def get_current_user(
    authorization: Annotated[Optional[str], Header()] = None,
    db: AsyncSession = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> StudentUser:
    """Get current authenticated user from JWT token"""
    if not authorization:
        raise AuthException("未提供认证信息")

    # Extract token from "Bearer <token>"
    if not authorization.startswith("Bearer "):
        raise AuthException("无效的认证格式")

    token = authorization[7:]

    # Check if token is blacklisted
    blacklisted = await redis.exists(f"token:blacklist:{token}")
    if blacklisted:
        raise AuthException("Token已失效")

    # Decode token
    payload = decode_access_token(token)
    if not payload:
        raise AuthException("无效的Token")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthException("无效的Token")

    # Get user from database
    result = await db.execute(
        select(StudentUser).where(
            StudentUser.id == int(user_id),
            StudentUser.is_deleted == False,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise AuthException("用户不存在")

    return user


# Type alias for dependency injection
CurrentUser = Annotated[StudentUser, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
Redis = Annotated[RedisClient, Depends(get_redis)]
