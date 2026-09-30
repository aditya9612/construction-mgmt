"""add_rental_billing_mode

Revision ID: d2e3f4a5b6c7
Revises: c1e2f3a4b5d6
Create Date: 2026-09-23 18:01:23.731464

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from app.core.enums import RentalBillingMode

# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1e2f3a4b5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('equipment_rental', sa.Column('billing_mode', sa.Enum('PER_DAY', 'LUMP_SUM', name='rentalbillingmode'), nullable=True))


def downgrade() -> None:
    op.drop_column('equipment_rental', 'billing_mode')
