from fastapi import APIRouter, Header
from sqlalchemy import select, exists
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.api.deps import DbSession, Redis, CurrentUser
from app.models.user import StudentUser
from app.models.binding import ParentStudentBinding
from app.schemas.base import BaseResponse
from app.schemas.auth import LoginRequest, LoginData
from app.utils.security import verify_password, create_access_token, decode_access_token
from app.utils.exceptions import AuthException
from app.config import settings

router = APIRouter()


@router.post("/login", response_model=BaseResponse[LoginData])
async def login(
    request: LoginRequest,
    db: DbSession,
):
    """
    用户登录

    - 校验手机号和密码
    - 更新最后登录时间和设备ID
    - 检查是否已绑定家长
    - 生成JWT Token
    """
    # 1. Query user by phone
    result = await db.execute(
        select(StudentUser).where(
            StudentUser.phone == request.phone,
            StudentUser.is_deleted == False,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise AuthException(message="用户不存在")

    # 2. Verify password
    if not verify_password(request.password, user.password_hash):
        raise AuthException(message="密码错误")

    # 3. Update last login time and device_id
    user.last_login_at = datetime.now(timezone.utc)
    user.device_id = request.device_id
    await db.commit()

    # 4. Check if user has any active binding
    binding_exists = await db.execute(
        select(
            exists().where(
                ParentStudentBinding.student_id == user.id,
                ParentStudentBinding.status == 1,
            )
        )
    )
    is_bound = binding_exists.scalar()

    # 5. Generate JWT token
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )

    # 6. Return response
    return BaseResponse.success(
        data=LoginData(
            token=token,
            expires_at=expires_at,
            user_id=user.id,
            nickname=user.nickname,
            avatar_url=user.avatar_url,
            grade=user.grade,
            is_bound=is_bound,
        )
    )


@router.post("/logout", response_model=BaseResponse[None])
async def logout(
    current_user: CurrentUser,
    redis: Redis,
    authorization: Optional[str] = Header(None),
):
    """
    退出登录

    - 将Token加入黑名单，过期时间 = Token剩余有效期
    """
    if not authorization or not authorization.startswith("Bearer "):
        return BaseResponse.success(message="退出成功")

    token = authorization[7:]

    # Decode token to get expiration time
    payload = decode_access_token(token)
    if payload and "exp" in payload:
        # Calculate remaining TTL
        exp_timestamp = payload["exp"]
        now_timestamp = datetime.now(timezone.utc).timestamp()
        remaining_ttl = int(exp_timestamp - now_timestamp)

        if remaining_ttl > 0:
            # Add token to blacklist with remaining TTL
            await redis.set(
                f"token:blacklist:{token}",
                "1",
                ex=remaining_ttl,
            )

    return BaseResponse.success(message="退出成功")
