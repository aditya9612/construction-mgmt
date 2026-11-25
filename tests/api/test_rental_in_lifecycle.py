"""
tests/api/test_rental_in_lifecycle.py

Focused regression tests for the Rental-IN Option C implementation.

Tests cover:
  Scenario 1 – New Equipment auto-created on receive (main gap fix)
  Scenario 2 – Existing Equipment backward compat (asset_id pre-set)
  Scenario 3 – Duplicate receive safety
  Scenario 4 – Complete Rental-IN E2E flow (Agreement→Receive→Allocate→Usage→Bill→Return)
  Scenario 5 – Negative / error cases
"""
import uuid
import pytest
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select, delete

from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_current_active_user
from app.models.user import User
from app.models.project import Project
from app.models.equipment import Equipment, EquipmentPurchase, EquipmentStatus, EquipmentCondition
from app.core.enums import PurchaseType

client = TestClient(app)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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


async def _seed_purchase(db, user: User, proj: Project, uid: str) -> EquipmentPurchase:
    """Insert a bare EquipmentPurchase (RENT) with no asset_id."""
    purchase = EquipmentPurchase(
        project_id=proj.id,
        purchase_type=PurchaseType.RENT,
        vendor_name=f"E2E Vendor {uid}",
        invoice_number=f"INV-RIN-{uid}",
        purchase_date=date.today(),
        quantity=1,
        unit_price=Decimal("5000.00"),
        total_amount=Decimal("5000.00"),
        start_date=date.today(),
        expected_end_date=date.today() + timedelta(days=30),
        is_received=False,
        is_returned=False,
    )
    db.add(purchase)
    await db.flush()
    return purchase


async def _seed_equipment(db, user: User, proj_id=None, uid: str = "") -> Equipment:
    """Insert a bare Equipment record (owned / pre-existing)."""
    eq = Equipment(
        company_id=user.company_id,
        project_id=proj_id,
        equipment_name=f"Pre-existing Eq {uid}",
        equipment_code=f"PRE-{uid}",
        status=EquipmentStatus.AVAILABLE,
        condition=EquipmentCondition.GOOD,
        working_hours=Decimal("0"),
        fuel_used=Decimal("0"),
        rental_cost=Decimal("0"),
    )
    db.add(eq)
    await db.flush()
    return eq


# ---------------------------------------------------------------------------
# Scenario 1 – New Equipment auto-created on receive
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_receive_creates_equipment_when_asset_id_is_null():
    """
    Core regression: calling POST /api/v1/equipment/rental-in/{purchase_id}/receive
    with purchase.asset_id = NULL must automatically create a physical Equipment record.
    """
    uid = uuid.uuid4().hex[:6]
    today = date.today()

    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
        assert user is not None, "Seed user required (company_id=1, role=Admin)"

        proj = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
        assert proj is not None, "Seed project required for company_id=1"

        purchase = await _seed_purchase(db, user, proj, uid)
        await db.commit()
        purchase_id = purchase.id

    created_equipment_id = None
    try:
        override_user(user)

        # ---- STEP 1: no equipment before receive ----
        resp_before = client.get("/api/v1/equipment/")
        # can't assert absence directly without the specific code; proceed

        # ---- STEP 2: call primary receive route ----
        resp = client.post(
            f"/api/v1/equipment/rental-in/{purchase_id}/receive",
            json={
                "equipment_name": f"E2E Rental Excavator {uid}",
                "equipment_code": f"E2E-RIN-{uid}",
                "condition": "GOOD",
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["status"] == "received", f"Unexpected status: {data}"
        assert "equipment_id" in data, "Response must contain equipment_id"
        assert data["equipment_id"] is not None, "equipment_id must not be None"

        created_equipment_id = data["equipment_id"]

        # ---- STEP 3: verify Equipment record persisted ----
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, created_equipment_id)
            assert eq is not None, "Equipment was not persisted to DB"
            assert eq.equipment_code == f"E2E-RIN-{uid}".upper()
            assert eq.status == EquipmentStatus.AVAILABLE
            assert eq.company_id == user.company_id
            assert eq.project_id is None, "Equipment should be unallocated after receive"

            # ---- STEP 4: verify EquipmentPurchase.asset_id linkage ----
            purch = await db.get(EquipmentPurchase, purchase_id)
            assert purch.asset_id == created_equipment_id, "purchase.asset_id must point to new Equipment"
            assert purch.is_received is True, "is_received must be True"

    finally:
        # cleanup
        async with AsyncSessionLocal() as db:
            await db.execute(delete(EquipmentPurchase).where(EquipmentPurchase.id == purchase_id))
            if created_equipment_id:
                await db.execute(delete(Equipment).where(Equipment.id == created_equipment_id))
            await db.commit()


# ---------------------------------------------------------------------------
# Scenario 2 – Existing Equipment backward compat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_receive_uses_existing_equipment_when_asset_id_preset():
    """
    When purchase.asset_id is already set, no new Equipment should be created.
    The existing Equipment's status should be updated and is_received set.
    """
    uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
        assert user is not None

        proj = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
        assert proj is not None

        # Pre-create the Equipment (Design A scenario)
        eq = await _seed_equipment(db, user, uid=uid)
        eq_id = eq.id

        purchase = await _seed_purchase(db, user, proj, uid)
        purchase.asset_id = eq_id   # pre-link
        await db.flush()
        await db.commit()
        purchase_id = purchase.id

    try:
        override_user(user)

        # Use the primary new route (it handles both cases)
        resp = client.post(
            f"/api/v1/equipment/rental-in/{purchase_id}/receive",
            json={
                "equipment_name": "Should not be used",
                "equipment_code": f"SHOULD-NOT-{uid}",
                "condition": "GOOD",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "received"
        assert data["equipment_id"] == eq_id, "Should return the pre-existing equipment_id"

        # Verify no duplicate Equipment was created
        async with AsyncSessionLocal() as db:
            count = await db.scalar(
                select(Equipment).where(Equipment.equipment_code == f"SHOULD-NOT-{uid}".upper())
            )
            assert count is None, "A duplicate Equipment should NOT have been created"

            purch = await db.get(EquipmentPurchase, purchase_id)
            assert purch.asset_id == eq_id
            assert purch.is_received is True

    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(EquipmentPurchase).where(EquipmentPurchase.id == purchase_id))
            await db.execute(delete(Equipment).where(Equipment.id == eq_id))
            await db.commit()


# ---------------------------------------------------------------------------
# Scenario 3 – Duplicate receive safety
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_receive_is_safe():
    """
    Calling receive twice must NOT create a second Equipment record.
    The second call should return a success response with the original equipment_id.
    """
    uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
        proj = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
        purchase = await _seed_purchase(db, user, proj, uid)
        await db.commit()
        purchase_id = purchase.id

    created_ids = []
    try:
        override_user(user)

        payload = {
            "equipment_name": f"Duplicate Test Eq {uid}",
            "equipment_code": f"DUP-RIN-{uid}",
            "condition": "GOOD",
        }

        # First receive
        r1 = client.post(f"/api/v1/equipment/rental-in/{purchase_id}/receive", json=payload)
        assert r1.status_code == 200, r1.text
        eq_id_first = r1.json()["equipment_id"]
        created_ids.append(eq_id_first)

        # Second receive – must NOT create another Equipment
        r2 = client.post(f"/api/v1/equipment/rental-in/{purchase_id}/receive", json=payload)
        # Should succeed (idempotent) – the implementation returns status=received with same equipment_id
        assert r2.status_code == 200, f"Second receive unexpected failure: {r2.text}"
        eq_id_second = r2.json()["equipment_id"]

        assert eq_id_first == eq_id_second, "Second receive must return same equipment_id"

        # Verify only ONE Equipment exists for this code
        async with AsyncSessionLocal() as db:
            results = (await db.execute(
                select(Equipment).where(
                    Equipment.equipment_code == f"DUP-RIN-{uid}".upper(),
                    Equipment.is_deleted == False,
                )
            )).scalars().all()
            assert len(results) == 1, f"Expected 1 Equipment, found {len(results)}"

    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(EquipmentPurchase).where(EquipmentPurchase.id == purchase_id))
            for eid in created_ids:
                await db.execute(delete(Equipment).where(Equipment.id == eid))
            await db.commit()


# ---------------------------------------------------------------------------
# Scenario 4 – Full Rental-IN E2E flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_rental_in_e2e_flow():
    """
    Critical E2E: runs the complete Rental-IN lifecycle using only API calls.

    Vendor → Agreement → Receive (auto Equipment) → Allocate → Usage → Bill → Return

    No manual Equipment creation is performed.
    """
    uid = uuid.uuid4().hex[:6]
    today = date.today()

    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
        assert user is not None
        proj = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
        assert proj is not None

        from app.models.material import Supplier
        supplier = Supplier(
            company_id=1,
            supplier_name=f"E2E-VENDOR-{uid}",
            contact_person="E2E Test",
            phone_email="9999999999 / vendor@test.com",
            gst_number=f"GST{uid}",
            address="123 Test St",
        )
        db.add(supplier)
        await db.commit()
        sup_id = supplier.id

    # We capture initial state and create purchase outside of the main cleanup try block.
    # It's okay if this throws because cleanup only needs to run if purchase or equipment is created.

    override_user(user)

    # ===== STEP 0: Capture Initial Project Allocations =====
    resp_initial = client.get("/api/v1/expenses/project-allocations")
    assert resp_initial.status_code == 200
    initial_data = resp_initial.json()
    initial_proj = next((p for p in initial_data["projects"] if p["project_name"] == proj.project_name), None)
    initial_equip_cost = initial_proj.get("equipment_cost", 0.0) if initial_proj else 0.0

    # ===== STEP 1: CREATE RENTAL-IN PURCHASE =====
    async with AsyncSessionLocal() as db:
        purchase = EquipmentPurchase(
            project_id=proj.id,
            supplier_id=sup_id,
            purchase_type=PurchaseType.RENT,
            vendor_name=f"E2E-RENTIN-VENDOR-{uid}",
            invoice_number=f"E2E-RENTIN-INV-{uid}",
            purchase_date=today,
            quantity=1,
            unit_price=Decimal("5000.00"),
            total_amount=Decimal("5000.00"),
            start_date=today,
            expected_end_date=today + timedelta(days=30),
            is_received=False,
            is_returned=False,
        )
        db.add(purchase)
        await db.commit()
        await db.refresh(purchase)
        purchase_id = purchase.id
        proj_id = proj.id

    equipment_id = None
    try:
        override_user(user)

        # ===== STEP 1: VERIFY no Equipment yet =====
        async with AsyncSessionLocal() as db:
            p = await db.get(EquipmentPurchase, purchase_id)
            assert p.asset_id is None, "asset_id must be NULL before receive"
            assert p.is_received is False

        # ===== STEP 2: RECEIVE (auto-creates Equipment) =====
        resp_receive = client.post(
            f"/api/v1/equipment/rental-in/{purchase_id}/receive",
            json={
                "equipment_name": f"E2E-RENTIN-{uid}",
                "equipment_code": f"E2E-RENTIN-{uid}",
                "condition": "GOOD",
            },
        )
        assert resp_receive.status_code == 200, f"Receive failed: {resp_receive.text}"
        receive_data = resp_receive.json()
        assert receive_data["status"] == "received"
        equipment_id = receive_data["equipment_id"]
        assert equipment_id is not None

        # ===== STEP 3: DB Verification after receive =====
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, equipment_id)
            assert eq is not None
            assert eq.status == EquipmentStatus.AVAILABLE
            assert eq.project_id is None

            p = await db.get(EquipmentPurchase, purchase_id)
            assert p.asset_id == equipment_id
            assert p.is_received is True

        # ===== STEP 4: ALLOCATE to project =====
        resp_alloc = client.post(
            "/api/v1/equipment/allocate",
            json={"equipment_ids": [equipment_id], "project_id": proj_id},
        )
        assert resp_alloc.status_code == 200, f"Allocate failed: {resp_alloc.text}"

        # ===== STEP 5: USAGE =====
        resp_usage = client.post(
            f"/api/v1/equipment/{equipment_id}/usage",
            json={
                "working_hours": "8.00",
                "fuel_used": "20.00",
                "fuel_cost": "1200.00",
                "usage_date": str(today),
            },
        )
        assert resp_usage.status_code in (200, 201), f"Usage failed: {resp_usage.text}"

        # ===== STEP 6: VENDOR BILL =====
        resp_bill = client.post(
            f"/api/v1/equipment/{equipment_id}/rental-in/{purchase_id}/vendor-bill",
        )
        assert resp_bill.status_code == 200, f"Vendor Bill failed: {resp_bill.text}"
        bill_data = resp_bill.json()
        assert "vendor_bill_id" in bill_data
        assert bill_data["vendor_bill_id"] is not None

        # ===== STEP 6.5: Project Cost (Financial Integrity & Double-Counting Check) =====
        resp_cost = client.get("/api/v1/expenses/project-allocations")
        assert resp_cost.status_code == 200, f"Project Cost failed: {resp_cost.text}"

        # Verify the equipment cost includes Rental-IN total_amount + fuel, but NOT usage rental-derived cost.
        cost_data = resp_cost.json()
        project_allocation = next((p for p in cost_data["projects"] if p["project_name"] == proj.project_name), None)
        assert project_allocation is not None, "Project missing from allocations"

        final_equip_cost = project_allocation.get("equipment_cost", 0.0)

        # Calculate expected increment
        # 5000.0 (Rental-IN total_amount) + 1200.0 (Usage fuel_cost)
        # 8 hours * 5000.0 = 40000.0 (Usage.cost) MUST NOT BE INCLUDED.
        expected_increment = 5000.0 + 1200.0
        actual_increment = final_equip_cost - initial_equip_cost

        assert abs(actual_increment - expected_increment) < 0.01, (
            f"Double counting detected! Expected increment {expected_increment}, got {actual_increment}. "
            f"Initial: {initial_equip_cost}, Final: {final_equip_cost}"
        )

        # ===== STEP 7: DEALLOCATE before return =====
        resp_dealloc = client.put(
            "/api/v1/equipment/deallocate",
            json={"equipment_ids": [equipment_id], "project_id": proj_id},
        )
        assert resp_dealloc.status_code == 200, f"Deallocate failed: {resp_dealloc.text}"

        # ===== STEP 8: RETURN =====
        resp_return = client.post(
            f"/api/v1/equipment/{equipment_id}/rental-in/{purchase_id}/return",
        )
        assert resp_return.status_code == 200, f"Return failed: {resp_return.text}"
        assert resp_return.json()["status"] == "returned"

        # ===== STEP 9: Final state verification =====
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, equipment_id)
            assert eq.status == EquipmentStatus.IDLE, f"Expected IDLE after return, got {eq.status}"

            p = await db.get(EquipmentPurchase, purchase_id)
            assert p.is_returned is True
            assert p.actual_return_date is not None

    finally:
        from app.models.equipment import EquipmentUsage
        async with AsyncSessionLocal() as db:
            if equipment_id:
                await db.execute(delete(EquipmentUsage).where(EquipmentUsage.equipment_id == equipment_id))
            await db.execute(delete(EquipmentPurchase).where(EquipmentPurchase.id == purchase_id))
            if equipment_id:
                await db.execute(delete(Equipment).where(Equipment.id == equipment_id))
            await db.commit()


# ---------------------------------------------------------------------------
# Scenario 5 – Negative / error cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_receive_invalid_purchase_id_returns_404():
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))

    override_user(user)
    resp = client.post(
        "/api/v1/equipment/rental-in/99999999/receive",
        json={"equipment_name": "Valid Name", "equipment_code": "Y-ERR-299"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_receive_wrong_purchase_type_returns_400():
    uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
        proj = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))

        # Create a PURCHASE (not RENT) purchase
        from app.core.enums import PurchaseType as PT
        purchase = EquipmentPurchase(
            project_id=proj.id,
            purchase_type=PT.NEW,
            vendor_name=f"Vendor {uid}",
            invoice_number=f"NON-RENT-{uid}",
            purchase_date=date.today(),
            quantity=1,
            unit_price=Decimal("1000.00"),
            total_amount=Decimal("1000.00"),
            is_received=False,
            is_returned=False,
        )
        db.add(purchase)
        await db.commit()
        purchase_id = purchase.id

    try:
        override_user(user)
        resp = client.post(
            f"/api/v1/equipment/rental-in/{purchase_id}/receive",
            json={"equipment_name": "Valid Name", "equipment_code": f"WRONG-{uid}"},
        )
        assert resp.status_code == 400, f"Expected 400 for non-RENT purchase, got {resp.status_code}"
        assert "RENT" in resp.json().get("detail", "").upper() or "rental" in resp.json().get("detail", "").lower()
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(EquipmentPurchase).where(EquipmentPurchase.id == purchase_id))
            await db.commit()


@pytest.mark.asyncio
async def test_receive_duplicate_equipment_code_returns_400():
    """Creating a second equipment with an already-used code must fail with 400."""
    uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
        proj = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))

        # Pre-create an Equipment with this code
        existing = Equipment(
            company_id=user.company_id,
            equipment_name=f"Existing Eq {uid}",
            equipment_code=f"DUPE-CODE-{uid}",
            status=EquipmentStatus.AVAILABLE,
            condition=EquipmentCondition.GOOD,
            working_hours=Decimal("0"),
            fuel_used=Decimal("0"),
            rental_cost=Decimal("0"),
        )
        db.add(existing)

        purchase = EquipmentPurchase(
            project_id=proj.id,
            purchase_type=PurchaseType.RENT,
            vendor_name=f"Vendor {uid}",
            invoice_number=f"DUPE-INV-{uid}",
            purchase_date=date.today(),
            quantity=1,
            unit_price=Decimal("1000.00"),
            total_amount=Decimal("1000.00"),
            is_received=False,
            is_returned=False,
        )
        db.add(purchase)
        await db.flush()
        existing_id = existing.id
        purchase_id = purchase.id
        await db.commit()

    try:
        override_user(user)
        resp = client.post(
            f"/api/v1/equipment/rental-in/{purchase_id}/receive",
            json={
                "equipment_name": f"Rental Eq {uid}",
                "equipment_code": f"DUPE-CODE-{uid}",  # duplicate code
            },
        )
        assert resp.status_code == 400, f"Expected 400 for duplicate code, got {resp.status_code}: {resp.text}"
        assert "already in use" in resp.json().get("detail", "").lower()
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(EquipmentPurchase).where(EquipmentPurchase.id == purchase_id))
            await db.execute(delete(Equipment).where(Equipment.id == existing_id))
            await db.commit()
