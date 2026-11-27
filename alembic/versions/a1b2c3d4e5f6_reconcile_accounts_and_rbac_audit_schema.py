"""reconcile_accounts_and_rbac_audit_schema

Revision ID: a1b2c3d4e5f6
Revises: f4a5b6c7d8e9
Create Date: 2026-11-25 15:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = set(inspector.get_table_names())

    # =========================================================================
    # 1. Accounts Table: Reconcile Multi-Tenant Unique Constraint (company_id, code)
    # =========================================================================
    if "accounts" in existing_tables:
        existing_indexes = inspector.get_indexes("accounts")
        existing_uniques = inspector.get_unique_constraints("accounts")

        index_names = {idx["name"] for idx in existing_indexes if idx.get("name")}
        unique_names = {u["name"] for u in existing_uniques if u.get("name")}

        # 1.1 Drop obsolete single-tenant global unique index on `code` if present
        for idx in existing_indexes:
            idx_name = idx.get("name")
            col_names = idx.get("column_names", [])
            is_unique = idx.get("unique", False)
            if idx_name == "code" or (col_names == ["code"] and is_unique):
                try:
                    op.drop_index(idx_name, table_name="accounts")
                except Exception:
                    # In some MySQL configurations, unique constraints must be dropped via drop_constraint
                    try:
                        op.drop_constraint(idx_name, "accounts", type_="unique")
                    except Exception:
                        pass

        for u in existing_uniques:
            u_name = u.get("name")
            col_names = u.get("column_names", [])
            if u_name == "code" or col_names == ["code"]:
                try:
                    op.drop_constraint(u_name, "accounts", type_="unique")
                except Exception:
                    pass

        # Re-inspect after dropping obsolete index
        inspector = Inspector.from_engine(bind)
        existing_indexes = inspector.get_indexes("accounts")
        existing_uniques = inspector.get_unique_constraints("accounts")
        index_names = {idx["name"] for idx in existing_indexes if idx.get("name")}
        unique_names = {u["name"] for u in existing_uniques if u.get("name")}

        # 1.2 Check if composite constraint uq_accounts_company_code already exists
        has_composite_unique = False
        for u in existing_uniques:
            if u.get("name") == "uq_accounts_company_code" or u.get("column_names") == ["company_id", "code"]:
                has_composite_unique = True
                break
        if not has_composite_unique:
            for idx in existing_indexes:
                if (idx.get("name") == "uq_accounts_company_code" or idx.get("column_names") == ["company_id", "code"]) and idx.get("unique"):
                    has_composite_unique = True
                    break

        if not has_composite_unique:
            # Pre-flight check: ensure no duplicate (company_id, code) records exist before applying constraint
            conn = op.get_bind()
            dup_query = sa.text("""
                SELECT company_id, code, COUNT(*) as cnt
                FROM accounts
                GROUP BY company_id, code
                HAVING cnt > 1
            """)
            duplicates = conn.execute(dup_query).fetchall()
            if duplicates:
                dup_str = ", ".join([f"(company_id={r[0]}, code='{r[1]}', count={r[2]})" for r in duplicates])
                raise RuntimeError(
                    f"Cannot apply unique constraint uq_accounts_company_code: duplicate accounts exist: {dup_str}"
                )

            op.create_unique_constraint(
                "uq_accounts_company_code",
                "accounts",
                ["company_id", "code"]
            )

        # 1.3 Ensure non-unique search index ix_accounts_code exists for fast code lookups
        if "ix_accounts_code" not in index_names:
            has_code_index = any(idx.get("column_names") == ["code"] and not idx.get("unique") for idx in existing_indexes)
            if not has_code_index:
                op.create_index("ix_accounts_code", "accounts", ["code"], unique=False)

    # =========================================================================
    # 2. RBAC Audit Logs Table: Reconcile Audit Tracking Columns
    # =========================================================================
    if "rbac_audit_logs" not in existing_tables:
        # If table is completely absent, create it with full modern schema
        op.create_table(
            "rbac_audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=True),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=100), nullable=False),
            sa.Column("target_type", sa.String(length=50), nullable=True),
            sa.Column("target_id", sa.String(length=100), nullable=True),
            sa.Column("permission", sa.String(length=150), nullable=True),
            sa.Column("old_value", sa.Text(), nullable=True),
            sa.Column("new_value", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        )
        op.create_index("ix_rbac_audit_logs_company_id", "rbac_audit_logs", ["company_id"])
        op.create_index("ix_rbac_audit_logs_actor_id", "rbac_audit_logs", ["actor_id"])
        op.create_index("ix_rbac_audit_logs_action", "rbac_audit_logs", ["action"])
        op.create_index("ix_rbac_audit_logs_target_type", "rbac_audit_logs", ["target_type"])
        op.create_index("ix_rbac_audit_logs_target_id", "rbac_audit_logs", ["target_id"])
        op.create_index("ix_rbac_audit_logs_created_at", "rbac_audit_logs", ["created_at"])
    else:
        # Table exists: inspect existing columns and add only missing columns
        audit_cols = {col["name"] for col in inspector.get_columns("rbac_audit_logs")}

        if "actor_id" not in audit_cols:
            op.add_column("rbac_audit_logs", sa.Column("actor_id", sa.Integer(), nullable=True))

        if "target_type" not in audit_cols:
            op.add_column("rbac_audit_logs", sa.Column("target_type", sa.String(length=50), nullable=True))

        if "target_id" not in audit_cols:
            op.add_column("rbac_audit_logs", sa.Column("target_id", sa.String(length=100), nullable=True))

        if "permission" not in audit_cols:
            op.add_column("rbac_audit_logs", sa.Column("permission", sa.String(length=150), nullable=True))

        if "old_value" not in audit_cols:
            op.add_column("rbac_audit_logs", sa.Column("old_value", sa.Text(), nullable=True))

        if "new_value" not in audit_cols:
            op.add_column("rbac_audit_logs", sa.Column("new_value", sa.Text(), nullable=True))

        # Re-inspect indexes on rbac_audit_logs
        audit_indexes = {idx["name"] for idx in inspector.get_indexes("rbac_audit_logs") if idx.get("name")}

        if "ix_rbac_audit_logs_actor_id" not in audit_indexes:
            op.create_index("ix_rbac_audit_logs_actor_id", "rbac_audit_logs", ["actor_id"])

        if "ix_rbac_audit_logs_target_type" not in audit_indexes:
            op.create_index("ix_rbac_audit_logs_target_type", "rbac_audit_logs", ["target_type"])

        if "ix_rbac_audit_logs_target_id" not in audit_indexes:
            op.create_index("ix_rbac_audit_logs_target_id", "rbac_audit_logs", ["target_id"])


def downgrade() -> None:
    # Production Safety Policy:
    # Reversing this migration would destroy tenant-isolation guarantees on accounts
    # and risk dropping audit log history in rbac_audit_logs.
    raise RuntimeError(
        "IrreversibleMigrationError: Downgrading revision a1b2c3d4e5f6 is prohibited because "
        "it would compromise multi-tenant chart-of-accounts isolation and risk permanent audit data loss."
    )
