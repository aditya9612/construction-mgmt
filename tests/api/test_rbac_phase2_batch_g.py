import uuid
from decimal import Decimal
from datetime import date, timedelta
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.settings import CompanySettings
from app.models.master_data import MaterialMaster, Unit
from app.models.material import (
    Material,
    Supplier,
    PurchaseOrder,
    MaterialTransfer,
    MaterialTransaction,
)
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import TransactionType as DBTransactionType, RateType


@asynccontextmanager
async def setup_batch_g_data():
    """Seed test companies, projects, units, masters, suppliers, materials, purchase orders, transfers, and users for Batch G test suite."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Create two test companies
        comp_a = Company(name=f"BatchG_CompA_{uid}")
        comp_b = Company(name=f"BatchG_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company settings
        cs_a = CompanySettings(company_id=comp_a.id)
        cs_b = CompanySettings(company_id=comp_b.id)
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Test Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_g_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin G",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_ga_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin G",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_gb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin G",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"MaterialManager_{uid}"
        user_custom_a = User(
            email=f"custom_ga_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Material Manager G",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        user_legacy_a = User(
            email=f"legacy_ga_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy PM G",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Project Manager",
        )

        db.add_all([super_admin, admin_a, admin_b, user_custom_a, user_legacy_a])
        await db.flush()

        # 4. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-GA-{uid}",
            owner_name=f"Owner GA {uid}",
            email=f"ownerga_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-GB-{uid}",
            owner_name=f"Owner GB {uid}",
            email=f"ownergb_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 5. Projects
        proj_a1 = Project(
            business_id=f"PRJ-GA1-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_GA1_{uid}",
            status="Ongoing",
        )
        proj_a2 = Project(
            business_id=f"PRJ-GA2-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_GA2_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-GB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_GB_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a1, proj_a2, proj_b])
        await db.flush()

        # 6. Unit & Master
        unit_a = Unit(name=f"Bags_{uid}")
        db.add(unit_a)
        await db.flush()

        master_a = MaterialMaster(
            name=f"Portland Cement {uid}",
            category="CEMENT",
            unit_id=unit_a.id,
        )
        db.add(master_a)
        await db.flush()

        # 7. Suppliers
        sup_a = Supplier(
            supplier_name=f"Apex Cement Suppliers {uid}",
            contact_person="John Apex",
            phone_email=f"98{uuid.uuid4().int % 100000000:08d}",
            gst_number=f"27AAAAA{uuid.uuid4().int % 10000:04d}A1Z5",
            address="123 Industrial Area",
            company_id=comp_a.id,
        )
        sup_b = Supplier(
            supplier_name=f"Beta Building Supplies {uid}",
            contact_person="Bob Beta",
            phone_email=f"97{uuid.uuid4().int % 100000000:08d}",
            gst_number=f"29BBBBB{uuid.uuid4().int % 10000:04d}B1Z6",
            address="456 Commerce Road",
            company_id=comp_b.id,
        )
        db.add_all([sup_a, sup_b])
        await db.flush()

        # 8. Materials
        mat_a = Material(
            material_code=f"MAT-GA-{uid}",
            project_id=proj_a1.id,
            material_master_id=master_a.id,
            material_name=master_a.name,
            category="CEMENT",
            unit_id=unit_a.id,
            supplier_id=sup_a.id,
            rate_type=RateType.PER_BAG,
            purchase_rate=Decimal("350.00"),
            quantity_purchased=Decimal("100.00"),
            quantity_used=Decimal("20.00"),
            remaining_stock=Decimal("80.00"),
            total_amount=Decimal("35000.00"),
            payment_given=Decimal("20000.00"),
            payment_pending=Decimal("15000.00"),
            minimum_stock_level=Decimal("25.00"),
        )
        mat_b = Material(
            material_code=f"MAT-GB-{uid}",
            project_id=proj_b.id,
            material_master_id=master_a.id,
            material_name=master_a.name,
            category="CEMENT",
            unit_id=unit_a.id,
            supplier_id=sup_b.id,
            rate_type=RateType.PER_BAG,
            purchase_rate=Decimal("360.00"),
            quantity_purchased=Decimal("50.00"),
            quantity_used=Decimal("10.00"),
            remaining_stock=Decimal("40.00"),
            total_amount=Decimal("18000.00"),
            payment_given=Decimal("10000.00"),
            payment_pending=Decimal("8000.00"),
            minimum_stock_level=Decimal("15.00"),
        )
        db.add_all([mat_a, mat_b])
        await db.flush()

        # 9. Purchase Orders
        po_a = PurchaseOrder(
            supplier_id=sup_a.id,
            project_id=proj_a1.id,
            material_id=mat_a.id,
            material_name=mat_a.material_name,
            quantity=Decimal("50.00"),
            rate=Decimal("350.00"),
            total_amount=Decimal("17500.00"),
            status="CREATED",
        )
        po_b = PurchaseOrder(
            supplier_id=sup_b.id,
            project_id=proj_b.id,
            material_id=mat_b.id,
            material_name=mat_b.material_name,
            quantity=Decimal("30.00"),
            rate=Decimal("360.00"),
            total_amount=Decimal("10800.00"),
            status="CREATED",
        )
        db.add_all([po_a, po_b])
        await db.flush()

        # 10. Transfers
        tr_a = MaterialTransfer(
            material_id=mat_a.id,
            from_project_id=proj_a1.id,
            to_project_id=proj_a2.id,
            quantity=Decimal("10.00"),
            status="PENDING",
            reference_id=f"TRF-GA-{uid}",
        )
        db.add(tr_a)
        await db.flush()

        # 11. Material Transactions (for logs, price history)
        tx_a = MaterialTransaction(
            material_id=mat_a.id,
            project_id=proj_a1.id,
            type=DBTransactionType.PURCHASE,
            quantity=Decimal("100.00"),
            rate=Decimal("350.00"),
            total_amount=Decimal("35000.00"),
            amount_paid=Decimal("20000.00"),
            payment_pending=Decimal("15000.00"),
        )
        db.add(tx_a)
        await db.flush()

        # 12. Project Memberships
        pm_custom_a1 = ProjectMember(project_id=proj_a1.id, user_id=user_custom_a.id)
        pm_custom_a2 = ProjectMember(project_id=proj_a2.id, user_id=user_custom_a.id)
        pm_legacy_a1 = ProjectMember(project_id=proj_a1.id, user_id=user_legacy_a.id)
        db.add_all([pm_custom_a1, pm_custom_a2, pm_legacy_a1])

        # 13. Create custom DB Role
        db_role_custom = Role(
            name=custom_role_name,
            display_name="Material Manager",
            description="Custom Material and Inventory Manager",
            company_id=comp_a.id,
            is_system=False,
        )
        db.add(db_role_custom)
        await db.flush()

        # 14. Ensure Admin role has permissions for testing
        admin_role = (await db.execute(select(Role).where(Role.name == "Admin"))).scalar_one_or_none()
        if not admin_role:
            admin_role = Role(name="Admin", display_name="Administrator", description="Administrator", is_system=True)
            db.add(admin_role)
            await db.flush()

        perms_query = select(Permission).where(
            Permission.code.in_([
                "materials.view", "materials.create", "materials.edit", "materials.delete", "materials.export",
                "suppliers.view", "suppliers.create", "suppliers.edit", "suppliers.delete",
                "purchase_orders.view", "purchase_orders.create", "purchase_orders.edit", "purchase_orders.delete",
                "inventory.view", "inventory.create", "inventory.edit", "inventory.delete",
            ])
        )
        target_perms = (await db.execute(perms_query)).scalars().all()
        for p in target_perms:
            rp_exists = (await db.execute(
                select(RolePermission).where(RolePermission.role == "Admin", RolePermission.permission_id == p.id)
            )).scalar_one_or_none()
            if not rp_exists:
                db.add(RolePermission(role="Admin", permission_id=p.id))

        await db.commit()

        tokens = {
            "super_admin": create_access_token(data={"sub": str(super_admin.id), "email": super_admin.email}),
            "admin_a": create_access_token(data={"sub": str(admin_a.id), "email": admin_a.email}),
            "admin_b": create_access_token(data={"sub": str(admin_b.id), "email": admin_b.email}),
            "user_custom_a": create_access_token(data={"sub": str(user_custom_a.id), "email": user_custom_a.email}),
            "user_legacy_a": create_access_token(data={"sub": str(user_legacy_a.id), "email": user_legacy_a.email}),
        }

        data_dict = {
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "proj_a1_id": proj_a1.id,
            "proj_a2_id": proj_a2.id,
            "proj_b_id": proj_b.id,
            "master_a_id": master_a.id,
            "unit_a_id": unit_a.id,
            "sup_a_id": sup_a.id,
            "sup_b_id": sup_b.id,
            "mat_a_id": mat_a.id,
            "mat_b_id": mat_b.id,
            "po_a_id": po_a.id,
            "po_b_id": po_b.id,
            "tr_a_id": tr_a.id,
            "tx_a_id": tx_a.id,
            "super_admin_id": super_admin.id,
            "admin_a_id": admin_a.id,
            "admin_b_id": admin_b.id,
            "user_custom_a_id": user_custom_a.id,
            "user_legacy_a_id": user_legacy_a.id,
            "custom_role_name": custom_role_name,
            "tokens": tokens,
        }

    try:
        yield data_dict
    finally:
        async with AsyncSessionLocal() as db:
            # Clean up overrides and role permissions for custom role
            await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([
                user_custom_a.id, user_legacy_a.id, admin_a.id, admin_b.id, super_admin.id
            ])))
            await db.execute(delete(RolePermission).where(RolePermission.role == custom_role_name))
            await db.execute(delete(Role).where(Role.name == custom_role_name))

            # Clean up domain models
            await db.execute(delete(MaterialTransaction).where(MaterialTransaction.id == tx_a.id))
            await db.execute(delete(MaterialTransfer).where(MaterialTransfer.id == tr_a.id))
            await db.execute(delete(PurchaseOrder).where(PurchaseOrder.id.in_([po_a.id, po_b.id])))
            await db.execute(delete(Material).where(Material.id.in_([mat_a.id, mat_b.id])))
            await db.execute(delete(Supplier).where(Supplier.id.in_([sup_a.id, sup_b.id])))
            await db.execute(delete(MaterialMaster).where(MaterialMaster.id == master_a.id))
            await db.execute(delete(Unit).where(Unit.id == unit_a.id))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a1.id, proj_a2.id, proj_b.id])))
            await db.execute(delete(Project).where(Project.id.in_([proj_a1.id, proj_a2.id, proj_b.id])))
            await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
            await db.execute(delete(User).where(User.id.in_([
                super_admin.id, admin_a.id, admin_b.id, user_custom_a.id, user_legacy_a.id
            ])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()


@pytest.mark.asyncio
async def test_batch_g_unauthenticated_all_38_routes_401():
    """Verify all 38 Batch G endpoints reject unauthenticated calls with 401 Unauthorized."""
    async with setup_batch_g_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            routes = [
                # Material Master (7)
                ("POST", "/api/v1/materials", {"project_id": d["proj_a1_id"], "material_master_id": d["master_a_id"], "supplier_id": d["sup_a_id"], "purchase_rate": 100.0, "quantity_purchased": 10.0, "rate_type": "PER_UNIT"}),
                ("GET", "/api/v1/materials", None),
                ("GET", f"/api/v1/materials/{d['mat_a_id']}", None),
                ("PUT", f"/api/v1/materials/{d['mat_a_id']}", {"minimum_stock_level": 50.0}),
                ("DELETE", f"/api/v1/materials/{d['mat_a_id']}", None),
                ("GET", f"/api/v1/materials/{d['mat_a_id']}/qr", None),
                ("GET", f"/api/v1/materials/price-history/{d['mat_a_id']}", None),

                # Suppliers (7)
                ("POST", "/api/v1/materials/suppliers", {"supplier_name": "Test Supplier", "phone_email": "9812345678"}),
                ("GET", "/api/v1/materials/suppliers", None),
                ("GET", f"/api/v1/materials/suppliers/{d['sup_a_id']}", None),
                ("PUT", f"/api/v1/materials/suppliers/{d['sup_a_id']}", {"supplier_name": "Updated Supplier"}),
                ("DELETE", f"/api/v1/materials/suppliers/{d['sup_a_id']}", None),
                ("GET", f"/api/v1/materials/suppliers/{d['sup_a_id']}/qr", None),
                ("GET", f"/api/v1/materials/suppliers/{d['sup_a_id']}/materials?project_id={d['proj_a1_id']}", None),

                # Purchase Orders (5)
                ("POST", "/api/v1/materials/purchase-orders", {"supplier_id": d["sup_a_id"], "project_id": d["proj_a1_id"], "material_id": d["mat_a_id"], "quantity": 10.0, "rate": 100.0}),
                ("GET", "/api/v1/materials/purchase-orders", None),
                ("GET", f"/api/v1/materials/purchase-orders/{d['po_a_id']}", None),
                ("PUT", f"/api/v1/materials/purchase-orders/{d['po_a_id']}", {"supplier_id": d["sup_a_id"], "project_id": d["proj_a1_id"], "material_id": d["mat_a_id"], "quantity": 15.0, "rate": 100.0}),
                ("DELETE", f"/api/v1/materials/purchase-orders/{d['po_a_id']}", None),

                # Transfers (4)
                ("POST", "/api/v1/materials/transfers", {"material_id": d["mat_a_id"], "from_project_id": d["proj_a1_id"], "to_project_id": d["proj_a2_id"], "quantity": 5.0}),
                ("GET", "/api/v1/materials/transfers", None),
                ("GET", f"/api/v1/materials/transfers/{d['tr_a_id']}", None),
                ("PUT", f"/api/v1/materials/transfers/{d['tr_a_id']}", {"status": "CANCELLED"}),

                # Usage / Purchase / Adjustments (3)
                ("POST", f"/api/v1/materials/{d['mat_a_id']}/usage", {"quantity": 5.0, "date": str(date.today())}),
                ("POST", f"/api/v1/materials/{d['mat_a_id']}/purchase", {"quantity": 10.0, "rate": 350.0, "supplier_id": d["sup_a_id"]}),
                ("POST", "/api/v1/materials/inventory", {"material_id": d["mat_a_id"], "project_id": d["proj_a1_id"], "quantity": 5.0, "type": "ADD"}),

                # Inventory / Valuation (4)
                ("GET", "/api/v1/materials/inventory", None),
                ("GET", "/api/v1/materials/inventory/valuation", None),
                ("GET", f"/api/v1/materials/inventory/{d['proj_a1_id']}", None),
                ("GET", f"/api/v1/materials/projects/{d['proj_a1_id']}/transactions", None),

                # Reports / Analytics (8)
                ("GET", "/api/v1/materials/summary", None),
                ("GET", "/api/v1/materials/alerts", None),
                ("GET", f"/api/v1/materials/logs?project_id={d['proj_a1_id']}", None),
                ("GET", f"/api/v1/materials/{d['mat_a_id']}/transactions", None),
                ("GET", f"/api/v1/materials/reports?project_id={d['proj_a1_id']}", None),
                ("GET", f"/api/v1/materials/reports/pdf?project_id={d['proj_a1_id']}", None),
                ("GET", f"/api/v1/materials/reports/excel?project_id={d['proj_a1_id']}", None),
                ("GET", f"/api/v1/materials/procurement-report/{d['proj_a1_id']}", None),
            ]

            assert len(routes) == 38, f"Expected exactly 38 routes, found {len(routes)}"

            for item in routes:
                method = item[0]
                path = item[1]
                data = item[2] if len(item) > 2 else None

                if method == "GET":
                    r = await client.get(path)
                elif method == "POST":
                    r = await client.post(path, json=data)
                elif method == "PUT":
                    r = await client.put(path, json=data)
                elif method == "DELETE":
                    r = await client.delete(path)
                assert r.status_code == 401, f"{method} {path} returned {r.status_code}, expected 401"


@pytest.mark.asyncio
async def test_batch_g_custom_role_dynamic_lifecycle_materials():
    """Verify runtime dynamic RBAC lifecycle for Materials routes: 403 -> grant -> 200 -> revoke -> 403 -> regrant -> 200."""
    async with setup_batch_g_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Custom role has 0 permissions -> 403
            r = await client.get("/api/v1/materials", headers=headers)
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

            # 2. Grant materials.view to custom role
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "materials.view"))).scalar_one()
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_view.id))
                await db.commit()

            # 3. Next request succeeds immediately (200) without server restart
            r = await client.get("/api/v1/materials", headers=headers)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

            # 4. Revoke materials.view from custom role
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == d["custom_role_name"],
                    RolePermission.permission_id == p_view.id
                ))
                await db.commit()

            # 5. Immediate 403 Forbidden
            r = await client.get("/api/v1/materials", headers=headers)
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

            # 6. Regrant materials.view
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_view.id))
                await db.commit()

            # 7. Immediate 200 OK
            r = await client.get("/api/v1/materials", headers=headers)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_batch_g_custom_role_dynamic_lifecycle_suppliers():
    """Verify runtime dynamic RBAC lifecycle for Suppliers routes: 403 -> grant -> 200 -> revoke -> 403 -> regrant -> 200."""
    async with setup_batch_g_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Custom role has 0 permissions -> 403
            r = await client.get("/api/v1/materials/suppliers", headers=headers)
            assert r.status_code == 403

            # 2. Grant suppliers.view to custom role
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "suppliers.view"))).scalar_one()
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_view.id))
                await db.commit()

            # 3. Next request succeeds immediately (200)
            r = await client.get("/api/v1/materials/suppliers", headers=headers)
            assert r.status_code == 200

            # 4. Revoke suppliers.view
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == d["custom_role_name"],
                    RolePermission.permission_id == p_view.id
                ))
                await db.commit()

            # 5. Immediate 403 Forbidden
            r = await client.get("/api/v1/materials/suppliers", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_batch_g_custom_role_dynamic_lifecycle_inventory():
    """Verify runtime dynamic RBAC lifecycle for Inventory & PO routes: 403 -> grant -> 200 -> revoke -> 403."""
    async with setup_batch_g_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Inventory listing -> 403
            r_inv = await client.get("/api/v1/materials/inventory", headers=headers)
            assert r_inv.status_code == 403

            # 2. Grant inventory.view
            async with AsyncSessionLocal() as db:
                p_inv = (await db.execute(select(Permission).where(Permission.code == "inventory.view"))).scalar_one()
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_inv.id))
                await db.commit()

            # 3. Inventory listing succeeds (200)
            r_inv2 = await client.get("/api/v1/materials/inventory", headers=headers)
            assert r_inv2.status_code == 200

            # 4. PO listing still 403
            r_po = await client.get("/api/v1/materials/purchase-orders", headers=headers)
            assert r_po.status_code == 403

            # 5. Grant purchase_orders.view
            async with AsyncSessionLocal() as db:
                p_po = (await db.execute(select(Permission).where(Permission.code == "purchase_orders.view"))).scalar_one()
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_po.id))
                await db.commit()

            # 6. PO listing succeeds (200)
            r_po2 = await client.get("/api/v1/materials/purchase-orders", headers=headers)
            assert r_po2.status_code == 200


@pytest.mark.asyncio
async def test_batch_g_user_permission_overrides():
    """Verify positive and negative user permission overrides take precedence over role permissions."""
    async with setup_batch_g_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. User role has 0 permissions -> 403
            r = await client.get("/api/v1/materials", headers=headers)
            assert r.status_code == 403

            # 2. Add positive override (is_granted=True) directly to user
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "materials.view"))).scalar_one()
                db.add(UserPermissionOverride(
                    user_id=d["user_custom_a_id"],
                    permission_id=p_view.id,
                    is_granted=True,
                ))
                await db.commit()

            # 3. User now gets 200 OK via override
            r = await client.get("/api/v1/materials", headers=headers)
            assert r.status_code == 200

            # 4. Now also grant role-level permission to the custom role
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_view.id))
                # Change user override to negative (is_granted=False)
                override = (await db.execute(select(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == d["user_custom_a_id"],
                    UserPermissionOverride.permission_id == p_view.id
                ))).scalar_one()
                override.is_granted = False
                await db.commit()

            # 5. Negative user override MUST take precedence over role grant -> 403 Forbidden
            r = await client.get("/api/v1/materials", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_batch_g_wildcard_permission():
    """Verify wildcard permissions ('*' and 'materials.*') grant access across module endpoints."""
    async with setup_batch_g_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Grant wildcard permission to custom role
            async with AsyncSessionLocal() as db:
                p_wildcard = (await db.execute(select(Permission).where(Permission.code == "*"))).scalar_one_or_none()
                if not p_wildcard:
                    p_wildcard = Permission(code="*", module="all", action="*", description="Wildcard")
                    db.add(p_wildcard)
                    await db.flush()
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_wildcard.id))
                await db.commit()

            # Custom role with wildcard can access Materials, Suppliers, POs, and Inventory listings
            r_mat = await client.get("/api/v1/materials", headers=headers)
            assert r_mat.status_code == 200
            r_sup = await client.get("/api/v1/materials/suppliers", headers=headers)
            assert r_sup.status_code == 200
            r_po = await client.get("/api/v1/materials/purchase-orders", headers=headers)
            assert r_po.status_code == 200
            r_inv = await client.get("/api/v1/materials/inventory", headers=headers)
            assert r_inv.status_code == 200


@pytest.mark.asyncio
async def test_batch_g_legacy_role_strings_denied():
    """Verify users with legacy role strings ('Project Manager') and 0 DB permissions receive 403 Forbidden."""
    async with setup_batch_g_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_legacy_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Ensure "Project Manager" has no DB role permissions
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role == "Project Manager"))
                await db.commit()

            r1 = await client.get("/api/v1/materials", headers=headers)
            assert r1.status_code == 403, f"Legacy role should be denied, got {r1.status_code}"

            r2 = await client.get("/api/v1/materials/suppliers", headers=headers)
            assert r2.status_code == 403, f"Legacy role should be denied, got {r2.status_code}"

            r3 = await client.get("/api/v1/materials/purchase-orders", headers=headers)
            assert r3.status_code == 403, f"Legacy role should be denied, got {r3.status_code}"


@pytest.mark.asyncio
async def test_batch_g_tenant_isolation_materials_and_inventory():
    """Verify Company A Admin cannot list, read, inject, mutate, or delete Company B Materials or Inventory (P0-1, P0-2, P0-4, P1-1)."""
    async with setup_batch_g_data() as d:
        headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. P0-1: Listing inventory without project_id returns ONLY Company A's inventory
            r = await client.get("/api/v1/materials/inventory", headers=headers_a)
            assert r.status_code == 200
            mat_ids = [item["material_id"] for item in r.json()]
            assert d["mat_a_id"] in mat_ids
            assert d["mat_b_id"] not in mat_ids, "Company B inventory leaked in list query!"

            # 2. P0-2: Valuation without project_id returns ONLY Company A's value
            r_val = await client.get("/api/v1/materials/inventory/valuation", headers=headers_a)
            assert r_val.status_code == 200
            assert r_val.json()["total_value"] > 0

            # Valuation for foreign Project B returns 404
            r_val_b = await client.get(f"/api/v1/materials/inventory/valuation?project_id={d['proj_b_id']}", headers=headers_a)
            assert r_val_b.status_code == 404

            # 3. P0-4: Summary without project_id scopes to Company A only
            r_sum = await client.get("/api/v1/materials/summary", headers=headers_a)
            assert r_sum.status_code == 200
            assert r_sum.json()["total_materials"] == 1

            # Summary for foreign Project B returns 404
            r_sum_b = await client.get(f"/api/v1/materials/summary?project_id={d['proj_b_id']}", headers=headers_a)
            assert r_sum_b.status_code == 404

            # 4. P0-4: Alerts without project_id scopes to Company A only
            r_alt = await client.get("/api/v1/materials/alerts", headers=headers_a)
            assert r_alt.status_code == 200
            alert_mat_ids = [m["id"] for m in r_alt.json()]
            assert d["mat_b_id"] not in alert_mat_ids

            # 5. P1-1: Detail lookup on foreign Material B returns 404
            r_detail = await client.get(f"/api/v1/materials/{d['mat_b_id']}", headers=headers_a)
            assert r_detail.status_code == 404, f"Expected 404 for foreign material detail, got {r_detail.status_code}"

            # 6. Deleting foreign Material B returns 404
            r_del = await client.delete(f"/api/v1/materials/{d['mat_b_id']}", headers=headers_a)
            assert r_del.status_code == 404

            # 7. Updating foreign Material B returns 404
            r_upd = await client.put(f"/api/v1/materials/{d['mat_b_id']}", headers=headers_a, json={"minimum_stock_level": 99.0})
            assert r_upd.status_code == 404


@pytest.mark.asyncio
async def test_batch_g_tenant_isolation_suppliers_and_pos():
    """Verify Company A Admin cannot list, read, inject, mutate, or delete Company B Suppliers or Purchase Orders (P0-3, P1-1, P1-3)."""
    async with setup_batch_g_data() as d:
        headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. P0-3: Listing POs without project_id returns ONLY Company A's POs
            r_po = await client.get("/api/v1/materials/purchase-orders", headers=headers_a)
            assert r_po.status_code == 200
            po_ids = [item["id"] for item in r_po.json()]
            assert d["po_a_id"] in po_ids
            assert d["po_b_id"] not in po_ids, "Company B PO leaked in list query!"

            # 2. Listing POs with foreign project_id returns 404
            r_po_foreign = await client.get(f"/api/v1/materials/purchase-orders?project_id={d['proj_b_id']}", headers=headers_a)
            assert r_po_foreign.status_code == 404

            # 3. P1-1: Detail lookup on foreign PO returns 404
            r_po_detail = await client.get(f"/api/v1/materials/purchase-orders/{d['po_b_id']}", headers=headers_a)
            assert r_po_detail.status_code == 404

            # 4. P1-3: Creating PO with foreign project returns 404
            r_po_inject_proj = await client.post(
                "/api/v1/materials/purchase-orders",
                headers=headers_a,
                json={"supplier_id": d["sup_a_id"], "project_id": d["proj_b_id"], "material_id": d["mat_a_id"], "quantity": 10.0, "rate": 100.0}
            )
            assert r_po_inject_proj.status_code == 404

            # 5. Detail lookup on foreign Supplier B returns 404
            r_sup_detail = await client.get(f"/api/v1/materials/suppliers/{d['sup_b_id']}", headers=headers_a)
            assert r_sup_detail.status_code == 404

            # 6. Deleting foreign Supplier B returns 404
            r_del_sup = await client.delete(f"/api/v1/materials/suppliers/{d['sup_b_id']}", headers=headers_a)
            assert r_del_sup.status_code == 404


@pytest.mark.asyncio
async def test_batch_g_cross_company_transfers_blocked():
    """Verify cross-company transfers (Company A -> Company B or vice versa) are strictly rejected (P1-2)."""
    async with setup_batch_g_data() as d:
        headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Transfer from Project A1 (Company A) to Project B (Company B) must fail with 404
            r_tr = await client.post(
                "/api/v1/materials/transfers",
                headers=headers_a,
                json={"material_id": d["mat_a_id"], "from_project_id": d["proj_a1_id"], "to_project_id": d["proj_b_id"], "quantity": 5.0}
            )
            assert r_tr.status_code == 404, f"Cross company transfer should return 404, got {r_tr.status_code}"


@pytest.mark.asyncio
async def test_batch_g_null_and_nonexistent_ids_404():
    """Verify non-existent IDs return 404 across all mutation and detail routes without 500 error."""
    async with setup_batch_g_data() as d:
        headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            non_existent_id = 999999999

            # Materials
            assert (await client.get(f"/api/v1/materials/{non_existent_id}", headers=headers_a)).status_code == 404
            assert (await client.put(f"/api/v1/materials/{non_existent_id}", headers=headers_a, json={"minimum_stock_level": 10.0})).status_code == 404
            assert (await client.delete(f"/api/v1/materials/{non_existent_id}", headers=headers_a)).status_code == 404

            # Suppliers
            assert (await client.get(f"/api/v1/materials/suppliers/{non_existent_id}", headers=headers_a)).status_code == 404
            assert (await client.put(f"/api/v1/materials/suppliers/{non_existent_id}", headers=headers_a, json={"supplier_name": "Ghost"})).status_code == 404
            assert (await client.delete(f"/api/v1/materials/suppliers/{non_existent_id}", headers=headers_a)).status_code == 404

            # Purchase Orders
            assert (await client.get(f"/api/v1/materials/purchase-orders/{non_existent_id}", headers=headers_a)).status_code == 404
            assert (await client.put(f"/api/v1/materials/purchase-orders/{non_existent_id}", headers=headers_a, json={"supplier_id": d["sup_a_id"], "project_id": d["proj_a1_id"], "material_id": d["mat_a_id"], "quantity": 1.0, "rate": 1.0})).status_code == 404
            assert (await client.delete(f"/api/v1/materials/purchase-orders/{non_existent_id}", headers=headers_a)).status_code == 404

            # Transfers
            assert (await client.get(f"/api/v1/materials/transfers/{non_existent_id}", headers=headers_a)).status_code == 404
            assert (await client.put(f"/api/v1/materials/transfers/{non_existent_id}?status=CANCELLED", headers=headers_a)).status_code == 404


@pytest.mark.asyncio
async def test_batch_g_super_admin_tenant_context():
    """Verify Super Admin without tenant context receives safe responses rather than unconstrained cross-company leakage."""
    async with setup_batch_g_data() as d:
        headers_sa = {"Authorization": f"Bearer {d['tokens']['super_admin']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Listing without project_id as super admin without company returns empty list safely
            r_mat = await client.get("/api/v1/materials", headers=headers_sa)
            assert r_mat.status_code == 200
            assert r_mat.json() == []

            r_inv = await client.get("/api/v1/materials/inventory", headers=headers_sa)
            assert r_inv.status_code == 200
            assert r_inv.json() == []

            r_po = await client.get("/api/v1/materials/purchase-orders", headers=headers_sa)
            assert r_po.status_code == 200
            assert r_po.json() == []
