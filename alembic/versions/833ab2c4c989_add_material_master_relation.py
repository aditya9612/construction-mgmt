"""add material master relation

Revision ID: 833ab2c4c989
Revises: b7c3c1fadb65
Create Date: 2026-06-18 17:36:32.296995
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "833ab2c4c989"
down_revision = "b7c3c1fadb65"
branch_labels = None
depends_on = None


def upgrade():

    # Populate existing rows
    op.execute(
        """
        UPDATE materials
        SET material_master_id = 1
        WHERE material_master_id IS NULL
        """
    )

    # Make NOT NULL
    op.alter_column(
        "materials",
        "material_master_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    # Drop old unique constraint if exists
    try:
        op.drop_constraint(
            "unique_material_per_project_supplier",
            "materials",
            type_="unique",
        )
    except Exception:
        pass

    # Create index
    try:
        op.create_index(
            "ix_materials_material_master_id",
            "materials",
            ["material_master_id"],
            unique=False,
        )
    except Exception:
        pass

    # Create unique constraint
    try:
        op.create_unique_constraint(
            "uq_project_master_supplier",
            "materials",
            [
                "project_id",
                "material_master_id",
                "supplier_id",
            ],
        )
    except Exception:
        pass

    # Create foreign key
    try:
        op.create_foreign_key(
            "fk_materials_material_master",
            "materials",
            "material_master",
            ["material_master_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    except Exception:
        pass

    # Remove obsolete task index if exists
    try:
        op.drop_index(
            "ix_tasks_assigned_user_id",
            table_name="tasks",
        )
    except Exception:
        pass


def downgrade():

    try:
        op.create_index(
            "ix_tasks_assigned_user_id",
            "tasks",
            ["assigned_user_id"],
            unique=False,
        )
    except Exception:
        pass

    try:
        op.drop_constraint(
            "fk_materials_material_master",
            "materials",
            type_="foreignkey",
        )
    except Exception:
        pass

    try:
        op.drop_constraint(
            "uq_project_master_supplier",
            "materials",
            type_="unique",
        )
    except Exception:
        pass

    try:
        op.drop_index(
            "ix_materials_material_master_id",
            table_name="materials",
        )
    except Exception:
        pass

    op.alter_column(
        "materials",
        "material_master_id",
        existing_type=sa.Integer(),
        nullable=True,
    )