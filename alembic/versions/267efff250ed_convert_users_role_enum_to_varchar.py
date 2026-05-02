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
    # =============================
    # 0. MODIFY USERS ROLE ENUM → VARCHAR
    # =============================
    op.execute("""
        ALTER TABLE users 
        MODIFY COLUMN role VARCHAR(50) NOT NULL;
    """)

    # =============================
    # 1. CREATE OFFER TABLE
    # =============================
    op.create_table(
        'redevelopment_offers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_name', sa.String(150), nullable=False),
        sa.Column('society_name', sa.String(150), nullable=False),
        sa.Column('address', sa.String(255), nullable=False),
        sa.Column('pdf_path', sa.String(255), nullable=True),
        sa.Column('developer_name', sa.String(150), nullable=False),
        sa.Column('contact_email', sa.String(150), nullable=True),
        sa.Column('contact_phone', sa.String(20), nullable=True),
        sa.Column('extra_carpet_percent', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # =============================
    # 2. ADD STATUS TO MILESTONES
    # =============================
    op.add_column(
        'milestones',
        sa.Column(
            'status',
            sa.Enum('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'DELAYED', name='milestonestatus'),
            nullable=True
        )
    )

    # =============================
    # 3. ADD created_by_user_id TO TASKS
    # =============================
    op.add_column(
        'tasks',
        sa.Column('created_by_user_id', sa.Integer(), nullable=True)
    )

    op.create_foreign_key(
        'fk_tasks_created_by_user',
        'tasks',
        'users',
        ['created_by_user_id'],
        ['id']
    )

    # =============================
    # 4. ADD INDEX
    # =============================
    op.create_index(
        'idx_task_project_status_assigned',
        'tasks',
        ['project_id', 'status', 'assigned_user_id'],
        unique=False
    )


def downgrade():
    # reverse order

    op.drop_index('idx_task_project_status_assigned', table_name='tasks')

    op.drop_constraint('fk_tasks_created_by_user', 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'created_by_user_id')

    op.drop_column('milestones', 'status')

    op.drop_table('redevelopment_offers')

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