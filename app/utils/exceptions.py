from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from app.schemas.base import ErrorCode, ERROR_MESSAGES


class APIException(Exception):
    """Custom API exception"""

    def __init__(
        self,
        code: ErrorCode = ErrorCode.SERVER_ERROR,
        message: str = None,
        status_code: int = 400,
    ):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "Unknown error")
        self.status_code = status_code
        super().__init__(self.message)


class AuthException(APIException):
    """Authentication exception"""

    def __init__(self, message: str = None):
        super().__init__(
            code=ErrorCode.AUTH_FAILED,
            message=message or "认证失败",
            status_code=401,
        )


class PermissionException(APIException):
    """Permission denied exception"""

    def __init__(self, message: str = None):
        super().__init__(
            code=ErrorCode.PERMISSION_DENIED,
            message=message or "权限不足",
            status_code=403,
        )


class NotFoundException(APIException):
    """Resource not found exception"""

    def __init__(self, message: str = None):
        super().__init__(
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message=message or "资源不存在",
            status_code=404,
        )


class ValidationException(APIException):
    """Validation exception"""

    def __init__(self, message: str = None):
        super().__init__(
            code=ErrorCode.PARAM_ERROR,
            message=message or "参数错误",
            status_code=400,
        )


class ExternalAPIException(APIException):
    """External API exception"""

    def __init__(self, message: str = None):
        super().__init__(
            code=ErrorCode.EXTERNAL_API_ERROR,
            message=message or "第三方API调用失败",
            status_code=502,
        )


async def api_exception_handler(request: Request, exc: APIException) -> JSONResponse:
    """Handler for APIException"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "data": None,
        },
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handler for generic exceptions"""
    return JSONResponse(
        status_code=500,
        content={
            "code": ErrorCode.SERVER_ERROR,
            "message": str(exc) if request.app.debug else "服务器内部错误",
            "data": None,
        },
    )
