from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse
from app.schemas.document import DocumentCreate, DocumentOut, DocumentUpdate
from app.services.document_service import DocumentService


router = APIRouter(dependencies=[default_rate_limiter_dependency()])


@router.post("", response_model=DocumentOut)
async def create_document(
    payload: DocumentCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = DocumentService(db, redis)
    return await service.create_document(payload)


@router.get("", response_model=PaginatedResponse[DocumentOut])
async def list_documents(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    document_type: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = DocumentService(db, redis)
    return await service.list_documents(
        limit=limit,
        offset=offset,
        search=search,
        document_type=document_type,
        project_id=project_id,
    )


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = DocumentService(db, redis)
    return await service.get_document(document_id)


@router.put("/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: int,
    payload: DocumentUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = DocumentService(db, redis)
    return await service.update_document(document_id, payload)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = DocumentService(db, redis)
    await service.delete_document(document_id)
    return None

