from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel

from app.schemas.base import BaseSchema


class UnitType(str, Enum):
    KG = "Kg"
    FEET = "Feet"
    METER = "Meter"


# ================= UPDATE =================
class UserSettingsUpdate(BaseSchema):
    default_project_id: Optional[int] = None
    unit: Optional[UnitType] = None
    notifications_enabled: Optional[bool] = None
    preferences: Optional[Dict] = None

    # NEW
    financial_year: Optional[str] = None
    currency: Optional[str] = None
    tax_settings: Optional[Dict[str, Any]] = None
    invoice_format: Optional[str] = None
    payment_terms: Optional[str] = None


# ================= OUTPUT =================
class UserSettingsOut(BaseSchema):
    user_id: int
    default_project_id: Optional[int]
    unit: UnitType
    notifications_enabled: bool
    preferences: Optional[Dict]

    financial_year: Optional[str]
    currency: Optional[str]
    tax_settings: Optional[Dict[str, Any]]
    invoice_format: Optional[str]
    payment_terms: Optional[str]

    class Config:
        from_attributes = True