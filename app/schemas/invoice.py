from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class InvoiceBase(BaseModel):
    project_id: int
    owner_id: int
    type: str
    reference_id: Optional[int] = None

    amount: float

    gst_percent: float = 0
    tax_percent: float = 0

    description: Optional[str] = None


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(BaseModel):
    amount: Optional[float] = None
    gst_percent: Optional[float] = None
    tax_percent: Optional[float] = None
    status: Optional[str] = None
    description: Optional[str] = None


class InvoiceOut(BaseModel):
    id: int
    project_id: int
    owner_id: int
    type: str
    reference_id: Optional[int]

    amount: float
    gst_percent: float
    gst_amount: float
    tax_percent: float
    tax_amount: float
    total_amount: float

    status: str
    description: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True