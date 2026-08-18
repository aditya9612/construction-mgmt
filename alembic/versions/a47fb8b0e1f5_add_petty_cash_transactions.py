"""add petty cash transactions

Revision ID: a47fb8b0e1f5
Revises: a8777a5892d8
Create Date: 2026-08-18 10:02:55.503443
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a47fb8b0e1f5'
down_revision = 'a8777a5892d8'
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index(op.f('ix_petty_cash_transactions_voucher_no'), table_name='petty_cash_transactions')
    op.drop_table('petty_cash_transactions')
