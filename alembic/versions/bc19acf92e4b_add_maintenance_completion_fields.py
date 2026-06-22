"""add maintenance completion fields

Revision ID: bc19acf92e4b
Revises: 7e1cf77df365
Create Date: 2026-06-18 13:16:11.028540
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "bc19acf92e4b"
down_revision = "7e1cf77df365"
branch_labels = None
depends_on = None


def upgrade():

    op.add_column(
        "equipment_maintenance",
        sa.Column(
            "is_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.add_column(
        "equipment_maintenance", sa.Column("completed_at", sa.DateTime(), nullable=True)
    )

    op.create_index(
        op.f("ix_equipment_maintenance_is_completed"),
        "equipment_maintenance",
        ["is_completed"],
        unique=False,
    )

    op.alter_column("equipment_maintenance", "is_completed", server_default=None)


def downgrade():

    op.drop_index(
        op.f("ix_equipment_maintenance_is_completed"),
        table_name="equipment_maintenance",
    )

    op.drop_column("equipment_maintenance", "completed_at")

    op.drop_column("equipment_maintenance", "is_completed")
