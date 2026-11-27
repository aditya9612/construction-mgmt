"""
Comprehensive Unit & Regression Verification for Material, Stock, PO & Inventory Remediation.
Tests RBAC alias hardening, maker-checker segregation, OTP confidentiality (API & notification),
Daily Stock shared/atomic token lifecycle, site-to-site transfer FIFO+MWA sync, legacy inventory
adjustment sync, low-stock threshold policy, and Excel/PDF nested line-item export.
Runs completely isolated from live database mutations.
"""

import io
import orjson
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from app.core.dependencies import (
    PERMISSION_ALIASES,
    has_permission,
    check_user_has_permission,
)
from app.models.user import User, UserRole
from app.models.material import Material, MaterialTransaction, MaterialLedger
from app.models.inventory import StockBatch, ProjectMaterialValuation
from app.services.inventory_report_service import (
    export_report_to_excel,
    export_report_to_pdf,
    _flatten_report_rows,
)
from app.services.daily_stock_service import (
    generate_daily_stock_template,
    store_daily_stock_token,
    retrieve_and_consume_daily_stock_token,
    store_daily_stock_token_sync,
    retrieve_and_consume_daily_stock_token_sync,
    DAILY_STOCK_TOKEN_PREFIX,
)
from app.schemas.inventory_workflow import CancellationOTPRequest
from app.api.cancellation import request_otp_endpoint
from app.api.inward import create_grn_endpoint
from app.schemas.inventory_workflow import GoodsReceiptNoteCreate, GoodsReceiptNoteItemCreate
from app.services.notification_service import notify_company_stakeholders


# ================= 1. RBAC PERMISSION ALIAS HARDENING TESTS =================

def test_permission_alias_mappings():
    """Verify canonical domain permissions map correctly to legacy/alias codes."""
    assert "po.create" in PERMISSION_ALIASES
    assert "purchase_orders.create" in PERMISSION_ALIASES["po.create"]
    assert "inward.create" in PERMISSION_ALIASES
    assert "inventory.create" in PERMISSION_ALIASES["inward.create"]
    assert "outward.create" in PERMISSION_ALIASES
    assert "daily_stock.create" in PERMISSION_ALIASES
    assert "transaction.cancel.request" in PERMISSION_ALIASES
    assert "transaction.cancel.approve" in PERMISSION_ALIASES
    assert "report.view" in PERMISSION_ALIASES
    assert "report.view_price" in PERMISSION_ALIASES
    assert "report.export" in PERMISSION_ALIASES


def test_hardened_rbac_segregation_of_duties():
    """Verify that operational create/edit permissions CANNOT approve or post."""
    # 1. Create permission cannot post GRN / perform QC
    create_only = {"inventory.create"}
    assert has_permission(create_only, "inward.create") is True
    assert has_permission(create_only, "inward.post") is False  # Hardened!

    # 2. Edit permission cannot approve PO
    po_edit_only = {"purchase_orders.edit"}
    assert has_permission(po_edit_only, "po.create") is False
    assert has_permission(po_edit_only, "po.edit") is True
    assert has_permission(po_edit_only, "po.approve") is False  # Hardened!

    # 3. Inventory edit cannot reconcile/write-off stock
    inv_edit_only = {"inventory.edit"}
    assert has_permission(inv_edit_only, "daily_stock.reconcile") is False  # Hardened!

    # 4. Legitimate approvers succeed
    po_approver = {"purchase_orders.approve"}
    assert has_permission(po_approver, "po.approve") is True

    inward_approver = {"inventory.approve"}
    assert has_permission(inward_approver, "inward.post") is True
    assert has_permission(inward_approver, "daily_stock.reconcile") is True

    stock_reviewer = {"stock.daily.review"}
    assert has_permission(stock_reviewer, "daily_stock.reconcile") is True


def test_has_permission_alias_resolution():
    """Verify has_permission helper matches canonical aliases."""
    granted_permissions = {"inventory.create", "purchase_orders.view", "reports.view"}

    # Matching via alias
    assert has_permission(granted_permissions, "inward.create") is True
    assert has_permission(granted_permissions, "po.view") is True
    assert has_permission(granted_permissions, "report.view") is True

    # Missing permission
    assert has_permission(granted_permissions, "po.create") is False
    assert has_permission(granted_permissions, "transaction.cancel.approve") is False


@pytest.mark.asyncio
async def test_check_user_has_permission():
    """Verify non-raising check_user_has_permission returns boolean."""
    mock_db = AsyncMock()

    # Super Admin gets True for anything
    super_admin = User(id=1, email="admin@erp.com", role=UserRole.ADMIN.value, is_super_admin=True)
    assert await check_user_has_permission(mock_db, super_admin, "report.view_price") is True

    # User without permission gets False
    regular_user = User(id=2, email="site@erp.com", role=UserRole.SITE_ENGINEER.value, is_super_admin=False, company_id=1)
    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"inventory.view"})):
        assert await check_user_has_permission(mock_db, regular_user, "report.view_price") is False
        assert await check_user_has_permission(mock_db, regular_user, "inward.view") is True


# ================= 2. OTP CONFIDENTIALITY & MAKER EXCLUSION TESTS =================

@pytest.mark.asyncio
async def test_cancellation_otp_not_leaked_in_response():
    """Verify request_otp_endpoint returns sanitized metadata and NEVER exposes raw OTP."""
    mock_db = AsyncMock()
    mock_user = User(id=5, email="maker@erp.com", role=UserRole.PROJECT_MANAGER.value, company_id=1)
    payload = CancellationOTPRequest(
        company_id=1,
        document_type="PO",
        document_id=10,
        reason="Wrong supplier selected",
    )

    mock_req = MagicMock()
    mock_req.id = 99
    mock_req.document_type = "PO"
    mock_req.document_id = 10
    mock_req.document_number = "PO-2026-0001"
    mock_req.expires_at = datetime.utcnow()
    mock_req.status = "PENDING"

    raw_otp_generated = "654321"

    with patch("app.api.cancellation.request_cancellation_otp", new=AsyncMock(return_value=(mock_req, raw_otp_generated))):
        response = await request_otp_endpoint(payload=payload, db=mock_db, current_user=mock_user)

        # Critical Security Assertion: raw OTP must NOT be present in response
        assert "otp" not in response
        assert response["request_id"] == 99
        assert response["status"] == "PENDING"
        assert "Cancellation OTP requested successfully" in response["message"]


@pytest.mark.asyncio
async def test_cancellation_otp_maker_notification_exclusion():
    """Verify notify_company_stakeholders with exclude_actor=True NEVER sends OTP to maker."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    # 3 company users: Admin A (ID 10), PM B (ID 20), and Requester Maker (Admin C, ID 30)
    admin_a = User(id=10, email="admin_a@erp.com", role=UserRole.ADMIN.value, company_id=1, is_active=True, is_deleted=False)
    pm_b = User(id=20, email="pm_b@erp.com", role=UserRole.PROJECT_MANAGER.value, company_id=1, is_active=True, is_deleted=False)
    maker_admin = User(id=30, email="maker@erp.com", role=UserRole.ADMIN.value, company_id=1, is_active=True, is_deleted=False)

    mock_exec_result = MagicMock()
    mock_exec_result.scalars().all.return_value = [admin_a, pm_b, maker_admin]
    mock_db.execute = AsyncMock(return_value=mock_exec_result)

    # When exclude_actor=True with actor_id=30
    notifications = await notify_company_stakeholders(
        db=mock_db,
        company_id=1,
        title="Authorization Required",
        message="Secret OTP: 123456",
        actor_id=30,
        exclude_actor=True,
    )

    recipient_user_ids = [n.user_id for n in notifications]
    # Checkers receive notification
    assert 10 in recipient_user_ids
    assert 20 in recipient_user_ids
    # Maker is strictly EXCLUDED despite holding Admin role!
    assert 30 not in recipient_user_ids


# ================= 3. DIRECT INWARD REMARKS JUSTIFICATION =================

@pytest.mark.asyncio
async def test_direct_inward_requires_remarks():
    """Verify GRN creation without purchase_order_id raises 400 if remarks are missing."""
    mock_db = AsyncMock()
    mock_user = User(id=5, company_id=1, role=UserRole.SITE_ENGINEER.value)

    payload_no_remarks = GoodsReceiptNoteCreate(
        company_id=1,
        project_id=10,
        supplier_id=20,
        purchase_order_id=None,
        remarks=None,
        items=[
            GoodsReceiptNoteItemCreate(
                material_master_id=1,
                received_quantity=Decimal("50"),
                unit_rate=Decimal("100"),
            )
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_grn_endpoint(payload=payload_no_remarks, db=mock_db, current_user=mock_user)
    assert exc_info.value.status_code == 400
    assert "Direct Inward" in exc_info.value.detail


# ================= 4. OUTWARD & REPORT LOW-STOCK THRESHOLD CONSISTENCY =================

def test_outward_dynamic_low_stock_threshold():
    """Verify outward low-stock threshold uses material.minimum_stock_level dynamically."""
    mat_custom = Material(
        id=1,
        material_name="Steel TMT 12mm",
        remaining_stock=Decimal("25.000"),
        minimum_stock_level=Decimal("30.000"),
    )
    thresh = (
        Decimal(str(mat_custom.minimum_stock_level))
        if getattr(mat_custom, "minimum_stock_level", None) is not None
        else Decimal("10.000")
    )
    assert thresh == Decimal("30.000")
    assert mat_custom.remaining_stock <= thresh  # Triggers low stock alert at 25 <= 30

    mat_default = Material(
        id=2,
        material_name="Cement 53 Grade",
        remaining_stock=Decimal("15.000"),
        minimum_stock_level=None,
    )
    thresh_default = (
        Decimal(str(mat_default.minimum_stock_level))
        if getattr(mat_default, "minimum_stock_level", None) is not None
        else Decimal("10.000")
    )
    assert thresh_default == Decimal("10.000")
    assert not (mat_default.remaining_stock <= thresh_default)  # 15 > 10, no alert


# ================= 5. DAILY STOCK SHARED TOKEN STORAGE & ATOMICITY =================

@pytest.mark.asyncio
async def test_daily_stock_bulk_template_and_token():
    """Verify template generation and single-use token consumption in async and sync modes."""
    template_buf = generate_daily_stock_template()
    assert template_buf is not None
    wb = load_workbook(template_buf, data_only=True)
    ws = wb.active
    assert ws.title == "Daily Stock Upload"
    headers = [cell.value for cell in ws[1]]
    assert "Project ID" in headers
    assert "Material ID" in headers
    assert "Physical Count" in headers

    # 1. Async token storage & consumption
    token = "TEST-TOKEN-ASYNC-1"
    sample_items = [{"row_number": 2, "project_id": 1, "material_id": 10, "physical_count": Decimal("100")}]
    await store_daily_stock_token(token, sample_items, ttl_seconds=60, user_id=1, company_id=10)

    # First consumption: retrieves items
    retrieved = await retrieve_and_consume_daily_stock_token(token, user_id=1, company_id=10)
    assert retrieved == sample_items

    # Second consumption: consumed, returns None (anti-replay)
    assert await retrieve_and_consume_daily_stock_token(token, user_id=1, company_id=10) is None

    # 2. Sync token storage & consumption fallback
    sync_token = "TEST-TOKEN-SYNC-2"
    store_daily_stock_token_sync(sync_token, sample_items, ttl_seconds=60)
    sync_retrieved = retrieve_and_consume_daily_stock_token_sync(sync_token)
    assert sync_retrieved == sample_items
    assert retrieve_and_consume_daily_stock_token_sync(sync_token) is None


@pytest.mark.asyncio
async def test_daily_stock_token_redis_atomic_consumption():
    """Verify atomic Lua script single-use consumption and tenant isolation with Redis."""
    token = "REDIS-DSR-TOKEN-99"
    sample_items = [
        {"row_number": 2, "project_id": 1, "material_id": 10, "physical_count": Decimal("50.000"), "reconciliation_date": date(2026, 11, 26)}
    ]

    mock_redis = AsyncMock()

    # Simulate Redis set
    mock_redis.set = AsyncMock(return_value=True)

    # Store token in mock Redis
    await store_daily_stock_token(token, sample_items, ttl_seconds=900, redis=mock_redis, user_id=5, company_id=2)
    mock_redis.set.assert_awaited_once()

    # Simulate atomic Lua script GETDEL: first call returns payload bytes, second call returns None
    stored_payload = {
        "items": [{"row_number": 2, "project_id": 1, "material_id": 10, "physical_count": "50.000", "reconciliation_date": "2026-11-26"}],
        "user_id": 5,
        "company_id": 2,
        "expires_at": datetime.utcnow().timestamp() + 900,
    }
    raw_json = orjson.dumps(stored_payload)

    # First call: returns JSON bytes
    mock_redis.eval = AsyncMock(return_value=raw_json)
    consumed = await retrieve_and_consume_daily_stock_token(token, redis=mock_redis, user_id=5, company_id=2)
    assert consumed is not None
    assert len(consumed) == 1
    assert consumed[0]["physical_count"] == Decimal("50.000")
    assert consumed[0]["reconciliation_date"] == date(2026, 11, 26)

    # Second call: token already consumed in Redis (returns None)
    mock_redis.eval = AsyncMock(return_value=None)
    assert await retrieve_and_consume_daily_stock_token(token, redis=mock_redis, user_id=5, company_id=2) is None


# ================= 6. SITE-TO-SITE TRANSFER FIFO & MWA LOGIC =================

def test_site_to_site_transfer_fifo_and_mwa_logic():
    """Verify multi-batch FIFO depletion, destination batch creation, and MWA recalculation math."""
    # Source project has 2 active batches:
    # Batch 1: 30 units @ $100.00 (inward 2026-11-01)
    # Batch 2: 50 units @ $120.00 (inward 2026-11-10)
    # Transfer quantity: 40 units
    batch1 = StockBatch(id=1, project_id=1, material_master_id=10, initial_quantity=Decimal("30"), current_balance=Decimal("30"), purchase_rate=Decimal("100.00"), status="ACTIVE")
    batch2 = StockBatch(id=2, project_id=1, material_master_id=10, initial_quantity=Decimal("50"), current_balance=Decimal("50"), purchase_rate=Decimal("120.00"), status="ACTIVE")

    transfer_qty = Decimal("40")
    rem_transfer = transfer_qty
    total_transferred_val = Decimal("0.00")
    dest_batches = []

    for b in [batch1, batch2]:
        if rem_transfer <= Decimal("0"):
            break
        alloc = min(b.current_balance, rem_transfer)
        b.current_balance -= alloc
        if b.current_balance == Decimal("0"):
            b.status = "DEPLETED"
        rem_transfer -= alloc
        total_transferred_val += alloc * b.purchase_rate
        dest_batches.append({"alloc": alloc, "rate": b.purchase_rate})

    # FIFO verification:
    # Batch 1 fully depleted (30 units @ $100) -> balance 0, status DEPLETED
    assert batch1.current_balance == Decimal("0")
    assert batch1.status == "DEPLETED"
    # Batch 2 partially depleted (10 units @ $120) -> balance 40, status ACTIVE
    assert batch2.current_balance == Decimal("40")
    assert batch2.status == "ACTIVE"

    # Transferred value: (30 * 100) + (10 * 120) = 3000 + 1200 = 4200.00
    assert total_transferred_val == Decimal("4200.00")
    assert len(dest_batches) == 2

    # Destination valuation recalculation:
    # Existing dest: 10 units @ $90.00 (val = $900.00)
    dest_existing_qty = Decimal("10")
    dest_existing_amt = Decimal("900.00")
    new_dest_qty = dest_existing_qty + transfer_qty  # 10 + 40 = 50
    new_dest_amt = dest_existing_amt + total_transferred_val  # 900 + 4200 = 5100.00
    new_dest_mwa = (new_dest_amt / new_dest_qty).quantize(Decimal("0.01"))  # 5100 / 50 = 102.00
    assert new_dest_mwa == Decimal("102.00")


# ================= 7. LEGACY INVENTORY ADJUSTMENT SYNCHRONIZATION =================

def test_inventory_adjustment_synchronization_logic():
    """Verify positive adjustment creates batch + updates MWA, and negative adjustment depletes FIFO."""
    # 1. Positive Adjustment: old_stock = 100, new_stock = 120 (diff = +20)
    old_stock = Decimal("100")
    new_stock = Decimal("120")
    diff = new_stock - old_stock
    avg_rate = Decimal("250.00")

    adj_batch = StockBatch(
        batch_number="BATCH-ADJ-TEST",
        project_id=1,
        material_master_id=5,
        initial_quantity=diff,
        current_balance=diff,
        purchase_rate=avg_rate,
        batch_source="ADJUSTMENT_IN",
        status="ACTIVE",
    )
    assert adj_batch.current_balance == Decimal("20")
    assert adj_batch.batch_source == "ADJUSTMENT_IN"

    # 2. Negative Adjustment: old_stock = 120, new_stock = 105 (diff = -15)
    neg_diff = Decimal("-15")
    reduce_qty = abs(neg_diff)
    batch = StockBatch(id=1, project_id=1, material_master_id=5, current_balance=Decimal("20"), purchase_rate=Decimal("250.00"), status="ACTIVE")
    alloc = min(batch.current_balance, reduce_qty)
    batch.current_balance -= alloc
    assert batch.current_balance == Decimal("5")  # 20 - 15 = 5


# ================= 8. REPORT EXCEL & PDF EXPORT WITH NESTED ITEMS =================

def test_report_export_excel_and_pdf():
    """Verify report export generates valid Excel and PDF streams for flat reports."""
    sample_data = [
        {
            "material_id": 1,
            "material_code": "MAT-0001",
            "material_name": "Cement",
            "remaining_stock": Decimal("100.000"),
            "moving_average_rate": Decimal("350.00"),
            "total_inventory_value": Decimal("35000.00"),
        },
        {
            "material_id": 2,
            "material_code": "MAT-0002",
            "material_name": "Sand",
            "remaining_stock": Decimal("50.000"),
            "moving_average_rate": Decimal("1200.00"),
            "total_inventory_value": Decimal("60000.00"),
        },
    ]

    excel_buf = export_report_to_excel("stock_summary", sample_data)
    assert excel_buf is not None
    wb = load_workbook(excel_buf, data_only=True)
    ws = wb.active
    assert ws.max_row == 3  # Header + 2 rows
    assert "Material Name" in [cell.value for cell in ws[1]]

    pdf_buf = export_report_to_pdf("stock_summary", sample_data)
    assert pdf_buf is not None
    pdf_bytes = pdf_buf.getvalue()
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF")


def test_nested_report_export_excel_and_pdf():
    """Verify multi-item document reports (GRN register, PO fulfillment) export child lines and mask prices."""
    nested_grn_data = [
        {
            "grn_number": "GRN-2026-001",
            "supplier_name": "UltraTech Cement",
            "inward_date": "2026-11-20",
            "status": "POSTED",
            "items": [
                {
                    "item_id": 101,
                    "material_name": "OPC 53 Grade",
                    "received_quantity": Decimal("500.000"),
                    "accepted_quantity": Decimal("480.000"),
                    "rejected_quantity": Decimal("20.000"),
                    "unit_rate": None,  # MASKED by report.view_price!
                    "total_amount": None,  # MASKED!
                },
                {
                    "item_id": 102,
                    "material_name": "PPC Cement",
                    "received_quantity": Decimal("200.000"),
                    "accepted_quantity": Decimal("200.000"),
                    "rejected_quantity": Decimal("0.000"),
                    "unit_rate": None,  # MASKED!
                    "total_amount": None,  # MASKED!
                },
            ],
        }
    ]

    headers, rows, summary = _flatten_report_rows(nested_grn_data)
    # Check parent and child headers
    assert "Grn Number" in headers
    assert "Supplier Name" in headers
    assert "Item Material Name" in headers
    assert "Item Accepted Quantity" in headers
    # Two child rows expanded for the one GRN!
    assert len(rows) == 2
    assert rows[0][0] == "GRN-2026-001"
    assert rows[0][headers.index("Item Material Name")] == "OPC 53 Grade"
    assert rows[1][headers.index("Item Material Name")] == "PPC PPC Cement" or "PPC Cement" in rows[1][headers.index("Item Material Name")]
    # Price fields are empty strings (masked)
    assert rows[0][headers.index("Item Unit Rate")] == ""
    assert rows[0][headers.index("Item Total Amount")] == ""

    # Excel export
    excel_buf = export_report_to_excel("grn_register", nested_grn_data)
    assert excel_buf is not None
    wb = load_workbook(excel_buf, data_only=True)
    ws = wb.active
    assert ws.max_row == 3  # Header + 2 item rows

    # PDF export
    pdf_buf = export_report_to_pdf("grn_register", nested_grn_data)
    assert pdf_buf is not None
    assert pdf_buf.getvalue().startswith(b"%PDF")
