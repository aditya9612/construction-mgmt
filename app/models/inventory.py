from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    DECIMAL,
    UniqueConstraint,
    text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class StockBatch(Base, TimestampMixin):
    __tablename__ = "stock_batches"

    id: Mapped[int] = mapped_column(primary_key=True)

    batch_number: Mapped[str] = mapped_column(
        String(100),
        index=True,
        nullable=False,
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )
    material_master_id: Mapped[int] = mapped_column(
        ForeignKey("material_master.id"),
        nullable=False,
        index=True,
    )
    supplier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("suppliers.id"),
        nullable=True,
        index=True,
    )
    grn_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("goods_receipt_notes.id"),
        nullable=True,
        index=True,
    )

    initial_quantity: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    current_balance: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    purchase_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)
    receipt_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        index=True,
    )
    batch_source: Mapped[str] = mapped_column(
        String(50),
        default="GRN",
        server_default=text("'GRN'"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="ACTIVE",
        server_default=text("'ACTIVE'"),
    )

    project = relationship("Project", lazy="selectin")
    material_master = relationship("MaterialMaster", lazy="selectin")
    supplier = relationship("Supplier", lazy="selectin")
    grn = relationship("GoodsReceiptNote", back_populates="batches", foreign_keys=[grn_id], lazy="selectin")

    __table_args__ = (
        Index("idx_batch_proj_mat", "project_id", "material_master_id"),
        Index("idx_batch_fifo", "project_id", "material_master_id", "receipt_date", "purchase_rate"),
    )


class ProjectMaterialValuation(Base, TimestampMixin):
    __tablename__ = "project_material_valuation"

    id: Mapped[int] = mapped_column(primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )
    material_master_id: Mapped[int] = mapped_column(
        ForeignKey("material_master.id"),
        nullable=False,
        index=True,
    )

    total_purchased_quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        default=Decimal("0.000"),
        server_default=text("'0.000'"),
    )
    total_purchased_amount: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2),
        default=Decimal("0.00"),
        server_default=text("'0.00'"),
    )
    current_moving_weighted_average_rate: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2),
        default=Decimal("0.00"),
        server_default=text("'0.00'"),
    )
    last_recalculated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        server_default=func.now(),
    )

    project = relationship("Project", lazy="selectin")
    material_master = relationship("MaterialMaster", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("project_id", "material_master_id", name="uq_proj_mat_val"),
    )


class GoodsReceiptNote(Base, TimestampMixin):
    __tablename__ = "goods_receipt_notes"

    id: Mapped[int] = mapped_column(primary_key=True)

    grn_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id"),
        nullable=False,
        index=True,
    )
    purchase_order_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("purchase_orders.id"),
        nullable=True,
        index=True,
    )

    challan_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    challan_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    vehicle_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    status: Mapped[str] = mapped_column(
        String(50),
        default="DRAFT",
        server_default=text("'DRAFT'"),
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    verified_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    company = relationship("Company", lazy="selectin")
    project = relationship("Project", lazy="selectin")
    supplier = relationship("Supplier", lazy="selectin")
    purchase_order = relationship("PurchaseOrder", lazy="selectin")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    verified_by = relationship("User", foreign_keys=[verified_by_id], lazy="selectin")

    items = relationship(
        "GoodsReceiptNoteItem",
        back_populates="grn",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    batches = relationship(
        "StockBatch",
        back_populates="grn",
        foreign_keys=[StockBatch.grn_id],
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_grn_comp_idemp"),
    )


class GoodsReceiptNoteItem(Base, TimestampMixin):
    __tablename__ = "goods_receipt_note_items"

    id: Mapped[int] = mapped_column(primary_key=True)

    grn_id: Mapped[int] = mapped_column(
        ForeignKey("goods_receipt_notes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    po_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("purchase_order_items.id"),
        nullable=True,
        index=True,
    )
    material_master_id: Mapped[int] = mapped_column(
        ForeignKey("material_master.id"),
        nullable=False,
        index=True,
    )
    material_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("materials.id"),
        nullable=True,
        index=True,
    )

    received_quantity: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    accepted_quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        default=Decimal("0.000"),
        server_default=text("'0.000'"),
    )
    rejected_quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        default=Decimal("0.000"),
        server_default=text("'0.000'"),
    )
    unit_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)
    batch_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    qc_remarks: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    grn = relationship("GoodsReceiptNote", back_populates="items")
    po_item = relationship("PurchaseOrderItem", lazy="selectin")
    material_master = relationship("MaterialMaster", lazy="selectin")
    material = relationship("Material", lazy="selectin")


class MaterialIssue(Base, TimestampMixin):
    __tablename__ = "material_issues"

    id: Mapped[int] = mapped_column(primary_key=True)

    issue_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )
    material_master_id: Mapped[int] = mapped_column(
        ForeignKey("material_master.id"),
        nullable=False,
        index=True,
    )
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id"),
        nullable=False,
        index=True,
    )

    quantity: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(50), nullable=False)
    issued_to: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contractor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("contractors.id"),
        nullable=True,
    )
    boq_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("boq_items.id"),
        nullable=True,
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        default="ISSUED",
        server_default=text("'ISSUED'"),
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    remarks: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    company = relationship("Company", lazy="selectin")
    project = relationship("Project", lazy="selectin")
    material_master = relationship("MaterialMaster", lazy="selectin")
    material = relationship("Material", lazy="selectin")
    contractor = relationship("Contractor", lazy="selectin")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")

    allocations = relationship(
        "MaterialIssueAllocation",
        back_populates="issue",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_issue_comp_idemp"),
    )


class MaterialIssueAllocation(Base, TimestampMixin):
    __tablename__ = "material_issue_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)

    issue_id: Mapped[int] = mapped_column(
        ForeignKey("material_issues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("stock_batches.id"),
        nullable=False,
        index=True,
    )

    allocated_quantity: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    unit_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)

    issue = relationship("MaterialIssue", back_populates="allocations")
    batch = relationship("StockBatch", lazy="selectin")
