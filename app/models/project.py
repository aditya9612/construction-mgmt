from datetime import date
from typing import Optional

from sqlalchemy import Date, Integer, String, Float, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[str] = mapped_column(
        String(30), unique=True, nullable=False, index=True
    )

    owner_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("owners.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    owner = relationship("Owner", back_populates="projects")

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    site_address: Mapped[str] = mapped_column(String(255), nullable=False)
    site_area: Mapped[float] = mapped_column(Float, nullable=False)

    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    estimated_duration: Mapped[Optional[str]] = mapped_column(String(100))

    budget: Mapped[float] = mapped_column(Float, nullable=False)
    advance_paid: Mapped[float] = mapped_column(Float, default=0)
    remaining_balance: Mapped[float] = mapped_column(Float, nullable=False)

    payment_terms: Mapped[str] = mapped_column(String(255), nullable=False)

    engineer_name: Mapped[str] = mapped_column(String(50), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default="Planned", index=True
    )

    __table_args__ = (
        CheckConstraint("budget >= 0", name="check_budget_positive"),
        CheckConstraint("advance_paid >= 0", name="check_advance_positive"),
        CheckConstraint(
            "advance_paid <= budget", name="check_advance_less_than_budget"
        ),
        Index("idx_project_owner_status", "owner_id", "status"),
    )