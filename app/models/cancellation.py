from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class OTPCancellationRequest(Base, TimestampMixin):
    __tablename__ = "otp_cancellation_requests"

    id: Mapped[int] = mapped_column(primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
    )
    document_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # PO, GRN, MIN, DAILY_STOCK
    document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )
    document_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    requested_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    otp_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        default=3,
        server_default=text("3"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="PENDING",
        server_default=text("'PENDING'"),
    )  # PENDING, VERIFIED, EXPIRED, FAILED, CANCELLED

    verified_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    company = relationship("Company", lazy="selectin")
    requested_by = relationship("User", foreign_keys=[requested_by_id], lazy="selectin")
    verified_by = relationship("User", foreign_keys=[verified_by_id], lazy="selectin")

    __table_args__ = (
        Index("idx_canc_doc", "document_type", "document_id"),
    )
