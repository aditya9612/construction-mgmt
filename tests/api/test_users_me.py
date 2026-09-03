import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_current_active_user
from app.models.user import User

client = TestClient(app)

super_admin = User(id=999, full_name="Super Admin", email="super@test.com", role="Admin", is_active=True, is_super_admin=True, company_id=None)
company_admin = User(id=1000, full_name="Company Admin", email="admin@compa.com", role="Admin", is_active=True, is_super_admin=False, company_id=43)
normal_user = User(id=1001, full_name="Normal User", email="user@compb.com", role="ProjectManager", is_active=True, is_super_admin=False, company_id=12)

def test_users_me_company_admin():
    app.dependency_overrides[get_current_active_user] = lambda: company_admin
    res = client.get("/api/v1/users/me")
    assert res.status_code == 200
    data = res.json()
    assert data["company_id"] == 43
    assert data["is_super_admin"] == False
    assert data["full_name"] == "Company Admin"
    assert data["role"] == "Admin"
    assert "user_id" in data

def test_users_me_normal_user():
    app.dependency_overrides[get_current_active_user] = lambda: normal_user
    res = client.get("/api/v1/users/me")
    assert res.status_code == 200
    data = res.json()
    assert data["company_id"] == 12
    assert data["is_super_admin"] == False
    assert data["role"] == "ProjectManager"

def test_users_me_super_admin():
    app.dependency_overrides[get_current_active_user] = lambda: super_admin
    res = client.get("/api/v1/users/me")
    assert res.status_code == 200
    data = res.json()
    assert data["company_id"] is None
    assert data["is_super_admin"] == True

def test_users_me_override_protection():
    # Frontend cannot override company_id by sending query params or json payload on GET
    app.dependency_overrides[get_current_active_user] = lambda: company_admin
    res = client.get("/api/v1/users/me?company_id=999")
    assert res.status_code == 200
    data = res.json()
    assert data["company_id"] == 43  # remains unchanged

def teardown_module(module):
    app.dependency_overrides.clear()
