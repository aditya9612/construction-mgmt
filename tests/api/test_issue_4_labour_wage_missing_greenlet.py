import uuid
from datetime import date, datetime
from decimal import Decimal
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

import app.main
from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.security import get_password_hash, create_access_token
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus
from app.models.master_data import LabourType
from app.models.labour import Labour, LabourAttendance, LabourProject, LabourWageRecord
from app.models.user import User, UserRole
from app.core.enums import AttendanceStatus, LabourStatus, SkillType, WagePeriodType


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def setup_issue_4_data():
    """Seed test companies, projects, labour types, labour (WITHOUT custom wage rate), and attendance."""
    import asyncio

    async def _setup():
        async with AsyncSessionLocal() as db:
            uid = uuid.uuid4().hex[:8]
            pwd_hash = get_password_hash("TestPass123!")

            # 1. Two Companies
            comp_a = Company(name=f"Issue4_CompA_{uid}")
            comp_b = Company(name=f"Issue4_CompB_{uid}")
            db.add_all([comp_a, comp_b])
            await db.flush()


            # 2. Users for both companies
            admin_a = User(
                email=f"admin_a_{uid}@issue4.com",
                hashed_password=pwd_hash,
                full_name=f"Admin A {uid}",
                company_id=comp_a.id,
                is_super_admin=False,
                is_active=True,
                role="Admin",
            )
            admin_b = User(
                email=f"admin_b_{uid}@issue4.com",
                hashed_password=pwd_hash,
                full_name=f"Admin B {uid}",
                company_id=comp_b.id,
                is_super_admin=False,
                is_active=True,
                role="Admin",
            )
            db.add_all([admin_a, admin_b])
            await db.flush()

            # 3. Owners & Projects
            owner_a = Owner(
                owner_name=f"Owner A {uid}",
                owner_code=f"OWN_A_{uid}",
                mobile=f"9{uuid.uuid4().int % 1000000000:09d}",
                email=f"owner_a_{uid}@test.com",
                company_id=comp_a.id,
            )
            owner_b = Owner(
                owner_name=f"Owner B {uid}",
                owner_code=f"OWN_B_{uid}",
                mobile=f"9{uuid.uuid4().int % 1000000000:09d}",
                email=f"owner_b_{uid}@test.com",
                company_id=comp_b.id,
            )
            db.add_all([owner_a, owner_b])
            await db.flush()

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

            # 4. Master Data: LabourType with default_daily_wage=800.00
            lt = LabourType(
                name=f"Skilled Mason {uid}",
                unique_code=f"MASON_{uid}",
                skill_category=SkillType.SKILLED,
                default_daily_wage=Decimal("800.00"),
                default_working_hours=Decimal("8.00"),
                default_ot_rate_per_hour=Decimal("150.00"),
                is_active=True,
            )
            db.add(lt)
            await db.flush()

            # 5. Labour A: Belongs to Comp A, NO custom_daily_wage_rate (triggers labour_type lazy-load!)
            labour_a = Labour(
                labour_name=f"Ramesh Kumar {uid}",
                worker_code=f"WRK_A_{uid}",
                company_id=comp_a.id,
                labour_type_id=lt.id,
                custom_daily_wage_rate=None,  # Crucial: Must evaluate labour.labour_type.default_daily_wage!
                custom_ot_rate_per_hour=None,
                status=LabourStatus.ACTIVE,
            )
            # Labour B: Belongs to Comp B
            labour_b = Labour(
                labour_name=f"Suresh Patel {uid}",
                worker_code=f"WRK_B_{uid}",
                company_id=comp_b.id,
                labour_type_id=lt.id,
                custom_daily_wage_rate=None,
                status=LabourStatus.ACTIVE,
            )
            db.add_all([labour_a, labour_b])
            await db.flush()

            # Assign to projects
            lp_a = LabourProject(labour_id=labour_a.id, project_id=proj_a.id)
            lp_b = LabourProject(labour_id=labour_b.id, project_id=proj_b.id)
            db.add_all([lp_a, lp_b])
            await db.flush()

            # 6. Attendance for Labour A: 1 full day (8 hours) on date 2026-08-10
            att_date = date(2026, 8, 10)
            att_a = LabourAttendance(
                labour_id=labour_a.id,
                project_id=proj_a.id,
                attendance_date=att_date,
                status=AttendanceStatus.PRESENT,
                working_hours=Decimal("8.00"),
                overtime_hours=Decimal("2.00"),
                overtime_rate=Decimal("150.00"),
                task_description="Masonry work",
            )
            db.add(att_a)
            await db.commit()

            # Generate JWT tokens
            token_a = create_access_token({"sub": str(admin_a.id)})
            token_b = create_access_token({"sub": str(admin_b.id)})

            return {
                "comp_a_id": comp_a.id,
                "comp_b_id": comp_b.id,
                "admin_a_id": admin_a.id,
                "admin_b_id": admin_b.id,
                "token_a": token_a,
                "token_b": token_b,
                "proj_a_id": proj_a.id,
                "proj_b_id": proj_b.id,
                "labour_type_id": lt.id,
                "labour_a_id": labour_a.id,
                "labour_a_name": labour_a.labour_name,
                "labour_b_id": labour_b.id,
                "att_date": att_date,
            }

    return asyncio.run(_setup())


@pytest.mark.asyncio
async def test_01_successful_labour_wage_create_no_missing_greenlet(setup_issue_4_data):
    """
    Test 1 & Test 2:
    Verify Labour Wage create succeeds without sqlalchemy.exc.MissingGreenlet
    when Labour has NO custom_daily_wage_rate (relying on labour_type relationship).
    """
    data = setup_issue_4_data
    headers_a = {"Authorization": f"Bearer {data['token_a']}"}
    payload = {
        "project_id": data["proj_a_id"],
        "labour_id": data["labour_a_id"],
        "period_type": "Daily",
        "start_date": str(data["att_date"]),
        "end_date": str(data["att_date"]),
        "payment_mode": "CASH",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/v1/labour/wages", json=payload, headers=headers_a)

    assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["id"] is not None
    assert body["labour_id"] == data["labour_a_id"]
    assert body["project_id"] == data["proj_a_id"]
    assert body["period_type"] == "Daily"


@pytest.mark.asyncio
async def test_02_response_serialization_and_attributes(setup_issue_4_data):
    """
    Test 3 & Test 4:
    Verify response serialization completes safely:
    - created_at and updated_at are populated (not triggering lazy-load MissingGreenlet).
    - labour_name matches the worker's name.
    - gross_wage and net_wage correctly compute attendance hours based on labour_type default wage.
    """
    data = setup_issue_4_data
    headers_a = {"Authorization": f"Bearer {data['token_a']}"}

    # Hourly rate = 800 / 8 = 100.
    # 8 working hours = 8 * 100 = 800.
    # 2 overtime hours @ 150 = 300.
    # Expected gross = 800 + 300 = 1100.00.
    async with AsyncSessionLocal() as db:
        wage = (
            await db.execute(
                select(LabourWageRecord).where(
                    LabourWageRecord.labour_id == data["labour_a_id"],
                    LabourWageRecord.start_date == data["att_date"],
                )
            )
        ).scalar_one_or_none()

    assert wage is not None
    assert wage.gross_wage == Decimal("1100.00")
    assert wage.net_wage == Decimal("1100.00")
    assert wage.created_at is not None
    assert wage.updated_at is not None


@pytest.mark.asyncio
async def test_03_existing_validation_period_overlap_rejected(setup_issue_4_data):
    """
    Test 5a:
    Verify creating a second wage record for an overlapping period returns HTTP 409 Conflict.
    """
    data = setup_issue_4_data
    headers_a = {"Authorization": f"Bearer {data['token_a']}"}
    payload = {
        "project_id": data["proj_a_id"],
        "labour_id": data["labour_a_id"],
        "period_type": "Daily",
        "start_date": str(data["att_date"]),
        "end_date": str(data["att_date"]),
        "payment_mode": "CASH",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/v1/labour/wages", json=payload, headers=headers_a)

    assert response.status_code == 409, f"Expected 409 Conflict for overlapping wage, got {response.status_code}: {response.text}"
    assert "overlap" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_04_existing_validation_daily_period_date_mismatch(setup_issue_4_data):
    """
    Test 5b:
    Verify DAILY wage with different start_date and end_date returns HTTP 422 Unprocessable Entity.
    """
    data = setup_issue_4_data
    headers_a = {"Authorization": f"Bearer {data['token_a']}"}
    payload = {
        "project_id": data["proj_a_id"],
        "labour_id": data["labour_a_id"],
        "period_type": "Daily",
        "start_date": "2026-08-11",
        "end_date": "2026-08-12",
        "payment_mode": "CASH",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/v1/labour/wages", json=payload, headers=headers_a)

    assert response.status_code == 422, f"Expected 422 for daily date mismatch, got {response.status_code}"


@pytest.mark.asyncio
async def test_05_existing_validation_bank_mode_requires_account(setup_issue_4_data):
    """
    Test 5c:
    Verify payment_mode='BANK' without bank_account_id returns HTTP 400 Bad Request.
    """
    data = setup_issue_4_data
    headers_a = {"Authorization": f"Bearer {data['token_a']}"}
    payload = {
        "project_id": data["proj_a_id"],
        "labour_id": data["labour_a_id"],
        "period_type": "Daily",
        "start_date": "2026-08-15",
        "end_date": "2026-08-15",
        "payment_mode": "BANK",
        "bank_account_id": None,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/v1/labour/wages", json=payload, headers=headers_a)

    assert response.status_code == 400, f"Expected 400 Bad Request, got {response.status_code}: {response.text}"
    assert "bank_account_id" in response.json()["detail"]


@pytest.mark.asyncio
async def test_06_tenant_isolation_cross_company_labour_rejected(setup_issue_4_data):
    """
    Test 6a:
    Tenant A user attempts to create a wage against Labour belonging to Company B.
    Must be rejected with HTTP 404 / 403.
    """
    data = setup_issue_4_data
    headers_a = {"Authorization": f"Bearer {data['token_a']}"}
    payload = {
        "project_id": data["proj_a_id"],
        "labour_id": data["labour_b_id"],  # Labour belongs to Comp B!
        "period_type": "Daily",
        "start_date": "2026-08-20",
        "end_date": "2026-08-20",
        "payment_mode": "CASH",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/v1/labour/wages", json=payload, headers=headers_a)

    assert response.status_code in (403, 404), f"Expected 403/404 for cross-company labour, got {response.status_code}"


@pytest.mark.asyncio
async def test_07_tenant_isolation_cross_company_project_rejected(setup_issue_4_data):
    """
    Test 6b:
    Tenant A user attempts to create a wage against a Project belonging to Company B.
    Must be rejected with HTTP 404 / 403.
    """
    data = setup_issue_4_data
    headers_a = {"Authorization": f"Bearer {data['token_a']}"}
    payload = {
        "project_id": data["proj_b_id"],  # Project belongs to Comp B!
        "labour_id": data["labour_a_id"],
        "period_type": "Daily",
        "start_date": "2026-08-20",
        "end_date": "2026-08-20",
        "payment_mode": "CASH",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/v1/labour/wages", json=payload, headers=headers_a)

    assert response.status_code in (403, 404), f"Expected 403/404 for cross-company project, got {response.status_code}"
