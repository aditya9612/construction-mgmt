from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from fastapi import Form
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
        invoice_id: int = Form(...),
        project_id: int = Form(...),
        amount: Decimal = Form(...),
        payment_method: PaymentMethod = Form(...),
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
        return ClientPaymentCreate(
            invoice_id=self.invoice_id,
            project_id=self.project_id,
            amount=self.amount,
            payment_method=self.payment_method,
            bank_name=(self.bank_name or "").strip() or None,
            cheque_no=(self.cheque_no or "").strip() or None,
            reference_no=(self.reference_no or "").strip() or None,
            remarks=(self.remarks or "").strip() or None,
        )


# =====================================================
# UPDATE
# =====================================================
class ClientPaymentUpdateForm:
    invoice_id: int
    project_id: int
    amount: Decimal
    payment_method: PaymentMethod

    bank_name: str | None = None
    cheque_no: str | None = None
    reference_no: str | None = None
    remarks: str | None = None

    def to_schema(self):
        return ClientPaymentCreate(
            invoice_id=self.invoice_id,
            project_id=self.project_id,
            amount=self.amount,
            payment_method=self.payment_method,
            bank_name=self.bank_name,
            cheque_no=self.cheque_no,
            reference_no=self.reference_no,
            remarks=self.remarks,
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