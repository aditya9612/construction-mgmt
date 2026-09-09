import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.dependencies import get_current_active_user, get_effective_user_permissions
from app.db.session import get_db_session
from app.main import app
from app.models.company import Company
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride
from app.models.user import User


def get_test_client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


def make_audit_user(user_id: int, company_id: int, role: str) -> User:
    return User(
        id=user_id,
        email=f"audit_{user_id}@test.com",
        role=role,
        company_id=company_id,
        is_active=True,
        is_super_admin=False,
    )


# ==============================================================================
# SECTION 3 & 4: REPRESENTATIVE END-TO-END FLOWS & DIFFERENT PERMISSION TYPES
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,perm_type,perm_code,method,url,payload",
    [
        ("Work Orders", "view", "work_orders.view", "GET", "/api/v1/work-orders", None),
        ("Settings", "view", "settings.view", "GET", "/api/v1/settings/company", None),
        ("Settings", "update", "settings.edit", "PUT", "/api/v1/settings/company", {"company_name": "Audited Co"}),
        ("Vendor Bills", "view", "vendor_bills.view", "GET", "/api/v1/vendor-bills", None),
        ("Invoices", "view", "invoices.view", "GET", "/api/v1/invoices", None),
        ("Materials", "view", "materials.view", "GET", "/api/v1/materials/summary", None),
        ("Quotations", "view", "quotations.view", "GET", "/api/v1/quotations/", None),
        ("Work Orders", "delete", "work_orders.delete", "DELETE", "/api/v1/work-orders/999999", None),
        ("Vendor Bills", "approve", "vendor_bills.approve", "POST", "/api/v1/vendor-bills/999999/approve", {"status": "APPROVED"}),
    ],
)
async def test_module_e2e_flow_and_permission_types(module, perm_type, perm_code, method, url, payload):
    """
    Step A: No permission -> 403 Forbidden
    Step B: Admin assigns permission -> 200 OK (or business validation 404 for nonexistent test IDs, NOT 403)
    Step C: Admin revokes permission -> 403 Forbidden
    """
    admin = make_audit_user(user_id=7701, company_id=1, role="Admin")
    target_user = make_audit_user(user_id=7702, company_id=1, role="Client")

    async with get_test_client() as client:
        # Ensure clean initial state for Client role in Company 1
        app.dependency_overrides[get_current_active_user] = lambda: admin
        await client.post("/api/v1/rbac/roles/Client/reset-defaults")

        # Step A: No permission -> Must return 403 Forbidden
        app.dependency_overrides[get_current_active_user] = lambda: target_user
        if method == "GET":
            res_a = await client.get(url)
        elif method == "POST":
            res_a = await client.post(url, json=payload or {})
        elif method == "PUT":
            res_a = await client.put(url, json=payload or {})
        elif method == "DELETE":
            res_a = await client.delete(url)
        assert res_a.status_code == 403, f"Step A: Expected 403 for {method} {url} without {perm_code}, got {res_a.status_code}"

        # Step B: Admin assigns permission
        app.dependency_overrides[get_current_active_user] = lambda: admin
        assign_res = await client.post(
            "/api/v1/rbac/roles/Client/permissions",
            json={"permission": perm_code},
        )
        assert assign_res.status_code == 200

        # Step B verify: Target user now accesses endpoint (Status code must not be 403)
        app.dependency_overrides[get_current_active_user] = lambda: target_user
        if method == "GET":
            res_b = await client.get(url)
        elif method == "POST":
            res_b = await client.post(url, json=payload or {})
        elif method == "PUT":
            res_b = await client.put(url, json=payload or {})
        elif method == "DELETE":
            res_b = await client.delete(url)
        assert res_b.status_code != 403, f"Step B: User must not be 403 after grant of {perm_code}, got {res_b.status_code}"

        # Step C: Admin revokes permission / resets role
        app.dependency_overrides[get_current_active_user] = lambda: admin
        reset_res = await client.post("/api/v1/rbac/roles/Client/reset-defaults")
        assert reset_res.status_code == 200

        # Step C verify: Target user access is rejected with 403 again
        app.dependency_overrides[get_current_active_user] = lambda: target_user
        if method == "GET":
            res_c = await client.get(url)
        elif method == "POST":
            res_c = await client.post(url, json=payload or {})
        elif method == "PUT":
            res_c = await client.put(url, json=payload or {})
        elif method == "DELETE":
            res_c = await client.delete(url)
        assert res_c.status_code == 403, f"Step C: Expected 403 for {method} {url} after revoking {perm_code}, got {res_c.status_code}"

    app.dependency_overrides.clear()


# ==============================================================================
# SECTION 5: VERIFY ADMIN ACCESS
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/work-orders",
        "/api/v1/settings/company",
        "/api/v1/vendor-bills",
        "/api/v1/invoices",
        "/api/v1/materials/summary",
        "/api/v1/quotations/",
    ],
)
async def test_admin_dynamic_access_to_endpoints(url):
    """
    Admin accesses protected endpoints automatically via dynamic catalog without manual permission assignment.
    """
    admin = make_audit_user(user_id=7703, company_id=1, role="Admin")
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        res = await client.get(url)
        assert res.status_code != 403, f"Admin must have dynamic access to {url}, got {res.status_code}"

    app.dependency_overrides.clear()


# ==============================================================================
# SECTION 6: TENANT ISOLATION ACROSS 3 REPRESENTATIVE MODULES
# ==============================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,perm_code,url",
    [
        ("Work Orders", "work_orders.view", "/api/v1/work-orders"),
        ("Vendor Bills", "vendor_bills.view", "/api/v1/vendor-bills"),
        ("Invoices", "invoices.view", "/api/v1/invoices"),
    ],
)
async def test_tenant_isolation_multimodule(module, perm_code, url):
    """
    Company A Client granted permission -> succeeds.
    Company B Client with no permission -> 403 Forbidden.
    Company A operations do not leak into Company B.
    """
    admin_a = make_audit_user(user_id=7710, company_id=1, role="Admin")
    admin_b = make_audit_user(user_id=7711, company_id=2, role="Admin")
    client_a = make_audit_user(user_id=7712, company_id=1, role="Client")
    client_b = make_audit_user(user_id=7713, company_id=2, role="Client")

    async with get_test_client() as client:
        # Reset Client in both companies
        app.dependency_overrides[get_current_active_user] = lambda: admin_a
        await client.post("/api/v1/rbac/roles/Client/reset-defaults")
        app.dependency_overrides[get_current_active_user] = lambda: admin_b
        await client.post("/api/v1/rbac/roles/Client/reset-defaults")

        # Company A grants permission
        app.dependency_overrides[get_current_active_user] = lambda: admin_a
        await client.post(
            "/api/v1/rbac/roles/Client/permissions",
            json={"permission": perm_code},
        )

        # Client A in Company 1 has access
        app.dependency_overrides[get_current_active_user] = lambda: client_a
        res_a = await client.get(url)
        assert res_a.status_code != 403, f"Company 1 client should have access to {url}"

        # Client B in Company 2 must STILL be denied with 403
        app.dependency_overrides[get_current_active_user] = lambda: client_b
        res_b = await client.get(url)
        assert res_b.status_code == 403, f"Company 2 client must NOT gain access from Company 1 grant for {url}"

        # Reset Company A
        app.dependency_overrides[get_current_active_user] = lambda: admin_a
        await client.post("/api/v1/rbac/roles/Client/reset-defaults")

    app.dependency_overrides.clear()


# ==============================================================================
# SECTION 7: USER OVERRIDE BEHAVIOR
# ==============================================================================

@pytest.mark.asyncio
async def test_user_override_positive_and_negative():
    """
    Positive override grants access to endpoint even if role has 0 permissions.
    Negative override denies access even to Admin.
    """
    admin_id = 7720
    client_id = 7721
    admin = make_audit_user(user_id=admin_id, company_id=1, role="Admin")
    client_user = make_audit_user(user_id=client_id, company_id=1, role="Client")

    async with get_test_client() as client:
        async for db in get_db_session():
            # Ensure users exist in users table for FK constraints
            for u in [admin, client_user]:
                existing = await db.scalar(select(User).where(User.id == u.id))
                if not existing:
                    db.add(User(
                        id=u.id,
                        email=u.email,
                        role=u.role,
                        company_id=u.company_id,
                        is_active=True,
                        is_super_admin=False,
                    ))
            await db.commit()

            # Clean any leftover overrides
            await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([client_id, admin_id])))
            await db.commit()

            perm_wo = await db.scalar(select(Permission).where(Permission.code == "work_orders.view"))

            try:
                # 1. Client initially 403 on /api/v1/work-orders
                app.dependency_overrides[get_current_active_user] = lambda: client_user
                r1 = await client.get("/api/v1/work-orders")
                assert r1.status_code == 403

                # 2. Positive override: grant work_orders.view to Client
                db.add(UserPermissionOverride(user_id=client_id, permission_id=perm_wo.id, is_granted=True))
                await db.commit()

                # Client now succeeds on /api/v1/work-orders
                r2 = await client.get("/api/v1/work-orders")
                assert r2.status_code == 200

                # 3. Negative override on Admin: deny work_orders.view
                db.add(UserPermissionOverride(user_id=admin_id, permission_id=perm_wo.id, is_granted=False))
                await db.commit()

                # Admin now returns 403 on /api/v1/work-orders
                app.dependency_overrides[get_current_active_user] = lambda: admin
                r3 = await client.get("/api/v1/work-orders")
                assert r3.status_code == 403, f"Admin must be denied when negative override is present, got {r3.status_code}"

                # But Admin still has access to other endpoints like /api/v1/invoices
                r4 = await client.get("/api/v1/invoices")
                assert r4.status_code != 403, "Admin should still have access to non-revoked endpoints"
            finally:
                app.dependency_overrides.clear()
                await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([client_id, admin_id])))
                await db.execute(delete(User).where(User.id.in_([client_id, admin_id])))
                await db.commit()
