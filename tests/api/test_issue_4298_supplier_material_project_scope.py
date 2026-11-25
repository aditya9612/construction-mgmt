import pytest
import pytest_asyncio
import uuid
from decimal import Decimal
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.user import User, UserRole
from app.models.master_data import MaterialMaster, Unit
from app.models.material import Material, Supplier
from app.core.dependencies import get_current_user, get_current_active_user


@pytest_asyncio.fixture
async def setup_supplier_materials_context():
    run_uid = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        comp_a = Company(name=f"CompA_4298_{run_uid}", is_active=True)
        comp_b = Company(name=f"CompB_4298_{run_uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OA_4298_{run_uid}",
            owner_name=f"OwnerA_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OB_4298_{run_uid}",
            owner_name=f"OwnerB_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a1 = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"ProjA1_{run_uid}",
            business_id=f"PA1-4298-{run_uid}",
            status="Ongoing",
        )
        proj_a2 = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"ProjA2_{run_uid}",
            business_id=f"PA2-4298-{run_uid}",
            status="Ongoing",
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"ProjB_{run_uid}",
            business_id=f"PB-4298-{run_uid}",
            status="Ongoing",
        )
        db.add_all([proj_a1, proj_a2, proj_b])
        await db.flush()

        admin_a = User(
            company_id=comp_a.id,
            email=f"admin_a_4298_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        admin_b = User(
            company_id=comp_b.id,
            email=f"admin_b_4298_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        user_pm_a = User(
            company_id=comp_a.id,
            email=f"pm_a_4298_{run_uid}@test.com",
            role=UserRole.PROJECT_MANAGER.value,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([admin_a, admin_b, user_pm_a])
        await db.flush()

        pm_member = ProjectMember(project_id=proj_a1.id, user_id=user_pm_a.id)
        db.add(pm_member)
        await db.flush()

        sup_a = Supplier(
            company_id=comp_a.id,
            supplier_name=f"SupplierA_4298_{run_uid}",
            phone_email="9876543210",
        )
        sup_empty = Supplier(
            company_id=comp_a.id,
            supplier_name=f"SupplierEmpty_4298_{run_uid}",
            phone_email="9876543212",
        )
        sup_b = Supplier(
            company_id=comp_b.id,
            supplier_name=f"SupplierB_4298_{run_uid}",
            phone_email="9876543211",
        )
        db.add_all([sup_a, sup_empty, sup_b])
        await db.flush()

        unit = Unit(name=f"Unit_4298_{run_uid}")
        db.add(unit)
        await db.flush()

        mm1 = MaterialMaster(name=f"MM1_4298_{run_uid}", category="Cement", unit_id=unit.id)
        mm2 = MaterialMaster(name=f"MM2_4298_{run_uid}", category="Steel", unit_id=unit.id)
        mm_b = MaterialMaster(name=f"MM_B_4298_{run_uid}", category="Wood", unit_id=unit.id)
        db.add_all([mm1, mm2, mm_b])
        await db.flush()

        # Material 1: In proj_a1 from sup_a
        mat_a1 = Material(
            material_code=f"MAT-A1-{run_uid}",
            project_id=proj_a1.id,
            material_master_id=mm1.id,
            supplier_id=sup_a.id,
            unit_id=unit.id,
            material_name="Cement 53 Grade",
            category="Cement",
            rate_type="FIXED",
            purchase_rate=Decimal("400.00"),
            quantity_purchased=Decimal("50.00"),
            remaining_stock=Decimal("50.00"),
            total_amount=Decimal("20000.00"),
            payment_given=Decimal("20000.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )
        # Material 2: In proj_a2 from sup_a
        mat_a2 = Material(
            material_code=f"MAT-A2-{run_uid}",
            project_id=proj_a2.id,
            material_master_id=mm2.id,
            supplier_id=sup_a.id,
            unit_id=unit.id,
            material_name="Steel TMT 12mm",
            category="Steel",
            rate_type="FIXED",
            purchase_rate=Decimal("80.00"),
            quantity_purchased=Decimal("200.00"),
            remaining_stock=Decimal("200.00"),
            total_amount=Decimal("16000.00"),
            payment_given=Decimal("16000.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )
        # Material B: In proj_b from sup_b
        mat_b = Material(
            material_code=f"MAT-B-{run_uid}",
            project_id=proj_b.id,
            material_master_id=mm_b.id,
            supplier_id=sup_b.id,
            unit_id=unit.id,
            material_name="Plywood Sheet",
            category="Wood",
            rate_type="FIXED",
            purchase_rate=Decimal("500.00"),
            quantity_purchased=Decimal("10.00"),
            remaining_stock=Decimal("10.00"),
            total_amount=Decimal("5000.00"),
            payment_given=Decimal("5000.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )
        db.add_all([mat_a1, mat_a2, mat_b])
        await db.commit()

        context = {
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "proj_a1_id": proj_a1.id,
            "proj_a2_id": proj_a2.id,
            "proj_b_id": proj_b.id,
            "admin_a": admin_a,
            "admin_b": admin_b,
            "user_pm_a": user_pm_a,
            "sup_a_id": sup_a.id,
            "sup_empty_id": sup_empty.id,
            "sup_b_id": sup_b.id,
            "mat_a1_id": mat_a1.id,
            "mat_a2_id": mat_a2.id,
            "mat_b_id": mat_b.id,
            "mm_ids": [mm1.id, mm2.id, mm_b.id],
            "unit_id": unit.id,
            "user_ids": [admin_a.id, admin_b.id, user_pm_a.id],
        }

    yield context

    # Teardown
    async with AsyncSessionLocal() as db:
        await db.execute(Material.__table__.delete().where(Material.id.in_([context["mat_a1_id"], context["mat_a2_id"], context["mat_b_id"]])))
        await db.execute(ProjectMember.__table__.delete().where(ProjectMember.user_id.in_(context["user_ids"])))
        await db.execute(MaterialMaster.__table__.delete().where(MaterialMaster.id.in_(context["mm_ids"])))
        await db.execute(Unit.__table__.delete().where(Unit.id == context["unit_id"]))
        await db.execute(Project.__table__.delete().where(Project.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.execute(Supplier.__table__.delete().where(Supplier.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.execute(Owner.__table__.delete().where(Owner.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.execute(User.__table__.delete().where(User.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.execute(Company.__table__.delete().where(Company.id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.commit()


@pytest.mark.asyncio
async def test_supplier_materials_with_valid_project(setup_supplier_materials_context):
    """Test retrieving materials supplied to a specific authorized project."""
    ctx = setup_supplier_materials_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_a_id']}/materials?project_id={ctx['proj_a1_id']}")
            assert res.status_code == 200, res.text
            items = res.json()
            assert len(items) == 1
            assert items[0]["id"] == ctx["mat_a1_id"]
            assert items[0]["project_id"] == ctx["proj_a1_id"]
            assert items[0]["supplier_id"] == ctx["sup_a_id"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_supplier_materials_without_project_company_scoped(setup_supplier_materials_context):
    """Admin calling without project_id receives all materials for this supplier across all company projects."""
    ctx = setup_supplier_materials_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_a_id']}/materials")
            assert res.status_code == 200, res.text
            items = res.json()
            assert len(items) == 2
            ids = {i["id"] for i in items}
            assert ids == {ctx["mat_a1_id"], ctx["mat_a2_id"]}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_supplier_materials_cross_company_isolation(setup_supplier_materials_context):
    """Calling supplier endpoint for a supplier belonging to another company must return 404."""
    ctx = setup_supplier_materials_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Company A admin tries to query Company B's supplier
            res = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_b_id']}/materials")
            assert res.status_code == 404
            assert "Supplier not found" in res.json()["detail"]

            # Company A admin queries own supplier with Company B's project_id
            res_cross_proj = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_a_id']}/materials?project_id={ctx['proj_b_id']}")
            assert res_cross_proj.status_code == 404
            assert "Project not found" in res_cross_proj.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_supplier_materials_non_admin_rbac(setup_supplier_materials_context, monkeypatch):
    """Non-admin user only sees materials from their allowed projects."""
    ctx = setup_supplier_materials_context
    import app.core.dependencies as deps
    monkeypatch.setattr(deps, "has_permission", lambda perms, perm: True)

    app.dependency_overrides[get_current_user] = lambda: ctx["user_pm_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["user_pm_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Querying allowed project proj_a1 succeeds
            res_ok = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_a_id']}/materials?project_id={ctx['proj_a1_id']}")
            assert res_ok.status_code == 200
            assert len(res_ok.json()) == 1

            # Querying company-wide without project_id returns only materials from allowed projects
            res_no_proj = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_a_id']}/materials")
            assert res_no_proj.status_code == 200
            items = res_no_proj.json()
            assert len(items) == 1
            assert items[0]["id"] == ctx["mat_a1_id"]

            # Querying unauthorized project proj_a2 returns 403 Forbidden
            res_denied = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_a_id']}/materials?project_id={ctx['proj_a2_id']}")
            assert res_denied.status_code == 403
            assert "Access denied" in res_denied.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_supplier_materials_empty_results(setup_supplier_materials_context):
    """Supplier with no materials linked returns an empty list."""
    ctx = setup_supplier_materials_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_empty_id']}/materials")
            assert res.status_code == 200
            assert res.json() == []
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_supplier_materials_pagination(setup_supplier_materials_context):
    """Pagination parameters skip and limit work as expected and are bounded."""
    ctx = setup_supplier_materials_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_lim1 = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_a_id']}/materials?limit=1")
            assert res_lim1.status_code == 200
            assert len(res_lim1.json()) == 1

            res_skip1 = await ac.get(f"/api/v1/materials/suppliers/{ctx['sup_a_id']}/materials?skip=1&limit=1")
            assert res_skip1.status_code == 200
            assert len(res_skip1.json()) == 1
            assert res_lim1.json()[0]["id"] != res_skip1.json()[0]["id"]
    finally:
        app.dependency_overrides.clear()
