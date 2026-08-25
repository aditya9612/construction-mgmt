"""Add company_id to remaining root entities

Revision ID: 3f1fcbce92f4
Revises: 3e544cbb3839
Create Date: 2026-08-25 21:55:57.207690
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3f1fcbce92f4'
down_revision = '3e544cbb3839'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add company_id to root entities
    for table in ["contractors", "material_master", "invoices", "client_payments", "accounts", "vendor_bills"]:
        op.add_column(table, sa.Column('company_id', sa.Integer(), nullable=True, server_default='1'))
        op.create_foreign_key(f"fk_{table}_company_id", table, "companies", ["company_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    for table in ["contractors", "material_master", "invoices", "client_payments", "accounts", "vendor_bills"]:
        op.drop_constraint(f"fk_{table}_company_id", table, type_="foreignkey")
        op.drop_column(table, 'company_id')
