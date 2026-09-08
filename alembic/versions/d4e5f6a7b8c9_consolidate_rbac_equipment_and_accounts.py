"""Consolidate RBAC models, enums, equipment multi-tenancy, and accounts seed

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-09-08 21:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c1d2e3f4a5b6'
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
    existing_tables = set(inspector.get_table_names())

    # ---------------------------------------------------------
    # 1. Create roles table
    # ---------------------------------------------------------
    if 'roles' not in existing_tables:
        op.create_table(
            'roles',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('company_id', sa.Integer(), nullable=True),
            sa.Column('name', sa.String(length=50), nullable=False),
            sa.Column('display_name', sa.String(length=100), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('is_system', sa.Boolean(), server_default=sa.text('0'), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('company_id', 'name', name='uq_role_company_name')
        )
        op.create_index(op.f('ix_roles_company_id'), 'roles', ['company_id'], unique=False)
        op.create_index(op.f('ix_roles_name'), 'roles', ['name'], unique=False)

    # ---------------------------------------------------------
    # 2. Create user_permission_overrides table
    # ---------------------------------------------------------
    if 'user_permission_overrides' not in existing_tables:
        op.create_table(
            'user_permission_overrides',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('permission_id', sa.Integer(), nullable=False),
            sa.Column('is_granted', sa.Boolean(), server_default=sa.text('1'), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'permission_id', name='uq_user_permission_override')
        )
        op.create_index(op.f('ix_user_permission_overrides_permission_id'), 'user_permission_overrides', ['permission_id'], unique=False)
        op.create_index(op.f('ix_user_permission_overrides_user_id'), 'user_permission_overrides', ['user_id'], unique=False)

    # ---------------------------------------------------------
    # 3. Update role_permissions: add role_id and FK to roles
    # ---------------------------------------------------------
    if 'role_permissions' in existing_tables:
        rp_cols = {c['name'] for c in inspector.get_columns('role_permissions')}
        if 'role_id' not in rp_cols:
            op.add_column('role_permissions', sa.Column('role_id', sa.Integer(), nullable=True))
            op.create_foreign_key(
                'fk_role_permissions_role_id',
                'role_permissions',
                'roles',
                ['role_id'],
                ['id'],
                ondelete='CASCADE'
            )
            op.create_index(op.f('ix_role_permissions_role_id'), 'role_permissions', ['role_id'], unique=False)

    # ---------------------------------------------------------
    # 4. Update equipment.condition ENUM
    # Final values: GOOD, REPAIR, DAMAGED, MAINTENANCE
    # ---------------------------------------------------------
    op.alter_column(
        'equipment',
        'condition',
        existing_type=sa.Enum('GOOD', 'REPAIR', 'DAMAGED', name='equipmentcondition'),
        type_=sa.Enum('GOOD', 'REPAIR', 'DAMAGED', 'MAINTENANCE', name='equipmentcondition'),
        existing_nullable=True,
        nullable=True
    )

    # ---------------------------------------------------------
    # 5. Update equipment.status ENUM
    # Final values: AVAILABLE, IN_PROJECT, IDLE, RENTED, MAINTENANCE, DAMAGED
    # ---------------------------------------------------------
    op.alter_column(
        'equipment',
        'status',
        existing_type=sa.Enum('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', name='equipmentstatus'),
        type_=sa.Enum('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', 'DAMAGED', name='equipmentstatus'),
        existing_nullable=False,
        nullable=False
    )

    # ---------------------------------------------------------
    # 6. Update invoices.status ENUM
    # Final values: pending, partial, paid, CANCELLED
    # ---------------------------------------------------------
    op.alter_column(
        'invoices',
        'status',
        existing_type=sa.Enum('pending', 'partial', 'paid', name='invoicestatus'),
        type_=sa.Enum('pending', 'partial', 'paid', 'CANCELLED', name='invoicestatus'),
        existing_nullable=True,
        nullable=True,
        existing_server_default=sa.text("'pending'"),
        server_default=sa.text("'pending'")
    )

    # ---------------------------------------------------------
    # 7. Update labour_payroll.status ENUM
    # Final values: DRAFT, LOCKED, PENDING, PAID, PARTIAL
    # ---------------------------------------------------------
    op.alter_column(
        'labour_payroll',
        'status',
        existing_type=sa.Enum('PENDING', 'PAID', 'PARTIAL', name='payrollstatus'),
        type_=sa.Enum('DRAFT', 'LOCKED', 'PENDING', 'PAID', 'PARTIAL', name='payrollstatus'),
        existing_nullable=True,
        nullable=True,
    )

    # ---------------------------------------------------------
    # 8. Equipment Multi-Tenancy Column, FK, and Indexes
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # 9. Backfill Equipment.company_id from Project.company_id
    # ---------------------------------------------------------
    op.execute(sa.text("""
        UPDATE equipment e
        INNER JOIN projects p ON e.project_id = p.id
        SET e.company_id = p.company_id
        WHERE e.project_id IS NOT NULL
          AND p.company_id IS NOT NULL
          AND e.company_id IS NULL;
    """))

    # ---------------------------------------------------------
    # 10. Accounts Table: Multi-Tenant Constraint (company_id, code)
    # ---------------------------------------------------------
    acc_indexes = inspector.get_indexes("accounts")
    for idx in acc_indexes:
        if idx["name"] == "code" or (idx["column_names"] == ["code"] and idx.get("unique")):
            try:
                op.drop_index(idx["name"], table_name="accounts")
            except Exception:
                pass

    acc_uniques = [u["name"] for u in inspector.get_unique_constraints("accounts")]
    if "uq_accounts_company_code" not in acc_uniques:
        op.create_unique_constraint(
            "uq_accounts_company_code",
            "accounts",
            ["company_id", "code"],
        )

    # ---------------------------------------------------------
    # 11. Seed Standard Chart of Accounts for All Companies (Set-based)
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # 12. Link Default Account IDs into CompanySettings
    # ---------------------------------------------------------
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
    existing_tables = set(inspector.get_table_names())

    # ---------------------------------------------------------
    # 1. Accounts table: revert multi-tenant unique constraint
    # Note: If duplicate codes exist across different companies,
    # MySQL will raise Error 1062 (Duplicate entry), failing loudly
    # rather than silently pretending success.
    # ---------------------------------------------------------
    acc_uniques = [u["name"] for u in inspector.get_unique_constraints("accounts")]
    if "uq_accounts_company_code" in acc_uniques:
        op.drop_constraint("uq_accounts_company_code", "accounts", type_="unique")

    op.create_unique_constraint("code", "accounts", ["code"])

    # ---------------------------------------------------------
    # 2. Revert Equipment Multi-Tenancy
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # 3. Revert labour_payroll.status ENUM
    # Under MySQL strict mode, if rows exist with status 'DRAFT' or 'LOCKED',
    # MySQL will reject the column modification (error 1265) to prevent
    # silent truncation or corruption of active business records.
    # ---------------------------------------------------------
    op.alter_column(
        'labour_payroll',
        'status',
        existing_type=sa.Enum('DRAFT', 'LOCKED', 'PENDING', 'PAID', 'PARTIAL', name='payrollstatus'),
        type_=sa.Enum('PENDING', 'PAID', 'PARTIAL', name='payrollstatus'),
        existing_nullable=True,
        nullable=True,
    )

    # ---------------------------------------------------------
    # 4. Revert invoices.status ENUM
    # ---------------------------------------------------------
    op.alter_column(
        'invoices',
        'status',
        existing_type=sa.Enum('pending', 'partial', 'paid', 'CANCELLED', name='invoicestatus'),
        type_=sa.Enum('pending', 'partial', 'paid', name='invoicestatus'),
        existing_nullable=True,
        nullable=True,
        existing_server_default=sa.text("'pending'"),
        server_default=sa.text("'pending'")
    )

    # ---------------------------------------------------------
    # 5. Revert equipment.status ENUM
    # ---------------------------------------------------------
    op.alter_column(
        'equipment',
        'status',
        existing_type=sa.Enum('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', 'DAMAGED', name='equipmentstatus'),
        type_=sa.Enum('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', name='equipmentstatus'),
        existing_nullable=False,
        nullable=False
    )

    # ---------------------------------------------------------
    # 6. Revert equipment.condition ENUM
    # ---------------------------------------------------------
    op.alter_column(
        'equipment',
        'condition',
        existing_type=sa.Enum('GOOD', 'REPAIR', 'DAMAGED', 'MAINTENANCE', name='equipmentcondition'),
        type_=sa.Enum('GOOD', 'REPAIR', 'DAMAGED', name='equipmentcondition'),
        existing_nullable=True,
        nullable=True
    )

    # ---------------------------------------------------------
    # 7. Revert role_permissions: drop FK, index, and role_id
    # ---------------------------------------------------------
    if 'role_permissions' in existing_tables:
        rp_cols = {c['name'] for c in inspector.get_columns('role_permissions')}
        if 'role_id' in rp_cols:
            op.drop_index(op.f('ix_role_permissions_role_id'), table_name='role_permissions')
            op.drop_constraint('fk_role_permissions_role_id', 'role_permissions', type_='foreignkey')
            op.drop_column('role_permissions', 'role_id')

    # ---------------------------------------------------------
    # 8. Drop user_permission_overrides table
    # ---------------------------------------------------------
    if 'user_permission_overrides' in existing_tables:
        op.drop_index(op.f('ix_user_permission_overrides_user_id'), table_name='user_permission_overrides')
        op.drop_index(op.f('ix_user_permission_overrides_permission_id'), table_name='user_permission_overrides')
        op.drop_table('user_permission_overrides')

    # ---------------------------------------------------------
    # 9. Drop roles table
    # ---------------------------------------------------------
    if 'roles' in existing_tables:
        op.drop_index(op.f('ix_roles_name'), table_name='roles')
        op.drop_index(op.f('ix_roles_company_id'), table_name='roles')
        op.drop_table('roles')
