from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.labour import Labour
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.labour import LabourCreate, LabourOut, LabourUpdate
from app.core.errors import NotFoundError

router = APIRouter(prefix="/labour", tags=["labour"], dependencies=[default_rate_limiter_dependency()])

VERSION_KEY = "cache_version:labour"


@router.post("", response_model=LabourOut)
async def create_labour(
    payload: LabourCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    data = payload.model_dump(exclude_unset=True)
    if data.get("total_cost") is None:
        data["total_cost"] = Decimal(data.get("quantity")) * Decimal(data.get("unit_cost"))
    obj = Labour(**data)
    db.add(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return LabourOut.model_validate(obj)


@router.get("", response_model=PaginatedResponse[LabourOut])
async def list_labour(
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
    cache_key = f"cache:labour:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[LabourOut].model_validate(cached)

    query = select(Labour)
    count_query = select(func.count()).select_from(Labour)

    if search:
        like = f"%{search}%"
        query = query.where(Labour.labour_title.ilike(like))
        count_query = count_query.where(Labour.labour_title.ilike(like))

    if status:
        query = query.where(Labour.status == status)
        count_query = count_query.where(Labour.status == status)

    if project_id is not None:
        query = query.where(Labour.project_id == project_id)
        count_query = count_query.where(Labour.project_id == project_id)

    query = query.order_by(Labour.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [LabourOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
    result = {"items": items, "meta": meta.model_dump()}
    await cache_set_json(redis, cache_key, result)
    return PaginatedResponse[LabourOut].model_validate(result)


@router.get("/{labour_id}", response_model=LabourOut)
async def get_labour(
    labour_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:labour:get:{version}:{labour_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return LabourOut.model_validate(cached)

    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))
    if obj is None:
        raise NotFoundError("Labour record not found")

    out = LabourOut.model_validate(obj)
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


@router.put("/{labour_id}", response_model=LabourOut)
async def update_labour(
    labour_id: int,
    payload: LabourUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))
    if obj is None:
        raise NotFoundError("Labour record not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)

    if "quantity" in data or "unit_cost" in data:
        obj.total_cost = Decimal(obj.quantity) * Decimal(obj.unit_cost)

    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return LabourOut.model_validate(obj)


@router.delete("/{labour_id}", status_code=204)
async def delete_labour(
    labour_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))
    if obj is None:
        raise NotFoundError("Labour record not found")

    await db.delete(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return None
