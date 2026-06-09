from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_roles
from app.db.session import get_db_session
from app.models.rbac import Permission, RolePermission
from app.models.user import ROLES, User, UserRole
from app.schemas.base import BaseSchema
from app.core.rbac_seed import seed_permissions
from app.core.default_role_permissions import (
    assign_default_role_permissions
)

router = APIRouter(
    prefix="/rbac",
    tags=["RBAC"],
)


# =========================================================
# SCHEMA
# =========================================================

class RolePermissionUpdate(BaseSchema):
    permissions: list[str]


# =========================================================
# GET ALL PERMISSIONS
# =========================================================

@router.get("/permissions")
async def get_permissions(
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(Permission).order_by(
            Permission.module,
            Permission.action
        )
    )

    permissions = result.scalars().all()

    grouped = defaultdict(list)

    for permission in permissions:
        grouped[permission.module].append(permission.code)

    return grouped


# =========================================================
# GET ALL ROLES
# =========================================================

@router.get("/roles")
async def get_roles(
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
):

    return {
        "roles": ROLES
    }


# =========================================================
# GET ROLE PERMISSIONS
# =========================================================

@router.get("/roles/{role}/permissions")
async def get_role_permissions(
    role: str,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):

    if role not in ROLES:
        return {
            "message": "Invalid role"
        }

    result = await db.execute(
        select(Permission.code)
        .join(
            RolePermission,
            RolePermission.permission_id == Permission.id
        )
        .where(RolePermission.role == role)
        .order_by(Permission.code)
    )

    permissions = result.scalars().all()

    return {
        "role": role,
        "permissions": permissions,
    }


# =========================================================
# UPDATE ROLE PERMISSIONS
# =========================================================

@router.put("/roles/{role}/permissions")
async def update_role_permissions(
    role: str,
    payload: RolePermissionUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):

    if role not in ROLES:
        return {
            "message": "Invalid role"
        }

    # -----------------------------------------------------
    # FETCH VALID PERMISSIONS
    # -----------------------------------------------------

    result = await db.execute(
        select(Permission)
        .where(Permission.code.in_(payload.permissions))
    )

    permissions = result.scalars().all()

    permission_ids = [p.id for p in permissions]

    # -----------------------------------------------------
    # DELETE OLD ROLE MAPPINGS
    # -----------------------------------------------------

    await db.execute(
        delete(RolePermission)
        .where(RolePermission.role == role)
    )

    # -----------------------------------------------------
    # INSERT NEW ROLE MAPPINGS
    # -----------------------------------------------------

    mappings = [
        RolePermission(
            role=role,
            permission_id=permission_id,
        )
        for permission_id in permission_ids
    ]

    db.add_all(mappings)

    await db.commit()

    return {
        "message": "Role permissions updated successfully",
        "role": role,
        "permissions": payload.permissions,
    }

@router.post("/seed")
async def seed_rbac_permissions(
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):

    return await seed_permissions(db)

@router.post("/assign-defaults")
async def assign_defaults(
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):

    return await assign_default_role_permissions(db)