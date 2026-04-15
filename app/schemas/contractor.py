from pydantic import BaseModel, Field, field_validator
from typing import Optional
from decimal import Decimal


class ContractorBase(BaseModel):
    name: str = Field(..., min_length=1)
    work_type: str = Field(..., min_length=1)
    contact_number: str = Field(..., min_length=10, max_length=15)
    gst_number: Optional[str] = None
    rate_type: str = Field(..., min_length=1)

    total_work_assigned: Decimal = Field(default=Decimal("0"), ge=0)
    payment_given: Decimal = Field(default=Decimal("0"), ge=0)

    bank_details: Optional[str] = None

    @field_validator("contact_number")
    def validate_phone(cls, v):
        if not v.isdigit() or len(v) != 10:
            raise ValueError("Invalid phone number")
        return v


class ContractorCreate(ContractorBase):
    pass


class ContractorUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    work_type: Optional[str] = Field(default=None, min_length=1)
    contact_number: Optional[str] = Field(default=None, min_length=10, max_length=15)
    gst_number: Optional[str] = None
    rate_type: Optional[str] = Field(default=None, min_length=1)

    total_work_assigned: Optional[Decimal] = Field(default=None, ge=0)

    bank_details: Optional[str] = None


class ContractorOut(BaseModel):
    id: int
    contractor_id: str
    name: str
    work_type: str
    contact_number: str
    gst_number: Optional[str]
    rate_type: str

    total_work_assigned: Decimal
    payment_given: Decimal
    payment_pending: Decimal

    bank_details: Optional[str]

    class Config:
        from_attributes = True