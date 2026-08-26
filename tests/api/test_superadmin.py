import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_super_admin
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.subscription import Plan, Subscription

client = TestClient(app)

# Mock users for authorization boundary testing
super_admin_user = User(
    id=1025,
    email="superadmin21@gmail.com",
    full_name="Platform Super Admin",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=True,
    company_id=None,
)

tenant_admin_user = User(
    id=2002,
    email="admin@tenant-a.com",
    full_name="Tenant A Admin",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)

normal_tenant_user = User(
    id=2003,
    email="engineer@tenant-a.com",
    full_name="Tenant A Engineer",
    role=UserRole.SITE_ENGINEER.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)


def override_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    if user.is_super_admin:
        app.dependency_overrides[require_super_admin] = lambda: user
    else:
        # If user is not super admin, require_super_admin will be executed normally or overridden to fail
        app.dependency_overrides.pop(require_super_admin, None)


def clear_overrides():
    app.dependency_overrides.clear()


# =============================================================================
# 1. AUTHENTICATION & ACCESS BOUNDARY TESTS
# =============================================================================

def test_unauthenticated_requests_denied():
    clear_overrides()
    assert client.get("/api/v1/superadmin/dashboard-stats").status_code == 401
    assert client.get("/api/v1/superadmin/companies").status_code == 401
    assert client.post("/api/v1/superadmin/companies", json={"name": "Test", "subdomain": "test-t1"}).status_code == 401
    assert client.get("/api/v1/superadmin/plans").status_code == 401
    assert client.get("/api/v1/superadmin/audit-logs").status_code == 401


def test_normal_user_denied_superadmin_api():
    override_user(normal_tenant_user)
    assert client.get("/api/v1/superadmin/dashboard-stats").status_code == 403
    assert client.get("/api/v1/superadmin/companies").status_code == 403
    assert client.post("/api/v1/superadmin/companies", json={"name": "Test", "subdomain": "test-t1"}).status_code == 403
    assert client.get("/api/v1/superadmin/plans").status_code == 403
    assert client.get("/api/v1/superadmin/audit-logs").status_code == 403
    clear_overrides()


def test_tenant_admin_denied_superadmin_api():
    override_user(tenant_admin_user)
    assert client.get("/api/v1/superadmin/dashboard-stats").status_code == 403
    assert client.get("/api/v1/superadmin/companies").status_code == 403
    assert client.post("/api/v1/superadmin/companies", json={"name": "Test", "subdomain": "test-t1"}).status_code == 403
    assert client.get("/api/v1/superadmin/plans").status_code == 403
    assert client.get("/api/v1/superadmin/audit-logs").status_code == 403
    clear_overrides()


def test_superadmin_dashboard_stats():
    override_user(super_admin_user)
    resp = client.get("/api/v1/superadmin/dashboard-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "companies" in data
    assert "active_companies" in data
    assert "users" in data
    assert "projects" in data
    assert "plans_count" in data
    assert "subscriptions_count" in data
    assert "subscription_distribution" in data
    clear_overrides()


# =============================================================================
# 2. TENANT MANAGEMENT & LIFECYCLE TESTS
# =============================================================================

def test_superadmin_company_management_lifecycle():
    override_user(super_admin_user)

    # 1. Create company
    subdomain = "test-saas-comp-01"
    create_payload = {
        "name": "Acme Builders Inc",
        "subdomain": subdomain,
    }
    resp = client.post("/api/v1/superadmin/companies", json=create_payload)
    assert resp.status_code in (200, 409)
    if resp.status_code == 200:
        company_data = resp.json()
        company_id = company_data["id"]
        assert company_data["name"] == "Acme Builders Inc"
        assert company_data["subdomain"] == subdomain
        assert company_data["is_active"] is True

        # 2. Get company
        get_resp = client.get(f"/api/v1/superadmin/companies/{company_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == company_id

        # 3. Update company
        update_resp = client.put(
            f"/api/v1/superadmin/companies/{company_id}",
            json={"name": "Acme Builders Global"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == "Acme Builders Global"

        # 4. Suspend company
        suspend_resp = client.post(f"/api/v1/superadmin/companies/{company_id}/suspend")
        assert suspend_resp.status_code == 200
        assert suspend_resp.json()["is_active"] is False

        # 5. Activate company
        activate_resp = client.post(f"/api/v1/superadmin/companies/{company_id}/activate")
        assert activate_resp.status_code == 200
        assert activate_resp.json()["is_active"] is True

        # 6. Get stats
        stats_resp = client.get(f"/api/v1/superadmin/companies/{company_id}/stats")
        assert stats_resp.status_code == 200
        stats_data = stats_resp.json()
        assert "total_projects" in stats_data
        assert "total_users" in stats_data

    # 7. List companies with filters
    list_resp = client.get("/api/v1/superadmin/companies?is_active=true&limit=10")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert "items" in list_data
    assert "meta" in list_data

    clear_overrides()


# =============================================================================
# 3. SAAS PLAN MANAGEMENT TESTS
# =============================================================================

def test_superadmin_plan_management():
    override_user(super_admin_user)

    # 1. Create plan
    plan_code = "enterprise-v1"
    plan_payload = {
        "name": "Enterprise Plus",
        "code": plan_code,
        "description": "Full platform access for large construction firms",
        "price": 4999.0,
        "billing_interval": "monthly",
        "currency": "INR",
        "features": {
            "max_users": 100,
            "max_projects": 50,
            "storage_gb": 100,
            "advanced_reports": True,
            "payroll": True,
            "equipment": True,
            "ai_features": True,
        },
        "is_active": True,
    }
    resp = client.post("/api/v1/superadmin/plans", json=plan_payload)
    assert resp.status_code in (200, 409)

    # 2. List plans
    list_resp = client.get("/api/v1/superadmin/plans")
    assert list_resp.status_code == 200
    plans = list_resp.json()
    assert isinstance(plans, list)

    if resp.status_code == 200:
        plan_id = resp.json()["id"]

        # 3. Get plan
        get_resp = client.get(f"/api/v1/superadmin/plans/{plan_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["code"] == plan_code

        # 4. Update plan
        up_resp = client.put(f"/api/v1/superadmin/plans/{plan_id}", json={"price": 5499.0})
        assert up_resp.status_code == 200
        assert up_resp.json()["price"] == 5499.0

    clear_overrides()


# =============================================================================
# 4. SUBSCRIPTION & ENTITLEMENTS TESTS
# =============================================================================

def test_superadmin_subscription_and_entitlements():
    override_user(super_admin_user)

    # Get company 1 subscription / entitlements
    sub_resp = client.get("/api/v1/superadmin/companies/1/subscription")
    assert sub_resp.status_code in (200, 404)

    ent_resp = client.get("/api/v1/superadmin/companies/1/entitlements")
    assert ent_resp.status_code in (200, 404)
    if ent_resp.status_code == 200:
        ent_data = ent_resp.json()
        assert "max_users" in ent_data
        assert "max_projects" in ent_data
        assert "is_active" in ent_data

    clear_overrides()


# =============================================================================
# 5. PLATFORM AUDIT LOGS TESTS
# =============================================================================

def test_superadmin_platform_audit_logs():
    override_user(super_admin_user)

    resp = client.get("/api/v1/superadmin/audit-logs?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "meta" in data

    comp_audit_resp = client.get("/api/v1/superadmin/companies/1/audit-logs")
    assert comp_audit_resp.status_code in (200, 404)

    clear_overrides()
