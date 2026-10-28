import pytest
import pytest_asyncio
import uuid
import asyncio
from decimal import Decimal
from datetime import date
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner, OwnerTransaction
from app.models.project import Project
from app.models.user import User, UserRole
from app.models.expense import Expense
from app.models.invoice import Invoice, Transaction
from app.models.alert import Alert
from app.models.accountant import JournalEntry, JournalLine
from app.core.enums import InvoiceStatus, InvoiceType, PaymentMode
from app.core.dependencies import get_current_user, get_current_active_user


@pytest.fixture
def run_uid():
    return uuid.uuid4().hex[:6]


@pytest_asyncio.fixture
async def setup_test_context(run_uid):
    """Create isolated test company, owner, project, and admin user."""
    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_4210_{run_uid}", is_active=True)
        db.add(comp)
        await db.flush()

        owner = Owner(
            company_id=comp.id,
            owner_code=f"OWN-{run_uid}",
            owner_name=f"Owner {run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_{run_uid}@test.com",
        )
        db.add(owner)
        await db.flush()

        proj = Project(
            company_id=comp.id,
            owner_id=owner.id,
            project_name=f"Project 4210 {run_uid}",
            business_id=f"PRJ-{run_uid}",
            status="Ongoing",
        )
        db.add(proj)
        await db.flush()

        admin_user = User(
            company_id=comp.id,
            email=f"admin_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=True,
        )
        db.add(admin_user)

        # Cross company user for tenant isolation tests
        comp_b = Company(name=f"CompB_4210_{run_uid}", is_active=True)
        db.add(comp_b)
        await db.flush()

        user_b = User(
            company_id=comp_b.id,
            email=f"user_b_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        db.add(user_b)

        await db.commit()
        await db.refresh(comp)
        await db.refresh(owner)
        await db.refresh(proj)
        await db.refresh(admin_user)
        await db.refresh(comp_b)
        await db.refresh(user_b)

    context = {
        "company_id": comp.id,
        "owner_id": owner.id,
        "project_id": proj.id,
        "admin_user": admin_user,
        "company_b_id": comp_b.id,
        "user_b": user_b,
    }

    # Set dependency override to admin_user
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_current_active_user] = lambda: admin_user

    yield context

    app.dependency_overrides.clear()

    # Teardown
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Alert).where(Alert.project_id == proj.id))
        await db.execute(delete(Transaction).where(Transaction.project_id == proj.id))
        await db.execute(delete(Invoice).where(Invoice.project_id == proj.id))

        expenses = (await db.scalars(select(Expense).where(Expense.project_id == proj.id))).all()
        for e in expenses:
            journals = (await db.scalars(select(JournalEntry).where(
                JournalEntry.journal_number.like(f"%J-EXP-{e.id}%")
            ))).all()
            for j in journals:
                await db.execute(delete(JournalLine).where(JournalLine.entry_id == j.id))
                await db.delete(j)
            await db.execute(delete(OwnerTransaction).where(OwnerTransaction.reference_id == e.id))
            await db.delete(e)

        await db.execute(delete(Project).where(Project.id == proj.id))
        await db.execute(delete(Owner).where(Owner.id == owner.id))
        await db.execute(delete(Alert).where(Alert.user_id.in_([admin_user.id, user_b.id])))
        await db.execute(delete(User).where(User.id.in_([admin_user.id, user_b.id])))
        await db.execute(delete(Company).where(Company.id.in_([comp.id, comp_b.id])))
        await db.commit()


# ==============================================================================
# Helper functions
# ==============================================================================

async def create_expense_api(
    ac: AsyncClient,
    project_id: int,
    amount: Decimal = Decimal("1000.00"),
    description: str = "Test Expense",
) -> int:
    res = await ac.post("/api/v1/expenses", json={
        "project_id": project_id,
        "category": "Material",
        "description": description,
        "amount": float(amount),
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    })
    assert res.status_code == 200, res.text
    return res.json()["id"]


async def create_invoice(
    company_id: int,
    project_id: int,
    owner_id: int,
    linked_expense_ids: list[int] | None,
    total_amount: Decimal = Decimal("1000.00"),
    status: InvoiceStatus = InvoiceStatus.PENDING,
    paid_amount: Decimal = Decimal("0.00"),
) -> Invoice:
    async with AsyncSessionLocal() as db:
        inv = Invoice(
            company_id=company_id,
            project_id=project_id,
            owner_id=owner_id,
            type=InvoiceType.MATERIAL,
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            amount=total_amount,
            gst_percent=Decimal(0),
            gst_amount=Decimal(0),
            tax_percent=Decimal(0),
            tax_amount=Decimal(0),
            total_amount=total_amount,
            paid_amount=paid_amount,
            pending_amount=total_amount - paid_amount,
            status=status,
            linked_expense_ids=linked_expense_ids,
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)
        return inv


# ==============================================================================
# Test Cases
# ==============================================================================

@pytest.mark.asyncio
async def test_expense_edit_unlinked_allowed(setup_test_context):
    """Expense without any linked invoice can be updated."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("5000.00"))

        res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
            "amount": 7500.00,
            "description": "Updated unlinked expense",
        })
        assert res.status_code == 200, res.text
        assert float(res.json()["amount"]) == 7500.0
        assert res.json()["description"] == "Updated unlinked expense"


@pytest.mark.asyncio
async def test_expense_edit_pending_invoice_allowed(setup_test_context):
    """Expense linked to PENDING invoice can be updated."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("4000.00"))
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            status=InvoiceStatus.PENDING,
        )

        res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
            "amount": 4500.00,
            "description": "Updated pending expense",
        })
        assert res.status_code == 200, res.text
        assert float(res.json()["amount"]) == 4500.0


@pytest.mark.asyncio
async def test_expense_edit_paid_invoice_rejected(setup_test_context):
    """Expense linked to PAID invoice cannot be updated (amount or description)."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("6000.00"), description="Original Desc")
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("6000.00"),
            paid_amount=Decimal("6000.00"),
            status=InvoiceStatus.PAID,
        )

        # Attempt amount edit
        res_amt = await ac.put(f"/api/v1/expenses/{exp_id}", json={"amount": 9000.00})
        assert res_amt.status_code == 400, res_amt.text
        assert "Expense cannot be modified or deleted because its associated invoice has received payment." in res_amt.text

        # Attempt description-only edit
        res_desc = await ac.put(f"/api/v1/expenses/{exp_id}", json={"description": "Tampered Desc"})
        assert res_desc.status_code == 400, res_desc.text
        assert "Expense cannot be modified or deleted because its associated invoice has received payment." in res_desc.text

    # Verify DB state untouched
    async with AsyncSessionLocal() as db:
        refreshed = await db.get(Expense, exp_id)
        assert refreshed.amount == Decimal("6000.00")
        assert refreshed.description == "Original Desc"


@pytest.mark.asyncio
async def test_expense_edit_partial_invoice_rejected(setup_test_context):
    """Expense linked to PARTIAL invoice cannot be updated."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("10000.00"))
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("10000.00"),
            paid_amount=Decimal("3000.00"),
            status=InvoiceStatus.PARTIAL,
        )

        res = await ac.put(f"/api/v1/expenses/{exp_id}", json={"amount": 12000.00})
        assert res.status_code == 400, res.text
        assert "Expense cannot be modified or deleted because its associated invoice has received payment." in res.text

    async with AsyncSessionLocal() as db:
        refreshed = await db.get(Expense, exp_id)
        assert refreshed.amount == Decimal("10000.00")


@pytest.mark.asyncio
async def test_expense_delete_unlinked_allowed(setup_test_context):
    """Unlinked expense can be deleted."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("2000.00"))

        res = await ac.delete(f"/api/v1/expenses/{exp_id}")
        assert res.status_code == 204

    async with AsyncSessionLocal() as db:
        refreshed = await db.get(Expense, exp_id)
        assert refreshed is None


@pytest.mark.asyncio
async def test_expense_delete_pending_invoice_allowed(setup_test_context):
    """Expense linked to PENDING invoice can be deleted."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("2500.00"))
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            status=InvoiceStatus.PENDING,
        )

        res = await ac.delete(f"/api/v1/expenses/{exp_id}")
        assert res.status_code == 204

    async with AsyncSessionLocal() as db:
        refreshed = await db.get(Expense, exp_id)
        assert refreshed is None


@pytest.mark.asyncio
async def test_expense_delete_paid_invoice_rejected(setup_test_context):
    """Expense linked to PAID invoice cannot be deleted."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("8000.00"))
        inv = await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("8000.00"),
            paid_amount=Decimal("8000.00"),
            status=InvoiceStatus.PAID,
        )

        res = await ac.delete(f"/api/v1/expenses/{exp_id}")
        assert res.status_code == 400, res.text
        assert "Expense cannot be modified or deleted because its associated invoice has received payment." in res.text

    # Verify expense remains intact
    async with AsyncSessionLocal() as db:
        refreshed_exp = await db.get(Expense, exp_id)
        assert refreshed_exp is not None
        refreshed_inv = await db.get(Invoice, inv.id)
        assert refreshed_inv.linked_expense_ids == [exp_id]


@pytest.mark.asyncio
async def test_expense_delete_partial_invoice_rejected(setup_test_context):
    """Expense linked to PARTIAL invoice cannot be deleted."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("7000.00"))
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("7000.00"),
            paid_amount=Decimal("2000.00"),
            status=InvoiceStatus.PARTIAL,
        )

        res = await ac.delete(f"/api/v1/expenses/{exp_id}")
        assert res.status_code == 400, res.text
        assert "Expense cannot be modified or deleted because its associated invoice has received payment." in res.text

    async with AsyncSessionLocal() as db:
        refreshed_exp = await db.get(Expense, exp_id)
        assert refreshed_exp is not None


@pytest.mark.asyncio
async def test_invoice_mark_paid_workflow_locks_expense(setup_test_context):
    """Payment via POST /invoices/{id}/mark-paid transitions to PAID and locks expense."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("15000.00"))
        inv = await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("15000.00"),
            status=InvoiceStatus.PENDING,
        )

        # Before mark-paid: edit allowed
        res_before = await ac.put(f"/api/v1/expenses/{exp_id}", json={"amount": 15000.00})
        assert res_before.status_code == 200

        # Mark invoice as paid
        res_paid = await ac.post(f"/api/v1/invoices/{inv.id}/mark-paid")
        assert res_paid.status_code == 200, res_paid.text
        assert res_paid.json()["status"] == InvoiceStatus.PAID.value

        # After mark-paid: edit rejected
        res_after_edit = await ac.put(f"/api/v1/expenses/{exp_id}", json={"amount": 16000.00})
        assert res_after_edit.status_code == 400
        assert "Expense cannot be modified or deleted because its associated invoice has received payment." in res_after_edit.text

        # After mark-paid: delete rejected
        res_after_del = await ac.delete(f"/api/v1/expenses/{exp_id}")
        assert res_after_del.status_code == 400


@pytest.mark.asyncio
async def test_invoice_pay_workflow_partial_and_full_locks_expense(setup_test_context):
    """Payment via POST /invoices/{id}/pay transitions to PARTIAL then PAID, locking expense at both stages."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("20000.00"))
        inv = await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("20000.00"),
            status=InvoiceStatus.PENDING,
        )

        # Partial payment: 5000 out of 20000
        res_part = await ac.post(f"/api/v1/invoices/{inv.id}/pay?amount=5000.00&mode=Cash")
        assert res_part.status_code == 200, res_part.text
        assert res_part.json()["status"] == InvoiceStatus.PARTIAL.value

        # Edit during PARTIAL status rejected
        res_edit_part = await ac.put(f"/api/v1/expenses/{exp_id}", json={"amount": 25000.00})
        assert res_edit_part.status_code == 400

        # Complete remaining payment: 15000
        res_full = await ac.post(f"/api/v1/invoices/{inv.id}/pay?amount=15000.00&mode=Cash")
        assert res_full.status_code == 200, res_full.text
        assert res_full.json()["status"] == InvoiceStatus.PAID.value

        # Edit during PAID status rejected
        res_edit_full = await ac.put(f"/api/v1/expenses/{exp_id}", json={"amount": 30000.00})
        assert res_edit_full.status_code == 400

        # Delete during PAID status rejected
        res_del_full = await ac.delete(f"/api/v1/expenses/{exp_id}")
        assert res_del_full.status_code == 400


@pytest.mark.asyncio
async def test_json_array_element_positions(setup_test_context):
    """Verify JSON array containment across first, middle, last positions, and non-membership."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp1_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("1000.00"))
        exp2_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("2000.00"))
        exp3_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("3000.00"))
        unlinked_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("4000.00"))

        # Invoice linking [exp1_id, exp2_id, exp3_id]
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp1_id, exp2_id, exp3_id],
            total_amount=Decimal("6000.00"),
            paid_amount=Decimal("6000.00"),
            status=InvoiceStatus.PAID,
        )

        # First element (exp1) -> rejected
        r1 = await ac.put(f"/api/v1/expenses/{exp1_id}", json={"amount": 1100.00})
        assert r1.status_code == 400

        # Middle element (exp2) -> rejected
        r2 = await ac.put(f"/api/v1/expenses/{exp2_id}", json={"amount": 2200.00})
        assert r2.status_code == 400

        # Last element (exp3) -> rejected
        r3 = await ac.put(f"/api/v1/expenses/{exp3_id}", json={"amount": 3300.00})
        assert r3.status_code == 400

        # Unlinked element -> allowed
        r_un = await ac.put(f"/api/v1/expenses/{unlinked_id}", json={"amount": 4400.00})
        assert r_un.status_code == 200
        assert float(r_un.json()["amount"]) == 4400.0


@pytest.mark.asyncio
async def test_multiple_invoices_status_combinations(setup_test_context):
    """Test complex scenarios with multiple invoices referencing the same expense."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_a_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("5000.00"))

        # Invoice 1 PENDING, Invoice 2 PAID -> Must be rejected because of Invoice 2
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_a_id],
            status=InvoiceStatus.PENDING,
        )
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_a_id],
            total_amount=Decimal("5000.00"),
            paid_amount=Decimal("5000.00"),
            status=InvoiceStatus.PAID,
        )

        res = await ac.put(f"/api/v1/expenses/{exp_a_id}", json={"amount": 5500.00})
        assert res.status_code == 400

        exp_b_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("6000.00"))
        # Invoice 1 PENDING, Invoice 2 PENDING -> Must be allowed because neither is PAID/PARTIAL
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_b_id],
            status=InvoiceStatus.PENDING,
        )
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_b_id],
            status=InvoiceStatus.PENDING,
        )

        res_b = await ac.put(f"/api/v1/expenses/{exp_b_id}", json={"amount": 6600.00})
        assert res_b.status_code == 200
        assert float(res_b.json()["amount"]) == 6600.0


@pytest.mark.asyncio
async def test_accounting_integrity_on_rejected_mutations(setup_test_context):
    """Verify that rejected expense edit/delete operations leave journal entries completely untouched."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("10000.00"))
        await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("10000.00"),
            paid_amount=Decimal("10000.00"),
            status=InvoiceStatus.PAID,
        )

        async with AsyncSessionLocal() as db:
            initial_entries = (await db.scalars(select(JournalEntry))).all()
            initial_lines = (await db.scalars(select(JournalLine))).all()
            entry_count = len(initial_entries)
            line_count = len(initial_lines)

        # Rejected update
        res_up = await ac.put(f"/api/v1/expenses/{exp_id}", json={"amount": 99999.00})
        assert res_up.status_code == 400

        # Rejected delete
        res_del = await ac.delete(f"/api/v1/expenses/{exp_id}")
        assert res_del.status_code == 400

    # Ensure zero changes in ledger tables
    async with AsyncSessionLocal() as db:
        post_entries = (await db.scalars(select(JournalEntry))).all()
        post_lines = (await db.scalars(select(JournalLine))).all()
        assert len(post_entries) == entry_count
        assert len(post_lines) == line_count

        exp_db = await db.get(Expense, exp_id)
        assert exp_db.amount == Decimal("10000.00")


@pytest.mark.asyncio
async def test_concurrency_payment_and_expense_update(setup_test_context):
    """Simulate concurrent invoice payment and expense edit using asyncio.gather."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("18000.00"))
        inv = await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("18000.00"),
            status=InvoiceStatus.PENDING,
        )

        async def do_pay():
            return await ac.post(f"/api/v1/invoices/{inv.id}/pay?amount=18000.00&mode=Cash")

        async def do_update():
            return await ac.put(f"/api/v1/expenses/{exp_id}", json={"amount": 19000.00})

        res_pay, res_up = await asyncio.gather(do_pay(), do_update())

        # Both operations must terminate cleanly (no 500, no unhandled deadlock exception)
        assert res_pay.status_code == 200

        # Depending on lock acquisition order:
        # If pay won: update returned 400
        # If update won: update returned 200, but pay still recorded against invoice
        assert res_up.status_code in (200, 400)


@pytest.mark.asyncio
async def test_concurrency_payment_and_expense_delete(setup_test_context):
    """Simulate concurrent invoice payment and expense delete using asyncio.gather."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("12000.00"))
        inv = await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("12000.00"),
            status=InvoiceStatus.PENDING,
        )

        async def do_pay():
            return await ac.post(f"/api/v1/invoices/{inv.id}/pay?amount=12000.00&mode=Cash")

        async def do_delete():
            return await ac.delete(f"/api/v1/expenses/{exp_id}")

        res_pay, res_del = await asyncio.gather(do_pay(), do_delete())

        assert res_pay.status_code == 200
        # If pay won: delete returns 400. If delete won: delete returns 204.
        assert res_del.status_code in (204, 400)


@pytest.mark.asyncio
async def test_concurrency_two_concurrent_payments(setup_test_context):
    """Simulate two concurrent payments for the same invoice, ensuring serialization via FOR UPDATE."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("10000.00"))
        inv = await create_invoice(
            ctx["company_id"],
            ctx["project_id"],
            ctx["owner_id"],
            linked_expense_ids=[exp_id],
            total_amount=Decimal("10000.00"),
            status=InvoiceStatus.PENDING,
        )

        async def do_pay1():
            return await ac.post(f"/api/v1/invoices/{inv.id}/pay?amount=10000.00&mode=Cash")

        async def do_pay2():
            return await ac.post(f"/api/v1/invoices/{inv.id}/pay?amount=10000.00&mode=Cash")

        res1, res2 = await asyncio.gather(do_pay1(), do_pay2())

        statuses = sorted([res1.status_code, res2.status_code])
        # Exactly one payment succeeds (200), and the second is rejected because pending is 0 (422)
        assert statuses == [200, 422]

        async with AsyncSessionLocal() as db:
            refreshed_inv = await db.get(Invoice, inv.id)
            assert refreshed_inv.status == InvoiceStatus.PAID
            assert refreshed_inv.paid_amount == Decimal("10000.00")
            assert refreshed_inv.pending_amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_cross_company_isolation(setup_test_context):
    """Ensure a user from Company B cannot access or modify Company A's expense."""
    ctx = setup_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        exp_id = await create_expense_api(ac, ctx["project_id"], amount=Decimal("5000.00"))

        # Switch dependency override to user from Company B
        app.dependency_overrides[get_current_user] = lambda: ctx["user_b"]
        app.dependency_overrides[get_current_active_user] = lambda: ctx["user_b"]

        res_get = await ac.get(f"/api/v1/expenses/{exp_id}")
        assert res_get.status_code in (403, 404)

        res_put = await ac.put(f"/api/v1/expenses/{exp_id}", json={"amount": 9999.00})
        assert res_put.status_code in (403, 404)

        res_del = await ac.delete(f"/api/v1/expenses/{exp_id}")
        assert res_del.status_code in (403, 404)
