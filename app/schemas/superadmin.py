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
    full_name: Optional[str] = None
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


class SubscriptionInvoiceOut(BaseSchema):
    id: int
    company_id: int
    subscription_id: int
    invoice_number: str
    status: str
    subtotal: float
    tax_amount: float
    total_amount: float
    currency: str
    issued_at: Optional[datetime] = None
    due_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    created_at: datetime


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


class BillingReconciliationOut(BaseSchema):
    company_id: int
    subscription_id: Optional[int] = None
    local_status: Optional[str] = None
    provider_name: str
    provider_subscription_id: Optional[str] = None
    provider_status: Optional[str] = None
    is_matched: bool
    has_drift: bool
    drift_type: str
    details: Optional[str] = None
    reconciled_at: datetime


class PlatformBillingReconciliationOut(BaseSchema):
    total_reconciled: int
    total_matched: int
    total_drifted: int
    total_unavailable: int
    results: List[BillingReconciliationOut]
    reconciled_at: datetime


class BillingWebhookEventOut(BaseSchema):

    id: int
    company_id: Optional[int] = None
    provider: str
    event_id: str
    event_type: str
    status: str
    payload_reference: Optional[str] = None
    processed_at: Optional[datetime] = None
    created_at: datetime


class ManualPaymentTransactionOut(BaseSchema):
    id: int
    company_id: int
    company_name: Optional[str] = None
    subscription_id: int
    plan_id: int
    plan_name: Optional[str] = None
    invoice_id: Optional[int] = None
    amount: float
    currency: str
    payment_method: str
    transaction_reference: str
    utr_reference: Optional[str] = None
    status: str
    rejection_reason: Optional[str] = None
    verified_by: Optional[int] = None
    verified_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    created_at: datetime


class ManualPaymentRejectRequest(BaseSchema):
    rejection_reason: constr(min_length=3, max_length=500)  # type: ignore


# =============================================================================
# 9. SUPER ADMIN PROFILE & PASSWORD MANAGEMENT
# =============================================================================

class SuperAdminProfileOut(BaseSchema):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    mobile: Optional[str] = None
    role: str
    is_super_admin: bool = True
    is_active: bool = True
    company_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SuperAdminProfileUpdate(BaseSchema):
    full_name: Optional[constr(min_length=2, max_length=100)] = None  # type: ignore
    mobile: Optional[constr(min_length=7, max_length=20)] = None  # type: ignore
    email: Optional[EmailStr] = None


class SuperAdminChangePassword(BaseSchema):
    current_password: str = Field(..., min_length=1)
    new_password: constr(min_length=6, max_length=128)  # type: ignore
    confirm_password: str = Field(..., min_length=1)


class SuperAdminPasswordChangeResponse(BaseSchema):
    success: bool = True
    message: str = "Password changed successfully"



