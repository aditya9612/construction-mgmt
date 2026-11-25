import io
import uuid
import pytest
import pandas as pd
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_permission
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember, ProjectStatus
from app.models.master_data import LabourType
from app.models.labour import Labour, LabourProject
from app.core.enums import SkillType, LabourStatus


def override_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    for p in ["labour.create", "labour.view", "labour.edit", "labour.delete", "labour.export"]:
        app.dependency_overrides[require_permission(p)] = lambda: user


def clear_user_override():
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_overrides():
    yield
    clear_user_override()


@pytest.mark.asyncio
async def test_issue_4768_excel_export_serial_number_column():
    """
    Issue #4768: Labour Registry Excel Export must contain a dedicated 'Sr. No.' column
    immediately before the 'Name' column, with sequential 1-based numbering based on the
    exported dataset, while preserving existing columns ('Name', 'Skill', 'Wage') and data.
    """
    uid = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        c = await db.get(Company, 1)
        if not c:
            c = Company(id=1, name="Test Company 1", is_active=True)
            db.add(c)
            await db.flush()

        owner = Owner(
            owner_name=f"Owner 4768 {uid}",
            owner_code=f"O4768-{uid}",
            mobile=f"93{uid[:8]}",
            email=f"own_4768_{uid}@test.com",
            company_id=1,
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            business_id=f"PRJ-4768-{uid}",
            company_id=1,
            project_name=f"Project 4768 {uid}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(proj)
        await db.flush()

        lt = LabourType(
            company_id=1,
            name=f"Skilled Mason {uid}",
            skill_category=SkillType.SKILLED,
            default_daily_wage=750,
        )
        db.add(lt)
        await db.flush()

        # Create three active labours and assign to the project
        labours = []
        for i in range(1, 4):
            l = Labour(
                company_id=1,
                worker_code=f"LAB-4768-{uid}-{i}",
                labour_name=f"Worker {i}",
                labour_type_id=lt.id,
                status=LabourStatus.ACTIVE,
            )
            db.add(l)
            await db.flush()
            labours.append(l)
            db.add(LabourProject(labour_id=l.id, project_id=proj.id))

        await db.commit()
        project_id = proj.id

    admin = User(
        id=9201,
        email=f"admin_4768_{uid}@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    override_user(admin)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/labour/report/export?project_id={project_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert "attachment; filename=labour.xlsx" in resp.headers["content-disposition"]

        # Parse Excel output
        df = pd.read_excel(io.BytesIO(resp.content))
        cols = list(df.columns)

        # 1. 'Sr. No.' exists
        assert "Sr. No." in cols, f"'Sr. No.' column missing from Excel export. Columns: {cols}"

        # 2. 'Sr. No.' appears immediately before 'Name'
        sr_idx = cols.index("Sr. No.")
        name_idx = cols.index("Name")
        assert sr_idx + 1 == name_idx, f"'Sr. No.' (idx {sr_idx}) must precede 'Name' (idx {name_idx})"
        assert cols[0] == "Sr. No.", f"First column must be 'Sr. No.', got {cols[0]}"
        assert cols[1] == "Name", f"Second column must be 'Name', got {cols[1]}"

        # 3. Existing columns 'Skill' and 'Wage' are preserved
        assert "Skill" in cols
        assert "Wage" in cols

        # 4. Sequential 1-based numbering
        sr_values = list(df["Sr. No."])
        assert sr_values == [1, 2, 3], f"Expected sequential [1, 2, 3], got {sr_values}"
        assert len(df) == 3


@pytest.mark.asyncio
async def test_issue_4710_labour_project_id_persistence_and_exposure():
    """
    Issue #4710: Labour record must return the correct project_id after registration,
    and subsequent GET /api/v1/labour/{id} and global GET /api/v1/labour API responses
    must return the correct project_id instead of null.
    """
    uid = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        c = await db.get(Company, 1)
        if not c:
            c = Company(id=1, name="Test Company 1", is_active=True)
            db.add(c)
            await db.flush()

        owner = Owner(
            owner_name=f"Owner 4710 {uid}",
            owner_code=f"O4710-{uid}",
            mobile=f"94{uid[:8]}",
            email=f"own_4710_{uid}@test.com",
            company_id=1,
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            business_id=f"PRJ-4710-{uid}",
            company_id=1,
            project_name=f"Project 4710 {uid}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(proj)
        await db.flush()

        lt = LabourType(
            company_id=1,
            name=f"Painter 4710 {uid}",
            skill_category=SkillType.SEMI_SKILLED,
            default_daily_wage=550,
        )
        db.add(lt)
        await db.flush()

        admin = User(
            email=f"admin_4710_{uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
            company_id=1,
        )
        db.add(admin)
        await db.flush()
        await db.commit()

        project_id = proj.id
        lt_id = lt.id

    override_user(admin)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        rand_mobile = f"9{uuid.uuid4().int % 1000000000:09d}"
        rand_aadhaar = f"6{uuid.uuid4().int % 100000000000:011d}"

        # 1. Register labour with project_id
        create_resp = await ac.post(
            f"/api/v1/labour?aadhaar_number={rand_aadhaar}&labour_name=Assigned%20Painter&mobile_number={rand_mobile}&labour_type_id={lt_id}&project_id={project_id}&status=Active"
        )
        assert create_resp.status_code == 200, create_resp.text
        create_data = create_resp.json()
        labour_id = create_data["id"]

        # 2. Verify POST response returns correct project_id (not null)
        assert create_data.get("project_id") == project_id, f"Expected project_id {project_id}, got {create_data.get('project_id')}"

        # 3. Verify LabourProject was persisted in database
        async with AsyncSessionLocal() as db:
            lp = await db.scalar(
                select(LabourProject).where(
                    LabourProject.labour_id == labour_id,
                    LabourProject.project_id == project_id,
                )
            )
            assert lp is not None, "LabourProject mapping record was not persisted in database"

        # 4. Verify GET /api/v1/labour/{id} returns correct project_id
        get_resp = await ac.get(f"/api/v1/labour/{labour_id}")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data.get("project_id") == project_id, f"GET /api/v1/labour/{labour_id} returned project_id {get_data.get('project_id')}"

        # 5. Verify GET /api/v1/labour (global list without project_id filter) exposes project_id
        list_resp = await ac.get("/api/v1/labour?limit=50&offset=0")
        assert list_resp.status_code == 200
        list_data = list_resp.json()
        matched = next((item for item in list_data["items"] if item["id"] == labour_id), None)
        assert matched is not None, "Created labour not found in global labour list"
        assert matched.get("project_id") == project_id, f"Global list item returned project_id {matched.get('project_id')}"

        # 6. Verify labour appears in the assigned project's Labour List
        proj_list_resp = await ac.get(f"/api/v1/labour?project_id={project_id}")
        assert proj_list_resp.status_code == 200
        proj_list_data = proj_list_resp.json()
        proj_matched = next((item for item in proj_list_data["items"] if item["id"] == labour_id), None)
        assert proj_matched is not None, "Assigned labour missing from project's Labour List"
        assert proj_matched.get("project_id") == project_id

        # 7. Invalid/unauthorized project assignment is rejected with 404
        unauth_mobile = f"9{uuid.uuid4().int % 1000000000:09d}"
        unauth_aadhaar = f"5{uuid.uuid4().int % 100000000000:011d}"
        fail_resp = await ac.post(
            f"/api/v1/labour?aadhaar_number={unauth_aadhaar}&labour_name=Ghost%20Worker&mobile_number={unauth_mobile}&labour_type_id={lt_id}&project_id=999999&status=Active"
        )
        assert fail_resp.status_code == 404


@pytest.mark.asyncio
async def test_issue_4765_assigned_to_project_filter():
    """
    Issue #4765: Assigned to Project Filter must display all personnel records assigned
    to the selected project, and exclude personnel assigned to other projects or unassigned.
    Tenant/company boundaries and pagination must be preserved.
    """
    uid = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        c1 = await db.get(Company, 1)
        if not c1:
            c1 = Company(id=1, name="Company 1", is_active=True)
            db.add(c1)
            await db.flush()

        c2 = await db.get(Company, 2)
        if not c2:
            c2 = Company(id=2, name="Company 2", is_active=True)
            db.add(c2)
            await db.flush()

        owner1 = Owner(
            owner_name=f"Owner 4765 {uid}",
            owner_code=f"O4765-{uid}",
            mobile=f"95{uid[:8]}",
            email=f"own_4765_{uid}@test.com",
            company_id=1,
        )
        db.add(owner1)
        await db.flush()

        # Company 1 Projects
        proj_a = Project(
            business_id=f"PRJ-4765-A-{uid}",
            company_id=1,
            project_name=f"Project Alpha 4765 {uid}",
            owner_id=owner1.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b = Project(
            business_id=f"PRJ-4765-B-{uid}",
            company_id=1,
            project_name=f"Project Beta 4765 {uid}",
            owner_id=owner1.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Company 2 Project (Tenant Boundary)
        owner2 = Owner(
            owner_name=f"Owner C2 {uid}",
            owner_code=f"OC2-{uid}",
            mobile=f"96{uid[:8]}",
            email=f"own_c2_{uid}@test.com",
            company_id=2,
        )
        db.add(owner2)
        await db.flush()

        proj_c2 = Project(
            business_id=f"PRJ-4765-C2-{uid}",
            company_id=2,
            project_name=f"Project C2 4765 {uid}",
            owner_id=owner2.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(proj_c2)
        await db.flush()

        lt1 = LabourType(
            company_id=1,
            name=f"General Labour {uid}",
            skill_category=SkillType.UNSKILLED,
            default_daily_wage=400,
        )
        lt2 = LabourType(
            company_id=2,
            name=f"General Labour C2 {uid}",
            skill_category=SkillType.UNSKILLED,
            default_daily_wage=400,
        )
        db.add_all([lt1, lt2])
        await db.flush()

        # Labour 1: Assigned to Project A
        lab_a = Labour(
            company_id=1,
            worker_code=f"LAB-4765-A-{uid}",
            labour_name="Worker Alpha",
            labour_type_id=lt1.id,
            status=LabourStatus.ACTIVE,
        )
        # Labour 2: Assigned to Project B
        lab_b = Labour(
            company_id=1,
            worker_code=f"LAB-4765-B-{uid}",
            labour_name="Worker Beta",
            labour_type_id=lt1.id,
            status=LabourStatus.ACTIVE,
        )
        # Labour 3: Unassigned (no project)
        lab_unassigned = Labour(
            company_id=1,
            worker_code=f"LAB-4765-U-{uid}",
            labour_name="Worker Unassigned",
            labour_type_id=lt1.id,
            status=LabourStatus.ACTIVE,
        )
        # Labour 4: Belongs to Company 2, assigned to Project C2
        lab_c2 = Labour(
            company_id=2,
            worker_code=f"LAB-4765-C2-{uid}",
            labour_name="Worker Company 2",
            labour_type_id=lt2.id,
            status=LabourStatus.ACTIVE,
        )
        db.add_all([lab_a, lab_b, lab_unassigned, lab_c2])
        await db.flush()

        db.add(LabourProject(labour_id=lab_a.id, project_id=proj_a.id))
        db.add(LabourProject(labour_id=lab_b.id, project_id=proj_b.id))
        db.add(LabourProject(labour_id=lab_c2.id, project_id=proj_c2.id))
        await db.commit()

        proj_a_id = proj_a.id
        proj_b_id = proj_b.id
        proj_c2_id = proj_c2.id
        lab_a_id = lab_a.id
        lab_b_id = lab_b.id
        lab_u_id = lab_unassigned.id
        lab_c2_id = lab_c2.id

    admin_c1 = User(
        id=9203,
        email=f"admin_c1_{uid}@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )
    override_user(admin_c1)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Filter by Project A:
        # Must return Labour A. Must EXCLUDE Labour B, Labour Unassigned, and Labour C2.
        resp_a = await ac.get(f"/api/v1/labour?project_id={proj_a_id}")
        assert resp_a.status_code == 200
        data_a = resp_a.json()
        items_a = data_a["items"]
        item_ids_a = [item["id"] for item in items_a]

        assert lab_a_id in item_ids_a, "Labour Alpha missing from Project Alpha filter"
        assert lab_b_id not in item_ids_a, "Labour Beta leaked into Project Alpha filter"
        assert lab_u_id not in item_ids_a, "Unassigned labour leaked into Project Alpha filter"
        assert lab_c2_id not in item_ids_a, "Company 2 labour leaked into Project Alpha filter"

        matched_a = next(i for i in items_a if i["id"] == lab_a_id)
        assert matched_a["project_id"] == proj_a_id

        # 2. Filter by Project B:
        # Must return Labour B. Must EXCLUDE Labour A, Labour Unassigned, and Labour C2.
        resp_b = await ac.get(f"/api/v1/labour?project_id={proj_b_id}")
        assert resp_b.status_code == 200
        data_b = resp_b.json()
        items_b = data_b["items"]
        item_ids_b = [item["id"] for item in items_b]

        assert lab_b_id in item_ids_b, "Labour Beta missing from Project Beta filter"
        assert lab_a_id not in item_ids_b, "Labour Alpha leaked into Project Beta filter"
        assert lab_u_id not in item_ids_b, "Unassigned labour leaked into Project Beta filter"
        assert lab_c2_id not in item_ids_b, "Company 2 labour leaked into Project Beta filter"

        matched_b = next(i for i in items_b if i["id"] == lab_b_id)
        assert matched_b["project_id"] == proj_b_id

        # 3. Global listing (no filter) returns Company 1 labours with their respective project_ids:
        resp_global = await ac.get("/api/v1/labour?limit=50&offset=0")
        assert resp_global.status_code == 200
        global_items = resp_global.json()["items"]
        global_by_id = {i["id"]: i for i in global_items}

        assert lab_a_id in global_by_id
        assert global_by_id[lab_a_id]["project_id"] == proj_a_id
        assert lab_b_id in global_by_id
        assert global_by_id[lab_b_id]["project_id"] == proj_b_id
        assert lab_u_id in global_by_id
        assert global_by_id[lab_u_id]["project_id"] is None
        assert lab_c2_id not in global_by_id, "Company 2 labour leaked into Company 1 global list"

        # 4. Tenant isolation: Company 1 user attempting to filter by Company 2 project -> 404
        cross_tenant_resp = await ac.get(f"/api/v1/labour?project_id={proj_c2_id}")
        assert cross_tenant_resp.status_code == 404

        # 5. Pagination check with project filter
        page_resp = await ac.get(f"/api/v1/labour?project_id={proj_a_id}&limit=1&offset=0")
        assert page_resp.status_code == 200
        page_data = page_resp.json()
        assert len(page_data["items"]) == 1
        assert page_data["meta"]["limit"] == 1
        assert page_data["meta"]["offset"] == 0
