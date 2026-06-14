from decimal import Decimal
from typing import Optional
from datetime import date, time
from sqlalchemy import (
    DECIMAL,
    ForeignKey,
    Integer,
    String,
    Column,
    Time,
    Index,
    Enum as SAEnum,
    UniqueConstraint,
    Date,
)
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.enums import AttendanceStatus, LabourStatus, PayrollStatus, SkillType
from app.models.base import Base, TimestampMixin


# ======================
# MANY-TO-MANY MAPPING
# ======================
class LabourProject(Base, TimestampMixin):
    __tablename__ = "labour_project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    labour_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("labour.id", ondelete="CASCADE"), index=True
    )

    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    assigned_date: Mapped[date] = mapped_column(Date, default=date.today)

    __table_args__ = (
        UniqueConstraint("labour_id", "project_id", name="uq_labour_project"),
    )


# ======================
# LABOUR (GLOBAL)
# ======================
class Labour(Base, TimestampMixin):
    __tablename__ = "labour"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    worker_code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )

    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )

    aadhaar_number: Mapped[Optional[str]] = mapped_column(String(20), index=True)

    labour_name: Mapped[str] = mapped_column(String(255), index=True)

    # skill_type: Mapped[SkillType] = mapped_column(SAEnum(SkillType), nullable=False)

    daily_wage_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2))

    contractor_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contractors.id", ondelete="SET NULL")
    )

    status: Mapped[LabourStatus] = mapped_column(
        SAEnum(LabourStatus), default=LabourStatus.ACTIVE
    )

    notes: Mapped[Optional[str]] = mapped_column(String(500))

    #  NEW RELATION
    projects = relationship("LabourProject", backref="labour", cascade="all, delete")
    contractor = relationship("Contractor")
    labour_type = relationship("LabourType")
    user = relationship("User")

    @property
    def contractor_name(self) -> Optional[str]:
        return self.contractor.name if self.contractor else None


# ======================
# PAYROLL (NO CHANGE)
# ======================
class LabourPayroll(Base, TimestampMixin):
    __tablename__ = "labour_payroll"

    id = Column(Integer, primary_key=True)

    labour_id = Column(Integer, ForeignKey("labour.id"))
    project_id = Column(Integer, ForeignKey("projects.id"))

    month = Column(Integer)
    year = Column(Integer)

    total_working_hours = Column(DECIMAL(10, 2))
    total_overtime_hours = Column(DECIMAL(10, 2))

    total_wage = Column(DECIMAL(18, 2))

    paid_amount = Column(DECIMAL(18, 2), default=0)
    remaining_amount = Column(DECIMAL(18, 2), default=0)

    status = Column(SAEnum(PayrollStatus), default=PayrollStatus.PENDING)

    labour = relationship("Labour")
