import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

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
async def setup_task_request_test_context():
    run_uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_4416_{run_uid}", is_active=True)
        db.add(comp)
        await db.flush()

        admin = User(
            company_id=comp.id,
            email=f"admin_4416_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=True,
        )
        db.add(admin)
        await db.flush()

        owner = Owner(
            company_id=comp.id,
            owner_code=f"O_4416_{run_uid}",
            owner_name=f"Owner_4416_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            company_id=comp.id,
            owner_id=owner.id,
            project_name=f"Proj_4416_{run_uid}",
            business_id=f"P4416-{run_uid}",
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
        email=f"admin_4416_{run_uid}@test.com",
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
    app.dependency_overrides[require_permission("tasks.delete")] = lambda: mock_admin

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
async def test_single_char_title_rejected_form(setup_task_request_test_context):
    ctx = setup_task_request_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "A",
                "category": "Civil",
                "priority": "HIGH",
                "description": "Valid description",
            },
        )
        assert resp.status_code == 422
        assert "at least 3 characters" in resp.text.lower() or "string_too_short" in resp.text.lower()


@pytest.mark.asyncio
async def test_two_char_title_rejected_form(setup_task_request_test_context):
    ctx = setup_task_request_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "AB",
                "category": "Civil",
                "priority": "HIGH",
            },
        )
        assert resp.status_code == 422
        assert "at least 3 characters" in resp.text.lower() or "string_too_short" in resp.text.lower()


@pytest.mark.asyncio
async def test_whitespace_title_rejected(setup_task_request_test_context):
    ctx = setup_task_request_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "   ",
                "category": "Civil",
                "priority": "HIGH",
            },
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_single_char_category_rejected(setup_task_request_test_context):
    ctx = setup_task_request_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Valid Title",
                "category": "C",
                "priority": "HIGH",
            },
        )
        assert resp.status_code == 422
        assert "at least 3 characters" in resp.text.lower() or "string_too_short" in resp.text.lower()


@pytest.mark.asyncio
async def test_invalid_priority_rejected(setup_task_request_test_context):
    ctx = setup_task_request_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Valid Title",
                "category": "Construction",
                "priority": "URGENT",
            },
        )
        assert resp.status_code == 422
        assert "one of: low, medium, high" in resp.text.lower()


@pytest.mark.asyncio
async def test_valid_task_request_creation_succeeds(setup_task_request_test_context):
    ctx = setup_task_request_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Inspect Foundation",
                "category": "Structural",
                "priority": "HIGH",
                "description": "Check foundation rebar spacing.",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Inspect Foundation"
        assert data["category"] == "Structural"
        assert data["priority"] == "HIGH"
        assert data["project_id"] == ctx["proj_id"]


@pytest.mark.asyncio
async def test_update_task_request_enforces_min_length(setup_task_request_test_context):
    ctx = setup_task_request_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Initial Task",
                "category": "Electrical",
                "priority": "LOW",
            },
        )
        assert create_resp.status_code == 201
        req_id = create_resp.json()["id"]

        # Try updating with single-character title
        update_resp = await ac.put(
            f"/api/v1/projects/task-requests/{req_id}",
            json={
                "title": "X",
                "category": "Electrical",
                "priority": "LOW",
                "status": "PENDING",
            },
        )
        assert update_resp.status_code == 422

        # Try updating with single-character category
        update_resp_cat = await ac.put(
            f"/api/v1/projects/task-requests/{req_id}",
            json={
                "title": "Updated Task",
                "category": "E",
                "priority": "LOW",
                "status": "PENDING",
            },
        )
        assert update_resp_cat.status_code == 422

        # Valid update
        valid_update = await ac.put(
            f"/api/v1/projects/task-requests/{req_id}",
            json={
                "title": "Updated Task Long",
                "category": "Electrical Works",
                "priority": "MEDIUM",
                "status": "APPROVED",
            },
        )
        assert valid_update.status_code == 200
        assert valid_update.json()["title"] == "Updated Task Long"
        assert valid_update.json()["status"] == "APPROVED"
