from pydantic import BaseModel
from typing import Optional


class ContractorBase(BaseModel):
    contractor_id: str
    name: str
    work_type: str
    contact_number: str
    gst_number: Optional[str] = None
    rate_type: str
    total_work_assigned: float = 0
    payment_given: float = 0
    bank_details: str


class ContractorCreate(ContractorBase):
    pass


class ContractorUpdate(BaseModel):
    name: Optional[str] = None
    work_type: Optional[str] = None
    contact_number: Optional[str] = None
    gst_number: Optional[str] = None
    rate_type: Optional[str] = None
    total_work_assigned: Optional[float] = None
    payment_given: Optional[float] = None
    bank_details: Optional[str] = None


class ContractorOut(ContractorBase):
    id: int
    payment_pending: float

    class Config:
        from_attributes = True