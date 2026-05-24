from pydantic import BaseModel, field_validator
from datetime import date
from typing import Optional
from decimal import Decimal
from app.core.validators import (
    validate_positive_required,
    validate_non_negative,
    validate_bill_date,
)

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
    @classmethod
    def positive_validator(cls, v):
        return validate_positive_required(v)


    @field_validator("deductions", "gst_percent")
    @classmethod
    def non_negative_validator(cls, v):
        return validate_non_negative(v)


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

    @field_validator("quantity", "rate")
    @classmethod
    def positive_validator(cls, v):
        if v is None:
            return v
        return validate_positive_required(v)


    @field_validator("deductions", "gst_percent")
    @classmethod
    def non_negative_validator(cls, v):
        if v is None:
            return v
        return validate_non_negative(v)


    @field_validator("bill_date")
    @classmethod
    def bill_date_validator(cls, v):
        return validate_bill_date(v)

class RABillOut(BaseModel):
    id: int
    project_id: int
    contractor_id: int
    work_order_id: Optional[int]

    # Make optional with default None so validation succeeds
    # even if the SQLAlchemy model does not have this attribute.
    quotation_id: Optional[int] = None

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
