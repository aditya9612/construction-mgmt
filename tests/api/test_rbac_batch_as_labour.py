import uuid
from decimal import Decimal
from datetime import date, timedelta
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.models.labour import Labour, LabourPayroll, LabourWageRecord
from app.core.security import get_password_hash, create_access_token
from tests.api.test_rbac_phase2_batch_h import setup_batch_h_data


# ============================================================================
# 1. UNAUTHENTICATED REQUESTS (401) FOR ALL 32 LABOUR ENDPOINTS
# ============================================================================

@pytest.mark.asyncio
async def test_as_01_unauthenticated_requests_all_32_endpoints_401():
    """Verify that all 32 labour endpoints strictly return 401 when unauthenticated."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        today = date.today()
        endpoints = [
            ("POST", "/api/v1/labour", {"data": {"labour_name": "Unauth Worker"}}),
            ("GET", "/api/v1/labour", {}),
            ("GET", "/api/v1/labour/payroll?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/stats?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/contractor-liability?project_id=1&month=1&year=2026", {}),
            ("PUT", "/api/v1/labour/9999", {"data": {"labour_name": "Updated"}}),
            ("DELETE", "/api/v1/labour/9999", {}),
            ("GET", "/api/v1/labour/9999/weekly-report", {}),
            ("GET", "/api/v1/labour/9999/monthly-report", {}),
            ("POST", "/api/v1/labour/payroll/generate", {"json": {"project_id": 1, "month": 1, "year": 2026}}),
            ("POST", "/api/v1/labour/payroll/lock", {"json": {"payroll_ids": [1]}}),
            ("POST", "/api/v1/labour/payroll/unlock", {"json": {"payroll_ids": [1]}}),
            ("POST", "/api/v1/labour/payroll/pay", {"json": {"project_id": 1, "labour_id": 1, "month": 1, "year": 2026, "amount": 100}}),
            ("POST", "/api/v1/labour/advance", {"json": {"project_id": 1, "labour_id": 1, "amount": 100, "description": "Advance"}}),
            ("GET", f"/api/v1/labour/attendance/dashboard?project_id=1&from_date={today}&to_date={today}", {}),
            ("GET", "/api/v1/labour/dashboard/stats", {}),
            ("GET", "/api/v1/labour/contractor/1", {}),
            ("GET", "/api/v1/labour/summary/skill?project_id=1", {}),
            ("GET", "/api/v1/labour/report/export?project_id=1", {}),
            ("GET", f"/api/v1/labour/attendance/export?project_id=1&from_date={today}&to_date={today}", {}),
            ("GET", "/api/v1/labour/payroll/export", {}),
            ("POST", "/api/v1/labour/wages", {"json": {"project_id": 1, "labour_id": 1, "period_type": "DAILY", "start_date": str(today), "end_date": str(today), "payment_mode": "CASH"}}),
            ("GET", "/api/v1/labour/wages", {}),
            ("POST", "/api/v1/labour/wages/9999/pay", {}),
            ("GET", "/api/v1/labour/wages/stats?project_id=1", {}),
            ("GET", "/api/v1/labour/9999/qr", {}),
            ("GET", "/api/v1/labour/9999", {}),
            ("GET", "/api/v1/labour/payroll/weekly-velocity?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/disbursement-history?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/fiscal-summary?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/momentum?project_id=1", {}),
            ("GET", "/api/v1/labour/payroll/aggregate-report?project_id=1&month=1&year=2026", {}),
        ]
        assert len(endpoints) == 32, f"Expected 32 endpoints, got {len(endpoints)}"

        for method, path, kwargs in endpoints:
            res = await ac.request(method, path, **kwargs)
            assert res.status_code == 401, f"{method} {path} returned {res.status_code}, expected 401"


# ============================================================================
# 2. DYNAMIC RBAC: labour.view (GRANT / REVOKE)
# ============================================================================

@pytest.mark.asyncio
async def test_as_02_dynamic_rbac_labour_view():
    """Verify dynamic grant/revoke lifecycle for labour.view."""
    async with setup_batch_h_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            proj_id = d["proj_a"].id
            today = date.today()

            # 1. Initially 403 Forbidden
            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 403
            res = await ac.get(f"/api/v1/labour/{d['labour_a'].id}", headers=headers)
            assert res.status_code == 403
            res = await ac.get(f"/api/v1/labour/payroll?project_id={proj_id}&month={today.month}&year={today.year}", headers=headers)
            assert res.status_code == 403
            res = await ac.get(f"/api/v1/labour/wages?project_id={proj_id}", headers=headers)
            assert res.status_code == 403

            # 2. Grant labour.view in DB
            async with AsyncSessionLocal() as db:
                p = (await db.execute(select(Permission).where(Permission.code == "labour.view"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # 3. Dynamic access granted (200) without restart
            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 200
            res = await ac.get(f"/api/v1/labour/{d['labour_a'].id}", headers=headers)
            assert res.status_code == 200
            res = await ac.get(f"/api/v1/labour/payroll?project_id={proj_id}&month={today.month}&year={today.year}", headers=headers)
            assert res.status_code == 200
            res = await ac.get(f"/api/v1/labour/wages?project_id={proj_id}", headers=headers)
            assert res.status_code == 200

            # 4. Revoke labour.view in DB
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == p.id))
                await db.commit()

            # 5. Access immediately blocked (403)
            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 3. DYNAMIC RBAC: labour.create (GRANT / REVOKE)
# ============================================================================

@pytest.mark.asyncio
async def test_as_03_dynamic_rbac_labour_create():
    """Verify dynamic grant/revoke lifecycle for labour.create."""
    async with setup_batch_h_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            proj_id = d["proj_a"].id
            today = date.today()

            wage_payload = {
                "project_id": proj_id,
                "labour_id": d["labour_a"].id,
                "period_type": "DAILY",
                "start_date": str(today),
                "end_date": str(today),
                "payment_mode": "CASH",
            }

            # 1. Initially 403
            res = await ac.post("/api/v1/labour/wages", json=wage_payload, headers=headers)
            assert res.status_code == 403

            # 2. Grant labour.create
            async with AsyncSessionLocal() as db:
                p = (await db.execute(select(Permission).where(Permission.code == "labour.create"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # 3. Dynamic access permitted (passes 403 RBAC check)
            res = await ac.post("/api/v1/labour/wages", json=wage_payload, headers=headers)
            assert res.status_code != 403

            # 4. Revoke labour.create
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == p.id))
                await db.commit()

            # 5. Blocked with 403
            res = await ac.post("/api/v1/labour/wages", json=wage_payload, headers=headers)
            assert res.status_code == 403


# ============================================================================
# 4. DYNAMIC RBAC: labour.edit (GRANT / REVOKE)
# ============================================================================

@pytest.mark.asyncio
async def test_as_04_dynamic_rbac_labour_edit():
    """Verify dynamic grant/revoke lifecycle for labour.edit."""
    async with setup_batch_h_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            labour_id = d["labour_a"].id

            # 1. Initially 403
            res = await ac.put(f"/api/v1/labour/{labour_id}", data={"labour_name": "Updated Name"}, headers=headers)
            assert res.status_code == 403
            res = await ac.post("/api/v1/labour/payroll/lock", json={"payroll_ids": [d["payroll_a"].id]}, headers=headers)
            assert res.status_code == 403

            # 2. Grant labour.edit
            async with AsyncSessionLocal() as db:
                p = (await db.execute(select(Permission).where(Permission.code == "labour.edit"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # 3. Now allowed
            res = await ac.put(f"/api/v1/labour/{labour_id}", data={"labour_name": "Updated Name"}, headers=headers)
            assert res.status_code == 200

            # 4. Revoke labour.edit
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == p.id))
                await db.commit()

            # 5. Blocked
            res = await ac.put(f"/api/v1/labour/{labour_id}", data={"labour_name": "Updated Name 2"}, headers=headers)
            assert res.status_code == 403


# ============================================================================
# 5. DYNAMIC RBAC: labour.delete (GRANT / REVOKE)
# ============================================================================

@pytest.mark.asyncio
async def test_as_05_dynamic_rbac_labour_delete():
    """Verify dynamic grant/revoke lifecycle for labour.delete."""
    async with setup_batch_h_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            labour_id = d["labour_a"].id

            # 1. Initially 403
            res = await ac.delete(f"/api/v1/labour/{labour_id}", headers=headers)
            assert res.status_code == 403

            # 2. Grant labour.delete
            async with AsyncSessionLocal() as db:
                p = (await db.execute(select(Permission).where(Permission.code == "labour.delete"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # 3. Now passes 403 check (200 status code)
            res = await ac.delete(f"/api/v1/labour/{labour_id}", headers=headers)
            assert res.status_code == 200

            # 4. Revoke labour.delete
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == p.id))
                await db.commit()

            # 5. Blocked with 403
            res = await ac.delete(f"/api/v1/labour/{labour_id}", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 6. DYNAMIC RBAC: labour.approve (GRANT / REVOKE)
# ============================================================================

@pytest.mark.asyncio
async def test_as_06_dynamic_rbac_labour_approve():
    """Verify dynamic grant/revoke lifecycle for labour.approve."""
    async with setup_batch_h_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            wage_id = d["wage_a"].id

            # 1. Initially 403
            res = await ac.post(f"/api/v1/labour/wages/{wage_id}/pay", headers=headers)
            assert res.status_code == 403

            # 2. Grant labour.approve
            async with AsyncSessionLocal() as db:
                p = (await db.execute(select(Permission).where(Permission.code == "labour.approve"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # 3. Now allowed through RBAC (passes 403)
            res = await ac.post(f"/api/v1/labour/wages/{wage_id}/pay", headers=headers)
            assert res.status_code != 403

            # 4. Revoke labour.approve
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == p.id))
                await db.commit()

            # 5. Blocked
            res = await ac.post(f"/api/v1/labour/wages/{wage_id}/pay", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 7. DYNAMIC RBAC: labour.export (GRANT / REVOKE)
# ============================================================================

@pytest.mark.asyncio
async def test_as_07_dynamic_rbac_labour_export():
    """Verify dynamic grant/revoke lifecycle for labour.export."""
    async with setup_batch_h_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            proj_id = d["proj_a"].id
            today = date.today()

            # 1. Initially 403
            res = await ac.get(f"/api/v1/labour/report/export?project_id={proj_id}", headers=headers)
            assert res.status_code == 403
            res = await ac.get(f"/api/v1/labour/attendance/export?project_id={proj_id}&from_date={today}&to_date={today}", headers=headers)
            assert res.status_code == 403
            res = await ac.get("/api/v1/labour/payroll/export", headers=headers)
            assert res.status_code == 403

            # 2. Grant labour.export
            async with AsyncSessionLocal() as db:
                p = (await db.execute(select(Permission).where(Permission.code == "labour.export"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # 3. Now allowed
            res = await ac.get(f"/api/v1/labour/report/export?project_id={proj_id}", headers=headers)
            assert res.status_code == 200
            res = await ac.get(f"/api/v1/labour/attendance/export?project_id={proj_id}&from_date={today}&to_date={today}", headers=headers)
            assert res.status_code == 200
            res = await ac.get("/api/v1/labour/payroll/export", headers=headers)
            assert res.status_code == 200

            # 4. Revoke labour.export
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == p.id))
                await db.commit()

            # 5. Blocked
            res = await ac.get(f"/api/v1/labour/report/export?project_id={proj_id}", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 8. TENANTLESS NON-SA USERS REJECTED WITH HTTP 403
# ============================================================================

@pytest.mark.asyncio
async def test_as_08_tenantless_non_sa_rejected_403():
    """Non-SA user with company_id=None must be rejected with 403 'Company context required'."""
    uid = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        user_tenantless = User(
            email=f"tenantless_{uid}@test.com",
            hashed_password=get_password_hash("Secret123!"),
            full_name="Tenantless Non-SA",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        db.add(user_tenantless)
        await db.commit()
        await db.refresh(user_tenantless)
        tenantless_id = user_tenantless.id

    token = create_access_token({"sub": str(tenantless_id)})
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints_to_check = [
            ("GET", "/api/v1/labour"),
            ("GET", "/api/v1/labour/payroll"),
            ("GET", "/api/v1/labour/dashboard/stats"),
            ("GET", "/api/v1/labour/wages"),
        ]
        for method, url in endpoints_to_check:
            res = await ac.request(method, url, headers=headers)
            assert res.status_code == 403, f"{url} expected 403, got {res.status_code}"
            assert res.json().get("detail", "") in ("Company context required", "User does not belong to any company.")

    # Cleanup
    async with AsyncSessionLocal() as db:
        await db.execute(delete(User).where(User.id == tenantless_id))
        await db.commit()


# ============================================================================
# 9. SUPER ADMIN SEMANTICS: EMPTY LIST CONTRACT AND SPECIFIC RESOURCE LOOKUP
# ============================================================================

@pytest.mark.asyncio
async def test_as_09_super_admin_semantics():
    """Verify Super Admin empty-list semantics when company_id=None and global resource lookups."""
    async with setup_batch_h_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            sa_headers = {"Authorization": f"Bearer {d['tokens']['super_admin']}"}

            # 1. list_labour with company_id=None returns empty list
            res = await ac.get("/api/v1/labour", headers=sa_headers)
            assert res.status_code == 200
            data = res.json()
            assert data["items"] == []
            assert data["meta"]["total"] == 0

            # 2. SA can inspect specific labour across companies
            res_a = await ac.get(f"/api/v1/labour/{d['labour_a'].id}", headers=sa_headers)
            assert res_a.status_code == 200
            assert res_a.json()["id"] == d["labour_a"].id

            res_b = await ac.get(f"/api/v1/labour/{d['labour_b'].id}", headers=sa_headers)
            assert res_b.status_code == 200
            assert res_b.json()["id"] == d["labour_b"].id


# ============================================================================
# 10. CROSS-TENANT IDOR MASKED AS HTTP 404
# ============================================================================

@pytest.mark.asyncio
async def test_as_10_cross_tenant_idor_masked_404():
    """Verify cross-tenant accesses return HTTP 404 (masked, no information leakage)."""
    async with setup_batch_h_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
            labour_b_id = d["labour_b"].id
            contractor_b_id = d["contractor_b"].id
            wage_b_id = d["wage_b"].id

            # 1. Admin A cannot read Labour B
            res = await ac.get(f"/api/v1/labour/{labour_b_id}", headers=headers_a)
            assert res.status_code == 404

            # 2. Admin A cannot update Labour B
            res = await ac.put(f"/api/v1/labour/{labour_b_id}", data={"labour_name": "Hacked"}, headers=headers_a)
            assert res.status_code == 404

            # 3. Admin A cannot delete Labour B
            res = await ac.delete(f"/api/v1/labour/{labour_b_id}", headers=headers_a)
            assert res.status_code == 404

            # 4. Admin A cannot view Labour B's QR
            res = await ac.get(f"/api/v1/labour/{labour_b_id}/qr", headers=headers_a)
            assert res.status_code == 404

            # 5. Admin A cannot access Labour B's reports
            res = await ac.get(f"/api/v1/labour/{labour_b_id}/weekly-report", headers=headers_a)
            assert res.status_code == 404
            res = await ac.get(f"/api/v1/labour/{labour_b_id}/monthly-report", headers=headers_a)
            assert res.status_code == 404

            # 6. Admin A cannot view Contractor B's labour
            res = await ac.get(f"/api/v1/labour/contractor/{contractor_b_id}", headers=headers_a)
            assert res.status_code == 404

            # 7. Admin A cannot pay Wage B
            res = await ac.post(f"/api/v1/labour/wages/{wage_b_id}/pay", headers=headers_a)
            assert res.status_code == 404


# ============================================================================
# 11. CROSS-TENANT FK INJECTION PROTECTED
# ============================================================================

@pytest.mark.asyncio
async def test_as_11_cross_tenant_fk_injection_protected():
    """Verify creating Labour with foreign contractor or project is blocked."""
    async with setup_batch_h_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
            rand_aadhaar = f"9{uuid.uuid4().int % 100000000000:011d}"
            rand_mobile = f"9{uuid.uuid4().int % 1000000000:09d}"

            # Attempt to inject contractor_b into company_a labour
            res = await ac.post(
                f"/api/v1/labour?aadhaar_number={rand_aadhaar}&labour_name=FKTest&mobile_number={rand_mobile}&labour_type_id={d['labour_type'].id}&contractor_id={d['contractor_b'].id}&status=Active",
                headers=headers_a,
            )
            assert res.status_code in (400, 404), f"Expected 400/404 on cross-tenant contractor FK, got {res.status_code}: {res.text}"

            # Attempt to inject project_b into company_a labour
            res = await ac.post(
                f"/api/v1/labour?aadhaar_number={rand_aadhaar}&labour_name=FKTest&mobile_number={rand_mobile}&labour_type_id={d['labour_type'].id}&project_id={d['proj_b'].id}&status=Active",
                headers=headers_a,
            )
            assert res.status_code in (400, 404), f"Expected 400/404 on cross-tenant project FK, got {res.status_code}: {res.text}"


# ============================================================================
# 12. BUSINESS INVARIANTS: PAYROLL LOCK / UNLOCK AND ADVANCE
# ============================================================================

@pytest.mark.asyncio
async def test_as_12_payroll_lock_unlock_and_advance():
    """Verify payroll locking/unlocking lifecycle and advance payment invariants."""
    async with setup_batch_h_data() as d:
        from app.core.enums import PayrollStatus
        payroll_id = d["payroll_a"].id
        async with AsyncSessionLocal() as db:
            p = await db.get(LabourPayroll, payroll_id)
            p.status = PayrollStatus.DRAFT
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}

            # 1. Lock payroll (transitions DRAFT -> LOCKED)
            res_lock = await ac.post("/api/v1/labour/payroll/lock", json={"payroll_ids": [payroll_id]}, headers=headers_a)
            assert res_lock.status_code == 200
            assert res_lock.json()[0]["status"].upper() == "LOCKED"

            # 2. Unlock payroll (transitions LOCKED -> DRAFT)
            res_unlock = await ac.post("/api/v1/labour/payroll/unlock", json={"payroll_ids": [payroll_id]}, headers=headers_a)
            assert res_unlock.status_code == 200
            assert res_unlock.json()[0]["status"].upper() == "DRAFT"

            # 3. Advance payment business invariant: negative amount rejected
            adv_res = await ac.post(
                "/api/v1/labour/advance",
                json={
                    "project_id": d["proj_a"].id,
                    "labour_id": d["labour_a"].id,
                    "amount": -50.0,
                    "description": "Invalid Negative Advance",
                },
                headers=headers_a,
            )
            assert adv_res.status_code == 400
