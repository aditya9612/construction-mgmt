from datetime import date, datetime

from pydantic import BaseModel, Field
from typing import Optional, List
from decimal import Decimal

from app.core.enums import PaymentMode


# ============================
#  PAYMENT REQUEST
# ============================
class PayablePaymentRequest(BaseModel):
    amount: Decimal
    mode: PaymentMode
    reference: Optional[str] = None


# ============================
#  PAYABLE VIEW
# ============================
class PayableOut(BaseModel):
    ra_id: int
    project_id: int
    contractor_id: Optional[int]

    total_amount: float
    paid_amount: float
    pending_amount: float

    status: str


# ============================
#  SUMMARY
# ============================
class PayableSummary(BaseModel):
    total: float
    paid: float
    pending: float


# ============================
#  CASHFLOW
# ============================
class CashflowOut(BaseModel):
    inflow: float
    outflow: float
    balance: float


# ============================
#  TRANSACTION OUT
# ============================
class TransactionOut(BaseModel):
    id: int
    project_id: int
    invoice_id: Optional[int]

    type: str
    amount: float

    mode: str
    reference: Optional[str]



# ============================
#  RECEIPT
# ============================
class ReceiptCreate(BaseModel):
    project_id: int
    amount: Decimal
    mode: PaymentMode
    reference: Optional[str] = None


# ============================
#  ACCOUNT (COA)
# ============================
class AccountCreate(BaseModel):
    name: str
    code: str
    type: str
    parent_id: Optional[int] = None


class AccountOut(BaseModel):
    id: int
    name: str
    code: str
    type: str
    parent_id: Optional[int]

    class Config:
        from_attributes = True


# ============================
#  JOURNAL
# ============================
class JournalLineCreate(BaseModel):
    account_id: int
    debit: Decimal = 0
    credit: Decimal = 0


class JournalEntryCreate(BaseModel):
    description: str
    lines: List[JournalLineCreate]


class AssetCreate(BaseModel):
    name: str
    purchase_value: Decimal
    purchase_date: Optional[date] = None
    depreciation_rate: Optional[Decimal] = 10
    project_id: Optional[int] = None


class OfferCreate(BaseModel):
    project_name: str
    society_name: str
    address: str

    developer_name: str
    contact_email: Optional[str]
    contact_phone: Optional[str]

    extra_carpet_percent: int = Field(..., gt=0, lt=100)
    note: Optional[str]


class OfferOut(BaseModel):
    id: int
    project_name: str
    society_name: str
    address: str
    extra_carpet_percent: int

    class Config:
        from_attributes = True


# ===================== NEW ACCOUNTING SCHEMAS =====================

class BankTransactionCreate(BaseModel):
    bank_account_id: int
    transaction_date: date
    amount: float
    type: str
    description: Optional[str] = None
    reference_number: Optional[str] = None

class BankTransactionOut(BankTransactionCreate):
    id: int
    is_reconciled: int
    matched_journal_id: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True

class FundTransferCreate(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: float
    transfer_date: date
    reference_number: Optional[str] = None
    remarks: Optional[str] = None

class FundTransferOut(FundTransferCreate):
    id: int
    journal_entry_id: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True

class GSTReturnCreate(BaseModel):
    filing_period: str
    return_type: str
    taxable_value: float = 0
    gst_liability: float = 0
    itc_available: float = 0
    net_gst_payable: float = 0
    status: str = "Draft"
    filing_date: Optional[date] = None

class GSTReturnOut(GSTReturnCreate):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
