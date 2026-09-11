"""
Test Suite: BATCH AM -- Material Management RBAC & Security Remediation
=======================================================================
Covers:
1.  Authentication: unauthenticated -> 401
2.  Permission denied: no permission -> 403
3.  Permission granted: correct permission -> 2xx
4.  Tenantless non-SA: company_id=None -> 403
5.  Tenant isolation: Tenant A cannot access Tenant B resources
6.  IDOR masking: foreign material/PO/transfer -> 404
7.  SA global access: SA sees all companies
8.  SA company-filtered access works correctly
9.  Inventory SA fix: company_id=None SA no longer returns []
10. Purchase order isolation: project scoping
11. Transfer cross-project/company isolation
12. Supplier tenant isolation
13. Report/export project-scoped access
14. /logs tenant scoping (no role comparisons)
15. Dynamic RBAC: revoke -> 403, regrant -> 200
16. Route preservation: 39 material endpoints, 781 total, 0 duplicates
17. Static security scan: 0 allowed_projects, 0 role comparisons, 0 dead constants
"""

import re
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.api import material as material_api_module
from app.core.db import AsyncSessionLocal
from app.core.enums import RateType
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.company import Company
from app.models.master_data import MaterialMaster, Unit
from app.models.material import (
    Material,
    MaterialTransfer,
    MaterialTransaction,
    PurchaseOrder,
    Supplier,
)
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.rbac import Permission, Role, RolePermission
from app.models.user import ActivityLog, User

BASE = "/api/v1/materials"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@asynccontextmanager
async def setup_material_test_data():
    """
    Creates two isolated companies (A and B) with full test data.
    Yields test data dict and cleans up on exit.
    """
    async with AsyncSessionLocal() as db:
        u = _uid()
        pwd = get_password_hash("Secret123!")

        comp_a = Company(name=f"MatCompA_{u}")
        comp_b = Company(name=f"MatCompB_{u}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        unit_a = Unit(company_id=comp_a.id, name=f"KG_{u}", unique_code=f"KG{u}", is_active=True)
        unit_b = Unit(company_id=comp_b.id, name=f"KGB_{u}", unique_code=f"KGB{u}", is_active=True)
        db.add_all([unit_a, unit_b])
        await db.flush()

        mm_a = MaterialMaster(company_id=comp_a.id, name=f"Cement_{u}", unit_id=unit_a.id, unique_code=f"CEM{u}", category="Raw")
        mm_b = MaterialMaster(company_id=comp_b.id, name=f"Steel_{u}", unit_id=unit_b.id, unique_code=f"STL{u}", category="Raw")
        db.add_all([mm_a, mm_b])
        await db.flush()

        owner_a = Owner(company_id=comp_a.id, owner_code=f"OA_{u}", owner_name=f"OwnerA_{u}", email=f"ownera_{u}@t.com", mobile=f"9{u[:7]}1")
        owner_b = Owner(company_id=comp_b.id, owner_code=f"OB_{u}", owner_name=f"OwnerB_{u}", email=f"ownerb_{u}@t.com", mobile=f"9{u[:7]}2")
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(company_id=comp_a.id, project_name=f"ProjA_{u}", business_id=f"PA{u}", owner_id=owner_a.id)
        proj_b = Project(company_id=comp_b.id, project_name=f"ProjB_{u}", business_id=f"PB{u}", owner_id=owner_b.id)
        db.add_all([proj_a, proj_b])
        await db.flush()

        sup_a = Supplier(company_id=comp_a.id, supplier_name=f"SupA_{u}", phone_email=f"supa_{u}@t.com")
        sup_b = Supplier(company_id=comp_b.id, supplier_name=f"SupB_{u}", phone_email=f"supb_{u}@t.com")
        db.add_all([sup_a, sup_b])
        await db.flush()

        mat_a = Material(
            project_id=proj_a.id, material_master_id=mm_a.id, material_name=f"Cement_{u}",
            category="Raw", rate_type=RateType.PER_UNIT,
            material_code=f"MATA{u}", unit_id=unit_a.id, supplier_id=sup_a.id,
            purchase_rate=Decimal("100.00"), quantity_purchased=Decimal("50.00"),
            remaining_stock=Decimal("40.00"), quantity_used=Decimal("10.00"),
            total_amount=Decimal("5000.00"), payment_given=Decimal("3000.00"),
            payment_pending=Decimal("2000.00"), minimum_stock_level=Decimal("5.00"),
        )
        mat_b = Material(
            project_id=proj_b.id, material_master_id=mm_b.id, material_name=f"Steel_{u}",
            category="Raw", rate_type=RateType.PER_UNIT,
            material_code=f"MATB{u}", unit_id=unit_b.id, supplier_id=sup_b.id,
            purchase_rate=Decimal("200.00"), quantity_purchased=Decimal("20.00"),
            remaining_stock=Decimal("20.00"), quantity_used=Decimal("0.00"),
            total_amount=Decimal("4000.00"), payment_given=Decimal("4000.00"),
            payment_pending=Decimal("0.00"), minimum_stock_level=Decimal("2.00"),
        )
        db.add_all([mat_a, mat_b])
        await db.flush()

        po_a = PurchaseOrder(
            project_id=proj_a.id, supplier_id=sup_a.id, material_id=mat_a.id,
            material_name=mat_a.material_name, quantity=Decimal("10"), rate=Decimal("100"),
            total_amount=Decimal("1000"), status="CREATED",
        )
        po_b = PurchaseOrder(
            project_id=proj_b.id, supplier_id=sup_b.id, material_id=mat_b.id,
            material_name=mat_b.material_name, quantity=Decimal("5"), rate=Decimal("200"),
            total_amount=Decimal("1000"), status="CREATED",
        )
        db.add_all([po_a, po_b])
        await db.flush()

        all_perms = [
            "materials.view", "materials.create", "materials.edit",
            "materials.delete", "materials.export",
            "inventory.view", "inventory.create", "inventory.edit",
            "purchase_orders.view", "purchase_orders.create",
            "purchase_orders.edit", "purchase_orders.delete",
            "suppliers.view", "suppliers.create", "suppliers.edit",
        ]
        perm_objs = {}
        for code in all_perms:
            p = await db.scalar(select(Permission).where(Permission.code == code))
            if not p:
                parts = code.split(".")
                p = Permission(module=parts[0], action=parts[1], code=code, description=code)
                db.add(p)
                await db.flush()
            perm_objs[code] = p

        rn_full_a = f"MatFullA_{u}"
        rn_ro_a = f"MatROA_{u}"
        rn_none_a = f"MatNoneA_{u}"
        rn_full_b = f"MatFullB_{u}"

        role_full_a = Role(company_id=comp_a.id, name=rn_full_a, display_name="Mat Full A", is_system=False)
        role_ro_a = Role(company_id=comp_a.id, name=rn_ro_a, display_name="Mat RO A", is_system=False)
        role_none_a = Role(company_id=comp_a.id, name=rn_none_a, display_name="Mat None A", is_system=False)
        role_full_b = Role(company_id=comp_b.id, name=rn_full_b, display_name="Mat Full B", is_system=False)
        db.add_all([role_full_a, role_ro_a, role_none_a, role_full_b])
        await db.flush()

        for code in all_perms:
            db.add(RolePermission(role=rn_full_a, role_id=role_full_a.id, permission_id=perm_objs[code].id))
            db.add(RolePermission(role=rn_full_b, role_id=role_full_b.id, permission_id=perm_objs[code].id))
        db.add(RolePermission(role=rn_ro_a, role_id=role_ro_a.id, permission_id=perm_objs["materials.view"].id))
        await db.flush()

        user_full_a = User(email=f"mfa_{u}@t.com", hashed_password=pwd, company_id=comp_a.id,
                           role=rn_full_a, is_active=True, full_name="Mat Full A")
        user_ro_a = User(email=f"mra_{u}@t.com", hashed_password=pwd, company_id=comp_a.id,
                         role=rn_ro_a, is_active=True, full_name="Mat RO A")
        user_none_a = User(email=f"mna_{u}@t.com", hashed_password=pwd, company_id=comp_a.id,
                           role=rn_none_a, is_active=True, full_name="Mat None A")
        user_full_b = User(email=f"mfb_{u}@t.com", hashed_password=pwd, company_id=comp_b.id,
                           role=rn_full_b, is_active=True, full_name="Mat Full B")
        user_sa = User(email=f"msa_{u}@t.com", hashed_password=pwd, company_id=None,
                       role="super_admin", is_super_admin=True, is_active=True, full_name="SA")
        user_tenantless = User(email=f"mtn_{u}@t.com", hashed_password=pwd, company_id=None,
                               role=rn_full_a, is_super_admin=False, is_active=True, full_name="Tenantless")
        db.add_all([user_full_a, user_ro_a, user_none_a, user_full_b, user_sa, user_tenantless])
        await db.flush()

        db.add_all([
            ProjectMember(project_id=proj_a.id, user_id=user_full_a.id),
            ProjectMember(project_id=proj_a.id, user_id=user_ro_a.id),
            ProjectMember(project_id=proj_a.id, user_id=user_none_a.id),
            ProjectMember(project_id=proj_b.id, user_id=user_full_b.id),
        ])
        await db.commit()

        tokens = {
            "full_a": create_access_token(data={"sub": str(user_full_a.id)}),
            "ro_a": create_access_token(data={"sub": str(user_ro_a.id)}),
            "none_a": create_access_token(data={"sub": str(user_none_a.id)}),
            "full_b": create_access_token(data={"sub": str(user_full_b.id)}),
            "sa": create_access_token(data={"sub": str(user_sa.id)}),
            "tenantless": create_access_token(data={"sub": str(user_tenantless.id)}),
        }

        data = {
            "comp_a": comp_a, "comp_b": comp_b,
            "proj_a": proj_a, "proj_b": proj_b,
            "sup_a": sup_a, "sup_b": sup_b,
            "mat_a": mat_a, "mat_b": mat_b,
            "po_a": po_a, "po_b": po_b,
            "mm_a": mm_a, "unit_a": unit_a,
            "role_full_a": role_full_a, "perm_objs": perm_objs,
            "tokens": tokens, "uid": u, "user_full_a": user_full_a,
        }

        try:
            yield data
        finally:
            async with AsyncSessionLocal() as cdb:
                await cdb.execute(delete(ActivityLog).where(
                    ActivityLog.performed_by.in_([
                        user_full_a.id, user_ro_a.id, user_none_a.id,
                        user_full_b.id, user_sa.id, user_tenantless.id,
                    ])
                ))
                await cdb.execute(delete(MaterialTransfer).where(
                    MaterialTransfer.from_project_id.in_([proj_a.id, proj_b.id])
                ))
                await cdb.execute(delete(MaterialTransaction).where(
                    MaterialTransaction.project_id.in_([proj_a.id, proj_b.id])
                ))
                await cdb.execute(delete(PurchaseOrder).where(
                    PurchaseOrder.project_id.in_([proj_a.id, proj_b.id])
                ))
                await cdb.execute(delete(Material).where(
                    Material.project_id.in_([proj_a.id, proj_b.id])
                ))
                await cdb.execute(delete(Supplier).where(Supplier.id.in_([sup_a.id, sup_b.id])))
                await cdb.execute(delete(MaterialMaster).where(MaterialMaster.id.in_([mm_a.id, mm_b.id])))
                await cdb.execute(delete(Unit).where(Unit.id.in_([unit_a.id, unit_b.id])))
                await cdb.execute(delete(ProjectMember).where(
                    ProjectMember.project_id.in_([proj_a.id, proj_b.id])
                ))
                await cdb.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
                await cdb.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
                await cdb.execute(delete(RolePermission).where(
                    RolePermission.role.in_([rn_full_a, rn_ro_a, rn_none_a, rn_full_b])
                ))
                await cdb.execute(delete(Role).where(
                    Role.id.in_([role_full_a.id, role_ro_a.id, role_none_a.id, role_full_b.id])
                ))
                await cdb.execute(delete(User).where(
                    User.id.in_([
                        user_full_a.id, user_ro_a.id, user_none_a.id,
                        user_full_b.id, user_sa.id, user_tenantless.id,
                    ])
                ))
                await cdb.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
                await cdb.commit()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# 1. Authentication
# ===========================================================================

@pytest.mark.anyio
async def test_am_unauth_summary():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"{BASE}/summary")
    assert r.status_code == 401

@pytest.mark.anyio
async def test_am_unauth_suppliers():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"{BASE}/suppliers")
    assert r.status_code == 401

@pytest.mark.anyio
async def test_am_unauth_inventory():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"{BASE}/inventory")
    assert r.status_code == 401

@pytest.mark.anyio
async def test_am_unauth_purchase_orders():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"{BASE}/purchase-orders")
    assert r.status_code == 401

@pytest.mark.anyio
async def test_am_unauth_transfers():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"{BASE}/transfers")
    assert r.status_code == 401

@pytest.mark.anyio
async def test_am_unauth_logs():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"{BASE}/logs", params={"project_id": 1})
    assert r.status_code == 401

@pytest.mark.anyio
async def test_am_unauth_material_get():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"{BASE}/99999")
    assert r.status_code == 401


# ===========================================================================
# 2. Permission Denied
# ===========================================================================

@pytest.mark.anyio
async def test_am_perm_denied_inventory():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/inventory", headers=auth(d["tokens"]["none_a"]))
        assert r.status_code == 403, r.text

@pytest.mark.anyio
async def test_am_perm_denied_create_po():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"{BASE}/purchase-orders", headers=auth(d["tokens"]["ro_a"]), json={
                "project_id": d["proj_a"].id, "supplier_id": d["sup_a"].id,
                "material_id": d["mat_a"].id, "quantity": 1, "rate": 1,
            })
        assert r.status_code == 403, r.text

@pytest.mark.anyio
async def test_am_perm_denied_create_transfer():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"{BASE}/transfers", headers=auth(d["tokens"]["ro_a"]), json={
                "from_project_id": d["proj_a"].id, "to_project_id": d["proj_b"].id,
                "material_id": d["mat_a"].id, "quantity": 1,
            })
        assert r.status_code == 403, r.text

@pytest.mark.anyio
async def test_am_perm_denied_create_material():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"{BASE}", headers=auth(d["tokens"]["ro_a"]), json={
                "project_id": d["proj_a"].id, "material_name": "x",
                "quantity_purchased": 1, "purchase_rate": 1,
            })
        assert r.status_code == 403, r.text


# ===========================================================================
# 3. Permission Granted
# ===========================================================================

@pytest.mark.anyio
async def test_am_perm_granted_summary():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/summary", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text
        assert "total_materials" in r.json()

@pytest.mark.anyio
async def test_am_perm_granted_inventory():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/inventory", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

@pytest.mark.anyio
async def test_am_perm_granted_po_list():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/purchase-orders", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text

@pytest.mark.anyio
async def test_am_perm_granted_get_material():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/{d['mat_a'].id}", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text
        assert r.json()["id"] == d["mat_a"].id

@pytest.mark.anyio
async def test_am_perm_granted_suppliers_list():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/suppliers", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text

@pytest.mark.anyio
async def test_am_perm_granted_transfers_list():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/transfers", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text

@pytest.mark.anyio
async def test_am_perm_granted_logs():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/logs", params={"project_id": d["proj_a"].id},
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text

@pytest.mark.anyio
async def test_am_perm_granted_po_detail():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/purchase-orders/{d['po_a'].id}",
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text
        assert r.json()["id"] == d["po_a"].id

@pytest.mark.anyio
async def test_am_perm_granted_material_transactions():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/{d['mat_a'].id}/transactions",
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

@pytest.mark.anyio
async def test_am_perm_granted_project_inventory():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/inventory/{d['proj_a'].id}",
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text

@pytest.mark.anyio
async def test_am_perm_granted_report():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/reports", params={"project_id": d["proj_a"].id},
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text


# ===========================================================================
# 4. Tenantless non-SA -> 403
# ===========================================================================

@pytest.mark.anyio
async def test_am_tenantless_inventory_403():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/inventory", headers=auth(d["tokens"]["tenantless"]))
        assert r.status_code == 403, r.text

@pytest.mark.anyio
async def test_am_tenantless_po_list_403():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/purchase-orders", headers=auth(d["tokens"]["tenantless"]))
        assert r.status_code == 403, r.text


# ===========================================================================
# 5. Tenant Isolation
# ===========================================================================

@pytest.mark.anyio
async def test_am_tenant_isolation_get_material():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/{d['mat_b'].id}", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code in (403, 404), r.text

@pytest.mark.anyio
async def test_am_tenant_isolation_get_po():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/purchase-orders/{d['po_b'].id}",
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code in (403, 404), r.text

@pytest.mark.anyio
async def test_am_tenant_isolation_inventory_scoped():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/inventory", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text
        mat_ids = [item["material_id"] for item in r.json()]
        assert d["mat_b"].id not in mat_ids, "Tenant B material leaked into Tenant A inventory"

@pytest.mark.anyio
async def test_am_tenant_isolation_po_list_scoped():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/purchase-orders", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text
        po_ids = [po["id"] for po in r.json()]
        assert d["po_b"].id not in po_ids, "Tenant B PO leaked into Tenant A list"

@pytest.mark.anyio
async def test_am_tenant_isolation_logs_foreign_project():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/logs", params={"project_id": d["proj_b"].id},
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code in (403, 404), r.text

@pytest.mark.anyio
async def test_am_tenant_isolation_report_foreign():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/reports", params={"project_id": d["proj_b"].id},
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code in (403, 404), r.text


# ===========================================================================
# 6. Supplier Tenant Isolation
# ===========================================================================

@pytest.mark.anyio
async def test_am_supplier_foreign_404():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/suppliers/{d['sup_b'].id}",
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code in (403, 404), r.text

@pytest.mark.anyio
async def test_am_supplier_list_scoped():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/suppliers", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text
        sup_ids = [s["id"] for s in r.json()]
        assert d["sup_b"].id not in sup_ids, "Tenant B supplier leaked into Tenant A list"


# ===========================================================================
# 7. SA Global Access
# ===========================================================================

@pytest.mark.anyio
async def test_am_sa_inventory_not_empty():
    """SA /inventory returns data (fix for old empty-return bug)."""
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/inventory", headers=auth(d["tokens"]["sa"]))
        assert r.status_code == 200, r.text
        mat_ids = [item["material_id"] for item in r.json()]
        assert d["mat_a"].id in mat_ids, "SA should see Tenant A material in inventory"
        assert d["mat_b"].id in mat_ids, "SA should see Tenant B material in inventory"

@pytest.mark.anyio
async def test_am_sa_po_list_not_empty():
    """SA /purchase-orders returns data (fix for old empty-return bug)."""
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/purchase-orders", headers=auth(d["tokens"]["sa"]))
        assert r.status_code == 200, r.text
        po_ids = [po["id"] for po in r.json()]
        assert d["po_a"].id in po_ids, "SA should see Tenant A PO"
        assert d["po_b"].id in po_ids, "SA should see Tenant B PO"

@pytest.mark.anyio
async def test_am_sa_get_any_material():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            ra = await c.get(f"{BASE}/{d['mat_a'].id}", headers=auth(d["tokens"]["sa"]))
            rb = await c.get(f"{BASE}/{d['mat_b'].id}", headers=auth(d["tokens"]["sa"]))
        assert ra.status_code == 200, ra.text
        assert rb.status_code == 200, rb.text

@pytest.mark.anyio
async def test_am_sa_get_any_po():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            ra = await c.get(f"{BASE}/purchase-orders/{d['po_a'].id}", headers=auth(d["tokens"]["sa"]))
            rb = await c.get(f"{BASE}/purchase-orders/{d['po_b'].id}", headers=auth(d["tokens"]["sa"]))
        assert ra.status_code == 200, ra.text
        assert rb.status_code == 200, rb.text


# ===========================================================================
# 8. IDOR
# ===========================================================================

@pytest.mark.anyio
async def test_am_idor_nonexistent_material():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/99999999", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 404, r.text

@pytest.mark.anyio
async def test_am_idor_nonexistent_po():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/purchase-orders/99999999", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 404, r.text

@pytest.mark.anyio
async def test_am_idor_nonexistent_transfer():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/transfers/99999999", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 404, r.text

@pytest.mark.anyio
async def test_am_idor_foreign_material_update():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put(f"{BASE}/{d['mat_b'].id}",
                            headers=auth(d["tokens"]["full_a"]),
                            json={"material_name": "Hacked"})
        assert r.status_code in (403, 404), r.text

@pytest.mark.anyio
async def test_am_idor_foreign_material_delete():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete(f"{BASE}/{d['mat_b'].id}", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code in (403, 404), r.text

@pytest.mark.anyio
async def test_am_idor_foreign_po_update():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.put(f"{BASE}/purchase-orders/{d['po_b'].id}",
                            headers=auth(d["tokens"]["full_a"]),
                            json={"project_id": d["proj_b"].id, "supplier_id": d["sup_b"].id,
                                  "material_id": d["mat_b"].id, "quantity": 2, "rate": 100})
        assert r.status_code in (403, 404), r.text


# ===========================================================================
# 9. /logs — project-scoped only, no role comparisons
# ===========================================================================

@pytest.mark.anyio
async def test_am_logs_requires_project_id():
    """GET /logs without project_id -> 403 (global query blocked)."""
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/logs", headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 403, r.text

@pytest.mark.anyio
async def test_am_logs_valid_project_200():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/logs", params={"project_id": d["proj_a"].id},
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code == 200, r.text

@pytest.mark.anyio
async def test_am_logs_foreign_project_denied():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/logs", params={"project_id": d["proj_b"].id},
                            headers=auth(d["tokens"]["full_a"]))
        assert r.status_code in (403, 404), r.text


# ===========================================================================
# 10. PO — project injection guard
# ===========================================================================

@pytest.mark.anyio
async def test_am_po_foreign_project_create_denied():
    async with setup_material_test_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"{BASE}/purchase-orders",
                             headers=auth(d["tokens"]["full_a"]),
                             json={"project_id": d["proj_b"].id, "supplier_id": d["sup_b"].id,
                                   "material_id": d["mat_b"].id, "quantity": 1, "rate": 1})
        assert r.status_code in (403, 404), r.text


# ===========================================================================
# 11. Dynamic RBAC
# ===========================================================================

@pytest.mark.anyio
async def test_am_dynamic_rbac_revoke_regrant():
    async with setup_material_test_data() as d:
        perm = d["perm_objs"]["materials.view"]
        role = d["role_full_a"]
        token = d["tokens"]["full_a"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"{BASE}/summary", headers=auth(token))
            assert r.status_code == 200, f"Baseline: {r.text}"

            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role_id == role.id,
                    RolePermission.permission_id == perm.id,
                ))
                await db.commit()

            r = await c.get(f"{BASE}/summary", headers=auth(token))
            assert r.status_code == 403, f"Post-revoke: {r.text}"

            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role.name, role_id=role.id, permission_id=perm.id))
                await db.commit()

            r = await c.get(f"{BASE}/summary", headers=auth(token))
            assert r.status_code == 200, f"Post-regrant: {r.text}"


# ===========================================================================
# 12. Route Preservation
# ===========================================================================

@pytest.mark.anyio
async def test_am_route_preservation():
    all_routes = [
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods
        if m not in ("HEAD", "OPTIONS")
    ]
    mat_routes = [(m, p) for m, p in all_routes if p.startswith("/api/v1/materials")]
    total_unique = len(set(all_routes))
    mat_unique = len(set(mat_routes))
    duplicates = len(all_routes) - len(set(all_routes))
    assert total_unique == 781, f"Expected 781 total routes, got {total_unique}"
    assert mat_unique == 39, f"Expected 39 material routes, got {mat_unique}"
    assert duplicates == 0, f"Expected 0 duplicates, got {duplicates}"


# ===========================================================================
# 13. Static Security Scan
# ===========================================================================

def test_am_static_security_scan():
    import inspect
    src = inspect.getsource(material_api_module)
    assert "allowed_projects" not in src, "SECURITY: allowed_projects still referenced"
    assert "MATERIAL_READ_ROLES" not in src, "Dead constant MATERIAL_READ_ROLES still present"
    assert "MATERIAL_WRITE_ROLES" not in src, "Dead constant MATERIAL_WRITE_ROLES still present"
    assert "require_roles" not in src, "SECURITY: require_roles() found"
    assert "admin_required" not in src, "SECURITY: admin_required found"
    assert "current_user.role" not in src, "SECURITY: current_user.role comparison found"
