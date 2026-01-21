from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.schemas.base import BaseResponse
from app.schemas.upload import UploadTokenRequest, UploadTokenData
from app.services.oss import oss_service
from app.utils.exceptions import ValidationException

router = APIRouter()


@router.post("/token", response_model=BaseResponse[UploadTokenData])
async def get_upload_token(
    request: UploadTokenRequest,
    current_user: CurrentUser,
):
    """
    获取上传凭证

    获取OSS上传凭证（STS临时凭证），前端直传OSS

    支持的文件类型:
    - image: 图片文件 (jpg, png, gif, webp等)
    - audio: 音频文件 (mp3, wav, m4a等)
    - video: 视频文件 (mp4, mov等)
    """
    allowed_types = {"image", "audio", "video"}
    if request.file_type not in allowed_types:
        raise ValidationException("不支持的文件类型")

    file_ext = request.file_ext.lower().lstrip(".")
    if not file_ext or len(file_ext) > 10:
        raise ValidationException("无效的文件扩展名")

    # Get upload credentials from OSS service
    credentials = await oss_service.get_upload_credentials(
        file_type=request.file_type,
        file_ext=file_ext,
    )

    return BaseResponse.success(
        data=UploadTokenData(
            upload_url=credentials["upload_url"],
            file_key=credentials["file_key"],
            file_url=credentials["file_url"],
            bucket=credentials.get("bucket"),
            region=credentials.get("region"),
            access_key_id=credentials.get("access_key_id"),
            access_key_secret=credentials.get("access_key_secret"),
            security_token=credentials.get("security_token"),
            expiration=credentials.get("expiration"),
        )
    )
