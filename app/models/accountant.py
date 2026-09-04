from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Index,
    Integer,
    String,
    ForeignKey,
    Enum,
    DECIMAL,
    DateTime,
    func,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from sqlalchemy import Text
from app.core.enums import AccountType, VendorBillStatus
from app.models.base import Base


# ===================== CHART OF ACCOUNTS =====================
class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True)

    name = Column(String(100), nullable=False)
    code = Column(String(20), nullable=False)

    type = Column(Enum(AccountType), nullable=False)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    parent_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    parent = relationship("Account", remote_side=[id])

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_accounts_company_code"),
        Index("ix_accounts_code", "code"),
    )


# ===================== JOURNAL ENTRY =====================
class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True)

    description = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    journal_number = Column(String(100), index=True)
    entry_date = Column(Date)
    status = Column(String(50), server_default='Posted')
    entry_type = Column(String(50), server_default='Auto')
    created_by = Column(Integer, ForeignKey("users.id"))
    lines = relationship(
        "JournalLine", back_populates="entry", cascade="all, delete-orphan"
    )

    # created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # reference_type = Column(String(50), nullable=True)
    # reference_id = Column(Integer, nullable=True)

# ===================== JOURNAL LINES =====================
class JournalLine(Base):
    __tablename__ = "journal_lines"

    id = Column(Integer, primary_key=True)

    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)

    debit = Column(DECIMAL(18, 2), default=0)
    credit = Column(DECIMAL(18, 2), default=0)

    entry = relationship("JournalEntry", back_populates="lines")

    __table_args__ = (Index("ix_journal_lines_account", "account_id"),)


class FixedAsset(Base):
    __tablename__ = "fixed_assets"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    purchase_value = Column(DECIMAL(18, 2), nullable=False)
    purchase_date = Column(Date)

    depreciation_rate = Column(DECIMAL(5, 2), default=10)

    current_value = Column(DECIMAL(18, 2), nullable=False)

    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RedevelopmentOffer(Base):
    __tablename__ = "redevelopment_offers"

    id = Column(Integer, primary_key=True)

    project_name = Column(String(150), nullable=False)
    society_name = Column(String(150), nullable=False)
    address = Column(String(255), nullable=False)
    pdf_path = Column(String(255), nullable=True)
    developer_name = Column(String(150), nullable=False)
    contact_email = Column(String(150))
    contact_phone = Column(String(20))

    extra_carpet_percent = Column(Integer, default=0)

    note = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ===================== BANK RECONCILIATION =====================
class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True)

    bank_account_id = Column(
        Integer, ForeignKey("accounts.id"), nullable=False, index=True
    )
    transaction_date = Column(Date, nullable=False, index=True)

    amount = Column(DECIMAL(18, 2), nullable=False)
    type = Column(String(10), nullable=False)  # Credit, Debit

    description = Column(String(255), nullable=True)
    reference_number = Column(String(100), nullable=True, index=True)

    is_reconciled = Column(
        Integer, default=0
    )  # 0 = False, 1 = True (SQLite boolean compat)
    matched_journal_id = Column(
        Integer, ForeignKey("journal_entries.id"), nullable=True
    )

    # created_at = Column(DateTime, default=datetime.utcnow)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ===================== FUND TRANSFERS =====================
class FundTransfer(Base):
    __tablename__ = "fund_transfers"

    id = Column(Integer, primary_key=True)

    from_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    to_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)

    amount = Column(DECIMAL(18, 2), nullable=False)
    transfer_date = Column(Date, nullable=False, index=True)

    reference_number = Column(String(100), nullable=True)
    remarks = Column(String(255), nullable=True)

    journal_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)

    # created_at = Column(DateTime, default=datetime.utcnow)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ===================== GST RETURNS =====================
class GSTReturn(Base):
    __tablename__ = "gst_returns"

    id = Column(Integer, primary_key=True)

    filing_period = Column(String(20), nullable=False, index=True)  # e.g. "2026-06"
    return_type = Column(String(50), nullable=False)  # e.g. "GSTR-1", "GSTR-3B"

    taxable_value = Column(DECIMAL(18, 2), default=0)
    gst_liability = Column(DECIMAL(18, 2), default=0)
    itc_available = Column(DECIMAL(18, 2), default=0)
    net_gst_payable = Column(DECIMAL(18, 2), default=0)

    status = Column(String(50), default="Draft")  # Draft, Filed
    filing_date = Column(Date, nullable=True)

    # created_at = Column(DateTime, default=datetime.utcnow)

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ===================== VENDOR BILLS =====================
class VendorBill(Base):
    __tablename__ = "vendor_bills"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True)

    supplier_id = Column(
        Integer, ForeignKey("suppliers.id"), nullable=False, index=True
    )
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    purchase_order_id = Column(
        Integer, ForeignKey("purchase_orders.id"), nullable=True, index=True
    )

    bill_number = Column(String(50), unique=True, nullable=False, index=True)
    bill_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)
    grn_number = Column(String(100), nullable=True)
    gross_amount = Column(DECIMAL(18, 2), nullable=True, default=0.0)
    gst_percent = Column(DECIMAL(5, 2), default=0)
    gst_amount = Column(DECIMAL(18, 2), default=0)
    tds_percent = Column(DECIMAL(5, 2), default=0)
    tds_amount = Column(DECIMAL(18, 2), default=0)
    advance_paid = Column(DECIMAL(18, 2), default=0)

    total_amount = Column(DECIMAL(18, 2), nullable=False)

    # NEW GST INVOICE FIELDS
    party_gstin = Column(String(20), nullable=True)
    cgst = Column(DECIMAL(18, 2), nullable=True, default=0.0)
    sgst = Column(DECIMAL(18, 2), nullable=True, default=0.0)
    igst = Column(DECIMAL(18, 2), nullable=True, default=0.0)
    gst_document_url = Column(String(500), nullable=True)
    amount_paid = Column(DECIMAL(18, 2), default=0)

    vendor_invoice_url = Column(String(500), nullable=True)
    po_copy_url = Column(String(500), nullable=True)
    grn_copy_url = Column(String(500), nullable=True)
    supporting_docs_url = Column(String(500), nullable=True)

    status = Column(String(50), default=VendorBillStatus.PENDING.value)  # PENDING, APPROVED, PARTIAL, PAID, REJECTED
    accrued_journal_id = Column(Integer, ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    items = relationship("VendorBillItem", back_populates="vendor_bill", cascade="all, delete-orphan")


class VendorBillItem(Base):
    __tablename__ = "vendor_bill_items"

    id = Column(Integer, primary_key=True)
    vendor_bill_id = Column(Integer, ForeignKey("vendor_bills.id", ondelete="CASCADE"), nullable=False, index=True)

    material_name = Column(String(150), nullable=False)
    category = Column(String(100), nullable=True)
    quantity = Column(DECIMAL(18, 2), nullable=False)
    unit = Column(String(50), nullable=False)
    rate = Column(DECIMAL(18, 2), nullable=False)
    total = Column(DECIMAL(18, 2), nullable=False)

    vendor_bill = relationship("VendorBill", back_populates="items")



# ===================== NEW CONSOLIDATED MODELS =====================
class BankAccount(Base):
    __tablename__ = "bank_accounts"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), unique=True, nullable=False)
    bank_name = Column(String(150), nullable=False)
    account_number = Column(String(100), unique=True, nullable=False)
    ifsc_code = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)
    # created_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RecurringJournal(Base):
    __tablename__ = "recurring_journals"
    id = Column(Integer, primary_key=True)
    template_name = Column(String(255), nullable=False)
    frequency = Column(String(50), nullable=False)
    next_run_date = Column(Date, nullable=False)
    status = Column(String(50), default="ACTIVE")
    template_data = Column(JSON, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    # created_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TDSDeduction(Base):
    __tablename__ = "tds_deductions"
    id = Column(Integer, primary_key=True)
    party_name = Column(String(255), nullable=False)
    pan_number = Column(String(20))
    invoice_number = Column(String(100))
    payment_amount = Column(DECIMAL(18, 2), nullable=False)
    tds_section = Column(String(50), nullable=False)
    tds_rate = Column(DECIMAL(5, 2), nullable=False)
    tds_amount = Column(DECIMAL(18, 2), nullable=False)
    deposit_date = Column(Date)
    status = Column(String(50), default="PENDING")
    vendor_bill_id = Column(Integer, ForeignKey("vendor_bills.id"))
    ra_bill_id = Column(Integer, ForeignKey("ra_bills.id"))
    created_by = Column(Integer, ForeignKey("users.id"))
    # created_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

class PaymentVoucher(Base):
    __tablename__ = "payment_vouchers"
    id = Column(Integer, primary_key=True)
    payment_voucher_number = Column(String(50), unique=True, nullable=False, index=True)
    payment_date = Column(DateTime(timezone=True), nullable=False)
    party_type = Column(String(50), nullable=False) # Vendor, Contractor
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    contractor_id = Column(Integer, ForeignKey("contractors.id"), nullable=True)
    vendor_bill_id = Column(Integer, ForeignKey("vendor_bills.id"), nullable=True)
    
    base_amount = Column(DECIMAL(18, 2), nullable=False, default=0)
    gst_amount = Column(DECIMAL(18, 2), nullable=False, default=0)
    gross_amount = Column(DECIMAL(18, 2), nullable=False, default=0)
    tds_amount = Column(DECIMAL(18, 2), nullable=False, default=0)
    retention_amount = Column(DECIMAL(18, 2), nullable=False, default=0)
    net_payable_amount = Column(DECIMAL(18, 2), nullable=False, default=0)
    
    payment_method = Column(String(50), nullable=False)
    bank_account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=False)
    reference_no = Column(String(100), nullable=True)
    status = Column(String(50), default="PENDING") # PENDING, PAID, CANCELLED
    journal_entry_id = Column(Integer, ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=True)
    
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    vendor_bill = relationship("VendorBill")
    journal_entry = relationship("JournalEntry")

class PettyCashTransaction(Base):
    __tablename__ = "petty_cash_transactions"

    id = Column(Integer, primary_key=True)
    voucher_no = Column(String(50), unique=True, nullable=False, index=True)
    type = Column(String(50), nullable=False)
    transaction_date = Column(Date, nullable=False)
    
    category_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    source_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    
    amount = Column(DECIMAL(18, 2), nullable=False)
    paid_to_received_from = Column(String(150), nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    remarks = Column(String(255), nullable=True)
    
    journal_entry_id = Column(Integer, ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=True)
    
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
