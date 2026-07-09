"""consolidated_migration

Revision ID: 8b7a7c6f5e4d
Revises: e13e5f557063
Create Date: 2026-07-06 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "8b7a7c6f5e4d"
down_revision = "e13e5f557063"
branch_labels = None
depends_on = None


def upgrade():

    op.execute("""
    UPDATE activity_logs
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column("activity_logs", "created_at", existing_type=sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.execute("""
    UPDATE chat_sessions
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column("chat_sessions", "created_at", existing_type=sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.execute("""
    UPDATE chat_members
    SET joined_at=CURRENT_TIMESTAMP
    WHERE joined_at IS NULL
    """)

    op.alter_column("chat_members", "joined_at", existing_type=sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.execute("""
    UPDATE chat_messages
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column("chat_messages", "created_at", existing_type=sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.execute("""
    UPDATE message_attachments
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column("message_attachments", "created_at", existing_type=sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.execute("""
    UPDATE message_mentions
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column("message_mentions", "created_at", existing_type=sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.execute("""
    UPDATE message_reads
    SET read_at=CURRENT_TIMESTAMP
    WHERE read_at IS NULL
    """)

    op.alter_column("message_reads", "read_at", existing_type=sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))

    # fix existing NULL data before enforcing NOT NULL
    op.execute("""
    UPDATE bank_transactions
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column(
        "bank_transactions", "created_at", existing_type=sa.DateTime(), existing_nullable=True, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    # fix existing NULL data before enforcing NOT NULL
    op.execute("""
    UPDATE fund_transfers
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column(
        "fund_transfers", "created_at", existing_type=sa.DateTime(), existing_nullable=True, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    # fix existing NULL data before enforcing NOT NULL
    op.execute("""
    UPDATE gst_returns
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column(
        "gst_returns", "created_at", existing_type=sa.DateTime(), existing_nullable=True, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    # fix existing NULL data before enforcing NOT NULL
    op.execute("""
    UPDATE vendor_bills
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column(
        "vendor_bills", "created_at", existing_type=sa.DateTime(), existing_nullable=True, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    # Fixed Assets timestamp standardization
    op.execute("""
    UPDATE fixed_assets
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column(
        "fixed_assets", "created_at", existing_type=sa.DateTime(), existing_nullable=True, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    op.execute("""
    UPDATE fixed_assets
    SET updated_at=CURRENT_TIMESTAMP
    WHERE updated_at IS NULL
    """)

    op.alter_column(
        "fixed_assets", "updated_at", existing_type=sa.DateTime(), existing_nullable=True, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    # Account timestamp standardization
    op.add_column("accounts", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.add_column("accounts", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))

    op.add_column("redevelopment_offers", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))

    # fix existing NULL data before enforcing NOT NULL
    op.execute("""
        UPDATE redevelopment_offers
        SET created_at = CURRENT_TIMESTAMP
        WHERE created_at IS NULL
        """)

    op.alter_column(
        "redevelopment_offers", "created_at", existing_type=sa.DateTime(), existing_nullable=True, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    op.add_column("bank_transactions", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.add_column("fund_transfers", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.add_column("gst_returns", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.add_column("invoices", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.add_column("journal_entries", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.add_column("owner_transactions", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.add_column("transactions", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))
    op.add_column("vendor_bills", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False))

    op.alter_column("notifications", "created_at", existing_type=sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"))
    op.alter_column("alerts", "created_at", existing_type=sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"))

    op.create_table(
        "bank_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("bank_name", sa.String(length=150), nullable=False),
        sa.Column("account_number", sa.String(length=100), nullable=False),
        sa.Column("ifsc_code", sa.String(length=50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id"),
        sa.UniqueConstraint("account_number"),
    )

    op.add_column("company_settings", sa.Column("primary_cash_account_id", sa.Integer(), nullable=True))

    op.create_foreign_key("fk_company_settings_primary_cash_account_id", "company_settings", "accounts", ["primary_cash_account_id"], ["id"])

    op.add_column("company_settings", sa.Column("petty_cash_account_id", sa.Integer(), nullable=True))

    op.create_foreign_key("fk_company_settings_petty_cash_account_id", "company_settings", "accounts", ["petty_cash_account_id"], ["id"])
    op.add_column("company_settings", sa.Column("wages_account_id", sa.Integer(), nullable=True))
    op.add_column("company_settings", sa.Column("staff_salary_account_id", sa.Integer(), nullable=True))
    op.add_column("company_settings", sa.Column("contractor_expense_account_id", sa.Integer(), nullable=True))
    op.add_column("company_settings", sa.Column("tds_payable_account_id", sa.Integer(), nullable=True))
    op.add_column("company_settings", sa.Column("retention_payable_account_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_company_settings_contractor_expense_account_id", "company_settings", "accounts", ["contractor_expense_account_id"], ["id"])
    op.create_foreign_key("fk_company_settings_wages_account_id", "company_settings", "accounts", ["wages_account_id"], ["id"])
    op.create_foreign_key("fk_company_settings_staff_salary_account_id", "company_settings", "accounts", ["staff_salary_account_id"], ["id"])
    op.create_foreign_key("fk_company_settings_retention_payable_account_id", "company_settings", "accounts", ["retention_payable_account_id"], ["id"])
    op.create_foreign_key("fk_company_settings_tds_payable_account_id", "company_settings", "accounts", ["tds_payable_account_id"], ["id"])

    op.create_table(
        "recurring_journals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_name", sa.String(length=255), nullable=False),
        sa.Column("frequency", sa.String(length=50), nullable=False),
        sa.Column("next_run_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("template_data", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute("""
    UPDATE journal_entries
    SET created_at=CURRENT_TIMESTAMP
    WHERE created_at IS NULL
    """)

    op.alter_column(
        "journal_entries", "created_at", existing_type=sa.DateTime(), existing_nullable=True, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )
    op.add_column("journal_entries", sa.Column("journal_number", sa.String(length=100), nullable=True))
    op.add_column("journal_entries", sa.Column("entry_date", sa.Date(), nullable=True))
    op.add_column("journal_entries", sa.Column("status", sa.String(length=50), server_default="Posted", nullable=True))
    op.add_column("journal_entries", sa.Column("entry_type", sa.String(length=50), server_default="Auto", nullable=True))
    op.add_column("journal_entries", sa.Column("created_by", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_journal_entries_journal_number"), "journal_entries", ["journal_number"], unique=False)
    op.create_foreign_key("fk_journal_entries_created_by_users", "journal_entries", "users", ["created_by"], ["id"])

    op.create_table(
        "tds_deductions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("party_name", sa.String(length=255), nullable=False),
        sa.Column("pan_number", sa.String(length=20), nullable=True),
        sa.Column("invoice_number", sa.String(length=100), nullable=True),
        sa.Column("payment_amount", sa.DECIMAL(precision=18, scale=2), nullable=False),
        sa.Column("tds_section", sa.String(length=50), nullable=False),
        sa.Column("tds_rate", sa.DECIMAL(precision=5, scale=2), nullable=False),
        sa.Column("tds_amount", sa.DECIMAL(precision=18, scale=2), nullable=False),
        sa.Column("deposit_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("vendor_bill_id", sa.Integer(), nullable=True),
        sa.Column("ra_bill_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["ra_bill_id"], ["ra_bills.id"]),
        sa.ForeignKeyConstraint(["vendor_bill_id"], ["vendor_bills.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Quotation -> Client mapping
    op.add_column("quotation_master", sa.Column("client_user_id", sa.Integer(), nullable=True))

    op.create_index(op.f("ix_quotation_master_client_user_id"), "quotation_master", ["client_user_id"], unique=False)

    op.create_foreign_key("fk_quotation_master_client_user_id", "quotation_master", "users", ["client_user_id"], ["id"])

    op.create_table(
        "client_payments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("payment_no", sa.String(30), nullable=False),
        sa.Column("client_user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "payment_method",
            sa.Enum(
                "CASH",
                "CHEQUE",
                "NEFT",
                "RTGS",
                "UPI",
                "ONLINE",
                name="paymentmethod",
            ),
            nullable=False,
        ),
        sa.Column(
            "payment_status",
            sa.Enum(
                "VERIFICATION_PENDING",
                "PENDING",
                "SUCCESS",
                "REJECTED",
                "FAILED",
                name="paymentstatus",
            ),
            nullable=False,
            server_default="VERIFICATION_PENDING",
        ),
        sa.Column("bank_name", sa.String(100), nullable=True),
        sa.Column("cheque_no", sa.String(50), nullable=True),
        sa.Column("reference_no", sa.String(100), nullable=True),
        sa.Column("transaction_id", sa.String(150), unique=True, nullable=True),
        sa.Column("receipt_url", sa.String(500), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("payment_date", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_client_payment_amount_positive"),
        sa.ForeignKeyConstraint(["client_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_client_payments_payment_no", "client_payments", ["payment_no"], unique=True)

    op.create_index("idx_client_payment_reference_no", "client_payments", ["reference_no"])

    op.create_index("idx_client_payment_cheque", "client_payments", ["bank_name", "cheque_no"])

    op.create_index("idx_client_payment_client", "client_payments", ["client_user_id"])

    op.create_index("idx_client_payment_project", "client_payments", ["project_id"])

    op.create_index("ix_client_payments_invoice_id", "client_payments", ["invoice_id"])

    op.create_index("idx_client_payment_status", "client_payments", ["payment_status"])

    op.create_index("idx_client_payment_method", "client_payments", ["payment_method"])

    op.create_index("idx_client_payment_date", "client_payments", ["payment_date"])

    op.create_index("idx_client_payment_client_status", "client_payments", ["client_user_id", "payment_status"])

    op.drop_constraint("equipment_audit_log_ibfk_1", "equipment_audit_log", type_="foreignkey")

    op.alter_column("equipment_audit_log", "equipment_id", existing_type=sa.Integer(), nullable=True)

    op.create_foreign_key("fk_equipment_audit_log_equipment_id", "equipment_audit_log", "equipment", ["equipment_id"], ["id"], ondelete="SET NULL")

    op.execute("""
    UPDATE equipment_maintenance
    SET is_completed=0
    WHERE is_completed IS NULL
    """)

    op.alter_column("equipment_maintenance", "is_completed", existing_type=sa.Boolean(), nullable=False)

    op.alter_column(
        "equipment_purchase",
        "purchase_type",
        existing_type=sa.String(length=20),
        type_=sa.Enum("NEW", "USED", "RENT", "SPARE_PART", name="purchasetype"),
        existing_nullable=False,
    )

    op.drop_constraint("equipment_purchase_ibfk_1", "equipment_purchase", type_="foreignkey")

    op.alter_column("equipment_purchase", "asset_id", existing_type=sa.Integer(), nullable=True)

    op.create_foreign_key("fk_equipment_purchase_asset_id", "equipment_purchase", "equipment", ["asset_id"], ["id"], ondelete="SET NULL")

    op.add_column("checklist_logs", sa.Column("executed_by", sa.Integer(), nullable=True))

    op.create_foreign_key("fk_checklist_logs_executed_by", "checklist_logs", "users", ["executed_by"], ["id"])


def downgrade():

    op.drop_constraint("fk_checklist_logs_executed_by", "checklist_logs", type_="foreignkey")

    op.drop_column("checklist_logs", "executed_by")

    op.alter_column("message_reads", "read_at", existing_type=sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.alter_column("message_mentions", "created_at", existing_type=sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.alter_column("message_attachments", "created_at", existing_type=sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.alter_column("chat_messages", "created_at", existing_type=sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.alter_column("chat_members", "joined_at", existing_type=sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.alter_column("chat_sessions", "created_at", existing_type=sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP"))

    op.alter_column("activity_logs", "created_at", existing_type=sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP"))

    # drop new FK
    op.drop_constraint("fk_equipment_purchase_asset_id", "equipment_purchase", type_="foreignkey")

    # revert nullable
    op.alter_column("equipment_purchase", "asset_id", existing_type=sa.Integer(), nullable=False)

    # restore old FK
    op.create_foreign_key("equipment_purchase_ibfk_1", "equipment_purchase", "equipment", ["asset_id"], ["id"], ondelete="RESTRICT")

    op.alter_column(
        "equipment_purchase",
        "purchase_type",
        existing_type=sa.Enum("NEW", "USED", "RENT", "SPARE_PART", name="purchasetype"),
        type_=sa.String(length=20),
        existing_nullable=False,
    )

    op.alter_column("equipment_maintenance", "is_completed", existing_type=sa.Boolean(), nullable=True)

    op.drop_constraint("fk_equipment_audit_log_equipment_id", "equipment_audit_log", type_="foreignkey")

    op.alter_column("equipment_audit_log", "equipment_id", existing_type=sa.Integer(), nullable=False)

    op.create_foreign_key("equipment_audit_log_ibfk_1", "equipment_audit_log", "equipment", ["equipment_id"], ["id"], ondelete="CASCADE")

    op.alter_column("journal_entries", "created_at", existing_type=sa.DateTime(), server_default=None)

    op.alter_column("bank_transactions", "created_at", existing_type=sa.DateTime(), server_default=None)

    op.alter_column("fund_transfers", "created_at", existing_type=sa.DateTime(), server_default=None)

    op.alter_column("gst_returns", "created_at", existing_type=sa.DateTime(), server_default=None)

    op.alter_column("vendor_bills", "created_at", existing_type=sa.DateTime(), server_default=None)

    op.alter_column("fixed_assets", "created_at", existing_type=sa.DateTime(), server_default=None)

    op.alter_column("fixed_assets", "updated_at", existing_type=sa.DateTime(), server_default=None)

    op.alter_column("redevelopment_offers", "created_at", existing_type=sa.DateTime(), nullable=True, server_default=None)
    op.drop_column("redevelopment_offers", "updated_at")
    op.drop_column("accounts", "updated_at")
    op.drop_column("accounts", "created_at")
    op.drop_table("tds_deductions")
    op.drop_constraint("fk_journal_entries_created_by_users", "journal_entries", type_="foreignkey")
    op.drop_index(op.f("ix_journal_entries_journal_number"), table_name="journal_entries")
    op.drop_column("journal_entries", "created_by")
    op.drop_column("journal_entries", "entry_type")
    op.drop_column("journal_entries", "status")
    op.drop_column("journal_entries", "entry_date")
    op.drop_column("journal_entries", "journal_number")
    op.drop_table("recurring_journals")
    op.drop_constraint("fk_company_settings_tds_payable_account_id", "company_settings", type_="foreignkey")
    op.drop_constraint("fk_company_settings_retention_payable_account_id", "company_settings", type_="foreignkey")
    op.drop_constraint("fk_company_settings_staff_salary_account_id", "company_settings", type_="foreignkey")
    op.drop_constraint("fk_company_settings_wages_account_id", "company_settings", type_="foreignkey")
    op.drop_constraint("fk_company_settings_contractor_expense_account_id", "company_settings", type_="foreignkey")
    op.drop_column("company_settings", "retention_payable_account_id")
    op.drop_column("company_settings", "tds_payable_account_id")
    op.drop_column("company_settings", "contractor_expense_account_id")
    op.drop_column("company_settings", "staff_salary_account_id")
    op.drop_column("company_settings", "wages_account_id")
    op.drop_constraint("fk_company_settings_petty_cash_account_id", "company_settings", type_="foreignkey")
    op.drop_column("company_settings", "petty_cash_account_id")
    op.drop_constraint("fk_company_settings_primary_cash_account_id", "company_settings", type_="foreignkey")
    op.drop_column("company_settings", "primary_cash_account_id")
    op.drop_table("bank_accounts")
    op.alter_column("alerts", "created_at", existing_type=sa.DateTime(), server_default=None)
    op.alter_column("notifications", "created_at", existing_type=sa.DateTime(), server_default=None)
    op.drop_column("vendor_bills", "updated_at")
    op.drop_column("transactions", "updated_at")
    op.drop_column("owner_transactions", "updated_at")
    op.drop_column("journal_entries", "updated_at")
    op.drop_column("invoices", "updated_at")
    op.drop_column("gst_returns", "updated_at")
    op.drop_column("fund_transfers", "updated_at")
    op.drop_column("bank_transactions", "updated_at")
    op.drop_index("idx_client_payment_client_status", table_name="client_payments")
    op.drop_index("idx_client_payment_date", table_name="client_payments")

    op.drop_index("idx_client_payment_method", table_name="client_payments")

    op.drop_index("idx_client_payment_status", table_name="client_payments")

    op.drop_index("ix_client_payments_invoice_id", table_name="client_payments")

    op.drop_index("idx_client_payment_project", table_name="client_payments")

    op.drop_index("idx_client_payment_client", table_name="client_payments")

    op.drop_index("idx_client_payment_cheque", table_name="client_payments")

    op.drop_index("idx_client_payment_reference_no", table_name="client_payments")

    op.drop_index("ix_client_payments_payment_no", table_name="client_payments")

    op.drop_table("client_payments")

    op.drop_constraint("fk_quotation_master_client_user_id", "quotation_master", type_="foreignkey")

    op.drop_index(op.f("ix_quotation_master_client_user_id"), table_name="quotation_master")

    op.drop_column("quotation_master", "client_user_id")
