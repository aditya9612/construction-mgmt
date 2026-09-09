"""Make project_id, request_type, quantity, requested_by not null on site_requests

Revision ID: f1a2b3c4d5e6
Revises: e2f3a4b5c6d7
Create Date: 2026-09-09 22:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        inspector = None
        existing_tables = set()

    if "site_requests" in existing_tables:
        # 1. Clean up or fill any NULL values in existing records before enforcing NOT NULL
        # If there are orphan rows missing project_id or requested_by, delete them safely
        op.execute(
            sa.text(
                "DELETE FROM site_requests WHERE project_id IS NULL OR requested_by IS NULL"
            )
        )
        # If request_type or quantity is NULL on remaining rows, backfill safe defaults
        op.execute(
            sa.text(
                "UPDATE site_requests SET request_type = 'Material' WHERE request_type IS NULL"
            )
        )
        op.execute(
            sa.text(
                "UPDATE site_requests SET quantity = 1.0 WHERE quantity IS NULL OR quantity <= 0"
            )
        )

        # 2. Alter columns to NOT NULL
        op.alter_column(
            "site_requests",
            "project_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        op.alter_column(
            "site_requests",
            "request_type",
            existing_type=sa.String(length=50),
            nullable=False,
        )
        op.alter_column(
            "site_requests",
            "quantity",
            existing_type=sa.Float(),
            nullable=False,
        )
        op.alter_column(
            "site_requests",
            "requested_by",
            existing_type=sa.Integer(),
            nullable=False,
        )
        # Ensure description remains nullable
        op.alter_column(
            "site_requests",
            "description",
            existing_type=sa.Text(),
            nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        inspector = None
        existing_tables = set()

    if "site_requests" in existing_tables:
        op.alter_column(
            "site_requests",
            "project_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        op.alter_column(
            "site_requests",
            "request_type",
            existing_type=sa.String(length=50),
            nullable=True,
        )
        op.alter_column(
            "site_requests",
            "quantity",
            existing_type=sa.Float(),
            nullable=True,
        )
        op.alter_column(
            "site_requests",
            "requested_by",
            existing_type=sa.Integer(),
            nullable=True,
        )
