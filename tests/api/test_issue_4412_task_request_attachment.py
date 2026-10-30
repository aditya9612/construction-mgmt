import os
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
async def setup_attachment_test_context():
    run_uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_4412_{run_uid}", is_active=True)
        db.add(comp)
        await db.flush()

        admin = User(
            company_id=comp.id,
            email=f"admin_4412_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=True,
        )
        db.add(admin)
        await db.flush()

        owner = Owner(
            company_id=comp.id,
            owner_code=f"O_4412_{run_uid}",
            owner_name=f"Owner_4412_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            company_id=comp.id,
            owner_id=owner.id,
            project_name=f"Proj_4412_{run_uid}",
            business_id=f"P4412-{run_uid}",
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
        email=f"admin_4412_{run_uid}@test.com",
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
async def test_attachment_upload_persists_and_returns_url(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    file_bytes = b"%PDF-1.4 test document content"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Review Site Blueprint",
                "category": "Architectural",
                "priority": "HIGH",
            },
            files={"attachment": ("blueprint.pdf", file_bytes, "application/pdf")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["attachment_url"] is not None
        assert data["attachment_url"].startswith("/uploads/task_requests/")
        assert "blueprint.pdf" in data["attachment_url"]

        # Verify file exists on local disk
        rel_path = data["attachment_url"].lstrip("/")
        assert os.path.exists(rel_path)
        with open(rel_path, "rb") as f:
            assert f.read() == file_bytes


@pytest.mark.asyncio
async def test_file_alias_upload_persists_and_returns_url(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    image_bytes = b"\x89PNG\r\n\x1a\n test png image"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Photo of Site Issue",
                "category": "Civil",
                "priority": "MEDIUM",
            },
            files={"file": ("site_photo.png", image_bytes, "image/png")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["attachment_url"] is not None
        assert data["attachment_url"].startswith("/uploads/task_requests/")
        assert "site_photo.png" in data["attachment_url"]


@pytest.mark.asyncio
async def test_disallowed_extension_rejected(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    malicious_bytes = b"#!/bin/bash\necho dangerous"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Malicious Script Task",
                "category": "General",
                "priority": "LOW",
            },
            files={"attachment": ("script.sh", malicious_bytes, "application/x-sh")},
        )
        assert resp.status_code == 400
        assert "invalid file extension" in resp.text.lower()


@pytest.mark.asyncio
async def test_file_size_exceeding_15mb_rejected(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    oversized_bytes = b"0" * (16 * 1024 * 1024)  # 16 MB

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Huge Document Task",
                "category": "General",
                "priority": "LOW",
            },
            files={"attachment": ("huge.pdf", oversized_bytes, "application/pdf")},
        )
        assert resp.status_code == 400
        assert "exceeds maximum allowed limit" in resp.text.lower()


@pytest.mark.asyncio
async def test_path_traversal_filename_sanitized(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    doc_bytes = b"Safe content"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Traversal Test Task",
                "category": "Security",
                "priority": "MEDIUM",
            },
            files={"attachment": ("../../etc/passwd.pdf", doc_bytes, "application/pdf")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["attachment_url"] is not None
        assert ".." not in data["attachment_url"]
        assert data["attachment_url"].startswith("/uploads/task_requests/")


@pytest.mark.asyncio
async def test_preserve_attachment_url_string(setup_attachment_test_context):
    ctx = setup_attachment_test_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "External Doc Task",
                "category": "General",
                "priority": "LOW",
                "attachment_url": "/uploads/documents/pre_existing.pdf",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["attachment_url"] == "/uploads/documents/pre_existing.pdf"
