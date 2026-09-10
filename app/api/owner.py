from datetime import date
from decimal import Decimal
from typing import Optional
from io import BytesIO, StringIO
import csv

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet

from app.core.dependencies import require_permission
from app.core.enums import OwnerTransactionType, ProjectStatus
from app.core.logger import logger
from app.db.session import get_db_session
from app.models.owner import Owner, OwnerTransaction, OwnerPaymentSchedule
from app.models.project import Project
from app.models.invoice import Invoice
from app.models.user import User
from app.schemas.owner import (
    OwnerCreate,
    OwnerUpdate,
    OwnerOut,
    OwnerLedgerResponse,
    OwnerTransactionOut,
    ClientPortfolioResponse,
    ClientPortfolioItem,
    ClientPortfolioSummary,
    OwnerPaymentScheduleCreate,
    OwnerPaymentScheduleOut,
)
from app.utils.common import generate_business_id
from app.utils.helpers import NotFoundError, ValidationError

router = APIRouter(
    prefix="/owners",
    tags=["owners"],
)


async def _get_scoped_owner(
    db: AsyncSession,
    owner_id: int,
    current_user: User,
) -> Owner:
    """Retrieve Owner enforcing tenant boundary isolation through Owner.company_id."""
    is_sa = getattr(current_user, "is_super_admin", False) is True

    query = select(Owner).where(Owner.id == owner_id)
    if not is_sa:
        if current_user.company_id is None:
            raise NotFoundError("Owner not found")
        query = query.where(Owner.company_id == current_user.company_id)

    obj = await db.scalar(query)
    if not obj:
        raise NotFoundError("Owner not found")

    return obj


@router.post("", response_model=OwnerOut)
async def create_owner(
    payload: OwnerCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.create")),
):
    logger.info(f"Creating owner name={payload.owner_name}")

    is_sa = getattr(current_user, "is_super_admin", False) is True

    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company.",
        )

    target_company_id = current_user.company_id
    data = payload.model_dump()

    for _ in range(3):
        try:
            data["owner_code"] = await generate_business_id(
                db, Owner, "owner_code", "OWN"
            )

            obj = Owner(**data)
            obj.company_id = target_company_id

            db.add(obj)
            await db.flush()
            await db.commit()
            await db.refresh(obj)

            logger.info(f"Owner created id={obj.id}")
            return OwnerOut.model_validate(obj)

        except IntegrityError:
            await db.rollback()
            logger.warning("Retrying owner creation due to duplicate owner_code")
        except Exception:
            await db.rollback()
            logger.exception("Owner creation failed")
            raise HTTPException(
                status_code=500,
                detail="An internal error occurred while creating owner",
            )

    raise HTTPException(
        status_code=500,
        detail="Failed to create owner with unique owner_code after multiple retries",
    )


@router.get("", response_model=list[OwnerOut])
async def list_owners(
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True

    query = select(Owner)
    if not is_sa:
        if current_user.company_id is None:
            return []
        query = query.where(Owner.company_id == current_user.company_id)

    if search:
        query = query.where(Owner.owner_name.ilike(f"%{search}%"))

    result = await db.execute(query)
    owners = result.scalars().all()

    return [OwnerOut.model_validate(o) for o in owners]


@router.get("/portfolio", response_model=ClientPortfolioResponse)
async def get_client_portfolio(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.view")),
):
    """
    Returns an aggregated view of clients (owners) with their project counts,
    billing status, and financial history as shown in the Client Portfolio design.
    """
    logger.info("Fetching client portfolio summary")

    is_sa = getattr(current_user, "is_super_admin", False) is True

    # 1. Fetch all owners
    owners_query = select(Owner)
    if not is_sa:
        if current_user.company_id is None:
            return ClientPortfolioResponse(
                summary=ClientPortfolioSummary(
                    total_clients=0,
                    total_outstanding_billing=Decimal("0"),
                    average_satisfaction_score=0.0,
                ),
                items=[],
            )
        owners_query = owners_query.where(Owner.company_id == current_user.company_id)

    owners_result = await db.execute(owners_query)
    owners = owners_result.scalars().all()

    portfolio_items = []
    total_outstanding = Decimal("0")

    owner_ids = [o.id for o in owners]
    if not owner_ids:
        return ClientPortfolioResponse(
            summary=ClientPortfolioSummary(
                total_clients=0,
                total_outstanding_billing=Decimal("0"),
                average_satisfaction_score=0.0,
            ),
            items=[],
        )
    # Batched Project Stats
    proj_stats_res = await db.execute(
        select(
            Project.owner_id, 
            func.count(Project.id), 
            func.max(Project.project_name),
            func.sum(case((and_(Project.status == ProjectStatus.ONGOING.value, Project.end_date < date.today()), 1), else_=0)),
            func.sum(case((Project.status == "Completed", 1), else_=0))
        ).where(Project.owner_id.in_(owner_ids)).group_by(Project.owner_id)
    )
    proj_stats = {row[0]: (row[1], row[2], int(row[3] or 0), int(row[4] or 0)) for row in proj_stats_res.all()}

    # Batched Financial Stats
    fin_stats_res = await db.execute(
        select(
            Invoice.owner_id,
            func.sum(Invoice.pending_amount),
            func.sum(Invoice.paid_amount)
        ).where(Invoice.owner_id.in_(owner_ids)).group_by(Invoice.owner_id)
    )
    fin_stats = {row[0]: (Decimal(row[1] or 0), Decimal(row[2] or 0)) for row in fin_stats_res.all()}

    # Batched Overdue Payments
    overdue_res = await db.execute(
        select(
            OwnerPaymentSchedule.owner_id,
            func.count(OwnerPaymentSchedule.id)
        ).where(
            OwnerPaymentSchedule.owner_id.in_(owner_ids),
            OwnerPaymentSchedule.status != "Paid",
            OwnerPaymentSchedule.due_date < date.today()
        ).group_by(OwnerPaymentSchedule.owner_id)
    )
    overdue_stats = {row[0]: int(row[1] or 0) for row in overdue_res.all()}

    for owner in owners:
        total_projects, latest_project, delayed_count, completed_count = proj_stats.get(owner.id, (0, None, 0, 0))
        pending_val, received_val = fin_stats.get(owner.id, (Decimal(0), Decimal(0)))
        overdue_count = overdue_stats.get(owner.id, 0)
        
        total_outstanding += pending_val

        score = 100
        score -= delayed_count * 10
        score -= overdue_count * 5
        score += completed_count * 2

        if pending_val > 500000:
            score -= 10
        if not total_projects:
            score = 0
            
        score = max(0, min(score, 100))

        portfolio_items.append(
            ClientPortfolioItem(
                id=owner.id,
                owner_name=owner.owner_name,
                mobile=owner.mobile,
                email=owner.email,
                total_projects=int(total_projects or 0),
                linked_project_name=latest_project,
                pending_billing=pending_val,
                total_received=received_val,
                satisfaction_score=score,
                status=(
                    "ACTIVE" if total_projects and total_projects > 0 else "INACTIVE"
                ),
            )
        )

    # 4. Calculate Average Satisfaction
    total_score = sum(item.satisfaction_score for item in portfolio_items)
    avg_satisfaction = total_score / len(portfolio_items) if portfolio_items else 0.0

    summary = ClientPortfolioSummary(
        total_clients=len(owners),
        total_outstanding_billing=total_outstanding,
        average_satisfaction_score=round(avg_satisfaction, 2),
    )

    return ClientPortfolioResponse(summary=summary, items=portfolio_items)


# =========================
# PAYMENT TRACKER
# =========================


@router.get("/payment-tracker", response_model=list[OwnerPaymentScheduleOut])
async def get_all_payments_tracker(
    owner_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.view")),
):
    """
    Returns a view of owner payments/milestones scoped to tenant permissions.
    """
    is_sa = getattr(current_user, "is_super_admin", False) is True

    if not is_sa and current_user.company_id is None:
        return []

    # If owner_id supplied, assert exists and belongs to caller company
    if owner_id:
        owner_q = select(Owner.id).where(Owner.id == owner_id)
        if not is_sa:
            owner_q = owner_q.where(Owner.company_id == current_user.company_id)
        if not await db.scalar(owner_q):
            raise NotFoundError("Owner not found")

    # If project_id supplied, assert exists and belongs to caller company
    if project_id:
        proj_q = select(Project.id).where(Project.id == project_id)
        if not is_sa:
            proj_q = proj_q.where(Project.company_id == current_user.company_id)
        if not await db.scalar(proj_q):
            raise NotFoundError("Project not found")

    query = (
        select(OwnerPaymentSchedule)
        .join(Owner, OwnerPaymentSchedule.owner_id == Owner.id)
        .join(Project, OwnerPaymentSchedule.project_id == Project.id)
    )

    if not is_sa:
        query = query.where(
            Owner.company_id == current_user.company_id,
            Project.company_id == current_user.company_id,
        )

    if owner_id:
        query = query.where(OwnerPaymentSchedule.owner_id == owner_id)
    if project_id:
        query = query.where(OwnerPaymentSchedule.project_id == project_id)
    if status:
        query = query.where(OwnerPaymentSchedule.status == status)

    query = query.order_by(OwnerPaymentSchedule.due_date.asc())

    result = await db.execute(query)
    rows = result.scalars().all()

    return [OwnerPaymentScheduleOut.model_validate(r) for r in rows]


@router.post("/payment-tracker", response_model=OwnerPaymentScheduleOut)
async def create_payment_milestone(
    payload: OwnerPaymentScheduleCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.create")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True

    if not is_sa and current_user.company_id is None:
        raise NotFoundError("Owner not found")

    # Validate Owner exists and belongs to tenant
    owner_q = select(Owner).where(Owner.id == payload.owner_id)
    if not is_sa:
        owner_q = owner_q.where(Owner.company_id == current_user.company_id)
    owner = await db.scalar(owner_q)
    if not owner:
        raise NotFoundError("Owner not found")

    # Validate Project exists and belongs to tenant
    proj_q = select(Project).where(Project.id == payload.project_id)
    if not is_sa:
        proj_q = proj_q.where(Project.company_id == current_user.company_id)
    project = await db.scalar(proj_q)
    if not project:
        raise NotFoundError("Project not found")

    # Validate Project.owner_id == Owner.id
    if project.owner_id != payload.owner_id:
        logger.warning(
            f"Project owner mismatch: project={payload.project_id} belongs to owner={project.owner_id}, got owner={payload.owner_id}"
        )
        raise NotFoundError("Project not found")

    obj = OwnerPaymentSchedule(**payload.model_dump())
    db.add(obj)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to create payment schedule milestone")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while creating payment milestone",
        )
    await db.refresh(obj)
    return obj


@router.get("/{owner_id}", response_model=OwnerOut)
async def get_owner(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.view")),
):
    obj = await _get_scoped_owner(db, owner_id, current_user)
    return OwnerOut.model_validate(obj)


@router.put("/{owner_id}", response_model=OwnerOut)
async def update_owner(
    owner_id: int,
    payload: OwnerUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.edit")),
):
    logger.info(f"Updating owner id={owner_id}")

    obj = await _get_scoped_owner(db, owner_id, current_user)

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    try:
        await db.flush()
        await db.commit()
        await db.refresh(obj)
    except IntegrityError:
        await db.rollback()
        logger.warning(f"Owner update failed duplicate mobile id={owner_id}")
        raise ValidationError("Mobile number already exists")
    except Exception:
        await db.rollback()
        logger.exception(f"Owner update failed id={owner_id}")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while updating owner",
        )

    logger.info(f"Owner updated id={owner_id}")
    return OwnerOut.model_validate(obj)


@router.delete("/{owner_id}", status_code=204)
async def delete_owner(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.delete")),
):
    logger.info(f"Deleting owner id={owner_id}")

    obj = await _get_scoped_owner(db, owner_id, current_user)

    # Prevent deletion if projects are linked
    project_count = await db.scalar(
        select(func.count(Project.id)).where(Project.owner_id == owner_id)
    )

    if project_count > 0:
        logger.warning(
            f"Owner delete blocked id={owner_id}, linked_projects={project_count}"
        )
        raise ValidationError(
            f"Owner cannot be deleted because {project_count} project(s) are assigned to this owner. Reassign or delete the projects first."
        )

    # Prevent deletion if there are payments
    payment_count = await db.scalar(
        select(func.count(OwnerPaymentSchedule.id)).where(OwnerPaymentSchedule.owner_id == owner_id)
    )
    transaction_count = await db.scalar(
        select(func.count(OwnerTransaction.id)).where(OwnerTransaction.owner_id == owner_id)
    )
    invoice_count = await db.scalar(
        select(func.count(Invoice.id)).where(Invoice.owner_id == owner_id)
    )

    if (payment_count or 0) > 0 or (transaction_count or 0) > 0 or (invoice_count or 0) > 0:
        logger.warning(f"Owner delete blocked id={owner_id}, related financial records exist.")
        raise ValidationError("Owner cannot be deleted because related financial records exist.")

    try:
        await db.delete(obj)
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning(f"Owner delete failed due to IntegrityError id={owner_id}")
        raise ValidationError("Owner cannot be deleted because there are related records.")
    except Exception:
        await db.rollback()
        logger.exception(f"Owner delete failed id={owner_id}")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while deleting owner",
        )

    logger.info(f"Owner deleted id={owner_id}")
    return None


@router.get("/{owner_id}/payments", response_model=list[OwnerTransactionOut])
async def get_owner_payments(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.view")),
):
    owner = await _get_scoped_owner(db, owner_id, current_user)

    result = await db.execute(
        select(OwnerTransaction)
        .where(OwnerTransaction.owner_id == owner.id)
        .order_by(OwnerTransaction.created_at.desc())
    )
    rows = result.scalars().all()

    return [OwnerTransactionOut.model_validate(r) for r in rows]


@router.get("/{owner_id}/ledger", response_model=OwnerLedgerResponse)
async def get_owner_ledger(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.view")),
):
    owner = await _get_scoped_owner(db, owner_id, current_user)

    result = await db.execute(
        select(OwnerTransaction)
        .where(OwnerTransaction.owner_id == owner.id)
        .order_by(OwnerTransaction.created_at.desc())
    )
    transactions = result.scalars().all()

    total_credit = sum(
        (t.amount or Decimal("0"))
        for t in transactions
        if t.type == OwnerTransactionType.CREDIT.value
    )

    total_debit = sum(
        (t.amount or Decimal("0"))
        for t in transactions
        if t.type == OwnerTransactionType.DEBIT.value
    )

    return OwnerLedgerResponse(
        total_credit=total_credit,
        total_debit=total_debit,
        balance=total_credit - total_debit,
        transactions=[OwnerTransactionOut.model_validate(t) for t in transactions],
    )


@router.get("/{owner_id}/ledger/pdf")
async def export_owner_ledger_pdf(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.export")),
):
    logger.info(f"Generating ledger PDF owner_id={owner_id}")

    owner = await _get_scoped_owner(db, owner_id, current_user)

    try:
        result = await db.execute(
            select(OwnerTransaction)
            .where(OwnerTransaction.owner_id == owner.id)
            .order_by(OwnerTransaction.created_at.desc())
        )
        transactions = result.scalars().all()

        if not transactions:
            raise ValidationError("No ledger data available to export for this owner.")

        total_credit = sum(
            (t.amount or Decimal("0"))
            for t in transactions
            if t.type == OwnerTransactionType.CREDIT.value
        )

        total_debit = sum(
            (t.amount or Decimal("0"))
            for t in transactions
            if t.type == OwnerTransactionType.DEBIT.value
        )

        buffer = BytesIO()

        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()

        elements = []

        elements.append(Paragraph("OWNER LEDGER REPORT", styles["Title"]))
        elements.append(Spacer(1, 10))

        elements.append(Paragraph(f"Owner: {owner.owner_name}", styles["Normal"]))
        elements.append(Spacer(1, 10))

        elements.append(Paragraph(f"Total Credit: {total_credit}", styles["Normal"]))
        elements.append(Paragraph(f"Total Debit: {total_debit}", styles["Normal"]))
        elements.append(
            Paragraph(f"Balance: {total_credit - total_debit}", styles["Normal"])
        )
        elements.append(Spacer(1, 15))

        data = [["Date", "Type", "Amount", "Reference", "Description"]]

        for t in transactions:
            data.append(
                [
                    str(t.created_at),
                    t.type,
                    f"{(t.amount or Decimal('0')):.2f}",
                    f"{t.reference_type} ({t.reference_id})",
                    t.description or "",
                ]
            )

        table = Table(data)

        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ]
            )
        )

        elements.append(table)

        doc.build(elements)

        buffer.seek(0)

    except ValidationError:
        raise
    except Exception:
        logger.exception(f"PDF generation failed owner_id={owner_id}")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while generating ledger PDF",
        )

    logger.info(f"Ledger PDF generated owner_id={owner_id}")

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=owner_ledger_{owner_id}.pdf"
        },
    )


@router.get("/{owner_id}/ledger/excel")
async def export_owner_ledger_excel(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("owners.export")),
):
    logger.info(f"Generating ledger CSV owner_id={owner_id}")

    owner = await _get_scoped_owner(db, owner_id, current_user)

    try:
        result = await db.execute(
            select(OwnerTransaction)
            .where(OwnerTransaction.owner_id == owner.id)
            .order_by(OwnerTransaction.created_at.desc())
        )
        transactions = result.scalars().all()

        if not transactions:
            raise ValidationError("No ledger data available to export for this owner.")

        string_buffer = StringIO()
        writer = csv.writer(string_buffer)

        writer.writerow(
            ["Date", "Type", "Amount", "Reference Type", "Reference ID", "Description"]
        )

        for t in transactions:
            writer.writerow(
                [
                    str(t.created_at),
                    t.type,
                    f"{(t.amount or Decimal('0')):.2f}",
                    t.reference_type,
                    t.reference_id,
                    t.description or "",
                ]
            )

        byte_buffer = BytesIO()
        byte_buffer.write(string_buffer.getvalue().encode("utf-8"))
        byte_buffer.seek(0)

    except ValidationError:
        raise
    except Exception:
        logger.exception(f"CSV generation failed owner_id={owner_id}")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while generating ledger CSV",
        )

    logger.info(f"Ledger CSV generated owner_id={owner_id}")

    return StreamingResponse(
        byte_buffer,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=owner_ledger_{owner_id}.csv"
        },
    )
