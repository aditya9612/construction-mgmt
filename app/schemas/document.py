from datetime import datetime
from typing import Optional

from app.schemas.base import BaseSchema


class DocumentCreate(BaseSchema):
    project_id: int
    title: str
    document_type: Optional[str] = None
    file_url: Optional[str] = None
    uploaded_by_user_id: Optional[int] = None


class DocumentUpdate(BaseSchema):
    title: Optional[str] = None
    document_type: Optional[str] = None
    file_url: Optional[str] = None
    uploaded_by_user_id: Optional[int] = None


class DocumentOut(BaseSchema):
    id: int
    project_id: int
    title: str
    document_type: Optional[str]
    file_url: Optional[str]
    uploaded_by_user_id: Optional[int]
    uploaded_at: datetime

