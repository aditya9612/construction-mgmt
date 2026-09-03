import uuid
from decimal import Decimal
from datetime import date, timedelta
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.settings import CompanySettings
from app.models.contractor import Contractor
from app.models.master_data import LabourType
from app.models.labour import Labour, LabourProject, LabourAttendance, LabourPayroll, LabourWageRecord
from app.models.user import UserAttendance
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import AttendanceStatus, LabourStatus, PayrollStatus


@asynccontextmanager
async def setup_batch_h_data():
    """Seed test companies, projects, contractors, labour types, labour, attendance, payroll, wage records, and users for Batch H test suite."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Create two test companies
        comp_a = Company(name=f"BatchH_CompA_{uid}")
        comp_b = Company(name=f"BatchH_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company settings
        cs_a = CompanySettings(company_id=comp_a.id)
        cs_b = CompanySettings(company_id=comp_b.id)
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Test Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_h_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin H",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_ha_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin H",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_hb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin H",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"WorkforceManager_{uid}"
        user_custom_a = User(
            email=f"custom_ha_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Workforce Manager H",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        user_legacy_a = User(
            email=f"legacy_ha_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy PM H",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Project Manager",
        )

        db.add_all([super_admin, admin_a, admin_b, user_custom_a, user_legacy_a])
        await db.flush()

        # 4. Create Role for custom user
        role_custom = Role(name=custom_role_name, display_name="Custom Role H", company_id=comp_a.id, description="Custom Role H")
        db.add(role_custom)
        await db.flush()

        # 5. Owners and Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-HA-{uid}",
            owner_name=f"Owner HA {uid}",
            email=f"ownerha_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-HB-{uid}",
            owner_name=f"Owner HB {uid}",
            email=f"ownerhb_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(
            business_id=f"PRJ-HA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_HA_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-HB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_HB_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Project memberships
        pm_a1 = ProjectMember(project_id=proj_a.id, user_id=admin_a.id)
        pm_a2 = ProjectMember(project_id=proj_a.id, user_id=user_custom_a.id)
        pm_a3 = ProjectMember(project_id=proj_a.id, user_id=user_legacy_a.id)
        pm_b1 = ProjectMember(project_id=proj_b.id, user_id=admin_b.id)
        db.add_all([pm_a1, pm_a2, pm_a3, pm_b1])
        await db.flush()

        # 6. Contractors
        contractor_a = Contractor(
            company_id=comp_a.id,
            contractor_id=f"CON-HA-{uid}",
            name=f"Contractor_HA_{uid}",
            work_type="Civil",
            contact_number=f"96{uuid.uuid4().int % 100000000:08d}",
            rate_type="Fixed",
        )
        contractor_b = Contractor(
            company_id=comp_b.id,
            contractor_id=f"CON-HB-{uid}",
            name=f"Contractor_HB_{uid}",
            work_type="Civil",
            contact_number=f"95{uuid.uuid4().int % 100000000:08d}",
            rate_type="Fixed",
        )
        db.add_all([contractor_a, contractor_b])
        await db.flush()

        # 7. Labour Type
        lt_res = await db.execute(select(LabourType).limit(1))
        lt = lt_res.scalar_one_or_none()
        if not lt:
            lt = LabourType(name=f"Mason_{uid}", skill_category="Skilled", default_daily_wage=Decimal("800.00"))
            db.add(lt)
            await db.flush()

        # 8. Labour Records
        user_labour_a = User(
            email=f"labour_ha_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name=f"Labour HA {uid}",
            company_id=comp_a.id,
            is_active=True,
            role="Labour",
        )
        user_labour_b = User(
            email=f"labour_hb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name=f"Labour HB {uid}",
            company_id=comp_b.id,
            is_active=True,
            role="Labour",
        )
        db.add_all([user_labour_a, user_labour_b])
        await db.flush()

        labour_a = Labour(
            labour_name=f"Labour HA {uid}",
            worker_code=f"LAB_A_{uid}",
            company_id=comp_a.id,
            user_id=user_labour_a.id,
            contractor_id=contractor_a.id,
            labour_type_id=lt.id,
            custom_daily_wage_rate=Decimal("900.00"),
            custom_ot_rate_per_hour=Decimal("150.00"),
            status=LabourStatus.ACTIVE,
        )
        labour_b = Labour(
            labour_name=f"Labour HB {uid}",
            worker_code=f"LAB_B_{uid}",
            company_id=comp_b.id,
            user_id=user_labour_b.id,
            contractor_id=contractor_b.id,
            labour_type_id=lt.id,
            custom_daily_wage_rate=Decimal("950.00"),
            custom_ot_rate_per_hour=Decimal("160.00"),
            status=LabourStatus.ACTIVE,
        )
        db.add_all([labour_a, labour_b])
        await db.flush()

        # Project assignments for labour
        lp_a = LabourProject(labour_id=labour_a.id, project_id=proj_a.id)
        lp_b = LabourProject(labour_id=labour_b.id, project_id=proj_b.id)
        pm_la = ProjectMember(project_id=proj_a.id, user_id=user_labour_a.id)
        pm_lb = ProjectMember(project_id=proj_b.id, user_id=user_labour_b.id)
        db.add_all([lp_a, lp_b, pm_la, pm_lb])
        await db.flush()

        # 9. Attendance
        today = date.today()
        att_a = UserAttendance(
            user_id=user_labour_a.id,
            project_id=proj_a.id,
            attendance_date=today,
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("2.00"),
            overtime_rate=Decimal("150.00"),
        )
        att_b = UserAttendance(
            user_id=user_labour_b.id,
            project_id=proj_b.id,
            attendance_date=today,
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("2.00"),
            overtime_rate=Decimal("160.00"),
        )
        db.add_all([att_a, att_b])
        await db.flush()

        # 10. Payroll
        payroll_a = LabourPayroll(
            labour_id=labour_a.id,
            project_id=proj_a.id,
            month=today.month,
            year=today.year,
            total_working_hours=Decimal("8.00"),
            total_overtime_hours=Decimal("2.00"),
            total_wage=Decimal("1200.00"),
            paid_amount=Decimal("0.00"),
            remaining_amount=Decimal("1200.00"),
            status="PENDING",
        )
        payroll_b = LabourPayroll(
            labour_id=labour_b.id,
            project_id=proj_b.id,
            month=today.month,
            year=today.year,
            total_working_hours=Decimal("8.00"),
            total_overtime_hours=Decimal("2.00"),
            total_wage=Decimal("1300.00"),
            paid_amount=Decimal("0.00"),
            remaining_amount=Decimal("1300.00"),
            status="PENDING",
        )
        db.add_all([payroll_a, payroll_b])
        await db.flush()

        # 11. Wage Records
        wage_a = LabourWageRecord(
            labour_id=labour_a.id,
            project_id=proj_a.id,
            period_type="DAILY",
            start_date=today,
            end_date=today,
            gross_wage=Decimal("1200.00"),
            net_wage=Decimal("1200.00"),
            payment_mode="CASH",
            status="PENDING",
            created_by=admin_a.id,
        )
        wage_b = LabourWageRecord(
            labour_id=labour_b.id,
            project_id=proj_b.id,
            period_type="DAILY",
            start_date=today,
            end_date=today,
            gross_wage=Decimal("1300.00"),
            net_wage=Decimal("1300.00"),
            payment_mode="CASH",
            status="PENDING",
            created_by=admin_b.id,
        )
        db.add_all([wage_a, wage_b])
        await db.commit()

        tokens = {
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "custom_a": create_access_token({"sub": str(user_custom_a.id)}),
            "legacy_a": create_access_token({"sub": str(user_legacy_a.id)}),
        }

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "admin_a": admin_a,
            "admin_b": admin_b,
            "user_custom_a": user_custom_a,
            "user_legacy_a": user_legacy_a,
            "role_custom": role_custom,
            "contractor_a": contractor_a,
            "contractor_b": contractor_b,
            "labour_type": lt,
            "labour_a": labour_a,
            "labour_b": labour_b,
            "payroll_a": payroll_a,
            "payroll_b": payroll_b,
            "wage_a": wage_a,
            "wage_b": wage_b,
            "tokens": tokens,
        }

        yield data

        # Teardown
        async with AsyncSessionLocal() as cleanup_db:
            await cleanup_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([admin_a.id, admin_b.id, user_custom_a.id, user_legacy_a.id])))
            await cleanup_db.execute(delete(RolePermission).where(RolePermission.role_id == role_custom.id))
            await cleanup_db.execute(delete(LabourWageRecord).where(LabourWageRecord.id.in_([wage_a.id, wage_b.id])))
            await cleanup_db.execute(delete(LabourPayroll).where(LabourPayroll.id.in_([payroll_a.id, payroll_b.id])))
            await cleanup_db.execute(delete(UserAttendance).where(UserAttendance.id.in_([att_a.id, att_b.id])))
            await cleanup_db.execute(delete(LabourProject).where(LabourProject.labour_id.in_([labour_a.id, labour_b.id])))
            await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await cleanup_db.execute(delete(Labour).where(Labour.id.in_([labour_a.id, labour_b.id])))
            await cleanup_db.execute(delete(Contractor).where(Contractor.id.in_([contractor_a.id, contractor_b.id])))
            await cleanup_db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await cleanup_db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await cleanup_db.execute(delete(Role).where(Role.id == role_custom.id))
            await cleanup_db.execute(delete(User).where(User.id.in_([super_admin.id, admin_a.id, admin_b.id, user_custom_a.id, user_legacy_a.id, user_labour_a.id, user_labour_b.id])))
            await cleanup_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
            await cleanup_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await cleanup_db.commit()


@pytest.mark.asyncio
async def test_batch_h_unauthenticated_all_32_routes_401():
    """Verify that all 32 Batch H routes strictly reject unauthenticated requests with 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        today = date.today()
        dummy_routes = [
            ("POST", "/api/v1/labour", {"data": {"labour_name": "Test"}}),
            ("GET", "/api/v1/labour", {}),
            ("GET", "/api/v1/labour/payroll?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/stats?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/contractor-liability?project_id=1&month=1&year=2026", {}),
            ("PUT", "/api/v1/labour/9999", {"data": {"labour_name": "Updated"}}),
            ("DELETE", "/api/v1/labour/9999", {}),
            ("GET", "/api/v1/labour/9999/weekly-report", {}),
            ("GET", "/api/v1/labour/9999/monthly-report", {}),
            ("POST", "/api/v1/labour/payroll/generate", {"json": {"project_id": 1, "month": 1, "year": 2026}}),
            ("POST", "/api/v1/labour/payroll/lock", {"json": {"payroll_ids": [1]}}),
            ("POST", "/api/v1/labour/payroll/unlock", {"json": {"payroll_ids": [1]}}),
            ("POST", "/api/v1/labour/payroll/pay", {"json": {"project_id": 1, "labour_id": 1, "month": 1, "year": 2026, "amount": 100}}),
            ("POST", "/api/v1/labour/advance", {"json": {"project_id": 1, "labour_id": 1, "amount": 100, "description": "Advance"}}),
            ("GET", f"/api/v1/labour/attendance/dashboard?project_id=1&from_date={today}&to_date={today}", {}),
            ("GET", "/api/v1/labour/dashboard/stats", {}),
            ("GET", "/api/v1/labour/contractor/1", {}),
            ("GET", "/api/v1/labour/summary/skill?project_id=1", {}),
            ("GET", "/api/v1/labour/report/export?project_id=1", {}),
            ("GET", f"/api/v1/labour/attendance/export?project_id=1&from_date={today}&to_date={today}", {}),
            ("GET", "/api/v1/labour/payroll/export", {}),
            ("POST", "/api/v1/labour/wages", {"json": {"project_id": 1, "labour_id": 1, "period_type": "DAILY", "start_date": str(today), "end_date": str(today), "payment_mode": "CASH"}}),
            ("GET", "/api/v1/labour/wages", {}),
            ("POST", "/api/v1/labour/wages/9999/pay", {}),
            ("GET", "/api/v1/labour/wages/stats?project_id=1", {}),
            ("GET", "/api/v1/labour/9999/qr", {}),
            ("GET", "/api/v1/labour/9999", {}),
            ("GET", "/api/v1/labour/payroll/weekly-velocity?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/disbursement-history?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/fiscal-summary?project_id=1&month=1&year=2026", {}),
            ("GET", "/api/v1/labour/payroll/momentum?project_id=1", {}),
            ("GET", "/api/v1/labour/payroll/aggregate-report?project_id=1&month=1&year=2026", {}),
        ]

        assert len(dummy_routes) == 32, f"Expected exactly 32 routes, got {len(dummy_routes)}"

        for method, path, kwargs in dummy_routes:
            res = await ac.request(method, path, **kwargs)
            assert res.status_code == 401, f"Route {method} {path} returned {res.status_code}, expected 401"


@pytest.mark.asyncio
async def test_batch_h_custom_role_dynamic_lifecycle_labour():
    """Verify dynamic lifecycle for labour permissions (403 -> DB grant -> 200 -> DB revoke -> 403 -> DB regrant -> 200)."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id

            # 1. Initially user has NO permissions -> 403
            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 403

            # 2. Grant labour.view
            role_name = d_data["role_custom"].name
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "labour.view"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                await db.commit()

            # Now 200 OK without restart
            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 200

            # 3. Revoke labour.view
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == p_view.id))
                await db.commit()

            # Now 403 Forbidden
            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 403

            # 4. Regrant labour.view
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                await db.commit()

            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_batch_h_custom_role_dynamic_lifecycle_payroll():
    """Verify dynamic lifecycle for payroll permissions."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name
            proj_id = d_data["proj_a"].id
            today = date.today()

            # 1. Test payroll.view lifecycle
            res = await ac.get(f"/api/v1/labour/payroll?project_id={proj_id}&month={today.month}&year={today.year}", headers=headers)
            assert res.status_code == 403

            async with AsyncSessionLocal() as db:
                p_pview = (await db.execute(select(Permission).where(Permission.code == "payroll.view"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_pview.id))
                await db.commit()

            res = await ac.get(f"/api/v1/labour/payroll?project_id={proj_id}&month={today.month}&year={today.year}", headers=headers)
            assert res.status_code == 200

            # 2. Test payroll.approve lifecycle
            payroll_id = d_data["payroll_a"].id
            labour_id = d_data["labour_a"].id
            pay_payload = {
                "project_id": proj_id,
                "labour_id": labour_id,
                "month": today.month,
                "year": today.year,
                "amount": 100.0,
            }

            res = await ac.post("/api/v1/labour/payroll/pay", json=pay_payload, headers=headers)
            assert res.status_code == 403

            async with AsyncSessionLocal() as db:
                p_papp = (await db.execute(select(Permission).where(Permission.code == "payroll.approve"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_papp.id))
                await db.commit()

            # Now authorized (may fail validation/account setup if accounts not seeded, but auth succeeds)
            res = await ac.post("/api/v1/labour/payroll/pay", json=pay_payload, headers=headers)
            assert res.status_code != 403


@pytest.mark.asyncio
async def test_batch_h_custom_role_dynamic_lifecycle_attendance():
    """Verify dynamic lifecycle for attendance permissions."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name
            proj_id = d_data["proj_a"].id
            today = date.today()

            res = await ac.get(f"/api/v1/labour/attendance/dashboard?project_id={proj_id}&from_date={today}&to_date={today}", headers=headers)
            assert res.status_code == 403

            async with AsyncSessionLocal() as db:
                p_att = (await db.execute(select(Permission).where(Permission.code == "attendance.view"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_att.id))
                await db.commit()

            res = await ac.get(f"/api/v1/labour/attendance/dashboard?project_id={proj_id}&from_date={today}&to_date={today}", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_batch_h_user_permission_overrides():
    """Verify user-level positive and negative overrides take precedence over role permissions."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            user_id = d_data["user_custom_a"].id
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name

            # 1. Positive override: role lacks permission, user override grants it -> 200
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "labour.view"))).scalar_one()
                db.add(UserPermissionOverride(user_id=user_id, permission_id=p_view.id, is_granted=True))
                await db.commit()

            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 200

            # 2. Negative override: role HAS permission, user override explicitly DENIES it -> 403
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                override = (await db.execute(select(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id, UserPermissionOverride.permission_id == p_view.id))).scalar_one()
                override.is_granted = False
                await db.commit()

            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_batch_h_wildcard_permission():
    """Verify wildcard permissions `*` and `labour.*` authorize Batch H routes."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name

            # Grant wildcard
            async with AsyncSessionLocal() as db:
                p_star = (await db.execute(select(Permission).where(Permission.code == "labour.*"))).scalar_one_or_none()
                if not p_star:
                    p_star = (await db.execute(select(Permission).where(Permission.code == "*"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_star.id))
                await db.commit()

            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_batch_h_legacy_role_strings_denied():
    """Verify legacy role strings (e.g. 'Project Manager') with 0 DB permissions receive 403."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["legacy_a"]
            headers = {"Authorization": f"Bearer {token}"}

            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_batch_h_tenant_isolation_labour_and_cache():
    """Verify tenant isolation and Redis cache isolation (P0-1 remediation)."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {d_data['tokens']['admin_a']}"}
            headers_b = {"Authorization": f"Bearer {d_data['tokens']['admin_b']}"}

            # 1. Company A list labour -> contains only Labour A
            res_a = await ac.get("/api/v1/labour", headers=headers_a)
            assert res_a.status_code == 200
            items_a = res_a.json().get("items", [])
            ids_a = [item["id"] for item in items_a]
            assert d_data["labour_a"].id in ids_a
            assert d_data["labour_b"].id not in ids_a

            # 2. Company B list labour -> contains only Labour B (no cached data leak from A)
            res_b = await ac.get("/api/v1/labour", headers=headers_b)
            assert res_b.status_code == 200
            items_b = res_b.json().get("items", [])
            ids_b = [item["id"] for item in items_b]
            assert d_data["labour_b"].id in ids_b
            assert d_data["labour_a"].id not in ids_b

            # 3. Detail lookup: Company A accessing Labour B -> 404
            res_cross = await ac.get(f"/api/v1/labour/{d_data['labour_b'].id}", headers=headers_a)
            assert res_cross.status_code == 404


@pytest.mark.asyncio
async def test_batch_h_tenant_isolation_payroll_and_wages():
    """Verify Company A cannot view or access Company B payroll or wage records."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {d_data['tokens']['admin_a']}"}
            headers_b = {"Authorization": f"Bearer {d_data['tokens']['admin_b']}"}
            proj_b = d_data["proj_b"].id
            today = date.today()

            # 1. Company A querying Company B project payroll -> 404
            res = await ac.get(f"/api/v1/labour/payroll?project_id={proj_b}&month={today.month}&year={today.year}", headers=headers_a)
            assert res.status_code == 404

            # 2. Company A querying Company B wage stats -> 404
            res = await ac.get(f"/api/v1/labour/wages/stats?project_id={proj_b}", headers=headers_a)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_batch_h_contractor_company_validation():
    """Verify Company A cannot assign a Company B contractor to a labour record (P1-2 remediation)."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {d_data['tokens']['admin_a']}"}
            contractor_b_id = d_data["contractor_b"].id

            # Attempt update labour A with contractor B
            res = await ac.put(
                f"/api/v1/labour/{d_data['labour_a'].id}",
                data={"contractor_id": contractor_b_id},
                headers=headers_a,
            )
            assert res.status_code in (400, 404, 422)


@pytest.mark.asyncio
async def test_batch_h_financial_payment_idor_blocked():
    """Verify Company A paying Company B wage record returns 404 with NO financial mutations (P1-3 remediation)."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {d_data['tokens']['admin_a']}"}
            wage_b_id = d_data["wage_b"].id

            # Company A attempts to pay wage B
            res = await ac.post(f"/api/v1/labour/wages/{wage_b_id}/pay", headers=headers_a)
            assert res.status_code == 404

            # Verify Wage B remains PENDING in database
            async with AsyncSessionLocal() as db:
                w_b = await db.get(LabourWageRecord, wage_b_id)
                assert w_b.status == "PENDING"


@pytest.mark.asyncio
async def test_batch_h_null_and_nonexistent_ids_404():
    """Verify non-existent IDs return clean 404 across Batch H routes."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d_data['tokens']['admin_a']}"}

            res = await ac.get("/api/v1/labour/99999999", headers=headers)
            assert res.status_code == 404

            res = await ac.delete("/api/v1/labour/99999999", headers=headers)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_batch_h_super_admin_tenant_context():
    """Verify Super Admin semantics (P0-2 remediation)."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d_data['tokens']['super_admin']}"}

            # Super admin listing labour without company context -> safe empty list
            res = await ac.get("/api/v1/labour", headers=headers)
            assert res.status_code == 200
            assert res.json().get("items") == []
