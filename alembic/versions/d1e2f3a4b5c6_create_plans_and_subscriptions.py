"""Create SaaS plans and subscriptions tables

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f01a2b
Create Date: 2026-08-26 17:35:00.000000
"""

import datetime
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = 'c7d8e9f01a2b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create plans table
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("billing_interval", sa.String(length=20), nullable=False, server_default=sa.text("'monthly'")),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default=sa.text("'INR'")),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plans_code"), "plans", ["code"], unique=True)

    # 2. Create subscriptions table
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'trial'")),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_reference", sa.String(length=255), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subscriptions_company_id"), "subscriptions", ["company_id"], unique=True)
    op.create_index(op.f("ix_subscriptions_plan_id"), "subscriptions", ["plan_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_subscriptions_status"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_plan_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_company_id"), table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index(op.f("ix_plans_code"), table_name="plans")
    op.drop_table("plans")
