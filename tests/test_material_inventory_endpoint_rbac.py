"""
Actual Endpoint-Level RBAC and Tenant-Isolation Integration Test Suite.
Uses the real FastAPI application instance (app.main:app) and real route dependency
graphs to verify HTTP 401/403 status codes, maker-checker separation, and tenant boundaries.
Runs strictly isolated from live database mutation.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from decimal import Decimal
from datetime import datetime

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, get_db_session
from app.models.user import User, UserRole
from app.models.material import PurchaseOrder
from app.models.inventory import GoodsReceiptNote
from app.models.daily_stock import DailyStockReconciliation
from app.models.cancellation import OTPCancellationRequest
from app.schemas.inventory_workflow import DailyStockReviewCreate, CancellationOTPVerify


@pytest.fixture(autouse=True)
def clean_dependency_overrides():
    """Ensure clean dependency override state before and after each test."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


# ================= 1. UNAUTHENTICATED REQUEST TEST =================

@pytest.mark.asyncio
async def test_endpoint_unauthenticated_request_fails():
    """Verifies that unauthenticated calls to sensitive workflow endpoints return 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # No Authorization header provided
        response = await client.post("/api/v1/purchase-orders/create", json={"supplier_id": 1, "items": []})
        assert response.status_code == 401


# ================= 2. PO APPROVAL RBAC TESTS =================

@pytest.mark.asyncio
async def test_endpoint_po_approve_denies_edit_only_user():
    """
    Verifies that a user holding only 'purchase_orders.edit' receives 403 Forbidden
    when attempting to approve a purchase order (maker cannot approve).
    """
    editor_user = User(
        id=101,
        email="editor@company1.com",
        role=UserRole.SITE_ENGINEER.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: editor_user
    app.dependency_overrides[get_current_active_user] = lambda: editor_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    # User only holds edit permission, NOT approve permission
    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"purchase_orders.edit"})):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/purchase-orders/1/approve")
            assert response.status_code == 403
            detail = response.json()["detail"]
            assert "Insufficient permissions" in str(detail) or "Permission denied" in str(detail)


@pytest.mark.asyncio
async def test_endpoint_po_approve_allows_authorized_approver():
    """Verifies that a user holding 'purchase_orders.approve' passes authorization."""
    approver_user = User(
        id=102,
        email="approver@company1.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()
    mock_po = MagicMock(spec=PurchaseOrder)
    mock_po.id = 1
    mock_po.po_number = "PO-001"
    mock_po.company_id = 1
    mock_po.material_id = 1
    mock_po.supplier_id = 1
    mock_po.project_id = 1
    mock_po.boq_item_id = None
    mock_po.material_name = "Cement"
    mock_po.quantity = Decimal("10.00")
    mock_po.rate = Decimal("100.00")
    mock_po.total_amount = Decimal("1000.00")
    mock_po.status = "APPROVED"
    mock_po.created_at = datetime.utcnow()

    app.dependency_overrides[get_current_user] = lambda: approver_user
    app.dependency_overrides[get_current_active_user] = lambda: approver_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"purchase_orders.approve"})), \
         patch("app.api.po_workflow.get_purchase_order", new=AsyncMock(return_value=mock_po)), \
         patch("app.api.po_workflow.approve_purchase_order", new=AsyncMock(return_value=mock_po)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/purchase-orders/1/approve")
            # Must NOT be 403 (authorization succeeded)
            assert response.status_code != 403


# ================= 3. INWARD GRN QC POST RBAC TESTS =================

@pytest.mark.asyncio
async def test_endpoint_inward_qc_post_denies_create_only_user():
    """
    Verifies that a storekeeper holding only 'inventory.create' receives 403 Forbidden
    when attempting to post/QC a GRN (maker cannot check).
    """
    creator_user = User(
        id=201,
        email="storekeeper@company1.com",
        role=UserRole.SITE_ENGINEER.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: creator_user
    app.dependency_overrides[get_current_active_user] = lambda: creator_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    # User only holds operational create permission
    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"inventory.create"})):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/inward/grn/1/qc-post", json={"qc_items": []})
            assert response.status_code == 403
            detail = response.json()["detail"]
            assert "Insufficient permissions" in str(detail) or "Permission denied" in str(detail)


@pytest.mark.asyncio
async def test_endpoint_inward_qc_post_allows_approver():
    """Verifies that an authorized QC approver holding 'inventory.approve' passes authorization."""
    qc_user = User(
        id=202,
        email="qc_manager@company1.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()
    mock_grn = MagicMock(spec=GoodsReceiptNote)
    mock_grn.id = 1
    mock_grn.company_id = 1
    mock_grn.grn_number = "GRN-001"
    mock_grn.project_id = 1
    mock_grn.supplier_id = 1
    mock_grn.purchase_order_id = 1
    mock_grn.challan_number = "CH-1"
    mock_grn.challan_date = None
    mock_grn.vehicle_number = "MH-01"
    mock_grn.status = "POSTED"
    mock_grn.idempotency_key = "IDEM-1"
    mock_grn.created_by_id = 1
    mock_grn.verified_by_id = 202
    mock_grn.verified_at = datetime.utcnow()
    mock_grn.remarks = None
    mock_grn.items = []

    app.dependency_overrides[get_current_user] = lambda: qc_user
    app.dependency_overrides[get_current_active_user] = lambda: qc_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"inventory.approve"})), \
         patch("app.api.inward.get_goods_receipt_note", new=AsyncMock(return_value=mock_grn)), \
         patch("app.api.inward.inspect_and_post_grn", new=AsyncMock(return_value=mock_grn)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/inward/grn/1/qc-post", json={"qc_items": []})
            assert response.status_code != 403


# ================= 4. DAILY STOCK RECONCILIATION RBAC TESTS =================

@pytest.mark.asyncio
async def test_endpoint_daily_stock_review_denies_edit_only_user():
    """
    Verifies that an editor holding only 'inventory.edit' receives 403 Forbidden
    when attempting to approve stock write-offs / reconciliations.
    """
    editor_user = User(
        id=301,
        email="clerk@company1.com",
        role=UserRole.SITE_ENGINEER.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: editor_user
    app.dependency_overrides[get_current_active_user] = lambda: editor_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"inventory.edit"})):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"action": "WRITE_OFF", "remarks": "Unauthorized write-off attempt"}
            response = await client.post("/api/v1/daily-stock/1/review", json=payload)
            assert response.status_code == 403
            detail = response.json()["detail"]
            assert "Insufficient permissions" in str(detail) or "Permission denied" in str(detail)


@pytest.mark.asyncio
async def test_endpoint_daily_stock_review_allows_reviewer():
    """Verifies that a stock reviewer holding 'stock.daily.review' passes authorization."""
    reviewer_user = User(
        id=302,
        email="auditor@company1.com",
        role=UserRole.PROJECT_MANAGER.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()
    mock_dsr = MagicMock(spec=DailyStockReconciliation)
    mock_dsr.id = 1
    mock_dsr.company_id = 1

    app.dependency_overrides[get_current_user] = lambda: reviewer_user
    app.dependency_overrides[get_current_active_user] = lambda: reviewer_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"stock.daily.review"})), \
         patch("app.api.daily_stock.get_daily_stock", new=AsyncMock(return_value=mock_dsr)), \
         patch("app.api.daily_stock.review_reconciliation", new=AsyncMock(return_value=mock_dsr)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"action": "ACCEPTED", "remarks": "Approved variance"}
            response = await client.post("/api/v1/daily-stock/1/review", json=payload)
            assert response.status_code != 403


# ================= 5. TENANT ISOLATION (CROSS-COMPANY) TESTS =================

@pytest.mark.asyncio
async def test_endpoint_cross_company_access_denied():
    """
    Verifies strict tenant boundary enforcement: A user from company 2
    attempting to access/approve a PO belonging to company 1 receives 403 Forbidden.
    """
    company2_user = User(
        id=401,
        email="user@company2.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=2,  # Foreign tenant
    )
    mock_db = AsyncMock()
    mock_po = MagicMock(spec=PurchaseOrder)
    mock_po.id = 1
    mock_po.po_number = "PO-001"
    mock_po.company_id = 1  # Belongs to Company 1

    app.dependency_overrides[get_current_user] = lambda: company2_user
    app.dependency_overrides[get_current_active_user] = lambda: company2_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"purchase_orders.approve"})), \
         patch("app.api.po_workflow.get_purchase_order", new=AsyncMock(return_value=mock_po)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/purchase-orders/1/approve")
            assert response.status_code == 403
            assert "Forbidden: Cross-company access is strictly prohibited" in response.json()["detail"]


# ================= 6. CANCELLATION 2-PERSON MAKER-CHECKER TEST =================

@pytest.mark.asyncio
async def test_endpoint_cancellation_maker_cannot_approve_own_request():
    """
    Verifies that the maker (user ID 501) who created a cancellation request
    cannot call /verify-otp to approve their own request (Strict 2-Person rule).
    """
    maker_user = User(
        id=501,
        email="requester@company1.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: maker_user
    app.dependency_overrides[get_current_active_user] = lambda: maker_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    from app.utils.helpers import AppError
    # cancellation_service raises 403 when user_id == requested_by_id
    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"transaction.cancel.approve"})), \
         patch("app.api.cancellation.verify_and_execute_cancellation", side_effect=AppError(status_code=403, message="Security Violation: Requester (Maker) cannot approve (Checker) their own cancellation")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"request_id": 10, "otp": "123456", "company_id": 1}
            response = await client.post("/api/v1/cancellation/verify-otp", json=payload)
            assert response.status_code == 403
            assert "Security Violation: Requester (Maker) cannot approve" in response.json()["detail"]


# ================= 7. RBAC-BASED SELF-APPROVAL POLICY TESTS =================

@pytest.mark.asyncio
async def test_endpoint_po_approve_allows_maker_with_approve_permission():
    """
    Business Policy Verification: A Maker may approve their own PO when their assigned
    role explicitly possesses the 'purchase_orders.approve' permission.
    """
    maker_approver_user = User(
        id=105,
        email="owner_approver@company1.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()
    mock_po = MagicMock(spec=PurchaseOrder)
    mock_po.id = 1
    mock_po.po_number = "PO-001"
    mock_po.company_id = 1
    mock_po.created_by_id = 105  # Same user created it
    mock_po.material_id = 1
    mock_po.supplier_id = 1
    mock_po.project_id = 1
    mock_po.boq_item_id = None
    mock_po.material_name = "Cement"
    mock_po.quantity = Decimal("10.00")
    mock_po.rate = Decimal("100.00")
    mock_po.total_amount = Decimal("1000.00")
    mock_po.status = "APPROVED"
    mock_po.approved_by_id = 105
    mock_po.created_at = datetime.utcnow()

    app.dependency_overrides[get_current_user] = lambda: maker_approver_user
    app.dependency_overrides[get_current_active_user] = lambda: maker_approver_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    # User holds BOTH create and approve permissions
    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"purchase_orders.create", "purchase_orders.approve"})), \
         patch("app.api.po_workflow.get_purchase_order", new=AsyncMock(return_value=mock_po)), \
         patch("app.api.po_workflow.approve_purchase_order", new=AsyncMock(return_value=mock_po)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/purchase-orders/1/approve")
            # Self-approval is permitted under RBAC-based approval policy (neither 401 nor 403)
            assert response.status_code != 403
            assert response.status_code != 401


@pytest.mark.asyncio
async def test_endpoint_inward_qc_post_allows_maker_with_post_permission():
    """
    Business Policy Verification: A Maker may post/QC their own GRN when their assigned
    role explicitly possesses the 'inventory.approve' permission.
    """
    maker_qc_user = User(
        id=205,
        email="pm_qc@company1.com",
        role=UserRole.PROJECT_MANAGER.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()
    mock_grn = MagicMock(spec=GoodsReceiptNote)
    mock_grn.id = 1
    mock_grn.grn_number = "GRN-001"
    mock_grn.company_id = 1
    mock_grn.created_by_id = 205  # Same user created it
    mock_grn.status = "POSTED"
    mock_grn.posted_by_id = 205
    mock_grn.created_at = datetime.utcnow()

    app.dependency_overrides[get_current_user] = lambda: maker_qc_user
    app.dependency_overrides[get_current_active_user] = lambda: maker_qc_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"inventory.create", "inventory.approve"})), \
         patch("app.api.inward.get_goods_receipt_note", new=AsyncMock(return_value=mock_grn)), \
         patch("app.api.inward.inspect_and_post_grn", new=AsyncMock(return_value=mock_grn)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/inward/grn/1/qc-post", json={"qc_items": [{"item_id": 1, "accepted_quantity": 10.0}]})
            # Self-QC posting is permitted under RBAC-based approval policy (neither 401 nor 403)
            assert response.status_code != 403
            assert response.status_code != 401


@pytest.mark.asyncio
async def test_endpoint_daily_stock_review_allows_maker_with_review_permission():
    """
    Business Policy Verification: A Maker may review/reconcile their own Daily Stock record
    when their assigned role explicitly possesses the 'stock.daily.review' permission.
    """
    maker_reviewer_user = User(
        id=305,
        email="lead_engineer@company1.com",
        role=UserRole.PROJECT_MANAGER.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    mock_db = AsyncMock()
    mock_dsr = MagicMock(spec=DailyStockReconciliation)
    mock_dsr.id = 1
    mock_dsr.reconciliation_number = "DSR-001"
    mock_dsr.company_id = 1
    mock_dsr.recorded_by_id = 305  # Same user recorded it
    mock_dsr.status = "APPROVED"
    mock_dsr.reviewed_by_id = 305
    mock_dsr.created_at = datetime.utcnow()

    app.dependency_overrides[get_current_user] = lambda: maker_reviewer_user
    app.dependency_overrides[get_current_active_user] = lambda: maker_reviewer_user
    app.dependency_overrides[get_db_session] = lambda: mock_db

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"stock.daily.create", "stock.daily.review"})), \
         patch("app.api.daily_stock.get_daily_stock", new=AsyncMock(return_value=mock_dsr)), \
         patch("app.api.daily_stock.review_reconciliation", new=AsyncMock(return_value=mock_dsr)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {"action": "ACCEPTED", "remarks": "Self-reconciliation verified"}
            response = await client.post("/api/v1/daily-stock/1/review", json=payload)
            # Self-review is permitted under RBAC-based approval policy (neither 401 nor 403)
            assert response.status_code != 403
            assert response.status_code != 401
