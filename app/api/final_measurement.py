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
from app.utils.helpers import NotFoundError, ValidationError
from app.models.invoice import Invoice
from app.core.logger import logger


router = APIRouter(prefix="/measurements", tags=["measurements"])


@router.post("", response_model=FinalMeasurementOut)
async def create_measurement(
    payload: FinalMeasurementCreate,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Creating final measurement project_id={payload.project_id}")

    project = await db.get(Project, payload.project_id)
    if not project:
        logger.warning(f"Project not found id={payload.project_id}")
        raise NotFoundError("Project not found")

    existing = await db.scalar(
        select(FinalMeasurement).where(
            FinalMeasurement.project_id == payload.project_id
        )
    )
    if existing:
        logger.warning(f"Measurement already exists project_id={payload.project_id}")
        raise ValidationError("Final measurement already exists for this project")

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

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Final measurement creation failed")
        raise

    await db.refresh(obj)

    logger.info(f"Final measurement created id={obj.id}")

    return FinalMeasurementOut.model_validate(obj)


@router.get("/{id}", response_model=FinalMeasurementOut)
async def get_measurement(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(FinalMeasurement, id)

    if not obj:
        raise NotFoundError("Measurement not found")

    return FinalMeasurementOut.model_validate(obj)


@router.get("/project/{project_id}")
async def get_by_project(project_id: int, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        select(FinalMeasurement).where(FinalMeasurement.project_id == project_id)
    )
    rows = result.scalars().all()

    return [FinalMeasurementOut.model_validate(r) for r in rows]


@router.put("/{id}", response_model=FinalMeasurementOut)
async def update_measurement(
    id: int,
    payload: FinalMeasurementUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating measurement id={id}")

    obj = await db.get(FinalMeasurement, id)

    if not obj:
        logger.warning(f"Measurement not found id={id}")
        raise NotFoundError("Measurement not found")

    invoice_exists = await db.scalar(
        select(Invoice).where(Invoice.project_id == obj.project_id)
    )
    if invoice_exists:
        logger.warning(f"Measurement locked (invoice exists) id={id}")
        raise ValidationError("Measurement is locked. Invoice already generated.")

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    final_area = obj.final_area or 0
    extra_area = obj.extra_area or 0
    approved_rate = obj.approved_rate or 0
    extra_rate = obj.extra_rate or 0

    obj.total_area = final_area + extra_area
    obj.total_amount = (final_area * approved_rate) + (extra_area * extra_rate)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Measurement update failed id={id}")
        raise

    await db.refresh(obj)

    logger.info(f"Measurement updated id={id}")

    return FinalMeasurementOut.model_validate(obj)


@router.delete("/{id}", status_code=204)
async def delete_measurement(
    id: int,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Deleting measurement id={id}")

    obj = await db.get(FinalMeasurement, id)

    if not obj:
        logger.warning(f"Measurement not found id={id}")
        raise NotFoundError("Measurement not found")

    invoice_exists = await db.scalar(
        select(Invoice).where(Invoice.project_id == obj.project_id)
    )

    if invoice_exists:
        logger.warning(f"Delete blocked (invoice exists) id={id}")
        raise ValidationError("Cannot delete measurement. Invoice already exists.")

    try:
        await db.delete(obj)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Measurement delete failed id={id}")
        raise

    logger.info(f"Measurement deleted id={id}")

    return None
