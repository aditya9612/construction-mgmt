import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    ADMIN = "Admin"
    PROJECT_MANAGER = "Project Manager"
    SITE_ENGINEER = "Site Engineer"
    CONTRACTOR = "Contractor"
    ACCOUNTANT = "Accountant"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.SITE_ENGINEER,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # created_at/updated_at are provided by TimestampMixin

