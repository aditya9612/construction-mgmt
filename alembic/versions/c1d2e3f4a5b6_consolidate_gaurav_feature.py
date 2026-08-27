"""Consolidate gaurav-feature migrations

Revision ID: c1d2e3f4a5b6
Revises: a0b1c2d3e4f5
Create Date: 2026-08-27 13:40:00.000000
"""

import datetime
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'c1d2e3f4a5b6'
down_revision = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Create companies table
    # ---------------------------------------------------------
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("subdomain", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companies_subdomain"), "companies", ["subdomain"], unique=True)

    # ---------------------------------------------------------
    # 2. Ensure Default Company exists and resolve its ID dynamically
    # ---------------------------------------------------------
    conn = op.get_bind()
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    res = conn.execute(sa.text("SELECT id FROM companies LIMIT 1")).fetchone()
    if not res:
        conn.execute(
            sa.text(
                f"INSERT INTO companies (name, is_active, created_at, updated_at) "
                f"VALUES ('Default Company', 1, '{now}', '{now}')"
            )
        )
    company_res = conn.execute(
        sa.text("SELECT id FROM companies ORDER BY id ASC LIMIT 1")
    ).fetchone()
    default_company_id = company_res[0] if company_res else 1
    def_comp_str = str(default_company_id)

    # ---------------------------------------------------------
    # 3. Column alterations and indexes on existing domain tables
    # ---------------------------------------------------------
    op.create_index(
        op.f("ix_client_payments_client_user_id"),
        "client_payments",
        ["client_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_client_payments_project_id"),
        "client_payments",
        ["project_id"],
        unique=False,
    )
    op.alter_column(
        "equipment_maintenance",
        "project_id",
        existing_type=mysql.INTEGER(),
        nullable=False,
    )
    op.create_index(
        op.f("ix_labour_contractor_id"), "labour", ["contractor_id"], unique=False
    )
    op.drop_column("labour", "daily_wage_rate")
    op.drop_column("labour", "skill_type")
    op.alter_column(
        "material_master", "unit_id", existing_type=mysql.INTEGER(), nullable=False
    )
    op.alter_column(
        "materials", "material_master_id", existing_type=mysql.INTEGER(), nullable=False
    )
    op.alter_column(
        "materials", "unit_id", existing_type=mysql.INTEGER(), nullable=False
    )
    op.create_index(op.f("ix_accounts_id"), "accounts", ["id"], unique=False)
    op.create_index(op.f("ix_vendor_bills_id"), "vendor_bills", ["id"], unique=False)

    # ---------------------------------------------------------
    # 4. Users, Projects, Company Settings tenant isolation & super admin
    # ---------------------------------------------------------
    op.add_column(
        "users",
        sa.Column(
            "is_super_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=True,
            server_default=sa.text(def_comp_str),
        ),
    )
    op.create_index(
        op.f("ix_users_company_id"), "users", ["company_id"], unique=False
    )
    op.create_foreign_key(
        "fk_users_company_id",
        "users",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "projects",
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=True,
            server_default=sa.text(def_comp_str),
        ),
    )
    op.create_index(
        op.f("ix_projects_company_id"), "projects", ["company_id"], unique=False
    )
    op.create_foreign_key(
        "fk_projects_company_id",
        "projects",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column(
        "company_settings", sa.Column("company_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        op.f("ix_company_settings_company_id"),
        "company_settings",
        ["company_id"],
        unique=True,
    )
    op.create_foreign_key(
        "fk_company_settings_company_id",
        "company_settings",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # ---------------------------------------------------------
    # 5. Root entities (CASCADE delete)
    # ---------------------------------------------------------
    cascade_tables = [
        "contractors",
        "material_master",
        "invoices",
        "client_payments",
        "accounts",
        "vendor_bills",
    ]
    for table in cascade_tables:
        op.add_column(
            table,
            sa.Column(
                "company_id",
                sa.Integer(),
                nullable=True,
                server_default=sa.text(def_comp_str),
            ),
        )
        op.create_foreign_key(
            f"fk_{table}_company_id",
            table,
            "companies",
            ["company_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # ---------------------------------------------------------
    # 6. Root entities (RESTRICT delete with indexes)
    # ---------------------------------------------------------
    restrict_tables = [
        "activity_types",
        "labour_types",
        "units",
        "labour",
        "owners",
        "suppliers",
    ]
    for table in restrict_tables:
        op.add_column(
            table,
            sa.Column(
                "company_id",
                sa.Integer(),
                server_default=sa.text(def_comp_str),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{table}_company_id", table, ["company_id"])
        op.create_foreign_key(
            f"fk_{table}_company_id_companies",
            table,
            "companies",
            ["company_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    # ---------------------------------------------------------
    # 7. Quotation master & CAD conversions
    # ---------------------------------------------------------
    op.add_column(
        "quotation_master",
        sa.Column(
            "company_id",
            sa.Integer(),
            nullable=True,
            server_default=sa.text(def_comp_str),
        ),
    )
    op.create_index(
        op.f("ix_quotation_master_company_id"),
        "quotation_master",
        ["company_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_quotation_master_company_id",
        "quotation_master",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "cad_conversions",
        sa.Column("company_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_cad_conversions_company_id"),
        "cad_conversions",
        ["company_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_cad_conversions_company_id",
        "cad_conversions",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # ---------------------------------------------------------
    # 8. Data Backfills across all tables
    # ---------------------------------------------------------
    all_backfill_tables = (
        ["projects", "company_settings", "quotation_master", "cad_conversions"]
        + cascade_tables
        + restrict_tables
    )
    for table in all_backfill_tables:
        conn.execute(
            sa.text(
                f"UPDATE {table} SET company_id = {default_company_id} WHERE company_id IS NULL"
            )
        )
    conn.execute(
        sa.text(
            f"UPDATE users SET company_id = {default_company_id} WHERE company_id IS NULL AND is_super_admin = 0"
        )
    )

    # ---------------------------------------------------------
    # 9. Create plans table
    # ---------------------------------------------------------
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("billing_interval", sa.String(length=20), nullable=False, server_default=sa.text("'monthly'")),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default=sa.text("'INR'")),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plans_code"), "plans", ["code"], unique=True)

    # ---------------------------------------------------------
    # 10. Create subscriptions table
    # ---------------------------------------------------------
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'trial'")),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_reference", sa.String(length=255), nullable=True),
        sa.Column("external_customer_id", sa.String(length=255), nullable=True),
        sa.Column("external_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subscriptions_company_id"), "subscriptions", ["company_id"], unique=True)
    op.create_index(op.f("ix_subscriptions_plan_id"), "subscriptions", ["plan_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"], unique=False)
    op.create_index(op.f("ix_subscriptions_external_customer_id"), "subscriptions", ["external_customer_id"], unique=False)
    op.create_index(op.f("ix_subscriptions_external_subscription_id"), "subscriptions", ["external_subscription_id"], unique=False)

    # ---------------------------------------------------------
    # 11. Create subscription_invoices table
    # ---------------------------------------------------------
    op.create_table(
        "subscription_invoices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=False),
        sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subtotal", sa.DECIMAL(precision=12, scale=2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("tax_amount", sa.DECIMAL(precision=12, scale=2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("total_amount", sa.DECIMAL(precision=12, scale=2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default=sa.text("'INR'")),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("external_invoice_id", sa.String(length=255), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subscription_invoices_company_id"), "subscription_invoices", ["company_id"], unique=False)
    op.create_index(op.f("ix_subscription_invoices_subscription_id"), "subscription_invoices", ["subscription_id"], unique=False)
    op.create_index(op.f("ix_subscription_invoices_invoice_number"), "subscription_invoices", ["invoice_number"], unique=True)
    op.create_index(op.f("ix_subscription_invoices_status"), "subscription_invoices", ["status"], unique=False)
    op.create_index(op.f("ix_subscription_invoices_external_invoice_id"), "subscription_invoices", ["external_invoice_id"], unique=False)

    # ---------------------------------------------------------
    # 12. Create billing_webhook_events table
    # ---------------------------------------------------------
    op.create_table(
        "billing_webhook_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload_reference", sa.String(length=255), nullable=True),
        sa.Column("payload_summary", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("provider", "event_id", name="uq_provider_event_id"),
    )
    op.create_index(op.f("ix_billing_webhook_events_company_id"), "billing_webhook_events", ["company_id"], unique=False)
    op.create_index(op.f("ix_billing_webhook_events_provider"), "billing_webhook_events", ["provider"], unique=False)
    op.create_index(op.f("ix_billing_webhook_events_event_id"), "billing_webhook_events", ["event_id"], unique=False)
    op.create_index(op.f("ix_billing_webhook_events_event_type"), "billing_webhook_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_billing_webhook_events_status"), "billing_webhook_events", ["status"], unique=False)

    # ---------------------------------------------------------
    # 13. Create manual_payment_transactions table
    # ---------------------------------------------------------
    op.create_table(
        "manual_payment_transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.DECIMAL(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="INR"),
        sa.Column("payment_method", sa.String(length=50), nullable=False, server_default="UPI"),
        sa.Column("transaction_reference", sa.String(length=100), nullable=False),
        sa.Column("utr_reference", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("verified_by", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["subscription_invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_manual_payment_transactions_company_id"), "manual_payment_transactions", ["company_id"], unique=False)
    op.create_index(op.f("ix_manual_payment_transactions_subscription_id"), "manual_payment_transactions", ["subscription_id"], unique=False)
    op.create_index(op.f("ix_manual_payment_transactions_plan_id"), "manual_payment_transactions", ["plan_id"], unique=False)
    op.create_index(op.f("ix_manual_payment_transactions_invoice_id"), "manual_payment_transactions", ["invoice_id"], unique=False)
    op.create_index(op.f("ix_manual_payment_transactions_transaction_reference"), "manual_payment_transactions", ["transaction_reference"], unique=True)
    op.create_index(op.f("ix_manual_payment_transactions_utr_reference"), "manual_payment_transactions", ["utr_reference"], unique=True)
    op.create_index(op.f("ix_manual_payment_transactions_status"), "manual_payment_transactions", ["status"], unique=False)


def downgrade() -> None:
    # 1. Drop SaaS tables
    op.drop_table("manual_payment_transactions")
    op.drop_table("billing_webhook_events")
    op.drop_table("subscription_invoices")
    op.drop_table("subscriptions")
    op.drop_table("plans")

    # 2. CAD conversions & Quotation master
    op.drop_constraint("fk_cad_conversions_company_id", "cad_conversions", type_="foreignkey")
    op.drop_index(op.f("ix_cad_conversions_company_id"), table_name="cad_conversions")
    op.drop_column("cad_conversions", "company_id")

    op.drop_constraint("fk_quotation_master_company_id", "quotation_master", type_="foreignkey")
    op.drop_index(op.f("ix_quotation_master_company_id"), table_name="quotation_master")
    op.drop_column("quotation_master", "company_id")

    # 3. RESTRICT tables
    restrict_tables = [
        "activity_types",
        "labour_types",
        "units",
        "labour",
        "owners",
        "suppliers",
    ]
    for table in restrict_tables:
        op.drop_constraint(f"fk_{table}_company_id_companies", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_column(table, "company_id")

    # 4. CASCADE tables
    cascade_tables = [
        "contractors",
        "material_master",
        "invoices",
        "client_payments",
        "accounts",
        "vendor_bills",
    ]
    for table in cascade_tables:
        op.drop_constraint(f"fk_{table}_company_id", table, type_="foreignkey")
        op.drop_column(table, "company_id")

    # 5. Company settings, projects, users
    op.drop_constraint("fk_company_settings_company_id", "company_settings", type_="foreignkey")
    op.drop_index(op.f("ix_company_settings_company_id"), table_name="company_settings")
    op.drop_column("company_settings", "company_id")

    op.drop_constraint("fk_projects_company_id", "projects", type_="foreignkey")
    op.drop_index(op.f("ix_projects_company_id"), table_name="projects")
    op.drop_column("projects", "company_id")

    op.drop_constraint("fk_users_company_id", "users", type_="foreignkey")
    op.drop_index(op.f("ix_users_company_id"), table_name="users")
    op.drop_column("users", "company_id")
    op.drop_column("users", "is_super_admin")

    # 6. Reverse schema & nullability changes
    op.drop_index(op.f("ix_vendor_bills_id"), table_name="vendor_bills")
    op.drop_index(op.f("ix_accounts_id"), table_name="accounts")
    op.alter_column(
        "materials", "unit_id", existing_type=mysql.INTEGER(), nullable=True
    )
    op.alter_column(
        "materials", "material_master_id", existing_type=mysql.INTEGER(), nullable=True
    )
    op.alter_column(
        "material_master", "unit_id", existing_type=mysql.INTEGER(), nullable=True
    )
    op.add_column(
        "labour",
        sa.Column("skill_type", mysql.ENUM("SKILLED", "UNSKILLED"), nullable=True),
    )
    op.add_column(
        "labour",
        sa.Column(
            "daily_wage_rate", mysql.DECIMAL(precision=18, scale=2), nullable=True
        ),
    )
    op.drop_index(op.f("ix_labour_contractor_id"), table_name="labour")
    op.alter_column(
        "equipment_maintenance",
        "project_id",
        existing_type=mysql.INTEGER(),
        nullable=True,
    )
    op.drop_index(op.f("ix_client_payments_project_id"), table_name="client_payments")
    op.drop_index(
        op.f("ix_client_payments_client_user_id"), table_name="client_payments"
    )

    # 7. Companies table
    op.drop_index(op.f("ix_companies_subdomain"), table_name="companies")
    op.drop_table("companies")
