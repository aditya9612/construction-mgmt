"""Consolidate tejas-feature migrations

Revision ID: a0b1c2d3e4f5
Revises: 3f8821e006b3
Create Date: 2026-08-21 10:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0b1c2d3e4f5'
down_revision = '3f8821e006b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # d6d7240714f7
    # ---------------------------------------------------------
    op.add_column('expenses', sa.Column('idempotency_key', sa.String(length=255), nullable=True))
    op.add_column('expenses', sa.Column('request_hash', sa.String(length=64), nullable=True))
    op.add_column('document_management', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.create_index(op.f('ix_expenses_idempotency_key'), 'expenses', ['idempotency_key'], unique=True)
    op.add_column('material_ledger', sa.Column('transaction_date', sa.Date(), server_default=sa.text('(CURRENT_DATE)'), nullable=False))
    op.add_column('material_transactions', sa.Column('journal_entry_id', sa.Integer(), nullable=True))
    op.add_column('material_transactions', sa.Column('idempotency_key', sa.String(length=255), nullable=True))
    op.add_column('material_transactions', sa.Column('request_hash', sa.String(length=64), nullable=True))
    op.add_column('material_transactions', sa.Column('transaction_date', sa.Date(), server_default=sa.text('(CURRENT_DATE)'), nullable=False))
    op.create_index(op.f('ix_material_transactions_idempotency_key'), 'material_transactions', ['idempotency_key'], unique=True)
    op.create_index(op.f('ix_material_transactions_journal_entry_id'), 'material_transactions', ['journal_entry_id'], unique=False)
    op.create_foreign_key('fk_material_transactions_journal_entry_id', 'material_transactions', 'journal_entries', ['journal_entry_id'], ['id'], ondelete='SET NULL')
    op.add_column('equipment_usage', sa.Column('rental_rate_at_usage', sa.DECIMAL(precision=10, scale=2), nullable=True))
    op.add_column('equipment_usage', sa.Column('cost', sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.create_unique_constraint('uq_labour_payroll', 'labour_payroll', ['labour_id', 'project_id', 'month', 'year'])

    # ---------------------------------------------------------
    # a8777a5892d8
    # ---------------------------------------------------------
    op.add_column('vendor_bills', sa.Column('accrued_journal_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_vendor_bills_accrued_journal_id',
        'vendor_bills', 'journal_entries',
        ['accrued_journal_id'], ['id'],
        ondelete='RESTRICT'
    )
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

    # ---------------------------------------------------------
    # a47fb8b0e1f5
    # ---------------------------------------------------------
    op.create_table('petty_cash_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('voucher_no', sa.String(length=50), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('transaction_date', sa.Date(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('source_account_id', sa.Integer(), nullable=True),
        sa.Column('amount', sa.DECIMAL(precision=18, scale=2), nullable=False),
        sa.Column('paid_to_received_from', sa.String(length=150), nullable=True),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('remarks', sa.String(length=255), nullable=True),
        sa.Column('journal_entry_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['category_id'], ['accounts.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['journal_entry_id'], ['journal_entries.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['source_account_id'], ['accounts.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_petty_cash_transactions_voucher_no'), 'petty_cash_transactions', ['voucher_no'], unique=True)

    # ---------------------------------------------------------
    # e12f819a43c9
    # ---------------------------------------------------------
    op.create_table('labour_wage_record',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('labour_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('period_type', sa.String(length=50), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('gross_wage', sa.DECIMAL(precision=18, scale=2), nullable=True),
        sa.Column('net_wage', sa.DECIMAL(precision=18, scale=2), nullable=True),
        sa.Column('payment_mode', sa.String(length=50), nullable=True),
        sa.Column('bank_account_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['bank_account_id'], ['accounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['labour_id'], ['labour.id']),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_labour_wage_record_end_date'), 'labour_wage_record', ['end_date'], unique=False)
    op.create_index(op.f('ix_labour_wage_record_labour_id'), 'labour_wage_record', ['labour_id'], unique=False)
    op.create_index(op.f('ix_labour_wage_record_project_id'), 'labour_wage_record', ['project_id'], unique=False)
    op.create_index(op.f('ix_labour_wage_record_start_date'), 'labour_wage_record', ['start_date'], unique=False)

    # ---------------------------------------------------------
    # 7957f84c4e1f
    # ---------------------------------------------------------
    op.add_column('invoices', sa.Column('invoice_number', sa.String(length=50), nullable=True))
    op.add_column('invoices', sa.Column('invoice_date', sa.Date(), nullable=True))
    op.add_column('invoices', sa.Column('party_gstin', sa.String(length=20), nullable=True))
    op.add_column('invoices', sa.Column('cgst', sa.DECIMAL(precision=18, scale=2), server_default='0.00', nullable=True))
    op.add_column('invoices', sa.Column('sgst', sa.DECIMAL(precision=18, scale=2), server_default='0.00', nullable=True))
    op.add_column('invoices', sa.Column('igst', sa.DECIMAL(precision=18, scale=2), server_default='0.00', nullable=True))
    op.add_column('invoices', sa.Column('invoice_copy_url', sa.String(length=500), nullable=True))
    op.add_column('invoices', sa.Column('gst_document_url', sa.String(length=500), nullable=True))
    op.create_index(op.f('ix_invoices_invoice_number'), 'invoices', ['invoice_number'], unique=False)

    op.add_column('vendor_bills', sa.Column('party_gstin', sa.String(length=20), nullable=True))
    op.add_column('vendor_bills', sa.Column('cgst', sa.DECIMAL(precision=18, scale=2), server_default='0.00', nullable=True))
    op.add_column('vendor_bills', sa.Column('sgst', sa.DECIMAL(precision=18, scale=2), server_default='0.00', nullable=True))
    op.add_column('vendor_bills', sa.Column('igst', sa.DECIMAL(precision=18, scale=2), server_default='0.00', nullable=True))
    op.add_column('vendor_bills', sa.Column('gst_document_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    # ---------------------------------------------------------
    # 7957f84c4e1f
    # ---------------------------------------------------------
    op.drop_column('vendor_bills', 'gst_document_url')
    op.drop_column('vendor_bills', 'igst')
    op.drop_column('vendor_bills', 'sgst')
    op.drop_column('vendor_bills', 'cgst')
    op.drop_column('vendor_bills', 'party_gstin')

    op.drop_index(op.f('ix_invoices_invoice_number'), table_name='invoices')
    op.drop_column('invoices', 'gst_document_url')
    op.drop_column('invoices', 'invoice_copy_url')
    op.drop_column('invoices', 'igst')
    op.drop_column('invoices', 'sgst')
    op.drop_column('invoices', 'cgst')
    op.drop_column('invoices', 'party_gstin')
    op.drop_column('invoices', 'invoice_date')
    op.drop_column('invoices', 'invoice_number')

    # ---------------------------------------------------------
    # e12f819a43c9
    # ---------------------------------------------------------
    op.drop_index(op.f('ix_labour_wage_record_start_date'), table_name='labour_wage_record')
    op.drop_index(op.f('ix_labour_wage_record_project_id'), table_name='labour_wage_record')
    op.drop_index(op.f('ix_labour_wage_record_labour_id'), table_name='labour_wage_record')
    op.drop_index(op.f('ix_labour_wage_record_end_date'), table_name='labour_wage_record')
    op.drop_table('labour_wage_record')

    # ---------------------------------------------------------
    # a47fb8b0e1f5
    # ---------------------------------------------------------
    op.drop_index(op.f('ix_petty_cash_transactions_voucher_no'), table_name='petty_cash_transactions')
    op.drop_table('petty_cash_transactions')

    # ---------------------------------------------------------
    # a8777a5892d8
    # ---------------------------------------------------------
    op.drop_index(op.f('ix_payment_vouchers_payment_voucher_number'), table_name='payment_vouchers')
    op.drop_table('payment_vouchers')
    op.drop_constraint('fk_vendor_bills_accrued_journal_id', 'vendor_bills', type_='foreignkey')
    op.drop_column('vendor_bills', 'accrued_journal_id')

    # ---------------------------------------------------------
    # d6d7240714f7
    # ---------------------------------------------------------
    op.drop_constraint('uq_labour_payroll', 'labour_payroll', type_='unique')
    op.drop_index(op.f('ix_expenses_idempotency_key'), table_name='expenses')
    op.drop_column('expenses', 'request_hash')
    op.drop_column('expenses', 'idempotency_key')
    op.drop_column('document_management', 'is_deleted')
    op.drop_column('equipment_usage', 'cost')
    op.drop_column('equipment_usage', 'rental_rate_at_usage')
    op.drop_constraint('fk_material_transactions_journal_entry_id', 'material_transactions', type_='foreignkey')
    op.drop_index(op.f('ix_material_transactions_journal_entry_id'), table_name='material_transactions')
    op.drop_index(op.f('ix_material_transactions_idempotency_key'), table_name='material_transactions')

    op.drop_column('material_transactions', 'transaction_date')
    op.drop_column('material_transactions', 'request_hash')
    op.drop_column('material_transactions', 'idempotency_key')
    op.drop_column('material_transactions', 'journal_entry_id')
    op.drop_column('material_ledger', 'transaction_date')
