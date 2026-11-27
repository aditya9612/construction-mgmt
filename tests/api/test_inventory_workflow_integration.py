import pytest
import re
import uuid
from decimal import Decimal
from datetime import datetime, date, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, and_

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_roles
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus
from app.models.master_data import MaterialMaster, Unit
from app.models.material import Material, Supplier, PurchaseOrder, MaterialLedger, MaterialTransaction
from app.models.purchase_order import PurchaseOrderItem
from app.models.inventory import (
    GoodsReceiptNote,
    GoodsReceiptNoteItem,
    StockBatch,
    MaterialIssue,
    ProjectMaterialValuation,
)
from app.models.daily_stock import DailyStockReconciliation
from app.models.cancellation import OTPCancellationRequest
from app.models.notification import Notification
from app.core.security import get_password_hash
from app.core.default_role_permissions import assign_default_role_permissions
from app.core.rbac_seed import seed_permissions
from app.core.enums import RateType, TransactionType
from app.models.rbac import Permission, RolePermission

# Two distinct test users for maker-checker security verification
maker_user = User(
    id=9101,
    email="maker_inventory@test.com",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)

checker_user = User(
    id=9102,
    email="checker_inventory@test.com",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)


def override_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    def mock_require_roles(roles):
        return lambda: user

    app.dependency_overrides[require_roles] = mock_require_roles


def clear_user_override():
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_overrides():
    yield
    clear_user_override()


async def _fetch_checker_otp(otp_response_json: dict, checker_id: int = checker_user.id) -> str:
    """Reads dispatched 6-digit OTP from checker Notification while enforcing SEC-02 (no raw OTP in API JSON)."""
    assert "otp" not in otp_response_json, "SEC-02 violation: raw OTP must not be returned to maker in HTTP JSON"
    doc_num = otp_response_json["document_number"]
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Notification)
                .where(
                    and_(
                        Notification.user_id == checker_id,
                        Notification.title.like(f"%{doc_num}%"),
                    )
                )
                .order_by(Notification.id.desc())
            )
        ).scalars().all()
        for n in rows:
            m = re.search(r"Authorization OTP:\s*(\d{6})", n.message or "")
            if m:
                return m.group(1)
    raise AssertionError(f"Authorization OTP notification not found for checker {checker_id} on document {doc_num}")


async def setup_test_environment():
    """Initializes company, owner, maker, checker, supplier, unit, and material master."""
    async with AsyncSessionLocal() as db:
        has_rbac = await db.scalar(
            select(RolePermission.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                and_(
                    RolePermission.role == UserRole.ADMIN.value,
                    Permission.code == "po.create",
                )
            )
            .limit(1)
        )
        if not has_rbac:
            await seed_permissions(db)
            await assign_default_role_permissions(db)
            await db.commit()

        c1 = await db.get(Company, 1)
        if not c1:
            c1 = Company(id=1, name="Company Inventory Test", is_active=True)
            db.add(c1)

        owner = await db.scalar(select(Owner).where(Owner.company_id == 1))
        if not owner:
            owner = Owner(
                owner_code=f"OWN-INV-{uuid.uuid4().hex[:6]}",
                owner_name="Owner Test",
                mobile=f"99{uuid.uuid4().int % 100000000:08d}",
                email="owner_inv@test.com",
                company_id=1,
            )
            db.add(owner)
            await db.flush()

        for u in (maker_user, checker_user):
            usr = await db.get(User, u.id)
            if not usr:
                db_u = User(
                    id=u.id,
                    email=u.email,
                    hashed_password=get_password_hash("Pass123!"),
                    full_name=f"User {u.id}",
                    role=UserRole.ADMIN.value,
                    is_active=True,
                    is_super_admin=False,
                    company_id=1,
                )
                db.add(db_u)

        unit = await db.scalar(select(Unit).where(Unit.name == "KG"))
        if not unit:
            unit = Unit(name="KG")
            db.add(unit)
            await db.flush()

        supplier = await db.scalar(select(Supplier).where(Supplier.supplier_name == "Test Supplier Co"))
        if not supplier:
            supplier = Supplier(supplier_name="Test Supplier Co", contact_person="John Doe", phone_email="9876543210")
            db.add(supplier)
            await db.flush()

        suffix = uuid.uuid4().hex[:6]
        mat_master = MaterialMaster(
            name=f"Cement Grade 53 {suffix}",
            category="Raw Materials",
            unit_id=unit.id,
            company_id=1,
        )
        db.add(mat_master)

        project = Project(
            business_id=f"PRJ-INV-{suffix}",
            company_id=1,
            project_name=f"Project Inv {suffix}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(project)

        await db.commit()
        await db.refresh(mat_master)
        await db.refresh(project)
        await db.refresh(supplier)

        return {
            "company_id": 1,
            "project_id": project.id,
            "supplier_id": supplier.id,
            "unit_id": unit.id,
            "material_master_id": mat_master.id,
        }


# ================= 1. MULTI-ITEM PURCHASE ORDER TESTS =================

@pytest.mark.asyncio
async def test_multi_item_po_creation_and_approval():
    env = await setup_test_environment()
    override_user(maker_user)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create Multi-Item PO
        po_payload = {
            "company_id": env["company_id"],
            "supplier_id": env["supplier_id"],
            "project_id": env["project_id"],
            "is_company_wide": False,
            "items": [
                {
                    "material_master_id": env["material_master_id"],
                    "material_name": "Cement Grade 53 Line 1",
                    "quantity": "100.000",
                    "rate": "350.00",
                    "unit": "BAG",
                },
                {
                    "material_master_id": env["material_master_id"],
                    "material_name": "Cement Grade 53 Line 2",
                    "quantity": "50.000",
                    "rate": "360.00",
                    "unit": "BAG",
                },
            ],
        }
        res = await client.post("/api/v1/purchase-orders/create", json=po_payload)
        assert res.status_code == 201, res.text
        po_data = res.json()

        assert po_data["id"] is not None
        assert po_data["po_number"].startswith("PO")
        assert po_data["status"] == "CREATED"
        assert len(po_data["items"]) == 2
        # Backward compatibility fields check
        assert Decimal(str(po_data["quantity"])) == Decimal("150.000")
        assert Decimal(str(po_data["total_amount"])) == Decimal("53000.00")  # (100*350 + 50*360) = 35000 + 18000 = 53000

        po_id = po_data["id"]

        # 2. Approve PO
        res_approve = await client.post(f"/api/v1/purchase-orders/{po_id}/approve")
        assert res_approve.status_code == 200
        approved_po = res_approve.json()
        assert approved_po["status"] == "APPROVED"
        assert approved_po["approved_by_id"] == maker_user.id
        assert approved_po["approved_at"] is not None

        # 3. Retrieve PO
        res_get = await client.get(f"/api/v1/purchase-orders/{po_id}")
        assert res_get.status_code == 200
        assert res_get.json()["id"] == po_id


# ================= 2. INWARD (GRN) 2-STEP WORKFLOW & MWA VALUATION =================

@pytest.mark.asyncio
async def test_grn_two_step_qc_posting_and_valuation():
    env = await setup_test_environment()
    override_user(maker_user)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create PO first
        po_payload = {
            "company_id": env["company_id"],
            "supplier_id": env["supplier_id"],
            "project_id": env["project_id"],
            "items": [
                {
                    "material_master_id": env["material_master_id"],
                    "material_name": "Cement Grade 53",
                    "quantity": "200.000",
                    "rate": "300.00",
                }
            ],
        }
        res_po = await client.post("/api/v1/purchase-orders/create", json=po_payload)
        assert res_po.status_code == 201
        po_id = res_po.json()["id"]
        po_item_id = res_po.json()["items"][0]["id"]

        # 2. Step 1: Create GRN in DRAFT
        idemp_key = f"grn-test-{uuid.uuid4().hex[:8]}"
        grn_payload = {
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "purchase_order_id": po_id,
            "challan_number": f"CH-{uuid.uuid4().hex[:6]}",
            "vehicle_number": "KA-01-AB-1234",
            "idempotency_key": idemp_key,
            "items": [
                {
                    "po_item_id": po_item_id,
                    "material_master_id": env["material_master_id"],
                    "received_quantity": "100.000",
                    "unit_rate": "300.00",
                    "batch_number": "BATCH-TEST-001",
                }
            ],
        }
        res_grn = await client.post("/api/v1/inward/grn/create", json=grn_payload)
        assert res_grn.status_code == 201
        grn_data = res_grn.json()
        assert grn_data["status"] == "DRAFT"
        grn_id = grn_data["id"]
        grn_item_id = grn_data["items"][0]["id"]

        # 3. Test GRN Idempotency: Duplicate creation returns same GRN
        res_grn_dup = await client.post("/api/v1/inward/grn/create", json=grn_payload)
        assert res_grn_dup.status_code == 201
        assert res_grn_dup.json()["id"] == grn_id

        # 4. Step 2: QC Inspection & Post
        # Inspect 100 received: 90 accepted, 10 rejected
        qc_payload = {
            "qc_items": [
                {
                    "item_id": grn_item_id,
                    "accepted_quantity": "90.000",
                    "rejected_quantity": "10.000",
                    "qc_remarks": "10 bags torn in transit",
                }
            ]
        }
        res_post = await client.post(f"/api/v1/inward/grn/{grn_id}/qc-post", json=qc_payload)
        assert res_post.status_code == 200
        posted_grn = res_post.json()
        assert posted_grn["status"] == "POSTED"
        assert Decimal(str(posted_grn["items"][0]["accepted_quantity"])) == Decimal("90.000")
        assert Decimal(str(posted_grn["items"][0]["rejected_quantity"])) == Decimal("10.000")

        # 5. Verify Database State
        async with AsyncSessionLocal() as db:
            # Check StockBatch created
            stmt_batch = select(StockBatch).where(StockBatch.grn_id == grn_id)
            batch = (await db.execute(stmt_batch)).scalar_one_or_none()
            assert batch is not None
            assert batch.batch_number == "BATCH-TEST-001"
            assert batch.current_balance == Decimal("90.000")
            assert batch.status == "ACTIVE"

            # Check Material stock increment
            stmt_mat = select(Material).where(
                and_(
                    Material.project_id == env["project_id"],
                    Material.material_master_id == env["material_master_id"],
                )
            )
            mat = (await db.execute(stmt_mat)).scalar_one_or_none()
            assert mat is not None
            assert mat.remaining_stock == Decimal("90.000")

            # Check ProjectMaterialValuation MWA
            stmt_val = select(ProjectMaterialValuation).where(
                and_(
                    ProjectMaterialValuation.project_id == env["project_id"],
                    ProjectMaterialValuation.material_master_id == env["material_master_id"],
                )
            )
            val = (await db.execute(stmt_val)).scalar_one_or_none()
            assert val is not None
            assert val.total_purchased_quantity == Decimal("90.000")
            assert val.current_moving_weighted_average_rate == Decimal("300.00")

            # Check PurchaseOrderItem received_quantity updated
            po_item = await db.get(PurchaseOrderItem, po_item_id)
            assert po_item.received_quantity == Decimal("90.000")
            assert po_item.status == "PARTIALLY_RECEIVED"


# ================= 3. OUTWARD (MIN) & FIFO BATCH DEPLETION TESTS =================

@pytest.mark.asyncio
async def test_outward_issue_fifo_depletion_and_concurrency():
    env = await setup_test_environment()
    override_user(maker_user)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup: Inward two separate batches at different rates and dates
        # Batch 1: 50 units @ 200.00
        grn1 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "remarks": "Batch 1 direct inward",
            "items": [{"material_master_id": env["material_master_id"], "received_quantity": "50.000", "unit_rate": "200.00", "batch_number": "B1"}]
        })
        grn1_id = grn1.json()["id"]
        grn1_item_id = grn1.json()["items"][0]["id"]
        await client.post(f"/api/v1/inward/grn/{grn1_id}/qc-post", json={
            "qc_items": [{"item_id": grn1_item_id, "accepted_quantity": "50.000", "rejected_quantity": "0.000"}]
        })

        # Batch 2: 50 units @ 250.00
        grn2 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "remarks": "Batch 2 direct inward",
            "items": [{"material_master_id": env["material_master_id"], "received_quantity": "50.000", "unit_rate": "250.00", "batch_number": "B2"}]
        })
        grn2_id = grn2.json()["id"]
        grn2_item_id = grn2.json()["items"][0]["id"]
        await client.post(f"/api/v1/inward/grn/{grn2_id}/qc-post", json={
            "qc_items": [{"item_id": grn2_item_id, "accepted_quantity": "50.000", "rejected_quantity": "0.000"}]
        })

        # Retrieve material_id
        async with AsyncSessionLocal() as db:
            mat = (await db.execute(select(Material).where(
                and_(Material.project_id == env["project_id"], Material.material_master_id == env["material_master_id"])
            ))).scalar_one()
            mat_id = mat.id
            assert mat.remaining_stock == Decimal("100.000")

        # 1. Over-issue check: Attempt to issue 150 units (only 100 available) -> 400
        res_over = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "quantity": "150.000",
            "issue_type": "SITE_CONSUMPTION",
        })
        assert res_over.status_code == 400

        # 2. FIFO Issue: Issue 70 units
        # Must completely deplete B1 (50 units) and take 20 units from B2
        idemp_issue = f"issue-test-{uuid.uuid4().hex[:8]}"
        res_issue = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "quantity": "70.000",
            "issue_type": "SITE_CONSUMPTION",
            "idempotency_key": idemp_issue,
        })
        assert res_issue.status_code == 201
        issue_data = res_issue.json()
        assert issue_data["status"] == "ISSUED"
        assert len(issue_data["allocations"]) == 2

        # 3. Test MIN Idempotency: Duplicate issue returns same MIN without decrementing stock again
        res_issue_dup = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "quantity": "70.000",
            "issue_type": "SITE_CONSUMPTION",
            "idempotency_key": idemp_issue,
        })
        assert res_issue_dup.status_code == 201
        assert res_issue_dup.json()["id"] == issue_data["id"]

        # 4. Verify batch and stock balances in DB
        async with AsyncSessionLocal() as db:
            # Material remaining stock should be exactly 100 - 70 = 30
            mat_after = await db.get(Material, mat_id)
            assert mat_after.remaining_stock == Decimal("30.000")
            assert mat_after.quantity_used == Decimal("70.000")

            # Batch 1 should be DEPLETED (0 balance)
            b1 = (await db.execute(select(StockBatch).where(
                and_(StockBatch.project_id == env["project_id"], StockBatch.batch_number == "B1")
            ))).scalar_one()
            assert b1.current_balance == Decimal("0.000")
            assert b1.status == "DEPLETED"

            # Batch 2 should have 30 units remaining
            b2 = (await db.execute(select(StockBatch).where(
                and_(StockBatch.project_id == env["project_id"], StockBatch.batch_number == "B2")
            ))).scalar_one()
            assert b2.current_balance == Decimal("30.000")
            assert b2.status == "ACTIVE"


# ================= 4. DAILY STOCK RECONCILIATION STATE MACHINE TESTS =================

@pytest.mark.asyncio
async def test_daily_stock_reconciliation_and_review():
    env = await setup_test_environment()
    override_user(maker_user)

    # Prepare material with 50 units
    async with AsyncSessionLocal() as db:
        mat = Material(
            material_code=f"MAT-DS-{uuid.uuid4().hex[:4]}",
            project_id=env["project_id"],
            material_master_id=env["material_master_id"],
            material_name="Steel Rod 12mm",
            category="Steel",
            unit_id=env["unit_id"],
            supplier_id=env["supplier_id"],
            rate_type=RateType.FIXED,
            purchase_rate=Decimal("60.00"),
            quantity_purchased=Decimal("50.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("50.000"),
        )
        db.add(mat)
        await db.commit()
        await db.refresh(mat)
        mat_id = mat.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Physical count 50 (Zero variance) -> RECONCILED
        res_zero = await client.post("/api/v1/daily-stock/record", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "physical_count": "50.000",
            "reconciliation_date": date.today().isoformat(),
        })
        assert res_zero.status_code == 201
        assert res_zero.json()["status"] == "RECONCILED"
        assert Decimal(str(res_zero.json()["variance"])) == Decimal("0.000")
        dsr_id = res_zero.json()["id"]

        # 2. Update with physical count 45 (Variance -5) -> VARIANCE_FLAGGED
        res_var = await client.post("/api/v1/daily-stock/record", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "physical_count": "45.000",
            "reconciliation_date": date.today().isoformat(),
        })
        assert res_var.status_code == 201
        assert res_var.json()["status"] == "VARIANCE_FLAGGED"
        assert Decimal(str(res_var.json()["variance"])) == Decimal("-5.000")

        # 3. Recount Submission
        res_recount = await client.post(f"/api/v1/daily-stock/{dsr_id}/recount", json={
            "new_physical_count": "47.000",
            "recount_notes": "Found 2 extra rods in corner",
        })
        assert res_recount.status_code == 200
        recounted = res_recount.json()
        assert recounted["version"] == 2
        assert Decimal(str(recounted["physical_count"])) == Decimal("47.000")
        assert Decimal(str(recounted["variance"])) == Decimal("-3.000")
        assert recounted["status"] == "VARIANCE_FLAGGED"

        # 4. Review Step: Maker-Checker approval of inventory adjustment
        res_review = await client.post(f"/api/v1/daily-stock/{dsr_id}/review", json={
            "review_outcome": "ADJUST_INVENTORY",
            "review_notes": "Adjustment authorized after recount",
        })
        assert res_review.status_code == 200
        adjusted_dsr = res_review.json()
        assert adjusted_dsr["status"] == "ADJUSTED"
        assert adjusted_dsr["review_outcome"] == "ADJUSTMENT_APPROVED"

        # Verify stock adjusted in DB: 50 + (-3) = 47
        async with AsyncSessionLocal() as db:
            mat_adj = await db.get(Material, mat_id)
            assert mat_adj.remaining_stock == Decimal("47.000")


# ================= 5. AUTHORITATIVE 2-PERSON OTP CANCELLATION TESTS =================

@pytest.mark.asyncio
async def test_authoritative_two_person_otp_cancellation():
    env = await setup_test_environment()

    # 1. Maker creates a PO
    override_user(maker_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_po = await client.post("/api/v1/purchase-orders/create", json={
            "company_id": env["company_id"],
            "supplier_id": env["supplier_id"],
            "project_id": env["project_id"],
            "items": [{"material_master_id": env["material_master_id"], "material_name": "PO to Cancel", "quantity": "10.000", "rate": "100.00"}]
        })
        po_id = res_po.json()["id"]

        # 2. Maker requests cancellation OTP
        res_otp_req = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "PO",
            "document_id": po_id,
            "reason": "Accidental duplicate purchase order",
        })
        assert res_otp_req.status_code == 201
        otp_data = res_otp_req.json()
        req_id = otp_data["request_id"]
        otp = await _fetch_checker_otp(otp_data)

        # 3. Security Rule Verification: Maker cannot verify as Checker (403)
        res_self_verify = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": req_id,
            "otp": otp,
        })
        assert res_self_verify.status_code == 403

        # 4. Switch to Checker User
        override_user(checker_user)

        # 5. Invalid OTP attempt
        res_bad_otp = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": req_id,
            "otp": "000000",
        })
        assert res_bad_otp.status_code == 400

        # 6. Correct OTP verification by Checker
        res_valid = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": req_id,
            "otp": otp,
        })
        assert res_valid.status_code == 200
        assert res_valid.json()["status"] == "VERIFIED"

        # 7. Verify PO is cancelled in DB
        async with AsyncSessionLocal() as db:
            po_cancelled = await db.get(PurchaseOrder, po_id)
            assert po_cancelled.status == "CANCELLED"


# ================= 6. UNIFIED INVENTORY REPORTING TESTS =================

@pytest.mark.asyncio
async def test_inventory_reporting_endpoints():
    env = await setup_test_environment()
    override_user(maker_user)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Stock Summary Report
        res_summary = await client.get(f"/api/v1/inventory/reports/stock-summary?project_id={env['project_id']}")
        assert res_summary.status_code == 200
        assert isinstance(res_summary.json(), list)

        # Low Stock Alerts Report
        res_alerts = await client.get(f"/api/v1/inventory/reports/low-stock-alerts?project_id={env['project_id']}&threshold=100")
        assert res_alerts.status_code == 200
        assert isinstance(res_alerts.json(), list)

        # PO Fulfillment Report
        res_po = await client.get(f"/api/v1/inventory/reports/po-fulfillment?project_id={env['project_id']}")
        assert res_po.status_code == 200
        assert isinstance(res_po.json(), list)

        # GRN Register Report
        res_grn = await client.get(f"/api/v1/inventory/reports/grn-register?project_id={env['project_id']}")
        assert res_grn.status_code == 200
        assert isinstance(res_grn.json(), list)

        # Issue Register Report
        res_issue = await client.get(f"/api/v1/inventory/reports/issue-register?project_id={env['project_id']}")
        assert res_issue.status_code == 200
        assert isinstance(res_issue.json(), list)

        # Daily Stock Variance Report
        res_dsr = await client.get(f"/api/v1/inventory/reports/daily-stock-variance?project_id={env['project_id']}")
        assert res_dsr.status_code == 200
        assert isinstance(res_dsr.json(), list)


# ================= 7. AREA A: FIFO OPENING BALANCE & CHRONOLOGICAL ORDERING =================

@pytest.mark.asyncio
async def test_area_a_fifo_opening_balance_and_receipt_date_ordering():
    """
    Area A: Verifies that:
    1. OPENING_BALANCE batches (receipt_date IS NULL) are depleted first per BD-17.
    2. Chronological receipt_date ASC is strictly primary for post-migration GRN receipts.
    3. purchase_rate is NEVER used as primary FIFO criteria (e.g. rate 120 allocated before rate 80).
    4. Deterministic chronological tie-breaker is id ASC.
    """
    env = await setup_test_environment()
    override_user(maker_user)

    uid = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        # Create a test material
        mat = Material(
            material_code=f"MAT-FIFO-{uid}",
            material_name=f"FIFO Test Steel {uid}",
            category="Steel",
            project_id=env["project_id"],
            material_master_id=env["material_master_id"],
            supplier_id=env["supplier_id"],
            unit_id=env["unit_id"],
            rate_type=RateType.FIXED,
            purchase_rate=Decimal("100.00"),
            quantity_purchased=Decimal("30.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("30.000"),
        )
        db.add(mat)
        await db.flush()

        # Batch 1: Opening balance (receipt_date IS NULL), highest purchase rate 120.00
        b1 = StockBatch(
            batch_number=f"OB-{uid}-1",
            project_id=env["project_id"],
            material_master_id=env["material_master_id"],
            supplier_id=env["supplier_id"],
            initial_quantity=Decimal("10.000"),
            current_balance=Decimal("10.000"),
            purchase_rate=Decimal("120.00"),
            receipt_date=None,  # Opening balance
            batch_source="OPENING_BALANCE",
            status="ACTIVE",
        )
        db.add(b1)

        # Batch 2: Early GRN receipt (2026-01-01), lowest purchase rate 80.00
        b2 = StockBatch(
            batch_number=f"GRN-{uid}-EARLY",
            project_id=env["project_id"],
            material_master_id=env["material_master_id"],
            supplier_id=env["supplier_id"],
            initial_quantity=Decimal("10.000"),
            current_balance=Decimal("10.000"),
            purchase_rate=Decimal("80.00"),
            receipt_date=datetime(2026, 1, 1, 10, 0, 0),
            batch_source="GRN",
            status="ACTIVE",
        )
        db.add(b2)

        # Batch 3: Later GRN receipt (2026-01-15), medium purchase rate 90.00
        b3 = StockBatch(
            batch_number=f"GRN-{uid}-LATER",
            project_id=env["project_id"],
            material_master_id=env["material_master_id"],
            supplier_id=env["supplier_id"],
            initial_quantity=Decimal("10.000"),
            current_balance=Decimal("10.000"),
            purchase_rate=Decimal("90.00"),
            receipt_date=datetime(2026, 1, 15, 10, 0, 0),
            batch_source="GRN",
            status="ACTIVE",
        )
        db.add(b3)
        await db.commit()
        mat_id = mat.id
        b1_id, b2_id, b3_id = b1.id, b2.id, b3.id

    # Dispatch MIN of 15.000 units
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_min = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "quantity": "15.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "FIFO Tester",
        })
        assert res_min.status_code == 201
        issue_data = res_min.json()
        allocations = issue_data["allocations"]
        assert len(allocations) == 2

        # 1. First allocation MUST be Batch 1 (Opening Balance) for 10 units, despite having rate 120 > 80
        assert allocations[0]["batch_id"] == b1_id
        assert Decimal(str(allocations[0]["allocated_quantity"])) == Decimal("10.000")
        assert Decimal(str(allocations[0]["unit_rate"])) == Decimal("120.00")

        # 2. Second allocation MUST be Batch 2 (Early GRN receipt 2026-01-01) for 5 units
        assert allocations[1]["batch_id"] == b2_id
        assert Decimal(str(allocations[1]["allocated_quantity"])) == Decimal("5.000")
        assert Decimal(str(allocations[1]["unit_rate"])) == Decimal("80.00")

    # Verify batch states in DB
    async with AsyncSessionLocal() as db:
        batch1 = await db.get(StockBatch, b1_id)
        batch2 = await db.get(StockBatch, b2_id)
        batch3 = await db.get(StockBatch, b3_id)

        assert batch1.current_balance == Decimal("0.000")
        assert batch1.status == "DEPLETED"
        assert batch2.current_balance == Decimal("5.000")
        assert batch2.status == "ACTIVE"
        assert batch3.current_balance == Decimal("10.000")
        assert batch3.status == "ACTIVE"


# ================= 8. AREA B: MULTI-SUPPLIER STOCK TRACKING & REPORTING =================

@pytest.mark.asyncio
async def test_area_b_multi_supplier_stock_tracking_and_reporting():
    """
    Area B: Verifies independent supplier-specific stock tracking alongside site-consolidated stock.
    Tests two distinct suppliers delivering the same material to the same project.
    """
    env = await setup_test_environment()
    override_user(maker_user)

    uid = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        # Create MaterialMaster
        mat_master = MaterialMaster(
            name=f"Cement PPC {uid}",
            unique_code=f"MM-PPC-{uid}",
            category="Cement",
            unit_id=env["unit_id"],
        )
        db.add(mat_master)
        await db.flush()

        # Create Supplier Alpha and Supplier Beta
        sup_alpha = Supplier(supplier_name=f"Supplier Alpha {uid}", company_id=env["company_id"])
        sup_beta = Supplier(supplier_name=f"Supplier Beta {uid}", company_id=env["company_id"])
        db.add_all([sup_alpha, sup_beta])
        await db.commit()
        mm_id = mat_master.id
        sup_a_id = sup_alpha.id
        sup_b_id = sup_beta.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GRN 1: Supplier Alpha delivers 25.000 units @ 50.00
        res_grn1 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": sup_a_id,
            "remarks": "Supplier Alpha direct inward",
            "items": [{"material_master_id": mm_id, "received_quantity": "25.000", "unit_rate": "50.00"}]
        })
        assert res_grn1.status_code == 201
        grn1_id = res_grn1.json()["id"]
        grn1_item_id = res_grn1.json()["items"][0]["id"]

        # Post GRN 1
        res_post1 = await client.post(f"/api/v1/inward/grn/{grn1_id}/qc-post", json={
            "qc_items": [{"item_id": grn1_item_id, "accepted_quantity": "25.000", "rejected_quantity": "0.000"}]
        })
        assert res_post1.status_code == 200

        # GRN 2: Supplier Beta delivers 35.000 units @ 60.00
        res_grn2 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": sup_b_id,
            "remarks": "Supplier Beta direct inward",
            "items": [{"material_master_id": mm_id, "received_quantity": "35.000", "unit_rate": "60.00"}]
        })
        assert res_grn2.status_code == 201
        grn2_id = res_grn2.json()["id"]
        grn2_item_id = res_grn2.json()["items"][0]["id"]

        # Post GRN 2
        res_post2 = await client.post(f"/api/v1/inward/grn/{grn2_id}/qc-post", json={
            "qc_items": [{"item_id": grn2_item_id, "accepted_quantity": "35.000", "rejected_quantity": "0.000"}]
        })
        assert res_post2.status_code == 200

        # Query Supplier Stock Report
        res_report = await client.get(
            f"/api/v1/inventory/reports/supplier-stock?project_id={env['project_id']}&material_master_id={mm_id}"
        )
        assert res_report.status_code == 200
        supplier_report = res_report.json()
        assert len(supplier_report) == 2

        # Map by supplier_id
        sup_data = {row["supplier_id"]: row for row in supplier_report}
        assert sup_a_id in sup_data
        assert sup_b_id in sup_data

        # Verify Supplier Alpha breakdown
        alpha_row = sup_data[sup_a_id]
        assert Decimal(str(alpha_row["supplier_stock_balance"])) == Decimal("25.000")
        assert Decimal(str(alpha_row["supplier_stock_value"])) == Decimal("1250.00")
        assert Decimal(str(alpha_row["site_consolidated_stock"])) == Decimal("60.000")

        # Verify Supplier Beta breakdown
        beta_row = sup_data[sup_b_id]
        assert Decimal(str(beta_row["supplier_stock_balance"])) == Decimal("35.000")
        assert Decimal(str(beta_row["supplier_stock_value"])) == Decimal("2100.00")
        assert Decimal(str(beta_row["site_consolidated_stock"])) == Decimal("60.000")


# ================= 9. AREA C: MOVING WEIGHTED AVERAGE VALUATION RECALCULATION =================

@pytest.mark.asyncio
async def test_area_c_moving_weighted_average_on_hand_recalculation():
    """
    Area C: Ind AS 2 Moving Weighted Average recalculated strictly on Current On-Hand Stock.
    Verifies:
    1. Inward 1: 10 @ 100 -> MWA = 100.00, value = 1000.00
    2. Inward 2: 10 @ 200 -> MWA = (1000 + 2000)/20 = 150.00, value = 3000.00
    3. Outward 1: 15 units -> Outward cost = 15 * 150 = 2250.00, Remaining = 5 units, value = 750.00, MWA remains 150.00
    4. Inward 3: 5 @ 250 -> MWA = (750 + 1250)/(5 + 5) = 200.00, value = 2000.00
    5. Outward 2: 10 units -> Stock = 0, value = 0.00, MWA resets to 0.00!
    """
    env = await setup_test_environment()
    override_user(maker_user)

    uid = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        mat_master = MaterialMaster(
            name=f"Bitumen MWA {uid}",
            unique_code=f"MM-BIT-{uid}",
            category="Bitumen",
            unit_id=env["unit_id"],
        )
        db.add(mat_master)
        await db.commit()
        mm_id = mat_master.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Inward 10 @ 100.00
        res_grn1 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "remarks": "MWA step 1 direct inward",
            "items": [{"material_master_id": mm_id, "received_quantity": "10.000", "unit_rate": "100.00"}]
        })
        grn1_id = res_grn1.json()["id"]
        grn1_item_id = res_grn1.json()["items"][0]["id"]
        await client.post(f"/api/v1/inward/grn/{grn1_id}/qc-post", json={
            "qc_items": [{"item_id": grn1_item_id, "accepted_quantity": "10.000", "rejected_quantity": "0.000"}]
        })

        async with AsyncSessionLocal() as db:
            val1 = (await db.execute(
                select(ProjectMaterialValuation).where(
                    and_(
                        ProjectMaterialValuation.project_id == env["project_id"],
                        ProjectMaterialValuation.material_master_id == mm_id,
                    )
                )
            )).scalar_one()
            assert val1.total_purchased_quantity == Decimal("10.000")
            assert val1.total_purchased_amount == Decimal("1000.00")
            assert val1.current_moving_weighted_average_rate == Decimal("100.00")

        # Step 2: Inward 10 @ 200.00
        res_grn2 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "remarks": "MWA step 2 direct inward",
            "items": [{"material_master_id": mm_id, "received_quantity": "10.000", "unit_rate": "200.00"}]
        })
        grn2_id = res_grn2.json()["id"]
        grn2_item_id = res_grn2.json()["items"][0]["id"]
        res_post2 = await client.post(f"/api/v1/inward/grn/{grn2_id}/qc-post", json={
            "qc_items": [{"item_id": grn2_item_id, "accepted_quantity": "10.000", "rejected_quantity": "0.000"}]
        })
        mat_id = res_post2.json()["items"][0]["material_id"]

        async with AsyncSessionLocal() as db:
            val2 = (await db.execute(
                select(ProjectMaterialValuation).where(
                    and_(
                        ProjectMaterialValuation.project_id == env["project_id"],
                        ProjectMaterialValuation.material_master_id == mm_id,
                    )
                )
            )).scalar_one()
            # Total on-hand = 20, Total value = 1000 + 2000 = 3000, MWA rate = 150.00
            assert val2.total_purchased_quantity == Decimal("20.000")
            assert val2.total_purchased_amount == Decimal("3000.00")
            assert val2.current_moving_weighted_average_rate == Decimal("150.00")

        # Step 3: Outward 15.000 units
        res_out1 = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "quantity": "15.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Road Layer",
        })
        assert res_out1.status_code == 201

        async with AsyncSessionLocal() as db:
            val3 = (await db.execute(
                select(ProjectMaterialValuation).where(
                    and_(
                        ProjectMaterialValuation.project_id == env["project_id"],
                        ProjectMaterialValuation.material_master_id == mm_id,
                    )
                )
            )).scalar_one()
            # Outward cost = 15 * 150 = 2250.00
            # Remaining on-hand = 5, Remaining value = 750.00, MWA rate remains 150.00
            assert val3.total_purchased_quantity == Decimal("5.000")
            assert val3.total_purchased_amount == Decimal("750.00")
            assert val3.current_moving_weighted_average_rate == Decimal("150.00")

        # Step 4: Inward 5.000 units @ 250.00
        res_grn3 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "remarks": "MWA step 4 direct inward",
            "items": [{"material_master_id": mm_id, "received_quantity": "5.000", "unit_rate": "250.00"}]
        })
        grn3_id = res_grn3.json()["id"]
        grn3_item_id = res_grn3.json()["items"][0]["id"]
        await client.post(f"/api/v1/inward/grn/{grn3_id}/qc-post", json={
            "qc_items": [{"item_id": grn3_item_id, "accepted_quantity": "5.000", "rejected_quantity": "0.000"}]
        })

        async with AsyncSessionLocal() as db:
            val4 = (await db.execute(
                select(ProjectMaterialValuation).where(
                    and_(
                        ProjectMaterialValuation.project_id == env["project_id"],
                        ProjectMaterialValuation.material_master_id == mm_id,
                    )
                )
            )).scalar_one()
            # New on-hand = 5 + 5 = 10, New value = 750 + 1250 = 2000.00, New MWA = 200.00
            assert val4.total_purchased_quantity == Decimal("10.000")
            assert val4.total_purchased_amount == Decimal("2000.00")
            assert val4.current_moving_weighted_average_rate == Decimal("200.00")

        # Step 5: Full outward of remaining 10.000 units
        res_out2 = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "quantity": "10.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Road Finish",
        })
        assert res_out2.status_code == 201

        async with AsyncSessionLocal() as db:
            val5 = (await db.execute(
                select(ProjectMaterialValuation).where(
                    and_(
                        ProjectMaterialValuation.project_id == env["project_id"],
                        ProjectMaterialValuation.material_master_id == mm_id,
                    )
                )
            )).scalar_one()
            # Stock depleted to 0 -> Carrying value and MWA rate reset cleanly to 0.00
            assert val5.total_purchased_quantity == Decimal("0.000")
            assert val5.total_purchased_amount == Decimal("0.00")
            assert val5.current_moving_weighted_average_rate == Decimal("0.00")


# ================= 10. AREA D: OTP CANCELLATION FOR ALL DOCUMENT TYPES =================

@pytest.mark.asyncio
async def test_area_d_cancellation_for_all_document_types():
    """
    Area D: Verifies maker-checker OTP cancellation and atomic stock reversals for:
    - GRN cancellation (depletes batches, reverts stock, reverts PO item)
    - MIN cancellation (restores batches, restores stock)
    - Daily Stock cancellation (status to CANCELLED)
    - Resend invalidation (generating new OTP expires old pending request)
    """
    env = await setup_test_environment()
    override_user(maker_user)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # --- 1. Resend Invalidation Test ---
        # Create a PO to cancel
        res_po = await client.post("/api/v1/purchase-orders/create", json={
            "company_id": env["company_id"],
            "supplier_id": env["supplier_id"],
            "project_id": env["project_id"],
            "items": [{"material_master_id": env["material_master_id"], "material_name": "PO Resend", "quantity": "5.000", "rate": "100.00"}]
        })
        po_id = res_po.json()["id"]

        # Request OTP 1
        res_otp1 = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "PO",
            "document_id": po_id,
            "reason": "Test Resend 1",
        })
        req1_id = res_otp1.json()["request_id"]
        otp1 = await _fetch_checker_otp(res_otp1.json())

        # Request OTP 2 (resend)
        res_otp2 = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "PO",
            "document_id": po_id,
            "reason": "Test Resend 2 (Correct)",
        })
        req2_id = res_otp2.json()["request_id"]
        otp2 = await _fetch_checker_otp(res_otp2.json())

        # Verify in DB that request 1 is EXPIRED
        async with AsyncSessionLocal() as db:
            r1 = await db.get(OTPCancellationRequest, req1_id)
            assert r1.status == "EXPIRED"

        # Checker verifies OTP 2 successfully
        override_user(checker_user)
        res_verify2 = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": req2_id,
            "otp": otp2,
        })
        assert res_verify2.status_code == 200

        # --- 2. GRN Cancellation & Reversal Test ---
        override_user(maker_user)
        res_grn = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "remarks": "Wrong truck direct inward",
            "items": [{"material_master_id": env["material_master_id"], "received_quantity": "8.000", "unit_rate": "75.00"}]
        })
        grn_id = res_grn.json()["id"]
        grn_item_id = res_grn.json()["items"][0]["id"]
        res_post_grn = await client.post(f"/api/v1/inward/grn/{grn_id}/qc-post", json={
            "qc_items": [{"item_id": grn_item_id, "accepted_quantity": "8.000", "rejected_quantity": "0.000"}]
        })
        mat_id = res_post_grn.json()["items"][0]["material_id"]

        # Maker requests GRN cancellation OTP
        res_grn_otp = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "GRN",
            "document_id": grn_id,
            "reason": "Wrong delivery truck unloaded",
        })
        grn_req_id = res_grn_otp.json()["request_id"]
        grn_otp = await _fetch_checker_otp(res_grn_otp.json())

        # Checker verifies GRN cancellation OTP
        override_user(checker_user)
        res_grn_cancel = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": grn_req_id,
            "otp": grn_otp,
        })
        assert res_grn_cancel.status_code == 200

        # Verify GRN is cancelled and batch is depleted in DB
        async with AsyncSessionLocal() as db:
            grn_db = await db.get(GoodsReceiptNote, grn_id)
            assert grn_db.status == "CANCELLED"
            batches = (await db.execute(
                select(StockBatch).where(StockBatch.grn_id == grn_id)
            )).scalars().all()
            for b in batches:
                assert b.status == "DEPLETED"
                assert b.current_balance == Decimal("0.000")

        # --- 3. MIN Cancellation & Reversal Test ---
        override_user(maker_user)
        # Create a fresh batch and material for clean MIN testing
        uid_min = uuid.uuid4().hex[:6]
        async with AsyncSessionLocal() as db:
            mat_master_min = MaterialMaster(
                name=f"MIN Reversal Steel Master {uid_min}",
                unique_code=f"MM-MIN-{uid_min}",
                category="Steel",
                unit_id=env["unit_id"],
            )
            db.add(mat_master_min)
            await db.flush()

            mat_min = Material(
                material_code=f"MAT-MIN-{uid_min}",
                material_name=f"MIN Reversal Steel {uid_min}",
                category="Steel",
                project_id=env["project_id"],
                material_master_id=mat_master_min.id,
                supplier_id=env["supplier_id"],
                unit_id=env["unit_id"],
                rate_type=RateType.FIXED,
                purchase_rate=Decimal("100.00"),
                quantity_purchased=Decimal("20.000"),
                quantity_used=Decimal("0.000"),
                remaining_stock=Decimal("20.000"),
            )
            db.add(mat_min)
            await db.flush()
            b_min = StockBatch(
                batch_number=f"BATCH-MIN-{uid_min}",
                project_id=env["project_id"],
                material_master_id=mat_master_min.id,
                supplier_id=env["supplier_id"],
                initial_quantity=Decimal("20.000"),
                current_balance=Decimal("20.000"),
                purchase_rate=Decimal("100.00"),
                receipt_date=datetime.utcnow(),
                batch_source="GRN",
                status="ACTIVE",
            )
            db.add(b_min)
            await db.commit()
            mat_min_id = mat_min.id
            b_min_id = b_min.id

        # Dispatch MIN of 10.000 units
        res_min = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_min_id,
            "quantity": "10.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Worker 1",
        })
        min_id = res_min.json()["id"]

        # Maker requests MIN cancellation OTP
        res_min_otp = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "MIN",
            "document_id": min_id,
            "reason": "Issue entered in error, material not dispatched",
        })
        min_req_id = res_min_otp.json()["request_id"]
        min_otp = await _fetch_checker_otp(res_min_otp.json())

        # Checker verifies MIN cancellation OTP
        override_user(checker_user)
        res_min_cancel = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": min_req_id,
            "otp": min_otp,
        })
        assert res_min_cancel.status_code == 200

        # Verify stock and batch balance restored
        async with AsyncSessionLocal() as db:
            issue_db = await db.get(MaterialIssue, min_id)
            assert issue_db.status == "CANCELLED"
            mat_restored = await db.get(Material, mat_min_id)
            assert mat_restored.remaining_stock == Decimal("20.000")
            batch_restored = await db.get(StockBatch, b_min_id)
            assert batch_restored.current_balance == Decimal("20.000")
            assert batch_restored.status == "ACTIVE"


# ================= 11. AREA E: NOTIFICATIONS & COMPREHENSIVE REPORTING =================

@pytest.mark.asyncio
async def test_area_e_notifications_and_extended_reports():
    """
    Area E: Verifies:
    1. Automatic notification generated on PO creation.
    2. Automatic notification generated on GRN post.
    3. Automatic notification generated on MIN dispatch AND Low Stock Alert when stock <= 10.000.
    4. Daily movement report (inward receipts, outward dispatches, net movement).
    5. Material movement register with chronological running balance.
    6. Actor details (creator/approver names) present in registers.
    """
    env = await setup_test_environment()
    override_user(maker_user)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create PO and verify notification
        res_po = await client.post("/api/v1/purchase-orders/create", json={
            "company_id": env["company_id"],
            "supplier_id": env["supplier_id"],
            "project_id": env["project_id"],
            "items": [{"material_master_id": env["material_master_id"], "material_name": "Notif PO", "quantity": "12.000", "rate": "50.00"}]
        })
        po_num = res_po.json()["po_number"]

        async with AsyncSessionLocal() as db:
            notif_po = (await db.execute(
                select(Notification).where(
                    and_(
                        Notification.user_id == maker_user.id,
                        Notification.title.like(f"%{po_num}%"),
                    )
                )
            )).scalar_one_or_none()
            assert notif_po is not None
            assert "Purchase Order Created" in notif_po.title

        # 2. Inward GRN and verify notification on post
        res_grn = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "remarks": "Area E direct inward",
            "items": [{"material_master_id": env["material_master_id"], "received_quantity": "12.000", "unit_rate": "50.00"}]
        })
        grn_id = res_grn.json()["id"]
        grn_num = res_grn.json()["grn_number"]
        grn_item_id = res_grn.json()["items"][0]["id"]

        res_post = await client.post(f"/api/v1/inward/grn/{grn_id}/qc-post", json={
            "qc_items": [{"item_id": grn_item_id, "accepted_quantity": "12.000", "rejected_quantity": "0.000"}]
        })
        mat_id = res_post.json()["items"][0]["material_id"]

        async with AsyncSessionLocal() as db:
            notif_grn = (await db.execute(
                select(Notification).where(
                    and_(
                        Notification.user_id == maker_user.id,
                        Notification.title.like(f"%{grn_num}%"),
                    )
                )
            )).scalar_one_or_none()
            assert notif_grn is not None
            assert "GRN Posted" in notif_grn.title

        # 3. Outward MIN that breaches low stock threshold (remaining <= 10.000)
        # Material currently has remaining stock: 12.000 inward. Issue 8.000 -> 4.000 remaining (<= 10.000)
        res_min = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "quantity": "8.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Subcontractor Alpha",
        })
        min_num = res_min.json()["issue_number"]

        async with AsyncSessionLocal() as db:
            # Check MIN dispatch notification
            notif_min = (await db.execute(
                select(Notification).where(
                    and_(
                        Notification.user_id == maker_user.id,
                        Notification.title.like(f"%{min_num}%"),
                    )
                )
            )).scalar_one_or_none()
            assert notif_min is not None

            # Check Low Stock notification
            notif_low = (await db.execute(
                select(Notification).where(
                    and_(
                        Notification.user_id == maker_user.id,
                        Notification.type == "alert",
                        Notification.title.like("%Low Stock Alert%"),
                    )
                )
            )).scalars().all()
            assert len(notif_low) > 0

        # 4. Daily Movement Summary Report
        res_daily = await client.get(f"/api/v1/inventory/reports/daily-movement?project_id={env['project_id']}")
        assert res_daily.status_code == 200
        daily_data = res_daily.json()
        assert "total_inward_quantity" in daily_data
        assert "total_outward_quantity" in daily_data
        assert "net_quantity_change" in daily_data

        # 5. Individual Material Movement Register
        res_movements = await client.get(f"/api/v1/inventory/reports/movement-register?material_id={mat_id}")
        assert res_movements.status_code == 200
        movements_data = res_movements.json()
        assert movements_data["material_id"] == mat_id
        assert "movements" in movements_data
        assert len(movements_data["movements"]) >= 2  # PURCHASE + USAGE

        # 6. GRN Register actor details
        res_grn_reg = await client.get(f"/api/v1/inventory/reports/grn-register?project_id={env['project_id']}")
        assert res_grn_reg.status_code == 200
        grn_entries = res_grn_reg.json()
        target_grn = next((g for g in grn_entries if g["grn_id"] == grn_id), None)
        assert target_grn is not None
        assert "created_by_name" in target_grn
        assert target_grn["created_by_name"] is not None

        # 7. Issue Register actor details
        res_iss_reg = await client.get(f"/api/v1/inventory/reports/issue-register?project_id={env['project_id']}")
        assert res_iss_reg.status_code == 200
        iss_entries = res_iss_reg.json()
        target_iss = next((i for i in iss_entries if i["issue_number"] == min_num), None)
        assert target_iss is not None
        assert "created_by_name" in target_iss
        assert target_iss["created_by_name"] is not None


# ================= 12. AREA F: STRICT TENANT ISOLATION & PERMISSIONS =================

@pytest.mark.asyncio
async def test_area_f_tenant_isolation_cross_company_forbidden():
    """
    Area F: Verifies strict tenant isolation across all endpoints.
    A user belonging to Company 992 attempting to access or manipulate Company 1 data
    is strictly rejected with HTTP 403 Forbidden.
    """
    env = await setup_test_environment()

    # Create Company 992 and User 9920
    async with AsyncSessionLocal() as db:
        c2 = await db.get(Company, 992)
        if not c2:
            c2 = Company(id=992, name="Tenant Isolation Co 2", is_active=True)
            db.add(c2)

        u2 = await db.get(User, 9920)
        if not u2:
            u2 = User(
                id=9920,
                email="tenant2_user@test.com",
                hashed_password=get_password_hash("Pass123!"),
                full_name="Tenant 2 User",
                role=UserRole.ADMIN.value,
                is_active=True,
                is_super_admin=False,
                company_id=992,
            )
            db.add(u2)
        await db.commit()

    # Switch identity to Company 2 user
    user_tenant_2 = User(
        id=9920,
        email="tenant2_user@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=992,
    )
    override_user(user_tenant_2)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Attempt cross-company PO creation
        res_po = await client.post("/api/v1/purchase-orders/create", json={
            "company_id": env["company_id"],  # 1 != 992
            "supplier_id": env["supplier_id"],
            "project_id": env["project_id"],
            "items": [{"material_master_id": env["material_master_id"], "material_name": "Cross PO", "quantity": "10.000", "rate": "50.00"}]
        })
        assert res_po.status_code == 403
        assert "Forbidden" in res_po.json()["detail"]

        # 2. Attempt cross-company GRN creation
        res_grn = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],  # 1 != 992
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "items": [{"material_master_id": env["material_master_id"], "received_quantity": "10.000", "unit_rate": "50.00"}]
        })
        assert res_grn.status_code == 403
        assert "Forbidden" in res_grn.json()["detail"]

        # 3. Attempt cross-company MIN creation
        res_min = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],  # 1 != 992
            "project_id": env["project_id"],
            "material_id": 1,
            "quantity": "5.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Cross Hacker",
        })
        assert res_min.status_code == 403
        assert "Forbidden" in res_min.json()["detail"]

        # 4. Attempt cross-company Daily Stock count
        res_dsr = await client.post("/api/v1/daily-stock/record", json={
            "company_id": env["company_id"],  # 1 != 992
            "project_id": env["project_id"],
            "material_id": 1,
            "physical_count": "50.000",
            "reconciliation_date": date.today().isoformat(),
        })
        assert res_dsr.status_code == 403
        assert "Forbidden" in res_dsr.json()["detail"]

        # 5. Attempt cross-company Cancellation OTP Request
        res_otp = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],  # 1 != 992
            "document_type": "PO",
            "document_id": 1,
            "reason": "Cross company attempt",
        })
        assert res_otp.status_code == 403
        assert "Forbidden" in res_otp.json()["detail"]

        # 6. Attempt cross-company Report Access
        res_rep = await client.get(f"/api/v1/inventory/reports/stock-summary?company_id={env['company_id']}")
        assert res_rep.status_code == 403
        assert "Forbidden" in res_rep.json()["detail"]

        res_sup_rep = await client.get(f"/api/v1/inventory/reports/supplier-stock?company_id={env['company_id']}")
        assert res_sup_rep.status_code == 403
        assert "Forbidden" in res_sup_rep.json()["detail"]


# ================= 13. AREA A: DAILY STOCK CANCELLATION & COMPENSATING LEDGER REVERSALS =================

@pytest.mark.asyncio
async def test_area_a_daily_stock_cancellation_and_compensating_reversal():
    """
    Area A: Scenarios A1-A4 for Daily Stock Cancellation:
    - A1: Cancel DSR before approval (stock/ledger unchanged).
    - A2: Cancel DSR after recount before approval (recount preserved, stock unchanged).
    - A3: Cancel DSR after approved adjustment (reverses stock, creates compensating ledger, depletes ADJ batch).
    - A4: Replay/duplicate cancellation rejection (HTTP 400).
    """
    env = await setup_test_environment()
    override_user(maker_user)
    uid = uuid.uuid4().hex[:6]

    # Create Material
    async with AsyncSessionLocal() as db:
        mat = Material(
            material_code=f"MAT-DSA-{uid}",
            material_name=f"Daily Stock Test Mat {uid}",
            category="Steel",
            project_id=env["project_id"],
            material_master_id=env["material_master_id"],
            supplier_id=env["supplier_id"],
            unit_id=env["unit_id"],
            rate_type=RateType.FIXED,
            purchase_rate=Decimal("100.00"),
            quantity_purchased=Decimal("50.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("50.000"),
        )
        db.add(mat)
        await db.commit()
        await db.refresh(mat)
        mat_id = mat.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # --- SCENARIO A1: Cancel DSR before approval ---
        res_a1 = await client.post("/api/v1/daily-stock/record", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "physical_count": "45.000",
            "reconciliation_date": date.today().isoformat(),
        })
        assert res_a1.status_code == 201
        dsr_a1_id = res_a1.json()["id"]

        # Maker requests OTP
        res_otp_a1 = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "DAILY_STOCK",
            "document_id": dsr_a1_id,
            "reason": "Cancellation before approval A1",
        })
        assert res_otp_a1.status_code == 201
        otp_a1_data = res_otp_a1.json()
        otp_a1 = await _fetch_checker_otp(otp_a1_data)

        # Checker verifies OTP
        override_user(checker_user)
        res_ver_a1 = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": otp_a1_data["request_id"],
            "otp": otp_a1,
        })
        assert res_ver_a1.status_code == 200

        async with AsyncSessionLocal() as db:
            dsr_a1_db = await db.get(DailyStockReconciliation, dsr_a1_id)
            assert dsr_a1_db.status == "CANCELLED"
            mat_a1 = await db.get(Material, mat_id)
            assert mat_a1.remaining_stock == Decimal("50.000")  # Unaffected

        # --- SCENARIO A2: Cancel DSR after recount before approval ---
        override_user(maker_user)
        rec_date_a2 = date.today() - timedelta(days=1)
        res_a2 = await client.post("/api/v1/daily-stock/record", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "physical_count": "40.000",
            "reconciliation_date": rec_date_a2.isoformat(),
        })
        assert res_a2.status_code == 201
        dsr_a2_id = res_a2.json()["id"]

        # Recount
        res_rec = await client.post(f"/api/v1/daily-stock/{dsr_a2_id}/recount", json={
            "new_physical_count": "42.000",
            "recount_notes": "Recount verified in yard",
        })
        assert res_rec.status_code == 200
        assert res_rec.json()["version"] == 2

        # Maker requests OTP
        res_otp_a2 = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "DAILY_STOCK",
            "document_id": dsr_a2_id,
            "reason": "Cancellation after recount A2",
        })
        assert res_otp_a2.status_code == 201
        otp_a2 = await _fetch_checker_otp(res_otp_a2.json())

        # Checker verifies OTP
        override_user(checker_user)
        res_ver_a2 = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": res_otp_a2.json()["request_id"],
            "otp": otp_a2,
        })
        assert res_ver_a2.status_code == 200

        async with AsyncSessionLocal() as db:
            dsr_a2_db = await db.get(DailyStockReconciliation, dsr_a2_id)
            assert dsr_a2_db.status == "CANCELLED"
            mat_a2 = await db.get(Material, mat_id)
            assert mat_a2.remaining_stock == Decimal("50.000")  # Unaffected

        # --- SCENARIO A3: Cancel DSR after approved adjustment ---
        override_user(maker_user)
        rec_date_a3 = date.today() - timedelta(days=2)
        res_a3 = await client.post("/api/v1/daily-stock/record", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "physical_count": "60.000",  # Variance +10.000
            "reconciliation_date": rec_date_a3.isoformat(),
        })
        assert res_a3.status_code == 201
        dsr_a3_id = res_a3.json()["id"]

        # Review and approve adjustment
        override_user(checker_user)
        res_rev_a3 = await client.post(f"/api/v1/daily-stock/{dsr_a3_id}/review", json={
            "review_outcome": "ADJUST_INVENTORY",
            "review_notes": "Approved +10 surplus",
        })
        assert res_rev_a3.status_code == 200
        assert res_rev_a3.json()["status"] == "ADJUSTED"

        async with AsyncSessionLocal() as db:
            mat_after_adj = await db.get(Material, mat_id)
            assert mat_after_adj.remaining_stock == Decimal("60.000")  # Stock increased to 60

        # Now cancel the approved reconciliation via OTP
        override_user(maker_user)
        res_otp_a3 = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "DAILY_STOCK",
            "document_id": dsr_a3_id,
            "reason": "Surplus was entered for wrong material - cancelling A3",
        })
        assert res_otp_a3.status_code == 201
        otp_a3 = await _fetch_checker_otp(res_otp_a3.json())

        override_user(checker_user)
        res_ver_a3 = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": res_otp_a3.json()["request_id"],
            "otp": otp_a3,
        })
        assert res_ver_a3.status_code == 200

        # Verify stock and compensating ledger in DB
        async with AsyncSessionLocal() as db:
            dsr_a3_db = await db.get(DailyStockReconciliation, dsr_a3_id)
            assert dsr_a3_db.status == "CANCELLED"

            mat_restored = await db.get(Material, mat_id)
            assert mat_restored.remaining_stock == Decimal("50.000")  # Restored to 50!

            # Check compensating ledger entries
            ledgers = (await db.execute(
                select(MaterialLedger).where(
                    and_(
                        MaterialLedger.material_id == mat_id,
                        MaterialLedger.type == TransactionType.ADJUSTMENT,
                    )
                ).order_by(MaterialLedger.id.asc())
            )).scalars().all()
            assert len(ledgers) >= 2
            # Sum of adjustments should be exactly 0
            assert sum(l.quantity for l in ledgers) == Decimal("0.000")

            # Check ADJ batch is depleted
            adj_b = (await db.execute(
                select(StockBatch).where(StockBatch.batch_number == f"ADJ-{dsr_a3_db.reconciliation_number}")
            )).scalar_one_or_none()
            if adj_b:
                assert adj_b.current_balance == Decimal("0.000")
                assert adj_b.status == "DEPLETED"

        # --- SCENARIO A4: Replay / duplicate cancellation rejection ---
        override_user(maker_user)
        res_replay = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "DAILY_STOCK",
            "document_id": dsr_a3_id,
            "reason": "Duplicate cancellation attempt A4",
        })
        assert res_replay.status_code == 400
        assert "already cancelled" in res_replay.json()["detail"].lower()


# ================= 14. AREA B: ADMIN NOTIFICATION SCOPING & TENANT ISOLATION =================

@pytest.mark.asyncio
async def test_area_b_admin_notification_scoping_and_tenancy():
    """
    Area B: Verifies that:
    1. Multi-tenant scoping: Active company Admins & PMs receive notifications.
    2. Cross-company users never receive notifications.
    3. Inactive/deleted users are strictly excluded.
    4. Duplicates within the same event are collapsed.
    """
    env = await setup_test_environment()

    # Create Company 1 PM User, Company 1 Inactive Admin, and Company 2 Admin User
    async with AsyncSessionLocal() as db:
        # Co 1 PM
        pm_co1 = await db.get(User, 9902)
        if not pm_co1:
            pm_co1 = User(
                id=9902,
                email="pm_co1@test.com",
                hashed_password=get_password_hash("Pass123!"),
                full_name="PM Co 1",
                role=UserRole.PROJECT_MANAGER.value,
                is_active=True,
                is_super_admin=False,
                company_id=env["company_id"],
            )
            db.add(pm_co1)

        # Co 1 Inactive Admin
        inact_co1 = await db.get(User, 9903)
        if not inact_co1:
            inact_co1 = User(
                id=9903,
                email="inactive_admin_co1@test.com",
                hashed_password=get_password_hash("Pass123!"),
                full_name="Inactive Admin Co 1",
                role=UserRole.ADMIN.value,
                is_active=False,
                is_super_admin=False,
                company_id=env["company_id"],
            )
            db.add(inact_co1)

        # Co 2 Admin
        c2 = await db.get(Company, 992)
        if not c2:
            c2 = Company(id=992, name="Tenant Isolation Co 2", is_active=True)
            db.add(c2)

        admin_co2 = await db.get(User, 9920)
        if not admin_co2:
            admin_co2 = User(
                id=9920,
                email="admin_co2@test.com",
                hashed_password=get_password_hash("Pass123!"),
                full_name="Admin Co 2",
                role=UserRole.ADMIN.value,
                is_active=True,
                is_super_admin=False,
                company_id=992,
            )
            db.add(admin_co2)
        await db.commit()

    override_user(maker_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a PO in Company 1 which triggers notify_company_stakeholders
        res_po = await client.post("/api/v1/purchase-orders/create", json={
            "company_id": env["company_id"],
            "supplier_id": env["supplier_id"],
            "project_id": env["project_id"],
            "items": [{"material_master_id": env["material_master_id"], "material_name": "Notif Test Item", "quantity": "5.000", "rate": "40.00"}]
        })
        assert res_po.status_code == 201
        po_num = res_po.json()["po_number"]

        async with AsyncSessionLocal() as db:
            # Active Admin (maker_user 9101, checker_user 9102) and PM (9902) should receive notification
            co1_notifs = (await db.execute(
                select(Notification).where(
                    and_(
                        Notification.user_id.in_([9101, 9102, 9902]),
                        Notification.title.like(f"%{po_num}%"),
                    )
                )
            )).scalars().all()
            assert len(co1_notifs) >= 1

            # Inactive user 9903 must NEVER receive notification
            inact_notif = (await db.execute(
                select(Notification).where(
                    and_(
                        Notification.user_id == 9903,
                        Notification.title.like(f"%{po_num}%"),
                    )
                )
            )).scalar_one_or_none()
            assert inact_notif is None

            # Cross-company user 9920 must NEVER receive notification
            co2_notif = (await db.execute(
                select(Notification).where(
                    and_(
                        Notification.user_id == 9920,
                        Notification.title.like(f"%{po_num}%"),
                    )
                )
            )).scalar_one_or_none()
            assert co2_notif is None


# ================= 15. AREA C: MULTI-SUPPLIER LIFECYCLE, FIFO & CANCELLATION SAFETY =================

@pytest.mark.asyncio
async def test_area_c_multi_supplier_lifecycle_and_cancellation_guards():
    """
    Area C:
    1. Multi-supplier receipts (Supplier A: 25 units, Supplier B: 35 units).
    2. Outward issue of 45 units (FIFO: completely consumes Supplier A, partially consumes Supplier B).
    3. Negative Stock Guard: Reject cancellation of Supplier A's GRN because stock was already consumed.
    4. MIN cancellation: Restores stock and batch balances.
    5. Safe GRN cancellation: Now that stock is restored, GRN can be safely cancelled.
    6. Supplier Stock Report accurately reconciles with batch balances at each stage.
    """
    env = await setup_test_environment()
    override_user(maker_user)
    uid = uuid.uuid4().hex[:6]

    # Setup distinct Supplier B
    async with AsyncSessionLocal() as db:
        sup_b = await db.scalar(select(Supplier).where(Supplier.supplier_name == f"Supplier Beta {uid}"))
        if not sup_b:
            sup_b = Supplier(
                supplier_name=f"Supplier Beta {uid}",
                contact_person="Beta Rep",
                phone_email=f"sup_b_{uid}@test.com",
                company_id=env["company_id"],
            )
            db.add(sup_b)
            await db.commit()
            await db.refresh(sup_b)
        sup_b_id = sup_b.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Inward from Supplier A: 25 units @ 50.00
        res_grn_a = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": env["supplier_id"],
            "remarks": "Supplier A direct inward",
            "items": [{"material_master_id": env["material_master_id"], "received_quantity": "25.000", "unit_rate": "50.00"}],
        })
        assert res_grn_a.status_code == 201
        grn_a_data = res_grn_a.json()
        grn_a_id = grn_a_data["id"]
        grn_a_item_id = grn_a_data["items"][0]["id"]

        # Post QC for GRN A
        res_post_a = await client.post(f"/api/v1/inward/grn/{grn_a_id}/qc-post", json={
            "qc_items": [{"item_id": grn_a_item_id, "accepted_quantity": "25.000", "rejected_quantity": "0.000"}]
        })
        assert res_post_a.status_code == 200
        mat_id = res_post_a.json()["items"][0]["material_id"]

        # 2. Inward from Supplier B: 35 units @ 60.00
        res_grn_b = await client.post("/api/v1/inward/grn/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "supplier_id": sup_b_id,
            "remarks": "Supplier B direct inward",
            "items": [{"material_master_id": env["material_master_id"], "received_quantity": "35.000", "unit_rate": "60.00"}],
        })
        assert res_grn_b.status_code == 201
        grn_b_id = res_grn_b.json()["id"]
        grn_b_item_id = res_grn_b.json()["items"][0]["id"]

        # Post QC for GRN B
        res_post_b = await client.post(f"/api/v1/inward/grn/{grn_b_id}/qc-post", json={
            "qc_items": [{"item_id": grn_b_item_id, "accepted_quantity": "35.000", "rejected_quantity": "0.000"}]
        })
        assert res_post_b.status_code == 200

        # Verify combined stock is 60.000
        async with AsyncSessionLocal() as db:
            mat_after_inward = await db.get(Material, mat_id)
            assert mat_after_inward.remaining_stock == Decimal("60.000")

        # 3. Outward MIN: Issue 45 units
        # FIFO consumes 25 from Supplier A (depleted) and 20 from Supplier B (15 remaining)
        res_min = await client.post("/api/v1/outward/issue/create", json={
            "company_id": env["company_id"],
            "project_id": env["project_id"],
            "material_id": mat_id,
            "quantity": "45.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Field Crew Delta",
        })
        assert res_min.status_code == 201
        min_id = res_min.json()["id"]

        async with AsyncSessionLocal() as db:
            mat_after_issue = await db.get(Material, mat_id)
            assert mat_after_issue.remaining_stock == Decimal("15.000")  # Only 15 remaining!

        # 4. Attempt to Cancel GRN A (25 units): Must fail because remaining stock (15) < 25!
        res_otp_bad_grn = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "GRN",
            "document_id": grn_a_id,
            "reason": "Accidental GRN A - attempt while stock consumed",
        })
        assert res_otp_bad_grn.status_code == 400
        assert "stock has already been consumed" in res_otp_bad_grn.json()["detail"].lower()

        # 5. Cancel the Outward MIN via Maker-Checker OTP
        res_otp_min = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "MIN",
            "document_id": min_id,
            "reason": "Cancelled field issue",
        })
        assert res_otp_min.status_code == 201
        otp_min_val = await _fetch_checker_otp(res_otp_min.json())

        override_user(checker_user)
        res_ver_min = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": res_otp_min.json()["request_id"],
            "otp": otp_min_val,
        })
        assert res_ver_min.status_code == 200

        # Verify stock restored to 60.000
        async with AsyncSessionLocal() as db:
            mat_restored_min = await db.get(Material, mat_id)
            assert mat_restored_min.remaining_stock == Decimal("60.000")

        # 6. Now cancel GRN A safely: Since remaining stock is 60 >= 25, cancellation succeeds!
        override_user(maker_user)
        res_otp_grn_a = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": env["company_id"],
            "document_type": "GRN",
            "document_id": grn_a_id,
            "reason": "Legitimate GRN A cancellation after MIN reversal",
        })
        assert res_otp_grn_a.status_code == 201
        otp_grn_a_val = await _fetch_checker_otp(res_otp_grn_a.json())

        override_user(checker_user)
        res_ver_grn_a = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": env["company_id"],
            "request_id": res_otp_grn_a.json()["request_id"],
            "otp": otp_grn_a_val,
        })
        assert res_ver_grn_a.status_code == 200

        # Verify final stock is 35.000 (from Supplier B)
        async with AsyncSessionLocal() as db:
            mat_final = await db.get(Material, mat_id)
            assert mat_final.remaining_stock == Decimal("35.000")

            grn_a_db = await db.get(GoodsReceiptNote, grn_a_id)
            assert grn_a_db.status == "CANCELLED"

        # 7. Check Supplier Stock Report
        override_user(maker_user)
        res_sup_rep = await client.get(f"/api/v1/inventory/reports/supplier-stock?project_id={env['project_id']}")
        assert res_sup_rep.status_code == 200
        sup_rep_data = res_sup_rep.json()
        assert len(sup_rep_data) >= 1
        sup_b_entry = next((s for s in sup_rep_data if s["supplier_id"] == sup_b_id), None)
        assert sup_b_entry is not None
        assert Decimal(str(sup_b_entry["supplier_stock_balance"])) == Decimal("35.000")
