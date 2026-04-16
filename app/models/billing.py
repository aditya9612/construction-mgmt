from sqlalchemy import Column, Integer, String, ForeignKey, Date, DECIMAL
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class RABill(Base, TimestampMixin):
    __tablename__ = "ra_bills"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    contractor_id = Column(
        Integer,
        ForeignKey("contractors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    bill_number = Column(String(100), nullable=False, unique=True, index=True)

    work_description = Column(String(255), nullable=False)

    quantity = Column(DECIMAL(18, 3), nullable=False)
    rate = Column(DECIMAL(18, 2), nullable=False)

    gross_amount = Column(DECIMAL(18, 2), nullable=False)
    deductions = Column(DECIMAL(18, 2), default=0)
    net_amount = Column(DECIMAL(18, 2), nullable=False)

    gst_percent = Column(DECIMAL(5, 2), default=0)
    total_amount = Column(DECIMAL(18, 2), nullable=False)

    bill_date = Column(Date, nullable=False)

    work_order_id = Column(
        Integer,
        ForeignKey("work_orders.id", ondelete="SET NULL"),
        nullable=True,
    )

    status = Column(
        String(50),
        default="Draft"  # Draft → Submitted → Approved → Paid
    )

    project = relationship("Project")
    contractor = relationship("Contractor")