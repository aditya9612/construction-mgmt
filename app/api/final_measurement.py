from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import InvoiceSourceType, MeasurementStatus
from app.core.dependencies import require_permission, get_effective_user_permissions, has_permission
from app.db.session import get_db_session
from app.models.final_measurement import FinalMeasurement
from app.models.project import Project
from app.models.boq import BOQ
from app.models.invoice import Invoice
from app.models.approval import Approval
from app.models.user import User
from app.schemas.final_measurement import (
    FinalMeasurementCreate,
    FinalMeasurementUpdate,
    FinalMeasurementOut,
    MeasurementDeleteOut,
)
from app.utils.helpers import NotFoundError, ValidationError
from app.core.logger import logger

router = APIRouter(prefix="/measurements", tags=["measurements"])


class MeasurementStatusUpdate(BaseModel):
    status: str


async def _get_scoped_measurement(
    db: AsyncSession,
    measurement_id: int,
    current_user: User,
    for_update: bool = False,
) -> FinalMeasurement:
    """Retrieve FinalMeasurement enforcing tenant boundary isolation through Project.company_id."""
    is_sa = getattr(current_user, "is_super_admin", False) is True

    query = (
        select(FinalMeasurement)
        .join(Project, FinalMeasurement.project_id == Project.id)
        .where(FinalMeasurement.id == measurement_id)
    )

    if not is_sa:
        if current_user.company_id is None:
            raise NotFoundError("Measurement not found")
        query = query.where(Project.company_id == current_user.company_id)

    if for_update:
        query = query.with_for_update()

    obj = await db.scalar(query)
    if not obj:
        raise NotFoundError("Measurement not found")

    return obj


@router.post("", response_model=FinalMeasurementOut)
async def create_measurement(
    payload: FinalMeasurementCreate,
    current_user: User = Depends(require_permission("measurements.create")),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Creating final measurement project_id={payload.project_id}")

    is_sa = getattr(current_user, "is_super_admin", False) is True

    # Validate requested Project exists and belongs to current tenant
    project_query = select(Project).where(Project.id == payload.project_id)
    if not is_sa:
        if current_user.company_id is None:
            raise NotFoundError("Project not found")
        project_query = project_query.where(Project.company_id == current_user.company_id)

    project = await db.scalar(project_query)
    if not project:
        logger.warning(f"Project not found id={payload.project_id}")
        raise NotFoundError("Project not found")

    # Add safeguard validation if boq_item_id is provided
    if payload.boq_item_id:
        boq_query = select(BOQ).where(
            BOQ.id == payload.boq_item_id,
            BOQ.project_id == payload.project_id,
        )
        boq_item = await db.scalar(boq_query)
        if not boq_item:
            raise NotFoundError("BOQ Item not found")

        existing_qty = await db.scalar(
            select(func.sum(FinalMeasurement.measured_qty)).where(
                FinalMeasurement.boq_item_id == payload.boq_item_id,
                FinalMeasurement.status != "REJECTED",
            )
        )
        existing_qty = float(existing_qty or 0)

        # Ensure measured_qty is safe to evaluate
        if hasattr(payload, "measured_qty") and payload.measured_qty:
            if existing_qty + payload.measured_qty > float(boq_item.quantity):
                raise ValidationError(
                    f"Measurement exceeds BOQ quantity. Available: {float(boq_item.quantity) - existing_qty}"
                )

    total_area = payload.final_area + payload.extra_area
    total_amount = (
        payload.final_area * payload.approved_rate
        + payload.extra_area * payload.extra_rate
    )

    create_data = payload.model_dump()
    create_data.pop("status", None)
    obj = FinalMeasurement(
        **create_data,
        status=MeasurementStatus.DRAFT.value,
        total_area=total_area,
        total_amount=total_amount,
    )

    db.add(obj)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Final measurement creation failed")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while creating measurement",
        )

    await db.refresh(obj)

    logger.info(f"Final measurement created id={obj.id}")

    return FinalMeasurementOut.model_validate(obj)


@router.get("/project/{project_id}", response_model=List[FinalMeasurementOut])
async def get_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("measurements.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True

    # Validate Project ownership
    project_query = select(Project.id).where(Project.id == project_id)
    if not is_sa:
        if current_user.company_id is None:
            raise NotFoundError("Project not found")
        project_query = project_query.where(Project.company_id == current_user.company_id)

    proj_exists = await db.scalar(project_query)
    if not proj_exists:
        raise NotFoundError("Project not found")

    result = await db.execute(
        select(FinalMeasurement).where(FinalMeasurement.project_id == project_id)
    )
    rows = result.scalars().all()

    return [FinalMeasurementOut.model_validate(r) for r in rows]


@router.get("/{id}", response_model=FinalMeasurementOut)
async def get_measurement(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("measurements.view")),
):
    obj = await _get_scoped_measurement(db, id, current_user)
    return FinalMeasurementOut.model_validate(obj)


@router.put("/{id}", response_model=FinalMeasurementOut)
async def update_measurement(
    id: int,
    payload: FinalMeasurementUpdate,
    current_user: User = Depends(require_permission("measurements.edit")),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating measurement id={id}")

    obj = await _get_scoped_measurement(db, id, current_user, for_update=True)

    if obj.status not in ["DRAFT", "REJECTED"]:
        raise ValidationError(
            "Cannot modify a measurement once it has been submitted for approval."
        )

    try:
        result = await db.execute(
            select(Invoice.id).where(
                Invoice.source_type == InvoiceSourceType.MEASUREMENT,
                Invoice.reference_id == obj.id,
            )
        )
        invoice_exists = result.scalar_one_or_none()
    except Exception:
        logger.exception(f"Invoice check failed measurement={id}")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while checking invoice status",
        )

    if invoice_exists:
        logger.warning(f"Measurement locked (invoice exists) id={id}")
        raise ValidationError("Measurement is locked. Invoice already generated.")

    if payload.status is not None:
        raise ValidationError("Status cannot be modified via general measurement update. Use dedicated workflow endpoints.")
    data = payload.model_dump(exclude_unset=True)
    data.pop("status", None)

    # If boq_item_id is updated, validate it belongs to the same project
    if "boq_item_id" in data and data["boq_item_id"] is not None:
        boq_query = select(BOQ).where(
            BOQ.id == data["boq_item_id"],
            BOQ.project_id == obj.project_id,
        )
        boq_item = await db.scalar(boq_query)
        if not boq_item:
            raise NotFoundError("BOQ Item not found")

    for k, v in data.items():
        if v is not None:
            setattr(obj, k, v)

    final_area = Decimal(str(obj.final_area or 0))
    extra_area = Decimal(str(obj.extra_area or 0))
    approved_rate = Decimal(str(obj.approved_rate or 0))
    extra_rate = Decimal(str(obj.extra_rate or 0))

    obj.total_area = final_area + extra_area
    obj.total_amount = (final_area * approved_rate) + (extra_area * extra_rate)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Measurement update failed id={id}")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while updating measurement",
        )

    await db.refresh(obj)

    logger.info(f"Measurement updated id={id}")

    return FinalMeasurementOut.model_validate(obj)


@router.delete("/{id}", response_model=MeasurementDeleteOut, status_code=200)
async def delete_measurement(
    id: int,
    current_user: User = Depends(require_permission("measurements.delete")),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Deleting measurement id={id}")

    obj = await _get_scoped_measurement(db, id, current_user, for_update=True)

    if obj.status not in ["DRAFT", "REJECTED"]:
        raise ValidationError(
            "Cannot modify a measurement once it has been submitted for approval."
        )

    try:
        result = await db.execute(
            select(Invoice.id).where(
                Invoice.source_type == InvoiceSourceType.MEASUREMENT,
                Invoice.reference_id == obj.id,
            )
        )
        invoice_exists = result.scalar_one_or_none()
    except Exception:
        logger.exception(f"Invoice check failed measurement={id}")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while checking invoice status",
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
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while deleting measurement",
        )

    logger.info(f"Measurement deleted id={id}")

    return {"message": "Measurement deleted successfully"}


@router.put("/{id}/status", response_model=FinalMeasurementOut)
async def update_measurement_status(
    id: int,
    payload: MeasurementStatusUpdate,
    current_user: User = Depends(require_permission("measurements.edit")),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating measurement status id={id} to {payload.status}")

    obj = await _get_scoped_measurement(db, id, current_user, for_update=True)

    valid_statuses = [s.value for s in MeasurementStatus]
    if payload.status not in valid_statuses:
        raise ValidationError(f"Invalid status. Must be one of: {valid_statuses}")

    if payload.status in ["APPROVED", "REJECTED"]:
        raise ValidationError(
            "Cannot manually set status to APPROVED or REJECTED. Must use the central Approvals API."
        )

    if payload.status == "BILLED":
        raise ValidationError(
            "Cannot manually set status to BILLED. Must be updated through the authorized billing workflow."
        )

    if obj.status in ["APPROVED", "BILLED"]:
        raise ValidationError(
            f"Cannot modify or transition a measurement once it has been {obj.status.lower()}."
        )

    if payload.status == "SUBMITTED":
        if obj.status not in ["DRAFT", "REJECTED"]:
            raise ValidationError(
                f"Cannot submit measurement with status '{obj.status}'. Only 'DRAFT' or 'REJECTED' measurements can be submitted."
            )
        obj.status = "SUBMITTED"
        existing_approval = await db.scalar(
            select(Approval).where(
                func.lower(Approval.entity_type) == "measurement",
                Approval.entity_id == obj.id,
                Approval.status == "Pending",
            )
        )
        if not existing_approval:
            db.add(
                Approval(
                    entity_type="measurement",
                    entity_id=obj.id,
                    requested_by=current_user.id,
                    status="Pending",
                )
            )

    elif payload.status == "VERIFIED":
        if obj.status != "SUBMITTED":
            raise ValidationError(
                f"Cannot verify measurement with status '{obj.status}'. Only 'SUBMITTED' measurements can be verified."
            )
        is_sa = getattr(current_user, "is_super_admin", False) is True
        if not is_sa:
            effective_perms = await get_effective_user_permissions(db, current_user)
            if not (
                has_permission(effective_perms, "measurements.approve")
                or has_permission(effective_perms, "approvals.approve")
                or has_permission(effective_perms, "measurements.manage")
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Insufficient permissions to verify measurement.",
                )
        obj.status = "VERIFIED"

    elif payload.status == "DRAFT":
        if obj.status != "REJECTED":
            raise ValidationError(
                f"Cannot revert measurement to DRAFT from status '{obj.status}'. Only REJECTED measurements can be reset to DRAFT."
            )
        obj.status = "DRAFT"

    else:
        raise ValidationError(
            f"Transition from '{obj.status}' to '{payload.status}' is not permitted."
        )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Measurement status update failed id={id}")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while updating measurement status",
        )

    await db.refresh(obj)
    return FinalMeasurementOut.model_validate(obj)
