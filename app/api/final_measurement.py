from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.final_measurement import FinalMeasurement
from app.models.project import Project
from app.schemas.final_measurement import (
    FinalMeasurementCreate,
    FinalMeasurementUpdate,
    FinalMeasurementOut,
)
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/measurements", tags=["measurements"])


# -------------------------
# CREATE
# -------------------------
@router.post("", response_model=FinalMeasurementOut)
async def create_measurement(
    payload: FinalMeasurementCreate,
    db: AsyncSession = Depends(get_db_session),
):
    project = await db.get(Project, payload.project_id)
    if not project:
        raise NotFoundError("Project not found")

    total_area = payload.final_area + payload.extra_area
    total_amount = (
        payload.final_area * payload.approved_rate
        + payload.extra_area * payload.extra_rate
    )

    obj = FinalMeasurement(
        **payload.model_dump(),
        total_area=total_area,
        total_amount=total_amount,
    )

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    return FinalMeasurementOut.model_validate(obj)


# -------------------------
# GET BY ID
# -------------------------
@router.get("/{id}", response_model=FinalMeasurementOut)
async def get_measurement(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(FinalMeasurement, id)

    if not obj:
        raise NotFoundError("Measurement not found")

    return FinalMeasurementOut.model_validate(obj)


# -------------------------
# GET BY PROJECT
# -------------------------
@router.get("/project/{project_id}")
async def get_by_project(project_id: int, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        select(FinalMeasurement).where(FinalMeasurement.project_id == project_id)
    )
    rows = result.scalars().all()

    return [FinalMeasurementOut.model_validate(r) for r in rows]


# -------------------------
# UPDATE
# -------------------------
@router.put("/{id}", response_model=FinalMeasurementOut)
async def update_measurement(
    id: int,
    payload: FinalMeasurementUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(FinalMeasurement, id)

    if not obj:
        raise NotFoundError("Measurement not found")

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    # recalculate
    final_area = obj.final_area or 0
    extra_area = obj.extra_area or 0
    approved_rate = obj.approved_rate or 0
    extra_rate = obj.extra_rate or 0

    obj.total_area = final_area + extra_area
    obj.total_amount = (final_area * approved_rate) + (extra_area * extra_rate)

    await db.commit()
    await db.refresh(obj)

    return FinalMeasurementOut.model_validate(obj)