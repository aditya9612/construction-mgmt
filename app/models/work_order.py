from sqlalchemy import Column, Integer, String, ForeignKey, DECIMAL
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class WorkOrder(Base, TimestampMixin):
    __tablename__ = "work_orders"

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

    work_order_number = Column(String(50), unique=True, nullable=False)

    work_description = Column(String(255), nullable=False)

    total_quantity = Column(DECIMAL(18, 2), nullable=False)
    completed_quantity = Column(DECIMAL(18, 2), default=0)

    rate = Column(DECIMAL(18, 2), nullable=False)

    total_amount = Column(DECIMAL(18, 2), nullable=False)

    status = Column(
        String(50),
        default="Assigned"  # Assigned / In Progress / Completed
    )

    project = relationship("Project")
    contractor = relationship("Contractor")