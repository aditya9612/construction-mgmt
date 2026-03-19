from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse
from app.schemas.material import MaterialCreate, MaterialOut, MaterialUpdate
from app.services.material_service import MaterialService


router = APIRouter(dependencies=[default_rate_limiter_dependency()])


@router.post("", response_model=MaterialOut)
async def create_material(
    payload: MaterialCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = MaterialService(db, redis)
    return await service.create_material(payload)


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
    service = MaterialService(db, redis)
    return await service.list_materials(
        limit=limit, offset=offset, search=search, status=status, project_id=project_id
    )


@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(
    material_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = MaterialService(db, redis)
    return await service.get_material(material_id)


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
    service = MaterialService(db, redis)
    return await service.update_material(material_id, payload)


@router.delete("/{material_id}", status_code=204)
async def delete_material(
    material_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = MaterialService(db, redis)
    await service.delete_material(material_id)
    return None

