from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.boq import BOQ
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.boq import BOQCreate, BOQOut, BOQUpdate
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/boq", tags=["boq"], dependencies=[default_rate_limiter_dependency()])

VERSION_KEY = "cache_version:boq"


@router.post("", response_model=BOQOut)
async def create_boq(
    payload: BOQCreate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    data = payload.model_dump(exclude_unset=True)
    if data.get("total_cost") is None:
        data["total_cost"] = Decimal(data.get("quantity")) * Decimal(data.get("unit_cost"))
    obj = BOQ(**data)
    db.add(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return BOQOut.model_validate(obj)


@router.get("", response_model=PaginatedResponse[BOQOut])
async def list_boq(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:boq:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[BOQOut].model_validate(cached)

    query = select(BOQ)
    count_query = select(func.count()).select_from(BOQ)

    if search:
        like = f"%{search}%"
        query = query.where(BOQ.item_name.ilike(like))
        count_query = count_query.where(BOQ.item_name.ilike(like))

    if status:
        query = query.where(BOQ.status == status)
        count_query = count_query.where(BOQ.status == status)

    if project_id is not None:
        query = query.where(BOQ.project_id == project_id)
        count_query = count_query.where(BOQ.project_id == project_id)

    query = query.order_by(BOQ.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [BOQOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
    result = {"items": items, "meta": meta.model_dump()}
    await cache_set_json(redis, cache_key, result)
    return PaginatedResponse[BOQOut].model_validate(result)


@router.get("/{boq_id}", response_model=BOQOut)
async def get_boq(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:boq:get:{version}:{boq_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return BOQOut.model_validate(cached)

    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id))
    if obj is None:
        raise NotFoundError("BOQ item not found")

    out = BOQOut.model_validate(obj)
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


@router.put("/{boq_id}", response_model=BOQOut)
async def update_boq(
    boq_id: int,
    payload: BOQUpdate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id))
    if obj is None:
        raise NotFoundError("BOQ item not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)

    if "quantity" in data or "unit_cost" in data:
        obj.total_cost = Decimal(obj.quantity) * Decimal(obj.unit_cost)

    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return BOQOut.model_validate(obj)


@router.delete("/{boq_id}", status_code=204)
async def delete_boq(
    boq_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id))
    if obj is None:
        raise NotFoundError("BOQ item not found")

    await db.delete(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return None
