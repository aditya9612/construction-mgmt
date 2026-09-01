from typing import Optional, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.models.subscription import Subscription, Plan
from app.models.company import Company
from app.models.user import User
from app.models.project import Project
from app.models.document import Document
from app.utils.helpers import AppError, ForbiddenError

DEFAULT_FEATURES = {
    "max_users": 10,
    "max_projects": 5,
    "storage_gb": 5,
    "advanced_reports": True,
    "payroll": True,
    "equipment": True,
    "ai_features": False,
}

class EntitlementService:
    async def get_company_subscription(
        self, db: AsyncSession, company_id: int
    ) -> Optional[Subscription]:
        stmt = (
            select(Subscription)
            .options(selectinload(Subscription.plan))
            .where(Subscription.company_id == company_id)
        )
        return await db.scalar(stmt)

    async def get_company_entitlements(
        self, db: AsyncSession, company_id: int
    ) -> Dict[str, Any]:
        sub = await self.get_company_subscription(db, company_id)
        if not sub or not sub.plan:
            # Default trial entitlement
            return {
                "plan_id": None,
                "plan_name": "Standard Trial",
                "plan_code": "trial",
                "status": "trial",
                "is_active": True,
                "max_users": DEFAULT_FEATURES["max_users"],
                "max_projects": DEFAULT_FEATURES["max_projects"],
                "storage_gb": DEFAULT_FEATURES["storage_gb"],
                "advanced_reports": DEFAULT_FEATURES["advanced_reports"],
                "payroll": DEFAULT_FEATURES["payroll"],
                "equipment": DEFAULT_FEATURES["equipment"],
                "ai_features": DEFAULT_FEATURES["ai_features"],
                "features": DEFAULT_FEATURES.copy(),
            }

        plan_features = sub.plan.features or {}
        merged_features = {**DEFAULT_FEATURES, **plan_features}
        is_sub_active = sub.status in ("trial", "active")

        return {
            "plan_id": sub.plan_id,
            "plan_name": sub.plan.name,
            "plan_code": sub.plan.code,
            "status": sub.status,
            "is_active": is_sub_active,
            "start_date": sub.start_date,
            "end_date": sub.end_date,
            "trial_end_date": sub.trial_end_date,
            "auto_renew": sub.auto_renew,
            "max_users": merged_features.get("max_users", DEFAULT_FEATURES["max_users"]),
            "max_projects": merged_features.get("max_projects", DEFAULT_FEATURES["max_projects"]),
            "storage_gb": merged_features.get("storage_gb", DEFAULT_FEATURES["storage_gb"]),
            "advanced_reports": merged_features.get("advanced_reports", DEFAULT_FEATURES["advanced_reports"]),
            "payroll": merged_features.get("payroll", DEFAULT_FEATURES["payroll"]),
            "equipment": merged_features.get("equipment", DEFAULT_FEATURES["equipment"]),
            "ai_features": merged_features.get("ai_features", DEFAULT_FEATURES["ai_features"]),
            "features": merged_features,
        }

    async def get_usage(self, db: AsyncSession, company_id: int) -> Dict[str, Any]:
        """Calculates actual usage metrics for the given company."""
        user_count = await db.scalar(
            select(func.count()).select_from(User).where(
                User.company_id == company_id,
                User.is_deleted == False,
                User.is_active == True,
            )
        ) or 0

        project_count = await db.scalar(
            select(func.count()).select_from(Project).where(
                Project.company_id == company_id
            )
        ) or 0

        # Calculate document storage usage
        storage_bytes = await db.scalar(
            select(func.coalesce(func.sum(Document.file_size), 0))
            .join(Project, Project.id == Document.project_id)
            .where(
                Project.company_id == company_id,
                Document.is_folder == False,
                Document.is_deleted == False,
            )
        ) or 0
        storage_gb = round(storage_bytes / (1024 ** 3), 4)

        return {
            "users": user_count,
            "projects": project_count,
            "storage_bytes": storage_bytes,
            "storage_gb": storage_gb,
        }

    async def get_limits(self, db: AsyncSession, company_id: int) -> Dict[str, Any]:
        """Returns limits and current usage for the given company."""
        entitlements = await self.get_company_entitlements(db, company_id)
        usage = await self.get_usage(db, company_id)
        return {
            "entitlements": entitlements,
            "usage": usage,
        }

    async def can_create_user(self, db: AsyncSession, company_id: int) -> Tuple[bool, Optional[str]]:
        entitlements = await self.get_company_entitlements(db, company_id)
        if not entitlements.get("is_active"):
            return False, f"Subscription is {entitlements.get('status')}. Cannot add new users."

        current_users = await db.scalar(
            select(func.count()).select_from(User).where(
                User.company_id == company_id,
                User.is_deleted == False,
                User.is_active == True,
            )
        ) or 0

        max_users = entitlements.get("max_users", DEFAULT_FEATURES["max_users"])
        if current_users >= max_users:
            return False, f"User limit reached ({current_users}/{max_users}). Upgrade your plan to add more users."

        return True, None

    async def assert_can_create_user(self, db: AsyncSession, company_id: int) -> None:
        allowed, reason = await self.can_create_user(db, company_id)
        if not allowed:
            raise ForbiddenError(reason or "Cannot create user: limit exceeded or subscription inactive.")

    async def can_create_project(self, db: AsyncSession, company_id: int) -> Tuple[bool, Optional[str]]:
        entitlements = await self.get_company_entitlements(db, company_id)
        if not entitlements.get("is_active"):
            return False, f"Subscription is {entitlements.get('status')}. Cannot create new projects."

        current_projects = await db.scalar(
            select(func.count()).select_from(Project).where(
                Project.company_id == company_id
            )
        ) or 0

        max_projects = entitlements.get("max_projects", DEFAULT_FEATURES["max_projects"])
        if current_projects >= max_projects:
            return False, f"Project limit reached ({current_projects}/{max_projects}). Upgrade your plan to create more projects."

        return True, None

    async def assert_can_create_project(self, db: AsyncSession, company_id: int) -> None:
        allowed, reason = await self.can_create_project(db, company_id)
        if not allowed:
            raise ForbiddenError(reason or "Cannot create project: limit exceeded or subscription inactive.")

    async def has_feature(
        self, db: AsyncSession, company_id: int, feature_key: str
    ) -> bool:
        entitlements = await self.get_company_entitlements(db, company_id)
        if not entitlements.get("is_active"):
            return False
        return bool(entitlements.get("features", {}).get(feature_key, False))

    async def assert_feature_enabled(
        self, db: AsyncSession, company_id: int, feature_key: str, feature_label: Optional[str] = None
    ) -> None:
        entitlements = await self.get_company_entitlements(db, company_id)
        if not entitlements.get("is_active"):
            raise ForbiddenError(f"Subscription is {entitlements.get('status')}. Feature is disabled.")
        if not bool(entitlements.get("features", {}).get(feature_key, False)):
            label = feature_label or feature_key
            raise ForbiddenError(f"The '{label}' feature is not enabled for your company's plan. Upgrade required.")

    async def assert_can_upload_file(
        self, db: AsyncSession, company_id: int, new_file_bytes: int = 0
    ) -> None:
        entitlements = await self.get_company_entitlements(db, company_id)
        if not entitlements.get("is_active"):
            raise ForbiddenError(f"Subscription is {entitlements.get('status')}. Uploads are disabled.")

        storage_limit_gb = entitlements.get("storage_gb", DEFAULT_FEATURES["storage_gb"])
        storage_limit_bytes = storage_limit_gb * (1024 ** 3)

        current_bytes = await db.scalar(
            select(func.coalesce(func.sum(Document.file_size), 0))
            .join(Project, Project.id == Document.project_id)
            .where(
                Project.company_id == company_id,
                Document.is_folder == False,
                Document.is_deleted == False,
            )
        ) or 0

        if (current_bytes + new_file_bytes) > storage_limit_bytes:
            raise ForbiddenError(f"Storage limit of {storage_limit_gb} GB exceeded. Upgrade your plan for additional storage.")


def get_entitlement_service() -> EntitlementService:
    return EntitlementService()
