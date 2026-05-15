from datetime import datetime
from typing import Optional, List

from app.schemas.base import BaseSchema
from app.core.enums import DocumentStatus


class DocumentCreate(BaseSchema):
    project_id: int
    title: str
    document_type: Optional[str] = None
    file_url: Optional[str] = None
    file_size: Optional[int] = None
    version: Optional[str] = "v1.0"
    is_folder: bool = False
    parent_id: Optional[int] = None
    uploaded_by_user_id: Optional[int] = None
    remarks: Optional[str] = None


class DocumentUpdate(BaseSchema):
    title: Optional[str] = None
    document_type: Optional[str] = None
    file_url: Optional[str] = None
    file_size: Optional[int] = None
    version: Optional[str] = None
    status: Optional[DocumentStatus] = None
    remarks: Optional[str] = None


class DocumentOut(BaseSchema):
    id: int
    project_id: int
    project_name: Optional[str] = None
    title: str
    document_type: Optional[str]
    file_url: Optional[str]
    file_size: Optional[int]
    version: Optional[str]
    status: DocumentStatus
    is_folder: bool
    parent_id: Optional[int]
    uploaded_by_user_id: Optional[int]
    uploaded_at: datetime
    remarks: Optional[str]


class DocumentStats(BaseSchema):
    total_storage_bytes: int
    total_storage_gb: float
    pending_approvals: int
    total_documents: int