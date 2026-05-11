from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class AgreementBase(BaseModel):
    project_id: Optional[int] = None
    owner_id: int
    type: str
    status: str = "Active"

class AgreementCreate(AgreementBase):
    pass

class AgreementOut(AgreementBase):
    id: int
    document_id: str
    project_name: Optional[str] = None
    owner_name: Optional[str] = None
    uploaded_at: datetime
    file_url: str

    class Config:
        from_attributes = True

class AgreementStats(BaseModel):
    total_agreements: int
    active_contracts: int
    storage_used: str
    missing_docs: int
    recent_uploads: int
