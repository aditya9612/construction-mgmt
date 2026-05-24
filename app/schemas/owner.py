from datetime import date
from typing import Optional
from pydantic import Field, EmailStr, field_validator
from app.schemas.base import BaseSchema
import re


# -------------------------
# CREATE
# -------------------------
class OwnerCreate(BaseSchema):
    owner_name: str = Field(..., min_length=3, max_length=100)
    mobile: str = Field(..., pattern=r"^[0-9]{10}$")
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=255)
    pan: Optional[str] = None
    satisfaction_score: float = Field( 0.0, ge=0, le=100 )

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, value):
        if value is None:
            return value
        pattern = r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"
        if not re.match(pattern, value):
            raise ValueError("Invalid PAN format (ABCDE1234F)")
        return value


# -------------------------
# UPDATE
# -------------------------
class OwnerUpdate(BaseSchema):
    owner_name: Optional[str] = Field(None, min_length=3, max_length=100)
    mobile: Optional[str] = Field(None, pattern=r"^[0-9]{10}$")
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=255)
    pan: Optional[str] = None
    satisfaction_score: float = Field( 0.0, ge=0, le=100 )

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, value):
        if value is None:
            return value
        pattern = r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"
        if not re.match(pattern, value):
            raise ValueError("Invalid PAN format (ABCDE1234F)")
        return value


# -------------------------
# RESPONSE
# -------------------------
class OwnerOut(BaseSchema):
    id: int
    owner_code: str
    owner_name: str
    mobile: str
    email: Optional[EmailStr]
    address: Optional[str]
    pan: Optional[str]
    satisfaction_score: Optional[float] = 0.0

    class Config:
        from_attributes = True


# =========================
# OWNER TRANSACTION
# =========================

class OwnerTransactionOut(BaseSchema):
    id: int
    owner_id: int
    project_id: int
    type: str
    amount: float
    reference_type: str
    reference_id: Optional[int]
    description: Optional[str]

    class Config:
        from_attributes = True


class OwnerLedgerResponse(BaseSchema):
    total_credit: float
    total_debit: float
    balance: float
    transactions: list[OwnerTransactionOut]


# =========================
# CLIENT PORTFOLIO (NEW)
# =========================

class ClientPortfolioItem(BaseSchema):
    id: int
    owner_name: str
    mobile: str
    email: Optional[EmailStr] = None
    
    total_projects: int
    linked_project_name: Optional[str] = None
    
    pending_billing: float
    total_received: float
    
    status: str # ACTIVE / INACTIVE

    class Config:
        from_attributes = True


class ClientPortfolioSummary(BaseSchema):
    total_clients: int
    total_outstanding_billing: float
    average_satisfaction_score: float # Mocked for now


class ClientPortfolioResponse(BaseSchema):
    summary: ClientPortfolioSummary
    items: list[ClientPortfolioItem]


# =========================
# PAYMENT TRACKER (NEW)
# =========================

class OwnerPaymentScheduleCreate(BaseSchema):
    owner_id: int
    project_id: int
    milestone_name: str
    due_date: Optional[date] = None
    amount: float
    description: Optional[str] = None
    reference_code: Optional[str] = None


class OwnerPaymentScheduleOut(BaseSchema):
    id: int
    owner_id: int
    project_id: int
    milestone_name: str
    due_date: Optional[date] = None
    amount: float
    paid_amount: float
    status: str
    reference_code: Optional[str]
    description: Optional[str]

    class Config:
        from_attributes = True
