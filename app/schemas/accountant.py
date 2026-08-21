from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator
from typing import Optional, List
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator
from app.core.enums import PaymentMode, PettyCashTransactionType
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
    contractor_id: Optional[int] = None

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


class PettyCashTransactionCreate(BaseModel):
    type: PettyCashTransactionType
    transaction_date: date
    category_id: Optional[int] = None
    source_account_id: Optional[int] = None
    amount: Decimal
    paid_to_received_from: Optional[str] = None
    approved_by: Optional[int] = None
    remarks: Optional[str] = None


class PettyCashTransactionOut(PettyCashTransactionCreate):
    id: int
    voucher_no: str

    class Config:
        from_attributes = True


class PettyCashLedgerLine(BaseModel):
    date: date
    voucher_no: Optional[str] = None
    description: Optional[str] = None
    debit: float = 0.0
    credit: float = 0.0
    category: Optional[str] = None
    remarks: Optional[str] = None
    paid_to: Optional[str] = None
    cash_in: Decimal = Decimal('0.0')
    cash_out: Decimal = Decimal('0.0')
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

class GSTReturnUpdate(BaseModel):
    filing_period: Optional[str] = None
    return_type: Optional[str] = None
    taxable_value: Optional[float] = None
    gst_liability: Optional[float] = None
    itc_available: Optional[float] = None
    net_gst_payable: Optional[float] = None
    status: Optional[str] = None
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


class TDSDeductionUpdate(BaseModel):
    party_name: Optional[str] = None
    pan_number: Optional[str] = None
    invoice_number: Optional[str] = None
    payment_amount: Optional[float] = None
    tds_section: Optional[str] = None
    tds_rate: Optional[float] = None
    tds_amount: Optional[float] = None
    deposit_date: Optional[date] = None
    status: Optional[str] = None
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

    cgst: Optional[float] = 0.0
    sgst: Optional[float] = 0.0
    igst: Optional[float] = 0.0
    invoice_copy_url: Optional[str] = None
    gst_document_url: Optional[str] = None

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

# ============================
#  VENDOR BILLS
# ============================

class VendorBillItemCreate(BaseModel):
    material_name: str
    category: Optional[str] = None
    quantity: float
    unit: str
    rate: float
    total: float

class VendorBillItemOut(VendorBillItemCreate):
    id: int
    vendor_bill_id: int

    class Config:
        from_attributes = True

class VendorBillCreate(BaseModel):
    supplier_id: int
    project_id: Optional[int] = None
    purchase_order_id: Optional[int] = None
    bill_number: str
    bill_date: date
    due_date: date
    grn_number: Optional[str] = None

    gross_amount: float = 0.0
    gst_percent: float = 0.0
    gst_amount: float = 0.0
    tds_percent: float = 0.0
    tds_amount: float = 0.0
    advance_paid: float = 0.0
    total_amount: float

    vendor_invoice_url: Optional[str] = None
    po_copy_url: Optional[str] = None
    grn_copy_url: Optional[str] = None
    supporting_docs_url: Optional[str] = None

    party_gstin: Optional[str] = None
    cgst: Optional[Decimal] = Field(default=Decimal('0.0'))
    sgst: Optional[Decimal] = Field(default=Decimal('0.0'))
    igst: Optional[Decimal] = Field(default=Decimal('0.0'))
    gst_document_url: Optional[str] = None

    @model_validator(mode='after')
    def validate_gst_split(self) -> 'VendorBillCreate':
        c = self.cgst or Decimal('0.0')
        s = self.sgst or Decimal('0.0')
        i = self.igst or Decimal('0.0')
        if c < Decimal('0.0') or s < Decimal('0.0') or i < Decimal('0.0'):
            raise ValueError('GST components cannot be negative')
        if i > Decimal('0.0') and (c > Decimal('0.0') or s > Decimal('0.0')):
            raise ValueError('IGST cannot be combined with CGST/SGST')
        t = c + s + i
        if t > Decimal('0.0'):
            gst_amt = Decimal(str(self.gst_amount or 0.0))
            if round(t, 2) != round(gst_amt, 2):
                raise ValueError(f'Total GST split {t} must reconcile exactly with gst_amount {gst_amt}')
        return self

    items: List[VendorBillItemCreate] = []

class VendorBillUpdate(BaseModel):
    status: Optional[str] = None
    amount_paid: Optional[float] = None
    # Add other fields if editable later

class VendorBillOut(VendorBillCreate):
    id: int
    status: str
    amount_paid: float
    supplier_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    items: List[VendorBillItemOut] = []

    class Config:
        from_attributes = True

class VendorBillApprovalRequest(BaseModel):
    status: str # "APPROVED" or "REJECTED"
    notes: Optional[str] = None

class VendorBillPaymentRequest(BaseModel):
    amount: float
    mode: PaymentMode
    reference: Optional[str] = None
    payment_date: Optional[date] = None


class FixedAssetOut(BaseModel):
    id: int
    name: str
    purchase_value: Decimal
    purchase_date: Optional[date] = None
    depreciation_rate: Optional[Decimal] = None
    current_value: Decimal
    project_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    project_name: Optional[str] = None

    class Config:
        from_attributes = True
