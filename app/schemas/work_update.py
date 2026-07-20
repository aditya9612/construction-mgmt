from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# =====================================================
# VALIDATION HELPERS
# =====================================================


def _require_meaningful_text(value: str, field_name: str, min_len: int) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} cannot be blank or whitespace only.")
    if len(stripped) < min_len:
        raise ValueError(
            f"{field_name} must be at least {min_len} characters of "
            f"meaningful text."
        )
    return stripped


def _optional_meaningful_text(
    value: Optional[str], field_name: str, min_len: int
) -> Optional[str]:
    if value is None:
        return value
    return _require_meaningful_text(value, field_name, min_len)


# =====================================================
# ENUMS
# =====================================================


class WorkUpdateStatus(str, Enum):
    DRAFT = "Draft"
    SUBMITTED = "Submitted"


class WorkUpdateImageType(str, Enum):
    BEFORE = "Before"
    AFTER = "After"


class ExportFormat(str, Enum):
    EXCEL = "excel"
    PDF = "pdf"


class WorkUpdateSortBy(str, Enum):
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    WORK_DATE = "work_date"
    TOTAL_HOURS = "total_hours"
    BUSINESS_ID = "business_id"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


# =====================================================
# CREATE (Draft)
# =====================================================


class WorkUpdateCreate(BaseModel):
    project_id: int
    task_id: int
    activity_type_id: int

    work_description: str = Field(..., min_length=3, max_length=2000)

    before_remarks: Optional[str] = Field(default=None, max_length=2000)

    work_date: date
    start_time: time

    location: str = Field(..., max_length=255)

    @field_validator("work_description")
    @classmethod
    def validate_work_description(cls, v: str) -> str:
        return _require_meaningful_text(v, "work_description", min_len=5)

    @field_validator("before_remarks")
    @classmethod
    def validate_before_remarks(cls, v: Optional[str]) -> Optional[str]:
        return _optional_meaningful_text(v, "before_remarks", min_len=2)

    @field_validator("location")
    @classmethod
    def validate_location(cls, v: str) -> str:
        return _require_meaningful_text(v, "location", min_len=2)

    @field_validator("work_date")
    @classmethod
    def validate_work_date_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("work_date cannot be in the future.")
        return v


# =====================================================
# UPDATE (Draft Only — PARTIAL update)
# =====================================================


class WorkUpdateUpdate(BaseModel):
    task_id: Optional[int] = None
    activity_type_id: Optional[int] = None

    work_description: Optional[str] = Field(default=None, min_length=3, max_length=2000)

    before_remarks: Optional[str] = Field(default=None, max_length=2000)

    work_date: Optional[date] = None

    start_time: Optional[time] = None

    location: Optional[str] = Field(default=None, max_length=255)

    @field_validator("work_description")
    @classmethod
    def validate_work_description(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _require_meaningful_text(v, "work_description", min_len=5)

    @field_validator("before_remarks")
    @classmethod
    def validate_before_remarks(cls, v: Optional[str]) -> Optional[str]:
        return _optional_meaningful_text(v, "before_remarks", min_len=2)

    @field_validator("location")
    @classmethod
    def validate_location(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _require_meaningful_text(v, "location", min_len=2)

    @field_validator("work_date")
    @classmethod
    def validate_work_date_not_future(cls, v: Optional[date]) -> Optional[date]:
        if v is None:
            return v
        if v > date.today():
            raise ValueError("work_date cannot be in the future.")
        return v


# =====================================================
# SUBMIT WORK
# =====================================================


class WorkUpdateSubmit(BaseModel):
    end_time: time

    total_hours: Decimal = Field(..., gt=0, le=24)

    after_remarks: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("after_remarks")
    @classmethod
    def validate_after_remarks(cls, v: Optional[str]) -> Optional[str]:
        return _optional_meaningful_text(v, "after_remarks", min_len=2)


# =====================================================
# IMAGE RESPONSE
# =====================================================


class WorkUpdateImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    image_type: WorkUpdateImageType
    image_url: str
    display_order: int


# =====================================================
# RESPONSE
# =====================================================


class WorkUpdateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    business_id: str

    project_id: int
    task_id: int
    activity_type_id: int
    created_by_id: int

    work_description: str

    before_remarks: Optional[str] = None
    after_remarks: Optional[str] = None

    work_date: date

    start_time: time
    end_time: Optional[time] = None

    total_hours: Optional[Decimal] = None

    location: Optional[str] = None

    status: WorkUpdateStatus

    created_at: datetime
    updated_at: datetime

    images: List[WorkUpdateImageOut] = Field(default_factory=list)


# =====================================================
# LIST ITEM (lightweight variant)
# =====================================================


class WorkUpdateListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    business_id: str

    project_id: int
    task_id: int
    activity_type_id: int

    work_date: date

    status: WorkUpdateStatus

    created_by_id: int

    created_at: datetime


# =====================================================
# LIST RESPONSE
# =====================================================


class WorkUpdateListOut(BaseModel):
    total: int
    items: List[WorkUpdateOut]
    page: int = 1
    page_size: int = 20
    total_pages: int = 0
    has_next: bool = False
    has_previous: bool = False
