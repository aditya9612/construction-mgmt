"""equipment updates

Revision ID: 7e1cf77df365
Revises: ef2999b755b6
Create Date: 2026-06-17 16:34:05.365966
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7e1cf77df365"
down_revision = "ef2999b755b6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "equipment_purchase",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purchase_type", sa.String(length=20), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("vendor_name", sa.String(length=255), nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.DECIMAL(12, 2), nullable=False),
        sa.Column("total_amount", sa.DECIMAL(14, 2), nullable=False),
        sa.Column("warranty_end_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["equipment.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_number"),
    )

    op.create_index(
        op.f("ix_equipment_purchase_asset_id"),
        "equipment_purchase",
        ["asset_id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_equipment_purchase_purchase_type"),
        "equipment_purchase",
        ["purchase_type"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_equipment_purchase_purchase_type"),
        table_name="equipment_purchase",
    )

    op.drop_index(
        op.f("ix_equipment_purchase_asset_id"),
        table_name="equipment_purchase",
    )

    op.drop_table("equipment_purchase")