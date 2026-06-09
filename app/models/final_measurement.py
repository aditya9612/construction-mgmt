from sqlalchemy import Column, Integer, ForeignKey, DECIMAL, text, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class FinalMeasurement(Base):
    __tablename__ = "final_measurements"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task_id = Column(
        Integer,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    boq_item_id = Column(
        Integer,
        ForeignKey("boq_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    measured_qty = Column(DECIMAL(18, 3), nullable=False, default=0, server_default=text("0"))
    certified_qty = Column(DECIMAL(18, 3), nullable=False, default=0, server_default=text("0"))
    rejected_qty = Column(DECIMAL(18, 3), nullable=False, default=0, server_default=text("0"))
    retention_amount = Column(DECIMAL(18, 2), nullable=False, default=0, server_default=text("0"))

    status = Column(String(50), nullable=False, default="DRAFT", server_default="'DRAFT'")

    final_area = Column(DECIMAL(18, 2), nullable=False)
    approved_rate = Column(DECIMAL(18, 2), nullable=False)

    extra_area = Column( DECIMAL(18, 2), nullable=False, default=0, server_default=text("0") )
    extra_rate = Column( DECIMAL(18, 2), nullable=False, default=0, server_default=text("0") )

    total_area = Column(DECIMAL(18, 2), nullable=False)
    total_amount = Column(DECIMAL(18, 2), nullable=False)