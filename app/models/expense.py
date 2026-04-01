from sqlalchemy import Column, Integer, String, ForeignKey, Date, DECIMAL
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Expense(Base, TimestampMixin):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    boq_item_id = Column(
    Integer,
    ForeignKey("boq_items.id", ondelete="SET NULL"),
    nullable=True,
    index=True,
    )

    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)

    amount: Mapped[float] = mapped_column(DECIMAL(18, 2), nullable=False)

    expense_date: Mapped[str] = mapped_column(Date, nullable=False, index=True)
    payment_mode: Mapped[str] = mapped_column(String(50), nullable=False)