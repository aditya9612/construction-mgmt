"""convert users.role enum to varchar

Revision ID: 267efff250ed
Revises: 31314a7b795e
Create Date: 2026-04-28 13:19:36.001825
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '267efff250ed'
down_revision = '31314a7b795e'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE users 
        MODIFY COLUMN role VARCHAR(50) NOT NULL;
    """)


def downgrade():
    op.execute("""
        ALTER TABLE users 
        MODIFY COLUMN role ENUM(
            'Admin',
            'ProjectManager',
            'SiteEngineer',
            'Contractor',
            'Accountant'
        ) NOT NULL;
    """)
