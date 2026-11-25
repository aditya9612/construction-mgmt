from decimal import Decimal
from datetime import date
import io
import csv
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

import app.db.base  # Ensure all models are registered
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember, DrawingDocument
from app.models.user import User, UserRole
from app.models.approval import Approval
from app.models.rbac import Permission, UserPermissionOverride
from app.models.expense import Expense
from app.models.material import Material
from app.models.accountant import VendorBill
from app.core.enums import DocumentStatus, ProjectStatus
from app.core.security import get_password_hash
from app.core.dependencies import get_current_user, get_current_active_user


@pytest.fixture
def run_uid():
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def setup_test_context(run_uid):
    """
    Sets up isolated Company A, Company B, Projects, Users, and teardown.
    """
    pwd_hash = get_password_hash("TestPass123!")

    async with AsyncSessionLocal() as db:
        # Company A
        comp_a = Company(name=f"CompA_{run_uid}", is_active=True)
        # Company B
        comp_b = Company(name=f"CompB_{run_uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        # Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-A-{run_uid}",
            owner_name=f"Owner A {run_uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_a_{run_uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-B-{run_uid}",
            owner_name=f"Owner B {run_uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_b_{run_uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # Projects in Company A
        # Project 1: Assigned to PM
        proj_assigned = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Project Assigned {run_uid}",
            business_id=f"PRJ-ASG-{run_uid}",
            status=ProjectStatus.ONGOING,
        )
        # Project 2: NOT assigned to PM
        proj_unassigned = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Project Unassigned {run_uid}",
            business_id=f"PRJ-UNASG-{run_uid}",
            status=ProjectStatus.ONGOING,
        )
        # Project 3 in Company B
        proj_comp_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Project CompB {run_uid}",
            business_id=f"PRJ-B-{run_uid}",
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_assigned, proj_unassigned, proj_comp_b])
        await db.flush()

        # Users in Company A
        admin_a = User(
            company_id=comp_a.id,
            email=f"admin_a_{run_uid}@test.com",
            full_name="Alice Admin",
            role=UserRole.ADMIN.value,
            hashed_password=pwd_hash,
            is_active=True,
            is_super_admin=False,
        )
        pm_a = User(
            company_id=comp_a.id,
            email=f"pm_a_{run_uid}@test.com",
            full_name="Peter ProjectManager",
            role=UserRole.PROJECT_MANAGER.value,
            hashed_password=pwd_hash,
            is_active=True,
            is_super_admin=False,
        )
        engineer_a = User(
            company_id=comp_a.id,
            email=f"eng_a_{run_uid}@test.com",
            full_name="Edward Engineer",
            role=UserRole.SITE_ENGINEER.value,
            hashed_password=pwd_hash,
            is_active=True,
            is_super_admin=False,
        )
        # User in Company B
        admin_b = User(
            company_id=comp_b.id,
            email=f"admin_b_{run_uid}@test.com",
            full_name="Bob AdminB",
            role=UserRole.ADMIN.value,
            hashed_password=pwd_hash,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([admin_a, pm_a, engineer_a, admin_b])
        await db.flush()

        # Grant dashboard.view to pm_a via UserPermissionOverride
        perm_dash = (await db.scalars(select(Permission).where(Permission.code == "dashboard.view"))).first()
        if perm_dash:
            override = UserPermissionOverride(user_id=pm_a.id, permission_id=perm_dash.id, is_granted=True)
            db.add(override)

        # Assign pm_a only to proj_assigned
        member = ProjectMember(project_id=proj_assigned.id, user_id=pm_a.id)
        db.add(member)
        await db.commit()

        context = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_assigned": proj_assigned,
            "proj_unassigned": proj_unassigned,
            "proj_comp_b": proj_comp_b,
            "admin_a": admin_a,
            "pm_a": pm_a,
            "engineer_a": engineer_a,
            "admin_b": admin_b,
        }

    try:
        yield context
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as clean_db:
            all_users = [admin_a.id, pm_a.id, engineer_a.id, admin_b.id]
            all_projs = [proj_assigned.id, proj_unassigned.id, proj_comp_b.id]

            # Delete approvals
            await clean_db.execute(delete(Approval).where(Approval.requested_by.in_(all_users)))
            # Delete drawings
            await clean_db.execute(delete(DrawingDocument).where(DrawingDocument.project_id.in_(all_projs)))
            # Delete expenses, materials, bills
            await clean_db.execute(delete(Expense).where(Expense.project_id.in_(all_projs)))
            await clean_db.execute(delete(Material).where(Material.project_id.in_(all_projs)))
            await clean_db.execute(delete(VendorBill).where(VendorBill.project_id.in_(all_projs)))
            # Delete permission overrides
            await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(all_users)))
            # Delete members, projects, users, owners, companies
            await clean_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_(all_projs)))
            await clean_db.execute(delete(Project).where(Project.id.in_(all_projs)))
            await clean_db.execute(delete(User).where(User.id.in_(all_users)))
            await clean_db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await clean_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await clean_db.commit()


# ==============================================================================
# ISSUE #3895 REGRESSION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_dashboard_drawing_approval_assigned_project(setup_test_context):
    """
    1. One pending drawing in PM's assigned project:
    Both Admin and PM dashboards report pending_approvals = 1.
    """
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        # Create drawing in assigned project
        drawing = DrawingDocument(
            project_id=ctx["proj_assigned"].id,
            drawing_name="Foundation Plan L0",
            file_url="uploads/drawings/l0.pdf",
            approval_status=DocumentStatus.UNDER_REVIEW,
        )
        db.add(drawing)
        await db.flush()

        approval = Approval(
            entity_type="drawing",
            entity_id=drawing.id,
            requested_by=ctx["engineer_a"].id,
            status="Pending",
            remarks="Review needed",
        )
        db.add(approval)
        await db.flush()
        drawing.approval_id = approval.id
        await db.commit()

    transport = ASGITransport(app=app)

    # 1. Check Admin dashboard
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/dashboard/admin")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["vitals"]["pending_approvals"] == 1

    # 2. Check PM Command Center
    app.dependency_overrides[get_current_user] = lambda: ctx["pm_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["pm_a"]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_cc = await client.get("/api/v1/dashboard/pm-command-center")
        assert res_cc.status_code == 200, res_cc.text
        data_cc = res_cc.json()
        assert data_cc["kpis"]["pending_reviews_count"] == 1

        # 3. Check PM Summary
        res_sum = await client.get("/api/v1/dashboard/project-manager-summary")
        assert res_sum.status_code == 200, res_sum.text
        data_sum = res_sum.json()
        assert data_sum["pending_approvals"] == 1


@pytest.mark.asyncio
async def test_pm_dashboard_drawing_approval_unassigned_project(setup_test_context):
    """
    2. A drawing in an unassigned project:
    Admin dashboard reports 1 pending approval, but PM dashboard reports 0.
    """
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        # Drawing in proj_unassigned (PM is NOT a member)
        drawing = DrawingDocument(
            project_id=ctx["proj_unassigned"].id,
            drawing_name="Unassigned Project Drawing",
            file_url="uploads/drawings/unassigned.pdf",
            approval_status=DocumentStatus.UNDER_REVIEW,
        )
        db.add(drawing)
        await db.flush()

        approval = Approval(
            entity_type="drawing",
            entity_id=drawing.id,
            requested_by=ctx["engineer_a"].id,
            status="Pending",
        )
        db.add(approval)
        await db.flush()
        drawing.approval_id = approval.id
        await db.commit()

    transport = ASGITransport(app=app)

    # Admin sees it (company wide)
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_admin = await client.get("/api/v1/dashboard/admin")
        assert res_admin.status_code == 200
        assert res_admin.json()["vitals"]["pending_approvals"] == 1

    # PM does NOT see it (unassigned project)
    app.dependency_overrides[get_current_user] = lambda: ctx["pm_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["pm_a"]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_cc = await client.get("/api/v1/dashboard/pm-command-center")
        assert res_cc.status_code == 200
        assert res_cc.json()["kpis"]["pending_reviews_count"] == 0

        res_sum = await client.get("/api/v1/dashboard/project-manager-summary")
        assert res_sum.status_code == 200
        assert res_sum.json()["pending_approvals"] == 0


@pytest.mark.asyncio
async def test_pm_dashboard_drawing_approval_cross_tenant_isolation(setup_test_context):
    """
    3. Drawing in Company B project does NOT appear in Company A dashboards.
    """
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        drawing_b = DrawingDocument(
            project_id=ctx["proj_comp_b"].id,
            drawing_name="Company B Drawing",
            file_url="uploads/drawings/comp_b.pdf",
            approval_status=DocumentStatus.UNDER_REVIEW,
        )
        db.add(drawing_b)
        await db.flush()

        approval_b = Approval(
            entity_type="drawing",
            entity_id=drawing_b.id,
            requested_by=ctx["admin_b"].id,
            status="Pending",
        )
        db.add(approval_b)
        await db.flush()
        drawing_b.approval_id = approval_b.id
        await db.commit()

    transport = ASGITransport(app=app)

    # Company A Admin: 0
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/dashboard/admin")
        assert res.json()["vitals"]["pending_approvals"] == 0

    # Company A PM: 0
    app.dependency_overrides[get_current_user] = lambda: ctx["pm_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["pm_a"]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_cc = await client.get("/api/v1/dashboard/pm-command-center")
        assert res_cc.json()["kpis"]["pending_reviews_count"] == 0

        res_sum = await client.get("/api/v1/dashboard/project-manager-summary")
        assert res_sum.json()["pending_approvals"] == 0


@pytest.mark.asyncio
async def test_pm_dashboard_drawing_non_pending_states_ignored(setup_test_context):
    """
    4. Approved and Rejected drawings do NOT count towards pending approvals.
    """
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        # Approved drawing
        d_app = DrawingDocument(
            project_id=ctx["proj_assigned"].id,
            drawing_name="Approved Drawing",
            file_url="uploads/drawings/app.pdf",
            approval_status=DocumentStatus.APPROVED,
        )
        # Rejected drawing
        d_rej = DrawingDocument(
            project_id=ctx["proj_assigned"].id,
            drawing_name="Rejected Drawing",
            file_url="uploads/drawings/rej.pdf",
            approval_status=DocumentStatus.REJECTED,
        )
        db.add_all([d_app, d_rej])
        await db.flush()

        a_app = Approval(
            entity_type="drawing",
            entity_id=d_app.id,
            requested_by=ctx["engineer_a"].id,
            approved_by=ctx["admin_a"].id,
            status="Approved",
        )
        a_rej = Approval(
            entity_type="drawing",
            entity_id=d_rej.id,
            requested_by=ctx["engineer_a"].id,
            approved_by=ctx["admin_a"].id,
            status="Rejected",
        )
        db.add_all([a_app, a_rej])
        await db.flush()
        d_app.approval_id = a_app.id
        d_rej.approval_id = a_rej.id
        await db.commit()

    transport = ASGITransport(app=app)
    app.dependency_overrides[get_current_user] = lambda: ctx["pm_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["pm_a"]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_cc = await client.get("/api/v1/dashboard/pm-command-center")
        assert res_cc.json()["kpis"]["pending_reviews_count"] == 0

        res_sum = await client.get("/api/v1/dashboard/project-manager-summary")
        assert res_sum.json()["pending_approvals"] == 0


@pytest.mark.asyncio
async def test_pm_dashboard_multi_entity_aggregations(setup_test_context):
    """
    5. Preserves existing expense, material, bill counting while adding drawing counts.
    1 drawing + 1 expense = 2 pending reviews.
    """
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        # Drawing
        drawing = DrawingDocument(
            project_id=ctx["proj_assigned"].id,
            drawing_name="HVAC Plan",
            file_url="uploads/drawings/hvac.pdf",
            approval_status=DocumentStatus.UNDER_REVIEW,
        )
        # Expense
        expense = Expense(
            project_id=ctx["proj_assigned"].id,
            amount=Decimal("5000.00"),
            category="Site",
            description="Site tools",
            expense_date=date.today(),
            payment_mode="Cash",
        )
        db.add_all([drawing, expense])
        await db.flush()

        app_drawing = Approval(
            entity_type="drawing",
            entity_id=drawing.id,
            requested_by=ctx["engineer_a"].id,
            status="Pending",
        )
        app_expense = Approval(
            entity_type="expense",
            entity_id=expense.id,
            requested_by=ctx["engineer_a"].id,
            status="Pending",
        )
        db.add_all([app_drawing, app_expense])
        await db.flush()
        drawing.approval_id = app_drawing.id
        await db.commit()

    transport = ASGITransport(app=app)
    app.dependency_overrides[get_current_user] = lambda: ctx["pm_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["pm_a"]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_cc = await client.get("/api/v1/dashboard/pm-command-center")
        assert res_cc.json()["kpis"]["pending_reviews_count"] == 2

        res_sum = await client.get("/api/v1/dashboard/project-manager-summary")
        assert res_sum.json()["pending_approvals"] == 2


# ==============================================================================
# ISSUE #3942 REGRESSION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_approval_list_and_history_display_user_names(setup_test_context):
    """
    Verifies that GET /api/v1/approvals and GET /api/v1/drawings/{id}/approval-history
    return requested_by_name and approved_by_name instead of only integer IDs.
    """
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        drawing = DrawingDocument(
            project_id=ctx["proj_assigned"].id,
            drawing_name="Elevation Drawing",
            file_url="uploads/drawings/elevation.pdf",
            approval_status=DocumentStatus.APPROVED,
        )
        db.add(drawing)
        await db.flush()

        # Approval approved by Alice Admin
        approval = Approval(
            entity_type="drawing",
            entity_id=drawing.id,
            requested_by=ctx["engineer_a"].id,
            approved_by=ctx["admin_a"].id,
            status="Approved",
            remarks="Elevation looks great",
        )
        db.add(approval)
        await db.flush()
        drawing.approval_id = approval.id
        await db.commit()

        drawing_id = drawing.id
        approval_id = approval.id

    transport = ASGITransport(app=app)
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /api/v1/approvals
        res = await client.get("/api/v1/approvals")
        assert res.status_code == 200, res.text
        approvals = res.json()
        target = next((a for a in approvals if a["id"] == approval_id), None)
        assert target is not None

        # Verify backward compatibility (integer IDs intact)
        assert target["requested_by"] == ctx["engineer_a"].id
        assert target["approved_by"] == ctx["admin_a"].id
        # Verify names are populated!
        assert target["requested_by_name"] == "Edward Engineer"
        assert target["approved_by_name"] == "Alice Admin"

        # 2. GET /api/v1/drawings/{id}/approval-history
        res_hist = await client.get(f"/api/v1/drawings/{drawing_id}/approval-history")
        assert res_hist.status_code == 200, res_hist.text
        hist_data = res_hist.json()
        assert len(hist_data) >= 1
        h_target = next((h for h in hist_data if h["id"] == approval_id), None)
        assert h_target is not None
        assert h_target["requested_by"] == ctx["engineer_a"].id
        assert h_target["requested_by_name"] == "Edward Engineer"
        assert h_target["approved_by"] == ctx["admin_a"].id
        assert h_target["approved_by_name"] == "Alice Admin"


@pytest.mark.asyncio
async def test_approval_list_pending_record_null_approver(setup_test_context):
    """
    Pending record with no approver has approved_by = None and approved_by_name = None.
    """
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        drawing = DrawingDocument(
            project_id=ctx["proj_assigned"].id,
            drawing_name="Plumbing Plan",
            file_url="uploads/drawings/plumb.pdf",
            approval_status=DocumentStatus.UNDER_REVIEW,
        )
        db.add(drawing)
        await db.flush()

        approval = Approval(
            entity_type="drawing",
            entity_id=drawing.id,
            requested_by=ctx["engineer_a"].id,
            status="Pending",
            remarks="Review needed",
        )
        db.add(approval)
        await db.commit()
        approval_id = approval.id

    transport = ASGITransport(app=app)
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/approvals")
        assert res.status_code == 200
        target = next((a for a in res.json() if a["id"] == approval_id), None)
        assert target is not None
        assert target["requested_by"] == ctx["engineer_a"].id
        assert target["requested_by_name"] == "Edward Engineer"
        assert target["approved_by"] is None
        assert target["approved_by_name"] is None


@pytest.mark.asyncio
async def test_approval_export_displays_user_names_in_csv(setup_test_context):
    """
    GET /api/v1/approvals/export returns CSV with actual user names in
    'Requested By' and 'Approved By' columns.
    """
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        drawing = DrawingDocument(
            project_id=ctx["proj_assigned"].id,
            drawing_name="Export Test Drawing",
            file_url="uploads/drawings/export.pdf",
            approval_status=DocumentStatus.APPROVED,
        )
        db.add(drawing)
        await db.flush()

        approval = Approval(
            entity_type="drawing",
            entity_id=drawing.id,
            requested_by=ctx["engineer_a"].id,
            approved_by=ctx["admin_a"].id,
            status="Approved",
            remarks="Export ready",
        )
        db.add(approval)
        await db.commit()
        approval_id = approval.id

    transport = ASGITransport(app=app)
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/approvals/export")
        assert res.status_code == 200, res.text
        assert "text/csv" in res.headers.get("content-type", "")

        csv_text = res.text
        reader = csv.reader(io.StringIO(csv_text))
        header = next(reader)
        assert header == [
            "Approval ID",
            "Entity Type",
            "Entity ID",
            "Status",
            "Requested By",
            "Approved By",
            "Remarks",
            "Created At",
        ]

        found_row = None
        for row in reader:
            if row[0] == str(approval_id):
                found_row = row
                break

        assert found_row is not None, f"Approval {approval_id} not found in export"
        # Requested By must be "Edward Engineer", NOT raw ID
        assert found_row[4] == "Edward Engineer"
        # Approved By must be "Alice Admin", NOT raw ID
        assert found_row[5] == "Alice Admin"


@pytest.mark.asyncio
async def test_approval_cross_tenant_isolation(setup_test_context):
    """
    Tenant A user cannot see approvals created by Tenant B in GET or export.
    """
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        approval_b = Approval(
            entity_type="boq",
            entity_id=9999,
            requested_by=ctx["admin_b"].id,
            status="Pending",
            remarks="Tenant B only",
        )
        db.add(approval_b)
        await db.commit()
        b_id = approval_b.id

    transport = ASGITransport(app=app)
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/approvals")
        assert res.status_code == 200
        ids = [a["id"] for a in res.json()]
        assert b_id not in ids

        res_exp = await client.get("/api/v1/approvals/export")
        assert res_exp.status_code == 200
        assert f"User #{ctx['admin_b'].id}" not in res_exp.text
        assert "Bob AdminB" not in res_exp.text
