from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException, status
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import os
import uuid
from datetime import datetime
from typing import List, Optional

from app.models.user import User
from app.db.session import get_db_session
from app.models.agreement import Agreement
from app.models.project import Project
from app.models.owner import Owner
from app.schemas.agreement import AgreementCreate, AgreementOut, AgreementStats
from app.utils.helpers import NotFoundError
from app.core.dependencies import require_permission
from app.core.logger import logger

router = APIRouter(prefix="/agreements", tags=["Agreements"])

UPLOAD_DIR = "uploads/agreements"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.get("/", response_model=List[AgreementOut])
async def list_agreements(
    search: Optional[str] = Query(None),
    owner_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_permission("agreements.view")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to any company",
        )

    # Validate owner_id if supplied
    if owner_id is not None:
        owner_stmt = select(Owner).where(Owner.id == owner_id)
        if not is_sa:
            owner_stmt = owner_stmt.where(Owner.company_id == current_user.company_id)
        owner_obj = await db.scalar(owner_stmt)
        if not owner_obj:
            raise NotFoundError("Owner not found")

    # Validate project_id if supplied
    if project_id is not None:
        proj_stmt = select(Project).where(Project.id == project_id)
        if not is_sa:
            proj_stmt = proj_stmt.where(Project.company_id == current_user.company_id)
        proj_obj = await db.scalar(proj_stmt)
        if not proj_obj:
            raise NotFoundError("Project not found")

    query = (
        select(Agreement, Project.project_name, Owner.owner_name)
        .join(Owner, Agreement.owner_id == Owner.id)
        .join(Project, Agreement.project_id == Project.id, isouter=True)
    )

    if not is_sa:
        query = query.where(Owner.company_id == current_user.company_id)

    if search:
        query = query.where(Agreement.document_id.ilike(f"%{search}%"))
    if owner_id is not None:
        query = query.where(Agreement.owner_id == owner_id)
    if project_id is not None:
        query = query.where(Agreement.project_id == project_id)

    offset = (page - 1) * limit
    query = query.offset(offset).limit(limit).order_by(Agreement.id.desc())

    result = await db.execute(query)
    agreements = []
    for row in result.all():
        aggr, p_name, o_name = row
        out = AgreementOut.from_orm(aggr)
        out.project_name = p_name
        out.owner_name = o_name
        agreements.append(out)

    return agreements


@router.post("/", response_model=AgreementOut)
async def upload_agreement(
    owner_id: int = Form(...),
    type: str = Form(...),
    project_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("agreements.create")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to any company",
        )

    # 1. Validate Owner exists and belongs to caller company
    owner_stmt = select(Owner).where(Owner.id == owner_id)
    if not is_sa:
        owner_stmt = owner_stmt.where(Owner.company_id == current_user.company_id)
    owner = await db.scalar(owner_stmt)
    if not owner:
        raise NotFoundError("Owner not found")

    # 2. Validate Project exists, belongs to caller company, and matches owner_id
    project = None
    if project_id is not None:
        proj_stmt = select(Project).where(Project.id == project_id)
        if not is_sa:
            proj_stmt = proj_stmt.where(Project.company_id == current_user.company_id)
        project = await db.scalar(proj_stmt)
        if not project:
            raise NotFoundError("Project not found")
        if project.owner_id != owner_id:
            raise NotFoundError("Project not found")

    # 3. File validation & path traversal prevention
    original_filename = os.path.basename(file.filename or "agreement.pdf")
    file_ext = os.path.splitext(original_filename)[1]
    if not file_ext:
        file_ext = ".pdf"

    # Generate Unique ID
    doc_id = f"AGR-{uuid.uuid4().hex[:4].upper()}"
    file_name = f"{doc_id}{file_ext}"

    upload_dir_abs = os.path.abspath(UPLOAD_DIR)
    file_path = os.path.abspath(os.path.join(UPLOAD_DIR, file_name))
    if not file_path.startswith(upload_dir_abs):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path",
        )

    try:
        def _save_agreement():
            with open(file_path, "wb") as buffer:
                buffer.write(file.file.read())

        await run_in_threadpool(_save_agreement)

        file_url = f"/uploads/agreements/{file_name}"

        # 4. Create DB Record
        agreement = Agreement(
            document_id=doc_id,
            owner_id=owner_id,
            project_id=project_id,
            type=type,
            file_url=file_url,
            status="Active",
        )

        db.add(agreement)
        await db.commit()
        await db.refresh(agreement)
    except Exception:
        logger.exception("Failed to upload agreement")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while uploading agreement",
        )

    out = AgreementOut.from_orm(agreement)
    out.owner_name = owner.owner_name
    if project:
        out.project_name = project.project_name

    return out


@router.get("/stats", response_model=AgreementStats)
async def get_agreement_stats(
    current_user: User = Depends(require_permission("agreements.view")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to any company",
        )

    today = datetime.utcnow()
    first_of_month = datetime(today.year, today.month, 1)

    total_stmt = (
        select(func.count(Agreement.id))
        .join(Owner, Agreement.owner_id == Owner.id)
    )
    active_stmt = (
        select(func.count(Agreement.id))
        .join(Owner, Agreement.owner_id == Owner.id)
        .where(Agreement.status == "Active")
    )
    recent_stmt = (
        select(func.count(Agreement.id))
        .join(Owner, Agreement.owner_id == Owner.id)
        .where(Agreement.uploaded_at >= first_of_month)
    )
    owners_stmt = select(func.count(Owner.id))
    owners_with_aggr_stmt = (
        select(func.count(func.distinct(Agreement.owner_id)))
        .join(Owner, Agreement.owner_id == Owner.id)
    )
    file_urls_stmt = (
        select(Agreement.file_url)
        .join(Owner, Agreement.owner_id == Owner.id)
    )

    if not is_sa:
        total_stmt = total_stmt.where(Owner.company_id == current_user.company_id)
        active_stmt = active_stmt.where(Owner.company_id == current_user.company_id)
        recent_stmt = recent_stmt.where(Owner.company_id == current_user.company_id)
        owners_stmt = owners_stmt.where(Owner.company_id == current_user.company_id)
        owners_with_aggr_stmt = owners_with_aggr_stmt.where(Owner.company_id == current_user.company_id)
        file_urls_stmt = file_urls_stmt.where(Owner.company_id == current_user.company_id)

    total = await db.scalar(total_stmt)
    active = await db.scalar(active_stmt)
    recent = await db.scalar(recent_stmt)
    owners_count = await db.scalar(owners_stmt)
    owners_with_aggr = await db.scalar(owners_with_aggr_stmt)
    missing = max(0, (owners_count or 0) - (owners_with_aggr or 0))

    # Calculate real storage scoped to caller's agreements
    file_urls = (await db.execute(file_urls_stmt)).scalars().all()
    total_size = 0
    for fu in file_urls:
        if fu:
            fname = os.path.basename(fu)
            fp = os.path.join(UPLOAD_DIR, fname)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)

    storage_str = f"{round(total_size / (1024 * 1024), 2)} MB"

    return {
        "total_agreements": total or 0,
        "active_contracts": active or 0,
        "storage_used": storage_str,
        "missing_docs": missing,
        "recent_uploads": recent or 0,
    }


@router.get("/{agreement_id}/download")
async def download_agreement(
    agreement_id: int,
    current_user: User = Depends(require_permission("agreements.download")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to any company",
        )

    stmt = (
        select(Agreement)
        .join(Owner, Agreement.owner_id == Owner.id)
        .where(Agreement.id == agreement_id)
    )
    if not is_sa:
        stmt = stmt.where(Owner.company_id == current_user.company_id)

    agreement = await db.scalar(stmt)
    if not agreement:
        raise NotFoundError("Agreement not found")

    file_name = os.path.basename(agreement.file_url)
    upload_dir_abs = os.path.abspath(UPLOAD_DIR)
    actual_path = os.path.abspath(os.path.join(UPLOAD_DIR, file_name))

    if not actual_path.startswith(upload_dir_abs) or not os.path.exists(actual_path):
        raise NotFoundError("Agreement file not found on disk")

    return FileResponse(
        path=actual_path,
        filename=file_name,
        media_type="application/octet-stream",
    )
