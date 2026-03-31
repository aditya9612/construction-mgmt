from decimal import Decimal
from typing import Optional
from pydantic import BaseModel
from datetime import date
from app.schemas.base import BaseSchema


class LabourCreate(BaseSchema):
    project_id: int
    labour_title: str
    quantity: Decimal = Decimal("0")
    unit_cost: Decimal = Decimal("0")
    total_cost: Optional[Decimal] = None
    status: Optional[str] = "Active"
    notes: Optional[str] = None


class LabourUpdate(BaseSchema):
    labour_title: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit_cost: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class LabourOut(BaseSchema):
    id: int
    project_id: int
    labour_title: str
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    status: str
    notes: Optional[str]

class LabourAttendanceBase(BaseModel):
    project_id: int
    attendance_date: date
    working_hours: float
    overtime_hours: float = 0
    task_description: str


class LabourAttendanceCreate(LabourAttendanceBase):
    pass


class LabourAttendanceOut(LabourAttendanceBase):
    id: int
    labour_id: int
    total_wage: float

    class Config:
        from_attributes = True

