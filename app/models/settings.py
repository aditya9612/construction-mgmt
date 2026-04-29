from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class UserSettings(Base, TimestampMixin):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Project selection
    default_project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)

    # Units
    unit = Column(String(20), default="Meter")

    # Notifications
    notifications_enabled = Column(Boolean, default=True)

    # Flexible preferences
    preferences = Column(JSON, nullable=True)

    # ================= NEW FIELDS =================
    financial_year = Column(String(10), default="2025-26")
    currency = Column(String(10), default="INR")

    tax_settings = Column(JSON, nullable=True)
    invoice_format = Column(String(50), default="standard")
    payment_terms = Column(String(50), default="30 days")

    user = relationship("User")