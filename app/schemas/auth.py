from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class LoginRequest(BaseModel):
    """Login request schema"""
    phone: str
    password: str
    device_id: str


class LoginData(BaseModel):
    """Login response data"""
    token: str
    expires_at: datetime
    user_id: int
    nickname: Optional[str] = None
    avatar_url: Optional[str] = None
    grade: Optional[str] = None
    is_bound: bool


class UserInfo(BaseModel):
    """User info schema"""
    user_id: int
    phone: str
    nickname: Optional[str] = None
    avatar_url: Optional[str] = None
    grade: Optional[str] = None
    personality: Optional[str] = None
    study_profile: Optional[dict] = None

    class Config:
        from_attributes = True
