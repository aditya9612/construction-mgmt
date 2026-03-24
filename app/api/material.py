from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.material import Material
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.material import MaterialCreate, MaterialOut, MaterialUpdate
from app.core.errors import NotFoundError


router = APIRouter(prefix="/materials", tags=["materials"], dependencies=[default_rate_limiter_dependency()])

VERSION_KEY = "cache_version:materials"


@router.post("", response_model=MaterialOut)
async def create_material(
    payload: MaterialCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = Material(**payload.model_dump(exclude_unset=True))
    db.add(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return MaterialOut.model_validate(obj)


@router.get("", response_model=PaginatedResponse[MaterialOut])
async def list_materials(
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
    cache_key = f"cache:materials:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[MaterialOut].model_validate(cached)

    query = select(Material)
    count_query = select(func.count()).select_from(Material)

    if search:
        like = f"%{search}%"
        query = query.where(Material.name.ilike(like))
        count_query = count_query.where(Material.name.ilike(like))

    if status:
        query = query.where(Material.status == status)
        count_query = count_query.where(Material.status == status)

    if project_id is not None:
        query = query.where(Material.project_id == project_id)
        count_query = count_query.where(Material.project_id == project_id)

    query = query.order_by(Material.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [MaterialOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
    result = {"items": items, "meta": meta.model_dump()}
    await cache_set_json(redis, cache_key, result)
    return PaginatedResponse[MaterialOut].model_validate(result)


@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(
    material_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:materials:get:{version}:{material_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return MaterialOut.model_validate(cached)

    obj = await db.scalar(select(Material).where(Material.id == material_id))
    if obj is None:
        raise NotFoundError("Material not found")

    out = MaterialOut.model_validate(obj)
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


@router.put("/{material_id}", response_model=MaterialOut)
async def update_material(
    material_id: int,
    payload: MaterialUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Material).where(Material.id == material_id))
    if obj is None:
        raise NotFoundError("Material not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)

    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return MaterialOut.model_validate(obj)


@router.delete("/{material_id}", status_code=204)
async def delete_material(
    material_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Material).where(Material.id == material_id))
    if obj is None:
        raise NotFoundError("Material not found")

    await db.delete(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return None
