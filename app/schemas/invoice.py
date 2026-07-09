from pydantic import BaseModel, Field
from typing import Optional
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


class SendInvoiceResponse(BaseModel):

    message: str

    invoice_id: int

    client_user_id: int


class InvoiceList(BaseModel):

    total: int

    items: list[InvoiceOut]


class InvoiceFilter(BaseModel):

    project_id: Optional[int] = None

    owner_id: Optional[int] = None

    status: Optional[InvoiceStatus] = None