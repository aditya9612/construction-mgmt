from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.dsr import DailySiteReport
from app.models.project import Project
from app.schemas.dsr import DSRCreate, DSRUpdate, DSROut
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/dsr", tags=["DSR"])


# -------------------------
# CREATE (1 per day rule)
# -------------------------
@router.post("", response_model=DSROut)
async def create_dsr(
    payload: DSRCreate,
    db: AsyncSession = Depends(get_db_session),
):
    project = await db.get(Project, payload.project_id)
    if not project:
        raise NotFoundError("Project not found")

    # ❗ Prevent duplicate DSR per day
    existing = await db.scalar(
        select(DailySiteReport).where(
            DailySiteReport.project_id == payload.project_id,
            DailySiteReport.report_date == payload.report_date,
        )
    )

    if existing:
        raise ValueError("DSR already exists for this date")

    obj = DailySiteReport(**payload.model_dump())

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    return DSROut.model_validate(obj)



@router.get("/project/{project_id}")
async def get_project_dsr(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(DailySiteReport).where(
            DailySiteReport.project_id == project_id
        )
    )
    rows = result.scalars().all()

    return [DSROut.model_validate(r) for r in rows]



@router.get("/{id}", response_model=DSROut)
async def get_dsr(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(DailySiteReport, id)

    if not obj:
        raise NotFoundError("DSR not found")

    return DSROut.model_validate(obj)



@router.put("/{id}", response_model=DSROut)
async def update_dsr(
    id: int,
    payload: DSRUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(DailySiteReport, id)

    if not obj:
        raise NotFoundError("DSR not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    await db.commit()
    await db.refresh(obj)

    return DSROut.model_validate(obj)


# -------------------------
# DELETE
# -------------------------
@router.delete("/{id}", status_code=204)
async def delete_dsr(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(DailySiteReport, id)

    if not obj:
        raise NotFoundError("DSR not found")

    await db.delete(obj)
    await db.commit()

    return None