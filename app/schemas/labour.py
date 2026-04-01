from decimal import Decimal
from typing import Optional
from datetime import date
from pydantic import BaseModel
from app.schemas.base import BaseSchema


class LabourCreate(BaseSchema):
    project_id: int
    labour_name: str
    skill_type: str
    daily_wage_rate: Decimal
    contractor_id: Optional[int] = None
    status: Optional[str] = "Active"
    notes: Optional[str] = None


class LabourUpdate(BaseSchema):
    labour_name: Optional[str] = None
    skill_type: Optional[str] = None
    daily_wage_rate: Optional[Decimal] = None
    contractor_id: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class LabourOut(BaseSchema):
    id: int
    project_id: int
    labour_name: str
    skill_type: str
    daily_wage_rate: Decimal
    contractor_id: Optional[int]
    status: str
    notes: Optional[str]


# -------------------------
# ATTENDANCE
# -------------------------

class LabourAttendanceBase(BaseModel):
    project_id: int
    attendance_date: date
    working_hours: float
    overtime_hours: float = 0
    overtime_rate: float
    task_description: str


class LabourAttendanceCreate(LabourAttendanceBase):
    pass


class LabourAttendanceOut(LabourAttendanceBase):
    id: int
    labour_id: int
    total_wage: float

    class Config:
        from_attributes = True