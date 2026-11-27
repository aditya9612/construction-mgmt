"""add_company_id_to_gst_returns

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-11-27 12:30:00.000000

Additive multi-tenant migration for `gst_returns`:
- Adds nullable `company_id` referencing `companies(id)` with `ON DELETE RESTRICT`.
- Adds lookup and ordering indexes `(company_id)` and `(company_id, created_at)`.
- Preserves all legacy rows untouched (`company_id = NULL`, zero backfill/updates/deletes).
- Guards downgrade against discarding tenant ownership once non-null rows exist.
"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_offline = context.is_offline_mode()
    is_sqlite = bind.dialect.name == "sqlite"

    existing_tables: set[str] = set()
    gst_cols: set[str] = set()
    gst_fks: set[str] = set()
    gst_indexes: set[str] = set()
    inspector = None

    if not is_offline:
        try:
            inspector = sa.inspect(bind)
            existing_tables = set(inspector.get_table_names())
            if "gst_returns" not in existing_tables:
                return
            gst_cols = {c["name"] for c in inspector.get_columns("gst_returns")}
            gst_fks = {
                fk["name"]
                for fk in inspector.get_foreign_keys("gst_returns")
                if fk.get("name")
            }
            gst_indexes = {
                idx["name"]
                for idx in inspector.get_indexes("gst_returns")
                if idx.get("name")
            }
        except Exception:
            inspector = None

    if is_sqlite and not is_offline:
        with op.batch_alter_table("gst_returns") as batch_op:
            if "company_id" not in gst_cols:
                batch_op.add_column(sa.Column("company_id", sa.Integer(), nullable=True))
            if "fk_gst_returns_company_id" not in gst_fks:
                batch_op.create_foreign_key(
                    "fk_gst_returns_company_id",
                    "companies",
                    ["company_id"],
                    ["id"],
                    ondelete="RESTRICT",
                )
        if "ix_gst_returns_company_id" not in gst_indexes:
            op.create_index(
                "ix_gst_returns_company_id",
                "gst_returns",
                ["company_id"],
                unique=False,
            )
        if "ix_gst_returns_company_created" not in gst_indexes:
            op.create_index(
                "ix_gst_returns_company_created",
                "gst_returns",
                ["company_id", "created_at"],
                unique=False,
            )
        return

    if inspector is None or "company_id" not in gst_cols:
        op.add_column(
            "gst_returns",
            sa.Column("company_id", sa.Integer(), nullable=True),
        )

    if inspector is None or "ix_gst_returns_company_id" not in gst_indexes:
        op.create_index(
            "ix_gst_returns_company_id",
            "gst_returns",
            ["company_id"],
            unique=False,
        )

    if inspector is None or "ix_gst_returns_company_created" not in gst_indexes:
        op.create_index(
            "ix_gst_returns_company_created",
            "gst_returns",
            ["company_id", "created_at"],
            unique=False,
        )

    if inspector is None or "fk_gst_returns_company_id" not in gst_fks:
        op.create_foreign_key(
            "fk_gst_returns_company_id",
            "gst_returns",
            "companies",
            ["company_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_offline = context.is_offline_mode()
    is_sqlite = bind.dialect.name == "sqlite"

    if not is_offline:
        inspector = sa.inspect(bind)
        if "gst_returns" not in set(inspector.get_table_names()):
            return
        gst_cols = {c["name"] for c in inspector.get_columns("gst_returns")}
        if "company_id" not in gst_cols:
            return

        owned_count = bind.execute(
            sa.text("SELECT COUNT(*) FROM gst_returns WHERE company_id IS NOT NULL")
        ).scalar()
        if owned_count and int(owned_count) > 0:
            raise RuntimeError(
                "IrreversibleMigrationError: Cannot downgrade revision b2c3d4e5f6a7 while "
                "gst_returns contains company-owned records (company_id IS NOT NULL). "
                "Dropping company_id would discard multi-tenant ownership."
            )

        gst_fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys("gst_returns")
            if fk.get("name")
        }
        gst_indexes = {
            idx["name"]
            for idx in inspector.get_indexes("gst_returns")
            if idx.get("name")
        }
    else:
        gst_cols = {"company_id"}
        gst_fks = {"fk_gst_returns_company_id"}
        gst_indexes = {"ix_gst_returns_company_id", "ix_gst_returns_company_created"}

    if is_sqlite and not is_offline:
        if "ix_gst_returns_company_created" in gst_indexes:
            op.drop_index("ix_gst_returns_company_created", table_name="gst_returns")
        if "ix_gst_returns_company_id" in gst_indexes:
            op.drop_index("ix_gst_returns_company_id", table_name="gst_returns")
        with op.batch_alter_table("gst_returns") as batch_op:
            if "fk_gst_returns_company_id" in gst_fks:
                batch_op.drop_constraint("fk_gst_returns_company_id", type_="foreignkey")
            if "company_id" in gst_cols:
                batch_op.drop_column("company_id")
        return

    if "fk_gst_returns_company_id" in gst_fks:
        op.drop_constraint("fk_gst_returns_company_id", "gst_returns", type_="foreignkey")
    if "ix_gst_returns_company_created" in gst_indexes:
        op.drop_index("ix_gst_returns_company_created", table_name="gst_returns")
    if "ix_gst_returns_company_id" in gst_indexes:
        op.drop_index("ix_gst_returns_company_id", table_name="gst_returns")
    if "company_id" in gst_cols:
        op.drop_column("gst_returns", "company_id")
