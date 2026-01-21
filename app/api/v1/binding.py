from fastapi import APIRouter
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
import uuid

from app.api.deps import DbSession, Redis, CurrentUser
from app.models.binding import ParentStudentBinding
from app.models.user import ParentUser
from app.schemas.base import BaseResponse
from app.schemas.binding import QRCodeData, BindingStatusData, BindingInfo
from app.services.qrcode import qrcode_service
from app.config import settings

router = APIRouter()

# QR code expiration time (5 minutes)
QRCODE_EXPIRE_SECONDS = 300


@router.get("/qrcode", response_model=BaseResponse[QRCodeData])
async def get_binding_qrcode(
    current_user: CurrentUser,
    redis: Redis,
):
    """
    获取绑定二维码

    生成一个供家长扫描的绑定二维码，有效期5分钟
    二维码内容为一个URL，家长扫描后可完成绑定
    """
    # Generate unique QR code ID
    qrcode_id = f"qr_{uuid.uuid4().hex[:12]}"

    # Calculate expiration time
    expire_at = datetime.now(timezone.utc) + timedelta(seconds=QRCODE_EXPIRE_SECONDS)

    # Store QR code info in Redis
    await redis.set_json(
        f"binding:qrcode:{qrcode_id}",
        {
            "student_id": current_user.id,
            "student_nickname": current_user.nickname,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        ex=QRCODE_EXPIRE_SECONDS,
    )

    # Generate the binding URL (this would be a deep link for the parent app)
    binding_url = f"https://your-domain.com/binding?code={qrcode_id}"

    # Generate QR code image and get URL
    qrcode_url = await qrcode_service.generate_and_upload_qrcode(
        data=binding_url,
        qrcode_id=qrcode_id,
    )

    return BaseResponse.success(
        data=QRCodeData(
            qrcode_id=qrcode_id,
            qrcode_url=qrcode_url,
            expire_at=expire_at,
        )
    )


@router.get("/status", response_model=BaseResponse[BindingStatusData])
async def get_binding_status(
    current_user: CurrentUser,
    db: DbSession,
):
    """
    查询绑定状态

    检查当前用户是否已绑定家长，返回绑定列表
    前端可轮询此接口检测绑定是否完成
    """
    # Query active bindings
    result = await db.execute(
        select(ParentStudentBinding, ParentUser)
        .join(ParentUser, ParentStudentBinding.parent_id == ParentUser.id)
        .where(
            ParentStudentBinding.student_id == current_user.id,
            ParentStudentBinding.status == 1,
        )
    )
    bindings = result.all()

    # Build response
    binding_list = [
        BindingInfo(
            parent_id=binding.ParentStudentBinding.parent_id,
            nickname=binding.ParentUser.nickname,
            relation=binding.ParentStudentBinding.relation,
            bound_at=binding.ParentStudentBinding.bound_at,
        )
        for binding in bindings
    ]

    return BaseResponse.success(
        data=BindingStatusData(
            is_bound=len(binding_list) > 0,
            bindings=binding_list,
        )
    )
