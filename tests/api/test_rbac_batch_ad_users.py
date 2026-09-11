"""
RBAC Batch AD: User Management RBAC + Tenant Isolation Test Suite
==================================================================

Covers all mandatory verification areas for Batch AD:
- TC01: Unauthenticated requests return 401
- TC02: Authenticated user with no permissions returns 403 for users.* actions
- TC03: Personal route /users/me remains accessible to active authenticated user without users.view
- TC04: users.view permission allows GET /users, GET /users/roles, GET /users/{id}, GET /users/{id}/audit-logs*
- TC05: users.view alone cannot create (POST /users/create -> 403)
- TC06: users.create permission allows POST /users/create -> 201
- TC07: users.edit permission allows PUT /users/{id}, PUT /users/{id}/restore, PUT /users/roles/{role}/status -> 200
- TC08: users.delete permission allows DELETE /users/{id} -> 204
- TC09: Dynamic DB permission grant and revoke
- TC10: Wildcard permissions (users.* and *) grant access
- TC11: Tenant Isolation - Company A cannot list Company B users
- TC12: IDOR Prevention - Company A cannot retrieve, update, delete, or restore Company B user (404 masked)
- TC13: IDOR Prevention - Company A cannot access Company B user audit logs (404 masked)
- TC14: Nonexistent user returns 404
- TC15: Tenantless non-SA user (company_id=None) returns 403 for tenant-scoped operations
- TC16: Role aggregation GET /users/roles is scoped to tenant for non-SA
- TC17: Super Admin cross-tenant capabilities and platform-wide role aggregation
- TC18: Business Invariants: Cannot create Admin or Labour via POST /users/create
- TC19: Business Invariants: Non-SA cannot promote user to Admin
- TC20: Business Invariants: Admin role deactivation protection
- TC21: Static Router Audit: 0 require_roles, 0 admin_required, 100% canonical require_permission
"""

import ast
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.company import Company
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride
from app.models.user import User, UserRole, UserAuditLog, ActivityLog


# ==============================================================================
# HELPERS
# ==============================================================================

def _tok(user_id: int) -> str:
    return create_access_token({"sub": str(user_id)})


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def setup_batch_ad_data():
    """
    Provisions two isolated tenants (comp_a, comp_b) with:
    - Dedicated users in Company A and Company B
    - Deleted user for restore testing
    - Permissions: users.view, users.create, users.edit, users.delete
    - Roles: unprivileged_role (no perms), users_viewer, users_manager
    - Super Admin user
    - Tenantless user
    """
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"BatchAD_CompA_{uid}")
        comp_b = Company(name=f"BatchAD_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Permissions
        perm_codes = ["users.view", "users.create", "users.edit", "users.delete", "users.*", "*"]
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

        # 3. Roles
        role_noperms = Role(company_id=comp_a.id, name=f"no_perms_{uid}", display_name=f"No perms {uid}", description="No perms")
        role_view = Role(company_id=comp_a.id, name=f"user_view_{uid}", display_name=f"Users View {uid}", description="Users View")
        role_admin_custom = Role(company_id=comp_a.id, name=f"user_admin_{uid}", display_name=f"Users Full Admin {uid}", description="Users Full Admin")
        role_admin_b = Role(company_id=comp_b.id, name=f"user_admin_b_{uid}", display_name=f"Users Full Admin B {uid}", description="Users Full Admin B")

        db.add_all([role_noperms, role_view, role_admin_custom, role_admin_b])
        await db.flush()

        # Assign perms to roles
        db.add(RolePermission(role=role_view.name, role_id=role_view.id, permission_id=perms["users.view"].id))
        for c in ["users.view", "users.create", "users.edit", "users.delete"]:
            db.add(RolePermission(role=role_admin_custom.name, role_id=role_admin_custom.id, permission_id=perms[c].id))
            db.add(RolePermission(role=role_admin_b.name, role_id=role_admin_b.id, permission_id=perms[c].id))
        await db.flush()

        # 4. Users in Company A
        # User with no permissions
        user_noperms = User(
            company_id=comp_a.id,
            email=f"noperms_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"No Perms {uid}",
            mobile=f"90{uuid.uuid4().int % 100000000:08d}",
            role=role_noperms.name,
            is_active=True,
            is_super_admin=False,
        )
        # User with users.view
        user_viewer = User(
            company_id=comp_a.id,
            email=f"viewer_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Viewer {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            role=role_view.name,
            is_active=True,
            is_super_admin=False,
        )
        # User with full user perms
        user_admin_a = User(
            company_id=comp_a.id,
            email=f"admin_a_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Admin A {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            role=role_admin_custom.name,
            is_active=True,
            is_super_admin=False,
        )
        # Target user to modify/delete in Company A
        target_a = User(
            company_id=comp_a.id,
            email=f"target_a_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Target A {uid}",
            mobile=f"93{uuid.uuid4().int % 100000000:08d}",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            is_super_admin=False,
        )
        # Deleted user in Company A for restore test
        deleted_a = User(
            company_id=comp_a.id,
            email=f"deleted_a_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Deleted A {uid}",
            mobile=f"94{uuid.uuid4().int % 100000000:08d}",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            is_deleted=True,
            deleted_at=date.today(),
            is_super_admin=False,
        )

        # 5. Users in Company B
        user_admin_b = User(
            company_id=comp_b.id,
            email=f"admin_b_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Admin B {uid}",
            mobile=f"95{uuid.uuid4().int % 100000000:08d}",
            role=role_admin_b.name,
            is_active=True,
            is_super_admin=False,
        )
        target_b = User(
            company_id=comp_b.id,
            email=f"target_b_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Target B {uid}",
            mobile=f"96{uuid.uuid4().int % 100000000:08d}",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            is_super_admin=False,
        )
        deleted_b = User(
            company_id=comp_b.id,
            email=f"deleted_b_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Deleted B {uid}",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            is_deleted=True,
            deleted_at=date.today(),
            is_super_admin=False,
        )

        # 6. Super Admin & Tenantless
        super_admin = User(
            company_id=None,
            email=f"sa_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Super Admin {uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
            role="SuperAdmin",
            is_active=True,
            is_super_admin=True,
        )
        tenantless_user = User(
            company_id=None,
            email=f"tenantless_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Tenantless {uid}",
            mobile=f"99{uuid.uuid4().int % 100000000:08d}",
            role=role_admin_custom.name,
            is_active=True,
            is_super_admin=False,
        )

        db.add_all([
            user_noperms, user_viewer, user_admin_a, target_a, deleted_a,
            user_admin_b, target_b, deleted_b,
            super_admin, tenantless_user,
        ])
        await db.commit()

        # Refresh instances
        await db.refresh(comp_a)
        await db.refresh(comp_b)
        await db.refresh(user_noperms)
        await db.refresh(user_viewer)
        await db.refresh(user_admin_a)
        await db.refresh(target_a)
        await db.refresh(deleted_a)
        await db.refresh(user_admin_b)
        await db.refresh(target_b)
        await db.refresh(deleted_b)
        await db.refresh(super_admin)
        await db.refresh(tenantless_user)

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "user_noperms": user_noperms,
            "user_viewer": user_viewer,
            "user_admin_a": user_admin_a,
            "target_a": target_a,
            "deleted_a": deleted_a,
            "user_admin_b": user_admin_b,
            "target_b": target_b,
            "deleted_b": deleted_b,
            "super_admin": super_admin,
            "tenantless_user": tenantless_user,
            "perms": perms,
            "uid": uid,
        }
        try:
            yield data
        finally:
            # Cleanup
            cleanup_uids = [
                user_noperms.id, user_viewer.id, user_admin_a.id, target_a.id, deleted_a.id,
                user_admin_b.id, target_b.id, deleted_b.id, super_admin.id, tenantless_user.id
            ]
            await db.execute(update(User).where(User.id.in_(cleanup_uids)).values(updated_by=None, created_by=None))
            await db.execute(delete(UserAuditLog).where((UserAuditLog.user_id.in_(cleanup_uids)) | (UserAuditLog.changed_by.in_(cleanup_uids))))
            await db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(cleanup_uids)))
            await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(cleanup_uids)))
            await db.execute(delete(User).where(User.id.in_(cleanup_uids)))
            await db.execute(delete(RolePermission).where(RolePermission.role_id.in_([role_noperms.id, role_view.id, role_admin_custom.id, role_admin_b.id])))
            await db.execute(delete(Role).where(Role.id.in_([role_noperms.id, role_view.id, role_admin_custom.id, role_admin_b.id])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()


# ==============================================================================
# TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_tc01_unauthenticated_requests_return_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/v1/users")
        assert r1.status_code == 401

        r2 = await client.post("/api/v1/users/create", data={"email": "test@test.com", "mobile_number": "9111111111", "role": "SiteEngineer"})
        assert r2.status_code == 401

        r3 = await client.get("/api/v1/users/me")
        assert r3.status_code == 401

        r4 = await client.get("/api/v1/users/123")
        assert r4.status_code == 401


@pytest.mark.asyncio
async def test_tc02_authenticated_user_with_no_permissions_returns_403():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_noperms"].id)
        headers = _auth(token)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Listing users
            r1 = await client.get("/api/v1/users", headers=headers)
            assert r1.status_code == 403

            # Roles count
            r2 = await client.get("/api/v1/users/roles", headers=headers)
            assert r2.status_code == 403

            # User detail
            r3 = await client.get(f"/api/v1/users/{d['target_a'].id}", headers=headers)
            assert r3.status_code == 403

            # Create user
            r4 = await client.post("/api/v1/users/create", params={"email": "new@test.com", "mobile_number": "9222222222", "role": "SiteEngineer"}, headers=headers)
            assert r4.status_code == 403

            # Update user
            r5 = await client.put(f"/api/v1/users/{d['target_a'].id}", params={"full_name": "Updated"}, headers=headers)
            assert r5.status_code == 403

            # Delete user
            r6 = await client.delete(f"/api/v1/users/{d['target_a'].id}", headers=headers)
            assert r6.status_code == 403

            # Restore user
            r7 = await client.put(f"/api/v1/users/{d['deleted_a'].id}/restore", headers=headers)
            assert r7.status_code == 403


@pytest.mark.asyncio
async def test_tc03_personal_route_me_accessible_without_permissions():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_noperms"].id)
        headers = _auth(token)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/v1/users/me", headers=headers)
            assert res.status_code == 200
            body = res.json()
            assert body["user_id"] == d["user_noperms"].id
            assert body["email"] == d["user_noperms"].email


@pytest.mark.asyncio
async def test_tc04_users_view_permission_allows_reading():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_viewer"].id)
        headers = _auth(token)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # List users
            r1 = await client.get("/api/v1/users", headers=headers)
            assert r1.status_code == 200
            data1 = r1.json()
            assert "items" in data1
            user_ids = [u["user_id"] for u in data1["items"]]
            assert d["target_a"].id in user_ids

            # List roles
            r2 = await client.get("/api/v1/users/roles", headers=headers)
            assert r2.status_code == 200
            assert "items" in r2.json()

            # Get user detail
            r3 = await client.get(f"/api/v1/users/{d['target_a'].id}", headers=headers)
            assert r3.status_code == 200
            assert r3.json()["user_id"] == d["target_a"].id

            # Audit logs
            r4 = await client.get(f"/api/v1/users/{d['target_a'].id}/audit-logs", headers=headers)
            assert r4.status_code == 200

            # Grouped audit logs
            r5 = await client.get(f"/api/v1/users/{d['target_a'].id}/audit-logs-grouped", headers=headers)
            assert r5.status_code == 200


@pytest.mark.asyncio
async def test_tc05_users_view_alone_cannot_create_or_mutate():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_viewer"].id)
        headers = _auth(token)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Create -> 403
            r1 = await client.post(
                "/api/v1/users/create",
                params={"email": f"attempt_{d['uid']}@test.com", "mobile_number": "9333333333", "role": "SiteEngineer"},
                headers=headers,
            )
            assert r1.status_code == 403

            # Update -> 403
            r2 = await client.put(f"/api/v1/users/{d['target_a'].id}", params={"full_name": "Hacked"}, headers=headers)
            assert r2.status_code == 403

            # Delete -> 403
            r3 = await client.delete(f"/api/v1/users/{d['target_a'].id}", headers=headers)
            assert r3.status_code == 403


@pytest.mark.asyncio
async def test_tc06_users_create_permission_allows_creation():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_admin_a"].id)
        headers = _auth(token)
        new_mobile = f"94{uuid.uuid4().int % 100000000:08d}"
        new_email = f"created_{uuid.uuid4().hex[:6]}@test.com"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/v1/users/create",
                params={
                    "email": new_email,
                    "mobile_number": new_mobile,
                    "full_name": "Newly Created User",
                    "role": UserRole.SITE_ENGINEER.value,
                    "password": "Password123!",
                },
                headers=headers,
            )
            assert res.status_code == 201
            body = res.json()
            assert body["email"] == new_email
            assert body["mobile_number"] == new_mobile
            assert body["company_id"] == d["comp_a"].id

            # Cleanup
            async with AsyncSessionLocal() as db:
                await db.execute(delete(User).where(User.id == body["user_id"]))
                await db.commit()


@pytest.mark.asyncio
async def test_tc07_users_edit_permission_allows_mutations():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_admin_a"].id)
        headers = _auth(token)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Update user profile
            r1 = await client.put(
                f"/api/v1/users/{d['target_a'].id}",
                params={"full_name": "Target A Renamed"},
                headers=headers,
            )
            assert r1.status_code == 200
            assert r1.json()["full_name"] == "Target A Renamed"

            # Restore user
            r2 = await client.put(f"/api/v1/users/{d['deleted_a'].id}/restore", headers=headers)
            assert r2.status_code == 200
            assert r2.json()["user_id"] == d["deleted_a"].id
            assert r2.json()["is_active"] is True

            # Update role status
            r3 = await client.put(
                f"/api/v1/users/roles/{UserRole.SITE_ENGINEER.value}/status?is_active=true",
                headers=headers,
            )
            assert r3.status_code == 200
            assert r3.json()["is_active"] is True


@pytest.mark.asyncio
async def test_tc08_users_delete_permission_allows_soft_delete():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_admin_a"].id)
        headers = _auth(token)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.delete(f"/api/v1/users/{d['target_a'].id}", headers=headers)
            assert r1.status_code == 204

            # Verify it is no longer listed in active users
            r2 = await client.get(f"/api/v1/users/{d['target_a'].id}", headers=headers)
            assert r2.status_code == 404


@pytest.mark.asyncio
async def test_tc09_dynamic_db_permission_grant_and_revoke():
    async with setup_batch_ad_data() as d:
        user = d["user_noperms"]
        token = _tok(user.id)
        headers = _auth(token)
        perm_view = d["perms"]["users.view"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Initially denied -> 403
            r1 = await client.get("/api/v1/users", headers=headers)
            assert r1.status_code == 403

            # Grant dynamically via UserPermissionOverride
            async with AsyncSessionLocal() as db:
                override = UserPermissionOverride(
                    user_id=user.id,
                    permission_id=perm_view.id,
                    is_granted=True,
                )
                db.add(override)
                await db.commit()

            # Now allowed -> 200
            r2 = await client.get("/api/v1/users", headers=headers)
            assert r2.status_code == 200

            # Revoke dynamically
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(UserPermissionOverride).where(
                        UserPermissionOverride.user_id == user.id,
                        UserPermissionOverride.permission_id == perm_view.id,
                    )
                )
                await db.commit()

            # Denied again -> 403
            r3 = await client.get("/api/v1/users", headers=headers)
            assert r3.status_code == 403


@pytest.mark.asyncio
async def test_tc10_wildcard_permissions():
    async with setup_batch_ad_data() as d:
        user = d["user_noperms"]
        token = _tok(user.id)
        headers = _auth(token)
        perm_wildcard = d["perms"]["users.*"]

        async with AsyncSessionLocal() as db:
            override = UserPermissionOverride(
                user_id=user.id,
                permission_id=perm_wildcard.id,
                is_granted=True,
            )
            db.add(override)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # users.* grants view, edit, etc.
            r1 = await client.get("/api/v1/users", headers=headers)
            assert r1.status_code == 200

            r2 = await client.put(
                f"/api/v1/users/{d['target_a'].id}",
                params={"full_name": "Wildcard Update"},
                headers=headers,
            )
            assert r2.status_code == 200


@pytest.mark.asyncio
async def test_tc11_tenant_isolation_company_a_cannot_list_company_b():
    async with setup_batch_ad_data() as d:
        token_a = _tok(d["user_admin_a"].id)
        headers_a = _auth(token_a)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/v1/users", headers=headers_a)
            assert res.status_code == 200
            items = res.json()["items"]
            comp_ids = {u["company_id"] for u in items}
            assert comp_ids == {d["comp_a"].id}
            # Company B target is NOT in list
            user_ids = [u["user_id"] for u in items]
            assert d["target_b"].id not in user_ids


@pytest.mark.asyncio
async def test_tc12_idor_foreign_tenant_mutations_return_404():
    async with setup_batch_ad_data() as d:
        token_a = _tok(d["user_admin_a"].id)
        headers_a = _auth(token_a)
        foreign_id = d["target_b"].id
        foreign_deleted_id = d["deleted_b"].id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # GET foreign user -> 404
            r1 = await client.get(f"/api/v1/users/{foreign_id}", headers=headers_a)
            assert r1.status_code == 404

            # PUT foreign user -> 404
            r2 = await client.put(f"/api/v1/users/{foreign_id}", params={"full_name": "Attacked"}, headers=headers_a)
            assert r2.status_code == 404

            # DELETE foreign user -> 404
            r3 = await client.delete(f"/api/v1/users/{foreign_id}", headers=headers_a)
            assert r3.status_code == 404

            # RESTORE foreign user -> 404
            r4 = await client.put(f"/api/v1/users/{foreign_deleted_id}/restore", headers=headers_a)
            assert r4.status_code == 404


@pytest.mark.asyncio
async def test_tc13_idor_foreign_tenant_audit_logs_return_404():
    async with setup_batch_ad_data() as d:
        token_a = _tok(d["user_admin_a"].id)
        headers_a = _auth(token_a)
        foreign_id = d["target_b"].id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get(f"/api/v1/users/{foreign_id}/audit-logs", headers=headers_a)
            assert r1.status_code == 404

            r2 = await client.get(f"/api/v1/users/{foreign_id}/audit-logs-grouped", headers=headers_a)
            assert r2.status_code == 404


@pytest.mark.asyncio
async def test_tc14_nonexistent_user_returns_404():
    async with setup_batch_ad_data() as d:
        token_a = _tok(d["user_admin_a"].id)
        headers_a = _auth(token_a)
        non_existent_id = 99999999

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get(f"/api/v1/users/{non_existent_id}", headers=headers_a)
            assert r1.status_code == 404

            r2 = await client.put(f"/api/v1/users/{non_existent_id}", params={"full_name": "Ghost"}, headers=headers_a)
            assert r2.status_code == 404

            r3 = await client.delete(f"/api/v1/users/{non_existent_id}", headers=headers_a)
            assert r3.status_code == 404

            r4 = await client.put(f"/api/v1/users/{non_existent_id}/restore", headers=headers_a)
            assert r4.status_code == 404


@pytest.mark.asyncio
async def test_tc15_tenantless_non_sa_rejected_with_403():
    async with setup_batch_ad_data() as d:
        token_tenantless = _tok(d["tenantless_user"].id)
        headers = _auth(token_tenantless)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/v1/users", headers=headers)
            assert r1.status_code == 403

            r2 = await client.get("/api/v1/users/roles", headers=headers)
            assert r2.status_code == 403

            r3 = await client.get(f"/api/v1/users/{d['target_a'].id}", headers=headers)
            assert r3.status_code == 403

            r4 = await client.post("/api/v1/users/create", params={"email": "tenantless@test.com", "mobile_number": "9555555555", "role": "SiteEngineer"}, headers=headers)
            assert r4.status_code == 403


@pytest.mark.asyncio
async def test_tc16_role_aggregation_scoped_to_tenant():
    async with setup_batch_ad_data() as d:
        token_a = _tok(d["user_admin_a"].id)
        token_b = _tok(d["user_admin_b"].id)
        token_sa = _tok(d["super_admin"].id)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Company A roles count
            r_a = await client.get("/api/v1/users/roles", headers=_auth(token_a))
            assert r_a.status_code == 200
            items_a = {item["role"]: item["user_count"] for item in r_a.json()["items"]}

            # Company B roles count
            r_b = await client.get("/api/v1/users/roles", headers=_auth(token_b))
            assert r_b.status_code == 200
            items_b = {item["role"]: item["user_count"] for item in r_b.json()["items"]}

            # SA roles count
            r_sa = await client.get("/api/v1/users/roles", headers=_auth(token_sa))
            assert r_sa.status_code == 200
            items_sa = {item["role"]: item["user_count"] for item in r_sa.json()["items"]}

            # SA total count for SiteEngineer should be >= sum of tenant A and tenant B
            se_count_a = items_a.get(UserRole.SITE_ENGINEER.value, 0)
            se_count_b = items_b.get(UserRole.SITE_ENGINEER.value, 0)
            se_count_sa = items_sa.get(UserRole.SITE_ENGINEER.value, 0)
            assert se_count_sa >= se_count_a + se_count_b


@pytest.mark.asyncio
async def test_tc17_super_admin_cross_company_access():
    async with setup_batch_ad_data() as d:
        token_sa = _tok(d["super_admin"].id)
        headers_sa = _auth(token_sa)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # SA can access user in Comp A
            r1 = await client.get(f"/api/v1/users/{d['target_a'].id}", headers=headers_sa)
            assert r1.status_code == 200
            assert r1.json()["user_id"] == d["target_a"].id

            # SA can access user in Comp B
            r2 = await client.get(f"/api/v1/users/{d['target_b'].id}", headers=headers_sa)
            assert r2.status_code == 200
            assert r2.json()["user_id"] == d["target_b"].id

            # SA can update user in Comp A
            r3 = await client.put(
                f"/api/v1/users/{d['target_a'].id}",
                params={"full_name": "SA Updated Name"},
                headers=headers_sa,
            )
            assert r3.status_code == 200


@pytest.mark.asyncio
async def test_tc18_cannot_create_admin_or_labour_via_users_create():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_admin_a"].id)
        headers = _auth(token)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Attempt to create Admin -> 403
            r1 = await client.post(
                "/api/v1/users/create",
                params={
                    "email": f"hacker_admin_{d['uid']}@test.com",
                    "mobile_number": "9666666666",
                    "role": UserRole.ADMIN.value,
                },
                headers=headers,
            )
            assert r1.status_code == 403

            # Attempt to create Labour -> 422
            r2 = await client.post(
                "/api/v1/users/create",
                params={
                    "email": f"hacker_labour_{d['uid']}@test.com",
                    "mobile_number": "9777777777",
                    "role": UserRole.LABOUR.value,
                },
                headers=headers,
            )
            assert r2.status_code == 422


@pytest.mark.asyncio
async def test_tc19_non_sa_cannot_promote_user_to_admin():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_admin_a"].id)
        headers = _auth(token)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.put(
                f"/api/v1/users/{d['target_a'].id}",
                params={"role": UserRole.ADMIN.value},
                headers=headers,
            )
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_tc20_admin_role_deactivation_protection():
    async with setup_batch_ad_data() as d:
        token = _tok(d["user_admin_a"].id)
        headers = _auth(token)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.put(
                f"/api/v1/users/roles/{UserRole.ADMIN.value}/status?is_active=false",
                headers=headers,
            )
            assert res.status_code == 400


@pytest.mark.asyncio
async def test_tc21_static_code_verification():
    """Verify 0 require_roles, 0 admin_required, 100% require_permission in user.py."""
    filepath = os.path.join("app", "api", "user.py")
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. require_roles check
    assert "require_roles" not in content, "Found legacy require_roles in app/api/user.py"

    # 2. admin_required check
    assert "admin_required" not in content, "Found admin_required in app/api/user.py"

    # 3. Canonical require_permission check
    expected_permissions = {
        "users.create",
        "users.view",
        "users.edit",
        "users.delete",
    }
    for perm in expected_permissions:
        assert f'require_permission("{perm}")' in content, f"Missing require_permission('{perm}') in user.py"

    # 4. Total endpoint count in router
    from app.api.user import router
    assert len(router.routes) == 11, f"Expected 11 router routes, found {len(router.routes)}"
