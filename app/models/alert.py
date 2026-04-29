from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from datetime import datetime
from app.models.base import Base


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)

    alert_type = Column(String(50), nullable=False)

    message = Column(String(255), nullable=False)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    status = Column(String(20), default="active")

    created_at = Column(DateTime, default=datetime.utcnow)