"""Update work activity model

Revision ID: 78a8c7c5cff5
Revises: f4353e1a6acd
Create Date: 2026-07-21 20:09:14.068118
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "78a8c7c5cff5"
down_revision = "f4353e1a6acd"
branch_labels = None
depends_on = None


def upgrade():

    # =====================================================
    # ADD NEW COLUMN
    # =====================================================

    op.add_column(
        "work_activities",
        sa.Column(
            "boq_item_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    # =====================================================
    # ALTER COLUMNS
    # =====================================================

    op.alter_column(
        "work_activities",
        "work_order_id",
        existing_type=mysql.INTEGER(),
        nullable=True,
    )

    op.alter_column(
        "work_activities",
        "planned_quantity",
        existing_type=mysql.DECIMAL(18, 2),
        nullable=False,
    )

    op.alter_column(
        "work_activities",
        "unit",
        existing_type=mysql.VARCHAR(50),
        nullable=False,
    )

    op.alter_column(
        "work_activities",
        "total_completed",
        existing_type=mysql.DECIMAL(18, 2),
        nullable=False,
    )

    op.alter_column(
        "work_activities",
        "remaining_quantity",
        existing_type=mysql.DECIMAL(18, 2),
        nullable=False,
    )

    op.alter_column(
        "work_activities",
        "completion_percentage",
        existing_type=mysql.DECIMAL(5, 2),
        nullable=False,
    )

    op.alter_column(
        "work_activities",
        "start_date",
        existing_type=sa.DATE(),
        nullable=False,
    )

    op.alter_column(
        "work_activities",
        "end_date",
        existing_type=sa.DATE(),
        nullable=False,
    )

    # =====================================================
    # INDEXES
    # =====================================================

    op.create_index(
        "idx_activity_boq_item",
        "work_activities",
        ["boq_item_id"],
        unique=False,
    )

    op.create_index(
        "idx_activity_project_engineer",
        "work_activities",
        ["project_id", "engineer_id"],
        unique=False,
    )

    op.create_index(
        "idx_activity_project_status",
        "work_activities",
        ["project_id", "status"],
        unique=False,
    )

    op.create_index(
        "idx_activity_work_order",
        "work_activities",
        ["work_order_id"],
        unique=False,
    )

    # =====================================================
    # UNIQUE
    # =====================================================

    op.create_unique_constraint(
        "uq_activity_project_boq",
        "work_activities",
        ["project_id", "boq_item_id"],
    )

    # =====================================================
    # FOREIGN KEYS
    # =====================================================

    op.drop_constraint(
        op.f("fk_work_activities_work_order_id"),
        "work_activities",
        type_="foreignkey",
    )

    op.create_foreign_key(
        None,
        "work_activities",
        "work_orders",
        ["work_order_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        None,
        "work_activities",
        "boq_items",
        ["boq_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # =====================================================
    # REMOVE OLD COLUMN
    # =====================================================

    op.drop_column(
        "work_activities",
        "boq_code",
    )


def downgrade():

    op.add_column(
        "work_activities",
        sa.Column(
            "boq_code",
            mysql.INTEGER(),
            nullable=True,
        ),
    )

    op.drop_constraint(
        None,
        "work_activities",
        type_="foreignkey",
    )

    op.drop_constraint(
        None,
        "work_activities",
        type_="foreignkey",
    )

    op.create_foreign_key(
        op.f("fk_work_activities_work_order_id"),
        "work_activities",
        "work_orders",
        ["work_order_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "uq_activity_project_boq",
        "work_activities",
        type_="unique",
    )

    op.drop_index(
        "idx_activity_work_order",
        table_name="work_activities",
    )

    op.drop_index(
        "idx_activity_project_status",
        table_name="work_activities",
    )

    op.drop_index(
        "idx_activity_project_engineer",
        table_name="work_activities",
    )

    op.drop_index(
        "idx_activity_boq_item",
        table_name="work_activities",
    )

    op.alter_column(
        "work_activities",
        "end_date",
        existing_type=sa.DATE(),
        nullable=True,
    )

    op.alter_column(
        "work_activities",
        "start_date",
        existing_type=sa.DATE(),
        nullable=True,
    )

    op.alter_column(
        "work_activities",
        "completion_percentage",
        existing_type=mysql.DECIMAL(5, 2),
        nullable=True,
    )

    op.alter_column(
        "work_activities",
        "remaining_quantity",
        existing_type=mysql.DECIMAL(18, 2),
        nullable=True,
    )

    op.alter_column(
        "work_activities",
        "total_completed",
        existing_type=mysql.DECIMAL(18, 2),
        nullable=True,
    )

    op.alter_column(
        "work_activities",
        "unit",
        existing_type=mysql.VARCHAR(50),
        nullable=True,
    )

    op.alter_column(
        "work_activities",
        "planned_quantity",
        existing_type=mysql.DECIMAL(18, 2),
        nullable=True,
    )

    op.alter_column(
        "work_activities",
        "work_order_id",
        existing_type=mysql.INTEGER(),
        nullable=False,
    )

    op.drop_column(
        "work_activities",
        "boq_item_id",
    )
