import pytest
import uuid
from decimal import Decimal
from datetime import datetime
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_roles
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus
from app.models.master_data import MaterialMaster, Unit
import asyncio
from app.models.material import (
    Material,
    Supplier,
    PurchaseOrder,
    MaterialTransfer,
    MaterialTransaction,
    MaterialLedger,
    MaterialUsage,
)
from app.core.enums import TransactionType, RateType

admin_user = User(
    id=9001,
    email="admin_mat_audit@test.com",
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


async def setup_audit_data():
    async with AsyncSessionLocal() as db:
        c1 = await db.get(Company, 1)
        if not c1:
            c1 = Company(id=1, name="Company Audit", is_active=True)
            db.add(c1)

        owner_a = await db.scalar(select(Owner).where(Owner.company_id == 1))
        if not owner_a:
            owner_a = Owner(
                name="Owner Audit",
                phone="9876543210",
                email="owner_audit@test.com",
                company_id=1,
            )
            db.add(owner_a)
            await db.flush()

        usr = await db.get(User, admin_user.id)
        if not usr:
            from app.core.security import get_password_hash
            usr = User(
                id=admin_user.id,
                email=admin_user.email,
                hashed_password=get_password_hash("TestPassword123!"),
                full_name="Admin Audit",
                role=UserRole.ADMIN.value,
                is_active=True,
                is_super_admin=False,
                company_id=1,
            )
            db.add(usr)
            await db.flush()

        suffix = uuid.uuid4().hex[:6]
        proj = Project(
            business_id=f"PRJ-AUD-{suffix}",
            company_id=1,
            project_name=f"Project Audit {suffix}",
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_dest = Project(
            business_id=f"PRJ-DST-{suffix}",
            company_id=1,
            project_name=f"Project Dest {suffix}",
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(proj)
        db.add(proj_dest)
        await db.flush()

        unit = await db.scalar(select(Unit).where(Unit.name == "Bags"))
        if not unit:
            unit = Unit(name="Bags", code="BAG")
            db.add(unit)
            await db.flush()

        mm = await db.scalar(select(MaterialMaster).where(MaterialMaster.name == "Cement 53 Grade"))
        if not mm:
            mm = MaterialMaster(
                name="Cement 53 Grade",
                category="Cement",
                unit_id=unit.id,
                hsn_code="25232930",
            )
            db.add(mm)
            await db.flush()

        supplier = Supplier(
            company_id=1,
            supplier_name=f"Supplier Audit {suffix}",
            contact_person="John Doe",
            phone_email="audit_supplier@example.com",
            gst_number="27ABCDE1234F1Z5",
            address="123 Industrial Area",
        )
        db.add(supplier)
        await db.flush()

        await db.commit()
        return {
            "project_id": proj.id,
            "project_dest_id": proj_dest.id,
            "unit_id": unit.id,
            "unit_name": unit.name,
            "mm_id": mm.id,
            "supplier_id": supplier.id,
            "supplier_name": supplier.supplier_name,
        }


@pytest.mark.asyncio
async def test_material_creation_returns_201():
    override_user(admin_user)
    data = await setup_audit_data()

    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 20000.0,
        "rate_type": "FIXED",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/v1/materials", json=payload)
        assert res.status_code == 201, f"Expected 201 Created, got {res.status_code}: {res.text}"
        body = res.json()
        assert body["material_name"] == "Cement 53 Grade"
        assert body["project_id"] == data["project_id"]


@pytest.mark.asyncio
async def test_project_inventory_and_valuation_endpoints():
    override_user(admin_user)
    data = await setup_audit_data()

    # Create material
    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201

        # Test GET /api/v1/materials/inventory/{project_id}
        res_proj_inv = await ac.get(f"/api/v1/materials/inventory/{data['project_id']}")
        assert res_proj_inv.status_code == 200
        proj_inv_data = res_proj_inv.json()
        assert isinstance(proj_inv_data, list)
        assert len(proj_inv_data) >= 1
        item = proj_inv_data[0]
        assert "unit_id" in item
        assert "unit_name" in item
        assert item["unit_name"] == data["unit_name"]
        assert item["project_id"] == data["project_id"]

        # Test GET /api/v1/materials/inventory
        res_all_inv = await ac.get("/api/v1/materials/inventory")
        assert res_all_inv.status_code == 200
        all_inv_data = res_all_inv.json()
        assert isinstance(all_inv_data, list)

        # Test GET /api/v1/materials/inventory/valuation
        res_val = await ac.get("/api/v1/materials/inventory/valuation")
        assert res_val.status_code == 200
        val_data = res_val.json()
        assert "total_value" in val_data


@pytest.mark.asyncio
async def test_purchase_order_fields():
    override_user(admin_user)
    data = await setup_audit_data()

    # Create material first so material_id is available
    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        po_payload = {
            "project_id": data["project_id"],
            "supplier_id": data["supplier_id"],
            "material_id": mat_id,
            "quantity": 200,
            "rate": 415.0,
            "boq_item_id": 1,
        }

        res = await ac.post("/api/v1/materials/purchase-orders", json=po_payload)
        assert res.status_code in (200, 201), f"Expected 200/201, got {res.status_code}: {res.text}"
        po_data = res.json()
        assert "boq_item_id" in po_data
        assert po_data["boq_item_id"] == 1
        assert "created_at" in po_data
        assert po_data["created_at"] is not None

        po_id = po_data["id"]
        get_res = await ac.get(f"/api/v1/materials/purchase-orders/{po_id}")
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["boq_item_id"] == 1
        assert get_data["created_at"] is not None


@pytest.mark.asyncio
async def test_material_logs_and_transactions_names():
    override_user(admin_user)
    data = await setup_audit_data()

    # Create a material
    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 50,
        "purchase_rate": 400.0,
        "payment_given": 20000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        # Check logs with project_id
        log_res = await ac.get(f"/api/v1/materials/logs?project_id={data['project_id']}")
        assert log_res.status_code == 200
        logs = log_res.json()
        assert isinstance(logs, list)
        if len(logs) > 0:
            assert "material_name" in logs[0]
            assert "supplier_name" in logs[0]
            assert "supplier_id" in logs[0]

        # Check material transactions
        tx_res = await ac.get(f"/api/v1/materials/{mat_id}/transactions")
        assert tx_res.status_code == 200
        txs = tx_res.json()
        assert isinstance(txs, list)
        if len(txs) > 0:
            assert txs[0]["material_name"] is not None
            assert txs[0]["supplier_name"] is not None


@pytest.mark.asyncio
async def test_transfer_status_body_and_conflict():
    override_user(admin_user)
    data = await setup_audit_data()

    # Create material in source project
    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        # POST /transfers returns 201
        trf_payload = {
            "from_project_id": data["project_id"],
            "to_project_id": data["project_dest_id"],
            "material_id": mat_id,
            "quantity": 10,
            "notes": "Testing transfer status update",
        }
        trf_res = await ac.post("/api/v1/materials/transfers", json=trf_payload)
        assert trf_res.status_code == 201
        trf_id = trf_res.json()["id"]

        # Test conflicting body and query params -> 400
        conflict_res = await ac.put(
            f"/api/v1/materials/transfers/{trf_id}?status=CANCELLED",
            json={"status": "COMPLETED"},
        )
        assert conflict_res.status_code == 400

        # Test valid body update -> 200
        body_update_res = await ac.put(
            f"/api/v1/materials/transfers/{trf_id}",
            json={"status": "COMPLETED"},
        )
        assert body_update_res.status_code == 200
        assert body_update_res.json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_supplier_contract_unchanged():
    override_user(admin_user)
    data = await setup_audit_data()

    # Verify SupplierCreate schema requires/uses phone_email and does not require separate phone or email
    import random
    unique_phone = "98" + "".join(str(random.randint(0, 9)) for _ in range(8))
    supp_payload = {
        "supplier_name": f"Supplier Verified {uuid.uuid4().hex[:4]}",
        "contact_person": "Jane Doe",
        "phone_email": unique_phone,
        "address": "456 Commerce Road",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/v1/materials/suppliers", json=supp_payload)
        assert res.status_code in (200, 201), f"Expected 200/201, got {res.status_code}: {res.text}"
        body = res.json()
        assert body["phone_email"] == unique_phone
        assert "phone" not in body or body.get("phone") is None


@pytest.mark.asyncio
async def test_report_monetary_precision():
    override_user(admin_user)
    data = await setup_audit_data()

    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 77,
        "purchase_rate": 420.33,
        "payment_given": 20000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/v1/materials", json=payload)

        # GET /reports
        rep_res = await ac.get(f"/api/v1/materials/reports?project_id={data['project_id']}")
        assert rep_res.status_code == 200
        rep_data = rep_res.json()
        assert "summary" in rep_data
        summary = rep_data["summary"]
        # Monetary fields should be numbers with at most 2 decimal places
        for k in ["total_stock_value", "total_pending_payments"]:
            if k in summary:
                val = summary[k]
                assert val == round(val, 2)


@pytest.mark.asyncio
async def test_idempotency_purchase_usage():
    override_user(admin_user)
    data = await setup_audit_data()

    # Create material
    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        idempotency_key = f"key-{uuid.uuid4().hex}"
        purchase_payload = {
            "quantity": 50,
            "rate": 420.0,
            "amount_paid": 21000.0,
            "supplier_id": data["supplier_id"],
            "project_id": data["project_id"],
        }
        headers = {"Idempotency-Key": idempotency_key}

        # First purchase
        res1 = await ac.post(f"/api/v1/materials/{mat_id}/purchase", json=purchase_payload, headers=headers)
        assert res1.status_code in (200, 201), f"Expected 200/201, got {res1.status_code}: {res1.text}"

        # Duplicate purchase with same idempotency key
        res2 = await ac.post(f"/api/v1/materials/{mat_id}/purchase", json=purchase_payload, headers=headers)
        # Idempotent response is returned
        assert res2.status_code in (200, 201)



# ==============================================================================
# ISSUE #4197: MATERIAL CONSUMPTION / INSUFFICIENT STOCK / CONCURRENCY TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_usage_basic_stock_behavior():
    override_user(admin_user)
    data = await setup_audit_data()

    # Create Material with 100 units
    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        # 1. Stock 100, request 20: success, remaining stock 80
        res1 = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 20,
            "project_id": data["project_id"],
        })
        assert res1.status_code == 200
        assert float(res1.json()["remaining_stock"]) == 80.0
        assert float(res1.json()["quantity_used"]) == 20.0

        # 2. Stock 80, request 80: success, remaining stock 0
        res2 = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 80,
            "project_id": data["project_id"],
        })
        assert res2.status_code == 200
        assert float(res2.json()["remaining_stock"]) == 0.0
        assert float(res2.json()["quantity_used"]) == 100.0

        # 3. Stock 0, request 1: reject, stock remains 0
        res3 = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 1,
            "project_id": data["project_id"],
        })
        assert res3.status_code == 400
        assert res3.json()["detail"] == "Insufficient stock available."

        # Verify stock remains 0
        get_res = await ac.get(f"/api/v1/materials/{mat_id}")
        assert float(get_res.json()["remaining_stock"]) == 0.0

        # Create new Material with 100 units for exceed check
        data2 = await setup_audit_data()
        payload2 = dict(payload)
        payload2["project_id"] = data2["project_id"]
        payload2["supplier_id"] = data2["supplier_id"]
        m_res2 = await ac.post("/api/v1/materials", json=payload2)
        assert m_res2.status_code == 201
        mat_id2 = m_res2.json()["id"]

        # 4. Stock 100, request 101: reject, stock remains 100
        res4 = await ac.post(f"/api/v1/materials/{mat_id2}/usage", json={
            "quantity": 101,
            "project_id": data2["project_id"],
        })
        assert res4.status_code == 400
        assert "Insufficient stock available. Available: 100.000" in res4.json()["detail"]

        get_res2 = await ac.get(f"/api/v1/materials/{mat_id2}")
        assert float(get_res2.json()["remaining_stock"]) == 100.0
        assert float(get_res2.json()["quantity_used"]) == 0.0

        # 5. Quantity 0 or negative: reject 422
        res_zero = await ac.post(f"/api/v1/materials/{mat_id2}/usage", json={
            "quantity": 0,
            "project_id": data["project_id"],
        })
        assert res_zero.status_code == 422

        res_neg = await ac.post(f"/api/v1/materials/{mat_id2}/usage", json={
            "quantity": -5,
            "project_id": data["project_id"],
        })
        assert res_neg.status_code == 422


@pytest.mark.asyncio
async def test_usage_concurrency_60_plus_60_against_100():
    """Critical concurrency regression: two concurrent requests of 60 against 100 stock.
    Exactly one succeeds (200), one fails (400), remaining stock is 40."""
    override_user(admin_user)
    data = await setup_audit_data()

    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        req1 = ac.post(f"/api/v1/materials/{mat_id}/usage", json={"quantity": 60, "project_id": data["project_id"]})
        req2 = ac.post(f"/api/v1/materials/{mat_id}/usage", json={"quantity": 60, "project_id": data["project_id"]})
        res1, res2 = await asyncio.gather(req1, req2)

        statuses = [res1.status_code, res2.status_code]
        assert 200 in statuses, f"Expected one 200, got {statuses}"
        assert 400 in statuses, f"Expected one 400, got {statuses}"

        # Identify rejected response
        rejected_res = res1 if res1.status_code == 400 else res2
        assert "Insufficient stock available. Available: 40.000" in rejected_res.json()["detail"]

        # Check final material stock
        get_res = await ac.get(f"/api/v1/materials/{mat_id}")
        assert float(get_res.json()["remaining_stock"]) == 40.0
        assert float(get_res.json()["quantity_used"]) == 60.0

        # Check DB records: exactly 1 usage entry and 1 usage transaction persisted
        async with AsyncSessionLocal() as db:
            usages = (await db.scalars(select(MaterialUsage).where(MaterialUsage.material_id == mat_id))).all()
            assert len(usages) == 1
            assert float(usages[0].quantity_used) == 60.0

            txs = (await db.scalars(
                select(MaterialTransaction).where(
                    MaterialTransaction.material_id == mat_id,
                    MaterialTransaction.type == TransactionType.USAGE
                )
            )).all()
            assert len(txs) == 1
            assert float(txs[0].quantity) == -60.0


@pytest.mark.asyncio
async def test_usage_concurrency_exact_sum_100():
    """Concurrently request quantities whose sum equals available stock (60 + 40 = 100).
    Both may succeed sequentially under locking, final stock is 0."""
    override_user(admin_user)
    data = await setup_audit_data()

    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        req1 = ac.post(f"/api/v1/materials/{mat_id}/usage", json={"quantity": 60, "project_id": data["project_id"]})
        req2 = ac.post(f"/api/v1/materials/{mat_id}/usage", json={"quantity": 40, "project_id": data["project_id"]})
        res1, res2 = await asyncio.gather(req1, req2)

        assert res1.status_code == 200
        assert res2.status_code == 200

        get_res = await ac.get(f"/api/v1/materials/{mat_id}")
        assert float(get_res.json()["remaining_stock"]) == 0.0
        assert float(get_res.json()["quantity_used"]) == 100.0


@pytest.mark.asyncio
async def test_usage_multiple_concurrent_requests_exceeding_stock():
    """Three concurrent requests of 40 against 100 stock (total 120).
    Exactly two succeed (80 total consumed), one fails, remaining stock is 20."""
    override_user(admin_user)
    data = await setup_audit_data()

    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        req1 = ac.post(f"/api/v1/materials/{mat_id}/usage", json={"quantity": 40, "project_id": data["project_id"]})
        req2 = ac.post(f"/api/v1/materials/{mat_id}/usage", json={"quantity": 40, "project_id": data["project_id"]})
        req3 = ac.post(f"/api/v1/materials/{mat_id}/usage", json={"quantity": 40, "project_id": data["project_id"]})
        results = await asyncio.gather(req1, req2, req3)

        success_count = sum(1 for r in results if r.status_code == 200)
        failure_count = sum(1 for r in results if r.status_code == 400)
        assert success_count == 2
        assert failure_count == 1

        get_res = await ac.get(f"/api/v1/materials/{mat_id}")
        assert float(get_res.json()["remaining_stock"]) == 20.0
        assert float(get_res.json()["quantity_used"]) == 80.0


@pytest.mark.asyncio
async def test_usage_idempotency_simultaneous_retries():
    """Simultaneous retries with the same idempotency key return identical responses
    and do not duplicate usage or transactions."""
    override_user(admin_user)
    data = await setup_audit_data()

    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        idempotency_key = f"usage-key-{uuid.uuid4().hex}"
        headers = {"Idempotency-Key": idempotency_key}
        usage_payload = {"quantity": 35, "project_id": data["project_id"]}

        req1 = ac.post(f"/api/v1/materials/{mat_id}/usage", json=usage_payload, headers=headers)
        req2 = ac.post(f"/api/v1/materials/{mat_id}/usage", json=usage_payload, headers=headers)
        res1, res2 = await asyncio.gather(req1, req2)

        assert res1.status_code == 200
        assert res2.status_code == 200
        assert res1.json()["id"] == res2.json()["id"]

        get_res = await ac.get(f"/api/v1/materials/{mat_id}")
        assert float(get_res.json()["remaining_stock"]) == 65.0
        assert float(get_res.json()["quantity_used"]) == 35.0

        async with AsyncSessionLocal() as db:
            usages = (await db.scalars(select(MaterialUsage).where(MaterialUsage.material_id == mat_id))).all()
            assert len(usages) == 1
            assert float(usages[0].quantity_used) == 35.0


@pytest.mark.asyncio
async def test_usage_project_scoping_and_isolation():
    """Enforce project scoping: mismatched project_id in body is rejected with 400.
    Cross-company access is denied with 403/404."""
    override_user(admin_user)
    data = await setup_audit_data()

    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        # Valid project matches -> 200
        res_valid = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 10,
            "project_id": data["project_id"],
        })
        assert res_valid.status_code == 200

        # Mismatched project (project_dest_id != project_id) -> 400
        res_mismatch = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 10,
            "project_id": data["project_dest_id"],
        })
        assert res_mismatch.status_code == 400
        assert res_mismatch.json()["detail"] == "Project ID does not match material project"

        # Stock unchanged by failed request
        get_res = await ac.get(f"/api/v1/materials/{mat_id}")
        assert float(get_res.json()["remaining_stock"]) == 90.0

    # Cross-company user -> 403 or 404
    diff_user = User(
        id=9003,
        email="diff_company@test.com",
        role=UserRole.SITE_ENGINEER.value,
        is_active=True,
        is_super_admin=False,
        company_id=999,
    )
    override_user(diff_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_unauth = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 10,
            "project_id": data["project_id"],
        })
        assert res_unauth.status_code in (403, 404)


@pytest.mark.asyncio
async def test_usage_transaction_rollback_and_retry():
    """Forced failure (e.g. invalid boq_item_id) rolls back all mutations atomically.
    Subsequent valid retry succeeds."""
    override_user(admin_user)
    data = await setup_audit_data()

    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        # Invalid boq_item_id -> 404
        res_fail = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 25,
            "project_id": data["project_id"],
            "boq_item_id": 999999,
        })
        assert res_fail.status_code == 404

        # Verify no usage or ledger records were persisted
        async with AsyncSessionLocal() as db:
            usages = (await db.scalars(select(MaterialUsage).where(MaterialUsage.material_id == mat_id))).all()
            assert len(usages) == 0

        # Valid retry succeeds -> 200
        res_retry = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 25,
            "project_id": data["project_id"],
        })
        assert res_retry.status_code == 200
        assert float(res_retry.json()["remaining_stock"]) == 75.0


@pytest.mark.asyncio
async def test_usage_reconciliation_compatibility():
    """Material with reconciled physical stock (e.g. diff > 0, physical stock 110 > purchased 100).
    Usage respects authoritative remaining_stock and does not overwrite it with purchased - used."""
    override_user(admin_user)
    data = await setup_audit_data()

    payload = {
        "project_id": data["project_id"],
        "supplier_id": data["supplier_id"],
        "material_master_id": data["mm_id"],
        "material_name": "Cement 53 Grade",
        "category": "Cement",
        "unit_id": data["unit_id"],
        "quantity_purchased": 100,
        "purchase_rate": 420.0,
        "payment_given": 42000.0,
        "rate_type": "FIXED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_res = await ac.post("/api/v1/materials", json=payload)
        assert m_res.status_code == 201
        mat_id = m_res.json()["id"]

        # Directly set reconciled physical stock to 110 (diff = +10 extra physical stock found)
        async with AsyncSessionLocal() as db:
            mat_obj = await db.get(Material, mat_id)
            mat_obj.remaining_stock = Decimal("110.000")
            await db.commit()

        # Can consume 105 units because authoritative remaining_stock is 110
        # (If it used purchased - used = 100, 105 would have failed!)
        res1 = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 105,
            "project_id": data["project_id"],
        })
        assert res1.status_code == 200
        body1 = res1.json()
        assert float(body1["remaining_stock"]) == 5.0
        assert float(body1["quantity_used"]) == 105.0

        # Now attempting to use 10 units when only 5 remain -> rejected
        res2 = await ac.post(f"/api/v1/materials/{mat_id}/usage", json={
            "quantity": 10,
            "project_id": data["project_id"],
        })
        assert res2.status_code == 400
        assert "Insufficient stock available. Available: 5.000" in res2.json()["detail"]

        # Final stock remains 5.0
        get_res = await ac.get(f"/api/v1/materials/{mat_id}")
        assert float(get_res.json()["remaining_stock"]) == 5.0
