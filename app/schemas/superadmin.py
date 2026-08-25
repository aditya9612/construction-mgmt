from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, constr, EmailStr
from app.schemas.base import BaseSchema


class DashboardStatsOut(BaseSchema):
    companies: int
    active_companies: int
    users: int
    projects: int


class CompanyOut(BaseSchema):
    id: int
    name: str
    subdomain: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    user_count: Optional[int] = None
    project_count: Optional[int] = None


class CompanyCreate(BaseSchema):
    name: constr(min_length=2, max_length=100) # type: ignore
    subdomain: constr(min_length=2, max_length=63, pattern=r"^[a-z0-9-]+$") # type: ignore


class CompanyUpdate(BaseSchema):
    name: Optional[constr(min_length=2, max_length=100)] = None # type: ignore
    subdomain: Optional[constr(min_length=2, max_length=63, pattern=r"^[a-z0-9-]+$")] = None # type: ignore


class CompanyStatusUpdate(BaseSchema):
    is_active: bool


class AuditLogOut(BaseSchema):
    id: int
    action: str
    entity: str
    entity_id: Optional[int]
    performed_by: Optional[int]
    details: Optional[dict]
    created_at: datetime


class CompanyAdminCreate(BaseSchema):
    email: EmailStr
    password: str
    full_name: str
    mobile: str
