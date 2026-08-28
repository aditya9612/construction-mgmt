import pytest
import uuid
import time
import asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_super_admin, get_db_session
from app.core.security import get_password_hash, verify_password, create_access_token
from app.models.user import User, UserRole, ActivityLog
from app.db.session import AsyncSessionLocal

client = TestClient(app)


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def delete(self, key):
        self.store.pop(key, None)


fake_redis_instance = FakeRedis()


@pytest.fixture(autouse=True)
def setup_fake_redis():
    fake_redis_instance.store.clear()
    app.state.redis = fake_redis_instance
    yield fake_redis_instance
    app.dependency_overrides.clear()


def create_test_superadmin():
    async def _create():
        async with AsyncSessionLocal() as db:
            unique_email = f"sa_{uuid.uuid4().hex[:8]}@platform.com"
            user = User(
                email=unique_email,
                full_name="Master Super Admin",
                mobile=f"9{uuid.uuid4().int % 1000000000:09d}",
                hashed_password=get_password_hash("SuperSecret123!"),
                role=UserRole.ADMIN.value,
                is_active=True,
                is_super_admin=True,
                company_id=None,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            return user
    return asyncio.run(_create())


def delete_test_user(user_id: int):
    async def _delete():
        async with AsyncSessionLocal() as db:
            from sqlalchemy import delete
            await db.execute(
                delete(ActivityLog).where(
                    (ActivityLog.performed_by == user_id)
                    | ((ActivityLog.entity == "User") & (ActivityLog.entity_id == user_id))
                )
            )
            user = await db.get(User, user_id)
            if user:
                await db.delete(user)
            await db.commit()
    asyncio.run(_delete())


def override_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    if user.is_super_admin:
        app.dependency_overrides[require_super_admin] = lambda: user
    else:
        app.dependency_overrides.pop(require_super_admin, None)


# =============================================================================
# 1. AUTHENTICATION & RBAC BOUNDARY TESTS
# =============================================================================

def test_unauthenticated_profile_and_password_denied():
    app.dependency_overrides.clear()
    assert client.get("/api/v1/superadmin/profile").status_code == 401
    assert client.put("/api/v1/superadmin/profile", json={"full_name": "Test"}).status_code == 401
    assert client.post("/api/v1/superadmin/change-password", json={
        "current_password": "a", "new_password": "b", "confirm_password": "b"
    }).status_code == 401


def test_tenant_admin_denied_profile_endpoints():
    tenant_admin = User(
        id=9001,
        email="tenant_admin@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    override_user(tenant_admin)
    assert client.get("/api/v1/superadmin/profile").status_code == 403
    assert client.put("/api/v1/superadmin/profile", json={"full_name": "Test"}).status_code == 403
    assert client.post("/api/v1/superadmin/change-password", json={
        "current_password": "a", "new_password": "b", "confirm_password": "b"
    }).status_code == 403
    app.dependency_overrides.clear()


def test_regular_user_denied_profile_endpoints():
    regular_user = User(
        id=9002,
        email="engineer@test.com",
        role=UserRole.SITE_ENGINEER.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    override_user(regular_user)
    assert client.get("/api/v1/superadmin/profile").status_code == 403
    assert client.put("/api/v1/superadmin/profile", json={"full_name": "Test"}).status_code == 403
    assert client.post("/api/v1/superadmin/change-password", json={
        "current_password": "a", "new_password": "b", "confirm_password": "b"
    }).status_code == 403
    app.dependency_overrides.clear()


# =============================================================================
# 2. PROFILE GET & UPDATE TESTS
# =============================================================================

def test_superadmin_get_profile():
    sa = create_test_superadmin()
    try:
        override_user(sa)
        resp = client.get("/api/v1/superadmin/profile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sa.id
        assert data["email"] == sa.email
        assert data["full_name"] == sa.full_name
        assert data["is_super_admin"] is True
        assert data["company_id"] is None
        assert "password" not in data
        assert "hashed_password" not in data
    finally:
        delete_test_user(sa.id)
        app.dependency_overrides.clear()


def test_superadmin_update_profile():
    sa = create_test_superadmin()
    try:
        override_user(sa)
        new_name = "Updated Super Admin"
        new_mobile = f"9{uuid.uuid4().int % 1000000000:09d}"
        new_email = f"updated_sa_{uuid.uuid4().hex[:6]}@platform.com"

        resp = client.put("/api/v1/superadmin/profile", json={
            "full_name": new_name,
            "mobile": new_mobile,
            "email": new_email,
            # Malicious payload attempts
            "company_id": 999,
            "is_super_admin": False,
            "role": "Labour"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["full_name"] == new_name
        assert data["mobile"] == new_mobile
        assert data["email"] == new_email
        assert data["is_super_admin"] is True
        assert data["company_id"] is None

        # Verify directly in DB
        async def _check_db():
            async with AsyncSessionLocal() as db:
                refreshed = await db.get(User, sa.id)
                assert refreshed.full_name == new_name
                assert refreshed.mobile == new_mobile
                assert refreshed.email == new_email
                assert refreshed.is_super_admin is True
                assert refreshed.company_id is None

                log = await db.scalar(
                    select(ActivityLog).where(
                        ActivityLog.entity == "User",
                        ActivityLog.entity_id == sa.id,
                        ActivityLog.action == "SUPER_ADMIN_PROFILE_UPDATED"
                    )
                )
                assert log is not None
                assert "password" not in str(log.details)
                assert "hashed_password" not in str(log.details)
        asyncio.run(_check_db())
    finally:
        delete_test_user(sa.id)
        app.dependency_overrides.clear()


def test_superadmin_update_profile_duplicate_email():
    sa1 = create_test_superadmin()
    sa2 = create_test_superadmin()
    try:
        override_user(sa1)
        resp = client.put("/api/v1/superadmin/profile", json={
            "email": sa2.email,
        })
        assert resp.status_code == 409
    finally:
        delete_test_user(sa1.id)
        delete_test_user(sa2.id)
        app.dependency_overrides.clear()


# =============================================================================
# 3. PASSWORD CHANGE TESTS
# =============================================================================

def test_superadmin_change_password_success():
    sa = create_test_superadmin()
    try:
        token = create_access_token({"sub": str(sa.id)})
        headers = {"Authorization": f"Bearer {token}"}
        override_user(sa)

        old_password = "SuperSecret123!"
        new_password = "NewStrongPassword456!"

        resp = client.post(
            "/api/v1/superadmin/change-password",
            json={
                "current_password": old_password,
                "new_password": new_password,
                "confirm_password": new_password,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["message"] == "Password changed successfully"
        assert "password" not in data
        assert "hashed_password" not in data

        # Verify in DB: new password matches, old password fails
        async def _check_db():
            async with AsyncSessionLocal() as db:
                refreshed = await db.get(User, sa.id)
                assert verify_password(new_password, refreshed.hashed_password) is True
                assert verify_password(old_password, refreshed.hashed_password) is False
                assert refreshed.is_super_admin is True
                assert refreshed.company_id is None

                log = await db.scalar(
                    select(ActivityLog).where(
                        ActivityLog.entity == "User",
                        ActivityLog.entity_id == sa.id,
                        ActivityLog.action == "SUPER_ADMIN_PASSWORD_CHANGED"
                    )
                )
                assert log is not None
                assert "password" not in str(log.details)
                assert "SuperSecret" not in str(log.details)
                assert "NewStrong" not in str(log.details)
        asyncio.run(_check_db())

        # Verify session invalidation in Redis
        logout_all_val = asyncio.run(fake_redis_instance.get(f"logout_all:user:{sa.id}"))
        assert logout_all_val is not None
    finally:
        delete_test_user(sa.id)
        app.dependency_overrides.clear()


def test_superadmin_change_password_wrong_current():
    sa = create_test_superadmin()
    try:
        token = create_access_token({"sub": str(sa.id)})
        headers = {"Authorization": f"Bearer {token}"}
        override_user(sa)
        resp = client.post(
            "/api/v1/superadmin/change-password",
            json={
                "current_password": "WrongPassword!",
                "new_password": "NewStrongPassword456!",
                "confirm_password": "NewStrongPassword456!",
            },
            headers=headers,
        )
        assert resp.status_code == 400
        assert "Current password is incorrect" in resp.json()["detail"]
    finally:
        delete_test_user(sa.id)
        app.dependency_overrides.clear()


def test_superadmin_change_password_mismatch_confirmation():
    sa = create_test_superadmin()
    try:
        token = create_access_token({"sub": str(sa.id)})
        headers = {"Authorization": f"Bearer {token}"}
        override_user(sa)
        resp = client.post(
            "/api/v1/superadmin/change-password",
            json={
                "current_password": "SuperSecret123!",
                "new_password": "NewStrongPassword456!",
                "confirm_password": "DifferentPassword789!",
            },
            headers=headers,
        )
        assert resp.status_code == 400
        assert "match" in resp.json()["detail"]
    finally:
        delete_test_user(sa.id)
        app.dependency_overrides.clear()

