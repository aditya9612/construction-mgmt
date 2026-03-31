from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, ForeignKey, Integer, String, Text, Index, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class BOQ(Base, TimestampMixin):
    __tablename__ = "boq_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    boq_group_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    version_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    is_latest: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        index=True,
    )

    item_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        nullable=False,
        default=0,
        server_default="0",
    )

    unit: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="unit",
        server_default="unit",
    )

    unit_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2),
        nullable=False,
        default=0,
        server_default="0",
    )

    total_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2),
        nullable=False,
        default=0,
        server_default="0",
    )

    actual_quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        nullable=False,
        default=0,
        server_default="0",
    )

    actual_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2),
        nullable=False,
        default=0,
        server_default="0",
    )

    variance_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2),
        nullable=False,
        default=0,
        server_default="0",
    )

    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="Active",
        server_default="Active",
        index=True,
    )

    project = relationship("Project")

    __table_args__ = (
        Index("idx_boq_project", "project_id"),
        Index("idx_boq_group", "boq_group_id"),
    )
