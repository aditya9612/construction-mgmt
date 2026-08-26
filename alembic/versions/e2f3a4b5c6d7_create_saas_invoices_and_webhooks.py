"""Create SaaS subscription invoices and billing webhook events tables

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-26 18:20:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e2f3a4b5c6d7'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add external_customer_id and external_subscription_id to subscriptions table
    op.add_column("subscriptions", sa.Column("external_customer_id", sa.String(length=255), nullable=True))
    op.add_column("subscriptions", sa.Column("external_subscription_id", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_subscriptions_external_customer_id"), "subscriptions", ["external_customer_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_external_subscription_id"), "subscriptions", ["external_subscription_id"], unique=False)

    # 2. Create subscription_invoices table
    op.create_table(
        "subscription_invoices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=False),
        sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subtotal", sa.DECIMAL(precision=12, scale=2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("tax_amount", sa.DECIMAL(precision=12, scale=2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("total_amount", sa.DECIMAL(precision=12, scale=2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default=sa.text("'INR'")),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("external_invoice_id", sa.String(length=255), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subscription_invoices_company_id"), "subscription_invoices", ["company_id"], unique=False)
    op.create_index(op.f("ix_subscription_invoices_subscription_id"), "subscription_invoices", ["subscription_id"], unique=False)
    op.create_index(op.f("ix_subscription_invoices_invoice_number"), "subscription_invoices", ["invoice_number"], unique=True)
    op.create_index(op.f("ix_subscription_invoices_status"), "subscription_invoices", ["status"], unique=False)
    op.create_index(op.f("ix_subscription_invoices_external_invoice_id"), "subscription_invoices", ["external_invoice_id"], unique=False)

    # 3. Create billing_webhook_events table
    op.create_table(
        "billing_webhook_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload_reference", sa.String(length=255), nullable=True),
        sa.Column("payload_summary", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "event_id", name="uq_provider_event_id"),
    )
    op.create_index(op.f("ix_billing_webhook_events_provider"), "billing_webhook_events", ["provider"], unique=False)
    op.create_index(op.f("ix_billing_webhook_events_event_id"), "billing_webhook_events", ["event_id"], unique=False)
    op.create_index(op.f("ix_billing_webhook_events_event_type"), "billing_webhook_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_billing_webhook_events_status"), "billing_webhook_events", ["status"], unique=False)


def downgrade() -> None:
    # 1. Drop billing_webhook_events table
    op.drop_table("billing_webhook_events")

    # 2. Drop subscription_invoices table
    op.drop_table("subscription_invoices")

    # 3. Drop subscription columns
    op.drop_index(op.f("ix_subscriptions_external_subscription_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_external_customer_id"), table_name="subscriptions")
    op.drop_column("subscriptions", "external_subscription_id")
    op.drop_column("subscriptions", "external_customer_id")
