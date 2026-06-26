from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.final_measurement import FinalMeasurement
from app.models.project import Project
from app.models.boq import BOQ
from app.models.user import User
from app.schemas.final_measurement import (
    FinalMeasurementCreate,
    FinalMeasurementUpdate,
    FinalMeasurementOut,
)
from app.utils.helpers import NotFoundError, ValidationError
from app.models.invoice import Invoice
from app.core.logger import logger
from app.core import dependencies as d
from app.models.user import User, UserRole

MEASUREMENT_READ_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.ACCOUNTANT,
        UserRole.CLIENT,
    ]
]

MEASUREMENT_WRITE_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
    ]
]

router = APIRouter(prefix="/measurements", tags=["measurements"])


@router.post("", response_model=FinalMeasurementOut)
async def create_measurement(
    payload: FinalMeasurementCreate,
    current_user: User = Depends(d.require_roles(MEASUREMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Creating final measurement project_id={payload.project_id}")

    project = await db.get(Project, payload.project_id)
    if not project:
        logger.warning(f"Project not found id={payload.project_id}")
        raise NotFoundError("Project not found")

    # Add safeguard validation if boq_item_id is provided
    if payload.boq_item_id:
        boq_item = await db.get(BOQ, payload.boq_item_id)
        if not boq_item:
            raise ValidationError("BOQ Item not found")
            
        existing_qty = await db.scalar(
            select(func.sum(FinalMeasurement.measured_qty)).where(
                FinalMeasurement.boq_item_id == payload.boq_item_id,
                FinalMeasurement.status != 'REJECTED'
            )
        )
        existing_qty = float(existing_qty or 0)
        
        # We need to make sure measured_qty is safe to evaluate
        if hasattr(payload, 'measured_qty') and payload.measured_qty:
            if existing_qty + payload.measured_qty > float(boq_item.quantity):
                raise ValidationError(f"Measurement exceeds BOQ quantity. Available: {float(boq_item.quantity) - existing_qty}")


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
async def get_measurement(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(d.require_roles(MEASUREMENT_READ_ROLES)),
):
    obj = await db.get(FinalMeasurement, id)

    if not obj:
        raise NotFoundError("Measurement not found")

    return FinalMeasurementOut.model_validate(obj)


@router.get("/project/{project_id}")
async def get_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(d.require_roles(MEASUREMENT_READ_ROLES)),
):
    result = await db.execute(
        select(FinalMeasurement).where(FinalMeasurement.project_id == project_id)
    )
    rows = result.scalars().all()

    return [FinalMeasurementOut.model_validate(r) for r in rows]


@router.put("/{id}", response_model=FinalMeasurementOut)
async def update_measurement(
    id: int,
    payload: FinalMeasurementUpdate,
    current_user: User = Depends(d.require_roles(MEASUREMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating measurement id={id}")

    obj = await db.get(FinalMeasurement, id)

    if not obj:
        logger.warning(f"Measurement not found id={id}")
        raise NotFoundError("Measurement not found")

    if obj.status not in ["DRAFT", "REJECTED"]:
        raise ValidationError("Cannot modify a measurement once it has been submitted for approval.")

    invoice_exists = await db.scalar(
        select(Invoice).where(
            Invoice.source_type == InvoiceSourceType.MEASUREMENT,
            Invoice.reference_id == obj.id
        )
    )
    if invoice_exists:
        logger.warning(f"Measurement locked (invoice exists) id={id}")
        raise ValidationError("Measurement is locked. Invoice already generated.")

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        if v is not None:
            setattr(obj, k, v)

    final_area = Decimal(str(obj.final_area or 0))
    extra_area = Decimal(obj.extra_area or 0)
    approved_rate = Decimal(obj.approved_rate or 0)
    extra_rate = Decimal(obj.extra_rate or 0)

    obj.total_area = final_area + extra_area
    obj.total_amount = (final_area * approved_rate) + (extra_area * extra_rate)

    try:
        await db.commit()

    except Exception as e:
        await db.rollback()

        # Full stack trace in server logs
        logger.exception(
            f"Measurement update failed id={id}. Error: {repr(e)}"
        )

        # Return actual database error in API response for debugging
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    await db.refresh(obj)

    logger.info(f"Measurement updated id={id}")

    return FinalMeasurementOut.model_validate(obj)


@router.delete("/{id}", status_code=204)
async def delete_measurement(
    id: int,
    current_user: User = Depends(d.require_roles(MEASUREMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Deleting measurement id={id}")

    obj = await db.get(FinalMeasurement, id)

    if not obj:
        logger.warning(f"Measurement not found id={id}")
        raise NotFoundError("Measurement not found")

    if obj.status not in ["DRAFT", "REJECTED"]:
        raise ValidationError("Cannot modify a measurement once it has been submitted for approval.")

    invoice_exists = await db.scalar(
        select(Invoice).where(
            Invoice.source_type == InvoiceSourceType.MEASUREMENT,
            Invoice.reference_id == obj.id
        )
    )

    if invoice_exists:
        logger.warning(f"Delete blocked (invoice exists) id={id}")
        raise ValidationError("Cannot delete measurement. Invoice already exists.")

    try:
        await db.delete(obj)
        await db.commit()

    except Exception as e:
        await db.rollback()

        # Full stack trace in server logs
        logger.exception(
            f"Measurement delete failed id={id}. Error: {repr(e)}"
        )

        # Return actual database error in API response for debugging
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    logger.info(f"Measurement deleted id={id}")

    return None


from pydantic import BaseModel

class MeasurementStatusUpdate(BaseModel):
    status: str

@router.put("/{id}/status", response_model=FinalMeasurementOut)
async def update_measurement_status(
    id: int,
    payload: MeasurementStatusUpdate,
    current_user: User = Depends(d.require_roles(MEASUREMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating measurement status id={id} to {payload.status}")

    obj = await db.get(FinalMeasurement, id)

    if not obj:
        raise NotFoundError("Measurement not found")

    valid_statuses = ["DRAFT", "SUBMITTED", "VERIFIED", "APPROVED", "REJECTED", "BILLED"]
    if payload.status not in valid_statuses:
        raise ValidationError(f"Invalid status. Must be one of: {valid_statuses}")

    if payload.status in ["APPROVED", "REJECTED"]:
        raise ValidationError("Cannot manually set status to APPROVED or REJECTED. Must use the central Approvals API.")

    obj.status = payload.status

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    await db.refresh(obj)
    return FinalMeasurementOut.model_validate(obj)

