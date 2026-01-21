from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class StudyRecordCreate(BaseModel):
    """Create study record request (simple mode - with duration)"""
    action: str  # homework/correction/tutoring/chat
    duration: int  # seconds
    abstract: Optional[str] = None
    related_id: Optional[int] = None
    related_type: Optional[str] = None


class StudyRecordStartRequest(BaseModel):
    """Start study record request"""
    action: str  # homework/correction/tutoring/chat


class StudyRecordEndRequest(BaseModel):
    """End study record request"""
    record_id: int
    abstract: Optional[str] = None
    related_id: Optional[int] = None
    related_type: Optional[str] = None  # correction/solving/conversation


class StudyRecordData(BaseModel):
    """Study record response data"""
    record_id: int


class StudyRecordEndData(BaseModel):
    """Study record end response data"""
    record_id: int
    duration: int  # calculated duration in seconds


class StudyRecordInfo(BaseModel):
    """Study record info"""
    id: int
    action: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration: Optional[int] = None
    abstract: Optional[str] = None
    related_id: Optional[int] = None
    related_type: Optional[str] = None
    status: int
    created_at: datetime

    class Config:
        from_attributes = True


class StudyRecordListData(BaseModel):
    """Study record list response data"""
    total: int
    page: int
    page_size: int
    list: List[StudyRecordInfo]
