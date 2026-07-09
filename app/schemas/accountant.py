from datetime import date, datetime

from pydantic import BaseModel, Field
from typing import Optional, List
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator
from app.core.enums import PaymentMode
from app.core.validators import validate_ifsc


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
    
    current_balance: Optional[float] = 0.0
    opening_balance: Optional[float] = 0.0
    status: Optional[str] = "Active"

    class Config:
        from_attributes = True

class AccountUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    status: Optional[str] = None
    parent_id: Optional[int] = None

class AccountDetailOut(AccountOut):
    parent: Optional[AccountOut] = None
    children: List[AccountOut] = []

class AccountTreeOut(AccountOut):
    children: List['AccountTreeOut'] = []

AccountTreeOut.model_rebuild()


# ============================
#  BANK ACCOUNT
# ============================
class BankAccountCreate(BaseModel):
    account_id: int = Field(..., description="Must point to a valid ASSET account representing the Bank")
    bank_name: str
    account_number: str
    ifsc_code: Optional[str] = None

    @field_validator("ifsc_code")
    def validate_ifsc_code(cls, v):
        if v:
            return validate_ifsc(v)
        return v

class BankAccountUpdate(BaseModel):
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    ifsc_code: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("ifsc_code")
    def validate_ifsc_code(cls, v):
        if v:
            return validate_ifsc(v)
        return v

class BankAccountOut(BankAccountCreate):
    id: int
    is_active: bool
    
    # Extended properties
    ledger_name: Optional[str] = None
    balance: float = 0.0

    class Config:
        from_attributes = True

class BankLedgerLine(BaseModel):
    date: date
    voucher_no: Optional[str] = None
    description: Optional[str] = None
    debit: float = 0.0
    credit: float = 0.0
    balance: float = 0.0


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

class ReconciliationDashboardOut(BaseModel):
    system_balance: float
    bank_balance: float
    unreconciled_amount: float

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

class TDSDeductionCreate(BaseModel):
    party_name: str
    pan_number: Optional[str] = None
    invoice_number: Optional[str] = None
    payment_amount: float
    tds_section: str
    tds_rate: float
    tds_amount: float
    deposit_date: Optional[date] = None
    status: str = "Pending"
    vendor_bill_id: Optional[int] = None
    ra_bill_id: Optional[int] = None

class TDSDeductionOut(TDSDeductionCreate):
    id: int
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class GSTRegisterItem(BaseModel):
    date: date
    invoice_no: str
    type: str  # 'SALES' or 'PURCHASE'
    party_name: str
    gstin: Optional[str] = None
    taxable_amount: float
    gst_amount: float
    invoice_total: float
    attachments: Optional[str] = None

class MonthlyTrend(BaseModel):
    month: str
    input: float
    output: float

class GSTReturnStatus(BaseModel):
    return_type: str
    filing_period: str
    status: str
    due_date: Optional[date] = None
    filing_date: Optional[date] = None

class GSTRecentFiling(BaseModel):
    return_type: str
    filing_period: str
    filing_date: Optional[date] = None
    status: str

class GSTImportResult(BaseModel):
    total_records: int
    valid_records: int
    errors: List[str]

class GSTDashboardOut(BaseModel):
    input_gst: float
    output_gst: float
    net_gst: float
    tds_collected: float
    upcoming_return: Optional[str] = None
    monthly_trend: List[MonthlyTrend] = []
    return_status: List[GSTReturnStatus] = []
    recent_filings: List[GSTRecentFiling] = []

class GSTReconciliationMismatch(BaseModel):
    invoice_no: str
    vendor: str
    erp_gst: float
    portal_gst: float
    difference: float
    status: str
