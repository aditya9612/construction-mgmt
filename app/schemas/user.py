from datetime import date, datetime
from typing import Any, Optional
from pydantic import EmailStr, Field, field_validator, model_validator
from app.schemas.base import BaseSchema
from app.core.validators import (
    validate_pan,
    validate_aadhaar,
    validate_mobile,
    validate_full_name,
    validate_joining_date,
    validate_password,
)


class UserRoleSchema(BaseSchema):
    value: str


class UserCreate(BaseSchema):
    email: str
    password: str
    full_name: Optional[str] = None
    role: Optional[str] = None


class UserLogin(BaseSchema):
    mobile: str


class OTPLoginResponse(BaseSchema):
    message: str
    mobile: str


class OTPRequest(BaseSchema):
    mobile: str


class OTPVerify(BaseSchema):
    mobile: str
    otp: str


# -------- CREATE --------
class UserCreatePayload(BaseSchema):
    # email: Optional[EmailStr] = None
    email: EmailStr
    password: Optional[str] = None
    mobile_number: str
    full_name: Optional[str] = None
    role: str
    address: Optional[str] = None
    pan_number: Optional[str] = None
    aadhaar_number: Optional[str] = None
    designation: Optional[str] = Field(None, max_length=100)
    joining_date: Optional[date] = None
    is_active: bool = True

    @field_validator("pan_number")
    @classmethod
    def pan_validator(cls, v):
        return validate_pan(v)

    @field_validator("aadhaar_number")
    @classmethod
    def aadhaar_validator(cls, v):
        return validate_aadhaar(v)

    @field_validator("mobile_number")
    @classmethod
    def mobile_validator(cls, v):
        return validate_mobile(v)

    @field_validator("full_name")
    @classmethod
    def full_name_validator(cls, v):
        return validate_full_name(v)

    @field_validator("joining_date")
    @classmethod
    def joining_date_validator(cls, v):
        return validate_joining_date(v)

    @field_validator("password")
    @classmethod
    def password_validator(cls, v):
        return validate_password(v)


# -------- UPDATE --------
class UserUpdatePayload(BaseSchema):
    full_name: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[str] = None
    address: Optional[str] = None
    pan_number: Optional[str] = None
    aadhaar_number: Optional[str] = None
    designation: Optional[str] = Field(None, max_length=100)
    joining_date: Optional[date] = None
    is_active: Optional[bool] = None

    @field_validator("pan_number")
    @classmethod
    def pan_validator(cls, v):
        return validate_pan(v)

    @field_validator("aadhaar_number")
    @classmethod
    def aadhaar_validator(cls, v):
        return validate_aadhaar(v)

    @field_validator("mobile_number")
    @classmethod
    def mobile_validator(cls, v):
        return validate_mobile(v)

    @field_validator("full_name")
    @classmethod
    def full_name_validator(cls, v):
        return validate_full_name(v)

    @field_validator("joining_date")
    @classmethod
    def joining_date_validator(cls, v):
        return validate_joining_date(v)


class UserOut(BaseSchema):
    user_id: int
    full_name: Optional[str]
    role: str
    mobile_number: Optional[str]
    email: Optional[str]
    address: Optional[str]
    pan_number: Optional[str]
    aadhaar_number: Optional[str]
    profile_image: Optional[str]
    designation: Optional[str]
    joining_date: Optional[date]
    is_active: bool

    @model_validator(mode="before")
    @classmethod
    def from_orm_adapter(cls, data: Any) -> Any:
        if hasattr(data, "__tablename__") and data.__tablename__ == "users":
            return {
                "user_id": data.id,
                "full_name": data.full_name,
                "role": data.role.value if hasattr(data.role, "value") else data.role,
                "mobile_number": data.mobile,
                "email": data.email,
                "address": getattr(data, "address", None),
                "pan_number": getattr(data, "pan_number", None),
                "aadhaar_number": getattr(data, "aadhaar_number", None),
                "profile_image": getattr(data, "profile_image", None),
                "designation": getattr(data, "designation", None),
                "joining_date": getattr(data, "joining_date", None),
                "is_active": data.is_active,
            }
        return data


class UserAuditOut(BaseSchema):
    id: int
    user_id: int
    field_name: str
    old_value: Optional[str]
    new_value: Optional[str]
    changed_by: Optional[int]
    changed_at: datetime
