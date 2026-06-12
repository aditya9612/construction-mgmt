from datetime import date, datetime, time
from decimal import Decimal
from typing import List, Optional, Union
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import Annotated
from app.core.enums import (
    ChecklistStatus,
    DocumentStatus,
    IssueCategory,
    IssuePriority,
    IssueStatus,
    MilestoneStatus,
    OTPolicyType,
    ProjectStatus,
    QCStatus,
    SafetyChecklistStatus,
    SiteRequestStatus,
    SiteRequestType,
    TaskPriority,
    TaskStatus,
    WeatherType,
    WorkActivityStatus,
    ProjectType,
    LocationType,
)
from app.schemas.base import BaseSchema
from pydantic_core.core_schema import ValidationInfo
from datetime import date as dt_date
from app.core.validators import (
    validate_activity_name,
    validate_non_empty_string,
    validate_progress_date,
    validate_progress_remarks,
    validate_start_end_dates,
    validate_unit,
    validate_work_activity_date,
)
from pydantic import Field

# ===================== PROJECT =====================


class ProjectCreate(BaseSchema):
    project_name: str
    owner_id: int
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[ProjectStatus] = ProjectStatus.PLANNED

    type: Optional[ProjectType] = None
    location_type: Optional[LocationType] = None
    site_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    shift_start_time: Optional[time] = None
    shift_end_time: Optional[time] = None
    grace_period_minutes: int = 15

    @field_validator("end_date")
    def validate_dates(cls, v, info: ValidationInfo):

        return validate_start_end_dates(info.data.get("start_date"), v)


class ProjectUpdate(BaseSchema):
    project_name: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[ProjectStatus] = None

    type: Optional[ProjectType] = None
    location_type: Optional[LocationType] = None
    site_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    shift_start_time: Optional[time] = None
    shift_end_time: Optional[time] = None
    grace_period_minutes: Optional[int] = None

    @field_validator("end_date")
    def validate_dates(cls, v, info: ValidationInfo):

        return validate_start_end_dates(info.data.get("start_date"), v)


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
    execution_completion_percentage: float = 0.0
    total_milestones: int = 0
    total_tasks: int = 0
    completed_tasks: int = 0
    delayed_tasks: int = 0

    type: Optional[str] = None
    location_type: Optional[str] = None
    site_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    shift_start_time: Optional[time] = None
    shift_end_time: Optional[time] = None
    grace_period_minutes: int = 15

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

    @field_validator("end_date")
    def validate_dates(cls, v, info: ValidationInfo):

        return validate_start_end_dates(info.data.get("start_date"), v)


class MilestoneUpdate(BaseSchema):
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[MilestoneStatus] = None

    @field_validator("end_date")
    def validate_dates(cls, v, info: ValidationInfo):

        return validate_start_end_dates(info.data.get("start_date"), v)


class MilestoneOut(BaseSchema):
    id: int
    project_id: int
    title: str
    description: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    status: MilestoneStatus
    total_tasks: int = 0
    completed_tasks: int = 0
    pending_tasks: int = 0
    delayed_tasks: int = 0
    is_delayed: bool = False
    completion_percentage: float = 0.0
    execution_completion_percentage: float = 0.0


# ===================== TASK =====================


# ===================== TASK =====================

from fastapi import Form
import json


class TaskCreate(BaseSchema):
    title: str
    description: Optional[str] = None

    priority: Union[int, TaskPriority]

    status: TaskStatus = TaskStatus.PLANNED

    start_date: Optional[date] = None
    end_date: Optional[date] = None

    assigned_user_ids: Optional[list[int]] = None

    activity_type_id: Optional[int] = None

    milestone_id: Optional[int] = None

    boq_id: Optional[int] = None

    @field_validator("end_date")
    def validate_dates(cls, v, info: ValidationInfo):

        return validate_start_end_dates(
            info.data.get("start_date"),
            v,
        )


# =========================================================
# TASK CREATE FORM (multipart/form-data support)
# =========================================================

class TaskCreateForm:

    def __init__(

        self,

        title: str = Form(...),

        description: Optional[str] = Form(None),

        priority: Optional[Union[int, TaskPriority]] = Form(None),

        status: TaskStatus = Form(TaskStatus.PLANNED),

        start_date: Optional[date] = Form(None),

        end_date: Optional[date] = Form(None),

        assigned_user_ids: Optional[str] = Form(None),

        activity_type_id: Optional[int] = Form(None),

        milestone_id: Optional[int] = Form(None),

        boq_id: Optional[int] = Form(None),

    ):

        self.title = title

        self.description = description

        self.priority = priority

        self.status = status

        self.start_date = start_date

        self.end_date = end_date

        self.assigned_user_ids = (
            json.loads(assigned_user_ids)
            if assigned_user_ids
            else None
        )

        self.activity_type_id = activity_type_id

        self.milestone_id = milestone_id

        self.boq_id = boq_id

    # =====================================================
    # CONVERT TO PYDANTIC SCHEMA
    # =====================================================

    def to_schema(self) -> TaskCreate:

        return TaskCreate(

            title=self.title,

            description=self.description,

            priority=self.priority,

            status=self.status,

            start_date=self.start_date,

            end_date=self.end_date,

            assigned_user_ids=self.assigned_user_ids,

            activity_type_id=self.activity_type_id,

            milestone_id=self.milestone_id,

            boq_id=self.boq_id,
        )


class TaskUpdate(BaseSchema):

    title: Optional[str] = None

    description: Optional[str] = None

    priority: Optional[Union[int, TaskPriority]] = None

    start_date: Optional[date] = None

    end_date: Optional[date] = None

    status: Optional[TaskStatus] = None

    assigned_user_id: Optional[int] = None

    activity_type_id: Optional[int] = None

    milestone_id: Optional[int] = None

    boq_id: Optional[int] = None

    @field_validator("end_date")
    def validate_dates(cls, v, info: ValidationInfo):

        return validate_start_end_dates(
            info.data.get("start_date"),
            v,
        )


# =========================================================
# TASK UPDATE FORM (multipart/form-data support)
# =========================================================

class TaskUpdateForm:

    def __init__(

        self,

        title: Optional[str] = Form(None),

        description: Optional[str] = Form(None),

        priority: Optional[int] = Form(None),

        start_date: Optional[date] = Form(None),

        end_date: Optional[date] = Form(None),

        status: Optional[TaskStatus] = Form(None),

        assigned_user_id: Optional[int] = Form(None),

        activity_type_id: Optional[int] = Form(None),

        milestone_id: Optional[int] = Form(None),

        boq_id: Optional[int] = Form(None),

        remove_audio: bool = Form(False),

        remove_image: bool = Form(False),

    ):

        self.title = title

        self.description = description

        self.priority = priority

        self.start_date = start_date

        self.end_date = end_date
        
        self.status = status

        self.assigned_user_id = assigned_user_id

        self.activity_type_id = activity_type_id

        self.milestone_id = milestone_id

        self.boq_id = boq_id

        self.remove_audio = remove_audio

        self.remove_image = remove_image

    def to_schema(self) -> TaskUpdate:

        return TaskUpdate(

            title=self.title,

            description=self.description,

            priority=self.priority,

            start_date=self.start_date,

            end_date=self.end_date,

            status=self.status,

            assigned_user_id=self.assigned_user_id,

            activity_type_id=self.activity_type_id,

            milestone_id=self.milestone_id,

            boq_id=self.boq_id,
        )


class TaskOut(BaseSchema):

    id: int

    project_id: int

    milestone_id: Optional[int] = None

    boq_id: Optional[int] = None

    title: str

    description: Optional[str] = None

    priority: TaskPriority

    status: TaskStatus

    start_date: Optional[date] = None

    end_date: Optional[date] = None

    actual_start_date: Optional[date] = None

    actual_end_date: Optional[date] = None

    created_by_user_id: int

    assigned_user_id: Optional[int]

    completion_percentage: float

    is_delayed: bool

    execution_duration: int = 0

    delay_days: int = 0

    actual_cost: float = 0.0

    planned_cost: float = 0.0

    # ================= TASK MEDIA =================

    audio_instruction_url: Optional[str] = None

    instruction_image_url: Optional[str] = None

    task_icon: Optional[str] = None


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
    task_id: Optional[int] = None
    report_date: date
    # report_date: date = Field(default_factory=date.today)

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


# ===================== DSR PHOTO OUTPUT =====================


class DSRPhotoOut(BaseModel):
    id: int
    file_url: str

    class Config:
        from_attributes = True


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

    photos: list[DSRPhotoOut] = []

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
        return validate_non_empty_string(v)

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
    task_id: Optional[int] = None
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

    @field_validator("date")
    def validate_date(cls, v):

        if v > date.today():
            raise ValueError("Future date not allowed")

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
    status: ChecklistStatus
    remarks: Optional[str] = None


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
    dsr_id: Optional[int] = None
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
    date: Optional[dt_date] = None
    remarks: Optional[str] = None

    @field_validator("drawing_name", "version")
    def validate_fields(cls, v):
        return validate_non_empty_string(v)


class DrawingUpdate(BaseSchema):
    drawing_name: Optional[str] = None
    version: Optional[str] = None
    date: Optional[dt_date] = None
    remarks: Optional[str] = None

    @field_validator("drawing_name", "version")
    def validate_fields(cls, v):
        return validate_non_empty_string(v)


class DrawingOut(DrawingCreate):
    id: int
    file_url: str

    approval_status: Optional[DocumentStatus] = None
    approval_id: Optional[int] = None

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
        return validate_non_empty_string(v)


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
        return validate_non_empty_string(v)


# =========================================================
# WORK ACTIVITY CREATE


class WorkActivityCreate(BaseSchema):

    project_id: int = Field(gt=0)

    boq_code: Optional[int] = Field(
        default=None,
        gt=0,
    )

    activity_name: str = Field(
        min_length=1,
        max_length=255,
    )

    planned_quantity: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    unit: str = Field(
        min_length=1,
        max_length=50,
    )

    start_date: date

    end_date: date

    work_order_id: int = Field(gt=0)

    engineer_id: Optional[int] = Field(
        default=None,
        gt=0,
    )

    # ================= ACTIVITY NAME =================

    @field_validator("activity_name")
    def validate_activity(cls, v):

        return validate_activity_name(v)

    # ================= UNIT =================

    @field_validator("unit")
    def validate_activity_unit(cls, v):

        return validate_unit(v)

    # ================= DATE RANGE =================

    @field_validator("end_date")
    def validate_dates(cls, v, info: ValidationInfo):

        return validate_start_end_dates(
            info.data.get("start_date"),
            v,
        )

    # ================= DATE VALIDATION =================

    @field_validator("start_date", "end_date")
    def validate_activity_dates(cls, v):

        return validate_work_activity_date(v)


# =========================================================
# WORK ACTIVITY UPDATE


class WorkActivityUpdate(BaseSchema):

    activity_name: Optional[str] = None

    planned_quantity: Optional[Decimal] = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    unit: Optional[str] = None

    start_date: Optional[date] = None

    end_date: Optional[date] = None

    engineer_id: Optional[int] = Field(
        default=None,
        gt=0,
    )

    # ================= ACTIVITY NAME =================

    @field_validator("activity_name")
    def validate_activity(cls, v):

        return validate_activity_name(v)

    # ================= UNIT =================

    @field_validator("unit")
    def validate_activity_unit(cls, v):

        return validate_unit(v)

    # ================= DATE RANGE =================

    @field_validator("end_date")
    def validate_dates(cls, v, info: ValidationInfo):

        return validate_start_end_dates(
            info.data.get("start_date"),
            v,
        )

    # ================= DATE VALIDATION =================

    @field_validator("start_date", "end_date")
    def validate_activity_dates(cls, v):

        if v is None:
            return v

        return validate_work_activity_date(v)


# =========================================================
# WORK ACTIVITY RESPONSE


class WorkActivityResponse(BaseSchema):

    id: int

    project_id: int

    work_order_id: int

    boq_code: Optional[int] = None

    activity_name: str = Field(
        min_length=1,
        max_length=255,
    )

    planned_quantity: Decimal

    total_completed: Decimal

    remaining_quantity: Decimal

    completion_percentage: Decimal

    unit: str = Field(
        min_length=1,
        max_length=50,
    )

    start_date: date

    end_date: date

    status: WorkActivityStatus

    engineer_id: Optional[int] = None

    discipline: Optional[str] = None

    created_at: datetime

    class Config:
        from_attributes = True


# =========================================================
# DAILY PROGRESS CREATE


class DailyProgressCreate(BaseSchema):

    activity_id: int = Field(gt=0)

    entry_date: date

    today_progress: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    remarks: Optional[str] = Field(
        default=None,
        max_length=500,
    )

    # ================= ENTRY DATE =================

    @field_validator("entry_date")
    def validate_entry_dates(cls, v):

        return validate_progress_date(v)

    # ================= REMARKS =================

    @field_validator("remarks")
    def validate_remarks(cls, v):

        return validate_progress_remarks(v)


# =========================================================
# DAILY PROGRESS UPDATE
class DailyProgressUpdate(BaseSchema):

    today_progress: Optional[Decimal] = Field(
        default=None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    remarks: Optional[str] = Field(
        default=None,
        max_length=500,
    )

    # ================= REMARKS =================

    @field_validator("remarks")
    def validate_remarks(cls, v):

        return validate_progress_remarks(v)


# =========================================================
# DAILY PROGRESS RESPONSE
class DailyProgressResponse(BaseSchema):

    id: int

    activity_id: int

    entry_date: date

    today_progress: Decimal

    remarks: Optional[str] = None

    created_by: Optional[int] = None

    created_at: datetime

    class Config:
        from_attributes = True


# =========================================================
# DAILY PROGRESS WITH ACTIVITY RESPONSE
class DailyProgressWithActivityResponse(BaseSchema):

    message: str

    progress: DailyProgressResponse

    activity: WorkActivityResponse

    class Config:
        from_attributes = True


# =========================================================
# PROJECTS MODULE SUMMARY


class ProjectsModuleSummary(BaseSchema):

    total_projects: int

    ongoing_sites: int

    completed_projects: int

    delayed_projects: int


# =========================================================
# PROJECT ACTIVITY ITEM


class ProjectActivityItem(BaseSchema):

    type: str

    user_name: str

    description: str

    project_name: str

    timestamp: datetime

    # ================= STRING VALIDATIONS =================

    @field_validator(
        "type",
        "user_name",
        "description",
        "project_name",
    )
    def validate_strings(cls, v):

        return validate_non_empty_string(v)


# =========================================================
# PROJECTS MODULE RESPONSE


class ProjectsModuleResponse(BaseSchema):

    summary: ProjectsModuleSummary

    activities: List[ProjectActivityItem]


# =======================PROJECTS ot-policy==================================


class ProjectOTPolicyCreate(BaseModel):

    policy_type: OTPolicyType

    normal_day_multiplier: Optional[Decimal] = Decimal("1.5")

    sunday_multiplier: Optional[Decimal] = Decimal("2.0")

    holiday_multiplier: Optional[Decimal] = Decimal("3.0")

    fixed_ot_rate: Optional[Decimal] = None

    @model_validator(mode="after")
    def validate_policy(self):

        if self.policy_type == OTPolicyType.FIXED_RATE:
            if self.fixed_ot_rate is None or self.fixed_ot_rate <= 0:
                raise ValueError("fixed_ot_rate must be > 0 for FixedRate policy")
        else:
            if self.normal_day_multiplier is not None and self.normal_day_multiplier <= 0:
                raise ValueError("normal_day_multiplier must be > 0")
            if self.sunday_multiplier is not None and self.sunday_multiplier <= 0:
                raise ValueError("sunday_multiplier must be > 0")
            if self.holiday_multiplier is not None and self.holiday_multiplier <= 0:
                raise ValueError("holiday_multiplier must be > 0")

        return self


class ProjectOTPolicyOut(ProjectOTPolicyCreate):

    id: int

    project_id: int

    class Config:
        from_attributes = True

        json_encoders = {Decimal: float}
