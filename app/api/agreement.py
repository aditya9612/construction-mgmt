from fastapi import APIRouter, Depends, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import os
import uuid
from datetime import datetime
from typing import List, Optional

from app.db.session import get_db_session
from app.models.agreement import Agreement
from app.models.project import Project
from app.models.owner import Owner
from app.schemas.agreement import AgreementCreate, AgreementOut, AgreementStats
from app.utils.helpers import NotFoundError
from app.core import dependencies as d

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
    db: AsyncSession = Depends(get_db_session),
):
    query = (
        select(Agreement, Project.project_name, Owner.owner_name)
        .join(Project, Agreement.project_id == Project.id, isouter=True)
        .join(Owner, Agreement.owner_id == Owner.id)
    )

    if search:
        query = query.where(Agreement.document_id.ilike(f"%{search}%"))
    if owner_id:
        query = query.where(Agreement.owner_id == owner_id)
    if project_id:
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
    db: AsyncSession = Depends(get_db_session),
):
    # 1. Generate Unique ID
    doc_id = f"AGR-{uuid.uuid4().hex[:4].upper()}"

    # 2. Save File
    file_ext = os.path.splitext(file.filename)[1]
    file_name = f"{doc_id}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, file_name)

    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    file_url = f"/uploads/agreements/{file_name}"

    # 3. Create DB Record
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

    return agreement


@router.get("/stats", response_model=AgreementStats)
async def get_agreement_stats(db: AsyncSession = Depends(get_db_session)):
    today = datetime.utcnow()
    first_of_month = datetime(today.year, today.month, 1)

    total = await db.scalar(select(func.count(Agreement.id)))
    active = await db.scalar(
        select(func.count(Agreement.id)).where(Agreement.status == "Active")
    )
    recent = await db.scalar(
        select(func.count(Agreement.id)).where(Agreement.uploaded_at >= first_of_month)
    )

    # 1. Real Storage Calculation
    total_size = 0
    if os.path.exists(UPLOAD_DIR):
        for f in os.listdir(UPLOAD_DIR):
            fp = os.path.join(UPLOAD_DIR, f)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)
    
    storage_str = f"{round(total_size / (1024 * 1024), 2)} MB"

    # 2. Real Missing Documents Calculation (Owners without any agreement)
    owners_count = await db.scalar(select(func.count(Owner.id)))
    owners_with_aggr = await db.scalar(
        select(func.count(func.distinct(Agreement.owner_id)))
    )
    missing = max(0, (owners_count or 0) - (owners_with_aggr or 0))

    return {
        "total_agreements": total or 0,
        "active_contracts": active or 0,
        "storage_used": storage_str,
        "missing_docs": missing,
        "recent_uploads": recent or 0,
    }
