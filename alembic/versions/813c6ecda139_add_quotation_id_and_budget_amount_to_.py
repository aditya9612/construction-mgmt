"""Add quotation_id and budget_amount to projects

Revision ID: 813c6ecda139
Revises: 74713c495e87
Create Date: 2026-06-12 22:22:02.484617
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '813c6ecda139'
down_revision = '74713c495e87'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('projects', sa.Column('quotation_id', sa.Integer(), nullable=True))
    op.add_column('projects', sa.Column('budget_amount', sa.DECIMAL(precision=15, scale=2), nullable=False, server_default='0.00'))
    op.create_index(op.f('ix_projects_quotation_id'), 'projects', ['quotation_id'], unique=True)
    op.create_foreign_key('fk_projects_quotation_id', 'projects', 'quotation_master', ['quotation_id'], ['id'], ondelete='SET NULL')
    # ### end Alembic commands ###


def downgrade():
    op.drop_constraint('fk_projects_quotation_id', 'projects', type_='foreignkey')
    op.drop_index(op.f('ix_projects_quotation_id'), table_name='projects')
    op.drop_column('projects', 'budget_amount')
    op.drop_column('projects', 'quotation_id')
    # ### end Alembic commands ###

