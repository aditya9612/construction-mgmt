"""Create manual_payment_transactions table

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
Create Date: 2026-08-27 00:25:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_payment_transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.DECIMAL(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("payment_method", sa.String(length=50), nullable=False, server_default="UPI"),
        sa.Column("transaction_reference", sa.String(length=100), nullable=False),
        sa.Column("utr_reference", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["subscription_invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_manual_payment_transactions_company_id"), "manual_payment_transactions", ["company_id"], unique=False)
    op.create_index(op.f("ix_manual_payment_transactions_subscription_id"), "manual_payment_transactions", ["subscription_id"], unique=False)
    op.create_index(op.f("ix_manual_payment_transactions_plan_id"), "manual_payment_transactions", ["plan_id"], unique=False)
    op.create_index(op.f("ix_manual_payment_transactions_invoice_id"), "manual_payment_transactions", ["invoice_id"], unique=False)
    op.create_index(op.f("ix_manual_payment_transactions_transaction_reference"), "manual_payment_transactions", ["transaction_reference"], unique=True)
    op.create_index(op.f("ix_manual_payment_transactions_utr_reference"), "manual_payment_transactions", ["utr_reference"], unique=True)
    op.create_index(op.f("ix_manual_payment_transactions_status"), "manual_payment_transactions", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("manual_payment_transactions")

