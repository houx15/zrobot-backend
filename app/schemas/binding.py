from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class QRCodeData(BaseModel):
    """QR code response data"""
    qrcode_id: str
    qrcode_url: str
    expire_at: datetime


class BindingInfo(BaseModel):
    """Binding info schema"""
    parent_id: int
    nickname: Optional[str] = None
    relation: Optional[str] = None
    bound_at: datetime


class BindingStatusData(BaseModel):
    """Binding status response data"""
    is_bound: bool
    bindings: List[BindingInfo] = []
