from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from fastapi import Query
from app.utils.pagination import PaginationParams
from app.db.session import get_db_session
from app.models.ra_bill import RABill
from app.models.project import Project
from app.models.contractor import Contractor
from app.models.owner import OwnerTransaction
from app.schemas.ra_bill import RABillCreate, RABillUpdate, RABillOut
from app.utils.helpers import NotFoundError, ValidationError
from app.core.logger import logger
from sqlalchemy import select, func
from app.schemas.base import PaginatedResponse, PaginationMeta

router = APIRouter(prefix="/ra-bills", tags=["RA Bills"])


@router.post("", response_model=RABillOut)
async def create_ra_bill(
    payload: RABillCreate,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Creating RA bill project_id={payload.project_id}")

    project = await db.get(Project, payload.project_id)
    contractor = await db.get(Contractor, payload.contractor_id)

    if not project:
        logger.warning(f"Project not found id={payload.project_id}")
        raise NotFoundError("Project not found")

    if not contractor:
        logger.warning(f"Contractor not found id={payload.contractor_id}")
        raise NotFoundError("Contractor not found")

    if payload.quantity <= 0:
        raise ValidationError("Quantity must be greater than 0")

    if payload.rate <= 0:
        raise ValidationError("Rate must be greater than 0")

    if payload.deductions < 0:
        raise ValidationError("Deductions cannot be negative")

    gross = payload.quantity * payload.rate

    if payload.deductions > gross:
        raise ValidationError("Deductions cannot exceed gross amount")

    if payload.gst_percent < 0 or payload.gst_percent > 28:
        raise ValidationError("Invalid GST percent")

    if payload.bill_date > date.today():
        raise ValidationError("Future bill date not allowed")

    existing = await db.scalar(
        select(RABill).where(RABill.bill_number == payload.bill_number)
    )
    if existing:
        raise ValidationError("Bill number already exists")

    net = gross - payload.deductions

    if net < 0:
        raise ValidationError("Net amount cannot be negative")

    gst_percent = payload.gst_percent or 0
    gst_amount = (net * gst_percent) / 100
    total = net + gst_amount

    obj = RABill(
        **payload.model_dump(),
        gross_amount=gross,
        net_amount=net,
        total_amount=total,
    )

    db.add(obj)

    try:
        await db.flush()

        owner_txn = OwnerTransaction(
            owner_id=project.owner_id,
            project_id=project.id,
            type="debit",
            amount=float(total),
            reference_type="ra_bill",
            reference_id=obj.id,
            description="Contractor RA Bill",
        )
        db.add(owner_txn)

        await db.commit()

    except Exception:
        await db.rollback()
        logger.exception("RA bill creation failed")
        raise

    await db.refresh(obj)

    logger.info(f"RA bill created id={obj.id}")

    return RABillOut.model_validate(obj)



@router.get("", response_model=PaginatedResponse[RABillOut])
async def list_ra_bills(
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db_session),
):
    pagination = pagination.normalized()

    # total
    total = await db.scalar(select(func.count()).select_from(RABill))

    # data
    query = (
        select(RABill)
        .order_by(RABill.id.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    rows = (await db.execute(query)).scalars().all()

    items = [RABillOut.model_validate(r) for r in rows]

    return PaginatedResponse(
        items=items,
        meta=PaginationMeta(
            total=int(total or 0),
            limit=pagination.limit,
            offset=pagination.offset,
        ),
    )


@router.get("/{id}", response_model=RABillOut)
async def get_ra_bill(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(RABill, id)

    if not obj:
        raise NotFoundError("RA Bill not found")

    return RABillOut.model_validate(obj)


@router.put("/{id}", response_model=RABillOut)
async def update_ra_bill(
    id: int,
    payload: RABillUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating RA bill id={id}")

    obj = await db.get(RABill, id)

    if not obj:
        logger.warning(f"RA bill not found id={id}")
        raise NotFoundError("RA Bill not found")

    if payload.quantity is not None and payload.quantity <= 0:
        raise ValidationError("Quantity must be greater than 0")

    if payload.rate is not None and payload.rate <= 0:
        raise ValidationError("Rate must be greater than 0")

    if payload.deductions is not None and payload.deductions < 0:
        raise ValidationError("Deductions cannot be negative")

    if payload.gst_percent is not None and (
        payload.gst_percent < 0 or payload.gst_percent > 28
    ):
        raise ValidationError("Invalid GST percent")

    if payload.bill_date is not None and payload.bill_date > date.today():
        raise ValidationError("Future bill date not allowed")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    gross = obj.quantity * obj.rate

    if obj.deductions is not None and obj.deductions > gross:
        raise ValidationError("Deductions cannot exceed gross amount")

    net = gross - (obj.deductions or 0)

    if net < 0:
        raise ValidationError("Net amount cannot be negative")

    gst_percent = obj.gst_percent or 0
    gst_amount = (net * gst_percent) / 100
    total = net + gst_amount

    obj.gross_amount = gross
    obj.net_amount = net
    obj.total_amount = total

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"RA bill update failed id={id}")
        raise

    await db.refresh(obj)

    logger.info(f"RA bill updated id={id}")

    return RABillOut.model_validate(obj)


@router.delete("/{id}")
async def delete_ra_bill(id: int, db: AsyncSession = Depends(get_db_session)):
    logger.info(f"Deleting RA bill id={id}")

    obj = await db.get(RABill, id)

    if not obj:
        logger.warning(f"RA bill not found id={id}")
        raise NotFoundError("RA Bill not found")

    try:
        await db.delete(obj)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"RA bill delete failed id={id}")
        raise

    logger.info(f"RA bill deleted id={id}")

    return {
        "success": True,
        "message": "RA Bill deleted successfully"
    }


@router.get("/contractor/{contractor_id}", response_model=PaginatedResponse[RABillOut])
async def bills_by_contractor(
    contractor_id: int,
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db_session),
):
    pagination = pagination.normalized()

    # 🔹 Total count
    total = await db.scalar(
        select(func.count()).where(RABill.contractor_id == contractor_id)
    )

    # 🔹 Data query
    query = (
        select(RABill)
        .where(RABill.contractor_id == contractor_id)
        .order_by(RABill.id.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    rows = (await db.execute(query)).scalars().all()

    items = [RABillOut.model_validate(r) for r in rows]

    return PaginatedResponse(
        items=items,
        meta=PaginationMeta(
            total=int(total or 0),
            limit=pagination.limit,
            offset=pagination.offset,
        ),
    )
