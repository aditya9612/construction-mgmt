from datetime import date
from typing import Any, Optional

from pydantic import model_validator

from app.schemas.base import BaseSchema


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


class UserCreatePayload(BaseSchema):
    """Create user - provide either email+password or mobile_number."""
    email: Optional[str] = None
    password: Optional[str] = None
    mobile_number: Optional[str] = None
    full_name: Optional[str] = None
    role: str
    address: Optional[str] = None
    pan_number: Optional[str] = None
    aadhaar_number: Optional[str] = None
    profile_image: Optional[str] = None
    designation: Optional[str] = None
    joining_date: Optional[date] = None
    is_active: bool = True


class UserUpdatePayload(BaseSchema):
    """Update user - all fields optional."""
    full_name: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    address: Optional[str] = None
    pan_number: Optional[str] = None
    aadhaar_number: Optional[str] = None
    profile_image: Optional[str] = None
    designation: Optional[str] = None
    joining_date: Optional[date] = None
    is_active: Optional[bool] = None


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

