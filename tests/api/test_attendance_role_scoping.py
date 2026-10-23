import pytest
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole, UserAttendance
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus, ProjectMember
from app.models.master_data import LabourType
from app.models.labour import Labour, LabourProject, LabourStatus
from app.models.rbac import Role, Permission, RolePermission
from app.core.enums import SkillType, AttendanceStatus


def get_auth_headers(user: User):
    token = create_access_token(
        data={"sub": str(user.id), "role": user.role, "company_id": user.company_id}
    )
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def setup_scoping_test_data():
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]

        # 1. Company A & B
        comp_a = Company(name=f"Comp A {uid}", is_active=True)
        comp_b = Company(name=f"Comp B {uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Owners
        owner_a = Owner(owner_name=f"Owner A {uid}", owner_code=f"OA-{uid}", mobile=f"91{uid[:8]}", email=f"own_a_{uid}@test.com", company_id=comp_a.id)
        owner_b = Owner(owner_name=f"Owner B {uid}", owner_code=f"OB-{uid}", mobile=f"92{uid[:8]}", email=f"own_b_{uid}@test.com", company_id=comp_b.id)
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 3. Projects
        proj_a = Project(
            business_id=f"PRJ-A-{uid}",
            company_id=comp_a.id,
            project_name=f"Project A {uid}",
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b = Project(
            business_id=f"PRJ-B-{uid}",
            company_id=comp_b.id,
            project_name=f"Project B {uid}",
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 4. Users
        user_admin = User(
            email=f"admin_{uid}@test.com",
            full_name=f"Admin {uid}",
            role=UserRole.ADMIN.value,
            is_active=True,
            company_id=comp_a.id,
        )
        user_pm = User(
            email=f"pm_{uid}@test.com",
            full_name=f"PM {uid}",
            role=UserRole.PROJECT_MANAGER.value,
            is_active=True,
            company_id=comp_a.id,
        )
        user_labour1 = User(
            email=f"labour1_{uid}@test.com",
            full_name=f"Labour One {uid}",
            role=UserRole.LABOUR.value,
            is_active=True,
            company_id=comp_a.id,
        )
        user_labour2 = User(
            email=f"labour2_{uid}@test.com",
            full_name=f"Labour Two {uid}",
            role=UserRole.LABOUR.value,
            is_active=True,
            company_id=comp_a.id,
        )
        db.add_all([user_admin, user_pm, user_labour1, user_labour2])
        await db.flush()

        # 5. Project Memberships
        pm_member = ProjectMember(project_id=proj_a.id, user_id=user_pm.id)
        l1_member = ProjectMember(project_id=proj_a.id, user_id=user_labour1.id)
        l2_member = ProjectMember(project_id=proj_a.id, user_id=user_labour2.id)
        db.add_all([pm_member, l1_member, l2_member])

        # 6. Labour Profiles & LabourProject mappings
        lt = await db.scalar(select(LabourType))
        if not lt:
            lt = LabourType(name="General Worker", skill_category=SkillType.SKILLED, default_wage=500)
            db.add(lt)
            await db.flush()

        labour1_profile = Labour(
            company_id=comp_a.id,
            worker_code=f"LAB1-{uid}",
            user_id=user_labour1.id,
            labour_name=f"Labour One {uid}",
            labour_type_id=lt.id,
            status=LabourStatus.ACTIVE,
        )
        labour2_profile = Labour(
            company_id=comp_a.id,
            worker_code=f"LAB2-{uid}",
            user_id=user_labour2.id,
            labour_name=f"Labour Two {uid}",
            labour_type_id=lt.id,
            status=LabourStatus.ACTIVE,
        )
        db.add_all([labour1_profile, labour2_profile])
        await db.flush()

        lp1 = LabourProject(labour_id=labour1_profile.id, project_id=proj_a.id)
        lp2 = LabourProject(labour_id=labour2_profile.id, project_id=proj_a.id)
        db.add_all([lp1, lp2])

        # 7. Roles & Permissions (grant attendance.view to ProjectManager and Labour roles in Company A)
        p_att_view = await db.scalar(select(Permission).where(Permission.code == "attendance.view"))
        if not p_att_view:
            p_att_view = Permission(code="attendance.view", module="attendance", name="View Attendance")
            db.add(p_att_view)
            await db.flush()

        role_pm = Role(name=UserRole.PROJECT_MANAGER.value, display_name="Project Manager", company_id=comp_a.id, is_system=False)
        role_labour = Role(name=UserRole.LABOUR.value, display_name="Labour", company_id=comp_a.id, is_system=False)
        db.add_all([role_pm, role_labour])
        await db.flush()

        rp_pm = RolePermission(role=UserRole.PROJECT_MANAGER.value, role_id=role_pm.id, permission_id=p_att_view.id)
        rp_labour = RolePermission(role=UserRole.LABOUR.value, role_id=role_labour.id, permission_id=p_att_view.id)
        db.add_all([rp_pm, rp_labour])

        # 8. Create attendance records for today
        today = date.today()
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        att_pm = UserAttendance(
            user_id=user_pm.id,
            project_id=proj_a.id,
            attendance_date=today,
            status=AttendanceStatus.PRESENT.value,
            in_time=now_utc,
            working_hours=8.0,
            is_approved=True,
        )
        att_l1 = UserAttendance(
            user_id=user_labour1.id,
            project_id=proj_a.id,
            attendance_date=today,
            status=AttendanceStatus.PRESENT.value,
            in_time=now_utc,
            working_hours=7.0,
            is_approved=True,
        )
        att_l2 = UserAttendance(
            user_id=user_labour2.id,
            project_id=proj_a.id,
            attendance_date=today,
            status=AttendanceStatus.PRESENT.value,
            in_time=now_utc,
            working_hours=6.0,
            is_approved=True,
        )
        db.add_all([att_pm, att_l1, att_l2])
        await db.commit()

        context = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "user_admin": user_admin,
            "user_pm": user_pm,
            "user_labour1": user_labour1,
            "user_labour2": user_labour2,
            "labour1_profile": labour1_profile,
            "labour2_profile": labour2_profile,
            "att_pm": att_pm,
            "att_l1": att_l1,
            "att_l2": att_l2,
            "today": today,
        }

    try:
        yield context
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(UserAttendance).where(UserAttendance.user_id.in_([
                user_pm.id, user_labour1.id, user_labour2.id, user_admin.id
            ])))
            await db.execute(delete(RolePermission).where(RolePermission.role_id.in_([role_pm.id, role_labour.id])))
            await db.execute(delete(Role).where(Role.id.in_([role_pm.id, role_labour.id])))
            await db.execute(delete(LabourProject).where(LabourProject.labour_id.in_([labour1_profile.id, labour2_profile.id])))
            await db.execute(delete(Labour).where(Labour.id.in_([labour1_profile.id, labour2_profile.id])))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(User).where(User.id.in_([user_admin.id, user_pm.id, user_labour1.id, user_labour2.id])))
            await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()


@pytest.mark.asyncio
async def test_pm_attendance_not_exposed_to_labour():
    """
    Requirement C.1: Verify a PM's own attendance record is not exposed to an unrelated Labour user
    when retrieving the attendance list.
    """
    async with setup_scoping_test_data() as data:
        headers_l1 = get_auth_headers(data["user_labour1"])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/attendance/list", headers=headers_l1)
            assert resp.status_code == 200, resp.text
            records = resp.json()["data"]

            user_ids = [r["user_id"] for r in records]
            # Only Labour 1's records must appear
            assert data["user_pm"].id not in user_ids, "PM attendance record leaked to Labour user!"
            assert data["user_labour2"].id not in user_ids, "Labour 2 attendance leaked to Labour 1!"
            assert data["user_labour1"].id in user_ids


@pytest.mark.asyncio
async def test_labour_cannot_retrieve_other_user_records():
    """
    Requirement C.2: Verify a Labour user cannot retrieve another user's records by passing another user_id.
    Must return HTTP 403 Forbidden.
    """
    async with setup_scoping_test_data() as data:
        headers_l1 = get_auth_headers(data["user_labour1"])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Attempt to view PM's attendance
            resp_pm = await ac.get(f"/api/v1/attendance/list?user_id={data['user_pm'].id}", headers=headers_l1)
            assert resp_pm.status_code == 403
            assert "Not authorized" in resp_pm.json()["detail"]

            # Attempt to view another labourer's attendance
            resp_l2 = await ac.get(f"/api/v1/attendance/list?user_id={data['user_labour2'].id}", headers=headers_l1)
            assert resp_l2.status_code == 403
            assert "Not authorized" in resp_l2.json()["detail"]


@pytest.mark.asyncio
async def test_labour_retrieves_own_permitted_records():
    """
    Requirement C.3: Verify the Labour user can retrieve their own permitted attendance records.
    """
    async with setup_scoping_test_data() as data:
        headers_l1 = get_auth_headers(data["user_labour1"])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Explicit user_id == own user_id
            resp = await ac.get(f"/api/v1/attendance/list?user_id={data['user_labour1'].id}", headers=headers_l1)
            assert resp.status_code == 200
            records = resp.json()["data"]
            assert len(records) >= 1
            for r in records:
                assert r["user_id"] == data["user_labour1"].id


@pytest.mark.asyncio
async def test_pm_and_admin_access_preserved():
    """
    Requirement C.4: Verify PM/Admin access remains consistent with documented authorization rules.
    PM can view all attendance on their project. Admin can view company attendance.
    """
    async with setup_scoping_test_data() as data:
        headers_pm = get_auth_headers(data["user_pm"])
        headers_admin = get_auth_headers(data["user_admin"])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. PM lists project attendance
            resp_pm = await ac.get(f"/api/v1/attendance/list?project_id={data['proj_a'].id}", headers=headers_pm)
            assert resp_pm.status_code == 200
            user_ids_pm = [r["user_id"] for r in resp_pm.json()["data"]]
            assert data["user_pm"].id in user_ids_pm
            assert data["user_labour1"].id in user_ids_pm
            assert data["user_labour2"].id in user_ids_pm

            # 2. PM filters by specific labourer
            resp_pm_filtered = await ac.get(
                f"/api/v1/attendance/list?project_id={data['proj_a'].id}&user_id={data['user_labour1'].id}",
                headers=headers_pm,
            )
            assert resp_pm_filtered.status_code == 200
            user_ids_filtered = [r["user_id"] for r in resp_pm_filtered.json()["data"]]
            assert user_ids_filtered == [data["user_labour1"].id]

            # 3. Admin lists all company attendance
            resp_admin = await ac.get("/api/v1/attendance/list", headers=headers_admin)
            assert resp_admin.status_code == 200
            user_ids_admin = [r["user_id"] for r in resp_admin.json()["data"]]
            assert data["user_pm"].id in user_ids_admin
            assert data["user_labour1"].id in user_ids_admin
            assert data["user_labour2"].id in user_ids_admin


@pytest.mark.asyncio
async def test_labour_dashboard_counts_exclude_pm():
    """
    Requirement C.5: Verify Labour dashboard counts do not include PM attendance as Labour attendance.
    """
    async with setup_scoping_test_data() as data:
        headers_pm = get_auth_headers(data["user_pm"])
        headers_l1 = get_auth_headers(data["user_labour1"])
        today_str = data["today"].isoformat()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # PM views project labour attendance dashboard
            resp_dash = await ac.get(
                f"/api/v1/labour/attendance/dashboard?project_id={data['proj_a'].id}&from_date={today_str}&to_date={today_str}",
                headers=headers_pm,
            )
            assert resp_dash.status_code == 200
            dash_data = resp_dash.json()

            # PM is checked in on Project A (8 hrs), Labour 1 (7 hrs), Labour 2 (6 hrs).
            # Total labour count must only be 2 (Labour 1 and 2), NOT 3!
            assert dash_data["total_labour"] == 2
            # Present count must only include Labour 1 and 2, NOT PM!
            assert dash_data["present"] == 2

            # Labour 1 views their own dashboard
            resp_l1 = await ac.get(
                f"/api/v1/labour/attendance/dashboard?project_id={data['proj_a'].id}&from_date={today_str}&to_date={today_str}",
                headers=headers_l1,
            )
            assert resp_l1.status_code == 200
            l1_data = resp_l1.json()
            assert l1_data["total_labour"] == 1
            assert l1_data["present"] == 1

            # Labour 1 attempts to view Labour 2's dashboard
            resp_cross = await ac.get(
                f"/api/v1/labour/attendance/dashboard?project_id={data['proj_a'].id}&from_date={today_str}&to_date={today_str}&labour_id={data['labour2_profile'].id}",
                headers=headers_l1,
            )
            assert resp_cross.status_code == 403


@pytest.mark.asyncio
async def test_project_and_tenant_boundaries_enforced():
    """
    Requirement C.6: Verify Project/site and tenant boundaries remain enforced where applicable.
    """
    async with setup_scoping_test_data() as data:
        headers_l1 = get_auth_headers(data["user_labour1"])
        headers_pm = get_auth_headers(data["user_pm"])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Cross-tenant project access in list: Labour 1 accessing Project B (different company)
            resp_cross_tenant = await ac.get(f"/api/v1/attendance/list?project_id={data['proj_b'].id}", headers=headers_l1)
            assert resp_cross_tenant.status_code == 403

            # Cross-tenant project access: PM accessing Project B
            resp_pm_cross = await ac.get(f"/api/v1/attendance/list?project_id={data['proj_b'].id}", headers=headers_pm)
            assert resp_pm_cross.status_code == 403
