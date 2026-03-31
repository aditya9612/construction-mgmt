from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, ForeignKey, Integer, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Material(Base, TimestampMixin):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    material_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    unit: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="unit",
        server_default="unit",
    )

    supplier_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    purchase_rate: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2),
        nullable=False,
        default=0,
        server_default="0",
    )

    rate_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    quantity_purchased: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        nullable=False,
        default=0,
        server_default="0",
    )

    quantity_used: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        nullable=False,
        default=0,
        server_default="0",
    )

    remaining_stock: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        nullable=False,
        default=0,
        server_default="0",
    )

    payment_given: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2),
        nullable=False,
        default=0,
        server_default="0",
    )

    payment_pending: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2),
        nullable=False,
        default=0,
        server_default="0",
    )

    project = relationship("Project")

    __table_args__ = (
        Index("idx_material_project", "project_id"),
        Index("idx_material_category", "category"),
    )