import asyncio
import uuid
import pytest
from datetime import date
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main
from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_current_active_user
from app.models.user import User
from app.models.project import Project
from app.models.equipment import Equipment, EquipmentPurchase, PurchaseType

client = TestClient(app)


def override_user(user: User | None):
    if user is None:
        app.dependency_overrides.clear()
    else:
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_current_active_user] = lambda: user


@pytest.fixture(autouse=True)
def cleanup_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def seed_purchase_calc_data():
    """
    Seeds isolated test projects and equipment for Company 1 and Company 2.
    """
    async def _setup():
        async with AsyncSessionLocal() as db:
            user_a = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
            user_b = await db.scalar(select(User).where(User.company_id == 2, User.role == "Admin").limit(1))
            user_super = await db.scalar(select(User).where(User.is_super_admin == True).limit(1))
            user_labour = await db.scalar(select(User).where(User.company_id == 1, User.role == "Labour").limit(1))

            if not user_a or not user_b or not user_super:
                raise RuntimeError("Required test users not found in DB")

            proj_a = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
            if not proj_a:
                proj_a = Project(company_id=1, project_name=f"Proj A Pur {uuid.uuid4().hex[:6]}", status="IN_PROGRESS")
                db.add(proj_a)
                await db.flush()

            proj_b = await db.scalar(select(Project).where(Project.company_id == 2).limit(1))
            if not proj_b:
                proj_b = Project(company_id=2, project_name=f"Proj B Pur {uuid.uuid4().hex[:6]}", status="IN_PROGRESS")
                db.add(proj_b)
                await db.flush()

            tag = uuid.uuid4().hex[:6]

            # Equipment in Company A Project
            eq_a = Equipment(
                company_id=1,
                project_id=proj_a.id,
                equipment_name=f"Company A Pur Test Rig {tag}",
                equipment_code=f"RIG-A-{tag}",
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
                is_deleted=False,
            )
            db.add(eq_a)
            await db.commit()
            await db.refresh(eq_a)

            return {
                "user_a": user_a,
                "user_b": user_b,
                "user_super": user_super,
                "user_labour": user_labour,
                "proj_a_id": proj_a.id,
                "proj_b_id": proj_b.id,
                "eq_a_id": eq_a.id,
                "created_purchase_ids": [],
            }

    data = asyncio.run(_setup())
    yield data

    # Teardown
    async def _cleanup():
        async with AsyncSessionLocal() as db:
            for pid in data["created_purchase_ids"]:
                obj = await db.get(EquipmentPurchase, pid)
                if obj:
                    await db.delete(obj)
            eq_obj = await db.get(Equipment, data["eq_a_id"])
            if eq_obj:
                await db.delete(eq_obj)
            await db.commit()

    asyncio.run(_cleanup())


def test_create_purchase_ignores_incorrect_client_total(seed_purchase_calc_data):
    """
    Test 1: quantity=10, unit_price=100, client sends total_amount=1
    -> persisted amount must be 1000.
    """
    data = seed_purchase_calc_data
    override_user(data["user_a"])

    inv = f"INV-TEST-1-{uuid.uuid4().hex[:6]}"
    payload = {
        "project_id": data["proj_a_id"],
        "purchase_type": "NEW",
        "purchase_date": str(date.today()),
        "vendor_name": "Test Vendor 1",
        "invoice_number": inv,
        "quantity": 10,
        "unit_price": 100.0,
        "total_amount": 1.0,  # Client tries to send manipulated total
    }

    res = client.post("/api/v1/equipment/purchase", json=payload)
    assert res.status_code == 201, f"Failed: {res.text}"
    body = res.json()
    assert body["total_amount"] == 1000.0
    purchase_id = body["id"]
    data["created_purchase_ids"].append(purchase_id)

    # Verify directly from database
    async def _verify_db():
        async with AsyncSessionLocal() as db:
            p = await db.get(EquipmentPurchase, purchase_id)
            assert p is not None
            assert p.total_amount == Decimal("1000.00")
            assert p.quantity == 10
            assert p.unit_price == Decimal("100.00")

    asyncio.run(_verify_db())


def test_create_purchase_with_correct_client_total(seed_purchase_calc_data):
    """
    Test 2: Correct client total_amount=1000 -> succeeds and persists 1000.
    """
    data = seed_purchase_calc_data
    override_user(data["user_a"])

    inv = f"INV-TEST-2-{uuid.uuid4().hex[:6]}"
    payload = {
        "project_id": data["proj_a_id"],
        "purchase_type": "NEW",
        "purchase_date": str(date.today()),
        "vendor_name": "Test Vendor 2",
        "invoice_number": inv,
        "quantity": 10,
        "unit_price": 100.0,
        "total_amount": 1000.0,
    }

    res = client.post("/api/v1/equipment/purchase", json=payload)
    assert res.status_code == 201, f"Failed: {res.text}"
    body = res.json()
    assert body["total_amount"] == 1000.0
    purchase_id = body["id"]
    data["created_purchase_ids"].append(purchase_id)

    async def _verify_db():
        async with AsyncSessionLocal() as db:
            p = await db.get(EquipmentPurchase, purchase_id)
            assert p is not None
            assert p.total_amount == Decimal("1000.00")

    asyncio.run(_verify_db())


def test_create_purchase_never_persists_incorrect_value(seed_purchase_calc_data):
    """
    Test 3: Incorrect client total_amount (e.g. 999999 or 0)
    must never persist the incorrect value.
    """
    data = seed_purchase_calc_data
    override_user(data["user_a"])

    inv = f"INV-TEST-3-{uuid.uuid4().hex[:6]}"
    payload = {
        "project_id": data["proj_a_id"],
        "purchase_type": "NEW",
        "purchase_date": str(date.today()),
        "vendor_name": "Test Vendor 3",
        "invoice_number": inv,
        "quantity": 5,
        "unit_price": 50.0,
        "total_amount": 999999.0,  # Malicious inflated total
    }

    res = client.post("/api/v1/equipment/purchase", json=payload)
    assert res.status_code == 201, f"Failed: {res.text}"
    body = res.json()
    assert body["total_amount"] == 250.0
    assert body["total_amount"] != 999999.0
    purchase_id = body["id"]
    data["created_purchase_ids"].append(purchase_id)

    async def _verify_db():
        async with AsyncSessionLocal() as db:
            p = await db.get(EquipmentPurchase, purchase_id)
            assert p is not None
            assert p.total_amount == Decimal("250.00")
            assert p.total_amount != Decimal("999999.00")

    asyncio.run(_verify_db())


def test_create_purchase_decimal_values(seed_purchase_calc_data):
    """
    Test 4: Decimal values: quantity=2.5, unit_price=100.50
    -> verify correct Decimal calculation/rounding (2.5 * 100.50 = 251.25).
    """
    data = seed_purchase_calc_data
    override_user(data["user_a"])

    inv = f"INV-TEST-4-{uuid.uuid4().hex[:6]}"
    payload = {
        "project_id": data["proj_a_id"],
        "purchase_type": "NEW",
        "purchase_date": str(date.today()),
        "vendor_name": "Test Vendor 4",
        "invoice_number": inv,
        "quantity": 2.5,
        "unit_price": 100.50,
        "total_amount": 1.0,  # Ignored
    }

    res = client.post("/api/v1/equipment/purchase", json=payload)
    assert res.status_code == 201, f"Failed: {res.text}"
    body = res.json()
    assert body["total_amount"] == 251.25
    purchase_id = body["id"]
    data["created_purchase_ids"].append(purchase_id)

    async def _verify_db():
        async with AsyncSessionLocal() as db:
            p = await db.get(EquipmentPurchase, purchase_id)
            assert p is not None
            assert p.total_amount == Decimal("251.25")

    asyncio.run(_verify_db())


def test_update_purchase_recalculates_and_ignores_client_total(seed_purchase_calc_data):
    """
    Verify PUT /purchase/{id} ignores client total_amount and recalculates
    from quantity and unit_price.
    """
    data = seed_purchase_calc_data
    override_user(data["user_a"])

    # Create initial purchase: 2 * 100 = 200
    inv = f"INV-TEST-UPD-{uuid.uuid4().hex[:6]}"
    create_res = client.post(
        "/api/v1/equipment/purchase",
        json={
            "project_id": data["proj_a_id"],
            "purchase_type": "NEW",
            "purchase_date": str(date.today()),
            "vendor_name": "Update Test Vendor",
            "invoice_number": inv,
            "quantity": 2,
            "unit_price": 100.0,
        },
    )
    assert create_res.status_code == 201
    purchase_id = create_res.json()["id"]
    data["created_purchase_ids"].append(purchase_id)

    # Update with new unit_price and manipulated total_amount
    update_res = client.put(
        f"/api/v1/equipment/purchase/{purchase_id}",
        json={
            "unit_price": 150.0,
            "total_amount": 5.0,  # Malicious
        },
    )
    assert update_res.status_code == 200
    body = update_res.json()
    # 2 * 150.00 = 300.00
    assert body["total_amount"] == 300.0

    async def _verify_db():
        async with AsyncSessionLocal() as db:
            p = await db.get(EquipmentPurchase, purchase_id)
            assert p is not None
            assert p.total_amount == Decimal("300.00")

    asyncio.run(_verify_db())


def test_purchase_tenant_isolation_and_rbac(seed_purchase_calc_data):
    """
    Test 5: Existing tenant isolation and RBAC behavior remains intact.
    - Company A user cannot create purchase on Company B project (404/403)
    - Company B user cannot update Company A purchase (404)
    - SuperAdmin cannot create purchase in standard API (403)
    - User without permission cannot create purchase (403)
    """
    data = seed_purchase_calc_data

    # 1. Company A attempts to create on Company B project
    override_user(data["user_a"])
    res = client.post(
        "/api/v1/equipment/purchase",
        json={
            "project_id": data["proj_b_id"],
            "purchase_type": "NEW",
            "purchase_date": str(date.today()),
            "vendor_name": "Foreign Project Vendor",
            "invoice_number": f"INV-FOR-{uuid.uuid4().hex[:6]}",
            "quantity": 1,
            "unit_price": 100.0,
        },
    )
    assert res.status_code in [403, 404]

    # 2. Company B attempts to update Company A purchase
    if data["created_purchase_ids"]:
        target_pid = data["created_purchase_ids"][0]
        override_user(data["user_b"])
        res = client.put(
            f"/api/v1/equipment/purchase/{target_pid}",
            json={"vendor_name": "Hacked Vendor"},
        )
        assert res.status_code in [403, 404]

    # 3. SuperAdmin blocked from creating purchases in standard API
    override_user(data["user_super"])
    res = client.post(
        "/api/v1/equipment/purchase",
        json={
            "project_id": data["proj_a_id"],
            "purchase_type": "NEW",
            "purchase_date": str(date.today()),
            "vendor_name": "SuperAdmin Vendor",
            "invoice_number": f"INV-SA-{uuid.uuid4().hex[:6]}",
            "quantity": 1,
            "unit_price": 100.0,
        },
    )
    assert res.status_code == 403

    # 4. User without equipment.create permission blocked
    if data["user_labour"]:
        override_user(data["user_labour"])
        res = client.post(
            "/api/v1/equipment/purchase",
            json={
                "project_id": data["proj_a_id"],
                "purchase_type": "NEW",
                "purchase_date": str(date.today()),
                "vendor_name": "Labour Vendor",
                "invoice_number": f"INV-LAB-{uuid.uuid4().hex[:6]}",
                "quantity": 1,
                "unit_price": 100.0,
            },
        )
        assert res.status_code == 403
