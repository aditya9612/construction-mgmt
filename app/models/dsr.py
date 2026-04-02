from sqlalchemy import Column, Integer, String, ForeignKey, Date, Text
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class DailySiteReport(Base, TimestampMixin):
    __tablename__ = "daily_site_reports"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    report_date = Column(Date, nullable=False, index=True)

    weather = Column(String(100), nullable=True)

    work_done = Column(Text, nullable=False)
    work_planned = Column(Text, nullable=True)

    labour_count = Column(Integer, default=0)

    material_used = Column(Text, nullable=True)  # simple text for Phase 1

    issues = Column(Text, nullable=True)

    remarks = Column(Text, nullable=True)

    project = relationship("Project")