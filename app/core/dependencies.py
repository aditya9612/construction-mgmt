from typing import Callable, Iterable, List, Optional, Dict, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.cache.redis import cache_get_json, cache_set_json
from app.core.logger import logger
from app.core.security import decode_access_token
from app.db.session import get_db_session
from app.models.user import User, UserRole, ROLES
from app.core.request_context import set_current_user_id
from app.models.rbac import Permission, RolePermission, Role, UserPermissionOverride
from app.models.company import Company

security = HTTPBearer()


# async def get_current_user(
#     credentials: HTTPAuthorizationCredentials = Depends(security),
#     db: AsyncSession = Depends(get_db_session),
# ) -> User:
#     token = credentials.credentials

#     credentials_exception = HTTPException(
#         status_code=status.HTTP_401_UNAUTHORIZED,
#         detail="Could not validate credentials",
#         headers={"WWW-Authenticate": "Bearer"},
#     )

#     try:
#         payload = decode_access_token(token)
#         user_id = payload.get("sub")
#         if user_id is None:
#             raise credentials_exception
#     except Exception:
#         logger.warning("JWT decode failed")
#         raise credentials_exception

#     user = await db.scalar(select(User).where(User.id == int(user_id)))
#     if user is None:
#         logger.warning(f"User not found id={user_id}")
#         raise credentials_exception

#     return user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception

        jti = payload.get("jti")
        iat = payload.get("iat")
    except Exception:
        logger.warning("JWT decode failed")
        raise credentials_exception

    redis = getattr(request.app.state, "redis", None)

    # --------------------------------------------------
    # Token Blocklist & Logout All Checks (Redis)
    # --------------------------------------------------
    if redis:
        try:
            if jti:
                is_blocked = await redis.exists(f"blocklist:jti:{jti}")
                if is_blocked:
                    logger.warning(f"Blocked token used jti={jti}")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Token has been logged out",
                    )

            if iat:
                logout_all_ts = await redis.get(f"logout_all:user:{user_id}")
                if logout_all_ts:
                    if float(iat) < float(logout_all_ts):
                        logger.warning(
                            f"Token issued before logout_all for user={user_id}"
                        )
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="All sessions have been terminated",
                        )

        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Redis blocklist check failed: {e}")

    # --------------------------------------------------
    # Try Redis cache first
    # --------------------------------------------------
    cache_key = f"cache:user:{user_id}"

    if redis:
        try:
            cached = await cache_get_json(redis, cache_key)

            if cached:
                user = await db.scalar(
                    select(User)
                    .options(selectinload(User.project_memberships))
                    .where(User.id == int(user_id))
                )

                if user:
                    return user

        except Exception as e:
            logger.warning(f"Redis cache read failed: {e}")

    # --------------------------------------------------
    # Fallback to database (ALWAYS EXECUTES)
    # --------------------------------------------------
    user = await db.scalar(
        select(User)
        .options(selectinload(User.project_memberships))
        .where(User.id == int(user_id))
    )

    if user is None:
        logger.warning(f"User not found id={user_id}")
        raise credentials_exception

    # --------------------------------------------------
    # Store in Redis for future requests
    # --------------------------------------------------
    if redis:
        try:
            await cache_set_json(
                redis,
                cache_key,
                {
                    "id": user.id,
                    "email": user.email,
                    "hashed_password": user.hashed_password,
                    "full_name": user.full_name,
                    "mobile": user.mobile,
                    "role": user.role,
                    "is_active": user.is_active,
                    "is_deleted": user.is_deleted,
                },
            )
        except Exception as e:
            logger.warning(f"Redis cache write failed: {e}")

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive"
        )

    #  SET USER ID IN CONTEXT
    set_current_user_id(current_user.id)

    # P0-1: Super Admin immediately returns, never blocked by tenant state
    if getattr(current_user, "is_super_admin", False) is True:
        return current_user

    # Normal user must belong to a company
    if current_user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to any company.",
        )

    # Normal user company must exist and be active
    company = await db.scalar(
        select(Company).where(Company.id == current_user.company_id)
    )
    if not company or not company.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Company is inactive or not found.",
        )

    return current_user


async def get_current_tenant(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session)
) -> Company:
    if current_user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to any company."
        )
    company = await db.scalar(select(Company).where(Company.id == current_user.company_id))
    if not company or not company.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Company is inactive or not found."
        )
    return company


async def require_super_admin(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin privileges required."
        )
    return current_user


async def require_tenant_admin(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if not current_user.company_id or current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant Admin privileges required.",
        )
    if current_user.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant Admin privileges required.",
        )
    return current_user



# def require_roles(allowed_roles: Iterable[UserRole]) -> Callable[[User], User]:
#     allowed: List[UserRole] = list(allowed_roles)

#     async def _dependency(
#         current_user: User = Depends(get_current_active_user),
#     ) -> User:
#         if current_user.role not in allowed:
#             raise HTTPException(
#                 status_code=status.HTTP_403_FORBIDDEN,
#                 detail=f"Insufficient permissions. Required: {[r.value for r in allowed]}",
#             )
#         return current_user

#     return _dependency

from fastapi import Depends


async def get_effective_user_permissions(
    db: AsyncSession,
    user: User,
) -> set[str]:
    """
    Resolves the effective permission codes for the given user from the database.

    Admin-Driven Authorization Model:
    1. Admin (Super Admin or Tenant Admin where user.role == 'Admin'):
       - Dynamically resolves ALL permissions available in the permissions catalog (permissions table).
       - Does NOT rely on a hardcoded permission list or static assignments.
       - Honors negative user-level overrides (UserPermissionOverride.is_granted == False) if specified.
    2. Non-Admin Roles (Client, Labour, SiteEngineer, Contractor, Accountant, ProjectManager, Custom Roles):
       - Start with ZERO implicit permissions (permissions = []).
       - Effective permissions derive strictly from permissions explicitly assigned by an Admin
         to this company's role (RolePermission.role_id == company_role.id).
       - Built-in non-admin roles NEVER inherit global/system fallback permissions.
       - Applies user-level overrides (is_granted=True adds, is_granted=False removes).
    """
    is_admin = (
        getattr(user, "is_super_admin", False) is True
        or user.role == UserRole.ADMIN.value
        or user.role == "Admin"
    )

    # =========================================================================
    # A. ADMIN ROLE: Dynamic Full Permission Access from Permissions Catalog
    # =========================================================================
    if is_admin:
        catalog_res = await db.execute(
            select(Permission.code).where(Permission.code != "*")
        )
        effective_permissions: set[str] = set(catalog_res.scalars().all())

        # Check for user-level explicit revocations on this admin user
        override_res = await db.execute(
            select(Permission.code)
            .join(UserPermissionOverride, UserPermissionOverride.permission_id == Permission.id)
            .where(
                UserPermissionOverride.user_id == user.id,
                UserPermissionOverride.is_granted == False,
            )
        )
        revoked = set(override_res.scalars().all())
        effective_permissions -= revoked
        return effective_permissions

    # =========================================================================
    # B. NON-ADMIN ROLES: Zero Implicit Access -> Explicit Company Assignments
    # =========================================================================
    company_id = user.company_id
    effective_permissions = set()

    # 1. Resolve company-scoped role permissions if user belongs to a company
    if company_id is not None:
        company_role = await db.scalar(
            select(Role).where(
                Role.name == user.role,
                Role.company_id == company_id,
            )
        )
        if company_role is not None:
            if user.role not in ROLES:
                res = await db.execute(
                    select(Permission.code)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .where(
                        (RolePermission.role_id == company_role.id)
                        | (
                            (RolePermission.role == user.role)
                            & RolePermission.role_id.is_(None)
                        )
                    )
                )
            else:
                res = await db.execute(
                    select(Permission.code)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .where(RolePermission.role_id == company_role.id)
                )
            effective_permissions = set(res.scalars().all())
        elif user.role not in ROLES:
            # Fallback only for ad-hoc custom/test roles created directly with role_id is None
            res = await db.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(
                    RolePermission.role == user.role,
                    RolePermission.role_id.is_(None),
                )
            )
            effective_permissions = set(res.scalars().all())
    elif user.role not in ROLES:
        # Fallback for un-scoped standalone test/custom roles created with role_id is None
        res = await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(
                RolePermission.role == user.role,
                RolePermission.role_id.is_(None),
            )
        )
        effective_permissions = set(res.scalars().all())

    # 2. Query user permission overrides
    override_res = await db.execute(
        select(Permission.code, UserPermissionOverride.is_granted)
        .join(UserPermissionOverride, UserPermissionOverride.permission_id == Permission.id)
        .where(UserPermissionOverride.user_id == user.id)
    )
    overrides = override_res.all()
    revoked_permissions: set[str] = set()

    for code, is_granted in overrides:
        if is_granted:
            effective_permissions.add(code)
        else:
            effective_permissions.discard(code)
            revoked_permissions.add(code)

    # 3. Handle wildcard with revocations (if explicit wildcard was granted)
    if "*" in effective_permissions and revoked_permissions:
        all_perms_res = await db.execute(
            select(Permission.code).where(~Permission.code.contains("*"))
        )
        all_codes = set(all_perms_res.scalars().all())
        effective_permissions = (effective_permissions | all_codes) - revoked_permissions
        effective_permissions.discard("*")
        for rev in revoked_permissions:
            if "." in rev:
                mod = rev.split(".")[0]
                effective_permissions.discard(f"{mod}.*")

    return effective_permissions


def has_permission(effective_permissions: set[str], required: str) -> bool:
    """
    Checks if the required permission is satisfied by the effective permissions set.
    Supports exact match, global wildcard '*', and module-level wildcard 'module.*'.
    """
    if "*" in effective_permissions:
        return True
    if required in effective_permissions:
        return True
    module = required.split(".")[0] if "." in required else required
    if f"{module}.*" in effective_permissions:
        return True
    return False


def require_permission(permission: str):
    """
    FastAPI dependency factory enforcing a single granular permission.
    Evaluates Super Admin bypass and database-driven effective permissions.
    """
    async def _dependency(
        current_user: User = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db_session),
    ) -> User:
        if getattr(current_user, "is_super_admin", False) is True:
            return current_user

        effective_perms = await get_effective_user_permissions(db, current_user)

        if not has_permission(effective_perms, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Insufficient permissions",
                    "required": [permission],
                    "missing": [permission],
                },
            )
        return current_user

    _dependency.__name__ = f"require_permission_{permission.replace('.', '_')}"
    return _dependency


def require_permissions(required_permissions: list[str]):
    """
    FastAPI dependency factory enforcing multiple permissions (all must be satisfied).
    Evaluates Super Admin bypass and database-driven effective permissions.
    """
    async def _dependency(
        current_user: User = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db_session),
    ) -> User:
        if getattr(current_user, "is_super_admin", False) is True:
            return current_user

        effective_perms = await get_effective_user_permissions(db, current_user)

        missing = [
            p for p in required_permissions
            if not has_permission(effective_perms, p)
        ]

        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Insufficient permissions",
                    "required": required_permissions,
                    "missing": missing,
                },
            )
        return current_user

    _dependency.__name__ = "permission_dependency"
    return _dependency


def require_roles(
    allowed_roles: Optional[Iterable[str]] = None,
    permission: Optional[str] = None,
):
    """
    Role dependency supporting backward compatibility and permission-first authorization.
    If `permission` is provided, authorization is determined strictly by the database permission engine.
    If `permission` is omitted, falls back to role name check for unmigrated routes.
    """
    if permission:
        return require_permission(permission)

    allowed = list(allowed_roles) if allowed_roles else []

    async def _dependency(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if current_user.is_super_admin:
            return current_user

        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {allowed}",
            )
        return current_user

    _dependency.__name__ = "role_dependency"
    return _dependency


def require_feature(feature_key: str, feature_label: Optional[str] = None):
    """
    Enforces that the current tenant's active subscription has the specified feature enabled.
    Super Admins (platform level) are exempt from tenant feature limits.
    """
    async def _dependency(
        current_user: User = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db_session),
    ) -> User:
        if current_user.is_super_admin:
            return current_user

        if not current_user.company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User does not belong to any tenant company.",
            )

        from app.services.entitlement import get_entitlement_service
        service = get_entitlement_service()
        entitlements = await service.get_company_entitlements(db, current_user.company_id)

        if not entitlements.get("is_active"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Tenant subscription is {entitlements.get('status')}. Feature '{feature_label or feature_key}' is unavailable.",
            )

        has_feat = bool(entitlements.get("features", {}).get(feature_key, False))
        if not has_feat:
            label = feature_label or feature_key
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"The '{label}' feature is not included in your company plan. Please upgrade to access this feature.",
            )

        return current_user

    _dependency.__name__ = f"require_feature_{feature_key}"
    return _dependency


def get_request_redis(request: Request):
    redis = getattr(request.app.state, "redis", None)
    return redis

