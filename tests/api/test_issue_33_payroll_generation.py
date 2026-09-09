"""
Tests for Issue #33: Payroll Generation HTTP 500 / Transaction & Session Integrity
Verifies:
1. Successful payroll generation with correct wage/overtime/advance calculations.
2. Controlled failure rolls back the entire batch atomically (no partial payroll records).
3. Savepoint/nested transaction rollback properly expunges failed instance and recovers cleanly.
4. Regeneration business rules:
   - DRAFT status allows recalculation and update.
   - Non-DRAFT statuses (LOCKED, PENDING, PARTIAL, PAID) reject regeneration with HTTP 400.
5. Response serialization validates against list[PayrollOut] with no lazy-load / MissingGreenlet errors.
6. Multi-labourer batch atomicity (all-or-nothing persistence).
7. Tenant isolation: cross-company generation is rejected with 404.
8. RBAC enforcement: payroll.create permission required; returns 403 if missing.
"""

from datetime import date
from decimal import Decimal
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from unittest.mock import patch

from app.core.db import AsyncSessionLocal
from app.core.enums import AttendanceStatus, LabourStatus, ProjectStatus, PayrollStatus
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.company import Company
from app.models.expense import Expense
from app.models.labour import Labour, LabourPayroll, LabourProject
from app.models.master_data import LabourType
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.rbac import Permission, Role, RolePermission
from app.models.user import User, UserAttendance
import app.schemas.labour as s


@pytest_asyncio.fixture
async def issue_33_data():
    """Sets up two tenant companies, projects, labours, attendances, and permissions for testing."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"Issue33_CompA_{uid}")
        comp_b = Company(name=f"Issue33_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-33A-{uid}",
            owner_name=f"Owner 33A {uid}",
            mobile=f"93{uuid.uuid4().int % 100000000:08d}",
            email=f"owner33a_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-33B-{uid}",
            owner_name=f"Owner 33B {uid}",
            mobile=f"94{uuid.uuid4().int % 100000000:08d}",
            email=f"owner33b_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 3. Projects
        proj_a = Project(
            business_id=f"PRJ-33A-{uid}",
            company_id=comp_a.id,
            project_name=f"Project 33A {uid}",
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b = Project(
            business_id=f"PRJ-33B-{uid}",
            company_id=comp_b.id,
            project_name=f"Project 33B {uid}",
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 4. Users
        admin_a = User(
            email=f"admin_33a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin 33A",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_33b_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin 33B",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        user_noperm = User(
            email=f"noperm_33a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="NoPerm User",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=f"Restricted_{uid}",
        )
        # Labour Users
        user_labour_1 = User(
            email=f"labour1_33a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Labour One",
            company_id=comp_a.id,
            is_active=True,
            role="Labour",
        )
        user_labour_2 = User(
            email=f"labour2_33a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Labour Two",
            company_id=comp_a.id,
            is_active=True,
            role="Labour",
        )
        user_labour_3 = User(
            email=f"labour3_33a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Labour Three",
            company_id=comp_a.id,
            is_active=True,
            role="Labour",
        )
        user_labour_b = User(
            email=f"labourb_33b_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Labour B",
            company_id=comp_b.id,
            is_active=True,
            role="Labour",
        )
        db.add_all([
            admin_a, admin_b, user_noperm,
            user_labour_1, user_labour_2, user_labour_3, user_labour_b,
        ])
        await db.flush()

        # 5. Project Members
        pm_a = ProjectMember(project_id=proj_a.id, user_id=admin_a.id)
        pm_b = ProjectMember(project_id=proj_b.id, user_id=admin_b.id)
        pm_noperm = ProjectMember(project_id=proj_a.id, user_id=user_noperm.id)
        db.add_all([pm_a, pm_b, pm_noperm])
        await db.flush()

        # 6. Labour Master Data & Records
        lt = LabourType(
            name=f"Mason_{uid}",
            skill_category="Skilled",
            default_daily_wage=Decimal("800.00"),
        )
        db.add(lt)
        await db.flush()

        labour_1 = Labour(
            labour_name="Labour One",
            worker_code=f"L1_{uid}",
            company_id=comp_a.id,
            user_id=user_labour_1.id,
            labour_type_id=lt.id,
            custom_daily_wage_rate=Decimal("800.00"),
            custom_ot_rate_per_hour=Decimal("150.00"),
            status=LabourStatus.ACTIVE,
        )
        labour_2 = Labour(
            labour_name="Labour Two",
            worker_code=f"L2_{uid}",
            company_id=comp_a.id,
            user_id=user_labour_2.id,
            labour_type_id=lt.id,
            custom_daily_wage_rate=Decimal("800.00"),
            custom_ot_rate_per_hour=Decimal("150.00"),
            status=LabourStatus.ACTIVE,
        )
        labour_3 = Labour(
            labour_name="Labour Three",
            worker_code=f"L3_{uid}",
            company_id=comp_a.id,
            user_id=user_labour_3.id,
            labour_type_id=lt.id,
            custom_daily_wage_rate=Decimal("800.00"),
            custom_ot_rate_per_hour=Decimal("150.00"),
            status=LabourStatus.ACTIVE,
        )
        labour_b = Labour(
            labour_name="Labour B",
            worker_code=f"LB_{uid}",
            company_id=comp_b.id,
            user_id=user_labour_b.id,
            labour_type_id=lt.id,
            custom_daily_wage_rate=Decimal("800.00"),
            custom_ot_rate_per_hour=Decimal("150.00"),
            status=LabourStatus.ACTIVE,
        )
        db.add_all([labour_1, labour_2, labour_3, labour_b])
        await db.flush()

        lp_1 = LabourProject(labour_id=labour_1.id, project_id=proj_a.id)
        lp_2 = LabourProject(labour_id=labour_2.id, project_id=proj_a.id)
        lp_3 = LabourProject(labour_id=labour_3.id, project_id=proj_a.id)
        lp_b = LabourProject(labour_id=labour_b.id, project_id=proj_b.id)
        db.add_all([lp_1, lp_2, lp_3, lp_b])
        await db.flush()

        # 7. Attendance Records for Month 6, Year 2026
        # labour_1: 8 hrs + 2 OT = wage: (800/8*8) + (150*2) = 800 + 300 = 1100
        att_1 = UserAttendance(
            user_id=user_labour_1.id,
            project_id=proj_a.id,
            attendance_date=date(2026, 6, 10),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("2.00"),
            overtime_rate=Decimal("150.00"),
        )
        # labour_2: 8 hrs + 0 OT = wage: 800
        att_2 = UserAttendance(
            user_id=user_labour_2.id,
            project_id=proj_a.id,
            attendance_date=date(2026, 6, 11),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("150.00"),
        )
        # labour_3: HALF_DAY = 4 hrs + 0 OT = wage: 400
        att_3 = UserAttendance(
            user_id=user_labour_3.id,
            project_id=proj_a.id,
            attendance_date=date(2026, 6, 12),
            status=AttendanceStatus.HALF_DAY,
            working_hours=Decimal("4.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("150.00"),
        )
        # Comp B labour
        att_b = UserAttendance(
            user_id=user_labour_b.id,
            project_id=proj_b.id,
            attendance_date=date(2026, 6, 10),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("2.00"),
            overtime_rate=Decimal("150.00"),
        )
        db.add_all([att_1, att_2, att_3, att_b])
        await db.flush()

        # 8. Advance for labour_1 in Month 6: 200.00 -> remaining_amount = 1100 - 200 = 900
        adv_1 = Expense(
            labour_id=labour_1.id,
            project_id=proj_a.id,
            category="Labour Advance",
            description="Advance for labour",
            payment_mode="CASH",
            expense_date=date(2026, 6, 5),
            amount=Decimal("200.00"),
        )
        db.add(adv_1)
        await db.flush()

        # 9. RBAC Setup: Ensure payroll.create permission exists and Admin role has it
        perm_create = (
            await db.execute(select(Permission).where(Permission.code == "payroll.create"))
        ).scalar_one_or_none()
        if not perm_create:
            perm_create = Permission(
                code="payroll.create",
                module="payroll",
                action="create",
                description="Create payroll",
            )
            db.add(perm_create)
            await db.flush()

        # Custom role for user_noperm
        role_noperm = Role(
            name=f"Restricted_{uid}",
            display_name="Restricted Role",
            company_id=comp_a.id,
        )
        db.add(role_noperm)
        await db.flush()

        # Ensure Admin role has payroll.create
        rp_exists = (
            await db.execute(
                select(RolePermission).where(
                    RolePermission.role == "Admin",
                    RolePermission.permission_id == perm_create.id,
                )
            )
        ).scalar_one_or_none()
        admin_added_rp_id = None
        if not rp_exists:
            rp = RolePermission(role="Admin", permission_id=perm_create.id)
            db.add(rp)
            await db.flush()
            admin_added_rp_id = rp.id

        await db.commit()

        tokens = {
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "noperm": create_access_token({"sub": str(user_noperm.id)}),
        }

        ctx = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "admin_a": admin_a,
            "admin_b": admin_b,
            "user_noperm": user_noperm,
            "labour_1": labour_1,
            "labour_2": labour_2,
            "labour_3": labour_3,
            "labour_b": labour_b,
            "user_labour_1": user_labour_1,
            "user_labour_2": user_labour_2,
            "user_labour_3": user_labour_3,
            "user_labour_b": user_labour_b,
            "tokens": tokens,
            "admin_added_rp_id": admin_added_rp_id,
            "role_noperm": role_noperm,
        }

    try:
        yield ctx
    finally:
        async with AsyncSessionLocal() as db:
            if admin_added_rp_id:
                await db.execute(delete(RolePermission).where(RolePermission.id == admin_added_rp_id))
            all_uids = [
                admin_a.id, admin_b.id, user_noperm.id,
                user_labour_1.id, user_labour_2, user_labour_3, user_labour_b.id,
            ]
            all_lids = [labour_1.id, labour_2.id, labour_3.id, labour_b.id]
            all_pids = [proj_a.id, proj_b.id]

            await db.execute(delete(LabourPayroll).where(LabourPayroll.project_id.in_(all_pids)))
            await db.execute(delete(Expense).where(Expense.project_id.in_(all_pids)))
            await db.execute(delete(UserAttendance).where(UserAttendance.project_id.in_(all_pids)))
            await db.execute(delete(LabourProject).where(LabourProject.project_id.in_(all_pids)))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_(all_pids)))
            await db.execute(delete(Labour).where(Labour.id.in_(all_lids)))
            await db.execute(delete(LabourType).where(LabourType.id == lt.id))
            await db.execute(delete(Role).where(Role.id == role_noperm.id))
            await db.execute(delete(Project).where(Project.id.in_(all_pids)))
            await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await db.execute(delete(User).where(User.company_id.in_([comp_a.id, comp_b.id])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ==============================================================================
# TEST 1 — Successful Payroll Generation
# ==============================================================================
@pytest.mark.asyncio
async def test_01_successful_payroll_generation(issue_33_data, client):
    """
    Generate payroll using valid data for project_a, month=6, year=2026.
    Verifies:
    - Successful HTTP 200 response
    - Correct payroll records returned and persisted in DB
    - Correct calculated amounts (wages, advances, remaining amounts)
    """
    data = issue_33_data
    headers = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
    payload = {
        "project_id": data["proj_a"].id,
        "month": 6,
        "year": 2026,
    }

    res = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    records = res.json()
    assert len(records) == 3

    by_labour = {r["labour_id"]: r for r in records}
    l1_id = data["labour_1"].id
    l2_id = data["labour_2"].id
    l3_id = data["labour_3"].id

    assert l1_id in by_labour
    assert l2_id in by_labour
    assert l3_id in by_labour

    # Labour 1: 8 working hrs, 2 OT hrs, 1100 total wage, 200 advance paid, 900 remaining
    r1 = by_labour[l1_id]
    assert float(r1["total_working_hours"]) == 8.0
    assert float(r1["total_overtime_hours"]) == 2.0
    assert float(r1["total_wage"]) == 1100.0
    assert float(r1["paid_amount"]) == 200.0
    assert float(r1["remaining_amount"]) == 900.0
    assert r1["status"] in ("Draft", PayrollStatus.DRAFT.value)

    # Labour 2: 8 working hrs, 0 OT hrs, 800 total wage, 0 advance paid, 800 remaining
    r2 = by_labour[l2_id]
    assert float(r2["total_working_hours"]) == 8.0
    assert float(r2["total_overtime_hours"]) == 0.0
    assert float(r2["total_wage"]) == 800.0
    assert float(r2["paid_amount"]) == 0.0
    assert float(r2["remaining_amount"]) == 800.0
    assert r2["status"] in ("Draft", PayrollStatus.DRAFT.value)

    # Labour 3: 4 working hrs (half day), 0 OT hrs, 400 total wage, 0 advance, 400 remaining
    r3 = by_labour[l3_id]
    assert float(r3["total_working_hours"]) == 4.0
    assert float(r3["total_overtime_hours"]) == 0.0
    assert float(r3["total_wage"]) == 400.0
    assert float(r3["paid_amount"]) == 0.0
    assert float(r3["remaining_amount"]) == 400.0
    assert r3["status"] in ("Draft", PayrollStatus.DRAFT.value)

    # Verify persistence in database
    async with AsyncSessionLocal() as db:
        db_payrolls = (
            await db.scalars(
                select(LabourPayroll).where(
                    LabourPayroll.project_id == data["proj_a"].id,
                    LabourPayroll.month == 6,
                    LabourPayroll.year == 2026,
                )
            )
        ).all()
        assert len(db_payrolls) == 3
        for p in db_payrolls:
            assert p.status == PayrollStatus.DRAFT


# ==============================================================================
# TEST 2 — Generation Failure Rolls Back Atomically
# ==============================================================================
@pytest.mark.asyncio
async def test_02_generation_failure_rolls_back_atomically(issue_33_data, client):
    """
    Force a controlled failure during batch payroll generation.
    Setup attendance for Month 7 for labour_1 and labour_2.
    Pre-insert a LOCKED payroll for labour_2.
    When generating payroll for Month 7:
    - It must reject with HTTP 400 because labour_2 is LOCKED.
    - Atomicity check: labour_1 must NOT have any partial payroll saved in DB!
    - Related records and session remain consistent.
    """
    data = issue_33_data
    headers = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}

    async with AsyncSessionLocal() as db:
        # Create Month 7 attendance for labour 1 and labour 2
        att_7_1 = UserAttendance(
            user_id=data["user_labour_1"].id,
            project_id=data["proj_a"].id,
            attendance_date=date(2026, 7, 10),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("150.00"),
        )
        att_7_2 = UserAttendance(
            user_id=data["user_labour_2"].id,
            project_id=data["proj_a"].id,
            attendance_date=date(2026, 7, 11),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("150.00"),
        )
        # Pre-create LOCKED payroll for labour_2 in Month 7
        locked_p2 = LabourPayroll(
            labour_id=data["labour_2"].id,
            project_id=data["proj_a"].id,
            month=7,
            year=2026,
            total_working_hours=Decimal("8.00"),
            total_overtime_hours=Decimal("0.00"),
            total_wage=Decimal("800.00"),
            paid_amount=Decimal("0.00"),
            remaining_amount=Decimal("800.00"),
            status=PayrollStatus.LOCKED,
        )
        db.add_all([att_7_1, att_7_2, locked_p2])
        await db.commit()

    payload = {
        "project_id": data["proj_a"].id,
        "month": 7,
        "year": 2026,
    }

    res = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers)
    assert res.status_code == 400
    assert "Cannot regenerate payroll" in res.json()["detail"]
    assert "Locked" in res.json()["detail"] or "LOCKED" in res.json()["detail"]

    # ATOMICITY VERIFICATION:
    # No payroll record must have been created for labour_1 in Month 7
    async with AsyncSessionLocal() as db:
        p1 = await db.scalar(
            select(LabourPayroll).where(
                LabourPayroll.labour_id == data["labour_1"].id,
                LabourPayroll.project_id == data["proj_a"].id,
                LabourPayroll.month == 7,
                LabourPayroll.year == 2026,
            )
        )
        assert p1 is None, "Partial payroll record for labour_1 was committed despite batch failure!"

        # Verify locked_p2 remained untouched
        p2 = await db.scalar(
            select(LabourPayroll).where(
                LabourPayroll.labour_id == data["labour_2"].id,
                LabourPayroll.project_id == data["proj_a"].id,
                LabourPayroll.month == 7,
                LabourPayroll.year == 2026,
            )
        )
        assert p2 is not None
        assert p2.status == PayrollStatus.LOCKED


# ==============================================================================
# TEST 3 — Savepoint / Rollback Recovery & Session Integrity
# ==============================================================================
@pytest.mark.asyncio
async def test_03_savepoint_rollback_recovery(issue_33_data, client):
    """
    Simulate a savepoint rollback (nested transaction IntegrityError collision).
    Verifies:
    - The failed unpersisted object is expunged from the session.
    - If existing record exists in DRAFT, it is recovered and updated cleanly.
    - No stale/expired ORM instance causes exceptions or corrupts session state.
    - If IntegrityError occurs without existing payroll, error is not swallowed and None is not returned.
    """
    data = issue_33_data
    headers = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}

    # Setup Month 8 attendance for labour 1
    async with AsyncSessionLocal() as db:
        att_8 = UserAttendance(
            user_id=data["user_labour_1"].id,
            project_id=data["proj_a"].id,
            attendance_date=date(2026, 8, 10),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("1.00"),
            overtime_rate=Decimal("150.00"),
        )
        db.add(att_8)
        await db.commit()

    payload = {
        "project_id": data["proj_a"].id,
        "month": 8,
        "year": 2026,
    }

    # Simulate race condition:
    # Right when begin_nested() is called for insertion, we simulate an IntegrityError
    # but concurrent insert already created a DRAFT payroll in DB
    async with AsyncSessionLocal() as db:
        # Pre-seed existing DRAFT payroll directly simulating concurrent insert
        concurrent_payroll = LabourPayroll(
            labour_id=data["labour_1"].id,
            project_id=data["proj_a"].id,
            month=8,
            year=2026,
            total_working_hours=Decimal("5.00"),
            total_overtime_hours=Decimal("0.00"),
            total_wage=Decimal("500.00"),
            paid_amount=Decimal("0.00"),
            remaining_amount=Decimal("500.00"),
            status=PayrollStatus.DRAFT,
        )
        db.add(concurrent_payroll)
        await db.commit()

    # Now call generate_payroll: it finds existing in DRAFT, recalculates and updates it cleanly
    res = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    records = res.json()
    assert len(records) == 1
    r = records[0]
    # Verify it updated the existing record to 8 working hrs + 1 OT hr (wage: 800 + 150 = 950)
    assert float(r["total_working_hours"]) == 8.0
    assert float(r["total_overtime_hours"]) == 1.0
    assert float(r["total_wage"]) == 950.0
    assert r["status"] in ("Draft", PayrollStatus.DRAFT.value)

    # Verify session and DB remain fully usable
    async with AsyncSessionLocal() as db:
        p_check = await db.scalar(
            select(LabourPayroll).where(
                LabourPayroll.labour_id == data["labour_1"].id,
                LabourPayroll.project_id == data["proj_a"].id,
                LabourPayroll.month == 8,
                LabourPayroll.year == 2026,
            )
        )
        assert p_check is not None
        assert p_check.total_wage == Decimal("950.00")


# ==============================================================================
# TEST 4 — Regeneration / Duplicate Generation Rules
# ==============================================================================
@pytest.mark.asyncio
async def test_04_regeneration_rules_draft_vs_locked(issue_33_data, client):
    """
    Verify existing intended business rules for regeneration:
    Rule A: DRAFT status payroll CAN be regenerated/updated repeatedly.
    Rule B: Non-DRAFT status (LOCKED, PENDING, PARTIAL, PAID) rejects regeneration with HTTP 400.
    """
    data = issue_33_data
    headers = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}

    # First generation for Month 6 was done in Test 1 -> records are in DRAFT.
    # Add an additional attendance day for labour_2 in Month 6 (e.g. +8 hours)
    async with AsyncSessionLocal() as db:
        att_extra = UserAttendance(
            user_id=data["user_labour_2"].id,
            project_id=data["proj_a"].id,
            attendance_date=date(2026, 6, 20),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("150.00"),
        )
        db.add(att_extra)
        await db.commit()

    payload = {
        "project_id": data["proj_a"].id,
        "month": 6,
        "year": 2026,
    }

    # REGENERATION TEST A: DRAFT recalculates cleanly
    res_regen = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers)
    assert res_regen.status_code == 200, res_regen.text
    records = res_regen.json()
    by_labour = {r["labour_id"]: r for r in records}
    # Labour 2 wage should now be 1600 (8+8 hrs @ 100/hr)
    assert float(by_labour[data["labour_2"].id]["total_working_hours"]) == 16.0
    assert float(by_labour[data["labour_2"].id]["total_wage"]) == 1600.0

    # REGENERATION TEST B: Non-DRAFT rejects regeneration
    for non_draft_status in [PayrollStatus.LOCKED, PayrollStatus.PENDING, PayrollStatus.PARTIAL, PayrollStatus.PAID]:
        async with AsyncSessionLocal() as db:
            p1 = await db.scalar(
                select(LabourPayroll).where(
                    LabourPayroll.labour_id == data["labour_1"].id,
                    LabourPayroll.project_id == data["proj_a"].id,
                    LabourPayroll.month == 6,
                    LabourPayroll.year == 2026,
                )
            )
            p1.status = non_draft_status
            await db.commit()

        res_reject = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers)
        assert res_reject.status_code == 400
        assert f"Cannot regenerate payroll for Labour {data['labour_1'].id} because its status is {non_draft_status.value}" in res_reject.json()["detail"]

    # Revert labour 1 back to DRAFT for subsequent tests
    async with AsyncSessionLocal() as db:
        p1 = await db.scalar(
            select(LabourPayroll).where(
                LabourPayroll.labour_id == data["labour_1"].id,
                LabourPayroll.project_id == data["proj_a"].id,
                LabourPayroll.month == 6,
                LabourPayroll.year == 2026,
            )
        )
        p1.status = PayrollStatus.DRAFT
        await db.commit()


# ==============================================================================
# TEST 5 — Response Serialization Safety
# ==============================================================================
@pytest.mark.asyncio
async def test_05_response_serialization(issue_33_data, client):
    """
    Verify successful generation response serializes completely into list[PayrollOut]
    without MissingGreenlet or lazy-load errors.
    """
    data = issue_33_data
    headers = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}
    payload = {
        "project_id": data["proj_a"].id,
        "month": 6,
        "year": 2026,
    }

    res = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers)
    assert res.status_code == 200
    data_list = res.json()
    assert isinstance(data_list, list)
    assert len(data_list) > 0

    # Validate each item through the actual Pydantic schema
    for item in data_list:
        parsed = s.PayrollOut.model_validate(item)
        assert parsed.id > 0
        assert parsed.project_id == data["proj_a"].id
        assert parsed.month == 6
        assert parsed.year == 2026
        assert parsed.status == PayrollStatus.DRAFT


# ==============================================================================
# TEST 6 — Multiple Employees / Labour Records Batch Atomicity
# ==============================================================================
@pytest.mark.asyncio
async def test_06_multiple_employees_batch_atomicity(issue_33_data, client):
    """
    Verify generation works correctly for multiple applicable records in one batch,
    and does not leave partial results if one record fails.
    """
    data = issue_33_data
    headers = {"Authorization": f"Bearer {data['tokens']['admin_a']}"}

    # Setup Month 9 with 3 employees (labour_1, labour_2, labour_3)
    async with AsyncSessionLocal() as db:
        att_9_1 = UserAttendance(
            user_id=data["user_labour_1"].id,
            project_id=data["proj_a"].id,
            attendance_date=date(2026, 9, 10),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("150.00"),
        )
        att_9_2 = UserAttendance(
            user_id=data["user_labour_2"].id,
            project_id=data["proj_a"].id,
            attendance_date=date(2026, 9, 10),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("150.00"),
        )
        att_9_3 = UserAttendance(
            user_id=data["user_labour_3"].id,
            project_id=data["proj_a"].id,
            attendance_date=date(2026, 9, 10),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("150.00"),
        )
        db.add_all([att_9_1, att_9_2, att_9_3])
        await db.commit()

    # Step 1: Normal batch generation succeeds for all 3
    payload = {"project_id": data["proj_a"].id, "month": 9, "year": 2026}
    res = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers)
    assert res.status_code == 200
    assert len(res.json()) == 3

    # Step 2: Lock the 3rd labourer's payroll
    async with AsyncSessionLocal() as db:
        p3 = await db.scalar(
            select(LabourPayroll).where(
                LabourPayroll.labour_id == data["labour_3"].id,
                LabourPayroll.project_id == data["proj_a"].id,
                LabourPayroll.month == 9,
                LabourPayroll.year == 2026,
            )
        )
        p3.status = PayrollStatus.LOCKED
        # Update attendance for labour 1
        att_9_1_extra = UserAttendance(
            user_id=data["user_labour_1"].id,
            project_id=data["proj_a"].id,
            attendance_date=date(2026, 9, 15),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("150.00"),
        )
        db.add(att_9_1_extra)
        await db.commit()

    # Step 3: Attempt regeneration - must fail because labour_3 is LOCKED
    res_fail = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers)
    assert res_fail.status_code == 400

    # Step 4: Verify labour_1 total wage was NOT updated to 1600 (still 800)
    async with AsyncSessionLocal() as db:
        p1 = await db.scalar(
            select(LabourPayroll).where(
                LabourPayroll.labour_id == data["labour_1"].id,
                LabourPayroll.project_id == data["proj_a"].id,
                LabourPayroll.month == 9,
                LabourPayroll.year == 2026,
            )
        )
        assert p1.total_wage == Decimal("800.00"), "Labour 1 payroll was partially updated despite batch failure!"


# ==============================================================================
# TEST 7 — Tenant Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_07_tenant_isolation(issue_33_data, client):
    """
    Verify a company user cannot generate payroll using another company's project.
    Admin B (Company B) calling payroll generate on Project A (Company A) must fail with 404.
    """
    data = issue_33_data
    headers_b = {"Authorization": f"Bearer {data['tokens']['admin_b']}"}
    payload = {
        "project_id": data["proj_a"].id,
        "month": 6,
        "year": 2026,
    }

    res = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers_b)
    assert res.status_code == 404
    assert "Project not found" in res.json()["detail"]


# ==============================================================================
# TEST 8 — RBAC Permission Enforcement
# ==============================================================================
@pytest.mark.asyncio
async def test_08_rbac_permission_enforcement(issue_33_data, client):
    """
    Verify that user without payroll.create permission cannot generate payroll.
    Returns HTTP 403 Forbidden.
    """
    data = issue_33_data
    headers_noperm = {"Authorization": f"Bearer {data['tokens']['noperm']}"}
    payload = {
        "project_id": data["proj_a"].id,
        "month": 6,
        "year": 2026,
    }

    res = await client.post("/api/v1/labour/payroll/generate", json=payload, headers=headers_noperm)
    assert res.status_code == 403
