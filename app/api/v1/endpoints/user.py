from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.user import UserOut


router = APIRouter(dependencies=[default_rate_limiter_dependency()])


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_active_user)):
    return current_user


@router.get("", response_model=PaginatedResponse[UserOut])
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
):
    query = select(User)
    count_query = select(func.count()).select_from(User)

    if search:
        like = f"%{search}%"
        query = query.where(User.email.ilike(like))
        count_query = count_query.where(User.email.ilike(like))

    total = await db.scalar(count_query)
    rows = (await db.execute(query.order_by(User.id.desc()).limit(limit).offset(offset))).scalars().all()

    items = [UserOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
    return {"items": items, "meta": meta.model_dump()}

