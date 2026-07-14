"""make contractor_id nullable

Revision ID: 1901a325b8fe
Revises: 8b7a7c6f5e4d
Create Date: 2026-07-14 18:34:00.208428
"""

from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "1901a325b8fe"
down_revision = "8b7a7c6f5e4d"
branch_labels = None
depends_on = None


def upgrade():

    # ==========================
    # RA BILL
    # ==========================

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

    # ==========================
    # WORK ORDER
    # ==========================

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


def downgrade():

    # ==========================
    # WORK ORDER
    # ==========================

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

    # ==========================
    # RA BILL
    # ==========================

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