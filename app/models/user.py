import enum
from datetime import date
from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON,
    VARCHAR,
    Boolean,
    Date,
    Enum,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from app.models.base import Base, TimestampMixin
from sqlalchemy import ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from typing import Optional

# Exact role values for RBAC
ROLES = [
    "Admin",
    "ProjectManager",
    "SiteEngineer",
    "Contractor",
    "Accountant",
    "Client",
    "Labour",
]


class UserRole(str, enum.Enum):
    ADMIN = "Admin"
    PROJECT_MANAGER = "ProjectManager"
    SITE_ENGINEER = "SiteEngineer"
    CONTRACTOR = "Contractor"
    ACCOUNTANT = "Accountant"
    CLIENT = "Client"
    LABOUR = "Labour"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    __table_args__ = (Index("idx_users_is_deleted_id", "is_deleted", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    email = mapped_column(
        VARCHAR(255, collation="utf8mb4_0900_ai_ci"),
        unique=True,
        index=True,
        nullable=False,
    )
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mobile: Mapped[Optional[str]] = mapped_column(
        String(20), unique=True, nullable=True, index=True
    )

    # role: Mapped[UserRole] = mapped_column(
    #     Enum(UserRole, name="user_role", values_callable=lambda x: [e.value for e in x]),
    #     nullable=False,
    #     default=UserRole.SITE_ENGINEER,
    # )

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=UserRole.SITE_ENGINEER.value,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pan_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    aadhaar_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    profile_image: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    designation: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    joining_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # -----------------------
    # AUDIT
    # ------------------------
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # ------------------------
    # SOFT DELETE
    # ------------------------
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    @property
    def user_id(self) -> int:
        return self.id

    @property
    def mobile_number(self) -> Optional[str]:
        return self.mobile


class UserAuditLog(Base):
    __tablename__ = "user_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(100))

    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    changed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    change_group_id: Mapped[str] = mapped_column(String(36), index=True)


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    action: Mapped[str] = mapped_column(String(100), index=True)
    entity: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    performed_by = mapped_column(ForeignKey("users.id"), index=True)
    details: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserAttendance(Base, TimestampMixin):
    __tablename__ = "user_attendance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    project_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    attendance_date: Mapped[date] = mapped_column(Date, index=True)

    status: Mapped[str] = mapped_column(String(50), default="present")

    in_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    out_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    check_in_image: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    check_out_image: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    working_hours: Mapped[float] = mapped_column(Float, default=0)
    overtime_hours: Mapped[float] = mapped_column(Float, default=0)
    overtime_rate: Mapped[float] = mapped_column(Float, default=0)

    check_in_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_in_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    check_out_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_out_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    check_in_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    check_out_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    task_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tasks.id"), nullable=True
    )
    task_description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    remarks: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    work_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    task_deadline_reason: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )

    work_report_pdf: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_outside_geofence: Mapped[bool] = mapped_column(Boolean, default=False)

    is_late: Mapped[bool] = mapped_column(Boolean, default=False)
    late_minutes: Mapped[int] = mapped_column(Integer, default=0)
    is_early_departure: Mapped[bool] = mapped_column(Boolean, default=False)
    early_minutes: Mapped[int] = mapped_column(Integer, default=0)
    work_location_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    project = relationship("Project")
    task = relationship("Task")
    user = relationship("User", backref="attendance_records", foreign_keys=[user_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])


Index(
    "idx_user_attendance_project_date",
    UserAttendance.project_id,
    UserAttendance.attendance_date,
)
