from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_current_active_user, require_roles
from app.models.expense import Expense
from app.models.final_measurement import FinalMeasurement
from app.models.labour import Labour, LabourAttendance
from app.models.project import Project, Task
from app.db.session import get_db_session
from app.models.invoice import Invoice, Transaction
from app.models.owner import OwnerTransaction
from app.models.user import User
from app.schemas.invoice import (
    AnalyticsSummaryOut,
    InvoiceCreate,
    InvoiceUpdate,
    InvoiceOut,
    LabourInvoiceCreate,
)
from app.utils.common import assert_project_access
from app.utils.helpers import NotFoundError, ValidationError
from decimal import Decimal
from fastapi.responses import StreamingResponse
from io import BytesIO
from app.core.logger import logger


from app.models.user import UserRole

INVOICE_READ_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.ACCOUNTANT,
        UserRole.SITE_ENGINEER,
        UserRole.CLIENT,
    ]
]

INVOICE_WRITE_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.ACCOUNTANT,
    ]
]

router = APIRouter(prefix="/invoices", tags=["invoices"])

from sqlalchemy import and_


# @router.post("/{type}", response_model=InvoiceOut)
# async def create_invoice(
#     type: str,
#     payload: InvoiceCreate,
#     db: AsyncSession = Depends(get_db_session),
# ):
#     logger.info(f"Creating invoice type={type} project_id={payload.project_id}")

#     data = payload.model_dump()

#     project = await db.get(Project, data["project_id"])
#     if not project:
#         logger.warning(f"Project not found id={data['project_id']}")
#         raise NotFoundError("Project not found")

#     if type == "owner":
#         raise ValidationError("Use /from-measurement API for owner invoice")

#     allowed_types = ["contractor", "labour", "material"]
#     if type not in allowed_types:
#         raise ValidationError("Invalid invoice type")

#     if type == "owner":
#         existing_invoice = await db.scalar(
#             select(Invoice).where(
#                 Invoice.project_id == data["project_id"],
#                 Invoice.type == "owner",
#             )
#         )
#         if existing_invoice:
#             raise ValidationError("Owner invoice already exists")

#     try:
#         if type == "owner":
#             measurement = await db.scalar(
#                 select(FinalMeasurement).where(
#                     FinalMeasurement.project_id == data["project_id"]
#                 )
#             )

#             if not measurement:
#                 raise NotFoundError("Final measurement not found for project")

#             base_amount = Decimal(measurement.total_amount)
#         else:
#             base_amount = Decimal(data["amount"])

#         gst_percent = Decimal(data.get("gst_percent", 0))
#         tax_percent = Decimal(data.get("tax_percent", 0))

#         gst_amount = (base_amount * gst_percent) / Decimal(100)
#         tax_amount = (base_amount * tax_percent) / Decimal(100)

#         total_amount = base_amount + gst_amount + tax_amount

#         obj = Invoice(
#             project_id=data["project_id"],
#             owner_id=project.owner_id,
#             type=type,
#             reference_id=data.get("reference_id"),
#             amount=base_amount,
#             gst_percent=gst_percent,
#             gst_amount=gst_amount,
#             tax_percent=tax_percent,
#             tax_amount=tax_amount,
#             total_amount=total_amount,
#             description=data.get("description"),

#             paid_amount=Decimal(0),
#             pending_amount=total_amount,
#             status="pending",
#         )

#         db.add(obj)
#         await db.flush()

#         owner_transaction = OwnerTransaction(
#             owner_id=project.owner_id,
#             project_id=obj.project_id,
#             type="credit",
#             amount=total_amount,
#             reference_type="invoice",
#             reference_id=obj.id,
#             description=f"{type} invoice created",
#         )

#         db.add(owner_transaction)

#         await db.commit()

#     except Exception:
#         await db.rollback()
#         logger.exception(f"Invoice creation failed type={type}")
#         raise

#     await db.refresh(obj)

#     logger.info(f"Invoice created id={obj.id}")

#     return InvoiceOut.model_validate(obj)


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
):
    rows = (await db.execute(select(Invoice))).scalars().all()
    return [InvoiceOut.model_validate(r) for r in rows]


from datetime import datetime, time


@router.get("/date-range")
async def get_by_date_range(
    start: date,
    end: date,
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)  # 🔥 FULL DAY

    rows = (
        (
            await db.execute(
                select(Invoice).where(Invoice.created_at.between(start_dt, end_dt))
            )
        )
        .scalars()
        .all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


@router.get("/{id}", response_model=InvoiceOut)
async def get_invoice(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
):
    obj = await db.get(Invoice, id)

    if not obj:
        raise NotFoundError("Invoice not found")

    return InvoiceOut.model_validate(obj)


@router.put("/{id}", response_model=InvoiceOut)
async def update_invoice(
    id: int,
    payload: InvoiceUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_WRITE_ROLES)),
):
    logger.info(f"Updating invoice id={id}")

    obj = await db.get(Invoice, id)

    if not obj:
        logger.warning(f"Invoice not found id={id}")
        raise NotFoundError("Invoice not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    amount = Decimal(obj.amount or 0)
    gst_percent = Decimal(obj.gst_percent or 0)
    tax_percent = Decimal(obj.tax_percent or 0)

    obj.gst_amount = (amount * gst_percent) / 100
    obj.tax_amount = (amount * tax_percent) / 100
    obj.total_amount = amount + obj.gst_amount + obj.tax_amount

    obj.pending_amount = obj.total_amount - (obj.paid_amount or 0)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Invoice update failed id={id}")
        raise

    await db.refresh(obj)

    logger.info(f"Invoice updated id={id}")

    return InvoiceOut.model_validate(obj)


@router.delete("/{id}", status_code=204)
async def delete_invoice(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_WRITE_ROLES)),
):
    logger.info(f"Deleting invoice id={id}")

    obj = await db.get(Invoice, id)

    if not obj:
        logger.warning(f"Invoice not found id={id}")
        raise NotFoundError("Invoice not found")

    try:
        await db.delete(obj)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Invoice delete failed id={id}")
        raise

    logger.info(f"Invoice deleted id={id}")

    return None


@router.get("/project/{project_id}")
async def get_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
):
    rows = (
        (await db.execute(select(Invoice).where(Invoice.project_id == project_id)))
        .scalars()
        .all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


@router.get("/type/{type}")
async def get_by_type(
    type: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
):
    rows = (
        (await db.execute(select(Invoice).where(Invoice.type == type))).scalars().all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


@router.post("/{id}/mark-paid")
async def mark_paid(
    id: int,
    current_user: User = Depends(require_roles(INVOICE_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    invoice = await db.get(Invoice, id)

    if not invoice:
        raise NotFoundError("Invoice not found")

    if invoice.pending_amount <= 0:
        raise ValidationError("Already paid")

    remaining = invoice.pending_amount

    # Create transaction for remaining amount
    txn = Transaction(
        project_id=invoice.project_id,
        invoice_id=invoice.id,
        type="receipt",
        amount=remaining,
        mode="system",
        reference="auto-mark-paid",
        created_by=current_user.id,
    )

    db.add(txn)

    # Update invoice correctly
    invoice.paid_amount += remaining
    invoice.pending_amount = 0
    invoice.status = "paid"

    await db.commit()

    return {
        "message": "Invoice marked as paid",
        "paid": float(invoice.paid_amount),
        "pending": float(invoice.pending_amount),
        "status": invoice.status,
    }


from io import BytesIO
from fastapi.responses import StreamingResponse
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


@router.get("/{id}/pdf")
async def generate_invoice_pdf(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
):

    obj = await db.get(Invoice, id)
    if not obj:
        raise NotFoundError("Invoice not found")

    #  Register Unicode font (₹ support)
    pdfmetrics.registerFont(TTFont("DejaVu", "app/fonts/DejaVuSans.ttf"))

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer)

    styles = getSampleStyleSheet()

    #  Apply font to ALL styles
    for style in styles.byName.values():
        style.fontName = "DejaVu"

    elements = []

    # Title
    elements.append(Paragraph(f"Invoice #{obj.id}", styles["Title"]))
    elements.append(Spacer(1, 12))

    # Details
    elements.append(Paragraph(f"Type: {obj.type}", styles["Normal"]))
    elements.append(Paragraph(f"Amount: ₹{float(obj.amount):,.2f}", styles["Normal"]))
    elements.append(Paragraph(f"GST: ₹{float(obj.gst_amount):,.2f}", styles["Normal"]))
    elements.append(Paragraph(f"Tax: ₹{float(obj.tax_amount):,.2f}", styles["Normal"]))
    elements.append(
        Paragraph(f"Total: ₹{float(obj.total_amount):,.2f}", styles["Normal"])
    )

    #  Fixed Status
    elements.append(
        Paragraph(f"Status: {obj.status.value.capitalize()}", styles["Normal"])
    )

    doc.build(elements)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=invoice_{obj.id}.pdf"},
    )


@router.post("/labour", response_model=InvoiceOut)
async def create_labour_invoice(
    payload: LabourInvoiceCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_WRITE_ROLES)),
):
    # 0. Validate dates
    if payload.end_date < payload.start_date:
        raise ValidationError("end_date must be >= start_date")

    project_id = payload.project_id
    start_date = payload.start_date
    end_date = payload.end_date

    # 1. Project check
    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project not found")

    description = f"Labour invoice ({start_date} to {end_date})"

    # 2. Prevent duplicate invoice for same range
    existing_invoice = await db.scalar(
        select(Invoice).where(
            Invoice.project_id == project_id,
            Invoice.type == "labour",
            Invoice.description == description,
        )
    )
    if existing_invoice:
        raise ValidationError("Labour invoice already exists for this date range")

    # 3. Fetch attendances
    result = await db.execute(
        select(LabourAttendance).where(
            LabourAttendance.project_id == project_id,
            LabourAttendance.attendance_date.between(start_date, end_date),
        )
    )
    attendances = result.scalars().all()

    if not attendances:
        raise NotFoundError("No labour attendance found")

    # 4. FIX: Load all labours in ONE query (avoid N+1)
    labour_ids = list({att.labour_id for att in attendances if att.labour_id})
    labours_result = await db.execute(select(Labour).where(Labour.id.in_(labour_ids)))
    labour_map = {l.id: l for l in labours_result.scalars().all()}

    total_amount = Decimal(0)
    attendance_ids: list[int] = []

    # 5. Calculate wages
    for att in attendances:
        labour = labour_map.get(att.labour_id)
        if not labour:
            continue

        attendance_ids.append(att.id)

        daily_rate = Decimal(labour.daily_wage_rate or 0)
        working_hours = Decimal(att.working_hours or 0)
        overtime_rate = Decimal(att.overtime_rate or 0)
        overtime_hours = Decimal(att.overtime_hours or 0)

        wage = daily_rate * working_hours + overtime_rate * overtime_hours
        total_amount += wage

    try:
        # 6. Create invoice
        obj = Invoice(
            project_id=project_id,
            owner_id=project.owner_id,
            type="labour",
            reference_id=None,
            linked_expense_ids=attendance_ids,
            amount=total_amount,
            gst_percent=Decimal(0),
            gst_amount=Decimal(0),
            tax_percent=Decimal(0),
            tax_amount=Decimal(0),
            total_amount=total_amount,
            paid_amount=Decimal(0),
            pending_amount=total_amount,
            status="pending",
            description=description,
        )

        db.add(obj)
        await db.flush()

        # 7. Owner ledger entry
        owner_txn = OwnerTransaction(
            owner_id=project.owner_id,
            project_id=project_id,
            type="credit",
            amount=total_amount,
            reference_type="invoice",
            reference_id=obj.id,
            description="Labour invoice generated",
        )

        db.add(owner_txn)

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    await db.refresh(obj)

    return InvoiceOut.model_validate(obj)


@router.post("/material", response_model=InvoiceOut)
async def create_material_invoice(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_WRITE_ROLES)),
):
    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project not found")

    result = await db.execute(
        select(Expense).where(
            Expense.project_id == project_id,
            Expense.category == "Material",
        )
    )
    expenses = result.scalars().all()

    if not expenses:
        raise NotFoundError("No material expenses found")

    total_amount = sum(Decimal(e.amount or 0) for e in expenses)
    expense_ids = [e.id for e in expenses]

    try:
        obj = Invoice(
            project_id=project_id,
            owner_id=project.owner_id,
            type="material",
            reference_id=None,
            linked_expense_ids=expense_ids,
            amount=total_amount,
            gst_percent=Decimal(0),
            gst_amount=Decimal(0),
            tax_percent=Decimal(0),
            tax_amount=Decimal(0),
            total_amount=total_amount,
            paid_amount=Decimal(0),
            pending_amount=total_amount,
            status="pending",
            description="Material invoice (aggregated)",
        )

        db.add(obj)
        await db.flush()

        owner_txn = OwnerTransaction(
            owner_id=project.owner_id,
            project_id=project_id,
            type="credit",
            amount=total_amount,
            reference_type="invoice",
            reference_id=obj.id,
            description="Material invoice generated",
        )

        db.add(owner_txn)

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    await db.refresh(obj)

    return InvoiceOut.model_validate(obj)


@router.post("/from-measurement/{measurement_id}", response_model=InvoiceOut)
async def create_invoice_from_measurement(
    measurement_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_WRITE_ROLES)),
):
    logger.info(f"Creating owner invoice from measurement_id={measurement_id}")

    # 1. Get measurement
    measurement = await db.get(FinalMeasurement, measurement_id)
    if not measurement:
        raise NotFoundError("Measurement not found")

    # 2. Get project
    project = await db.get(Project, measurement.project_id)
    if not project:
        raise NotFoundError("Project not found")

    # 3. Check existing owner invoice
    existing_invoice = await db.scalar(
        select(Invoice).where(
            Invoice.project_id == measurement.project_id,
            Invoice.type == "owner",
        )
    )
    if existing_invoice:
        raise ValidationError("Owner invoice already exists")

    try:
        total_amount = Decimal(measurement.total_amount)

        # 4. Create invoice
        obj = Invoice(
            project_id=measurement.project_id,
            owner_id=project.owner_id,
            type="owner",
            reference_id=measurement.id,  #  link to measurement
            amount=total_amount,
            gst_percent=Decimal(0),
            gst_amount=Decimal(0),
            tax_percent=Decimal(0),
            tax_amount=Decimal(0),
            total_amount=total_amount,
            paid_amount=Decimal(0),
            pending_amount=total_amount,
            status="pending",
            description="Invoice from final measurement",
        )

        db.add(obj)
        await db.flush()

        # 5. Owner ledger entry
        owner_txn = OwnerTransaction(
            owner_id=project.owner_id,
            project_id=measurement.project_id,
            type="credit",
            amount=total_amount,
            reference_type="invoice",
            reference_id=obj.id,
            description="Measurement invoice generated",
        )

        db.add(owner_txn)

        await db.commit()

    except Exception:
        await db.rollback()
        logger.exception("Owner invoice creation failed")
        raise

    await db.refresh(obj)

    logger.info(f"Owner invoice created id={obj.id}")

    return InvoiceOut.model_validate(obj)


@router.get("/project/{project_id}/summary")
async def payment_summary(
    project_id: int,
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    paid = await db.scalar(
        select(func.sum(Invoice.paid_amount)).where(Invoice.project_id == project_id)
    )

    pending = await db.scalar(
        select(func.sum(Invoice.pending_amount)).where(Invoice.project_id == project_id)
    )

    return {
        "paid": float(paid or 0),
        "pending": float(pending or 0),
    }


@router.get("/analytics/summary", response_model=AnalyticsSummaryOut)
async def analytics_summary(
    project_id: int,
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    # 1. Progress (task-based)
    progress = await db.scalar(
        select(func.avg(Task.completion_percentage)).where(
            Task.project_id == project_id
        )
    )

    # 2. Revenue (owner invoices)
    total_revenue = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.type == "owner",
        )
    )

    # 3. Expense (labour + material)
    total_expense = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.type.in_(["labour", "material"]),
        )
    )

    # 4. Paid amount (for financial progress)
    total_paid = await db.scalar(
        select(func.sum(Invoice.paid_amount)).where(Invoice.project_id == project_id)
    )

    #  Convert safely (important for Decimal)
    total_revenue_val = float(total_revenue or 0)
    total_paid_val = float(total_paid or 0)

    # 5. Financial progress
    financial_progress = (
        (total_paid_val / total_revenue_val * 100) if total_revenue_val > 0 else 0
    )

    return AnalyticsSummaryOut(
        progress_percent=round(float(progress or 0), 2),
        financial_progress_percent=round(financial_progress, 2),
        total_expense=float(total_expense or 0),
        total_revenue=total_revenue_val,
    )


@router.post("/{id}/pay")
async def pay_invoice(
    id: int,
    amount: Decimal,
    mode: str,
    reference: str | None = None,
    current_user: User = Depends(require_roles(INVOICE_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    invoice = await db.get(Invoice, id)

    if not invoice:
        raise NotFoundError("Invoice not found")

    if amount <= 0:
        raise ValidationError("Invalid payment amount")

    if invoice.pending_amount <= 0:
        raise ValidationError("Invoice already fully paid")

    if amount > invoice.pending_amount:
        raise ValidationError("Amount exceeds pending")

    # 1. Create transaction
    txn = Transaction(
        project_id=invoice.project_id,
        invoice_id=invoice.id,
        type="receipt",
        amount=amount,
        mode=mode,
        reference=reference or f"inv:{invoice.id}",
        created_by=current_user.id,
    )
    db.add(txn)

    # 2. Update invoice
    invoice.paid_amount += amount
    invoice.pending_amount = invoice.total_amount - invoice.paid_amount

    if invoice.pending_amount == 0:
        invoice.status = "paid"
    else:
        invoice.status = "partial"

    await db.commit()

    return {
        "message": "Payment recorded",
        "paid": float(invoice.paid_amount),
        "pending": float(invoice.pending_amount),
        "status": invoice.status,
    }


@router.get("/{id}/transactions")
async def invoice_transactions(
    id: int,
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(select(Transaction).where(Transaction.invoice_id == id))
    return result.scalars().all()


@router.get("/receivables/summary")
async def receivable_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
):
    total = await db.scalar(select(func.sum(Invoice.total_amount)))
    paid = await db.scalar(select(func.sum(Invoice.paid_amount)))
    pending = await db.scalar(select(func.sum(Invoice.pending_amount)))

    return {
        "total": float(total or 0),
        "paid": float(paid or 0),
        "pending": float(pending or 0),
    }


@router.get("/receivables/aging")
async def receivable_aging(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(INVOICE_READ_ROLES)),
):
    today = date.today()

    rows = (await db.execute(select(Invoice))).scalars().all()

    result = {"0-30": 0, "30-60": 0, "60+": 0}

    for inv in rows:
        if not inv.pending_amount or inv.pending_amount <= 0:
            continue

        days = (today - inv.created_at.date()).days

        if days <= 30:
            result["0-30"] += float(inv.pending_amount)
        elif days <= 60:
            result["30-60"] += float(inv.pending_amount)
        else:
            result["60+"] += float(inv.pending_amount)

    return result
