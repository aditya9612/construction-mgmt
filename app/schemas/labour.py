from decimal import Decimal
from typing import Optional, List
from datetime import date, time, datetime
from pydantic import BaseModel, field_validator
from app.core import enums as e


# ======================
# LABOUR (GLOBAL NOW)
# ======================
class LabourCreate(BaseModel):
    aadhaar_number: Optional[str]
    labour_name: str
    skill_type: e.SkillType
    daily_wage_rate: Decimal
    contractor_id: Optional[int]
    status: Optional[e.LabourStatus] = e.LabourStatus.ACTIVE
    notes: Optional[str] = None

    @field_validator("aadhaar_number")
    def validate_aadhaar(cls, v):
        if v and (not v.isdigit() or len(v) != 12):
            raise ValueError("Aadhaar must be 12 digits")
        return v


class LabourUpdate(BaseModel):
    labour_name: Optional[str]
    skill_type: Optional[e.SkillType]
    daily_wage_rate: Optional[Decimal]
    contractor_id: Optional[int]
    status: Optional[e.LabourStatus]
    notes: Optional[str] = None


class LabourOut(BaseModel):
    id: int
    worker_code: str
    aadhaar_number: Optional[str]
    labour_name: str
    skill_type: e.SkillType
    daily_wage_rate: Decimal
    contractor_id: Optional[int]
    status: e.LabourStatus
    notes: Optional[str] = None

    class Config:
        from_attributes = True


# ======================
# 🔥 NEW: ASSIGN LABOUR TO PROJECT
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
    overtime_rate: Decimal
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
    skill_type: e.SkillType
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
    contractor_name: str
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
    skill_type: e.SkillType
    daily_wage: Decimal
    days_present: int
    ot_hours: Decimal
    total_wage_earned: Decimal
    status: str

    class Config:
        json_encoders = {Decimal: float}