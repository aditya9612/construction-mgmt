"""add foreign keys to work activities

Revision ID: 0457551c6cd0
Revises: 884c40cc5b68
Create Date: 2026-05-12 17:31:47.203346
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0457551c6cd0"
down_revision = "884c40cc5b68"
branch_labels = None
depends_on = None


def upgrade():

    op.create_index(
        op.f("ix_work_activities_engineer_id"),
        "work_activities",
        ["engineer_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_work_activities_project_id"),
        "work_activities",
        ["project_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_work_activities_project_id",
        "work_activities",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_foreign_key(
        "fk_work_activities_engineer_id",
        "work_activities",
        "users",
        ["engineer_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():

    op.drop_constraint(
        "fk_work_activities_engineer_id", "work_activities", type_="foreignkey"
    )

    op.drop_constraint(
        "fk_work_activities_project_id", "work_activities", type_="foreignkey"
    )

    op.drop_index(op.f("ix_work_activities_project_id"), table_name="work_activities")

    op.drop_index(op.f("ix_work_activities_engineer_id"), table_name="work_activities")
