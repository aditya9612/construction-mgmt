"""Add company_id to billing_webhook_events table

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-26 23:55:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f3a4b5c6d7e8'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("billing_webhook_events", sa.Column("company_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_billing_webhook_events_company_id"), "billing_webhook_events", ["company_id"], unique=False)
    op.create_foreign_key(
        "fk_billing_webhook_events_company_id",
        "billing_webhook_events",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_billing_webhook_events_company_id", "billing_webhook_events", type_="foreignkey")
    op.drop_index(op.f("ix_billing_webhook_events_company_id"), table_name="billing_webhook_events")
    op.drop_column("billing_webhook_events", "company_id")
