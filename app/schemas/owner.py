from typing import Optional
from pydantic import Field, EmailStr, field_validator
from app.schemas.base import BaseSchema
import re


# -------------------------
# CREATE
# -------------------------
class OwnerCreate(BaseSchema):
    owner_name: str = Field(..., min_length=3, max_length=100)
    mobile: str = Field(..., pattern=r"^[0-9]{10}$")
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=255)
    pan: Optional[str] = None

    # PAN Validation
    @field_validator("pan")
    @classmethod
    def validate_pan(cls, value):
        if value is None:
            return value
        pattern = r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"
        if not re.match(pattern, value):
            raise ValueError("Invalid PAN format (ABCDE1234F)")
        return value


# -------------------------
# UPDATE
# -------------------------
class OwnerUpdate(BaseSchema):
    owner_name: Optional[str] = Field(None, min_length=3, max_length=100)
    mobile: Optional[str] = Field(None, pattern=r"^[0-9]{10}$")
    email: Optional[EmailStr] = None
    address: Optional[str] = Field(None, max_length=255)
    pan: Optional[str] = None

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, value):
        if value is None:
            return value
        pattern = r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$"
        if not re.match(pattern, value):
            raise ValueError("Invalid PAN format (ABCDE1234F)")
        return value


# -------------------------
# RESPONSE
# -------------------------
class OwnerOut(BaseSchema):
    id: int
    owner_name: str
    mobile: str
    email: Optional[EmailStr]
    address: Optional[str]
    pan: Optional[str]