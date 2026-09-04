import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete, select, text
from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, get_db_session, require_permission
from app.models.company import Company
from app.models.rbac import Permission, RolePermission, UserPermissionOverride
from app.models.user import ActivityLog, User, UserAuditLog, UserRole
from fastapi import APIRouter, Depends


# Router specifically for verifying P0-4 Tenant Admin permission enforcement
p0_test_router = APIRouter(prefix="/api/v1/test-p0-enforcement", tags=["TestP0"])


@p0_test_router.get("/reports-view")
async def _endpoint_reports_view(
    current_user: User = Depends(require_permission("reports.view")),
):
    return {"status": "ok", "user_id": current_user.id}


@p0_test_router.get("/confidential-export")
async def _endpoint_confidential_export(
    current_user: User = Depends(require_permission("confidential.export")),
):
    return {"status": "ok", "user_id": current_user.id}


app.include_router(p0_test_router)


def get_client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


# ==============================================================================
# Scenario A: P0-4 Tenant Admin Permission Revoke / Regrant / No Blanket Bypass
# ==============================================================================
@pytest.mark.asyncio
async def test_p0_4_tenant_admin_permission_lifecycle():
    async for db in get_db_session():
        # Ensure permission exists
        perm = await db.scalar(select(Permission).where(Permission.code == "reports.view"))
        if not perm:
            perm = Permission(module="reports", action="view", code="reports.view")
            db.add(perm)
            await db.commit()

        # Clean any preexisting role permission
        await db.execute(delete(RolePermission).where(RolePermission.role == "Admin", RolePermission.permission_id == perm.id))
        await db.commit()

        # Tenant Admin user
        tenant_admin = User(
            id=7701,
            email="tenant_admin_p0@test.com",
            role="Admin",
            is_active=True,
            company_id=1,
            is_super_admin=False,
        )

        app.dependency_overrides[get_current_user] = lambda: tenant_admin
        app.dependency_overrides[get_current_active_user] = lambda: tenant_admin

        async with get_client() as client:
            # 4. Normal role name "Admin" alone MUST NOT bypass permission
            res_no_perm = await client.get("/api/v1/test-p0-enforcement/reports-view")
            assert res_no_perm.status_code == 403, "Admin without permission must be denied (no blanket bypass)"

            # 1. Grant permission to Admin role -> endpoint allowed (200)
            db.add(RolePermission(role="Admin", permission_id=perm.id))
            await db.commit()

            res_granted = await client.get("/api/v1/test-p0-enforcement/reports-view")
            assert res_granted.status_code == 200, "Admin with granted permission must be allowed"

            # 2. Same Admin permission revoked in DB -> endpoint returns 403 immediately
            await db.execute(delete(RolePermission).where(RolePermission.role == "Admin", RolePermission.permission_id == perm.id))
            await db.commit()

            res_revoked = await client.get("/api/v1/test-p0-enforcement/reports-view")
            assert res_revoked.status_code == 403, "Revoked permission must return 403 immediately"

            # 3. Permission re-granted -> endpoint allowed again
            db.add(RolePermission(role="Admin", permission_id=perm.id))
            await db.commit()

            res_regranted = await client.get("/api/v1/test-p0-enforcement/reports-view")
            assert res_regranted.status_code == 200, "Re-granted permission must allow access again"

        # Cleanup
        await db.execute(delete(RolePermission).where(RolePermission.role == "Admin", RolePermission.permission_id == perm.id))
        await db.commit()
        app.dependency_overrides.clear()


# ==============================================================================
# Scenario B: P0-2 update_role_status Tenant Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_p0_2_update_role_status_tenant_isolation():
    async for db in get_db_session():
        uid_suffix = "p02_test"
        comp_a_id = 1
        comp_b_id = 2

        # Clean existing test users if any
        existing_user_ids = (await db.scalars(select(User.id).where(User.email.in_([
            f"admin_a_{uid_suffix}@test.com",
            f"user_a_{uid_suffix}@test.com",
            f"user_b_{uid_suffix}@test.com",
            f"user_sa_{uid_suffix}@test.com",
        ])))).all()
        if existing_user_ids:
            await db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(existing_user_ids)))
            await db.execute(text(f"UPDATE users SET updated_by = NULL WHERE id IN ({','.join(str(i) for i in existing_user_ids)})"))
            await db.execute(delete(User).where(User.id.in_(existing_user_ids)))
            await db.commit()

        # Insert real Admin user in DB for Company A so updated_by foreign key constraint passes
        admin_a = User(
            email=f"admin_a_{uid_suffix}@test.com",
            hashed_password="pw",
            full_name="Admin A",
            mobile="9900112232",
            role=UserRole.ADMIN.value,
            is_active=True,
            company_id=comp_a_id,
            is_super_admin=False,
        )
        user_a = User(
            email=f"user_a_{uid_suffix}@test.com",
            hashed_password="pw",
            full_name="User A",
            mobile="9900112233",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            company_id=comp_a_id,
            is_super_admin=False,
        )
        user_b = User(
            email=f"user_b_{uid_suffix}@test.com",
            hashed_password="pw",
            full_name="User B",
            mobile="9900112234",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            company_id=comp_b_id,
            is_super_admin=False,
        )
        user_sa = User(
            email=f"user_sa_{uid_suffix}@test.com",
            hashed_password="pw",
            full_name="Super User",
            mobile="9900112235",
            role=UserRole.SITE_ENGINEER.value,  # Role matches but is_super_admin is True
            is_active=True,
            company_id=comp_a_id,
            is_super_admin=True,
        )

        db.add_all([admin_a, user_a, user_b, user_sa])
        await db.commit()
        await db.refresh(admin_a)
        await db.refresh(user_a)
        await db.refresh(user_b)
        await db.refresh(user_sa)

        app.dependency_overrides[get_current_user] = lambda: admin_a
        app.dependency_overrides[get_current_active_user] = lambda: admin_a

        async with get_client() as client:
            # Company A Admin deactivates SiteEngineer role
            res = await client.put(
                f"/api/v1/users/roles/{UserRole.SITE_ENGINEER.value}/status?is_active=false"
            )
            assert res.status_code == 200

            # Reload users from DB (commit ends previous snapshot in MySQL REPEATABLE READ)
            await db.commit()
            await db.refresh(user_a)
            await db.refresh(user_b)
            await db.refresh(user_sa)

            # Company A user: MUST be deactivated
            assert user_a.is_active is False, "Company A user must be deactivated"

            # Company B user: MUST remain unchanged (active=True)
            assert user_b.is_active is True, "Company B user must remain active"

            # Super Admin user: MUST NOT be modified (active=True)
            assert user_sa.is_active is True, "Super Admin user must never be modified by update_role_status"

            # Invariant: non-Super Admin with company_id=None must get 403
            admin_orphan = User(
                id=7703,
                email=f"orphan_{uid_suffix}@test.com",
                role=UserRole.ADMIN.value,
                is_active=True,
                company_id=None,
                is_super_admin=False,
            )
            app.dependency_overrides[get_current_user] = lambda: admin_orphan
            app.dependency_overrides[get_current_active_user] = lambda: admin_orphan

            res_orphan = await client.put(
                f"/api/v1/users/roles/{UserRole.SITE_ENGINEER.value}/status?is_active=true"
            )
            assert res_orphan.status_code == 403, "Non-SA with company_id=None must be rejected with 403"

        # Cleanup
        all_ids = [admin_a.id, user_a.id, user_b.id, user_sa.id]
        await db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(all_ids)))
        await db.execute(text(f"UPDATE users SET updated_by = NULL WHERE id IN ({','.join(str(i) for i in all_ids)})"))
        await db.execute(delete(User).where(User.id.in_(all_ids)))
        await db.commit()
        app.dependency_overrides.clear()


# ==============================================================================
# Scenario C: P0-3 User Audit Log IDOR with 404 Masking
# ==============================================================================
@pytest.mark.asyncio
async def test_p0_3_user_audit_log_idor():
    async for db in get_db_session():
        uid_suffix = "p03_test"
        comp_a_id = 1
        comp_b_id = 2

        # Create target user in Company A and Company B
        user_a = User(
            email=f"target_a_{uid_suffix}@test.com",
            hashed_password="pw",
            full_name="Target A",
            mobile="9900223344",
            role=UserRole.ACCOUNTANT.value,
            is_active=True,
            company_id=comp_a_id,
            is_super_admin=False,
        )
        user_b = User(
            email=f"target_b_{uid_suffix}@test.com",
            hashed_password="pw",
            full_name="Target B",
            mobile="9900223345",
            role=UserRole.ACCOUNTANT.value,
            is_active=True,
            company_id=comp_b_id,
            is_super_admin=False,
        )
        db.add_all([user_a, user_b])
        await db.commit()
        await db.refresh(user_a)
        await db.refresh(user_b)

        # Add audit logs
        log_a = UserAuditLog(
            user_id=user_a.id,
            field_name="role",
            old_value="SiteEngineer",
            new_value="Accountant",
            changed_by=user_a.id,
            change_group_id="grp-a-1",
        )
        log_b = UserAuditLog(
            user_id=user_b.id,
            field_name="role",
            old_value="SiteEngineer",
            new_value="Accountant",
            changed_by=user_b.id,
            change_group_id="grp-b-1",
        )
        db.add_all([log_a, log_b])
        await db.commit()

        # Company A Admin
        admin_a = User(
            id=7704,
            email=f"admin_a_audit_{uid_suffix}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            company_id=comp_a_id,
            is_super_admin=False,
        )

        app.dependency_overrides[get_current_user] = lambda: admin_a
        app.dependency_overrides[get_current_active_user] = lambda: admin_a

        async with get_client() as client:
            # 1. Company A Admin -> own-company user audit logs allowed (200)
            res_own = await client.get(f"/api/v1/users/{user_a.id}/audit-logs")
            assert res_own.status_code == 200
            assert len(res_own.json()) >= 1

            res_own_grouped = await client.get(f"/api/v1/users/{user_a.id}/audit-logs-grouped")
            assert res_own_grouped.status_code == 200
            assert len(res_own_grouped.json()) >= 1

            # 2. Company A Admin -> Company B user audit logs return 404 (IDOR prevented & masked)
            res_foreign = await client.get(f"/api/v1/users/{user_b.id}/audit-logs")
            assert res_foreign.status_code == 404, "Foreign company audit logs must return 404"

            res_foreign_grouped = await client.get(f"/api/v1/users/{user_b.id}/audit-logs-grouped")
            assert res_foreign_grouped.status_code == 404, "Foreign company grouped audit logs must return 404"

            # 3. Super Admin -> cross-company behavior preserved
            super_admin = User(
                id=7705,
                email=f"super_audit_{uid_suffix}@test.com",
                role="Super Admin",
                is_active=True,
                company_id=None,
                is_super_admin=True,
            )
            app.dependency_overrides[get_current_user] = lambda: super_admin
            app.dependency_overrides[get_current_active_user] = lambda: super_admin

            res_sa = await client.get(f"/api/v1/users/{user_b.id}/audit-logs")
            assert res_sa.status_code == 200, "Super Admin must be able to view cross-company user audit logs"

        # Cleanup
        await db.execute(delete(UserAuditLog).where(UserAuditLog.user_id.in_([user_a.id, user_b.id])))
        await db.execute(delete(User).where(User.id.in_([user_a.id, user_b.id])))
        await db.commit()
        app.dependency_overrides.clear()


# ==============================================================================
# Scenario D & E: P0-1 Company Deactivation Enforcement & Orphan Tenant Access
# ==============================================================================
@pytest.mark.asyncio
async def test_p0_1_company_deactivation_and_null_company():
    async for db in get_db_session():
        # Create a test company for deactivation testing
        test_comp = Company(
            name="P0-1 Test Deactivation Corp",
            subdomain="p0-1-deact-test",
            is_active=True,
        )
        db.add(test_comp)
        await db.commit()
        await db.refresh(test_comp)

        comp_user = User(
            email="p01_user@test.com",
            hashed_password="pw",
            full_name="P01 User",
            mobile="9900334455",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            company_id=test_comp.id,
            is_super_admin=False,
        )
        db.add(comp_user)
        await db.commit()
        await db.refresh(comp_user)

        # Clear overrides so real get_current_active_user dependency logic executes!
        app.dependency_overrides.clear()
        # Override only get_current_user to simulate authenticated user token resolution
        app.dependency_overrides[get_current_user] = lambda: comp_user

        async with get_client() as client:
            # 1. Company is active -> tenant API works (200)
            res_active = await client.get("/api/v1/users/me")
            assert res_active.status_code == 200, "Active company user should access /users/me"

            # 2. Deactivate company in DB -> normal user API access returns 403
            test_comp.is_active = False
            await db.commit()

            res_inactive = await client.get("/api/v1/users/me")
            assert res_inactive.status_code == 403, "Inactive company user must return 403"
            assert "Company is inactive" in res_inactive.text

            # 3. Super Admin accessing platform -> unaffected by tenant inactive status
            sa_user = User(
                id=7706,
                email="p01_sa@test.com",
                role="Super Admin",
                is_active=True,
                company_id=test_comp.id,  # even if assigned to inactive company
                is_super_admin=True,
            )
            app.dependency_overrides[get_current_user] = lambda: sa_user

            res_sa = await client.get("/api/v1/superadmin/dashboard-stats")
            assert res_sa.status_code == 200, "Super Admin must not be blocked by company inactive status"

            # 4. Reactivate company -> tenant access works again
            test_comp.is_active = True
            await db.commit()

            app.dependency_overrides[get_current_user] = lambda: comp_user
            res_reactivated = await client.get("/api/v1/users/me")
            assert res_reactivated.status_code == 200, "Reactivated company user should access /users/me"

            # Scenario E: Non-Super user with company_id=None must NOT gain tenant access (403)
            orphan_user = User(
                id=7707,
                email="orphan_user@test.com",
                role=UserRole.SITE_ENGINEER.value,
                is_active=True,
                company_id=None,
                is_super_admin=False,
            )
            app.dependency_overrides[get_current_user] = lambda: orphan_user

            res_orphan = await client.get("/api/v1/users/me")
            assert res_orphan.status_code == 403, "Non-SA user with company_id=None must return 403"
            assert "User does not belong to any company" in res_orphan.text

        # Cleanup
        await db.execute(delete(User).where(User.id == comp_user.id))
        await db.execute(delete(Company).where(Company.id == test_comp.id))
        await db.commit()
        app.dependency_overrides.clear()
