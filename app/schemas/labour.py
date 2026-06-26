from decimal import Decimal
from typing import Optional, List, Dict, Any
from datetime import date, time, datetime
from fastapi import Form
from pydantic import BaseModel, field_validator
from app.core import enums as e
from pydantic import EmailStr
from app.core.validators import (
    validate_aadhaar,
    validate_mobile,
    validate_full_name,
    validate_positive_decimal,
)


# ======================
# LABOUR (GLOBAL NOW)
# ======================
class LabourCreate(BaseModel):
    aadhaar_number: Optional[str] = None

    labour_name: str

    mobile_number: str

    email: Optional[EmailStr] = None

    pan_number: Optional[str] = None
    address: Optional[str] = None

    # skill_type: e.SkillType

    # daily_wage_rate: Decimal

    labour_type_id: int

    custom_daily_wage_rate: Optional[Decimal] = None

    custom_ot_rate_per_hour: Optional[Decimal] = None

    contractor_id: Optional[int] = None

    status: Optional[e.LabourStatus] = e.LabourStatus.ACTIVE

    notes: Optional[str] = None

    @field_validator("aadhaar_number")
    def validate_aadhaar_field(cls, v):
        return validate_aadhaar(v)

    @field_validator("mobile_number")
    def validate_mobile_field(cls, v):
        return validate_mobile(v)

    @field_validator("labour_name")
    def validate_labour_name(cls, v):
        return validate_full_name(v)

    # @field_validator("daily_wage_rate")
    # def validate_wage(cls, v):
    #     return validate_positive_decimal(v, "Daily wage")

    @field_validator("custom_daily_wage_rate")
    def validate_custom_wage(cls, v):

        if v is None:
            return v

        return validate_positive_decimal(v, "Custom daily wage")

    @field_validator("custom_ot_rate_per_hour")
    def validate_custom_ot(cls, v):

        if v is None:
            return v

        return validate_positive_decimal(v, "Custom OT rate")


class LabourUpdate(BaseModel):
    aadhaar_number: Optional[str] = None

    labour_name: Optional[str] = None

    mobile_number: Optional[str] = None

    email: Optional[EmailStr] = None

    pan_number: Optional[str] = None

    address: Optional[str] = None

    # skill_type: Optional[e.SkillType] = None

    # daily_wage_rate: Optional[Decimal] = None

    labour_type_id: Optional[int] = None

    custom_daily_wage_rate: Optional[Decimal] = None

    custom_ot_rate_per_hour: Optional[Decimal] = None

    contractor_id: Optional[int] = None

    status: Optional[e.LabourStatus] = None

    notes: Optional[str] = None

    @field_validator("custom_daily_wage_rate")
    def validate_custom_wage(cls, v):

        if v is None:
            return v

        return validate_positive_decimal(v, "Custom daily wage")

    @field_validator("custom_ot_rate_per_hour")
    def validate_custom_ot(cls, v):

        if v is None:
            return v

        return validate_positive_decimal(v, "Custom OT rate")

    @field_validator("aadhaar_number")
    def validate_aadhaar_field(cls, v):
        return validate_aadhaar(v)

    @field_validator("mobile_number")
    def validate_mobile_field(cls, v):
        return validate_mobile(v)

    @field_validator("labour_name")
    def validate_labour_name(cls, v):
        return validate_full_name(v)


class LabourOut(BaseModel):
    id: int
    worker_code: str
    user_id: Optional[int] = None
    role: Optional[str] = None
    aadhaar_number: Optional[str]
    labour_name: str
    mobile_number: Optional[str]
    pan_number: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str]
    profile_image: Optional[str]
    # skill_type: e.SkillType
    # daily_wage_rate: Decimal
    labour_type_id: Optional[int] = None
    labour_type_name: Optional[str] = None
    skill_category: Optional[e.SkillType] = None
    default_daily_wage: Optional[Decimal] = None
    custom_daily_wage_rate: Optional[Decimal] = None
    custom_ot_rate_per_hour: Optional[Decimal] = None
    effective_daily_wage: Decimal
    effective_ot_rate: Decimal
    contractor_id: Optional[int]
    contractor_name: Optional[str] = None
    status: e.LabourStatus
    notes: Optional[str] = None

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}


# ======================
#  NEW: ASSIGN LABOUR TO PROJECT
# ======================
class LabourAssignProject(BaseModel):
    labour_id: int
    project_id: int


class LabourProjectOut(BaseModel):
    labour_id: int
    project_id: int
    assigned_date: date

    class Config:
        from_attributes = True


# ======================
# ATTENDANCE (NO CHANGE)
# ======================
class LabourAttendanceCreate(BaseModel):
    project_id: int
    attendance_date: date
    status: e.AttendanceStatus = e.AttendanceStatus.PRESENT
    in_time: Optional[time]
    out_time: Optional[time]
    working_hours: Decimal
    overtime_hours: Decimal = Decimal("0")
    task_description: str


class LabourAttendanceOut(BaseModel):
    id: int
    labour_id: int
    project_id: int
    attendance_date: date
    status: e.AttendanceStatus

    check_in_address: Optional[str]
    check_out_address: Optional[str]

    in_time: Optional[time]
    out_time: Optional[time]

    task_id: Optional[int]

    check_in_image: Optional[str]
    check_out_image: Optional[str]

    working_hours: Decimal
    overtime_hours: Decimal
    overtime_rate: Decimal

    task_description: str
    total_wage: Decimal

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}


# ======================
# PAYROLL
# ======================
class PayrollGenerate(BaseModel):
    month: int
    year: int


class PayrollPayment(BaseModel):
    labour_id: int
    project_id: int
    month: int
    year: int
    amount: Decimal


class PayrollOut(BaseModel):
    id: int
    labour_id: int
    project_id: int
    month: int
    year: int

    total_working_hours: Decimal
    total_overtime_hours: Decimal
    total_wage: Decimal

    paid_amount: Decimal
    remaining_amount: Decimal

    status: e.PayrollStatus

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}


# ======================
# ADVANCE
# ======================
class AdvancePayment(BaseModel):
    labour_id: int
    project_id: int
    amount: Decimal
    description: Optional[str]


# ======================
# NEW PAYROLL & FISCAL SCHEMAS
# ======================
class PayrollDetailsOut(BaseModel):
    id: int
    labour_id: int
    project_id: int
    month: int
    year: int
    total_working_hours: Decimal
    total_overtime_hours: Decimal
    total_wage: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal
    status: e.PayrollStatus

    # Enriched properties
    labour_name: str
    worker_code: str
    # skill_type: e.SkillType
    # daily_wage_rate: Decimal
    skill_category: e.SkillType
    daily_wage_rate: Decimal
    contractor_id: Optional[int] = None

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}


class PayrollStatsOut(BaseModel):
    paid_this_month: Decimal
    pending_due: Decimal
    monthly_budget: Decimal
    advance_logs: int

    class Config:
        json_encoders = {Decimal: float}


class ContractorLiabilityOut(BaseModel):
    contractor_id: Optional[int] = None
    contractor_name: Optional[str] = None
    total_wage: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal

    class Config:
        json_encoders = {Decimal: float}


class WeeklyVelocityOut(BaseModel):
    week_number: int
    total_wage: Decimal
    attendance_count: int

    class Config:
        json_encoders = {Decimal: float}


class DisbursementHistoryOut(BaseModel):
    id: int
    labour_id: int
    labour_name: str
    amount: Decimal
    mode: str
    reference: str
    created_at: datetime

    class Config:
        json_encoders = {Decimal: float}


class FiscalSummaryOut(BaseModel):
    total_payout: Decimal
    high_payouts: int
    ot_intensive: int
    advance_adjusted: Decimal

    class Config:
        json_encoders = {Decimal: float}


class PayrollMomentumOut(BaseModel):
    month: int
    year: int
    period_name: str
    total_wage: Decimal

    class Config:
        json_encoders = {Decimal: float}


class AggregateReportOut(BaseModel):
    labour_id: int
    labour_name: str
    # skill_type: e.SkillType
    skill_category: e.SkillType
    daily_wage: Decimal
    days_present: int
    ot_hours: Decimal
    total_wage_earned: Decimal
    status: str

    class Config:
        json_encoders = {Decimal: float}

class LabourPaymentSummary(BaseModel):
    total_payout: float
    high_payouts: int
    ot_intensive: int
    advance_adjusted: float

class LabourPaymentRecord(BaseModel):
    id: str
    date: str
    skill_type: str
    daily_wage: str
    ot_hours: str
    total_wage_earned: str
    remarks: Optional[str]
    status: str

class LabourPaymentResponse(BaseModel):
    summary: LabourPaymentSummary
    records: List[LabourPaymentRecord]
    total_records: int
    page: int
    page_size: int
    total_pages: int
