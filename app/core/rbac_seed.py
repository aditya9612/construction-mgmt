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
    "vendor_bills",
    "payment_vouchers",
    "journal",

    # =====================================================
    # CONTRACTORS, WORK ORDERS & OWNERS
    # =====================================================

    "contractors",
    "work_orders",
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
    # APPROVALS
    # =====================================================

    "approvals",

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


MODULE_ACTIONS = {
    "approvals": [
        "view",
        "create",
        "approve",
    ],
    "work_orders": [
        "view",
        "create",
        "edit",
        "delete",
    ],
    "vendor_bills": [
        "view",
        "create",
        "edit",
        "delete",
        "approve",
        "pay",
    ],
    "payment_vouchers": [
        "view",
        "create",
        "edit",
        "delete",
        "pay",
    ],
    "journal": [
        "view",
        "create",
        "edit",
        "delete",
        "export",
    ],
}


async def seed_permissions(db: AsyncSession):

    created = 0

    for module in MODULES:

        actions = MODULE_ACTIONS.get(module, ACTIONS)

        for action in actions:

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