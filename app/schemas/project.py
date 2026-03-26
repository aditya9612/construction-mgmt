from datetime import date, datetime
from typing import Optional

from pydantic import conint

from app.schemas.base import BaseSchema


class ProjectCreate(BaseSchema):
    name: str
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[str] = "Planned"


class ProjectUpdate(BaseSchema):
    name: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[str] = None


class ProjectOut(BaseSchema):
    id: int
    name: str
    description: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    status: str
    completion_percentage: float = 0.0


class ProjectMemberAssign(BaseSchema):
    user_id: int


class ProjectMemberOut(BaseSchema):
    user_id: int
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None


class MilestoneCreate(BaseSchema):
    title: str
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class MilestoneUpdate(BaseSchema):
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class MilestoneOut(BaseSchema):
    id: int
    project_id: int
    title: str
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class TaskCreate(BaseSchema):
    title: str
    description: Optional[str] = None
    priority: int = 0
    status: str = "Planned"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assigned_user_id: int


class TaskUpdate(BaseSchema):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assigned_user_id: Optional[int] = None


class TaskOut(BaseSchema):
    id: int
    project_id: int
    title: str
    description: Optional[str] = None
    priority: int
    status: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assigned_user_id: int
    completion_percentage: int
    is_delayed: bool


class TaskProgressUpdate(BaseSchema):
    percentage: conint(ge=0, le=100)
    remarks: Optional[str] = None


class TaskProgressOut(BaseSchema):
    id: int
    task_id: int
    percentage: int
    remarks: Optional[str] = None
    created_at: datetime


class CommentCreate(BaseSchema):
    content: str


class CommentOut(BaseSchema):
    id: int
    task_id: int
    author_user_id: int
    content: str

