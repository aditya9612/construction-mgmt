"""accountant upgradation

Revision ID: ef2999b755b6
Revises: 3bcfb82f7a21
Create Date: 2026-06-16 14:51:50.762780
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "ef2999b755b6"
down_revision = "3bcfb82f7a21"
branch_labels = None
depends_on = None


def upgrade():
    # GST Returns
    op.create_table(
        "gst_returns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_period", sa.String(length=20), nullable=False),
        sa.Column("return_type", sa.String(length=50), nullable=False),
        sa.Column("taxable_value", sa.DECIMAL(18, 2), nullable=True),
        sa.Column("gst_liability", sa.DECIMAL(18, 2), nullable=True),
        sa.Column("itc_available", sa.DECIMAL(18, 2), nullable=True),
        sa.Column("net_gst_payable", sa.DECIMAL(18, 2), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_gst_returns_filing_period"),
        "gst_returns",
        ["filing_period"],
        unique=False,
    )

    # Bank Transactions
    op.create_table(
        "bank_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.DECIMAL(18, 2), nullable=False),
        sa.Column("type", sa.String(length=10), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("reference_number", sa.String(length=100), nullable=True),
        sa.Column("is_reconciled", sa.Integer(), nullable=True),
        sa.Column("matched_journal_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["bank_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["matched_journal_id"], ["journal_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_bank_transactions_bank_account_id"),
        "bank_transactions",
        ["bank_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bank_transactions_reference_number"),
        "bank_transactions",
        ["reference_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_bank_transactions_transaction_date"),
        "bank_transactions",
        ["transaction_date"],
        unique=False,
    )

    # Fund Transfers
    op.create_table(
        "fund_transfers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_account_id", sa.Integer(), nullable=False),
        sa.Column("to_account_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.DECIMAL(18, 2), nullable=False),
        sa.Column("transfer_date", sa.Date(), nullable=False),
        sa.Column("reference_number", sa.String(length=100), nullable=True),
        sa.Column("remarks", sa.String(length=255), nullable=True),
        sa.Column("journal_entry_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["from_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["to_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["journal_entry_id"], ["journal_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_fund_transfers_transfer_date"),
        "fund_transfers",
        ["transfer_date"],
        unique=False,
    )

    # Vendor Bills
    op.create_table(
        "vendor_bills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("purchase_order_id", sa.Integer(), nullable=True),
        sa.Column("bill_number", sa.String(length=50), nullable=False),
        sa.Column("bill_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.DECIMAL(18, 2), nullable=False),
        sa.Column("amount_paid", sa.DECIMAL(18, 2), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vendor_bills_bill_number"),
        "vendor_bills",
        ["bill_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_vendor_bills_supplier_id"),
        "vendor_bills",
        ["supplier_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vendor_bills_project_id"), "vendor_bills", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_vendor_bills_purchase_order_id"),
        "vendor_bills",
        ["purchase_order_id"],
        unique=False,
    )


def downgrade():
    # Vendor Bills
    op.drop_index(op.f("ix_vendor_bills_purchase_order_id"), table_name="vendor_bills")
    op.drop_index(op.f("ix_vendor_bills_project_id"), table_name="vendor_bills")
    op.drop_index(op.f("ix_vendor_bills_supplier_id"), table_name="vendor_bills")
    op.drop_index(op.f("ix_vendor_bills_bill_number"), table_name="vendor_bills")
    op.drop_table("vendor_bills")

    # Fund Transfers
    op.drop_index(op.f("ix_fund_transfers_transfer_date"), table_name="fund_transfers")
    op.drop_table("fund_transfers")

    # Bank Transactions
    op.drop_index(
        op.f("ix_bank_transactions_transaction_date"), table_name="bank_transactions"
    )
    op.drop_index(
        op.f("ix_bank_transactions_reference_number"), table_name="bank_transactions"
    )
    op.drop_index(
        op.f("ix_bank_transactions_bank_account_id"), table_name="bank_transactions"
    )
    op.drop_table("bank_transactions")

    # GST Returns
    op.drop_index(op.f("ix_gst_returns_filing_period"), table_name="gst_returns")
    op.drop_table("gst_returns")
