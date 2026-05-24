from enum import Enum
from typing import Optional, Dict, Any
from app.schemas.base import BaseSchema
from app.core.validators import (
    validate_mobile,
    validate_non_empty_string,
    validate_gst,
    validate_ifsc,
    validate_upi,
    validate_account_number,
)
from pydantic import (
    BaseModel,
    field_validator,
    EmailStr,
    HttpUrl,
)

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

    email: Optional[EmailStr] = None

    website: Optional[HttpUrl] = None

    address: Optional[str] = None

    instagram_handle: Optional[str] = None

    whatsapp_number: Optional[str] = None

    bank_name: Optional[str] = None

    account_holder_name: Optional[str] = None

    account_number: Optional[str] = None

    ifsc_code: Optional[str] = None

    upi_id: Optional[str] = None

    terms_conditions: Optional[str] = None

    # =====================================
    # STRING CLEANING
    # =====================================

    @field_validator(
        "company_name",
        "bank_name",
        "account_holder_name",
        "address",
        "terms_conditions",
        mode="before"
    )
    @classmethod
    def validate_strings(cls, v):

        if v is None:
            return v

        return validate_non_empty_string(v)

    # =====================================
    # MOBILE
    # =====================================

    @field_validator(
        "mobile_number",
        "whatsapp_number",
        mode="before"
    )
    @classmethod
    def validate_mobile_fields(cls, v):

        return validate_mobile(v)

    # =====================================
    # GST
    # =====================================

    @field_validator(
        "gst_number",
        mode="before"
    )
    @classmethod
    def validate_gst_field(cls, v):

        return validate_gst(v)

    # =====================================
    # IFSC
    # =====================================

    @field_validator(
        "ifsc_code",
        mode="before"
    )
    @classmethod
    def validate_ifsc_field(cls, v):

        return validate_ifsc(v)

    # =====================================
    # UPI
    # =====================================

    @field_validator(
        "upi_id",
        mode="before"
    )
    @classmethod
    def validate_upi_field(cls, v):

        return validate_upi(v)
    
    @field_validator(
        "account_number",
        mode="before"
    )
    @classmethod
    def validate_account_number_field(cls, v):

        return validate_account_number(v)


class CompanySettingsOut(BaseModel):

    id: int

    company_name: Optional[str]

    company_logo: Optional[str]

    gst_number: Optional[str]

    mobile_number: Optional[str]

    email: Optional[EmailStr]

    website: Optional[HttpUrl] = None

    address: Optional[str]

    instagram_handle: Optional[str]
    
    whatsapp_number: Optional[str]

    bank_name: Optional[str]

    account_holder_name: Optional[str]

    account_number: Optional[str]

    ifsc_code: Optional[str]

    upi_id: Optional[str]

    signature_image: Optional[str]

    terms_conditions: Optional[str]

    class Config:
        from_attributes = True