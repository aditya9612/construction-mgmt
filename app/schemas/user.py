from typing import Optional

from app.schemas.base import BaseSchema


class UserRoleSchema(BaseSchema):
    # Kept for API docs; DB uses `UserRole` enum.
    value: str


class UserCreate(BaseSchema):
    email: str
    password: str
    full_name: Optional[str] = None
    role: Optional[str] = None  # Defaults to "Site Engineer"


class UserLogin(BaseSchema):
    email: str
    password: str


class UserOut(BaseSchema):
    id: int
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool

