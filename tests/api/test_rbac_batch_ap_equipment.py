import uuid
from decimal import Decimal
from datetime import date, timedelta
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.equipment import (
    Equipment,
    EquipmentPurchase,
    EquipmentUsage,
    EquipmentMaintenance,
    EquipmentRental,
    EquipmentAuditLog,
)
from app.models.boq import BOQ, BOQGroup
from app.models.notification import Notification
from app.models.rbac import UserPermissionOverride
from tests.api.test_rbac_phase2_batch_e import setup_batch_e_data


@pytest.mark.asyncio
async def test_ap_01_unauthenticated_all_routes():
    """1. Unauthenticated requests to equipment routes return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        routes = [
            ("GET", "/api/v1/equipment"),
            ("POST", "/api/v1/equipment"),
            ("GET", "/api/v1/equipment/kpi"),
            ("GET", "/api/v1/equipment/usage"),
            ("GET", "/api/v1/equipment/maintenance"),
            ("GET", "/api/v1/equipment/rental"),
            ("GET", "/api/v1/equipment/purchase"),
            ("POST", "/api/v1/equipment/allocate"),
            ("PUT", "/api/v1/equipment/deallocate"),
            ("POST", "/api/v1/equipment/transfer"),
        ]
        for method, url in routes:
            if method == "GET":
                r = await ac.get(url)
            elif method == "POST":
                r = await ac.post(url, json={})
            elif method == "PUT":
                r = await ac.put(url, json={})
            assert r.status_code == 401, f"{method} {url} expected 401, got {r.status_code}"


@pytest.mark.asyncio
async def test_ap_02_missing_view_permission():
    """2. User lacking equipment.view cannot list or get equipment (403)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}
            async with AsyncSessionLocal() as db:
                ov = UserPermissionOverride(
                    user_id=data["user_custom_a_id"],
                    permission_id=data["perm_view_id"],
                    is_granted=False,
                )
                db.add(ov)
                await db.commit()

            r = await ac.get("/api/v1/equipment", headers=headers)
            assert r.status_code == 403
            r = await ac.get(f"/api/v1/equipment/{data['eq_a_id']}", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_ap_03_missing_create_permission():
    """3. User lacking equipment.create cannot create equipment (403)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}
            async with AsyncSessionLocal() as db:
                ov = UserPermissionOverride(
                    user_id=data["user_custom_a_id"],
                    permission_id=data["perm_create_id"],
                    is_granted=False,
                )
                db.add(ov)
                await db.commit()

            payload = {
                "equipment_name": "Denied Crane",
                "equipment_code": f"EQ-DENY-{uuid.uuid4().hex[:6]}",
                "condition": "GOOD",
            }
            r = await ac.post("/api/v1/equipment", json=payload, headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_ap_04_missing_edit_permission():
    """4. User lacking equipment.edit cannot update equipment (403)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}
            async with AsyncSessionLocal() as db:
                ov = UserPermissionOverride(
                    user_id=data["user_custom_a_id"],
                    permission_id=data["perm_edit_id"],
                    is_granted=False,
                )
                db.add(ov)
                await db.commit()

            r = await ac.put(f"/api/v1/equipment/{data['eq_a_id']}", json={"equipment_name": "New Name"}, headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_ap_05_missing_delete_permission():
    """5. User lacking equipment.delete cannot delete equipment (403)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}
            async with AsyncSessionLocal() as db:
                ov = UserPermissionOverride(
                    user_id=data["user_custom_a_id"],
                    permission_id=data["perm_delete_id"],
                    is_granted=False,
                )
                db.add(ov)
                await db.commit()

            r = await ac.delete(f"/api/v1/equipment/{data['eq_a_id']}", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_ap_06_missing_assign_permission():
    """6. User lacking equipment.assign cannot transfer/allocate (403)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}
            async with AsyncSessionLocal() as db:
                ov = UserPermissionOverride(
                    user_id=data["user_custom_a_id"],
                    permission_id=data["perm_assign_id"],
                    is_granted=False,
                )
                db.add(ov)
                await db.commit()

            r = await ac.post("/api/v1/equipment/transfer", json={
                "equipment_id": data["eq_a_id"],
                "to_project_id": data["proj_a_id"]
            }, headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_ap_07_missing_export_permission():
    """7. User lacking equipment.export cannot export reports (403)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}
            async with AsyncSessionLocal() as db:
                ov = UserPermissionOverride(
                    user_id=data["user_custom_a_id"],
                    permission_id=data["perm_export_id"],
                    is_granted=False,
                )
                db.add(ov)
                await db.commit()

            r = await ac.get("/api/v1/equipment/reports/excel", headers=headers)
            assert r.status_code == 403
            r = await ac.get("/api/v1/equipment/reports/pdf", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_ap_08_tenantless_non_sa_rejected():
    """8. Non-SA user with company_id=None gets 403 Company context required."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}
            async with AsyncSessionLocal() as db:
                u = await db.get(User, data["user_custom_a_id"])
                u.company_id = None
                await db.commit()

            r = await ac.get("/api/v1/equipment", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_ap_09_create_equipment_cross_tenant_project_rejected():
    """9. Company A user cannot create equipment referencing Company B project (403 or 404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            payload = {
                "project_id": data["proj_b_id"],
                "equipment_name": "Cross Tenant Project Eq",
                "equipment_code": f"EQ-XP-{uuid.uuid4().hex[:6]}",
                "condition": "GOOD",
            }
            r = await ac.post("/api/v1/equipment", json=payload, headers=headers)
            assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_ap_10_get_equipment_cross_tenant_returns_404():
    """10. Company A user cannot view Company B equipment (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.get(f"/api/v1/equipment/{data['eq_b_id']}", headers=headers)
            assert r.status_code == 404
            assert r.json().get("detail") == "Equipment not found"


@pytest.mark.asyncio
async def test_ap_11_update_equipment_cross_tenant_returns_404():
    """11. Company A user cannot update Company B equipment (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.put(f"/api/v1/equipment/{data['eq_b_id']}", json={"equipment_name": "Hacked"}, headers=headers)
            assert r.status_code == 404
            assert r.json().get("detail") == "Equipment not found"


@pytest.mark.asyncio
async def test_ap_12_update_equipment_cross_tenant_project_rejected():
    """12. Company A user cannot reassign Company A equipment to Company B project (403 or 404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.put(f"/api/v1/equipment/{data['eq_a_id']}", json={"project_id": data["proj_b_id"]}, headers=headers)
            assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_ap_13_delete_equipment_cross_tenant_returns_404():
    """13. Company A user cannot delete Company B equipment (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.delete(f"/api/v1/equipment/{data['eq_b_unalloc_id']}", headers=headers)
            assert r.status_code == 404
            assert r.json().get("detail") == "Equipment not found"


@pytest.mark.asyncio
async def test_ap_14_restore_equipment_cross_tenant_returns_404():
    """14. Company A user cannot restore Company B equipment (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.put(f"/api/v1/equipment/{data['eq_b_id']}/restore", headers=headers)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_ap_15_allocate_cross_tenant_equipment_returns_404():
    """15. Company A user cannot allocate Company B equipment."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.post("/api/v1/equipment/allocate", json={
                "project_id": data["proj_a_id"],
                "equipment_ids": [data["eq_b_unalloc_id"]]
            }, headers=headers)
            assert r.status_code == 200
            res = r.json()
            failed_ids = [f["equipment_id"] for f in res.get("failed", [])]
            assert data["eq_b_unalloc_id"] in failed_ids


@pytest.mark.asyncio
async def test_ap_16_allocate_cross_tenant_project_returns_404():
    """16. Company A user cannot allocate to Company B project (403 or 404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.post("/api/v1/equipment/allocate", json={
                "project_id": data["proj_b_id"],
                "equipment_ids": [data["eq_a_id"]]
            }, headers=headers)
            assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_ap_17_deallocate_cross_tenant_equipment_returns_404():
    """17. Company A user cannot deallocate Company B equipment."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.put("/api/v1/equipment/deallocate", json={
                "project_id": data["proj_a_id"],
                "equipment_ids": [data["eq_b_id"]]
            }, headers=headers)
            assert r.status_code == 200
            res = r.json()
            failed_ids = [f["equipment_id"] for f in res.get("failed", [])]
            assert data["eq_b_id"] in failed_ids


@pytest.mark.asyncio
async def test_ap_18_transfer_cross_tenant_project_returns_404():
    """18. Company A user cannot transfer equipment to Company B project (403 or 404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.post("/api/v1/equipment/transfer", json={
                "equipment_id": data["eq_a_id"],
                "to_project_id": data["proj_b_id"]
            }, headers=headers)
            assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_ap_19_create_usage_cross_tenant_equipment_returns_404():
    """19. Company A user cannot create usage on Company B equipment (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.post(f"/api/v1/equipment/{data['eq_b_id']}/usage", json={
                "working_hours": 5.0,
                "fuel_used": 10.0,
                "usage_date": str(date.today()),
            }, headers=headers)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_ap_20_get_usage_cross_tenant_returns_404():
    """20. Company A user cannot view Company B usage (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.get(f"/api/v1/equipment/usage/{data['usage_b_id']}", headers=headers)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_ap_21_update_usage_cross_tenant_returns_404():
    """21. Company A user cannot update Company B usage (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.put(f"/api/v1/equipment/usage/{data['usage_b_id']}", json={
                "working_hours": 9.0
            }, headers=headers)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_ap_22_update_usage_invalid_boq_rejected():
    """22. Updating usage with cross-tenant/invalid BOQ item is rejected (400 or 404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.put(f"/api/v1/equipment/usage/{data['usage_a_id']}", json={
                "boq_item_id": 999999
            }, headers=headers)
            assert r.status_code in (400, 404)


@pytest.mark.asyncio
async def test_ap_23_maintenance_cross_tenant_returns_404():
    """23. Company A user cannot view/update/delete Company B maintenance (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.get(f"/api/v1/equipment/maintenance/{data['maint_b_id']}", headers=headers)
            assert r.status_code == 404
            r = await ac.put(f"/api/v1/equipment/maintenance/{data['maint_b_id']}", json={"description": "Hacked"}, headers=headers)
            assert r.status_code == 404
            r = await ac.delete(f"/api/v1/equipment/maintenance/{data['maint_b_id']}", headers=headers)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_ap_24_maintenance_boq_rollback_on_delete():
    """24. Deleting maintenance triggers clean BOQ rollback without stale aggregation."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            # Create a separate equipment, BOQGroup, and BOQ item for Company A
            async with AsyncSessionLocal() as db:
                eq_m = Equipment(
                    company_id=data["comp_a_id"],
                    project_id=data["proj_a_id"],
                    equipment_name="Maintenance Rollback Test Eq",
                    equipment_code=f"EQ-MBQ-{uuid.uuid4().hex[:6]}",
                    status="IN_PROJECT",
                    condition="GOOD",
                    working_hours=Decimal("10.0"),
                    fuel_used=Decimal("5.0"),
                    is_deleted=False,
                )
                bg = BOQGroup(project_id=data["proj_a_id"], name=f"BG Maint {uuid.uuid4().hex[:6]}")
                db.add_all([eq_m, bg])
                await db.flush()

                boq = BOQ(
                    project_id=data["proj_a_id"],
                    boq_group_id=bg.id,
                    category="Civil",
                    item_name="Equipment Maintenance BOQ",
                    quantity=Decimal("10.0"),
                    unit="LS",
                    unit_cost=Decimal("100.0"),
                    total_cost=Decimal("1000.0"),
                    actual_cost=Decimal("0.0"),
                    is_latest=True,
                )
                db.add(boq)
                await db.commit()
                await db.refresh(eq_m)
                await db.refresh(boq)
                eq_m_id = eq_m.id
                bg_id = bg.id
                boq_id = boq.id

            try:
                # Create maintenance linked to this BOQ
                r_create = await ac.post(f"/api/v1/equipment/{eq_m_id}/maintenance", json={
                    "description": "BOQ Maintenance",
                    "maintenance_date": str(date.today()),
                    "cost": 500.0,
                    "project_id": data["proj_a_id"],
                    "boq_item_id": boq_id,
                }, headers=headers)
                assert r_create.status_code in (200, 201), f"Expected 200/201, got {r_create.status_code}: {r_create.text}"
                m_id = r_create.json()["id"]

                # Verify BOQ actuals was updated to 500.0
                async with AsyncSessionLocal() as db:
                    boq_mid = await db.get(BOQ, boq_id)
                    assert boq_mid.actual_cost == Decimal("500.00") or boq_mid.actual_cost == Decimal("500.0")

                # Delete maintenance
                r_del = await ac.delete(f"/api/v1/equipment/maintenance/{m_id}", headers=headers)
                assert r_del.status_code == 200

                # Verify BOQ actuals rolled back
                async with AsyncSessionLocal() as db:
                    boq_updated = await db.get(BOQ, boq_id)
                    assert boq_updated.actual_cost == Decimal("0.00") or boq_updated.actual_cost == Decimal("0.0")
            finally:
                async with AsyncSessionLocal() as clean_db:
                    await clean_db.execute(delete(Notification))
                    await clean_db.execute(delete(EquipmentMaintenance).where(EquipmentMaintenance.equipment_id == eq_m_id))
                    await clean_db.execute(delete(BOQ).where(BOQ.id == boq_id))
                    await clean_db.execute(delete(BOQGroup).where(BOQGroup.id == bg_id))
                    await clean_db.execute(delete(Equipment).where(Equipment.id == eq_m_id))
                    await clean_db.commit()


@pytest.mark.asyncio
async def test_ap_25_rental_cross_tenant_returns_404():
    """25. Company A user cannot view/update/delete Company B rental (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.get(f"/api/v1/equipment/rental/{data['rental_b_id']}", headers=headers)
            assert r.status_code == 404
            r = await ac.put(f"/api/v1/equipment/rental/{data['rental_b_id']}", json={"client_name": "Hacked"}, headers=headers)
            assert r.status_code == 404
            r = await ac.delete(f"/api/v1/equipment/rental/{data['rental_b_id']}", headers=headers)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_ap_26_rental_boq_rollback_on_delete():
    """26. Deleting rental cleanly rolls back BOQ actuals."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            # Create a separate available equipment, BOQGroup, and BOQ item for Company A
            async with AsyncSessionLocal() as db:
                eq_r = Equipment(
                    company_id=data["comp_a_id"],
                    project_id=None,
                    equipment_name="Rental Rollback Test Eq",
                    equipment_code=f"EQ-RBQ-{uuid.uuid4().hex[:6]}",
                    status="AVAILABLE",
                    condition="GOOD",
                    working_hours=Decimal("10.0"),
                    fuel_used=Decimal("5.0"),
                    is_deleted=False,
                )
                bg = BOQGroup(project_id=data["proj_a_id"], name=f"BG Rental {uuid.uuid4().hex[:6]}")
                db.add_all([eq_r, bg])
                await db.flush()

                boq = BOQ(
                    project_id=data["proj_a_id"],
                    boq_group_id=bg.id,
                    category="Civil",
                    item_name="Equipment Rental BOQ",
                    quantity=Decimal("1.0"),
                    unit="LS",
                    unit_cost=Decimal("2000.0"),
                    total_cost=Decimal("2000.0"),
                    actual_cost=Decimal("0.0"),
                    is_latest=True,
                )
                db.add(boq)
                await db.commit()
                await db.refresh(eq_r)
                await db.refresh(boq)
                eq_r_id = eq_r.id
                bg_id = bg.id
                boq_id = boq.id

            try:
                # Create rental linked to BOQ
                r_create = await ac.post(f"/api/v1/equipment/{eq_r_id}/rental", json={
                    "project_id": data["proj_a_id"],
                    "client_name": "Rollback Test Client",
                    "start_date": str(date.today()),
                    "end_date": str(date.today() + timedelta(days=5)),
                    "rental_cost": 750.0,
                    "boq_item_id": boq_id,
                }, headers=headers)
                assert r_create.status_code in (200, 201), f"Expected 200/201, got {r_create.status_code}: {r_create.text}"
                rent_id = r_create.json()["id"]

                # Delete rental
                r_del = await ac.delete(f"/api/v1/equipment/rental/{rent_id}", headers=headers)
                assert r_del.status_code == 200

                # Verify BOQ actuals rolled back
                async with AsyncSessionLocal() as db:
                    boq_updated = await db.get(BOQ, boq_id)
                    assert boq_updated.actual_cost == Decimal("0.00") or boq_updated.actual_cost == Decimal("0.0")
            finally:
                async with AsyncSessionLocal() as clean_db:
                    await clean_db.execute(delete(EquipmentRental).where(EquipmentRental.equipment_id == eq_r_id))
                    await clean_db.execute(delete(BOQ).where(BOQ.id == boq_id))
                    await clean_db.execute(delete(BOQGroup).where(BOQGroup.id == bg_id))
                    await clean_db.execute(delete(Equipment).where(Equipment.id == eq_r_id))
                    await clean_db.commit()


@pytest.mark.asyncio
async def test_ap_27_purchase_cross_tenant_returns_404():
    """27. Company A user cannot view/update/delete Company B purchase (404)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            r = await ac.get(f"/api/v1/equipment/purchase/{data['purchase_b_id']}", headers=headers)
            assert r.status_code == 404
            r = await ac.put(f"/api/v1/equipment/purchase/{data['purchase_b_id']}", json={"vendor_name": "Hacked"}, headers=headers)
            assert r.status_code == 404
            r = await ac.delete(f"/api/v1/equipment/purchase/{data['purchase_b_id']}", headers=headers)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_ap_28_purchase_invoice_scoped_to_company():
    """28. Invoice uniqueness is scoped to company (duplicate within company returns 400)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['token_admin_a']}"}
            # Fetch purchase A's invoice number
            async with AsyncSessionLocal() as db:
                p_a = await db.get(EquipmentPurchase, data["purchase_a_id"])
                inv_num = p_a.invoice_number

            # Company A attempts to create another purchase with SAME invoice number -> Should return 400
            payload = {
                "project_id": data["proj_a_id"],
                "purchase_type": "NEW",
                "purchase_date": str(date.today()),
                "vendor_name": "Vendor A Duplicate",
                "invoice_number": inv_num,
                "quantity": 1,
                "unit_price": 5000.0,
                "total_amount": 5000.0,
            }
            r = await ac.post("/api/v1/equipment/purchase", json=payload, headers=headers_a)
            assert r.status_code == 400, f"Expected 400 for duplicate invoice, got {r.status_code}: {r.text}"
            assert "Invoice number already exists" in r.text


@pytest.mark.asyncio
async def test_ap_29_super_admin_cross_tenant_access():
    """29. Super Admin can view equipment across companies with company filter."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_super']}"}
            # With Company A filter
            r_a = await ac.get(f"/api/v1/equipment?company_id={data['comp_a_id']}", headers=headers)
            assert r_a.status_code == 200
            ids_a = [item["id"] for item in r_a.json().get("items", [])]
            assert data["eq_a_id"] in ids_a
            assert data["eq_b_id"] not in ids_a

            # With Company B filter
            r_b = await ac.get(f"/api/v1/equipment?company_id={data['comp_b_id']}", headers=headers)
            assert r_b.status_code == 200
            ids_b = [item["id"] for item in r_b.json().get("items", [])]
            assert data["eq_b_id"] in ids_b
            assert data["eq_a_id"] not in ids_b


@pytest.mark.asyncio
async def test_ap_30_equipment_reports_tenant_isolated():
    """30. KPI, Usage, Maintenance, Rental reports are isolated by company."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['token_admin_a']}"}
            # KPI
            r_kpi = await ac.get("/api/v1/equipment/kpi", headers=headers)
            assert r_kpi.status_code == 200
            # Usage
            r_usage = await ac.get("/api/v1/equipment/usage", headers=headers)
            assert r_usage.status_code == 200
            usage_ids = [u["id"] for u in r_usage.json()]
            assert data["usage_a_id"] in usage_ids
            assert data["usage_b_id"] not in usage_ids
            # Maintenance
            r_maint = await ac.get("/api/v1/equipment/maintenance", headers=headers)
            assert r_maint.status_code == 200
            maint_ids = [m["id"] for m in r_maint.json()]
            assert data["maint_a_id"] in maint_ids
            assert data["maint_b_id"] not in maint_ids
            # Rental
            r_rentals = await ac.get("/api/v1/equipment/rental", headers=headers)
            assert r_rentals.status_code == 200
            rent_ids = [r["id"] for r in r_rentals.json()]
            assert data["rental_a_id"] in rent_ids
            assert data["rental_b_id"] not in rent_ids
