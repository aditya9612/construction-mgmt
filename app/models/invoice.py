from sqlalchemy import Column, Integer, String, ForeignKey, DECIMAL, DateTime
from sqlalchemy.sql import func
from app.models.base import Base


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    owner_id = Column(Integer, ForeignKey("owners.id", ondelete="CASCADE"), index=True)

    type = Column(String(50), nullable=False)
    reference_id = Column(Integer, nullable=True)

    amount = Column(DECIMAL(18, 2), nullable=False)

    gst_percent = Column(DECIMAL(5, 2), default=0)
    gst_amount = Column(DECIMAL(18, 2), default=0)

    tax_percent = Column(DECIMAL(5, 2), default=0)
    tax_amount = Column(DECIMAL(18, 2), default=0)

    total_amount = Column(DECIMAL(18, 2), nullable=False)

    status = Column(String(20), default="pending")

    description = Column(String(255), nullable=True)

    created_at = Column(DateTime, server_default=func.now())