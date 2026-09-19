"""Consolidate Equipment Rental-IN feature migrations

Combined effect of:
  - ffe8b7cdc601: equipment inspections table, supplier/client linkage, enum additions
  - b2c3d4e5f6a7: invoice nullable, fuel_cost, rental-in tracking fields
  - afc6739a2ae8: VendorBill.equipment_purchase_id FK, Transaction.vendor_bill_id FK

Revision ID: c1e2f3a4b5d6
Revises: 869afae522be
Create Date: 2026-09-19 14:01:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1e2f3a4b5d6'
down_revision = '869afae522be'
branch_labels = None
depends_on = None


def upgrade():
    # -------------------------------------------------------------------------
    # 1. Create equipment_inspections table
    #    (must come before any FK that references equipment_rental)
    # -------------------------------------------------------------------------
    op.create_table(
        'equipment_inspections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('equipment_id', sa.Integer(), nullable=False),
        sa.Column('rental_id', sa.Integer(), nullable=True),
        sa.Column('inspection_date', sa.Date(), nullable=False),
        sa.Column('inspector_id', sa.Integer(), nullable=True),
        sa.Column('condition', sa.Enum('GOOD', 'REPAIR', 'DAMAGED', 'MAINTENANCE', name='equipmentcondition'), nullable=False),
        sa.Column('damage_description', sa.Text(), nullable=True),
        sa.Column('repair_cost', sa.DECIMAL(precision=10, scale=2), nullable=False),
        sa.Column('remarks', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['equipment_id'], ['equipment.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['inspector_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['rental_id'], ['equipment_rental.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_equipment_inspections_equipment_id'), 'equipment_inspections', ['equipment_id'], unique=False)
    op.create_index(op.f('ix_equipment_inspections_rental_id'), 'equipment_inspections', ['rental_id'], unique=False)

    # -------------------------------------------------------------------------
    # 2. equipment_purchase — supplier FK + rental-in tracking columns
    # -------------------------------------------------------------------------
    op.add_column('equipment_purchase', sa.Column('supplier_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_equipment_purchase_supplier_id'), 'equipment_purchase', ['supplier_id'], unique=False)
    op.create_foreign_key('fk_equip_purch_supplier', 'equipment_purchase', 'suppliers', ['supplier_id'], ['id'], ondelete='SET NULL')

    op.add_column('equipment_purchase', sa.Column('start_date', sa.Date(), nullable=True))
    op.add_column('equipment_purchase', sa.Column('expected_end_date', sa.Date(), nullable=True))
    op.add_column('equipment_purchase', sa.Column('actual_return_date', sa.Date(), nullable=True))
    op.add_column('equipment_purchase', sa.Column('is_received', sa.Boolean(), server_default='0', nullable=False))
    op.add_column('equipment_purchase', sa.Column('is_returned', sa.Boolean(), server_default='0', nullable=False))

    # -------------------------------------------------------------------------
    # 3. equipment_rental — client + invoice columns
    # -------------------------------------------------------------------------
    op.add_column('equipment_rental', sa.Column('client_id', sa.Integer(), nullable=True))
    op.add_column('equipment_rental', sa.Column('invoice_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_equipment_rental_client_id'), 'equipment_rental', ['client_id'], unique=False)
    op.create_index(op.f('ix_equipment_rental_invoice_id'), 'equipment_rental', ['invoice_id'], unique=False)
    op.create_foreign_key('fk_equip_rental_client', 'equipment_rental', 'users', ['client_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_equip_rental_invoice', 'equipment_rental', 'invoices', ['invoice_id'], ['id'], ondelete='SET NULL')

    # -------------------------------------------------------------------------
    # 4. equipment_usage — fuel_cost column
    # -------------------------------------------------------------------------
    op.add_column('equipment_usage', sa.Column('fuel_cost', sa.DECIMAL(12, 2), nullable=True))

    # -------------------------------------------------------------------------
    # 5. invoices — make project_id and owner_id nullable
    # -------------------------------------------------------------------------
    op.alter_column('invoices', 'project_id', existing_type=sa.Integer(), nullable=True)
    op.alter_column('invoices', 'owner_id', existing_type=sa.Integer(), nullable=True)

    # -------------------------------------------------------------------------
    # 6. vendor_bills — equipment_purchase FK
    # -------------------------------------------------------------------------
    op.add_column('vendor_bills', sa.Column('equipment_purchase_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_vendor_bills_equipment_purchase_id'), 'vendor_bills', ['equipment_purchase_id'], unique=False)
    op.create_foreign_key('vendor_bills_ibfk_4', 'vendor_bills', 'equipment_purchase', ['equipment_purchase_id'], ['id'], ondelete='SET NULL')

    # -------------------------------------------------------------------------
    # 7. transactions — vendor_bill FK
    # -------------------------------------------------------------------------
    op.add_column('transactions', sa.Column('vendor_bill_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_transactions_vendor_bill_id'), 'transactions', ['vendor_bill_id'], unique=False)
    op.create_foreign_key('transactions_ibfk_4', 'transactions', 'vendor_bills', ['vendor_bill_id'], ['id'], ondelete='SET NULL')

    # -------------------------------------------------------------------------
    # 8. Enum extensions (run last — no dependencies)
    # -------------------------------------------------------------------------
    op.execute("ALTER TABLE invoices MODIFY COLUMN type ENUM('OWNER', 'LABOUR', 'MATERIAL', 'CONTRACTOR', 'EQUIPMENT', 'RENTAL')")
    op.execute("ALTER TABLE equipment MODIFY COLUMN status ENUM('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', 'DAMAGED', 'INSPECTION_PENDING')")


def downgrade():
    # Reverse in strict reverse-dependency order

    # -------------------------------------------------------------------------
    # 1. Revert enum extensions first
    # -------------------------------------------------------------------------
    op.execute("ALTER TABLE invoices MODIFY COLUMN type ENUM('OWNER', 'LABOUR', 'MATERIAL', 'CONTRACTOR')")
    op.execute("ALTER TABLE equipment MODIFY COLUMN status ENUM('AVAILABLE', 'IN_PROJECT', 'IDLE', 'RENTED', 'MAINTENANCE', 'DAMAGED')")

    # -------------------------------------------------------------------------
    # 2. transactions — drop vendor_bill FK/index/column
    # -------------------------------------------------------------------------
    op.drop_constraint('transactions_ibfk_4', 'transactions', type_='foreignkey')
    op.drop_index(op.f('ix_transactions_vendor_bill_id'), table_name='transactions')
    op.drop_column('transactions', 'vendor_bill_id')

    # -------------------------------------------------------------------------
    # 3. vendor_bills — drop equipment_purchase FK/index/column
    # -------------------------------------------------------------------------
    op.drop_constraint('vendor_bills_ibfk_4', 'vendor_bills', type_='foreignkey')
    op.drop_index(op.f('ix_vendor_bills_equipment_purchase_id'), table_name='vendor_bills')
    op.drop_column('vendor_bills', 'equipment_purchase_id')

    # -------------------------------------------------------------------------
    # 4. invoices — restore NOT NULL
    # -------------------------------------------------------------------------
    op.alter_column('invoices', 'owner_id', existing_type=sa.Integer(), nullable=False)
    op.alter_column('invoices', 'project_id', existing_type=sa.Integer(), nullable=False)

    # -------------------------------------------------------------------------
    # 5. equipment_usage — drop fuel_cost
    # -------------------------------------------------------------------------
    op.drop_column('equipment_usage', 'fuel_cost')

    # -------------------------------------------------------------------------
    # 6. equipment_rental — drop client/invoice FKs/indexes/columns
    # -------------------------------------------------------------------------
    op.drop_constraint('fk_equip_rental_invoice', 'equipment_rental', type_='foreignkey')
    op.drop_constraint('fk_equip_rental_client', 'equipment_rental', type_='foreignkey')
    op.drop_index(op.f('ix_equipment_rental_invoice_id'), table_name='equipment_rental')
    op.drop_index(op.f('ix_equipment_rental_client_id'), table_name='equipment_rental')
    op.drop_column('equipment_rental', 'invoice_id')
    op.drop_column('equipment_rental', 'client_id')

    # -------------------------------------------------------------------------
    # 7. equipment_purchase — drop rental-in columns + supplier FK/index/column
    # -------------------------------------------------------------------------
    op.drop_column('equipment_purchase', 'is_returned')
    op.drop_column('equipment_purchase', 'is_received')
    op.drop_column('equipment_purchase', 'actual_return_date')
    op.drop_column('equipment_purchase', 'expected_end_date')
    op.drop_column('equipment_purchase', 'start_date')
    op.drop_constraint('fk_equip_purch_supplier', 'equipment_purchase', type_='foreignkey')
    op.drop_index(op.f('ix_equipment_purchase_supplier_id'), table_name='equipment_purchase')
    op.drop_column('equipment_purchase', 'supplier_id')

    # -------------------------------------------------------------------------
    # 8. equipment_inspections table — drop last (has FKs to other tables)
    # -------------------------------------------------------------------------
    op.drop_index(op.f('ix_equipment_inspections_rental_id'), table_name='equipment_inspections')
    op.drop_index(op.f('ix_equipment_inspections_equipment_id'), table_name='equipment_inspections')
    op.drop_table('equipment_inspections')
