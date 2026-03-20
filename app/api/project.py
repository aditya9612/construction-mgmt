from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.project import Project
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[default_rate_limiter_dependency()])

VERSION_KEY = "cache_version:projects"


@router.post("", response_model=ProjectOut)
async def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    data = payload.model_dump(exclude_unset=True)
    obj = Project(**data)
    db.add(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return ProjectOut.model_validate(obj)


@router.get("", response_model=PaginatedResponse[ProjectOut])
async def list_projects(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:projects:list:{version}:{limit}:{offset}:{search}:{status}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[ProjectOut].model_validate(cached)

    query = select(Project)
    count_query = select(func.count()).select_from(Project)

    if search:
        like = f"%{search}%"
        query = query.where(Project.name.ilike(like))
        count_query = count_query.where(Project.name.ilike(like))

    if status:
        query = query.where(Project.status == status)
        count_query = count_query.where(Project.status == status)

    query = query.order_by(Project.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [ProjectOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
    result = {"items": items, "meta": meta.model_dump()}
    await cache_set_json(redis, cache_key, result)
    return PaginatedResponse[ProjectOut].model_validate(result)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:projects:get:{version}:{project_id}"
    cached_json = await cache_get_json(redis, cache_key)
    if cached_json is not None:
        return ProjectOut.model_validate(cached_json)

    obj = await db.scalar(select(Project).where(Project.id == project_id))
    if obj is None:
        raise NotFoundError("Project not found")

    out = ProjectOut.model_validate(obj)
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Project).where(Project.id == project_id))
    if obj is None:
        raise NotFoundError("Project not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)

    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return ProjectOut.model_validate(obj)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Project).where(Project.id == project_id))
    if obj is None:
        raise NotFoundError("Project not found")

    await db.delete(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return None
