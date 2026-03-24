from typing import Callable, Iterable, List
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.security import decode_access_token
from app.db.session import get_db_session
from app.models.user import User, UserRole

from app.core.errors import (
    UnauthorizedError,
    PermissionDeniedError,
    InternalServerError,
)

security = HTTPBearer()


# -------------------------
# CURRENT USER
# -------------------------
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_session),
):
    if not credentials:
        raise UnauthorizedError("Not authenticated")

    token = credentials.credentials

    try:
        payload = decode_access_token(token)
    except Exception:
        raise UnauthorizedError("Invalid or expired token")

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise UnauthorizedError("Invalid token payload")

    stmt = select(User).where(User.id == user_id).limit(1)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedError("User not found")

    return user


# -------------------------
# ACTIVE USER
# -------------------------
async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise PermissionDeniedError("User is inactive")
    return current_user


# -------------------------
# ROLE BASED ACCESS (FIXED)
# -------------------------
def require_roles(allowed_roles: Iterable[UserRole]) -> Callable[[User], User]:
    allowed_values = [r.value for r in allowed_roles]

    async def _dependency(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        user_role = (
            current_user.role.value
            if hasattr(current_user.role, "value")
            else current_user.role
        )

        if user_role not in allowed_values:
            raise PermissionDeniedError(
                f"Insufficient permissions. Required: {allowed_values}"
            )
        return current_user

    return _dependency


# -------------------------
# REDIS ACCESS
# -------------------------
def get_request_redis(request: Request):
    redis = getattr(request.app.state, "redis", None)

    if redis is None:
        raise InternalServerError("Redis not initialized")
    return redis