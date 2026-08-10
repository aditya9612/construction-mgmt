"""Add idempotency_key to expenses

Revision ID: d6d7240714f7
Revises: 0f4fbf155ee5
Create Date: 2026-08-09 01:03:24.341136
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd6d7240714f7'
down_revision = '0f4fbf155ee5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('expenses', sa.Column('idempotency_key', sa.String(length=255), nullable=True))
    op.add_column('expenses', sa.Column('request_hash', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_expenses_idempotency_key'), 'expenses', ['idempotency_key'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_expenses_idempotency_key'), table_name='expenses')
    op.drop_column('expenses', 'request_hash')
    op.drop_column('expenses', 'idempotency_key')
