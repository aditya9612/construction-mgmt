from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, DateTime
from datetime import datetime
from app.models.base import Base


class Expense(Base):

    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)

    expense_id = Column(String(20), unique=True, nullable=False, index=True)

    project_id = Column(Integer, ForeignKey("projects.id"), index=True)

    expense_type = Column(String(50), index=True)
    description = Column(String(500), nullable=False)

    amount = Column(Float, index=True)

    date = Column(Date, index=True)

    payment_mode = Column(String(50))

    bill_path = Column(String(255))

    created_at = Column(DateTime, default=datetime.utcnow, index=True)