"""fix_tenant_columns_backward_compatibility

Revision ID: 3e544cbb3839
Revises: 016d34b4816c
Create Date: 2026-08-25 21:29:38.898501
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '3e544cbb3839'
down_revision = '016d34b4816c'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Fix is_super_admin server default
    op.alter_column(
        'users',
        'is_super_admin',
        existing_type=mysql.TINYINT(display_width=1),
        server_default=sa.text('0'),
        existing_nullable=False
    )

    # 2. Get the default company ID dynamically
    conn = op.get_bind()
    default_company_id = conn.execute(sa.text("SELECT id FROM companies ORDER BY id ASC LIMIT 1")).scalar()

    if default_company_id is not None:
        # 3. Add dynamic server_default for users.company_id
        op.alter_column(
            'users',
            'company_id',
            existing_type=sa.Integer(),
            server_default=sa.text(str(default_company_id)),
            existing_nullable=True
        )

        # 4. Add dynamic server_default for projects.company_id
        op.alter_column(
            'projects',
            'company_id',
            existing_type=sa.Integer(),
            server_default=sa.text(str(default_company_id)),
            existing_nullable=True
        )


def downgrade():
    # Revert users.company_id server default
    op.alter_column(
        'users',
        'company_id',
        existing_type=sa.Integer(),
        server_default=None,
        existing_nullable=True
    )

    # Revert projects.company_id server default
    op.alter_column(
        'projects',
        'company_id',
        existing_type=sa.Integer(),
        server_default=None,
        existing_nullable=True
    )

    # Revert is_super_admin server default
    op.alter_column(
        'users',
        'is_super_admin',
        existing_type=mysql.TINYINT(display_width=1),
        server_default=None,
        existing_nullable=False
    )
