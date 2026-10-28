import pytest
import pytest_asyncio
import uuid
import asyncio
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.user import User, UserRole, ActivityLog
from app.models.notification import Notification
from app.models.invoice import Invoice, Transaction
from app.models.client_payment import ClientPayment
from app.models.accountant import JournalEntry, JournalLine
from app.core.enums import InvoiceStatus, InvoiceType, PaymentMode, PaymentStatus
from app.core.dependencies import get_current_user, get_current_active_user


@pytest.fixture
def run_uid():
    return uuid.uuid4().hex[:6]


@pytest_asyncio.fixture
async def setup_invoice_context(run_uid):
    async with AsyncSessionLocal() as db:
        comp_a = Company(name=f"CompA_4242_{run_uid}", is_active=True)
        comp_b = Company(name=f"CompB_4242_{run_uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OA_4242_{run_uid}",
            owner_name=f"OwnerA_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OB_4242_{run_uid}",
            owner_name=f"OwnerB_{run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"ProjA_4242_{run_uid}",
            business_id=f"PA4242-{run_uid}",
            status="Ongoing",
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"ProjB_4242_{run_uid}",
            business_id=f"PB4242-{run_uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        admin_a = User(
            company_id=comp_a.id,
            email=f"admin_a_4242_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        admin_b = User(
            company_id=comp_b.id,
            email=f"admin_b_4242_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([admin_a, admin_b])
        await db.flush()

        # Invoice 1: 1000.00 pending
        inv1 = Invoice(
            company_id=comp_a.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type=InvoiceType.MATERIAL,
            invoice_number=f"INV1-{run_uid}",
            amount=Decimal("1000.00"),
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("0.00"),
            pending_amount=Decimal("1000.00"),
            status=InvoiceStatus.PENDING,
        )
        # Invoice 2: 500.00 partially paid (300 paid, 200 pending)
        inv2 = Invoice(
            company_id=comp_a.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type=InvoiceType.MATERIAL,
            invoice_number=f"INV2-{run_uid}",
            amount=Decimal("500.00"),
            total_amount=Decimal("500.00"),
            paid_amount=Decimal("300.00"),
            pending_amount=Decimal("200.00"),
            status=InvoiceStatus.PARTIAL,
        )
        # Invoice 3: Already paid invoice
        inv3 = Invoice(
            company_id=comp_a.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type=InvoiceType.MATERIAL,
            invoice_number=f"INV3-{run_uid}",
            amount=Decimal("200.00"),
            total_amount=Decimal("200.00"),
            paid_amount=Decimal("200.00"),
            pending_amount=Decimal("0.00"),
            status=InvoiceStatus.PAID,
        )
        db.add_all([inv1, inv2, inv3])
        await db.commit()

        context = {
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "proj_a_id": proj_a.id,
            "proj_b_id": proj_b.id,
            "owner_a_id": owner_a.id,
            "owner_b_id": owner_b.id,
            "admin_a": admin_a,
            "admin_b": admin_b,
            "inv1_id": inv1.id,
            "inv2_id": inv2.id,
            "inv3_id": inv3.id,
        }

    yield context

    # Teardown
    async with AsyncSessionLocal() as db:
        res_p = await db.execute(select(ClientPayment.id).where(ClientPayment.company_id.in_([comp_a.id, comp_b.id])))
        p_ids = list(res_p.scalars().all())
        if p_ids:
            await db.execute(delete(ClientPayment).where(ClientPayment.id.in_(p_ids)))

        res_i = await db.execute(select(Invoice.id).where(Invoice.company_id.in_([comp_a.id, comp_b.id])))
        i_ids = list(res_i.scalars().all())
        if i_ids:
            await db.execute(delete(Transaction).where(Transaction.invoice_id.in_(i_ids)))
            await db.execute(delete(Invoice).where(Invoice.id.in_(i_ids)))

        await db.execute(delete(Notification).where(Notification.user_id.in_([admin_a.id, admin_b.id])))
        await db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_([admin_a.id, admin_b.id])))
        await db.execute(delete(User).where(User.id.in_([admin_a.id, admin_b.id])))
        await db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
        await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
        await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
        await db.commit()


@pytest.mark.asyncio
async def test_pay_invoice_success_partial_and_full(setup_invoice_context):
    """Normal partial payment and then remaining payment succeeds and marks invoice PAID."""
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Pay 400 out of 1000
            res1 = await ac.post(f"/api/v1/invoices/{ctx['inv1_id']}/pay?amount=400.00&mode=Cash")
            assert res1.status_code == 200, res1.text
            assert res1.json()["status"] == InvoiceStatus.PARTIAL.value
            assert res1.json()["paid"] == 400.0
            assert res1.json()["pending"] == 600.0

            # Pay remaining 600
            res2 = await ac.post(f"/api/v1/invoices/{ctx['inv1_id']}/pay?amount=600.00&mode=Cash")
            assert res2.status_code == 200, res2.text
            assert res2.json()["status"] == InvoiceStatus.PAID.value
            assert res2.json()["paid"] == 1000.0
            assert res2.json()["pending"] == 0.0

            # Another payment attempt when fully paid must be rejected
            res3 = await ac.post(f"/api/v1/invoices/{ctx['inv1_id']}/pay?amount=10.00&mode=Cash")
            assert res3.status_code in (400, 422)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_pay_invoice_overpayment_rejected(setup_invoice_context):
    """
    Issue #4242: Payment exceeding pending balance must be rejected with 4xx.
    Invoice total = 500, paid = 300, pending = 200.
    Attempting to pay 200.01 or 250 must be rejected.
    """
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Attempt to pay 250 when pending is 200
            res = await ac.post(f"/api/v1/invoices/{ctx['inv2_id']}/pay?amount=250.00&mode=Cash")
            assert res.status_code in (400, 422), res.text

        # Verify no database changes or orphaned transactions
        async with AsyncSessionLocal() as db:
            inv = await db.get(Invoice, ctx["inv2_id"])
            assert inv.paid_amount == Decimal("300.00")
            assert inv.pending_amount == Decimal("200.00")
            assert inv.status == InvoiceStatus.PARTIAL

            txs = (await db.execute(select(Transaction).where(Transaction.invoice_id == ctx["inv2_id"]))).scalars().all()
            assert len(txs) == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_pay_invoice_invalid_amount_and_cancelled(setup_invoice_context):
    """Zero amount, negative amount, and payment on cancelled invoice must be rejected."""
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Zero amount
            r_zero = await ac.post(f"/api/v1/invoices/{ctx['inv1_id']}/pay?amount=0.00&mode=Cash")
            assert r_zero.status_code in (400, 422)

            # Negative amount
            r_neg = await ac.post(f"/api/v1/invoices/{ctx['inv1_id']}/pay?amount=-50.00&mode=Cash")
            assert r_neg.status_code in (400, 422)

            # Fully paid invoice rejection
            r_paid = await ac.post(f"/api/v1/invoices/{ctx['inv3_id']}/pay?amount=100.00&mode=Cash")
            assert r_paid.status_code in (400, 422)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_client_payment_create_overpayment_rejected(setup_invoice_context):
    """
    POST /client-payments: Cannot create a client payment exceeding the pending invoice amount.
    """
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # inv2 has pending 200, requested 250 -> must be rejected with 400
            res = await ac.post("/api/v1/client-payments", data={
                "project_id": ctx["proj_a_id"],
                "invoice_id": ctx["inv2_id"],
                "amount": "250.00",
                "payment_method": "CASH",
            })
            assert res.status_code == 400
            assert "exceeds" in res.text.lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_client_payment_unverified_queue_protection(setup_invoice_context):
    """
    Issue #4242: Multiple client payments must not exceed the invoice balance even if unverified.
    Invoice has pending 200.
    Payment 1 created for 120 (leaves 80 available).
    Payment 2 created for 100 (exceeds 80 available) -> must be rejected with 400!
    """
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Payment 1 for 120 succeeds
            r1 = await ac.post("/api/v1/client-payments", data={
                "project_id": ctx["proj_a_id"],
                "invoice_id": ctx["inv2_id"],
                "amount": "120.00",
                "payment_method": "CASH",
            })
            assert r1.status_code == 201, r1.text

            # Payment 2 for 100 exceeds remaining available (80) -> rejected!
            r2 = await ac.post("/api/v1/client-payments", data={
                "project_id": ctx["proj_a_id"],
                "invoice_id": ctx["inv2_id"],
                "amount": "100.00",
                "payment_method": "CASH",
            })
            assert r2.status_code == 400
            assert "exceeds" in r2.text.lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_client_payment_verify_overpayment_rejected(setup_invoice_context):
    """
    Issue #4242: When verifying/approving a payment, if payment.amount exceeds pending balance,
    it must be rejected with HTTP 400, never overpaying the invoice.
    """
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    # Direct DB fixture: simulate an unverified payment of 150 against inv2 (which has pending 200)
    async with AsyncSessionLocal() as db:
        cp = ClientPayment(
            company_id=ctx["comp_a_id"],
            client_user_id=ctx["admin_a"].id,
            invoice_id=ctx["inv2_id"],
            project_id=ctx["proj_a_id"],
            amount=Decimal("150.00"),
            payment_method="CASH",
            payment_status=PaymentStatus.VERIFICATION_PENDING,
            payment_no="CP_TEST_VERIFY",
        )
        db.add(cp)
        await db.commit()
        cp_id = cp.id

    try:
        # Meanwhile, someone paid 100 via /pay, leaving pending = 100
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_pay = await ac.post(f"/api/v1/invoices/{ctx['inv2_id']}/pay?amount=100.00&mode=Cash")
            assert r_pay.status_code == 200

            # Now admin tries to verify cp (amount=150), which exceeds current pending (100)
            r_ver = await ac.post(f"/api/v1/client-payments/{cp_id}/verify", json={"action": "approve"})
            assert r_ver.status_code == 400
            assert "exceeds" in r_ver.text.lower()

        # Verify DB state: invoice paid remains 400, pending remains 100, cp not verified
        async with AsyncSessionLocal() as db:
            inv = await db.get(Invoice, ctx["inv2_id"])
            assert inv.paid_amount == Decimal("400.00")
            assert inv.pending_amount == Decimal("100.00")

            cp_db = await db.get(ClientPayment, cp_id)
            assert cp_db.payment_status == PaymentStatus.VERIFICATION_PENDING
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_payments_prevent_overpayment(setup_invoice_context):
    """
    Concurrency safety test:
    Two concurrent requests try to pay 600.00 on an invoice with pending 1000.00 (combined = 1200.00).
    Exactly one or both must be handled such that total paid NEVER exceeds total_amount (1000.00).
    """
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            async def pay_attempt():
                return await ac.post(f"/api/v1/invoices/{ctx['inv1_id']}/pay?amount=600.00&mode=Cash")

            res1, res2 = await asyncio.gather(pay_attempt(), pay_attempt())
            statuses = [res1.status_code, res2.status_code]

            # One succeeds (200) and the other fails (422) because 600+600 > 1000
            assert 200 in statuses
            assert any(s in (400, 422) for s in statuses)

        async with AsyncSessionLocal() as db:
            inv = await db.get(Invoice, ctx["inv1_id"])
            assert inv.paid_amount == Decimal("600.00")
            assert inv.pending_amount == Decimal("400.00")
            assert inv.paid_amount <= inv.total_amount
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_tenant_isolation_cross_company_pay_blocked(setup_invoice_context):
    """Company B user cannot pay Company A's invoice."""
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_b"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_b"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(f"/api/v1/invoices/{ctx['inv1_id']}/pay?amount=100.00&mode=Cash")
            assert res.status_code in (403, 404)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_update_invoice_total_less_than_paid_rejected(setup_invoice_context):
    """Cannot reduce invoice total amount below already received paid amount."""
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # inv2 has paid_amount = 300. Trying to update total to 250 must fail.
            res = await ac.put(f"/api/v1/invoices/{ctx['inv2_id']}", json={
                "amount": 250.00,
                "gst_percent": 0,
                "tax_percent": 0,
            })
            assert res.status_code in (400, 422)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_client_payment_creation_unverified_queue_limit(setup_invoice_context):
    """
    Two concurrent requests try to create a client payment of 600.00 each on an invoice
    with pending 1000.00. Exactly one must succeed (201) and the second must be rejected (400)
    because the unverified payment queue would exceed pending amount.
    """
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    barrier = asyncio.Barrier(2)

    async def submit_cp(client):
        await barrier.wait()
        return await client.post("/api/v1/client-payments", data={
            "project_id": ctx["proj_a_id"],
            "invoice_id": ctx["inv1_id"],
            "amount": "600.00",
            "payment_method": "CASH",
        })

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c1, \
                   AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
            res1, res2 = await asyncio.gather(submit_cp(c1), submit_cp(c2))
            statuses = [res1.status_code, res2.status_code]
            assert 201 in statuses
            assert 400 in statuses

        async with AsyncSessionLocal() as db:
            cps = (await db.execute(select(ClientPayment).where(ClientPayment.invoice_id == ctx["inv1_id"]))).scalars().all()
            assert len(cps) == 1
            assert cps[0].amount == Decimal("600.00")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_payment_verification_race_prevent_overpayment(setup_invoice_context):
    """
    Two pending payments of 600.00 exist on an invoice with pending 1000.00.
    Concurrently approving both must result in exactly one 200 and one 400,
    leaving invoice paid_amount = 600.00 and pending_amount = 400.00.
    """
    ctx = setup_invoice_context
    app.dependency_overrides[get_current_user] = lambda: ctx["admin_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["admin_a"]

    # Create two pending payments directly
    async with AsyncSessionLocal() as db:
        cp_a = ClientPayment(
            company_id=ctx["comp_a_id"],
            client_user_id=ctx["admin_a"].id,
            invoice_id=ctx["inv1_id"],
            project_id=ctx["proj_a_id"],
            amount=Decimal("600.00"),
            payment_method="CASH",
            payment_status=PaymentStatus.VERIFICATION_PENDING,
            payment_no="CP_RACE_VER_A",
        )
        cp_b = ClientPayment(
            company_id=ctx["comp_a_id"],
            client_user_id=ctx["admin_a"].id,
            invoice_id=ctx["inv1_id"],
            project_id=ctx["proj_a_id"],
            amount=Decimal("600.00"),
            payment_method="CASH",
            payment_status=PaymentStatus.VERIFICATION_PENDING,
            payment_no="CP_RACE_VER_B",
        )
        db.add_all([cp_a, cp_b])
        await db.commit()
        cp_a_id = cp_a.id
        cp_b_id = cp_b.id

    barrier = asyncio.Barrier(2)

    async def verify_cp(client, cp_id):
        await barrier.wait()
        return await client.post(f"/api/v1/client-payments/{cp_id}/verify", json={"action": "approve"})

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c1, \
                   AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
            res1, res2 = await asyncio.gather(verify_cp(c1, cp_a_id), verify_cp(c2, cp_b_id))
            statuses = [res1.status_code, res2.status_code]
            assert 200 in statuses
            assert 400 in statuses

        async with AsyncSessionLocal() as db:
            inv = await db.get(Invoice, ctx["inv1_id"])
            assert inv.paid_amount == Decimal("600.00")
            assert inv.pending_amount == Decimal("400.00")
    finally:
        app.dependency_overrides.clear()
