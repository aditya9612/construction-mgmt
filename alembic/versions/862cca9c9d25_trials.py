"""remove approved_by from drawing_documents

Revision ID: 862cca9c9d25
Revises: 3d3d64691d0f
Create Date: 2026-05-20 19:14:35
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "862cca9c9d25"
down_revision = "3d3d64691d0f"
branch_labels = None
depends_on = None


def upgrade():
    # remove approved_by column
    op.drop_column("drawing_documents", "approved_by")

    # make satisfaction_score safe
    op.alter_column(
        "owners",
        "satisfaction_score",
        existing_type=sa.DECIMAL(precision=5, scale=2),
        nullable=False,
        server_default="0.00",
    )

    # fix invoice status enum to lowercase values
    op.execute("""
        ALTER TABLE invoices
        MODIFY status ENUM('pending','partial','paid')
        DEFAULT 'pending'
    """)


def downgrade():
    # restore approved_by
    op.add_column(
        "drawing_documents",
        sa.Column("approved_by", sa.String(length=100), nullable=True),
    )

    # revert satisfaction_score changes
    op.alter_column(
        "owners",
        "satisfaction_score",
        existing_type=sa.DECIMAL(precision=5, scale=2),
        nullable=True,
        server_default=None,
    )

    # revert invoice status enum back to uppercase
    op.execute("""
        ALTER TABLE invoices
        MODIFY status ENUM('PENDING','PARTIAL','PAID')
    """)