from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.owner import Owner, OwnerTransaction, OwnerPaymentSchedule
from app.models.project import Project
from app.models.invoice import Invoice
from sqlalchemy import select, func
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
from app.utils.helpers import NotFoundError, ValidationError
from fastapi.responses import StreamingResponse
from io import BytesIO , StringIO
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
import csv
from fastapi.responses import StreamingResponse
from app.core.logger import logger
from app.utils.common import generate_business_id
from sqlalchemy.exc import IntegrityError

router = APIRouter(
    prefix="/owners",
    tags=["owners"],
)


@router.post("", response_model=OwnerOut)
async def create_owner(
    payload: OwnerCreate,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Creating owner name={payload.owner_name}")

    data = payload.model_dump()

    for _ in range(3):
        try:
            data["owner_code"] = await generate_business_id(
                db, Owner, "owner_code", "OWN"
            )

            obj = Owner(**data)

            db.add(obj)
            await db.flush()
            await db.refresh(obj)

            logger.info(f"Owner created id={obj.id}")

            return OwnerOut.model_validate(obj)

        except IntegrityError:
            await db.rollback()
            logger.warning("Retrying owner creation due to duplicate owner_code")

    raise Exception("Failed to create owner with unique owner_code")


@router.get("", response_model=list[OwnerOut])
async def list_owners(
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_session),
):
    query = select(Owner)

    if search:
        query = query.where(Owner.owner_name.ilike(f"%{search}%"))

    result = await db.execute(query)
    owners = result.scalars().all()

    return [OwnerOut.model_validate(o) for o in owners]


@router.get("/portfolio", response_model=ClientPortfolioResponse)
async def get_client_portfolio(
    db: AsyncSession = Depends(get_db_session),
):
    """
    Returns an aggregated view of clients (owners) with their project counts,
    billing status, and financial history as shown in the Client Portfolio design.
    """
    logger.info("Fetching client portfolio summary")

    # 1. Fetch all owners
    owners_result = await db.execute(select(Owner))
    owners = owners_result.scalars().all()

    portfolio_items = []
    total_outstanding = 0.0

    for owner in owners:

        # 2. Get project stats for this owner
        project_stats = await db.execute(
            select(func.count(Project.id), func.max(Project.project_name))
            .where(Project.owner_id == owner.id)
        )

        total_projects, latest_project = project_stats.one()

        # 3. Get financial stats for this owner
        financial_stats = await db.execute(
            select(
                func.sum(Invoice.pending_amount),
                func.sum(Invoice.paid_amount)
            )
            .where(Invoice.owner_id == owner.id)
        )

        pending_billing, total_received = financial_stats.one()

        pending_val = float(pending_billing or 0)
        received_val = float(total_received or 0)

        total_outstanding += pending_val

        # --- AUTO SATISFACTION CALCULATION ---

        score = 100

        # delayed projects penalty
        delayed_projects = await db.scalar(
            select(func.count(Project.id))
            .where(
                Project.owner_id == owner.id,
                Project.status == "Delayed"
            )
        )

        score -= int(delayed_projects or 0) * 10

        # overdue payment milestones penalty
        overdue_payments = await db.scalar(
            select(func.count(OwnerPaymentSchedule.id))
            .where(
                OwnerPaymentSchedule.owner_id == owner.id,
                OwnerPaymentSchedule.status != "Paid",
                OwnerPaymentSchedule.due_date < date.today()
            )
        )

        score -= int(overdue_payments or 0) * 5

        # completed projects bonus
        completed_projects = await db.scalar(
            select(func.count(Project.id))
            .where(
                Project.owner_id == owner.id,
                Project.status == "Completed"
            )
        )

        score += int(completed_projects or 0) * 2

        # high pending billing penalty
        if pending_val > 500000:
            score -= 10

        # no projects = no score
        if not total_projects:
            score = 0

        # clamp final value
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
                status="ACTIVE" if total_projects and total_projects > 0 else "INACTIVE",
            )
        )

    # 4. Calculate Average Satisfaction
    total_score = sum(item.satisfaction_score for item in portfolio_items)
    avg_satisfaction = (
        total_score / len(portfolio_items)
        if portfolio_items else 0.0
    )

    summary = ClientPortfolioSummary(
        total_clients=len(owners),
        total_outstanding_billing=total_outstanding,
        average_satisfaction_score=round(avg_satisfaction, 2),
    )

    return ClientPortfolioResponse(summary=summary, items=portfolio_items)


# =========================
# PAYMENT TRACKER (NEW)
# =========================

@router.get("/payment-tracker", response_model=list[OwnerPaymentScheduleOut])
async def get_all_payments_tracker(
    owner_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Returns a global view of all owner payments/milestones as seen in the Payment Tracker design.
    """
    query = select(OwnerPaymentSchedule)

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
):
    obj = OwnerPaymentSchedule(**payload.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.get("/{owner_id}", response_model=OwnerOut)
async def get_owner(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(select(Owner).where(Owner.id == owner_id))

    if not obj:
        raise NotFoundError("Owner not found")

    return OwnerOut.model_validate(obj)


@router.put("/{owner_id}", response_model=OwnerOut)
async def update_owner(
    owner_id: int,
    payload: OwnerUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating owner id={owner_id}")

    obj = await db.scalar(select(Owner).where(Owner.id == owner_id))

    if not obj:
        logger.warning(f"Owner not found id={owner_id}")
        raise NotFoundError("Owner not found")

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    try:
        await db.flush()
        await db.refresh(obj)
    except IntegrityError:
        await db.rollback()
        logger.warning(f"Owner update failed duplicate mobile id={owner_id}")
        raise ValidationError("Mobile number already exists")
    except Exception:
        await db.rollback()
        logger.exception(f"Owner update failed id={owner_id}")
        raise

    logger.info(f"Owner updated id={owner_id}")

    return OwnerOut.model_validate(obj)


@router.delete("/{owner_id}", status_code=204)
async def delete_owner(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Deleting owner id={owner_id}")

    obj = await db.scalar(select(Owner).where(Owner.id == owner_id))

    if not obj:
        logger.warning(f"Owner not found id={owner_id}")
        raise NotFoundError("Owner not found")

    try:
        await db.delete(obj)
        await db.flush()
    except Exception:
        await db.rollback()
        logger.exception(f"Owner delete failed id={owner_id}")
        raise

    logger.info(f"Owner deleted id={owner_id}")

    return None


@router.get("/{owner_id}/payments")
async def get_owner_payments(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    owner = await db.get(Owner, owner_id)
    if not owner:
        raise NotFoundError("Owner not found")

    result = await db.execute(
        select(OwnerTransaction).where(OwnerTransaction.owner_id == owner_id)
    )
    rows = result.scalars().all()

    return [OwnerTransactionOut.model_validate(r) for r in rows]


@router.get("/{owner_id}/ledger", response_model=OwnerLedgerResponse)
async def get_owner_ledger(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    owner = await db.get(Owner, owner_id)
    if not owner:
        raise NotFoundError("Owner not found")

    result = await db.execute(
        select(OwnerTransaction).where(OwnerTransaction.owner_id == owner_id)
    )
    transactions = result.scalars().all()

    total_credit = sum(float(t.amount) for t in transactions if t.type == "credit")
    total_debit = sum(float(t.amount) for t in transactions if t.type == "debit")

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
):
    logger.info(f"Generating ledger PDF owner_id={owner_id}")

    owner = await db.get(Owner, owner_id)
    if not owner:
        logger.warning(f"Owner not found id={owner_id}")
        raise NotFoundError("Owner not found")

    try:
        result = await db.execute(
            select(OwnerTransaction)
            .where(OwnerTransaction.owner_id == owner_id)
            .order_by(OwnerTransaction.created_at.desc())
        )
        transactions = result.scalars().all()

        total_credit = sum(float(t.amount) for t in transactions if t.type == "credit")
        total_debit = sum(float(t.amount) for t in transactions if t.type == "debit")

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
        elements.append(Paragraph(f"Balance: {total_credit - total_debit}", styles["Normal"]))
        elements.append(Spacer(1, 15))

        data = [["Date", "Type", "Amount", "Reference", "Description"]]

        for t in transactions:
            data.append([
                str(t.created_at),
                t.type,
                float(t.amount),
                f"{t.reference_type} ({t.reference_id})",
                t.description or ""
            ])

        table = Table(data)

        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ]))

        elements.append(table)

        doc.build(elements)

        buffer.seek(0)

    except Exception:
        logger.exception(f"PDF generation failed owner_id={owner_id}")
        raise

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
):
    logger.info(f"Generating ledger CSV owner_id={owner_id}")

    owner = await db.get(Owner, owner_id)
    if not owner:
        logger.warning(f"Owner not found id={owner_id}")
        raise NotFoundError("Owner not found")

    try:
        result = await db.execute(
            select(OwnerTransaction)
            .where(OwnerTransaction.owner_id == owner_id)
            .order_by(OwnerTransaction.created_at.desc())
        )
        transactions = result.scalars().all()

        string_buffer = StringIO()
        writer = csv.writer(string_buffer)

        writer.writerow([
            "Date",
            "Type",
            "Amount",
            "Reference Type",
            "Reference ID",
            "Description"
        ])

        for t in transactions:
            writer.writerow([
                str(t.created_at),
                t.type,
                float(t.amount),
                t.reference_type,
                t.reference_id,
                t.description or "",
            ])

        byte_buffer = BytesIO()
        byte_buffer.write(string_buffer.getvalue().encode("utf-8"))
        byte_buffer.seek(0)

    except Exception:
        logger.exception(f"CSV generation failed owner_id={owner_id}")
        raise

    logger.info(f"Ledger CSV generated owner_id={owner_id}")

    return StreamingResponse(
        byte_buffer,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=owner_ledger_{owner_id}.csv"
        },
    )