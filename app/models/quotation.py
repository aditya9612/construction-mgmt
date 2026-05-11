from typing import Optional

from sqlalchemy import (
    String,
    Float,
    Integer,
    Date,
    DateTime,
    Text,
    Enum,
    ForeignKey,
    Boolean,
)

from sqlalchemy.orm import Mapped, mapped_column, relationship

from datetime import datetime, date
import enum

from app.models.base import Base

# =========================================================
# ENUMS
# =========================================================


class QuotationStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONVERTED = "converted"


# =========================================================
# QUOTATION MASTER
# =========================================================


class QuotationMaster(Base):
    __tablename__ = "quotation_master"

    id: Mapped[int] = mapped_column(primary_key=True)

    quotation_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    # ================= CLIENT =================

    client_name: Mapped[str] = mapped_column(String(150))

    company_name: Mapped[str | None] = mapped_column(String(150))

    mobile_number: Mapped[str] = mapped_column(String(20))

    email: Mapped[str | None] = mapped_column(String(150))

    billing_address: Mapped[str | None] = mapped_column(Text)

    site_address: Mapped[str | None] = mapped_column(Text)

    gst_number: Mapped[str | None] = mapped_column(String(50))

    # ================= PROJECT =================
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )

    project = relationship("Project", lazy="selectin")

    project_name: Mapped[str] = mapped_column(String(150))

    project_type: Mapped[str] = mapped_column(String(100))

    project_start_date: Mapped[date | None] = mapped_column(Date)

    project_end_date: Mapped[date | None] = mapped_column(Date)

    engineer_name: Mapped[str | None] = mapped_column(String(150))

    work_order_no: Mapped[str | None] = mapped_column(String(100))

    labour_items = relationship(
        "QuotationLabour",
        back_populates="quotation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # ================= TOTALS =================

    subtotal: Mapped[float] = mapped_column(Float, default=0)

    gst_percent: Mapped[float] = mapped_column(Float, default=0)

    gst_amount: Mapped[float] = mapped_column(Float, default=0)

    cgst_percent: Mapped[float] = mapped_column(Float, default=0)

    sgst_percent: Mapped[float] = mapped_column(Float, default=0)

    tds_percent: Mapped[float] = mapped_column(Float, default=0)

    cgst_amount: Mapped[float] = mapped_column(Float, default=0)

    sgst_amount: Mapped[float] = mapped_column(Float, default=0)

    tds_amount: Mapped[float] = mapped_column(Float, default=0)

    discount_amount: Mapped[float] = mapped_column(Float, default=0)

    grand_total: Mapped[float] = mapped_column(Float, default=0)

    advance_paid: Mapped[float] = mapped_column(Float, default=0)

    balance_due: Mapped[float] = mapped_column(Float, default=0)

    payment_mode: Mapped[str | None] = mapped_column(String(50))

    upi_id: Mapped[str | None] = mapped_column(String(150))

    bank_name: Mapped[str | None] = mapped_column(String(150))

    account_holder_name: Mapped[str | None] = mapped_column(String(150))

    account_number: Mapped[str | None] = mapped_column(String(100))

    ifsc_code: Mapped[str | None] = mapped_column(String(50))

    due_date: Mapped[date | None] = mapped_column(Date)

    # ================= APPROVAL =================

    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    approved_at: Mapped[datetime | None] = mapped_column(DateTime)

    rejected_reason: Mapped[str | None] = mapped_column(Text)

    # ================= CONVERSION =================

    converted_to_bill: Mapped[bool] = mapped_column(Boolean, default=False)

    converted_to_invoice: Mapped[bool] = mapped_column(Boolean, default=False)

    converted_to_work_order: Mapped[bool] = mapped_column(Boolean, default=False)

    # ================= EXTRA =================

    notes: Mapped[str | None] = mapped_column(Text)

    terms_conditions: Mapped[str | None] = mapped_column(Text)

    status: Mapped[QuotationStatus] = mapped_column(
        Enum(QuotationStatus), default=QuotationStatus.DRAFT
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # ================= RELATIONSHIPS =================

    items = relationship(
        "QuotationItem",
        back_populates="quotation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    material_items = relationship(
        "QuotationMaterial",
        back_populates="quotation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    extra_charge_items = relationship(
        "QuotationExtraCharge",
        back_populates="quotation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


# =========================================================
# QUOTATION ITEM
# =========================================================


class QuotationItem(Base):
    __tablename__ = "quotation_items"

    id: Mapped[int] = mapped_column(primary_key=True)

    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotation_master.id", ondelete="CASCADE")
    )

    item_type: Mapped[str] = mapped_column(String(100), index=True)

    title: Mapped[str] = mapped_column(String(150))

    description: Mapped[str | None] = mapped_column(Text)

    unit: Mapped[str | None] = mapped_column(String(50))

    quantity: Mapped[float] = mapped_column(Float, default=0)

    rate: Mapped[float] = mapped_column(Float, default=0)

    amount: Mapped[float] = mapped_column(Float, default=0)

    sort_order: Mapped[int] = mapped_column(Integer, default=1)

    quotation = relationship("QuotationMaster", back_populates="items", lazy="selectin")

    measurements = relationship(
        "MeasurementDetail",
        back_populates="quotation_item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


# =========================================================
# MEASUREMENTS
# =========================================================


class MeasurementDetail(Base):
    __tablename__ = "measurement_details"

    id: Mapped[int] = mapped_column(primary_key=True)

    quotation_item_id: Mapped[int] = mapped_column(
        ForeignKey("quotation_items.id", ondelete="CASCADE")
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

    quotation_item = relationship(
        "QuotationItem", back_populates="measurements", lazy="selectin"
    )


# =========================================================
# QUOTATION LABOUR ESTIMATE
# =========================================================


class QuotationLabour(Base):
    __tablename__ = "quotation_labour"

    id: Mapped[int] = mapped_column(primary_key=True)

    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotation_master.id", ondelete="CASCADE"), index=True
    )

    # OPTIONAL REAL LABOUR LINK
    labour_id: Mapped[int | None] = mapped_column(
        ForeignKey("labour.id", ondelete="SET NULL"), nullable=True
    )

    # SNAPSHOT DATA
    skill_type: Mapped[str] = mapped_column(String(100), index=True)

    labour_count: Mapped[int] = mapped_column(Integer, default=1)

    daily_wage: Mapped[float] = mapped_column(Float, default=0)

    labour_days: Mapped[float] = mapped_column(Float, default=1)

    overtime_hours: Mapped[float] = mapped_column(Float, default=0)

    overtime_rate: Mapped[float] = mapped_column(Float, default=0)

    amount: Mapped[float] = mapped_column(Float, default=0)

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # ================= RELATIONSHIPS =================

    quotation = relationship(
        "QuotationMaster", back_populates="labour_items", lazy="selectin"
    )

    labour = relationship("Labour", lazy="selectin")


class QuotationMaterial(Base):

    __tablename__ = "quotation_materials"

    id: Mapped[int] = mapped_column(primary_key=True)

    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotation_master.id", ondelete="CASCADE")
    )

    quotation = relationship(
        "QuotationMaster", back_populates="material_items", lazy="selectin"
    )

    # OPTIONAL LINK
    material_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("materials.id"), nullable=True
    )

    material = relationship("Material", lazy="selectin")

    material_name: Mapped[str] = mapped_column(String(255))

    category: Mapped[Optional[str]] = mapped_column(String(100))

    unit: Mapped[str] = mapped_column(String(50))

    estimated_quantity: Mapped[float] = mapped_column(Float, default=0)

    estimated_rate: Mapped[float] = mapped_column(Float, default=0)

    estimated_amount: Mapped[float] = mapped_column(Float, default=0)

    notes: Mapped[Optional[str]] = mapped_column(Text)


class QuotationExtraCharge(Base):

    __tablename__ = "quotation_extra_charges"

    id: Mapped[int] = mapped_column(primary_key=True)

    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("quotation_master.id", ondelete="CASCADE")
    )

    quotation = relationship(
        "QuotationMaster", back_populates="extra_charge_items", lazy="selectin"
    )

    # OPTIONAL REAL EQUIPMENT LINK
    equipment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("equipment.id"), nullable=True
    )

    equipment = relationship("Equipment", lazy="selectin")

    expense_type: Mapped[str] = mapped_column(String(100), index=True)

    description: Mapped[Optional[str]] = mapped_column(Text)

    quantity: Mapped[float] = mapped_column(Float, default=1)

    rate: Mapped[float] = mapped_column(Float, default=0)

    amount: Mapped[float] = mapped_column(Float, default=0)

    notes: Mapped[Optional[str]] = mapped_column(Text)
