from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import Permission, RolePermission
from app.models.user import UserRole


DEFAULT_ROLE_PERMISSIONS = {

    # =====================================================
    # ADMIN
    # =====================================================

    UserRole.ADMIN.value: [
        "*"
    ],

    # =====================================================
    # PROJECT MANAGER
    # =====================================================

    UserRole.PROJECT_MANAGER.value: [

        "projects.view",
        "projects.create",
        "projects.edit",
        "projects.approve",
        "projects.export",

        "tasks.view",
        "tasks.create",
        "tasks.edit",
        "tasks.assign",

        "boq.view",
        "boq.create",
        "boq.edit",
        "boq.approve",
        "boq.export",

        "materials.view",

        "labour.view",

        "attendance.view",

        "billing.view",
        "billing.approve",

        "reports.view",
        "reports.export",

        "documents.view",
        "documents.upload",


        # AGREEMENTS
        "agreements.view",
        "agreements.create",


    ],

    # =====================================================
    # SITE ENGINEER
    # =====================================================

    UserRole.SITE_ENGINEER.value: [

        "projects.view",

        "tasks.view",
        "tasks.edit",

        "materials.view",

        "labour.view",

        "attendance.view",
        "attendance.manage",

        "documents.view",
        "documents.upload",

        "reports.view",

        "agreements.view",
    ],

    # =====================================================
    # CONTRACTOR
    # =====================================================

    UserRole.CONTRACTOR.value: [

        "projects.view",

        "tasks.view",

        "labour.view",

        "attendance.view",

        "billing.view",

        "documents.view",
    ],

    # =====================================================
    # ACCOUNTANT
    # =====================================================

    UserRole.ACCOUNTANT.value: [

        "billing.view",
        "billing.create",
        "billing.edit",
        "billing.approve",
        "billing.export",

        "reports.view",
        "reports.export",

        "labour.view",

        "attendance.view",

        "agreements.view",
    ],

    # =====================================================
    # CLIENT
    # =====================================================

    UserRole.CLIENT.value: [

        "projects.view",

        "tasks.view",

        "billing.view",

        "reports.view",

        "documents.view",

        "agreements.view",
    ],

    # =====================================================
    # LABOUR
    # =====================================================

    UserRole.LABOUR.value: [

        "attendance.view",

        "tasks.view",
    ],
}


async def assign_default_role_permissions(
    db: AsyncSession,
):

    # -----------------------------------------------------
    # FETCH ALL PERMISSIONS
    # -----------------------------------------------------

    result = await db.execute(
        select(Permission)
    )

    all_permissions = result.scalars().all()

    permission_map = {
        p.code: p.id
        for p in all_permissions
    }

    created = 0

    # -----------------------------------------------------
    # LOOP ROLES
    # -----------------------------------------------------

    for role, permissions in DEFAULT_ROLE_PERMISSIONS.items():

        # DELETE OLD
        await db.execute(
            RolePermission.__table__.delete().where(
                RolePermission.role == role
            )
        )

        # ADMIN => ALL
        if "*" in permissions:

            mappings = [
                RolePermission(
                    role=role,
                    permission_id=p.id,
                )
                for p in all_permissions
            ]

            db.add_all(mappings)

            created += len(mappings)

            continue

        # NORMAL ROLE
        mappings = []

        for code in permissions:

            permission_id = permission_map.get(code)

            if not permission_id:
                continue

            mappings.append(
                RolePermission(
                    role=role,
                    permission_id=permission_id,
                )
            )

        db.add_all(mappings)

        created += len(mappings)

    await db.commit()

    return {
        "message": "Default role permissions assigned successfully",
        "created": created,
    }