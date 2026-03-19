from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse
from app.schemas.equipment import EquipmentCreate, EquipmentOut, EquipmentUpdate
from app.services.equipment_service import EquipmentService


router = APIRouter(dependencies=[default_rate_limiter_dependency()])


@router.post("", response_model=EquipmentOut)
async def create_equipment(
    payload: EquipmentCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = EquipmentService(db, redis)
    return await service.create_equipment(payload)


@router.get("", response_model=PaginatedResponse[EquipmentOut])
async def list_equipment(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = EquipmentService(db, redis)
    return await service.list_equipment(
        limit=limit, offset=offset, search=search, status=status, project_id=project_id
    )


@router.get("/{equipment_id}", response_model=EquipmentOut)
async def get_equipment(
    equipment_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = EquipmentService(db, redis)
    return await service.get_equipment(equipment_id)


@router.put("/{equipment_id}", response_model=EquipmentOut)
async def update_equipment(
    equipment_id: int,
    payload: EquipmentUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = EquipmentService(db, redis)
    return await service.update_equipment(equipment_id, payload)


@router.delete("/{equipment_id}", status_code=204)
async def delete_equipment(
    equipment_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = EquipmentService(db, redis)
    await service.delete_equipment(equipment_id)
    return None

