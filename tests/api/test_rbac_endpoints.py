import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.dependencies import get_current_active_user, get_effective_user_permissions
from app.db.session import get_db_session
from app.main import app
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride
from app.models.user import User, UserRole


def get_test_client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


def make_test_admin(user_id: int = 9101, company_id: int = 1, role: str = "Admin") -> User:
    return User(
        id=user_id,
        email=f"admin_{user_id}@test.com",
        role=role,
        is_active=True,
        company_id=company_id,
        is_super_admin=False,
    )


def make_test_user(user_id: int = 9102, company_id: int = 1, role: str = "Labour") -> User:
    return User(
        id=user_id,
        email=f"user_{user_id}@test.com",
        role=role,
        is_active=True,
        company_id=company_id,
        is_super_admin=False,
    )


# ==============================================================================
# 1. INCREMENTAL ADD PERMISSIONS
# ==============================================================================

@pytest.mark.asyncio
async def test_add_permission_incremental():
    admin = make_test_admin(user_id=9201, company_id=1)
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        role_name = "TestRoleInc"
        # Cleanup if exists
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

        create_res = await client.post(
            "/api/v1/rbac/roles",
            json={"name": role_name, "display_name": "Test Role Inc", "description": "Test"},
        )
        assert create_res.status_code == 200

        # 1. Add materials.view
        res1 = await client.post(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permission": "materials.view"},
        )
        assert res1.status_code == 200
        data1 = res1.json()
        assert "materials.view" in data1["permissions"]

        # 2. Add labour.view incrementally
        res2 = await client.post(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permission": "labour.view"},
        )
        assert res2.status_code == 200
        data2 = res2.json()

        # Verify BOTH exist and materials.view was NOT removed
        assert "materials.view" in data2["permissions"]
        assert "labour.view" in data2["permissions"]

        # GET verification
        get_res = await client.get(f"/api/v1/rbac/roles/{role_name}/permissions")
        assert get_res.status_code == 200
        get_perms = get_res.json()["permissions"]
        assert "materials.view" in get_perms
        assert "labour.view" in get_perms

        # Cleanup
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

    app.dependency_overrides.clear()


# ==============================================================================
# 2. MULTIPLE ADD & 3. DUPLICATE ADD (IDEMPOTENCY)
# ==============================================================================

@pytest.mark.asyncio
async def test_multiple_and_duplicate_add():
    admin = make_test_admin(user_id=9202, company_id=1)
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        role_name = "TestRoleMulti"
        # Cleanup if exists
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

        create_res = await client.post(
            "/api/v1/rbac/roles",
            json={"name": role_name, "display_name": "Test Role Multi"},
        )
        assert create_res.status_code == 200

        # Multiple Add: materials.view + labour.view + projects.view
        res = await client.post(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permissions": ["materials.view", "labour.view", "projects.view"]},
        )
        assert res.status_code == 200
        perms = res.json()["permissions"]
        assert "materials.view" in perms
        assert "labour.view" in perms
        assert "projects.view" in perms

        # Duplicate Add: Add materials.view again
        res_dup = await client.post(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permission": "materials.view"},
        )
        assert res_dup.status_code == 200
        dup_data = res_dup.json()
        assert dup_data["added"] == []  # Idempotent: nothing newly added
        assert dup_data["permissions"].count("materials.view") == 1

        # Cleanup
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

    app.dependency_overrides.clear()


# ==============================================================================
# 4. PUT REPLACEMENT (FULL OVERWRITE SEMANTICS)
# ==============================================================================

@pytest.mark.asyncio
async def test_put_full_replacement():
    admin = make_test_admin(user_id=9203, company_id=1)
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        role_name = "TestRolePut"
        # Cleanup if exists
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

        create_res = await client.post(
            "/api/v1/rbac/roles",
            json={"name": role_name, "display_name": "Test Role Put"},
        )
        assert create_res.status_code == 200

        # Initial permissions: materials.view and labour.view
        await client.post(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permissions": ["materials.view", "labour.view"]},
        )

        # PUT projects.view
        put_res = await client.put(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permissions": ["projects.view"]},
        )
        assert put_res.status_code == 200
        put_perms = put_res.json()["permissions"]

        # MUST contain projects.view and MUST NOT contain materials.view or labour.view
        assert put_perms == ["projects.view"]

        # Cleanup
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

    app.dependency_overrides.clear()


# ==============================================================================
# 5. SINGLE DELETE PERMISSION
# ==============================================================================

@pytest.mark.asyncio
async def test_single_delete_permission():
    admin = make_test_admin(user_id=9204, company_id=1)
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        role_name = "TestRoleDelSingle"
        # Cleanup if exists
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

        create_res = await client.post(
            "/api/v1/rbac/roles",
            json={"name": role_name, "display_name": "Test Role Del Single"},
        )
        assert create_res.status_code == 200

        # Add materials.view and labour.view
        await client.post(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permissions": ["materials.view", "labour.view"]},
        )

        # Delete single: labour.view via path parameter
        del_res = await client.delete(
            f"/api/v1/rbac/roles/{role_name}/permissions/labour.view"
        )
        assert del_res.status_code == 200
        del_perms = del_res.json()["permissions"]

        assert "materials.view" in del_perms
        assert "labour.view" not in del_perms

        # Cleanup
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

    app.dependency_overrides.clear()


# ==============================================================================
# 6. BULK DELETE PERMISSIONS
# ==============================================================================

@pytest.mark.asyncio
async def test_bulk_delete_permissions():
    admin = make_test_admin(user_id=9205, company_id=1)
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        role_name = "TestRoleDelBulk"
        # Cleanup if exists
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

        create_res = await client.post(
            "/api/v1/rbac/roles",
            json={"name": role_name, "display_name": "Test Role Del Bulk"},
        )
        assert create_res.status_code == 200

        # Add materials.view, labour.view, projects.view
        await client.post(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permissions": ["materials.view", "labour.view", "projects.view"]},
        )

        # Bulk Delete: materials.view + labour.view
        del_res = await client.request(
            "DELETE",
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permissions": ["materials.view", "labour.view"]},
        )
        assert del_res.status_code == 200
        del_perms = del_res.json()["permissions"]

        assert del_perms == ["projects.view"]

        # Cleanup
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

    app.dependency_overrides.clear()


# ==============================================================================
# 7. CUSTOM ROLE DELETE (SYSTEM ROLE BLOCKED, ASSIGNED USER CONFLICT)
# ==============================================================================

@pytest.mark.asyncio
async def test_custom_role_delete_and_protections():
    admin = make_test_admin(user_id=9206, company_id=1)
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        # A. Cannot delete built-in system role (e.g. Client, Admin)
        sys_del = await client.delete("/api/v1/rbac/roles/Client")
        assert sys_del.status_code == 400
        assert "built-in" in sys_del.json()["detail"].lower()

        # B. Cannot delete role if assigned users exist -> 409 Conflict
        role_with_user = "RoleAssignedUser"
        # Cleanup if exists
        await client.delete(f"/api/v1/rbac/roles/{role_with_user}")

        await client.post(
            "/api/v1/rbac/roles",
            json={"name": role_with_user, "display_name": "Role with user"},
        )

        # Create a user with this role in DB
        async for db in get_db_session():
            assigned_user = User(
                id=99881,
                email="assigned_user_test@test.com",
                role=role_with_user,
                is_active=True,
                company_id=1,
                is_super_admin=False,
            )
            db.add(assigned_user)
            await db.commit()

            # Attempt deletion -> 409 Conflict
            del_conflict = await client.delete(f"/api/v1/rbac/roles/{role_with_user}")
            assert del_conflict.status_code == 409

            # Clean up user
            await db.execute(delete(User).where(User.id == 99881))
            await db.commit()

            # Now deletion succeeds -> 200
            del_ok = await client.delete(f"/api/v1/rbac/roles/{role_with_user}")
            assert del_ok.status_code == 200

    app.dependency_overrides.clear()


# ==============================================================================
# 8. RESET ROLE DEFAULTS
# ==============================================================================

@pytest.mark.asyncio
async def test_reset_role_defaults():
    admin = make_test_admin(user_id=9207, company_id=1)
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        # First ensure clean state
        await client.post("/api/v1/rbac/roles/Client/reset-defaults")

        # Get baseline system default permissions for Client (MUST be zero)
        base_res = await client.get("/api/v1/rbac/roles/Client/permissions")
        assert base_res.status_code == 200
        default_client_perms = sorted(base_res.json()["permissions"])
        assert default_client_perms == [], "Client must have zero default permissions"

        # Customize Client for Company 1: Add labour.view
        add_res = await client.post(
            "/api/v1/rbac/roles/Client/permissions",
            json={"permission": "labour.view"},
        )
        assert add_res.status_code == 200
        assert "labour.view" in add_res.json()["permissions"]

        # Reset defaults
        reset_res = await client.post("/api/v1/rbac/roles/Client/reset-defaults")
        assert reset_res.status_code == 200
        reset_perms = sorted(reset_res.json()["permissions"])

        # Should match baseline system defaults exactly (zero permissions)
        assert reset_perms == []
        assert reset_perms == default_client_perms

    app.dependency_overrides.clear()


# ==============================================================================
# 9. TENANT ISOLATION (COMPANY A DOES NOT AFFECT COMPANY B)
# ==============================================================================

@pytest.mark.asyncio
async def test_tenant_isolation():
    company_a_admin = make_test_admin(user_id=9208, company_id=1)
    company_b_admin = make_test_admin(user_id=9209, company_id=2)

    async with get_test_client() as client:
        # Reset defaults on Client for both companies first to ensure pristine state
        app.dependency_overrides[get_current_active_user] = lambda: company_a_admin
        await client.post("/api/v1/rbac/roles/Client/reset-defaults")
        app.dependency_overrides[get_current_active_user] = lambda: company_b_admin
        await client.post("/api/v1/rbac/roles/Client/reset-defaults")

        # Company B gets baseline Client permissions (zero permissions)
        res_b_before = await client.get("/api/v1/rbac/roles/Client/permissions")
        assert res_b_before.status_code == 200
        b_baseline = sorted(res_b_before.json()["permissions"])
        assert b_baseline == []

        # Company A customizes Client: adds labour.view
        app.dependency_overrides[get_current_active_user] = lambda: company_a_admin
        res_a = await client.post(
            "/api/v1/rbac/roles/Client/permissions",
            json={"permission": "labour.view"},
        )
        assert res_a.status_code == 200
        assert "labour.view" in res_a.json()["permissions"]

        # Company B must STILL have its original baseline permissions (labour.view must NOT be in B)
        app.dependency_overrides[get_current_active_user] = lambda: company_b_admin
        res_b_after = await client.get("/api/v1/rbac/roles/Client/permissions")
        assert res_b_after.status_code == 200
        b_after = sorted(res_b_after.json()["permissions"])
        assert b_after == b_baseline
        assert "labour.view" not in b_after

        # Clean up Company A customization
        app.dependency_overrides[get_current_active_user] = lambda: company_a_admin
        await client.post("/api/v1/rbac/roles/Client/reset-defaults")

    app.dependency_overrides.clear()


# ==============================================================================
# 10. AUTHORIZATION (NON-ADMIN USERS BLOCKED)
# ==============================================================================

@pytest.mark.asyncio
async def test_authorization_non_admin_blocked():
    read_only_user = make_test_user(user_id=9210, company_id=1, role="Labour")
    app.dependency_overrides[get_current_active_user] = lambda: read_only_user

    async with get_test_client() as client:
        # 1. Add permissions -> 403
        r1 = await client.post(
            "/api/v1/rbac/roles/Labour/permissions",
            json={"permission": "materials.view"},
        )
        assert r1.status_code == 403

        # 2. PUT permissions -> 403
        r2 = await client.put(
            "/api/v1/rbac/roles/Labour/permissions",
            json={"permissions": ["materials.view"]},
        )
        assert r2.status_code == 403

        # 3. Delete permissions -> 403
        r3 = await client.delete("/api/v1/rbac/roles/Labour/permissions/tasks.view")
        assert r3.status_code == 403

        # 4. Delete role -> 403
        r4 = await client.delete("/api/v1/rbac/roles/CustomRole")
        assert r4.status_code == 403

        # 5. Reset defaults -> 403
        r5 = await client.post("/api/v1/rbac/roles/Labour/reset-defaults")
        assert r5.status_code == 403

    app.dependency_overrides.clear()


# ==============================================================================
# 11. ZERO DEFAULT PERMISSIONS FOR BUILT-IN NON-ADMIN ROLES
# ==============================================================================

@pytest.mark.asyncio
async def test_client_and_labour_zero_default_permissions():
    admin = make_test_admin(user_id=9211, company_id=1)
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        # Ensure pristine state for Client & Labour
        await client.post("/api/v1/rbac/roles/Client/reset-defaults")
        await client.post("/api/v1/rbac/roles/Labour/reset-defaults")
        await client.post("/api/v1/rbac/roles/SiteEngineer/reset-defaults")
        await client.post("/api/v1/rbac/roles/Contractor/reset-defaults")
        await client.post("/api/v1/rbac/roles/Accountant/reset-defaults")
        await client.post("/api/v1/rbac/roles/ProjectManager/reset-defaults")

        # Built-in roles must have ZERO default permissions
        for role in ["Client", "Labour", "SiteEngineer", "Contractor", "Accountant", "ProjectManager"]:
            res = await client.get(f"/api/v1/rbac/roles/{role}/permissions")
            assert res.status_code == 200
            assert res.json()["permissions"] == [], f"Expected {role} to have 0 permissions by default"

        # Effective permissions via get_effective_user_permissions
        async for db in get_db_session():
            client_user = make_test_user(user_id=9212, company_id=1, role="Client")
            labour_user = make_test_user(user_id=9213, company_id=1, role="Labour")

            client_perms = await get_effective_user_permissions(db, client_user)
            labour_perms = await get_effective_user_permissions(db, labour_user)

            assert client_perms == set(), "Client must have zero implicit effective permissions"
            assert labour_perms == set(), "Labour must have zero implicit effective permissions"

    app.dependency_overrides.clear()


# ==============================================================================
# 12. ADMIN DYNAMIC PERMISSION CATALOG & RUNTIME PERMISSION INJECTION
# ==============================================================================

@pytest.mark.asyncio
async def test_admin_dynamic_permission_catalog_and_new_permission():
    admin = make_test_admin(user_id=9214, company_id=1)
    app.dependency_overrides[get_current_active_user] = lambda: admin

    dyn_code = "equipment.test_runtime_dyn_perm"

    async with get_test_client() as client:
        async for db in get_db_session():
            # Clean up if leftover
            await db.execute(delete(Permission).where(Permission.code == dyn_code))
            await db.commit()

            # Initial Admin permissions
            admin_perms_before = await get_effective_user_permissions(db, admin)
            assert len(admin_perms_before) > 0
            assert dyn_code not in admin_perms_before

            # Insert a new runtime permission into the permissions catalog table
            new_perm = Permission(
                module="equipment",
                action="test_runtime_dyn_perm",
                code=dyn_code,
                description="Test dynamic permission resolution",
            )
            db.add(new_perm)
            await db.commit()
            new_perm_id = new_perm.id

            try:
                # Admin must immediately resolve the newly inserted catalog permission
                admin_perms_after = await get_effective_user_permissions(db, admin)
                assert dyn_code in admin_perms_after, "Admin must dynamically receive new catalog permission"

                # Client user must NOT have the new permission
                client_user = make_test_user(user_id=9215, company_id=1, role="Client")
                client_perms = await get_effective_user_permissions(db, client_user)
                assert dyn_code not in client_perms, "Client must NOT implicitly receive new catalog permission"

                # Explicitly assign to Client via Admin API
                add_res = await client.post(
                    "/api/v1/rbac/roles/Client/permissions",
                    json={"permission": dyn_code},
                )
                assert add_res.status_code == 200
                assert dyn_code in add_res.json()["permissions"]

                # Refresh snapshot of current session after client's external commit
                await db.rollback()

                # Client user NOW has the permission
                client_perms_updated = await get_effective_user_permissions(db, client_user)
                assert dyn_code in client_perms_updated, "Client must have explicitly assigned permission"

                # Reset Client to clean up assignment
                await client.post("/api/v1/rbac/roles/Client/reset-defaults")
            finally:
                # Cleanup newly created permission
                await db.execute(delete(RolePermission).where(RolePermission.permission_id == new_perm_id))
                await db.execute(delete(Permission).where(Permission.code == dyn_code))
                await db.commit()

    app.dependency_overrides.clear()


# ==============================================================================
# 13. CUSTOM ROLE ZERO DEFAULT & EXPLICIT ASSIGNMENT & TENANT ISOLATION
# ==============================================================================

@pytest.mark.asyncio
async def test_custom_role_zero_default_and_explicit_assignment():
    admin_comp1 = make_test_admin(user_id=9216, company_id=1)
    admin_comp2 = make_test_admin(user_id=9217, company_id=2)
    role_name = "CustomAuditor"

    app.dependency_overrides[get_current_active_user] = lambda: admin_comp1

    async with get_test_client() as client:
        # Cleanup if exists
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

        # 1. Create custom role
        create_res = await client.post(
            "/api/v1/rbac/roles",
            json={"name": role_name, "display_name": "Custom Auditor"},
        )
        assert create_res.status_code == 200

        # 2. Must start with ZERO permissions
        get_res = await client.get(f"/api/v1/rbac/roles/{role_name}/permissions")
        assert get_res.status_code == 200
        assert get_res.json()["permissions"] == []

        # 3. Explicitly assign 2 permissions: materials.view and projects.view
        add_res = await client.post(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permissions": ["materials.view", "projects.view"]},
        )
        assert add_res.status_code == 200
        assert sorted(add_res.json()["permissions"]) == ["materials.view", "projects.view"]

        # 4. Effective user permissions for Company 1 user
        async for db in get_db_session():
            user_comp1 = make_test_user(user_id=9218, company_id=1, role=role_name)
            user_comp2 = make_test_user(user_id=9219, company_id=2, role=role_name)

            perms_1 = await get_effective_user_permissions(db, user_comp1)
            perms_2 = await get_effective_user_permissions(db, user_comp2)

            assert perms_1 == {"materials.view", "projects.view"}
            assert perms_2 == set(), "Company 2 user must not inherit Company 1 role permissions"

        # Cleanup
        await client.delete(f"/api/v1/rbac/roles/{role_name}")

    app.dependency_overrides.clear()


# ==============================================================================
# 14. USER OVERRIDE PRECEDENCE
# ==============================================================================

@pytest.mark.asyncio
async def test_user_override_precedence():
    admin_id = 9220
    admin = make_test_admin(user_id=admin_id, company_id=1)
    target_user_id = 9221
    target_user = make_test_user(user_id=target_user_id, company_id=1, role="Labour")
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        async for db in get_db_session():
            # Ensure test users exist in users table for FK constraint
            for u in [admin, target_user]:
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
            await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([target_user_id, admin_id])))
            await db.commit()

            try:
                # Baseline Labour has 0 permissions
                perms_baseline = await get_effective_user_permissions(db, target_user)
                assert "materials.view" not in perms_baseline

                # Positive override: grant materials.view
                p_mat = await db.scalar(select(Permission).where(Permission.code == "materials.view"))
                override_grant = UserPermissionOverride(
                    user_id=target_user_id,
                    permission_id=p_mat.id,
                    is_granted=True,
                )
                db.add(override_grant)
                await db.commit()

                perms_granted = await get_effective_user_permissions(db, target_user)
                assert "materials.view" in perms_granted, "Positive override must grant permission"

                # Negative override on Admin: deny materials.view
                override_deny_admin = UserPermissionOverride(
                    user_id=admin_id,
                    permission_id=p_mat.id,
                    is_granted=False,
                )
                db.add(override_deny_admin)
                await db.commit()

                admin_perms = await get_effective_user_permissions(db, admin)
                assert "materials.view" not in admin_perms, "Negative override must deny permission even for Admin"
                assert "projects.view" in admin_perms, "Other Admin permissions must remain intact"
            finally:
                # Cleanup overrides and test users
                await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([target_user_id, admin_id])))
                await db.execute(delete(User).where(User.id.in_([target_user_id, admin_id])))
                await db.commit()

    app.dependency_overrides.clear()

