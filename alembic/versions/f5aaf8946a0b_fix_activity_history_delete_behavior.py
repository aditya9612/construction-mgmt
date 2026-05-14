"""fix activity history delete behavior

Revision ID: f5aaf8946a0b
Revises: 7f4339afcd66
Create Date: 2026-05-14 18:28:41.782742
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = 'f5aaf8946a0b'
down_revision = '7f4339afcd66'
branch_labels = None
depends_on = None


def upgrade():

    # ================= MAKE activity_id NULLABLE =================

    op.alter_column(
        'activity_history',
        'activity_id',
        existing_type=mysql.INTEGER(),
        nullable=True
    )

    # ================= CREATE NEW FK WITH SET NULL =================

    op.create_foreign_key(
        None,
        'activity_history',
        'work_activities',
        ['activity_id'],
        ['id'],
        ondelete='SET NULL'
    )


def downgrade():

    # ================= DROP NEW FK =================

    op.drop_constraint(
        None,
        'activity_history',
        type_='foreignkey'
    )

    # ================= RESTORE OLD FK =================

    op.create_foreign_key(
        op.f('activity_history_ibfk_1'),
        'activity_history',
        'work_activities',
        ['activity_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # ================= MAKE activity_id NOT NULL AGAIN =================

    op.alter_column(
        'activity_history',
        'activity_id',
        existing_type=mysql.INTEGER(),
        nullable=False
    )