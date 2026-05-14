"""fix activity history and boq code

Revision ID: 7f4339afcd66
Revises: 9e4dbce48c2b
Create Date: 2026-05-14 15:49:52.034076
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "7f4339afcd66"
down_revision = "9e4dbce48c2b"
branch_labels = None
depends_on = None


def upgrade():

    # ================= FIX created_by =================

    op.alter_column(
        "daily_progress_entries",
        "created_by",
        existing_type=mysql.INTEGER(),
        nullable=True,
    )

    # ================= FIX boq_code =================

    op.alter_column(
        "work_activities",
        "boq_code",
        existing_type=sa.String(length=100),
        type_=mysql.INTEGER(),
        existing_nullable=True,
    )


def downgrade():

    # ================= REVERT boq_code =================

    op.alter_column(
        "work_activities",
        "boq_code",
        existing_type=mysql.INTEGER(),
        type_=sa.String(length=100),
        existing_nullable=True,
    )

    # ================= REVERT created_by =================

    op.alter_column(
        "daily_progress_entries",
        "created_by",
        existing_type=mysql.INTEGER(),
        nullable=False,
    )
