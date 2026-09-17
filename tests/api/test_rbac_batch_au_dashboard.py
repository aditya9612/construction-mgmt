import uuid
from datetime import date, datetime, timezone
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

import app.db.base
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserAttendance
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.expense import Expense
from app.models.labour import Labour
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token


def _make_auth_header(user: User):
    token = create_access_token(
        data={"sub": str(user.id), "role": user.role, "company_id": user.company_id}
    )
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# 1. UNAUTHENTICATED REQUESTS (401) FOR ALL 21 DASHBOARD ENDPOINTS
# ============================================================================

@pytest.mark.asyncio
async def test_au_01_all_21_endpoints_unauthenticated_401():
    """Verify that all 21 dashboard endpoints strictly return 401 when unauthenticated."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("GET", "/api/v1/dashboard/admin", {}),
            ("GET", "/api/v1/dashboard/engineer", {}),
            ("GET", "/api/v1/dashboard/manager", {}),
            ("GET", "/api/v1/dashboard/accountant", {}),
            ("GET", "/api/v1/dashboard/pm-command-center", {}),
            ("GET", "/api/v1/dashboard/project-manager-summary", {}),
            ("POST", "/api/v1/dashboard/refresh", {}),
            ("GET", "/api/v1/dashboard/accountant/export", {}),
            ("GET", "/api/v1/dashboard/admin/projects/export/csv", {}),
            ("GET", "/api/v1/dashboard/admin/projects/export/pdf", {}),
            ("GET", "/api/v1/dashboard/client?project_id=1", {}),
            ("GET", "/api/v1/dashboard/graph/labour", {}),
            ("GET", "/api/v1/dashboard/graph/expense", {}),
            ("GET", "/api/v1/dashboard/graph/combined", {}),
            ("GET", "/api/v1/dashboard/graph/forecast", {}),
            ("GET", "/api/v1/dashboard/graph/advanced-forecast", {}),
            ("GET", "/api/v1/dashboard/graph/ml-forecast", {}),
            ("GET", "/api/v1/dashboard/engineer/1", {}),
            ("GET", "/api/v1/dashboard/labour", {}),
            ("GET", "/api/v1/dashboard/labour/payments", {}),
            ("GET", "/api/v1/dashboard/labour/payments/export", {}),
        ]
        assert len(endpoints) == 21, f"Expected exactly 21 endpoints, got {len(endpoints)}"

        for method, path, kwargs in endpoints:
            res = await ac.request(method, path, **kwargs)
            assert res.status_code == 401, f"{method} {path} returned {res.status_code}, expected 401"


# ============================================================================
# 2. DYNAMIC RBAC: GRANT / REVOKE FOR dashboard.* PERMISSIONS
# ============================================================================

@pytest.mark.asyncio
async def test_au_02_dynamic_rbac_dashboard_view_grant_revoke():
    """Verify dynamic grant and revocation of dashboard.view without app restart."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp = Company(name=f"AU-Comp-{uid}", subdomain=f"au{uid[:4]}")
        db.add(comp)
        await db.flush()

        role_name = f"AU_ViewRole_{uid}"
        role = Role(company_id=comp.id, name=role_name, display_name="AU View Role", is_system=False)
        user = User(
            email=f"au_view_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AU View User",
            company_id=comp.id,
            role=role_name,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role, user])
        await db.commit()

        headers = _make_auth_header(user)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Unprivileged user returns 403
            res_admin = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert res_admin.status_code == 403
            res_eng = await ac.get("/api/v1/dashboard/engineer", headers=headers)
            assert res_eng.status_code == 403

            # 2. Grant dashboard.view in DB
            p_view = await db.scalar(select(Permission).where(Permission.code == "dashboard.view"))
            rp = RolePermission(role=role_name, role_id=role.id, permission_id=p_view.id)
            db.add(rp)
            await db.commit()

            # 3. Request immediately succeeds (200) without restart
            res_admin_ok = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert res_admin_ok.status_code == 200
            res_eng_ok = await ac.get("/api/v1/dashboard/engineer", headers=headers)
            assert res_eng_ok.status_code == 200

            # 4. Revoke dashboard.view
            await db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
            await db.commit()

            # 5. Immediately 403 again
            res_revoked = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert res_revoked.status_code == 403

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        await db.execute(delete(Role).where(Role.id == role.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.execute(delete(Company).where(Company.id == comp.id))
        await db.commit()


@pytest.mark.asyncio
async def test_au_03_dynamic_rbac_dashboard_export_grant_revoke():
    """Verify dynamic grant/revoke lifecycle for dashboard.export endpoints."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp = Company(name=f"AU-ExpComp-{uid}", subdomain=f"auexp{uid[:4]}")
        db.add(comp)
        await db.flush()

        role_name = f"AU_ExpRole_{uid}"
        role = Role(company_id=comp.id, name=role_name, display_name="AU Export Role", is_system=False)
        user = User(
            email=f"au_exp_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AU Exp User",
            company_id=comp.id,
            role=role_name,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role, user])
        await db.commit()

        headers = _make_auth_header(user)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. 403 without dashboard.export
            res_csv = await ac.get("/api/v1/dashboard/admin/projects/export/csv", headers=headers)
            assert res_csv.status_code == 403
            res_pdf = await ac.get("/api/v1/dashboard/admin/projects/export/pdf", headers=headers)
            assert res_pdf.status_code == 403
            res_acc = await ac.get("/api/v1/dashboard/accountant/export", headers=headers)
            assert res_acc.status_code == 403

            # 2. Grant dashboard.export
            p_export = await db.scalar(select(Permission).where(Permission.code == "dashboard.export"))
            rp = RolePermission(role=role_name, role_id=role.id, permission_id=p_export.id)
            db.add(rp)
            await db.commit()

            # 3. 200 after grant
            res_csv_ok = await ac.get("/api/v1/dashboard/admin/projects/export/csv", headers=headers)
            assert res_csv_ok.status_code == 200
            res_pdf_ok = await ac.get("/api/v1/dashboard/admin/projects/export/pdf", headers=headers)
            assert res_pdf_ok.status_code == 200

            # 4. Revoke
            await db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
            await db.commit()

            res_csv_rev = await ac.get("/api/v1/dashboard/admin/projects/export/csv", headers=headers)
            assert res_csv_rev.status_code == 403

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        await db.execute(delete(Role).where(Role.id == role.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.execute(delete(Company).where(Company.id == comp.id))
        await db.commit()


@pytest.mark.asyncio
async def test_au_04_dynamic_rbac_dashboard_manage_refresh():
    """Verify POST /refresh strictly requires dashboard.manage."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp = Company(name=f"AU-MgComp-{uid}", subdomain=f"aumg{uid[:4]}")
        db.add(comp)
        await db.flush()

        role_name = f"AU_MgRole_{uid}"
        role = Role(company_id=comp.id, name=role_name, display_name="AU Mgmt Role", is_system=False)
        user = User(
            email=f"au_mg_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AU Mg User",
            company_id=comp.id,
            role=role_name,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role, user])
        await db.commit()

        headers = _make_auth_header(user)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. 403 initially
            res_init = await ac.post("/api/v1/dashboard/refresh", headers=headers)
            assert res_init.status_code == 403

            # 2. Grant dashboard.manage
            p_manage = await db.scalar(select(Permission).where(Permission.code == "dashboard.manage"))
            rp = RolePermission(role=role_name, role_id=role.id, permission_id=p_manage.id)
            db.add(rp)
            await db.commit()

            # 3. 200 after grant
            res_ok = await ac.post("/api/v1/dashboard/refresh", headers=headers)
            assert res_ok.status_code == 200
            assert "successfully" in res_ok.json().get("message", "")

            # 4. Revoke
            await db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
            await db.commit()

            res_rev = await ac.post("/api/v1/dashboard/refresh", headers=headers)
            assert res_rev.status_code == 403

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        await db.execute(delete(Role).where(Role.id == role.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.execute(delete(Company).where(Company.id == comp.id))
        await db.commit()


# ============================================================================
# 3. PREVIOUSLY UNPROTECTED ENDPOINTS NOW ENFORCE RBAC
# ============================================================================

@pytest.mark.asyncio
async def test_au_05_three_previously_unprotected_endpoints_enforce_rbac():
    """Verify /labour, /labour/payments, and /labour/payments/export require canonical RBAC."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp = Company(name=f"AU-UnpComp-{uid}", subdomain=f"auunp{uid[:4]}")
        db.add(comp)
        await db.flush()

        role_name = f"AU_UnpRole_{uid}"
        role = Role(company_id=comp.id, name=role_name, display_name="AU Unp Role", is_system=False)
        user = User(
            email=f"au_unp_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AU Unp User",
            company_id=comp.id,
            role=role_name,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role, user])
        await db.commit()

        headers = _make_auth_header(user)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Without permissions, all three return 403
            res_labour = await ac.get("/api/v1/dashboard/labour", headers=headers)
            assert res_labour.status_code == 403, f"Expected 403, got {res_labour.status_code}"

            res_payments = await ac.get("/api/v1/dashboard/labour/payments", headers=headers)
            assert res_payments.status_code == 403, f"Expected 403, got {res_payments.status_code}"

            res_export = await ac.get("/api/v1/dashboard/labour/payments/export", headers=headers)
            assert res_export.status_code == 403, f"Expected 403, got {res_export.status_code}"

            # 2. Grant dashboard.view: /labour and /labour/payments pass RBAC (returns 404 since no Labour profile exists)
            p_view = await db.scalar(select(Permission).where(Permission.code == "dashboard.view"))
            p_export = await db.scalar(select(Permission).where(Permission.code == "dashboard.export"))
            db.add_all([
                RolePermission(role=role_name, role_id=role.id, permission_id=p_view.id),
                RolePermission(role=role_name, role_id=role.id, permission_id=p_export.id),
            ])
            await db.commit()

            # Now RBAC allows access; business logic executes (404 for missing labour profile, not 403)
            res_labour_ok = await ac.get("/api/v1/dashboard/labour", headers=headers)
            assert res_labour_ok.status_code in [200, 404], f"Expected 200 or 404, got {res_labour_ok.status_code}"

            res_payments_ok = await ac.get("/api/v1/dashboard/labour/payments", headers=headers)
            assert res_payments_ok.status_code in [200, 404], f"Expected 200 or 404, got {res_payments_ok.status_code}"

            res_export_ok = await ac.get("/api/v1/dashboard/labour/payments/export", headers=headers)
            assert res_export_ok.status_code in [200, 404], f"Expected 200 or 404, got {res_export_ok.status_code}"

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        await db.execute(delete(Role).where(Role.id == role.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.execute(delete(Company).where(Company.id == comp.id))
        await db.commit()


# ============================================================================
# 4. TENANTLESS NON-SA REQUESTS REJECTED WITH 403
# ============================================================================

@pytest.mark.asyncio
async def test_au_06_tenantless_non_sa_rejected_403():
    """Verify authenticated non-SA with company_id=None receives HTTP 403 'Company context required'."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        # User has role Admin (so permissions catalog grants all), but company_id is None and not SA
        user_tenantless = User(
            email=f"au_tenantless_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Tenantless User",
            company_id=None,
            role="Admin",
            is_active=True,
            is_super_admin=False,
        )
        db.add(user_tenantless)
        await db.commit()

        headers = _make_auth_header(user_tenantless)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("GET", "/api/v1/dashboard/admin"),
                ("GET", "/api/v1/dashboard/engineer"),
                ("GET", "/api/v1/dashboard/manager"),
                ("GET", "/api/v1/dashboard/accountant"),
                ("GET", "/api/v1/dashboard/pm-command-center"),
                ("GET", "/api/v1/dashboard/project-manager-summary"),
                ("POST", "/api/v1/dashboard/refresh"),
                ("GET", "/api/v1/dashboard/accountant/export"),
                ("GET", "/api/v1/dashboard/admin/projects/export/csv"),
                ("GET", "/api/v1/dashboard/admin/projects/export/pdf"),
                ("GET", "/api/v1/dashboard/client?project_id=1"),
                ("GET", "/api/v1/dashboard/graph/labour"),
                ("GET", "/api/v1/dashboard/graph/expense"),
                ("GET", "/api/v1/dashboard/graph/combined"),
                ("GET", "/api/v1/dashboard/graph/forecast"),
                ("GET", "/api/v1/dashboard/graph/advanced-forecast"),
                ("GET", "/api/v1/dashboard/graph/ml-forecast"),
                ("GET", "/api/v1/dashboard/engineer/1"),
                ("GET", "/api/v1/dashboard/labour"),
                ("GET", "/api/v1/dashboard/labour/payments"),
                ("GET", "/api/v1/dashboard/labour/payments/export"),
            ]

            for method, path in endpoints:
                res = await ac.request(method, path, headers=headers)
                assert res.status_code == 403, f"{method} {path} returned {res.status_code}, expected 403"
                assert "Company context required" in res.text or "User does not belong to any company" in res.text

        # Cleanup
        await db.execute(delete(User).where(User.id == user_tenantless.id))
        await db.commit()


# ============================================================================
# 5. CROSS-TENANT PROJECT ISOLATION AND 404 MASKING
# ============================================================================

@pytest.mark.asyncio
async def test_au_07_cross_tenant_project_isolation_and_404():
    """Verify accessing a project belonging to another tenant returns 404."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp_a = Company(name=f"AU-CA-{uid}", subdomain=f"auca{uid[:4]}")
        comp_b = Company(name=f"AU-CB-{uid}", subdomain=f"aucb{uid[:4]}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        owner_a = Owner(owner_code=f"OA-{uid}", owner_name="Owner A", mobile=f"91{uuid.uuid4().int % 100000000:08d}", email=f"oa_{uid}@test.com", company_id=comp_a.id)
        owner_b = Owner(owner_code=f"OB-{uid}", owner_name="Owner B", mobile=f"92{uuid.uuid4().int % 100000000:08d}", email=f"ob_{uid}@test.com", company_id=comp_b.id)
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(business_id=f"PA-{uid}", project_name=f"ProjA-{uid}", company_id=comp_a.id, owner_id=owner_a.id, status="Ongoing", budget_amount=100000.0)
        proj_b = Project(business_id=f"PB-{uid}", project_name=f"ProjB-{uid}", company_id=comp_b.id, owner_id=owner_b.id, status="Ongoing", budget_amount=200000.0)
        db.add_all([proj_a, proj_b])
        await db.flush()

        # User in Comp A with dashboard.view
        role_a = Role(company_id=comp_a.id, name=f"RoleA_{uid}", display_name="Role A", is_system=False)
        user_a = User(
            email=f"user_a_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="User A",
            company_id=comp_a.id,
            role=f"RoleA_{uid}",
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role_a, user_a])
        await db.flush()

        # User A is member of Proj A only
        pm_a = ProjectMember(project_id=proj_a.id, user_id=user_a.id)
        db.add(pm_a)

        # Grant dashboard.view to Role A
        p_view = await db.scalar(select(Permission).where(Permission.code == "dashboard.view"))
        db.add(RolePermission(role=role_a.name, role_id=role_a.id, permission_id=p_view.id))
        await db.commit()

        headers_a = _make_auth_header(user_a)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Own project -> 200
            res_own_adv = await ac.get(f"/api/v1/dashboard/graph/advanced-forecast?project_id={proj_a.id}", headers=headers_a)
            assert res_own_adv.status_code == 200

            res_own_client = await ac.get(f"/api/v1/dashboard/client?project_id={proj_a.id}", headers=headers_a)
            assert res_own_client.status_code == 200

            res_own_eng = await ac.get(f"/api/v1/dashboard/engineer/{proj_a.id}", headers=headers_a)
            assert res_own_eng.status_code == 200

            # 2. Foreign project (Comp B) -> 404
            res_cross_adv = await ac.get(f"/api/v1/dashboard/graph/advanced-forecast?project_id={proj_b.id}", headers=headers_a)
            assert res_cross_adv.status_code == 404

            res_cross_ml = await ac.get(f"/api/v1/dashboard/graph/ml-forecast?project_id={proj_b.id}", headers=headers_a)
            assert res_cross_ml.status_code == 404

            res_cross_client = await ac.get(f"/api/v1/dashboard/client?project_id={proj_b.id}", headers=headers_a)
            assert res_cross_client.status_code == 404

            res_cross_eng = await ac.get(f"/api/v1/dashboard/engineer/{proj_b.id}", headers=headers_a)
            assert res_cross_eng.status_code == 404

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == role_a.name))
        await db.execute(delete(Role).where(Role.id == role_a.id))
        await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
        await db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
        await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
        await db.execute(delete(User).where(User.id == user_a.id))
        await db.execute(delete(Company).where(Company.id == comp_a.id))
        await db.execute(delete(Company).where(Company.id == comp_b.id))
        await db.commit()


# ============================================================================
# 6. SUPER ADMIN GLOBAL ACCESS
# ============================================================================

@pytest.mark.asyncio
async def test_au_08_superadmin_global_access():
    """Verify Super Admin retains canonical global access across all dashboard endpoints."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        user_sa = User(
            email=f"au_sa_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AU Super Admin",
            company_id=None,
            role="Admin",
            is_active=True,
            is_super_admin=True,
        )
        db.add(user_sa)
        await db.commit()

        headers_sa = _make_auth_header(user_sa)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Admin dashboard accessible to SA
            res_admin = await ac.get("/api/v1/dashboard/admin", headers=headers_sa)
            assert res_admin.status_code == 200

            # 2. Accountant dashboard accessible to SA
            res_acc = await ac.get("/api/v1/dashboard/accountant", headers=headers_sa)
            assert res_acc.status_code == 200

            # 3. CSV/PDF exports accessible to SA
            res_csv = await ac.get("/api/v1/dashboard/admin/projects/export/csv", headers=headers_sa)
            assert res_csv.status_code == 200

            res_pdf = await ac.get("/api/v1/dashboard/admin/projects/export/pdf", headers=headers_sa)
            assert res_pdf.status_code == 200

            # 4. PM dashboards accessible to SA
            res_pm = await ac.get("/api/v1/dashboard/pm-command-center", headers=headers_sa)
            assert res_pm.status_code == 200

            res_pms = await ac.get("/api/v1/dashboard/project-manager-summary", headers=headers_sa)
            assert res_pms.status_code == 200

        # Cleanup
        await db.execute(delete(User).where(User.id == user_sa.id))
        await db.commit()


# ============================================================================
# 7. BUSINESS AGGREGATION AND CONTRACT INVARIANTS
# ============================================================================

@pytest.mark.asyncio
async def test_au_09_business_aggregation_invariants():
    """Verify existing calculations, KPIs, and response models remain intact."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp = Company(name=f"AU-BizComp-{uid}", subdomain=f"aubiz{uid[:4]}")
        db.add(comp)
        await db.flush()

        role = Role(company_id=comp.id, name=f"AU_BizRole_{uid}", display_name="Biz Role", is_system=False)
        user = User(
            email=f"au_biz_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AU Biz User",
            company_id=comp.id,
            role=f"AU_BizRole_{uid}",
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role, user])
        await db.flush()

        p_view = await db.scalar(select(Permission).where(Permission.code == "dashboard.view"))
        db.add(RolePermission(role=role.name, role_id=role.id, permission_id=p_view.id))
        await db.commit()

        headers = _make_auth_header(user)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Engineer dashboard
            res_eng = await ac.get("/api/v1/dashboard/engineer", headers=headers)
            assert res_eng.status_code == 200
            data_eng = res_eng.json()
            assert data_eng["role"] == "engineer"
            assert "labour_today" in data_eng
            assert "progress" in data_eng

            # Manager dashboard
            res_mgr = await ac.get("/api/v1/dashboard/manager", headers=headers)
            assert res_mgr.status_code == 200
            data_mgr = res_mgr.json()
            assert data_mgr["role"] == "manager"
            assert "budget" in data_mgr
            assert "spent" in data_mgr
            assert "budget_utilization" in data_mgr

            # PM Summary
            res_pms = await ac.get("/api/v1/dashboard/project-manager-summary", headers=headers)
            assert res_pms.status_code == 200
            data_pms = res_pms.json()
            assert "total_projects" in data_pms
            assert "active_projects" in data_pms

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == role.name))
        await db.execute(delete(Role).where(Role.id == role.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.execute(delete(Company).where(Company.id == comp.id))
        await db.commit()
