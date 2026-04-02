from pydantic import BaseModel
from datetime import date
from typing import Optional


class IssueBase(BaseModel):
    project_id: int
    title: str
    category: str
    description: Optional[str] = None
    reported_date: date
    priority: str = "Medium"


class IssueCreate(IssueBase):
    pass


class IssueUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[int] = None
    resolution: Optional[str] = None


class IssueOut(IssueBase):
    id: int
    status: str
    assigned_to: Optional[int]
    resolution: Optional[str]

    class Config:
        from_attributes = True