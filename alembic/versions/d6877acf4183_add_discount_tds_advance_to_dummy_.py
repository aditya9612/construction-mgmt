"""add_discount_tds_advance_to_dummy_quotation

Revision ID: d6877acf4183
Revises: 356b0eaf0ef0
Create Date: 2026-09-09 18:12:10.365597
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd6877acf4183'
down_revision = '356b0eaf0ef0'
branch_labels = None
depends_on = None


def upgrade():
    # Add missing financial fields to dummy_quotations table
    op.add_column('dummy_quotations', sa.Column('discount_amount', sa.Float(), nullable=False, server_default='0'))
    op.add_column('dummy_quotations', sa.Column('tds_percent', sa.Float(), nullable=False, server_default='0'))
    op.add_column('dummy_quotations', sa.Column('tds_amount', sa.Float(), nullable=False, server_default='0'))
    op.add_column('dummy_quotations', sa.Column('advance_paid', sa.Float(), nullable=False, server_default='0'))
    op.add_column('dummy_quotations', sa.Column('balance_due', sa.Float(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('dummy_quotations', 'balance_due')
    op.drop_column('dummy_quotations', 'advance_paid')
    op.drop_column('dummy_quotations', 'tds_amount')
    op.drop_column('dummy_quotations', 'tds_percent')
    op.drop_column('dummy_quotations', 'discount_amount')
