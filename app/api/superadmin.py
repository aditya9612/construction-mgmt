from typing import Optional, List
from fastapi import APIRouter, Depends, Query, Path, Body
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db_session
from app.core.dependencies import require_super_admin, get_request_redis, security
from app.models.user import User
from app.schemas.superadmin import (
    DashboardStatsOut,
    CompanyOut,
    CompanyCreate,
    CompanyUpdate,
    CompanyStatusUpdate,
    CompanyStatsOut,
    CompanyAdminCreate,
    TenantUserOut,
    TenantUserStatusUpdate,
    PlanOut,
    PlanCreate,
    PlanUpdate,
    SubscriptionOut,
    SubscriptionCreate,
    SubscriptionUpdate,
    EntitlementOut,
    AuditLogOut,
    SubscriptionInvoiceOut,
    BillingReconciliationOut,
    PlatformBillingReconciliationOut,
    BillingWebhookEventOut,
    ManualPaymentTransactionOut,
    ManualPaymentRejectRequest,
    SuperAdminProfileOut,
    SuperAdminProfileUpdate,
    SuperAdminChangePassword,
    SuperAdminPasswordChangeResponse,
)
from app.schemas.user import UserOut
from app.schemas.base import PaginatedResponse
from app.services.superadmin import get_superadmin_service, SuperAdminService

router = APIRouter(
    prefix="/superadmin",
    tags=["superadmin"],
    dependencies=[Depends(require_super_admin)],
)


# =============================================================================
# 0. PROFILE & PASSWORD MANAGEMENT
# =============================================================================

@router.get("/profile", response_model=SuperAdminProfileOut)
async def get_superadmin_profile(
    current_user: User = Depends(require_super_admin),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_profile(current_user)


@router.put("/profile", response_model=SuperAdminProfileOut)
async def update_superadmin_profile(
    data: SuperAdminProfileUpdate = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_profile(db, current_user, data, redis=redis)


@router.post("/change-password", response_model=SuperAdminPasswordChangeResponse)
async def change_superadmin_password(
    data: SuperAdminChangePassword = Body(...),
    current_user: User = Depends(require_super_admin),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    current_token = credentials.credentials if credentials else None
    return await service.change_password(
        db, current_user, data, redis=redis, current_token=current_token
    )


# =============================================================================
# 1. PLATFORM DASHBOARD
# =============================================================================

@router.get("/dashboard-stats", response_model=DashboardStatsOut)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_dashboard_stats(db)


# =============================================================================
# 2. TENANT / COMPANY MANAGEMENT
# =============================================================================

@router.get("/companies", response_model=PaginatedResponse[CompanyOut])
async def list_companies(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    search: Optional[str] = Query(None, description="Search by name or subdomain"),
    subscription_status: Optional[str] = Query(None, description="Filter by subscription status"),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_companies(
        db, limit=limit, offset=offset, is_active=is_active, search=search, subscription_status=subscription_status
    )


@router.post("/companies", response_model=CompanyOut)
async def create_company(
    data: CompanyCreate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.create_company(db, current_user, data)


@router.get("/companies/{company_id}", response_model=CompanyOut)
async def get_company(
    company_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_company(db, company_id)


@router.put("/companies/{company_id}", response_model=CompanyOut)
async def update_company(
    company_id: int = Path(..., ge=1),
    data: CompanyUpdate = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company(db, company_id, current_user, data)


@router.put("/companies/{company_id}/status", response_model=CompanyOut)
async def update_company_status(
    company_id: int = Path(..., ge=1),
    data: CompanyStatusUpdate = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company_status(
        db, company_id, current_user, is_active=data.is_active, reason=data.reason
    )


@router.post("/companies/{company_id}/activate", response_model=CompanyOut)
async def activate_company(
    company_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company_status(db, company_id, current_user, is_active=True)


@router.post("/companies/{company_id}/suspend", response_model=CompanyOut)
async def suspend_company(
    company_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company_status(db, company_id, current_user, is_active=False)


@router.delete("/companies/{company_id}")
async def delete_company(
    company_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.delete_company(db, company_id, current_user)


@router.get("/companies/{company_id}/stats", response_model=CompanyStatsOut)
async def get_company_stats(
    company_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_company_stats(db, company_id)


# =============================================================================
# 3. TENANT USER MANAGEMENT
# =============================================================================

@router.get("/companies/{company_id}/users", response_model=PaginatedResponse[TenantUserOut])
async def list_company_users(
    company_id: int = Path(..., ge=1),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    role: Optional[str] = Query(None, description="Filter by role"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    search: Optional[str] = Query(None, description="Search name, email, or mobile"),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_company_users(
        db, company_id=company_id, limit=limit, offset=offset, role=role, is_active=is_active, search=search
    )


@router.post("/companies/{company_id}/admin", response_model=UserOut)
async def create_company_admin(
    company_id: int = Path(..., ge=1),
    data: CompanyAdminCreate = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.create_company_admin(db, company_id, current_user, data)


@router.get("/companies/{company_id}/users/{user_id}", response_model=TenantUserOut)
async def get_company_user(
    company_id: int = Path(..., ge=1),
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_company_user(db, company_id=company_id, user_id=user_id)


@router.put("/companies/{company_id}/users/{user_id}/status", response_model=TenantUserOut)
async def update_company_user_status(
    company_id: int = Path(..., ge=1),
    user_id: int = Path(..., ge=1),
    data: TenantUserStatusUpdate = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company_user_status(
        db, company_id=company_id, user_id=user_id, current_user=current_user, is_active=data.is_active
    )


@router.post("/companies/{company_id}/users/{user_id}/activate", response_model=TenantUserOut)
async def activate_company_user(
    company_id: int = Path(..., ge=1),
    user_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company_user_status(
        db, company_id=company_id, user_id=user_id, current_user=current_user, is_active=True
    )


@router.post("/companies/{company_id}/users/{user_id}/deactivate", response_model=TenantUserOut)
async def deactivate_company_user(
    company_id: int = Path(..., ge=1),
    user_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company_user_status(
        db, company_id=company_id, user_id=user_id, current_user=current_user, is_active=False
    )


# =============================================================================
# 4. SAAS PLANS MANAGEMENT
# =============================================================================

@router.get("/plans", response_model=List[PlanOut])
async def list_plans(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_plans(db, is_active=is_active)


@router.post("/plans", response_model=PlanOut)
async def create_plan(
    data: PlanCreate = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.create_plan(db, current_user, data)


@router.get("/plans/{plan_id}", response_model=PlanOut)
async def get_plan(
    plan_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_plan(db, plan_id)


@router.put("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: int = Path(..., ge=1),
    data: PlanUpdate = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_plan(db, plan_id, current_user, data)


@router.delete("/plans/{plan_id}")
async def delete_plan(
    plan_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.delete_plan(db, plan_id, current_user)


# =============================================================================
# 5. TENANT SUBSCRIPTIONS & ENTITLEMENTS
# =============================================================================

@router.get("/companies/{company_id}/subscription", response_model=SubscriptionOut)
async def get_company_subscription(
    company_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_company_subscription(db, company_id)


@router.post("/companies/{company_id}/subscription", response_model=SubscriptionOut)
async def assign_company_subscription(
    company_id: int = Path(..., ge=1),
    data: SubscriptionCreate = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.assign_company_subscription(db, company_id, current_user, data)


@router.put("/companies/{company_id}/subscription", response_model=SubscriptionOut)
async def update_company_subscription(
    company_id: int = Path(..., ge=1),
    data: SubscriptionUpdate = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company_subscription(db, company_id, current_user, data)


@router.post("/companies/{company_id}/subscription/activate", response_model=SubscriptionOut)
async def activate_company_subscription(
    company_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_subscription_status(db, company_id, current_user, new_status="active")


@router.post("/companies/{company_id}/subscription/suspend", response_model=SubscriptionOut)
async def suspend_company_subscription(
    company_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_subscription_status(db, company_id, current_user, new_status="suspended")


@router.post("/companies/{company_id}/subscription/cancel", response_model=SubscriptionOut)
async def cancel_company_subscription(
    company_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_subscription_status(db, company_id, current_user, new_status="cancelled")


@router.get("/companies/{company_id}/entitlements", response_model=EntitlementOut)
async def get_company_entitlements(
    company_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_company_entitlements(db, company_id)


@router.get("/companies/{company_id}/invoices", response_model=PaginatedResponse[SubscriptionInvoiceOut])
async def list_company_invoices(
    company_id: int = Path(..., ge=1),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_company_invoices(db, company_id, limit, offset)


@router.get("/billing/reconciliation", response_model=PlatformBillingReconciliationOut)
async def reconcile_platform_billing(
    batch_size: int = Query(50, ge=1, le=200, description="Batch size for tenant reconciliation"),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.reconcile_platform_billing(db, current_user, batch_size=batch_size)


@router.get("/companies/{company_id}/billing/reconciliation", response_model=BillingReconciliationOut)
async def reconcile_company_billing(

    company_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.reconcile_company_billing(db, company_id, current_user)


@router.get("/companies/{company_id}/billing-events", response_model=PaginatedResponse[BillingWebhookEventOut])
async def list_company_billing_events(
    company_id: int = Path(..., ge=1),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_company_billing_events(db, company_id, limit, offset)



# =============================================================================
# 6. PLATFORM AUDIT LOGS
# =============================================================================

@router.get("/audit-logs", response_model=PaginatedResponse[AuditLogOut])
async def list_platform_audit_logs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    entity: Optional[str] = Query(None, description="Filter by entity type (Company, Plan, User, etc)"),
    action: Optional[str] = Query(None, description="Filter by action (CREATE_COMPANY, SUSPEND_COMPANY, etc)"),
    performed_by: Optional[int] = Query(None, description="Filter by user id"),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_audit_logs(
        db, limit=limit, offset=offset, entity=entity, action=action, performed_by=performed_by
    )


@router.get("/companies/{company_id}/audit-logs", response_model=PaginatedResponse[AuditLogOut])
async def get_company_audit_logs(
    company_id: int = Path(..., ge=1),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_company_audit_logs(db, company_id, limit, offset)


# =============================================================================
# 7. MANUAL UPI PAYMENT VERIFICATION (PHASE 5.9B)
# =============================================================================

@router.get("/manual-payments", response_model=PaginatedResponse[ManualPaymentTransactionOut])
async def list_manual_payments(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by transaction status (e.g. pending, verified, rejected)"),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_manual_payments(db, limit=limit, offset=offset, status=status)


@router.post("/manual-payments/{transaction_id}/verify", response_model=ManualPaymentTransactionOut)
async def verify_manual_payment(
    transaction_id: int = Path(..., ge=1),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.verify_manual_payment(db, transaction_id, current_user)


@router.post("/manual-payments/{transaction_id}/reject", response_model=ManualPaymentTransactionOut)
async def reject_manual_payment(
    transaction_id: int = Path(..., ge=1),
    data: ManualPaymentRejectRequest = Body(...),
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.reject_manual_payment(db, transaction_id, current_user, data.rejection_reason)
