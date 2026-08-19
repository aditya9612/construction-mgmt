"""add_labour_wage_record

Revision ID: e12f819a43c9
Revises: a47fb8b0e1f5
Create Date: 2026-08-19 14:04:10.615643
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e12f819a43c9'
down_revision = 'a47fb8b0e1f5'
branch_labels = None
depends_on = None


def upgrade():
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

def downgrade():
    op.drop_index(op.f('ix_labour_wage_record_start_date'), table_name='labour_wage_record')
    op.drop_index(op.f('ix_labour_wage_record_project_id'), table_name='labour_wage_record')
    op.drop_index(op.f('ix_labour_wage_record_labour_id'), table_name='labour_wage_record')
    op.drop_index(op.f('ix_labour_wage_record_end_date'), table_name='labour_wage_record')
    op.drop_table('labour_wage_record')
