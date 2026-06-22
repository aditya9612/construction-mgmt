from datetime import date, datetime
from typing import Optional, List
from decimal import Decimal
from pydantic import Field
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    DECIMAL,
    ForeignKey,
    Integer,
    String,
    Text,
    CheckConstraint,
    Index,
    Enum as SqlEnum,
    JSON,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import EquipmentCondition, EquipmentStatus
from app.models.base import Base, TimestampMixin
from app.schemas.base import BaseSchema


class Equipment(Base, TimestampMixin):
    __tablename__ = "equipment"

    __table_args__ = (
        CheckConstraint("working_hours >= 0", name="check_working_hours_positive"),
        CheckConstraint("fuel_used >= 0", name="check_fuel_used_positive"),
        CheckConstraint(
            "rental_cost >= 0", name="check_equipment_rental_cost_positive"
        ),
        Index(
            "ix_equipment_code_unique_active",
            "equipment_code",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("ix_equipment_project_condition", "project_id", "condition"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    equipment_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    equipment_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    operator_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    working_hours: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), nullable=False, default=0
    )

    fuel_used: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), nullable=False, default=0
    )

    condition: Mapped[Optional[EquipmentCondition]] = mapped_column(
        SqlEnum(EquipmentCondition),
        nullable=True,
        index=True,
    )

    rental_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), nullable=False, default=0
    )

    maintenance_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, index=True
    )

    # 🔥 MAIN FIELD (CONTROL EVERYTHING)
    status: Mapped[EquipmentStatus] = mapped_column(
        SqlEnum(EquipmentStatus),
        default=EquipmentStatus.AVAILABLE,
        nullable=False,
        index=True,
    )

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    deleted_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ================= RELATIONSHIPS =================

    usages: Mapped[List["EquipmentUsage"]] = relationship(
        "EquipmentUsage",
        back_populates="equipment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    maintenances: Mapped[List["EquipmentMaintenance"]] = relationship(
        "EquipmentMaintenance",
        back_populates="equipment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    rentals: Mapped[List["EquipmentRental"]] = relationship(
        "EquipmentRental",
        back_populates="equipment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    audit_logs: Mapped[List["EquipmentAuditLog"]] = relationship(
        "EquipmentAuditLog",
        back_populates="equipment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self):
        return (
            f"<Equipment id={self.id} code={self.equipment_code} status={self.status}>"
        )


class EquipmentUsage(Base, TimestampMixin):
    __tablename__ = "equipment_usage"

    __table_args__ = (
        CheckConstraint(
            "working_hours >= 0",
            name="check_usage_hours_positive",
        ),
        CheckConstraint(
            "fuel_used >= 0",
            name="check_usage_fuel_positive",
        ),
        UniqueConstraint(
            "equipment_id",
            "usage_date",
            name="uq_equipment_usage_date",
        ),
        Index(
            "ix_equipment_usage_date",
            "equipment_id",
            "usage_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), index=True
    )

    working_hours: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    fuel_used: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)

    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500))

    equipment: Mapped["Equipment"] = relationship(
        back_populates="usages", lazy="selectin"
    )


class EquipmentMaintenance(Base, TimestampMixin):
    __tablename__ = "equipment_maintenance"

    __table_args__ = (
        Index(
            "ix_equipment_maintenance_date",
            "equipment_id",
            "maintenance_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    equipment_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"),
        index=True,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    maintenance_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    cost: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2))

    next_maintenance_date: Mapped[Optional[date]] = mapped_column(Date)

    # NEW FIELDS
    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    equipment: Mapped["Equipment"] = relationship(
        back_populates="maintenances",
        lazy="selectin",
    )


class EquipmentRental(Base, TimestampMixin):
    __tablename__ = "equipment_rental"

    __table_args__ = (
        CheckConstraint("rental_cost >= 0", name="check_rental_cost_positive"),
        Index("ix_equipment_rental_dates", "equipment_id", "start_date", "end_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), index=True
    )

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Optional[date]] = mapped_column(Date)

    rental_cost: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False)

    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    equipment: Mapped["Equipment"] = relationship(
        back_populates="rentals", lazy="selectin"
    )


class EquipmentAuditLog(Base, TimestampMixin):
    __tablename__ = "equipment_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), index=True
    )

    action: Mapped[str] = mapped_column(String(50), nullable=False)

    old_values: Mapped[Optional[dict]] = mapped_column(JSON)
    new_values: Mapped[Optional[dict]] = mapped_column(JSON)

    user_id: Mapped[Optional[int]] = mapped_column(Integer)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))

    equipment: Mapped["Equipment"] = relationship(
        back_populates="audit_logs", lazy="selectin"
    )


class EquipmentPurchase(Base, TimestampMixin):
    __tablename__ = "equipment_purchase"

    id = mapped_column(Integer, primary_key=True)

    purchase_type = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("equipment.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    equipment: Mapped["Equipment"] = relationship(lazy="selectin")

    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)

    vendor_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    invoice_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    unit_price: Mapped[Decimal] = mapped_column(
        DECIMAL(12, 2),
        nullable=False,
    )

    total_amount: Mapped[Decimal] = mapped_column(
        DECIMAL(14, 2),
        nullable=False,
    )

    warranty_end_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
