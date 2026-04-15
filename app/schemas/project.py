from datetime import date, datetime
from typing import Optional
from enum import Enum

from pydantic import Field, field_validator
from typing_extensions import Annotated
from app.schemas.base import BaseSchema
from pydantic_core.core_schema import ValidationInfo


# ===================== ENUMS =====================

class ProjectStatus(str, Enum):
    PLANNED = "Planned"
    ONGOING = "Ongoing"
    COMPLETED = "Completed"
    ON_HOLD = "On Hold"


class IssuePriority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class IssueStatus(str, Enum):
    OPEN = "Open"
    CLOSED = "Closed"


class TaskStatus(str, Enum):
    PLANNED = "Planned"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"


class WeatherType(str, Enum):
    SUNNY = "Sunny"
    RAINY = "Rainy"
    CLOUDY = "Cloudy"
    WINDY = "Windy"


class IssueCategory(str, Enum):
    MATERIAL = "Material"
    SAFETY = "Safety"
    DELAY = "Delay"


# ===================== PROJECT =====================

class ProjectCreate(BaseSchema):
    project_name: str
    owner_id: int
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[ProjectStatus] = ProjectStatus.PLANNED

    @field_validator("end_date")
    def validate_dates(cls, v, info: ValidationInfo):
        start_date = info.data.get("start_date")
        if v and start_date and v < start_date:
            raise ValueError("End date cannot be before start date")
        return v


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


class ProjectMemberAssign(BaseSchema):
    user_id: int


class ProjectMemberOut(BaseSchema):
    user_id: int
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None


# ===================== MILESTONE =====================

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


# ===================== TASK =====================

class TaskCreate(BaseSchema):
    title: str
    description: Optional[str] = None
    priority: Annotated[int, Field(ge=0, le=5)]
    status: TaskStatus = TaskStatus.PLANNED
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assigned_user_id: Optional[int] = None


class TaskUpdate(BaseSchema):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[TaskStatus] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assigned_user_id: Optional[int] = None


class TaskOut(BaseSchema):
    id: int
    project_id: int
    title: str
    description: Optional[str] = None
    priority: int
    status: TaskStatus
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assigned_user_id: Optional[int]
    completion_percentage: float
    is_delayed: bool


class TaskProgressUpdate(BaseSchema):
    percentage: Annotated[int, Field(ge=0, le=100)]
    remarks: Optional[str] = None


class TaskProgressOut(BaseSchema):
    id: int
    task_id: int
    percentage: int
    remarks: Optional[str] = None
    created_at: datetime


# ===================== COMMENTS =====================

class CommentCreate(BaseSchema):
    content: str


class CommentOut(BaseSchema):
    id: int
    task_id: int
    author_user_id: int
    content: str

# ===================== DSR BASE =====================

class DSRBase(BaseSchema):
    project_id: int
    report_date: date

    site_location: Optional[str] = None
    contractor_name: Optional[str] = None

    weather: Optional[WeatherType] = None

    work_done: str
    work_planned: Optional[str] = None

    labour_count: Annotated[int, Field(ge=0)] = 0

    machinery_used: Optional[str] = None
    material_received: Optional[str] = None
    material_used: Optional[str] = None

    issues: Optional[str] = None
    safety_observations: Optional[str] = None

    remarks: Optional[str] = None

    @field_validator("work_done")
    def validate_work_done(cls, v):
        if not v.strip():
            raise ValueError("Work done cannot be empty")
        return v

    @field_validator("report_date")
    def validate_report_date(cls, v):
        if v > date.today():
            raise ValueError("Future report date not allowed")
        return v

    @field_validator("contractor_name")
    def validate_contractor(cls, v):
        if v and not v.strip():
            raise ValueError("Contractor name cannot be empty")
        return v


# ===================== CREATE =====================

class DSRCreate(DSRBase):
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @field_validator("latitude")
    def validate_lat(cls, v):
        if v is not None and not (-90 <= v <= 90):
            raise ValueError("Invalid latitude")
        return v
    
    @field_validator("longitude")
    def validate_lng(cls, v):
        if v is not None and not (-180 <= v <= 180):
            raise ValueError("Invalid longitude")
        return v


# ===================== UPDATE =====================

class DSRUpdate(BaseSchema):
    report_date: Optional[date] = None
    site_location: Optional[str] = None
    contractor_name: Optional[str] = None

    weather: Optional[WeatherType] = None

    work_done: Optional[str] = None
    work_planned: Optional[str] = None

    labour_count: Optional[int] = None

    machinery_used: Optional[str] = None
    material_received: Optional[str] = None
    material_used: Optional[str] = None

    issues: Optional[str] = None
    safety_observations: Optional[str] = None

    remarks: Optional[str] = None

    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @field_validator("work_done")
    def validate_update_work_done(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Work done cannot be empty")
        return v

    @field_validator("report_date")
    def validate_update_date(cls, v):
        if v and v > date.today():
            raise ValueError("Future report date not allowed")
        return v

    @field_validator("site_location")
    def validate_site_location(cls, v):
        if v and not v.strip():
            raise ValueError("Site location cannot be empty")
        return v

    @field_validator("contractor_name")
    def validate_update_contractor(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Contractor name cannot be empty")
        return v


# ===================== OUTPUT =====================

class DSROut(DSRBase):
    id: int
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[int] = None
    created_by_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    class Config:
        from_attributes = True


# ===================== PAGINATION META =====================

class PaginationMeta(BaseSchema):
    total: int
    limit: int
    offset: int


# ===================== ISSUES =====================

class IssueBase(BaseSchema):
    project_id: int
    title: str
    category: IssueCategory
    description: Optional[str] = None
    reported_date: date
    priority: IssuePriority = IssuePriority.MEDIUM

    @field_validator("title")
    def validate_title(cls, v):
        if not v.strip():
            raise ValueError("Title cannot be empty")
        return v

    @field_validator("reported_date")
    def validate_reported_date(cls, v):
        if v > date.today():
            raise ValueError("Future date not allowed")
        return v


class IssueCreate(IssueBase):
    pass


class IssueUpdate(BaseSchema):
    title: Optional[str] = None
    category: Optional[IssueCategory] = None
    description: Optional[str] = None
    priority: Optional[IssuePriority] = None
    status: Optional[IssueStatus] = None
    assigned_to: Optional[int] = None
    resolution: Optional[str] = None
    reported_date: Optional[date] = None

    @field_validator("title")
    def validate_update_title(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Title cannot be empty")
        return v

    @field_validator("reported_date")
    def validate_update_date(cls, v):
        if v and v > date.today():
            raise ValueError("Future date not allowed")
        return v


class IssueOut(IssueBase):
    id: int
    status: IssueStatus
    assigned_to: Optional[int]
    resolution: Optional[str]

    class Config:
        from_attributes = True