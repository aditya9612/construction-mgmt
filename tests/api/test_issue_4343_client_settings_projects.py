import pytest
import pytest_asyncio
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.quotation import QuotationMaster
from app.models.user import User, UserRole
from app.models.rbac import Permission, UserPermissionOverride
from app.core.dependencies import get_current_user, get_current_active_user


@pytest_asyncio.fixture
async def setup_client_projects_context():
    uid = uuid.uuid4().hex[:6]
    rand_int = uuid.uuid4().int % 100000000

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_4343_{uid}", is_active=True)
        db.add(comp)
        await db.flush()

        admin = User(
            email=f"admin_4343_{uid}@test.com",
            mobile=f"90{rand_int:08d}",
            role=UserRole.ADMIN.value,
            is_active=True,
            company_id=comp.id,
        )
        client_a = User(
            email=f"clia_4343_{uid}@test.com",
            mobile=f"93{rand_int:08d}",
            role=UserRole.CLIENT.value,
            is_active=True,
            company_id=comp.id,
        )
        client_b = User(
            email=f"clib_4343_{uid}@test.com",
            mobile=f"94{rand_int:08d}",
            role=UserRole.CLIENT.value,
            is_active=True,
            company_id=comp.id,
        )
        client_c = User(
            email=f"clic_4343_{uid}@test.com",
            mobile=f"95{rand_int:08d}",
            role=UserRole.CLIENT.value,
            is_active=True,
            company_id=comp.id,
        )
        db.add_all([admin, client_a, client_b, client_c])
        await db.flush()

        # Grant projects.view permission
        p_view = await db.scalar(select(Permission).where(Permission.code == "projects.view"))
        if p_view:
            db.add_all([
                UserPermissionOverride(user_id=client_a.id, permission_id=p_view.id, is_granted=True),
                UserPermissionOverride(user_id=client_b.id, permission_id=p_view.id, is_granted=True),
                UserPermissionOverride(user_id=client_c.id, permission_id=p_view.id, is_granted=True),
                UserPermissionOverride(user_id=admin.id, permission_id=p_view.id, is_granted=True),
            ])

        owner_a = Owner(
            company_id=comp.id,
            owner_code=f"OA_{uid}",
            owner_name=f"OwnerA_{uid}",
            mobile=f"91{rand_int:08d}",
            email=f"ownera_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp.id,
            owner_code=f"OB_{uid}",
            owner_name=f"OwnerB_{uid}",
            mobile=f"92{rand_int:08d}",
            email=f"ownerb_{uid}@test.com",
        )
        # Owner D: shares mobile and email with Client A, but is NOT linked via ProjectMember or Quotation
        owner_d = Owner(
            company_id=comp.id,
            owner_code=f"OD_{uid}",
            owner_name=f"OwnerD_{uid}",
            mobile=client_a.mobile,
            email=client_a.email,
        )
        # Owner E: has NULL email
        owner_e = Owner(
            company_id=comp.id,
            owner_code=f"OE_{uid}",
            owner_name=f"OwnerE_{uid}",
            mobile=f"96{rand_int:08d}",
            email=None,
        )
        db.add_all([owner_a, owner_b, owner_d, owner_e])
        await db.flush()

        # Project A: Assigned to Client A via ProjectMember
        proj_a = Project(
            company_id=comp.id,
            owner_id=owner_a.id,
            project_name=f"ProjectA_{uid}",
            business_id=f"PA-4343-{uid}",
            status="Ongoing",
        )
        # Project B: Assigned to Client B via ProjectMember
        proj_b = Project(
            company_id=comp.id,
            owner_id=owner_b.id,
            project_name=f"ProjectB_{uid}",
            business_id=f"PB-4343-{uid}",
            status="Ongoing",
        )
        # Project C: Linked to Client C via QuotationMaster
        proj_c = Project(
            company_id=comp.id,
            owner_id=owner_a.id,
            project_name=f"ProjectC_{uid}",
            business_id=f"PC-4343-{uid}",
            status="Ongoing",
        )
        # Project D: Has Owner D (matches Client A contact info), but NO ProjectMember or Quotation
        proj_d = Project(
            company_id=comp.id,
            owner_id=owner_d.id,
            project_name=f"ProjectD_Unlinked_{uid}",
            business_id=f"PD-4343-{uid}",
            status="Ongoing",
        )
        # Project E: Has Owner E with NULL email
        proj_e = Project(
            company_id=comp.id,
            owner_id=owner_e.id,
            project_name=f"ProjectE_NullOwnerEmail_{uid}",
            business_id=f"PE-4343-{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b, proj_c, proj_d, proj_e])
        await db.flush()

        # ProjectMember links
        db.add(ProjectMember(project_id=proj_a.id, user_id=client_a.id))
        db.add(ProjectMember(project_id=proj_b.id, user_id=client_b.id))

        # QuotationMaster link for Project C -> Client C
        qm_c = QuotationMaster(
            quotation_no=f"Q-4343-{uid}",
            company_id=comp.id,
            project_id=proj_c.id,
            client_user_id=client_c.id,
            client_name="Client C",
            mobile_number=client_c.mobile,
            project_name=f"ProjectC_{uid}",
            project_type="Residential",
        )
        db.add(qm_c)
        await db.commit()

        p_a_id = proj_a.id
        p_b_id = proj_b.id
        p_c_id = proj_c.id
        p_d_id = proj_d.id
        p_e_id = proj_e.id

    return {
        "admin": admin,
        "client_a": client_a,
        "client_b": client_b,
        "client_c": client_c,
        "p_a_id": p_a_id,
        "p_b_id": p_b_id,
        "p_c_id": p_c_id,
        "p_d_id": p_d_id,
        "p_e_id": p_e_id,
    }


@pytest.mark.asyncio
async def test_client_projects_dropdown_scoping(setup_client_projects_context):
    """
    Issue #4343: The project dropdown should display only those projects that are canonically assigned
    to the logged-in client (via ProjectMember or QuotationMaster).
    Unassigned projects, projects belonging to other clients, and projects with contact-field matches
    without canonical membership must NOT be visible.
    """
    ctx = setup_client_projects_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["client_a"]
    app.dependency_overrides[get_current_user] = lambda: ctx["client_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/projects")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        items = res.json().get("items", [])
        project_ids = [p["id"] for p in items]

        # Client A must see Project A (assigned via ProjectMember)
        assert ctx["p_a_id"] in project_ids, "Assigned project A must be in projects list"

        # Client A must NOT see Project B (assigned to Client B)
        assert ctx["p_b_id"] not in project_ids, "Project B must NOT be visible to client A"

        # Client A must NOT see Project C (assigned to Client C)
        assert ctx["p_c_id"] not in project_ids, "Project C must NOT be visible to client A"

        # Client A must NOT see Project D (Owner contact info similarity alone must NOT grant access)
        assert ctx["p_d_id"] not in project_ids, "Project D (contact similarity only) must NOT be visible to client A"

        # Client A must NOT see Project E (Owner email is NULL)
        assert ctx["p_e_id"] not in project_ids, "Project E (NULL owner email) must NOT be visible to client A"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_client_quotation_linkage_scoping(setup_client_projects_context):
    """
    Issue #4343: Client C linked via QuotationMaster.client_user_id sees Project C.
    """
    ctx = setup_client_projects_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["client_c"]
    app.dependency_overrides[get_current_user] = lambda: ctx["client_c"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/projects")
        assert res.status_code == 200
        items = res.json().get("items", [])
        project_ids = [p["id"] for p in items]

        assert ctx["p_c_id"] in project_ids, "Quotation-linked Project C must be visible to Client C"
        assert ctx["p_a_id"] not in project_ids, "Project A must NOT be visible to Client C"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_client_settings_reject_unassigned_project(setup_client_projects_context):
    """
    Issue #4343: A client attempting to set default_project_id to an unassigned project
    (or a project with only contact similarity) must be rejected with 400.
    """
    ctx = setup_client_projects_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["client_a"]
    app.dependency_overrides[get_current_user] = lambda: ctx["client_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Attempt to set unassigned project B as default -> 400
        res_fail_b = await client.put("/api/v1/settings", json={"default_project_id": ctx["p_b_id"]})
        assert res_fail_b.status_code == 400
        assert "not assigned" in res_fail_b.json().get("detail", "")

        # Attempt to set project D (unassigned, only contact similarity) as default -> 400
        res_fail_d = await client.put("/api/v1/settings", json={"default_project_id": ctx["p_d_id"]})
        assert res_fail_d.status_code == 400
        assert "not assigned" in res_fail_d.json().get("detail", "")

        # Setting assigned project A succeeds -> 200
        res_ok = await client.put("/api/v1/settings", json={"default_project_id": ctx["p_a_id"]})
        assert res_ok.status_code == 200
        assert res_ok.json().get("default_project_id") == ctx["p_a_id"]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_sees_all_company_projects(setup_client_projects_context):
    """
    Admin retains full visibility across all company projects.
    """
    ctx = setup_client_projects_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin"]
    app.dependency_overrides[get_current_user] = lambda: ctx["admin"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/projects")
        assert res.status_code == 200
        items = res.json().get("items", [])
        project_ids = [p["id"] for p in items]

        assert ctx["p_a_id"] in project_ids
        assert ctx["p_b_id"] in project_ids
        assert ctx["p_c_id"] in project_ids
        assert ctx["p_d_id"] in project_ids
        assert ctx["p_e_id"] in project_ids

    app.dependency_overrides.clear()
