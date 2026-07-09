from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import PaymentMethod, PaymentStatus
from app.models.base import Base, TimestampMixin
from app.models.invoice import Invoice
from app.models.project import Project
from app.models.user import User


class ClientPayment(Base, TimestampMixin):
    __tablename__ = "client_payments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    payment_no: Mapped[str] = mapped_column(
        String(30),
        unique=True,
        nullable=False,
        index=True,
    )

    client_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    payment_method: Mapped[PaymentMethod] = mapped_column(
        SAEnum(PaymentMethod),
        nullable=False,
    )

    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus),
        nullable=False,
        server_default=PaymentStatus.VERIFICATION_PENDING.value,
    )

    bank_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    cheque_no: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    reference_no: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    transaction_id: Mapped[Optional[str]] = mapped_column(
        String(150),
        unique=True,
        nullable=True,
    )

    receipt_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    remarks: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    payment_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    verified_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    client_user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[client_user_id],
    )

    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="client_payments",
    )

    verifier: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[verified_by],
    )

    invoice: Mapped["Invoice"] = relationship(
        "Invoice",
        back_populates="payments",
    )

    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="ck_client_payment_amount_positive",
        ),
        # NOTE: reference_no / (bank_name, cheque_no) are intentionally NOT
        # enforced as DB-level UniqueConstraints here. Business rule (see
        # check_duplicate_payment in app/api/client_payment.py) allows a
        # REJECTED/FAILED payment's cheque/reference number to be reused in
        # a later resubmission. MySQL has no partial/filtered unique index,
        # so a table-wide UniqueConstraint would reject that legitimate
        # resubmission with an IntegrityError even though the
        # application-level check approved it. Duplicate prevention for
        # "live" (non-rejected/failed) payments is enforced exclusively in
        # check_duplicate_payment(), which is called under normal request
        # flow before every insert/update.
        #
        # IMPORTANT: this check (and the original DB constraint it
        # replaces) is GLOBAL on (bank_name, cheque_no) — it is NOT scoped
        # by client. check_duplicate_payment() filters only on bank_name +
        # cheque_no with no client_user_id predicate, so the index below
        # matches that actual query shape instead of a client-scoped one.
        Index(
            "idx_client_payment_reference_no",
            "reference_no",
        ),
        Index(
            "idx_client_payment_cheque",
            "bank_name",
            "cheque_no",
        ),
        Index(
            "idx_client_payment_client",
            "client_user_id",
        ),
        Index(
            "idx_client_payment_project",
            "project_id",
        ),
        Index(
            "idx_client_payment_status",
            "payment_status",
        ),
        Index(
            "idx_client_payment_method",
            "payment_method",
        ),
        Index(
            "idx_client_payment_date",
            "payment_date",
        ),
        Index(
            "idx_client_payment_client_status",
            "client_user_id",
            "payment_status",
        ),
    )
