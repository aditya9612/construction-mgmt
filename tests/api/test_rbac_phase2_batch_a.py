import pytest
import uuid
from contextlib import asynccontextmanager
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

import app.db.base  # Ensure all SQLAlchemy models are registered
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.settings import CompanySettings, UserSettings
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.models.alert import Alert
from app.models.notification import Notification
from app.core.security import get_password_hash, create_access_token


@asynccontextmanager
async def setup_batch_a_data():
    """Seed test companies, users, projects, and permissions for Batch A test suite."""
    async with AsyncSessionLocal() as db:
        # Create test companies
        comp_a = Company(
            name=f"BatchA-CompA-{uuid.uuid4().hex[:6]}",
            subdomain=f"baca{uuid.uuid4().hex[:4]}",
        )
        comp_b = Company(
            name=f"BatchA-CompB-{uuid.uuid4().hex[:6]}",
            subdomain=f"bacb{uuid.uuid4().hex[:4]}",
        )
        db.add_all([comp_a, comp_b])
        await db.flush()

        # Create Owners
        owner_a = Owner(
            owner_code=f"OWN-A-{uuid.uuid4().hex[:6]}",
            owner_name="Owner A",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_a_{uuid.uuid4().hex[:6]}@test.com",
            company_id=comp_a.id,
        )
        owner_b = Owner(
            owner_code=f"OWN-B-{uuid.uuid4().hex[:6]}",
            owner_name="Owner B",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_b_{uuid.uuid4().hex[:6]}@test.com",
            company_id=comp_b.id,
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # Company settings for Comp A
        cs_a = CompanySettings(
            company_id=comp_a.id,
            company_name=comp_a.name,
            gst_number="27ABCDE1234F1Z5",
        )
        db.add(cs_a)

        # Projects for Comp A and Comp B
        proj_a = Project(
            business_id=f"PRJ-A-{uuid.uuid4().hex[:6]}",
            project_name=f"BatchA-ProjA-{uuid.uuid4().hex[:6]}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status="Planned",
        )
        proj_b = Project(
            business_id=f"PRJ-B-{uuid.uuid4().hex[:6]}",
            project_name=f"BatchA-ProjB-{uuid.uuid4().hex[:6]}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            status="Planned",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Users
        # 1. Unprivileged user in Comp A (Role without permissions)
        user_noperm = User(
            email=f"noperm_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="No Perm User",
            company_id=comp_a.id,
            role="UnprivilegedRole",
            is_active=True,
            is_super_admin=False,
        )
        # 2. User with Custom Role in Comp A
        custom_role_name = f"Draftsman_{uuid.uuid4().hex[:6]}"
        user_custom = User(
            email=f"custom_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Custom Role User",
            company_id=comp_a.id,
            role=custom_role_name,
            is_active=True,
            is_super_admin=False,
        )
        # 3. User with Tenant Admin in Comp A
        user_admin = User(
            email=f"admin_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Tenant Admin User",
            company_id=comp_a.id,
            role="Admin",
            is_active=True,
            is_super_admin=False,
        )
        # 4. User in Comp B
        user_comp_b = User(
            email=f"compb_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Comp B User",
            company_id=comp_b.id,
            role=custom_role_name,  # Same role name, but for Comp B
            is_active=True,
            is_super_admin=False,
        )
        # 5. Super Admin (No tenant)
        user_super = User(
            email=f"super_{uuid.uuid4().hex[:6]}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Super Admin User",
            company_id=None,
            role="Admin",
            is_active=True,
            is_super_admin=True,
        )

        db.add_all([user_noperm, user_custom, user_admin, user_comp_b, user_super])
        await db.flush()

        # Create Custom Role in Comp A
        role_record_a = Role(
            company_id=comp_a.id,
            name=custom_role_name,
            display_name="Custom Draftsman",
            is_system=False,
        )
        db.add(role_record_a)
        await db.flush()

        # Fetch relevant DB permissions
        p_drawings_view = await db.scalar(select(Permission).where(Permission.code == "drawings.view"))
        p_drawings_create = await db.scalar(select(Permission).where(Permission.code == "drawings.create"))
        p_settings_view = await db.scalar(select(Permission).where(Permission.code == "settings.view"))
        p_settings_edit = await db.scalar(select(Permission).where(Permission.code == "settings.edit"))
        p_projects_view = await db.scalar(select(Permission).where(Permission.code == "projects.view"))
        p_alerts_create = await db.scalar(select(Permission).where(Permission.code == "alerts.create"))
        p_notif_view = await db.scalar(select(Permission).where(Permission.code == "notifications.view"))

        # Assign drawings.view and projects.view to user_custom's role in Comp A
        rp1 = RolePermission(role=custom_role_name, role_id=role_record_a.id, permission_id=p_drawings_view.id)
        rp2 = RolePermission(role=custom_role_name, role_id=role_record_a.id, permission_id=p_projects_view.id)
        db.add_all([rp1, rp2])
        await db.commit()

        batch_data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "user_noperm": user_noperm,
            "user_custom": user_custom,
            "user_admin": user_admin,
            "user_comp_b": user_comp_b,
            "user_super": user_super,
            "custom_role_name": custom_role_name,
            "role_record_a": role_record_a,
            "perms": {
                "drawings.view": p_drawings_view,
                "drawings.create": p_drawings_create,
                "settings.view": p_settings_view,
                "settings.edit": p_settings_edit,
                "projects.view": p_projects_view,
                "alerts.create": p_alerts_create,
                "notifications.view": p_notif_view,
            }
        }

    try:
        yield batch_data
    finally:
        # Cleanup
        async with AsyncSessionLocal() as db:
            await db.execute(delete(RolePermission).where(RolePermission.role == custom_role_name))
            await db.execute(delete(UserPermissionOverride).where(
                UserPermissionOverride.user_id.in_([
                    user_noperm.id, user_custom.id, user_admin.id, user_comp_b.id, user_super.id
                ])
            ))
            await db.execute(delete(Role).where(Role.id == role_record_a.id))
            await db.execute(delete(Alert).where(Alert.user_id.in_([user_noperm.id, user_custom.id])))
            await db.execute(delete(Notification).where(Notification.user_id.in_([user_noperm.id, user_custom.id])))
            await db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(CompanySettings).where(CompanySettings.company_id == comp_a.id))
            await db.execute(delete(UserSettings).where(
                UserSettings.user_id.in_([user_noperm.id, user_custom.id, user_admin.id, user_comp_b.id, user_super.id])
            ))
            await db.execute(delete(User).where(
                User.id.in_([user_noperm.id, user_custom.id, user_admin.id, user_comp_b.id, user_super.id])
            ))
            await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()


def get_auth_headers(user: User):
    token = create_access_token(data={"sub": str(user.id), "role": user.role, "company_id": user.company_id})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_requests_fail():
    """Verify unauthenticated requests to migrated endpoints return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r1 = await ac.get("/api/v1/settings/company")
        assert r1.status_code == 401

        r2 = await ac.get("/api/v1/projects/1/visualizations")
        assert r2.status_code == 401

        r3 = await ac.get("/api/v1/cad/logs")
        assert r3.status_code == 401

        r4 = await ac.get("/api/v1/drawings?project_id=1")
        assert r4.status_code == 401

        r5 = await ac.post("/api/v1/alerts", json={"project_id": 1, "alert_type": "info", "message": "msg", "user_id": 1})
        assert r5.status_code == 401

        r6 = await ac.get("/api/v1/notifications/project-manager")
        assert r6.status_code == 401


@pytest.mark.asyncio
async def test_standard_unprivileged_role_denied():
    """Verify user without permission receives 403 on migrated endpoints."""
    async with setup_batch_a_data() as data:
        headers = get_auth_headers(data["user_noperm"])
        proj_a_id = data["proj_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Settings (needs settings.view)
            res = await ac.get("/api/v1/settings/company", headers=headers)
            assert res.status_code == 403

            # Visualizations (needs projects.view)
            res = await ac.get(f"/api/v1/projects/{proj_a_id}/visualizations", headers=headers)
            assert res.status_code == 403

            # Drawings (needs drawings.view)
            res = await ac.get(f"/api/v1/drawings?project_id={proj_a_id}", headers=headers)
            assert res.status_code == 403

            # CAD logs (needs drawings.view)
            res = await ac.get("/api/v1/cad/logs", headers=headers)
            assert res.status_code == 403

            # Alerts create (needs alerts.create)
            res = await ac.post(
                "/api/v1/alerts",
                json={"project_id": proj_a_id, "alert_type": "warning", "message": "denied test", "user_id": data["user_noperm"].id},
                headers=headers,
            )
            assert res.status_code == 403

            # PM Notifications (needs notifications.view)
            res = await ac.get("/api/v1/notifications/project-manager", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_custom_role_authorization():
    """Verify user with custom role in DB receives 200 on permitted endpoints."""
    async with setup_batch_a_data() as data:
        headers = get_auth_headers(data["user_custom"])
        proj_a_id = data["proj_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Custom role was granted drawings.view and projects.view
            res_draw = await ac.get(f"/api/v1/drawings?project_id={proj_a_id}", headers=headers)
            assert res_draw.status_code == 200

            res_cad = await ac.get("/api/v1/cad/logs", headers=headers)
            assert res_cad.status_code == 200

            res_viz = await ac.get(f"/api/v1/projects/{proj_a_id}/visualizations", headers=headers)
            assert res_viz.status_code == 200

            # But NOT granted settings.view -> 403
            res_settings = await ac.get("/api/v1/settings/company", headers=headers)
            assert res_settings.status_code == 403


@pytest.mark.asyncio
async def test_runtime_grant_revoke_regrant_lifecycle():
    """Dynamic Runtime Test: grant -> 200 -> revoke -> 403 -> regrant -> 200."""
    async with setup_batch_a_data() as data:
        headers = get_auth_headers(data["user_custom"])
        role_id = data["role_record_a"].id
        role_name = data["custom_role_name"]
        p_settings_view = data["perms"]["settings.view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initial state: no settings.view -> 403
            r_init = await ac.get("/api/v1/settings/company", headers=headers)
            assert r_init.status_code == 403

            # 1. GRANT settings.view to custom role at runtime in DB
            async with AsyncSessionLocal() as db:
                rp = RolePermission(role=role_name, role_id=role_id, permission_id=p_settings_view.id)
                db.add(rp)
                await db.commit()

            # Immediate check -> 200 OK
            r_granted = await ac.get("/api/v1/settings/company", headers=headers)
            assert r_granted.status_code == 200
            assert r_granted.json()["company_name"] == data["comp_a"].name

            # 2. REVOKE settings.view from custom role at runtime
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role == role_name,
                        RolePermission.permission_id == p_settings_view.id,
                    )
                )
                await db.commit()

            # Immediate check -> 403 Forbidden
            r_revoked = await ac.get("/api/v1/settings/company", headers=headers)
            assert r_revoked.status_code == 403

            # 3. RE-GRANT settings.view at runtime
            async with AsyncSessionLocal() as db:
                rp_new = RolePermission(role=role_name, role_id=role_id, permission_id=p_settings_view.id)
                db.add(rp_new)
                await db.commit()

            # Immediate check -> 200 OK
            r_regranted = await ac.get("/api/v1/settings/company", headers=headers)
            assert r_regranted.status_code == 200


@pytest.mark.asyncio
async def test_user_permission_override_grant():
    """Verify user-level override (is_granted=True) authorizes a user whose role lacks permission."""
    async with setup_batch_a_data() as data:
        user = data["user_noperm"]
        headers = get_auth_headers(user)
        proj_a_id = data["proj_a"].id
        p_alerts = data["perms"]["alerts.create"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially user role lacks alerts.create -> 403
            payload = {"project_id": proj_a_id, "alert_type": "test", "message": "override test", "user_id": user.id}
            res_init = await ac.post("/api/v1/alerts", json=payload, headers=headers)
            assert res_init.status_code == 403

            # Grant explicit user permission override
            async with AsyncSessionLocal() as db:
                override = UserPermissionOverride(user_id=user.id, permission_id=p_alerts.id, is_granted=True)
                db.add(override)
                await db.commit()

            # Now succeeds -> 200 OK
            res_overridden = await ac.post("/api/v1/alerts", json=payload, headers=headers)
            assert res_overridden.status_code == 200
            assert res_overridden.json()["message"] == "override test"


@pytest.mark.asyncio
async def test_user_permission_override_revoke():
    """Verify user-level negative override (is_granted=False) denies user whose role has permission."""
    async with setup_batch_a_data() as data:
        user = data["user_custom"]  # Role has drawings.view
        headers = get_auth_headers(user)
        proj_a_id = data["proj_a"].id
        p_drawings_view = data["perms"]["drawings.view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially allowed via role -> 200 OK
            res_init = await ac.get(f"/api/v1/drawings?project_id={proj_a_id}", headers=headers)
            assert res_init.status_code == 200

            # Add negative override (is_granted=False)
            async with AsyncSessionLocal() as db:
                override = UserPermissionOverride(user_id=user.id, permission_id=p_drawings_view.id, is_granted=False)
                db.add(override)
                await db.commit()

            # Explicit negative override overrides role -> 403 Forbidden
            res_denied = await ac.get(f"/api/v1/drawings?project_id={proj_a_id}", headers=headers)
            assert res_denied.status_code == 403


@pytest.mark.asyncio
async def test_wildcard_permission_and_negative_override():
    """Verify role wildcard grant (drawings.*) allows all drawings routes, and explicit override revokes one."""
    async with setup_batch_a_data() as data:
        user = data["user_custom"]
        headers = get_auth_headers(user)
        role_name = data["custom_role_name"]
        role_id = data["role_record_a"].id
        proj_a_id = data["proj_a"].id

        async with AsyncSessionLocal() as db:
            p_delete = await db.scalar(select(Permission).where(Permission.code == "drawings.delete"))
            # In our engine, role_permissions can have all drawings perms
            all_drawing_perms = (await db.scalars(select(Permission).where(Permission.module == "drawings"))).all()
            for p in all_drawing_perms:
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
            # Add negative override for drawings.delete
            db.add(UserPermissionOverride(user_id=user.id, permission_id=p_delete.id, is_granted=False))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # drawings.view works -> 200
            r_view = await ac.get(f"/api/v1/drawings?project_id={proj_a_id}", headers=headers)
            assert r_view.status_code == 200

            # drawings.delete is negatively overridden -> 403
            r_del = await ac.delete("/api/v1/drawings/999999", headers=headers)
            assert r_del.status_code == 403


@pytest.mark.asyncio
async def test_tenant_boundary_isolation():
    """Verify Company A permissions do NOT leak to Company B, and resource tenant check rejects cross-tenant access."""
    async with setup_batch_a_data() as data:
        user_a = data["user_custom"]
        user_b = data["user_comp_b"]
        proj_b_id = data["proj_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # User B (Company B) does NOT inherit Company A's custom role permissions (role was scoped to Comp A)
            headers_b = get_auth_headers(user_b)
            res_b = await ac.get(f"/api/v1/drawings?project_id={proj_b_id}", headers=headers_b)
            assert res_b.status_code == 403

            # User A has projects.view in Comp A, but trying to view Comp B's project visualizations yields 404 (NotFoundError)
            headers_a = get_auth_headers(user_a)
            res_cross = await ac.get(f"/api/v1/projects/{proj_b_id}/visualizations", headers=headers_a)
            assert res_cross.status_code == 404


@pytest.mark.asyncio
async def test_admin_bypass_and_resource_boundary():
    """Verify Super Admin & Tenant Admin permission bypass, while resource boundaries remain enforced."""
    async with setup_batch_a_data() as data:
        proj_b_id = data["proj_b"].id

        headers_admin = get_auth_headers(data["user_admin"])
        headers_super = get_auth_headers(data["user_super"])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Tenant Admin has permission bypass -> 200 on Company A settings
            r_adm_sett = await ac.get("/api/v1/settings/company", headers=headers_admin)
            assert r_adm_sett.status_code == 200

            # Tenant Admin cannot access Comp B project visualizations -> 404
            r_adm_cross = await ac.get(f"/api/v1/projects/{proj_b_id}/visualizations", headers=headers_admin)
            assert r_adm_cross.status_code == 404

            # Super Admin has permission bypass, but company settings has explicit boundary check
            # 'Super Admin cannot access tenant company settings.' -> 403
            r_sup_sett = await ac.get("/api/v1/settings/company", headers=headers_super)
            assert r_sup_sett.status_code == 403
            assert "Super Admin cannot access tenant company settings" in r_sup_sett.json()["detail"]


@pytest.mark.asyncio
async def test_personal_routes_remain_accessible():
    """Verify personal / self-service routes remain accessible to standard users without administrative permissions."""
    async with setup_batch_a_data() as data:
        headers = get_auth_headers(data["user_noperm"])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Personal user settings
            r1 = await ac.get("/api/v1/settings", headers=headers)
            assert r1.status_code == 200

            # Personal user profile
            r2 = await ac.get("/api/v1/settings/profile", headers=headers)
            assert r2.status_code == 200

            # Personal alerts inbox
            r3 = await ac.get("/api/v1/alerts", headers=headers)
            assert r3.status_code == 200

            # Personal notifications inbox
            r4 = await ac.get("/api/v1/notifications", headers=headers)
            assert r4.status_code == 200

            # Personal unread count
            r5 = await ac.get("/api/v1/notifications/unread-count", headers=headers)
            assert r5.status_code == 200
