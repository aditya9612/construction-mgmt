"""Add PaymentVoucher and accrued_journal_id

Revision ID: a8777a5892d8
Revises: d6d7240714f7
Create Date: 2026-08-17 14:31:11.605611
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8777a5892d8'
down_revision = 'd6d7240714f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add accrued_journal_id to vendor_bills
    op.add_column('vendor_bills', sa.Column('accrued_journal_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_vendor_bills_accrued_journal_id',
        'vendor_bills', 'journal_entries',
        ['accrued_journal_id'], ['id'],
        ondelete='RESTRICT'
    )

    # Create payment_vouchers table
    op.create_table(
        'payment_vouchers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payment_voucher_number', sa.String(length=50), nullable=False),
        sa.Column('payment_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('party_type', sa.String(length=50), nullable=False),
        sa.Column('supplier_id', sa.Integer(), nullable=True),
        sa.Column('contractor_id', sa.Integer(), nullable=True),
        sa.Column('vendor_bill_id', sa.Integer(), nullable=True),
        sa.Column('base_amount', sa.DECIMAL(precision=18, scale=2), nullable=False, default=0),
        sa.Column('gst_amount', sa.DECIMAL(precision=18, scale=2), nullable=False, default=0),
        sa.Column('gross_amount', sa.DECIMAL(precision=18, scale=2), nullable=False, default=0),
        sa.Column('tds_amount', sa.DECIMAL(precision=18, scale=2), nullable=False, default=0),
        sa.Column('retention_amount', sa.DECIMAL(precision=18, scale=2), nullable=False, default=0),
        sa.Column('net_payable_amount', sa.DECIMAL(precision=18, scale=2), nullable=False, default=0),
        sa.Column('payment_method', sa.String(length=50), nullable=False),
        sa.Column('bank_account_id', sa.Integer(), nullable=False),
        sa.Column('reference_no', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True, default='PENDING'),
        sa.Column('journal_entry_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['bank_account_id'], ['bank_accounts.id'], ),
        sa.ForeignKeyConstraint(['contractor_id'], ['contractors.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['journal_entry_id'], ['journal_entries.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.ForeignKeyConstraint(['vendor_bill_id'], ['vendor_bills.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_payment_vouchers_payment_voucher_number'), 'payment_vouchers', ['payment_voucher_number'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_payment_vouchers_payment_voucher_number'), table_name='payment_vouchers')
    op.drop_table('payment_vouchers')
    op.drop_constraint('fk_vendor_bills_accrued_journal_id', 'vendor_bills', type_='foreignkey')
    op.drop_column('vendor_bills', 'accrued_journal_id')
