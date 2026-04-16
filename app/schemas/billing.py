from pydantic import BaseModel, field_validator
from datetime import date
from typing import Optional
from decimal import Decimal


class RABillBase(BaseModel):
    project_id: int
    contractor_id: int
    work_order_id: Optional[int] = None
    bill_number: str
    work_description: str
    quantity: Decimal
    rate: Decimal
    deductions: Decimal = Decimal("0")
    gst_percent: Decimal = Decimal("0")
    bill_date: date

    @field_validator("quantity", "rate")
    def validate_positive_required(cls, v):
        if v <= 0:
            raise ValueError("Must be greater than 0")
        return v

    @field_validator("deductions", "gst_percent")
    def validate_non_negative(cls, v):
        if v < 0:
            raise ValueError("Cannot be negative")
        return v


class RABillCreate(RABillBase):
    pass


class RABillUpdate(BaseModel):
    work_order_id: Optional[int] = None
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
            raise ValueError("Future bill date not allowed")
        return v


class RABillOut(BaseModel):
    id: int
    project_id: int
    contractor_id: int
    work_order_id: Optional[int]
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
    progress_percent: Optional[float] = None
    total_billed_quantity: Optional[float] = None
    remaining_quantity: Optional[float] = None
    available_to_bill: Optional[float] = None

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}
