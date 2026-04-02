from pydantic import BaseModel
from datetime import date
from typing import Optional


class DSRBase(BaseModel):
    project_id: int
    report_date: date
    weather: Optional[str] = None
    work_done: str
    work_planned: Optional[str] = None
    labour_count: int = 0
    material_used: Optional[str] = None
    issues: Optional[str] = None
    remarks: Optional[str] = None


class DSRCreate(DSRBase):
    pass


class DSRUpdate(BaseModel):
    weather: Optional[str] = None
    work_done: Optional[str] = None
    work_planned: Optional[str] = None
    labour_count: Optional[int] = None
    material_used: Optional[str] = None
    issues: Optional[str] = None
    remarks: Optional[str] = None


class DSROut(DSRBase):
    id: int

    class Config:
        from_attributes = True