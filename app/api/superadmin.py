from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db_session
from app.core.dependencies import require_super_admin
from app.models.user import User
from app.schemas.superadmin import (
    DashboardStatsOut,
    CompanyOut,
    CompanyCreate,
    CompanyUpdate,
    CompanyStatusUpdate,
    AuditLogOut,
    CompanyAdminCreate,
)
from app.schemas.user import UserOut
from app.schemas.base import PaginatedResponse
from app.services.superadmin import get_superadmin_service, SuperAdminService

router = APIRouter(
    prefix="/superadmin",
    tags=["superadmin"],
    dependencies=[Depends(require_super_admin)],
)

@router.get("/dashboard-stats", response_model=DashboardStatsOut)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_dashboard_stats(db)

@router.get("/companies", response_model=PaginatedResponse[CompanyOut])
async def list_companies(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_companies(db, limit, offset)

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
    company_id: int,
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.get_company(db, company_id)

@router.put("/companies/{company_id}", response_model=CompanyOut)
async def update_company(
    company_id: int,
    data: CompanyUpdate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company(db, company_id, current_user, data)

@router.put("/companies/{company_id}/status", response_model=CompanyOut)
async def update_company_status(
    company_id: int,
    data: CompanyStatusUpdate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.update_company_status(db, company_id, current_user, data.is_active)

@router.delete("/companies/{company_id}")
async def delete_company(
    company_id: int,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.delete_company(db, company_id, current_user)

@router.get("/companies/{company_id}/audit-logs", response_model=PaginatedResponse[AuditLogOut])
async def get_company_audit_logs(
    company_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.list_company_audit_logs(db, company_id, limit, offset)

@router.post("/companies/{company_id}/admin", response_model=UserOut)
async def create_company_admin(
    company_id: int,
    data: CompanyAdminCreate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
    service: SuperAdminService = Depends(get_superadmin_service),
):
    return await service.create_company_admin(db, company_id, current_user, data)
