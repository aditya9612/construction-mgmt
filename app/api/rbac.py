from collections import defaultdict
from typing import Any, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.default_role_permissions import assign_default_role_permissions
from app.core.dependencies import require_roles
from app.core.rbac_seed import seed_permissions
from app.db.session import get_db_session
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride
from app.models.user import ROLES, User, UserRole
from app.schemas.base import BaseSchema

router = APIRouter(
    prefix="/rbac",
    tags=["RBAC"],
)


# =========================================================
# SCHEMAS
# =========================================================

class RolePermissionUpdate(BaseSchema):
    permissions: list[str]


class RolePermissionAdd(BaseSchema):
    permission: Optional[str] = None
    permissions: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_permissions(cls, data: Any) -> Any:
        if isinstance(data, dict):
            perms = []
            if "permission" in data and data["permission"]:
                val = data["permission"]
                if isinstance(val, str) and val.strip():
                    perms.append(val.strip())
                elif isinstance(val, list):
                    perms.extend([p.strip() for p in val if isinstance(p, str) and p.strip()])
            if "permissions" in data and data["permissions"]:
                val = data["permissions"]
                if isinstance(val, list):
                    perms.extend([p.strip() for p in val if isinstance(p, str) and p.strip()])
                elif isinstance(val, str) and val.strip():
                    perms.append(val.strip())

            # Deduplicate preserving insertion order
            seen = set()
            normalized = []
            for p in perms:
                if p not in seen:
                    seen.add(p)
                    normalized.append(p)

            if not normalized:
                raise ValueError("At least one permission must be provided via 'permission' or 'permissions'.")

            data["permissions"] = normalized
        return data


class RolePermissionDelete(BaseSchema):
    permission: Optional[str] = None
    permissions: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_permissions(cls, data: Any) -> Any:
        if isinstance(data, dict):
            perms = []
            if "permission" in data and data["permission"]:
                val = data["permission"]
                if isinstance(val, str) and val.strip():
                    perms.append(val.strip())
                elif isinstance(val, list):
                    perms.extend([p.strip() for p in val if isinstance(p, str) and p.strip()])
            if "permissions" in data and data["permissions"]:
                val = data["permissions"]
                if isinstance(val, list):
                    perms.extend([p.strip() for p in val if isinstance(p, str) and p.strip()])
                elif isinstance(val, str) and val.strip():
                    perms.append(val.strip())

            # Deduplicate preserving insertion order
            seen = set()
            normalized = []
            for p in perms:
                if p not in seen:
                    seen.add(p)
                    normalized.append(p)

            if not normalized:
                raise ValueError("At least one permission must be provided via 'permission' or 'permissions'.")

            data["permissions"] = normalized
        return data


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
# RBAC INTERNAL HELPERS FOR TENANT ISOLATION
# =========================================================

async def _validate_role_exists(
    db: AsyncSession,
    role_name: str,
    current_user: User,
) -> None:
    """
    Checks if a role is known in the system (either built-in or created for this company).
    If completely unknown, raises 404 HTTPException.
    """
    if role_name in ROLES:
        return

    # Check company-specific role
    if current_user.company_id is not None:
        c_role = await db.scalar(
            select(Role).where(
                Role.name == role_name,
                Role.company_id == current_user.company_id,
            )
        )
        if c_role:
            return

    # Check system / global role in DB
    sys_role = await db.scalar(
        select(Role).where(
            Role.name == role_name,
            (Role.company_id.is_(None)) | (Role.is_system == True),
        )
    )
    if sys_role:
        return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Role '{role_name}' not found",
    )


async def _get_or_create_company_role(
    db: AsyncSession,
    role_name: str,
    current_user: User,
    copy_defaults_if_created: bool = False,
) -> Optional[Role]:
    """
    Ensures that when a tenant admin customizes a role, the modifications are scoped
    strictly to that tenant's company, protecting global system defaults.

    Returns the company-scoped Role instance.
    If current_user is super_admin with no company_id, returns None (global scope).
    Under Admin-driven model, newly created company roles start with permissions = []
    unless copy_defaults_if_created is explicitly requested for custom global templates.
    """
    if current_user.company_id is None and current_user.is_super_admin:
        return None

    company_id = current_user.company_id
    company_role = await db.scalar(
        select(Role).where(
            Role.name == role_name,
            Role.company_id == company_id,
        )
    )
    if company_role is not None:
        return company_role

    # Create company-scoped role entry
    existing = await db.scalar(
        select(Role).where(
            Role.name == role_name,
            (Role.company_id.is_(None)) | (Role.is_system == True),
        )
    )
    display_name = existing.display_name if existing else role_name
    description = existing.description if existing else f"Customized {role_name} for company {company_id}"

    company_role = Role(
        company_id=company_id,
        name=role_name,
        display_name=display_name,
        description=description,
        is_system=False,
    )
    db.add(company_role)
    await db.flush()

    if copy_defaults_if_created and role_name not in ROLES:
        # Only copy defaults for custom roles that intentionally defined global defaults
        res = await db.execute(
            select(Permission.id)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .outerjoin(Role, RolePermission.role_id == Role.id)
            .where(
                RolePermission.role == role_name,
                (RolePermission.role_id.is_(None)) | (Role.company_id.is_(None)) | (Role.is_system == True),
            )
        )
        default_perm_ids = res.scalars().all()
        if default_perm_ids:
            mappings = [
                RolePermission(
                    role=role_name,
                    permission_id=pid,
                    role_id=company_role.id,
                )
                for pid in default_perm_ids
            ]
            db.add_all(mappings)
            await db.flush()

    return company_role


async def _fetch_role_permissions_list(
    db: AsyncSession,
    role_name: str,
    company_role: Optional[Role],
) -> list[str]:
    """
    Returns ordered list of permission codes for the given role scope.
    Under Admin-driven model:
    - Admin dynamically has access to ALL permissions from the permissions catalog.
    - Company-scoped role has exactly its explicitly assigned permissions.
    - Built-in non-admin roles have zero implicit permissions.
    """
    # 1. Admin dynamically resolves all permissions in the permissions catalog
    if role_name == UserRole.ADMIN.value or role_name == "Admin":
        res = await db.execute(
            select(Permission.code).where(Permission.code != "*").order_by(Permission.code)
        )
        return list(res.scalars().all())

    # 2. Explicit company-scoped role permissions
    if company_role is not None:
        res = await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == company_role.id)
            .order_by(Permission.code)
        )
        return list(res.scalars().all())

    # 3. Built-in non-admin roles have ZERO implicit permissions
    if role_name in ROLES:
        return []

    # 4. Custom global roles (is_system=True or un-scoped test roles)
    res = await db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .outerjoin(Role, RolePermission.role_id == Role.id)
        .where(
            RolePermission.role == role_name,
            (RolePermission.role_id.is_(None)) | (Role.company_id.is_(None)) | (Role.is_system == True),
        )
        .order_by(Permission.code)
    )
    return list(res.scalars().all())


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
            Permission.action,
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
    if existing or (payload.name in ROLES and not current_user.is_super_admin):
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
# DELETE CUSTOM ROLE
# =========================================================

@router.delete("/roles/{role}")
async def delete_custom_role(
    role: str,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    # 1. Built-in / system roles cannot be deleted
    if role in ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete built-in system role '{role}'",
        )

    # 2. Check if role exists for this company
    stmt = select(Role).where(Role.name == role)
    if not current_user.is_super_admin:
        stmt = stmt.where(Role.company_id == current_user.company_id)

    target_role = await db.scalar(stmt)
    if not target_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role}' not found",
        )

    if target_role.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete built-in system role '{role}'",
        )

    # 3. Check whether users are currently assigned to the role
    user_stmt = select(func.count(User.id)).where(
        User.role == role,
        User.is_deleted == False,
    )
    if not current_user.is_super_admin:
        user_stmt = user_stmt.where(User.company_id == current_user.company_id)

    assigned_count = await db.scalar(user_stmt) or 0
    if assigned_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete role '{role}' because {assigned_count} user(s) are currently assigned to it. Reassign users first.",
        )

    # 4. Safely delete related RolePermission mappings
    await db.execute(
        delete(RolePermission).where(RolePermission.role_id == target_role.id)
    )

    # 5. Delete the Role record
    await db.delete(target_role)
    await db.commit()

    return {
        "message": f"Role '{role}' deleted successfully",
        "role": role,
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
    await _validate_role_exists(db, role, current_user)

    company_role = None
    if current_user.company_id is not None:
        company_role = await db.scalar(
            select(Role).where(
                Role.name == role,
                Role.company_id == current_user.company_id,
            )
        )

    permissions = await _fetch_role_permissions_list(db, role, company_role)

    return {
        "role": role,
        "permissions": permissions,
    }


# =========================================================
# INCREMENTAL ADD ROLE PERMISSION(S)
# =========================================================

@router.post("/roles/{role}/permissions")
async def add_role_permissions(
    role: str,
    payload: RolePermissionAdd,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    await _validate_role_exists(db, role, current_user)

    perms_to_add = payload.permissions or []

    # Validate all requested permissions exist in DB
    result = await db.execute(
        select(Permission).where(Permission.code.in_(perms_to_add))
    )
    valid_permissions = result.scalars().all()
    valid_map = {p.code: p.id for p in valid_permissions}
    invalid_codes = [c for c in perms_to_add if c not in valid_map]
    if invalid_codes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown permission code(s): {', '.join(invalid_codes)}",
        )

    # Scoped company role (starts with zero implicit permissions)
    company_role = await _get_or_create_company_role(
        db, role, current_user, copy_defaults_if_created=False
    )

    # Fetch currently assigned permission IDs for this role scope
    if company_role is not None:
        curr_res = await db.execute(
            select(RolePermission.permission_id).where(RolePermission.role_id == company_role.id)
        )
    else:
        curr_res = await db.execute(
            select(RolePermission.permission_id).where(
                RolePermission.role == role,
                RolePermission.role_id.is_(None),
            )
        )
    existing_pids = set(curr_res.scalars().all())

    # Only add permissions that aren't already assigned (idempotent / prevent duplicates)
    new_mappings = []
    added_codes = []
    for code in perms_to_add:
        pid = valid_map[code]
        if pid not in existing_pids:
            new_mappings.append(
                RolePermission(
                    role=role,
                    permission_id=pid,
                    role_id=company_role.id if company_role else None,
                )
            )
            existing_pids.add(pid)
            added_codes.append(code)

    if new_mappings:
        db.add_all(new_mappings)
        await db.commit()

    # Re-fetch complete sorted list of permissions
    resulting_permissions = await _fetch_role_permissions_list(db, role, company_role)

    return {
        "message": "Permissions added successfully",
        "role": role,
        "added": added_codes,
        "permissions": resulting_permissions,
    }


# =========================================================
# UPDATE ROLE PERMISSIONS (FULL REPLACEMENT)
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
    await _validate_role_exists(db, role, current_user)

    # Validate all requested permissions exist in DB
    result = await db.execute(
        select(Permission).where(Permission.code.in_(payload.permissions))
    )
    valid_permissions = result.scalars().all()
    valid_codes = {p.code for p in valid_permissions}
    invalid_codes = [c for c in payload.permissions if c not in valid_codes]
    if invalid_codes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown permission code(s): {', '.join(invalid_codes)}",
        )

    permission_ids = [p.id for p in valid_permissions]

    # Scoped company role (copy_defaults_if_created=False because PUT is FULL REPLACEMENT)
    company_role = await _get_or_create_company_role(
        db, role, current_user, copy_defaults_if_created=False
    )

    if company_role is not None:
        # Tenant scope: delete old mappings for this company role
        await db.execute(
            delete(RolePermission).where(RolePermission.role_id == company_role.id)
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
        # Global scope (super_admin with no company_id)
        await db.execute(
            delete(RolePermission).where(
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

    resulting_permissions = await _fetch_role_permissions_list(db, role, company_role)

    return {
        "message": "Role permissions replaced successfully",
        "role": role,
        "permissions": resulting_permissions,
    }


# =========================================================
# DELETE SINGLE ROLE PERMISSION
# =========================================================

@router.delete("/roles/{role}/permissions/{permission}")
async def delete_single_role_permission(
    role: str,
    permission: str,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    await _validate_role_exists(db, role, current_user)

    target_perm = await db.scalar(select(Permission).where(Permission.code == permission))
    if not target_perm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown permission code: '{permission}'",
        )

    # Scoped company role (starts with zero implicit permissions)
    company_role = await _get_or_create_company_role(
        db, role, current_user, copy_defaults_if_created=False
    )

    if company_role is not None:
        await db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == company_role.id,
                RolePermission.permission_id == target_perm.id,
            )
        )
    else:
        await db.execute(
            delete(RolePermission).where(
                RolePermission.role == role,
                RolePermission.role_id.is_(None),
                RolePermission.permission_id == target_perm.id,
            )
        )

    await db.commit()

    resulting_permissions = await _fetch_role_permissions_list(db, role, company_role)

    return {
        "message": f"Permission '{permission}' removed from role '{role}'",
        "role": role,
        "removed": permission,
        "permissions": resulting_permissions,
    }


# =========================================================
# DELETE BULK ROLE PERMISSIONS
# =========================================================

@router.delete("/roles/{role}/permissions")
async def delete_bulk_role_permissions(
    role: str,
    payload: RolePermissionDelete,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    await _validate_role_exists(db, role, current_user)

    perms_to_remove = payload.permissions or []

    # Validate all permission codes
    result = await db.execute(
        select(Permission).where(Permission.code.in_(perms_to_remove))
    )
    valid_permissions = result.scalars().all()
    valid_map = {p.code: p.id for p in valid_permissions}
    invalid_codes = [c for c in perms_to_remove if c not in valid_map]
    if invalid_codes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown permission code(s): {', '.join(invalid_codes)}",
        )

    target_pids = [valid_map[c] for c in perms_to_remove]

    # Scoped company role (starts with zero implicit permissions)
    company_role = await _get_or_create_company_role(
        db, role, current_user, copy_defaults_if_created=False
    )

    if company_role is not None:
        await db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == company_role.id,
                RolePermission.permission_id.in_(target_pids),
            )
        )
    else:
        await db.execute(
            delete(RolePermission).where(
                RolePermission.role == role,
                RolePermission.role_id.is_(None),
                RolePermission.permission_id.in_(target_pids),
            )
        )

    await db.commit()

    resulting_permissions = await _fetch_role_permissions_list(db, role, company_role)

    return {
        "message": f"Permissions removed from role '{role}'",
        "role": role,
        "removed": perms_to_remove,
        "permissions": resulting_permissions,
    }


# =========================================================
# RESET ROLE DEFAULTS
# =========================================================

@router.post("/roles/{role}/reset-defaults")
async def reset_role_defaults(
    role: str,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN.value])
    ),
    db: AsyncSession = Depends(get_db_session),
):
    await _validate_role_exists(db, role, current_user)

    if current_user.company_id is not None:
        company_role = await db.scalar(
            select(Role).where(
                Role.name == role,
                Role.company_id == current_user.company_id,
            )
        )
        if company_role:
            # Delete customized role permissions
            await db.execute(
                delete(RolePermission).where(RolePermission.role_id == company_role.id)
            )
            # Delete company-scoped role entry so it falls back cleanly to system defaults
            await db.delete(company_role)
            await db.commit()

    # Re-fetch system default permissions
    default_permissions = await _fetch_role_permissions_list(db, role, company_role=None)

    return {
        "message": f"Role '{role}' permissions reset to system defaults",
        "role": role,
        "permissions": default_permissions,
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
# SEEDING & DEFAULTS (MAINTENANCE)
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