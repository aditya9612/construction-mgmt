from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    DECIMAL,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class PurchaseOrderItem(Base, TimestampMixin):
    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)

    purchase_order_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    material_master_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("material_master.id"),
        nullable=True,
        index=True,
    )
    material_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("materials.id"),
        nullable=True,
        index=True,
    )
    material_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    received_quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        default=Decimal("0.000"),
        server_default=text("'0.000'"),
    )
    rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        default="PENDING",
        server_default=text("'PENDING'"),
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("0"),
    )

    purchase_order = relationship("PurchaseOrder", back_populates="items")
    material_master = relationship("MaterialMaster", lazy="selectin")
    material = relationship("Material", lazy="selectin")

    __table_args__ = (
        Index("idx_po_items_po_id", "purchase_order_id"),
        Index("idx_po_items_mat_master", "material_master_id"),
    )
