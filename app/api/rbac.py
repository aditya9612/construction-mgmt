from datetime import date, datetime, time
from collections import defaultdict
from typing import Any, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.default_role_permissions import assign_default_role_permissions
from app.core.dependencies import (
    get_effective_user_permissions,
    has_permission,
    require_permission,
    require_super_admin,
)
from app.core.rbac_seed import seed_permissions
from app.db.session import get_db_session
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride, RBACAuditLog
from app.models.user import ROLES, User, UserRole
from app.schemas.base import BaseSchema
from app.services.rbac_audit import record_rbac_audit

router = APIRouter(
    prefix="/rbac",
    tags=["RBAC"],
)


# =========================================================
# SCHEMAS
# =========================================================

class RBACAuditLogOut(BaseSchema):
    id: int
    company_id: Optional[int] = None
    actor_id: Optional[int] = None
    action: str
    target_type: str
    target_id: Optional[str] = None
    permission: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    created_at: datetime


class RBACAuditLogsResponse(BaseSchema):
    total: int
    page: int
    page_size: int
    items: List[RBACAuditLogOut]


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
# RBAC INTERNAL HELPERS FOR TENANT ISOLATION & SECURITY
# =========================================================

def _require_tenant_context(current_user: User) -> None:
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant context required",
        )


async def _validate_permission_boundary(
    db: AsyncSession,
    caller: User,
    requested_permissions: list[str],
) -> None:
    """
    Validates that a non-SA caller does not grant permissions outside their
    own effective permissions boundary.
    Prevents privilege escalation via role permissions or user overrides.
    """
    if getattr(caller, "is_super_admin", False) is True:
        return

    caller_effective = await get_effective_user_permissions(db, caller)

    for perm in requested_permissions:
        if perm == "*":
            if "*" not in caller_effective:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot grant wildcard '*' permission outside your effective permissions",
                )
        elif perm.endswith(".*"):
            if "*" not in caller_effective and perm not in caller_effective:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Cannot grant wildcard '{perm}' permission outside your effective permissions",
                )
        else:
            if not has_permission(caller_effective, perm):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Cannot grant permission '{perm}' outside your effective permissions boundary",
                )


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

    is_sa = getattr(current_user, "is_super_admin", False) is True
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
    elif is_sa:
        any_role = await db.scalar(
            select(Role).where(
                Role.name == role_name,
            )
        )
        if any_role:
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
    _require_tenant_context(current_user)
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if current_user.company_id is None and is_sa:
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
        require_permission("roles.view")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
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
        require_permission("roles.view")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
    is_sa = getattr(current_user, "is_super_admin", False) is True
    stmt = select(Role)
    if not is_sa:
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
        require_permission("roles.create")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
    is_sa = getattr(current_user, "is_super_admin", False) is True
    role_name = payload.name.strip()
    if not role_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role name cannot be empty",
        )

    # Built-in role collision check (case-insensitive)
    built_in_lower = {r.lower() for r in ROLES}
    if role_name.lower() in built_in_lower and not is_sa:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot create custom role matching built-in role name '{role_name}'",
        )

    # Check if role already exists for this tenant (case-insensitive query)
    existing = await db.scalar(
        select(Role).where(
            func.lower(Role.name) == role_name.lower(),
            Role.company_id == current_user.company_id,
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role with this name already exists for your company",
        )

    new_role = Role(
        company_id=current_user.company_id if not is_sa else None,
        name=role_name,
        display_name=payload.display_name.strip() if payload.display_name else role_name,
        description=payload.description.strip() if payload.description else None,
        is_system=False if not is_sa else True,
    )
    db.add(new_role)
    await db.flush()

    # Transactional audit logging
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="ROLE_CREATE",
        target_type="ROLE",
        target_id=new_role.name,
        company_id=new_role.company_id,
        new_value={
            "id": new_role.id,
            "name": new_role.name,
            "display_name": new_role.display_name,
            "is_system": new_role.is_system,
        },
    )

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
        require_permission("roles.delete")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
    is_sa = getattr(current_user, "is_super_admin", False) is True
    role_clean = role.strip()

    # 1. Built-in / system roles cannot be deleted (case-insensitive check)
    built_in_lower = {r.lower() for r in ROLES}
    if role_clean.lower() in built_in_lower:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete built-in system role '{role}'",
        )

    # 2. Check if role exists for this company
    stmt = select(Role).where(func.lower(Role.name) == role_clean.lower())
    if not is_sa:
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
        func.lower(User.role) == role_clean.lower(),
        User.is_deleted == False,
    )
    if not is_sa:
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

    # Transactional audit logging
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="ROLE_DELETE",
        target_type="ROLE",
        target_id=target_role.name,
        company_id=target_role.company_id,
        old_value={"id": target_role.id, "name": target_role.name},
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
        require_permission("roles.view")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
    await _validate_role_exists(db, role, current_user)
    is_sa = getattr(current_user, "is_super_admin", False) is True

    company_role = None
    if current_user.company_id is not None:
        company_role = await db.scalar(
            select(Role).where(
                Role.name == role,
                Role.company_id == current_user.company_id,
            )
        )
    elif is_sa:
        company_role = await db.scalar(
            select(Role).where(
                Role.name == role,
                Role.company_id.is_(None),
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
        require_permission("roles.edit")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
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

    # Validate privilege boundary
    await _validate_permission_boundary(db, current_user, perms_to_add)

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

    # Re-fetch complete sorted list of permissions
    resulting_permissions = await _fetch_role_permissions_list(db, role, company_role)

    # Transactional audit logging
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="ROLE_PERMISSIONS_ADD",
        target_type="ROLE",
        target_id=role,
        company_id=company_role.company_id if company_role else None,
        new_value={"added": added_codes, "permissions": resulting_permissions},
    )

    await db.commit()

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
        require_permission("roles.edit")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
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

    # Validate privilege boundary BEFORE modifying database
    await _validate_permission_boundary(db, current_user, payload.permissions)

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

    resulting_permissions = await _fetch_role_permissions_list(db, role, company_role)

    # Transactional audit logging
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="ROLE_PERMISSIONS_UPDATE",
        target_type="ROLE",
        target_id=role,
        company_id=company_role.company_id if company_role else None,
        new_value={"permissions": resulting_permissions},
    )

    await db.commit()

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
        require_permission("roles.edit")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
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

    # Re-fetch complete sorted list of permissions
    resulting_permissions = await _fetch_role_permissions_list(db, role, company_role)

    # Transactional audit logging
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="ROLE_PERMISSION_DELETE",
        target_type="ROLE",
        target_id=role,
        permission=permission,
        company_id=company_role.company_id if company_role else None,
        old_value=permission,
        new_value={"permissions": resulting_permissions},
    )

    await db.commit()

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
        require_permission("roles.edit")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
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

    resulting_permissions = await _fetch_role_permissions_list(db, role, company_role)

    # Transactional audit logging
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="ROLE_PERMISSIONS_BULK_DELETE",
        target_type="ROLE",
        target_id=role,
        company_id=company_role.company_id if company_role else None,
        old_value={"removed": perms_to_remove},
        new_value={"permissions": resulting_permissions},
    )

    await db.commit()

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
        require_permission("roles.edit")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
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

    # Re-fetch system default permissions
    default_permissions = await _fetch_role_permissions_list(db, role, company_role=None)

    # Transactional audit logging
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="ROLE_DEFAULTS_RESET",
        target_type="ROLE",
        target_id=role,
        company_id=current_user.company_id,
        new_value={"permissions": default_permissions},
    )

    await db.commit()

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
        require_permission("roles.view")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
    is_sa = getattr(current_user, "is_super_admin", False) is True

    # Database-level tenant query filtering (prevents cross-tenant existence leakage)
    stmt = select(User).where(User.id == user_id, User.is_deleted == False)
    if not is_sa:
        stmt = stmt.where(User.company_id == current_user.company_id)

    target_user = await db.scalar(stmt)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
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
        require_permission("roles.edit")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    _require_tenant_context(current_user)
    is_sa = getattr(current_user, "is_super_admin", False) is True

    # Database-level tenant query filtering (prevents cross-tenant existence leakage)
    stmt = select(User).where(User.id == user_id, User.is_deleted == False)
    if not is_sa:
        stmt = stmt.where(User.company_id == current_user.company_id)

    target_user = await db.scalar(stmt)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Self-escalation check: Admins cannot modify their own permission overrides
    if not is_sa and current_user.id == target_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins cannot modify their own permission overrides",
        )

    # Fetch permissions by code and validate against catalog
    perm_codes = [item.permission for item in payload.overrides]
    if perm_codes:
        res = await db.execute(
            select(Permission).where(Permission.code.in_(perm_codes))
        )
        perms = {p.code: p.id for p in res.scalars().all()}
        invalid_codes = [c for c in perm_codes if c not in perms]
        if invalid_codes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown permission code(s): {', '.join(invalid_codes)}",
            )
    else:
        perms = {}

    # Validate privilege boundary for any granted permission
    granted_perms = [item.permission for item in payload.overrides if item.is_granted]
    if granted_perms:
        await _validate_permission_boundary(db, current_user, granted_perms)

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

    # Transactional audit logging
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="USER_OVERRIDES_UPDATE",
        target_type="USER_OVERRIDE",
        target_id=str(user_id),
        company_id=target_user.company_id,
        new_value=[{"permission": item.permission, "is_granted": item.is_granted} for item in payload.overrides],
    )

    await db.commit()

    return {
        "message": "User permission overrides updated successfully",
        "user_id": user_id,
        "overrides": payload.overrides,
    }


# =========================================================
# SEEDING & DEFAULTS (MAINTENANCE) - RESTRICTED TO SUPER ADMIN
# =========================================================

@router.post("/seed")
async def seed_rbac_permissions(
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
):
    res = await seed_permissions(db)

    # Transactional audit logging for system maintenance
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="RBAC_SEED",
        target_type="SYSTEM",
        target_id="permissions_catalog",
        company_id=None,
        new_value=res,
    )

    await db.commit()
    return res


@router.post("/assign-defaults")
async def assign_defaults(
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db_session),
):
    res = await assign_default_role_permissions(db)

    # Transactional audit logging for system maintenance
    await record_rbac_audit(
        db=db,
        actor=current_user,
        action="RBAC_ASSIGN_DEFAULTS",
        target_type="SYSTEM",
        target_id="default_role_permissions",
        company_id=None,
        new_value=res,
    )

    await db.commit()
    return res


# =========================================================
# AUDIT LOGS QUERY API
# =========================================================

@router.get("/audit-logs", response_model=RBACAuditLogsResponse)
async def get_rbac_audit_logs(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    start_date: Optional[date] = Query(None, description="Filter logs on or after this date"),
    end_date: Optional[date] = Query(None, description="Filter logs on or before this date"),
    action: Optional[str] = Query(None, description="Filter by action code"),
    actor_id: Optional[int] = Query(None, description="Filter by actor user ID"),
    target_type: Optional[str] = Query(None, description="Filter by target type"),
    company_id: Optional[int] = Query(None, description="Filter by company ID (Super Admin only)"),
    current_user: User = Depends(
        require_permission("roles.view")
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Retrieve RBAC audit trail logs with strict multi-tenant isolation.
    - Tenant Admins can only query their own company's audit records.
    - Super Admins can query all records or filter by company_id.
    - Deterministic ordering by newest records first (created_at DESC, id DESC).
    """
    _require_tenant_context(current_user)
    is_sa = getattr(current_user, "is_super_admin", False) is True

    query = select(RBACAuditLog)

    # Server-side tenant boundary enforcement
    if not is_sa:
        query = query.where(RBACAuditLog.company_id == current_user.company_id)
    else:
        if company_id is not None:
            query = query.where(RBACAuditLog.company_id == company_id)

    if action:
        query = query.where(RBACAuditLog.action == action.strip())

    if actor_id is not None:
        query = query.where(RBACAuditLog.actor_id == actor_id)

    if target_type:
        query = query.where(RBACAuditLog.target_type == target_type.strip())

    if start_date:
        query = query.where(
            RBACAuditLog.created_at >= datetime.combine(start_date, time.min)
        )

    if end_date:
        query = query.where(
            RBACAuditLog.created_at <= datetime.combine(end_date, time.max)
        )

    # Total count for pagination
    count_stmt = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_stmt) or 0

    # Deterministic ordering: newest records first
    query = query.order_by(RBACAuditLog.created_at.desc(), RBACAuditLog.id.desc())

    # Pagination calculation
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    items = result.scalars().all()

    return RBACAuditLogsResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )