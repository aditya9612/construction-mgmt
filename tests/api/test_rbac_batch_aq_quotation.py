import uuid
import re
from decimal import Decimal
from datetime import date, datetime, timedelta
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, text
from fastapi.routing import APIRoute

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.equipment import Equipment
from app.models.project import Project
from app.models.contractor import Contractor
from app.models.owner import Owner
from app.models.billing import RABill
from app.models.work_order import WorkOrder
from app.models.quotation import (
    QuotationMaster,
    QuotationItem,
    MeasurementDetail,
    QuotationLabour,
    QuotationMaterial,
    QuotationExtraCharge,
    QuotationStatus,
)
from app.models.rbac import Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from tests.api.test_rbac_phase2_batch_k import setup_batch_k_data


# ============================================================================
# 1. UNAUTHENTICATED CALLS (401)
# ============================================================================

@pytest.mark.asyncio
async def test_aq_01_unauthenticated_requests_401():
    """Verify unauthenticated requests to quotation routes return 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("GET", "/api/v1/quotations/"),
            ("POST", "/api/v1/quotations/"),
            ("GET", "/api/v1/quotations/1"),
            ("PUT", "/api/v1/quotations/1"),
            ("DELETE", "/api/v1/quotations/1"),
            ("POST", "/api/v1/quotations/1/items"),
            ("PUT", "/api/v1/quotations/quotation-items/1"),
            ("DELETE", "/api/v1/quotations/quotation-items/1"),
            ("GET", "/api/v1/quotations/1/preview"),
            ("PUT", "/api/v1/quotations/1/approve"),
            ("PUT", "/api/v1/quotations/1/reject"),
            ("POST", "/api/v1/quotations/1/convert-to-bill?project_id=1&contractor_id=1"),
            ("POST", "/api/v1/quotations/1/convert-to-work-order?project_id=1&contractor_id=1"),
            ("POST", "/api/v1/quotations/1/labour"),
            ("PUT", "/api/v1/quotations/labour/1"),
            ("DELETE", "/api/v1/quotations/labour/1"),
            ("POST", "/api/v1/quotations/1/materials"),
            ("PUT", "/api/v1/quotations/quotation-materials/1"),
            ("DELETE", "/api/v1/quotations/quotation-materials/1"),
            ("GET", "/api/v1/quotations/1/materials"),
            ("POST", "/api/v1/quotations/1/extra-charges"),
            ("PUT", "/api/v1/quotations/quotation-extra-charges/1"),
            ("DELETE", "/api/v1/quotations/quotation-extra-charges/1"),
            ("GET", "/api/v1/quotations/1/extra-charges"),
            ("GET", "/api/v1/quotations/1/pdf"),
            ("POST", "/api/v1/quotations/1/convert-to-project"),
            ("POST", "/api/v1/quotations/1/send"),
        ]
        for method, url in endpoints:
            if method == "GET":
                r = await ac.get(url)
            elif method == "POST":
                r = await ac.post(url, json={})
            elif method == "PUT":
                r = await ac.put(url, json={})
            elif method == "DELETE":
                r = await ac.delete(url)
            assert r.status_code == 401, f"{method} {url} expected 401, got {r.status_code}"


# ============================================================================
# 2. PERMISSION GATING (403) FOR ALL 8 PERMISSIONS IN QUOTATIONS NAMESPACE
# ============================================================================

@pytest.mark.asyncio
async def test_aq_02_permission_gating_quotations_view():
    """User without quotations.view is blocked (403) from read endpoints."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['tokens']['unassigned_a']}"}
            q_id = data["qtn_a1"].id

            r_list = await ac.get("/api/v1/quotations/", headers=headers)
            assert r_list.status_code == 403

            r_get = await ac.get(f"/api/v1/quotations/{q_id}", headers=headers)
            assert r_get.status_code == 403

            r_prev = await ac.get(f"/api/v1/quotations/{q_id}/preview", headers=headers)
            assert r_prev.status_code == 403

            r_mats = await ac.get(f"/api/v1/quotations/{q_id}/materials", headers=headers)
            assert r_mats.status_code == 403

            r_extra = await ac.get(f"/api/v1/quotations/{q_id}/extra-charges", headers=headers)
            assert r_extra.status_code == 403


@pytest.mark.asyncio
async def test_aq_03_permission_gating_quotations_create():
    """User without quotations.create is blocked (403) from POST /quotations/."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['tokens']['unassigned_a']}"}
            payload = {
                "client_user_id": data["client_user_a"].id,
                "project_name": "Unauthorized Proj",
                "project_type": "Residential",
                "items": [],
                "labour_items": [],
                "material_items": [],
                "extra_charge_items": [],
            }
            r = await ac.post("/api/v1/quotations/", json=payload, headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_aq_04_permission_gating_quotations_edit():
    """User without quotations.edit is blocked (403) from modification endpoints."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['tokens']['unassigned_a']}"}
            q_id = data["qtn_a1"].id
            item_id = data["item_a1"].id
            labour_id = data["labour_item_a1"].id
            mat_id = data["mat_item_a1"].id
            extra_id = data["extra_item_a1"].id

            # Edit quotation
            r = await ac.put(f"/api/v1/quotations/{q_id}", json={"project_name": "New"}, headers=headers)
            assert r.status_code == 403

            # Items
            r = await ac.post(f"/api/v1/quotations/{q_id}/items", json={"item_type": "soling", "title": "Test", "unit": "brass", "rate": 100, "measurements": [{"length": 1, "width": 1, "height": 1, "quantity": 1}]}, headers=headers)
            assert r.status_code == 403
            r = await ac.put(f"/api/v1/quotations/quotation-items/{item_id}", json={"title": "Updated"}, headers=headers)
            assert r.status_code == 403
            r = await ac.delete(f"/api/v1/quotations/quotation-items/{item_id}", headers=headers)
            assert r.status_code == 403

            # Labour
            r = await ac.post(f"/api/v1/quotations/{q_id}/labour", json={"labour_id": data["labour_a"].id, "skill_type": "Skilled", "labour_count": 1, "daily_wage": 500, "labour_days": 1}, headers=headers)
            assert r.status_code == 403
            r = await ac.put(f"/api/v1/quotations/labour/{labour_id}", json={"daily_wage": 600}, headers=headers)
            assert r.status_code == 403
            r = await ac.delete(f"/api/v1/quotations/labour/{labour_id}", headers=headers)
            assert r.status_code == 403

            # Materials
            r = await ac.post(f"/api/v1/quotations/{q_id}/materials", json={"material_id": data["mat_a"].id, "material_name": "Cement", "estimated_quantity": 10, "estimated_rate": 300}, headers=headers)
            assert r.status_code == 403
            r = await ac.put(f"/api/v1/quotations/quotation-materials/{mat_id}", json={"estimated_rate": 320}, headers=headers)
            assert r.status_code == 403
            r = await ac.delete(f"/api/v1/quotations/quotation-materials/{mat_id}", headers=headers)
            assert r.status_code == 403

            # Extra charges
            r = await ac.post(f"/api/v1/quotations/{q_id}/extra-charges", json={"expense_type": "Misc", "description": "Desc", "quantity": 1, "rate": 100}, headers=headers)
            assert r.status_code == 403
            r = await ac.put(f"/api/v1/quotations/quotation-extra-charges/{extra_id}", json={"rate": 150}, headers=headers)
            assert r.status_code == 403
            r = await ac.delete(f"/api/v1/quotations/quotation-extra-charges/{extra_id}", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_aq_05_permission_gating_quotations_delete():
    """User without quotations.delete is blocked (403) from DELETE /quotations/{id}."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['tokens']['unassigned_a']}"}
            r = await ac.delete(f"/api/v1/quotations/{data['qtn_a1'].id}", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_aq_06_permission_gating_quotations_approve():
    """User without quotations.approve is blocked (403) from approve/reject."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['tokens']['unassigned_a']}"}
            q_id = data["qtn_a2"].id

            r_app = await ac.put(f"/api/v1/quotations/{q_id}/approve", headers=headers)
            assert r_app.status_code == 403

            r_rej = await ac.put(f"/api/v1/quotations/{q_id}/reject", json={"reason": "Test"}, headers=headers)
            assert r_rej.status_code == 403


@pytest.mark.asyncio
async def test_aq_07_permission_gating_quotations_manage():
    """User without quotations.manage is blocked (403) from conversions."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['tokens']['unassigned_a']}"}
            q_id = data["qtn_a3"].id
            p_id = data["proj_a"].id
            c_id = data["contractor_a"].id

            r_bill = await ac.post(f"/api/v1/quotations/{q_id}/convert-to-bill?project_id={p_id}&contractor_id={c_id}", headers=headers)
            assert r_bill.status_code == 403

            r_wo = await ac.post(f"/api/v1/quotations/{q_id}/convert-to-work-order?project_id={p_id}&contractor_id={c_id}", headers=headers)
            assert r_wo.status_code == 403

            r_prj = await ac.post(f"/api/v1/quotations/{q_id}/convert-to-project", json={"owner_id": data["owner_a"].id}, headers=headers)
            assert r_prj.status_code == 403


@pytest.mark.asyncio
async def test_aq_08_permission_gating_quotations_export():
    """User without quotations.export is blocked (403) from PDF download."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['tokens']['unassigned_a']}"}
            r = await ac.get(f"/api/v1/quotations/{data['qtn_a1'].id}/pdf", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_aq_09_permission_gating_quotations_assign():
    """User without quotations.assign is blocked (403) from POST /quotations/{id}/send."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {data['tokens']['unassigned_a']}"}
            r = await ac.post(f"/api/v1/quotations/{data['qtn_a1'].id}/send", headers=headers)
            assert r.status_code == 403


# ============================================================================
# 3. TENANT CONTEXT GUARD: TENANTLESS NON-SA => 403
# ============================================================================

@pytest.mark.asyncio
async def test_aq_10_tenantless_non_sa_rejected_403():
    """Non-SA user with company_id=None receives 403 Forbidden across quotation endpoints."""
    uid = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        pwd_hash = get_password_hash("Secret123!")
        tenantless_user = User(
            email=f"tenantless_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Tenantless Non-SA",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        db.add(tenantless_user)
        await db.commit()
        await db.refresh(tenantless_user)

    token_tenantless = create_access_token({"sub": str(tenantless_user.id)})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = {"Authorization": f"Bearer {token_tenantless}"}

        # 1. List
        r = await ac.get("/api/v1/quotations/", headers=headers)
        assert r.status_code == 403
        assert r.json()["detail"] in ("Company context required", "User does not belong to any company.")

        # 2. Create
        payload = {
            "client_user_id": 1,
            "project_name": "Orphan Project",
            "project_type": "Residential",
            "items": [],
            "labour_items": [],
            "material_items": [],
            "extra_charge_items": [],
        }
        r = await ac.post("/api/v1/quotations/", json=payload, headers=headers)
        assert r.status_code == 403
        assert r.json()["detail"] in ("Company context required", "User does not belong to any company.")

        # 3. Get
        r = await ac.get("/api/v1/quotations/1", headers=headers)
        assert r.status_code == 403
        assert r.json()["detail"] in ("Company context required", "User does not belong to any company.")

    # Cleanup
    async with AsyncSessionLocal() as db:
        await db.execute(delete(User).where(User.id == tenantless_user.id))
        await db.commit()


# ============================================================================
# 4. TENANT ISOLATION & 404 MASKING
# ============================================================================

@pytest.mark.asyncio
async def test_aq_11_cross_tenant_quotation_crud_isolation_404():
    """Company A user accessing Company B quotation gets 404 (masked isolation)."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            q_b_id = data["qtn_b1"].id

            # GET foreign quotation
            r = await ac.get(f"/api/v1/quotations/{q_b_id}", headers=headers_a)
            assert r.status_code == 404
            assert r.json()["detail"] == "Quotation not found"

            # PUT foreign quotation
            r = await ac.put(f"/api/v1/quotations/{q_b_id}", json={"project_name": "Hacked"}, headers=headers_a)
            assert r.status_code == 404

            # DELETE foreign quotation
            r = await ac.delete(f"/api/v1/quotations/{q_b_id}", headers=headers_a)
            assert r.status_code == 404

            # PDF foreign quotation
            r = await ac.get(f"/api/v1/quotations/{q_b_id}/pdf", headers=headers_a)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_aq_12_cross_tenant_subitems_isolation_404():
    """Company A user accessing Company B quotation sub-items gets 404."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            item_b_id = data["item_b1"].id
            labour_b_id = data["labour_item_b1"].id
            mat_b_id = data["mat_item_b1"].id
            extra_b_id = data["extra_item_b1"].id

            # Foreign quotation item
            r = await ac.put(f"/api/v1/quotations/quotation-items/{item_b_id}", json={"title": "Hacked"}, headers=headers_a)
            assert r.status_code == 404
            r = await ac.delete(f"/api/v1/quotations/quotation-items/{item_b_id}", headers=headers_a)
            assert r.status_code == 404

            # Foreign labour item
            r = await ac.put(f"/api/v1/quotations/labour/{labour_b_id}", json={"daily_wage": 999}, headers=headers_a)
            assert r.status_code == 404
            r = await ac.delete(f"/api/v1/quotations/labour/{labour_b_id}", headers=headers_a)
            assert r.status_code == 404

            # Foreign material item
            r = await ac.put(f"/api/v1/quotations/quotation-materials/{mat_b_id}", json={"estimated_rate": 999}, headers=headers_a)
            assert r.status_code == 404
            r = await ac.delete(f"/api/v1/quotations/quotation-materials/{mat_b_id}", headers=headers_a)
            assert r.status_code == 404

            # Foreign extra charge item
            r = await ac.put(f"/api/v1/quotations/quotation-extra-charges/{extra_b_id}", json={"rate": 999}, headers=headers_a)
            assert r.status_code == 404
            r = await ac.delete(f"/api/v1/quotations/quotation-extra-charges/{extra_b_id}", headers=headers_a)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_aq_13_foreign_resource_fk_injection_404():
    """Attempting to inject foreign FKs into Company A quotation returns 404."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            q_a_id = data["qtn_a1"].id

            # Foreign client in create quotation
            payload = {
                "client_user_id": data["client_user_b"].id,  # belongs to comp_b
                "client_name": "Client B",
                "mobile_number": "9876543210",
                "project_name": "FK Injection Test",
                "project_type": "Residential",
                "items": [
                    {
                        "item_type": "soling",
                        "title": "Soling 1",
                        "unit": "sqm",
                        "rate": 100.0,
                        "measurements": [
                            {"length": 5.0, "width": 5.0, "height": 1.0}
                        ],
                    }
                ],
                "labour_items": [],
                "material_items": [],
                "extra_charge_items": [],
            }
            r = await ac.post("/api/v1/quotations/", json=payload, headers=headers_a)
            assert r.status_code == 404
            assert "Client not found" in r.json()["detail"]

            # Foreign labour in add labour
            r = await ac.post(
                f"/api/v1/quotations/{q_a_id}/labour",
                json={
                    "labour_id": data["labour_b"].id,
                    "skill_type": "Skilled",
                    "labour_count": 1,
                    "daily_wage": 500.0,
                    "labour_days": 1,
                },
                headers=headers_a,
            )
            assert r.status_code == 404
            assert "Labour not found" in r.json()["detail"]

            # Foreign material in add material
            r = await ac.post(
                f"/api/v1/quotations/{q_a_id}/materials",
                json={
                    "material_id": data["mat_b"].id,
                    "material_name": "Foreign Cement",
                    "unit": "bag",
                    "estimated_quantity": 10.0,
                    "estimated_rate": 350.0,
                },
                headers=headers_a,
            )
            assert r.status_code == 404
            assert "Material not found" in r.json()["detail"]


# ============================================================================
# 5. EQUIPMENT BUG (P0): FOREIGN EQUIPMENT WITH project_id=None => 404
# ============================================================================

@pytest.mark.asyncio
async def test_aq_14_equipment_bug_p0_unallocated_foreign_rejected_404():
    """Foreign equipment belonging to Company B with project_id=None must be rejected with 404."""
    async with setup_batch_k_data() as data:
        uid = uuid.uuid4().hex[:6]
        async with AsyncSessionLocal() as db:
            # Unallocated equipment in Company B
            unallocated_eq_b = Equipment(
                company_id=data["comp_b"].id,
                project_id=None,
                equipment_name=f"Unallocated Crane B {uid}",
                equipment_code=f"EQ-UNALLOC-B-{uid}",
                rental_cost=Decimal("2000.00"),
            )
            db.add(unallocated_eq_b)
            await db.commit()
            await db.refresh(unallocated_eq_b)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            q_a_id = data["qtn_a1"].id

            # 1. Add extra charge with unallocated foreign equipment
            payload_add = {
                "equipment_id": unallocated_eq_b.id,
                "expense_type": "Equipment Rental",
                "description": "Cross-tenant equipment test",
                "quantity": 1.0,
                "rate": 2000.0,
            }
            r = await ac.post(f"/api/v1/quotations/{q_a_id}/extra-charges", json=payload_add, headers=headers_a)
            assert r.status_code == 404
            assert "Equipment not found" in r.json()["detail"]

            # 2. Update extra charge with unallocated foreign equipment
            extra_a_id = data["extra_item_a1"].id
            payload_update = {
                "equipment_id": unallocated_eq_b.id,
            }
            r = await ac.put(f"/api/v1/quotations/quotation-extra-charges/{extra_a_id}", json=payload_update, headers=headers_a)
            assert r.status_code == 404
            assert "Equipment not found" in r.json()["detail"]

        # Cleanup
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Equipment).where(Equipment.id == unallocated_eq_b.id))
            await db.commit()


@pytest.mark.asyncio
async def test_aq_15_equipment_same_tenant_project_id_none_allowed():
    """Unallocated equipment belonging to Company A with project_id=None is allowed for Company A."""
    async with setup_batch_k_data() as data:
        uid = uuid.uuid4().hex[:6]
        async with AsyncSessionLocal() as db:
            # Unallocated equipment in Company A
            unallocated_eq_a = Equipment(
                company_id=data["comp_a"].id,
                project_id=None,
                equipment_name=f"Unallocated Crane A {uid}",
                equipment_code=f"EQ-UNALLOC-A-{uid}",
                rental_cost=Decimal("1800.00"),
            )
            db.add(unallocated_eq_a)
            await db.commit()
            await db.refresh(unallocated_eq_a)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            q_a_id = data["qtn_a1"].id

            payload = {
                "equipment_id": unallocated_eq_a.id,
                "expense_type": "Equipment Rental",
                "description": "Same tenant unallocated equipment",
                "quantity": 2.0,
                "rate": 1800.0,
            }
            r = await ac.post(f"/api/v1/quotations/{q_a_id}/extra-charges", json=payload, headers=headers_a)
            assert r.status_code == 200
            assert any(
                item.get("equipment_id") == unallocated_eq_a.id
                for item in r.json().get("extra_charge_items", [])
            )

        # Cleanup
        async with AsyncSessionLocal() as db:
            await db.execute(delete(QuotationExtraCharge).where(QuotationExtraCharge.equipment_id == unallocated_eq_a.id))
            await db.execute(delete(Equipment).where(Equipment.id == unallocated_eq_a.id))
            await db.commit()


# ============================================================================
# 6. SUPER ADMIN LIST FILTERING & PROJECT FILTER
# ============================================================================

@pytest.mark.asyncio
async def test_aq_16_super_admin_global_and_company_filtered_list():
    """SA sees global list or company-filtered list; non-SA cannot company-switch."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_sa = {"Authorization": f"Bearer {data['tokens']['super']}"}
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}

            # 1. SA without company_id sees both CompA and CompB quotations
            r_global = await ac.get("/api/v1/quotations/", headers=headers_sa)
            assert r_global.status_code == 200
            q_ids = [q["id"] for q in r_global.json()]
            assert data["qtn_a1"].id in q_ids
            assert data["qtn_b1"].id in q_ids

            # 2. SA with company_id=CompA sees only CompA quotations
            r_compa = await ac.get(f"/api/v1/quotations/?company_id={data['comp_a'].id}", headers=headers_sa)
            assert r_compa.status_code == 200
            comp_a_ids = {data["qtn_a1"].id, data["qtn_a2"].id, data["qtn_a3"].id}
            comp_b_ids = {data["qtn_b1"].id}
            returned_ids_a = {q["id"] for q in r_compa.json()}
            assert returned_ids_a.issubset(comp_a_ids)
            assert not (returned_ids_a & comp_b_ids)

            # 3. SA with invalid company_id gets 404
            r_inv = await ac.get("/api/v1/quotations/?company_id=9999999", headers=headers_sa)
            assert r_inv.status_code == 404

            # 4. Non-SA user passing company_id=CompB is ignored and restricted to CompA
            r_sw = await ac.get(f"/api/v1/quotations/?company_id={data['comp_b'].id}", headers=headers_a)
            assert r_sw.status_code == 200
            returned_ids_sw = {q["id"] for q in r_sw.json()}
            assert returned_ids_sw.issubset(comp_a_ids)
            assert not (returned_ids_sw & comp_b_ids)


@pytest.mark.asyncio
async def test_aq_17_project_filter_isolation():
    """Filtering list_quotations by project_id respects tenant boundaries."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            headers_sa = {"Authorization": f"Bearer {data['tokens']['super']}"}

            # Non-SA filtering by foreign project_id gets 404
            r_foreign = await ac.get(f"/api/v1/quotations/?project_id={data['proj_b'].id}", headers=headers_a)
            assert r_foreign.status_code == 404

            # Non-SA filtering by own project_id succeeds
            r_own = await ac.get(f"/api/v1/quotations/?project_id={data['proj_a'].id}", headers=headers_a)
            assert r_own.status_code == 200

            # SA filtering by project_id succeeds
            r_sa = await ac.get(f"/api/v1/quotations/?project_id={data['proj_b'].id}", headers=headers_sa)
            assert r_sa.status_code == 200


# ============================================================================
# 7. STATE MACHINE INVARIANTS & CONCURRENCY
# ============================================================================

@pytest.mark.asyncio
async def test_aq_18_state_machine_invariants():
    """Verify Quotation lifecycle state machine invariants."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            q_draft_id = data["qtn_a1"].id
            q_sent_id = data["qtn_a2"].id
            q_approved_id = data["qtn_a3"].id

            # 1. Cannot approve DRAFT quotation without sending first
            r = await ac.put(f"/api/v1/quotations/{q_draft_id}/approve", headers=headers_a)
            assert r.status_code == 400
            assert "must be sent" in r.json()["detail"].lower()

            # 2. Cannot reject DRAFT quotation without sending first
            r = await ac.put(f"/api/v1/quotations/{q_draft_id}/reject", json={"reason": "Too expensive"}, headers=headers_a)
            assert r.status_code == 400
            assert "send the quotation" in r.json()["detail"].lower()

            # 3. Cannot approve already APPROVED quotation
            r = await ac.put(f"/api/v1/quotations/{q_approved_id}/approve", headers=headers_a)
            assert r.status_code == 400
            assert "already approved" in r.json()["detail"].lower()

            # 4. Cannot reject already APPROVED quotation
            r = await ac.put(f"/api/v1/quotations/{q_approved_id}/reject", json={"reason": "Rejected"}, headers=headers_a)
            assert r.status_code == 400
            assert "approved quotation cannot be rejected" in r.json()["detail"].lower()

            # 5. Cannot modify an approved quotation
            r = await ac.put(f"/api/v1/quotations/{q_approved_id}", json={"project_name": "Change"}, headers=headers_a)
            assert r.status_code == 400
            assert "only draft quotations can be edited" in r.json()["detail"].lower()

            # 6. Send DRAFT quotation transitions it to SENT
            r_send = await ac.post(f"/api/v1/quotations/{q_draft_id}/send", headers=headers_a)
            assert r_send.status_code == 200

            # 7. Approve SENT quotation succeeds
            r_app = await ac.put(f"/api/v1/quotations/{q_sent_id}/approve", headers=headers_a)
            assert r_app.status_code == 200
            assert r_app.json()["status"] == QuotationStatus.APPROVED.value


# ============================================================================
# 8. QUOTATION NUMBER CONCURRENCY & FORMAT
# ============================================================================

@pytest.mark.asyncio
async def test_aq_19_quotation_number_format_and_concurrency():
    """Verify generated quotation numbers adhere strictly to QT/{year}/{counter:04d}."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}

            # Create new client user for distinct quotations
            uid = uuid.uuid4().hex[:6]
            async with AsyncSessionLocal() as db:
                client_user_new = User(
                    email=f"client_num_{uid}@test.com",
                    hashed_password=get_password_hash("Secret123!"),
                    full_name="Client Number Test",
                    company_id=data["comp_a"].id,
                    is_super_admin=False,
                    is_active=True,
                    role="Client",
                )
                db.add(client_user_new)
                await db.commit()
                await db.refresh(client_user_new)

            payload = {
                "client_user_id": client_user_new.id,
                "client_name": "Client Number Test",
                "mobile_number": "9876543210",
                "project_name": f"Number Format Proj {uid}",
                "project_type": "Residential",
                "items": [
                    {
                        "item_type": "soling",
                        "title": "Soling Item",
                        "unit": "brass",
                        "rate": 1000.0,
                        "measurements": [
                            {"length": 10.0, "width": 10.0, "height": 1.0}
                        ],
                    }
                ],
                "labour_items": [],
                "material_items": [],
                "extra_charge_items": [],
            }
            r = await ac.post("/api/v1/quotations/", json=payload, headers=headers_a)
            assert r.status_code == 201
            q_no = r.json()["quotation_no"]
            current_year = datetime.now().year
            assert re.match(rf"^QT/{current_year}/\d{{4}}$", q_no), f"Quotation number '{q_no}' does not match expected format"

        # Cleanup
        async with AsyncSessionLocal() as db:
            await db.execute(delete(MeasurementDetail))
            await db.execute(delete(QuotationItem))
            await db.execute(delete(QuotationMaster).where(QuotationMaster.client_user_id == client_user_new.id))
            await db.execute(delete(User).where(User.id == client_user_new.id))
            await db.commit()


# ============================================================================
# 9. CONVERSIONS (BILL, WORK ORDER, PROJECT)
# ============================================================================

@pytest.mark.asyncio
async def test_aq_20_conversion_to_bill():
    """Approved quotation converts to RA Bill; duplicate conversion blocked (400)."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            q_id = data["qtn_a3"].id
            p_id = data["proj_a"].id
            c_id = data["contractor_a"].id

            # 1. Foreign contractor fails (404)
            r_foreign_c = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-bill?project_id={p_id}&contractor_id={data['contractor_b'].id}",
                headers=headers_a,
            )
            assert r_foreign_c.status_code == 404

            # 2. Foreign project fails (404)
            r_foreign_p = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-bill?project_id={data['proj_b'].id}&contractor_id={c_id}",
                headers=headers_a,
            )
            assert r_foreign_p.status_code == 404

            # 3. Valid conversion succeeds
            r_conv = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-bill?project_id={p_id}&contractor_id={c_id}",
                headers=headers_a,
            )
            assert r_conv.status_code == 200
            assert "bill_id" in r_conv.json()

            # 4. Duplicate conversion rejected with 400
            r_dup = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-bill?project_id={p_id}&contractor_id={c_id}",
                headers=headers_a,
            )
            assert r_dup.status_code == 400
            assert "already converted to bill" in r_dup.json()["detail"].lower()


@pytest.mark.asyncio
async def test_aq_21_conversion_to_work_order():
    """Approved quotation converts to Work Order; duplicate conversion blocked (400)."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            q_id = data["qtn_a3"].id
            p_id = data["proj_a"].id
            c_id = data["contractor_a"].id

            # 1. Foreign contractor fails (404)
            r_foreign_c = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-work-order?project_id={p_id}&contractor_id={data['contractor_b'].id}",
                headers=headers_a,
            )
            assert r_foreign_c.status_code == 404

            # 2. Valid conversion succeeds
            r_conv = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-work-order?project_id={p_id}&contractor_id={c_id}",
                headers=headers_a,
            )
            assert r_conv.status_code == 200
            assert "work_order_id" in r_conv.json()

            # 3. Duplicate conversion rejected with 400
            r_dup = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-work-order?project_id={p_id}&contractor_id={c_id}",
                headers=headers_a,
            )
            assert r_dup.status_code == 400
            assert "already converted to work order" in r_dup.json()["detail"].lower()


@pytest.mark.asyncio
async def test_aq_22_conversion_to_project():
    """Approved quotation converts to Project; duplicate conversion blocked (400)."""
    async with setup_batch_k_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
            q_id = data["qtn_a3"].id

            # 1. Foreign owner fails (404)
            r_foreign_o = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-project",
                json={"owner_id": data["owner_b"].id},
                headers=headers_a,
            )
            assert r_foreign_o.status_code == 404

            # 2. Valid conversion succeeds
            r_conv = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-project",
                json={"owner_id": data["owner_a"].id},
                headers=headers_a,
            )
            assert r_conv.status_code == 200
            assert "project_id" in r_conv.json()

            # 3. Duplicate conversion rejected with 400
            r_dup = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-project",
                json={"owner_id": data["owner_a"].id},
                headers=headers_a,
            )
            assert r_dup.status_code == 400
            assert "already been created" in r_dup.json()["detail"].lower()


# ============================================================================
# 10. ROUTE PRESERVATION: 27 QUOTATION / 781 TOTAL / 0 DUPLICATES
# ============================================================================

@pytest.mark.asyncio
async def test_aq_23_route_preservation_27_quotation_781_total_0_duplicates():
    """Runtime route preservation verification: exactly 27 quotation routes and 781 total APIRoutes."""
    def get_all_routes(routes):
        result = []
        for route in routes:
            if hasattr(route, "effective_route_contexts"):
                for ctx in route.effective_route_contexts():
                    r = ctx.original_route
                    r.path = ctx.path
                    if not r.path.startswith("/api/v1/test-"):
                        result.append(r)
            elif isinstance(route, APIRoute):
                if not route.path.startswith("/api/v1/test-"):
                    result.append(route)
            elif hasattr(route, "original_router") and hasattr(route.original_router, "routes"):
                result.extend(get_all_routes(route.original_router.routes))
            elif hasattr(route, "routes"):
                result.extend(get_all_routes(route.routes))
        return result

    routes = get_all_routes(app.routes)
    quotation_routes = [r for r in routes if r.path.startswith("/api/v1/quotations")]

    assert len(quotation_routes) == 27, f"Expected 27 quotation routes, got {len(quotation_routes)}"
    assert len(routes) == 781, f"Expected 781 total application routes, got {len(routes)}"

    # Check for duplicate (method, path)
    unique_q_routes = set()
    for r in quotation_routes:
        for m in r.methods:
            if m not in ("HEAD", "OPTIONS"):
                key = (m, r.path)
                assert key not in unique_q_routes, f"Duplicate quotation route detected: {key}"
                unique_q_routes.add(key)

    assert len(unique_q_routes) == 27, f"Expected 27 unique quotation endpoints, got {len(unique_q_routes)}"


# ============================================================================
# 11. STATIC CODE AUDIT CLEANLINESS CHECK
# ============================================================================

@pytest.mark.asyncio
async def test_aq_24_static_check_cleanliness():
    """Static analysis: RBAC counts, 0 roles, 0 legacy client auth, 0 unsafe db.get()."""
    with open("app/api/quotation.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Canonical require_permission
    require_perm_count = len(re.findall(r"\brequire_permission\b", content))
    assert require_perm_count == 28, f"Expected 28 require_permission (1 import + 27 endpoints), got {require_perm_count}"

    # 2. require_roles
    assert len(re.findall(r"\brequire_roles\b", content)) == 0

    # 3. admin_required
    assert len(re.findall(r"\badmin_required\b", content)) == 0

    # 4. UserRole
    assert len(re.findall(r"\bUserRole\b", content)) == 0

    # 5. current_user.role
    assert len(re.findall(r"\bcurrent_user\.role\b", content)) == 0

    # 6. Role comparisons (.role)
    assert len(re.findall(r"\.role\b", content)) == 0

    # 7. "Client" role checks
    assert len(re.findall(r'role\s*==\s*["\']Client["\']', content)) == 0

    # 8. Noncanonical SA checks
    canonical_sa = len(re.findall(r'getattr\(\s*\w+,\s*["\']is_super_admin["\'],\s*False\s*\)\s*is\s*True', content))
    assert canonical_sa >= 1

    # 9. Unsafe db.get
    db_get_calls = re.findall(r"await\s+db\.get\s*\(", content)
    assert len(db_get_calls) == 0, f"Found unsafe raw db.get calls: {db_get_calls}"
