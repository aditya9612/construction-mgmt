import logging
import mimetypes
import os
import shutil
import textwrap
import uuid
from decimal import Decimal
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse, StreamingResponse

from openpyxl import Workbook
from pydantic import BaseModel
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfgen import canvas

from app.models.invoice import Transaction

from sqlalchemy import (
    select,
    func,
    extract,
    or_,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.schemas.client_payment as s
from app.cache.redis import bump_cache_version
from app.core.dependencies import get_request_redis, require_roles
from app.core.enums import (
    InvoiceStatus,
    PaymentMethod,
    PaymentStatus,
    ProjectStatus,
)
from app.db.session import get_db_session
from app.models.client_payment import ClientPayment
from app.models.invoice import Invoice
from app.models.project import Project, ProjectMember
from app.models.user import ActivityLog, User, UserRole
from app.utils.accounting import auto_post_journal

logger = logging.getLogger(__name__)

VERSION_KEY = "cache_version:client_payments"

# ---------------------------------------------------------------------------
# Company details used on receipt PDFs & statements.
# ---------------------------------------------------------------------------
COMPANY_NAME = "Construction Management System"
COMPANY_ADDRESS_LINE = "123, Business Park, Andheri East, Mumbai - 400069"
COMPANY_CONTACT_LINE = (
    "Tel: +91 22 1234 5678 | Email: accounts@cms.com | GSTIN: 27XXXXX1234X1ZX"
)

# ---------------------------------------------------------------------------
# Role tables
# ---------------------------------------------------------------------------
CLIENT_PAYMENT_CREATE_ROLES = [r.value for r in (UserRole.CLIENT,)]

PAYMENT_VERIFY_ROLES = [
    UserRole.ADMIN.value,
    UserRole.ACCOUNTANT.value,
]

CLIENT_PAYMENT_READ_ROLES = [
    UserRole.ADMIN.value,
    UserRole.ACCOUNTANT.value,
    UserRole.CLIENT.value,
]

CLIENT_PAYMENT_REPORTING_ROLES = [
    UserRole.CLIENT.value,
    UserRole.ADMIN.value,
    UserRole.ACCOUNTANT.value,
]


router = APIRouter(
    prefix="/client-payments",
    tags=["Client Payments"],
)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "application/pdf"}
MAX_FILE_SIZE = 5 * 1024 * 1024
UPLOAD_FOLDER = Path("uploads/payments").resolve()

OFFLINE_PAYMENT_METHODS = {
    PaymentMethod.CHEQUE,
    PaymentMethod.NEFT,
    PaymentMethod.RTGS,
    PaymentMethod.UPI,
}

ELECTRONIC_PAYMENT_METHODS = {
    PaymentMethod.ONLINE,
    PaymentMethod.UPI,
    PaymentMethod.NEFT,
    PaymentMethod.RTGS,
}

NON_BLOCKING_DUPLICATE_STATUSES = (
    PaymentStatus.REJECTED,
    PaymentStatus.FAILED,
)

# Colors for PDFs
PRIMARY_COLOR = HexColor("#1a365d")
SECONDARY_COLOR = HexColor("#2c5282")
ACCENT_COLOR = HexColor("#e53e3e")
SUCCESS_COLOR = HexColor("#38a169")
LIGHT_BG = HexColor("#f7fafc")
BORDER_COLOR = HexColor("#cbd5e0")
TEXT_COLOR = HexColor("#2d3748")
MUTED_TEXT = HexColor("#718096")


# ===========================================================================
# PDF & Helper Utilities
# ===========================================================================


def format_indian_currency(amount: float) -> str:
    """Format number using Indian numbering system (lakh/crore)."""
    is_negative = amount < 0
    amount = abs(amount)
    integer_part, decimal_part = f"{amount:.2f}".split(".")

    if len(integer_part) <= 3:
        formatted_int = integer_part
    else:
        last_three = integer_part[-3:]
        remaining = integer_part[:-3]
        groups = []
        while len(remaining) > 2:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups.insert(0, remaining)
        formatted_int = ",".join(groups) + "," + last_three

    result = f"Rs. {formatted_int}.{decimal_part}"
    return f"-{result}" if is_negative else result


def number_to_words_indian(num: float) -> str:
    """Convert number to words in Indian format (Rupees and Paise)."""
    units = [
        "",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
        "Six",
        "Seven",
        "Eight",
        "Nine",
        "Ten",
        "Eleven",
        "Twelve",
        "Thirteen",
        "Fourteen",
        "Fifteen",
        "Sixteen",
        "Seventeen",
        "Eighteen",
        "Nineteen",
    ]
    tens = [
        "",
        "",
        "Twenty",
        "Thirty",
        "Forty",
        "Fifty",
        "Sixty",
        "Seventy",
        "Eighty",
        "Ninety",
    ]

    def convert_less_than_thousand(n: int) -> str:
        if n < 20:
            return units[n]
        elif n < 100:
            return tens[n // 10] + (" " + units[n % 10] if n % 10 else "")
        else:
            return (
                units[n // 100]
                + " Hundred"
                + (" and " + convert_less_than_thousand(n % 100) if n % 100 else "")
            )

    def convert(n: int) -> str:
        if n == 0:
            return "Zero"
        crore = n // 10000000
        lakh = (n // 100000) % 100
        thousand = (n // 1000) % 100
        remainder = n % 1000
        result = ""
        if crore:
            result += convert_less_than_thousand(crore) + " Crore "
        if lakh:
            result += convert_less_than_thousand(lakh) + " Lakh "
        if thousand:
            result += convert_less_than_thousand(thousand) + " Thousand "
        if remainder:
            result += convert_less_than_thousand(remainder)
        return result.strip()

    integer_part = int(num)
    decimal_part = round((num - integer_part) * 100)
    words = convert(integer_part) + " Rupees"
    if decimal_part > 0:
        words += f" and {convert(decimal_part)} Paise"
    words += " Only"
    return words


def get_status_color(status_value: str) -> HexColor:
    """Get color based on payment status."""
    status_colors = {
        "SUCCESS": SUCCESS_COLOR,
        "COMPLETED": SUCCESS_COLOR,
        "PENDING": HexColor("#d69e2e"),
        "VERIFICATION_PENDING": HexColor("#d69e2e"),
        "FAILED": ACCENT_COLOR,
        "CANCELLED": HexColor("#718096"),
        "REFUNDED": HexColor("#805ad5"),
    }
    return status_colors.get(status_value.upper(), TEXT_COLOR)


# ===========================================================================
# DB & Validation Helpers
# ===========================================================================


async def get_client_or_404(db: AsyncSession, client_user_id: int) -> User:
    """Validate that client_user_id refers to a real, active CLIENT user."""
    client = await db.scalar(
        select(User).where(
            User.id == client_user_id,
            User.role == UserRole.CLIENT.value,
            User.is_deleted == False,
        )
    )
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    if not client.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Client is inactive."
        )
    return client


async def validate_client_project_membership(
    db: AsyncSession,
    client_user_id: int,
    project_id: int,
) -> User:
    """Validate user exists, is CLIENT, active, and assigned to project."""
    client = await db.scalar(
        select(User).where(
            User.id == client_user_id,
            User.role == UserRole.CLIENT.value,
            User.is_deleted == False,
        )
    )
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found."
        )
    if not client.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Client is inactive."
        )
    member = await db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == client_user_id,
        )
    )
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Client is not assigned to this project.",
        )
    return client


async def get_project_or_404(db: AsyncSession, project_id: int) -> Project:
    """Get project or raise 404/400 if invalid."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    if project.status == ProjectStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project is already completed.",
        )
    if project.status == ProjectStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Project is cancelled."
        )
    return project


def assert_client_owns_payment(current_user: User, payment: ClientPayment) -> None:
    """Ownership guard: CLIENT may only access their own payments."""
    if (
        current_user.role == UserRole.CLIENT.value
        and payment.client_user_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this payment.",
        )


async def assert_client_is_project_member(
    db: AsyncSession, project_id: int, current_user: User
) -> None:
    """Shared membership guard for CLIENT role."""
    if current_user.role != UserRole.CLIENT.value:
        return
    member = await db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
        )
    )
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not assigned to this project.",
        )


async def check_duplicate_payment(
    db: AsyncSession,
    payload: "s.ClientPaymentCreate | s.ClientPaymentBase",
    exclude_payment_id: int | None = None,
):
    """Prevent duplicate cheque/reference payments."""
    stmt = None
    if payload.payment_method == PaymentMethod.CHEQUE:
        stmt = select(ClientPayment).where(
            ClientPayment.bank_name == payload.bank_name.strip(),
            ClientPayment.cheque_no == payload.cheque_no.strip(),
            ClientPayment.payment_status.notin_(NON_BLOCKING_DUPLICATE_STATUSES),
        )
    elif payload.payment_method in (
        PaymentMethod.NEFT,
        PaymentMethod.RTGS,
        PaymentMethod.UPI,
    ):
        stmt = select(ClientPayment).where(
            ClientPayment.reference_no == payload.reference_no.strip(),
            ClientPayment.payment_status.notin_(NON_BLOCKING_DUPLICATE_STATUSES),
        )
    if stmt is None:
        return
    if exclude_payment_id is not None:
        stmt = stmt.where(ClientPayment.id != exclude_payment_id)
    payment = await db.scalar(stmt)
    if payment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Duplicate payment found."
        )


def validate_and_normalize_payment_fields(data: "s.ClientPaymentBase") -> None:
    """Method-specific required/forbidden field validation."""
    method = data.payment_method

    if method == PaymentMethod.CHEQUE:
        if not data.bank_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bank name is required for cheque payment.",
            )
        if not data.cheque_no:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cheque number is required for cheque payment.",
            )
        if data.reference_no:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reference number should not be provided for cheque payment.",
            )

    elif method in (PaymentMethod.NEFT, PaymentMethod.RTGS):
        if not data.bank_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bank name is required for bank transfer.",
            )
        if not data.reference_no:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reference number is required for bank transfer.",
            )
        if data.cheque_no:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cheque number should not be provided for bank transfer.",
            )
        data.cheque_no = None

    elif method == PaymentMethod.UPI:
        if not data.reference_no:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="UPI transaction reference is required.",
            )
        if data.cheque_no:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cheque number should not be provided for UPI payment.",
            )
        if data.bank_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bank name should not be provided for UPI payment.",
            )
        data.bank_name, data.cheque_no = None, None

    elif method in (PaymentMethod.ONLINE, PaymentMethod.CASH):
        if any((data.bank_name, data.cheque_no, data.reference_no)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{method.value.title()} payment should not contain bank, "
                    "cheque or reference details."
                ),
            )
        data.bank_name, data.cheque_no, data.reference_no = None, None, None


async def assert_invoice_payable(invoice_obj: Invoice, amount) -> None:
    """Shared invoice-eligibility check for create and update."""
    if invoice_obj.status in (InvoiceStatus.CANCELLED, InvoiceStatus.PAID):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment cannot be created or updated for this invoice.",
        )
    if invoice_obj.pending_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invoice is already fully paid.",
        )
    if amount > invoice_obj.pending_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount exceeds pending invoice amount.",
        )


async def validate_receipt(receipt: UploadFile | None, payment_method: PaymentMethod):
    """Validate receipt file if required for payment method."""
    if payment_method in OFFLINE_PAYMENT_METHODS and receipt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Receipt is required."
        )
    if receipt is None:
        return
    if not receipt.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid receipt file."
        )
    extension = Path(receipt.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPG, JPEG, PNG and PDF files are allowed.",
        )
    if receipt.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file type."
        )
    receipt.file.seek(0, os.SEEK_END)
    size = receipt.file.tell()
    receipt.file.seek(0)
    if size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty."
        )
    if size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum file size is 5 MB."
        )


async def save_receipt(receipt: UploadFile | None, payment_no: str) -> str | None:
    """Save receipt file and return path."""
    if receipt is None:
        return None
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
    try:
        if receipt.content_type == "application/pdf":
            from app.core.validators import validate_and_save_document
            file_path_str = await validate_and_save_document(
                file=receipt, upload_dir=str(UPLOAD_FOLDER), prefix=payment_no
            )
            return file_path_str
        elif receipt.content_type.startswith("image/"):
            from app.core.validators import validate_and_save_image
            file_path_str = await validate_and_save_image(
                file=receipt, upload_dir=str(UPLOAD_FOLDER), prefix=payment_no
            )
            return file_path_str
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported receipt type. Please upload a PDF or an image."
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.exception("Unable to save receipt.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error saving receipt."
        )
    finally:
        await receipt.close()


def create_payment_status(method: PaymentMethod) -> PaymentStatus:
    """Determine initial payment status based on method."""
    if method == PaymentMethod.ONLINE:
        return PaymentStatus.PENDING
    return PaymentStatus.VERIFICATION_PENDING


def generate_transaction_id(method: PaymentMethod) -> str | None:
    """Auto-generate transaction id for electronic payment methods."""
    if method in ELECTRONIC_PAYMENT_METHODS:
        return f"TXN{uuid.uuid4().hex[:12].upper()}"
    return None


def generate_receipt_no(payment: ClientPayment) -> str:
    """Generate human-friendly receipt number: RCP<year><5-digit id>."""
    year = payment.payment_date.year if payment.payment_date else datetime.now().year
    return f"RCP{year}{payment.id:05d}"


def _safe_remove(path: str | None):
    """Safely remove a file."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            logger.warning("Unable to delete file: %s", path)


async def send_notification(
    db: AsyncSession,
    user_id: int,
    title: str,
    message: str,
    notification_type: str = "INFO",
) -> None:
    """Best-effort notification dispatch."""
    try:
        from app.models.notification import Notification

        db.add(
            Notification(
                user_id=user_id,
                title=title,
                message=message,
                type=notification_type,
            )
        )
        await db.flush()
    except Exception:
        logger.warning(
            "Notification not sent (user=%s, title=%s) - Notification model unavailable.",
            user_id,
            title,
        )


def build_payment_response(
    payment: ClientPayment,
    invoice: Invoice | None = None,
) -> s.ClientPaymentOut:
    """Build standardized payment response."""
    response = s.ClientPaymentOut.model_validate(payment)
    response.user_name = payment.client_user.full_name if payment.client_user else None
    response.project_name = payment.project.project_name if payment.project else None
    invoice_obj = invoice if invoice is not None else payment.invoice
    if invoice_obj is not None:
        response.invoice_no = f"INV-{invoice_obj.id:06d}"
        response.invoice_status = invoice_obj.status
        response.pending_amount = invoice_obj.pending_amount
    return response


# =============================================================================
# GET /client-payments/invoice-summary
# =============================================================================


@router.get("/invoice-summary")
async def get_invoice_payment_summary(
    project_id: int = Query(..., gt=0),
    invoice_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_READ_ROLES)),
):
    """Get payment summary per invoice for a project."""
    await assert_client_is_project_member(db, project_id, current_user)

    stmt = (
        select(ClientPayment)
        .where(ClientPayment.project_id == project_id)
        .options(
            selectinload(ClientPayment.client_user),
            selectinload(ClientPayment.invoice),
        )
    )

    if invoice_id:
        stmt = stmt.where(ClientPayment.invoice_id == invoice_id)

    stmt = stmt.order_by(ClientPayment.created_at.desc())
    payments = (await db.execute(stmt)).scalars().unique().all()

    # Group by invoice
    summary = {}
    for p in payments:
        inv_id = p.invoice_id
        if inv_id not in summary:
            invoice_obj = p.invoice
            summary[inv_id] = {
                "invoice_id": inv_id,
                "invoice_no": f"INV-{inv_id:06d}" if inv_id else None,
                "total_amount": float(invoice_obj.total_amount) if invoice_obj else 0,
                "paid_amount": float(invoice_obj.paid_amount) if invoice_obj else 0,
                "pending_amount": (
                    float(invoice_obj.pending_amount) if invoice_obj else 0
                ),
                "status": (
                    invoice_obj.status.value
                    if invoice_obj and invoice_obj.status
                    else None
                ),
                "payments": [],
            }

        summary[inv_id]["payments"].append(
            {
                "payment_id": p.id,
                "payment_no": p.payment_no,
                "amount": float(p.amount),
                "method": p.payment_method.value,
                "status": p.payment_status.value,
                "payment_date": p.payment_date.isoformat() if p.payment_date else None,
                "verified_at": p.verified_at.isoformat() if p.verified_at else None,
            }
        )

    return {
        "project_id": project_id,
        "total_invoices": len(summary),
        "invoices": list(summary.values()),
    }


# =============================================================================
# GET /client-payments/history
# =============================================================================


@router.get("/history")
async def get_payment_history(
    project_id: int = Query(..., gt=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_READ_ROLES)),
):
    """Get payment history timeline for a project."""
    await assert_client_is_project_member(db, project_id, current_user)

    # Count
    count_stmt = select(func.count(ClientPayment.id)).where(
        ClientPayment.project_id == project_id
    )
    if current_user.role == UserRole.CLIENT.value:
        count_stmt = count_stmt.where(ClientPayment.client_user_id == current_user.id)

    total = await db.scalar(count_stmt) or 0

    # Data
    stmt = (
        select(ClientPayment)
        .where(ClientPayment.project_id == project_id)
        .options(
            selectinload(ClientPayment.client_user),
            selectinload(ClientPayment.invoice),
        )
    )

    if current_user.role == UserRole.CLIENT.value:
        stmt = stmt.where(ClientPayment.client_user_id == current_user.id)

    stmt = stmt.order_by(ClientPayment.created_at.desc()).offset(offset).limit(limit)
    payments = (await db.execute(stmt)).scalars().unique().all()

    history = []
    for p in payments:
        history.append(
            {
                "id": p.id,
                "payment_no": p.payment_no,
                "amount": float(p.amount),
                "method": p.payment_method.value,
                "status": p.payment_status.value,
                "client_name": p.client_user.full_name if p.client_user else None,
                "invoice_no": f"INV-{p.invoice_id:06d}" if p.invoice_id else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "payment_date": p.payment_date.isoformat() if p.payment_date else None,
                "verified_at": p.verified_at.isoformat() if p.verified_at else None,
                "remarks": p.remarks,
            }
        )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "history": history,
    }


# =============================================================================
# GET /client-payments/pending-invoices
# =============================================================================


@router.get("/pending-invoices", response_model=s.PendingInvoiceList)
async def get_pending_invoices(
    project_id: int | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_REPORTING_ROLES)),
):
    """Pending invoices available for payment."""
    stmt = (
        select(
            Invoice.id,
            Invoice.project_id,
            Invoice.total_amount,
            Invoice.paid_amount,
            Invoice.pending_amount,
            Invoice.status,
            Invoice.description,
            Invoice.created_at,
            Project.project_name,
        )
        .outerjoin(Project, Invoice.project_id == Project.id)
        .where(Invoice.pending_amount > 0)
    )

    if current_user.role == UserRole.CLIENT.value:
        stmt = stmt.where(
            Invoice.project_id.in_(
                select(ProjectMember.project_id).where(
                    ProjectMember.user_id == current_user.id
                )
            )
        )

    if project_id:
        await assert_client_is_project_member(db, project_id, current_user)
        stmt = stmt.where(Invoice.project_id == project_id)

    if search:
        search = search.strip()
        if search:
            stmt = stmt.where(
                or_(
                    Project.project_name.ilike(f"%{search}%"),
                    Invoice.description.ilike(f"%{search}%"),
                )
            )

    total_stmt = select(func.count(Invoice.id.distinct())).where(
        Invoice.pending_amount > 0
    )

    if current_user.role == UserRole.CLIENT.value:
        total_stmt = total_stmt.where(
            Invoice.project_id.in_(
                select(ProjectMember.project_id).where(
                    ProjectMember.user_id == current_user.id
                )
            )
        )
    if project_id:
        total_stmt = total_stmt.where(Invoice.project_id == project_id)
    if search:
        search_text = search.strip()
        if search_text:
            total_stmt = total_stmt.outerjoin(
                Project, Invoice.project_id == Project.id
            ).where(
                or_(
                    Project.project_name.ilike(f"%{search_text}%"),
                    Invoice.description.ilike(f"%{search_text}%"),
                )
            )

    total = await db.scalar(total_stmt)
    stmt = stmt.order_by(Invoice.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    rows = result.all()

    items = [
        s.PendingInvoiceOut(
            invoice_id=row.id,
            invoice_no=f"INV-{row.id:06d}",
            project_id=row.project_id,
            project_name=row.project_name,
            total_amount=row.total_amount,
            paid_amount=row.paid_amount,
            pending_amount=row.pending_amount,
            due_date=None,
            status=(
                row.status.value if hasattr(row.status, "value") else str(row.status)
            ),
        )
        for row in rows
    ]

    return s.PendingInvoiceList(
        total=total or 0,
        limit=limit,
        offset=offset,
        items=items,
    )


# =============================================================================
# GET /client-payments/analytics
# =============================================================================


@router.get("/analytics", response_model=s.ClientPaymentAnalyticsOut)
async def payment_analytics(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_REPORTING_ROLES)),
):
    """Payment analytics - CLIENT sees own, others see org-wide."""
    is_client = current_user.role == UserRole.CLIENT.value

    def scope(stmt_):
        if is_client:
            return stmt_.where(ClientPayment.client_user_id == current_user.id)
        return stmt_

    # Payment method breakdown
    method_rows = (
        await db.execute(
            scope(
                select(
                    ClientPayment.payment_method,
                    func.coalesce(func.sum(ClientPayment.amount), 0),
                ).where(ClientPayment.payment_status == PaymentStatus.SUCCESS)
            ).group_by(ClientPayment.payment_method)
        )
    ).all()
    methods = {"cash": 0, "cheque": 0, "upi": 0, "neft": 0, "rtgs": 0, "online": 0}
    for method, total in method_rows:
        methods[method.value.lower()] = total

    # Monthly collection
    rows = (
        await db.execute(
            scope(
                select(
                    extract("month", ClientPayment.payment_date),
                    extract("year", ClientPayment.payment_date),
                    func.sum(ClientPayment.amount),
                ).where(ClientPayment.payment_status == PaymentStatus.SUCCESS)
            )
            .group_by(
                extract("year", ClientPayment.payment_date),
                extract("month", ClientPayment.payment_date),
            )
            .order_by(
                extract("year", ClientPayment.payment_date),
                extract("month", ClientPayment.payment_date),
            )
        )
    ).all()
    monthly = [
        s.MonthlyCollection(month=int(month), year=int(year), total_amount=total)
        for month, year, total in rows
    ]

    # Aggregates
    success = await db.scalar(
        scope(
            select(func.count()).where(
                ClientPayment.payment_status == PaymentStatus.SUCCESS
            )
        )
    )
    rejected = await db.scalar(
        scope(
            select(func.count()).where(
                ClientPayment.payment_status == PaymentStatus.REJECTED
            )
        )
    )
    pending = await db.scalar(
        scope(
            select(func.count()).where(
                ClientPayment.payment_status == PaymentStatus.VERIFICATION_PENDING
            )
        )
    )
    total_collection = await db.scalar(
        scope(
            select(func.coalesce(func.sum(ClientPayment.amount), 0)).where(
                ClientPayment.payment_status == PaymentStatus.SUCCESS
            )
        )
    )
    average_payment = await db.scalar(
        scope(
            select(func.coalesce(func.avg(ClientPayment.amount), 0)).where(
                ClientPayment.payment_status == PaymentStatus.SUCCESS
            )
        )
    )
    highest_payment = await db.scalar(
        scope(
            select(func.coalesce(func.max(ClientPayment.amount), 0)).where(
                ClientPayment.payment_status == PaymentStatus.SUCCESS
            )
        )
    )

    # Total invoices count
    if is_client:
        total_invoices = (
            await db.scalar(
                select(func.count(Invoice.id.distinct()))
                .join(ProjectMember, ProjectMember.project_id == Invoice.project_id)
                .where(ProjectMember.user_id == current_user.id)
            )
            or 0
        )
    else:
        total_invoices = await db.scalar(select(func.count(Invoice.id))) or 0

    # TODO: Implement proper overdue calculation when due_date is added to Invoice
    overdue_invoices = 0

    return s.ClientPaymentAnalyticsOut(
        payment_methods=s.PaymentMethodAnalytics(**methods),
        monthly_collection=monthly,
        total_collection=total_collection,
        successful_payments=success or 0,
        rejected_payments=rejected or 0,
        pending_verification=pending or 0,
        total_invoices=total_invoices,
        overdue_invoices=overdue_invoices,
        average_payment=average_payment,
        highest_payment=highest_payment,
    )


# =============================================================================
# GET /client-payments/export/excel
# =============================================================================


@router.get("/export/excel", summary="Export Client Payments to Excel")
async def export_client_payments_excel(
    user_id: int | None = Query(None),
    project_id: int | None = Query(None),
    payment_method: PaymentMethod | None = Query(None),
    payment_status: PaymentStatus | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_REPORTING_ROLES)),
):
    """Export client payments to Excel file."""
    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_date cannot be greater than to_date.",
        )
    if search is not None:
        search = search.strip()
        if len(search) > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search text is too long.",
            )
        if not search:
            search = None

    stmt = select(ClientPayment).options(
        selectinload(ClientPayment.client_user),
        selectinload(ClientPayment.project),
        selectinload(ClientPayment.invoice),
    )

    if current_user.role == UserRole.CLIENT.value:
        stmt = stmt.where(ClientPayment.client_user_id == current_user.id)
    elif user_id is not None:
        await get_client_or_404(db, user_id)
        stmt = stmt.where(ClientPayment.client_user_id == user_id)

    if project_id is not None:
        stmt = stmt.where(ClientPayment.project_id == project_id)
    if payment_method is not None:
        stmt = stmt.where(ClientPayment.payment_method == payment_method)
    if payment_status is not None:
        stmt = stmt.where(ClientPayment.payment_status == payment_status)
    if from_date is not None:
        stmt = stmt.where(
            ClientPayment.payment_date >= datetime.combine(from_date, time.min)
        )
    if to_date is not None:
        stmt = stmt.where(
            ClientPayment.payment_date
            < datetime.combine(to_date + timedelta(days=1), time.min)
        )

    if search:
        stmt = (
            stmt.join(ClientPayment.client_user)
            .join(ClientPayment.project)
            .where(
                or_(
                    ClientPayment.payment_no.ilike(f"%{search}%"),
                    ClientPayment.reference_no.ilike(f"%{search}%"),
                    ClientPayment.cheque_no.ilike(f"%{search}%"),
                    User.full_name.ilike(f"%{search}%"),
                    Project.project_name.ilike(f"%{search}%"),
                )
            )
        )
    stmt = stmt.order_by(ClientPayment.created_at.desc())
    payments = (await db.execute(stmt)).scalars().unique().all()

    try:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Client Payments"
        headers = [
            "Payment No",
            "Client",
            "Project",
            "Invoice No",
            "Amount",
            "Payment Method",
            "Status",
            "Bank Name",
            "Cheque No",
            "Reference No",
            "Payment Date",
            "Verified By",
            "Verified At",
            "Remarks",
        ]
        sheet.append(headers)
        for payment in payments:
            sheet.append(
                [
                    payment.payment_no,
                    payment.client_user.full_name if payment.client_user else "",
                    payment.project.project_name if payment.project else "",
                    f"INV-{payment.invoice.id:06d}" if payment.invoice else "",
                    float(payment.amount),
                    payment.payment_method.value,
                    payment.payment_status.value,
                    payment.bank_name or "",
                    payment.cheque_no or "",
                    payment.reference_no or "",
                    (
                        payment.payment_date.strftime("%Y-%m-%d %H:%M")
                        if payment.payment_date
                        else ""
                    ),
                    payment.verified_by or "",
                    (
                        payment.verified_at.strftime("%Y-%m-%d %H:%M")
                        if payment.verified_at
                        else ""
                    ),
                    payment.remarks or "",
                ]
            )
        for column_cells in sheet.columns:
            length = max(
                (len(str(cell.value)) if cell.value is not None else 0)
                for cell in column_cells
            )
            sheet.column_dimensions[column_cells[0].column_letter].width = min(
                max(length + 2, 12), 40
            )
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
    except Exception:
        logger.exception("Unable to generate client payments Excel export.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to generate Excel export.",
        )

    filename = f"client_payments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    logger.info(
        "Client payments exported to Excel by User %s (%s rows)",
        current_user.id,
        len(payments),
    )
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =============================================================================
# GET /client-payments/export/pdf
# =============================================================================


@router.get("/export/pdf", summary="Export Client Payments to PDF")
async def export_client_payments_pdf(
    user_id: int | None = Query(None),
    project_id: int | None = Query(None),
    payment_method: PaymentMethod | None = Query(None),
    payment_status: PaymentStatus | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_REPORTING_ROLES)),
):
    """Export client payments to PDF report."""
    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_date cannot be greater than to_date.",
        )
    if search is not None:
        search = search.strip()
        if len(search) > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search text is too long.",
            )
        if not search:
            search = None

    stmt = select(ClientPayment).options(
        selectinload(ClientPayment.client_user),
        selectinload(ClientPayment.project),
        selectinload(ClientPayment.invoice),
    )

    if current_user.role == UserRole.CLIENT.value:
        stmt = stmt.where(ClientPayment.client_user_id == current_user.id)
    elif user_id is not None:
        await get_client_or_404(db, user_id)
        stmt = stmt.where(ClientPayment.client_user_id == user_id)

    if project_id is not None:
        stmt = stmt.where(ClientPayment.project_id == project_id)
    if payment_method is not None:
        stmt = stmt.where(ClientPayment.payment_method == payment_method)
    if payment_status is not None:
        stmt = stmt.where(ClientPayment.payment_status == payment_status)
    if from_date is not None:
        stmt = stmt.where(
            ClientPayment.payment_date >= datetime.combine(from_date, time.min)
        )
    if to_date is not None:
        stmt = stmt.where(
            ClientPayment.payment_date
            < datetime.combine(to_date + timedelta(days=1), time.min)
        )

    if search:
        stmt = (
            stmt.join(ClientPayment.client_user)
            .join(ClientPayment.project)
            .where(
                or_(
                    ClientPayment.payment_no.ilike(f"%{search}%"),
                    ClientPayment.reference_no.ilike(f"%{search}%"),
                    ClientPayment.cheque_no.ilike(f"%{search}%"),
                    User.full_name.ilike(f"%{search}%"),
                    Project.project_name.ilike(f"%{search}%"),
                )
            )
        )
    stmt = stmt.order_by(ClientPayment.created_at.desc())
    payments = (await db.execute(stmt)).scalars().unique().all()

    try:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=35,
            leftMargin=35,
            topMargin=35,
            bottomMargin=30,
        )
        styles = getSampleStyleSheet()
        content = []

        # Header
        content.append(Paragraph(f"<b>{COMPANY_NAME}</b>", styles["Title"]))
        content.append(Paragraph(COMPANY_ADDRESS_LINE, styles["Normal"]))
        content.append(Paragraph(COMPANY_CONTACT_LINE, styles["Normal"]))
        content.append(Spacer(1, 20))
        content.append(Paragraph("<b>Client Payments Report</b>", styles["Heading1"]))
        content.append(
            Paragraph(
                f"Generated On: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}",
                styles["Normal"],
            )
        )
        content.append(Spacer(1, 20))

        # Table
        table_data = [
            [
                "Payment No",
                "Client",
                "Project",
                "Amount",
                "Method",
                "Status",
                "Date",
            ]
        ]
        total_amount = 0
        for payment in payments:
            total_amount += float(payment.amount)
            table_data.append(
                [
                    payment.payment_no,
                    payment.client_user.full_name if payment.client_user else "",
                    payment.project.project_name if payment.project else "",
                    format_indian_currency(float(payment.amount)),
                    payment.payment_method.value,
                    payment.payment_status.value,
                    (
                        payment.payment_date.strftime("%d-%m-%Y")
                        if payment.payment_date
                        else ""
                    ),
                ]
            )

        payment_table = Table(
            table_data,
            colWidths=[70, 90, 90, 80, 60, 60, 70],
        )
        payment_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_COLOR),
                    ("TEXTCOLOR", (0, 0), (-1, 0), white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                    ("FONTSIZE", (0, 1), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        content.append(payment_table)
        content.append(Spacer(1, 20))

        # Summary
        content.append(
            Paragraph(
                f"<b>Total Payments:</b> {len(payments)}",
                styles["Normal"],
            )
        )
        content.append(
            Paragraph(
                f"<b>Total Amount:</b> {format_indian_currency(total_amount)}",
                styles["Normal"],
            )
        )
        content.append(Spacer(1, 30))
        content.append(
            Paragraph("This is a system generated report.", styles["Italic"])
        )

        doc.build(content)
        buffer.seek(0)
    except Exception:
        logger.exception("Unable to generate client payments PDF export.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to generate PDF export.",
        )

    filename = f"client_payments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =============================================================================
# POST /client-payments
# =============================================================================


@router.post(
    "",
    response_model=s.ClientPaymentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_client_payment(
    payload: s.ClientPaymentCreateForm = Depends(),
    receipt: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_CREATE_ROLES)),
):
    """Create a new client payment."""
    try:
        data = payload.to_schema()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid data format provided.",
        )

    # Trim string fields
    data.bank_name = data.bank_name.strip() if data.bank_name else None
    data.cheque_no = data.cheque_no.strip() if data.cheque_no else None
    data.reference_no = data.reference_no.strip() if data.reference_no else None
    data.remarks = data.remarks.strip() if data.remarks else None

    # Amount validation
    if data.amount is None or data.amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount must be greater than zero.",
        )

    validate_and_normalize_payment_fields(data)
    project = await get_project_or_404(db, data.project_id)
    await assert_client_is_project_member(db, data.project_id, current_user)
    await check_duplicate_payment(db, data)

    # Validate invoice
    invoice_obj = await db.get(Invoice, data.invoice_id)
    if invoice_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
        )
    if invoice_obj.project_id != data.project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invoice does not belong to this project.",
        )
    await assert_invoice_payable(invoice_obj, data.amount)

    await validate_receipt(receipt, data.payment_method)
    payment_status_value = create_payment_status(data.payment_method)

    payment: ClientPayment | None = None

    try:
        payment = ClientPayment(
            payment_no="",
            client_user_id=current_user.id,
            invoice_id=data.invoice_id,
            project_id=data.project_id,
            amount=data.amount,
            payment_method=data.payment_method,
            payment_status=payment_status_value,
            bank_name=data.bank_name,
            cheque_no=data.cheque_no,
            reference_no=data.reference_no,
            remarks=data.remarks,
            receipt_url=None,
            transaction_id=generate_transaction_id(data.payment_method),
            verified_by=None,
            verified_at=None,
        )

        db.add(payment)
        await db.flush()
        payment.payment_no = f"CP{payment.id:06d}"

        db.add(
            ActivityLog(
                action="CLIENT_PAYMENT_CREATED",
                entity="client_payment",
                entity_id=payment.id,
                performed_by=current_user.id,
                details={
                    "payment_no": payment.payment_no,
                    "amount": str(payment.amount),
                    "invoice_id": payment.invoice_id,
                    "project_id": payment.project_id,
                },
            )
        )

        if receipt:
            payment.receipt_url = await save_receipt(receipt, payment.payment_no)

        await db.commit()

        stmt = (
            select(ClientPayment)
            .where(ClientPayment.id == payment.id)
            .options(
                selectinload(ClientPayment.client_user),
                selectinload(ClientPayment.project),
                selectinload(ClientPayment.invoice),
            )
        )
        payment = (await db.execute(stmt)).scalar_one()

    except IntegrityError:
        await db.rollback()
        if payment:
            _safe_remove(payment.receipt_url)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Duplicate payment found."
        )
    except HTTPException:
        await db.rollback()
        if payment:
            _safe_remove(payment.receipt_url)
        raise
    except Exception:
        await db.rollback()
        if payment:
            _safe_remove(payment.receipt_url)
        logger.exception("Unable to create client payment.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create payment.",
        )

    try:
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.warning("Unable to update cache version.")

    logger.info("Payment %s created by User %s", payment.payment_no, current_user.id)
    return build_payment_response(payment, invoice=invoice_obj)


# =============================================================================
# GET /client-payments
# =============================================================================


@router.get("", response_model=list[s.ClientPaymentOut])
async def list_client_payments(
    user_id: int | None = Query(None),
    project_id: int | None = Query(None),
    payment_method: PaymentMethod | None = Query(None),
    payment_status: PaymentStatus | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_READ_ROLES)),
):
    """List client payments with filters."""
    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_date cannot be greater than to_date.",
        )

    if search is not None:
        search = search.strip()
        if len(search) > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search text is too long.",
            )
        if not search:
            search = None

    stmt = select(ClientPayment).options(
        selectinload(ClientPayment.client_user),
        selectinload(ClientPayment.project),
        selectinload(ClientPayment.invoice),
    )

    # SECURITY: CLIENT may only see their own payments
    if current_user.role == UserRole.CLIENT.value:
        stmt = stmt.where(ClientPayment.client_user_id == current_user.id)
    elif user_id is not None:
        # Admin/Accountant filtering by client
        if project_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="project_id is required when filtering by user_id.",
            )
        await validate_client_project_membership(
            db=db,
            client_user_id=user_id,
            project_id=project_id,
        )
        stmt = stmt.where(ClientPayment.client_user_id == user_id)

    if project_id is not None:
        stmt = stmt.where(ClientPayment.project_id == project_id)
    if payment_method is not None:
        stmt = stmt.where(ClientPayment.payment_method == payment_method)
    if payment_status is not None:
        stmt = stmt.where(ClientPayment.payment_status == payment_status)
    if from_date is not None:
        stmt = stmt.where(
            ClientPayment.payment_date >= datetime.combine(from_date, time.min)
        )
    if to_date is not None:
        stmt = stmt.where(
            ClientPayment.payment_date
            < datetime.combine(to_date + timedelta(days=1), time.min)
        )

    if search:
        stmt = (
            stmt.join(ClientPayment.client_user)
            .join(ClientPayment.project)
            .where(
                or_(
                    ClientPayment.payment_no.ilike(f"%{search}%"),
                    ClientPayment.reference_no.ilike(f"%{search}%"),
                    ClientPayment.cheque_no.ilike(f"%{search}%"),
                    User.full_name.ilike(f"%{search}%"),
                    Project.project_name.ilike(f"%{search}%"),
                )
            )
        )

    stmt = stmt.order_by(ClientPayment.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    payments = result.scalars().unique().all()

    return [build_payment_response(p) for p in payments]


# =============================================================================
# GET /client-payments/{payment_id}
# =============================================================================


@router.get("/{payment_id}", response_model=s.ClientPaymentOut)
async def get_client_payment(
    payment_id: int = ApiPath(..., gt=0),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_READ_ROLES)),
):
    """Get a single client payment by ID."""
    stmt = (
        select(ClientPayment)
        .where(ClientPayment.id == payment_id)
        .options(
            selectinload(ClientPayment.client_user),
            selectinload(ClientPayment.project),
            selectinload(ClientPayment.invoice),
        )
    )
    payment = (await db.execute(stmt)).scalar_one_or_none()

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found.",
        )

    assert_client_owns_payment(current_user, payment)
    return build_payment_response(payment)


# =============================================================================
# PUT /client-payments/{payment_id}
# =============================================================================


@router.put("/{payment_id}", response_model=s.ClientPaymentOut)
async def update_client_payment(
    payment_id: int = ApiPath(..., gt=0),
    payload: s.ClientPaymentUpdateForm = Depends(),
    receipt: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_CREATE_ROLES)),
):
    """Update a client payment (only before verification)."""
    stmt = (
        select(ClientPayment)
        .where(ClientPayment.id == payment_id)
        .options(
            selectinload(ClientPayment.client_user),
            selectinload(ClientPayment.project),
            selectinload(ClientPayment.invoice),
        )
    )
    payment = (await db.execute(stmt)).scalar_one_or_none()

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found.",
        )

    assert_client_owns_payment(current_user, payment)

    # Only allow editing if not yet verified
    if payment.payment_status not in (
        PaymentStatus.PENDING,
        PaymentStatus.VERIFICATION_PENDING,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment cannot be updated after verification.",
        )

    try:
        data = payload.to_schema()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid data format provided.",
        )

    # Trim string fields
    data.bank_name = data.bank_name.strip() if data.bank_name else None
    data.cheque_no = data.cheque_no.strip() if data.cheque_no else None
    data.reference_no = data.reference_no.strip() if data.reference_no else None
    data.remarks = data.remarks.strip() if data.remarks else None

    # Amount validation
    if data.amount is not None and data.amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount must be greater than zero.",
        )

    validate_and_normalize_payment_fields(data)
    await check_duplicate_payment(db, data, exclude_payment_id=payment_id)

    # Validate invoice if changed
    if data.invoice_id and data.invoice_id != payment.invoice_id:
        invoice_obj = await db.get(Invoice, data.invoice_id)
        if not invoice_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found."
            )
        if invoice_obj.project_id != payment.project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invoice does not belong to this project.",
            )
        await assert_invoice_payable(invoice_obj, data.amount or payment.amount)
    elif data.amount and data.amount != payment.amount:
        await assert_invoice_payable(payment.invoice, data.amount)

    await validate_receipt(receipt, data.payment_method)

    old_receipt_url = payment.receipt_url

    try:
        # Update fields
        if data.amount is not None:
            payment.amount = data.amount
        if data.payment_method is not None:
            payment.payment_method = data.payment_method
        payment.bank_name = data.bank_name
        payment.cheque_no = data.cheque_no
        payment.reference_no = data.reference_no
        payment.remarks = data.remarks
        if data.invoice_id:
            payment.invoice_id = data.invoice_id
        if data.payment_method:
            payment.transaction_id = generate_transaction_id(data.payment_method)

        if receipt:
            payment.receipt_url = await save_receipt(receipt, payment.payment_no)

        db.add(
            ActivityLog(
                action="CLIENT_PAYMENT_UPDATED",
                entity="client_payment",
                entity_id=payment.id,
                performed_by=current_user.id,
                details={
                    "payment_no": payment.payment_no,
                    "amount": str(payment.amount),
                },
            )
        )

        await db.commit()

        # Remove old receipt if replaced
        if old_receipt_url and old_receipt_url != payment.receipt_url:
            _safe_remove(old_receipt_url)

        await db.refresh(payment)
        # Reload relations
        stmt = (
            select(ClientPayment)
            .where(ClientPayment.id == payment.id)
            .options(
                selectinload(ClientPayment.client_user),
                selectinload(ClientPayment.project),
                selectinload(ClientPayment.invoice),
            )
        )
        payment = (await db.execute(stmt)).scalar_one()

    except IntegrityError:
        await db.rollback()
        if payment.receipt_url != old_receipt_url:
            _safe_remove(payment.receipt_url)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Duplicate payment found."
        )
    except HTTPException:
        await db.rollback()
        if payment.receipt_url != old_receipt_url:
            _safe_remove(payment.receipt_url)
        raise
    except Exception:
        await db.rollback()
        if payment.receipt_url != old_receipt_url:
            _safe_remove(payment.receipt_url)
        logger.exception("Unable to update client payment.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to update payment.",
        )

    try:
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.warning("Unable to update cache version.")

    logger.info("Payment %s updated by User %s", payment.payment_no, current_user.id)
    return build_payment_response(payment)


# =============================================================================
# DELETE /client-payments/{payment_id}
# =============================================================================


@router.delete("/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client_payment(
    payment_id: int = ApiPath(..., gt=0),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_CREATE_ROLES)),
):
    """Cancel/delete a client payment (only before verification)."""
    stmt = (
        select(ClientPayment)
        .where(ClientPayment.id == payment_id)
        .options(
            selectinload(ClientPayment.invoice),
        )
    )
    payment = (await db.execute(stmt)).scalar_one_or_none()

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found.",
        )

    assert_client_owns_payment(current_user, payment)

    # Only allow cancellation if not yet verified
    if payment.payment_status not in (
        PaymentStatus.PENDING,
        PaymentStatus.VERIFICATION_PENDING,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment cannot be cancelled after verification.",
        )

    receipt_url = payment.receipt_url

    try:
        # If linked to invoice, restore pending amount
        if payment.invoice:
            payment.invoice.paid_amount -= payment.amount
            payment.invoice.pending_amount += payment.amount
            if payment.invoice.paid_amount <= 0:
                payment.invoice.paid_amount = 0
            if payment.invoice.status == InvoiceStatus.PARTIAL:
                # Check if still partial or fully unpaid
                if payment.invoice.pending_amount >= payment.invoice.total_amount:
                    payment.invoice.status = InvoiceStatus.PENDING

        db.add(
            ActivityLog(
                action="CLIENT_PAYMENT_DELETED",
                entity="client_payment",
                entity_id=payment.id,
                performed_by=current_user.id,
                details={
                    "payment_no": payment.payment_no,
                    "amount": str(payment.amount),
                },
            )
        )

        await db.delete(payment)
        await db.commit()

        _safe_remove(receipt_url)

    except Exception:
        await db.rollback()
        logger.exception("Unable to delete client payment.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to cancel payment.",
        )

    try:
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.warning("Unable to update cache version.")

    logger.info("Payment %s cancelled by User %s", payment.payment_no, current_user.id)
    return None


# =============================================================================
# POST /client-payments/{payment_id}/verify
# =============================================================================


class VerifyPaymentRequest(BaseModel):
    action: str  # "approve" or "reject"
    remarks: str | None = None


@router.post("/{payment_id}/verify", response_model=s.ClientPaymentOut)
async def verify_client_payment(
    payment_id: int = ApiPath(..., gt=0),
    payload: VerifyPaymentRequest = ...,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(require_roles(PAYMENT_VERIFY_ROLES)),
):
    """Verify (approve/reject) a client payment. Staff-only endpoint."""
    stmt = (
        select(ClientPayment)
        .where(ClientPayment.id == payment_id)
        .options(
            selectinload(ClientPayment.client_user),
            selectinload(ClientPayment.project),
            selectinload(ClientPayment.invoice),
        )
    )
    payment = (await db.execute(stmt)).scalar_one_or_none()

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found.",
        )

    # Must be in verification pending state
    if payment.payment_status != PaymentStatus.VERIFICATION_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment is not pending verification.",
        )

    action = payload.action.lower().strip()
    if action not in ("approve", "reject"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action must be 'approve' or 'reject'.",
        )

    try:
        if action == "approve":
            payment.payment_status = PaymentStatus.SUCCESS
            payment.verified_by = current_user.id
            payment.verified_at = datetime.now(timezone.utc)

            # Update invoice
            if payment.invoice:
                payment.invoice.paid_amount += payment.amount
                payment.invoice.pending_amount -= payment.amount
                if payment.invoice.pending_amount <= 0:
                    payment.invoice.pending_amount = 0
                    payment.invoice.status = InvoiceStatus.PAID
                else:
                    payment.invoice.status = InvoiceStatus.PARTIAL

                # Create transaction record
                txn = Transaction(
                    project_id=payment.project_id,
                    invoice_id=payment.invoice_id,
                    type="receipt",
                    amount=payment.amount,
                    mode=payment.payment_method.value,
                    reference=payment.payment_no,
                    created_by=current_user.id,
                )
                db.add(txn)

                # Auto post journal
                await auto_post_journal(
                    db=db,
                    amount=payment.amount,
                    debit_code="1001",  # Cash/Bank
                    credit_code="1200",  # Accounts Receivable
                    description=f"Client payment {payment.payment_no} verified",
                    reference_type="client_payment",
                    reference_id=payment.id,
                    created_by=current_user.id,
                )

            # Notify client
            if payment.client_user_id:
                await send_notification(
                    db=db,
                    user_id=payment.client_user_id,
                    title="Payment Approved",
                    message=f"Your payment {payment.payment_no} of Rs. {payment.amount:,.2f} has been approved.",
                    notification_type="SUCCESS",
                )

            log_action = "CLIENT_PAYMENT_APPROVED"

        else:  # reject
            payment.payment_status = PaymentStatus.REJECTED
            payment.verified_by = current_user.id
            payment.verified_at = datetime.now(timezone.utc)

            # Notify client
            if payment.client_user_id:
                await send_notification(
                    db=db,
                    user_id=payment.client_user_id,
                    title="Payment Rejected",
                    message=f"Your payment {payment.payment_no} has been rejected. {payload.remarks or 'Please contact support.'}",
                    notification_type="ALERT",
                )

            log_action = "CLIENT_PAYMENT_REJECTED"

        # Append verifier remarks
        if payload.remarks:
            existing_remarks = payment.remarks or ""
            payment.remarks = (
                f"{existing_remarks}\n[Verifier: {payload.remarks}]".strip()
            )

        db.add(
            ActivityLog(
                action=log_action,
                entity="client_payment",
                entity_id=payment.id,
                performed_by=current_user.id,
                details={
                    "payment_no": payment.payment_no,
                    "action": action,
                    "remarks": payload.remarks,
                },
            )
        )

        await db.commit()

        # Reload
        stmt = (
            select(ClientPayment)
            .where(ClientPayment.id == payment.id)
            .options(
                selectinload(ClientPayment.client_user),
                selectinload(ClientPayment.project),
                selectinload(ClientPayment.invoice),
            )
        )
        payment = (await db.execute(stmt)).scalar_one()

    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        logger.exception("Unable to verify client payment.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to verify payment.",
        )

    try:
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.warning("Unable to update cache version.")

    logger.info(
        "Payment %s %s by User %s",
        payment.payment_no,
        action,
        current_user.id,
    )
    return build_payment_response(payment)


# =============================================================================
# GET /client-payments/{payment_id}/receipt
# =============================================================================


@router.get("/{payment_id}/receipt")
async def download_payment_receipt(
    payment_id: int = ApiPath(..., gt=0),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CLIENT_PAYMENT_READ_ROLES)),
):
    """Download payment receipt as PDF."""
    stmt = (
        select(ClientPayment)
        .where(ClientPayment.id == payment_id)
        .options(
            selectinload(ClientPayment.client_user),
            selectinload(ClientPayment.project),
            selectinload(ClientPayment.invoice),
        )
    )
    payment = (await db.execute(stmt)).scalar_one_or_none()

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found.",
        )

    assert_client_owns_payment(current_user, payment)

    # Only generate receipt for successful/paid payments
    if payment.payment_status not in (PaymentStatus.SUCCESS,):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Receipt is only available for verified payments.",
        )

    try:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=35,
            leftMargin=35,
            topMargin=35,
            bottomMargin=30,
        )
        styles = getSampleStyleSheet()
        content = []

        # Company Header
        content.append(Paragraph(f"<b>{COMPANY_NAME}</b>", styles["Title"]))
        content.append(Paragraph(COMPANY_ADDRESS_LINE, styles["Normal"]))
        content.append(Paragraph(COMPANY_CONTACT_LINE, styles["Normal"]))
        content.append(Spacer(1, 25))

        # Receipt Title
        content.append(Paragraph("<b>PAYMENT RECEIPT</b>", styles["Heading1"]))
        content.append(Spacer(1, 20))

        # Receipt Details Table
        receipt_no = generate_receipt_no(payment)
        receipt_data = [
            ["Receipt No", receipt_no],
            ["Payment No", payment.payment_no],
            [
                "Payment Date",
                (
                    payment.payment_date.strftime("%d-%m-%Y %H:%M")
                    if payment.payment_date
                    else "N/A"
                ),
            ],
            ["Payment Method", payment.payment_method.value.upper()],
            ["Status", payment.payment_status.value.upper()],
        ]

        if payment.invoice:
            receipt_data.append(
                [
                    "Invoice No",
                    f"INV-{payment.invoice.id:06d}",
                ]
            )

        if payment.transaction_id:
            receipt_data.append(["Transaction ID", payment.transaction_id])

        receipt_table = Table(receipt_data, colWidths=[150, 320])
        receipt_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        content.append(receipt_table)
        content.append(Spacer(1, 20))

        # Party Details
        content.append(Paragraph("<b>Party Details</b>", styles["Heading2"]))
        content.append(Spacer(1, 10))
        party_data = [
            [
                "Client Name",
                payment.client_user.full_name if payment.client_user else "N/A",
            ],
            ["Project", payment.project.project_name if payment.project else "N/A"],
        ]
        party_table = Table(party_data, colWidths=[150, 320])
        party_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        content.append(party_table)
        content.append(Spacer(1, 20))

        # Amount Details
        content.append(Paragraph("<b>Payment Details</b>", styles["Heading2"]))
        content.append(Spacer(1, 10))
        amount_data = [
            ["Amount", format_indian_currency(float(payment.amount))],
            ["In Words", number_to_words_indian(float(payment.amount))],
        ]
        if payment.bank_name:
            amount_data.append(["Bank Name", payment.bank_name])
        if payment.cheque_no:
            amount_data.append(["Cheque No", payment.cheque_no])
        if payment.reference_no:
            amount_data.append(["Reference No", payment.reference_no])

        amount_table = Table(amount_data, colWidths=[150, 320])
        amount_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 0), (1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (1, 0), (1, 0), 12),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        content.append(amount_table)

        if payment.remarks:
            content.append(Spacer(1, 15))
            content.append(
                Paragraph(f"<b>Remarks:</b> {payment.remarks}", styles["Normal"])
            )

        content.append(Spacer(1, 40))

        # Footer
        content.append(
            Paragraph(
                "This is a system generated receipt and does not require signature.",
                styles["Italic"],
            )
        )
        content.append(
            Paragraph(
                f"Generated On: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}",
                styles["Italic"],
            )
        )

        doc.build(content)
        buffer.seek(0)

    except Exception:
        logger.exception("Unable to generate receipt PDF.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to generate receipt.",
        )

    filename = f"receipt_{payment.payment_no}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
