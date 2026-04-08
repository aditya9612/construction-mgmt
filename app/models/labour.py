from decimal import Decimal
from typing import Optional
from datetime import date
from sqlalchemy import DECIMAL, ForeignKey, Integer, String, Column
from sqlalchemy.orm import Mapped, mapped_column , relationship
from app.models.base import Base, TimestampMixin


class Labour(Base, TimestampMixin):
    __tablename__ = "labour"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    labour_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    skill_type: Mapped[str] = mapped_column(String(100), nullable=False)

    daily_wage_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False)

    contractor_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("contractors.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="Active")
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class LabourAttendance(Base, TimestampMixin):
    __tablename__ = "labour_attendance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    labour_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("labour.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    attendance_date: Mapped[date] = mapped_column(nullable=False, index=True)

    working_hours: Mapped[Decimal] = mapped_column(
        DECIMAL(5, 2), nullable=False, default=0
    )

    overtime_hours: Mapped[Decimal] = mapped_column(
        DECIMAL(5, 2), nullable=False, default=0
    )

    overtime_rate: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), nullable=False, default=0
    )

    task_description: Mapped[str] = mapped_column(String(255), nullable=False)


class LabourPayroll(Base, TimestampMixin):
    __tablename__ = "labour_payroll"

    id = Column(Integer, primary_key=True)

    labour_id = Column(Integer, ForeignKey("labour.id", ondelete="CASCADE"))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"))

    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)

    total_working_hours = Column(DECIMAL(10, 2), default=0)
    total_overtime_hours = Column(DECIMAL(10, 2), default=0)

    total_wage = Column(DECIMAL(18, 2), nullable=False)

    paid_amount = Column(DECIMAL(18, 2), default=0)
    remaining_amount = Column(DECIMAL(18, 2), default=0)
    status = Column(String(50), default="Pending")  # Pending / Paid / Partial

    labour = relationship("Labour")
