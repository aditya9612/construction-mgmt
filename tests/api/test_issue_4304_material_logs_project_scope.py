import uuid
from decimal import Decimal
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

import app.models as m
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.user import User, UserRole
from app.models.project import Project, ProjectMember
from app.models.master_data import MaterialMaster, Unit
from app.models.material import Material, MaterialTransaction, TransactionType, Supplier
from app.models.owner import Owner
from app.core.dependencies import get_current_user, get_current_active_user


@pytest_asyncio.fixture
async def setup_material_logs_context():
    run_uid = uuid.uuid4().hex[:8]

    async with AsyncSessionLocal() as db:
        # 1. Company A & Company B
        comp_a = Company(name=f"CompA_4304_{run_uid}", is_active=True)
        comp_b = Company(name=f"CompB_4304_{run_uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OA_4304_{run_uid}",
            owner_name=f"OwnerA_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OB_4304_{run_uid}",
            owner_name=f"OwnerB_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 3. Projects
        proj_a1 = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"ProjA1_{run_uid}",
            business_id=f"PA1-4304-{run_uid}",
            status="Ongoing",
        )
        proj_a2 = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"ProjA2_{run_uid}",
            business_id=f"PA2-4304-{run_uid}",
            status="Ongoing",
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"ProjB_{run_uid}",
            business_id=f"PB-4304-{run_uid}",
            status="Ongoing",
        )
        db.add_all([proj_a1, proj_a2, proj_b])
        await db.flush()

        # 4. Users
        admin_a = User(
            company_id=comp_a.id,
            email=f"admin_a_4304_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        admin_b = User(
            company_id=comp_b.id,
            email=f"admin_b_4304_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        user_pm_a = User(
            company_id=comp_a.id,
            email=f"pm_a_4304_{run_uid}@test.com",
            role=UserRole.PROJECT_MANAGER.value,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([admin_a, admin_b, user_pm_a])
        await db.flush()

        # 5. Project Membership: PM has access to proj_a1 only
        pm_link = ProjectMember(project_id=proj_a1.id, user_id=user_pm_a.id)
        db.add(pm_link)
        await db.flush()

        # 6. Suppliers & Units
        sup_a = Supplier(company_id=comp_a.id, supplier_name=f"SupplierA_4304_{run_uid}", phone_email="9876543210")
        sup_b = Supplier(company_id=comp_b.id, supplier_name=f"SupplierB_4304_{run_uid}", phone_email="9876543211")
        unit = Unit(name=f"Unit_4304_{run_uid}")
        db.add_all([sup_a, sup_b, unit])
        await db.flush()

        # 7. MaterialMasters
        mm1 = MaterialMaster(name=f"MM1_4304_{run_uid}", category="Cement", unit_id=unit.id)
        mm2 = MaterialMaster(name=f"MM2_4304_{run_uid}", category="Steel", unit_id=unit.id)
        mm_b = MaterialMaster(name=f"MM_B_4304_{run_uid}", category="Sand", unit_id=unit.id)
        db.add_all([mm1, mm2, mm_b])
        await db.flush()

        # 8. Materials
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
            quantity_purchased=Decimal("100.00"),
            remaining_stock=Decimal("80.00"),
            total_amount=Decimal("40000.00"),
            payment_given=Decimal("40000.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )
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
            quantity_purchased=Decimal("50.00"),
            remaining_stock=Decimal("50.00"),
            total_amount=Decimal("4000.00"),
            payment_given=Decimal("4000.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )
        mat_b = Material(
            material_code=f"MAT-B-{run_uid}",
            project_id=proj_b.id,
            material_master_id=mm_b.id,
            supplier_id=sup_b.id,
            unit_id=unit.id,
            material_name="River Sand",
            category="Sand",
            rate_type="FIXED",
            purchase_rate=Decimal("50.00"),
            quantity_purchased=Decimal("20.00"),
            remaining_stock=Decimal("20.00"),
            total_amount=Decimal("1000.00"),
            payment_given=Decimal("1000.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )
        db.add_all([mat_a1, mat_a2, mat_b])
        await db.flush()

        # 9. Material Transactions (Logs)
        # Purchase for mat_a1
        tx_a1_in = MaterialTransaction(
            material_id=mat_a1.id,
            project_id=proj_a1.id,
            type=TransactionType.PURCHASE.value,
            quantity=Decimal("100.00"),
            rate=Decimal("400.00"),
            total_amount=Decimal("40000.00"),
            remarks="Initial stock purchase",
        )
        # Usage for mat_a1
        tx_a1_out = MaterialTransaction(
            material_id=mat_a1.id,
            project_id=proj_a1.id,
            type=TransactionType.USAGE.value,
            quantity=Decimal("20.00"),
            rate=Decimal("400.00"),
            total_amount=Decimal("8000.00"),
            remarks="Consumed in foundation",
        )
        # Purchase for mat_a2
        tx_a2_in = MaterialTransaction(
            material_id=mat_a2.id,
            project_id=proj_a2.id,
            type=TransactionType.PURCHASE.value,
            quantity=Decimal("50.00"),
            rate=Decimal("80.00"),
            total_amount=Decimal("4000.00"),
            remarks="Steel purchase",
        )
        # Purchase for mat_b
        tx_b_in = MaterialTransaction(
            material_id=mat_b.id,
            project_id=proj_b.id,
            type=TransactionType.PURCHASE.value,
            quantity=Decimal("20.00"),
            rate=Decimal("50.00"),
            total_amount=Decimal("1000.00"),
            remarks="Sand purchase",
        )
        db.add_all([tx_a1_in, tx_a1_out, tx_a2_in, tx_b_in])
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
            "mat_a1_id": mat_a1.id,
            "mat_a2_id": mat_a2.id,
            "mat_b_id": mat_b.id,
            "tx_a1_in_id": tx_a1_in.id,
            "tx_a1_out_id": tx_a1_out.id,
            "tx_a2_in_id": tx_a2_in.id,
            "tx_b_in_id": tx_b_in.id,
            "tx_ids": [tx_a1_in.id, tx_a1_out.id, tx_a2_in.id, tx_b_in.id],
            "mat_ids": [mat_a1.id, mat_a2.id, mat_b.id],
            "mm_ids": [mm1.id, mm2.id, mm_b.id],
            "unit_id": unit.id,
            "user_ids": [admin_a.id, admin_b.id, user_pm_a.id],
        }

    yield context

    # Teardown
    async with AsyncSessionLocal() as db:
        await db.execute(MaterialTransaction.__table__.delete().where(MaterialTransaction.id.in_(context["tx_ids"])))
        await db.execute(Material.__table__.delete().where(Material.id.in_(context["mat_ids"])))
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
async def test_material_logs_with_valid_project(setup_material_logs_context):
    """Querying logs with project_id returns only logs for that project."""
    ctx = setup_material_logs_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/materials/logs?project_id={ctx['proj_a1_id']}")
            assert res.status_code == 200, res.text
            items = res.json()
            assert len(items) == 2
            tx_ids = {i["id"] for i in items}
            assert tx_ids == {ctx["tx_a1_in_id"], ctx["tx_a1_out_id"]}
            for item in items:
                assert item["project_id"] == ctx["proj_a1_id"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_material_logs_without_project_company_scoped(setup_material_logs_context):
    """Admin calling logs without project_id receives all company transactions across projects."""
    ctx = setup_material_logs_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/materials/logs")
            assert res.status_code == 200, res.text
            items = res.json()
            tx_ids = {i["id"] for i in items}
            # Should include all Company A transactions: tx_a1_in, tx_a1_out, tx_a2_in
            assert ctx["tx_a1_in_id"] in tx_ids
            assert ctx["tx_a1_out_id"] in tx_ids
            assert ctx["tx_a2_in_id"] in tx_ids
            # Should NOT include Company B transactions
            assert ctx["tx_b_in_id"] not in tx_ids
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_material_logs_cross_company_isolation(setup_material_logs_context):
    """Querying logs with another company's project_id returns 404."""
    ctx = setup_material_logs_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/materials/logs?project_id={ctx['proj_b_id']}")
            assert res.status_code == 404
            assert "Project not found" in res.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_material_logs_non_admin_rbac(setup_material_logs_context, monkeypatch):
    """Non-admin user only sees transactions from their allowed projects."""
    ctx = setup_material_logs_context
    import app.core.dependencies as deps
    monkeypatch.setattr(deps, "has_permission", lambda perms, perm: True)

    app.dependency_overrides[get_current_user] = lambda: ctx["user_pm_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["user_pm_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Querying allowed project proj_a1 succeeds
            res_ok = await ac.get(f"/api/v1/materials/logs?project_id={ctx['proj_a1_id']}")
            assert res_ok.status_code == 200
            assert len(res_ok.json()) == 2

            # Querying without project_id returns only transactions from allowed projects
            res_no_proj = await ac.get("/api/v1/materials/logs")
            assert res_no_proj.status_code == 200
            items = res_no_proj.json()
            tx_ids = {i["id"] for i in items}
            assert tx_ids == {ctx["tx_a1_in_id"], ctx["tx_a1_out_id"]}
            assert ctx["tx_a2_in_id"] not in tx_ids

            # Querying unauthorized project proj_a2 returns 403 Forbidden
            res_denied = await ac.get(f"/api/v1/materials/logs?project_id={ctx['proj_a2_id']}")
            assert res_denied.status_code == 403
            assert "Access denied" in res_denied.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_material_logs_filters(setup_material_logs_context):
    """Filtering by material_id and transaction type works correctly."""
    ctx = setup_material_logs_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Filter by material_id
            res_mat = await ac.get(f"/api/v1/materials/logs?material_id={ctx['mat_a1_id']}")
            assert res_mat.status_code == 200
            items_mat = res_mat.json()
            assert len(items_mat) == 2
            assert all(i["material_id"] == ctx["mat_a1_id"] for i in items_mat)

            # Filter by type=USAGE
            res_type = await ac.get(f"/api/v1/materials/logs?type=USAGE")
            assert res_type.status_code == 200
            items_type = res_type.json()
            usage_txs = [i for i in items_type if i["id"] == ctx["tx_a1_out_id"]]
            assert len(usage_txs) == 1
            assert usage_txs[0]["type"] == "USAGE"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_material_logs_pagination(setup_material_logs_context):
    """Pagination parameters limit and offset work and are bounded."""
    ctx = setup_material_logs_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_lim1 = await ac.get(f"/api/v1/materials/logs?project_id={ctx['proj_a1_id']}&limit=1")
            assert res_lim1.status_code == 200
            assert len(res_lim1.json()) == 1

            res_offset1 = await ac.get(f"/api/v1/materials/logs?project_id={ctx['proj_a1_id']}&offset=1&limit=1")
            assert res_offset1.status_code == 200
            assert len(res_offset1.json()) == 1
            assert res_lim1.json()[0]["id"] != res_offset1.json()[0]["id"]
    finally:
        app.dependency_overrides.clear()
