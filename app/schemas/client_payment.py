from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Optional

from fastapi import Form, HTTPException, status
from fastapi.params import Form as FormParam
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import InvoiceStatus, PaymentMethod, PaymentStatus

# =====================================================
# BASE
# =====================================================


class ClientPaymentBase(BaseModel):

    invoice_id: int = Field(..., gt=0)

    project_id: int = Field(..., gt=0)

    amount: Decimal = Field(..., gt=0, decimal_places=2)

    payment_method: PaymentMethod

    bank_name: Optional[str] = Field(None, max_length=100)

    cheque_no: Optional[str] = Field(None, max_length=50)

    reference_no: Optional[str] = Field(None, max_length=100)

    remarks: Optional[str] = Field(None, max_length=500)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v is None or v <= Decimal("0"):
            raise ValueError("Paid Amount must be greater than zero.")
        return v


# =====================================================
# CREATE
# =====================================================


class ClientPaymentCreate(ClientPaymentBase):
    pass


# =====================================================
# FORM
# =====================================================


class ClientPaymentCreateForm:

    def __init__(
        self,
        invoice_id: Optional[Any] = Form(None),
        project_id: Optional[Any] = Form(None),
        amount: Optional[Any] = Form(None),
        payment_method: Optional[Any] = Form(None),
        bank_name: Optional[str] = Form(None),
        cheque_no: Optional[str] = Form(None),
        reference_no: Optional[str] = Form(None),
        remarks: Optional[str] = Form(None),
    ):
        self.invoice_id = invoice_id
        self.project_id = project_id
        self.amount = amount
        self.payment_method = payment_method
        self.bank_name = bank_name
        self.cheque_no = cheque_no
        self.reference_no = reference_no
        self.remarks = remarks

    def to_schema(self) -> ClientPaymentCreate:
        def is_empty(val):
            if val is None or isinstance(val, FormParam):
                return True
            if isinstance(val, str) and not val.strip():
                return True
            return False

        if (
            is_empty(self.invoice_id)
            or is_empty(self.project_id)
            or is_empty(self.amount)
            or is_empty(self.payment_method)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please fill out all required fields.",
            )

        try:
            inv_id = int(self.invoice_id)
            if inv_id <= 0:
                raise ValueError()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid invoice ID.",
            )

        try:
            proj_id = int(self.project_id)
            if proj_id <= 0:
                raise ValueError()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid project ID.",
            )

        try:
            amt = Decimal(str(self.amount).strip())
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid payment amount.",
            )

        if amt <= Decimal("0"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paid Amount must be greater than zero.",
            )

        if isinstance(self.payment_method, PaymentMethod):
            pm = self.payment_method
        else:
            pm_raw = str(self.payment_method).strip()
            try:
                pm = PaymentMethod(pm_raw.upper())
            except ValueError:
                try:
                    pm = PaymentMethod(pm_raw)
                except ValueError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid payment method: {pm_raw}",
                    )

        bank = (
            (self.bank_name or "").strip() or None
            if not isinstance(self.bank_name, FormParam)
            else None
        )
        cheque = (
            (self.cheque_no or "").strip() or None
            if not isinstance(self.cheque_no, FormParam)
            else None
        )
        ref = (
            (self.reference_no or "").strip() or None
            if not isinstance(self.reference_no, FormParam)
            else None
        )
        rem = (
            (self.remarks or "").strip() or None
            if not isinstance(self.remarks, FormParam)
            else None
        )

        return ClientPaymentCreate(
            invoice_id=inv_id,
            project_id=proj_id,
            amount=amt,
            payment_method=pm,
            bank_name=bank,
            cheque_no=cheque,
            reference_no=ref,
            remarks=rem,
        )


# =====================================================
# UPDATE
# =====================================================
class ClientPaymentUpdateForm:
    invoice_id: Optional[Any] = None
    project_id: Optional[Any] = None
    amount: Optional[Any] = None
    payment_method: Optional[Any] = None

    bank_name: str | None = None
    cheque_no: str | None = None
    reference_no: str | None = None
    remarks: str | None = None

    def to_schema(self) -> ClientPaymentCreate:
        def is_empty(val):
            if val is None or isinstance(val, FormParam):
                return True
            if isinstance(val, str) and not val.strip():
                return True
            return False

        if (
            is_empty(self.invoice_id)
            or is_empty(self.project_id)
            or is_empty(self.amount)
            or is_empty(self.payment_method)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please fill out all required fields.",
            )

        try:
            inv_id = int(self.invoice_id)
            if inv_id <= 0:
                raise ValueError()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid invoice ID.",
            )

        try:
            proj_id = int(self.project_id)
            if proj_id <= 0:
                raise ValueError()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid project ID.",
            )

        try:
            amt = Decimal(str(self.amount).strip())
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid payment amount.",
            )

        if amt <= Decimal("0"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paid Amount must be greater than zero.",
            )

        if isinstance(self.payment_method, PaymentMethod):
            pm = self.payment_method
        else:
            pm_raw = str(self.payment_method).strip()
            try:
                pm = PaymentMethod(pm_raw.upper())
            except ValueError:
                try:
                    pm = PaymentMethod(pm_raw)
                except ValueError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid payment method: {pm_raw}",
                    )

        bank = (self.bank_name or "").strip() or None
        cheque = (self.cheque_no or "").strip() or None
        ref = (self.reference_no or "").strip() or None
        rem = (self.remarks or "").strip() or None

        return ClientPaymentCreate(
            invoice_id=inv_id,
            project_id=proj_id,
            amount=amt,
            payment_method=pm,
            bank_name=bank,
            cheque_no=cheque,
            reference_no=ref,
            remarks=rem,
        )


# =====================================================
# VERIFY
# =====================================================


class ClientPaymentVerify(BaseModel):

    payment_status: Literal[
        PaymentStatus.SUCCESS,
        PaymentStatus.REJECTED,
    ]

    remarks: Optional[str] = Field(None, max_length=500)


# =====================================================
# RESPONSE
# =====================================================


class ClientPaymentOut(ClientPaymentBase):

    model_config = ConfigDict(from_attributes=True)

    id: int

    payment_no: str

    payment_status: PaymentStatus

    transaction_id: Optional[str] = None

    receipt_url: Optional[str] = None

    payment_date: datetime

    verified_by: Optional[int] = None

    verified_at: Optional[datetime] = None

    created_at: datetime

    updated_at: datetime

    user_name: Optional[str] = None

    project_name: Optional[str] = None

    invoice_no: Optional[str] = None

    invoice_status: Optional[InvoiceStatus] = None

    pending_amount: Optional[Decimal] = None


# =====================================================
# INVOICE SUMMARY
# =====================================================


class InvoiceSummaryOut(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    invoice_id: int
    invoice_no: str

    project_id: int
    project_name: Optional[str] = None

    owner_id: int
    owner_name: Optional[str] = None

    quotation_id: Optional[int] = None

    amount: Decimal
    gst_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal

    paid_amount: Decimal
    pending_amount: Decimal

    payment_count: int

    status: InvoiceStatus

    description: Optional[str] = None

    created_at: datetime

    last_payment_date: Optional[datetime] = None


# =====================================================
# INVOICE PAYMENT HISTORY
# =====================================================


class InvoicePaymentHistoryOut(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    id: int

    payment_no: str

    amount: Decimal

    payment_method: PaymentMethod

    payment_status: PaymentStatus

    bank_name: Optional[str] = None

    cheque_no: Optional[str] = None

    reference_no: Optional[str] = None

    transaction_id: Optional[str] = None

    remarks: Optional[str] = None

    payment_date: datetime

    receipt_url: Optional[str] = None

    receipt_download_url: Optional[str] = None

    verified_by: Optional[int] = None

    verified_at: Optional[datetime] = None

    verified_by_name: Optional[str] = None


class InvoicePaymentHistoryList(BaseModel):

    total: int

    limit: int

    offset: int

    items: list[InvoicePaymentHistoryOut]


# =====================================================
# PENDING INVOICES
# =====================================================


class PendingInvoiceOut(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    invoice_id: int

    invoice_no: str

    project_id: int

    project_name: str

    total_amount: Decimal

    paid_amount: Decimal

    pending_amount: Decimal

    due_date: Optional[datetime] = None

    status: str


class PendingInvoiceList(BaseModel):

    total: int

    limit: int

    offset: int

    items: list[PendingInvoiceOut]


# =====================================================
# ANALYTICS
# =====================================================


class PaymentMethodAnalytics(BaseModel):
    cash: Decimal
    cheque: Decimal
    upi: Decimal
    neft: Decimal
    rtgs: Decimal
    online: Decimal


class MonthlyCollection(BaseModel):
    month: int
    year: int
    total_amount: Decimal


class ClientPaymentAnalyticsOut(BaseModel):

    payment_methods: PaymentMethodAnalytics

    monthly_collection: list[MonthlyCollection]

    total_collection: Decimal

    successful_payments: int

    rejected_payments: int

    pending_verification: int

    total_invoices: int

    overdue_invoices: int

    average_payment: Decimal

    highest_payment: Decimal


class ClientDashboardSummary(BaseModel):

    total_invoices: int

    total_invoice_amount: Decimal

    total_paid: Decimal

    total_pending: Decimal

    overdue_amount: Decimal

    today_collection: Decimal

    this_month_collection: Decimal

    payment_success_rate: float


class ExportResponse(BaseModel):

    filename: str

    download_url: str


class ReceiptOut(BaseModel):

    payment_no: str

    receipt_download_url: str


class PaymentTimelineOut(BaseModel):

    payment_no: str

    payment_status: PaymentStatus

    created_at: datetime

    verified_at: Optional[datetime] = None

    remarks: Optional[str] = None