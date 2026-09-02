from collections import defaultdict
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_roles
from app.db.session import get_db_session
from app.models.rbac import Permission, RolePermission, Role, UserPermissionOverride
from app.models.user import ROLES, User, UserRole
from app.schemas.base import BaseSchema
from app.core.rbac_seed import seed_permissions
from app.core.default_role_permissions import assign_default_role_permissions

router = APIRouter(
    prefix="/rbac",
    tags=["RBAC"],
)


# =========================================================
# SCHEMAS
# =========================================================

class RolePermissionUpdate(BaseSchema):
    permissions: list[str]


class RoleCreate(BaseSchema):
    name: str
    display_name: str
    description: Optional[str] = None


class UserOverrideItem(BaseSchema):
    permission: str
    is_granted: bool


class UserOverrideUpdate(BaseSchema):
    overrides: list[UserOverrideItem]


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
# GET ALL ROLES (System + Tenant Custom Roles)
# =========================================================

@router.get("/roles")
async def get_roles(
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    # Query database roles
    stmt = select(Role)
    if not current_user.is_super_admin:
        stmt = stmt.where(
            (Role.company_id == current_user.company_id) | (Role.company_id.is_(None)) | (Role.is_system == True)
        )
    result = await db.execute(stmt.order_by(Role.is_system.desc(), Role.name))
    db_roles = result.scalars().all()

    role_names = set(ROLES)
    custom_roles_info = []

    for r in db_roles:
        role_names.add(r.name)
        custom_roles_info.append({
            "id": r.id,
            "name": r.name,
            "display_name": r.display_name,
            "description": r.description,
            "company_id": r.company_id,
            "is_system": r.is_system,
        })

    return {
        "roles": sorted(list(role_names)),
        "details": custom_roles_info,
    }


# =========================================================
# CREATE CUSTOM ROLE
# =========================================================

@router.post("/roles")
async def create_role(
    payload: RoleCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    # Check if role already exists for this tenant
    existing = await db.scalar(
        select(Role).where(
            Role.name == payload.name,
            Role.company_id == current_user.company_id,
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role with this name already exists for your company",
        )

    new_role = Role(
        company_id=current_user.company_id if not current_user.is_super_admin else None,
        name=payload.name,
        display_name=payload.display_name,
        description=payload.description,
        is_system=False if not current_user.is_super_admin else True,
    )
    db.add(new_role)
    await db.commit()
    await db.refresh(new_role)

    return {
        "message": "Role created successfully",
        "role": {
            "id": new_role.id,
            "name": new_role.name,
            "display_name": new_role.display_name,
            "company_id": new_role.company_id,
            "is_system": new_role.is_system,
        },
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
    # 1. Check if tenant has a customized role
    company_role = None
    if current_user.company_id is not None:
        company_role = await db.scalar(
            select(Role).where(
                Role.name == role,
                Role.company_id == current_user.company_id,
            )
        )

    role_perms = []
    if company_role is not None:
        res = await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == company_role.id)
            .order_by(Permission.code)
        )
        role_perms = res.scalars().all()

    if not role_perms:
        res = await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .outerjoin(Role, RolePermission.role_id == Role.id)
            .where(
                RolePermission.role == role,
                (RolePermission.role_id.is_(None)) | (Role.company_id.is_(None)) | (Role.is_system == True),
            )
            .order_by(Permission.code)
        )
        role_perms = res.scalars().all()

    return {
        "role": role,
        "permissions": role_perms,
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
    # Fetch valid permissions from database
    result = await db.execute(
        select(Permission)
        .where(Permission.code.in_(payload.permissions))
    )
    permissions = result.scalars().all()
    permission_ids = [p.id for p in permissions]

    # Check if target role belongs to another company
    target_role = await db.scalar(
        select(Role).where(Role.name == role)
    )
    if target_role and target_role.company_id and not current_user.is_super_admin:
        if target_role.company_id != current_user.company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot modify permissions of another company's role",
            )

    company_role = None
    if current_user.company_id is not None:
        company_role = await db.scalar(
            select(Role).where(
                Role.name == role,
                Role.company_id == current_user.company_id,
            )
        )

    if company_role is not None:
        # Delete old mappings for this company role
        await db.execute(
            delete(RolePermission)
            .where(RolePermission.role_id == company_role.id)
        )
        mappings = [
            RolePermission(
                role=role,
                permission_id=pid,
                role_id=company_role.id,
            )
            for pid in permission_ids
        ]
    else:
        # Delete old mappings by role name
        await db.execute(
            delete(RolePermission)
            .where(
                RolePermission.role == role,
                RolePermission.role_id.is_(None),
            )
        )
        mappings = [
            RolePermission(
                role=role,
                permission_id=pid,
                role_id=None,
            )
            for pid in permission_ids
        ]

    db.add_all(mappings)
    await db.commit()

    return {
        "message": "Role permissions updated successfully",
        "role": role,
        "permissions": payload.permissions,
    }


# =========================================================
# USER PERMISSION OVERRIDES
# =========================================================

@router.get("/users/{user_id}/overrides")
async def get_user_permission_overrides(
    user_id: int,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    target_user = await db.scalar(select(User).where(User.id == user_id))
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not current_user.is_super_admin:
        if target_user.company_id != current_user.company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to users of another company",
            )

    res = await db.execute(
        select(Permission.code, UserPermissionOverride.is_granted)
        .join(UserPermissionOverride, UserPermissionOverride.permission_id == Permission.id)
        .where(UserPermissionOverride.user_id == user_id)
        .order_by(Permission.code)
    )

    overrides = [
        {"permission": code, "is_granted": is_granted}
        for code, is_granted in res.all()
    ]

    return {
        "user_id": user_id,
        "overrides": overrides,
    }


@router.put("/users/{user_id}/overrides")
async def update_user_permission_overrides(
    user_id: int,
    payload: UserOverrideUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    target_user = await db.scalar(select(User).where(User.id == user_id))
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Tenant isolation check
    if not current_user.is_super_admin:
        if target_user.company_id != current_user.company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot modify permission overrides for a user in another company",
            )
        # Self-escalation check
        if current_user.id == target_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot modify their own permission overrides",
            )

    # Fetch permissions by code
    perm_codes = [item.permission for item in payload.overrides]
    res = await db.execute(
        select(Permission).where(Permission.code.in_(perm_codes))
    )
    perms = {p.code: p.id for p in res.scalars().all()}

    # Delete existing overrides for this user
    await db.execute(
        delete(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id)
    )

    # Insert new overrides
    new_overrides = []
    for item in payload.overrides:
        pid = perms.get(item.permission)
        if pid:
            new_overrides.append(
                UserPermissionOverride(
                    user_id=user_id,
                    permission_id=pid,
                    is_granted=item.is_granted,
                )
            )

    if new_overrides:
        db.add_all(new_overrides)
    await db.commit()

    return {
        "message": "User permission overrides updated successfully",
        "user_id": user_id,
        "overrides": payload.overrides,
    }


# =========================================================
# SEEDING & DEFAULTS
# =========================================================

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