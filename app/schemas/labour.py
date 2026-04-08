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

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}


class LabourAttendanceBase(BaseModel):
    project_id: int
    attendance_date: date
    working_hours: Decimal
    overtime_hours: Decimal = Decimal("0")
    overtime_rate: Decimal
    task_description: str


class LabourAttendanceCreate(LabourAttendanceBase):
    pass


class LabourAttendanceOut(LabourAttendanceBase):
    id: int
    labour_id: int
    total_wage: Decimal

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}


class PayrollGenerate(BaseModel):
    month: int
    year: int


class PayrollOut(BaseModel):
    labour_id: int
    project_id: int
    month: int
    year: int

    total_working_hours: Decimal
    total_overtime_hours: Decimal
    total_wage: Decimal

    paid_amount: Decimal
    remaining_amount: Decimal
    status: str

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}
