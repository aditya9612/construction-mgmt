"""add_dummy_quotation_tables

Revision ID: 356b0eaf0ef0
Revises: c1d2e3f4a5b6
Create Date: 2026-09-04 13:17:54.176402
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '356b0eaf0ef0'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'dummy_quotations',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('dummy_quotation_no', sa.String(length=50), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=True),
        sa.Column('client_name', sa.String(length=150), nullable=True),
        sa.Column('mobile_number', sa.String(length=20), nullable=True),
        sa.Column('email', sa.String(length=150), nullable=True),
        sa.Column('billing_address', sa.Text(), nullable=True),
        sa.Column('gst_number', sa.String(length=50), nullable=True),
        sa.Column('subtotal', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('gst_percent', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('cgst_percent', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('sgst_percent', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('cgst_amount', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('sgst_amount', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('grand_total', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='RESTRICT'),
    )
    op.create_index('ix_dummy_quotations_dummy_quotation_no', 'dummy_quotations', ['dummy_quotation_no'], unique=True)
    op.create_index('ix_dummy_quotations_company_id', 'dummy_quotations', ['company_id'])

    op.create_table(
        'dummy_quotation_items',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('dummy_quotation_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('unit', sa.String(length=50), nullable=True),
        sa.Column('quantity', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('rate', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('amount', sa.Float(), nullable=False, server_default='0.0'),
        sa.ForeignKeyConstraint(['dummy_quotation_id'], ['dummy_quotations.id'], ondelete='CASCADE'),
    )

    op.create_table(
        'dummy_measurement_details',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('dummy_quotation_item_id', sa.Integer(), nullable=False),
        sa.Column('length', sa.Float(), nullable=True),
        sa.Column('width', sa.Float(), nullable=True),
        sa.Column('height', sa.Float(), nullable=True),
        sa.Column('unit', sa.String(length=20), nullable=True),
        sa.Column('cubic_feet', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('cubic_meter', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('brass', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('quantity', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('formula_used', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['dummy_quotation_item_id'], ['dummy_quotation_items.id'], ondelete='CASCADE'),
    )


def downgrade():
    op.drop_table('dummy_measurement_details')
    op.drop_table('dummy_quotation_items')
    op.drop_index('ix_dummy_quotations_company_id', table_name='dummy_quotations')
    op.drop_index('ix_dummy_quotations_dummy_quotation_no', table_name='dummy_quotations')
    op.drop_table('dummy_quotations')

