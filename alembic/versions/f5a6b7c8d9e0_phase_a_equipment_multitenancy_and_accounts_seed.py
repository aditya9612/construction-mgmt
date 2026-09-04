"""Phase A: Equipment multi-tenancy and standard chart of accounts seed

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-09-04 19:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f5a6b7c8d9e0'
down_revision = 'e4f5a6b7c8d9'
branch_labels = None
depends_on = None


STANDARD_SYSTEM_ACCOUNTS = [
    # Assets
    {"name": "Main Bank Account", "code": "BANK", "type": "ASSET"},
    {"name": "Primary Cash", "code": "CASH", "type": "ASSET"},
    {"name": "Petty Cash", "code": "PETTY_CASH", "type": "ASSET"},
    {"name": "Input GST", "code": "INPUT_GST", "type": "ASSET"},
    {"name": "Accounts Receivable", "code": "ACCOUNTS_RECEIVABLE", "type": "ASSET"},
    {"name": "Cash / Bank (1001)", "code": "1001", "type": "ASSET"},
    {"name": "Accounts Receivable (1200)", "code": "1200", "type": "ASSET"},

    # Liabilities
    {"name": "Vendor Payable", "code": "VENDOR_PAYABLE", "type": "LIABILITY"},
    {"name": "Contractor Payable", "code": "CONTRACTOR_PAYABLE", "type": "LIABILITY"},
    {"name": "Wages Payable", "code": "WAGES_PAYABLE", "type": "LIABILITY"},
    {"name": "Output GST", "code": "OUTPUT_GST", "type": "LIABILITY"},
    {"name": "TDS Payable", "code": "TDS_PAYABLE", "type": "LIABILITY"},
    {"name": "Retention Payable", "code": "RETENTION_PAYABLE", "type": "LIABILITY"},

    # Income
    {"name": "Sales Revenue", "code": "SALES_REVENUE", "type": "INCOME"},

    # Expenses
    {"name": "General Expense", "code": "GENERAL_EXPENSE", "type": "EXPENSE"},
    {"name": "Operating Expense", "code": "EXPENSE", "type": "EXPENSE"},
    {"name": "Labour Expense", "code": "LABOUR_EXPENSE", "type": "EXPENSE"},
    {"name": "Wages Expense", "code": "WAGES_EXPENSE", "type": "EXPENSE"},
    {"name": "Staff Salary Expense", "code": "SALARY_EXPENSE", "type": "EXPENSE"},
    {"name": "Contractor Expense", "code": "CONTRACTOR_EXPENSE", "type": "EXPENSE"},
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # -------------------------------------------------------------
    # 1. Equipment Multi-Tenancy Column, FK, and Index
    # -------------------------------------------------------------
    eq_cols = [c["name"] for c in inspector.get_columns("equipment")]
    if "company_id" not in eq_cols:
        op.add_column(
            "equipment",
            sa.Column("company_id", sa.Integer(), nullable=True)
        )
        op.create_foreign_key(
            "fk_equipment_company_id",
            "equipment",
            "companies",
            ["company_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_equipment_company_id", "equipment", ["company_id"])
        op.create_index("ix_equipment_company_project", "equipment", ["company_id", "project_id"])

    # -------------------------------------------------------------
    # 2. Backfill Equipment.company_id from Project.company_id
    # -------------------------------------------------------------
    op.execute(sa.text("""
        UPDATE equipment e
        INNER JOIN projects p ON e.project_id = p.id
        SET e.company_id = p.company_id
        WHERE e.project_id IS NOT NULL 
          AND p.company_id IS NOT NULL 
          AND e.company_id IS NULL;
    """))

    # Backfill equipment 481 if unassigned, using audit log evidence (created for Project 5 -> Company 2)
    op.execute(sa.text("""
        UPDATE equipment e
        SET e.company_id = 2
        WHERE e.id = 481 AND e.company_id IS NULL;
    """))

    # -------------------------------------------------------------
    # 3. Accounts Table: Multi-Tenant Constraint (company_id, code)
    # -------------------------------------------------------------
    acc_indexes = inspector.get_indexes("accounts")
    for idx in acc_indexes:
        if idx["name"] == "code" or (idx["column_names"] == ["code"] and idx.get("unique")):
            try:
                op.drop_index(idx["name"], table_name="accounts")
            except Exception:
                pass

    acc_uniques = [u["name"] for u in inspector.get_unique_constraints("accounts")]
    if "uq_accounts_company_code" not in acc_uniques:
        try:
            op.create_unique_constraint(
                "uq_accounts_company_code",
                "accounts",
                ["company_id", "code"],
            )
        except Exception:
            pass

    # -------------------------------------------------------------
    # 4. Seed Standard Chart of Accounts for All Companies (Set-based)
    # -------------------------------------------------------------
    template_rows = " UNION ALL ".join([
        f"SELECT '{acc['name']}' as name, '{acc['code']}' as code, '{acc['type']}' as type"
        for acc in STANDARD_SYSTEM_ACCOUNTS
    ])

    op.execute(sa.text(f"""
        INSERT INTO accounts (company_id, name, code, type, created_at, updated_at)
        SELECT c.id, t.name, t.code, t.type, NOW(), NOW()
        FROM companies c
        CROSS JOIN ({template_rows}) t
        LEFT JOIN accounts a ON a.company_id = c.id AND a.code = t.code
        WHERE a.id IS NULL;
    """))

    # -------------------------------------------------------------
    # 5. Link Default Account IDs into CompanySettings
    # -------------------------------------------------------------
    for setting_col, acc_code in [
        ("primary_cash_account_id", "CASH"),
        ("petty_cash_account_id", "PETTY_CASH"),
        ("wages_account_id", "WAGES_EXPENSE"),
        ("staff_salary_account_id", "SALARY_EXPENSE"),
        ("contractor_expense_account_id", "CONTRACTOR_EXPENSE"),
        ("tds_payable_account_id", "TDS_PAYABLE"),
        ("retention_payable_account_id", "RETENTION_PAYABLE"),
    ]:
        op.execute(sa.text(f"""
            UPDATE company_settings cs
            INNER JOIN accounts a ON a.company_id = cs.company_id AND a.code = '{acc_code}'
            SET cs.{setting_col} = a.id
            WHERE cs.{setting_col} IS NULL;
        """))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    acc_uniques = [u["name"] for u in inspector.get_unique_constraints("accounts")]
    if "uq_accounts_company_code" in acc_uniques:
        op.drop_constraint("uq_accounts_company_code", "accounts", type_="unique")

    op.create_unique_constraint("code", "accounts", ["code"])

    eq_indexes = [idx["name"] for idx in inspector.get_indexes("equipment")]
    if "ix_equipment_company_project" in eq_indexes:
        op.drop_index("ix_equipment_company_project", table_name="equipment")
    if "ix_equipment_company_id" in eq_indexes:
        op.drop_index("ix_equipment_company_id", table_name="equipment")

    eq_fks = [fk["name"] for fk in inspector.get_foreign_keys("equipment")]
    if "fk_equipment_company_id" in eq_fks:
        op.drop_constraint("fk_equipment_company_id", "equipment", type_="foreignkey")

    eq_cols = [c["name"] for c in inspector.get_columns("equipment")]
    if "company_id" in eq_cols:
        op.drop_column("equipment", "company_id")
