import io
import csv
import uuid
from decimal import Decimal
from datetime import datetime
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
import pypdf
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner, OwnerTransaction, OwnerTransactionType
from app.models.project import Project
from app.models.user import User, UserRole
from app.core.security import create_access_token


@pytest.fixture
def run_uid():
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def setup_test_context(run_uid):
    """Set up isolated test fixtures for Issue #3783 Owner Ledger export."""
    async with AsyncSessionLocal() as db:
        # 1. Company A & B
        comp_a = Company(name=f"CompA_3783_{run_uid}", is_active=True)
        comp_b = Company(name=f"CompB_3783_{run_uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Users
        admin_a = User(
            company_id=comp_a.id,
            email=f"admin_a_3783_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        admin_b = User(
            company_id=comp_b.id,
            email=f"admin_b_3783_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([admin_a, admin_b])
        await db.flush()

        # 3. Owners
        # Owner A1: Multiple transactions
        owner_a1 = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-A1-{run_uid}",
            owner_name=f"Owner A1 Multi {run_uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_a1_{run_uid}@test.com",
        )
        # Owner A2: Single transaction
        owner_a2 = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-A2-{run_uid}",
            owner_name=f"Owner A2 Single {run_uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_a2_{run_uid}@test.com",
        )
        # Owner A3: Empty transactions
        owner_a3 = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-A3-{run_uid}",
            owner_name=f"Owner A3 Empty {run_uid}",
            mobile=f"93{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_a3_{run_uid}@test.com",
        )
        # Owner B1: Belongs to Company B
        owner_b1 = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-B1-{run_uid}",
            owner_name=f"Owner B1 Multi {run_uid}",
            mobile=f"94{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_b1_{run_uid}@test.com",
        )
        db.add_all([owner_a1, owner_a2, owner_a3, owner_b1])
        await db.flush()

        # 4. Project for Company A
        proj_a = Project(
            company_id=comp_a.id,
            owner_id=owner_a1.id,
            project_name=f"Project A {run_uid}",
            business_id=f"PRJ-A-{run_uid}",
            status="Ongoing",
        )
        db.add(proj_a)
        await db.flush()

        # 5. Transactions for Owner A1 (3 transactions created at different times)
        t_a1_1 = OwnerTransaction(
            owner_id=owner_a1.id,
            project_id=proj_a.id,
            type=OwnerTransactionType.CREDIT.value,
            amount=Decimal("150000.00"),
            reference_type="DIRECT",
            reference_id=101,
            description="Initial capital contribution",
            created_at=datetime(2026, 1, 15, 10, 30, 0),
        )
        t_a1_2 = OwnerTransaction(
            owner_id=owner_a1.id,
            project_id=proj_a.id,
            type=OwnerTransactionType.DEBIT.value,
            amount=Decimal("50000.00"),
            reference_type="EXPENSE",
            reference_id=102,
            description="Drawings withdrawal",
            created_at=datetime(2026, 2, 20, 14, 0, 0),
        )
        t_a1_3 = OwnerTransaction(
            owner_id=owner_a1.id,
            project_id=proj_a.id,
            type=OwnerTransactionType.CREDIT.value,
            amount=Decimal("75000.00"),
            reference_type="DIRECT",
            reference_id=103,
            description="Second equity tranche",
            created_at=datetime(2026, 3, 10, 9, 15, 0),
        )

        # 6. Transaction for Owner A2 (Single transaction)
        t_a2_1 = OwnerTransaction(
            owner_id=owner_a2.id,
            project_id=proj_a.id,
            type=OwnerTransactionType.CREDIT.value,
            amount=Decimal("25000.00"),
            reference_type="INVOICE",
            reference_id=201,
            description="Single milestone contribution",
            created_at=datetime(2026, 4, 1, 11, 0, 0),
        )

        # 7. Transaction for Owner B1
        t_b1_1 = OwnerTransaction(
            owner_id=owner_b1.id,
            project_id=None,
            type=OwnerTransactionType.CREDIT.value,
            amount=Decimal("10000.00"),
            reference_type="DIRECT",
            reference_id=301,
            description="Company B transaction",
            created_at=datetime(2026, 1, 1, 10, 0, 0),
        )

        db.add_all([t_a1_1, t_a1_2, t_a1_3, t_a2_1, t_b1_1])
        await db.commit()

        token_a = create_access_token({"sub": str(admin_a.id)})
        token_b = create_access_token({"sub": str(admin_b.id)})

        context = {
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "admin_a_id": admin_a.id,
            "admin_b_id": admin_b.id,
            "owner_a1_id": owner_a1.id,
            "owner_a2_id": owner_a2.id,
            "owner_a3_id": owner_a3.id,
            "owner_b1_id": owner_b1.id,
            "proj_a_id": proj_a.id,
            "token_a": token_a,
            "token_b": token_b,
        }

    yield context

    # Isolated clean teardown
    async with AsyncSessionLocal() as db:
        await db.execute(delete(OwnerTransaction).where(OwnerTransaction.owner_id.in_([
            context["owner_a1_id"],
            context["owner_a2_id"],
            context["owner_a3_id"],
            context["owner_b1_id"],
        ])))
        await db.execute(delete(Project).where(Project.id == context["proj_a_id"]))
        await db.execute(delete(Owner).where(Owner.id.in_([
            context["owner_a1_id"],
            context["owner_a2_id"],
            context["owner_a3_id"],
            context["owner_b1_id"],
        ])))
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
async def test_owner_ledger_csv_export_headers_and_sequential_numbering(setup_test_context):
    """Verify CSV export has 'Sr. No.' as the first column and sequential row numbers."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(f"/api/v1/owners/{ctx['owner_a1_id']}/ledger/excel", headers=headers)
        assert res.status_code == 200
        assert "text/csv" in res.headers.get("content-type", "")
        assert f"attachment; filename=owner_ledger_{ctx['owner_a1_id']}.csv" in res.headers.get("content-disposition", "")

        csv_text = res.content.decode("utf-8")
        reader = list(csv.reader(io.StringIO(csv_text)))

        # 1. Header validation
        assert len(reader) >= 4  # 1 header + 3 data rows
        header_row = reader[0]
        expected_header = ["Sr. No.", "Date", "Type", "Amount", "Reference Type", "Reference ID", "Description"]
        assert header_row == expected_header
        assert header_row[0] == "Sr. No."
        assert header_row[1] == "Date"

        # 2. Rows are ordered by created_at DESC (2026-03-10, 2026-02-20, 2026-01-15)
        # Sequential serial numbers must be 1, 2, 3
        row1 = reader[1]
        row2 = reader[2]
        row3 = reader[3]

        assert row1[0] == "1"
        assert row1[1] == "2026-03-10 09:15:00"
        assert row1[2] == "credit"
        assert row1[3] == "75000.00"
        assert row1[4] == "DIRECT"
        assert row1[5] == "103"
        assert row1[6] == "Second equity tranche"

        assert row2[0] == "2"
        assert row2[1] == "2026-02-20 14:00:00"
        assert row2[2] == "debit"
        assert row2[3] == "50000.00"
        assert row2[4] == "EXPENSE"
        assert row2[5] == "102"
        assert row2[6] == "Drawings withdrawal"

        assert row3[0] == "3"
        assert row3[1] == "2026-01-15 10:30:00"
        assert row3[2] == "credit"
        assert row3[3] == "150000.00"
        assert row3[4] == "DIRECT"
        assert row3[5] == "101"
        assert row3[6] == "Initial capital contribution"


@pytest.mark.asyncio
async def test_owner_ledger_csv_export_single_transaction(setup_test_context):
    """Verify CSV export for a single transaction has serial number 1."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(f"/api/v1/owners/{ctx['owner_a2_id']}/ledger/excel", headers=headers)
        assert res.status_code == 200

        csv_text = res.content.decode("utf-8")
        reader = list(csv.reader(io.StringIO(csv_text)))

        assert len(reader) == 2  # 1 header + 1 row
        assert reader[0][0] == "Sr. No."
        assert reader[1][0] == "1"
        assert reader[1][3] == "25000.00"


@pytest.mark.asyncio
async def test_owner_ledger_csv_export_empty_ledger_422(setup_test_context):
    """Verify CSV export returns HTTP 422 when there are no ledger transactions."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(f"/api/v1/owners/{ctx['owner_a3_id']}/ledger/excel", headers=headers)
        assert res.status_code == 422
        body = res.json()
        assert "No ledger data available to export for this owner." in body["detail"]


@pytest.mark.asyncio
async def test_owner_ledger_pdf_export_headers_and_sequential_numbering(setup_test_context):
    """Verify PDF export includes 'Sr. No.' column and sequential serial numbers."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(f"/api/v1/owners/{ctx['owner_a1_id']}/ledger/pdf", headers=headers)
        assert res.status_code == 200
        assert "application/pdf" in res.headers.get("content-type", "")
        assert f"attachment; filename=owner_ledger_{ctx['owner_a1_id']}.pdf" in res.headers.get("content-disposition", "")
        assert len(res.content) > 0

        # Parse generated PDF using pypdf
        reader = pypdf.PdfReader(io.BytesIO(res.content))
        assert len(reader.pages) >= 1
        page_text = reader.pages[0].extract_text()

        # Check headers and row elements present in the rendered document
        assert "Sr. No." in page_text
        assert "Date" in page_text
        assert "Type" in page_text
        assert "Amount" in page_text
        assert "Reference" in page_text
        assert "Description" in page_text

        # Check serial numbers and transaction descriptions
        assert "Second equity tranche" in page_text
        assert "Drawings withdrawal" in page_text
        assert "Initial capital contribution" in page_text


@pytest.mark.asyncio
async def test_owner_ledger_pdf_export_single_transaction(setup_test_context):
    """Verify PDF export for a single transaction succeeds and renders properly."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(f"/api/v1/owners/{ctx['owner_a2_id']}/ledger/pdf", headers=headers)
        assert res.status_code == 200
        assert "application/pdf" in res.headers.get("content-type", "")

        reader = pypdf.PdfReader(io.BytesIO(res.content))
        page_text = reader.pages[0].extract_text()
        assert "Sr. No." in page_text
        assert "Single milestone contribution" in page_text


@pytest.mark.asyncio
async def test_owner_ledger_pdf_export_empty_ledger_422(setup_test_context):
    """Verify PDF export returns HTTP 422 when there are no ledger transactions."""
    ctx = setup_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(f"/api/v1/owners/{ctx['owner_a3_id']}/ledger/pdf", headers=headers)
        assert res.status_code == 422
        body = res.json()
        assert "No ledger data available to export for this owner." in body["detail"]


@pytest.mark.asyncio
async def test_owner_ledger_export_tenant_isolation(setup_test_context):
    """Verify tenant boundary: Admin B cannot export Owner A's ledger (CSV or PDF)."""
    ctx = setup_test_context
    headers_b = {"Authorization": f"Bearer {ctx['token_b']}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Cross-tenant CSV attempt
        res_csv = await ac.get(f"/api/v1/owners/{ctx['owner_a1_id']}/ledger/excel", headers=headers_b)
        assert res_csv.status_code == 404
        assert res_csv.json()["detail"] == "Owner not found"

        # Cross-tenant PDF attempt
        res_pdf = await ac.get(f"/api/v1/owners/{ctx['owner_a1_id']}/ledger/pdf", headers=headers_b)
        assert res_pdf.status_code == 404
        assert res_pdf.json()["detail"] == "Owner not found"
