"""Consolidate site requests mandatory columns and accountant tenant isolation (gst_returns, ai_predictions)

Revision ID: c4d5e6f7a8b9
Revises: e2f3a4b5c6d7
Create Date: 2026-09-10 17:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d5e6f7a8b9'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # Ensure all required tables exist; fail loudly if any are missing
    required_tables = {"site_requests", "gst_returns", "ai_predictions", "companies", "users", "projects"}
    missing_tables = required_tables - existing_tables
    if missing_tables:
        raise RuntimeError(
            f"Required table(s) missing for migration: {', '.join(sorted(missing_tables))}. "
            f"Halting migration."
        )

    # =========================================================================
    # PART 1: site_requests - Strict Validation & Enforce NOT NULL
    # =========================================================================
    # 1. Validate mandatory foreign key references (no silent deletion)
    null_refs_count = bind.execute(sa.text(
        "SELECT count(*) FROM site_requests WHERE project_id IS NULL OR requested_by IS NULL"
    )).scalar()
    if null_refs_count and null_refs_count > 0:
        raise RuntimeError(
            f"Found {null_refs_count} site_requests row(s) with NULL project_id or requested_by. "
            f"Halting migration without deleting records. Manual resolution required."
        )

    # 2. Validate request_type (no silent default mutation)
    null_type_count = bind.execute(sa.text(
        "SELECT count(*) FROM site_requests WHERE request_type IS NULL OR TRIM(request_type) = ''"
    )).scalar()
    if null_type_count and null_type_count > 0:
        raise RuntimeError(
            f"Found {null_type_count} site_requests row(s) with missing or empty request_type. "
            f"Halting migration. Manual resolution required."
        )

    # 3. Validate quantity (no silent rewrite of historical values)
    invalid_qty_count = bind.execute(sa.text(
        "SELECT count(*) FROM site_requests WHERE quantity IS NULL OR quantity <= 0"
    )).scalar()
    if invalid_qty_count and invalid_qty_count > 0:
        raise RuntimeError(
            f"Found {invalid_qty_count} site_requests row(s) with quantity <= 0 or NULL. "
            f"Halting migration. Manual resolution required."
        )

    # 4. Validate foreign key integrity using NOT EXISTS
    orphan_projects = bind.execute(sa.text(
        "SELECT count(*) FROM site_requests s "
        "WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.id = s.project_id)"
    )).scalar()
    if orphan_projects and orphan_projects > 0:
        raise RuntimeError(
            f"Found {orphan_projects} site_requests row(s) referencing non-existent project_id."
        )

    orphan_users = bind.execute(sa.text(
        "SELECT count(*) FROM site_requests s "
        "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = s.requested_by)"
    )).scalar()
    if orphan_users and orphan_users > 0:
        raise RuntimeError(
            f"Found {orphan_users} site_requests row(s) referencing non-existent requested_by user."
        )

    # 5. Enforce column constraints on site_requests
    op.alter_column(
        "site_requests",
        "project_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "site_requests",
        "request_type",
        existing_type=sa.String(length=50),
        nullable=False,
    )
    op.alter_column(
        "site_requests",
        "quantity",
        existing_type=sa.Float(),
        nullable=False,
    )
    op.alter_column(
        "site_requests",
        "requested_by",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "site_requests",
        "description",
        existing_type=sa.Text(),
        nullable=True,
    )

    # =========================================================================
    # PART 2: gst_returns - Deterministic Backfill & Tenant Isolation
    # =========================================================================
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("gst_returns")]

    # Step 2.1: Add company_id as nullable first if missing
    if "company_id" not in columns:
        op.add_column('gst_returns', sa.Column('company_id', sa.Integer(), nullable=True))

    # Step 2.2: Deterministic backfill via rbac_audit_logs (if present)
    if "rbac_audit_logs" in existing_tables:
        # Verify that each target_id maps to at most ONE distinct company_id
        conflicting_audit_mappings = bind.execute(sa.text("""
            SELECT count(*) FROM (
                SELECT target_id, COUNT(DISTINCT company_id) as comp_count
                FROM rbac_audit_logs
                WHERE target_type IN ('gst_returns', 'gst_return', 'GSTReturn')
                  AND company_id IS NOT NULL
                GROUP BY target_id
                HAVING COUNT(DISTINCT company_id) > 1
            ) conflicts
        """)).scalar()
        if conflicting_audit_mappings and conflicting_audit_mappings > 0:
            raise RuntimeError(
                f"Conflicting audit log company mappings detected ({conflicting_audit_mappings} target_ids have multiple distinct company_ids). "
                f"Halting migration to prevent non-deterministic tenant assignment."
            )

        # Apply deterministic mapping from audit logs
        bind.execute(sa.text("""
            UPDATE gst_returns g
            INNER JOIN (
                SELECT target_id, MAX(company_id) as single_company_id
                FROM rbac_audit_logs
                WHERE target_type IN ('gst_returns', 'gst_return', 'GSTReturn')
                  AND company_id IS NOT NULL
                GROUP BY target_id
                HAVING COUNT(DISTINCT company_id) = 1
            ) a ON a.target_id = CAST(g.id AS CHAR)
            SET g.company_id = a.single_company_id
            WHERE g.company_id IS NULL
        """))

    # Step 2.3: Single-tenant fallback (only if strictly 1 company exists in the entire system)
    total_companies = bind.execute(sa.text("SELECT count(*) FROM companies")).scalar() or 0
    if total_companies == 1:
        bind.execute(sa.text("""
            UPDATE gst_returns SET company_id = (SELECT id FROM companies LIMIT 1)
            WHERE company_id IS NULL
        """))

    # Step 2.4: Validate that no unmapped rows remain (reject guessing or cross-tenant contamination)
    unmapped_count = bind.execute(sa.text(
        "SELECT count(*) FROM gst_returns WHERE company_id IS NULL"
    )).scalar()
    if unmapped_count and unmapped_count > 0:
        raise RuntimeError(
            f"Cannot deterministically map company_id for {unmapped_count} historical gst_returns record(s). "
            f"No reliable deterministic tenant mapping found. Halting migration to protect tenant data integrity."
        )

    # Step 2.5: Validate foreign key integrity using NOT EXISTS before creating FK
    orphaned_gst = bind.execute(sa.text(
        "SELECT count(*) FROM gst_returns g "
        "WHERE g.company_id IS NOT NULL "
        "  AND NOT EXISTS (SELECT 1 FROM companies c WHERE c.id = g.company_id)"
    )).scalar()
    if orphaned_gst and orphaned_gst > 0:
        raise RuntimeError(
            f"Found {orphaned_gst} gst_returns row(s) referencing non-existent company_id. Halting migration."
        )

    # Idempotent index and foreign key creation
    inspector = sa.inspect(bind)
    existing_indexes = {i["name"] for i in inspector.get_indexes("gst_returns")}
    existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("gst_returns")}

    # Step 2.6: Create index if missing
    if "ix_gst_returns_company_id" not in existing_indexes:
        op.create_index(
            op.f('ix_gst_returns_company_id'),
            'gst_returns',
            ['company_id'],
            unique=False,
        )

    # Step 2.7: Create FK if missing
    if "fk_gst_returns_company_id" not in existing_fks:
        op.create_foreign_key(
            'fk_gst_returns_company_id',
            'gst_returns',
            'companies',
            ['company_id'],
            ['id'],
            ondelete='CASCADE',
        )

    # Step 2.8: Alter column to NOT NULL
    op.alter_column(
        'gst_returns',
        'company_id',
        existing_type=sa.Integer(),
        nullable=False,
    )

    # =========================================================================
    # PART 3: ai_predictions - Backfill, Multi-Check Validation & NOT NULL
    # =========================================================================
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("ai_predictions")]

    # Step 3.1: Add company_id as nullable first if missing
    if "company_id" not in columns:
        op.add_column('ai_predictions', sa.Column('company_id', sa.Integer(), nullable=True))

    # Step 3.2: Backfill historical rows from users.company_id via created_by_user_id
    bind.execute(sa.text("""
        UPDATE ai_predictions SET company_id = (
            SELECT users.company_id FROM users WHERE users.id = ai_predictions.created_by_user_id
        ) WHERE company_id IS NULL
    """))

    # Step 3.3: Validate that no NULL company_id remains
    null_count = bind.execute(sa.text(
        "SELECT count(*) FROM ai_predictions WHERE company_id IS NULL"
    )).scalar()
    if null_count and null_count > 0:
        raise RuntimeError(
            f"Unmapped historical ai_predictions rows detected ({null_count} rows). Halting migration."
        )

    # Step 3.4: Validate foreign key integrity using NOT EXISTS
    orphaned_ai = bind.execute(sa.text(
        "SELECT count(*) FROM ai_predictions a "
        "WHERE a.company_id IS NOT NULL "
        "  AND NOT EXISTS (SELECT 1 FROM companies c WHERE c.id = a.company_id)"
    )).scalar()
    if orphaned_ai and orphaned_ai > 0:
        raise RuntimeError(
            f"Orphaned company_id detected in ai_predictions ({orphaned_ai} rows). Halting migration."
        )

    # Re-inspect indexes and foreign keys for idempotent creation
    inspector = sa.inspect(bind)
    existing_indexes = {i["name"] for i in inspector.get_indexes("ai_predictions")}
    existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("ai_predictions")}

    # Step 3.5: Create index if missing
    if "ix_ai_predictions_company_id" not in existing_indexes:
        op.create_index(
            op.f('ix_ai_predictions_company_id'),
            'ai_predictions',
            ['company_id'],
            unique=False,
        )

    # Step 3.6: Create FK if missing
    if "fk_ai_predictions_company_id" not in existing_fks:
        op.create_foreign_key(
            'fk_ai_predictions_company_id',
            'ai_predictions',
            'companies',
            ['company_id'],
            ['id'],
            ondelete='CASCADE',
        )

    # Step 3.7: Alter column to NOT NULL
    op.alter_column(
        'ai_predictions',
        'company_id',
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # =========================================================================
    # REVERSE PART 3: ai_predictions (Drop FK -> Index -> Column)
    # =========================================================================
    if "ai_predictions" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("ai_predictions")]
        if "company_id" in columns:
            try:
                op.drop_constraint('fk_ai_predictions_company_id', 'ai_predictions', type_='foreignkey')
            except Exception:
                pass
            try:
                op.drop_index(op.f('ix_ai_predictions_company_id'), table_name='ai_predictions')
            except Exception:
                pass
            op.drop_column('ai_predictions', 'company_id')

    # =========================================================================
    # REVERSE PART 2: gst_returns (Drop FK -> Index -> Column)
    # =========================================================================
    if "gst_returns" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("gst_returns")]
        if "company_id" in columns:
            try:
                op.drop_constraint('fk_gst_returns_company_id', 'gst_returns', type_='foreignkey')
            except Exception:
                pass
            try:
                op.drop_index(op.f('ix_gst_returns_company_id'), table_name='gst_returns')
            except Exception:
                pass
            op.drop_column('gst_returns', 'company_id')

    # =========================================================================
    # REVERSE PART 1: site_requests (Revert columns to nullable)
    # =========================================================================
    if "site_requests" in existing_tables:
        op.alter_column(
            "site_requests",
            "requested_by",
            existing_type=sa.Integer(),
            nullable=True,
        )
        op.alter_column(
            "site_requests",
            "quantity",
            existing_type=sa.Float(),
            nullable=True,
        )
        op.alter_column(
            "site_requests",
            "request_type",
            existing_type=sa.String(length=50),
            nullable=True,
        )
        op.alter_column(
            "site_requests",
            "project_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
