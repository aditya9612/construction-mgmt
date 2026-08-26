from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, constr, EmailStr
from app.schemas.base import BaseSchema


class AuditLogOut(BaseSchema):
    id: int
    action: str
    entity: str
    entity_id: Optional[int] = None
    performed_by: Optional[int] = None
    details: Optional[Dict[str, Any]] = None
    created_at: datetime


class DashboardStatsOut(BaseSchema):
    companies: int = 0
    active_companies: int = 0
    suspended_companies: int = 0
    users: int = 0
    active_users: int = 0
    projects: int = 0
    active_projects: int = 0
    plans_count: int = 0
    subscriptions_count: int = 0
    subscription_distribution: Dict[str, int] = Field(default_factory=dict)
    expiring_subscriptions: int = 0
    recent_activity: List[AuditLogOut] = Field(default_factory=list)


class CompanyOut(BaseSchema):
    id: int
    name: str
    subdomain: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    user_count: Optional[int] = None
    project_count: Optional[int] = None
    subscription_status: Optional[str] = None
    plan_name: Optional[str] = None


class CompanyCreate(BaseSchema):
    name: constr(min_length=2, max_length=100)  # type: ignore
    subdomain: constr(min_length=2, max_length=63, pattern=r"^[a-z0-9-]+$")  # type: ignore
    plan_id: Optional[int] = None


class CompanyUpdate(BaseSchema):
    name: Optional[constr(min_length=2, max_length=100)] = None  # type: ignore
    subdomain: Optional[constr(min_length=2, max_length=63, pattern=r"^[a-z0-9-]+$")] = None  # type: ignore


class CompanyStatusUpdate(BaseSchema):
    is_active: bool
    reason: Optional[str] = None


class CompanyStatsOut(BaseSchema):
    company_id: int
    company_name: str
    total_projects: int = 0
    active_projects: int = 0
    completed_projects: int = 0
    total_users: int = 0
    active_users: int = 0
    users_by_role: Dict[str, int] = Field(default_factory=dict)
    subscription_status: str = "trial"
    plan_name: str = "Standard Trial"


class CompanyAdminCreate(BaseSchema):
    email: EmailStr
    password: str
    full_name: str
    mobile: str


class TenantUserOut(BaseSchema):
    id: int
    email: str
    full_name: str
    mobile: Optional[str] = None
    role: str
    is_active: bool
    company_id: Optional[int] = None
    created_at: Optional[datetime] = None


class TenantUserStatusUpdate(BaseSchema):
    is_active: bool


class PlanOut(BaseSchema):
    id: int
    name: str
    code: str
    description: Optional[str] = None
    price: float = 0.0
    billing_interval: str = "monthly"
    currency: str = "INR"
    features: Optional[Dict[str, Any]] = None
    is_active: bool = True
    created_at: datetime


class PlanCreate(BaseSchema):
    name: constr(min_length=2, max_length=100)  # type: ignore
    code: constr(min_length=2, max_length=50, pattern=r"^[a-z0-9_-]+$")  # type: ignore
    description: Optional[str] = None
    price: float = 0.0
    billing_interval: str = "monthly"
    currency: str = "INR"
    features: Optional[Dict[str, Any]] = None
    is_active: bool = True


class PlanUpdate(BaseSchema):
    name: Optional[constr(min_length=2, max_length=100)] = None  # type: ignore
    description: Optional[str] = None
    price: Optional[float] = None
    billing_interval: Optional[str] = None
    currency: Optional[str] = None
    features: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class SubscriptionOut(BaseSchema):
    id: int
    company_id: int
    plan_id: int
    plan_name: Optional[str] = None
    plan_code: Optional[str] = None
    status: str
    start_date: datetime
    end_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    auto_renew: bool = True
    created_at: datetime


class SubscriptionCreate(BaseSchema):
    plan_id: int
    status: str = "active"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    auto_renew: bool = True


class SubscriptionUpdate(BaseSchema):
    plan_id: Optional[int] = None
    status: Optional[str] = None
    end_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    auto_renew: Optional[bool] = None


class EntitlementOut(BaseSchema):
    plan_id: Optional[int] = None
    plan_name: str
    plan_code: str
    status: str
    is_active: bool
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    auto_renew: bool = True
    max_users: int = 10
    max_projects: int = 5
    storage_gb: int = 5
    advanced_reports: bool = True
    payroll: bool = True
    equipment: bool = True
    ai_features: bool = False
    features: Dict[str, Any] = Field(default_factory=dict)
