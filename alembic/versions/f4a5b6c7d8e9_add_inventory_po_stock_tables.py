"""add_inventory_po_stock_tables

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-11-25 10:10:00.000000

"""
from typing import Sequence, Union
from decimal import Decimal

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = Inspector.from_engine(bind)
    existing_tables = set(inspector.get_table_names())

    # 1. Update purchase_orders table
    if "purchase_orders" in existing_tables:
        po_columns = {col["name"] for col in inspector.get_columns("purchase_orders")}

        if "company_id" not in po_columns:
            op.add_column("purchase_orders", sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=True))
            op.create_index("ix_purchase_orders_company_id", "purchase_orders", ["company_id"])

        if "po_number" not in po_columns:
            op.add_column("purchase_orders", sa.Column("po_number", sa.String(50), nullable=True, unique=True))
            op.create_index("ix_purchase_orders_po_number", "purchase_orders", ["po_number"])

        if "is_company_wide" not in po_columns:
            op.add_column("purchase_orders", sa.Column("is_company_wide", sa.Boolean(), nullable=False, server_default=sa.text("0")))

        if "approved_by_id" not in po_columns:
            op.add_column("purchase_orders", sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))

        if "approved_at" not in po_columns:
            op.add_column("purchase_orders", sa.Column("approved_at", sa.DateTime(), nullable=True))

        # Make single-item columns nullable for multi-item PO support
        op.alter_column("purchase_orders", "material_id", existing_type=sa.Integer(), nullable=True)
        op.alter_column("purchase_orders", "material_name", existing_type=sa.String(255), nullable=True)
        op.alter_column("purchase_orders", "quantity", existing_type=sa.DECIMAL(18, 3), nullable=True)
        op.alter_column("purchase_orders", "rate", existing_type=sa.DECIMAL(18, 2), nullable=True)
        op.alter_column("purchase_orders", "total_amount", existing_type=sa.DECIMAL(18, 2), nullable=True)
        op.alter_column("purchase_orders", "project_id", existing_type=sa.Integer(), nullable=True)

    # 2. purchase_order_items
    if "purchase_order_items" not in existing_tables:
        op.create_table(
            "purchase_order_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("material_master_id", sa.Integer(), sa.ForeignKey("material_master.id"), nullable=True, index=True),
            sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=True, index=True),
            sa.Column("material_name", sa.String(255), nullable=False),
            sa.Column("quantity", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("received_quantity", sa.DECIMAL(18, 3), nullable=False, server_default=sa.text("'0.000'")),
            sa.Column("rate", sa.DECIMAL(18, 2), nullable=False),
            sa.Column("total_amount", sa.DECIMAL(18, 2), nullable=False),
            sa.Column("unit", sa.String(50), nullable=True),
            sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'PENDING'")),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
        )

    # 3. goods_receipt_notes
    if "goods_receipt_notes" not in existing_tables:
        op.create_table(
            "goods_receipt_notes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("grn_number", sa.String(50), nullable=False, unique=True, index=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False, index=True),
            sa.Column("purchase_order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id"), nullable=True, index=True),
            sa.Column("challan_number", sa.String(100), nullable=True),
            sa.Column("challan_date", sa.Date(), nullable=True),
            sa.Column("vehicle_number", sa.String(50), nullable=True),
            sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'DRAFT'")),
            sa.Column("idempotency_key", sa.String(100), nullable=True),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("verified_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("verified_at", sa.DateTime(), nullable=True),
            sa.Column("remarks", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
            sa.UniqueConstraint("company_id", "idempotency_key", name="uq_grn_comp_idemp"),
        )

    # 4. goods_receipt_note_items
    if "goods_receipt_note_items" not in existing_tables:
        op.create_table(
            "goods_receipt_note_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("grn_id", sa.Integer(), sa.ForeignKey("goods_receipt_notes.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("po_item_id", sa.Integer(), sa.ForeignKey("purchase_order_items.id"), nullable=True, index=True),
            sa.Column("material_master_id", sa.Integer(), sa.ForeignKey("material_master.id"), nullable=False, index=True),
            sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=True, index=True),
            sa.Column("received_quantity", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("accepted_quantity", sa.DECIMAL(18, 3), nullable=False, server_default=sa.text("'0.000'")),
            sa.Column("rejected_quantity", sa.DECIMAL(18, 3), nullable=False, server_default=sa.text("'0.000'")),
            sa.Column("unit_rate", sa.DECIMAL(18, 2), nullable=False),
            sa.Column("total_amount", sa.DECIMAL(18, 2), nullable=False),
            sa.Column("batch_number", sa.String(100), nullable=True),
            sa.Column("qc_remarks", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
        )

    # 5. stock_batches
    if "stock_batches" not in existing_tables:
        op.create_table(
            "stock_batches",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("batch_number", sa.String(100), nullable=False, index=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("material_master_id", sa.Integer(), sa.ForeignKey("material_master.id"), nullable=False, index=True),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=True, index=True),
            sa.Column("grn_id", sa.Integer(), sa.ForeignKey("goods_receipt_notes.id"), nullable=True, index=True),
            sa.Column("initial_quantity", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("current_balance", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("purchase_rate", sa.DECIMAL(18, 2), nullable=False),
            sa.Column("receipt_date", sa.DateTime(), nullable=True, index=True),
            sa.Column("batch_source", sa.String(50), nullable=False, server_default=sa.text("'GRN'")),
            sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'ACTIVE'")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
        )
        op.create_index("idx_batch_proj_mat", "stock_batches", ["project_id", "material_master_id"])
        op.create_index("idx_batch_fifo", "stock_batches", ["project_id", "material_master_id", "receipt_date", "purchase_rate"])

    # 6. project_material_valuation
    if "project_material_valuation" not in existing_tables:
        op.create_table(
            "project_material_valuation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("material_master_id", sa.Integer(), sa.ForeignKey("material_master.id"), nullable=False, index=True),
            sa.Column("total_purchased_quantity", sa.DECIMAL(18, 3), nullable=False, server_default=sa.text("'0.000'")),
            sa.Column("total_purchased_amount", sa.DECIMAL(18, 2), nullable=False, server_default=sa.text("'0.00'")),
            sa.Column("current_moving_weighted_average_rate", sa.DECIMAL(18, 2), nullable=False, server_default=sa.text("'0.00'")),
            sa.Column("last_recalculated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
            sa.UniqueConstraint("project_id", "material_master_id", name="uq_proj_mat_val"),
        )

    # 7. material_issues
    if "material_issues" not in existing_tables:
        op.create_table(
            "material_issues",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("issue_number", sa.String(50), nullable=False, unique=True, index=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("material_master_id", sa.Integer(), sa.ForeignKey("material_master.id"), nullable=False, index=True),
            sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=False, index=True),
            sa.Column("quantity", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("issue_type", sa.String(50), nullable=False),
            sa.Column("issued_to", sa.String(255), nullable=True),
            sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id"), nullable=True),
            sa.Column("boq_item_id", sa.Integer(), sa.ForeignKey("boq_items.id"), nullable=True),
            sa.Column("idempotency_key", sa.String(100), nullable=True),
            sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'ISSUED'")),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("remarks", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
            sa.UniqueConstraint("company_id", "idempotency_key", name="uq_issue_comp_idemp"),
        )

    # 8. material_issue_allocations
    if "material_issue_allocations" not in existing_tables:
        op.create_table(
            "material_issue_allocations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("issue_id", sa.Integer(), sa.ForeignKey("material_issues.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("batch_id", sa.Integer(), sa.ForeignKey("stock_batches.id"), nullable=False, index=True),
            sa.Column("allocated_quantity", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("unit_rate", sa.DECIMAL(18, 2), nullable=False),
            sa.Column("total_amount", sa.DECIMAL(18, 2), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
        )

    # 9. daily_stock_reconciliation
    if "daily_stock_reconciliation" not in existing_tables:
        op.create_table(
            "daily_stock_reconciliation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("reconciliation_number", sa.String(50), nullable=False, unique=True, index=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False, index=True),
            sa.Column("material_master_id", sa.Integer(), sa.ForeignKey("material_master.id"), nullable=False, index=True),
            sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=False, index=True),
            sa.Column("reconciliation_date", sa.Date(), nullable=False, index=True),
            sa.Column("system_book_stock", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("physical_count", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("variance", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'DRAFT'")),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("review_outcome", sa.String(50), nullable=True),
            sa.Column("recorded_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("recount_reason", sa.String(255), nullable=True),
            sa.Column("remarks", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
            sa.UniqueConstraint("project_id", "material_id", "reconciliation_date", name="uq_dsr_site_mat_date"),
        )

    # 10. daily_stock_recount_history
    if "daily_stock_recount_history" not in existing_tables:
        op.create_table(
            "daily_stock_recount_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("reconciliation_id", sa.Integer(), sa.ForeignKey("daily_stock_reconciliation.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("previous_count", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("new_count", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("variance", sa.DECIMAL(18, 3), nullable=False),
            sa.Column("recounted_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("recount_notes", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
        )

    # 11. daily_stock_idempotency_log
    if "daily_stock_idempotency_log" not in existing_tables:
        op.create_table(
            "daily_stock_idempotency_log",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("idempotency_key", sa.String(100), nullable=False, index=True),
            sa.Column("endpoint", sa.String(100), nullable=False),
            sa.Column("response_status", sa.Integer(), nullable=False),
            sa.Column("response_payload", sa.String(4000), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
            sa.UniqueConstraint("company_id", "idempotency_key", name="uq_ds_idemp"),
        )

    # 12. otp_cancellation_requests
    if "otp_cancellation_requests" not in existing_tables:
        op.create_table(
            "otp_cancellation_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False, index=True),
            sa.Column("document_type", sa.String(50), nullable=False),
            sa.Column("document_id", sa.Integer(), nullable=False, index=True),
            sa.Column("document_number", sa.String(100), nullable=False),
            sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("reason", sa.String(500), nullable=False),
            sa.Column("otp_hash", sa.String(128), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
            sa.Column("status", sa.String(50), nullable=False, server_default=sa.text("'PENDING'")),
            sa.Column("verified_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("verified_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
        )
        op.create_index("idx_canc_doc", "otp_cancellation_requests", ["document_type", "document_id"])

    # 13. Safe backfill of single-item purchase orders into purchase_order_items
    op.execute(
        sa.text(
            """
            INSERT INTO purchase_order_items (
                purchase_order_id,
                material_master_id,
                material_id,
                material_name,
                quantity,
                received_quantity,
                rate,
                total_amount,
                status,
                is_deleted
            )
            SELECT
                po.id,
                m.material_master_id,
                po.material_id,
                po.material_name,
                po.quantity,
                0.000,
                po.rate,
                po.total_amount,
                po.status,
                po.is_deleted
            FROM purchase_orders po
            LEFT JOIN materials m ON po.material_id = m.id
            WHERE po.material_id IS NOT NULL
              AND po.id NOT IN (SELECT DISTINCT purchase_order_id FROM purchase_order_items)
            """
        )
    )

    # 14. Safe backfill of active materials stock into stock_batches
    op.execute(
        sa.text(
            """
            INSERT INTO stock_batches (
                batch_number,
                project_id,
                material_master_id,
                initial_quantity,
                current_balance,
                purchase_rate,
                receipt_date,
                batch_source,
                status
            )
            SELECT
                CONCAT('OB-', COALESCE(m.material_code, CAST(m.id AS CHAR))),
                m.project_id,
                m.material_master_id,
                m.remaining_stock,
                m.remaining_stock,
                IFNULL(m.purchase_rate, 0.00),
                NULL,
                'OPENING_BALANCE',
                'ACTIVE'
            FROM materials m
            WHERE m.remaining_stock > 0
              AND m.is_deleted = 0
              AND NOT EXISTS (
                  SELECT 1 FROM stock_batches sb
                  WHERE sb.project_id = m.project_id
                    AND sb.material_master_id = m.material_master_id
                    AND sb.batch_source = 'OPENING_BALANCE'
              )
            """
        )
    )

    # 15. Initialize project_material_valuation from active materials
    op.execute(
        sa.text(
            """
            INSERT INTO project_material_valuation (
                project_id,
                material_master_id,
                total_purchased_quantity,
                total_purchased_amount,
                current_moving_weighted_average_rate
            )
            SELECT
                m.project_id,
                m.material_master_id,
                SUM(m.remaining_stock),
                SUM(m.remaining_stock * IFNULL(m.purchase_rate, 0.00)),
                IFNULL(SUM(m.remaining_stock * IFNULL(m.purchase_rate, 0.00)) / NULLIF(SUM(m.remaining_stock), 0), 0.00)
            FROM materials m
            WHERE m.is_deleted = 0
              AND NOT EXISTS (
                  SELECT 1 FROM project_material_valuation pmv
                  WHERE pmv.project_id = m.project_id
                    AND pmv.material_master_id = m.material_master_id
              )
            GROUP BY m.project_id, m.material_master_id
            """
        )
    )


def downgrade() -> None:
    # Production safety guard: verify whether new tables have business data before dropping
    bind = op.get_bind()
    for table_name in [
        "otp_cancellation_requests",
        "daily_stock_recount_history",
        "daily_stock_idempotency_log",
        "daily_stock_reconciliation",
        "material_issue_allocations",
        "material_issues",
        "project_material_valuation",
        "goods_receipt_note_items",
        "stock_batches",
        "goods_receipt_notes",
        "purchase_order_items",
    ]:
        try:
            count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            if count and count > 0:
                raise RuntimeError(f"TableNotEmptyError: Cannot downgrade. Table '{table_name}' contains {count} records.")
        except Exception as e:
            if "TableNotEmptyError" in str(e):
                raise
            pass

    op.drop_table("otp_cancellation_requests")
    op.drop_table("daily_stock_recount_history")
    op.drop_table("daily_stock_idempotency_log")
    op.drop_table("daily_stock_reconciliation")
    op.drop_table("material_issue_allocations")
    op.drop_table("material_issues")
    op.drop_table("project_material_valuation")
    op.drop_table("goods_receipt_note_items")
    op.drop_table("stock_batches")
    op.drop_table("goods_receipt_notes")
    op.drop_table("purchase_order_items")
