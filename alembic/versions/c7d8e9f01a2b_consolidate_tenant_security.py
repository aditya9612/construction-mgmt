"""Consolidate tenant security and company isolation

Revision ID: c7d8e9f01a2b
Revises: a0b1c2d3e4f5
Create Date: 2026-08-26 17:05:00.000000
"""

import datetime
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'c7d8e9f01a2b'
down_revision = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create companies table
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
    op.create_index(op.f("ix_companies_name"), "companies", ["name"], unique=False)
    op.create_index(
        op.f("ix_companies_subdomain"), "companies", ["subdomain"], unique=True
    )

    # 2. Ensure Default Company exists and resolve its ID dynamically
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

    # 3. Column alterations and index changes from original a53fa727c188
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

    # 4. Core tenant isolation & super admin additions (users, projects, company_settings)
    # Users
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
        ondelete="CASCADE",
    )

    # Projects
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

    # Company settings
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

    # 5. Root entities (CASCADE delete) - contractors, material_master, invoices, client_payments, accounts, vendor_bills
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

    # 6. Root entities (RESTRICT delete with indexes) - activity_types, labour_types, units, labour, owners, suppliers
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

    # 7. Quotation master (RESTRICT delete with index)
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

    # 8. Data Backfills across all tables
    all_backfill_tables = (
        ["projects", "company_settings", "quotation_master"]
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


def downgrade() -> None:
    # 1. Quotation master
    op.drop_constraint("fk_quotation_master_company_id", "quotation_master", type_="foreignkey")
    op.drop_index(op.f("ix_quotation_master_company_id"), table_name="quotation_master")
    op.drop_column("quotation_master", "company_id")

    # 2. RESTRICT tables
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

    # 3. CASCADE tables
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

    # 4. Company settings, projects, users
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

    # 5. Reverse schema & nullability changes
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

    # 6. Companies table
    op.drop_index(op.f("ix_companies_subdomain"), table_name="companies")
    op.drop_index(op.f("ix_companies_name"), table_name="companies")
    op.drop_table("companies")
