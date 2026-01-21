from pydantic import BaseModel
from typing import TypeVar, Generic, Optional, Any, List
from enum import IntEnum


class ErrorCode(IntEnum):
    """Error codes as defined in API.md"""
    SUCCESS = 0
    PARAM_ERROR = 1001
    AUTH_FAILED = 1002
    PERMISSION_DENIED = 1003
    USER_NOT_FOUND = 2001
    PASSWORD_ERROR = 2002
    ACCOUNT_DISABLED = 2003
    RESOURCE_NOT_FOUND = 3001
    SERVER_ERROR = 5001
    EXTERNAL_API_ERROR = 5002


ERROR_MESSAGES = {
    ErrorCode.SUCCESS: "success",
    ErrorCode.PARAM_ERROR: "参数错误",
    ErrorCode.AUTH_FAILED: "认证失败 / Token无效",
    ErrorCode.PERMISSION_DENIED: "权限不足",
    ErrorCode.USER_NOT_FOUND: "用户不存在",
    ErrorCode.PASSWORD_ERROR: "密码错误",
    ErrorCode.ACCOUNT_DISABLED: "账号已被禁用",
    ErrorCode.RESOURCE_NOT_FOUND: "资源不存在",
    ErrorCode.SERVER_ERROR: "服务器内部错误",
    ErrorCode.EXTERNAL_API_ERROR: "第三方API调用失败",
}


DataT = TypeVar("DataT")


class BaseResponse(BaseModel, Generic[DataT]):
    """Base response model for all API responses"""
    code: int = ErrorCode.SUCCESS
    message: str = "success"
    data: Optional[DataT] = None

    @classmethod
    def success(cls, data: Optional[DataT] = None, message: str = "success") -> "BaseResponse[DataT]":
        return cls(code=ErrorCode.SUCCESS, message=message, data=data)

    @classmethod
    def error(cls, code: ErrorCode, message: Optional[str] = None) -> "BaseResponse[None]":
        return cls(
            code=code,
            message=message or ERROR_MESSAGES.get(code, "Unknown error"),
            data=None,
        )


class PaginatedData(BaseModel, Generic[DataT]):
    """Paginated data wrapper"""
    total: int
    page: int
    page_size: int
    list: List[DataT]


class PaginatedResponse(BaseResponse[PaginatedData[DataT]], Generic[DataT]):
    """Paginated response model"""
    pass


class PaginationParams(BaseModel):
    """Pagination parameters"""
    page: int = 1
    page_size: int = 10

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size
