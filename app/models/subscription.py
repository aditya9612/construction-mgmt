from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any, List
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Float,
    ForeignKey,
    DateTime,
    JSON,
    DECIMAL,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    billing_interval: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")  # monthly, yearly
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    features: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    subscriptions = relationship("Subscription", back_populates="plan", cascade="all, delete-orphan")


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plans.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="trial", index=True
    )  # trial, active, suspended, cancelled, expired
    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    external_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    external_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    company = relationship("Company", back_populates="subscription")
    plan = relationship("Plan", back_populates="subscriptions")
    invoices = relationship("SubscriptionInvoice", back_populates="subscription", cascade="all, delete-orphan")


class SubscriptionInvoice(Base, TimestampMixin):
    __tablename__ = "subscription_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    invoice_number: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    billing_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False, default=Decimal("0.00"))
    tax_amount: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False, default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False, default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending", index=True
    )  # draft, pending, paid, failed, void, refunded
    external_invoice_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    company = relationship("Company", back_populates="subscription_invoices")
    subscription = relationship("Subscription", back_populates="invoices")


class BillingWebhookEvent(Base, TimestampMixin):
    __tablename__ = "billing_webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # razorpay, stripe, etc.
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload_summary: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending", index=True
    )  # pending, processed, failed, ignored
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    company = relationship("Company", backref="billing_webhook_events")

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_provider_event_id"),
    )


class ManualPaymentTransaction(Base, TimestampMixin):
    __tablename__ = "manual_payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plans.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    invoice_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("subscription_invoices.id", ondelete="SET NULL"), index=True, nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(DECIMAL(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False, default="UPI")
    transaction_reference: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    utr_reference: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending", index=True
    )  # pending, verified, rejected
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    verified_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    company = relationship("Company", backref="manual_payment_transactions")
    subscription = relationship("Subscription", backref="manual_payment_transactions")
    plan = relationship("Plan", backref="manual_payment_transactions")
    invoice = relationship("SubscriptionInvoice", backref="manual_payment_transactions")
    verifier = relationship("User", foreign_keys=[verified_by], backref="verified_manual_payments")


