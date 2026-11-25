import io
import uuid
from decimal import Decimal
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
import pypdf

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.user import User, UserRole
from app.models.owner import Owner
from app.models.project import Project
from app.models.master_data import LabourType
from app.models.labour import Labour, LabourPayroll
from app.core.enums import PayrollStatus, SkillType
from app.core.dependencies import (
    get_current_user,
    get_current_active_user,
)


@pytest_asyncio.fixture
async def setup_labour_pdf_test_context():
    run_uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_1739_{run_uid}", is_active=True)
        db.add(comp)
        await db.flush()

        labour_user = User(
            company_id=comp.id,
            email=f"labour_1739_{run_uid}@test.com",
            role=UserRole.LABOUR.value,
            is_active=True,
            is_super_admin=False,
            full_name="Rajesh Sharma",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        empty_labour_user = User(
            company_id=comp.id,
            email=f"empty_1739_{run_uid}@test.com",
            role=UserRole.LABOUR.value,
            is_active=True,
            is_super_admin=False,
            full_name="Sunil Verma",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([labour_user, empty_labour_user])
        await db.flush()

        owner = Owner(
            company_id=comp.id,
            owner_code=f"O_1739_{run_uid}",
            owner_name=f"Owner_1739_{run_uid}",
            mobile=f"96{uuid.uuid4().int % 100000000:08d}",
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            company_id=comp.id,
            owner_id=owner.id,
            project_name=f"Proj_1739_{run_uid}",
            business_id=f"P1739-{run_uid}",
            status="Ongoing",
        )
        db.add(proj)
        await db.flush()

        ltype = LabourType(
            company_id=comp.id,
            name=f"Mason_1739_{run_uid}",
            default_daily_wage=850.0,
            skill_category=SkillType.SKILLED,
        )
        db.add(ltype)
        await db.flush()

        labour = Labour(
            company_id=comp.id,
            user_id=labour_user.id,
            labour_name="Rajesh Sharma",
            worker_code=f"LAB-{run_uid.upper()}",
            labour_type_id=ltype.id,
            custom_daily_wage_rate=Decimal("850.00"),
            custom_ot_rate_per_hour=Decimal("150.00"),
            mobile_number=labour_user.mobile,
        )
        empty_labour = Labour(
            company_id=comp.id,
            user_id=empty_labour_user.id,
            labour_name="Sunil Verma",
            worker_code=f"LAB-E-{run_uid.upper()}",
            labour_type_id=ltype.id,
            custom_daily_wage_rate=Decimal("700.00"),
            mobile_number=empty_labour_user.mobile,
        )
        db.add_all([labour, empty_labour])
        await db.flush()

        pr1 = LabourPayroll(
            labour_id=labour.id,
            project_id=proj.id,
            month=9,
            year=2026,
            total_working_hours=Decimal("200.00"),
            total_overtime_hours=Decimal("15.50"),
            total_wage=Decimal("23575.00"),
            paid_amount=Decimal("23575.00"),
            remaining_amount=Decimal("0.00"),
            status=PayrollStatus.PAID,
            remarks="September salary payout",
        )
        pr2 = LabourPayroll(
            labour_id=labour.id,
            project_id=proj.id,
            month=10,
            year=2026,
            total_working_hours=Decimal("180.00"),
            total_overtime_hours=Decimal("8.00"),
            total_wage=Decimal("20400.00"),
            paid_amount=Decimal("20400.00"),
            remaining_amount=Decimal("0.00"),
            status=PayrollStatus.PAID,
            remarks="October salary payout",
        )
        db.add_all([pr1, pr2])
        await db.commit()

        context = {
            "comp_id": comp.id,
            "proj_id": proj.id,
            "labour_user": labour_user,
            "empty_labour_user": empty_labour_user,
            "labour_id": labour.id,
            "empty_labour_id": empty_labour.id,
            "pr1_id": pr1.id,
            "pr2_id": pr2.id,
            "ltype_id": ltype.id,
        }

    yield context

    app.dependency_overrides.clear()
    async with AsyncSessionLocal() as db:
        await db.execute(LabourPayroll.__table__.delete().where(LabourPayroll.id.in_([context["pr1_id"], context["pr2_id"]])))
        await db.execute(Labour.__table__.delete().where(Labour.id.in_([context["labour_id"], context["empty_labour_id"]])))
        await db.execute(LabourType.__table__.delete().where(LabourType.id == context["ltype_id"]))
        await db.execute(Project.__table__.delete().where(Project.company_id == context["comp_id"]))
        await db.execute(Owner.__table__.delete().where(Owner.company_id == context["comp_id"]))
        await db.execute(User.__table__.delete().where(User.company_id == context["comp_id"]))
        await db.execute(Company.__table__.delete().where(Company.id == context["comp_id"]))
        await db.commit()


@pytest.mark.asyncio
async def test_labour_payments_pdf_export_structure(setup_labour_pdf_test_context):
    ctx = setup_labour_pdf_test_context
    app.dependency_overrides[get_current_user] = lambda: ctx["labour_user"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["labour_user"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/dashboard/labour/payments/export?export_format=pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert "filename=labour_payments.pdf" in resp.headers["content-disposition"]

        content = resp.content
        assert content.startswith(b"%PDF-")
        assert b"%%EOF" in content

        # Inspect extracted text via pypdf
        reader = pypdf.PdfReader(io.BytesIO(content))
        assert len(reader.pages) >= 1
        page_text = reader.pages[0].extract_text()

        # Verify Header and Title
        assert "LABOUR PAYMENT REPORT" in page_text
        assert "Page 1 of" in page_text

        # Verify Worker Metadata
        assert "Rajesh Sharma" in page_text
        assert "Rs. 850.00" in page_text

        # Verify Summary Metrics
        assert "TOTAL RECORDS" in page_text
        assert "TOTAL OT HOURS" in page_text
        assert "TOTAL WAGE EARNED" in page_text
        assert "23.5 hrs" in page_text  # 15.5 + 8.0 = 23.5
        assert "Rs. 43,975.00" in page_text  # 23575 + 20400 = 43975

        # Verify Table Content and Formatted Currency
        assert "Rs. 23,575.00" in page_text
        assert "Rs. 20,400.00" in page_text
        print("DEBUG TEXT:\n", page_text)
        assert "September salary" in page_text and "payout" in page_text
        assert "October salary payout" in page_text
        assert "GRAND TOTAL" in page_text


@pytest.mark.asyncio
async def test_labour_payments_pdf_export_empty_records(setup_labour_pdf_test_context):
    ctx = setup_labour_pdf_test_context
    app.dependency_overrides[get_current_user] = lambda: ctx["empty_labour_user"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["empty_labour_user"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/dashboard/labour/payments/export?export_format=pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"

        reader = pypdf.PdfReader(io.BytesIO(resp.content))
        page_text = reader.pages[0].extract_text()
        assert "LABOUR PAYMENT REPORT" in page_text
        assert "Sunil Verma" in page_text
        assert "No payment records found" in page_text


@pytest.mark.asyncio
async def test_labour_payments_csv_export_backward_compatible(setup_labour_pdf_test_context):
    ctx = setup_labour_pdf_test_context
    app.dependency_overrides[get_current_user] = lambda: ctx["labour_user"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["labour_user"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/dashboard/labour/payments/export?export_format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "ID,Date,Skill Type,Daily Wage,OT Hours,Total Wage Earned,Remarks,Status" in resp.text
        assert "September salary payout" in resp.text
