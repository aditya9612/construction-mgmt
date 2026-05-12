from sqlalchemy import Column, Integer, String, DateTime, Date, ForeignKey, DECIMAL
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base


class Owner(Base):
    __tablename__ = "owners"

    id = Column(Integer, primary_key=True, index=True)

    owner_code = Column(String(20), unique=True, nullable=False, index=True)

    owner_name = Column(String(100), nullable=False)
    mobile = Column(String(20), nullable=False, unique=True, index=True)
    email = Column(String(100), nullable=True)
    address = Column(String(255), nullable=True)
    pan = Column(String(20), nullable=True)
    satisfaction_score = Column(DECIMAL(5, 2), default=0.0) # 0 to 100


    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    projects = relationship("Project", back_populates="owner", cascade="all, delete")

    transactions = relationship(
        "OwnerTransaction", back_populates="owner", cascade="all, delete"
    )


class OwnerTransaction(Base):
    __tablename__ = "owner_transactions"

    id = Column(Integer, primary_key=True, index=True)

    owner_id = Column(Integer, ForeignKey("owners.id", ondelete="CASCADE"), index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    type = Column(String(10))  # credit / debit
    amount = Column(DECIMAL(18, 2))

    reference_type = Column(String(50))  # expense/material/invoice
    reference_id = Column(Integer, nullable=True)

    description = Column(String(255))

    created_at = Column(DateTime, server_default=func.now())

    owner = relationship("Owner", back_populates="transactions")


class OwnerPaymentSchedule(Base):
    __tablename__ = "owner_payment_schedules"

    id = Column(Integer, primary_key=True, index=True)

    owner_id = Column(Integer, ForeignKey("owners.id", ondelete="CASCADE"), index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True)

    milestone_name = Column(String(100), nullable=False)  # e.g., "Initial Booking", "1st Installment"
    due_date = Column(Date, nullable=True)
    amount = Column(DECIMAL(18, 2), nullable=False)
    
    status = Column(String(20), default="Unpaid")  # Unpaid, Paid, Partially Paid
    paid_amount = Column(DECIMAL(18, 2), default=0.0)
    
    reference_code = Column(String(50), nullable=True)  # e.g., "REF-12345"
    description = Column(String(255), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    owner = relationship("Owner")
    project = relationship("Project")

