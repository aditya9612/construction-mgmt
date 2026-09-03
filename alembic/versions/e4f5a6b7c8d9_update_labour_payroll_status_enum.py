"""Update labour_payroll status enum to include DRAFT and LOCKED

Revision ID: e4f5a6b7c8d9
Revises: d2e3f4a5b6c7
Create Date: 2026-09-03 14:35:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4f5a6b7c8d9'
down_revision = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # Update labour_payroll.status ENUM
    # Expands allowed enum values to match SQLAlchemy PayrollStatus:
    # DRAFT, LOCKED, PENDING, PAID, PARTIAL
    # ---------------------------------------------------------
    op.alter_column(
        'labour_payroll',
        'status',
        existing_type=sa.Enum('PENDING', 'PAID', 'PARTIAL', name='payrollstatus'),
        type_=sa.Enum('DRAFT', 'LOCKED', 'PENDING', 'PAID', 'PARTIAL', name='payrollstatus'),
        existing_nullable=True,
        nullable=True,
    )


def downgrade() -> None:
    # ---------------------------------------------------------
    # Revert labour_payroll.status ENUM back to PENDING, PAID, PARTIAL.
    # Irreversible downgrade behavior: Under MySQL strict mode (standard in
    # production), if rows exist with status 'DRAFT' or 'LOCKED', MySQL will
    # reject the column modification (error 1265) to prevent silent truncation
    # or corruption of active business records.
    # ---------------------------------------------------------
    op.alter_column(
        'labour_payroll',
        'status',
        existing_type=sa.Enum('DRAFT', 'LOCKED', 'PENDING', 'PAID', 'PARTIAL', name='payrollstatus'),
        type_=sa.Enum('PENDING', 'PAID', 'PARTIAL', name='payrollstatus'),
        existing_nullable=True,
        nullable=True,
    )
