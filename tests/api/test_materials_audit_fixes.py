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
from app.models.material import (
    Material,
    Supplier,
    PurchaseOrder,
    MaterialTransfer,
    MaterialTransaction,
    MaterialLedger,
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

