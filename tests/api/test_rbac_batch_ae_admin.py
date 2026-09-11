"""
RBAC Batch AE: Role & Permission Administration Test Suite
============================================================

Covers all mandatory verification areas for Batch AE:
- TC01: Unauthenticated requests return 401
- TC02: Authenticated user with no permissions returns 403 for roles.* actions
- TC03: roles.view permission allows GET /rbac/permissions, GET /rbac/roles, GET /rbac/roles/{role}/permissions, GET /rbac/users/{id}/overrides, GET /rbac/audit-logs -> 200
- TC04: roles.create permission allows POST /rbac/roles -> 200
- TC05: roles.create alone cannot edit permissions (POST /rbac/roles/{role}/permissions -> 403)
- TC06: roles.edit permission allows role-permission mutations & user overrides -> 200
- TC07: roles.delete permission allows DELETE /rbac/roles/{role} -> 200
- TC08: Dynamic DB permission grant and revoke takes immediate effect
- TC09: Tenant Isolation - Company A cannot see or modify Company B custom roles
- TC10: Tenant Isolation - Company A cannot view or update Company B user overrides (404 masked)
- TC11: Tenantless non-SA user (company_id=None) returns 403 Forbidden
- TC12: IDOR Prevention - Nonexistent role returns 404
- TC13: Privilege Escalation Prevention - Cannot grant wildcard '*' or unpossessed permissions
- TC14: Privilege Escalation Prevention - Cannot grant unpossessed permissions via user overrides
- TC15: Privilege Escalation Prevention - Caller cannot modify own user overrides (403)
- TC16: Built-in role protection - Cannot delete built-in roles (400)
- TC17: Built-in role customization does not mutate global system role template
- TC18: Maintenance endpoints (/seed and /assign-defaults) allow SA and reject non-SA (403)
- TC19: Audit logs are tenant-scoped and immutable (no deletion/mutation API)
- TC20: Route Preservation - All 15 endpoints preserved exactly
"""

import uuid
from contextlib import asynccontextmanager
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.api.rbac import router as rbac_router
from app.models.company import Company
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride, RBACAuditLog
from app.models.user import User, UserRole, ROLES


# ==============================================================================
# HELPERS
# ==============================================================================

def _tok(user_id: int) -> str:
    return create_access_token({"sub": str(user_id)})


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def setup_batch_ae_data():
    """
    Provisions two isolated tenants (comp_a, comp_b) with:
    - Dedicated users in Company A and Company B
    - Permissions: roles.view, roles.create, roles.edit, roles.delete, projects.view
    - Roles: unprivileged_role, role_viewer, role_creator, role_editor, role_deleter, full_role_admin
    - Super Admin user
    - Tenantless non-SA user
    """
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"BatchAE_CompA_{uid}")
        comp_b = Company(name=f"BatchAE_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Permissions required
        perm_codes = [
            "roles.view",
            "roles.create",
            "roles.edit",
            "roles.delete",
            "projects.view",
            "users.delete",
            "users.*",
            "*",
        ]
        perms = {}
        for code in perm_codes:
            existing = await db.scalar(select(Permission).where(Permission.code == code))
            if not existing:
                action = code.split(".")[-1] if "." in code else code
                module = code.split(".")[0] if "." in code else "*"
                p = Permission(
                    module=module,
                    action=action,
                    code=code,
                    description=f"{action} permission for {module}",
                )
                db.add(p)
                await db.flush()
                perms[code] = p
            else:
                perms[code] = existing

        # 3. Roles in Company A
        role_noperms = Role(company_id=comp_a.id, name=f"ae_noperms_{uid}", display_name=f"No perms {uid}")
        role_view = Role(company_id=comp_a.id, name=f"ae_view_{uid}", display_name=f"Roles View {uid}")
        role_create = Role(company_id=comp_a.id, name=f"ae_create_{uid}", display_name=f"Roles Create {uid}")
        role_edit = Role(company_id=comp_a.id, name=f"ae_edit_{uid}", display_name=f"Roles Edit {uid}")
        role_delete = Role(company_id=comp_a.id, name=f"ae_delete_{uid}", display_name=f"Roles Delete {uid}")
        role_full = Role(company_id=comp_a.id, name=f"ae_full_{uid}", display_name=f"Roles Full {uid}")

        # Roles in Company B
        role_b_custom = Role(company_id=comp_b.id, name=f"ae_compb_{uid}", display_name=f"Comp B Role {uid}")

        db.add_all([
            role_noperms, role_view, role_create, role_edit, role_delete, role_full, role_b_custom
        ])
        await db.flush()

        # Assign perms to roles in DB
        db.add(RolePermission(role=role_view.name, role_id=role_view.id, permission_id=perms["roles.view"].id))
        db.add(RolePermission(role=role_create.name, role_id=role_create.id, permission_id=perms["roles.create"].id))

        # role_edit gets roles.view, roles.edit, and projects.view (to test privilege boundary)
        db.add(RolePermission(role=role_edit.name, role_id=role_edit.id, permission_id=perms["roles.view"].id))
        db.add(RolePermission(role=role_edit.name, role_id=role_edit.id, permission_id=perms["roles.edit"].id))
        db.add(RolePermission(role=role_edit.name, role_id=role_edit.id, permission_id=perms["projects.view"].id))

        db.add(RolePermission(role=role_delete.name, role_id=role_delete.id, permission_id=perms["roles.delete"].id))

        for c in ["roles.view", "roles.create", "roles.edit", "roles.delete", "projects.view"]:
            db.add(RolePermission(role=role_full.name, role_id=role_full.id, permission_id=perms[c].id))

        await db.flush()

        # 4. Users in Company A
        user_noperms = User(
            company_id=comp_a.id,
            email=f"ae_noperms_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"No Perms {uid}",
            mobile=f"81{uuid.uuid4().int % 100000000:08d}",
            role=role_noperms.name,
            is_active=True,
            is_super_admin=False,
        )
        user_viewer = User(
            company_id=comp_a.id,
            email=f"ae_viewer_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Viewer {uid}",
            mobile=f"82{uuid.uuid4().int % 100000000:08d}",
            role=role_view.name,
            is_active=True,
            is_super_admin=False,
        )
        user_creator = User(
            company_id=comp_a.id,
            email=f"ae_creator_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Creator {uid}",
            mobile=f"83{uuid.uuid4().int % 100000000:08d}",
            role=role_create.name,
            is_active=True,
            is_super_admin=False,
        )
        user_editor = User(
            company_id=comp_a.id,
            email=f"ae_editor_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Editor {uid}",
            mobile=f"84{uuid.uuid4().int % 100000000:08d}",
            role=role_edit.name,
            is_active=True,
            is_super_admin=False,
        )
        user_deleter = User(
            company_id=comp_a.id,
            email=f"ae_deleter_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Deleter {uid}",
            mobile=f"85{uuid.uuid4().int % 100000000:08d}",
            role=role_delete.name,
            is_active=True,
            is_super_admin=False,
        )
        user_admin_a = User(
            company_id=comp_a.id,
            email=f"ae_admin_a_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Admin A {uid}",
            mobile=f"86{uuid.uuid4().int % 100000000:08d}",
            role=role_full.name,
            is_active=True,
            is_super_admin=False,
        )
        # Target user to modify overrides in Company A
        target_a = User(
            company_id=comp_a.id,
            email=f"ae_target_a_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Target A {uid}",
            mobile=f"87{uuid.uuid4().int % 100000000:08d}",
            role=role_noperms.name,
            is_active=True,
            is_super_admin=False,
        )

        # 5. Users in Company B
        user_b = User(
            company_id=comp_b.id,
            email=f"ae_user_b_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"User B {uid}",
            mobile=f"88{uuid.uuid4().int % 100000000:08d}",
            role=role_b_custom.name,
            is_active=True,
            is_super_admin=False,
        )

        # 6. Tenantless non-SA user
        user_tenantless = User(
            company_id=None,
            email=f"ae_tenantless_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Tenantless {uid}",
            mobile=f"89{uuid.uuid4().int % 100000000:08d}",
            role=role_full.name,
            is_active=True,
            is_super_admin=False,
        )

        # 7. Super Admin
        user_sa = User(
            company_id=None,
            email=f"ae_sa_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Super Admin {uid}",
            mobile=f"80{uuid.uuid4().int % 100000000:08d}",
            role="Admin",
            is_active=True,
            is_super_admin=True,
        )

        db.add_all([
            user_noperms, user_viewer, user_creator, user_editor, user_deleter,
            user_admin_a, target_a, user_b, user_tenantless, user_sa,
        ])
        await db.commit()

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "perms": perms,
            "roles": {
                "noperms": role_noperms,
                "view": role_view,
                "create": role_create,
                "edit": role_edit,
                "delete": role_delete,
                "full": role_full,
                "b_custom": role_b_custom,
            },
            "users": {
                "noperms": user_noperms,
                "viewer": user_viewer,
                "creator": user_creator,
                "editor": user_editor,
                "deleter": user_deleter,
                "admin_a": user_admin_a,
                "target_a": target_a,
                "user_b": user_b,
                "tenantless": user_tenantless,
                "sa": user_sa,
            },
            "uid": uid,
        }

    try:
        yield data
    finally:
        # Cleanup
        async with AsyncSessionLocal() as clean_db:
            await clean_db.execute(delete(UserPermissionOverride).where(
                UserPermissionOverride.user_id.in_([u.id for u in data["users"].values()])
            ))
            await clean_db.execute(delete(RolePermission).where(
                RolePermission.role_id.in_([r.id for r in data["roles"].values()])
            ))
            await clean_db.execute(delete(User).where(
                User.id.in_([u.id for u in data["users"].values()])
            ))
            await clean_db.execute(delete(Role).where(
                Role.id.in_([r.id for r in data["roles"].values()])
            ))
            await clean_db.execute(delete(Company).where(
                Company.id.in_([data["comp_a"].id, data["comp_b"].id])
            ))
            await clean_db.commit()


# ==============================================================================
# TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_tc01_unauthenticated_requests():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/v1/rbac/permissions")
        assert r1.status_code == 401
        r2 = await client.get("/api/v1/rbac/roles")
        assert r2.status_code == 401
        r3 = await client.post("/api/v1/rbac/roles", json={"name": "test", "display_name": "Test"})
        assert r3.status_code == 401


@pytest.mark.asyncio
async def test_tc02_authenticated_user_no_permissions():
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["noperms"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/v1/rbac/permissions", headers=_auth(tok))
            assert r1.status_code == 403

            r2 = await client.get("/api/v1/rbac/roles", headers=_auth(tok))
            assert r2.status_code == 403

            r3 = await client.post("/api/v1/rbac/roles", json={"name": "test", "display_name": "Test"}, headers=_auth(tok))
            assert r3.status_code == 403

            r4 = await client.delete("/api/v1/rbac/roles/some_role", headers=_auth(tok))
            assert r4.status_code == 403

            r5 = await client.get(f"/api/v1/rbac/users/{d['users']['target_a'].id}/overrides", headers=_auth(tok))
            assert r5.status_code == 403


@pytest.mark.asyncio
async def test_tc03_roles_view_permission():
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. GET /rbac/permissions -> 200
            r1 = await client.get("/api/v1/rbac/permissions", headers=_auth(tok))
            assert r1.status_code == 200
            assert "roles" in r1.json()

            # 2. GET /rbac/roles -> 200
            r2 = await client.get("/api/v1/rbac/roles", headers=_auth(tok))
            assert r2.status_code == 200
            assert "roles" in r2.json()

            # 3. GET /rbac/roles/{role}/permissions -> 200
            r3 = await client.get("/api/v1/rbac/roles/SiteEngineer/permissions", headers=_auth(tok))
            assert r3.status_code == 200
            assert "permissions" in r3.json()

            # 4. GET /rbac/users/{id}/overrides -> 200
            r4 = await client.get(f"/api/v1/rbac/users/{d['users']['target_a'].id}/overrides", headers=_auth(tok))
            assert r4.status_code == 200
            assert "overrides" in r4.json()

            # 5. GET /rbac/audit-logs -> 200
            r5 = await client.get("/api/v1/rbac/audit-logs", headers=_auth(tok))
            assert r5.status_code == 200
            assert "items" in r5.json()

            # But viewer cannot mutate
            r_post = await client.post("/api/v1/rbac/roles", json={"name": "test", "display_name": "Test"}, headers=_auth(tok))
            assert r_post.status_code == 403


@pytest.mark.asyncio
async def test_tc04_roles_create_permission():
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["creator"].id)
        role_name = f"new_custom_{d['uid']}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/v1/rbac/roles",
                json={"name": role_name, "display_name": "New Custom Role", "description": "Desc"},
                headers=_auth(tok),
            )
            assert res.status_code == 200
            data = res.json()
            assert data["role"]["name"] == role_name
            assert data["role"]["company_id"] == d["comp_a"].id


@pytest.mark.asyncio
async def test_tc05_roles_create_cannot_edit_permissions():
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["creator"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/v1/rbac/roles/SiteEngineer/permissions",
                json={"permission": "projects.view"},
                headers=_auth(tok),
            )
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_tc06_roles_edit_permission_and_privilege_boundary():
    async with setup_batch_ae_data() as d:
        # user_editor has roles.view, roles.edit, and projects.view
        tok = _tok(d["users"]["editor"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Allowed to assign projects.view (caller possesses it)
            res1 = await client.post(
                "/api/v1/rbac/roles/SiteEngineer/permissions",
                json={"permission": "projects.view"},
                headers=_auth(tok),
            )
            assert res1.status_code == 200
            assert "projects.view" in res1.json()["permissions"]

            # 2. Allowed to delete that permission
            res2 = await client.delete(
                "/api/v1/rbac/roles/SiteEngineer/permissions/projects.view",
                headers=_auth(tok),
            )
            assert res2.status_code == 200
            assert "projects.view" not in res2.json()["permissions"]

            # 3. Allowed to reset defaults
            res3 = await client.post(
                "/api/v1/rbac/roles/SiteEngineer/reset-defaults",
                headers=_auth(tok),
            )
            assert res3.status_code == 200

            # 4. Allowed to grant projects.view via user override to target_a
            res4 = await client.put(
                f"/api/v1/rbac/users/{d['users']['target_a'].id}/overrides",
                json={"overrides": [{"permission": "projects.view", "is_granted": True}]},
                headers=_auth(tok),
            )
            assert res4.status_code == 200


@pytest.mark.asyncio
async def test_tc07_roles_delete_permission():
    async with setup_batch_ae_data() as d:
        tok_create = _tok(d["users"]["admin_a"].id)
        tok_delete = _tok(d["users"]["deleter"].id)
        role_to_delete = f"del_role_{d['uid']}"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Create the role first
            c_res = await client.post(
                "/api/v1/rbac/roles",
                json={"name": role_to_delete, "display_name": "To Delete"},
                headers=_auth(tok_create),
            )
            assert c_res.status_code == 200

            # Delete with user_deleter
            d_res = await client.delete(
                f"/api/v1/rbac/roles/{role_to_delete}",
                headers=_auth(tok_delete),
            )
            assert d_res.status_code == 200
            assert d_res.json()["role"] == role_to_delete


@pytest.mark.asyncio
async def test_tc08_dynamic_permission_grant_and_revoke():
    async with setup_batch_ae_data() as d:
        target_user = d["users"]["noperms"]
        tok = _tok(target_user.id)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Initially blocked
            r1 = await client.get("/api/v1/rbac/roles", headers=_auth(tok))
            assert r1.status_code == 403

            # Dynamically grant roles.view via UserPermissionOverride in DB
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(
                    user_id=target_user.id,
                    permission_id=d["perms"]["roles.view"].id,
                    is_granted=True,
                ))
                await db.commit()

            # Next request is immediately allowed
            r2 = await client.get("/api/v1/rbac/roles", headers=_auth(tok))
            assert r2.status_code == 200

            # Dynamically revoke via override
            async with AsyncSessionLocal() as db:
                await db.execute(delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == target_user.id
                ))
                await db.commit()

            # Next request is immediately blocked
            r3 = await client.get("/api/v1/rbac/roles", headers=_auth(tok))
            assert r3.status_code == 403


@pytest.mark.asyncio
async def test_tc09_tenant_isolation_roles():
    async with setup_batch_ae_data() as d:
        tok_a = _tok(d["users"]["admin_a"].id)
        comp_b_role_name = d["roles"]["b_custom"].name

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Company A list roles does not include Company B custom role
            r_list = await client.get("/api/v1/rbac/roles", headers=_auth(tok_a))
            assert r_list.status_code == 200
            role_names = [d_info["name"] for d_info in r_list.json()["details"]]
            assert comp_b_role_name not in role_names

            # 2. Company A cannot view Company B custom role permissions -> 404
            r_get = await client.get(f"/api/v1/rbac/roles/{comp_b_role_name}/permissions", headers=_auth(tok_a))
            assert r_get.status_code == 404

            # 3. Company A cannot delete Company B custom role -> 404
            r_del = await client.delete(f"/api/v1/rbac/roles/{comp_b_role_name}", headers=_auth(tok_a))
            assert r_del.status_code == 404


@pytest.mark.asyncio
async def test_tc10_tenant_isolation_overrides():
    async with setup_batch_ae_data() as d:
        tok_a = _tok(d["users"]["admin_a"].id)
        comp_b_user_id = d["users"]["user_b"].id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Company A cannot read Company B user overrides -> 404
            r_get = await client.get(f"/api/v1/rbac/users/{comp_b_user_id}/overrides", headers=_auth(tok_a))
            assert r_get.status_code == 404

            # 2. Company A cannot update Company B user overrides -> 404
            r_put = await client.put(
                f"/api/v1/rbac/users/{comp_b_user_id}/overrides",
                json={"overrides": [{"permission": "projects.view", "is_granted": True}]},
                headers=_auth(tok_a),
            )
            assert r_put.status_code == 404


@pytest.mark.asyncio
async def test_tc11_tenantless_non_sa_rejected():
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["tenantless"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/v1/rbac/permissions", headers=_auth(tok))
            assert r1.status_code == 403
            detail_msg = r1.json()["detail"].lower()
            assert "company" in detail_msg or "tenant" in detail_msg

            r2 = await client.get("/api/v1/rbac/roles", headers=_auth(tok))
            assert r2.status_code == 403

            r3 = await client.post("/api/v1/rbac/roles", json={"name": "test", "display_name": "Test"}, headers=_auth(tok))
            assert r3.status_code == 403


@pytest.mark.asyncio
async def test_tc12_idor_nonexistent_role_404():
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["admin_a"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/rbac/roles/NonExistentRole999/permissions", headers=_auth(tok))
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_tc13_privilege_escalation_role_permissions():
    """
    CRITICAL P0: A non-SA caller CANNOT assign permissions that they themselves do not possess.
    user_editor only has roles.view, roles.edit, and projects.view.
    """
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["editor"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Attempt to assign global wildcard '*' -> 403
            r1 = await client.post(
                "/api/v1/rbac/roles/SiteEngineer/permissions",
                json={"permission": "*"},
                headers=_auth(tok),
            )
            assert r1.status_code == 403
            assert "Cannot grant wildcard" in r1.json()["detail"]

            # 2. Attempt to assign module wildcard 'users.*' -> 403
            r2 = await client.post(
                "/api/v1/rbac/roles/SiteEngineer/permissions",
                json={"permission": "users.*"},
                headers=_auth(tok),
            )
            assert r2.status_code == 403
            assert "Cannot grant wildcard" in r2.json()["detail"]

            # 3. Attempt to assign discrete unpossessed permission 'users.delete' -> 403
            r3 = await client.post(
                "/api/v1/rbac/roles/SiteEngineer/permissions",
                json={"permission": "users.delete"},
                headers=_auth(tok),
            )
            assert r3.status_code == 403
            assert "outside your effective permissions boundary" in r3.json()["detail"]

            # 4. Attempt via PUT (replacement) with mixed valid + invalid perms -> 403 before any mutation
            r4 = await client.put(
                "/api/v1/rbac/roles/SiteEngineer/permissions",
                json={"permissions": ["projects.view", "users.delete"]},
                headers=_auth(tok),
            )
            assert r4.status_code == 403
            assert "outside your effective permissions boundary" in r4.json()["detail"]


@pytest.mark.asyncio
async def test_tc14_privilege_escalation_user_overrides():
    """
    CRITICAL P0: A non-SA caller CANNOT grant unpossessed permissions to another user.
    """
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["editor"].id)
        target_id = d["users"]["target_a"].id
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Attempt to grant users.delete to target_a -> 403
            res = await client.put(
                f"/api/v1/rbac/users/{target_id}/overrides",
                json={"overrides": [{"permission": "users.delete", "is_granted": True}]},
                headers=_auth(tok),
            )
            assert res.status_code == 403
            assert "outside your effective permissions boundary" in res.json()["detail"]


@pytest.mark.asyncio
async def test_tc15_self_override_protection():
    async with setup_batch_ae_data() as d:
        editor_id = d["users"]["editor"].id
        tok = _tok(editor_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.put(
                f"/api/v1/rbac/users/{editor_id}/overrides",
                json={"overrides": [{"permission": "projects.view", "is_granted": True}]},
                headers=_auth(tok),
            )
            assert res.status_code == 403
            assert "Admins cannot modify their own permission overrides" in res.json()["detail"]


@pytest.mark.asyncio
async def test_tc16_builtin_role_deletion_protection():
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["admin_a"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for role in ["Admin", "SiteEngineer", "Labour", "Client"]:
                res = await client.delete(f"/api/v1/rbac/roles/{role}", headers=_auth(tok))
                assert res.status_code == 400
                assert "Cannot delete built-in system role" in res.json()["detail"]


@pytest.mark.asyncio
async def test_tc17_builtin_role_customization_preserves_global_template():
    async with setup_batch_ae_data() as d:
        tok = _tok(d["users"]["admin_a"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Customize Labour role for Company A
            res = await client.post(
                "/api/v1/rbac/roles/Labour/permissions",
                json={"permission": "projects.view"},
                headers=_auth(tok),
            )
            assert res.status_code == 200

            # Verify in DB: global Labour Role has NOT been mutated
            async with AsyncSessionLocal() as db:
                company_labour = await db.scalar(
                    select(Role).where(Role.name == "Labour", Role.company_id == d["comp_a"].id)
                )
                assert company_labour is not None
                assert company_labour.is_system is False

                # Global Labour role (if exists) has company_id IS NULL
                global_labour = await db.scalar(
                    select(Role).where(Role.name == "Labour", Role.company_id.is_(None))
                )
                if global_labour:
                    assert global_labour.is_system is True

            # Clean up by resetting defaults
            reset_res = await client.post("/api/v1/rbac/roles/Labour/reset-defaults", headers=_auth(tok))
            assert reset_res.status_code == 200


@pytest.mark.asyncio
async def test_tc18_super_admin_maintenance_endpoints():
    async with setup_batch_ae_data() as d:
        tok_sa = _tok(d["users"]["sa"].id)
        tok_non_sa = _tok(d["users"]["admin_a"].id)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Non-SA calling /seed -> 403
            r_seed_denied = await client.post("/api/v1/rbac/seed", headers=_auth(tok_non_sa))
            assert r_seed_denied.status_code == 403

            # 2. Non-SA calling /assign-defaults -> 403
            r_def_denied = await client.post("/api/v1/rbac/assign-defaults", headers=_auth(tok_non_sa))
            assert r_def_denied.status_code == 403

            # 3. SA calling /seed -> 200
            r_seed_ok = await client.post("/api/v1/rbac/seed", headers=_auth(tok_sa))
            assert r_seed_ok.status_code == 200

            # 4. SA calling /assign-defaults -> 200
            r_def_ok = await client.post("/api/v1/rbac/assign-defaults", headers=_auth(tok_sa))
            assert r_def_ok.status_code == 200


@pytest.mark.asyncio
async def test_tc19_audit_logs_tenant_scoped():
    async with setup_batch_ae_data() as d:
        tok_a = _tok(d["users"]["admin_a"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/v1/rbac/audit-logs", headers=_auth(tok_a))
            assert res.status_code == 200
            items = res.json()["items"]
            # Every returned item must be scoped to Company A
            for it in items:
                assert it["company_id"] == d["comp_a"].id


@pytest.mark.asyncio
async def test_tc20_public_route_preservation():
    """
    Verifies that all 15 endpoints exist on rbac_router with exact HTTP method and path.
    """
    expected_routes = {
        ("GET", "/rbac/permissions"),
        ("GET", "/rbac/roles"),
        ("POST", "/rbac/roles"),
        ("DELETE", "/rbac/roles/{role}"),
        ("GET", "/rbac/roles/{role}/permissions"),
        ("POST", "/rbac/roles/{role}/permissions"),
        ("PUT", "/rbac/roles/{role}/permissions"),
        ("DELETE", "/rbac/roles/{role}/permissions/{permission}"),
        ("DELETE", "/rbac/roles/{role}/permissions"),
        ("POST", "/rbac/roles/{role}/reset-defaults"),
        ("GET", "/rbac/users/{user_id}/overrides"),
        ("PUT", "/rbac/users/{user_id}/overrides"),
        ("POST", "/rbac/seed"),
        ("POST", "/rbac/assign-defaults"),
        ("GET", "/rbac/audit-logs"),
    }

    actual_routes = set()
    for route in rbac_router.routes:
        for method in route.methods:
            actual_routes.add((method, route.path))

    assert actual_routes == expected_routes
    assert len(actual_routes) == 15
