"""add usage unique constraint

Revision ID: b7c3c1fadb65
Revises: bc19acf92e4b
Create Date: 2026-06-18 14:56:40.683156
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b7c3c1fadb65"
down_revision = "bc19acf92e4b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_equipment_usage_date", "equipment_usage", ["equipment_id", "usage_date"]
    )
    # ### end Alembic commands ###


def downgrade():
    op.drop_constraint("uq_equipment_usage_date", "equipment_usage", type_="unique")
    # ### end Alembic commands ###
