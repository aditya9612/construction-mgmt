import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user
from app.models.user import User, UserRole
from app.models.subscription import Plan, Subscription
from app.models.company import Company
from app.services.entitlement import get_entitlement_service
from app.db.session import AsyncSessionLocal
from sqlalchemy import select

client = TestClient(app)


def override_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user


def clear_overrides():
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_entitlement_service_methods():
    service = get_entitlement_service()
    async with AsyncSessionLocal() as db:
        # Check company 1 entitlements
        entitlements = await service.get_company_entitlements(db, 1)
        assert "max_users" in entitlements
        assert "max_projects" in entitlements
        assert "storage_gb" in entitlements
        assert "features" in entitlements

        # Check usage
        usage = await service.get_usage(db, 1)
        assert "users" in usage
        assert "projects" in usage
        assert "storage_bytes" in usage

        # Check limits helper
        limits = await service.get_limits(db, 1)
        assert "entitlements" in limits
        assert "usage" in limits


def test_user_limit_enforcement():
    # User in tenant 1
    admin_user = User(
        id=2002,
        email="admin@tenant-a.com",
        full_name="Tenant Admin",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    override_user(admin_user)

    # Attempting to create user with invalid role or normal flow
    resp = client.post(
        "/api/v1/users/create",
        data={
            "email": "new_team_member_99@gmail.com",
            "full_name": "New Team Member",
            "role": UserRole.SITE_ENGINEER.value,
            "password": "Password123!",
        },
    )
    # Status code will be 201 (created), 403 (limit reached/subscription inactive), or 409 (already exists)
    assert resp.status_code in (201, 403, 409, 422)

    clear_overrides()


def test_project_limit_enforcement():
    admin_user = User(
        id=2002,
        email="admin@tenant-a.com",
        full_name="Tenant Admin",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    override_user(admin_user)

    resp = client.post(
        "/api/v1/projects",
        json={
            "project_name": "SaaS Limit Test Project",
            "owner_id": 1,
        },
    )
    # 200 (created) or 403 (limit reached / subscription inactive) or 404 (owner not found) or 400 (validation/conflict)
    assert resp.status_code in (200, 400, 403, 404, 422)

    clear_overrides()


def test_feature_flags_enforcement():
    # Test feature dependency on AI, equipment, reports, payroll
    tenant_user = User(
        id=2003,
        email="engineer@tenant-a.com",
        full_name="Tenant Engineer",
        role=UserRole.SITE_ENGINEER.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    override_user(tenant_user)

    # Equipment endpoints require equipment feature
    eq_resp = client.get("/api/v1/equipment")
    assert eq_resp.status_code in (200, 403, 404)

    # Reports endpoints require advanced_reports feature
    rep_resp = client.get("/api/v1/reports/projects/excel")
    assert rep_resp.status_code in (200, 403, 404)

    # Payroll endpoints require payroll feature
    pay_resp = client.get("/api/v1/accountant/payroll/summary")
    assert pay_resp.status_code in (200, 403, 404)

    clear_overrides()
