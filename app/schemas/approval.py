from pydantic import BaseModel
from typing import Optional


class ApprovalCreate(BaseModel):
    entity_type: str
    entity_id: int
    remarks: Optional[str] = None


class ApprovalAction(BaseModel):
    remarks: Optional[str] = None


class ApprovalOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    status: str
    requested_by: int
    approved_by: Optional[int]
    remarks: Optional[str]

    class Config:
        from_attributes = True