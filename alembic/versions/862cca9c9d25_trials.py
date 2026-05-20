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
    op.drop_column("drawing_documents", "approved_by")


def downgrade():
    op.add_column(
        "drawing_documents",
        sa.Column("approved_by", sa.String(length=100), nullable=True),
    )