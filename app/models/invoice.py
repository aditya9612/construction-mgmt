from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    DECIMAL,
    DateTime,
    Enum,
    JSON,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.enums import InvoiceSourceType, InvoiceStatus, InvoiceType
from app.models.base import Base


# ===================== INVOICE =====================
class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)

    # Relations
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id = Column(
        Integer, ForeignKey("owners.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Linked data
    linked_expense_ids = Column(JSON, nullable=True)

    # Type
    type = Column(
        Enum(InvoiceType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )  # owner / labour / material / contractor

    source_type = Column(
        Enum(
            InvoiceSourceType,
            values_callable=lambda obj: [e.value for e in obj]
        ),
        nullable=True,
        index=True,
    ) # quotation / measurement / manual

    reference_id = Column(Integer, nullable=True, index=True)

    quotation_id = Column(
        Integer,
        ForeignKey("quotation_master.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        unique=True,
    )

    # Financials
    amount = Column(DECIMAL(18, 2), nullable=False)

    gst_percent = Column(DECIMAL(5, 2), default=0)
    gst_amount = Column(DECIMAL(18, 2), default=0)

    tax_percent = Column(DECIMAL(5, 2), default=0)
    tax_amount = Column(DECIMAL(18, 2), default=0)

    total_amount = Column(DECIMAL(18, 2), nullable=False)

    #  PAYMENT TRACKING (CRITICAL)
    paid_amount = Column(DECIMAL(18, 2), default=0)
    pending_amount = Column(DECIMAL(18, 2), nullable=False)

    # Status (ENUM)
    status = Column(
        Enum(InvoiceStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=InvoiceStatus.PENDING,
    )

    # Meta
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    transactions = relationship(
        "Transaction",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )

    quotation = relationship("QuotationMaster", lazy="selectin")


# ===================== TRANSACTION =====================
class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)

    # Relations
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)

    # Type: receipt (incoming) / payment (outgoing)
    type = Column(String(20), nullable=False)

    # Financial
    amount = Column(DECIMAL(18, 2), nullable=False)

    # Payment Info
    mode = Column(String(20), nullable=False)  # cash / bank / upi
    reference = Column(String(100), nullable=True)

    linked_to = Column(String(50), nullable=True, index=True)

    # Audit
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    invoice = relationship("Invoice", back_populates="transactions")
