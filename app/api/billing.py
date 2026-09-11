from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, require_permission
from app.core.enums import AccountType
from app.core.logger import logger
from app.db.session import get_db_session
from app.models.accountant import Account, JournalEntry, JournalLine
from app.models.approval import Approval
from app.models.billing import RABill
from app.models.company import Company
from app.models.contractor import Contractor
from app.models.owner import OwnerTransaction
from app.models.project import Project
from app.models.user import User
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.billing import RABillCreate, RABillOut, RABillUpdate
from app.utils.accounting import (
    get_accounts_receivable,
    get_revenue_account,
    resolve_tax_accounts,
)
from app.utils.common import assert_project_access
from app.utils.helpers import NotFoundError, ValidationError
from app.utils.pagination import PaginationParams

router = APIRouter(prefix="/billing", tags=["Billing"])


def _check_tenant_access(current_user: User) -> bool:
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=403, detail="Company context required")
    return is_sa


async def _get_authorized_ra_bill(
    db: AsyncSession,
    id: int,
    current_user: User,
    for_update: bool = False,
) -> RABill:
    is_sa = _check_tenant_access(current_user)

    query = select(RABill).where(RABill.id == id)
    if for_update:
        query = query.with_for_update()

    obj = await db.scalar(query)
    if not obj:
        raise NotFoundError("RA Bill not found")

    project = await db.get(Project, obj.project_id)
    if not project:
        raise NotFoundError("RA Bill not found")

    if not is_sa and project.company_id != current_user.company_id:
        raise NotFoundError("RA Bill not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )
    return obj


# ======================
# CREATE
# ======================
@router.post("", response_model=RABillOut)
async def create_ra_bill(
    payload: RABillCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("billing.create")),
):
    from app.models.final_measurement import FinalMeasurement
    from app.models.work_order import WorkOrder

    is_sa = _check_tenant_access(current_user)

    project = await db.get(Project, payload.project_id)
    if not project or (not is_sa and project.company_id != current_user.company_id):
        raise NotFoundError("Project not found")

    target_company_id = project.company_id

    contractor = None
    if payload.contractor_id:
        contractor = await db.get(Contractor, payload.contractor_id)
        if not contractor or (
            target_company_id is not None
            and contractor.company_id != target_company_id
        ):
            raise NotFoundError("Contractor not found")

    await assert_project_access(
        db,
        project_id=payload.project_id,
        current_user=current_user,
    )

    if payload.bill_date > date.today():
        raise ValidationError("Future bill date not allowed")

    # ================= Measurement Validation =================
    if payload.measurement_id:
        measurement = await db.get(
            FinalMeasurement,
            payload.measurement_id,
        )

        if not measurement:
            raise ValidationError("Measurement not found")

        if measurement.project_id != payload.project_id:
            raise ValidationError("Measurement project mismatch")

        if measurement.status not in ["VERIFIED", "APPROVED"]:
            raise ValidationError(
                "Only VERIFIED or APPROVED measurements can be billed"
            )

    # ================= Duplicate Bill (Tenant-Scoped) =================
    dup_filter = (
        Project.company_id == target_company_id
        if target_company_id is not None
        else Project.company_id.is_(None)
    )
    existing = await db.scalar(
        select(RABill)
        .join(Project, RABill.project_id == Project.id)
        .where(
            dup_filter,
            RABill.bill_number == payload.bill_number,
        )
    )

    if existing:
        raise ValidationError("Bill number already exists")

    # ================= Work Order Validation =================
    work_order = None
    if payload.work_order_id:
        work_order = await db.get(
            WorkOrder,
            payload.work_order_id,
        )

        if not work_order:
            raise ValidationError("Invalid work order")

        if (
            work_order.contractor_id is not None
            and work_order.contractor_id != payload.contractor_id
        ):
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
            or Decimal("0")
        )

        if total_billed + payload.quantity > work_order.completed_quantity:
            raise ValidationError("Total billing exceeds completed quantity")

    # ================= Amount Calculation =================
    gross = payload.quantity * payload.rate

    if payload.deductions > gross:
        raise ValidationError("Deductions cannot exceed gross")

    net = gross - payload.deductions
    gst_amount = (net * payload.gst_percent) / 100
    total = net + gst_amount

    # ================= Create RA Bill =================
    obj = RABill(
        **payload.model_dump(),
        gross_amount=gross,
        net_amount=net,
        total_amount=total,
        status="Draft",
    )

    db.add(obj)
    await db.flush()

    # ================= Owner Transaction =================
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

    # ================= Approval =================
    db.add(
        Approval(
            entity_type="bill",
            entity_id=obj.id,
            requested_by=current_user.id,
            status="Pending",
        )
    )

    await db.flush()

    # ================= Response Calculation =================
    progress = None
    total_billed_qty = None
    remaining_qty = None
    available_qty = None

    if work_order:
        if work_order.total_quantity:
            progress = float((payload.quantity / work_order.total_quantity) * 100)

        total_billed_qty = (
            await db.scalar(
                select(func.sum(RABill.quantity)).where(
                    RABill.work_order_id == payload.work_order_id
                )
            )
            or Decimal("0")
        )

        remaining_qty = work_order.total_quantity - total_billed_qty
        available_qty = work_order.completed_quantity - total_billed_qty

        total_billed_qty = float(total_billed_qty)
        remaining_qty = float(remaining_qty)
        available_qty = float(available_qty)

    return RABillOut.model_validate(
        {
            **obj.__dict__,
            "project_name": project.project_name if project else None,
            "contractor_name": contractor.name if contractor else None,
            "progress_percent": round(progress, 2) if progress else None,
            "total_billed_quantity": total_billed_qty,
            "remaining_quantity": remaining_qty,
            "available_to_bill": available_qty,
        }
    )


# ======================
# LIST
# ======================
@router.get("", response_model=PaginatedResponse[RABillOut])
async def list_ra_bills(
    company_id: Optional[int] = Query(None),
    status: Optional[str] = None,
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("billing.view")),
):
    from app.models.work_order import WorkOrder

    is_sa = _check_tenant_access(current_user)

    effective_company_id: Optional[int] = None
    if is_sa:
        if company_id is not None:
            comp = await db.get(Company, company_id)
            if not comp:
                raise NotFoundError("Company not found")
            effective_company_id = company_id
        else:
            effective_company_id = None
    else:
        # Non-SA: strictly constrained to current_user.company_id
        effective_company_id = current_user.company_id

    pagination = pagination.normalized()

    query = (
        select(
            RABill,
            Project.project_name,
            Contractor.name.label("contractor_name"),
            Approval.id.label("approval_id"),
        )
        .outerjoin(Project, RABill.project_id == Project.id)
        .outerjoin(Contractor, RABill.contractor_id == Contractor.id)
        .outerjoin(
            Approval,
            (Approval.entity_type == "bill")
            & (Approval.entity_id == RABill.id)
            & (Approval.status == "Pending"),
        )
    )

    count_query = (
        select(func.count())
        .select_from(RABill)
        .join(Project, RABill.project_id == Project.id)
    )

    if effective_company_id is not None:
        query = query.where(Project.company_id == effective_company_id)
        count_query = count_query.where(Project.company_id == effective_company_id)

    if status:
        query = query.where(RABill.status == status)
        count_query = count_query.where(RABill.status == status)

    query = (
        query.order_by(RABill.id.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    rows = (await db.execute(query)).all()
    total = await db.scalar(count_query)

    items = []

    for row in rows:
        r = row.RABill
        project_name = row.project_name
        contractor_name = row.contractor_name
        approval_id = row.approval_id
        progress = None
        total_billed_qty = None
        remaining_qty = None
        available_qty = None

        if r.work_order_id:
            work_order = await db.get(WorkOrder, r.work_order_id)

            if work_order:
                if work_order.total_quantity:
                    progress = float((r.quantity / work_order.total_quantity) * 100)

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
            RABillOut.model_validate(
                {
                    **r.__dict__,
                    "project_name": project_name,
                    "contractor_name": contractor_name,
                    "approval_id": approval_id,
                    "progress_percent": round(progress, 2) if progress else None,
                    "total_billed_quantity": total_billed_qty,
                    "remaining_quantity": remaining_qty,
                    "available_to_bill": available_qty,
                }
            )
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
    current_user: User = Depends(require_permission("billing.view")),
):
    from app.models.work_order import WorkOrder

    obj = await _get_authorized_ra_bill(db, id, current_user)

    progress = None
    total_billed_qty = None
    remaining_qty = None
    available_qty = None

    if obj.work_order_id:
        work_order = await db.get(WorkOrder, obj.work_order_id)

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

    return RABillOut.model_validate(
        {
            **obj.__dict__,
            "progress_percent": round(progress, 2) if progress else None,
            "total_billed_quantity": total_billed_qty,
            "remaining_quantity": remaining_qty,
            "available_to_bill": available_qty,
        }
    )


# ======================
# UPDATE
# ======================
@router.put("/{id}", response_model=RABillOut)
async def update_ra_bill(
    id: int,
    payload: RABillUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("billing.edit")),
):
    from app.models.work_order import WorkOrder

    obj = await _get_authorized_ra_bill(db, id, current_user, for_update=True)

    project = await db.get(Project, obj.project_id)
    target_company_id = project.company_id if project else None

    data = payload.model_dump(exclude_unset=True)

    # ================= Contractor Validation =================
    if "contractor_id" in data and data["contractor_id"] is not None:
        contractor = await db.get(
            Contractor,
            data["contractor_id"],
        )

        if not contractor or (
            target_company_id is not None
            and contractor.company_id != target_company_id
        ):
            raise NotFoundError("Contractor not found")

    # ================= Work Order Validation =================
    work_order_id = data.get("work_order_id", obj.work_order_id)

    if work_order_id:
        work_order = await db.get(
            WorkOrder,
            work_order_id,
        )

        if not work_order:
            raise ValidationError("Invalid work order")

        contractor_id = data.get(
            "contractor_id",
            obj.contractor_id,
        )

        if (
            work_order.contractor_id is not None
            and work_order.contractor_id != contractor_id
        ):
            raise ValidationError("Work order contractor mismatch")

        if work_order.project_id != obj.project_id:
            raise ValidationError("Work order project mismatch")

    # ================= Update Fields =================
    for k, v in data.items():
        setattr(obj, k, v)

    # ================= Recalculate Amount =================
    gross = obj.quantity * obj.rate

    if (obj.deductions or 0) > gross:
        raise ValidationError("Deductions cannot exceed gross")

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
    current_user: User = Depends(require_permission("billing.delete")),
):
    obj = await _get_authorized_ra_bill(db, id, current_user, for_update=True)

    if obj.status in ["Approved", "Paid"]:
        raise ValidationError("Cannot delete approved or paid RA bill")

    if obj.status not in ["Draft", "Submitted"]:
        raise ValidationError(f"Cannot delete bill with status '{obj.status}'")

    await db.execute(
        delete(Approval).where(
            Approval.entity_type == "bill",
            Approval.entity_id == obj.id,
        )
    )
    await db.execute(
        delete(OwnerTransaction).where(
            OwnerTransaction.reference_type == "ra_bill",
            OwnerTransaction.reference_id == obj.id,
        )
    )

    await db.delete(obj)
    await db.flush()

    return {"success": True}


# ======================
# STATUS FLOW
# ======================


@router.put("/{id}/submit")
async def submit_bill(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("billing.edit")),
):
    obj = await _get_authorized_ra_bill(db, id, current_user, for_update=True)

    if obj.status != "Draft":
        raise ValidationError("Only draft bills can be submitted")

    obj.status = "Submitted"

    await db.flush()

    return {"message": "Submitted"}


@router.put("/{id}/approve")
async def approve_bill(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("billing.approve")),
):
    obj = await _get_authorized_ra_bill(db, id, current_user, for_update=True)

    if obj.status != "Submitted":
        raise ValidationError("Bill must be submitted first")

    # Check duplicate journal entry to prevent double-posting
    existing_je = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.journal_number == f"J-RAB-{obj.id}"
        )
    )
    if existing_je:
        raise ValidationError(f"Journal entry for bill {obj.id} already exists")

    obj.status = "Approved"

    # sync approval table
    approval = await db.scalar(
        select(Approval).where(
            Approval.entity_type == "bill",
            Approval.entity_id == obj.id,
        ).with_for_update()
    )
    if approval:
        approval.status = "Approved"

    # Create Journal Entry
    je = JournalEntry(
        entry_type="Invoice",
        journal_number=f"J-RAB-{obj.id}",
        entry_date=date.today(),
        description=f"RA Bill {obj.bill_number} Approved",
        status="Posted",
    )
    db.add(je)
    await db.flush()

    project = await db.get(Project, obj.project_id)
    company_id = (
        getattr(project, "company_id", None)
        or getattr(current_user, "company_id", None)
    )
    if not company_id:
        raise HTTPException(
            status_code=400,
            detail="Company context required for billing approval",
        )

    ar_acc = await get_accounts_receivable(db, company_id=company_id)
    rev_acc = await get_revenue_account(db, company_id=company_id)

    db.add(
        JournalLine(
            entry_id=je.id,
            account_id=ar_acc.id,
            debit=obj.total_amount,
            credit=Decimal(0),
        )
    )

    db.add(
        JournalLine(
            entry_id=je.id,
            account_id=rev_acc.id,
            debit=Decimal(0),
            credit=obj.gross_amount,
        )
    )

    gst_amount = (obj.net_amount * obj.gst_percent) / 100

    if gst_amount > 0:
        gst_acc = await resolve_tax_accounts(
            db, "output_gst", company_id=company_id
        )

        db.add(
            JournalLine(
                entry_id=je.id,
                account_id=gst_acc.id,
                debit=Decimal(0),
                credit=gst_amount,
            )
        )

    if obj.deductions and obj.deductions > 0:
        retention_acc = await db.scalar(
            select(Account).where(
                Account.code == "RETENTION_PAYABLE",
                Account.company_id == company_id,
            )
        )
        if not retention_acc:
            retention_acc = Account(
                name="Retention / Deductions",
                code="RETENTION_PAYABLE",
                type=AccountType.LIABILITY,
                company_id=company_id,
            )
            db.add(retention_acc)
            await db.flush()
        db.add(
            JournalLine(
                entry_id=je.id,
                account_id=retention_acc.id,
                debit=obj.deductions,
                credit=Decimal(0),
            )
        )

    await db.flush()
    return {"message": "Approved"}


@router.put("/{id}/pay")
async def pay_bill(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("billing.pay")),
):
    obj = await _get_authorized_ra_bill(db, id, current_user, for_update=True)

    if obj.status != "Approved":
        raise ValidationError("Only approved bills can be paid")

    obj.status = "Paid"

    await db.flush()

    return {"message": "Paid"}
