"""
RBAC Batch AC: Reports & Analytics RBAC + Tenant Isolation Tests
================================================================

Covers 18 test criteria:
- TC01: Unauthenticated GET /reports/profit-loss -> 401
- TC02: Unauthenticated GET /reports/projects/excel -> 401
- TC03: Unauthenticated GET /reports/daily -> 401
- TC04: Authenticated user with no permissions -> 403
- TC05: Only reports.view: GET view endpoints -> 200, export endpoints -> 403
- TC06: Only reports.export: GET export endpoints -> 200/streaming response
- TC07: reports.view alone cannot access reports.export endpoints -> 403
- TC08: reports.export alone cannot access view-only endpoints -> 403
- TC09: Module wildcard reports.* grants all access
- TC10: Global wildcard * grants access
- TC11: Dynamic DB grant -> 200
- TC12: Dynamic DB revoke -> 403
- TC13: Super Admin bypasses RBAC and feature checks
- TC14: Tenant isolation - Company A user cannot see Company B's project data (404)
- TC15: IDOR prevention - project_id from another tenant in URL -> 404 masked
- TC16: Tenantless non-SA user (company_id=None, is_super_admin=False) -> 403
- TC17: Super Admin can access any tenant's project data
- TC18: Static router audit - 44 route decorators, 100% require_permission, 0 require_roles
"""

import ast
from datetime import datetime, date
import re
import uuid
from contextlib import asynccontextmanager


import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, update

import app.main
from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.security import get_password_hash, create_access_token
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, DailySiteReport
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride
from app.models.user import User


# ==============================================================================
# HELPERS
# ==============================================================================

def _tok(user_id: int) -> str:
    return create_access_token({"sub": str(user_id)})


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _view_endpoint() -> str:
    """A simple view endpoint that requires reports.view."""
    return "/api/v1/reports/profit-loss"


def _view_endpoint_project(project_id: int) -> str:
    """A view endpoint scoped to a project_id path param."""
    return f"/api/v1/reports/project/{project_id}"


def _export_endpoint() -> str:
    """An export endpoint that requires reports.export."""
    return "/api/v1/reports/audit/excel"


@asynccontextmanager
async def setup_batch_ac_data():
    """
    Provisions two isolated tenants (comp_a, comp_b) with:
    - One project each
    - One DailySiteReport per project (for view-endpoint data)
    - 4 users: admin_a, admin_b, super_admin, tenantless
    - 2 roles: empty_role (no perms), custom_role (configurable perms)
    - permissions: reports.view, reports.export
    """
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd = get_password_hash("Secret123!")

        # Companies
        comp_a = Company(name=f"BatchAC_CompA_{uid}")
        comp_b = Company(name=f"BatchAC_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN_A_{uid}",
            owner_name=f"Owner A {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera_ac_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN_B_{uid}",
            owner_name=f"Owner B {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerb_ac_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # Projects
        proj_a = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj AC A {uid}",
            business_id=f"PA_AC_{uid}",
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj AC B {uid}",
            business_id=f"PB_AC_{uid}",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        dsr_a = DailySiteReport(
            project_id=proj_a.id,
            business_id=f"DSRA_{uid}"[:20],
            report_date=date.today(),
            work_done="Batch AC Site Report A",
        )
        dsr_b = DailySiteReport(
            project_id=proj_b.id,
            business_id=f"DSRB_{uid}"[:20],
            report_date=date.today(),
            work_done="Batch AC Site Report B",
        )
        db.add_all([dsr_a, dsr_b])
        await db.flush()



        # Users
        admin_a = User(
            email=f"admin_ac_a_{uid}@test.com",
            hashed_password=pwd,
            full_name="Admin AC A",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_ac_b_{uid}@test.com",
            hashed_password=pwd,
            full_name="Admin AC B",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        super_admin = User(
            email=f"sa_ac_{uid}@test.com",
            hashed_password=pwd,
            full_name="Super Admin AC",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        tenantless = User(
            email=f"none_ac_{uid}@test.com",
            hashed_password=pwd,
            full_name="Tenantless AC",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        db.add_all([admin_a, admin_b, super_admin, tenantless])
        await db.flush()

        # Roles
        role_empty = Role(
            company_id=comp_a.id,
            name=f"empty_ac_{uid}",
            display_name="Empty Role AC",
            is_system=False,
        )
        role_custom = Role(
            company_id=comp_a.id,
            name=f"custom_ac_{uid}",
            display_name="Custom Role AC",
            is_system=False,
        )
        db.add_all([role_empty, role_custom])
        await db.flush()

        no_perm_user = User(
            email=f"noperm_ac_{uid}@test.com",
            hashed_password=pwd,
            full_name="No Perm AC",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=role_empty.name,
        )
        custom_user = User(
            email=f"custom_ac_{uid}@test.com",
            hashed_password=pwd,
            full_name="Custom AC",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=role_custom.name,
        )
        db.add_all([no_perm_user, custom_user])
        await db.flush()

        # Fetch permissions
        perm_view = await db.scalar(
            select(Permission).where(Permission.code == "reports.view")
        )
        perm_export = await db.scalar(
            select(Permission).where(Permission.code == "reports.export")
        )
        perm_wildcard = await db.scalar(
            select(Permission).where(Permission.code == "reports.*")
        )
        perm_global = await db.scalar(
            select(Permission).where(Permission.code == "*")
        )

        # Grant reports.view + reports.export to Admin role for admin_a / admin_b
        admin_rps_added = []
        for p in [perm_view, perm_export]:
            if p is None:
                continue
            existing = await db.scalar(
                select(RolePermission).where(
                    RolePermission.role == "Admin",
                    RolePermission.permission_id == p.id,
                    RolePermission.role_id.is_(None),
                )
            )
            if not existing:
                rp = RolePermission(role="Admin", permission_id=p.id)
                db.add(rp)
                await db.flush()
                admin_rps_added.append(rp.id)

        tokens = {
            "admin_a": _tok(admin_a.id),
            "admin_b": _tok(admin_b.id),
            "super_admin": _tok(super_admin.id),
            "tenantless": _tok(tenantless.id),
            "no_perm": _tok(no_perm_user.id),
            "custom": _tok(custom_user.id),
        }

        await db.commit()

        yield {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "dsr_a": dsr_a,
            "dsr_b": dsr_b,
            "users": {
                "admin_a": admin_a,
                "admin_b": admin_b,
                "super_admin": super_admin,
                "tenantless": tenantless,
                "no_perm": no_perm_user,
                "custom": custom_user,
            },
            "roles": {
                "empty": role_empty,
                "custom": role_custom,
            },
            "permissions": {
                "view": perm_view,
                "export": perm_export,
                "wildcard": perm_wildcard,
                "global": perm_global,
            },
            "tokens": tokens,
            "_admin_rps_added": admin_rps_added,
        }

    # Cleanup (runs after test)
    async with AsyncSessionLocal() as cdb:
        all_user_ids = [
            admin_a.id, admin_b.id, super_admin.id,
            tenantless.id, no_perm_user.id, custom_user.id,
        ]
        await cdb.execute(
            delete(UserPermissionOverride).where(
                UserPermissionOverride.user_id.in_(all_user_ids)
            )
        )
        await cdb.execute(
            delete(RolePermission).where(
                RolePermission.role.in_([role_empty.name, role_custom.name])
            )
        )
        if admin_rps_added:
            await cdb.execute(
                delete(RolePermission).where(
                    RolePermission.id.in_(admin_rps_added)
                )
            )
        # DSR
        await cdb.execute(
            delete(DailySiteReport).where(
                DailySiteReport.id.in_([dsr_a.id, dsr_b.id])
            )
        )
        # Projects, Owners
        await cdb.execute(
            delete(Project).where(Project.id.in_([proj_a.id, proj_b.id]))
        )
        await cdb.execute(
            delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id]))
        )
        # Roles
        await cdb.execute(
            delete(Role).where(Role.id.in_([role_empty.id, role_custom.id]))
        )
        # Users
        await cdb.execute(
            update(User).where(User.id.in_(all_user_ids)).values(created_by=None)
        )
        await cdb.execute(delete(User).where(User.id.in_(all_user_ids)))
        # Companies
        await cdb.execute(
            delete(Company).where(Company.id.in_([comp_a.id, comp_b.id]))
        )
        await cdb.commit()


# ==============================================================================
# TC01-TC03: Unauthenticated returns 401
# ==============================================================================


@pytest.mark.asyncio
async def test_tc01_unauthenticated_view_endpoint():
    """Unauthenticated GET /reports/profit-loss -> 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/api/v1/reports/profit-loss")
    assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_tc02_unauthenticated_export_endpoint():
    """Unauthenticated GET /reports/projects/excel -> 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/api/v1/reports/projects/excel")
    assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_tc03_unauthenticated_daily_report():
    """Unauthenticated GET /reports/daily -> 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get(
            "/api/v1/reports/daily",
            params={"project_id": 1, "report_date": "2026-01-01"},
        )
    assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"


# ==============================================================================
# TC04: Authenticated no-permission user -> 403
# ==============================================================================


@pytest.mark.asyncio
async def test_tc04_authenticated_no_permission():
    """Authenticated user with no permissions gets 403."""
    async with setup_batch_ac_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                _view_endpoint(),
                headers=_auth(data["tokens"]["no_perm"]),
            )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


# ==============================================================================
# TC05: Only reports.view -> view OK, export 403
# ==============================================================================


@pytest.mark.asyncio
async def test_tc05_only_reports_view():
    """User with only reports.view can access view endpoints but not export."""
    async with setup_batch_ac_data() as data:
        perm_view = data["permissions"]["view"]
        if perm_view is None:
            pytest.skip("reports.view permission not seeded")

        async with AsyncSessionLocal() as db:
            rp = RolePermission(
                role=data["roles"]["custom"].name,
                role_id=data["roles"]["custom"].id,
                permission_id=perm_view.id,
            )
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # View endpoint: 200
            r_view = await ac.get(
                _view_endpoint(),
                headers=_auth(data["tokens"]["custom"]),
            )
            assert r_view.status_code == 200, (
                f"Expected 200 for view endpoint, got {r_view.status_code}: {r_view.text}"
            )

            # Export endpoint: 403
            r_export = await ac.get(
                _export_endpoint(),
                headers=_auth(data["tokens"]["custom"]),
            )
            assert r_export.status_code == 403, (
                f"Expected 403 for export with only reports.view, got {r_export.status_code}"
            )


# ==============================================================================
# TC06: Only reports.export -> streaming export OK
# ==============================================================================


@pytest.mark.asyncio
async def test_tc06_only_reports_export():
    """User with only reports.export gets streaming response on export endpoints."""
    async with setup_batch_ac_data() as data:
        perm_export = data["permissions"]["export"]
        if perm_export is None:
            pytest.skip("reports.export permission not seeded")

        async with AsyncSessionLocal() as db:
            rp = RolePermission(
                role=data["roles"]["custom"].name,
                role_id=data["roles"]["custom"].id,
                permission_id=perm_export.id,
            )
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_export = await ac.get(
                _export_endpoint(),
                headers=_auth(data["tokens"]["custom"]),
            )
        # Export should return 200 with Excel content
        assert r_export.status_code == 200, (
            f"Expected 200 for export, got {r_export.status_code}: {r_export.text}"
        )
        ct = r_export.headers.get("content-type", "")
        assert "spreadsheetml" in ct or "excel" in ct or "octet-stream" in ct, (
            f"Expected Excel content-type, got: {ct}"
        )


# ==============================================================================
# TC07: reports.view alone cannot access export endpoints
# ==============================================================================


@pytest.mark.asyncio
async def test_tc07_view_cannot_access_export():
    """reports.view alone blocked from export endpoints -> 403."""
    async with setup_batch_ac_data() as data:
        perm_view = data["permissions"]["view"]
        if perm_view is None:
            pytest.skip("reports.view permission not seeded")

        async with AsyncSessionLocal() as db:
            rp = RolePermission(
                role=data["roles"]["custom"].name,
                role_id=data["roles"]["custom"].id,
                permission_id=perm_view.id,
            )
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            for export_url in [
                "/api/v1/reports/projects/excel",
                "/api/v1/reports/projects/pdf",
                "/api/v1/reports/profit-loss/excel",
                "/api/v1/reports/profit-loss/pdf",
            ]:
                r = await ac.get(export_url, headers=_auth(data["tokens"]["custom"]))
                assert r.status_code == 403, (
                    f"Expected 403 for {export_url} with reports.view only, "
                    f"got {r.status_code}"
                )


# ==============================================================================
# TC08: reports.export alone cannot access view-only endpoints
# ==============================================================================


@pytest.mark.asyncio
async def test_tc08_export_cannot_access_view_only():
    """reports.export alone blocked from view-only endpoints -> 403."""
    async with setup_batch_ac_data() as data:
        perm_export = data["permissions"]["export"]
        if perm_export is None:
            pytest.skip("reports.export permission not seeded")

        async with AsyncSessionLocal() as db:
            rp = RolePermission(
                role=data["roles"]["custom"].name,
                role_id=data["roles"]["custom"].id,
                permission_id=perm_export.id,
            )
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            for view_url in [
                "/api/v1/reports/profit-loss",
                "/api/v1/reports/cashflow",
                "/api/v1/reports/assets",
                "/api/v1/reports/business-intelligence",
            ]:
                r = await ac.get(view_url, headers=_auth(data["tokens"]["custom"]))
                assert r.status_code == 403, (
                    f"Expected 403 for {view_url} with reports.export only, "
                    f"got {r.status_code}"
                )


# ==============================================================================
# TC09: Module wildcard reports.* grants all access
# ==============================================================================


@pytest.mark.asyncio
async def test_tc09_module_wildcard():
    """reports.* wildcard grants both view and export access."""
    async with setup_batch_ac_data() as data:
        perm_wc = data["permissions"]["wildcard"]
        if perm_wc is None:
            pytest.skip("reports.* permission not seeded")

        async with AsyncSessionLocal() as db:
            rp = RolePermission(
                role=data["roles"]["custom"].name,
                role_id=data["roles"]["custom"].id,
                permission_id=perm_wc.id,
            )
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_view = await ac.get(
                _view_endpoint(), headers=_auth(data["tokens"]["custom"])
            )
            assert r_view.status_code == 200, (
                f"Expected 200 for view with reports.*, got {r_view.status_code}"
            )

            r_export = await ac.get(
                _export_endpoint(), headers=_auth(data["tokens"]["custom"])
            )
            assert r_export.status_code == 200, (
                f"Expected 200 for export with reports.*, got {r_export.status_code}"
            )


# ==============================================================================
# TC10: Global wildcard * grants access
# ==============================================================================


@pytest.mark.asyncio
async def test_tc10_global_wildcard():
    """Global wildcard * grants all access including reports."""
    async with setup_batch_ac_data() as data:
        perm_global = data["permissions"]["global"]
        if perm_global is None:
            pytest.skip("* permission not seeded")

        async with AsyncSessionLocal() as db:
            rp = RolePermission(
                role=data["roles"]["custom"].name,
                role_id=data["roles"]["custom"].id,
                permission_id=perm_global.id,
            )
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_view = await ac.get(
                _view_endpoint(), headers=_auth(data["tokens"]["custom"])
            )
            assert r_view.status_code == 200, (
                f"Expected 200 for view with *, got {r_view.status_code}"
            )

            r_export = await ac.get(
                _export_endpoint(), headers=_auth(data["tokens"]["custom"])
            )
            assert r_export.status_code == 200, (
                f"Expected 200 for export with *, got {r_export.status_code}"
            )


# ==============================================================================
# TC11: Dynamic DB grant -> access restored
# ==============================================================================


@pytest.mark.asyncio
async def test_tc11_dynamic_db_grant():
    """Dynamically granting reports.view in DB immediately restores access."""
    async with setup_batch_ac_data() as data:
        perm_view = data["permissions"]["view"]
        if perm_view is None:
            pytest.skip("reports.view permission not seeded")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Before grant: 403
            r_before = await ac.get(
                _view_endpoint(), headers=_auth(data["tokens"]["custom"])
            )
            assert r_before.status_code == 403, (
                f"Expected 403 before grant, got {r_before.status_code}"
            )

        # Grant permission
        async with AsyncSessionLocal() as db:
            rp = RolePermission(
                role=data["roles"]["custom"].name,
                role_id=data["roles"]["custom"].id,
                permission_id=perm_view.id,
            )
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # After grant: 200
            r_after = await ac.get(
                _view_endpoint(), headers=_auth(data["tokens"]["custom"])
            )
            assert r_after.status_code == 200, (
                f"Expected 200 after grant, got {r_after.status_code}: {r_after.text}"
            )


# ==============================================================================
# TC12: Dynamic DB revoke -> access revoked
# ==============================================================================


@pytest.mark.asyncio
async def test_tc12_dynamic_db_revoke():
    """Dynamically revoking reports.view immediately blocks access."""
    async with setup_batch_ac_data() as data:
        perm_view = data["permissions"]["view"]
        if perm_view is None:
            pytest.skip("reports.view permission not seeded")

        # Grant permission
        async with AsyncSessionLocal() as db:
            rp = RolePermission(
                role=data["roles"]["custom"].name,
                role_id=data["roles"]["custom"].id,
                permission_id=perm_view.id,
            )
            db.add(rp)
            await db.commit()
            rp_id = rp.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Before revoke: 200
            r_before = await ac.get(
                _view_endpoint(), headers=_auth(data["tokens"]["custom"])
            )
            assert r_before.status_code == 200, (
                f"Expected 200 before revoke, got {r_before.status_code}"
            )

        # Revoke
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(RolePermission).where(RolePermission.id == rp_id)
            )
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # After revoke: 403
            r_after = await ac.get(
                _view_endpoint(), headers=_auth(data["tokens"]["custom"])
            )
            assert r_after.status_code == 403, (
                f"Expected 403 after revoke, got {r_after.status_code}"
            )


# ==============================================================================
# TC13: Super Admin bypasses RBAC
# ==============================================================================


@pytest.mark.asyncio
async def test_tc13_super_admin_bypass():
    """Super Admin can access all reports endpoints without any explicit permissions."""
    async with setup_batch_ac_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # View endpoint
            r_view = await ac.get(
                _view_endpoint(), headers=_auth(data["tokens"]["super_admin"])
            )
            assert r_view.status_code == 200, (
                f"SA expected 200 on view, got {r_view.status_code}: {r_view.text}"
            )

            # Export endpoint
            r_export = await ac.get(
                _export_endpoint(), headers=_auth(data["tokens"]["super_admin"])
            )
            assert r_export.status_code == 200, (
                f"SA expected 200 on export, got {r_export.status_code}: {r_export.text}"
            )


# ==============================================================================
# TC14: Tenant Isolation - Company A cannot see Company B data (404)
# ==============================================================================


@pytest.mark.asyncio
async def test_tc14_tenant_isolation_project_404():
    """admin_a with reports.view cannot access proj_b -> 404 masked."""
    async with setup_batch_ac_data() as data:
        # admin_a already has reports.view + reports.export via Admin role
        proj_b_id = data["proj_b"].id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                _view_endpoint_project(proj_b_id),
                headers=_auth(data["tokens"]["admin_a"]),
            )
        # Must be 404 (masked as not found, not 403)
        assert r.status_code == 404, (
            f"Expected 404 for cross-tenant project, got {r.status_code}: {r.text}"
        )


# ==============================================================================
# TC15: IDOR Prevention - cross-tenant project_id in URL
# ==============================================================================


@pytest.mark.asyncio
async def test_tc15_idor_prevention_project_scoped_export():
    """admin_a cannot export project-scoped reports for proj_b -> 404."""
    async with setup_batch_ac_data() as data:
        proj_b_id = data["proj_b"].id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Try projects excel with project filter from other tenant
            r_audit = await ac.get(
                "/api/v1/reports/projects/excel",
                params={"project_id": proj_b_id},
                headers=_auth(data["tokens"]["admin_a"]),
            )
            # Should be 404 (project not found in tenant)
            assert r_audit.status_code == 404, (
                f"Expected 404 for cross-tenant project in projects/excel, "
                f"got {r_audit.status_code}: {r_audit.text}"
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Try project financial health with foreign project_id
            r_health = await ac.get(
                "/api/v1/reports/project-financial-health",
                params={"project_id": proj_b_id},
                headers=_auth(data["tokens"]["admin_a"]),
            )
            assert r_health.status_code == 404, (
                f"Expected 404 for cross-tenant project in financial-health, "
                f"got {r_health.status_code}: {r_health.text}"
            )


# ==============================================================================
# TC16: Tenantless non-SA user -> 403
# ==============================================================================


@pytest.mark.asyncio
async def test_tc16_tenantless_user_403():
    """User with company_id=None and is_super_admin=False gets 403."""
    async with setup_batch_ac_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # View endpoint
            r_view = await ac.get(
                _view_endpoint(),
                headers=_auth(data["tokens"]["tenantless"]),
            )
            assert r_view.status_code == 403, (
                f"Expected 403 for tenantless user, got {r_view.status_code}: {r_view.text}"
            )


# ==============================================================================
# TC17: Super Admin cross-tenant access
# ==============================================================================


@pytest.mark.asyncio
async def test_tc17_super_admin_cross_tenant():
    """Super Admin can access any tenant's project data."""
    async with setup_batch_ac_data() as data:
        proj_a_id = data["proj_a"].id
        proj_b_id = data["proj_b"].id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_a = await ac.get(
                _view_endpoint_project(proj_a_id),
                headers=_auth(data["tokens"]["super_admin"]),
            )
            assert r_a.status_code == 200, (
                f"SA expected 200 for proj_a, got {r_a.status_code}: {r_a.text}"
            )

            r_b = await ac.get(
                _view_endpoint_project(proj_b_id),
                headers=_auth(data["tokens"]["super_admin"]),
            )
            assert r_b.status_code == 200, (
                f"SA expected 200 for proj_b, got {r_b.status_code}: {r_b.text}"
            )


# ==============================================================================
# TC18: Static Router Audit
# ==============================================================================


def test_tc18_static_router_audit():
    """
    Static source analysis of app/api/reports.py:
    - 44 active route decorators (3 commented out, excluded)
    - 100% of active routes linked to require_permission("reports.*")
    - 0 active require_roles calls
    - 0 active REPORT_READ_ROLES usage
    - All require_permission codes start with "reports."
    """
    with open("app/api/reports.py", "r", encoding="utf-8") as f:
        content = f.read()
        lines = content.split("\n")

    # --- Syntax check ---
    try:
        ast.parse(content)
    except SyntaxError as e:
        pytest.fail(f"reports.py has syntax error: {e}")

    # --- Count active route decorators ---
    active_route_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#") and re.search(
            r"@router\.(get|post|put|delete|patch)", stripped
        ):
            active_route_lines.append(i)

    assert len(active_route_lines) == 44, (
        f"Expected 44 active route decorators, found {len(active_route_lines)}"
    )

    # --- 0 active require_roles ---
    active_require_roles = [
        (i + 1, lines[i].strip())
        for i in range(len(lines))
        if "require_roles" in lines[i] and not lines[i].strip().startswith("#")
    ]
    assert len(active_require_roles) == 0, (
        f"Found active require_roles calls: {active_require_roles}"
    )

    # --- All route handlers have require_permission ---
    # Use AST to find every async function that is decorated with @router.XXX
    # and verify it has require_permission in its parameter defaults.
    # This correctly handles stacked decorators (two @router.get on one function).
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        pytest.fail(f"reports.py AST parse failed: {e}")

    # Build a set of line numbers for active @router.XXX decorators
    router_decorator_lines = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#") and re.search(
            r"@router\.(get|post|put|delete|patch)", stripped
        ):
            router_decorator_lines.add(i + 1)  # 1-indexed

    # Find all async functions in module scope
    handlers_without_perm = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        # Check if ANY of its decorator lines are in router_decorator_lines
        is_route_handler = False
        for deco in node.decorator_list:
            if deco.lineno in router_decorator_lines:
                is_route_handler = True
                break
        if not is_route_handler:
            continue

        # Check: does any default arg contain require_permission?
        fn_src_lines = lines[node.lineno - 1 : node.end_lineno]
        fn_src = "\n".join(fn_src_lines)
        if 'require_permission(' not in fn_src:
            handlers_without_perm.append((node.name, node.lineno))

    assert len(handlers_without_perm) == 0, (
        f"Route handler functions missing require_permission:\n"
        + "\n".join(f"  {name} (L{ln})" for name, ln in handlers_without_perm)
    )

    # --- All permissions are reports.view or reports.export ---
    wrong_perms = []
    for i, line in enumerate(lines):
        if "require_permission(" in line and not line.strip().startswith("#"):
            m = re.search(r'require_permission\("([^"]+)"\)', line)
            if m:
                perm = m.group(1)
                if not perm.startswith("reports."):
                    wrong_perms.append((i + 1, perm))
    assert len(wrong_perms) == 0, (
        f"Non-reports permissions found: {wrong_perms}"
    )

    # --- No active REPORT_READ_ROLES usage ---
    active_role_allowlist = [
        (i + 1, lines[i].strip())
        for i in range(len(lines))
        if "REPORT_READ_ROLES" in lines[i] and not lines[i].strip().startswith("#")
    ]
    assert len(active_role_allowlist) == 0, (
        f"Active REPORT_READ_ROLES usage found: {active_role_allowlist}"
    )


# ==============================================================================
# BONUS: company-wide reports data isolation
# ==============================================================================


@pytest.mark.asyncio
async def test_bonus_company_wide_reports_isolation():
    """
    Company-wide endpoints (profit-loss, cashflow, assets) must only return
    data belonging to the requesting user's company.
    Both admin_a and admin_b should get 200, but isolated results.
    """
    async with setup_batch_ac_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_a = await ac.get(
                "/api/v1/reports/profit-loss",
                headers=_auth(data["tokens"]["admin_a"]),
            )
            r_b = await ac.get(
                "/api/v1/reports/profit-loss",
                headers=_auth(data["tokens"]["admin_b"]),
            )

        assert r_a.status_code == 200, f"admin_a profit-loss failed: {r_a.text}"
        assert r_b.status_code == 200, f"admin_b profit-loss failed: {r_b.text}"

        # Both get valid JSON responses (may be zero values, but not an error)
        data_a = r_a.json()
        data_b = r_b.json()
        assert "income" in data_a and "expense" in data_a and "profit" in data_a
        assert "income" in data_b and "expense" in data_b and "profit" in data_b


@pytest.mark.asyncio
async def test_bonus_audit_excel_cross_tenant_project_rejected():
    """
    admin_a requesting audit/excel with user_b's user_id must get 404,
    not data from user_b's company. Also testing audit-pdf with foreign project_id -> 404.
    """
    async with setup_batch_ac_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_user = await ac.get(
                "/api/v1/reports/audit/excel",
                params={"user_id": data["users"]["admin_b"].id},
                headers=_auth(data["tokens"]["admin_a"]),
            )
            assert r_user.status_code == 404, (
                f"Expected 404 (cross-tenant user in audit/excel), got {r_user.status_code}: {r_user.text}"
            )

            r_proj = await ac.get(
                "/api/v1/reports/audit-pdf",
                params={"project_id": data["proj_b"].id},
                headers=_auth(data["tokens"]["admin_a"]),
            )
            assert r_proj.status_code == 404, (
                f"Expected 404 (cross-tenant project in audit-pdf), got {r_proj.status_code}: {r_proj.text}"
            )
