"""add unit_id to material tables

Revision ID: 5f1191330b96
Revises: 833ab2c4c989
Create Date: 2026-06-19 14:38:23.256232
"""

from alembic import op
import sqlalchemy as sa


revision = "5f1191330b96"
down_revision = "833ab2c4c989"
branch_labels = None
depends_on = None


def upgrade():

    # material_master
    op.create_foreign_key(
        "fk_material_master_unit_id",
        "material_master",
        "units",
        ["unit_id"],
        ["id"],
    )

    op.drop_column(
        "material_master",
        "unit",
    )

    # materials
    op.add_column(
        "materials",
        sa.Column(
            "unit_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_materials_unit_id",
        "materials",
        "units",
        ["unit_id"],
        ["id"],
    )

    op.drop_column(
        "materials",
        "unit",
    )


def downgrade():

    op.add_column(
        "material_master",
        sa.Column(
            "unit",
            sa.String(length=50),
            nullable=False,
        ),
    )

    op.drop_constraint(
        "fk_material_master_unit_id",
        "material_master",
        type_="foreignkey",
    )

    op.add_column(
        "materials",
        sa.Column(
            "unit",
            sa.String(length=50),
            nullable=False,
            server_default="unit",
        ),
    )

    op.drop_constraint(
        "fk_materials_unit_id",
        "materials",
        type_="foreignkey",
    )

    op.drop_column(
        "materials",
        "unit_id",
    )