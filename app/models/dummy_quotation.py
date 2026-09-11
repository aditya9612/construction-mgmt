from typing import Optional
from sqlalchemy import (
    String,
    Float,
    Integer,
    Date,
    DateTime,
    Text,
    ForeignKey,
    Boolean,
    DECIMAL,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, date
from decimal import Decimal

from app.models.base import Base

class DummyQuotation(Base):
    __tablename__ = "dummy_quotations"

    id: Mapped[int] = mapped_column(primary_key=True)
    dummy_quotation_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    # Basic Client Details
    client_name: Mapped[str | None] = mapped_column(String(150))
    mobile_number: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(150))
    billing_address: Mapped[str | None] = mapped_column(Text)
    gst_number: Mapped[str | None] = mapped_column(String(50))

    # Basic Total / Financial Info
    subtotal: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))
    gst_percent: Mapped[float] = mapped_column(Float, default=0)
    cgst_percent: Mapped[float] = mapped_column(Float, default=0)
    sgst_percent: Mapped[float] = mapped_column(Float, default=0)
    cgst_amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))
    sgst_amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))
    grand_total: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))
    discount_amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))
    tds_percent: Mapped[float] = mapped_column(Float, default=0)
    tds_amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))
    advance_paid: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))
    balance_due: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))

    notes: Mapped[str | None] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    items = relationship(
        "DummyQuotationItem",
        back_populates="dummy_quotation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class DummyQuotationItem(Base):
    __tablename__ = "dummy_quotation_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    dummy_quotation_id: Mapped[int] = mapped_column(
        ForeignKey("dummy_quotations.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(50))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))
    amount: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), default=Decimal('0.00'))
    
    dummy_quotation = relationship("DummyQuotation", back_populates="items", lazy="selectin")
    
    measurements = relationship(
        "DummyMeasurementDetail",
        back_populates="dummy_quotation_item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class DummyMeasurementDetail(Base):
    __tablename__ = "dummy_measurement_details"

    id: Mapped[int] = mapped_column(primary_key=True)
    dummy_quotation_item_id: Mapped[int] = mapped_column(
        ForeignKey("dummy_quotation_items.id", ondelete="CASCADE")
    )
    
    length: Mapped[float | None] = mapped_column(Float)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    
    cubic_feet: Mapped[float] = mapped_column(Float, default=0)
    cubic_meter: Mapped[float] = mapped_column(Float, default=0)
    brass: Mapped[float] = mapped_column(Float, default=0)
    quantity: Mapped[float] = mapped_column(Float, default=0)
    
    formula_used: Mapped[str | None] = mapped_column(String(100))
    
    dummy_quotation_item = relationship(
        "DummyQuotationItem", back_populates="measurements", lazy="selectin"
    )
