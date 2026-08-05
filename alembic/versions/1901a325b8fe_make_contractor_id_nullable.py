"""consolidated migration for contractor, work update and work activities

Revision ID: 1901a325b8fe
Revises: 8b7a7c6f5e4d
Create Date: 2026-07-26 18:09:13.561944
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "1901a325b8fe"
down_revision = "8b7a7c6f5e4d"
branch_labels = None
depends_on = None


def upgrade():
    # ==========================
    # 1. contractor_id nullable changes
    # ==========================
    # RA BILL
    op.drop_constraint(
        "ra_bills_ibfk_1",
        "ra_bills",
        type_="foreignkey",
    )

    op.alter_column(
        "ra_bills",
        "contractor_id",
        existing_type=mysql.INTEGER(),
        nullable=True,
    )

    op.create_foreign_key(
        "ra_bills_ibfk_1",
        "ra_bills",
        "contractors",
        ["contractor_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # WORK ORDER
    op.drop_constraint(
        "work_orders_ibfk_1",
        "work_orders",
        type_="foreignkey",
    )

    op.alter_column(
        "work_orders",
        "contractor_id",
        existing_type=mysql.INTEGER(),
        nullable=True,
    )

    op.create_foreign_key(
        "work_orders_ibfk_1",
        "work_orders",
        "contractors",
        ["contractor_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ==========================
    # 2. work_updates module
    # ==========================
    op.create_table(
        "work_updates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_id", sa.String(length=50), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("activity_type_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("work_description", sa.Text(), nullable=False),
        sa.Column("before_remarks", sa.Text(), nullable=True),
        sa.Column("after_remarks", sa.Text(), nullable=True),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("total_hours", sa.DECIMAL(precision=5, scale=2), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum("Draft", "Submitted", name="workupdatestatus"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["activity_type_id"], ["activity_types.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_work_updates_project_date",
        "work_updates",
        ["project_id", "work_date"],
        unique=False,
    )

    op.create_index(
        "idx_work_updates_status",
        "work_updates",
        ["status"],
        unique=False,
    )

    op.create_index(
        op.f("ix_work_updates_activity_type_id"),
        "work_updates",
        ["activity_type_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_work_updates_business_id"),
        "work_updates",
        ["business_id"],
        unique=True,
    )

    op.create_index(
        op.f("ix_work_updates_created_by_id"),
        "work_updates",
        ["created_by_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_work_updates_project_id"),
        "work_updates",
        ["project_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_work_updates_task_id"),
        "work_updates",
        ["task_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_work_updates_work_date"),
        "work_updates",
        ["work_date"],
        unique=False,
    )

    op.create_table(
        "work_update_images",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("work_update_id", sa.Integer(), nullable=False),
        sa.Column(
            "image_type",
            sa.Enum("Before", "After", name="workupdateimagetype"),
            nullable=False,
        ),
        sa.Column("image_url", sa.String(length=500), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["work_update_id"],
            ["work_updates.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_work_update_images_image_type"),
        "work_update_images",
        ["image_type"],
        unique=False,
    )

    op.create_index(
        op.f("ix_work_update_images_work_update_id"),
        "work_update_images",
        ["work_update_id"],
        unique=False,
    )

    # =====================================================
    # 3. work_activity schema changes
    # =====================================================
    op.add_column(
        "work_activities",
        sa.Column(
            "boq_item_id",
            sa.Integer(),
            nullable=True,
        ),
    )

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

    op.create_unique_constraint(
        "uq_activity_project_boq",
        "work_activities",
        ["project_id", "boq_item_id"],
    )

    op.drop_constraint(
        op.f("fk_work_activities_work_order_id"),
        "work_activities",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "fk_work_activities_work_order_id",
        "work_activities",
        "work_orders",
        ["work_order_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        "fk_work_activities_boq_item_id",
        "work_activities",
        "boq_items",
        ["boq_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_column(
        "work_activities",
        "boq_code",
    )

    # =====================================================
    # 4. work_activity sync changes
    # =====================================================
    op.alter_column(
        "work_activities",
        "created_at",
        existing_type=mysql.TIMESTAMP(),
        type_=sa.DateTime(timezone=True),
        nullable=False,
        existing_server_default=sa.text("(now())"),
    )

    op.create_index(
        op.f("ix_work_activities_status"),
        "work_activities",
        ["status"],
        unique=False,
    )

    op.execute(
        "ALTER TABLE tasks MODIFY COLUMN status ENUM('PLANNED','IN_PROGRESS','COMPLETED','CANCELLED') NOT NULL"
    )


def downgrade():
    # =====================================================
    # 4. work_activity sync changes (Reverse)
    # =====================================================

    op.execute(
        """
        ALTER TABLE tasks
        MODIFY COLUMN status
        ENUM(
            'PLANNED',
            'IN_PROGRESS',
            'COMPLETED'
        ) NOT NULL
        """
    )

    op.drop_index(op.f("ix_work_activities_status"), table_name="work_activities")
    op.alter_column(
        "work_activities",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        type_=mysql.TIMESTAMP(),
        nullable=True,
        existing_server_default=sa.text("(now())"),
    )

    # =====================================================
    # 3. work_activity schema changes (Reverse)
    # =====================================================
    # 1. Restore column: boq_code
    op.add_column(
        "work_activities",
        sa.Column(
            "boq_code",
            mysql.INTEGER(),
            nullable=True,
        ),
    )

    # 2. Drop foreign key: fk_work_activities_boq_item_id
    op.drop_constraint(
        "fk_work_activities_boq_item_id",
        "work_activities",
        type_="foreignkey",
    )

    # 3. Drop foreign key: fk_work_activities_work_order_id
    op.drop_constraint(
        "fk_work_activities_work_order_id",
        "work_activities",
        type_="foreignkey",
    )

    # 4. Drop unique constraint: uq_activity_project_boq
    op.drop_constraint(
        "uq_activity_project_boq",
        "work_activities",
        type_="unique",
    )

    # 5. Drop indexes
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

    # 6. Revert altered columns
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

    # 7. Drop column: boq_item_id
    op.drop_column(
        "work_activities",
        "boq_item_id",
    )

    # 8. Recreate the original work_order foreign key with CASCADE
    op.create_foreign_key(
        op.f("fk_work_activities_work_order_id"),
        "work_activities",
        "work_orders",
        ["work_order_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # =====================================================
    # 2. work_updates module (Reverse)
    # =====================================================
    op.drop_index(op.f("ix_work_update_images_work_update_id"), table_name="work_update_images")
    op.drop_index(op.f("ix_work_update_images_image_type"), table_name="work_update_images")
    op.drop_table("work_update_images")

    op.drop_index(op.f("ix_work_updates_work_date"), table_name="work_updates")
    op.drop_index(op.f("ix_work_updates_task_id"), table_name="work_updates")
    op.drop_index(op.f("ix_work_updates_project_id"), table_name="work_updates")
    op.drop_index(op.f("ix_work_updates_created_by_id"), table_name="work_updates")
    op.drop_index(op.f("ix_work_updates_business_id"), table_name="work_updates")
    op.drop_index(op.f("ix_work_updates_activity_type_id"), table_name="work_updates")
    op.drop_index("idx_work_updates_status", table_name="work_updates")
    op.drop_index("idx_work_updates_project_date", table_name="work_updates")
    op.drop_table("work_updates")

    # ==========================
    # 1. contractor_id nullable changes (Reverse)
    # ==========================
    # WORK ORDER
    op.drop_constraint(
        "work_orders_ibfk_1",
        "work_orders",
        type_="foreignkey",
    )

    op.alter_column(
        "work_orders",
        "contractor_id",
        existing_type=mysql.INTEGER(),
        nullable=False,
    )

    op.create_foreign_key(
        "work_orders_ibfk_1",
        "work_orders",
        "contractors",
        ["contractor_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # RA BILL
    op.drop_constraint(
        "ra_bills_ibfk_1",
        "ra_bills",
        type_="foreignkey",
    )

    op.alter_column(
        "ra_bills",
        "contractor_id",
        existing_type=mysql.INTEGER(),
        nullable=False,
    )

    op.create_foreign_key(
        "ra_bills_ibfk_1",
        "ra_bills",
        "contractors",
        ["contractor_id"],
        ["id"],
        ondelete="CASCADE",
    )