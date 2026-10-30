import os
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
from app.models.project import Project, ProjectMember, TaskRequest
from app.models.rbac import Permission, UserPermissionOverride
from app.core.dependencies import (
    get_current_user,
    get_current_active_user,
    require_permission,
)


@pytest_asyncio.fixture
async def setup_attachment_test_context():
    run_uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        comp_a = Company(name=f"CompA_4412_{run_uid}", is_active=True)
        comp_b = Company(name=f"CompB_4412_{run_uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        admin_a = User(
            company_id=comp_a.id,
            email=f"admin_4412_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=True,
        )
        user_b = User(
            company_id=comp_b.id,
            email=f"user_b_4412_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        site_eng_unassigned = User(
            company_id=comp_a.id,
            email=f"siteeng_4412_{run_uid}@test.com",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            is_super_admin=False,
        )
        site_eng_assigned = User(
            company_id=comp_a.id,
            email=f"siteeng_ass_4412_{run_uid}@test.com",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([admin_a, user_b, site_eng_unassigned, site_eng_assigned])
        await db.flush()

        # Grant site_eng permissions via UserPermissionOverride
        perm_task_view = await db.scalar(select(Permission.id).where(Permission.code == "tasks.view"))
        if perm_task_view:
            ov1 = UserPermissionOverride(user_id=site_eng_unassigned.id, permission_id=perm_task_view, is_granted=True)
            ov2 = UserPermissionOverride(user_id=site_eng_assigned.id, permission_id=perm_task_view, is_granted=True)
            db.add_all([ov1, ov2])
            await db.flush()

        owner = Owner(
            company_id=comp_a.id,
            owner_code=f"O_4412_{run_uid}",
            owner_name=f"Owner_4412_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            company_id=comp_a.id,
            owner_id=owner.id,
            project_name=f"Proj_4412_{run_uid}",
            business_id=f"P4412-{run_uid}",
            status="Ongoing",
        )
        db.add(proj)
        await db.flush()

        # Assign only site_eng_assigned to proj
        member = ProjectMember(project_id=proj.id, user_id=site_eng_assigned.id)
        db.add(member)
        await db.commit()

        context = {
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "proj_id": proj.id,
            "admin_a": admin_a,
            "user_b": user_b,
            "site_eng_unassigned": site_eng_unassigned,
            "site_eng_assigned": site_eng_assigned,
        }

    # Default auth as Admin A
    app.dependency_overrides[get_current_user] = lambda: context["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: context["admin_a"]
    app.dependency_overrides[require_permission("tasks.create")] = lambda: context["admin_a"]
    app.dependency_overrides[require_permission("tasks.view")] = lambda: context["admin_a"]
    app.dependency_overrides[require_permission("tasks.edit")] = lambda: context["admin_a"]
    app.dependency_overrides[require_permission("tasks.delete")] = lambda: context["admin_a"]

    yield context

    app.dependency_overrides.clear()
    async with AsyncSessionLocal() as db:
        await db.execute(TaskRequest.__table__.delete().where(TaskRequest.project_id == context["proj_id"]))
        await db.execute(ProjectMember.__table__.delete().where(ProjectMember.project_id == context["proj_id"]))
        await db.execute(Project.__table__.delete().where(Project.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.execute(Owner.__table__.delete().where(Owner.company_id == context["comp_a_id"]))
        await db.execute(UserPermissionOverride.__table__.delete().where(UserPermissionOverride.user_id.in_([context["site_eng_unassigned"].id, context["site_eng_assigned"].id])))
        await db.execute(User.__table__.delete().where(User.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.execute(Company.__table__.delete().where(Company.id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.commit()


# =============================================================================
# 1. ATTACHMENT CREATION & URL VALIDATION
# =============================================================================


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
async def test_uploaded_file_ignores_malicious_attachment_url(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    file_bytes = b"%PDF-1.4 safe document"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Override Test Task",
                "category": "Civil",
                "priority": "HIGH",
                "attachment_url": "https://malicious.com/exploit.exe",
            },
            files={"attachment": ("doc.pdf", file_bytes, "application/pdf")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "malicious.com" not in data["attachment_url"]
        assert data["attachment_url"].startswith("/uploads/task_requests/")


@pytest.mark.asyncio
async def test_no_file_arbitrary_urls_rejected_with_422(setup_attachment_test_context):
    ctx = setup_attachment_test_context

    malicious_inputs = [
        "https://evil.com/phishing.pdf",
        "http://attacker.com/payload.exe",
        "javascript:alert(document.cookie)",
        "data:text/html,<script>alert(1)</script>",
        "/etc/passwd",
        "/uploads/documents/fake.pdf",
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for bad_url in malicious_inputs:
            resp = await ac.post(
                "/api/v1/projects/task-requests",
                data={
                    "project_id": ctx["proj_id"],
                    "title": "Attack Task Request",
                    "category": "Civil",
                    "priority": "HIGH",
                    "attachment_url": bad_url,
                },
            )
            assert resp.status_code == 422, f"Expected 422 for {bad_url}, got {resp.status_code}"


@pytest.mark.asyncio
async def test_no_attachment_url_remains_null(setup_attachment_test_context):
    ctx = setup_attachment_test_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "No Attachment Task",
                "category": "Civil",
                "priority": "LOW",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["attachment_url"] is None


@pytest.mark.asyncio
async def test_update_preserves_trusted_attachment_and_rejects_malicious_mutation(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    file_bytes = b"%PDF-1.4 initial content"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Initial Task With File",
                "category": "Civil",
                "priority": "MEDIUM",
            },
            files={"attachment": ("initial.pdf", file_bytes, "application/pdf")},
        )
        assert create_resp.status_code == 201
        req_id = create_resp.json()["id"]
        trusted_url = create_resp.json()["attachment_url"]

        # Update without attachment_url preserves trusted URL
        update_resp = await ac.put(
            f"/api/v1/projects/task-requests/{req_id}",
            json={
                "title": "Updated Task Title",
                "category": "Civil",
                "priority": "HIGH",
                "status": "APPROVED",
            },
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["attachment_url"] == trusted_url

        # Attempt to inject javascript or external URL via update
        bad_update = await ac.put(
            f"/api/v1/projects/task-requests/{req_id}",
            json={
                "title": "Updated Task Title",
                "category": "Civil",
                "priority": "HIGH",
                "status": "APPROVED",
                "attachment_url": "javascript:alert(1)",
            },
        )
        assert bad_update.status_code == 422


# =============================================================================
# 2. FILE VALIDATION & INTEGRITY
# =============================================================================


@pytest.mark.asyncio
async def test_empty_file_rejected_with_400(setup_attachment_test_context):
    ctx = setup_attachment_test_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Empty File Task",
                "category": "General",
                "priority": "LOW",
            },
            files={"attachment": ("empty.pdf", b"", "application/pdf")},
        )
        assert resp.status_code == 400
        assert "cannot be empty" in resp.text.lower()


@pytest.mark.asyncio
async def test_disallowed_extension_rejected(setup_attachment_test_context):
    ctx = setup_attachment_test_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Malicious Script Task",
                "category": "General",
                "priority": "LOW",
            },
            files={"attachment": ("script.sh", b"#!/bin/bash", "application/x-sh")},
        )
        assert resp.status_code == 400
        assert "invalid file extension" in resp.text.lower()


@pytest.mark.asyncio
async def test_file_size_exceeding_15mb_rejected(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    oversized_bytes = b"0" * (16 * 1024 * 1024)

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
async def test_fake_signature_rejected_with_400(setup_attachment_test_context):
    ctx = setup_attachment_test_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Fake PDF Task",
                "category": "General",
                "priority": "LOW",
            },
            files={"attachment": ("fake.pdf", b"<script>alert(1)</script>", "text/html")},
        )
        assert resp.status_code == 400
        assert "signature" in resp.text.lower() or "format" in resp.text.lower()

        # Non-OOXML zip disguised as docx
        import io, zipfile
        fake_docx_buf = io.BytesIO()
        with zipfile.ZipFile(fake_docx_buf, "w") as zf:
            zf.writestr("test.txt", "not an office document")
        fake_docx_bytes = fake_docx_buf.getvalue()

        resp_docx = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Fake Docx Task",
                "category": "General",
                "priority": "LOW",
            },
            files={"attachment": ("fake.docx", fake_docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        assert resp_docx.status_code == 400
        assert "office" in resp_docx.text.lower()


@pytest.mark.asyncio
async def test_path_traversal_filename_sanitized(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    doc_bytes = b"%PDF-1.4 Safe content"

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


# =============================================================================
# 3. SECURE AUTHENTICATED DOWNLOAD & DIRECT STATIC ACCESS BLOCK
# =============================================================================


@pytest.mark.asyncio
async def test_authenticated_attachment_download_succeeds(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    pdf_bytes = b"%PDF-1.4 test document content for download"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Download Test Task",
                "category": "Civil",
                "priority": "HIGH",
            },
            files={"attachment": ("doc_to_download.pdf", pdf_bytes, "application/pdf")},
        )
        assert create_resp.status_code == 201
        req_id = create_resp.json()["id"]

        # Authorized Admin downloads file via authenticated endpoint
        down_resp = await ac.get(f"/api/v1/projects/task-requests/{req_id}/attachment")
        assert down_resp.status_code == 200
        assert down_resp.content == pdf_bytes
        assert down_resp.headers["content-type"] == "application/octet-stream"
        assert "attachment;" in down_resp.headers["content-disposition"]
        assert down_resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_direct_static_access_to_task_requests_and_sensitive_dirs_forbidden(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    pdf_bytes = b"%PDF-1.4 confidential attachment"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Static Block Test Task",
                "category": "Civil",
                "priority": "HIGH",
            },
            files={"attachment": ("confidential.pdf", pdf_bytes, "application/pdf")},
        )
        assert create_resp.status_code == 201
        static_url = create_resp.json()["attachment_url"] # /uploads/task_requests/...

        # Anonymous access to static URL must be 403 Forbidden
        app.dependency_overrides.clear()
        anon_resp = await ac.get(static_url)
        assert anon_resp.status_code == 403
        assert "forbidden" in anon_resp.text.lower()

        # Case variations of task_requests under /uploads must remain 403 Forbidden
        assert (await ac.get("/uploads/TASK_REQUESTS/test.pdf")).status_code == 403
        assert (await ac.get("/uploads/Task_Requests/test.pdf")).status_code == 403
        # Other directories are not blocked by Task Request restriction
        assert (await ac.get("/uploads/payments/nonexistent.pdf")).status_code != 403
        assert (await ac.get("/uploads/qc/nonexistent.pdf")).status_code != 403


@pytest.mark.asyncio
async def test_cross_tenant_and_unassigned_user_download_denied(setup_attachment_test_context):
    ctx = setup_attachment_test_context
    pdf_bytes = b"%PDF-1.4 secret tenant doc"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_resp = await ac.post(
            "/api/v1/projects/task-requests",
            data={
                "project_id": ctx["proj_id"],
                "title": "Tenant Scoping Task",
                "category": "Civil",
                "priority": "HIGH",
            },
            files={"attachment": ("scoped.pdf", pdf_bytes, "application/pdf")},
        )
        assert create_resp.status_code == 201
        req_id = create_resp.json()["id"]

        # 1. Cross-tenant user (Company B) -> 404 Not Found (tenant isolation)
        app.dependency_overrides[get_current_user] = lambda: ctx["user_b"]
        app.dependency_overrides[get_current_active_user] = lambda: ctx["user_b"]
        app.dependency_overrides[require_permission("tasks.view")] = lambda: ctx["user_b"]
        resp_b = await ac.get(f"/api/v1/projects/task-requests/{req_id}/attachment")
        assert resp_b.status_code == 404

        # 2. Site Engineer unassigned to project -> 403 Forbidden (assert_project_access)
        app.dependency_overrides[get_current_user] = lambda: ctx["site_eng_unassigned"]
        app.dependency_overrides[get_current_active_user] = lambda: ctx["site_eng_unassigned"]
        app.dependency_overrides[require_permission("tasks.view")] = lambda: ctx["site_eng_unassigned"]
        resp_unass = await ac.get(f"/api/v1/projects/task-requests/{req_id}/attachment")
        assert resp_unass.status_code == 403

        # 3. Site Engineer assigned to project -> 200 OK
        app.dependency_overrides[get_current_user] = lambda: ctx["site_eng_assigned"]
        app.dependency_overrides[get_current_active_user] = lambda: ctx["site_eng_assigned"]
        app.dependency_overrides[require_permission("tasks.view")] = lambda: ctx["site_eng_assigned"]
        resp_ass = await ac.get(f"/api/v1/projects/task-requests/{req_id}/attachment")
        assert resp_ass.status_code == 200
        assert resp_ass.content == pdf_bytes
