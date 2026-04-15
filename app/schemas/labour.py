from decimal import Decimal
from typing import Optional, List
from datetime import date, time
from pydantic import BaseModel
from app.core import enums as e


# ======================
# LABOUR
# ======================
class LabourCreate(BaseModel):
    worker_code: str
    aadhaar_number: Optional[str]
    project_id: int
    labour_name: str
    skill_type: str
    daily_wage_rate: Decimal
    contractor_id: Optional[int]
    status: Optional[e.LabourStatus] = e.LabourStatus.ACTIVE
    notes: Optional[str] = None


class LabourUpdate(BaseModel):
    labour_name: Optional[str]
    skill_type: Optional[str]
    daily_wage_rate: Optional[Decimal]
    contractor_id: Optional[int]
    status: Optional[e.LabourStatus]
    notes: Optional[str] = None


class LabourOut(BaseModel):
    id: int
    worker_code: str
    aadhaar_number: Optional[str]
    project_id: int
    labour_name: str
    skill_type: str
    daily_wage_rate: Decimal
    contractor_id: Optional[int]
    status: e.LabourStatus
    notes: Optional[str] = None

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}


# ======================
# ATTENDANCE
# ======================
class LabourAttendanceCreate(BaseModel):
    project_id: int
    attendance_date: date
    status: e.AttendanceStatus = e.AttendanceStatus.PRESENT
    in_time: Optional[time]
    out_time: Optional[time]
    working_hours: Decimal
    overtime_hours: Decimal = 0
    overtime_rate: Decimal
    task_description: str


class LabourAttendanceOut(BaseModel):
    id: int
    labour_id: int
    project_id: int
    attendance_date: date
    status: e.AttendanceStatus
    in_time: Optional[time]
    out_time: Optional[time]
    working_hours: Decimal
    overtime_hours: Decimal
    overtime_rate: Decimal
    task_description: str
    total_wage: Decimal

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}


class BulkAttendanceItem(BaseModel):
    labour_id: int
    project_id: int
    attendance_date: date
    status: e.AttendanceStatus = e.AttendanceStatus.PRESENT
    in_time: Optional[time]
    out_time: Optional[time]
    working_hours: Decimal
    overtime_hours: Decimal = 0
    overtime_rate: Decimal
    task_description: str


class BulkAttendanceCreate(BaseModel):
    items: List[BulkAttendanceItem]


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