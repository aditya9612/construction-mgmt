from pydantic import BaseModel
from datetime import date
from typing import Optional
from pydantic import field_validator
from decimal import Decimal


class RABillBase(BaseModel):
    project_id: int
    contractor_id: int
    bill_number: str
    work_description: str
    quantity: Decimal
    rate: Decimal
    deductions: Decimal = Decimal("0")
    gst_percent: Decimal = Decimal("0")
    bill_date: date

    @field_validator("quantity", "rate", "deductions", "gst_percent")
    def validate_positive(cls, v):
        if v is not None and v < 0: 
            raise ValueError("Value cannot be negative")
        return v


class RABillCreate(RABillBase):
    pass


class RABillUpdate(BaseModel):
    work_description: Optional[str] = None
    quantity: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    deductions: Optional[Decimal] = None
    gst_percent: Optional[Decimal] = None
    status: Optional[str] = None
    bill_date: Optional[date] = None

    @field_validator("bill_date")
    def validate_bill_date(cls, v):
        if v and v > date.today():
            raise ValueError("Bill date cannot be in future")
        return v

    @field_validator("quantity", "rate", "deductions", "gst_percent")
    def validate_positive(cls, v):
        if v is not None and v < 0:
            raise ValueError("Value cannot be negative")
        return v


class RABillOut(BaseModel):
    id: int
    project_id: int
    contractor_id: int
    bill_number: str
    work_description: str
    quantity: Decimal
    rate: Decimal
    gross_amount: Decimal
    deductions: Decimal
    net_amount: Decimal
    gst_percent: Decimal
    total_amount: Decimal
    bill_date: date
    status: str

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}  