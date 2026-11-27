"""reconcile rbac_audit_logs schema

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-27 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_offline = context.is_offline_mode()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_offline:
        op.create_index(
            "ix_rbac_audit_logs_company_id",
            "rbac_audit_logs",
            ["company_id"],
            unique=False,
        )
        op.create_index(
            "ix_rbac_audit_logs_action",
            "rbac_audit_logs",
            ["action"],
            unique=False,
        )
        op.create_index(
            "ix_rbac_audit_logs_created_at",
            "rbac_audit_logs",
            ["created_at"],
            unique=False,
        )
        return

    inspector = sa.inspect(bind)
    if "rbac_audit_logs" not in set(inspector.get_table_names()):
        return

    audit_cols = {c["name"] for c in inspector.get_columns("rbac_audit_logs")}

    if "entity_type" not in audit_cols:
        op.add_column(
            "rbac_audit_logs",
            sa.Column("entity_type", sa.String(length=50), nullable=True),
        )
    if "entity_id" not in audit_cols:
        op.add_column(
            "rbac_audit_logs",
            sa.Column("entity_id", sa.Integer(), nullable=True),
        )
    if "performed_by" not in audit_cols:
        op.add_column(
            "rbac_audit_logs",
            sa.Column("performed_by", sa.Integer(), nullable=True),
        )
    if "details" not in audit_cols:
        op.add_column(
            "rbac_audit_logs",
            sa.Column("details", sa.JSON(), nullable=True),
        )

    audit_indexes = {
        idx["name"]
        for idx in inspector.get_indexes("rbac_audit_logs")
        if idx.get("name")
    }

    for idx_name, cols in (
        ("ix_rbac_audit_logs_company_id", ["company_id"]),
        ("ix_rbac_audit_logs_actor_id", ["actor_id"]),
        ("ix_rbac_audit_logs_action", ["action"]),
        ("ix_rbac_audit_logs_target_type", ["target_type"]),
        ("ix_rbac_audit_logs_target_id", ["target_id"]),
        ("ix_rbac_audit_logs_created_at", ["created_at"]),
    ):
        if idx_name not in audit_indexes:
            op.create_index(idx_name, "rbac_audit_logs", cols, unique=False)

    if not is_sqlite:
        fk_cols = {
            tuple(fk.get("constrained_columns") or [])
            for fk in inspector.get_foreign_keys("rbac_audit_logs")
        }
        if ("performed_by",) not in fk_cols:
            op.create_foreign_key(
                "fk_rbac_audit_logs_performed_by",
                "rbac_audit_logs",
                "users",
                ["performed_by"],
                ["id"],
                ondelete="CASCADE",
            )
        if ("company_id",) not in fk_cols:
            op.create_foreign_key(
                "fk_rbac_audit_logs_company_id",
                "rbac_audit_logs",
                "companies",
                ["company_id"],
                ["id"],
                ondelete="CASCADE",
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_offline = context.is_offline_mode()

    if not is_offline:
        inspector = sa.inspect(bind)
        if "rbac_audit_logs" not in set(inspector.get_table_names()):
            return

        audit_cols = {c["name"] for c in inspector.get_columns("rbac_audit_logs")}
        legacy_checks = [
            f"{col} IS NOT NULL"
            for col in ("entity_type", "entity_id", "performed_by", "details")
            if col in audit_cols
        ]
        if legacy_checks:
            non_null = bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM rbac_audit_logs WHERE "
                    + " OR ".join(legacy_checks)
                )
            ).scalar()
            if non_null and int(non_null) > 0:
                raise RuntimeError(
                    "IrreversibleMigrationError: Cannot downgrade revision c3d4e5f6a7b8 "
                    "while legacy RBAC audit columns contain non-null data."
                )

        audit_indexes = {
            idx["name"]
            for idx in inspector.get_indexes("rbac_audit_logs")
            if idx.get("name")
        }
    else:
        audit_indexes = {
            "ix_rbac_audit_logs_created_at",
            "ix_rbac_audit_logs_action",
            "ix_rbac_audit_logs_company_id",
        }

    for idx_name in (
        "ix_rbac_audit_logs_created_at",
        "ix_rbac_audit_logs_action",
        "ix_rbac_audit_logs_company_id",
    ):
        if idx_name in audit_indexes:
            op.drop_index(idx_name, table_name="rbac_audit_logs")
