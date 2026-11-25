import uuid
from decimal import Decimal
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.user import User, UserRole
from app.models.project import Project
from app.models.master_data import MaterialMaster, Unit
from app.models.material import Material, Supplier, MaterialTransaction, MaterialLedger
from app.models.owner import Owner
from app.core.enums import RateType
from app.core.dependencies import (
    get_current_user,
    get_current_active_user,
    require_permission,
    require_roles,
)


@pytest_asyncio.fixture
async def setup_material_test_context():
    run_uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_4386_{run_uid}", is_active=True)
        db.add(comp)
        await db.flush()

        admin = User(
            company_id=comp.id,
            email=f"admin_4386_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=True,
        )
        db.add(admin)
        await db.flush()

        owner = Owner(
            company_id=comp.id,
            owner_code=f"OA_4386_{run_uid}",
            owner_name=f"OwnerA_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            company_id=comp.id,
            owner_id=owner.id,
            project_name=f"Proj_4386_{run_uid}",
            business_id=f"P4386-{run_uid}",
            status="Ongoing",
        )
        db.add(proj)
        await db.flush()

        sup = Supplier(
            company_id=comp.id,
            supplier_name=f"Supplier_4386_{run_uid}",
            phone_email="9876543210",
        )
        unit = Unit(name=f"Unit_4386_{run_uid}")
        db.add_all([sup, unit])
        await db.flush()

        mm1 = MaterialMaster(name=f"MM1_4386_{run_uid}", category="Cement", unit_id=unit.id)
        mm2 = MaterialMaster(name=f"MM2_4386_{run_uid}", category="Steel", unit_id=unit.id)
        mm3 = MaterialMaster(name=f"MM3_4386_{run_uid}", category="Aggregate", unit_id=unit.id)
        db.add_all([mm1, mm2, mm3])
        await db.commit()

        admin_id = admin.id
        comp_id = comp.id
        proj_id = proj.id
        mm1_id = mm1.id
        mm2_id = mm2.id
        mm3_id = mm3.id
        unit_id = unit.id
        sup_id = sup.id

    mock_admin = User(
        id=admin_id,
        company_id=comp_id,
        email=f"admin_4386_{run_uid}@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=True,
    )
    mock_admin.project_memberships = []

    # Setup auth dependency overrides
    app.dependency_overrides[get_current_user] = lambda: mock_admin
    app.dependency_overrides[get_current_active_user] = lambda: mock_admin
    app.dependency_overrides[require_permission("materials.create")] = lambda: mock_admin
    app.dependency_overrides[require_permission("materials.edit")] = lambda: mock_admin
    app.dependency_overrides[require_roles([UserRole.ADMIN.value])] = lambda: mock_admin

    context = {
        "comp_id": comp_id,
        "proj_id": proj_id,
        "admin": mock_admin,
        "mm1_id": mm1_id,
        "mm2_id": mm2_id,
        "mm3_id": mm3_id,
        "unit_id": unit_id,
        "sup_id": sup_id,
    }

    yield context

    # Cleanup overrides and test database entities
    app.dependency_overrides.clear()
    async with AsyncSessionLocal() as db:
        from app.models.user import ActivityLog
        mats = await db.scalars(select(Material.id).where(Material.project_id == context["proj_id"]))
        mat_ids = mats.all()

        if mat_ids:
            await db.execute(MaterialLedger.__table__.delete().where(MaterialLedger.material_id.in_(mat_ids)))
            await db.execute(MaterialTransaction.__table__.delete().where(MaterialTransaction.material_id.in_(mat_ids)))
            await db.execute(Material.__table__.delete().where(Material.id.in_(mat_ids)))

        await db.execute(ActivityLog.__table__.delete().where(ActivityLog.performed_by == context["admin"].id))
        await db.execute(MaterialMaster.__table__.delete().where(MaterialMaster.id.in_([context["mm1_id"], context["mm2_id"], context["mm3_id"]])))
        await db.execute(Unit.__table__.delete().where(Unit.id == context["unit_id"]))
        await db.execute(Project.__table__.delete().where(Project.company_id == context["comp_id"]))
        await db.execute(Supplier.__table__.delete().where(Supplier.company_id == context["comp_id"]))
        await db.execute(Owner.__table__.delete().where(Owner.company_id == context["comp_id"]))
        await db.execute(User.__table__.delete().where(User.company_id == context["comp_id"]))
        await db.execute(Company.__table__.delete().where(Company.id == context["comp_id"]))
        await db.commit()


@pytest.mark.asyncio
async def test_material_creation_calculates_total_amount(setup_material_test_context):
    """
    Test 1: Valid creation calculates total_amount = quantity_purchased * purchase_rate
    and sets payment_pending accordingly.
    """
    ctx = setup_material_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "project_id": ctx["proj_id"],
            "material_master_id": ctx["mm1_id"],
            "supplier_id": ctx["sup_id"],
            "purchase_rate": 120.00,
            "rate_type": "PER_UNIT",
            "quantity_purchased": 50.000,
            "payment_given": 0.00,
            "minimum_stock_level": 5.000,
        }
        res = await client.post("/api/v1/materials", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["total_amount"] == 6000.0  # 50 * 120.00
        assert data["purchase_rate"] == 120.0
        assert data["payment_pending"] == 6000.0
        mat_id = data["id"]

        # Verify persisted DB row
        async with AsyncSessionLocal() as db:
            m = await db.get(Material, mat_id)
            assert m.total_amount == Decimal("6000.00")
            assert m.purchase_rate == Decimal("120.00")
            assert m.quantity_purchased == Decimal("50.000")
            assert m.payment_pending == Decimal("6000.00")


@pytest.mark.asyncio
async def test_material_update_purchase_rate_recalculates_total_and_financials(setup_material_test_context):
    """
    Test 2: Updating purchase_rate via PUT /materials/{id} recalculates total_amount
    using ROUND_HALF_UP and automatically refreshes payment_pending.
    """
    ctx = setup_material_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create initial material: 50 units @ 120.00 = 6000.00
        payload = {
            "project_id": ctx["proj_id"],
            "material_master_id": ctx["mm1_id"],
            "supplier_id": ctx["sup_id"],
            "purchase_rate": 120.00,
            "rate_type": "PER_UNIT",
            "quantity_purchased": 50.000,
            "payment_given": 0.00,
            "minimum_stock_level": 5.000,
        }
        create_res = await client.post("/api/v1/materials", json=payload)
        assert create_res.status_code == 201
        mat_id = create_res.json()["id"]

        # Update purchase_rate to 150.00
        update_payload = {"purchase_rate": 150.00}
        upd_res = await client.put(f"/api/v1/materials/{mat_id}", json=update_payload)
        assert upd_res.status_code == 200
        upd_data = upd_res.json()

        # Expected: 50.000 * 150.00 = 7500.00
        assert upd_data["purchase_rate"] == 150.0
        assert upd_data["total_amount"] == 7500.0
        assert upd_data["payment_pending"] == 7500.0

        # Verify DB row
        async with AsyncSessionLocal() as db:
            m = await db.get(Material, mat_id)
            assert m.purchase_rate == Decimal("150.00")
            assert m.total_amount == Decimal("7500.00")
            assert m.payment_pending == Decimal("7500.00")


@pytest.mark.asyncio
async def test_material_update_unchanged_rate_preserves_total(setup_material_test_context):
    """
    Test 3: Unchanged-rate updates (modifying other fields like minimum_stock_level or passing
    the exact same rate) do not trigger unnecessary recalculations of total_amount.
    """
    ctx = setup_material_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "project_id": ctx["proj_id"],
            "material_master_id": ctx["mm1_id"],
            "supplier_id": ctx["sup_id"],
            "purchase_rate": 100.00,
            "rate_type": "PER_UNIT",
            "quantity_purchased": 30.000,
            "payment_given": 0.00,
            "minimum_stock_level": 5.000,
        }
        create_res = await client.post("/api/v1/materials", json=payload)
        assert create_res.status_code == 201
        mat_id = create_res.json()["id"]
        assert create_res.json()["total_amount"] == 3000.0

        # 1. Update minimum_stock_level only (no purchase_rate in payload)
        res1 = await client.put(f"/api/v1/materials/{mat_id}", json={"minimum_stock_level": 20.000})
        assert res1.status_code == 200
        assert res1.json()["total_amount"] == 3000.0
        assert res1.json()["minimum_stock_level"] == 20.0

        # 2. Update with identical purchase_rate: 100.00
        res2 = await client.put(f"/api/v1/materials/{mat_id}", json={"purchase_rate": 100.00})
        assert res2.status_code == 200
        assert res2.json()["total_amount"] == 3000.0

        # Verify DB row
        async with AsyncSessionLocal() as db:
            m = await db.get(Material, mat_id)
            assert m.total_amount == Decimal("3000.00")
            assert m.purchase_rate == Decimal("100.00")


@pytest.mark.asyncio
async def test_financial_fields_recalculation_with_advance(setup_material_test_context):
    """
    Test 4: Financial field recalculation handles payment_pending and advance_amount
    when rate changes make total_amount smaller than payment_given.
    """
    ctx = setup_material_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 10 units @ 100.00 = 1000.00, payment_given = 600.00
        payload = {
            "project_id": ctx["proj_id"],
            "material_master_id": ctx["mm1_id"],
            "supplier_id": ctx["sup_id"],
            "purchase_rate": 100.00,
            "rate_type": "PER_UNIT",
            "quantity_purchased": 10.000,
            "payment_given": 600.00,
            "minimum_stock_level": 2.000,
        }
        create_res = await client.post("/api/v1/materials", json=payload)
        assert create_res.status_code == 201
        mat_id = create_res.json()["id"]
        assert create_res.json()["total_amount"] == 1000.0
        assert create_res.json()["payment_pending"] == 400.0
        assert create_res.json()["extra_paid"] == 0.0

        # Reduce rate to 50.00 -> new total_amount = 500.00
        # Since payment_given (600.00) > total (500.00):
        # payment_pending = 0, advance_amount (extra_paid) = 100.00
        upd_res = await client.put(f"/api/v1/materials/{mat_id}", json={"purchase_rate": 50.00})
        assert upd_res.status_code == 200
        data = upd_res.json()
        assert data["total_amount"] == 500.0
        assert data["payment_pending"] == 0.0
        assert data["extra_paid"] == 100.0

        async with AsyncSessionLocal() as db:
            m = await db.get(Material, mat_id)
            assert m.total_amount == Decimal("500.00")
            assert m.payment_pending == Decimal("0.00")
            assert m.advance_amount == Decimal("100.00")


@pytest.mark.asyncio
async def test_supported_rate_types_consistency(setup_material_test_context):
    """
    Test 5: Supported rate types (FIXED, PER_UNIT, PER_KG, PER_TON, PER_BAG)
    all compute and recompute total_amount identically according to standard units.
    """
    ctx = setup_material_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create material with PER_KG
        payload = {
            "project_id": ctx["proj_id"],
            "material_master_id": ctx["mm1_id"],
            "supplier_id": ctx["sup_id"],
            "purchase_rate": 45.50,
            "rate_type": "PER_KG",
            "quantity_purchased": 100.000,
            "payment_given": 0.00,
            "minimum_stock_level": 10.000,
        }
        res = await client.post("/api/v1/materials", json=payload)
        assert res.status_code == 201
        mat_id = res.json()["id"]
        assert res.json()["total_amount"] == 4550.0

        # Cycle through other rate types
        for rt in ["PER_TON", "PER_BAG", "FIXED", "PER_UNIT"]:
            upd = await client.put(f"/api/v1/materials/{mat_id}", json={"rate_type": rt, "purchase_rate": 50.00})
            assert upd.status_code == 200
            data = upd.json()
            assert data["rate_type"] == rt
            assert data["total_amount"] == 5000.0  # 100 * 50.00

            # Verify persisted DB row
            async with AsyncSessionLocal() as db:
                m = await db.get(Material, mat_id)
                assert str(m.rate_type) in [f"RateType.{rt}", rt]
                assert m.total_amount == Decimal("5000.00")
                assert m.purchase_rate == Decimal("50.00")


@pytest.mark.asyncio
async def test_direct_update_protection_blocked_fields(setup_material_test_context):
    """
    Test 6: Direct updates to quantity_purchased and total_amount remain protected
    and cannot be tampered with through the PUT endpoint.
    """
    ctx = setup_material_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "project_id": ctx["proj_id"],
            "material_master_id": ctx["mm1_id"],
            "supplier_id": ctx["sup_id"],
            "purchase_rate": 100.00,
            "rate_type": "PER_UNIT",
            "quantity_purchased": 20.000,
            "payment_given": 0.00,
            "minimum_stock_level": 5.000,
        }
        create_res = await client.post("/api/v1/materials", json=payload)
        assert create_res.status_code == 201
        mat_id = create_res.json()["id"]

        # Attempt to tamper with total_amount or quantity_purchased directly
        tamper_res = await client.put(f"/api/v1/materials/{mat_id}", json={
            "total_amount": 99999.00,
            "quantity_purchased": 999.000
        })
        # The schema and blocked_fields ensure total_amount and quantity_purchased are unchanged
        assert tamper_res.status_code in [200, 400]
        if tamper_res.status_code == 200:
            tamper_data = tamper_res.json()
            assert tamper_data["total_amount"] == 2000.0
            assert tamper_data["quantity_purchased"] == 20.0

        # Verify DB row is completely unmutated
        async with AsyncSessionLocal() as db:
            m = await db.get(Material, mat_id)
            assert m.total_amount == Decimal("2000.00")
            assert m.quantity_purchased == Decimal("20.000")


@pytest.mark.asyncio
async def test_repeated_purchases_weighted_average_cumulative_cost_semantics(setup_material_test_context):
    """
    Test 7 (Requirement 9): Repeated purchases via POST /purchase use weighted-average rate
    for inventory pricing while preserving exact cumulative historical purchase cost semantics
    in total_amount without overwriting it with quantity * rounded_rate.
    """
    ctx = setup_material_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Initial purchase: 10 units @ 100.00 = 1000.00
        create_payload = {
            "project_id": ctx["proj_id"],
            "material_master_id": ctx["mm1_id"],
            "supplier_id": ctx["sup_id"],
            "purchase_rate": 100.00,
            "rate_type": "PER_UNIT",
            "quantity_purchased": 10.000,
            "payment_given": 0.00,
            "minimum_stock_level": 5.000,
        }
        create_res = await client.post("/api/v1/materials", json=create_payload)
        assert create_res.status_code == 201
        mat_id = create_res.json()["id"]

        # Additional purchase 1: 3 units @ 105.00 = 315.00
        # New cumulative total = 1000.00 + 315.00 = 1315.00
        # New quantity = 13.000
        # Weighted average rate = (1000 + 315) / 13 = 101.1538... -> rounded to 101.15
        # Notice: 13.000 * 101.15 = 1314.95 (a 0.05 rounding divergence)
        # Accounting rule: total_amount MUST remain 1315.00 to preserve actual cumulative purchase cost.
        purchase_payload_1 = {
            "project_id": ctx["proj_id"],
            "quantity": 3.000,
            "rate": 105.00,
            "amount_paid": 0.00,
        }
        p_res1 = await client.post(f"/api/v1/materials/{mat_id}/purchase", json=purchase_payload_1)
        assert p_res1.status_code == 200
        p_data1 = p_res1.json()

        assert p_data1["quantity_purchased"] == 13.0
        assert p_data1["purchase_rate"] == 101.15
        assert p_data1["total_amount"] == 1315.0  # Exact cumulative cost preserved!

        async with AsyncSessionLocal() as db:
            m = await db.get(Material, mat_id)
            assert m.total_amount == Decimal("1315.00")
            assert m.purchase_rate == Decimal("101.15")
            assert m.quantity_purchased == Decimal("13.000")

        # Additional purchase 2: 4 units @ 110.00 = 440.00
        # New cumulative total = 1315.00 + 440.00 = 1755.00
        purchase_payload_2 = {
            "project_id": ctx["proj_id"],
            "quantity": 4.000,
            "rate": 110.00,
            "amount_paid": 0.00,
        }
        p_res2 = await client.post(f"/api/v1/materials/{mat_id}/purchase", json=purchase_payload_2)
        assert p_res2.status_code == 200
        p_data2 = p_res2.json()

        assert p_data2["quantity_purchased"] == 17.0
        assert p_data2["total_amount"] == 1755.0

        async with AsyncSessionLocal() as db:
            m = await db.get(Material, mat_id)
            assert m.total_amount == Decimal("1755.00")
            assert m.quantity_purchased == Decimal("17.000")
