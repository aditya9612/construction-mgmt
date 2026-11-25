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
from app.models.project import Project, ProjectMember, DrawingDocument
from app.models.approval import Approval
from app.models.rbac import Permission, UserPermissionOverride
from app.core.enums import DocumentStatus
from app.core.dependencies import (
    get_current_user,
    get_current_active_user,
)


@pytest_asyncio.fixture
async def setup_drawing_security_context():
    run_uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_4403_{run_uid}", is_active=True)
        db.add(comp)
        await db.flush()

        # Admin user
        admin = User(
            company_id=comp.id,
            email=f"admin_4403_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
            full_name="Admin User",
        )
        # Site Engineer user
        site_eng = User(
            company_id=comp.id,
            email=f"siteeng_4403_{run_uid}@test.com",
            role="SiteEngineer",
            is_active=True,
            is_super_admin=False,
            full_name="Site Engineer User",
        )
        db.add_all([admin, site_eng])
        await db.flush()

        # Grant site_eng explicit permissions via UserPermissionOverride
        perm_approval = await db.scalar(select(Permission.id).where(Permission.code == "approvals.*"))
        perm_drawing = await db.scalar(select(Permission.id).where(Permission.code == "drawings.*"))
        ov1 = UserPermissionOverride(user_id=site_eng.id, permission_id=perm_approval, is_granted=True)
        ov2 = UserPermissionOverride(user_id=site_eng.id, permission_id=perm_drawing, is_granted=True)
        db.add_all([ov1, ov2])
        await db.flush()

        owner = Owner(
            company_id=comp.id,
            owner_code=f"O_4403_{run_uid}",
            owner_name=f"Owner_4403_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add(owner)
        await db.flush()

        # Project 1: Assigned to Site Engineer
        proj1 = Project(
            company_id=comp.id,
            owner_id=owner.id,
            project_name=f"Assigned_Proj_{run_uid}",
            business_id=f"P4403A-{run_uid}",
            status="Ongoing",
        )
        # Project 2: UNASSIGNED to Site Engineer
        proj2 = Project(
            company_id=comp.id,
            owner_id=owner.id,
            project_name=f"Unassigned_Proj_{run_uid}",
            business_id=f"P4403U-{run_uid}",
            status="Ongoing",
        )
        db.add_all([proj1, proj2])
        await db.flush()

        # Assign Site Engineer only to proj1
        member1 = ProjectMember(project_id=proj1.id, user_id=site_eng.id)
        db.add(member1)
        await db.flush()

        # Create drawing in proj1
        draw1 = DrawingDocument(
            project_id=proj1.id,
            drawing_name="Foundation Plan P1",
            version="V1",
            revision_no=0,
            is_folder=False,
            is_latest_version=True,
            approval_status=DocumentStatus.UNDER_REVIEW,
            file_url="uploads/drawings/dummy1.pdf",
        )
        # Create drawing in proj2 (unassigned)
        draw2 = DrawingDocument(
            project_id=proj2.id,
            drawing_name="Elevation Section P2",
            version="V1",
            revision_no=0,
            is_folder=False,
            is_latest_version=True,
            approval_status=DocumentStatus.UNDER_REVIEW,
            file_url="uploads/drawings/dummy2.pdf",
        )
        db.add_all([draw1, draw2])
        await db.flush()

        # Create approval for drawing 1 (assigned project)
        app1 = Approval(
            entity_type="drawing",
            entity_id=draw1.id,
            requested_by=admin.id,
            status="Pending",
            remarks="Approval for P1 drawing",
        )
        # Create approval for drawing 2 (unassigned project)
        app2 = Approval(
            entity_type="drawing",
            entity_id=draw2.id,
            requested_by=admin.id,
            status="Pending",
            remarks="Approval for P2 drawing",
        )
        db.add_all([app1, app2])
        await db.flush()

        draw1.approval_id = app1.id
        draw2.approval_id = app2.id
        await db.commit()

        context = {
            "comp_id": comp.id,
            "admin": admin,
            "site_eng": site_eng,
            "proj1_id": proj1.id,
            "proj2_id": proj2.id,
            "draw1_id": draw1.id,
            "draw2_id": draw2.id,
            "app1_id": app1.id,
            "app2_id": app2.id,
        }

    yield context

    app.dependency_overrides.clear()
    async with AsyncSessionLocal() as db:
        await db.execute(UserPermissionOverride.__table__.delete().where(UserPermissionOverride.user_id == context["site_eng"].id))
        await db.execute(Approval.__table__.delete().where(Approval.id.in_([context["app1_id"], context["app2_id"]])))
        await db.execute(DrawingDocument.__table__.delete().where(DrawingDocument.id.in_([context["draw1_id"], context["draw2_id"]])))
        await db.execute(ProjectMember.__table__.delete().where(ProjectMember.project_id.in_([context["proj1_id"], context["proj2_id"]])))
        await db.execute(Project.__table__.delete().where(Project.company_id == context["comp_id"]))
        await db.execute(Owner.__table__.delete().where(Owner.company_id == context["comp_id"]))
        await db.execute(User.__table__.delete().where(User.company_id == context["comp_id"]))
        await db.execute(Company.__table__.delete().where(Company.id == context["comp_id"]))
        await db.commit()


def override_as_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user


@pytest.mark.asyncio
async def test_site_engineer_cannot_see_unassigned_drawing_approvals(setup_drawing_security_context):
    ctx = setup_drawing_security_context
    override_as_user(ctx["site_eng"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/approvals")
        assert resp.status_code == 200
        items = resp.json()
        item_ids = [item["id"] for item in items]

        # Must see assigned project approval
        assert ctx["app1_id"] in item_ids
        # MUST NOT see unassigned project approval
        assert ctx["app2_id"] not in item_ids


@pytest.mark.asyncio
async def test_site_engineer_cannot_export_unassigned_drawing_approvals(setup_drawing_security_context):
    ctx = setup_drawing_security_context
    override_as_user(ctx["site_eng"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/approvals/export")
        assert resp.status_code == 200
        csv_text = resp.text
        assert "Approval for P1 drawing" in csv_text
        assert "Approval for P2 drawing" not in csv_text


@pytest.mark.asyncio
async def test_site_engineer_unassigned_drawing_approval_history_forbidden(setup_drawing_security_context):
    ctx = setup_drawing_security_context
    override_as_user(ctx["site_eng"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Assigned project drawing history -> 200 OK
        resp_assigned = await ac.get(f"/api/v1/drawings/{ctx['draw1_id']}/approval-history")
        assert resp_assigned.status_code == 200

        # Unassigned project drawing history -> 403 Forbidden
        resp_unassigned = await ac.get(f"/api/v1/drawings/{ctx['draw2_id']}/approval-history")
        assert resp_unassigned.status_code == 403


@pytest.mark.asyncio
async def test_site_engineer_unassigned_drawing_listing_forbidden(setup_drawing_security_context):
    ctx = setup_drawing_security_context
    override_as_user(ctx["site_eng"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Assigned project list drawings -> 200 OK
        resp_assigned = await ac.get(f"/api/v1/drawings?project_id={ctx['proj1_id']}")
        assert resp_assigned.status_code == 200
        assert len(resp_assigned.json()["items"]) >= 1

        # Unassigned project list drawings -> 403 Forbidden
        resp_unassigned = await ac.get(f"/api/v1/drawings?project_id={ctx['proj2_id']}")
        assert resp_unassigned.status_code == 403


@pytest.mark.asyncio
async def test_site_engineer_unassigned_drawing_upload_forbidden(setup_drawing_security_context):
    ctx = setup_drawing_security_context
    override_as_user(ctx["site_eng"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/drawings/upload",
            data={
                "project_id": ctx["proj2_id"],
                "drawing_name": "Unauthorized Drawing",
                "version": "V1",
            },
            files={"file": ("test.pdf", b"%PDF-1.4 dummy", "application/pdf")},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_site_engineer_unassigned_approval_decision_forbidden(setup_drawing_security_context):
    ctx = setup_drawing_security_context
    override_as_user(ctx["site_eng"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Trying to approve unassigned drawing approval -> 403
        resp_approve = await ac.put(
            f"/api/v1/approvals/{ctx['app2_id']}/approve",
            json={"remarks": "Attempted approve"},
        )
        assert resp_approve.status_code == 403

        # Trying to reject unassigned drawing approval -> 403
        resp_reject = await ac.put(
            f"/api/v1/approvals/{ctx['app2_id']}/reject",
            json={"remarks": "Attempted reject"},
        )
        assert resp_reject.status_code == 403


@pytest.mark.asyncio
async def test_admin_has_company_wide_access_to_all_drawings_and_approvals(setup_drawing_security_context):
    ctx = setup_drawing_security_context
    override_as_user(ctx["admin"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Admin can view all approvals
        resp_approvals = await ac.get("/api/v1/approvals")
        assert resp_approvals.status_code == 200
        item_ids = [item["id"] for item in resp_approvals.json()]
        assert ctx["app1_id"] in item_ids
        assert ctx["app2_id"] in item_ids

        # Admin can view drawings for both projects
        resp_p1 = await ac.get(f"/api/v1/drawings?project_id={ctx['proj1_id']}")
        assert resp_p1.status_code == 200
        resp_p2 = await ac.get(f"/api/v1/drawings?project_id={ctx['proj2_id']}")
        assert resp_p2.status_code == 200
