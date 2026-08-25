"""tenant isolate remaining roots

Revision ID: 060202e5d966
Revises: 3f1fcbce92f4
Create Date: 2026-08-25 22:31:15.181526
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '060202e5d966'
down_revision = '3f1fcbce92f4'
branch_labels = None
depends_on = None


def upgrade():
    tables = ['activity_types', 'labour_types', 'units', 'labour', 'owners', 'suppliers']
    for table in tables:
        op.add_column(
            table, 
            sa.Column('company_id', sa.Integer(), server_default='1', nullable=True)
        )
        op.create_index(f'ix_{table}_company_id', table, ['company_id'])
        op.create_foreign_key(
            f'fk_{table}_company_id_companies',
            table, 'companies',
            ['company_id'], ['id'],
            ondelete='RESTRICT'
        )


def downgrade():
    tables = ['activity_types', 'labour_types', 'units', 'labour', 'owners', 'suppliers']
    for table in tables:
        op.drop_constraint(f'fk_{table}_company_id_companies', table, type_='foreignkey')
        op.drop_index(f'ix_{table}_company_id', table_name=table)
        op.drop_column(table, 'company_id')

