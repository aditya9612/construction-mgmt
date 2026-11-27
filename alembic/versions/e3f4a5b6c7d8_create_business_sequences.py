"""create_business_sequences

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-11-23 09:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    try:
        inspector = sa.inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        inspector = None
        existing_tables = set()

    if "business_sequences" not in existing_tables:
        op.create_table(
            "business_sequences",
            sa.Column("sequence_name", sa.String(length=50), primary_key=True, index=True),
            sa.Column("current_value", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
                nullable=False,
            ),
        )

        # Seed the 'MAT' sequence using the highest existing numeric material code
        # Ignores non-standard, malformed, or hyphenated codes via REGEXP
        op.execute(
            sa.text(
                "INSERT INTO business_sequences (sequence_name, current_value) "
                "SELECT 'MAT', IFNULL(MAX(CAST(SUBSTRING(material_code, 4) AS UNSIGNED)), 0) "
                "FROM materials WHERE material_code REGEXP '^MAT[0-9]+$'"
            )
        )


def downgrade() -> None:
    op.drop_table("business_sequences")
