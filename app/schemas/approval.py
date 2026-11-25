from pydantic import BaseModel, ConfigDict
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
    requested_by_name: Optional[str] = None
    approved_by: Optional[int] = None
    approved_by_name: Optional[str] = None
    remarks: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
