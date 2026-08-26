from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from app.models.company import Company
from app.models.settings import CompanySettings
from app.models.user import User, ActivityLog, UserRole
from app.models.project import Project
from app.models.subscription import Plan, Subscription
from app.core.security import get_password_hash
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
)
from app.schemas.user import UserOut
from app.utils.helpers import AppError, NotFoundError, ConflictError
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.services.entitlement import get_entitlement_service


class SuperAdminService:
    def __init__(self):
        self.entitlement_service = get_entitlement_service()

    # =========================================================================
    # 1. PLATFORM DASHBOARD
    # =========================================================================
    async def get_dashboard_stats(self, db: AsyncSession) -> DashboardStatsOut:
        companies_count = await db.scalar(select(func.count()).select_from(Company)) or 0
        active_companies_count = await db.scalar(
            select(func.count()).select_from(Company).where(Company.is_active == True)
        ) or 0
        suspended_companies_count = companies_count - active_companies_count

        users_count = await db.scalar(
            select(func.count()).select_from(User).where(User.is_deleted == False)
        ) or 0
        active_users_count = await db.scalar(
            select(func.count()).select_from(User).where(
                User.is_active == True, User.is_deleted == False
            )
        ) or 0

        projects_count = await db.scalar(select(func.count()).select_from(Project)) or 0
        active_projects_count = await db.scalar(
            select(func.count()).select_from(Project).where(
                Project.status.notin_(["COMPLETED", "CANCELLED", "ARCHIVED"])
            )
        ) or 0

        plans_count = await db.scalar(
            select(func.count()).select_from(Plan).where(Plan.is_active == True)
        ) or 0
        subscriptions_count = await db.scalar(
            select(func.count()).select_from(Subscription)
        ) or 0

        # Subscription status breakdown
        sub_rows = (
            await db.execute(
                select(Subscription.status, func.count(Subscription.id)).group_by(
                    Subscription.status
                )
            )
        ).all()
        sub_dist = {status: count for status, count in sub_rows}

        # Expiring subscriptions in next 30 days
        now = datetime.utcnow()
        expiring_threshold = now + timedelta(days=30)
        expiring_count = await db.scalar(
            select(func.count()).select_from(Subscription).where(
                Subscription.end_date != None,
                Subscription.end_date <= expiring_threshold,
                Subscription.end_date >= now,
                Subscription.status == "active",
            )
        ) or 0

        # Recent Activity (latest 10)
        recent_activity_rows = (
            await db.execute(
                select(ActivityLog).order_by(ActivityLog.id.desc()).limit(10)
            )
        ).scalars().all()

        recent_activity = [
            AuditLogOut(
                id=a.id,
                action=a.action,
                entity=a.entity,
                entity_id=a.entity_id,
                performed_by=a.performed_by,
                details=a.details,
                created_at=a.created_at,
            )
            for a in recent_activity_rows
        ]

        return DashboardStatsOut(
            companies=companies_count,
            active_companies=active_companies_count,
            suspended_companies=suspended_companies_count,
            users=users_count,
            active_users=active_users_count,
            projects=projects_count,
            active_projects=active_projects_count,
            plans_count=plans_count,
            subscriptions_count=subscriptions_count,
            subscription_distribution=sub_dist,
            expiring_subscriptions=expiring_count,
            recent_activity=recent_activity,
        )

    # =========================================================================
    # 2. TENANT / COMPANY MANAGEMENT
    # =========================================================================
    async def list_companies(
        self,
        db: AsyncSession,
        limit: int = 20,
        offset: int = 0,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        subscription_status: Optional[str] = None,
    ) -> PaginatedResponse[CompanyOut]:
        query = (
            select(Company)
            .options(
                selectinload(Company.subscription).selectinload(Subscription.plan)
            )
            .order_by(Company.id.desc())
        )
        count_query = select(func.count()).select_from(Company)

        if is_active is not None:
            query = query.where(Company.is_active == is_active)
            count_query = count_query.where(Company.is_active == is_active)

        if search:
            search_pattern = f"%{search.strip().lower()}%"
            filter_cond = or_(
                func.lower(Company.name).like(search_pattern),
                func.lower(Company.subdomain).like(search_pattern),
            )
            query = query.where(filter_cond)
            count_query = count_query.where(filter_cond)

        if subscription_status:
            query = query.join(
                Subscription, Company.id == Subscription.company_id
            ).where(Subscription.status == subscription_status)
            count_query = count_query.join(
                Subscription, Company.id == Subscription.company_id
            ).where(Subscription.status == subscription_status)

        total = await db.scalar(count_query) or 0
        rows = (await db.execute(query.limit(limit).offset(offset))).scalars().all()

        items: List[CompanyOut] = []
        for r in rows:
            sub_status = r.subscription.status if r.subscription else "trial"
            plan_name = (
                r.subscription.plan.name
                if r.subscription and r.subscription.plan
                else "Standard Trial"
            )
            items.append(
                CompanyOut(
                    id=r.id,
                    name=r.name,
                    subdomain=r.subdomain or "",
                    is_active=r.is_active,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    subscription_status=sub_status,
                    plan_name=plan_name,
                )
            )

        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[CompanyOut](items=items, meta=meta)

    async def create_company(
        self, db: AsyncSession, current_user: User, data: CompanyCreate
    ) -> CompanyOut:
        # Validate subdomain uniqueness
        if data.subdomain:
            existing = await db.scalar(
                select(Company).where(
                    func.lower(Company.subdomain) == data.subdomain.strip().lower()
                )
            )
            if existing:
                raise ConflictError("Subdomain already in use")

        # Create Company
        company = Company(
            name=data.name.strip(),
            subdomain=data.subdomain.strip().lower() if data.subdomain else None,
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

        # Create default Trial Subscription or assigned Plan
        plan = None
        if data.plan_id:
            plan = await db.get(Plan, data.plan_id)
            if not plan:
                raise NotFoundError("Specified Plan not found")
        else:
            # Fallback or default plan if one exists
            plan = await db.scalar(
                select(Plan).where(Plan.is_active == True).order_by(Plan.id.asc())
            )

        if plan:
            subscription = Subscription(
                company_id=company.id,
                plan_id=plan.id,
                status="trial",
                start_date=datetime.utcnow(),
                trial_end_date=datetime.utcnow() + timedelta(days=14),
                auto_renew=True,
            )
            db.add(subscription)

        # Platform Audit Log
        audit_log = ActivityLog(
            action="CREATE_COMPANY",
            entity="Company",
            entity_id=company.id,
            performed_by=current_user.id,
            details={
                "name": company.name,
                "subdomain": company.subdomain,
                "plan_id": plan.id if plan else None,
            },
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
            subscription_status="trial" if plan else "none",
            plan_name=plan.name if plan else "Standard Trial",
        )

    async def get_company(self, db: AsyncSession, company_id: int) -> CompanyOut:
        stmt = (
            select(Company)
            .options(
                selectinload(Company.subscription).selectinload(Subscription.plan)
            )
            .where(Company.id == company_id)
        )
        company = await db.scalar(stmt)
        if not company:
            raise NotFoundError("Company not found")

        user_count = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.company_id == company_id, User.is_deleted == False)
        ) or 0
        project_count = await db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.company_id == company_id)
        ) or 0

        sub_status = company.subscription.status if company.subscription else "trial"
        plan_name = (
            company.subscription.plan.name
            if company.subscription and company.subscription.plan
            else "Standard Trial"
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
            subscription_status=sub_status,
            plan_name=plan_name,
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

        if data.subdomain and data.subdomain.strip().lower() != (company.subdomain or "").lower():
            clean_subdomain = data.subdomain.strip().lower()
            existing = await db.scalar(
                select(Company).where(
                    func.lower(Company.subdomain) == clean_subdomain,
                    Company.id != company_id,
                )
            )
            if existing:
                raise ConflictError("Subdomain already in use")
            company.subdomain = clean_subdomain

        if data.name:
            company.name = data.name.strip()

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

        return await self.get_company(db, company_id)

    async def update_company_status(
        self,
        db: AsyncSession,
        company_id: int,
        current_user: User,
        is_active: bool,
        reason: Optional[str] = None,
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
                details={"is_active": is_active, "reason": reason},
            )
            db.add(audit_log)
            await db.commit()
            await db.refresh(company)

        return await self.get_company(db, company_id)

    async def delete_company(
        self, db: AsyncSession, company_id: int, current_user: User
    ) -> dict:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        # Soft suspend instead of hard delete for data integrity
        if company.is_active:
            company.is_active = False
            audit_log = ActivityLog(
                action="SUSPEND_COMPANY",
                entity="Company",
                entity_id=company.id,
                performed_by=current_user.id,
                details={"note": "Company deactivated via delete/suspend endpoint"},
            )
            db.add(audit_log)
            await db.commit()
            return {"message": "Company deactivated successfully. Hard deletion is disabled."}
        else:
            return {"message": "Company is already deactivated."}

    async def get_company_stats(
        self, db: AsyncSession, company_id: int
    ) -> CompanyStatsOut:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        total_projects = await db.scalar(
            select(func.count()).select_from(Project).where(Project.company_id == company_id)
        ) or 0
        active_projects = await db.scalar(
            select(func.count()).select_from(Project).where(
                Project.company_id == company_id,
                Project.status.notin_(["COMPLETED", "CANCELLED", "ARCHIVED"]),
            )
        ) or 0
        completed_projects = await db.scalar(
            select(func.count()).select_from(Project).where(
                Project.company_id == company_id, Project.status == "COMPLETED"
            )
        ) or 0

        total_users = await db.scalar(
            select(func.count()).select_from(User).where(
                User.company_id == company_id, User.is_deleted == False
            )
        ) or 0
        active_users = await db.scalar(
            select(func.count()).select_from(User).where(
                User.company_id == company_id,
                User.is_active == True,
                User.is_deleted == False,
            )
        ) or 0

        role_rows = (
            await db.execute(
                select(User.role, func.count(User.id))
                .where(User.company_id == company_id, User.is_deleted == False)
                .group_by(User.role)
            )
        ).all()
        users_by_role = {role: count for role, count in role_rows}

        entitlements = await self.entitlement_service.get_company_entitlements(db, company_id)

        return CompanyStatsOut(
            company_id=company.id,
            company_name=company.name,
            total_projects=total_projects,
            active_projects=active_projects,
            completed_projects=completed_projects,
            total_users=total_users,
            active_users=active_users,
            users_by_role=users_by_role,
            subscription_status=entitlements.get("status", "trial"),
            plan_name=entitlements.get("plan_name", "Standard Trial"),
        )

    # =========================================================================
    # 3. TENANT USER ADMINISTRATION
    # =========================================================================
    async def list_company_users(
        self,
        db: AsyncSession,
        company_id: int,
        limit: int = 20,
        offset: int = 0,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
    ) -> PaginatedResponse[TenantUserOut]:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        query = (
            select(User)
            .where(User.company_id == company_id, User.is_deleted == False)
            .order_by(User.id.desc())
        )
        count_query = (
            select(func.count())
            .select_from(User)
            .where(User.company_id == company_id, User.is_deleted == False)
        )

        if role:
            query = query.where(User.role == role)
            count_query = count_query.where(User.role == role)

        if is_active is not None:
            query = query.where(User.is_active == is_active)
            count_query = count_query.where(User.is_active == is_active)

        if search:
            search_pat = f"%{search.strip().lower()}%"
            cond = or_(
                func.lower(User.email).like(search_pat),
                func.lower(User.full_name).like(search_pat),
                func.lower(User.mobile).like(search_pat),
            )
            query = query.where(cond)
            count_query = count_query.where(cond)

        total = await db.scalar(count_query) or 0
        rows = (await db.execute(query.limit(limit).offset(offset))).scalars().all()

        items = [
            TenantUserOut(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                mobile=u.mobile,
                role=u.role,
                is_active=u.is_active,
                company_id=u.company_id,
                created_at=getattr(u, "created_at", None),
            )
            for u in rows
        ]

        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[TenantUserOut](items=items, meta=meta)

    async def get_company_user(
        self, db: AsyncSession, company_id: int, user_id: int
    ) -> TenantUserOut:
        user = await db.scalar(
            select(User).where(
                User.id == user_id,
                User.company_id == company_id,
                User.is_deleted == False,
            )
        )
        if not user:
            raise NotFoundError("Tenant user not found")

        return TenantUserOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            mobile=user.mobile,
            role=user.role,
            is_active=user.is_active,
            company_id=user.company_id,
            created_at=getattr(user, "created_at", None),
        )

    async def update_company_user_status(
        self,
        db: AsyncSession,
        company_id: int,
        user_id: int,
        current_user: User,
        is_active: bool,
    ) -> TenantUserOut:
        user = await db.scalar(
            select(User).where(
                User.id == user_id,
                User.company_id == company_id,
                User.is_deleted == False,
            )
        )
        if not user:
            raise NotFoundError("Tenant user not found")

        if user.is_super_admin:
            raise AppError(status_code=400, message="Cannot modify platform super admin via tenant user API")

        if user.is_active != is_active:
            user.is_active = is_active
            action = "ACTIVATE_TENANT_USER" if is_active else "DEACTIVATE_TENANT_USER"
            audit_log = ActivityLog(
                action=action,
                entity="User",
                entity_id=user.id,
                performed_by=current_user.id,
                details={"company_id": company_id, "is_active": is_active},
            )
            db.add(audit_log)
            await db.commit()
            await db.refresh(user)

        return TenantUserOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            mobile=user.mobile,
            role=user.role,
            is_active=user.is_active,
            company_id=user.company_id,
            created_at=getattr(user, "created_at", None),
        )

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
            created_by=current_user.id,
        )
        db.add(new_admin)
        await db.flush()

        audit_log = ActivityLog(
            action="CREATE_COMPANY_ADMIN",
            entity="User",
            entity_id=new_admin.id,
            performed_by=current_user.id,
            details={"company_id": company_id, "email": new_admin.email},
        )
        db.add(audit_log)
        await db.commit()
        await db.refresh(new_admin)

        return UserOut.model_validate(new_admin)

    # =========================================================================
    # 4. SAAS PLANS MANAGEMENT
    # =========================================================================
    async def list_plans(
        self, db: AsyncSession, is_active: Optional[bool] = None
    ) -> List[PlanOut]:
        query = select(Plan).order_by(Plan.price.asc(), Plan.id.asc())
        if is_active is not None:
            query = query.where(Plan.is_active == is_active)

        rows = (await db.execute(query)).scalars().all()
        return [
            PlanOut(
                id=p.id,
                name=p.name,
                code=p.code,
                description=p.description,
                price=p.price,
                billing_interval=p.billing_interval,
                currency=p.currency,
                features=p.features,
                is_active=p.is_active,
                created_at=p.created_at,
            )
            for p in rows
        ]

    async def create_plan(
        self, db: AsyncSession, current_user: User, data: PlanCreate
    ) -> PlanOut:
        clean_code = data.code.strip().lower()
        existing = await db.scalar(select(Plan).where(Plan.code == clean_code))
        if existing:
            raise ConflictError(f"Plan with code '{clean_code}' already exists")

        plan = Plan(
            name=data.name.strip(),
            code=clean_code,
            description=data.description,
            price=data.price,
            billing_interval=data.billing_interval,
            currency=data.currency,
            features=data.features or {},
            is_active=data.is_active,
        )
        db.add(plan)
        await db.flush()

        audit_log = ActivityLog(
            action="CREATE_PLAN",
            entity="Plan",
            entity_id=plan.id,
            performed_by=current_user.id,
            details={"code": plan.code, "name": plan.name, "price": plan.price},
        )
        db.add(audit_log)
        await db.commit()
        await db.refresh(plan)

        return PlanOut(
            id=plan.id,
            name=plan.name,
            code=plan.code,
            description=plan.description,
            price=plan.price,
            billing_interval=plan.billing_interval,
            currency=plan.currency,
            features=plan.features,
            is_active=plan.is_active,
            created_at=plan.created_at,
        )

    async def get_plan(self, db: AsyncSession, plan_id: int) -> PlanOut:
        plan = await db.get(Plan, plan_id)
        if not plan:
            raise NotFoundError("Plan not found")

        return PlanOut(
            id=plan.id,
            name=plan.name,
            code=plan.code,
            description=plan.description,
            price=plan.price,
            billing_interval=plan.billing_interval,
            currency=plan.currency,
            features=plan.features,
            is_active=plan.is_active,
            created_at=plan.created_at,
        )

    async def update_plan(
        self, db: AsyncSession, plan_id: int, current_user: User, data: PlanUpdate
    ) -> PlanOut:
        plan = await db.get(Plan, plan_id)
        if not plan:
            raise NotFoundError("Plan not found")

        if data.name is not None:
            plan.name = data.name.strip()
        if data.description is not None:
            plan.description = data.description
        if data.price is not None:
            plan.price = data.price
        if data.billing_interval is not None:
            plan.billing_interval = data.billing_interval
        if data.currency is not None:
            plan.currency = data.currency
        if data.features is not None:
            plan.features = data.features
        if data.is_active is not None:
            plan.is_active = data.is_active

        audit_log = ActivityLog(
            action="UPDATE_PLAN",
            entity="Plan",
            entity_id=plan.id,
            performed_by=current_user.id,
            details={"code": plan.code, "name": plan.name},
        )
        db.add(audit_log)
        await db.commit()
        await db.refresh(plan)

        return PlanOut(
            id=plan.id,
            name=plan.name,
            code=plan.code,
            description=plan.description,
            price=plan.price,
            billing_interval=plan.billing_interval,
            currency=plan.currency,
            features=plan.features,
            is_active=plan.is_active,
            created_at=plan.created_at,
        )

    async def delete_plan(
        self, db: AsyncSession, plan_id: int, current_user: User
    ) -> dict:
        plan = await db.get(Plan, plan_id)
        if not plan:
            raise NotFoundError("Plan not found")

        # Soft deactivate plan so active subscriptions aren't broken
        plan.is_active = False
        audit_log = ActivityLog(
            action="DEACTIVATE_PLAN",
            entity="Plan",
            entity_id=plan.id,
            performed_by=current_user.id,
            details={"code": plan.code},
        )
        db.add(audit_log)
        await db.commit()
        return {"message": "Plan deactivated successfully"}

    # =========================================================================
    # 5. TENANT SUBSCRIPTIONS & ENTITLEMENTS
    # =========================================================================
    async def get_company_subscription(
        self, db: AsyncSession, company_id: int
    ) -> SubscriptionOut:
        stmt = (
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.company_id == company_id)
        )
        sub = await db.scalar(stmt)
        if not sub:
            raise NotFoundError("Company subscription not found")

        return SubscriptionOut(
            id=sub.id,
            company_id=sub.company_id,
            plan_id=sub.plan_id,
            plan_name=sub.plan.name if sub.plan else None,
            plan_code=sub.plan.code if sub.plan else None,
            status=sub.status,
            start_date=sub.start_date,
            end_date=sub.end_date,
            trial_end_date=sub.trial_end_date,
            auto_renew=sub.auto_renew,
            created_at=sub.created_at,
        )

    async def assign_company_subscription(
        self,
        db: AsyncSession,
        company_id: int,
        current_user: User,
        data: SubscriptionCreate,
    ) -> SubscriptionOut:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        plan = await db.get(Plan, data.plan_id)
        if not plan:
            raise NotFoundError("Plan not found")

        stmt = select(Subscription).where(Subscription.company_id == company_id)
        sub = await db.scalar(stmt)

        if sub:
            old_plan_id = sub.plan_id
            sub.plan_id = plan.id
            sub.status = data.status
            if data.start_date:
                sub.start_date = data.start_date
            if data.end_date:
                sub.end_date = data.end_date
            if data.trial_end_date:
                sub.trial_end_date = data.trial_end_date
            sub.auto_renew = data.auto_renew
            action = "CHANGE_PLAN"
            details = {"old_plan_id": old_plan_id, "new_plan_id": plan.id, "status": data.status}
        else:
            sub = Subscription(
                company_id=company_id,
                plan_id=plan.id,
                status=data.status,
                start_date=data.start_date or datetime.utcnow(),
                end_date=data.end_date,
                trial_end_date=data.trial_end_date,
                auto_renew=data.auto_renew,
            )
            db.add(sub)
            action = "ASSIGN_PLAN"
            details = {"plan_id": plan.id, "status": data.status}

        await db.flush()

        audit_log = ActivityLog(
            action=action,
            entity="Subscription",
            entity_id=sub.id,
            performed_by=current_user.id,
            details=details,
        )
        db.add(audit_log)
        await db.commit()
        await db.refresh(sub)

        return await self.get_company_subscription(db, company_id)

    async def update_company_subscription(
        self,
        db: AsyncSession,
        company_id: int,
        current_user: User,
        data: SubscriptionUpdate,
    ) -> SubscriptionOut:
        stmt = select(Subscription).where(Subscription.company_id == company_id)
        sub = await db.scalar(stmt)
        if not sub:
            raise NotFoundError("Subscription not found")

        if data.plan_id is not None:
            plan = await db.get(Plan, data.plan_id)
            if not plan:
                raise NotFoundError("Plan not found")
            sub.plan_id = plan.id

        if data.status is not None:
            sub.status = data.status
        if data.end_date is not None:
            sub.end_date = data.end_date
        if data.trial_end_date is not None:
            sub.trial_end_date = data.trial_end_date
        if data.auto_renew is not None:
            sub.auto_renew = data.auto_renew

        audit_log = ActivityLog(
            action="UPDATE_SUBSCRIPTION",
            entity="Subscription",
            entity_id=sub.id,
            performed_by=current_user.id,
            details={"plan_id": sub.plan_id, "status": sub.status},
        )
        db.add(audit_log)
        await db.commit()

        return await self.get_company_subscription(db, company_id)

    async def update_subscription_status(
        self,
        db: AsyncSession,
        company_id: int,
        current_user: User,
        new_status: str,
        reason: Optional[str] = None,
    ) -> SubscriptionOut:
        stmt = select(Subscription).where(Subscription.company_id == company_id)
        sub = await db.scalar(stmt)
        if not sub:
            raise NotFoundError("Subscription not found")

        old_status = sub.status
        sub.status = new_status

        action = f"SUBSCRIPTION_{new_status.upper()}"
        audit_log = ActivityLog(
            action=action,
            entity="Subscription",
            entity_id=sub.id,
            performed_by=current_user.id,
            details={"old_status": old_status, "new_status": new_status, "reason": reason},
        )
        db.add(audit_log)
        await db.commit()

        return await self.get_company_subscription(db, company_id)

    async def get_company_entitlements(
        self, db: AsyncSession, company_id: int
    ) -> EntitlementOut:
        company = await db.get(Company, company_id)
        if not company:
            raise NotFoundError("Company not found")

        data = await self.entitlement_service.get_company_entitlements(db, company_id)
        return EntitlementOut(**data)

    # =========================================================================
    # 6. PLATFORM AUDIT LOGS
    # =========================================================================
    async def list_audit_logs(
        self,
        db: AsyncSession,
        limit: int = 50,
        offset: int = 0,
        entity: Optional[str] = None,
        action: Optional[str] = None,
        performed_by: Optional[int] = None,
    ) -> PaginatedResponse[AuditLogOut]:
        query = select(ActivityLog).order_by(ActivityLog.id.desc())
        count_query = select(func.count()).select_from(ActivityLog)

        if entity:
            query = query.where(ActivityLog.entity == entity)
            count_query = count_query.where(ActivityLog.entity == entity)

        if action:
            query = query.where(ActivityLog.action == action)
            count_query = count_query.where(ActivityLog.action == action)

        if performed_by:
            query = query.where(ActivityLog.performed_by == performed_by)
            count_query = count_query.where(ActivityLog.performed_by == performed_by)

        total = await db.scalar(count_query) or 0
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

        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[AuditLogOut](items=items, meta=meta)

    async def list_company_audit_logs(
        self, db: AsyncSession, company_id: int, limit: int = 20, offset: int = 0
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

        total = await db.scalar(count_query) or 0
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

        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[AuditLogOut](items=items, meta=meta)


def get_superadmin_service() -> SuperAdminService:
    return SuperAdminService()
