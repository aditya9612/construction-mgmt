import pytest
import pytest_asyncio
import uuid
import io
import os
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.user import User, UserRole, UserAuditLog
from app.core.dependencies import get_current_user, get_current_active_user


@pytest_asyncio.fixture
async def setup_deactivation_context():
    uid = uuid.uuid4().hex[:6]
    rand_int = uuid.uuid4().int % 100000000

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_4362_{uid}", is_active=True)
        db.add(comp)
        await db.flush()

        admin = User(
            email=f"admin_4362_{uid}@test.com",
            mobile=f"91{rand_int:08d}",
            role=UserRole.ADMIN.value,
            is_active=True,
            company_id=comp.id,
        )
        c1 = User(
            email=f"client1_4362_{uid}@test.com",
            mobile=f"92{rand_int:08d}",
            role=UserRole.CLIENT.value,
            is_active=True,
            full_name=f"Client One {uid}",
            company_id=comp.id,
        )
        c2 = User(
            email=f"client2_4362_{uid}@test.com",
            mobile=f"93{rand_int:08d}",
            role=UserRole.CLIENT.value,
            is_active=True,
            full_name=f"Client Two {uid}",
            company_id=comp.id,
        )
        c3 = User(
            email=f"client3_4362_{uid}@test.com",
            mobile=f"94{rand_int:08d}",
            role=UserRole.CLIENT.value,
            is_active=True,
            full_name=f"Client Three {uid}",
            company_id=comp.id,
        )
        c4 = User(
            email=f"client4_4362_{uid}@test.com",
            mobile=f"95{rand_int:08d}",
            role=UserRole.CLIENT.value,
            is_active=True,
            full_name=f"Client Four {uid}",
            company_id=comp.id,
        )
        db.add_all([admin, c1, c2, c3, c4])
        await db.commit()

        admin_id = admin.id
        c1_id = c1.id
        c2_id = c2.id
        c3_id = c3.id
        c4_id = c4.id

    return {
        "admin": admin,
        "c1_id": c1_id,
        "c2_id": c2_id,
        "c3_id": c3_id,
        "c4_id": c4_id,
    }


@pytest.mark.asyncio
async def test_admin_deactivate_client_multipart_form(setup_deactivation_context):
    """
    Issue #4362: Admin deactivates client via multipart/form-data with is_active='false'.
    Must process successfully without 500 and update client to inactive in DB.
    """
    ctx = setup_deactivation_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin"]
    app.dependency_overrides[get_current_user] = lambda: ctx["admin"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.put(f"/api/v1/users/{ctx['c1_id']}", data={"is_active": "false"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        assert res.json().get("is_active") is False

    async with AsyncSessionLocal() as db:
        u = await db.scalar(select(User).where(User.id == ctx["c1_id"]))
        assert u.is_active is False

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_deactivate_client_json_status(setup_deactivation_context):
    """
    Issue #4362: Admin deactivates client via JSON with status='Inactive'.
    Must process successfully without 500.
    """
    ctx = setup_deactivation_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin"]
    app.dependency_overrides[get_current_user] = lambda: ctx["admin"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.put(f"/api/v1/users/{ctx['c2_id']}", json={"status": "Inactive"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        assert res.json().get("is_active") is False

    async with AsyncSessionLocal() as db:
        u = await db.scalar(select(User).where(User.id == ctx["c2_id"]))
        assert u.is_active is False

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_deactivate_client_invalid_status_returns_422(setup_deactivation_context):
    """
    Issue #4362: If invalid status is submitted, return clear 422 error, NOT 500.
    """
    ctx = setup_deactivation_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin"]
    app.dependency_overrides[get_current_user] = lambda: ctx["admin"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.put(f"/api/v1/users/{ctx['c3_id']}", json={"status": "UnknownStatus"})
        assert res.status_code == 422, f"Expected 422, got {res.status_code}: {res.text}"
        assert "Invalid status" in res.json().get("detail", "")

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_user_profile_json_partial_update(setup_deactivation_context):
    """
    Issue #4362: Updating normal text fields in JSON preserves unrelated fields.
    """
    ctx = setup_deactivation_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin"]
    app.dependency_overrides[get_current_user] = lambda: ctx["admin"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.put(
            f"/api/v1/users/{ctx['c4_id']}",
            json={
                "full_name": "Updated Client Name",
                "designation": "Executive",
                "department": "Procurement",
            }
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert data.get("full_name") == "Updated Client Name"
        assert data.get("designation") == "Executive"
        assert data.get("department") == "Procurement"
        assert data.get("is_active") is True  # preserved

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_multipart_image_upload_and_preservation(setup_deactivation_context):
    """
    Issue #4362: Multipart profile image upload and preservation:
    1. Upload valid synthetic PNG image alongside metadata.
    2. Verify image is saved to disk and profile_image URL updated.
    3. Perform subsequent update without new image -> existing profile_image is preserved.
    4. Submitting invalid file type (e.g. .txt) returns 400.
    """
    ctx = setup_deactivation_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin"]
    app.dependency_overrides[get_current_user] = lambda: ctx["admin"]

    # Valid 1x1 PNG bytes
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Upload valid image with metadata
        res = await client.put(
            f"/api/v1/users/{ctx['c4_id']}",
            data={"full_name": "Client With Image"},
            files={"profile_image": ("avatar.png", png_bytes, "image/png")}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        saved_img_path = res.json().get("profile_image")
        assert saved_img_path is not None
        assert saved_img_path.startswith("uploads/profile/")
        assert os.path.exists(saved_img_path)

        # 2. Subsequent update with text only (no image) -> existing image preserved
        res_no_img = await client.put(
            f"/api/v1/users/{ctx['c4_id']}",
            data={"designation": "Senior Partner"}
        )
        assert res_no_img.status_code == 200
        assert res_no_img.json().get("profile_image") == saved_img_path
        assert res_no_img.json().get("designation") == "Senior Partner"

        # 3. Invalid file type upload returns 400
        res_invalid = await client.put(
            f"/api/v1/users/{ctx['c4_id']}",
            data={"full_name": "Bad File"},
            files={"profile_image": ("document.txt", b"plain text content", "text/plain")}
        )
        assert res_invalid.status_code == 400

        # Clean up uploaded test file
        if os.path.exists(saved_img_path):
            os.remove(saved_img_path)

    app.dependency_overrides.clear()
