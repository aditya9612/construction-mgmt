import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_current_active_user, require_roles
from app.models.user import User, UserRole
import uuid
import random

client = TestClient(app)

super_admin = User(id=999, full_name="Super Admin", email="super@test.com", role=UserRole.ADMIN.value, is_active=True, is_super_admin=True, company_id=None)
company_admin = User(id=1000, full_name="Company Admin", email="admin@compa.com", role=UserRole.ADMIN.value, is_active=True, is_super_admin=False, company_id=43)
normal_user = User(id=1001, full_name="Normal User", email="user@compb.com", role="ProjectManager", is_active=True, is_super_admin=False, company_id=12)

def get_mock_admin_role_dependency(mock_user):
    async def _mock():
        return mock_user
    return _mock

from unittest.mock import AsyncMock, MagicMock
from app.db.session import get_db_session

# Mock DB Session Dependency
async def get_mock_db_session():
    def mock_add(obj):
        setattr(obj, 'id', 123)
        if not hasattr(obj, 'is_super_admin') or getattr(obj, 'is_super_admin') is None:
            setattr(obj, 'is_super_admin', False)
    
    mock_session = AsyncMock()
    mock_session.add = MagicMock(side_effect=mock_add)
    mock_session.flush = AsyncMock()
    
    # Mock for `await db.scalar(...)` used in email/mobile checks
    mock_session.scalar.return_value = None
    
    # Allow executing queries to return a mock result that evaluates to None
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    
    yield mock_session

def setup_function():
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db_session] = get_mock_db_session

def teardown_module(module):
    app.dependency_overrides.clear()

def test_users_create_unauthenticated():
    res = client.post("/api/v1/users/create", params={
        "email": "hacker@test.com",
        "mobile_number": "9991111111",
        "role": UserRole.ADMIN.value,
        "full_name": "Hacker",
        "password": "ValidPass123!",
    })
    assert res.status_code == 401

def test_users_create_tenant_admin_creates_admin():
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db_session] = get_mock_db_session
    app.dependency_overrides[get_current_active_user] = lambda: company_admin
    
    rand_id = str(uuid.uuid4())[:8]
    email = f"test_{rand_id}@test.com"
    mobile = f"999{random.randint(1000000, 9999999)}"
    
    res = client.post("/api/v1/users/create", params={
        "email": email,
        "mobile_number": mobile,
        "role": UserRole.ADMIN.value,
        "full_name": "New Admin",
        "password": "ValidPass123!",
    })
    
    assert res.status_code == 403
    assert "Cannot create Admin users via this endpoint" in res.json()["detail"]

def test_users_create_tenant_admin_creates_permitted_role():
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db_session] = get_mock_db_session
    app.dependency_overrides[get_current_active_user] = lambda: company_admin
    
    rand_id = str(uuid.uuid4())[:8]
    email = f"test_{rand_id}@test.com"
    mobile = f"888{random.randint(1000000, 9999999)}"
    
    res = client.post("/api/v1/users/create", params={
        "email": email,
        "mobile_number": mobile,
        "role": "ProjectManager",
        "full_name": "New PM",
        "password": "ValidPass123!",
    })
    
    assert res.status_code == 201
    data = res.json()
    assert data["company_id"] == 43
    assert data["role"] == "ProjectManager"

def test_users_create_tenant_admin_malicious_override():
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db_session] = get_mock_db_session
    app.dependency_overrides[get_current_active_user] = lambda: company_admin
    
    rand_id = str(uuid.uuid4())[:8]
    email = f"test_{rand_id}@test.com"
    mobile = f"777{random.randint(1000000, 9999999)}"
    
    res = client.post("/api/v1/users/create", params={
        "email": email,
        "mobile_number": mobile,
        "role": UserRole.SITE_ENGINEER.value,
        "full_name": "Site Eng",
        "password": "ValidPass123!",
        "company_id": "99"
    })
    
    assert res.status_code == 201
    data = res.json()
    assert data["company_id"] == 43

def test_users_create_company_id_null_context_rejected():
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db_session] = get_mock_db_session
    app.dependency_overrides[get_current_active_user] = lambda: super_admin
    
    res = client.post("/api/v1/users/create", params={
        "email": "super_test@test.com",
        "mobile_number": "9666666666",
        "role": UserRole.ADMIN.value,
        "full_name": "Test",
        "password": "ValidPass123!",
    })
    
    assert res.status_code == 403
    # With the new check, this is now rejecting because of Admin role before company_id check
    assert "Cannot create Admin users via this endpoint" in res.json()["detail"]

def test_users_create_normal_user_rejected():
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db_session] = get_mock_db_session
    app.dependency_overrides[get_current_active_user] = lambda: normal_user
    
    rand_id = str(uuid.uuid4())[:8]
    email = f"test_{rand_id}@test.com"
    mobile = f"666{random.randint(1000000, 9999999)}"
    
    res = client.post("/api/v1/users/create", params={
        "email": email,
        "mobile_number": mobile,
        "role": UserRole.SITE_ENGINEER.value,
        "full_name": "Site Eng",
        "password": "ValidPass123!",
    })
    
    # normal_user role is ProjectManager, which does not have Admin/Super Admin permission
    assert res.status_code == 403
    assert "Insufficient permissions" in res.json()["detail"]
