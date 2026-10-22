import pytest
import uuid
import os
from contextlib import asynccontextmanager
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.user import User
from app.models.project import Project, DrawingDocument
from app.models.owner import Owner
from app.models.approval import Approval
from app.core.security import get_password_hash, create_access_token


@asynccontextmanager
async def setup_drawing_test_data():
    """Setup company, project, admin user, and teardown after test."""
    uid = uuid.uuid4().hex[:8]
    uploaded_files = []

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Drawing_Comp_{uid}")
        db.add(comp)
        await db.flush()

        owner = Owner(
            owner_code=f"OWN-DRW-{uid}",
            owner_name=f"Owner DRW {uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_{uid}@test.com",
            company_id=comp.id,
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            business_id=f"PRJ-DRW-{uid}",
            project_name=f"Project DRW {uid}",
            company_id=comp.id,
            owner_id=owner.id,
            status="Planned",
        )
        db.add(proj)
        await db.flush()

        admin_user = User(
            email=f"admin_drw_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name=f"Admin DRW {uid}",
            company_id=comp.id,
            role="Admin",
            is_active=True,
            is_super_admin=False,
        )
        db.add(admin_user)
        await db.commit()

        token = create_access_token(
            data={"sub": str(admin_user.id), "role": admin_user.role, "company_id": comp.id}
        )
        headers = {"Authorization": f"Bearer {token}"}

        context = {
            "comp": comp,
            "proj": proj,
            "admin_user": admin_user,
            "headers": headers,
            "uploaded_files": uploaded_files,
        }

    try:
        yield context
    finally:
        # Cleanup DB and disk
        async with AsyncSessionLocal() as clean_db:
            drawings = (
                await clean_db.scalars(
                    select(DrawingDocument).where(DrawingDocument.project_id == proj.id)
                )
            ).all()
            for d in drawings:
                if d.file_url and os.path.exists(d.file_url):
                    try:
                        os.remove(d.file_url)
                    except OSError:
                        pass

            for f_path in uploaded_files:
                if os.path.exists(f_path):
                    try:
                        os.remove(f_path)
                    except OSError:
                        pass

            await clean_db.execute(
                delete(Approval).where(Approval.requested_by == admin_user.id)
            )
            await clean_db.execute(
                delete(DrawingDocument).where(DrawingDocument.project_id == proj.id)
            )
            await clean_db.execute(delete(Project).where(Project.id == proj.id))
            await clean_db.execute(delete(Owner).where(Owner.id == owner.id))
            await clean_db.execute(delete(User).where(User.id == admin_user.id))
            await clean_db.execute(delete(Company).where(Company.id == comp.id))
            await clean_db.commit()


# ==============================================================================
# Endpoint 1: POST /api/v1/drawings/upload
# ==============================================================================


@pytest.mark.asyncio
async def test_drawing_upload_accepts_254_chars():
    """Verify POST /api/v1/drawings/upload accepts a 254-character drawing_name."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            name_254 = "A" * 254
            file_content = b"%PDF-1.4 test upload 254 chars"

            resp = await ac.post(
                "/api/v1/drawings/upload",
                headers=headers,
                data={
                    "project_id": str(proj_id),
                    "drawing_name": name_254,
                    "version": "v1.0",
                },
                files={"file": ("plan_254.pdf", file_content, "application/pdf")},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data["drawing_name"] == name_254
            if data.get("file_url"):
                ctx["uploaded_files"].append(data["file_url"])

            # Verify persisted in DB
            async with AsyncSessionLocal() as db:
                db_record = await db.scalar(
                    select(DrawingDocument).where(
                        DrawingDocument.project_id == proj_id,
                        DrawingDocument.drawing_name == name_254,
                    )
                )
                assert db_record is not None
                assert db_record.drawing_name == name_254


@pytest.mark.asyncio
async def test_drawing_upload_accepts_255_chars():
    """Verify POST /api/v1/drawings/upload accepts a 255-character drawing_name (boundary valid)."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            name_255 = "B" * 255
            file_content = b"%PDF-1.4 test upload 255 chars"

            resp = await ac.post(
                "/api/v1/drawings/upload",
                headers=headers,
                data={
                    "project_id": str(proj_id),
                    "drawing_name": name_255,
                    "version": "v1.0",
                },
                files={"file": ("plan_255.pdf", file_content, "application/pdf")},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data["drawing_name"] == name_255
            if data.get("file_url"):
                ctx["uploaded_files"].append(data["file_url"])

            # Verify persisted in DB
            async with AsyncSessionLocal() as db:
                db_record = await db.scalar(
                    select(DrawingDocument).where(
                        DrawingDocument.project_id == proj_id,
                        DrawingDocument.drawing_name == name_255,
                    )
                )
                assert db_record is not None
                assert db_record.drawing_name == name_255


@pytest.mark.asyncio
async def test_drawing_upload_rejects_256_chars():
    """Verify POST /api/v1/drawings/upload rejects a 256-character drawing_name with HTTP 400."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            name_256 = "C" * 256
            file_content = b"%PDF-1.4 test upload 256 chars"

            resp = await ac.post(
                "/api/v1/drawings/upload",
                headers=headers,
                data={
                    "project_id": str(proj_id),
                    "drawing_name": name_256,
                    "version": "v1.0",
                },
                files={"file": ("plan_256.pdf", file_content, "application/pdf")},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            assert "Drawing Name must not exceed the maximum allowed character limit." in resp.text

            # Verify NOT persisted in DB
            async with AsyncSessionLocal() as db:
                db_record = await db.scalar(
                    select(DrawingDocument).where(
                        DrawingDocument.project_id == proj_id,
                        DrawingDocument.drawing_name == name_256,
                    )
                )
                assert db_record is None, "Overlength drawing record must not be persisted to database"


@pytest.mark.asyncio
async def test_drawing_upload_rejects_empty_string():
    """Verify POST /api/v1/drawings/upload rejects an empty/whitespace drawing_name with HTTP 400."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            file_content = b"%PDF-1.4 test upload empty"

            resp = await ac.post(
                "/api/v1/drawings/upload",
                headers=headers,
                data={
                    "project_id": str(proj_id),
                    "drawing_name": "    ",
                    "version": "v1.0",
                },
                files={"file": ("plan_empty.pdf", file_content, "application/pdf")},
            )
            assert resp.status_code == 400
            assert "Field cannot be empty" in resp.text


# ==============================================================================
# Endpoint 2: PUT /api/v1/drawings/{id}
# ==============================================================================


@pytest.mark.asyncio
async def test_drawing_update_accepts_254_chars():
    """Verify PUT /api/v1/drawings/{id} accepts 254 characters on DrawingUpdate."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]

            async with AsyncSessionLocal() as db:
                doc = DrawingDocument(
                    project_id=proj_id,
                    drawing_name="Initial Name 254",
                    version="v1.0",
                    is_folder=False,
                    is_latest_version=True,
                )
                db.add(doc)
                await db.commit()
                await db.refresh(doc)
                doc_id = doc.id

            name_254 = "X" * 254
            resp = await ac.put(
                f"/api/v1/drawings/{doc_id}",
                headers=headers,
                json={"drawing_name": name_254},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert resp.json()["drawing_name"] == name_254

            # Verify in DB
            async with AsyncSessionLocal() as db:
                refreshed = await db.get(DrawingDocument, doc_id)
                assert refreshed.drawing_name == name_254


@pytest.mark.asyncio
async def test_drawing_update_accepts_255_chars():
    """Verify PUT /api/v1/drawings/{id} accepts 255 characters on DrawingUpdate (boundary valid)."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]

            async with AsyncSessionLocal() as db:
                doc = DrawingDocument(
                    project_id=proj_id,
                    drawing_name="Initial Name 255",
                    version="v1.0",
                    is_folder=False,
                    is_latest_version=True,
                )
                db.add(doc)
                await db.commit()
                await db.refresh(doc)
                doc_id = doc.id

            name_255 = "Y" * 255
            resp = await ac.put(
                f"/api/v1/drawings/{doc_id}",
                headers=headers,
                json={"drawing_name": name_255},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert resp.json()["drawing_name"] == name_255

            # Verify in DB
            async with AsyncSessionLocal() as db:
                refreshed = await db.get(DrawingDocument, doc_id)
                assert refreshed.drawing_name == name_255


@pytest.mark.asyncio
async def test_drawing_update_rejects_256_chars():
    """Verify PUT /api/v1/drawings/{id} rejects 256 characters on DrawingUpdate with HTTP 422."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            initial_name = "Preserved Drawing Name"

            async with AsyncSessionLocal() as db:
                doc = DrawingDocument(
                    project_id=proj_id,
                    drawing_name=initial_name,
                    version="v1.0",
                    is_folder=False,
                    is_latest_version=True,
                )
                db.add(doc)
                await db.commit()
                await db.refresh(doc)
                doc_id = doc.id

            name_256 = "Z" * 256
            resp = await ac.put(
                f"/api/v1/drawings/{doc_id}",
                headers=headers,
                json={"drawing_name": name_256},
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
            assert "Drawing Name must not exceed the maximum allowed character limit." in resp.text

            # Verify DB was NOT updated
            async with AsyncSessionLocal() as db:
                refreshed = await db.get(DrawingDocument, doc_id)
                assert refreshed.drawing_name == initial_name


@pytest.mark.asyncio
async def test_drawing_update_rejects_empty_string():
    """Verify PUT /api/v1/drawings/{id} rejects empty string on DrawingUpdate with HTTP 422."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            initial_name = "Preserved For Empty Test"

            async with AsyncSessionLocal() as db:
                doc = DrawingDocument(
                    project_id=proj_id,
                    drawing_name=initial_name,
                    version="v1.0",
                    is_folder=False,
                    is_latest_version=True,
                )
                db.add(doc)
                await db.commit()
                await db.refresh(doc)
                doc_id = doc.id

            resp = await ac.put(
                f"/api/v1/drawings/{doc_id}",
                headers=headers,
                json={"drawing_name": "   "},
            )
            assert resp.status_code == 422
            assert "Field cannot be empty" in resp.text

            # Verify DB was NOT updated
            async with AsyncSessionLocal() as db:
                refreshed = await db.get(DrawingDocument, doc_id)
                assert refreshed.drawing_name == initial_name


# ==============================================================================
# Endpoint 3: POST /api/v1/drawings/folders
# ==============================================================================


@pytest.mark.asyncio
async def test_drawing_folder_create_accepts_254_chars():
    """Verify POST /api/v1/drawings/folders accepts 254-character folder_name."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            name_254 = "F" * 254

            resp = await ac.post(
                f"/api/v1/drawings/folders?project_id={proj_id}",
                headers=headers,
                json={"folder_name": name_254},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert resp.json()["folder_name"] == name_254

            # Verify in DB
            async with AsyncSessionLocal() as db:
                record = await db.scalar(
                    select(DrawingDocument).where(
                        DrawingDocument.project_id == proj_id,
                        DrawingDocument.drawing_name == name_254,
                    )
                )
                assert record is not None
                assert record.is_folder is True


@pytest.mark.asyncio
async def test_drawing_folder_create_accepts_255_chars():
    """Verify POST /api/v1/drawings/folders accepts 255-character folder_name (boundary valid)."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            name_255 = "G" * 255

            resp = await ac.post(
                f"/api/v1/drawings/folders?project_id={proj_id}",
                headers=headers,
                json={"folder_name": name_255},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert resp.json()["folder_name"] == name_255

            # Verify in DB
            async with AsyncSessionLocal() as db:
                record = await db.scalar(
                    select(DrawingDocument).where(
                        DrawingDocument.project_id == proj_id,
                        DrawingDocument.drawing_name == name_255,
                    )
                )
                assert record is not None
                assert record.is_folder is True


@pytest.mark.asyncio
async def test_drawing_folder_create_rejects_256_chars():
    """Verify POST /api/v1/drawings/folders rejects 256-character folder_name with HTTP 422."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            name_256 = "H" * 256

            resp = await ac.post(
                f"/api/v1/drawings/folders?project_id={proj_id}",
                headers=headers,
                json={"folder_name": name_256},
            )
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
            assert "Drawing Name must not exceed the maximum allowed character limit." in resp.text

            # Verify NOT in DB
            async with AsyncSessionLocal() as db:
                record = await db.scalar(
                    select(DrawingDocument).where(
                        DrawingDocument.project_id == proj_id,
                        DrawingDocument.drawing_name == name_256,
                    )
                )
                assert record is None


@pytest.mark.asyncio
async def test_drawing_folder_create_rejects_empty_string():
    """Verify POST /api/v1/drawings/folders rejects empty string with HTTP 422."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]

            resp = await ac.post(
                f"/api/v1/drawings/folders?project_id={proj_id}",
                headers=headers,
                json={"folder_name": "   "},
            )
            assert resp.status_code == 422
            assert "Field cannot be empty" in resp.text


# ==============================================================================
# Approval Remarks Bounds & History Regression Test
# ==============================================================================


@pytest.mark.asyncio
async def test_drawing_upload_approval_remarks_bounds_at_255_chars():
    """Verify that uploading a 255-character drawing bounds Approval.remarks to <= 255 chars,
    allowing successful creation and retrieval via approval-history endpoint."""
    async with setup_drawing_test_data() as ctx:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            proj_id = ctx["proj"].id
            headers = ctx["headers"]
            name_255 = "K" * 255
            file_content = b"%PDF-1.4 test approval remarks boundary"

            resp = await ac.post(
                "/api/v1/drawings/upload",
                headers=headers,
                data={
                    "project_id": str(proj_id),
                    "drawing_name": name_255,
                    "version": "v1.0",
                },
                files={"file": ("plan_approval_255.pdf", file_content, "application/pdf")},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            doc_id = data["id"]
            if data.get("file_url"):
                ctx["uploaded_files"].append(data["file_url"])

            # Verify in DB: Approval.remarks is bounded and matches prefix
            async with AsyncSessionLocal() as db:
                approval = await db.scalar(
                    select(Approval).where(
                        Approval.entity_type == "drawing",
                        Approval.entity_id == doc_id,
                    )
                )
                assert approval is not None
                assert len(approval.remarks) <= 255
                expected_prefix = "Approval requested for drawing: "
                assert approval.remarks.startswith(expected_prefix)
                assert approval.remarks == f"{expected_prefix}{name_255}"[:255]

            # Verify GET /api/v1/drawings/{id}/approval-history returns HTTP 200 with the remarks
            history_resp = await ac.get(
                f"/api/v1/drawings/{doc_id}/approval-history",
                headers=headers,
            )
            assert history_resp.status_code == 200
            history_data = history_resp.json()
            assert len(history_data) >= 1
            assert history_data[0]["remarks"] == f"{expected_prefix}{name_255}"[:255]
