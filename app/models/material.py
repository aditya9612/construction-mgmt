from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from sqlalchemy import Column

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    DECIMAL,
    UniqueConstraint,
    Enum as SqlEnum,
    text,
    CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func 

from app.models.base import Base, TimestampMixin
from app.core.enums import IssueType, TransactionType, RateType


# ================= MATERIAL =================
class Material(Base, TimestampMixin):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_code = Column(String(20), unique=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))


    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    material_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)

    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    supplier = relationship("Supplier", lazy="selectin")

    rate_type: Mapped[RateType] = mapped_column(
        SqlEnum(
            RateType,
            name="rate_type_enum",
            create_constraint=True,
            native_enum=False,
        ),
        nullable=False,
    )

    purchase_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)

    quantity_purchased: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3), nullable=False, server_default=text("0.000")
    )
    quantity_used: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3), nullable=False, server_default=text("0.000")
    )
    remaining_stock: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3), nullable=False, server_default=text("0.000")
    )
    payment_given: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), nullable=False, server_default=text("0.00")
    )
    payment_pending: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), nullable=False, server_default=text("0.00")
    )
    total_amount: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), nullable=False, server_default=text("0.00")
    )
    advance_amount: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), nullable=False, server_default=text("0.00")
    )

    minimum_stock_level: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        nullable=False,
        server_default=text("0.000"),
    )

    usages: Mapped[List["MaterialUsage"]] = relationship(
        "MaterialUsage", back_populates="material", cascade="all, delete"
    )

    transactions: Mapped[List["MaterialTransaction"]] = relationship(
        "MaterialTransaction", back_populates="material", cascade="all, delete"
    )

    ledger_entries: Mapped[List["MaterialLedger"]] = relationship(
        "MaterialLedger", back_populates="material", cascade="all, delete"
    )

    __table_args__ = (
        Index("idx_material_project", "project_id"),
        Index("idx_material_deleted", "is_deleted"),
        Index("idx_material_supplier", "supplier_id"),
        UniqueConstraint(
            "project_id",
            "material_name",
            "supplier_id",
            name="unique_material_per_project_supplier",
        ),
        CheckConstraint("remaining_stock >= 0", name="check_stock_not_negative"),
        CheckConstraint("minimum_stock_level >= 0", name="check_min_stock_not_negative"),
    )


# ================= MATERIAL USAGE =================
class MaterialUsage(Base, TimestampMixin):
    __tablename__ = "material_usage"

    id: Mapped[int] = mapped_column(primary_key=True)

    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE")
    )
    material = relationship("Material", back_populates="usages")

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))

    quantity_used: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)

    usage_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("idx_usage_material", "material_id"),)


# ================= MATERIAL TRANSACTION =================
class MaterialTransaction(Base, TimestampMixin):
    __tablename__ = "material_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)

    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE")
    )
    material = relationship("Material", back_populates="transactions")

    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True
    )

    type: Mapped[TransactionType] = mapped_column(
        SqlEnum(
            TransactionType,
            name="transaction_type_enum",
            create_constraint=True,
            native_enum=False,
        )
    )

    quantity: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)

    rate: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), server_default=text("0.00")
    )

    total_amount: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), server_default=text("0.00")
    )

    amount_paid: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), server_default=text("0.00")
    )

    payment_pending: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), server_default=text("0.00")
    )

    issue_type: Mapped[IssueType] = mapped_column(
        SqlEnum(
            IssueType,
            name="issue_type_enum",
            create_constraint=True,
            native_enum=False,
        ),
        default=IssueType.SYSTEM,
    )

    reference_id: Mapped[Optional[str]] = mapped_column(String(100))
    remarks: Mapped[Optional[str]] = mapped_column(String(255))

    __table_args__ = (
        Index("idx_tx_material", "material_id"),
        Index("idx_tx_project", "project_id"),
        Index("idx_tx_reference", "reference_id"),
    )


# ================= SUPPLIER =================
class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    contact: Mapped[Optional[str]] = mapped_column(String(50))

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("idx_supplier_deleted", "is_deleted"),)


# ================= PURCHASE ORDER =================
class PurchaseOrder(Base, TimestampMixin):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)

    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))

    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id")) 
    material_name: Mapped[str] = mapped_column(String(255))

    quantity: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2))

    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(50), default="CREATED")

    __table_args__ = (Index("idx_po_project", "project_id"),)


# ================= TRANSFER =================
class MaterialTransfer(Base, TimestampMixin):
    __tablename__ = "material_transfers"

    id: Mapped[int] = mapped_column(primary_key=True)

    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    material = relationship("Material", lazy="joined")

    from_project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    to_project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))

    quantity: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING")

    reference_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    __table_args__ = (Index("idx_transfer_material", "material_id"),)


# ================= LEDGER =================
class MaterialLedger(Base, TimestampMixin):
    __tablename__ = "material_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)

    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE")
    )
    material = relationship("Material", back_populates="ledger_entries")

    type: Mapped[TransactionType] = mapped_column(
        SqlEnum(
            TransactionType,
            name="ledger_type_enum",
            create_constraint=True,
            native_enum=False,
        )
    )

    quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3), default=Decimal("0.000"), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal("0"))

    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal("0"))
    amount_paid: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal("0"))
    payment_pending: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), default=Decimal("0")
    )

    issue_type: Mapped[IssueType] = mapped_column(
        SqlEnum(
            IssueType,
            name="ledger_issue_type_enum",
            create_constraint=True,
            native_enum=False,
        ),
        default=IssueType.SYSTEM,
    )

    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True
    )

    reference_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("idx_ledger_material", "material_id"),
        Index("idx_ledger_reference", "reference_id"),
    ) 
