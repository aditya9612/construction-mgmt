from pydantic import BaseModel, Field, model_validator
from typing import Optional
from decimal import Decimal
from datetime import date, datetime
from app.core.enums import (
    InvoiceStatus,
    InvoiceType,
    InvoiceSourceType,
)
from decimal import Decimal


class InvoiceBase(BaseModel):
    project_id: int

    type: InvoiceType
    source_type: Optional[InvoiceSourceType] = None

    reference_id: Optional[int] = None

    amount: float
    gst_percent: float = 0
    tax_percent: float = 0

    description: Optional[str] = None


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(BaseModel):
    amount: Optional[float] = None
    gst_percent: Optional[float] = None
    tax_percent: Optional[float] = None

    source_type: Optional[InvoiceSourceType] = None

    description: Optional[str] = None


class InvoiceOut(BaseModel):
    id: int
    project_id: int
    owner_id: int

    type: InvoiceType
    source_type: Optional[InvoiceSourceType] = None

    reference_id: Optional[int]
    quotation_id: Optional[int]

    amount: float
    gst_percent: float
    gst_amount: float
    tax_percent: float
    tax_amount: float
    total_amount: float
    paid_amount: float
    pending_amount: float

    status: InvoiceStatus
    description: Optional[str]

    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    party_gstin: Optional[str] = None
    cgst: Optional[float] = None
    sgst: Optional[float] = None
    igst: Optional[float] = None
    invoice_copy_url: Optional[str] = None
    gst_document_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class LabourInvoiceCreate(BaseModel):
    project_id: int
    start_date: date
    end_date: date


class AnalyticsSummaryOut(BaseModel):
    progress_percent: float
    financial_progress_percent: float
    total_expense: float
    total_revenue: float


class ReceivablesSummaryOut(BaseModel):
    portfolio_value: float
    total_billed: float
    total_received: float
    pending_amount: float
    overdue_amount: float


class ManualReceivableCreate(BaseModel):
    client_id: int
    amount: float
    description: str
    due_date: date
    reference: Optional[str] = None


class ClientLedgerTransactionOut(BaseModel):
    date: datetime
    particulars: str
    debit: float
    credit: float
    running_balance: float


class ClientLedgerResponse(BaseModel):
    total_billed: float
    total_received: float
    outstanding: float
    transactions: list[ClientLedgerTransactionOut]


class CollectionOut(BaseModel):
    invoice_no: str
    client: str
    amount_received: float
    received_on: datetime
    mode: str
    reference: str
    status: str


class CreateInvoice(BaseModel):
    project_id: int

    owner_id: int

    amount: Decimal = Field(gt=0)

    gst_percent: Decimal = Field(default=0)

    tax_percent: Decimal = Field(default=0)

    description: Optional[str] = None

    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    party_gstin: Optional[str] = None
    cgst: Optional[Decimal] = Field(default=Decimal('0.0'))
    sgst: Optional[Decimal] = Field(default=Decimal('0.0'))
    igst: Optional[Decimal] = Field(default=Decimal('0.0'))
    invoice_copy_url: Optional[str] = None
    gst_document_url: Optional[str] = None

    @model_validator(mode='after')
    def validate_gst_split(self) -> 'CreateInvoice':
        cgst = self.cgst or Decimal('0.0')
        sgst = self.sgst or Decimal('0.0')
        igst = self.igst or Decimal('0.0')

        if cgst < 0 or sgst < 0 or igst < 0:
            raise ValueError('GST components cannot be negative')

        if igst > 0 and (cgst > 0 or sgst > 0):
            raise ValueError('IGST cannot be combined with CGST/SGST')

        total_split = cgst + sgst + igst
        if total_split > 0:
            amt = self.amount or Decimal('0.0')
            pct = self.gst_percent or Decimal('0.0')
            expected_gst = round((amt * pct) / Decimal('100.0'), 2)
            if round(total_split, 2) != expected_gst:
                raise ValueError(f'Total GST split {total_split} must reconcile exactly with calculated GST amount {expected_gst}')
        return self


class InvoiceList(BaseModel):

    total: int

    items: list[InvoiceOut]


class InvoiceFilter(BaseModel):

    project_id: Optional[int] = None

    owner_id: Optional[int] = None

    status: Optional[InvoiceStatus] = None


class SendInvoiceRequest(BaseModel):
    client_user_id: int


class SendInvoiceResponse(BaseModel):
    message: str
    invoice_id: int
    client_user_id: int
