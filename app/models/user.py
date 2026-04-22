import enum
from datetime import date
from typing import Any, Dict, Optional

from sqlalchemy import JSON, VARCHAR, Boolean, Date, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from app.models.base import Base, TimestampMixin
from sqlalchemy import ForeignKey , DateTime
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
]


class UserRole(str, enum.Enum):
    ADMIN = "Admin"
    PROJECT_MANAGER = "ProjectManager"
    SITE_ENGINEER = "SiteEngineer"
    CONTRACTOR = "Contractor"
    ACCOUNTANT = "Accountant"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    email = mapped_column(
        VARCHAR(255, collation="utf8mb4_0900_ai_ci"),
        unique=True,
        index=True,
        nullable=False
    )
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mobile: Mapped[Optional[str]] = mapped_column(String(20), unique=True, nullable=True, index=True)

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=UserRole.SITE_ENGINEER,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pan_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    aadhaar_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    profile_image: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    designation: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    joining_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # -----------------------
    # AUDIT
    # ------------------------
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

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
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow , index=True)
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