import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.user import User, UserRole
from app.models.owner import Owner
from app.models.project import Project, TaskRequest
from app.core.dependencies import (
    get_current_user,
    get_current_active_user,
    require_permission,
)


@pytest_asyncio.fixture
async def setup_length_test_context():
    run_uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_4411_{run_uid}", is_active=True)
        db.add(comp)
        await db.flush()

        admin = User(
            company_id=comp.id,
            email=f"admin_4411_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=True,
        )
        db.add(admin)
        await db.flush()

        owner = Owner(
            company_id=comp.id,
            owner_code=f"O_4411_{run_uid}",
            owner_name=f"Owner_4411_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            company_id=comp.id,
            owner_id=owner.id,
            project_name=f"Proj_4411_{run_uid}",
            business_id=f"P4411-{run_uid}",
            status="Ongoing",
        )
        db.add(proj)
        await db.commit()

        admin_id = admin.id
        comp_id = comp.id
        proj_id = proj.id

    mock_admin = User(
        id=admin_id,
        company_id=comp_id,
        email=f"admin_4411_{run_uid}@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=True,
    )
    mock_admin.project_memberships = []

    app.dependency_overrides[get_current_user] = lambda: mock_admin
    app.dependency_overrides[get_current_active_user] = lambda: mock_admin
    app.dependency_overrides[require_permission("tasks.create")] = lambda: mock_admin
    app.dependency_overrides[require_permission("tasks.view")] = lambda: mock_admin
    app.dependency_overrides[require_permission("tasks.edit")] = lambda: mock_admin

    context = {
        "comp_id": comp_id,
        "proj_id": proj_id,
        "admin": mock_admin,
    }

    yield context

    app.dependency_overrides.clear()
    async with AsyncSessionLocal() as db:
        await db.execute(TaskRequest.__table__.delete().where(TaskRequest.project_id == context["proj_id"]))
        await db.execute(Project.__table__.delete().where(Project.company_id == context["comp_id"]))
        await db.execute(Owner.__table__.delete().where(Owner.company_id == context["comp_id"]))
        await db.execute(User.__table__.delete().where(User.company_id == context["comp_id"]))
        await db.execute(Company.__table__.delete().where(Company.id == context["comp_id"]))
        await db.commit()


@pytest.mark.asyncio
async def test_excessive_title_rejected_with_422(setup_length_test_context):
    ctx = setup_length_test_context
    long_title = "A" * 256
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": long_title,
                "category": "Civil",
                "priority": "HIGH",
            },
        )
        assert resp.status_code == 422
        assert "cannot exceed 255 characters" in resp.text or "string_too_long" in resp.text


@pytest.mark.asyncio
async def test_excessive_category_rejected_with_422(setup_length_test_context):
    ctx = setup_length_test_context
    long_cat = "C" * 101
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Standard Title",
                "category": long_cat,
                "priority": "HIGH",
            },
        )
        assert resp.status_code == 422
        assert "cannot exceed 100 characters" in resp.text or "string_too_long" in resp.text


@pytest.mark.asyncio
async def test_excessive_description_rejected_with_422(setup_length_test_context):
    ctx = setup_length_test_context
    long_desc = "D" * 5001
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Standard Title",
                "category": "Civil",
                "priority": "HIGH",
                "description": long_desc,
            },
        )
        assert resp.status_code == 422
        assert "cannot exceed 5000 characters" in resp.text or "string_too_long" in resp.text


@pytest.mark.asyncio
async def test_boundary_max_lengths_succeed(setup_length_test_context):
    ctx = setup_length_test_context
    title_255 = "T" * 255
    cat_100 = "C" * 100
    desc_5000 = "D" * 5000

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": title_255,
                "category": cat_100,
                "priority": "HIGH",
                "description": desc_5000,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["title"]) == 255
        assert len(data["category"]) == 100
        assert len(data["description"]) == 5000


@pytest.mark.asyncio
async def test_update_excessive_length_rejected(setup_length_test_context):
    ctx = setup_length_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Task Initial",
                "category": "General",
                "priority": "LOW",
            },
        )
        assert create_resp.status_code == 201
        req_id = create_resp.json()["id"]

        # Attempt to update title with 300 chars
        resp_update = await ac.put(
            f"/api/v1/projects/task-requests/{req_id}",
            json={
                "title": "T" * 300,
                "category": "General",
                "priority": "LOW",
                "status": "PENDING",
            },
        )
        assert resp_update.status_code == 422
