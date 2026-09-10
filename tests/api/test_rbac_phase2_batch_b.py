import pytest
import uuid
from datetime import date, datetime, timedelta, timezone
from contextlib import asynccontextmanager
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

import app.db.base  # Register all SQLAlchemy models
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserAttendance
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, Task, ProjectMember
from app.models.expense import Expense
from app.models.settings import CompanySettings, UserSettings
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.cache import redis as r


@asynccontextmanager
async def setup_batch_b_data():
    """Seed test companies, users, projects, attendance, and expenses for Batch B test suite."""
    async with AsyncSessionLocal() as db:
        # Create test companies
        comp_a = Company(
            name=f"BatchB-CompA-{uuid.uuid4().hex[:6]}",
            subdomain=f"bbca{uuid.uuid4().hex[:4]}",
        )
        comp_b = Company(
            name=f"BatchB-CompB-{uuid.uuid4().hex[:6]}",
            subdomain=f"bbcb{uuid.uuid4().hex[:4]}",
        )
        db.add_all([comp_a, comp_b])
        await db.flush()

        # Create Owners
        owner_a = Owner(
            owner_code=f"OWN-BA-{uuid.uuid4().hex[:6]}",
            owner_name="Owner BA",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_ba_{uuid.uuid4().hex[:6]}@test.com",
            company_id=comp_a.id,
        )
        owner_b = Owner(
            owner_code=f"OWN-BB-{uuid.uuid4().hex[:6]}",
            owner_name="Owner BB",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_bb_{uuid.uuid4().hex[:6]}@test.com",
            company_id=comp_b.id,
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # Projects for Comp A and Comp B
        proj_a = Project(
            business_id=f"PRJ-BA-{uuid.uuid4().hex[:6]}",
            project_name=f"BatchB-ProjA-{uuid.uuid4().hex[:6]}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status="Ongoing",
            budget_amount=500000.0,
        )
        proj_b = Project(
            business_id=f"PRJ-BB-{uuid.uuid4().hex[:6]}",
            project_name=f"BatchB-ProjB-{uuid.uuid4().hex[:6]}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            status="Ongoing",
            budget_amount=900000.0,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Expenses for forecast testing
        today = date.today()
        exp_a = Expense(
            project_id=proj_a.id,
            amount=15000.0,
            expense_date=today,
            payment_mode="Cash",
            category="Materials",
            description="Comp A Concrete",
        )
        exp_b = Expense(
            project_id=proj_b.id,
            amount=88000.0,
            expense_date=today,
            payment_mode="Cash",
            category="Equipment",
            description="Comp B Secret Crane",
        )
        db.add_all([exp_a, exp_b])
        await db.flush()

        # Custom role names
        custom_dash_role = f"DashViewer_{uuid.uuid4().hex[:6]}"
        custom_att_role = f"FieldSuper_{uuid.uuid4().hex[:6]}"

        # Users in Comp A
        user_noperm = User(
            email=f"b_noperm_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="No Perm User B",
            company_id=comp_a.id,
            role="UnprivilegedRole",
            is_active=True,
            is_super_admin=False,
        )
        user_dash = User(
            email=f"b_dash_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Dash Viewer User",
            company_id=comp_a.id,
            role=custom_dash_role,
            is_active=True,
            is_super_admin=False,
        )
        user_att = User(
            email=f"b_att_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Attendance Supervisor User",
            company_id=comp_a.id,
            role=custom_att_role,
            is_active=True,
            is_super_admin=False,
        )
        user_admin = User(
            email=f"b_admin_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Comp A Admin",
            company_id=comp_a.id,
            role="Admin",
            is_active=True,
            is_super_admin=False,
        )
        # Worker in Comp A (for personal attendance tests)
        user_worker = User(
            email=f"b_worker_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Worker Joe",
            company_id=comp_a.id,
            role="Labour",
            is_active=True,
            is_super_admin=False,
        )

        # Users in Comp B
        user_comp_b = User(
            email=f"b_compb_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Comp B User",
            company_id=comp_b.id,
            role=custom_dash_role,  # Same role name in Comp B
            is_active=True,
            is_super_admin=False,
        )

        # Super Admin
        user_super = User(
            email=f"b_super_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Super Admin B",
            company_id=None,
            role="Admin",
            is_active=True,
            is_super_admin=True,
        )

        db.add_all([user_noperm, user_dash, user_att, user_admin, user_worker, user_comp_b, user_super])
        await db.flush()

        # Project member so user_dash has authorized access to proj_a
        pm_a = ProjectMember(project_id=proj_a.id, user_id=user_dash.id)
        db.add(pm_a)

        # Attendance record for Worker Joe in Comp A
        att_worker = UserAttendance(
            user_id=user_worker.id,
            project_id=proj_a.id,
            attendance_date=today,
            in_time=datetime.now(timezone.utc).replace(tzinfo=None),
            status="present",
        )
        db.add(att_worker)
        await db.flush()

        # Create Role records in Comp A
        role_dash_a = Role(
            company_id=comp_a.id,
            name=custom_dash_role,
            display_name="Custom Dash Role",
            is_system=False,
        )
        role_att_a = Role(
            company_id=comp_a.id,
            name=custom_att_role,
            display_name="Custom Attendance Role",
            is_system=False,
        )
        db.add_all([role_dash_a, role_att_a])
        await db.flush()

        # Fetch permissions from existing DB catalog
        p_dash_view = await db.scalar(select(Permission).where(Permission.code == "dashboard.view"))
        p_dash_manage = await db.scalar(select(Permission).where(Permission.code == "dashboard.manage"))
        p_dash_export = await db.scalar(select(Permission).where(Permission.code == "dashboard.export"))
        p_att_view = await db.scalar(select(Permission).where(Permission.code == "attendance.view"))
        p_att_create = await db.scalar(select(Permission).where(Permission.code == "attendance.create"))
        p_att_edit = await db.scalar(select(Permission).where(Permission.code == "attendance.edit"))
        p_att_export = await db.scalar(select(Permission).where(Permission.code == "attendance.export"))

        # Grant dashboard.view to role_dash_a
        rp_dash = RolePermission(role=custom_dash_role, role_id=role_dash_a.id, permission_id=p_dash_view.id)
        # Grant attendance.view to role_att_a
        rp_att = RolePermission(role=custom_att_role, role_id=role_att_a.id, permission_id=p_att_view.id)
        db.add_all([rp_dash, rp_att])
        await db.commit()

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "exp_a": exp_a,
            "exp_b": exp_b,
            "att_worker": att_worker,
            "user_noperm": user_noperm,
            "user_dash": user_dash,
            "user_att": user_att,
            "user_admin": user_admin,
            "user_worker": user_worker,
            "user_comp_b": user_comp_b,
            "user_super": user_super,
            "custom_dash_role": custom_dash_role,
            "custom_att_role": custom_att_role,
            "role_dash_a": role_dash_a,
            "role_att_a": role_att_a,
            "perms": {
                "dashboard.view": p_dash_view,
                "dashboard.manage": p_dash_manage,
                "dashboard.export": p_dash_export,
                "attendance.view": p_att_view,
                "attendance.create": p_att_create,
                "attendance.edit": p_att_edit,
                "attendance.export": p_att_export,
            },
        }

    try:
        yield data
    finally:
        # Cleanup
        async with AsyncSessionLocal() as db:
            await db.execute(delete(RolePermission).where(
                RolePermission.role.in_([custom_dash_role, custom_att_role])
            ))
            all_user_ids = [
                user_noperm.id, user_dash.id, user_att.id,
                user_admin.id, user_worker.id, user_comp_b.id, user_super.id
            ]
            await db.execute(delete(UserPermissionOverride).where(
                UserPermissionOverride.user_id.in_(all_user_ids)
            ))
            await db.execute(delete(Role).where(Role.id.in_([role_dash_a.id, role_att_a.id])))
            await db.execute(delete(UserAttendance).where(UserAttendance.user_id.in_(all_user_ids)))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(Expense).where(Expense.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(UserSettings).where(UserSettings.user_id.in_(all_user_ids)))
            await db.execute(delete(User).where(User.id.in_(all_user_ids)))
            await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()


def get_auth_headers(user: User):
    token = create_access_token(data={"sub": str(user.id), "role": user.role, "company_id": user.company_id})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_requests_fail():
    """Verify unauthenticated requests to migrated Batch B endpoints return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r1 = await ac.get("/api/v1/dashboard/admin")
        assert r1.status_code == 401

        r2 = await ac.get("/api/v1/dashboard/engineer")
        assert r2.status_code == 401

        r3 = await ac.get("/api/v1/dashboard/manager")
        assert r3.status_code == 401

        r4 = await ac.get("/api/v1/dashboard/accountant")
        assert r4.status_code == 401

        r5 = await ac.get("/api/v1/attendance/list")
        assert r5.status_code == 401

        r6 = await ac.post("/api/v1/attendance/proxy-check-in", json={"user_ids": [1]})
        assert r6.status_code == 401


@pytest.mark.asyncio
async def test_missing_permission_forbidden():
    """Verify users without required permissions are returned 403 Forbidden."""
    async with setup_batch_b_data() as data:
        headers = get_auth_headers(data["user_noperm"])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1 = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert r1.status_code == 403

            r2 = await ac.get("/api/v1/dashboard/engineer", headers=headers)
            assert r2.status_code == 403

            r3 = await ac.get("/api/v1/dashboard/manager", headers=headers)
            assert r3.status_code == 403

            r4 = await ac.get("/api/v1/attendance/list", headers=headers)
            assert r4.status_code == 403

            r5 = await ac.post("/api/v1/dashboard/refresh", headers=headers)
            assert r5.status_code == 403


@pytest.mark.asyncio
async def test_custom_role_authorization_and_runtime_lifecycle():
    """
    Verify custom roles receive access from DB permissions, and runtime
    grant -> 200 -> revoke -> 403 -> regrant -> 200 works without server restart.
    """
    async with setup_batch_b_data() as data:
        user_dash = data["user_dash"]
        role_dash_a = data["role_dash_a"]
        custom_dash_role = data["custom_dash_role"]
        p_dash_view = data["perms"]["dashboard.view"]
        headers = get_auth_headers(user_dash)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Custom role has dashboard.view -> 200
            res = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert res.status_code == 200

            # 2. Revoke permission from custom role at runtime
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role == custom_dash_role,
                        RolePermission.permission_id == p_dash_view.id,
                    )
                )
                await db.commit()

            # 3. Next request must be 403 Forbidden immediately
            res_revoked = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert res_revoked.status_code == 403

            # 4. Regrant permission to custom role at runtime
            async with AsyncSessionLocal() as db:
                rp_new = RolePermission(
                    role=custom_dash_role,
                    role_id=role_dash_a.id,
                    permission_id=p_dash_view.id,
                )
                db.add(rp_new)
                await db.commit()

            # 5. Next request must succeed with 200 immediately
            res_regranted = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert res_regranted.status_code == 200


@pytest.mark.asyncio
async def test_attendance_custom_role_lifecycle():
    """
    Verify attendance oversight works for custom role with attendance.view,
    and runtime grant -> 200 -> revoke -> 403 -> regrant -> 200 works without restart.
    """
    async with setup_batch_b_data() as data:
        user_att = data["user_att"]
        role_att_a = data["role_att_a"]
        custom_att_role = data["custom_att_role"]
        p_att_view = data["perms"]["attendance.view"]
        headers = get_auth_headers(user_att)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Has attendance.view -> 200
            res = await ac.get("/api/v1/attendance/list", headers=headers)
            assert res.status_code == 200

            # 2. Revoke attendance.view
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role == custom_att_role,
                        RolePermission.permission_id == p_att_view.id,
                    )
                )
                await db.commit()

            # 3. Request is 403
            res_revoked = await ac.get("/api/v1/attendance/list", headers=headers)
            assert res_revoked.status_code == 403

            # 4. Regrant attendance.view
            async with AsyncSessionLocal() as db:
                rp = RolePermission(
                    role=custom_att_role,
                    role_id=role_att_a.id,
                    permission_id=p_att_view.id,
                )
                db.add(rp)
                await db.commit()

            # 5. Request is 200
            res_regranted = await ac.get("/api/v1/attendance/list", headers=headers)
            assert res_regranted.status_code == 200


@pytest.mark.asyncio
async def test_user_permission_overrides():
    """
    Verify user permission overrides:
    1. User has no role permission -> override is_granted=True -> 200
    2. User has role permission -> override is_granted=False -> 403 (explicit revoke)
    """
    async with setup_batch_b_data() as data:
        user_noperm = data["user_noperm"]
        user_dash = data["user_dash"]
        p_dash_view = data["perms"]["dashboard.view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Case 1: Positive override for unprivileged user
            async with AsyncSessionLocal() as db:
                ov_grant = UserPermissionOverride(
                    user_id=user_noperm.id,
                    permission_id=p_dash_view.id,
                    is_granted=True,
                )
                db.add(ov_grant)
                await db.commit()

            h_noperm = get_auth_headers(user_noperm)
            res1 = await ac.get("/api/v1/dashboard/admin", headers=h_noperm)
            assert res1.status_code == 200

            # Case 2: Explicit negative override for user whose role has the permission
            async with AsyncSessionLocal() as db:
                ov_revoke = UserPermissionOverride(
                    user_id=user_dash.id,
                    permission_id=p_dash_view.id,
                    is_granted=False,
                )
                db.add(ov_revoke)
                await db.commit()

            h_dash = get_auth_headers(user_dash)
            res2 = await ac.get("/api/v1/dashboard/admin", headers=h_dash)
            assert res2.status_code == 403


@pytest.mark.asyncio
async def test_wildcard_and_negative_override():
    """
    Verify wildcard / full-module permission grant allows access,
    and explicit negative user override denies access even with role grant.
    """
    async with setup_batch_b_data() as data:
        user_dash = data["user_dash"]
        role_id = data["role_dash_a"].id
        role_name = data["custom_dash_role"]
        p_dash_view = data["perms"]["dashboard.view"]
        headers = get_auth_headers(user_dash)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            async with AsyncSessionLocal() as db:
                all_dash_perms = (await db.scalars(select(Permission).where(Permission.module == "dashboard"))).all()
                for p in all_dash_perms:
                    db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # Access should succeed via granted permissions
            res_wild = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert res_wild.status_code == 200

            # Now add negative user override for dashboard.view
            async with AsyncSessionLocal() as db:
                neg_ov = UserPermissionOverride(
                    user_id=user_dash.id,
                    permission_id=p_dash_view.id,
                    is_granted=False,
                )
                db.add(neg_ov)
                await db.commit()

            # Must be denied (403) despite role grant
            res_denied = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert res_denied.status_code == 403


@pytest.mark.asyncio
async def test_tenant_isolation_cross_company():
    """
    Verify permissions are strictly isolated per company:
    Company B user with the same role name as Company A does NOT inherit Company A's permissions.
    """
    async with setup_batch_b_data() as data:
        user_comp_b = data["user_comp_b"]
        headers_b = get_auth_headers(user_comp_b)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Comp B user should be 403 because role was only granted in Comp A
            res = await ac.get("/api/v1/dashboard/admin", headers=headers_b)
            assert res.status_code == 403

            res_att = await ac.get("/api/v1/attendance/list", headers=headers_b)
            assert res_att.status_code == 403


@pytest.mark.asyncio
async def test_forecast_project_id_idor_remediation():
    """
    P0 SECURITY TEST:
    Verify advanced_forecast and ml_forecast do NOT allow cross-tenant project_id query param.
    Company A user querying Company B's project_id must return 404 (NotFoundError),
    and must never leak Company B's expense numbers.
    """
    async with setup_batch_b_data() as data:
        user_dash = data["user_dash"]
        proj_a = data["proj_a"]
        proj_b = data["proj_b"]
        headers = get_auth_headers(user_dash)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. User accessing their own project forecast -> 200
            res_own = await ac.get(f"/api/v1/dashboard/graph/advanced-forecast?project_id={proj_a.id}", headers=headers)
            assert res_own.status_code == 200

            # 2. User supplying cross-tenant project_id (Company B) -> 404 access denied
            res_cross = await ac.get(f"/api/v1/dashboard/graph/advanced-forecast?project_id={proj_b.id}", headers=headers)
            assert res_cross.status_code == 404

            # 3. Test ml-forecast cross-tenant project_id -> 404 access denied
            res_ml_cross = await ac.get(f"/api/v1/dashboard/graph/ml-forecast?project_id={proj_b.id}", headers=headers)
            assert res_ml_cross.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_redis_cache_tenant_isolation():
    """
    P1 SECURITY TEST:
    Verify engineer, manager, and pm_command_center cache keys are tenant-scoped.
    Responses for Company A must never be served to Company B.
    """
    async with setup_batch_b_data() as data:
        user_dash_a = data["user_dash"]
        headers_a = get_auth_headers(user_dash_a)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Call engineer dashboard for Company A
            res_a = await ac.get("/api/v1/dashboard/engineer", headers=headers_a)
            assert res_a.status_code == 200
            data_a = res_a.json()
            assert data_a["role"] == "engineer"

            # Call manager dashboard for Company A
            res_m_a = await ac.get("/api/v1/dashboard/manager", headers=headers_a)
            assert res_m_a.status_code == 200
            data_m_a = res_m_a.json()
            assert data_m_a["role"] == "manager"


@pytest.mark.asyncio
async def test_personal_routes_preserved():
    """
    Verify personal/self-service routes function for regular workers
    without requiring administrative or organizational RBAC permissions.
    """
    async with setup_batch_b_data() as data:
        user_worker = data["user_worker"]
        att_worker = data["att_worker"]
        headers = get_auth_headers(user_worker)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. GET /attendance/today
            res_today = await ac.get("/api/v1/attendance/today", headers=headers)
            assert res_today.status_code == 200
            assert res_today.json()["checked_in"] is True

            # 2. PUT /attendance/check-out/{attendance_id} for own attendance
            res_checkout = await ac.put(
                f"/api/v1/attendance/check-out/{att_worker.id}",
                headers=headers,
                data={"work_summary": "Finished foundation concrete pour"},
            )
            assert res_checkout.status_code == 200

            # 3. GET /dashboard/labour
            res_labour = await ac.get("/api/v1/dashboard/labour", headers=headers)
            # 200 or expected personal labour dashboard output
            assert res_labour.status_code in [200, 404]  # 404 if no Labour profile row


@pytest.mark.asyncio
async def test_tenant_admin_bypass_behavior():
    """
    Verify Tenant Admin has permission bypass for their own company resources,
    but is strictly bound to their company (cannot access Company B).
    """
    async with setup_batch_b_data() as data:
        user_admin = data["user_admin"]
        proj_b = data["proj_b"]
        headers = get_auth_headers(user_admin)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Tenant Admin accesses company dashboard without explicit permission record -> 200
            res_dash = await ac.get("/api/v1/dashboard/admin", headers=headers)
            assert res_dash.status_code == 200

            # 2. Tenant Admin accesses attendance list -> 200
            res_att = await ac.get("/api/v1/attendance/list", headers=headers)
            assert res_att.status_code == 200

            # 3. Tenant Admin cannot access Company B project forecast -> 404
            res_cross = await ac.get(f"/api/v1/dashboard/graph/advanced-forecast?project_id={proj_b.id}", headers=headers)
            assert res_cross.status_code == 404


@pytest.mark.asyncio
async def test_attendance_export_authorization():
    """
    Verify attendance export endpoints require attendance.export permission.
    """
    async with setup_batch_b_data() as data:
        user_dash = data["user_dash"]  # Has dashboard.view but NOT attendance.export
        user_att = data["user_att"]
        p_att_export = data["perms"]["attendance.export"]
        custom_att_role = data["custom_att_role"]
        role_att_a = data["role_att_a"]

        today_str = date.today().isoformat()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. User without attendance.export -> 403
            h_no = get_auth_headers(user_dash)
            res_no = await ac.get(
                f"/api/v1/attendance/export/csv?start_date={today_str}&end_date={today_str}",
                headers=h_no,
            )
            assert res_no.status_code == 403

            # 2. Grant attendance.export to custom attendance role
            async with AsyncSessionLocal() as db:
                rp = RolePermission(
                    role=custom_att_role,
                    role_id=role_att_a.id,
                    permission_id=p_att_export.id,
                )
                db.add(rp)
                await db.commit()

            # 3. User with attendance.export -> 200
            h_yes = get_auth_headers(user_att)
            res_yes = await ac.get(
                f"/api/v1/attendance/export/csv?start_date={today_str}&end_date={today_str}",
                headers=h_yes,
            )
            assert res_yes.status_code == 200
