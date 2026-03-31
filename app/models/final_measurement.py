from sqlalchemy import Column, Integer, ForeignKey, DECIMAL
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

    final_area = Column(DECIMAL(18, 2), nullable=False)
    approved_rate = Column(DECIMAL(18, 2), nullable=False)

    extra_area = Column(DECIMAL(18, 2), default=0)
    extra_rate = Column(DECIMAL(18, 2), default=0)

    total_area = Column(DECIMAL(18, 2), nullable=False)
    total_amount = Column(DECIMAL(18, 2), nullable=False)