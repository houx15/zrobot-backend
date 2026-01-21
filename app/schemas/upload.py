from pydantic import BaseModel
from typing import Optional


class UploadTokenRequest(BaseModel):
    """Upload token request"""
    file_type: str  # image/audio/video
    file_ext: str  # jpg/png/mp3 etc.


class UploadTokenData(BaseModel):
    """Upload token response data with STS credentials"""
    upload_url: str
    file_key: str
    file_url: str
    bucket: Optional[str] = None
    region: Optional[str] = None
    # STS credentials
    access_key_id: Optional[str] = None
    access_key_secret: Optional[str] = None
    security_token: Optional[str] = None
    expiration: Optional[str] = None
