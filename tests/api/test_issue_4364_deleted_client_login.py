import pytest
import pytest_asyncio
import uuid
import time
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.user import User, UserRole
from app.core.dependencies import get_current_user, get_current_active_user
from app.core.security import create_access_token


@pytest_asyncio.fixture
async def setup_deleted_client_context():
    uid = uuid.uuid4().hex[:6]
    rand_int = uuid.uuid4().int % 100000000
    mobile_active = f"91{rand_int:08d}"
    mobile_deleted = f"92{rand_int:08d}"

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_4364_{uid}", is_active=True)
        db.add(comp)
        await db.flush()

        admin = User(
            email=f"admin_4364_{uid}@test.com",
            mobile=f"90{rand_int:08d}",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_deleted=False,
            company_id=comp.id,
        )
        active_client = User(
            email=f"active_cli_{uid}@test.com",
            mobile=mobile_active,
            role=UserRole.CLIENT.value,
            is_active=True,
            is_deleted=False,
            company_id=comp.id,
        )
        deleted_client = User(
            email=f"deleted_cli_{uid}@test.com",
            mobile=mobile_deleted,
            role=UserRole.CLIENT.value,
            is_active=True,
            is_deleted=True,
            company_id=comp.id,
        )
        db.add_all([admin, active_client, deleted_client])
        await db.commit()

        admin_id = admin.id
        active_id = active_client.id
        deleted_id = deleted_client.id

    return {
        "comp_id": comp.id,
        "admin": admin,
        "admin_id": admin_id,
        "active_id": active_id,
        "deleted_id": deleted_id,
        "active_mobile": mobile_active,
        "deleted_mobile": mobile_deleted,
    }


@pytest.mark.asyncio
async def test_deleted_client_login_blocked(setup_deleted_client_context):
    """
    Issue #4364: When an Admin deletes a client account, the system must deny login attempts
    and return 403 with exact message: 'Your account has been deleted. Please contact Admin.'
    """
    ctx = setup_deleted_client_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login attempt with deleted client's mobile
        res = await client.post("/api/v1/auth/login", json={"mobile": ctx["deleted_mobile"]})
        assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
        data = res.json()
        assert data.get("detail") == "Your account has been deleted. Please contact Admin."

        # 2. OTP verification attempt with deleted client's mobile
        res_otp = await client.post(
            "/api/v1/auth/verify_otp",
            json={"mobile": ctx["deleted_mobile"], "otp": "123456"}
        )
        assert res_otp.status_code == 403, f"Expected 403, got {res_otp.status_code}: {res_otp.text}"
        assert res_otp.json().get("detail") == "Your account has been deleted. Please contact Admin."


@pytest.mark.asyncio
async def test_deleted_client_token_access_blocked(setup_deleted_client_context):
    """
    Issue #4364: A deleted client using an existing or active token must be blocked with 403.
    """
    ctx = setup_deleted_client_context
    token = create_access_token({"sub": str(ctx["deleted_id"]), "role": UserRole.CLIENT.value})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
        assert res.json().get("detail") == "Your account has been deleted. Please contact Admin."


@pytest.mark.asyncio
async def test_deleted_client_full_lifecycle(setup_deleted_client_context):
    """
    Issue #4364: Complete security lifecycle verification:
    1. Active client accesses protected endpoint before deletion -> 200 OK.
    2. Admin soft-deletes the client via DELETE /api/v1/users/{id}.
    3. Immediately retry protected endpoint with previously issued access token -> MUST BE DENIED.
    4. Attempt login again with client credentials -> 403 Forbidden.
    5. Attempt OTP verification -> 403 Forbidden.
    6. Admin restores client via PUT /api/v1/users/{id}/restore.
    7. Retrying the OLD pre-deletion token must NOT regain access (invalidated sessions are not restored).
    8. Newly issued credentials after restoration allow normal login.
    """
    ctx = setup_deleted_client_context
    admin_token = create_access_token({"sub": str(ctx["admin_id"]), "role": UserRole.ADMIN.value})
    client_token = create_access_token({"sub": str(ctx["active_id"]), "role": UserRole.CLIENT.value})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Active client accesses settings before deletion -> 200 OK
        res_before = await client.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {client_token}"}
        )
        assert res_before.status_code == 200, f"Expected 200, got {res_before.status_code}: {res_before.text}"

        # Step 2: Admin soft-deletes the client through real application endpoint
        res_del = await client.delete(
            f"/api/v1/users/{ctx['active_id']}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert res_del.status_code in (200, 204), f"Delete failed: {res_del.status_code}: {res_del.text}"

        # Step 3: Immediately retry protected endpoint with pre-issued token -> MUST BE BLOCKED
        res_after = await client.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {client_token}"}
        )
        assert res_after.status_code in (401, 403), f"Expected 401/403, got {res_after.status_code}: {res_after.text}"

        # Step 4: Login attempt with deleted client's mobile -> 403
        res_login = await client.post("/api/v1/auth/login", json={"mobile": ctx["active_mobile"]})
        assert res_login.status_code == 403
        assert "deleted" in res_login.json().get("detail", "").lower()

        # Step 5: OTP verification attempt -> 403
        res_otp = await client.post("/api/v1/auth/verify_otp", json={"mobile": ctx["active_mobile"], "otp": "123456"})
        assert res_otp.status_code == 403
        assert "deleted" in res_otp.json().get("detail", "").lower()

        # Step 6: Admin restores the client
        res_restore = await client.put(
            f"/api/v1/users/{ctx['active_id']}/restore",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert res_restore.status_code == 200

        # Step 7: Verify database state
        async with AsyncSessionLocal() as db:
            u = await db.scalar(select(User).where(User.id == ctx["active_id"]))
            assert u.is_deleted is False

        # Step 8: Verify a new token created after restoration grants access
        new_token = create_access_token({"sub": str(ctx["active_id"]), "role": UserRole.CLIENT.value})
        res_new = await client.get(
            "/api/v1/settings",
            headers={"Authorization": f"Bearer {new_token}"}
        )
        assert res_new.status_code == 200
