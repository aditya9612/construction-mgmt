"""Add company_id to gst_returns

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-10 15:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        existing_tables = set()

    if "gst_returns" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("gst_returns")]
        if "company_id" not in columns:
            op.add_column('gst_returns', sa.Column('company_id', sa.Integer(), nullable=False))
            op.create_index(op.f('ix_gst_returns_company_id'), 'gst_returns', ['company_id'], unique=False)
            op.create_foreign_key('fk_gst_returns_company_id', 'gst_returns', 'companies', ['company_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        existing_tables = set()

    if "gst_returns" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("gst_returns")]
        if "company_id" in columns:
            try:
                op.drop_constraint('fk_gst_returns_company_id', 'gst_returns', type_='foreignkey')
            except Exception:
                pass
            try:
                op.drop_index(op.f('ix_gst_returns_company_id'), table_name='gst_returns')
            except Exception:
                pass
            op.drop_column('gst_returns', 'company_id')
