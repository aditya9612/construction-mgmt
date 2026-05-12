from typing import Optional, List
import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, File, UploadFile, Form
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse, FileResponse

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.document import Document
from app.models.project import Project
from app.models.user import User, UserRole
from app.core.enums import DocumentStatus
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.document import DocumentCreate, DocumentOut, DocumentUpdate, DocumentStats
from app.utils.helpers import NotFoundError

DOCUMENT_WRITE_ROLES = [r.value for r in [
    UserRole.ADMIN,
    UserRole.PROJECT_MANAGER,
    UserRole.SITE_ENGINEER,
]]

DOCUMENT_DELETE_ROLES = [r.value for r in [
    UserRole.ADMIN,
    UserRole.PROJECT_MANAGER,
]]

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[default_rate_limiter_dependency()])

VERSION_KEY = "cache_version:documents"
UPLOAD_DIR = Path("uploads/documents")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/stats", response_model=DocumentStats)
async def get_document_stats(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    """
    Returns statistics for the document repository.
    """
    total_size = await db.scalar(select(func.sum(Document.file_size))) or 0
    pending_count = await db.scalar(
        select(func.count(Document.id)).where(Document.status == DocumentStatus.PENDING)
    )
    total_docs = await db.scalar(
        select(func.count(Document.id)).where(Document.is_folder == False)
    )

    return DocumentStats(
        total_storage_bytes=int(total_size),
        total_storage_gb=round(float(total_size) / (1024**3), 2),
        pending_approvals=int(pending_count),
        total_documents=int(total_docs),
    )


@router.post("/upload", response_model=DocumentOut)
async def upload_document(
    project_id: int = Form(...),
    title: Optional[str] = Form(None),
    document_type: Optional[str] = Form("Other"),
    parent_id: Optional[int] = Form(None),
    remarks: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(DOCUMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """
    Uploads a physical file and creates a document record.
    """
    file_extension = Path(file.filename).suffix
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = UPLOAD_DIR / unique_filename

    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    file_size = os.path.getsize(file_path)

    doc_title = title or file.filename

    obj = Document(
        project_id=project_id,
        title=doc_title,
        document_type=document_type,
        file_url=str(file_path),
        file_size=file_size,
        parent_id=parent_id,
        remarks=remarks,
        uploaded_by_user_id=current_user.id,
        is_folder=False,
        status=DocumentStatus.PENDING
    )

    db.add(obj)
    await db.commit()          # Save to database
    await db.refresh(obj)      # Load uploaded_at, created_at, updated_at, id

    await bump_cache_version(redis, VERSION_KEY)

    # Load project name
    project_name = await db.scalar(
        select(Project.project_name).where(Project.id == obj.project_id)
    )

    out = DocumentOut.model_validate(obj)
    out.project_name = project_name

    return out


@router.post("/folders", response_model=DocumentOut)
async def create_folder(
    project_id: int,
    title: str,
    parent_id: Optional[int] = None,
    current_user: User = Depends(require_roles(DOCUMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """
    Creates a new folder in the document repository.
    """
    obj = Document(
        project_id=project_id,
        title=title,
        is_folder=True,
        parent_id=parent_id,
        uploaded_by_user_id=current_user.id,
        status=DocumentStatus.APPROVED  # Folders don't need approval
    )

    db.add(obj)
    await db.commit()          # Save to database
    await db.refresh(obj)      # Load uploaded_at, created_at, updated_at

    await bump_cache_version(redis, VERSION_KEY)

    project_name = await db.scalar(
        select(Project.project_name).where(Project.id == obj.project_id)
    )

    out = DocumentOut.model_validate(obj)
    out.project_name = project_name

    return out

@router.post("", response_model=DocumentOut)
async def create_document(
    payload: DocumentCreate,
    current_user: User = Depends(require_roles(DOCUMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """
    Creates a document record (metadata only).
    """
    # Ignore uploaded_by_user_id from request and always use logged-in user
    data = payload.model_dump(exclude_unset=True)
    data["uploaded_by_user_id"] = current_user.id

    # If status is not provided, default to PENDING
    if "status" not in data:
        data["status"] = DocumentStatus.PENDING

    obj = Document(**data)

    db.add(obj)
    await db.commit()          # Save to database
    await db.refresh(obj)      # Load uploaded_at, created_at, updated_at, id

    await bump_cache_version(redis, VERSION_KEY)

    # Load project name
    project_name = await db.scalar(
        select(Project.project_name).where(Project.id == obj.project_id)
    )

    out = DocumentOut.model_validate(obj)
    out.project_name = project_name

    return out


@router.get("", response_model=PaginatedResponse[DocumentOut])
async def list_documents(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    document_type: Optional[str] = None,
    project_id: Optional[int] = None,
    parent_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:documents:list:{version}:{limit}:{offset}:{search}:{document_type}:{project_id}:{parent_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[DocumentOut].model_validate(cached)

    # Join with Project to get project_name
    query = select(Document, Project.project_name).outerjoin(Project, Document.project_id == Project.id)
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

    if parent_id is not None:
        query = query.where(Document.parent_id == parent_id)
        count_query = count_query.where(Document.parent_id == parent_id)
    else:
        # Default to root documents if no parent_id specified (optional logic)
        # query = query.where(Document.parent_id == None)
        pass

    query = query.order_by(Document.is_folder.desc(), Document.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    result = await db.execute(query)
    
    items = []
    for doc, proj_name in result.all():
        out = DocumentOut.model_validate(doc)
        out.project_name = proj_name
        items.append(out.model_dump())

    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
    res = {"items": items, "meta": meta.model_dump()}
    await cache_set_json(redis, cache_key, res)
    return PaginatedResponse[DocumentOut].model_validate(res)


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

    result = await db.execute(
        select(Document, Project.project_name)
        .outerjoin(Project, Document.project_id == Project.id)
        .where(Document.id == document_id)
    )
    row = result.first()
    if row is None:
        raise NotFoundError("Document not found")

    doc, proj_name = row
    out = DocumentOut.model_validate(doc)
    out.project_name = proj_name
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


@router.put("/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: int,
    payload: DocumentUpdate,
    current_user: User = Depends(require_roles(DOCUMENT_WRITE_ROLES)),
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
    
    # Reload with project name
    project_name = await db.scalar(select(Project.project_name).where(Project.id == obj.project_id))
    out = DocumentOut.model_validate(obj)
    out.project_name = project_name
    return out


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    current_user: User = Depends(require_roles(DOCUMENT_DELETE_ROLES)),
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


@router.get("/{document_id}/download")
async def download_document(
    document_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    doc = await db.get(Document, document_id)
    if not doc or not doc.file_url:
        raise NotFoundError("Document or file not found")
    
    # If file_url is a local path
    if os.path.exists(doc.file_url):
        return FileResponse(doc.file_url, filename=doc.title)
    
    # Else, maybe redirect or handle as URL (basic implementation)
    return {"file_url": doc.file_url}
