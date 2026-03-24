from datetime import date
from typing import Optional
import re

from pydantic import field_validator
from app.schemas.base import BaseSchema


# -------------------------
# CREATE
# -------------------------
class ProjectCreate(BaseSchema):
    owner_id: int
    name: str

    site_address: str
    site_area: float
    type: str

    start_date: date
    end_date: date

    estimated_duration: Optional[str]

    budget: float
    advance_paid: float = 0

    payment_terms: str
    engineer_name: str

    status: str

    # -------------------------
    # VALIDATIONS
    # -------------------------

    @field_validator("site_area")
    def validate_area(cls, v):
        if v <= 0:
            raise ValueError("Site area must be positive")
        return v

    @field_validator("type")
    def validate_type(cls, v):
        if v not in ["Residential", "Commercial"]:
            raise ValueError("Invalid project type")
        return v

    @field_validator("budget")
    def validate_budget(cls, v):
        if v <= 0:
            raise ValueError("Budget must be positive")
        return v

    @field_validator("advance_paid")
    def validate_advance(cls, v):
        if v < 0:
            raise ValueError("Advance cannot be negative")
        return v

    @field_validator("engineer_name")
    def validate_engineer(cls, v):
        if not re.fullmatch(r"[A-Za-z ]{5,50}", v):
            raise ValueError("Engineer name must be 5–50 characters")
        return v

    @field_validator("status")
    def validate_status(cls, v):
        if v not in ["Planned", "Ongoing", "Completed", "Hold"]:
            raise ValueError("Invalid project status")
        return v

    @field_validator("end_date")
    def validate_dates(cls, v, info):
        start = info.data.get("start_date")
        if start and v < start:
            raise ValueError("End date must be after start date")
        return v


# -------------------------
# UPDATE
# -------------------------
class ProjectUpdate(BaseSchema):
    name: Optional[str]

    site_address: Optional[str]
    site_area: Optional[float]
    type: Optional[str]

    start_date: Optional[date]
    end_date: Optional[date]

    estimated_duration: Optional[str]

    budget: Optional[float]
    advance_paid: Optional[float]

    payment_terms: Optional[str]
    engineer_name: Optional[str]

    status: Optional[str]


# -------------------------
# RESPONSE
# -------------------------
class ProjectOut(BaseSchema):
    id: int
    owner_id: int

    name: str
    site_address: str
    site_area: float
    type: str

    start_date: date
    end_date: date

    estimated_duration: Optional[str]

    budget: float
    advance_paid: float
    remaining_balance: float

    payment_terms: str
    engineer_name: str

    status: str