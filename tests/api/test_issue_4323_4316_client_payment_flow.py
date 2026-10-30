import pytest
import pytest_asyncio
import uuid
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.invoice import Invoice
from app.models.user import User, UserRole
from app.models.rbac import Permission, UserPermissionOverride
from app.core.enums import InvoiceStatus, InvoiceType, PaymentMethod
from app.core.dependencies import get_current_user, get_current_active_user


@pytest_asyncio.fixture
async def setup_payment_flow_context():
    uid = uuid.uuid4().hex[:6]
    rand_int = uuid.uuid4().int % 100000000

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_Pay_{uid}", is_active=True)
        db.add(comp)
        await db.flush()

        owner_a = Owner(
            company_id=comp.id,
            owner_code=f"OA_Pay_{uid}",
            owner_name=f"OwnerA_{uid}",
            mobile=f"91{rand_int:08d}",
        )
        owner_b = Owner(
            company_id=comp.id,
            owner_code=f"OB_Pay_{uid}",
            owner_name=f"OwnerB_{uid}",
            mobile=f"92{rand_int:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        client_a = User(
            email=f"clia_pay_{uid}@test.com",
            mobile=f"93{rand_int:08d}",
            role=UserRole.CLIENT.value,
            is_active=True,
            company_id=comp.id,
        )
        accountant = User(
            email=f"acc_pay_{uid}@test.com",
            mobile=f"94{rand_int:08d}",
            role=UserRole.ACCOUNTANT.value,
            is_active=True,
            company_id=comp.id,
        )
        db.add_all([client_a, accountant])
        await db.flush()

        # Grant permissions to client_a and accountant
        for p_code in ["invoices.view", "invoices.create", "projects.view"]:
            p = await db.scalar(select(Permission).where(Permission.code == p_code))
            if p:
                db.add_all([
                    UserPermissionOverride(user_id=client_a.id, permission_id=p.id, is_granted=True),
                    UserPermissionOverride(user_id=accountant.id, permission_id=p.id, is_granted=True),
                ])

        proj_a = Project(
            company_id=comp.id,
            owner_id=owner_a.id,
            project_name=f"ProjectA_{uid}",
            business_id=f"PA-Pay-{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            company_id=comp.id,
            owner_id=owner_b.id,
            project_name=f"ProjectB_{uid}",
            business_id=f"PB-Pay-{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Assign client_a to proj_a
        db.add(ProjectMember(project_id=proj_a.id, user_id=client_a.id))

        inv_a = Invoice(
            company_id=comp.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type=InvoiceType.OWNER,
            invoice_number=f"INV-A-{uid}",
            amount=Decimal("1500.00"),
            total_amount=Decimal("1500.00"),
            paid_amount=Decimal("0.00"),
            pending_amount=Decimal("1500.00"),
            status=InvoiceStatus.PENDING,
        )
        inv_b = Invoice(
            company_id=comp.id,
            project_id=proj_b.id,
            owner_id=owner_b.id,
            type=InvoiceType.OWNER,
            invoice_number=f"INV-B-{uid}",
            amount=Decimal("2500.00"),
            total_amount=Decimal("2500.00"),
            paid_amount=Decimal("0.00"),
            pending_amount=Decimal("2500.00"),
            status=InvoiceStatus.PENDING,
        )
        db.add_all([inv_a, inv_b])
        await db.commit()

        p_a_id = proj_a.id
        p_b_id = proj_b.id
        i_a_id = inv_a.id
        i_b_id = inv_b.id

    return {
        "client_a": client_a,
        "accountant": accountant,
        "p_a_id": p_a_id,
        "p_b_id": p_b_id,
        "i_a_id": i_a_id,
        "i_b_id": i_b_id,
    }


@pytest.mark.asyncio
async def test_valid_project_and_invoice_payment_success(setup_payment_flow_context):
    """
    Issue #4323: Selecting a valid project and invoice belonging to that project
    must succeed and allow payment creation without 404.
    """
    ctx = setup_payment_flow_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["client_a"]
    app.dependency_overrides[get_current_user] = lambda: ctx["client_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET pending invoices for project A
        r_inv = await client.get(f"/api/v1/client-payments/pending-invoices?project_id={ctx['p_a_id']}")
        assert r_inv.status_code == 200, f"Expected 200, got {r_inv.status_code}: {r_inv.text}"
        items = r_inv.json().get("items", [])
        assert len(items) == 1
        assert items[0]["invoice_id"] == ctx["i_a_id"]

        # 2. Pay payment for project A and invoice A
        r_pay = await client.post("/api/v1/client-payments", data={
            "project_id": str(ctx["p_a_id"]),
            "invoice_id": str(ctx["i_a_id"]),
            "amount": "500.00",
            "payment_method": "CASH",
        })
        assert r_pay.status_code == 201, f"Expected 201, got {r_pay.status_code}: {r_pay.text}"
        data = r_pay.json()
        assert data["project_id"] == ctx["p_a_id"]
        assert data["invoice_id"] == ctx["i_a_id"]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_accountant_payment_access(setup_payment_flow_context):
    """
    Issue #4323: Company Accountant accessing client payment endpoints for company projects
    must not receive 404 Project Not Found.
    """
    ctx = setup_payment_flow_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["accountant"]
    app.dependency_overrides[get_current_user] = lambda: ctx["accountant"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_inv = await client.get(f"/api/v1/client-payments/pending-invoices?project_id={ctx['p_a_id']}")
        assert r_inv.status_code == 200, f"Expected 200, got {r_inv.status_code}: {r_inv.text}"

        r_hist = await client.get(f"/api/v1/client-payments/history?project_id={ctx['p_a_id']}")
        assert r_hist.status_code == 200, f"Expected 200, got {r_hist.status_code}: {r_hist.text}"

        r_sum = await client.get(f"/api/v1/client-payments/invoice-summary?project_id={ctx['p_a_id']}")
        assert r_sum.status_code == 200, f"Expected 200, got {r_sum.status_code}: {r_sum.text}"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mismatched_invoice_error_message(setup_payment_flow_context):
    """
    Issue #4316: When the selected invoice does not belong to the selected project,
    the API must return exact detail: "Selected invoice does not belong to this project."
    """
    ctx = setup_payment_flow_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["client_a"]
    app.dependency_overrides[get_current_user] = lambda: ctx["client_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Project A with Invoice B (which belongs to Project B)
        res = await client.post("/api/v1/client-payments", data={
            "project_id": str(ctx["p_a_id"]),
            "invoice_id": str(ctx["i_b_id"]),
            "amount": "500.00",
            "payment_method": "CASH",
        })
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
        assert res.json().get("detail") == "Selected invoice does not belong to this project."

    app.dependency_overrides.clear()
