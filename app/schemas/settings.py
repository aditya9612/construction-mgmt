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


class CompanySettingsUpdate(BaseModel):

    company_name: Optional[str] = None

    gst_number: Optional[str] = None

    mobile_number: Optional[str] = None

    email: Optional[str] = None

    website: Optional[str] = None

    address: Optional[str] = None

    bank_name: Optional[str] = None

    account_holder_name: Optional[str] = None

    account_number: Optional[str] = None

    ifsc_code: Optional[str] = None

    upi_id: Optional[str] = None

    terms_conditions: Optional[str] = None


class CompanySettingsOut(BaseModel):

    id: int

    company_name: Optional[str]

    company_logo: Optional[str]

    gst_number: Optional[str]

    mobile_number: Optional[str]

    email: Optional[str]

    website: Optional[str]

    address: Optional[str]

    bank_name: Optional[str]

    account_holder_name: Optional[str]

    account_number: Optional[str]

    ifsc_code: Optional[str]

    upi_id: Optional[str]

    signature_image: Optional[str]

    terms_conditions: Optional[str]

    class Config:
        from_attributes = True