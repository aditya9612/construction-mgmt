from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import Permission


MODULES = [

    # =====================================================
    # USER & RBAC
    # =====================================================

    "users",
    "roles",
    "permissions",

    # =====================================================
    # PROJECT EXECUTION
    # =====================================================

    "projects",
    "tasks",
    "milestones",
    "work_progress",
    "dsr",
    "issues",
    "measurements",

    # =====================================================
    # BOQ & MATERIALS
    # =====================================================

    "boq",
    "materials",
    "inventory",
    "purchase_orders",
    "suppliers",

    # =====================================================
    # LABOUR
    # =====================================================

    "labour",
    "attendance",
    "payroll",

    # =====================================================
    # FINANCE
    # =====================================================

    "billing",
    "expenses",
    "invoices",
    "quotations",

    # =====================================================
    # CONTRACTORS & OWNERS
    # =====================================================

    "contractors",
    "owners",

    # =====================================================
    # DOCUMENTS
    # =====================================================

    "documents",
    "drawings",
    "agreements",

    # =====================================================
    # SAFETY & QC
    # =====================================================

    "qc",
    "safety",
    "checklists",

    # =====================================================
    # EQUIPMENT
    # =====================================================

    "equipment",

    # =====================================================
    # REPORTS & DASHBOARD
    # =====================================================

    "reports",
    "dashboard",

    # =====================================================
    # COMMUNICATION
    # =====================================================

    "chat",
    "notifications",
    "alerts",

    # =====================================================
    # SETTINGS
    # =====================================================

    "settings",
]


ACTIONS = [
    "view",
    "create",
    "edit",
    "delete",
    "approve",
    "export",
    "manage",
    "assign",
    "upload",
    "download",
]


async def seed_permissions(db: AsyncSession):

    created = 0

    for module in MODULES:

        for action in ACTIONS:

            code = f"{module}.{action}"

            existing = await db.scalar(
                select(Permission).where(
                    Permission.code == code
                )
            )

            if existing:
                continue

            permission = Permission(
                module=module,
                action=action,
                code=code,
                description=f"{action} permission for {module}",
            )

            db.add(permission)

            created += 1

    await db.commit()

    return {
        "message": "Permissions seeded successfully",
        "created": created,
    }