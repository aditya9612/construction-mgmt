from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse
from app.schemas.boq import BOQCreate, BOQOut, BOQUpdate
from app.services.boq_service import BOQService


router = APIRouter(dependencies=[default_rate_limiter_dependency()])


@router.post("", response_model=BOQOut)
async def create_boq(
    payload: BOQCreate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = BOQService(db, redis)
    return await service.create_boq(payload)


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
    service = BOQService(db, redis)
    return await service.list_boq(limit=limit, offset=offset, search=search, status=status, project_id=project_id)


@router.get("/{boq_id}", response_model=BOQOut)
async def get_boq(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = BOQService(db, redis)
    return await service.get_boq(boq_id)


@router.put("/{boq_id}", response_model=BOQOut)
async def update_boq(
    boq_id: int,
    payload: BOQUpdate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = BOQService(db, redis)
    return await service.update_boq(boq_id, payload)


@router.delete("/{boq_id}", status_code=204)
async def delete_boq(
    boq_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = BOQService(db, redis)
    await service.delete_boq(boq_id)
    return None

