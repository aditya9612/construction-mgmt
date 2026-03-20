"""add user extended fields

Revision ID: add_user_extended
Revises: add_rbac_mobile
Create Date: 2026-03-20

Adds address, pan_number, aadhaar_number, profile_image, designation, joining_date, owner_id.
"""

from alembic import op
import sqlalchemy as sa


revision = "add_user_extended"
down_revision = "add_rbac_mobile"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("pan_number", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("aadhaar_number", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("profile_image", sa.String(500), nullable=True))
    op.add_column("users", sa.Column("designation", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("joining_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_users_owner_id", "users", "users", ["owner_id"], ["id"], ondelete="SET NULL")


def downgrade():
    op.drop_constraint("fk_users_owner_id", "users", type_="foreignkey")
    op.drop_column("users", "owner_id")
    op.drop_column("users", "joining_date")
    op.drop_column("users", "designation")
    op.drop_column("users", "profile_image")
    op.drop_column("users", "aadhaar_number")
    op.drop_column("users", "pan_number")
    op.drop_column("users", "address")
