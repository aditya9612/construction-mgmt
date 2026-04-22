from datetime import date, datetime
import re
from typing import Any, Optional

from pydantic import EmailStr, field_validator, model_validator

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


# -------- CREATE --------
class UserCreatePayload(BaseSchema):
    # email: Optional[EmailStr] = None
    email: Optional[EmailStr]
    password: Optional[str] = None
    mobile_number: Optional[str] = None
    full_name: Optional[str] = None
    role: str
    address: Optional[str] = None
    pan_number: Optional[str] = None
    aadhaar_number: Optional[str] = None
    designation: Optional[str] = None
    joining_date: Optional[date] = None
    is_active: bool = True

    @field_validator("pan_number")
    @classmethod
    def validate_pan(cls, v):
        if v is None:
            return v

        v = v.strip().upper() 

        if not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", v):
            raise ValueError("Invalid PAN format (e.g., ABCDE1234F)")

        return v

    @field_validator("aadhaar_number")
    @classmethod
    def validate_aadhaar(cls, v):
        if v is None:
            return v

        v = v.replace(" ", "").strip() 

        if not re.match(r"^[0-9]{12}$", v):
            raise ValueError("Aadhaar must be 12 digits")

        return v
    
    @field_validator("mobile_number")
    @classmethod
    def validate_mobile(cls, v):
        if v is None:
            return v

        # normalize (same idea as your helper)
        digits = "".join(c for c in v if c.isdigit())

        # handle +91 / 91 / 0
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]

        # final validation
        if not re.match(r"^[6-9][0-9]{9}$", digits):
            raise ValueError("Invalid Indian mobile number")

        return digits


# -------- UPDATE --------
class UserUpdatePayload(BaseSchema):
    full_name: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[str] = None
    address: Optional[str] = None
    pan_number: Optional[str] = None
    aadhaar_number: Optional[str] = None
    designation: Optional[str] = None
    joining_date: Optional[date] = None
    is_active: Optional[bool] = None

    @field_validator("pan_number")
    @classmethod
    def validate_pan(cls, v):
        if v is None:
            return v

        v = v.strip().upper()  

        if not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", v):
            raise ValueError("Invalid PAN format (e.g., ABCDE1234F)")

        return v

    @field_validator("aadhaar_number")
    @classmethod
    def validate_aadhaar(cls, v):
        if v is None:
            return v

        v = v.replace(" ", "").strip() 

        if not re.match(r"^[0-9]{12}$", v):
            raise ValueError("Aadhaar must be 12 digits")

        return v
    
    @field_validator("mobile_number")
    @classmethod
    def validate_mobile(cls, v):
        if v is None:
            return v

        digits = "".join(c for c in v if c.isdigit())

        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]

        if not re.match(r"^[6-9][0-9]{9}$", digits):
            raise ValueError("Invalid Indian mobile number")

        return digits


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
