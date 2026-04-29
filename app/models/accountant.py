from sqlalchemy import Column, Date, Index, Integer, String, ForeignKey, Enum, DECIMAL, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

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

    __table_args__ = (
        Index('ix_accounts_code', 'code'),
    )


# ===================== JOURNAL ENTRY =====================
class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True)

    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lines = relationship(
        "JournalLine",
        back_populates="entry",
        cascade="all, delete-orphan"
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

    __table_args__ = (
        Index('ix_journal_lines_account', 'account_id'),
    )


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