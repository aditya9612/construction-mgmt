from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    DECIMAL,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class DailyStockReconciliation(Base, TimestampMixin):
    __tablename__ = "daily_stock_reconciliation"

    id: Mapped[int] = mapped_column(primary_key=True)

    reconciliation_number: Mapped[str] = mapped_column(
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

    reconciliation_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )
    system_book_stock: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        nullable=False,
    )
    physical_count: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        nullable=False,
    )
    variance: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default="DRAFT",
        server_default=text("'DRAFT'"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
    )
    review_outcome: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    recorded_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )
    recount_reason: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    remarks: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    company = relationship("Company", lazy="selectin")
    project = relationship("Project", lazy="selectin")
    material_master = relationship("MaterialMaster", lazy="selectin")
    material = relationship("Material", lazy="selectin")
    recorded_by = relationship("User", foreign_keys=[recorded_by_id], lazy="selectin")
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id], lazy="selectin")

    recounts = relationship(
        "DailyStockRecountHistory",
        back_populates="reconciliation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "material_id", "reconciliation_date", name="uq_dsr_site_mat_date"),
    )


class DailyStockRecountHistory(Base, TimestampMixin):
    __tablename__ = "daily_stock_recount_history"

    id: Mapped[int] = mapped_column(primary_key=True)

    reconciliation_id: Mapped[int] = mapped_column(
        ForeignKey("daily_stock_reconciliation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_count: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    new_count: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    variance: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False)
    recounted_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    recount_notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    reconciliation = relationship("DailyStockReconciliation", back_populates="recounts")
    recounted_by = relationship("User", foreign_keys=[recounted_by_id], lazy="selectin")


class DailyStockIdempotencyLog(Base, TimestampMixin):
    __tablename__ = "daily_stock_idempotency_log"

    id: Mapped[int] = mapped_column(primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    endpoint: Mapped[str] = mapped_column(String(100), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_payload: Mapped[str] = mapped_column(String(4000), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_ds_idemp"),
    )
