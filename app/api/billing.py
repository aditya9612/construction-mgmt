from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from app.db.session import get_db_session
from app.models.billing import RABill
from app.models.project import Project
from app.models.contractor import Contractor
from app.models.owner import OwnerTransaction

from app.schemas.billing import RABillCreate, RABillUpdate, RABillOut
from app.schemas.base import PaginatedResponse, PaginationMeta

from app.utils.helpers import NotFoundError, ValidationError
from app.utils.pagination import PaginationParams
from app.utils.common import assert_project_access

from app.models.approval import Approval
from app.models.user import User
from app.core.dependencies import get_current_active_user, require_roles

router = APIRouter(prefix="/billing", tags=["Billing"])


# ======================
# CREATE
# ======================
@router.post("", response_model=RABillOut)
async def create_ra_bill(
    payload: RABillCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    from app.models.work_order import WorkOrder

    project = await db.get(Project, payload.project_id)
    contractor = await db.get(Contractor, payload.contractor_id)

    if not project:
        raise NotFoundError("Project not found")

    if not contractor:
        raise NotFoundError("Contractor not found")

    await assert_project_access(
        db,
        project_id=payload.project_id,
        current_user=current_user,
    )

    if payload.bill_date > date.today():
        raise ValidationError("Future bill date not allowed")

    existing = await db.scalar(
        select(RABill).where(RABill.bill_number == payload.bill_number)
    )
    if existing:
        raise ValidationError("Bill number already exists")

    work_order = None
    if payload.work_order_id:
        work_order = await db.get(WorkOrder, payload.work_order_id)

        if not work_order:
            raise ValidationError("Invalid work order")

        if work_order.contractor_id != payload.contractor_id:
            raise ValidationError("Work order contractor mismatch")

        if work_order.project_id != payload.project_id:
            raise ValidationError("Work order project mismatch")

        if payload.quantity > work_order.completed_quantity:
            raise ValidationError("Billing exceeds completed work")

        total_billed = (
            await db.scalar(
                select(func.sum(RABill.quantity)).where(
                    RABill.work_order_id == payload.work_order_id
                )
            )
            or 0
        )

        if total_billed + payload.quantity > work_order.completed_quantity:
            raise ValidationError("Total billing exceeds completed quantity")

    gross = payload.quantity * payload.rate

    if payload.deductions > gross:
        raise ValidationError("Deductions cannot exceed gross")

    net = gross - payload.deductions
    gst_amount = (net * payload.gst_percent) / 100
    total = net + gst_amount

    obj = RABill(
        **payload.model_dump(),
        gross_amount=gross,
        net_amount=net,
        total_amount=total,
        status="Draft",
    )

    db.add(obj)
    await db.flush()

    db.add(
        OwnerTransaction(
            owner_id=project.owner_id,
            project_id=project.id,
            type="debit",
            amount=float(total),
            reference_type="ra_bill",
            reference_id=obj.id,
            description="Contractor RA Bill",
        )
    )

    db.add(
        Approval(
            entity_type="bill",
            entity_id=obj.id,
            requested_by=current_user.id,
            status="Pending",
        )
    )

    await db.flush()

    progress = None
    total_billed_qty = None
    remaining_qty = None
    available_qty = None

    if work_order:
        if work_order.total_quantity:
            progress = float((obj.quantity / work_order.total_quantity) * 100)

        total_billed_qty = (
            await db.scalar(
                select(func.sum(RABill.quantity)).where(
                    RABill.work_order_id == obj.work_order_id
                )
            )
            or Decimal("0")
        )

        remaining_qty = work_order.total_quantity - total_billed_qty
        available_qty = work_order.completed_quantity - total_billed_qty

        total_billed_qty = float(total_billed_qty)
        remaining_qty = float(remaining_qty)
        available_qty = float(available_qty)
        
    return RABillOut.model_validate({
        **obj.__dict__,
        "progress_percent": round(progress, 2) if progress else None,
        "total_billed_quantity": total_billed_qty,
        "remaining_quantity": remaining_qty,
        "available_to_bill": available_qty,
    })


# ======================
# LIST
# ======================
@router.get("", response_model=PaginatedResponse[RABillOut])
async def list_ra_bills(
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db_session),
):
    from app.models.work_order import WorkOrder

    pagination = pagination.normalized()

    total = await db.scalar(select(func.count()).select_from(RABill))

    query = (
        select(RABill)
        .order_by(RABill.id.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    rows = (await db.execute(query)).scalars().all()

    items = []

    for r in rows:
        progress = None
        total_billed_qty = None
        remaining_qty = None
        available_qty = None

        if r.work_order_id:
            work_order = await db.get(WorkOrder, r.work_order_id)

            if work_order:
                if work_order.total_quantity:
                    progress = float(
                        (r.quantity / work_order.total_quantity) * 100
                    )

                total_billed_qty = (
                    await db.scalar(
                        select(func.sum(RABill.quantity)).where(
                            RABill.work_order_id == r.work_order_id
                        )
                    )
                    or Decimal("0")
                )

                remaining_qty = work_order.total_quantity - total_billed_qty
                available_qty = work_order.completed_quantity - total_billed_qty

                total_billed_qty = float(total_billed_qty)
                remaining_qty = float(remaining_qty)
                available_qty = float(available_qty)

        items.append(
            RABillOut.model_validate({
                **r.__dict__,
                "progress_percent": round(progress, 2) if progress else None,
                "total_billed_quantity": total_billed_qty,
                "remaining_quantity": remaining_qty,
                "available_to_bill": available_qty,
            })
        )

    return PaginatedResponse(
        items=items,
        meta=PaginationMeta(
            total=int(total or 0),
            limit=pagination.limit,
            offset=pagination.offset,
        ),
    )

# ======================
# GET
# ======================
@router.get("/{id}", response_model=RABillOut)
async def get_ra_bill(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    from app.models.work_order import WorkOrder

    obj = await db.get(RABill, id)
    if not obj:
        raise NotFoundError("RA Bill not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    progress = None
    total_billed_qty = None
    remaining_qty = None
    available_qty = None

    if obj.work_order_id:
        work_order = await db.get(WorkOrder, obj.work_order_id)

        if work_order:
            if work_order.total_quantity:
                progress = float(
                    (obj.quantity / work_order.total_quantity) * 100
                )

            total_billed_qty = (
                await db.scalar(
                    select(func.sum(RABill.quantity)).where(
                        RABill.work_order_id == obj.work_order_id
                    )
                )
                or Decimal("0")
            )

            remaining_qty = work_order.total_quantity - total_billed_qty
            available_qty = work_order.completed_quantity - total_billed_qty

            total_billed_qty = float(total_billed_qty)
            remaining_qty = float(remaining_qty)
            available_qty = float(available_qty)

    return RABillOut.model_validate({
        **obj.__dict__,
        "progress_percent": round(progress, 2) if progress else None,
        "total_billed_quantity": total_billed_qty,
        "remaining_quantity": remaining_qty,
        "available_to_bill": available_qty,
    })

# ======================
# UPDATE
# ======================
@router.put("/{id}", response_model=RABillOut)
async def update_ra_bill(
    id: int,
    payload: RABillUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    obj = await db.get(RABill, id)
    if not obj:
        raise NotFoundError("RA Bill not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    gross = obj.quantity * obj.rate
    net = gross - (obj.deductions or 0)
    gst_amount = (net * (obj.gst_percent or 0)) / 100
    total = net + gst_amount

    obj.gross_amount = gross
    obj.net_amount = net
    obj.total_amount = total

    await db.flush()

    return RABillOut.model_validate(obj)


# ======================
# DELETE
# ======================
@router.delete("/{id}")
async def delete_ra_bill(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    obj = await db.get(RABill, id)
    if not obj:
        raise NotFoundError("RA Bill not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    await db.delete(obj)
    await db.flush()

    return {"success": True}


# ======================
# STATUS FLOW
# ======================


@router.put("/{id}/submit")
async def submit_bill(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(RABill, id)
    if not obj:
        raise NotFoundError("Bill not found")

    if obj.status != "Draft":
        raise ValidationError("Only draft bills can be submitted")

    obj.status = "Submitted"

    await db.flush()

    return {"message": "Submitted"}


@router.put("/{id}/approve")
async def approve_bill(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(RABill, id)
    if not obj:
        raise NotFoundError("Bill not found")

    if obj.status != "Submitted":
        raise ValidationError("Bill must be submitted first")

    obj.status = "Approved"

    # sync approval table
    approval = await db.scalar(
        select(Approval).where(
            Approval.entity_type == "bill",
            Approval.entity_id == obj.id,
        )
    )
    if approval:
        approval.status = "Approved"

    await db.flush()

    return {"message": "Approved"}


@router.put("/{id}/pay")
async def pay_bill(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(RABill, id)
    if not obj:
        raise NotFoundError("Bill not found")

    if obj.status != "Approved":
        raise ValidationError("Only approved bills can be paid")

    obj.status = "Paid"

    await db.flush()

    return {"message": "Paid"}
