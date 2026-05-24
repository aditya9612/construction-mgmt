"""remove approved_by from drawing_documents

Revision ID: 862cca9c9d25
Revises: 3d3d64691d0f
Create Date: 2026-05-20 19:14:35
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "862cca9c9d25"
down_revision = "3d3d64691d0f"
branch_labels = None
depends_on = None


def upgrade():
    # remove approved_by column

    op.drop_column("owners", "satisfaction_score")

    op.alter_column(
        "boq_items",
        "quantity",
        existing_type=sa.DECIMAL(18, 3),
        server_default="1",
        existing_nullable=False,
    )

    op.alter_column(
        "boq_items",
        "unit_cost",
        existing_type=sa.DECIMAL(18, 2),
        server_default="1",
        existing_nullable=False,
    )
    op.add_column(
        "boq_items",
        sa.Column(
            "approval_status",
            sa.String(length=50),
            server_default="Draft",
            nullable=False,
        ),
    )
    op.execute("""
        UPDATE boq_items
        SET approval_status = 'Draft'
        WHERE approval_status IS NULL
    """)
    op.create_index(
        op.f("ix_boq_items_approval_status"),
        "boq_items",
        ["approval_status"],
        unique=False,
    )
    op.add_column("tasks", sa.Column("boq_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_tasks_boq_id"), "tasks", ["boq_id"], unique=False)
    op.create_foreign_key(
        "fk_tasks_boq_id",
        "tasks",
        "boq_items",
        ["boq_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("drawing_documents", "approved_by")
    op.create_unique_constraint('uq_project_drawing_version', 'drawing_documents', ['project_id', 'drawing_name', 'version'])

    # fix invoice status enum to lowercase values
    op.execute("""
        ALTER TABLE invoices
        MODIFY status ENUM('pending','partial','paid')
        DEFAULT 'pending'
    """)

    op.create_unique_constraint(
        "uq_boq_version_item",
        "boq_items",
        ["boq_group_id", "version_no", "item_name", "category"],
    )
    op.alter_column(
        "checklist_logs",
        "status",
        existing_type=mysql.VARCHAR(length=20),
        type_=sa.Enum("DONE", "PENDING", name="checkliststatus"),
        nullable=False,
    )
    op.alter_column(
        "drawing_documents", "project_id", existing_type=mysql.INTEGER(), nullable=False
    )
    op.alter_column(
        "drawing_documents",
        "drawing_name",
        existing_type=mysql.VARCHAR(length=255),
        nullable=False,
    )
    op.alter_column(
        "drawing_documents",
        "version",
        existing_type=mysql.VARCHAR(length=50),
        nullable=False,
    )
    op.alter_column(
        "drawing_documents",
        "file_url",
        existing_type=mysql.VARCHAR(length=500),
        nullable=False,
    )
    op.create_index(
        "idx_drawing_project", "drawing_documents", ["project_id"], unique=False
    )

    # ===================== ADD COLUMNS =====================

    op.add_column(
        "drawing_documents",
        sa.Column(
            "approval_status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "REJECTED",
                "UNDER_REVIEW",
                name="documentstatus",
            ),
            nullable=False,
            server_default="PENDING",
        ),
    )

    op.add_column(
        "drawing_documents",
        sa.Column(
            "approval_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "drawing_documents",
        sa.Column(
            "is_latest_version",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )

    op.add_column(
        "drawing_documents",
        sa.Column(
            "revision_no",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    # ===================== SAFETY UPDATE FOR EXISTING DATA =====================

    op.execute(
        """
        UPDATE drawing_documents
        SET
            approval_status = 'PENDING',
            is_latest_version = 1,
            revision_no = 1
        WHERE
            approval_status IS NULL
            OR is_latest_version IS NULL
            OR revision_no IS NULL
        """
    )

    # ===================== INDEXES =====================

    op.create_index(
        "idx_drawing_approval",
        "drawing_documents",
        ["approval_status", "is_latest_version"],
        unique=False,
    )

    op.create_index(
        "idx_drawing_latest",
        "drawing_documents",
        ["project_id", "drawing_name", "is_latest_version"],
        unique=False,
    )

    op.create_index(
        "idx_drawing_project_status",
        "drawing_documents",
        ["project_id", "approval_status"],
        unique=False,
    )

    op.create_index(
        "idx_drawing_revision",
        "drawing_documents",
        ["project_id", "drawing_name", "revision_no"],
        unique=False,
    )

    op.create_index(
        "ix_drawing_documents_approval_id",
        "drawing_documents",
        ["approval_id"],
        unique=False,
    )

    # ===================== CONSTRAINTS =====================

    op.create_unique_constraint(
        "uq_project_drawing_revision",
        "drawing_documents",
        ["project_id", "drawing_name", "revision_no"],
    )

    op.create_foreign_key(
        "fk_drawing_documents_approval_id",
        "drawing_documents",
        "approvals",
        ["approval_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    # restore approved_by

    op.drop_constraint(
        "fk_drawing_documents_approval_id",
        "drawing_documents",
        type_="foreignkey",
    )

    op.drop_constraint(
        "uq_project_drawing_revision",
        "drawing_documents",
        type_="unique",
    )

    # ===================== DROP INDEXES =====================

    op.drop_index(
        "ix_drawing_documents_approval_id",
        table_name="drawing_documents",
    )

    op.drop_index(
        "idx_drawing_revision",
        table_name="drawing_documents",
    )

    op.drop_index(
        "idx_drawing_project_status",
        table_name="drawing_documents",
    )

    op.drop_index(
        "idx_drawing_latest",
        table_name="drawing_documents",
    )

    op.drop_index(
        "idx_drawing_approval",
        table_name="drawing_documents",
    )

    # ===================== DROP COLUMNS =====================

    op.drop_column(
        "drawing_documents",
        "revision_no",
    )

    op.drop_column(
        "drawing_documents",
        "is_latest_version",
    )

    op.drop_column(
        "drawing_documents",
        "approval_id",
    )

    op.drop_column(
        "drawing_documents",
        "approval_status",
    )

    # ===================== DROP ENUM =====================

    sa.Enum(name="documentstatus").drop(
        op.get_bind(),
        checkfirst=True,
    )

    op.alter_column(
        "boq_items",
        "quantity",
        existing_type=sa.DECIMAL(18, 3),
        server_default="0",
        existing_nullable=False,
    )

    op.alter_column(
        "boq_items",
        "unit_cost",
        existing_type=sa.DECIMAL(18, 2),
        server_default="0",
        existing_nullable=False,
    )
    op.drop_constraint(
        "fk_tasks_boq_id",
        "tasks",
        type_="foreignkey"
    )
    op.drop_index(op.f("ix_tasks_boq_id"), table_name="tasks")
    op.drop_column("tasks", "boq_id")
    op.drop_index(op.f("ix_boq_items_approval_status"), table_name="boq_items")
    op.drop_column("boq_items", "approval_status")
    op.drop_constraint('uq_project_drawing_version', 'drawing_documents', type_='unique')

    op.add_column(
        "drawing_documents",
        sa.Column("approved_by", sa.String(length=100), nullable=True),
    )

    # revert satisfaction_score changes
    op.add_column(
        "owners",
        sa.Column(
            "satisfaction_score",
            sa.DECIMAL(5, 2),
            nullable=True
        )
    )

    # revert invoice status enum back to uppercase
    op.execute("""
        UPDATE invoices
        SET status = UPPER(status)
    """)

    op.execute("""
        ALTER TABLE invoices
        MODIFY status ENUM(
            'PENDING',
            'PARTIAL',
            'PAID'
        )
    """)

    op.drop_index("idx_drawing_project", table_name="drawing_documents")
    op.alter_column(
        "drawing_documents",
        "file_url",
        existing_type=mysql.VARCHAR(length=500),
        nullable=True,
    )
    op.alter_column(
        "drawing_documents",
        "version",
        existing_type=mysql.VARCHAR(length=50),
        nullable=True,
    )
    op.alter_column(
        "drawing_documents",
        "drawing_name",
        existing_type=mysql.VARCHAR(length=255),
        nullable=True,
    )
    op.alter_column(
        "drawing_documents", "project_id", existing_type=mysql.INTEGER(), nullable=True
    )
    op.alter_column(
        "checklist_logs",
        "status",
        existing_type=sa.Enum("DONE", "PENDING", name="checkliststatus"),
        type_=mysql.VARCHAR(length=20),
        nullable=True,
    )
    op.drop_constraint("uq_boq_version_item", "boq_items", type_="unique")
