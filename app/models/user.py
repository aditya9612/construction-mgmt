import enum
from datetime import date
from typing import Optional

from sqlalchemy import Boolean, Date, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


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

    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)
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

    @property
    def user_id(self) -> int:
        return self.id

    @property
    def mobile_number(self) -> Optional[str]:
        return self.mobile