from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class CompanySettings(Base, TimestampMixin):

    __tablename__ = "company_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    company_name: Mapped[str | None] = mapped_column(String(255))

    company_logo: Mapped[str | None] = mapped_column(String(500))

    gst_number: Mapped[str | None] = mapped_column(String(100))

    mobile_number: Mapped[str | None] = mapped_column(String(20))

    email: Mapped[str | None] = mapped_column(String(255))

    website: Mapped[str | None] = mapped_column(String(255))

    address: Mapped[str | None] = mapped_column(Text)

    bank_name: Mapped[str | None] = mapped_column(String(255))

    account_holder_name: Mapped[str | None] = mapped_column(String(255))

    account_number: Mapped[str | None] = mapped_column(String(100))

    ifsc_code: Mapped[str | None] = mapped_column(String(100))

    upi_id: Mapped[str | None] = mapped_column(String(255))

    signature_image: Mapped[str | None] = mapped_column(String(500))

    terms_conditions: Mapped[str | None] = mapped_column(Text)
