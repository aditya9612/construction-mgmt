import pytest
from fastapi import APIRouter, Depends
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete, select

from app.core.dependencies import (
    get_current_active_user,
    require_permission,
    require_permissions,
    require_roles,
)
from app.db.session import get_db_session
from app.main import app
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride
from app.models.user import User, UserRole

# Register test routes to verify exact dependency behavior under real ASGI pipeline
test_rbac_router = APIRouter(prefix="/api/v1/test-rbac-engine", tags=["TestRBAC"])


@test_rbac_router.get("/perm-reports-view")
async def endpoint_reports_view(
    current_user: User = Depends(require_permission("reports.view")),
):
    return {"status": "ok", "user_id": current_user.id}


@test_rbac_router.get("/perm-billing-create")
async def endpoint_billing_create(
    current_user: User = Depends(require_permission("billing.create")),
):
    return {"status": "ok", "user_id": current_user.id}


@test_rbac_router.get("/perm-projects-delete")
async def endpoint_projects_delete(
    current_user: User = Depends(require_permission("projects.delete")),
):
    return {"status": "ok", "user_id": current_user.id}


@test_rbac_router.get("/perm-projects-view")
async def endpoint_projects_view(
    current_user: User = Depends(require_permission("projects.view")),
):
    return {"status": "ok", "user_id": current_user.id}


@test_rbac_router.get("/perm-arbitrary")
async def endpoint_arbitrary(
    current_user: User = Depends(require_permission("custom_module.do_magic")),
):
    return {"status": "ok", "user_id": current_user.id}


@test_rbac_router.get("/multi-perms")
async def endpoint_multi(
    current_user: User = Depends(require_permissions(["projects.view", "tasks.view"])),
):
    return {"status": "ok", "user_id": current_user.id}


@test_rbac_router.get("/legacy-role-only")
async def endpoint_legacy_role(
    current_user: User = Depends(require_roles(["ProjectManager", "Admin"])),
):
    return {"status": "ok", "user_id": current_user.id}


@test_rbac_router.get("/role-with-perm")
async def endpoint_role_with_perm(
    current_user: User = Depends(
        require_roles(
            ["Admin", "ProjectManager"],
            permission="reports.view",
        )
    ),
):
    return {"status": "ok", "user_id": current_user.id}


app.include_router(test_rbac_router)


def get_test_client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


# ==============================================================================
# A. AUTHENTICATION TEST (No token -> 401)
# ==============================================================================
@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401():
    app.dependency_overrides.clear()
    async with get_test_client() as client:
        res = await client.get("/api/v1/test-rbac-engine/perm-reports-view")
        assert res.status_code == 401


# ==============================================================================
# B. DB PERMISSION GRANT & C. DB PERMISSION MISSING
# ==============================================================================
@pytest.mark.asyncio
async def test_db_permission_grant_and_missing():
    async for db in get_db_session():
        # Ensure 'reports.view' permission exists
        perm = await db.scalar(select(Permission).where(Permission.code == "reports.view"))
        if not perm:
            perm = Permission(module="reports", action="view", code="reports.view")
            db.add(perm)
            await db.commit()

        # Create test role & assign permission in DB
        await db.execute(delete(RolePermission).where(RolePermission.role == "TestGrantRole"))
        rp = RolePermission(role="TestGrantRole", permission_id=perm.id)
        db.add(rp)
        await db.commit()

        user = User(
            id=5001,
            email="test_grant@test.com",
            role="TestGrantRole",
            is_active=True,
            company_id=1,
            is_super_admin=False,
        )

        app.dependency_overrides[get_current_active_user] = lambda: user

        async with get_test_client() as client:
            # B. Granted permission in DB -> 200
            res_granted = await client.get("/api/v1/test-rbac-engine/perm-reports-view")
            assert res_granted.status_code == 200

            # C. Missing permission in DB -> 403
            res_missing = await client.get("/api/v1/test-rbac-engine/perm-billing-create")
            assert res_missing.status_code == 403

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == "TestGrantRole"))
        await db.commit()
        app.dependency_overrides.clear()


# ==============================================================================
# D. RUNTIME DYNAMIC CHANGE (Grant -> 200, Remove -> 403, Re-grant -> 200)
# ==============================================================================
@pytest.mark.asyncio
async def test_dynamic_runtime_permission_change_without_restart():
    async for db in get_db_session():
        perm = await db.scalar(select(Permission).where(Permission.code == "reports.view"))
        assert perm is not None

        role_name = "DynamicTestRole"
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        await db.commit()

        user = User(
            id=5002,
            email="dynamic@test.com",
            role=role_name,
            is_active=True,
            company_id=1,
            is_super_admin=False,
        )
        app.dependency_overrides[get_current_active_user] = lambda: user

        async with get_test_client() as client:
            # Step 1: Assign permission -> 200
            db.add(RolePermission(role=role_name, permission_id=perm.id))
            await db.commit()

            res1 = await client.get("/api/v1/test-rbac-engine/perm-reports-view")
            assert res1.status_code == 200

            # Step 2: Remove permission in DB -> 403 immediately without restart
            await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
            await db.commit()

            res2 = await client.get("/api/v1/test-rbac-engine/perm-reports-view")
            assert res2.status_code == 403

            # Step 3: Re-grant permission in DB -> 200 immediately without restart
            db.add(RolePermission(role=role_name, permission_id=perm.id))
            await db.commit()

            res3 = await client.get("/api/v1/test-rbac-engine/perm-reports-view")
            assert res3.status_code == 200

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        await db.commit()
        app.dependency_overrides.clear()


# ==============================================================================
# E & F. CUSTOM ROLE SUPPORT
# ==============================================================================
@pytest.mark.asyncio
async def test_custom_role_authorization():
    async for db in get_db_session():
        perm = await db.scalar(select(Permission).where(Permission.code == "billing.create"))
        assert perm is not None

        custom_role_name = "FinanceManager"
        # Cleanup prior
        await db.execute(delete(RolePermission).where(RolePermission.role == custom_role_name))
        await db.execute(delete(Role).where(Role.name == custom_role_name))
        await db.commit()

        # Create DB role record
        db_role = Role(
            name=custom_role_name,
            display_name="Finance Manager",
            company_id=1,
            is_system=False,
        )
        db.add(db_role)
        await db.commit()
        await db.refresh(db_role)

        # Assign permission to custom role in DB
        db.add(RolePermission(role=custom_role_name, permission_id=perm.id, role_id=db_role.id))
        await db.commit()

        user = User(
            id=5003,
            email="finance_mgr@test.com",
            role=custom_role_name,
            is_active=True,
            company_id=1,
            is_super_admin=False,
        )
        app.dependency_overrides[get_current_active_user] = lambda: user

        async with get_test_client() as client:
            # E. Custom role with permission -> 200
            res_custom_allowed = await client.get("/api/v1/test-rbac-engine/perm-billing-create")
            assert res_custom_allowed.status_code == 200

            # F. Custom role without permission -> 403
            res_custom_denied = await client.get("/api/v1/test-rbac-engine/perm-projects-delete")
            assert res_custom_denied.status_code == 403

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == custom_role_name))
        await db.execute(delete(Role).where(Role.name == custom_role_name))
        await db.commit()
        app.dependency_overrides.clear()


# ==============================================================================
# G & H. USER PERMISSION OVERRIDES (Grant True & Revoke False)
# ==============================================================================
@pytest.mark.asyncio
async def test_user_permission_overrides():
    async for db in get_db_session():
        perm_view = await db.scalar(select(Permission).where(Permission.code == "projects.view"))
        perm_delete = await db.scalar(select(Permission).where(Permission.code == "projects.delete"))
        assert perm_view is not None and perm_delete is not None

        role_name = "OverrideTestRole"
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        # Role has 'projects.view' but NOT 'projects.delete'
        db.add(RolePermission(role=role_name, permission_id=perm_view.id))
        await db.commit()

        user_id = 5004
        # Ensure user exists in users table for FK integrity
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()

        db_user = User(
            id=user_id,
            email="override_user_5004@test.com",
            role=role_name,
            is_active=True,
            company_id=1,
            is_super_admin=False,
        )
        db.add(db_user)
        await db.commit()

        app.dependency_overrides[get_current_active_user] = lambda: db_user

        async with get_test_client() as client:
            # Initially: has projects.view, lacks projects.delete
            res_view_init = await client.get("/api/v1/test-rbac-engine/perm-projects-view")
            assert res_view_init.status_code == 200
            res_del_init = await client.get("/api/v1/test-rbac-engine/perm-projects-delete")
            assert res_del_init.status_code == 403

            # G. User Grant Override: Role lacks projects.delete, User override=True -> 200
            db.add(UserPermissionOverride(user_id=user_id, permission_id=perm_delete.id, is_granted=True))
            await db.commit()

            res_override_grant = await client.get("/api/v1/test-rbac-engine/perm-projects-delete")
            assert res_override_grant.status_code == 200

            # H. User Revoke Override: Role has projects.view, User override=False -> 403
            db.add(UserPermissionOverride(user_id=user_id, permission_id=perm_view.id, is_granted=False))
            await db.commit()

            res_override_revoke = await client.get("/api/v1/test-rbac-engine/perm-projects-view")
            assert res_override_revoke.status_code == 403

        # Cleanup
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        await db.commit()
        app.dependency_overrides.clear()


# ==============================================================================
# I. WILDCARD SUPPORT
# ==============================================================================
@pytest.mark.asyncio
async def test_wildcard_permission_and_revocation():
    async for db in get_db_session():
        # Ensure wildcard permission code '*' exists in DB
        wildcard_perm = await db.scalar(select(Permission).where(Permission.code == "*"))
        if not wildcard_perm:
            wildcard_perm = Permission(module="all", action="*", code="*", description="Wildcard full access")
            db.add(wildcard_perm)
            await db.commit()

        perm_view = await db.scalar(select(Permission).where(Permission.code == "projects.view"))

        role_name = "WildcardRole"
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        db.add(RolePermission(role=role_name, permission_id=wildcard_perm.id))
        await db.commit()

        user_id = 5005
        # Ensure user exists in users table for FK integrity
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()

        db_user = User(
            id=user_id,
            email="wildcard_5005@test.com",
            role=role_name,
            is_active=True,
            company_id=1,
            is_super_admin=False,
        )
        db.add(db_user)
        await db.commit()

        app.dependency_overrides[get_current_active_user] = lambda: db_user

        async with get_test_client() as client:
            # Wildcard allows arbitrary permissions
            res_arb = await client.get("/api/v1/test-rbac-engine/perm-arbitrary")
            assert res_arb.status_code == 200

            res_rep = await client.get("/api/v1/test-rbac-engine/perm-reports-view")
            assert res_rep.status_code == 200

            # Explicitly revoke projects.view for this user
            db.add(UserPermissionOverride(user_id=user_id, permission_id=perm_view.id, is_granted=False))
            await db.commit()

            # Revoked permission fails with 403 even under wildcard
            res_revoked = await client.get("/api/v1/test-rbac-engine/perm-projects-view")
            assert res_revoked.status_code == 403

            # Other permissions still pass via wildcard
            res_rep_after = await client.get("/api/v1/test-rbac-engine/perm-reports-view")
            assert res_rep_after.status_code == 200

        # Cleanup
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        await db.commit()
        app.dependency_overrides.clear()


# ==============================================================================
# J & K. SUPER ADMIN & TENANT ADMIN BYPASS
# ==============================================================================
@pytest.mark.asyncio
async def test_super_admin_and_tenant_admin_bypass():
    app.dependency_overrides.clear()

    async with get_test_client() as client:
        # Super admin bypasses all permission requirements
        super_admin = User(
            id=999,
            email="super@test.com",
            role="SomeRandomRoleWithoutPermissions",
            is_active=True,
            company_id=None,
            is_super_admin=True,
        )
        app.dependency_overrides[get_current_active_user] = lambda: super_admin
        res_sa1 = await client.get("/api/v1/test-rbac-engine/perm-arbitrary")
        assert res_sa1.status_code == 200
        res_sa2 = await client.get("/api/v1/test-rbac-engine/perm-projects-delete")
        assert res_sa2.status_code == 200

        # Tenant Admin bypasses permission checks
        tenant_admin = User(
            id=1000,
            email="admin@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            company_id=1,
            is_super_admin=False,
        )
        app.dependency_overrides[get_current_active_user] = lambda: tenant_admin
        res_ta1 = await client.get("/api/v1/test-rbac-engine/perm-arbitrary")
        assert res_ta1.status_code == 200
        res_ta2 = await client.get("/api/v1/test-rbac-engine/perm-reports-view")
        assert res_ta2.status_code == 200

    app.dependency_overrides.clear()


# ==============================================================================
# L & M. TENANT ISOLATION (Role permissions & overrides do not leak)
# ==============================================================================
@pytest.mark.asyncio
async def test_tenant_role_and_override_isolation():
    async for db in get_db_session():
        perm = await db.scalar(select(Permission).where(Permission.code == "projects.view"))
        assert perm is not None

        isolated_role_name = "TenantIsolatedRole"
        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == isolated_role_name))
        await db.execute(delete(Role).where(Role.name == isolated_role_name))
        await db.commit()

        # Create role specifically belonging to Company 1
        comp1_role = Role(
            name=isolated_role_name,
            display_name="C1 Isolated Role",
            company_id=1,
            is_system=False,
        )
        db.add(comp1_role)
        await db.commit()
        await db.refresh(comp1_role)

        # Assign permission ONLY to Company 1's role
        db.add(RolePermission(role=isolated_role_name, permission_id=perm.id, role_id=comp1_role.id))
        await db.commit()

        # Create real users in users table for FK integrity
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([5010, 5011])))
        await db.execute(delete(User).where(User.id.in_([5010, 5011])))
        await db.commit()

        user_c1 = User(id=5010, email="c1_5010@test.com", role=isolated_role_name, company_id=1, is_active=True, is_super_admin=False)
        user_c2 = User(id=5011, email="c2_5011@test.com", role=isolated_role_name, company_id=2, is_active=True, is_super_admin=False)
        db.add_all([user_c1, user_c2])
        await db.commit()

        async with get_test_client() as client:
            app.dependency_overrides[get_current_active_user] = lambda: user_c1
            res_c1 = await client.get("/api/v1/test-rbac-engine/perm-projects-view")
            assert res_c1.status_code == 200

            # L. User from Company 2 with same role name: MUST NOT get Company 1's permissions -> 403
            app.dependency_overrides[get_current_active_user] = lambda: user_c2
            res_c2 = await client.get("/api/v1/test-rbac-engine/perm-projects-view")
            assert res_c2.status_code == 403

            # M. Cross-tenant override isolation:
            # Give User 5010 (Company 1) an override for billing.create
            perm_bill = await db.scalar(select(Permission).where(Permission.code == "billing.create"))
            db.add(UserPermissionOverride(user_id=5010, permission_id=perm_bill.id, is_granted=True))
            await db.commit()

            app.dependency_overrides[get_current_active_user] = lambda: user_c1
            res_c1_bill = await client.get("/api/v1/test-rbac-engine/perm-billing-create")
            assert res_c1_bill.status_code == 200

            # User 5011 (Company 2) must NOT be affected
            app.dependency_overrides[get_current_active_user] = lambda: user_c2
            res_c2_bill = await client.get("/api/v1/test-rbac-engine/perm-billing-create")
            assert res_c2_bill.status_code == 403

        # Cleanup
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([5010, 5011])))
        await db.execute(delete(User).where(User.id.in_([5010, 5011])))
        await db.execute(delete(RolePermission).where(RolePermission.role == isolated_role_name))
        await db.execute(delete(Role).where(Role.name == isolated_role_name))
        await db.commit()
        app.dependency_overrides.clear()


# ==============================================================================
# N. EXISTING require_roles BACKWARD COMPATIBILITY
# ==============================================================================
@pytest.mark.asyncio
async def test_require_roles_without_permission_compatibility():
    app.dependency_overrides.clear()

    async with get_test_client() as client:
        # User in allowed roles list
        pm_user = User(id=5020, email="pm@test.com", role="ProjectManager", is_active=True, company_id=1, is_super_admin=False)
        app.dependency_overrides[get_current_active_user] = lambda: pm_user
        res_allowed = await client.get("/api/v1/test-rbac-engine/legacy-role-only")
        assert res_allowed.status_code == 200

        # User not in allowed roles list -> 403
        accountant_user = User(id=5021, email="acc@test.com", role="Accountant", is_active=True, company_id=1, is_super_admin=False)
        app.dependency_overrides[get_current_active_user] = lambda: accountant_user
        res_denied = await client.get("/api/v1/test-rbac-engine/legacy-role-only")
        assert res_denied.status_code == 403

    app.dependency_overrides.clear()


# ==============================================================================
# O. CUSTOM ROLE THROUGH require_roles(..., permission="...")
# ==============================================================================
@pytest.mark.asyncio
async def test_custom_role_through_require_roles_with_permission():
    async for db in get_db_session():
        perm = await db.scalar(select(Permission).where(Permission.code == "reports.view"))
        assert perm is not None

        custom_role = "CustomAuditor"
        await db.execute(delete(RolePermission).where(RolePermission.role == custom_role))
        db.add(RolePermission(role=custom_role, permission_id=perm.id))
        await db.commit()

        # User has role 'CustomAuditor' which is NOT in ['Admin', 'ProjectManager']
        auditor_user = User(id=5030, email="auditor@test.com", role=custom_role, is_active=True, company_id=1, is_super_admin=False)
        app.dependency_overrides[get_current_active_user] = lambda: auditor_user

        async with get_test_client() as client:
            # Even though role is not in static list, the endpoint declares permission="reports.view"
            # Since CustomAuditor has reports.view in DB, it MUST return 200!
            res = await client.get("/api/v1/test-rbac-engine/role-with-perm")
            assert res.status_code == 200

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == custom_role))
        await db.commit()
        app.dependency_overrides.clear()
