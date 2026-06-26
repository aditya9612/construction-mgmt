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
from app.core.enums import AttendanceStatus, LabourStatus, OTPolicyType, PayrollStatus, SkillType
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

    mobile_number: Mapped[Optional[str]] = mapped_column(
        String(15),unique=True,index=True,nullable=True
    )

    email: Mapped[Optional[str]] = mapped_column(
        String(255),nullable=True,unique=True
    )

    profile_image: Mapped[Optional[str]] = mapped_column(
        String(500),nullable=True
    )

    aadhaar_number: Mapped[Optional[str]] = mapped_column(String(20), index=True)

    pan_number: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True
    )

    address: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True
    )

    labour_name: Mapped[str] = mapped_column(String(255), index=True)

    # skill_type: Mapped[SkillType] = mapped_column(SAEnum(SkillType), nullable=False)

    # daily_wage_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 2))

    labour_type_id: Mapped[int] = mapped_column(
        Integer,ForeignKey("labour_types.id"),nullable=True,index=True
    ) #we need to make it false later

    custom_daily_wage_rate: Mapped[Optional[Decimal]] = mapped_column(
        DECIMAL(18, 2),nullable=True
    )

    custom_ot_rate_per_hour: Mapped[Optional[Decimal]] = mapped_column(
        DECIMAL(18, 2),nullable=True
    )

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


    @property
    def labour_type_name(self):

        return (
            self.labour_type.name
            if self.labour_type else None
        )


    @property
    def skill_category(self):

        return (
            self.labour_type.skill_category
            if self.labour_type else None
        )


    @property
    def effective_daily_wage(self):

        if self.custom_daily_wage_rate:
            return self.custom_daily_wage_rate

        if self.labour_type:
            return self.labour_type.default_daily_wage

        return Decimal("0")


    @property
    def effective_ot_rate(self):

        if self.custom_ot_rate_per_hour:
            return self.custom_ot_rate_per_hour

        if self.labour_type:
            return (
                self.labour_type.default_ot_rate_per_hour
                or Decimal("0")
            )

        return Decimal("0")

    @property
    def default_daily_wage(self):

        if self.labour_type:
            return self.labour_type.default_daily_wage

        return Decimal("0")
    

    @property
    def role(self):
        return self.user.role if self.user else None


# ======================
# ATTENDANCE (NO CHANGE)
# ======================
class LabourAttendance(Base, TimestampMixin):
    __tablename__ = "labour_attendance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    labour_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("labour.id", ondelete="CASCADE"), index=True
    )

    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    attendance_date: Mapped[date] = mapped_column(index=True)

    status: Mapped[AttendanceStatus] = mapped_column(
        SAEnum(AttendanceStatus),
        default=AttendanceStatus.PRESENT
    )

    in_time: Mapped[Optional[time]] = mapped_column(Time)
    out_time: Mapped[Optional[time]] = mapped_column(Time)

    check_in_image: Mapped[Optional[str]] = mapped_column(String(500))
    check_out_image: Mapped[Optional[str]] = mapped_column(String(500))

    working_hours: Mapped[Decimal] = mapped_column(DECIMAL(5, 2), default=0)
    overtime_hours: Mapped[Decimal] = mapped_column(DECIMAL(5, 2), default=0)
    overtime_rate: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), default=0)

    check_in_latitude: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(9, 6))
    check_in_longitude: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(9, 6))

    check_out_latitude: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(9, 6))
    check_out_longitude: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(9, 6))

    check_in_address: Mapped[Optional[str]] = mapped_column(String(255))
    check_out_address: Mapped[Optional[str]] = mapped_column(String(255))

    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=True
    )

    task = relationship("Task")

    task_description: Mapped[str] = mapped_column(String(255))


Index(
    "idx_labour_attendance_project_date",
    LabourAttendance.project_id,
    LabourAttendance.attendance_date,
)


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
    
    advance_adjusted = Column(DECIMAL(18, 2), default=0, server_default="0")
    remarks = Column(String(500), nullable=True)

    paid_amount = Column(DECIMAL(18, 2), default=0)
    remaining_amount = Column(DECIMAL(18, 2), default=0)

    status = Column(
        SAEnum(PayrollStatus),
        default=PayrollStatus.PENDING
    )

    labour = relationship("Labour")
