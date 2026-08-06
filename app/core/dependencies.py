from typing import Callable, Iterable, List

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.cache.redis import cache_get_json, cache_set_json
from app.core.logger import logger
from app.core.security import decode_access_token
from app.db.session import get_db_session
from app.models.user import User, UserRole
from app.core.request_context import set_current_user_id
from app.models.rbac import Permission, RolePermission

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
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive"
        )

    #  SET USER ID IN CONTEXT
    set_current_user_id(current_user.id)

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


def require_roles(allowed_roles: Iterable[str]):
    allowed = list(allowed_roles)

    async def _dependency(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {allowed}",
            )
        return current_user

    _dependency.__name__ = "role_dependency"
    return _dependency


def require_permissions(required_permissions: list[str]):

    async def _dependency(
        current_user: User = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db_session),
    ) -> User:

        # -------------------------------------------------
        # ADMIN BYPASS
        # -------------------------------------------------

        if current_user.role == UserRole.ADMIN.value:
            return current_user

        # -------------------------------------------------
        # FETCH USER ROLE PERMISSIONS
        # -------------------------------------------------

        result = await db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role == current_user.role)
        )

        user_permissions = set(result.scalars().all())

        # -------------------------------------------------
        # CHECK REQUIRED PERMISSIONS
        # -------------------------------------------------

        missing_permissions = [
            permission
            for permission in required_permissions
            if permission not in user_permissions
        ]

        if missing_permissions:

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Insufficient permissions",
                    "required": required_permissions,
                    "missing": missing_permissions,
                },
            )

        return current_user

    _dependency.__name__ = "permission_dependency"

    return _dependency


def get_request_redis(request: Request):
    redis = getattr(request.app.state, "redis", None)
    return redis
