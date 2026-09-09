from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import Permission, RolePermission
from app.models.user import UserRole


# =============================================================================
# ADMIN-DRIVEN ROLE PERMISSION DEFAULTS
# =============================================================================
# Under the Admin-driven authorization model:
# - Admin role has access to ALL permissions available in the permissions catalog.
# - All other roles (Client, Labour, SiteEngineer, Contractor, Accountant,
#   ProjectManager, custom roles) have ZERO hardcoded or pre-assigned permissions.
# - Effective permissions for non-admin roles derive strictly from explicit Admin assignment.
# =============================================================================

DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    UserRole.ADMIN.value: ["*"],
}


async def assign_default_role_permissions(
    db: AsyncSession,
) -> dict:
    """
    Initializes system role permissions:
    1. Ensures all obsolete global non-admin mappings (role_id IS NULL and role != 'Admin')
       are purged so non-admin roles start with zero implicit permissions.
    2. Maps Admin to all available catalog permissions for global scope.
    Non-admin roles (Client, Labour, SiteEngineer, Contractor, Accountant, ProjectManager)
    receive ZERO default permissions and must be explicitly configured by an Admin.
    """
    # -----------------------------------------------------
    # 1. PURGE OBSOLETE GLOBAL NON-ADMIN MAPPINGS
    # -----------------------------------------------------
    await db.execute(
        delete(RolePermission).where(
            RolePermission.role_id.is_(None),
            RolePermission.role != UserRole.ADMIN.value,
            RolePermission.role != "Admin",
        )
    )

    # -----------------------------------------------------
    # 2. FETCH ALL ACTIVE CATALOG PERMISSIONS
    # -----------------------------------------------------
    result = await db.execute(select(Permission))
    all_permissions = result.scalars().all()

    # -----------------------------------------------------
    # 3. ENSURE ADMIN GLOBAL MAPPINGS
    # -----------------------------------------------------
    await db.execute(
        delete(RolePermission).where(
            RolePermission.role_id.is_(None),
            (RolePermission.role == UserRole.ADMIN.value) | (RolePermission.role == "Admin"),
        )
    )

    admin_mappings = [
        RolePermission(
            role=UserRole.ADMIN.value,
            permission_id=p.id,
            role_id=None,
        )
        for p in all_permissions
    ]
    db.add_all(admin_mappings)

    await db.commit()

    return {
        "message": "Role permissions initialized: Admin has full catalog access, non-admin roles require explicit assignment",
        "admin_permissions_count": len(admin_mappings),
    }