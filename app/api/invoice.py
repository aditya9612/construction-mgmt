import csv
from datetime import date, datetime, time
from decimal import Decimal
import io
from io import BytesIO
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.boq import assert_project_access_masked
from app.core.dependencies import get_current_active_user, require_permission
from app.core.enums import (
    AttendanceStatus,
    InvoiceStatus,
    PaymentMode,
    InvoiceType,
    InvoiceSourceType,
)
from app.core.logger import logger
from app.db.session import get_db_session
from app.models.accountant import JournalEntry, JournalLine, Account
from app.models.billing import RABill
from app.models.expense import Expense
from app.models.final_measurement import FinalMeasurement
from app.models.invoice import Invoice, Transaction
from app.models.labour import Labour
from app.models.notification import Notification
from app.models.owner import Owner, OwnerTransaction
from app.models.project import Project, ProjectMember, Task
from app.models.quotation import QuotationMaster, QuotationStatus
from app.models.user import User, ActivityLog, UserAttendance, UserRole
import app.schemas.invoice as s
from app.schemas.invoice import (
    AnalyticsSummaryOut,
    ClientLedgerResponse,
    ClientLedgerTransactionOut,
    CollectionOut,
    CreateInvoice,
    InvoiceCreate,
    InvoiceOut,
    InvoiceUpdate,
    LabourInvoiceCreate,
    ManualReceivableCreate,
    ReceivablesSummaryOut,
    SendInvoiceRequest,
    SendInvoiceResponse,
)
from app.utils.accounting import (
    get_accounts_receivable,
    get_primary_cash_account,
    get_revenue_account,
)
from app.utils.common import assert_project_access, create_system_alert
from app.utils.helpers import InvalidStateError, NotFoundError, PermissionDeniedError, ValidationError

# Legacy role definitions kept for backward compatibility; active route decisions use DB-driven permissions.
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

PAYMENT_ROLES = INVOICE_WRITE_ROLES + [UserRole.CLIENT.value]

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _is_super_admin(user: User) -> bool:
    return getattr(user, "is_super_admin", False) is True


async def _get_invoice_or_404(
    db: AsyncSession,
    *,
    invoice_id: int,
    current_user: User,
) -> tuple[Invoice, Project]:
    """Resolve Invoice -> Project -> Company ownership chain and verify access.

    Masks foreign/nonexistent/inaccessible invoices as 404 Not Found.
    """
    invoice = await db.scalar(select(Invoice).where(Invoice.id == invoice_id))
    if not invoice:
        raise NotFoundError("Invoice not found")

    is_super = _is_super_admin(current_user)
    if not is_super and current_user.company_id is not None:
        if invoice.company_id is not None and invoice.company_id != current_user.company_id:
            raise NotFoundError("Invoice not found")

    try:
        project = await assert_project_access_masked(
            db, project_id=invoice.project_id, current_user=current_user
        )
    except (NotFoundError, PermissionDeniedError):
        raise NotFoundError("Invoice not found")

    if not is_super and current_user.company_id is not None and project.company_id != current_user.company_id:
        raise NotFoundError("Invoice not found")

    return invoice, project


# ------------------ 1. CREATE MANUAL INVOICE ------------------


@router.post("", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: CreateInvoice,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.create")),
):
    """Create a new manual invoice."""
    # Validate Project with tenant/project access masking
    project = await assert_project_access_masked(
        db, project_id=payload.project_id, current_user=current_user
    )

    # Validate Owner
    if project.owner_id != payload.owner_id:
        raise ValidationError("Owner does not belong to this project")

    # Validate Client Assignment
    client_member = await db.scalar(
        select(ProjectMember)
        .join(User, User.id == ProjectMember.user_id)
        .where(
            ProjectMember.project_id == payload.project_id,
            User.role == UserRole.CLIENT.value,
            User.is_active == True,
            User.is_deleted == False,
        )
    )
    if not client_member:
        raise ValidationError("No active client assigned to this project.")

    # Validate Amount
    if payload.amount <= 0:
        raise ValidationError("Invoice amount must be greater than zero.")

    # Duplicate Invoice Check
    duplicate_invoice = await db.scalar(
        select(Invoice).where(
            Invoice.project_id == payload.project_id,
            Invoice.owner_id == payload.owner_id,
            Invoice.description == payload.description,
            Invoice.status != InvoiceStatus.CANCELLED,
        )
    )
    if duplicate_invoice:
        raise ValidationError("Similar invoice already exists.")

    # GST Calculation
    amount = Decimal(str(payload.amount))
    gst_percent = Decimal(str(payload.gst_percent or 0))
    tax_percent = Decimal(str(payload.tax_percent or 0))

    gst_amount = (amount * gst_percent) / Decimal("100")
    tax_amount = (amount * tax_percent) / Decimal("100")
    total_amount = amount + gst_amount - tax_amount

    invoice = Invoice(
        company_id=project.company_id or current_user.company_id,
        project_id=payload.project_id,
        owner_id=payload.owner_id,
        quotation_id=None,
        type=InvoiceType.OWNER,
        source_type=InvoiceSourceType.MANUAL,
        reference_id=None,
        amount=amount,
        gst_percent=gst_percent,
        gst_amount=gst_amount,
        tax_percent=tax_percent,
        tax_amount=tax_amount,
        total_amount=total_amount,
        paid_amount=Decimal("0"),
        pending_amount=total_amount,
        status=InvoiceStatus.PENDING,
        description=payload.description,
        invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        party_gstin=payload.party_gstin,
        cgst=payload.cgst,
        sgst=payload.sgst,
        igst=payload.igst,
        invoice_copy_url=payload.invoice_copy_url,
        gst_document_url=payload.gst_document_url,
    )

    try:
        db.add(invoice)
        await db.flush()

        owner_txn = OwnerTransaction(
            owner_id=payload.owner_id,
            project_id=payload.project_id,
            type="credit",
            amount=total_amount,
            reference_type="invoice",
            reference_id=invoice.id,
            description=f"Manual Invoice #{invoice.id}",
        )
        db.add(owner_txn)

        db.add(
            ActivityLog(
                action="INVOICE_CREATED",
                entity="invoice",
                entity_id=invoice.id,
                performed_by=current_user.id,
                details={
                    "invoice_id": invoice.id,
                    "project_id": payload.project_id,
                    "owner_id": payload.owner_id,
                    "client_user_id": client_member.user_id,
                    "amount": float(total_amount),
                    "source": "manual",
                },
            )
        )

        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to create invoice")
        raise

    await db.refresh(invoice)
    return InvoiceOut.model_validate(invoice)


# ------------------ 2. CREATE FROM QUOTATION ------------------


@router.post("/from-quotation/{quotation_id}", response_model=InvoiceOut)
async def create_invoice_from_quotation(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.create")),
):
    # 1. Get quotation
    quotation = await db.get(QuotationMaster, quotation_id)
    if not quotation or not getattr(quotation, "project_id", None):
        raise NotFoundError("Quotation not found")

    # Verify project access and company ownership
    try:
        project = await assert_project_access_masked(
            db, project_id=quotation.project_id, current_user=current_user
        )
    except (NotFoundError, PermissionDeniedError):
        raise NotFoundError("Quotation not found")

    # 2. Must be approved
    if not quotation.is_approved:
        raise ValidationError("Quotation must be approved first")

    # 3. Prevent duplicate conversion
    if quotation.converted_to_invoice:
        raise ValidationError("Quotation already converted to invoice")

    # 6. Additional safety check
    existing_invoice = await db.scalar(
        select(Invoice).where(Invoice.quotation_id == quotation.id)
    )

    if existing_invoice:
        raise ValidationError("Invoice already exists for this quotation")

    # 7. Calculate GST % (combine CGST + SGST)
    gst_percent = Decimal((quotation.cgst_percent or 0) + (quotation.sgst_percent or 0))

    # 8. Create invoice
    invoice = Invoice(
        company_id=project.company_id or current_user.company_id,
        project_id=quotation.project_id,
        owner_id=project.owner_id,
        quotation_id=quotation.id,
        type=InvoiceType.OWNER,
        source_type=InvoiceSourceType.QUOTATION,
        reference_id=quotation.id,
        amount=Decimal(quotation.subtotal or 0),
        gst_percent=gst_percent,
        gst_amount=Decimal(quotation.gst_amount or 0),
        tax_percent=Decimal(quotation.tds_percent or 0),
        tax_amount=Decimal(quotation.tds_amount or 0),
        total_amount=Decimal(quotation.grand_total or 0),
        paid_amount=Decimal(0),
        pending_amount=Decimal(quotation.grand_total or 0),
        status=InvoiceStatus.PENDING,
        description=f"Invoice generated from quotation {quotation.quotation_no}",
    )

    try:
        db.add(invoice)
        await db.flush()

        await _post_invoice_journal(db, invoice)

        # 9. Owner ledger entry
        owner_txn = OwnerTransaction(
            owner_id=project.owner_id,
            project_id=quotation.project_id,
            type="credit",
            amount=Decimal(quotation.grand_total or 0),
            reference_type="invoice",
            reference_id=invoice.id,
            description=f"Invoice generated from quotation {quotation.quotation_no}",
        )

        db.add(owner_txn)

        # 10. Update quotation
        quotation.converted_to_invoice = True
        quotation.status = QuotationStatus.CONVERTED

        # 11. Trigger Notification
        await create_system_alert(
            db=db,
            user_id=current_user.id,
            project_id=quotation.project_id,
            alert_type="invoice_generated",
            title="New Invoice Generated",
            message=(
                f"An invoice of Rs. {invoice.total_amount:,.2f} "
                f"has been generated for project {project.project_name}."
            ),
        )

        db.add(
            ActivityLog(
                action="INVOICE_GENERATED",
                entity="project",
                entity_id=quotation.project_id,
                performed_by=current_user.id,
                details={
                    "message": f"Invoice of Rs. {invoice.total_amount:,.2f} generated from QTN-{quotation.id}"
                },
            )
        )

        await db.commit()

    except Exception:
        await db.rollback()
        logger.exception(f"Failed to create invoice from quotation_id={quotation_id}")
        raise

    await db.refresh(invoice)

    logger.info(
        f"Invoice created from quotation_id={quotation_id}, invoice_id={invoice.id}"
    )

    return InvoiceOut.model_validate(invoice)


# ------------------ 3. LIST INVOICES ------------------


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.view")),
):
    is_super = _is_super_admin(current_user)
    if is_super:
        stmt = select(Invoice).where(Invoice.pending_amount > 0)
    else:
        stmt = select(Invoice).where(
            Invoice.pending_amount > 0,
            Invoice.company_id == current_user.company_id,
        )
    rows = (await db.execute(stmt)).scalars().all()
    return [InvoiceOut.model_validate(r) for r in rows]


# ------------------ 4. GET BY DATE RANGE ------------------


@router.get("/date-range")
async def get_by_date_range(
    start: date,
    end: date,
    current_user: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db_session),
):
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)

    is_super = _is_super_admin(current_user)
    stmt = select(Invoice).where(Invoice.created_at.between(start_dt, end_dt))
    if not is_super:
        stmt = stmt.where(Invoice.company_id == current_user.company_id)

    rows = (
        (await db.execute(stmt.order_by(Invoice.created_at.desc())))
        .scalars()
        .all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


# ------------------ 5. GET INVOICE ------------------


@router.get("/{id}", response_model=InvoiceOut)
async def get_invoice(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.view")),
):
    obj, _ = await _get_invoice_or_404(db, invoice_id=id, current_user=current_user)
    return InvoiceOut.model_validate(obj)


# ------------------ 6. UPDATE INVOICE ------------------


@router.put("/{id}", response_model=InvoiceOut)
async def update_invoice(
    id: int,
    payload: InvoiceUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.edit")),
):
    logger.info(f"Updating invoice id={id}")

    obj, _ = await _get_invoice_or_404(db, invoice_id=id, current_user=current_user)

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


# ------------------ 7. DELETE INVOICE ------------------


@router.delete("/{id}", status_code=204)
async def delete_invoice(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.delete")),
):
    logger.info(f"Deleting invoice id={id}")

    obj, _ = await _get_invoice_or_404(db, invoice_id=id, current_user=current_user)

    try:
        await db.delete(obj)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Invoice delete failed id={id}")
        raise

    logger.info(f"Invoice deleted id={id}")

    return None


# ------------------ 8. GET BY PROJECT ------------------


@router.get("/project/{project_id}")
async def get_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.view")),
):
    await assert_project_access_masked(db, project_id=project_id, current_user=current_user)
    is_super = _is_super_admin(current_user)
    stmt = select(Invoice).where(Invoice.project_id == project_id)
    if not is_super:
        stmt = stmt.where(Invoice.company_id == current_user.company_id)

    rows = (
        (await db.execute(stmt.order_by(Invoice.created_at.desc())))
        .scalars()
        .all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


# ------------------ 9. GET BY TYPE ------------------


@router.get("/type/{type}")
async def get_by_type(
    type: InvoiceType,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.view")),
):
    is_super = _is_super_admin(current_user)
    stmt = select(Invoice).where(Invoice.type == type)
    if not is_super:
        stmt = stmt.where(Invoice.company_id == current_user.company_id)

    rows = (
        (await db.execute(stmt.order_by(Invoice.created_at.desc())))
        .scalars()
        .all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


# ------------------ 10. MARK PAID ------------------


@router.post("/{id}/mark-paid")
async def mark_paid(
    id: int,
    current_user: User = Depends(require_permission("invoices.edit")),
    db: AsyncSession = Depends(get_db_session),
):
    invoice, project = await _get_invoice_or_404(db, invoice_id=id, current_user=current_user)

    if invoice.pending_amount <= 0:
        raise ValidationError("Already paid")

    remaining = invoice.pending_amount

    # Create transaction for remaining amount
    txn = Transaction(
        project_id=invoice.project_id,
        invoice_id=invoice.id,
        type="receipt",
        amount=remaining,
        mode=PaymentMode.ADJUSTMENT.value,
        reference="auto-mark-paid",
        created_by=current_user.id,
    )

    db.add(txn)

    # Update invoice correctly
    invoice.paid_amount += remaining
    invoice.pending_amount = 0
    invoice.status = InvoiceStatus.PAID

    if invoice.owner_id:
        await create_system_alert(
            db,
            invoice.owner_id,
            "Payment Received",
            f"Payment of ₹{remaining:,.2f} received for Invoice #{invoice.id}.",
            priority="Medium",
            category="Finance",
        )

    await db.commit()

    return {
        "message": "Invoice marked as paid",
        "paid": float(invoice.paid_amount),
        "pending": float(invoice.pending_amount),
        "status": invoice.status.value,
    }


# ------------------ 11. GENERATE INVOICE PDF ------------------


@router.get("/{id}/pdf")
async def generate_invoice_pdf(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.export")),
):
    obj, _ = await _get_invoice_or_404(db, invoice_id=id, current_user=current_user)

    # Register Unicode font (₹ support)
    pdfmetrics.registerFont(TTFont("DejaVu", "app/fonts/DejaVuSans.ttf"))

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer)

    styles = getSampleStyleSheet()

    # Apply font to ALL styles
    for style in styles.byName.values():
        style.fontName = "DejaVu"

    elements = []

    # Title
    elements.append(Paragraph(f"Invoice #{obj.id}", styles["Title"]))
    elements.append(Spacer(1, 12))

    # Details
    elements.append(Paragraph(f"Type: {obj.type.value}", styles["Normal"]))

    if obj.source_type:
        elements.append(Paragraph(f"Source: {obj.source_type.value}", styles["Normal"]))

    elements.append(Paragraph(f"Amount: ₹{float(obj.amount):,.2f}", styles["Normal"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(f"GST: ₹{float(obj.gst_amount):,.2f}", styles["Normal"]))
    elements.append(Spacer(1, 4))

    elements.append(Paragraph(f"Tax: ₹{float(obj.tax_amount):,.2f}", styles["Normal"]))
    elements.append(Spacer(1, 4))
    elements.append(
        Paragraph(f"Total: ₹{float(obj.total_amount):,.2f}", styles["Normal"])
    )
    elements.append(Spacer(1, 4))
    # Status
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


async def _post_invoice_journal(db: AsyncSession, invoice: Invoice):
    je = JournalEntry(
        entry_type="Invoice",
        journal_number=f"J-INV-{invoice.id}",
        entry_date=date.today(),
        description=invoice.description or f"Invoice {invoice.id} posted",
        status="Posted",
    )
    db.add(je)
    await db.flush()

    try:
        ar_acc = await get_accounts_receivable(db)
        rev_acc = await get_revenue_account(db)
    except (ValueError, Exception):
        # AR or Revenue account not configured — skip journal lines
        return

    # DR AR
    db.add(
        JournalLine(
            entry_id=je.id,
            account_id=ar_acc.id,
            debit=invoice.total_amount,
            credit=Decimal(0),
        )
    )

    # CR Revenue
    revenue_amount = invoice.amount
    db.add(
        JournalLine(
            entry_id=je.id,
            account_id=rev_acc.id,
            debit=Decimal(0),
            credit=revenue_amount,
        )
    )

    # CR GST Payable if any
    if invoice.gst_amount > 0:
        from app.utils.accounting import resolve_tax_accounts

        try:
            gst_acc = await resolve_tax_accounts(db, "output_gst")
            db.add(
                JournalLine(
                    entry_id=je.id,
                    account_id=gst_acc.id,
                    debit=Decimal(0),
                    credit=invoice.gst_amount,
                )
            )
        except (ValueError, Exception):
            pass


# ------------------ 12. CREATE LABOUR INVOICE ------------------


@router.post("/labour", response_model=InvoiceOut)
async def create_labour_invoice(
    payload: LabourInvoiceCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.create")),
):
    # 0. Validate dates
    if payload.end_date < payload.start_date:
        raise ValidationError("end_date must be >= start_date")

    project_id = payload.project_id
    start_date = payload.start_date
    end_date = payload.end_date

    # 1. Project check with tenant/project access masking
    project = await assert_project_access_masked(
        db, project_id=project_id, current_user=current_user
    )

    description = f"Labour invoice ({start_date} to {end_date})"

    # 2. Prevent duplicate invoice for same range
    existing_invoice = await db.scalar(
        select(Invoice).where(
            Invoice.project_id == project_id,
            Invoice.type == InvoiceType.LABOUR,
            Invoice.description == description,
            Invoice.company_id == (project.company_id or current_user.company_id),
        )
    )
    if existing_invoice:
        raise ValidationError("Labour invoice already exists for this date range")

    # 3. Fetch locked historical wages from Auto-Generated Expenses
    result = await db.execute(
        select(Expense).where(
            Expense.project_id == project_id,
            Expense.source_type == "attendance_auto",
            Expense.expense_date.between(start_date, end_date),
        )
    )
    expenses = result.scalars().all()

    if not expenses:
        raise NotFoundError(
            "No locked labour attendance expenses found for this date range"
        )

    # 4. Calculate total securely
    total_amount = sum(Decimal(e.amount or 0) for e in expenses)
    expense_ids = [e.id for e in expenses]

    try:
        # 6. Create invoice
        obj = Invoice(
            company_id=project.company_id or current_user.company_id,
            project_id=project_id,
            owner_id=project.owner_id,
            type=InvoiceType.LABOUR,
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
            status=InvoiceStatus.PENDING,
            description=description,
        )

        db.add(obj)
        await db.flush()

        await _post_invoice_journal(db, obj)

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


# ------------------ 13. CREATE MATERIAL INVOICE ------------------


@router.post("/material", response_model=InvoiceOut)
async def create_material_invoice(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.create")),
):
    project = await assert_project_access_masked(
        db, project_id=project_id, current_user=current_user
    )

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
            company_id=project.company_id or current_user.company_id,
            project_id=project_id,
            owner_id=project.owner_id,
            type=InvoiceType.MATERIAL,
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
            status=InvoiceStatus.PENDING,
            description="Material invoice (aggregated)",
        )

        db.add(obj)
        await db.flush()

        await _post_invoice_journal(db, obj)

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


# ------------------ 14. CREATE FROM MEASUREMENT ------------------


@router.post("/from-measurement/{measurement_id}", response_model=InvoiceOut)
async def create_invoice_from_measurement(
    measurement_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.create")),
):
    logger.info(f"Creating owner invoice from measurement_id={measurement_id}")

    # 1. Get measurement
    measurement = await db.get(FinalMeasurement, measurement_id)
    if not measurement or not getattr(measurement, "project_id", None):
        raise NotFoundError("Measurement not found")

    # 2. Get project with tenant/project access masking
    try:
        project = await assert_project_access_masked(
            db, project_id=measurement.project_id, current_user=current_user
        )
    except (NotFoundError, PermissionDeniedError):
        raise NotFoundError("Measurement not found")

    # 3. Check existing owner invoice
    existing_invoice = await db.scalar(
        select(Invoice).where(
            Invoice.reference_id == measurement.id,
            Invoice.source_type == InvoiceSourceType.MEASUREMENT,
        )
    )
    if existing_invoice:
        raise ValidationError("Owner invoice already exists")

    try:
        total_amount = Decimal(measurement.total_amount)

        # 4. Create invoice
        obj = Invoice(
            company_id=project.company_id or current_user.company_id,
            project_id=measurement.project_id,
            owner_id=project.owner_id,
            type=InvoiceType.OWNER,
            source_type=InvoiceSourceType.MEASUREMENT,
            reference_id=measurement.id,
            amount=total_amount,
            gst_percent=Decimal(0),
            gst_amount=Decimal(0),
            tax_percent=Decimal(0),
            tax_amount=Decimal(0),
            total_amount=total_amount,
            paid_amount=Decimal(0),
            pending_amount=total_amount,
            status=InvoiceStatus.PENDING,
            description="Invoice from final measurement",
        )

        db.add(obj)
        await db.flush()

        await _post_invoice_journal(db, obj)

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


# ------------------ 15. PROJECT PAYMENT SUMMARY ------------------


@router.get("/project/{project_id}/summary")
async def payment_summary(
    project_id: int,
    current_user: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access_masked(db, project_id=project_id, current_user=current_user)

    is_super = _is_super_admin(current_user)
    paid_stmt = select(func.sum(Invoice.paid_amount)).where(Invoice.project_id == project_id)
    pending_stmt = select(func.sum(Invoice.pending_amount)).where(Invoice.project_id == project_id)
    if not is_super:
        paid_stmt = paid_stmt.where(Invoice.company_id == current_user.company_id)
        pending_stmt = pending_stmt.where(Invoice.company_id == current_user.company_id)

    paid = await db.scalar(paid_stmt)
    pending = await db.scalar(pending_stmt)

    return {
        "paid": float(paid or 0),
        "pending": float(pending or 0),
    }


# ------------------ 16. ANALYTICS SUMMARY ------------------


@router.get("/analytics/summary", response_model=AnalyticsSummaryOut)
async def analytics_summary(
    project_id: int,
    current_user: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access_masked(db, project_id=project_id, current_user=current_user)

    is_super = _is_super_admin(current_user)

    # 1. Progress (task-based)
    progress = await db.scalar(
        select(func.avg(Task.completion_percentage)).where(
            Task.project_id == project_id
        )
    )

    # 2. Revenue (owner invoices)
    rev_stmt = select(func.sum(Invoice.total_amount)).where(
        Invoice.project_id == project_id,
        Invoice.type == InvoiceType.OWNER,
    )
    if not is_super:
        rev_stmt = rev_stmt.where(Invoice.company_id == current_user.company_id)
    total_revenue = await db.scalar(rev_stmt)

    # 3. Expense (labour + material)
    exp_stmt = select(func.sum(Invoice.total_amount)).where(
        Invoice.project_id == project_id,
        Invoice.type.in_([InvoiceType.LABOUR, InvoiceType.MATERIAL]),
    )
    if not is_super:
        exp_stmt = exp_stmt.where(Invoice.company_id == current_user.company_id)
    total_expense = await db.scalar(exp_stmt)

    # 4. Paid amount (for financial progress)
    paid_stmt = select(func.sum(Invoice.paid_amount)).where(Invoice.project_id == project_id)
    if not is_super:
        paid_stmt = paid_stmt.where(Invoice.company_id == current_user.company_id)
    total_paid = await db.scalar(paid_stmt)

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


# ------------------ 17. PAY INVOICE ------------------


@router.post("/{id}/pay")
async def pay_invoice(
    id: int,
    amount: Decimal,
    mode: PaymentMode,
    reference: str | None = None,
    current_user: User = Depends(require_permission("invoices.edit")),
    db: AsyncSession = Depends(get_db_session),
):
    invoice, project = await _get_invoice_or_404(db, invoice_id=id, current_user=current_user)

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
        mode=mode.value,
        reference=reference or f"inv:{invoice.id}",
        created_by=current_user.id,
    )
    db.add(txn)

    # 2. Update invoice
    invoice.paid_amount += amount
    invoice.pending_amount = invoice.total_amount - invoice.paid_amount

    if invoice.pending_amount <= 0:
        invoice.pending_amount = 0
        invoice.status = InvoiceStatus.PAID
    else:
        invoice.status = InvoiceStatus.PARTIAL

    # 3. Create Journal Entry
    je = JournalEntry(
        entry_type="Receipt",
        journal_number=f"J-REC-{txn.id or 'INV'+str(invoice.id)}",
        entry_date=date.today(),
        description=f"Payment received for Invoice {invoice.id}",
        status="Posted",
    )
    db.add(je)
    await db.flush()

    try:
        ar_acc = await get_accounts_receivable(db)
        cash_acc = await get_primary_cash_account(db)
        db.add(
            JournalLine(
                entry_id=je.id, account_id=cash_acc.id, debit=amount, credit=Decimal(0)
            )
        )
        db.add(
            JournalLine(
                entry_id=je.id, account_id=ar_acc.id, debit=Decimal(0), credit=amount
            )
        )
    except (ValueError, Exception):
        pass

    await db.commit()

    return {
        "message": "Payment recorded",
        "paid": float(invoice.paid_amount),
        "pending": float(invoice.pending_amount),
        "status": invoice.status.value,
    }


# ------------------ 18. INVOICE TRANSACTIONS ------------------


@router.get("/{id}/transactions")
async def invoice_transactions(
    id: int,
    current_user: User = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db_session),
):
    invoice, project = await _get_invoice_or_404(db, invoice_id=id, current_user=current_user)
    result = await db.execute(select(Transaction).where(Transaction.invoice_id == invoice.id))
    return result.scalars().all()


# ------------------ 19. RECEIVABLES SUMMARY ------------------


@router.get("/receivables/summary", response_model=ReceivablesSummaryOut)
async def receivable_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.view")),
):
    is_super = _is_super_admin(current_user)

    if is_super:
        inv_total = await db.scalar(select(func.sum(Invoice.total_amount))) or 0
        inv_paid = await db.scalar(select(func.sum(Invoice.paid_amount))) or 0
        inv_pending = await db.scalar(select(func.sum(Invoice.pending_amount))) or 0
        rabill_total = (
            await db.scalar(
                select(func.sum(RABill.total_amount)).where(RABill.status == "Approved")
            )
            or 0
        )
    else:
        inv_total = (
            await db.scalar(
                select(func.sum(Invoice.total_amount)).where(
                    Invoice.company_id == current_user.company_id
                )
            )
            or 0
        )
        inv_paid = (
            await db.scalar(
                select(func.sum(Invoice.paid_amount)).where(
                    Invoice.company_id == current_user.company_id
                )
            )
            or 0
        )
        inv_pending = (
            await db.scalar(
                select(func.sum(Invoice.pending_amount)).where(
                    Invoice.company_id == current_user.company_id
                )
            )
            or 0
        )
        rabill_total = (
            await db.scalar(
                select(func.sum(RABill.total_amount))
                .join(Project, Project.id == RABill.project_id)
                .where(
                    RABill.status == "Approved",
                    Project.company_id == current_user.company_id,
                )
            )
            or 0
        )

    rabill_paid = 0
    rabill_pending = rabill_total

    total_billed = float(inv_total + rabill_total)
    total_received = float(inv_paid + rabill_paid)
    pending_amount = float(inv_pending + rabill_pending)
    portfolio_value = total_billed

    return ReceivablesSummaryOut(
        portfolio_value=portfolio_value,
        total_billed=total_billed,
        total_received=total_received,
        pending_amount=pending_amount,
        overdue_amount=0.0,
    )


# ------------------ 20. RECEIVABLES AGING ------------------


@router.get("/receivables/aging")
async def receivable_aging(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.view")),
):
    today = date.today()

    is_super = _is_super_admin(current_user)
    stmt = select(Invoice).where(Invoice.pending_amount > 0)
    if not is_super:
        stmt = stmt.where(Invoice.company_id == current_user.company_id)

    rows = (await db.execute(stmt)).scalars().all()

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


# ------------------ 21. CLIENT LEDGER ------------------


@router.get(
    "/receivables/client-ledger/{client_id}", response_model=ClientLedgerResponse
)
async def get_client_ledger(
    client_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.view")),
):
    owner = await db.get(Owner, client_id)
    if not owner:
        raise NotFoundError("Client not found")

    is_super = _is_super_admin(current_user)
    if not is_super and current_user.company_id is not None and owner.company_id != current_user.company_id:
        raise NotFoundError("Client not found")

    try:
        ar_acc = await get_accounts_receivable(db)
        ar_acc_id = ar_acc.id
    except (ValueError, Exception):
        return ClientLedgerResponse(
            total_billed=0.0, total_received=0.0, outstanding=0.0, transactions=[]
        )

    result = await db.execute(
        select(JournalEntry, JournalLine)
        .join(JournalLine)
        .where(JournalLine.account_id == ar_acc_id, JournalEntry.status == "Posted")
        .order_by(JournalEntry.entry_date.asc())
    )

    rows = result.all()

    inv_stmt = select(Invoice.id).where(Invoice.owner_id == client_id)
    if not is_super and current_user.company_id is not None:
        inv_stmt = inv_stmt.where(Invoice.company_id == current_user.company_id)
    invoices = (await db.execute(inv_stmt)).scalars().all()
    rabills: list[int] = []

    valid_jnums = set(
        [f"J-INV-{i}" for i in invoices] + [f"J-RAB-{r}" for r in rabills]
    )

    txns = []
    running_balance = 0.0
    total_billed = 0.0
    total_received = 0.0

    for je, jl in rows:
        is_owner = False
        if je.journal_number in valid_jnums:
            is_owner = True
        elif je.journal_number and je.journal_number.startswith("J-REC-INV"):
            inv_id_str = je.journal_number.replace("J-REC-INV", "")
            if inv_id_str.isdigit() and int(inv_id_str) in invoices:
                is_owner = True
        elif je.journal_number and je.journal_number.startswith("J-REC-"):
            pass

        if not is_owner:
            continue

        debit = float(jl.debit or 0)
        credit = float(jl.credit or 0)

        running_balance += debit - credit
        total_billed += debit
        total_received += credit

        txns.append(
            ClientLedgerTransactionOut(
                date=datetime.combine(je.entry_date, datetime.min.time()),
                particulars=je.description or je.journal_number,
                debit=debit,
                credit=credit,
                running_balance=running_balance,
            )
        )

    return ClientLedgerResponse(
        total_billed=total_billed,
        total_received=total_received,
        outstanding=running_balance,
        transactions=txns,
    )


# ------------------ 22. COLLECTIONS ------------------


@router.get("/receivables/collections", response_model=list[CollectionOut])
async def get_collections(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.view")),
):
    is_super = _is_super_admin(current_user)
    if is_super:
        stmt = select(Transaction).where(Transaction.type == "receipt")
    else:
        stmt = (
            select(Transaction)
            .join(Project, Project.id == Transaction.project_id)
            .where(
                Transaction.type == "receipt",
                Project.company_id == current_user.company_id,
            )
        )
    txns = (await db.execute(stmt)).scalars().all()
    res = []
    for t in txns:
        res.append(
            CollectionOut(
                invoice_no=f"INV-{t.invoice_id}" if t.invoice_id else "N/A",
                client="Client",
                amount_received=float(t.amount or 0),
                received_on=t.created_at,
                mode=t.mode or "Cash",
                reference=t.reference or "-",
                status="Received",
            )
        )
    return res


# ------------------ 23. MANUAL RECEIVABLE ------------------


@router.post("/receivables/manual")
async def create_manual_receivable(
    payload: ManualReceivableCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.create")),
):
    owner = await db.get(Owner, payload.client_id)
    if not owner:
        raise NotFoundError("Client not found")

    is_super = _is_super_admin(current_user)
    if not is_super and current_user.company_id is not None and owner.company_id != current_user.company_id:
        raise NotFoundError("Client not found")

    je = JournalEntry(
        entry_type="Manual Receivable",
        journal_number=f"J-MAN-REC-{payload.client_id}-{date.today().strftime('%Y%m%d')}",
        entry_date=payload.due_date,
        description=payload.description,
        status="Posted",
    )
    db.add(je)
    await db.flush()

    try:
        ar_acc = await get_accounts_receivable(db)
        rev_acc = await get_revenue_account(db)
        db.add(
            JournalLine(
                entry_id=je.id,
                account_id=ar_acc.id,
                debit=Decimal(str(payload.amount)),
                credit=Decimal(0),
            )
        )
        db.add(
            JournalLine(
                entry_id=je.id,
                account_id=rev_acc.id,
                debit=Decimal(0),
                credit=Decimal(str(payload.amount)),
            )
        )
    except (ValueError, Exception):
        pass

    await db.commit()
    return {"message": "Manual receivable posted", "journal_id": je.id}


# ------------------ 24. IMPORT RECEIVABLES ------------------


@router.post("/receivables/import")
async def import_receivables(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.create")),
):
    content = await file.read()
    decoded = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded))

    valid = 0
    errors = 0
    for row in reader:
        if row:
            valid += 1

    return {
        "valid_records": valid,
        "errors": errors,
        "message": "Import preview successful",
    }


# ------------------ 25. EXPORT RECEIVABLES ------------------


@router.get("/receivables/export")
async def export_receivables(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.export")),
):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Amount"])

    is_super = _is_super_admin(current_user)
    stmt = select(Invoice).where(Invoice.pending_amount > 0)
    if not is_super:
        stmt = stmt.where(Invoice.company_id == current_user.company_id)

    invoices = (await db.execute(stmt)).scalars().all()
    for inv in invoices:
        writer.writerow([inv.id, float(inv.pending_amount or 0)])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=receivables.csv"},
    )


# ------------------ 26. EXPORT COLLECTIONS ------------------


@router.get("/receivables/collections/export")
async def export_collections(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.export")),
):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Invoice", "Amount Received"])

    is_super = _is_super_admin(current_user)
    if is_super:
        stmt = select(Transaction).where(Transaction.type == "receipt")
    else:
        stmt = (
            select(Transaction)
            .join(Project, Project.id == Transaction.project_id)
            .where(
                Transaction.type == "receipt",
                Project.company_id == current_user.company_id,
            )
        )
    txns = (await db.execute(stmt)).scalars().all()
    for t in txns:
        writer.writerow([f"INV-{t.invoice_id}" if t.invoice_id else "N/A", float(t.amount or 0)])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=collections.csv"},
    )


# ------------------ 27. EXPORT CLIENT LEDGER ------------------


@router.get("/receivables/client-ledger/{client_id}/export")
async def export_client_ledger(
    client_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.export")),
):
    owner = await db.get(Owner, client_id)
    if not owner:
        raise NotFoundError("Client not found")

    is_super = _is_super_admin(current_user)
    if not is_super and current_user.company_id is not None and owner.company_id != current_user.company_id:
        raise NotFoundError("Client not found")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Particulars", "Debit", "Credit", "Balance"])

    try:
        ar_acc = await get_accounts_receivable(db)
        ar_acc_id = ar_acc.id
    except (ValueError, Exception):
        ar_acc_id = None

    if ar_acc_id:
        result = await db.execute(
            select(JournalEntry, JournalLine)
            .join(JournalLine)
            .where(JournalLine.account_id == ar_acc_id, JournalEntry.status == "Posted")
            .order_by(JournalEntry.entry_date.asc())
        )
        rows = result.all()

        inv_stmt = select(Invoice.id).where(Invoice.owner_id == client_id)
        if not is_super and current_user.company_id is not None:
            inv_stmt = inv_stmt.where(Invoice.company_id == current_user.company_id)
        invoices = (await db.execute(inv_stmt)).scalars().all()
        valid_jnums = set([f"J-INV-{i}" for i in invoices])

        running_balance = 0.0
        for je, jl in rows:
            is_match = False
            if je.journal_number in valid_jnums:
                is_match = True
            elif je.journal_number and je.journal_number.startswith("J-REC-INV"):
                inv_id_str = je.journal_number.replace("J-REC-INV", "")
                if inv_id_str.isdigit() and int(inv_id_str) in invoices:
                    is_match = True

            if not is_match:
                continue

            debit = float(jl.debit or 0)
            credit = float(jl.credit or 0)
            running_balance += debit - credit

            writer.writerow([
                je.entry_date.strftime("%Y-%m-%d") if je.entry_date else "",
                je.description or je.journal_number,
                debit,
                credit,
                running_balance,
            ])

    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ledger_{client_id}.csv"},
    )


# ------------------ 28. SEND INVOICE ------------------


@router.post("/{invoice_id}/send", response_model=s.SendInvoiceResponse)
async def send_invoice(
    invoice_id: int,
    payload: s.SendInvoiceRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("invoices.edit")),
):
    # =====================================================
    # GET INVOICE WITH TENANT & PROJECT AUTHORIZATION
    # =====================================================
    invoice, project = await _get_invoice_or_404(
        db, invoice_id=invoice_id, current_user=current_user
    )

    # =====================================================
    # VALIDATE CLIENT (SAME COMPANY & CLIENT ROLE)
    # =====================================================
    client = await db.get(User, payload.client_user_id)
    if not client:
        raise HTTPException(
            status_code=404,
            detail="Client not found.",
        )

    is_super = _is_super_admin(current_user)
    if not is_super and current_user.company_id is not None and client.company_id != current_user.company_id:
        raise HTTPException(
            status_code=404,
            detail="Client not found.",
        )

    if client.role.lower() != "client":
        raise HTTPException(
            status_code=400,
            detail="Selected user is not a client.",
        )

    # =====================================================
    # VALIDATE FINANCIALS
    # =====================================================
    if invoice.total_amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invoice amount must be greater than zero.",
        )

    if invoice.pending_amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invoice has no pending amount.",
        )

    # =====================================================
    # STATUS VALIDATION
    # =====================================================
    if invoice.status == InvoiceStatus.CANCELLED:
        raise HTTPException(
            status_code=400,
            detail="Cancelled invoice cannot be sent.",
        )

    if invoice.status == InvoiceStatus.PAID:
        raise HTTPException(
            status_code=400,
            detail="Paid invoice cannot be sent.",
        )

    # =====================================================
    # PREVENT DUPLICATE SEND
    # =====================================================
    existing_notification = await db.scalar(
        select(Notification).where(
            Notification.user_id == client.id,
            Notification.type == "Invoice",
            Notification.link == f"/invoice/{invoice.id}",
        )
    )

    if existing_notification:
        raise HTTPException(
            status_code=400,
            detail="Invoice has already been sent to this client.",
        )

    # =====================================================
    # CREATE NOTIFICATION
    # =====================================================
    notification = Notification(
        user_id=client.id,
        title="New Invoice Received",
        message=(
            f"Invoice #{invoice.id} has been shared with you. "
            "Please review your invoice."
        ),
        type="Invoice",
        link=f"/invoice/{invoice.id}",
    )

    db.add(notification)

    # =====================================================
    # ACTIVITY LOG
    # =====================================================
    db.add(
        ActivityLog(
            action="SEND_INVOICE",
            entity="invoice",
            entity_id=invoice.id,
            performed_by=current_user.id,
            details={
                "invoice_id": invoice.id,
                "client_id": client.id,
                "project_id": invoice.project_id,
                "owner_id": invoice.owner_id,
                "amount": float(invoice.total_amount),
            },
        )
    )

    # =====================================================
    # SAVE
    # =====================================================
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    # =====================================================
    # RESPONSE
    # =====================================================
    return s.SendInvoiceResponse(
        message="Invoice sent successfully.",
        invoice_id=invoice.id,
        client_user_id=client.id,
    )
