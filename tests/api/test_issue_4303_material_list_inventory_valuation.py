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
from app.models.material import Material, Supplier, MaterialTransaction, TransactionType
from app.models.owner import Owner
from app.core.dependencies import get_current_user, get_current_active_user


@pytest_asyncio.fixture
async def setup_inventory_valuation_context():
    run_uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        # Company
        comp = Company(name=f"Comp_4303_{run_uid}", is_active=True)
        db.add(comp)
        await db.flush()

        # Admin User
        admin = User(
            company_id=comp.id,
            email=f"admin_4303_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        db.add(admin)
        await db.flush()

        # Owner & Project
        owner = Owner(
            company_id=comp.id,
            owner_code=f"OA_4303_{run_uid}",
            owner_name=f"OwnerA_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            company_id=comp.id,
            owner_id=owner.id,
            project_name=f"Proj_4303_{run_uid}",
            business_id=f"P4303-{run_uid}",
            status="Ongoing",
        )
        db.add(proj)
        await db.flush()

        sup = Supplier(
            company_id=comp.id,
            supplier_name=f"Supplier_4303_{run_uid}",
            phone_email="9876543210",
        )
        unit = Unit(name=f"Unit_4303_{run_uid}")
        db.add_all([sup, unit])
        await db.flush()

        mm1 = MaterialMaster(name=f"MM1_4303_{run_uid}", category="Cement", unit_id=unit.id)
        mm2 = MaterialMaster(name=f"MM2_4303_{run_uid}", category="Steel", unit_id=unit.id)
        mm3 = MaterialMaster(name=f"MM3_4303_{run_uid}", category="Aggregate", unit_id=unit.id)
        db.add_all([mm1, mm2, mm3])
        await db.flush()

        # Material 1: Standard purchase, full stock remaining
        # purchase_rate = 400.00, qty_purchased = 50, remaining_stock = 50, total_amount = 20000.00
        mat1 = Material(
            material_code=f"MAT1-4303-{run_uid}",
            project_id=proj.id,
            material_master_id=mm1.id,
            supplier_id=sup.id,
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

        # Material 2: Partial usage (consumed stock)
        # purchase_rate = 80.00, qty_purchased = 100, remaining_stock = 60 (40 used)
        # total historical cost = 8000.00, remaining stock valuation = 60 * 80 = 4800.00
        mat2 = Material(
            material_code=f"MAT2-4303-{run_uid}",
            project_id=proj.id,
            material_master_id=mm2.id,
            supplier_id=sup.id,
            unit_id=unit.id,
            material_name="Steel TMT 12mm",
            category="Steel",
            rate_type="FIXED",
            purchase_rate=Decimal("80.00"),
            quantity_purchased=Decimal("100.00"),
            remaining_stock=Decimal("60.00"),
            total_amount=Decimal("8000.00"),
            payment_given=Decimal("8000.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )

        # Material 3: Opening stock balance (quantity_purchased == 0, remaining_stock == 30, purchase_rate == 150.00)
        # In prior bug, avg_rate evaluated to 0.0 and total_value to 0.0 in get_project_inventory,
        # but was valued at 30 * 150 = 4500.00 in get_inventory_valuation!
        mat3 = Material(
            material_code=f"MAT3-4303-{run_uid}",
            project_id=proj.id,
            material_master_id=mm3.id,
            supplier_id=sup.id,
            unit_id=unit.id,
            material_name="Coarse Aggregate",
            category="Aggregate",
            rate_type="FIXED",
            purchase_rate=Decimal("150.00"),
            quantity_purchased=Decimal("0.00"),
            remaining_stock=Decimal("30.00"),
            total_amount=Decimal("0.00"),
            payment_given=Decimal("0.00"),
            payment_pending=Decimal("0.00"),
            is_deleted=False,
        )

        db.add_all([mat1, mat2, mat3])
        await db.commit()

        context = {
            "comp_id": comp.id,
            "proj_id": proj.id,
            "admin": admin,
            "mat1_id": mat1.id,
            "mat2_id": mat2.id,
            "mat3_id": mat3.id,
            "mat_ids": [mat1.id, mat2.id, mat3.id],
            "mm_ids": [mm1.id, mm2.id, mm3.id],
            "unit_id": unit.id,
            "sup_id": sup.id,
            "user_ids": [admin.id],
        }

    yield context

    # Teardown
    async with AsyncSessionLocal() as db:
        await db.execute(Material.__table__.delete().where(Material.id.in_(context["mat_ids"])))
        await db.execute(MaterialMaster.__table__.delete().where(MaterialMaster.id.in_(context["mm_ids"])))
        await db.execute(Unit.__table__.delete().where(Unit.id == context["unit_id"]))
        await db.execute(Project.__table__.delete().where(Project.company_id == context["comp_id"]))
        await db.execute(Supplier.__table__.delete().where(Supplier.company_id == context["comp_id"]))
        await db.execute(Owner.__table__.delete().where(Owner.company_id == context["comp_id"]))
        await db.execute(User.__table__.delete().where(User.company_id == context["comp_id"]))
        await db.execute(Company.__table__.delete().where(Company.id == context["comp_id"]))
        await db.commit()


@pytest.mark.asyncio
async def test_inventory_valuation_and_project_inventory_consistency(setup_inventory_valuation_context):
    """
    Verify that:
    1. get_project_inventory items sum up exactly to get_inventory_valuation total_value.
    2. Material 3 (opening stock with quantity_purchased=0) has non-zero rate and valuation in project inventory.
    """
    ctx = setup_inventory_valuation_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Project Inventory
            res_proj = await ac.get(f"/api/v1/materials/inventory/{ctx['proj_id']}")
            assert res_proj.status_code == 200, res_proj.text
            proj_items = res_proj.json()
            assert len(proj_items) == 3

            # Check individual items in project inventory
            item_by_id = {i["material_id"]: i for i in proj_items}

            # mat1: 50 * 400 = 20000
            assert item_by_id[ctx["mat1_id"]]["remaining_stock"] == 50.0
            assert item_by_id[ctx["mat1_id"]]["avg_rate"] == 400.0
            assert item_by_id[ctx["mat1_id"]]["total_value"] == 20000.0

            # mat2: 60 * 80 = 4800
            assert item_by_id[ctx["mat2_id"]]["remaining_stock"] == 60.0
            assert item_by_id[ctx["mat2_id"]]["avg_rate"] == 80.0
            assert item_by_id[ctx["mat2_id"]]["total_value"] == 4800.0

            # mat3 (opening stock): 30 * 150 = 4500 (Previously bug was 0.0)
            assert item_by_id[ctx["mat3_id"]]["remaining_stock"] == 30.0
            assert item_by_id[ctx["mat3_id"]]["avg_rate"] == 150.0
            assert item_by_id[ctx["mat3_id"]]["total_value"] == 4500.0

            total_project_inventory_val = sum(i["total_value"] for i in proj_items)
            assert total_project_inventory_val == 29300.0

            # 2. Inventory Valuation API
            res_val = await ac.get(f"/api/v1/materials/inventory/valuation?project_id={ctx['proj_id']}")
            assert res_val.status_code == 200, res_val.text
            val_data = res_val.json()

            # Valuation total_value must equal sum of project inventory items!
            assert val_data["total_value"] == total_project_inventory_val
            assert val_data["total_value"] == 29300.0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_all_inventory_and_valuation_consistency(setup_inventory_valuation_context):
    """
    Verify get_all_inventory (/inventory) reports the same total_value and avg_rate
    matching the valuation endpoint.
    """
    ctx = setup_inventory_valuation_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_all = await ac.get("/api/v1/materials/inventory")
            assert res_all.status_code == 200, res_all.text
            all_items = res_all.json()

            target_items = [i for i in all_items if i["material_id"] in ctx["mat_ids"]]
            assert len(target_items) == 3

            item_by_id = {i["material_id"]: i for i in target_items}
            assert item_by_id[ctx["mat3_id"]]["avg_rate"] == 150.0
            assert item_by_id[ctx["mat3_id"]]["total_value"] == 4500.0

            sum_all_val = sum(i["total_value"] for i in target_items)
            assert sum_all_val == 29300.0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_material_list_vs_inventory_valuation_metric_distinction(setup_inventory_valuation_context):
    """
    Clarify and assert the exact metric relationship between Material List and Inventory Valuation:
    - Material List returns total_amount = historical cumulative purchase cost
    - Inventory Valuation returns total_value = remaining stock valuation on hand (remaining_stock * rate)
    - Difference equals consumed value: sum(total_amount) - valuation = sum((purchased - remaining) * rate)
    """
    ctx = setup_inventory_valuation_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Material List
            res_list = await ac.get(f"/api/v1/materials?project_id={ctx['proj_id']}")
            assert res_list.status_code == 200, res_list.text
            list_items = res_list.json()

            mat1_item = next(i for i in list_items if i["id"] == ctx["mat1_id"])
            mat2_item = next(i for i in list_items if i["id"] == ctx["mat2_id"])

            # mat1: total_amount = 20000.0 (50 purchased * 400)
            assert mat1_item["total_amount"] == 20000.0
            # mat2: total_amount = 8000.0 (100 purchased * 80)
            assert mat2_item["total_amount"] == 8000.0

            # 2. Valuation
            res_val = await ac.get(f"/api/v1/materials/inventory/valuation?project_id={ctx['proj_id']}")
            assert res_val.status_code == 200
            val_data = res_val.json()

            # For mat1: 0 consumed, remaining = 50 -> valuation = 20000.0
            # For mat2: 40 consumed, remaining = 60 -> valuation = 4800.0
            # Consumed value = 40 * 80 = 3200.0
            # Valuation = (20000 - 0) + (8000 - 3200) + (0 opening cost + 4500 opening stock val) = 29300.0
            # Total purchase amount in list = 20000 + 8000 + 0 = 28000.0
            # Inventory valuation on hand = 29300.0
            consumed_value_mat2 = (Decimal("100.00") - Decimal("60.00")) * Decimal("80.00")  # 3200.00
            assert float(consumed_value_mat2) == 3200.0

            # When accounting for consumed stock and opening stock, the values reconcile perfectly:
            # Valuation = Historical Purchase Total - Consumed Value + Opening Stock Value
            # 29300.0 = 28000.0 - 3200.0 + 4500.0 = 29300.0
            total_purchased_cost = sum(i["total_amount"] for i in [mat1_item, mat2_item])
            reconciled_valuation = total_purchased_cost - float(consumed_value_mat2) + 4500.0
            assert val_data["total_value"] == reconciled_valuation
    finally:
        app.dependency_overrides.clear()
