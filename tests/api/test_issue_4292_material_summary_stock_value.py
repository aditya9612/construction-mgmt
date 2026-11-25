import pytest
import pytest_asyncio
import uuid
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.user import User, UserRole, ActivityLog
from app.models.master_data import MaterialMaster, Unit
from app.models.material import Material, Supplier
from app.core.dependencies import get_current_user, get_current_active_user


@pytest.fixture
def run_uid():
    return uuid.uuid4().hex[:6]


@pytest_asyncio.fixture
async def setup_mat_context(run_uid):
    async with AsyncSessionLocal() as db:
        comp_a = Company(name=f"CompA_4292_{run_uid}", is_active=True)
        comp_b = Company(name=f"CompB_4292_{run_uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OA_{run_uid}",
            owner_name=f"OwnerA_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OB_{run_uid}",
            owner_name=f"OwnerB_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a1 = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"ProjA1_{run_uid}",
            business_id=f"PA1-{run_uid}",
            status="Ongoing",
        )
        proj_a2 = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"ProjA2_{run_uid}",
            business_id=f"PA2-{run_uid}",
            status="Ongoing",
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"ProjB_{run_uid}",
            business_id=f"PB-{run_uid}",
            status="Ongoing",
        )
        db.add_all([proj_a1, proj_a2, proj_b])
        await db.flush()

        user_a = User(
            company_id=comp_a.id,
            email=f"admin_a_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        user_b = User(
            company_id=comp_b.id,
            email=f"admin_b_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([user_a, user_b])
        await db.flush()

        unit = await db.scalar(select(Unit))
        if not unit:
            unit = Unit(name=f"Unit_{run_uid}")
            db.add(unit)
            await db.flush()

        m1 = MaterialMaster(company_id=comp_a.id, name=f"Cement1_{run_uid}", category="CIVIL", unit_id=unit.id)
        m2 = MaterialMaster(company_id=comp_a.id, name=f"Steel2_{run_uid}", category="CIVIL", unit_id=unit.id)
        m3 = MaterialMaster(company_id=comp_a.id, name=f"Sand3_{run_uid}", category="CIVIL", unit_id=unit.id)
        m4 = MaterialMaster(company_id=comp_b.id, name=f"Brick4_{run_uid}", category="CIVIL", unit_id=unit.id)
        m5 = MaterialMaster(company_id=comp_a.id, name=f"Paint5_{run_uid}", category="CIVIL", unit_id=unit.id)
        db.add_all([m1, m2, m3, m4, m5])
        await db.flush()

        supplier = Supplier(
            company_id=comp_a.id,
            supplier_name=f"Supp_{run_uid}",
        )
        db.add(supplier)
        await db.flush()

        # Material 1: Zero quantity_purchased, but remaining_stock = 50, purchase_rate = 100
        # (Opening stock / adjustment case where total_amount = 0)
        mat1 = Material(
            material_code=f"MAT1-{run_uid}",
            project_id=proj_a1.id,
            material_master_id=m1.id,
            supplier_id=supplier.id,
            material_name="Cement Bag Grade 53",
            category="CIVIL",
            unit_id=unit.id,
            rate_type="FIXED",
            purchase_rate=Decimal("100.00"),
            quantity_purchased=Decimal("0.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("50.000"),
            total_amount=Decimal("0.00"),
            payment_given=Decimal("0.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )
        # Material 2: Standard purchase 100 units at 200 = 20000, 20 used, 80 remaining
        mat2 = Material(
            material_code=f"MAT2-{run_uid}",
            project_id=proj_a1.id,
            material_master_id=m2.id,
            supplier_id=supplier.id,
            material_name="Steel TMT Bar 12mm",
            category="CIVIL",
            unit_id=unit.id,
            rate_type="FIXED",
            purchase_rate=Decimal("200.00"),
            quantity_purchased=Decimal("100.000"),
            quantity_used=Decimal("20.000"),
            remaining_stock=Decimal("80.000"),
            total_amount=Decimal("20000.00"),
            payment_given=Decimal("15000.00"),
            payment_pending=Decimal("5000.00"),
            is_deleted=False,
        )
        # Material 3: Soft-deleted material (must be excluded from summary)
        mat3 = Material(
            material_code=f"MAT3-{run_uid}",
            project_id=proj_a1.id,
            material_master_id=m3.id,
            supplier_id=supplier.id,
            material_name="Deleted Material",
            category="CIVIL",
            unit_id=unit.id,
            rate_type="FIXED",
            purchase_rate=Decimal("500.00"),
            quantity_purchased=Decimal("10.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("10.000"),
            total_amount=Decimal("5000.00"),
            payment_given=Decimal("0.00"),
            payment_pending=Decimal("5000.00"),
            is_deleted=True,
        )
        # Material 4: Company B material (tenant isolation check)
        mat4 = Material(
            material_code=f"MAT4-{run_uid}",
            project_id=proj_b.id,
            material_master_id=m4.id,
            supplier_id=supplier.id,
            material_name="Company B Material",
            category="CIVIL",
            unit_id=unit.id,
            rate_type="FIXED",
            purchase_rate=Decimal("999.00"),
            quantity_purchased=Decimal("10.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("10.000"),
            total_amount=Decimal("9990.00"),
            payment_given=Decimal("0.00"),
            payment_pending=Decimal("9990.00"),
            is_deleted=False,
        )
        # Material 5: Project A2 material (project filtering check)
        mat5 = Material(
            material_code=f"MAT5-{run_uid}",
            project_id=proj_a2.id,
            material_master_id=m5.id,
            supplier_id=supplier.id,
            material_name="Project A2 Material",
            category="CIVIL",
            unit_id=unit.id,
            rate_type="FIXED",
            purchase_rate=Decimal("300.00"),
            quantity_purchased=Decimal("10.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("10.000"),
            total_amount=Decimal("3000.00"),
            payment_given=Decimal("3000.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )
        db.add_all([mat1, mat2, mat3, mat4, mat5])
        await db.commit()

        context = {
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "user_a": user_a,
            "user_b": user_b,
            "proj_a1_id": proj_a1.id,
            "proj_a2_id": proj_a2.id,
            "proj_b_id": proj_b.id,
            "mat1_id": mat1.id,
            "mat2_id": mat2.id,
            "mat3_id": mat3.id,
            "mat4_id": mat4.id,
            "mat5_id": mat5.id,
            "m_ids": [m1.id, m2.id, m3.id, m4.id, m5.id],
            "supplier_id": supplier.id,
        }

    yield context

    # Cleanup
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Material).where(Material.id.in_([mat1.id, mat2.id, mat3.id, mat4.id, mat5.id])))
        await db.execute(delete(Supplier).where(Supplier.id == supplier.id))
        await db.execute(delete(MaterialMaster).where(MaterialMaster.id.in_(context["m_ids"])))
        await db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_([user_a.id, user_b.id])))
        await db.execute(delete(User).where(User.id.in_([user_a.id, user_b.id])))
        await db.execute(delete(Project).where(Project.id.in_([proj_a1.id, proj_a2.id, proj_b.id])))
        await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
        await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
        await db.commit()


@pytest.mark.asyncio
async def test_material_summary_stock_value_with_zero_purchased_stock(setup_mat_context):
    """
    Issue #4292: Material with quantity_purchased=0 (opening stock/adjustment)
    must NOT drop to ₹0 valuation. It must be valued at remaining_stock * purchase_rate.
    mat1: remaining=50, purchase_rate=100 => 5000
    mat2: remaining=80, purchase_rate=200 => 16000
    Total expected stock value in proj_a1 = 21000.00 (NOT 16000.00).
    """
    ctx = setup_mat_context
    app.dependency_overrides[get_current_user] = lambda: ctx["user_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["user_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/materials/summary?project_id={ctx['proj_a1_id']}")
            assert res.status_code == 200, res.text
            data = res.json()

            assert data["total_materials"] == 2
            # 50 * 100 + 80 * 200 = 5000 + 16000 = 21000.00
            assert data["total_stock_value"] == 21000.00
            assert data["total_pending_payments"] == 5000.00
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_material_summary_excludes_soft_deleted_material(setup_mat_context):
    """
    Summary across the company must exclude soft-deleted materials (mat3).
    Active materials for comp_a:
    mat1 (proj_a1): 50 * 100 = 5000
    mat2 (proj_a1): 80 * 200 = 16000
    mat5 (proj_a2): 10 * 300 = 3000
    Total = 24000.00
    """
    ctx = setup_mat_context
    app.dependency_overrides[get_current_user] = lambda: ctx["user_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["user_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/materials/summary")
            assert res.status_code == 200, res.text
            data = res.json()

            assert data["total_materials"] == 3
            assert data["total_stock_value"] == 24000.00
            assert data["total_pending_payments"] == 5000.00
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_material_summary_tenant_isolation(setup_mat_context):
    """
    Company B user must only see Company B materials, never Company A.
    """
    ctx = setup_mat_context
    app.dependency_overrides[get_current_user] = lambda: ctx["user_b"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["user_b"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/materials/summary")
            assert res.status_code == 200, res.text
            data = res.json()

            # Company B has only mat4: remaining=10, rate=999 => 9990.00
            assert data["total_materials"] == 1
            assert data["total_stock_value"] == 9990.00
            assert data["total_pending_payments"] == 9990.00
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_inventory_valuation_endpoint_consistency(setup_mat_context):
    """
    GET /api/v1/materials/inventory/valuation must also value mat1 correctly.
    """
    ctx = setup_mat_context
    app.dependency_overrides[get_current_user] = lambda: ctx["user_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["user_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/materials/inventory/valuation?project_id={ctx['proj_a1_id']}")
            assert res.status_code == 200, res.text
            data = res.json()

            assert data["total_value"] == 21000.00
    finally:
        app.dependency_overrides.clear()
