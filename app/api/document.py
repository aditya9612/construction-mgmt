from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.document import Document
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.document import DocumentCreate, DocumentOut, DocumentUpdate
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[default_rate_limiter_dependency()])

VERSION_KEY = "cache_version:documents"


@router.post("", response_model=DocumentOut)
async def create_document(
    payload: DocumentCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = Document(**payload.model_dump(exclude_unset=True))
    db.add(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return DocumentOut.model_validate(obj)


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
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:documents:list:{version}:{limit}:{offset}:{search}:{document_type}:{project_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[DocumentOut].model_validate(cached)

    query = select(Document)
    count_query = select(func.count()).select_from(Document)

    if search:
        like = f"%{search}%"
        query = query.where(Document.title.ilike(like))
        count_query = count_query.where(Document.title.ilike(like))

    if document_type:
        query = query.where(Document.document_type == document_type)
        count_query = count_query.where(Document.document_type == document_type)

    if project_id is not None:
        query = query.where(Document.project_id == project_id)
        count_query = count_query.where(Document.project_id == project_id)

    query = query.order_by(Document.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [DocumentOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
    result = {"items": items, "meta": meta.model_dump()}
    await cache_set_json(redis, cache_key, result)
    return PaginatedResponse[DocumentOut].model_validate(result)


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:documents:get:{version}:{document_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return DocumentOut.model_validate(cached)

    obj = await db.scalar(select(Document).where(Document.id == document_id))
    if obj is None:
        raise NotFoundError("Document not found")

    out = DocumentOut.model_validate(obj)
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


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
    obj = await db.scalar(select(Document).where(Document.id == document_id))
    if obj is None:
        raise NotFoundError("Document not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)

    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return DocumentOut.model_validate(obj)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Document).where(Document.id == document_id))
    if obj is None:
        raise NotFoundError("Document not found")

    await db.delete(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return None
