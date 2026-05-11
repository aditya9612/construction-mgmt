from datetime import date, datetime
from typing import Optional, Union
from enum import Enum

from pydantic import BaseModel, Field, field_validator
from typing_extensions import Annotated
from app.core.enums import (
    IssueCategory,
    IssuePriority,
    IssueStatus,
    MilestoneStatus,
    ProjectStatus,
    QCStatus,
    SafetyChecklistStatus,
    SiteRequestStatus,
    SiteRequestType,
    TaskPriority,
    TaskStatus,
    WeatherType,
    WorkActivityStatus,
)
from app.schemas.base import BaseSchema
from pydantic_core.core_schema import ValidationInfo
from datetime import date as dt_date

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
    business_id: str = Field(..., description="Auto-generated project ID (PRJ001)")
    project_name: str
    owner_id: int
    description: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    status: str
    completion_percentage: float = 0.0

    @field_validator("business_id")
    def validate_business_id(cls, v):
        if not v.startswith("PRJ"):
            raise ValueError("Invalid project ID format")
        return v

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
    status: Optional[MilestoneStatus] = MilestoneStatus.PLANNED


class MilestoneUpdate(BaseSchema):
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[MilestoneStatus] = None


class MilestoneOut(BaseSchema):
    id: int
    project_id: int
    title: str
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: MilestoneStatus


# ===================== TASK =====================


class TaskCreate(BaseSchema):
    title: str
    description: Optional[str] = None
    priority: Union[int, TaskPriority]
    status: TaskStatus = TaskStatus.PLANNED
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assigned_user_ids: Optional[list[int]] = None
    activity_type_id: Optional[int] = None


class TaskUpdate(BaseSchema):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[Union[int, TaskPriority]] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assigned_user_id: Optional[int] = None


class TaskOut(BaseSchema):
    id: int
    project_id: int
    title: str
    description: Optional[str] = None
    priority: TaskPriority
    status: TaskStatus
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    created_by_user_id: int
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


class TaskPass(BaseSchema):
    new_user_id: int
    remark: Optional[str] = None


class TaskStatusUpdate(BaseSchema):
    status: TaskStatus


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

    contractor_id: Optional[int] = None

    weather: Optional[WeatherType] = None

    work_done: str
    work_planned: Optional[str] = None

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

    @field_validator("contractor_id")
    def validate_contractor_id(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Invalid contractor_id")
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

    contractor_id: Optional[int] = None

    weather: Optional[WeatherType] = None

    work_done: Optional[str] = None
    work_planned: Optional[str] = None

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

    @field_validator("contractor_id")
    def validate_contractor_id(cls, v):
        if v is not None and v <= 0:
            raise ValueError("Invalid contractor_id")
        return v


# ===================== OUTPUT =====================


class DSROut(DSRBase):
    id: int
    business_id: str
    created_at: datetime
    updated_at: datetime
    created_by_id: Optional[int] = None
    created_by_name: Optional[str] = None
    status: str

    latitude: Optional[float] = None
    longitude: Optional[float] = None

    contractor_name: Optional[str] = None
    total_labour: int = 0
    skilled_labour: int = 0
    unskilled_labour: int = 0
    photos: list[str] = []

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
    business_id: str
    status: IssueStatus
    assigned_to: Optional[int]
    resolution: Optional[str]

    class Config:
        from_attributes = True


# ===================== QC =====================


class QCCreate(BaseSchema):
    project_id: int
    task_id: Optional[int] = None
    dsr_id: Optional[int] = None
    inspection_type: str
    test_type: str
    result: float
    standard_value: float
    status: QCStatus
    engineer_name: str
    remarks: Optional[str] = None

    @field_validator("inspection_type", "test_type", "engineer_name")
    def validate_text_fields(cls, v):
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v


class QCOut(QCCreate):
    id: int

    class Config:
        from_attributes = True


# ===================== SAFETY =====================


class SafetyCreate(BaseSchema):
    project_id: int
    date: date
    safety_checklist_status: SafetyChecklistStatus
    ppe_compliance: bool = True
    violation_type: str
    description: str
    injury_details: Optional[str] = None
    action_taken: Optional[str] = None
    responsible_person: str

    @field_validator("description", "violation_type", "responsible_person")
    def validate_required_text(cls, v):
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v


class SafetyOut(SafetyCreate):
    id: int

    class Config:
        from_attributes = True


# ===================== CHECKLIST =====================


class ChecklistCreate(BaseSchema):
    project_id: int
    name: str
    type: str

    @field_validator("name", "type")
    def validate_checklist_fields(cls, v):
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v


class ChecklistItemCreate(BaseSchema):
    checklist_id: int
    item: str

    @field_validator("item")
    def validate_item(cls, v):
        if not v.strip():
            raise ValueError("Checklist item cannot be empty")
        return v


class ChecklistLogCreate(BaseSchema):
    project_id: int
    checklist_id: int
    status: str
    remarks: Optional[str] = None

    @field_validator("status")
    def validate_status(cls, v):
        if v not in ["Done", "Pending"]:
            raise ValueError("Status must be Done or Pending")
        return v


class ChecklistLogOut(BaseModel):
    id: int
    project_id: Optional[int]
    checklist_id: Optional[int]
    status: Optional[str]
    remarks: Optional[str]

    model_config = {"from_attributes": True}  #  VERY IMPORTANT


class SitePhotoCreate(BaseSchema):
    project_id: int
    task_id: Optional[int] = None
    date: Optional[dt_date] = None
    activity_tag: Optional[str] = None
    location_tag: Optional[str] = None
    description: Optional[str] = None


class SitePhotoOut(SitePhotoCreate):
    id: int
    photo_url: str

    class Config:
        from_attributes = True


class DrawingCreate(BaseSchema):
    project_id: int
    drawing_name: str
    version: str
    approved_by: Optional[str]
    date: Optional[date]
    remarks: Optional[str]


class DrawingOut(DrawingCreate):
    id: int
    file_url: str

    class Config:
        from_attributes = True


# ===================== CREATE =====================


class SiteRequestCreate(BaseSchema):
    project_id: int
    request_type: SiteRequestType
    description: str
    quantity: float

    @field_validator("description")
    def validate_description(cls, v):
        if not v.strip():
            raise ValueError("Description cannot be empty")
        return v


# ===================== ACTION =====================


class SiteRequestAction(BaseSchema):
    remarks: Optional[str] = None


# ===================== OUTPUT =====================


class SiteRequestOut(BaseSchema):
    id: int
    project_id: int
    request_type: SiteRequestType
    description: str
    quantity: float
    requested_by: int
    approved_by: Optional[int]
    status: SiteRequestStatus

    class Config:
        from_attributes = True


class MessageCreate(BaseSchema):
    message: str
    parent_id: Optional[int] = None
    attachment_url: Optional[str] = None

    @field_validator("message")
    def validate_message(cls, v):
        if not v.strip():
            raise ValueError("Message cannot be empty")
        return v


class WorkActivityCreate(BaseModel):
    project_id: int
    boq_code: int
    activity_name: str
    planned_quantity: float
    unit: str
    start_date: date
    end_date: date

    # Use enum instead of str
    status: WorkActivityStatus = WorkActivityStatus.NOT_STARTED

    engineer_id: int


class WorkActivityUpdate(BaseModel):
    activity_name: Optional[str] = None
    planned_quantity: Optional[float] = None
    unit: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    # Optional enum for partial updates
    status: Optional[WorkActivityStatus] = None


class WorkActivityResponse(BaseModel):
    id: int
    project_id: int
    boq_code: int
    activity_name: str
    planned_quantity: float
    unit: str
    start_date: date
    end_date: date

    # Response will return enum values such as "On Track"
    status: WorkActivityStatus

    engineer_id: int

    class Config:
        from_attributes = True


class DailyProgressCreate(BaseModel):
    activity_id: int
    entry_date: date
    today_progress: float
    remarks: Optional[str] = None
    created_by: int


class DailyProgressUpdate(BaseModel):
    today_progress: Optional[float] = None
    remarks: Optional[str] = None


class DailyProgressResponse(BaseModel):
    id: int
    activity_id: int
    entry_date: date
    today_progress: float
    remarks: Optional[str] = None
    created_by: int

    class Config:
        from_attributes = True