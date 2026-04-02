from pydantic import BaseModel
from datetime import date
from typing import Optional


class RABillBase(BaseModel):
    project_id: int
    contractor_id: int
    bill_number: str
    work_description: str
    quantity: float
    rate: float
    deductions: float = 0
    gst_percent: float = 0
    bill_date: date


class RABillCreate(RABillBase):
    pass


class RABillUpdate(BaseModel):
    work_description: Optional[str] = None
    quantity: Optional[float] = None
    rate: Optional[float] = None
    deductions: Optional[float] = None
    gst_percent: Optional[float] = None
    status: Optional[str] = None


class RABillOut(BaseModel):
    id: int
    project_id: int
    contractor_id: int
    bill_number: str
    work_description: str
    quantity: float
    rate: float
    gross_amount: float
    deductions: float
    net_amount: float
    gst_percent: float
    total_amount: float
    bill_date: date
    status: str

    class Config:
        from_attributes = True