"""Add idempotency_key to expenses

Revision ID: d6d7240714f7
Revises: 3f8821e006b3
Create Date: 2026-08-09 01:03:24.341136
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd6d7240714f7'
down_revision = '3f8821e006b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('expenses', sa.Column('idempotency_key', sa.String(length=255), nullable=True))
    op.add_column('expenses', sa.Column('request_hash', sa.String(length=64), nullable=True))
    op.add_column('document_management', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.create_index(op.f('ix_expenses_idempotency_key'), 'expenses', ['idempotency_key'], unique=True)
    # --- added material & equipment schema changes ---
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



def downgrade() -> None:
    op.drop_constraint('uq_labour_payroll', 'labour_payroll', type_='unique')
    op.drop_index(op.f('ix_expenses_idempotency_key'), table_name='expenses')
    op.drop_column('expenses', 'request_hash')
    op.drop_column('expenses', 'idempotency_key')
    op.drop_column('document_management', 'is_deleted')
    # --- downgrade added material & equipment schema changes ---
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
