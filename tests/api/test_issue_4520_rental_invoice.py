import os
import uuid
from datetime import date, timedelta
from decimal import Decimal
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.user import User, UserRole
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.equipment import Equipment, EquipmentRental
from app.models.invoice import Invoice, InvoiceType, InvoiceStatus, Transaction
from app.models.accountant import JournalEntry, JournalLine
from app.core.enums import EquipmentStatus, RentalBillingMode
from app.core.dependencies import (
    get_current_user,
    get_current_active_user,
    require_permission,
)


@pytest_asyncio.fixture
async def setup_rental_invoice_context():
    run_uid = uuid.uuid4().hex[:6]

    async with AsyncSessionLocal() as db:
        # 1. Companies
        comp_a = Company(name=f"CompA_4520_{run_uid}", is_active=True)
        comp_b = Company(name=f"CompB_4520_{run_uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Users
        admin_a = User(
            company_id=comp_a.id,
            email=f"admin_a_4520_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        user_b = User(
            company_id=comp_b.id,
            email=f"user_b_4520_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        user_no_perms = User(
            company_id=comp_a.id,
            email=f"noperm_4520_{run_uid}@test.com",
            role="ViewerWithoutPerms",
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([admin_a, user_b, user_no_perms])
        await db.flush()

        # 3. Owners & Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN4520A-{run_uid}",
            owner_name="Owner A 4520",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN4520B-{run_uid}",
            owner_name="Owner B 4520",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"ProjA_4520_{run_uid}",
            business_id=f"P4520A-{run_uid}",
            status="Ongoing",
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"ProjB_4520_{run_uid}",
            business_id=f"P4520B-{run_uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Project memberships
        db.add_all([
            ProjectMember(project_id=proj_a.id, user_id=admin_a.id),
            ProjectMember(project_id=proj_b.id, user_id=user_b.id),
        ])
        await db.flush()

        # 4. Equipment
        eq_a1 = Equipment(
            company_id=comp_a.id,
            project_id=proj_a.id,
            equipment_name=f"Excavator A1 {run_uid}",
            equipment_code=f"EQA1-{run_uid}",
            status=EquipmentStatus.IDLE,
        )
        eq_a2 = Equipment(
            company_id=comp_a.id,
            project_id=proj_a.id,
            equipment_name=f"Loader A2 {run_uid}",
            equipment_code=f"EQA2-{run_uid}",
            status=EquipmentStatus.IDLE,
        )
        eq_b = Equipment(
            company_id=comp_b.id,
            project_id=proj_b.id,
            equipment_name=f"Crane B {run_uid}",
            equipment_code=f"EQB-{run_uid}",
            status=EquipmentStatus.IDLE,
        )
        db.add_all([eq_a1, eq_a2, eq_b])
        await db.flush()

        # 5. Rentals
        # Completed rental on eq_a1 ready for invoicing
        rental_completed = EquipmentRental(
            equipment_id=eq_a1.id,
            project_id=proj_a.id,
            start_date=date.today() - timedelta(days=5),
            end_date=date.today(),
            rental_cost=Decimal("2000.00"),
            billing_mode=RentalBillingMode.PER_DAY,
            client_name="Alpha Infrastructure",
            is_completed=True,
            invoice_id=None,
        )
        # Another completed rental without invoice (to test GET before POST)
        rental_no_invoice = EquipmentRental(
            equipment_id=eq_a1.id,
            project_id=proj_a.id,
            start_date=date.today() - timedelta(days=3),
            end_date=date.today(),
            rental_cost=Decimal("1500.00"),
            billing_mode=RentalBillingMode.LUMP_SUM,
            client_name="Beta Construction",
            is_completed=True,
            invoice_id=None,
        )
        # Rental on eq_b (Company B)
        rental_b = EquipmentRental(
            equipment_id=eq_b.id,
            project_id=proj_b.id,
            start_date=date.today() - timedelta(days=2),
            end_date=date.today(),
            rental_cost=Decimal("3000.00"),
            billing_mode=RentalBillingMode.PER_DAY,
            client_name="Gamma Industries",
            is_completed=True,
            invoice_id=None,
        )
        db.add_all([rental_completed, rental_no_invoice, rental_b])
        await db.commit()

        context = {
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "admin_a": admin_a,
            "user_b": user_b,
            "user_no_perms": user_no_perms,
            "proj_a_id": proj_a.id,
            "proj_b_id": proj_b.id,
            "eq_a1_id": eq_a1.id,
            "eq_a2_id": eq_a2.id,
            "eq_b_id": eq_b.id,
            "rental_completed_id": rental_completed.id,
            "rental_no_invoice_id": rental_no_invoice.id,
            "rental_b_id": rental_b.id,
        }

    # Set default authentication as Admin A with equipment permissions
    app.dependency_overrides[get_current_user] = lambda: context["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: context["admin_a"]
    app.dependency_overrides[require_permission("equipment.edit")] = lambda: context["admin_a"]
    app.dependency_overrides[require_permission("equipment.view")] = lambda: context["admin_a"]
    app.dependency_overrides[require_permission("invoices.export")] = lambda: context["admin_a"]

    yield context

    app.dependency_overrides.clear()
    async with AsyncSessionLocal() as db:
        # Find generated invoice IDs to delete cleanly
        invoices = (await db.scalars(select(Invoice).where(Invoice.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))).all()
        for inv in invoices:
            await db.delete(inv)
        await db.execute(EquipmentRental.__table__.delete().where(EquipmentRental.id.in_([
            context["rental_completed_id"],
            context["rental_no_invoice_id"],
            context["rental_b_id"],
        ])))
        await db.execute(Equipment.__table__.delete().where(Equipment.id.in_([
            context["eq_a1_id"],
            context["eq_a2_id"],
            context["eq_b_id"],
        ])))
        await db.execute(ProjectMember.__table__.delete().where(ProjectMember.project_id.in_([context["proj_a_id"], context["proj_b_id"]])))
        await db.execute(Project.__table__.delete().where(Project.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.execute(Owner.__table__.delete().where(Owner.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.execute(User.__table__.delete().where(User.company_id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.execute(Company.__table__.delete().where(Company.id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.commit()


# =============================================================================
# TESTS FOR ISSUE #4520: RENTAL INVOICE GENERATION, RETRIEVAL, AND ISOLATION
# =============================================================================


@pytest.mark.asyncio
async def test_01_first_post_generates_invoice_successfully(setup_rental_invoice_context):
    """1. First POST generates an invoice successfully and links rental.invoice_id."""
    ctx = setup_rental_invoice_context
    eq_id = ctx["eq_a1_id"]
    rental_id = ctx["rental_completed_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") == "success"
        invoice_id = body.get("invoice_id")
        assert invoice_id is not None

        # Verify DB linkage
        async with AsyncSessionLocal() as db:
            rental = await db.get(EquipmentRental, rental_id)
            assert rental.invoice_id == invoice_id

            invoice = await db.get(Invoice, invoice_id)
            assert invoice is not None
            assert invoice.type == InvoiceType.RENTAL
            assert invoice.company_id == ctx["comp_a_id"]
            assert invoice.project_id == ctx["proj_a_id"]


@pytest.mark.asyncio
async def test_02_second_post_does_not_generate_duplicate_invoice(setup_rental_invoice_context):
    """2. Second POST does not generate another invoice; rejects with HTTP 400 duplicate error."""
    ctx = setup_rental_invoice_context
    eq_id = ctx["eq_a1_id"]
    rental_id = ctx["rental_completed_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # First POST generates
        resp1 = await ac.post(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
        assert resp1.status_code == 200
        first_invoice_id = resp1.json()["invoice_id"]

        # Count invoices in DB before second POST
        async with AsyncSessionLocal() as db:
            count_before = await db.scalar(
                select(func.count(Invoice.id)).where(Invoice.project_id == ctx["proj_a_id"])
            )

        # Second POST must be rejected with 400
        resp2 = await ac.post(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
        assert resp2.status_code == 400
        assert "Invoice already generated for this rental." in resp2.json()["detail"]

        # Count invoices in DB after second POST must remain unchanged
        async with AsyncSessionLocal() as db:
            count_after = await db.scalar(
                select(func.count(Invoice.id)).where(Invoice.project_id == ctx["proj_a_id"])
            )
            assert count_after == count_before

            # Rental invoice_id must still point to first invoice
            rental = await db.get(EquipmentRental, rental_id)
            assert rental.invoice_id == first_invoice_id


@pytest.mark.asyncio
async def test_03_get_retrieves_existing_generated_invoice_pdf(setup_rental_invoice_context):
    """3. GET retrieves the existing generated invoice PDF successfully."""
    ctx = setup_rental_invoice_context
    eq_id = ctx["eq_a1_id"]
    rental_id = ctx["rental_completed_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Generate invoice via POST
        gen_resp = await ac.post(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
        assert gen_resp.status_code == 200
        invoice_id = gen_resp.json()["invoice_id"]

        # GET invoice PDF via dedicated endpoint
        get_resp = await ac.get(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
        assert get_resp.status_code == 200
        assert get_resp.headers.get("content-type") == "application/pdf"
        assert f"invoice_{invoice_id}.pdf" in get_resp.headers.get("content-disposition", "")
        assert get_resp.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_04_get_does_not_create_or_modify_invoice_records(setup_rental_invoice_context):
    """4. GET does not create or modify invoice records in database."""
    ctx = setup_rental_invoice_context
    eq_id = ctx["eq_a1_id"]
    rental_id = ctx["rental_completed_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Generate invoice
        await ac.post(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")

        # Snapshot invoice before GET
        async with AsyncSessionLocal() as db:
            total_inv_before = await db.scalar(select(func.count(Invoice.id)))
            rental = await db.get(EquipmentRental, rental_id)
            inv_before = await db.get(Invoice, rental.invoice_id)
            before_amount = inv_before.amount
            before_status = inv_before.status
            before_updated_at = inv_before.updated_at

        # Call GET multiple times
        for _ in range(3):
            get_resp = await ac.get(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
            assert get_resp.status_code == 200

        # Snapshot invoice after GETs
        async with AsyncSessionLocal() as db:
            total_inv_after = await db.scalar(select(func.count(Invoice.id)))
            inv_after = await db.get(Invoice, rental.invoice_id)
            assert total_inv_after == total_inv_before
            assert inv_after.amount == before_amount
            assert inv_after.status == before_status
            assert inv_after.updated_at == before_updated_at


@pytest.mark.asyncio
async def test_05_get_does_not_create_duplicate_financial_transactions(setup_rental_invoice_context):
    """5. GET does not create duplicate financial transactions or journal entries."""
    ctx = setup_rental_invoice_context
    eq_id = ctx["eq_a1_id"]
    rental_id = ctx["rental_completed_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Generate invoice
        await ac.post(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")

        # Count transactions & journal entries before GET
        async with AsyncSessionLocal() as db:
            tx_count_before = await db.scalar(select(func.count(Transaction.id)))
            je_count_before = await db.scalar(select(func.count(JournalEntry.id)))
            jl_count_before = await db.scalar(select(func.count(JournalLine.id)))

        # Invoke GET
        get_resp = await ac.get(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
        assert get_resp.status_code == 200

        # Verify counts remain identical
        async with AsyncSessionLocal() as db:
            tx_count_after = await db.scalar(select(func.count(Transaction.id)))
            je_count_after = await db.scalar(select(func.count(JournalEntry.id)))
            jl_count_after = await db.scalar(select(func.count(JournalLine.id)))
            assert tx_count_after == tx_count_before
            assert je_count_after == je_count_before
            assert jl_count_after == jl_count_before


@pytest.mark.asyncio
async def test_06_get_with_no_generated_invoice_returns_404(setup_rental_invoice_context):
    """6. GET with no generated invoice returns HTTP 404 with appropriate detail."""
    ctx = setup_rental_invoice_context
    eq_id = ctx["eq_a1_id"]
    rental_id = ctx["rental_no_invoice_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
        assert resp.status_code == 404
        assert "Invoice not generated for this rental" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_07_cross_tenant_access_is_denied(setup_rental_invoice_context):
    """7. Cross-tenant access is denied (User B cannot access Company A's rental invoice)."""
    ctx = setup_rental_invoice_context
    eq_a_id = ctx["eq_a1_id"]
    rental_a_id = ctx["rental_completed_id"]

    # First, Admin A generates invoice for rental_a
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        gen_resp = await ac.post(f"/api/v1/equipment/{eq_a_id}/rental-out/{rental_a_id}/invoice")
        assert gen_resp.status_code == 200

    # Switch authentication to User B (Company B)
    app.dependency_overrides[get_current_user] = lambda: ctx["user_b"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["user_b"]
    app.dependency_overrides[require_permission("equipment.edit")] = lambda: ctx["user_b"]
    app.dependency_overrides[require_permission("equipment.view")] = lambda: ctx["user_b"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Cross-tenant GET must return 404 (masked)
        get_resp = await ac.get(f"/api/v1/equipment/{eq_a_id}/rental-out/{rental_a_id}/invoice")
        assert get_resp.status_code == 404
        assert "Equipment not found" in get_resp.json()["detail"]

        # Cross-tenant POST must return 404 (masked)
        post_resp = await ac.post(f"/api/v1/equipment/{eq_a_id}/rental-out/{rental_a_id}/invoice")
        assert post_resp.status_code == 404
        assert "Equipment not found" in post_resp.json()["detail"]


@pytest.mark.asyncio
async def test_08_rental_belonging_to_another_equipment_id_is_denied(setup_rental_invoice_context):
    """8. Rental belonging to another equipment ID is denied with 404."""
    ctx = setup_rental_invoice_context
    eq_a1_id = ctx["eq_a1_id"]
    eq_a2_id = ctx["eq_a2_id"]  # different equipment in same company
    rental_a1_id = ctx["rental_completed_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Generate invoice on correct equipment
        await ac.post(f"/api/v1/equipment/{eq_a1_id}/rental-out/{rental_a1_id}/invoice")

        # Request with mismatched equipment_id (eq_a2 with rental belonging to eq_a1)
        mismatch_get = await ac.get(f"/api/v1/equipment/{eq_a2_id}/rental-out/{rental_a1_id}/invoice")
        assert mismatch_get.status_code == 404
        assert "Rental not found" in mismatch_get.json()["detail"]

        # Nonexistent equipment_id
        nonexistent_get = await ac.get(f"/api/v1/equipment/999999/rental-out/{rental_a1_id}/invoice")
        assert nonexistent_get.status_code == 404
        assert "Equipment not found" in nonexistent_get.json()["detail"]


@pytest.mark.asyncio
async def test_09_unauthorized_users_cannot_download_invoice(setup_rental_invoice_context):
    """9. Unauthorized users without equipment.view cannot download the invoice (403 Forbidden)."""
    ctx = setup_rental_invoice_context
    eq_id = ctx["eq_a1_id"]
    rental_id = ctx["rental_completed_id"]

    # Generate invoice with Admin A first
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")

    # Override with unauthorized user lacking equipment.view
    app.dependency_overrides.clear()
    app.dependency_overrides[get_current_user] = lambda: ctx["user_no_perms"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["user_no_perms"]
    # Do not override require_permission; let standard dependency enforce DB permissions

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        unauth_resp = await ac.get(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
        assert unauth_resp.status_code == 403


@pytest.mark.asyncio
async def test_10_existing_central_invoice_pdf_endpoint_remains_functional(setup_rental_invoice_context):
    """10. Existing central endpoint GET /api/v1/invoices/{id}/pdf remains functional."""
    ctx = setup_rental_invoice_context
    eq_id = ctx["eq_a1_id"]
    rental_id = ctx["rental_completed_id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Generate invoice via rental workflow
        gen_resp = await ac.post(f"/api/v1/equipment/{eq_id}/rental-out/{rental_id}/invoice")
        assert gen_resp.status_code == 200
        invoice_id = gen_resp.json()["invoice_id"]

        # Central invoice PDF endpoint
        central_resp = await ac.get(f"/api/v1/invoices/{invoice_id}/pdf")
        assert central_resp.status_code == 200
        assert central_resp.headers.get("content-type") == "application/pdf"
        assert central_resp.content.startswith(b"%PDF-")
        assert f"invoice_{invoice_id}.pdf" in central_resp.headers.get("content-disposition", "")
