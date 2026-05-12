"""added work progress constraints

Revision ID: dcdeaa3c049c
Revises: 0457551c6cd0
Create Date: 2026-05-12 18:27:05.420063
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "dcdeaa3c049c"
down_revision = "0457551c6cd0"
branch_labels = None
depends_on = None


def upgrade():

    op.alter_column(
        "daily_progress_entries",
        "activity_id",
        existing_type=mysql.INTEGER(),
        nullable=False,
    )

    op.alter_column(
        "daily_progress_entries",
        "entry_date",
        existing_type=sa.DATE(),
        nullable=False,
    )

    op.alter_column(
        "daily_progress_entries",
        "today_progress",
        existing_type=mysql.DECIMAL(precision=18, scale=2),
        nullable=False,
    )

    op.drop_constraint(
        op.f("daily_progress_entries_ibfk_1"),
        "daily_progress_entries",
        type_="foreignkey",
    )

    op.create_foreign_key(
        None,
        "daily_progress_entries",
        "work_activities",
        ["activity_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade():

    op.drop_constraint(None, "daily_progress_entries", type_="foreignkey")

    op.create_foreign_key(
        op.f("daily_progress_entries_ibfk_1"),
        "daily_progress_entries",
        "work_activities",
        ["activity_id"],
        ["id"],
    )

    op.alter_column(
        "daily_progress_entries",
        "today_progress",
        existing_type=mysql.DECIMAL(precision=18, scale=2),
        nullable=True,
    )

    op.alter_column(
        "daily_progress_entries",
        "entry_date",
        existing_type=sa.DATE(),
        nullable=True,
    )

    op.alter_column(
        "daily_progress_entries",
        "activity_id",
        existing_type=mysql.INTEGER(),
        nullable=True,
    )
