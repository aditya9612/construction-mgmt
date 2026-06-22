from sqlalchemy import (
    Column,
    Date,
    Index,
    Integer,
    String,
    ForeignKey,
    Enum,
    DECIMAL,
    DateTime,
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from sqlalchemy import Text
from app.core.enums import AccountType
from app.models.base import Base


# ===================== CHART OF ACCOUNTS =====================
class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)

    name = Column(String(100), nullable=False)
    code = Column(String(20), unique=True, nullable=False)

    type = Column(Enum(AccountType), nullable=False)

    parent_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    parent = relationship("Account", remote_side=[id])

    __table_args__ = (Index("ix_accounts_code", "code"),)


# ===================== JOURNAL ENTRY =====================
class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True)

    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lines = relationship(
        "JournalLine", back_populates="entry", cascade="all, delete-orphan"
    )


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

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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

    created_at = Column(DateTime, default=datetime.utcnow)


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

    created_at = Column(DateTime, default=datetime.utcnow)


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

    created_at = Column(DateTime, default=datetime.utcnow)


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

    created_at = Column(DateTime, default=datetime.utcnow)


# ===================== VENDOR BILLS =====================
class VendorBill(Base):
    __tablename__ = "vendor_bills"

    id = Column(Integer, primary_key=True)

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

    total_amount = Column(DECIMAL(18, 2), nullable=False)
    amount_paid = Column(DECIMAL(18, 2), default=0)

    status = Column(String(50), default="PENDING")  # PENDING, PARTIAL, PAID

    created_at = Column(DateTime, default=datetime.utcnow)
