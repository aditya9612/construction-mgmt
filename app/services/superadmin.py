from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.exc import IntegrityError
from app.models.company import Company
from app.models.settings import CompanySettings
from app.models.user import User, ActivityLog, UserRole
from app.core.security import get_password_hash
from app.models.project import Project
from app.schemas.superadmin import (
    DashboardStatsOut,
    CompanyOut,
    CompanyCreate,
    CompanyUpdate,
    AuditLogOut,
    CompanyAdminCreate,
)
from app.schemas.user import UserOut
from app.utils.helpers import AppError, NotFoundError, ConflictError
from app.schemas.base import PaginatedResponse, PaginationMeta

class SuperAdminService:
    async def get_dashboard_stats(self, db: AsyncSession) -> DashboardStatsOut:
        companies_count = await db.scalar(select(func.count()).select_from(Company))
        active_companies_count = await db.scalar(
            select(func.count()).select_from(Company).where(Company.is_active == True)
        )
        users_count = await db.scalar(select(func.count()).select_from(User))
        projects_count = await db.scalar(select(func.count()).select_from(Project))

        return DashboardStatsOut(
            companies=companies_count or 0,
            active_companies=active_companies_count or 0,
            users=users_count or 0,
            projects=projects_count or 0,
        )

    async def list_companies(
        self, db: AsyncSession, limit: int, offset: int
    ) -> PaginatedResponse[CompanyOut]:
        query = select(Company).order_by(Company.id.desc())
        count_query = select(func.count()).select_from(Company)

        total = await db.scalar(count_query)
        rows = (await db.execute(query.limit(limit).offset(offset))).scalars().all()

        items = [
            CompanyOut(
                id=r.id,
                name=r.name,
                subdomain=r.subdomain or "",
                is_active=r.is_active,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
        return PaginatedResponse[CompanyOut](items=items, meta=meta)

    async def create_company(
        self, db: AsyncSession, current_user: User, data: CompanyCreate
    ) -> CompanyOut:
        # Validate subdomain uniqueness
        if data.subdomain:
            existing = await db.scalar(
                select(Company).where(Company.subdomain == data.subdomain)
            )
            if existing:
                raise ConflictError("Subdomain already in use")

        # Create Company
        company = Company(
            name=data.name,
            subdomain=data.subdomain,
            is_active=True,
        )
        db.add(company)
        await db.flush()

        # Create CompanySettings
        settings = CompanySettings(
            company_id=company.id,
            company_name=company.name,
        )
        db.add(settings)

        # Audit Log
        audit_log = ActivityLog(
            action="CREATE_COMPANY",
            entity="Company",
            entity_id=company.id,
            performed_by=current_user.id,
            details={"name": company.name, "subdomain": company.subdomain},
        )
        db.add(audit_log)

        await db.commit()
        await db.refresh(company)

        return CompanyOut(
            id=company.id,
            name=company.name,
            subdomain=company.subdomain or "",
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at,
        )

    async def get_company(self, db: AsyncSession, company_id: int) -> CompanyOut:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        user_count = await db.scalar(
            select(func.count()).select_from(User).where(User.company_id == company_id)
        )
        project_count = await db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.company_id == company_id)
        )

        return CompanyOut(
            id=company.id,
            name=company.name,
            subdomain=company.subdomain or "",
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at,
            user_count=user_count,
            project_count=project_count,
        )

    async def update_company(
        self,
        db: AsyncSession,
        company_id: int,
        current_user: User,
        data: CompanyUpdate,
    ) -> CompanyOut:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        if data.subdomain and data.subdomain != company.subdomain:
            existing = await db.scalar(
                select(Company).where(
                    Company.subdomain == data.subdomain, Company.id != company_id
                )
            )
            if existing:
                raise ConflictError("Subdomain already in use")
            company.subdomain = data.subdomain

        if data.name:
            company.name = data.name

        audit_log = ActivityLog(
            action="UPDATE_COMPANY",
            entity="Company",
            entity_id=company.id,
            performed_by=current_user.id,
            details={"name": company.name, "subdomain": company.subdomain},
        )
        db.add(audit_log)
        await db.commit()
        await db.refresh(company)

        return CompanyOut(
            id=company.id,
            name=company.name,
            subdomain=company.subdomain or "",
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at,
        )

    async def update_company_status(
        self,
        db: AsyncSession,
        company_id: int,
        current_user: User,
        is_active: bool,
    ) -> CompanyOut:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        if company.is_active != is_active:
            company.is_active = is_active
            action = "ACTIVATE_COMPANY" if is_active else "SUSPEND_COMPANY"
            audit_log = ActivityLog(
                action=action,
                entity="Company",
                entity_id=company.id,
                performed_by=current_user.id,
                details={"is_active": is_active},
            )
            db.add(audit_log)
            await db.commit()
            await db.refresh(company)

        return CompanyOut(
            id=company.id,
            name=company.name,
            subdomain=company.subdomain or "",
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at,
        )

    async def delete_company(
        self, db: AsyncSession, company_id: int, current_user: User
    ) -> dict:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        # Soft suspend instead of hard delete for safety
        if company.is_active:
            company.is_active = False
            audit_log = ActivityLog(
                action="DELETE_COMPANY", # Soft delete logical equivalent
                entity="Company",
                entity_id=company.id,
                performed_by=current_user.id,
                details={"note": "Company deactivated via delete endpoint"},
            )
            db.add(audit_log)
            await db.commit()
            return {"message": "Company deactivated successfully. Hard deletion is disabled."}
        else:
            return {"message": "Company is already deactivated."}

    async def list_company_audit_logs(
        self, db: AsyncSession, company_id: int, limit: int, offset: int
    ) -> PaginatedResponse[AuditLogOut]:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        query = select(ActivityLog).where(
            ActivityLog.entity == "Company", ActivityLog.entity_id == company_id
        ).order_by(ActivityLog.id.desc())
        count_query = select(func.count()).select_from(ActivityLog).where(
            ActivityLog.entity == "Company", ActivityLog.entity_id == company_id
        )

        total = await db.scalar(count_query)
        rows = (await db.execute(query.limit(limit).offset(offset))).scalars().all()

        items = [
            AuditLogOut(
                id=r.id,
                action=r.action,
                entity=r.entity,
                entity_id=r.entity_id,
                performed_by=r.performed_by,
                details=r.details,
                created_at=r.created_at,
            )
            for r in rows
        ]

        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
        return PaginatedResponse[AuditLogOut](items=items, meta=meta)

    async def create_company_admin(
        self, db: AsyncSession, company_id: int, current_user: User, data: CompanyAdminCreate
    ) -> UserOut:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")
        if not company.is_active:
            raise AppError(status_code=400, message="Company is not active")

        # Check unique email
        existing_email = await db.scalar(select(User).where(User.email == data.email))
        if existing_email:
            raise ConflictError("Email already registered")

        # Check unique mobile
        existing_mobile = await db.scalar(select(User).where(User.mobile == data.mobile))
        if existing_mobile:
            raise ConflictError("Mobile already registered")

        new_admin = User(
            email=data.email,
            mobile=data.mobile,
            full_name=data.full_name,
            hashed_password=get_password_hash(data.password),
            role=UserRole.ADMIN.value,
            is_super_admin=False,
            is_active=True,
            company_id=company_id,
            created_by=current_user.id
        )
        db.add(new_admin)
        await db.flush()

        audit_log = ActivityLog(
            action="CREATE_COMPANY_ADMIN",
            entity="User",
            entity_id=new_admin.id,
            performed_by=current_user.id,
            details={"company_id": company_id},
        )
        db.add(audit_log)
        await db.commit()
        await db.refresh(new_admin)

        return UserOut.model_validate(new_admin)

def get_superadmin_service() -> SuperAdminService:
    return SuperAdminService()
