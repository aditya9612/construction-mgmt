
"""financial_precision_hardening

Revision ID: 869afae522be
Revises: d6877acf4183
Create Date: 2026-09-11 23:04:36.606058
"""
from alembic import op
import sqlalchemy as sa

revision = "869afae522be"
down_revision = "d6877acf4183"
branch_labels = None
depends_on = None

def upgrade():
    tables = [
        ("dummy_quotations", ["subtotal", "cgst_amount", "sgst_amount", "discount_amount", "tds_amount", "grand_total", "advance_paid", "balance_due"]),
        ("dummy_quotation_items", ["rate", "amount"]),
        ("quotation_master", ["subtotal", "gst_amount", "cgst_amount", "sgst_amount", "tds_amount", "discount_amount", "grand_total", "advance_paid", "balance_due"]),
        ("quotation_items", ["rate", "amount"]),
        ("quotation_labour", ["daily_wage", "overtime_rate", "amount"]),
        ("quotation_materials", ["estimated_rate", "estimated_amount"]),
        ("quotation_extra_charges", ["rate", "amount"]),
        ("plans", ["price"]),
        ("user_attendance", ["overtime_rate"])
    ]
    for table, cols in tables:
        for col in cols:
            op.alter_column(table, col, existing_type=sa.Float(), type_=sa.DECIMAL(precision=18, scale=2), existing_nullable=True)

def downgrade():
    tables = [
        ("dummy_quotations", ["subtotal", "cgst_amount", "sgst_amount", "discount_amount", "tds_amount", "grand_total", "advance_paid", "balance_due"]),
        ("dummy_quotation_items", ["rate", "amount"]),
        ("quotation_master", ["subtotal", "gst_amount", "cgst_amount", "sgst_amount", "tds_amount", "discount_amount", "grand_total", "advance_paid", "balance_due"]),
        ("quotation_items", ["rate", "amount"]),
        ("quotation_labour", ["daily_wage", "overtime_rate", "amount"]),
        ("quotation_materials", ["estimated_rate", "estimated_amount"]),
        ("quotation_extra_charges", ["rate", "amount"]),
        ("plans", ["price"]),
        ("user_attendance", ["overtime_rate"])
    ]
    for table, cols in tables:
        for col in cols:
            op.alter_column(table, col, existing_type=sa.DECIMAL(precision=18, scale=2), type_=sa.Float(), existing_nullable=True)

