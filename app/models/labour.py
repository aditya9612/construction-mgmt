from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

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

    labour_title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(DECIMAL(18, 3), nullable=False, default=0)
    unit_cost: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False, default=0)
    total_cost: Mapped[Decimal] = mapped_column(DECIMAL(18, 2), nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="Active", index=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

