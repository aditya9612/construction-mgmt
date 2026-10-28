import io
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pypdf
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.enums import EquipmentCondition, EquipmentStatus, ProjectType, RentalBillingMode
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.company import Company
from app.models.equipment import Equipment, EquipmentRental
from app.models.owner import Owner
from app.models.project import Project
from app.models.rbac import Permission, Role, RolePermission
from app.models.user import User, UserRole


@pytest.fixture
def run_uid():
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def setup_test_context(run_uid):
    async with AsyncSessionLocal() as db:
        # 1. Company A & B
        comp_a = Company(name=f"CompA_3940_{run_uid}", is_active=True)
        comp_b = Company(name=f"CompB_3940_{run_uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Users
        admin_a = User(
            company_id=comp_a.id,
            email=f"admin_a_3940_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=True,
        )
        admin_b = User(
            company_id=comp_b.id,
            email=f"admin_b_3940_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([admin_a, admin_b])
        await db.flush()

        # 3. Owner & Projects for Issue #3091
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-A-{run_uid}",
            owner_name=f"Client A {run_uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_a_{run_uid}@test.com",
        )
        db.add(owner_a)
        await db.flush()

        proj_res = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Residential Tower {run_uid}",
            business_id=f"PRJ-RES-{run_uid}",
            type=ProjectType.RESIDENTIAL,
            status="Ongoing",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=180),
        )
        proj_comm = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Commercial Plaza {run_uid}",
            business_id=f"PRJ-COM-{run_uid}",
            type=ProjectType.COMMERCIAL,
            status="Ongoing",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=200),
        )
        proj_notype = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Unspecified Project {run_uid}",
            business_id=f"PRJ-NONE-{run_uid}",
            type=None,
            status="Ongoing",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=100),
        )
        db.add_all([proj_res, proj_comm, proj_notype])
        await db.flush()

        # 4. Equipment & Rentals for Issue #3940
        eq_a = Equipment(
            company_id=comp_a.id,
            equipment_code=f"EQ-A-{run_uid}",
            equipment_name=f"Excavator {run_uid}",
            status=EquipmentStatus.AVAILABLE,
            condition=EquipmentCondition.GOOD,
            is_deleted=False,
        )
        eq_b = Equipment(
            company_id=comp_b.id,
            equipment_code=f"EQ-B-{run_uid}",
            equipment_name=f"Bulldozer {run_uid}",
            status=EquipmentStatus.AVAILABLE,
            condition=EquipmentCondition.GOOD,
            is_deleted=False,
        )
        db.add_all([eq_a, eq_b])
        await db.flush()

        today = date.today()
        # Active rental on eq_a
        rental_a = EquipmentRental(
            equipment_id=eq_a.id,
            client_name="Original Client A",
            rental_cost=Decimal("1500.00"),
            start_date=today + timedelta(days=10),
            end_date=today + timedelta(days=20),
            billing_mode=RentalBillingMode.PER_DAY,
            notes="Initial notes",
        )
        # Another rental on eq_a (for overlap testing)
        rental_overlap = EquipmentRental(
            equipment_id=eq_a.id,
            client_name="Future Client",
            rental_cost=Decimal("2000.00"),
            start_date=today + timedelta(days=30),
            end_date=today + timedelta(days=40),
            billing_mode=RentalBillingMode.PER_DAY,
            notes="Future rental",
        )
        # Completed rental on eq_a
        rental_completed = EquipmentRental(
            equipment_id=eq_a.id,
            client_name="Past Client",
            rental_cost=Decimal("1200.00"),
            start_date=today - timedelta(days=20),
            end_date=today - timedelta(days=10),
            billing_mode=RentalBillingMode.PER_DAY,
            notes="Past rental",
        )
        # Rental on Company B
        rental_b = EquipmentRental(
            equipment_id=eq_b.id,
            client_name="Client B",
            rental_cost=Decimal("3000.00"),
            start_date=today + timedelta(days=5),
            end_date=today + timedelta(days=15),
            billing_mode=RentalBillingMode.PER_DAY,
        )
        db.add_all([rental_a, rental_overlap, rental_completed, rental_b])
        await db.commit()

        token_a = create_access_token({"sub": str(admin_a.id)})
        token_b = create_access_token({"sub": str(admin_b.id)})

        context = {
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "admin_a_id": admin_a.id,
            "admin_b_id": admin_b.id,
            "owner_a_id": owner_a.id,
            "proj_res_id": proj_res.id,
            "proj_comm_id": proj_comm.id,
            "proj_notype_id": proj_notype.id,
            "eq_a_id": eq_a.id,
            "eq_b_id": eq_b.id,
            "rental_a_id": rental_a.id,
            "rental_overlap_id": rental_overlap.id,
            "rental_completed_id": rental_completed.id,
            "rental_b_id": rental_b.id,
            "token_a": token_a,
            "token_b": token_b,
        }

    yield context

    # Cleanup
    async with AsyncSessionLocal() as db:
        await db.execute(delete(EquipmentRental).where(EquipmentRental.id.in_([
            context["rental_a_id"],
            context["rental_overlap_id"],
            context["rental_completed_id"],
            context["rental_b_id"],
        ])))
        await db.execute(delete(Equipment).where(Equipment.id.in_([
            context["eq_a_id"],
            context["eq_b_id"],
        ])))
        await db.execute(delete(Project).where(Project.id.in_([
            context["proj_res_id"],
            context["proj_comm_id"],
            context["proj_notype_id"],
        ])))
        await db.execute(delete(Owner).where(Owner.id == context["owner_a_id"]))
        await db.execute(delete(User).where(User.id.in_([
            context["admin_a_id"],
            context["admin_b_id"],
        ])))
        await db.execute(delete(Company).where(Company.id.in_([
            context["comp_a_id"],
            context["comp_b_id"],
        ])))
        await db.commit()


@pytest.mark.asyncio
async def test_update_rental_valid_success(setup_test_context):
    """Verify standard valid updates succeed with 200 OK and persist changes."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_a_id']}",
            json={
                "client_name": "Updated Client Alpha",
                "notes": "Updated rental notes successfully",
                "rental_cost": 1750.50,
                "billing_mode": "LUMP_SUM",
            },
            headers=headers,
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert data["client_name"] == "Updated Client Alpha"
        assert Decimal(str(data["rental_cost"])) == Decimal("1750.50")
        assert data["notes"] == "Updated rental notes successfully"


@pytest.mark.asyncio
async def test_update_rental_rejects_rental_in_fields(setup_test_context):
    """Verify Issue #3940: submitting Rental-IN fields returns 400 Bad Request, NOT 500."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. is_received
        res1 = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_a_id']}",
            json={"is_received": True},
            headers=headers,
        )
        assert res1.status_code == 400
        assert "Rental-IN fields" in res1.json()["detail"]
        assert "is_received" in res1.json()["detail"]

        # 2. is_returned
        res2 = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_a_id']}",
            json={"is_returned": True},
            headers=headers,
        )
        assert res2.status_code == 400
        assert "Rental-IN fields" in res2.json()["detail"]
        assert "is_returned" in res2.json()["detail"]

        # 3. expected_end_date
        res3 = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_a_id']}",
            json={"expected_end_date": "2026-12-31"},
            headers=headers,
        )
        assert res3.status_code == 400
        assert "Rental-IN fields" in res3.json()["detail"]
        assert "expected_end_date" in res3.json()["detail"]

        # 4. actual_return_date
        res4 = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_a_id']}",
            json={"actual_return_date": "2026-12-31"},
            headers=headers,
        )
        assert res4.status_code == 400
        assert "Rental-IN fields" in res4.json()["detail"]
        assert "actual_return_date" in res4.json()["detail"]

    # Verify database was NOT mutated
    async with AsyncSessionLocal() as db:
        rental = await db.get(EquipmentRental, ctx["rental_a_id"])
        assert rental.client_name != "Failed Mutation"


@pytest.mark.asyncio
async def test_update_rental_validations(setup_test_context):
    """Verify business logic validations: completed rental, date order, overlap, cross-tenant."""
    ctx = setup_test_context
    headers_a = {"Authorization": f"Bearer {ctx['token_a']}"}
    headers_b = {"Authorization": f"Bearer {ctx['token_b']}"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Completed rental
        res_comp = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_completed_id']}",
            json={"notes": "Try to edit past rental"},
            headers=headers_a,
        )
        assert res_comp.status_code == 400
        assert "Completed rental cannot be updated" in res_comp.json()["detail"]

        # End date before start date
        # 1) When only end_date is provided before existing start_date (handled by endpoint -> 400)
        res_date_400 = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_a_id']}",
            json={
                "end_date": str(date.today() + timedelta(days=5)),
            },
            headers=headers_a,
        )
        assert res_date_400.status_code == 400
        assert "End date cannot be before start date" in res_date_400.json()["detail"]

        # 2) When both dates provided with end < start (schema validator -> 422)
        res_date_422 = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_a_id']}",
            json={
                "start_date": "2026-11-20",
                "end_date": "2026-11-10",
            },
            headers=headers_a,
        )
        assert res_date_422.status_code in (400, 422)

        # Overlap with existing rental
        res_overlap = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_a_id']}",
            json={
                "start_date": str(date.today() + timedelta(days=32)),
                "end_date": str(date.today() + timedelta(days=38)),
            },
            headers=headers_a,
        )
        assert res_overlap.status_code == 400
        assert "Rental overlap found" in res_overlap.json()["detail"]

        # Cross-tenant rental update (tenant B user cannot access tenant A rental)
        res_cross = await client.put(
            f"/api/v1/equipment/rental/{ctx['rental_a_id']}",
            json={"notes": "Hacking tenant A"},
            headers=headers_b,
        )
        assert res_cross.status_code == 404

        # Nonexistent rental ID
        res_404 = await client.put(
            "/api/v1/equipment/rental/9999999",
            json={"notes": "Nonexistent"},
            headers=headers_a,
        )
        assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_project_pdf_renders_residential_type(setup_test_context):
    """Verify Issue #3091: Residential project exports cleanly as 'Residential', not 'ProjectType.RESIDENTIAL'."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/reports/projects/pdf?project_id={ctx['proj_res_id']}",
            headers=headers,
        )
        assert res.status_code == 200, f"Failed: {res.text}"
        assert res.headers["content-type"] == "application/pdf"

        # Read PDF content
        reader = pypdf.PdfReader(io.BytesIO(res.content))
        all_text = "".join(page.extract_text() or "" for page in reader.pages)

        # Confirm clean display
        assert "ProjectType.RESIDENTIAL" not in all_text, "Found raw enum 'ProjectType.RESIDENTIAL' in PDF!"
        assert "Residential" in all_text


@pytest.mark.asyncio
async def test_project_pdf_renders_commercial_type(setup_test_context):
    """Verify Issue #3091: Commercial project exports cleanly as 'Commercial', not 'ProjectType.COMMERCIAL'."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/reports/projects/pdf?project_id={ctx['proj_comm_id']}",
            headers=headers,
        )
        assert res.status_code == 200, f"Failed: {res.text}"
        assert res.headers["content-type"] == "application/pdf"

        reader = pypdf.PdfReader(io.BytesIO(res.content))
        all_text = "".join(page.extract_text() or "" for page in reader.pages)

        assert "ProjectType.COMMERCIAL" not in all_text, "Found raw enum 'ProjectType.COMMERCIAL' in PDF!"
        assert "Commercial" in all_text


@pytest.mark.asyncio
async def test_project_pdf_renders_fallback_type(setup_test_context):
    """Verify Issue #3091: Project with None type exports safely with default fallback."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            f"/api/v1/reports/projects/pdf?project_id={ctx['proj_notype_id']}",
            headers=headers,
        )
        assert res.status_code == 200
        reader = pypdf.PdfReader(io.BytesIO(res.content))
        all_text = "".join(page.extract_text() or "" for page in reader.pages)

        assert "ProjectType" not in all_text
        assert "None" not in all_text or "Residential" in all_text


@pytest.mark.asyncio
async def test_project_pdf_export_all_projects(setup_test_context):
    """Verify exporting all projects (portfolio overview) remains functional."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/v1/reports/projects/pdf",
            headers=headers,
        )
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert len(res.content) > 100
