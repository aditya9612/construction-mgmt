"""Create rbac_audit_logs table

Revision ID: e2f3a4b5c6d7
Revises: d4e5f6a7b8c9
Create Date: 2026-09-09 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2f3a4b5c6d7'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        inspector = None
        existing_tables = set()

    if "rbac_audit_logs" not in existing_tables:
        op.create_table(
            "rbac_audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), nullable=True),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=50), nullable=False),
            sa.Column("target_type", sa.String(length=50), nullable=False),
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


def downgrade() -> None:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        existing_tables = set()

    if "rbac_audit_logs" in existing_tables:
        op.drop_table("rbac_audit_logs")
