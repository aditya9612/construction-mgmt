from sqlalchemy import Column, Integer, String, ForeignKey, Date, Text
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class Issue(Base, TimestampMixin):
    __tablename__ = "issues"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title = Column(String(255), nullable=False)

    category = Column(String(100), nullable=False)  # Material / Labour / Delay

    description = Column(Text, nullable=True)

    reported_date = Column(Date, nullable=False)

    priority = Column(String(50), default="Medium")  # Low / Medium / High

    status = Column(String(50), default="Open")  # Open / Closed

    assigned_to = Column(Integer, nullable=True)  # user_id (optional Phase 1)

    resolution = Column(Text, nullable=True)

    project = relationship("Project")