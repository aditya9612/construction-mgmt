from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse
from app.schemas.labour import LabourCreate, LabourOut, LabourUpdate
from app.services.labour_service import LabourService


router = APIRouter(dependencies=[default_rate_limiter_dependency()])


@router.post("", response_model=LabourOut)
async def create_labour(
    payload: LabourCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = LabourService(db, redis)
    return await service.create_labour(payload)


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
    service = LabourService(db, redis)
    return await service.list_labour(
        limit=limit, offset=offset, search=search, status=status, project_id=project_id
    )


@router.get("/{labour_id}", response_model=LabourOut)
async def get_labour(
    labour_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = LabourService(db, redis)
    return await service.get_labour(labour_id)


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
    service = LabourService(db, redis)
    return await service.update_labour(labour_id, payload)


@router.delete("/{labour_id}", status_code=204)
async def delete_labour(
    labour_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = LabourService(db, redis)
    await service.delete_labour(labour_id)
    return None

