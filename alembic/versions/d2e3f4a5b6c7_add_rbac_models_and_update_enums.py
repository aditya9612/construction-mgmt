"""Add RBAC models and update enums

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-09-03 11:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2e3f4a5b6c7'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ---------------------------------------------------------
    # 1. Create roles table
    # ---------------------------------------------------------
    if 'roles' not in existing_tables:
        op.create_table(
            'roles',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('company_id', sa.Integer(), nullable=True),
            sa.Column('name', sa.String(length=50), nullable=False),
            sa.Column('display_name', sa.String(length=100), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('is_system', sa.Boolean(), server_default=sa.text('0'), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('company_id', 'name', name='uq_role_company_name')
        )
        op.create_index(op.f('ix_roles_company_id'), 'roles', ['company_id'], unique=False)
        op.create_index(op.f('ix_roles_name'), 'roles', ['name'], unique=False)

    # ---------------------------------------------------------
    # 2. Create user_permission_overrides table
    # ---------------------------------------------------------
    if 'user_permission_overrides' not in existing_tables:
        op.create_table(
            'user_permission_overrides',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('permission_id', sa.Integer(), nullable=False),
            sa.Column('is_granted', sa.Boolean(), server_default=sa.text('1'), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'permission_id', name='uq_user_permission_override')
        )
        op.create_index(op.f('ix_user_permission_overrides_permission_id'), 'user_permission_overrides', ['permission_id'], unique=False)
        op.create_index(op.f('ix_user_permission_overrides_user_id'), 'user_permission_overrides', ['user_id'], unique=False)

    # ---------------------------------------------------------
    # 3. Update role_permissions: add role_id and FK to roles
    # ---------------------------------------------------------
    if 'role_permissions' in existing_tables:
        rp_cols = {c['name'] for c in inspector.get_columns('role_permissions')}
        if 'role_id' not in rp_cols:
            op.add_column('role_permissions', sa.Column('role_id', sa.Integer(), nullable=True))
            op.create_foreign_key(
                'fk_role_permissions_role_id',
                'role_permissions',
                'roles',
                ['role_id'],
                ['id'],
                ondelete='CASCADE'
            )
            op.create_index(op.f('ix_role_permissions_role_id'), 'role_permissions', ['role_id'], unique=False)

    # ---------------------------------------------------------
    # 4. Update equipment.condition ENUM
    # Final values: GOOD, REPAIR, DAMAGED, MAINTENANCE
    # ---------------------------------------------------------
    op.alter_column(
        'equipment',
        'condition',
        existing_type=sa.Enum('GOOD', 'REPAIR', 'DAMAGED', name='equipmentcondition'),
        type_=sa.Enum('GOOD', 'REPAIR', 'DAMAGED', 'MAINTENANCE', name='equipmentcondition'),
        existing_nullable=True,
        nullable=True
    )

    # ---------------------------------------------------------
    # 5. Update equipment.status ENUM
    # Final values: AVAILABLE, IN_PROJECT, IDLE, RENTED, MAINTENANCE, DAMAGED
    # ---------------------------------------------------------
    op.alter_column(
        'equipment',
        'status',
        existing_type=sa.Enum('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', name='equipmentstatus'),
        type_=sa.Enum('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', 'DAMAGED', name='equipmentstatus'),
        existing_nullable=False,
        nullable=False
    )

    # ---------------------------------------------------------
    # 6. Update invoices.status ENUM
    # Final values: pending, partial, paid, CANCELLED
    # ---------------------------------------------------------
    op.alter_column(
        'invoices',
        'status',
        existing_type=sa.Enum('pending', 'partial', 'paid', name='invoicestatus'),
        type_=sa.Enum('pending', 'partial', 'paid', 'CANCELLED', name='invoicestatus'),
        existing_nullable=True,
        nullable=True,
        existing_server_default=sa.text("'pending'"),
        server_default=sa.text("'pending'")
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ---------------------------------------------------------
    # 6. Revert invoices.status ENUM
    # ---------------------------------------------------------
    op.alter_column(
        'invoices',
        'status',
        existing_type=sa.Enum('pending', 'partial', 'paid', 'CANCELLED', name='invoicestatus'),
        type_=sa.Enum('pending', 'partial', 'paid', name='invoicestatus'),
        existing_nullable=True,
        nullable=True,
        existing_server_default=sa.text("'pending'"),
        server_default=sa.text("'pending'")
    )

    # ---------------------------------------------------------
    # 5. Revert equipment.status ENUM
    # ---------------------------------------------------------
    op.alter_column(
        'equipment',
        'status',
        existing_type=sa.Enum('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', 'DAMAGED', name='equipmentstatus'),
        type_=sa.Enum('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', name='equipmentstatus'),
        existing_nullable=False,
        nullable=False
    )

    # ---------------------------------------------------------
    # 4. Revert equipment.condition ENUM
    # ---------------------------------------------------------
    op.alter_column(
        'equipment',
        'condition',
        existing_type=sa.Enum('GOOD', 'REPAIR', 'DAMAGED', 'MAINTENANCE', name='equipmentcondition'),
        type_=sa.Enum('GOOD', 'REPAIR', 'DAMAGED', name='equipmentcondition'),
        existing_nullable=True,
        nullable=True
    )

    # ---------------------------------------------------------
    # 3. Revert role_permissions: drop FK, index, and role_id
    # ---------------------------------------------------------
    if 'role_permissions' in existing_tables:
        rp_cols = {c['name'] for c in inspector.get_columns('role_permissions')}
        if 'role_id' in rp_cols:
            op.drop_index(op.f('ix_role_permissions_role_id'), table_name='role_permissions')
            op.drop_constraint('fk_role_permissions_role_id', 'role_permissions', type_='foreignkey')
            op.drop_column('role_permissions', 'role_id')

    # ---------------------------------------------------------
    # 2. Drop user_permission_overrides table
    # ---------------------------------------------------------
    if 'user_permission_overrides' in existing_tables:
        op.drop_index(op.f('ix_user_permission_overrides_user_id'), table_name='user_permission_overrides')
        op.drop_index(op.f('ix_user_permission_overrides_permission_id'), table_name='user_permission_overrides')
        op.drop_table('user_permission_overrides')

    # ---------------------------------------------------------
    # 1. Drop roles table
    # ---------------------------------------------------------
    if 'roles' in existing_tables:
        op.drop_index(op.f('ix_roles_name'), table_name='roles')
        op.drop_index(op.f('ix_roles_company_id'), table_name='roles')
        op.drop_table('roles')
