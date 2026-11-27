import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from app.core.dependencies import get_current_active_user, get_effective_user_permissions
from app.db.session import get_db_session
from app.main import app
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride, RBACAuditLog
from app.models.user import User, UserRole, ActivityLog, UserAuditLog
from app.services.rbac_audit import record_rbac_audit


def get_test_client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


def make_security_user(user_id: int, company_id: int, role: str, is_super_admin: bool = False) -> User:
    return User(
        id=user_id,
        email=f"sec_user_{user_id}@test.com",
        role=role,
        is_active=True,
        company_id=company_id if not is_super_admin else None,
        is_super_admin=is_super_admin,
    )


# ==============================================================================
# 1. AUTHENTICATION & BASE AUTHORIZATION
# ==============================================================================

@pytest.mark.asyncio
async def test_unauthenticated_requests_return_401():
    async with get_test_client() as client:
        # No dependency overrides -> unauthenticated token check fails
        endpoints = [
            ("GET", "/api/v1/rbac/permissions"),
            ("GET", "/api/v1/rbac/roles"),
            ("POST", "/api/v1/rbac/roles"),
            ("GET", "/api/v1/rbac/roles/Admin/permissions"),
            ("POST", "/api/v1/rbac/seed"),
            ("POST", "/api/v1/rbac/assign-defaults"),
        ]
        for method, url in endpoints:
            if method == "GET":
                res = await client.get(url)
            else:
                res = await client.post(url, json={})
            assert res.status_code == 401, f"Expected 401 for unauthenticated {method} {url}, got {res.status_code}"


@pytest.mark.asyncio
async def test_normal_user_cannot_access_rbac():
    labour_user = make_security_user(user_id=8801, company_id=1, role="Labour")
    app.dependency_overrides[get_current_active_user] = lambda: labour_user

    async with get_test_client() as client:
        endpoints = [
            ("GET", "/api/v1/rbac/permissions"),
            ("GET", "/api/v1/rbac/roles"),
            ("POST", "/api/v1/rbac/roles", {"name": "HackerRole", "display_name": "Hacker"}),
            ("GET", "/api/v1/rbac/roles/Labour/permissions"),
            ("POST", "/api/v1/rbac/roles/Labour/permissions", {"permission": "materials.view"}),
            ("PUT", "/api/v1/rbac/roles/Labour/permissions", {"permissions": ["materials.view"]}),
            ("DELETE", "/api/v1/rbac/roles/Labour/permissions"),
            ("POST", "/api/v1/rbac/roles/Labour/reset-defaults", {}),
            ("GET", "/api/v1/rbac/users/8801/overrides"),
            ("PUT", "/api/v1/rbac/users/8801/overrides", {"overrides": []}),
            ("POST", "/api/v1/rbac/seed", {}),
            ("POST", "/api/v1/rbac/assign-defaults", {}),
        ]
        for item in endpoints:
            method, url = item[0], item[1]
            payload = item[2] if len(item) > 2 else {}
            if method == "GET":
                res = await client.get(url)
            elif method == "POST":
                res = await client.post(url, json=payload)
            elif method == "PUT":
                res = await client.put(url, json=payload)
            elif method == "DELETE":
                res = await client.request("DELETE", url, json=payload)
            assert res.status_code == 403, f"Expected 403 for Labour user on {method} {url}, got {res.status_code}"

    app.dependency_overrides.clear()


# ==============================================================================
# 2. MAINTENANCE ENDPOINTS AUTHORIZATION (SEED & ASSIGN-DEFAULTS)
# ==============================================================================

@pytest.mark.asyncio
async def test_maintenance_endpoints_superadmin_only():
    super_admin = make_security_user(user_id=8802, company_id=1, role="Admin", is_super_admin=True)
    tenant_admin = make_security_user(user_id=8803, company_id=1, role="Admin", is_super_admin=False)
    normal_user = make_security_user(user_id=8804, company_id=1, role="SiteEngineer", is_super_admin=False)

    async with get_test_client() as client:
        # A. Tenant Admin must get 403
        app.dependency_overrides[get_current_active_user] = lambda: tenant_admin
        res_seed_ta = await client.post("/api/v1/rbac/seed")
        assert res_seed_ta.status_code == 403, f"Tenant Admin must get 403 on /seed, got {res_seed_ta.status_code}"
        res_def_ta = await client.post("/api/v1/rbac/assign-defaults")
        assert res_def_ta.status_code == 403, f"Tenant Admin must get 403 on /assign-defaults, got {res_def_ta.status_code}"

        # B. Normal User must get 403
        app.dependency_overrides[get_current_active_user] = lambda: normal_user
        res_seed_nu = await client.post("/api/v1/rbac/seed")
        assert res_seed_nu.status_code == 403
        res_def_nu = await client.post("/api/v1/rbac/assign-defaults")
        assert res_def_nu.status_code == 403

        # C. Super Admin must get 200
        app.dependency_overrides[get_current_active_user] = lambda: super_admin
        res_seed_sa = await client.post("/api/v1/rbac/seed")
        assert res_seed_sa.status_code == 200, f"Super Admin must succeed on /seed, got {res_seed_sa.status_code}"
        res_def_sa = await client.post("/api/v1/rbac/assign-defaults")
        assert res_def_sa.status_code == 200, f"Super Admin must succeed on /assign-defaults, got {res_def_sa.status_code}"

    app.dependency_overrides.clear()


# ==============================================================================
# 3. TENANT ISOLATION (TENANT A VS TENANT B)
# ==============================================================================

@pytest.mark.asyncio
async def test_cross_tenant_isolation_all_endpoints():
    admin_a = make_security_user(user_id=8810, company_id=101, role="Admin")
    admin_b = make_security_user(user_id=8820, company_id=102, role="Admin")
    user_b = make_security_user(user_id=8821, company_id=102, role="SiteEngineer")

    async with get_test_client() as client:
        # Create users in DB for FK
        async for db in get_db_session():
            for u in [admin_a, admin_b, user_b]:
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

        # Step 1: Tenant B creates custom role "CustomB"
        app.dependency_overrides[get_current_active_user] = lambda: admin_b
        res_create_b = await client.post(
            "/api/v1/rbac/roles",
            json={"name": "CustomB", "display_name": "Tenant B Custom Role", "description": "Scoped to Tenant B"},
        )
        assert res_create_b.status_code == 200

        # Assign a permission to CustomB under Tenant B
        await client.post(
            "/api/v1/rbac/roles/CustomB/permissions",
            json={"permission": "materials.view"},
        )

        # Step 2: Tenant A attempts to view Tenant B roles
        app.dependency_overrides[get_current_active_user] = lambda: admin_a
        roles_res_a = await client.get("/api/v1/rbac/roles")
        assert roles_res_a.status_code == 200
        roles_data_a = roles_res_a.json()
        custom_role_names_a = [r["name"] for r in roles_data_a["details"]]
        assert "CustomB" not in custom_role_names_a, "Tenant A must NOT see Tenant B custom roles in /roles"

        # Step 3: Tenant A attempts to read CustomB role permissions -> 404
        perm_res_a = await client.get("/api/v1/rbac/roles/CustomB/permissions")
        assert perm_res_a.status_code == 404, f"Tenant A must get 404 on Tenant B role, got {perm_res_a.status_code}"

        # Step 4: Tenant A attempts to add permissions to CustomB -> 404
        add_res_a = await client.post(
            "/api/v1/rbac/roles/CustomB/permissions",
            json={"permission": "materials.create"},
        )
        assert add_res_a.status_code == 404

        # Step 5: Tenant A attempts to replace permissions on CustomB -> 404
        put_res_a = await client.put(
            "/api/v1/rbac/roles/CustomB/permissions",
            json={"permissions": ["materials.create"]},
        )
        assert put_res_a.status_code == 404

        # Step 6: Tenant A attempts to delete single permission from CustomB -> 404
        del_single_a = await client.delete("/api/v1/rbac/roles/CustomB/permissions/materials.view")
        assert del_single_a.status_code == 404

        # Step 7: Tenant A attempts to delete bulk permissions from CustomB -> 404
        del_bulk_a = await client.request(
            "DELETE",
            "/api/v1/rbac/roles/CustomB/permissions",
            json={"permissions": ["materials.view"]},
        )
        assert del_bulk_a.status_code == 404

        # Step 8: Tenant A attempts to delete CustomB role -> 404
        del_role_a = await client.delete("/api/v1/rbac/roles/CustomB")
        assert del_role_a.status_code == 404

        # Step 9: Tenant A attempts to reset CustomB role defaults -> 404
        reset_res_a = await client.post("/api/v1/rbac/roles/CustomB/reset-defaults")
        assert reset_res_a.status_code == 404

        # Step 10: Tenant A attempts to read user overrides of Tenant B user (id=8821)
        # MUST return 404 at DB query level without information leakage
        get_override_a = await client.get("/api/v1/rbac/users/8821/overrides")
        assert get_override_a.status_code == 404, f"Tenant A must get 404 on Tenant B user, got {get_override_a.status_code}"

        # Step 11: Tenant A attempts to modify user overrides of Tenant B user
        # MUST return 404 at DB query level without information leakage
        put_override_a = await client.put(
            "/api/v1/rbac/users/8821/overrides",
            json={"overrides": [{"permission": "materials.view", "is_granted": True}]},
        )
        assert put_override_a.status_code == 404

        # Cleanup Tenant B role
        app.dependency_overrides[get_current_active_user] = lambda: admin_b
        del_b = await client.delete("/api/v1/rbac/roles/CustomB")
        assert del_b.status_code == 200

        # DB cleanup
        async for db in get_db_session():
            await db.execute(delete(User).where(User.id.in_([admin_a.id, admin_b.id, user_b.id])))
            await db.commit()

    app.dependency_overrides.clear()


# ==============================================================================
# 4. PRIVILEGE ESCALATION & ROLE-NAME PROTECTION
# ==============================================================================

@pytest.mark.asyncio
async def test_self_escalation_and_role_protection():
    admin = make_security_user(user_id=8830, company_id=1, role="Admin")
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        # DB ensure user exists
        async for db in get_db_session():
            existing = await db.scalar(select(User).where(User.id == admin.id))
            if not existing:
                db.add(User(
                    id=admin.id,
                    email=admin.email,
                    role=admin.role,
                    company_id=admin.company_id,
                    is_active=True,
                    is_super_admin=False,
                ))
                await db.commit()

        # 1. Direct self-override escalation must be 403 Forbidden
        res_self = await client.put(
            f"/api/v1/rbac/users/{admin.id}/overrides",
            json={"overrides": [{"permission": "settings.edit", "is_granted": True}]},
        )
        assert res_self.status_code == 403, f"Self-override modification must return 403, got {res_self.status_code}"

        # 2. Cannot create custom role shadowing built-in role names (case-insensitive)
        for shadowed in ["admin", "ADMIN", " Admin ", "client", "Client", "labour", "SiteEngineer"]:
            res_shadow = await client.post(
                "/api/v1/rbac/roles",
                json={"name": shadowed, "display_name": "Shadowed"},
            )
            assert res_shadow.status_code == 400, f"Must reject shadowed role '{shadowed}', got {res_shadow.status_code}"

        # 3. Cannot delete built-in system role (case-insensitive)
        for sys_role in ["Admin", "admin", "ADMIN", "Client", "client"]:
            res_del_sys = await client.delete(f"/api/v1/rbac/roles/{sys_role}")
            assert res_del_sys.status_code == 400, f"Must reject deleting built-in role '{sys_role}', got {res_del_sys.status_code}"

        # DB cleanup
        async for db in get_db_session():
            await db.execute(delete(User).where(User.id == admin.id))
            await db.commit()

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_assigned_custom_role_deletion_conflict():
    admin = make_security_user(user_id=8840, company_id=1, role="Admin")
    assigned_user = make_security_user(user_id=8841, company_id=1, role="RoleAssignedTest")

    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        # Create role
        role_name = "RoleAssignedTest"
        await client.post(
            "/api/v1/rbac/roles",
            json={"name": role_name, "display_name": "Role Assigned Test"},
        )

        async for db in get_db_session():
            # Add user with this custom role
            existing = await db.scalar(select(User).where(User.id == assigned_user.id))
            if not existing:
                db.add(User(
                    id=assigned_user.id,
                    email=assigned_user.email,
                    role=role_name,
                    company_id=admin.company_id,
                    is_active=True,
                    is_super_admin=False,
                ))
                await db.commit()

        # Attempt to delete custom role that has active assigned users -> 409 Conflict
        del_res = await client.delete(f"/api/v1/rbac/roles/{role_name}")
        assert del_res.status_code == 409, f"Deleting assigned role must return 409 Conflict, got {del_res.status_code}"

        # Reassign/remove user and then delete -> 200 OK
        async for db in get_db_session():
            await db.execute(delete(User).where(User.id == assigned_user.id))
            await db.commit()

        del_ok = await client.delete(f"/api/v1/rbac/roles/{role_name}")
        assert del_ok.status_code == 200

    app.dependency_overrides.clear()


# ==============================================================================
# 5. WILDCARD & NEGATIVE OVERRIDE MATRIX
# ==============================================================================

@pytest.mark.asyncio
async def test_module_wildcard_with_negative_action_override():
    """
    Role: work_orders.*
    User override: work_orders.view = False
    Expected:
    - work_orders.view -> False (Denied)
    - work_orders.create -> True (Preserved!)
    - work_orders.edit -> True (Preserved!)
    - work_orders.delete -> True (Preserved!)
    """
    test_user = make_security_user(user_id=8850, company_id=1, role="CustomTesterRole")

    async for db in get_db_session():
        # Ensure user exists
        existing_u = await db.scalar(select(User).where(User.id == test_user.id))
        if not existing_u:
            db.add(User(
                id=test_user.id,
                email=test_user.email,
                role=test_user.role,
                company_id=test_user.company_id,
                is_active=True,
                is_super_admin=False,
            ))
            await db.commit()

        # Ensure custom role exists for company 1
        role_obj = await db.scalar(select(Role).where(Role.name == "CustomTesterRole", Role.company_id == 1))
        if not role_obj:
            role_obj = Role(name="CustomTesterRole", display_name="Custom Tester", company_id=1, is_system=False)
            db.add(role_obj)
            await db.flush()

        # Clean old mappings
        await db.execute(delete(RolePermission).where(RolePermission.role_id == role_obj.id))
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == test_user.id))
        await db.commit()

        # Assign work_orders.* to role
        perm_wc = await db.scalar(select(Permission).where(Permission.code == "work_orders.*"))
        assert perm_wc is not None, "work_orders.* must exist in seeded permissions"
        db.add(RolePermission(role="CustomTesterRole", permission_id=perm_wc.id, role_id=role_obj.id))
        await db.commit()

        # 1. Without overrides -> effective perms includes work_orders.*
        perms_before = await get_effective_user_permissions(db, test_user)
        assert "work_orders.*" in perms_before

        # 2. Add negative override: work_orders.view = False
        perm_view = await db.scalar(select(Permission).where(Permission.code == "work_orders.view"))
        db.add(UserPermissionOverride(user_id=test_user.id, permission_id=perm_view.id, is_granted=False))
        await db.commit()

        # Evaluate effective permissions
        perms_after = await get_effective_user_permissions(db, test_user)

        # Verification: work_orders.view MUST NOT be present
        assert "work_orders.view" not in perms_after, "work_orders.view must be revoked"
        assert "work_orders.*" not in perms_after, "work_orders.* must be discarded to avoid bypass"

        # Concrete sibling actions MUST be preserved
        assert "work_orders.create" in perms_after, "work_orders.create must be preserved!"
        assert "work_orders.edit" in perms_after, "work_orders.edit must be preserved!"
        assert "work_orders.delete" in perms_after, "work_orders.delete must be preserved!"

        # 3. Add explicit positive override: materials.view = True
        perm_mat = await db.scalar(select(Permission).where(Permission.code == "materials.view"))
        db.add(UserPermissionOverride(user_id=test_user.id, permission_id=perm_mat.id, is_granted=True))
        await db.commit()

        perms_mixed = await get_effective_user_permissions(db, test_user)
        assert "materials.view" in perms_mixed, "materials.view positive override must be granted"
        assert "work_orders.view" not in perms_mixed, "work_orders.view must remain revoked"
        assert "work_orders.create" in perms_mixed, "work_orders.create must remain preserved"

        # 4. Reset negative override: delete negative override, keep materials.view
        await db.execute(
            delete(UserPermissionOverride).where(
                UserPermissionOverride.user_id == test_user.id,
                UserPermissionOverride.permission_id == perm_view.id,
            )
        )
        await db.commit()

        perms_reset = await get_effective_user_permissions(db, test_user)
        # Now work_orders.* is restored and work_orders.view is again accessible
        assert "work_orders.*" in perms_reset
        assert "materials.view" in perms_reset

        # Cleanup
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == test_user.id))
        await db.execute(delete(RolePermission).where(RolePermission.role_id == role_obj.id))
        await db.execute(delete(Role).where(Role.id == role_obj.id))
        await db.execute(delete(User).where(User.id == test_user.id))
        await db.commit()


@pytest.mark.asyncio
async def test_global_wildcard_with_negative_action_override():
    """
    Role: * (Global Wildcard)
    User override: work_orders.view = False
    Expected:
    - work_orders.view -> False (Denied)
    - All other catalog permissions (e.g. work_orders.create, materials.view, invoices.view) preserved!
    """
    test_user = make_security_user(user_id=8860, company_id=1, role="GlobalWildcardRole")

    async for db in get_db_session():
        # Ensure user exists
        existing_u = await db.scalar(select(User).where(User.id == test_user.id))
        if not existing_u:
            db.add(User(
                id=test_user.id,
                email=test_user.email,
                role=test_user.role,
                company_id=test_user.company_id,
                is_active=True,
                is_super_admin=False,
            ))
            await db.commit()

        role_obj = Role(name="GlobalWildcardRole", display_name="Global Wildcard Role", company_id=1, is_system=False)
        db.add(role_obj)
        await db.flush()

        perm_star = await db.scalar(select(Permission).where(Permission.code == "*"))
        assert perm_star is not None, "Global wildcard '*' must exist in catalog"
        db.add(RolePermission(role="GlobalWildcardRole", permission_id=perm_star.id, role_id=role_obj.id))

        perm_view = await db.scalar(select(Permission).where(Permission.code == "work_orders.view"))
        db.add(UserPermissionOverride(user_id=test_user.id, permission_id=perm_view.id, is_granted=False))
        await db.commit()

        perms = await get_effective_user_permissions(db, test_user)

        assert "work_orders.view" not in perms, "work_orders.view must be denied"
        assert "*" not in perms, "'*' must be discarded to prevent bypass"
        assert "work_orders.*" not in perms, "'work_orders.*' must be discarded"

        # Concrete permissions across modules must be preserved
        assert "work_orders.create" in perms, "work_orders.create must be preserved"
        assert "materials.view" in perms, "materials.view must be preserved"
        assert "invoices.view" in perms, "invoices.view must be preserved"

        # Cleanup
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == test_user.id))
        await db.execute(delete(RolePermission).where(RolePermission.role_id == role_obj.id))
        await db.execute(delete(Role).where(Role.id == role_obj.id))
        await db.execute(delete(User).where(User.id == test_user.id))
        await db.commit()


# ==============================================================================
# 6. TRANSACTIONAL RBAC AUDIT LOGGING VERIFICATION
# ==============================================================================

@pytest.mark.asyncio
async def test_rbac_mutations_generate_audit_logs():
    admin = make_security_user(user_id=8870, company_id=1, role="Admin")
    target_user = make_security_user(user_id=8871, company_id=1, role="Labour")

    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        async for db in get_db_session():
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

        role_name = "AuditTestRole"

        # 1. Create Role -> Audit log ROLE_CREATE
        res_create = await client.post(
            "/api/v1/rbac/roles",
            json={"name": role_name, "display_name": "Audit Test Role", "description": "Test"},
        )
        assert res_create.status_code == 200

        # 2. Add Permission -> Audit log ROLE_PERMISSIONS_ADD
        res_add = await client.post(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permission": "materials.view"},
        )
        assert res_add.status_code == 200

        # 3. Update Permissions -> Audit log ROLE_PERMISSIONS_UPDATE
        res_put = await client.put(
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permissions": ["materials.view", "labour.view"]},
        )
        assert res_put.status_code == 200

        # 4. Delete Single Permission -> Audit log ROLE_PERMISSION_DELETE
        res_del_single = await client.delete(f"/api/v1/rbac/roles/{role_name}/permissions/labour.view")
        assert res_del_single.status_code == 200

        # 5. Delete Bulk Permissions -> Audit log ROLE_PERMISSIONS_BULK_DELETE
        res_del_bulk = await client.request(
            "DELETE",
            f"/api/v1/rbac/roles/{role_name}/permissions",
            json={"permissions": ["materials.view"]},
        )
        assert res_del_bulk.status_code == 200

        # 6. User Override Update -> Audit log USER_OVERRIDES_UPDATE
        res_override = await client.put(
            f"/api/v1/rbac/users/{target_user.id}/overrides",
            json={"overrides": [{"permission": "materials.view", "is_granted": True}]},
        )
        assert res_override.status_code == 200

        # 7. Delete Role -> Audit log ROLE_DELETE
        res_del_role = await client.delete(f"/api/v1/rbac/roles/{role_name}")
        assert res_del_role.status_code == 200

        # Verify audit logs in database
        async for db in get_db_session():
            logs_res = await db.execute(
                select(RBACAuditLog).where(
                    RBACAuditLog.actor_id == admin.id,
                ).order_by(RBACAuditLog.id.asc())
            )
            logs = logs_res.scalars().all()
            actions = [log.action for log in logs]

            assert "ROLE_CREATE" in actions
            assert "ROLE_PERMISSIONS_ADD" in actions
            assert "ROLE_PERMISSIONS_UPDATE" in actions
            assert "ROLE_PERMISSION_DELETE" in actions
            assert "ROLE_PERMISSIONS_BULK_DELETE" in actions
            assert "USER_OVERRIDES_UPDATE" in actions
            assert "ROLE_DELETE" in actions

            # Check company_id isolation: tenant admin operations must record company_id = 1
            for log in logs:
                assert log.company_id == 1, f"Audit log {log.action} must have company_id=1, got {log.company_id}"

            # Cleanup
            await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == target_user.id))
            await db.execute(delete(RBACAuditLog).where(RBACAuditLog.actor_id == admin.id))
            await db.execute(delete(User).where(User.id.in_([admin.id, target_user.id])))
            await db.commit()

    app.dependency_overrides.clear()


# ==============================================================================
# 7. TRANSACTIONAL ROLLBACK ATOMICITY VERIFICATION
# ==============================================================================

@pytest.mark.asyncio
async def test_rbac_mutation_rollback_atomicity():
    """
    Verify that:
    - successful RBAC mutation => mutation + audit record committed
    - failed RBAC mutation => mutation + audit record rolled back
    There must never be:
    - mutation failed + success audit
    - mutation succeeded + missing audit
    """
    admin = make_security_user(user_id=8880, company_id=1, role="Admin")
    app.dependency_overrides[get_current_active_user] = lambda: admin

    async with get_test_client() as client:
        async for db in get_db_session():
            existing = await db.scalar(select(User).where(User.id == admin.id))
            if not existing:
                db.add(User(
                    id=admin.id,
                    email=admin.email,
                    role=admin.role,
                    company_id=admin.company_id,
                    is_active=True,
                    is_super_admin=False,
                ))
                await db.commit()

        # 1. Failed request (Validation Error / Bad Request) -> e.g. empty permissions list
        res_fail = await client.post(
            "/api/v1/rbac/roles/NonExistentRole/permissions",
            json={"permission": ""},
        )
        assert res_fail.status_code in [400, 404, 422]

        # Verify NO audit record was written for this failed request
        async for db in get_db_session():
            audit_fail = await db.scalar(
                select(RBACAuditLog).where(
                    RBACAuditLog.actor_id == admin.id,
                    RBACAuditLog.target_id == "NonExistentRole",
                )
            )
            assert audit_fail is None, "Failed request must NOT create any audit log record"

        # 2. Direct Session Rollback Test: simulate error before commit
        async for db in get_db_session():
            # Create a test role object and audit record
            aborted_role = Role(
                name="AbortedRole",
                display_name="Aborted Role",
                company_id=admin.company_id,
                is_system=False,
            )
            db.add(aborted_role)
            await record_rbac_audit(
                db=db,
                actor=admin,
                action="ROLE_CREATE",
                target_type="ROLE",
                target_id="AbortedRole",
                company_id=admin.company_id,
            )
            # Rollback transaction
            await db.rollback()

        # Verify neither the role nor the audit log persisted
        async for db in get_db_session():
            role_persisted = await db.scalar(select(Role).where(Role.name == "AbortedRole"))
            audit_persisted = await db.scalar(
                select(RBACAuditLog).where(RBACAuditLog.target_id == "AbortedRole")
            )
            assert role_persisted is None, "Rolled-back role mutation must NOT persist"
            assert audit_persisted is None, "Rolled-back audit record must NOT persist"

            # Cleanup admin user
            await db.execute(delete(User).where(User.id == admin.id))
            await db.commit()

    app.dependency_overrides.clear()


# ==============================================================================
# 8. WILDCARD & NEGATIVE OVERRIDE COMPREHENSIVE MATRIX (CASES A, B, C, D)
# ==============================================================================

@pytest.mark.asyncio
async def test_wildcard_override_matrix_cases_a_b_c_d():
    """
    Exact matrix cases:
    Case A: work_orders.* + work_orders.view = false
            => view denied, create/edit/delete allowed
    Case B: * + work_orders.view = false
            => work_orders.view denied, unrelated catalog permissions remain available
    Case C: multiple negative overrides (e.g. work_orders.view = false AND work_orders.delete = false)
            => view denied, delete denied, create/edit allowed
    Case D: remove negative overrides
            => previous wildcard access restored
    """
    test_user = make_security_user(user_id=8885, company_id=1, role="MatrixTesterRole")

    async for db in get_db_session():
        # Setup user and custom role
        existing_u = await db.scalar(select(User).where(User.id == test_user.id))
        if not existing_u:
            db.add(User(
                id=test_user.id,
                email=test_user.email,
                role=test_user.role,
                company_id=test_user.company_id,
                is_active=True,
                is_super_admin=False,
            ))
            await db.commit()

        role_obj = Role(name="MatrixTesterRole", display_name="Matrix Tester Role", company_id=1, is_system=False)
        db.add(role_obj)
        await db.flush()

        perm_wo_wildcard = await db.scalar(select(Permission).where(Permission.code == "work_orders.*"))
        perm_view = await db.scalar(select(Permission).where(Permission.code == "work_orders.view"))
        perm_del = await db.scalar(select(Permission).where(Permission.code == "work_orders.delete"))
        perm_global = await db.scalar(select(Permission).where(Permission.code == "*"))

        # ----------------------------------------------------------------------
        # CASE A: work_orders.* + work_orders.view = false
        # ----------------------------------------------------------------------
        rp_a = RolePermission(role="MatrixTesterRole", permission_id=perm_wo_wildcard.id, role_id=role_obj.id)
        db.add(rp_a)
        ov_view = UserPermissionOverride(user_id=test_user.id, permission_id=perm_view.id, is_granted=False)
        db.add(ov_view)
        await db.commit()

        perms_a = await get_effective_user_permissions(db, test_user)
        assert "work_orders.view" not in perms_a, "Case A: view must be denied"
        assert "work_orders.*" not in perms_a, "Case A: wildcard must be expanded/discarded to enforce deny"
        assert "work_orders.create" in perms_a, "Case A: create must be preserved"
        assert "work_orders.edit" in perms_a, "Case A: edit must be preserved"
        assert "work_orders.delete" in perms_a, "Case A: delete must be preserved"

        # ----------------------------------------------------------------------
        # CASE C: Multiple negative overrides (view = false, delete = false)
        # ----------------------------------------------------------------------
        ov_del = UserPermissionOverride(user_id=test_user.id, permission_id=perm_del.id, is_granted=False)
        db.add(ov_del)
        await db.commit()

        perms_c = await get_effective_user_permissions(db, test_user)
        assert "work_orders.view" not in perms_c, "Case C: view must be denied"
        assert "work_orders.delete" not in perms_c, "Case C: delete must be denied"
        assert "work_orders.create" in perms_c, "Case C: create must be preserved"
        assert "work_orders.edit" in perms_c, "Case C: edit must be preserved"

        # ----------------------------------------------------------------------
        # CASE D: Remove negative overrides -> previous wildcard access restored
        # ----------------------------------------------------------------------
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == test_user.id))
        await db.commit()

        perms_d = await get_effective_user_permissions(db, test_user)
        assert "work_orders.*" in perms_d, "Case D: work_orders.* must be restored"

        # ----------------------------------------------------------------------
        # CASE B: * (global) + work_orders.view = false
        # ----------------------------------------------------------------------
        await db.execute(delete(RolePermission).where(RolePermission.role_id == role_obj.id))
        rp_b = RolePermission(role="MatrixTesterRole", permission_id=perm_global.id, role_id=role_obj.id)
        db.add(rp_b)
        db.add(UserPermissionOverride(user_id=test_user.id, permission_id=perm_view.id, is_granted=False))
        await db.commit()

        perms_b = await get_effective_user_permissions(db, test_user)
        assert "work_orders.view" not in perms_b, "Case B: work_orders.view must be denied"
        assert "*" not in perms_b, "Case B: '*' must be discarded"
        # Unrelated permissions from catalog must remain available
        assert "work_orders.create" in perms_b, "Case B: work_orders.create must be available"
        assert "materials.view" in perms_b, "Case B: materials.view must be available"
        assert "invoices.view" in perms_b, "Case B: invoices.view must be available"

        # Cleanup
        await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id == test_user.id))
        await db.execute(delete(RolePermission).where(RolePermission.role_id == role_obj.id))
        await db.execute(delete(Role).where(Role.id == role_obj.id))
        await db.execute(delete(User).where(User.id == test_user.id))
        await db.commit()


# ==============================================================================
# 9. RBAC AUDIT LOG QUERY API VERIFICATION (GET /api/v1/rbac/audit-logs)
# ==============================================================================

@pytest.mark.asyncio
async def test_rbac_audit_logs_query_api():
    """
    Test GET /api/v1/rbac/audit-logs:
    - unauthenticated -> 401
    - unauthorized user -> 403
    - tenant admin sees own tenant only
    - tenant A cannot see tenant B audit logs
    - pagination
    - date filtering
    - action filtering
    - actor filtering
    - target type filtering
    - deterministic ordering
    """
    admin_a = make_security_user(user_id=8890, company_id=201, role="Admin")
    admin_b = make_security_user(user_id=8891, company_id=202, role="Admin")
    super_admin = make_security_user(user_id=8892, company_id=None, role="Admin", is_super_admin=True)
    labour_user = make_security_user(user_id=8893, company_id=201, role="Labour")

    app.dependency_overrides.clear()
    async with get_test_client() as client:
        # 1. Unauthenticated -> 401
        res_401 = await client.get("/api/v1/rbac/audit-logs")
        assert res_401.status_code == 401

        # 2. Unauthorized (Labour) -> 403
        app.dependency_overrides[get_current_active_user] = lambda: labour_user
        res_403 = await client.get("/api/v1/rbac/audit-logs")
        assert res_403.status_code == 403

        # Seed audit records in DB for Company 201 (Tenant A) and Company 202 (Tenant B)
        async for db in get_db_session():
            # Clean any old test records for these tenants
            await db.execute(delete(RBACAuditLog).where(RBACAuditLog.company_id.in_([201, 202])))
            await db.commit()

            # Insert 5 logs for Tenant A
            for i in range(1, 6):
                db.add(RBACAuditLog(
                    company_id=201,
                    actor_id=admin_a.id,
                    action=f"ACTION_A_{i}",
                    target_type="ROLE" if i <= 3 else "USER_OVERRIDE",
                    target_id=f"Target_A_{i}",
                    permission="materials.view",
                ))
            # Insert 3 logs for Tenant B
            for i in range(1, 4):
                db.add(RBACAuditLog(
                    company_id=202,
                    actor_id=admin_b.id,
                    action=f"ACTION_B_{i}",
                    target_type="ROLE",
                    target_id=f"Target_B_{i}",
                    permission="labour.view",
                ))
            await db.commit()

        # 3. Tenant A Admin queries audit logs -> Sees ONLY Tenant A records
        app.dependency_overrides[get_current_active_user] = lambda: admin_a
        res_a = await client.get("/api/v1/rbac/audit-logs")
        assert res_a.status_code == 200
        data_a = res_a.json()
        assert data_a["total"] == 5
        assert len(data_a["items"]) == 5
        for item in data_a["items"]:
            assert item["company_id"] == 201, "Tenant A must only see Company 201 records"
            assert "ACTION_B" not in item["action"], "Tenant A must never see Tenant B actions"

        # 4. Tenant B Admin queries audit logs -> Sees ONLY Tenant B records
        app.dependency_overrides[get_current_active_user] = lambda: admin_b
        res_b = await client.get("/api/v1/rbac/audit-logs")
        assert res_b.status_code == 200
        data_b = res_b.json()
        assert data_b["total"] == 3
        for item in data_b["items"]:
            assert item["company_id"] == 202, "Tenant B must only see Company 202 records"

        # 5. Client attempt to bypass tenant boundary by passing company_id=202 as Tenant A
        app.dependency_overrides[get_current_active_user] = lambda: admin_a
        res_bypass = await client.get("/api/v1/rbac/audit-logs?company_id=202")
        assert res_bypass.status_code == 200
        data_bypass = res_bypass.json()
        # Must strictly return Tenant A logs despite client query param
        for item in data_bypass["items"]:
            assert item["company_id"] == 201

        # 6. Pagination test
        res_page = await client.get("/api/v1/rbac/audit-logs?page=1&page_size=2")
        assert res_page.status_code == 200
        data_page = res_page.json()
        assert data_page["total"] == 5
        assert len(data_page["items"]) == 2
        assert data_page["page"] == 1
        assert data_page["page_size"] == 2

        # 7. Action Filter
        res_filter_act = await client.get("/api/v1/rbac/audit-logs?action=ACTION_A_1")
        assert res_filter_act.status_code == 200
        data_act = res_filter_act.json()
        assert data_act["total"] == 1
        assert data_act["items"][0]["action"] == "ACTION_A_1"

        # 8. Target Type Filter
        res_filter_tgt = await client.get("/api/v1/rbac/audit-logs?target_type=USER_OVERRIDE")
        assert res_filter_tgt.status_code == 200
        data_tgt = res_filter_tgt.json()
        assert data_tgt["total"] == 2
        for item in data_tgt["items"]:
            assert item["target_type"] == "USER_OVERRIDE"

        # 9. Actor ID Filter
        res_filter_actor = await client.get(f"/api/v1/rbac/audit-logs?actor_id={admin_a.id}")
        assert res_filter_actor.status_code == 200
        assert res_filter_actor.json()["total"] == 5

        # 10. Super Admin access
        app.dependency_overrides[get_current_active_user] = lambda: super_admin
        res_sa = await client.get("/api/v1/rbac/audit-logs")
        assert res_sa.status_code == 200
        # Super admin sees both Tenant A and Tenant B
        assert res_sa.json()["total"] >= 8

        # Super admin filter by company_id=202
        res_sa_filtered = await client.get("/api/v1/rbac/audit-logs?company_id=202")
        assert res_sa_filtered.status_code == 200
        assert res_sa_filtered.json()["total"] == 3

        # Deterministic ordering check (newest first, id descending)
        items = res_sa.json()["items"]
        for i in range(len(items) - 1):
            assert items[i]["id"] > items[i + 1]["id"]

        # Cleanup DB
        async for db in get_db_session():
            await db.execute(delete(RBACAuditLog).where(RBACAuditLog.company_id.in_([201, 202])))
            await db.commit()

    app.dependency_overrides.clear()


# ==============================================================================
# 10. ROLE ASSIGNMENT ENFORCEMENT, ESCALATION PREVENTION & AUDIT
# ==============================================================================

@pytest.mark.asyncio
async def test_role_assignment_escalation_and_audit():
    """
    Enforcement scenario tests:
    - Tenant Admin cannot escalate a user to Admin via PUT /users/{id} (403)
    - Tenant Admin cannot escalate a user to Super Admin via mass-assignment (is_super_admin remains False)
    - Tenant A cannot modify Tenant B user's role (404)
    - Legitimate role change logs USER_ROLE_CHANGE to RBACAuditLog
    """
    admin_a = make_security_user(user_id=8910, company_id=301, role="Admin")
    target_user_a = make_security_user(user_id=8911, company_id=301, role="SiteEngineer")
    user_b = make_security_user(user_id=8920, company_id=302, role="SiteEngineer")

    app.dependency_overrides.clear()

    async with get_test_client() as client:
        # Seed users in DB
        async for db in get_db_session():
            for u in [admin_a, target_user_a, user_b]:
                existing = await db.scalar(select(User).where(User.id == u.id))
                if not existing:
                    db.add(User(
                        id=u.id,
                        email=u.email,
                        mobile=f"999300{u.id}",
                        role=u.role,
                        company_id=u.company_id,
                        is_active=True,
                        is_super_admin=False,
                    ))
            await db.commit()

        app.dependency_overrides[get_current_active_user] = lambda: admin_a

        # 1. Tenant Admin attempts to escalate target_user_a to Admin -> 403 Forbidden
        res_esc = await client.put(
            f"/api/v1/users/{target_user_a.id}",
            params={"role": "Admin", "email": target_user_a.email, "mobile_number": f"999300{target_user_a.id}"},
        )
        assert res_esc.status_code == 403, f"Promoting user to Admin by Tenant Admin must return 403, got {res_esc.status_code}"

        # 2. Tenant Admin attempts to modify Tenant B user's role -> 404 Not Found
        res_cross = await client.put(
            f"/api/v1/users/{user_b.id}",
            params={"role": "Accountant", "email": user_b.email, "mobile_number": f"999300{user_b.id}"},
        )
        assert res_cross.status_code == 404, f"Cross-tenant user update must return 404, got {res_cross.status_code}"

        # 3. Legitimate role change: SiteEngineer -> Accountant
        res_legit = await client.put(
            f"/api/v1/users/{target_user_a.id}",
            params={"role": "Accountant", "email": target_user_a.email, "mobile_number": f"999300{target_user_a.id}"},
        )
        assert res_legit.status_code == 200
        assert res_legit.json()["role"] == "Accountant"

        # Verify RBAC audit log was written for this role assignment
        async for db in get_db_session():
            audit_entry = await db.scalar(
                select(RBACAuditLog).where(
                    RBACAuditLog.actor_id == admin_a.id,
                    RBACAuditLog.action == "USER_ROLE_CHANGE",
                    RBACAuditLog.target_id == str(target_user_a.id),
                )
            )
            assert audit_entry is not None, "USER_ROLE_CHANGE audit log must be recorded"
            assert audit_entry.company_id == 301

            # Cleanup
            await db.execute(delete(RBACAuditLog).where(RBACAuditLog.company_id.in_([301, 302])))
            await db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_([admin_a.id, target_user_a.id, user_b.id])))
            await db.execute(delete(UserAuditLog).where(UserAuditLog.changed_by.in_([admin_a.id, target_user_a.id, user_b.id])))
            await db.execute(delete(UserAuditLog).where(UserAuditLog.user_id.in_([admin_a.id, target_user_a.id, user_b.id])))
            await db.execute(update(User).where(User.id.in_([admin_a.id, target_user_a.id, user_b.id])).values(updated_by=None))
            await db.execute(delete(User).where(User.id.in_([admin_a.id, target_user_a.id, user_b.id])))
            await db.commit()

    app.dependency_overrides.clear()


