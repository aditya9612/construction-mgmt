from datetime import date, datetime
from typing import Optional
from enum import Enum

from pydantic import conint
from app.schemas.base import BaseSchema

from typing_extensions import Annotated
from pydantic import Field


# -------------------------
# ENUM
# -------------------------
class ProjectStatus(str, Enum):
    PLANNED = "Planned"
    ONGOING = "Ongoing"
    COMPLETED = "Completed"
    ON_HOLD = "On Hold"


# -------------------------
# PROJECT
# -------------------------
class ProjectCreate(BaseSchema):
    project_name: str
    owner_id: int
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[ProjectStatus] = ProjectStatus.PLANNED


class ProjectUpdate(BaseSchema):
    project_name: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[ProjectStatus] = None


class ProjectOut(BaseSchema):
    id: int
    project_name: str
    owner_id: int
    description: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    status: str
    completion_percentage: float = 0.0

    class Config:
        from_attributes = True


# -------------------------
# MEMBERS
# -------------------------
class ProjectMemberAssign(BaseSchema):
    user_id: int


class ProjectMemberOut(BaseSchema):
    user_id: int
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None


# -------------------------
# MILESTONE
# -------------------------
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


# -------------------------
# TASK
# -------------------------
class TaskCreate(BaseSchema):
    title: str
    description: Optional[str] = None
    priority: int = 0
    status: str = "Planned"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assigned_user_id: Optional[int] = None


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
    assigned_user_id: Optional[int]
    completion_percentage: int
    is_delayed: bool


# -------------------------
# TASK PROGRESS
# -------------------------
class TaskProgressUpdate(BaseSchema):
    percentage: Annotated[int, Field(ge=0, le=100)]
    remarks: Optional[str] = None


class TaskProgressOut(BaseSchema):
    id: int
    task_id: int
    percentage: int
    remarks: Optional[str] = None
    created_at: datetime


# -------------------------
# COMMENTS
# -------------------------
class CommentCreate(BaseSchema):
    content: str


class CommentOut(BaseSchema):
    id: int
    task_id: int
    author_user_id: int
    content: str