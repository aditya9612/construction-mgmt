"""Add company_id to ai_predictions

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-10 16:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        existing_tables = set()

    if "ai_predictions" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("ai_predictions")]
        
        # Step 1: Add company_id as nullable if missing
        if "company_id" not in columns:
            op.add_column('ai_predictions', sa.Column('company_id', sa.Integer(), nullable=True))

        # Step 2: Backfill historical rows from users.company_id via created_by_user_id
        bind.execute(sa.text(
            "UPDATE ai_predictions SET company_id = ("
            "    SELECT users.company_id FROM users WHERE users.id = ai_predictions.created_by_user_id"
            ") WHERE company_id IS NULL"
        ))

        # Step 3: Validate that no NULL company_id remains
        null_count = bind.execute(sa.text(
            "SELECT count(*) FROM ai_predictions WHERE company_id IS NULL"
        )).scalar()
        if null_count and null_count > 0:
            raise RuntimeError(
                f"Unmapped historical ai_predictions rows detected ({null_count} rows). Halting migration."
            )

        # Step 4: Validate that all populated company_id exist in companies.id
        orphaned_count = bind.execute(sa.text(
            "SELECT count(*) FROM ai_predictions WHERE company_id NOT IN (SELECT id FROM companies)"
        )).scalar()
        if orphaned_count and orphaned_count > 0:
            raise RuntimeError(
                f"Orphaned company_id detected in ai_predictions ({orphaned_count} rows). Halting migration."
            )

        # Re-inspect indexes and foreign keys for idempotent creation
        inspector = sa.inspect(bind)
        existing_indexes = {i["name"] for i in inspector.get_indexes("ai_predictions")}
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("ai_predictions")}

        # Step 5: Create index if missing
        if "ix_ai_predictions_company_id" not in existing_indexes:
            op.create_index(
                op.f('ix_ai_predictions_company_id'),
                'ai_predictions',
                ['company_id'],
                unique=False,
            )

        # Step 6: Create FK if missing
        if "fk_ai_predictions_company_id" not in existing_fks:
            op.create_foreign_key(
                'fk_ai_predictions_company_id',
                'ai_predictions',
                'companies',
                ['company_id'],
                ['id'],
                ondelete='CASCADE',
            )

        # Step 7: Alter column to NOT NULL
        op.alter_column(
            'ai_predictions',
            'company_id',
            existing_type=sa.Integer(),
            nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        existing_tables = set()

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
