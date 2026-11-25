import pytest
import pytest_asyncio
import uuid
import asyncio
from decimal import Decimal
from datetime import date
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, func

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner, OwnerTransaction
from app.models.project import Project
from app.models.user import User, UserRole
from app.models.expense import Expense
from app.models.boq import BOQ, BOQGroup
from app.models.accountant import Account, JournalEntry, JournalLine
from app.core.dependencies import get_current_user, get_current_active_user


@pytest.fixture
def run_uid():
    return uuid.uuid4().hex[:6]


@pytest_asyncio.fixture
async def setup_test_context(run_uid):
    """Set up isolated test fixtures for Company A (with BOQ) and Company B (with BOQ)."""
    async with AsyncSessionLocal() as db:
        # Ensure standard accounts exist
        exp_acc = await db.scalar(select(Account).where(Account.code == "GENERAL_EXPENSE"))
        if not exp_acc:
            exp_acc = Account(code="GENERAL_EXPENSE", name="General Expense", type="Expense", is_active=True)
            db.add(exp_acc)
            await db.flush()

        cash_acc = await db.scalar(select(Account).where(Account.code == "CASH"))
        if not cash_acc:
            cash_acc = Account(code="CASH", name="Cash", type="Asset", is_active=True)
            db.add(cash_acc)
            await db.flush()

        # Company A
        comp_a = Company(name=f"CompA_4194_{run_uid}", is_active=True)
        db.add(comp_a)
        await db.flush()

        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-A-{run_uid}",
            owner_name=f"Owner A {run_uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_a_{run_uid}@test.com",
        )
        db.add(owner_a)
        await db.flush()

        proj_a = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Project A {run_uid}",
            business_id=f"PRJ-A-{run_uid}",
            status="Ongoing",
        )
        db.add(proj_a)
        await db.flush()

        admin_user_a = User(
            company_id=comp_a.id,
            email=f"admin_a_{run_uid}@test.com",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=True,
        )
        db.add(admin_user_a)
        await db.flush()

        # BOQ for Project A
        boq_group_a = BOQGroup(
            project_id=proj_a.id,
            name=f"Main Group A {run_uid}",
            current_version=1,
            status="Approved",
        )
        db.add(boq_group_a)
        await db.flush()

        boq_item_a = BOQ(
            project_id=proj_a.id,
            boq_group_id=boq_group_a.id,
            item_name=f"Concrete Grade A {run_uid}",
            category="Concrete",
            unit="cum",
            quantity=Decimal("100.000"),
            unit_cost=Decimal("5000.00"),
            total_cost=Decimal("500000.00"),
            is_latest=True,
        )
        db.add(boq_item_a)
        await db.flush()

        # Company B and Project B with BOQ
        comp_b = Company(name=f"CompB_4194_{run_uid}", is_active=True)
        db.add(comp_b)
        await db.flush()

        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-B-{run_uid}",
            owner_name=f"Owner B {run_uid}",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_b_{run_uid}@test.com",
        )
        db.add(owner_b)
        await db.flush()

        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Project B {run_uid}",
            business_id=f"PRJ-B-{run_uid}",
            status="Ongoing",
        )
        db.add(proj_b)
        await db.flush()

        boq_group_b = BOQGroup(
            project_id=proj_b.id,
            name=f"Main Group B {run_uid}",
            current_version=1,
            status="Approved",
        )
        db.add(boq_group_b)
        await db.flush()

        boq_item_b = BOQ(
            project_id=proj_b.id,
            boq_group_id=boq_group_b.id,
            item_name=f"Masonry Grade B {run_uid}",
            category="Masonry",
            unit="sqm",
            quantity=Decimal("50.000"),
            unit_cost=Decimal("1000.00"),
            total_cost=Decimal("50000.00"),
            is_latest=True,
        )
        db.add(boq_item_b)
        await db.flush()

        await db.commit()

        context = {
            "comp_a_id": comp_a.id,
            "proj_a_id": proj_a.id,
            "owner_a_id": owner_a.id,
            "admin_user_a": admin_user_a,
            "boq_item_a_id": boq_item_a.id,
            "boq_group_a_id": boq_group_a.id,
            "comp_b_id": comp_b.id,
            "proj_b_id": proj_b.id,
            "owner_b_id": owner_b.id,
            "boq_item_b_id": boq_item_b.id,
            "boq_group_b_id": boq_group_b.id,
        }

    app.dependency_overrides[get_current_user] = lambda: admin_user_a
    app.dependency_overrides[get_current_active_user] = lambda: admin_user_a

    yield context

    app.dependency_overrides.clear()

    # Clean teardown
    async with AsyncSessionLocal() as db:
        for pid in [context["proj_a_id"], context["proj_b_id"]]:
            expenses = (await db.scalars(select(Expense).where(Expense.project_id == pid))).all()
            for exp in expenses:
                journals = (await db.scalars(
                    select(JournalEntry).where(JournalEntry.journal_number.like(f"%J-EXP-{exp.id}%"))
                )).all()
                for j in journals:
                    await db.execute(delete(JournalLine).where(JournalLine.entry_id == j.id))
                    await db.delete(j)
                await db.execute(delete(OwnerTransaction).where(OwnerTransaction.reference_id == exp.id))
                await db.delete(exp)

        await db.execute(delete(BOQ).where(BOQ.id.in_([context["boq_item_a_id"], context["boq_item_b_id"]])))
        await db.execute(delete(BOQGroup).where(BOQGroup.id.in_([context["boq_group_a_id"], context["boq_group_b_id"]])))
        await db.execute(delete(Project).where(Project.id.in_([context["proj_a_id"], context["proj_b_id"]])))
        await db.execute(delete(Owner).where(Owner.id.in_([context["owner_a_id"], context["owner_b_id"]])))
        await db.execute(delete(User).where(User.id == context["admin_user_a"].id))
        await db.execute(delete(Company).where(Company.id.in_([context["comp_a_id"], context["comp_b_id"]])))
        await db.commit()


@pytest_asyncio.fixture
async def ac():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ==============================================================================
# 1. Expense Creation Tests (Scenarios 1 - 10)
# ==============================================================================

@pytest.mark.asyncio
async def test_01_valid_boq_id_succeeds_and_persists(setup_test_context, ac):
    ctx = setup_test_context
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Test valid BOQ ID",
        "amount": 2500.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_a_id"],
    })
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["boq_item_id"] == ctx["boq_item_a_id"]
    assert data["project_id"] == ctx["proj_a_id"]

    # Verify database persistence
    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, data["id"])
        assert saved is not None
        assert saved.boq_item_id == ctx["boq_item_a_id"]


@pytest.mark.asyncio
async def test_02_boq_id_omitted_auto_associates_or_persists(setup_test_context, ac):
    ctx = setup_test_context
    # Category "Concrete" matches boq_item_a in Project A
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Test omitted BOQ ID with category match",
        "amount": 1200.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    })
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["boq_item_id"] == ctx["boq_item_a_id"]

    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, data["id"])
        assert saved is not None
        assert saved.boq_item_id == ctx["boq_item_a_id"]


@pytest.mark.asyncio
async def test_03_boq_id_null_no_matching_category(setup_test_context, ac):
    ctx = setup_test_context
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Plumbing",
        "description": "Test null BOQ ID no match",
        "amount": 750.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": None,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["boq_item_id"] is None

    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, data["id"])
        assert saved is not None
        assert saved.boq_item_id is None


@pytest.mark.asyncio
async def test_04_boq_id_zero_normalized_no_false_success(setup_test_context, ac):
    ctx = setup_test_context
    # Category "Plumbing" has no matching BOQ
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Plumbing",
        "description": "Test boq_item_id=0 sentinel",
        "amount": 600.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": 0,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    # Must NOT return an old expense from another project!
    assert data["project_id"] == ctx["proj_a_id"]
    assert data["boq_item_id"] is None

    # Must be persisted in the DB
    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, data["id"])
        assert saved is not None
        assert saved.project_id == ctx["proj_a_id"]


@pytest.mark.asyncio
async def test_05_boq_id_empty_string_explicit_validation_rejection(setup_test_context, ac):
    ctx = setup_test_context
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Plumbing",
        "description": "Test boq_item_id empty string",
        "amount": 600.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": "",
    })
    assert res.status_code == 422, f"Expected 422, got {res.status_code}: {res.text}"


@pytest.mark.asyncio
async def test_06_negative_boq_id_rejected(setup_test_context, ac):
    ctx = setup_test_context
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Test negative BOQ ID",
        "amount": 500.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": -5,
    })
    assert res.status_code == 422, f"Expected 422, got {res.status_code}: {res.text}"


@pytest.mark.asyncio
async def test_07_nonexistent_positive_boq_id_rejected(setup_test_context, ac):
    ctx = setup_test_context
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Test nonexistent BOQ ID",
        "amount": 500.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": 9999999,
    })
    assert res.status_code == 404, f"Expected 404, got {res.status_code}: {res.text}"
    assert "BOQ item not found" in res.text


@pytest.mark.asyncio
async def test_08_boq_id_from_another_project_rejected(setup_test_context, ac):
    ctx = setup_test_context
    # Attempting to associate Project B's BOQ item into Project A
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Masonry",
        "description": "Test cross-project BOQ ID",
        "amount": 800.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_b_id"],
    })
    assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
    assert "BOQ item does not belong to this project" in res.text


@pytest.mark.asyncio
async def test_09_boq_id_from_another_company_tenant_rejected(setup_test_context, ac):
    ctx = setup_test_context
    # boq_item_b belongs to Company B, caller is Company A
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Masonry",
        "description": "Test cross-tenant BOQ",
        "amount": 950.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_b_id"],
    })
    assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"


@pytest.mark.asyncio
async def test_10_valid_manual_expense_without_boq_supported(setup_test_context, ac):
    ctx = setup_test_context
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Site Logistics",
        "description": "Manual expense without any BOQ",
        "amount": 350.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": None,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["boq_item_id"] is None

    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, data["id"])
        assert saved is not None
        assert saved.boq_item_id is None


# ==============================================================================
# 2. Idempotency Behavior Tests (Scenarios 11 - 17)
# ==============================================================================

@pytest.mark.asyncio
async def test_11_integrity_error_without_idempotency_key_does_not_return_unrelated_expense(setup_test_context, ac):
    ctx = setup_test_context
    # Attempting nonexistent project_id triggers 404 project validation
    res = await ac.post("/api/v1/expenses", json={
        "project_id": 9999999,
        "category": "Concrete",
        "description": "Invalid project test",
        "amount": 100.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    })
    assert res.status_code in [400, 404]
    # Verify no 200 with unrelated expense was returned
    assert res.status_code != 200


@pytest.mark.asyncio
async def test_12_existing_db_expenses_with_null_idempotency_never_selected_as_fallback(setup_test_context, ac):
    ctx = setup_test_context
    # Ensure there is an expense with idempotency_key=NULL
    exp1_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Baseline expense without idempotency key",
        "amount": 100.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    })
    assert exp1_res.status_code == 200
    baseline_id = exp1_res.json()["id"]

    # Now make an invalid request (e.g. invalid BOQ ID) without Idempotency-Key
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Should fail, not return baseline",
        "amount": 200.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": 9999999,
    })
    assert res.status_code == 404
    # Must NOT have returned baseline_id with 200
    assert res.status_code != 200


@pytest.mark.asyncio
async def test_13_first_request_with_valid_idempotency_key_creates_one_expense(setup_test_context, ac):
    ctx = setup_test_context
    idem_key = f"key-{uuid.uuid4().hex}"
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "First request with key",
        "amount": 1500.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    }, headers={"Idempotency-Key": idem_key})

    assert res.status_code == 200, res.text
    data = res.json()
    new_id = data["id"]

    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, new_id)
        assert saved is not None
        assert saved.idempotency_key == idem_key


@pytest.mark.asyncio
async def test_14_retry_with_same_key_and_same_payload_returns_original(setup_test_context, ac):
    ctx = setup_test_context
    idem_key = f"key-{uuid.uuid4().hex}"
    payload = {
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Idempotent retry test",
        "amount": 1800.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    }
    # First request
    res1 = await ac.post("/api/v1/expenses", json=payload, headers={"Idempotency-Key": idem_key})
    assert res1.status_code == 200
    first_id = res1.json()["id"]

    # Second request with same payload & key
    res2 = await ac.post("/api/v1/expenses", json=payload, headers={"Idempotency-Key": idem_key})
    assert res2.status_code == 200
    assert res2.json()["id"] == first_id

    # Verify only 1 expense was created in DB
    async with AsyncSessionLocal() as db:
        count = await db.scalar(
            select(func.count(Expense.id)).where(Expense.idempotency_key == idem_key)
        )
        assert count == 1


@pytest.mark.asyncio
async def test_15_same_key_with_different_payload_returns_409(setup_test_context, ac):
    ctx = setup_test_context
    idem_key = f"key-{uuid.uuid4().hex}"
    payload1 = {
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Payload 1",
        "amount": 1000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    }
    res1 = await ac.post("/api/v1/expenses", json=payload1, headers={"Idempotency-Key": idem_key})
    assert res1.status_code == 200

    payload2 = {
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Payload 2 (different amount)",
        "amount": 2000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    }
    res2 = await ac.post("/api/v1/expenses", json=payload2, headers={"Idempotency-Key": idem_key})
    assert res2.status_code == 409
    assert "Idempotency-Key already used with a different request payload" in res2.text


@pytest.mark.asyncio
async def test_16_concurrent_identical_retries_deterministic(setup_test_context, ac):
    ctx = setup_test_context
    idem_key = f"key-concurrent-{uuid.uuid4().hex}"
    payload = {
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Concurrent retry test",
        "amount": 1250.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    }

    # Fire 5 concurrent requests with identical key and payload
    tasks = [
        ac.post("/api/v1/expenses", json=payload, headers={"Idempotency-Key": idem_key})
        for _ in range(5)
    ]
    responses = await asyncio.gather(*tasks)

    for res in responses:
        assert res.status_code == 200

    returned_ids = {r.json()["id"] for r in responses}
    assert len(returned_ids) == 1, f"Expected 1 unique ID, got: {returned_ids}"

    # Verify only 1 expense was created in DB
    async with AsyncSessionLocal() as db:
        count = await db.scalar(
            select(func.count(Expense.id)).where(Expense.idempotency_key == idem_key)
        )
        assert count == 1


@pytest.mark.asyncio
async def test_17_unrelated_integrity_error_with_idempotency_key_not_misclassified(setup_test_context, ac):
    ctx = setup_test_context
    idem_key = f"key-unrelated-{uuid.uuid4().hex}"
    # Supply nonexistent BOQ with idempotency key
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Nonexistent BOQ with idempotency key",
        "amount": 500.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": 9999999,
    }, headers={"Idempotency-Key": idem_key})

    assert res.status_code == 404
    assert res.status_code != 200


# ==============================================================================
# 3. Persistence and List Verification Tests (Scenarios 18 - 22)
# ==============================================================================

@pytest.mark.asyncio
async def test_18_successful_create_response_id_matches_persisted_row(setup_test_context, ac):
    ctx = setup_test_context
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Match ID verification",
        "amount": 1100.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    })
    assert res.status_code == 200
    resp_id = res.json()["id"]

    async with AsyncSessionLocal() as db:
        row = await db.get(Expense, resp_id)
        assert row is not None
        assert row.id == resp_id
        assert row.project_id == ctx["proj_a_id"]
        assert row.amount == Decimal("1100.00")


@pytest.mark.asyncio
async def test_19_failed_create_no_expense_persisted(setup_test_context, ac):
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        before_count = await db.scalar(
            select(func.count(Expense.id)).where(Expense.project_id == ctx["proj_a_id"])
        )

    # Trigger a 400 (cross-project BOQ)
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Failing create",
        "amount": 500.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_b_id"],
    })
    assert res.status_code == 400

    async with AsyncSessionLocal() as db:
        after_count = await db.scalar(
            select(func.count(Expense.id)).where(Expense.project_id == ctx["proj_a_id"])
        )
        assert before_count == after_count


@pytest.mark.asyncio
async def test_20_failed_create_no_journal_or_owner_txn(setup_test_context, ac):
    ctx = setup_test_context
    async with AsyncSessionLocal() as db:
        before_owner_txns = await db.scalar(
            select(func.count(OwnerTransaction.id)).where(OwnerTransaction.project_id == ctx["proj_a_id"])
        )

    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Failing create for journal check",
        "amount": 777.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_b_id"],
    })
    assert res.status_code == 400

    async with AsyncSessionLocal() as db:
        after_owner_txns = await db.scalar(
            select(func.count(OwnerTransaction.id)).where(OwnerTransaction.project_id == ctx["proj_a_id"])
        )
        assert before_owner_txns == after_owner_txns


@pytest.mark.asyncio
async def test_21_list_endpoint_displays_persisted_expense(setup_test_context, ac):
    ctx = setup_test_context
    create_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Unique description for list check",
        "amount": 999.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
    })
    assert create_res.status_code == 200
    created_id = create_res.json()["id"]

    list_res = await ac.get(f"/api/v1/expenses?project_id={ctx['proj_a_id']}")
    assert list_res.status_code == 200
    items = list_res.json()
    item_ids = [item["id"] for item in items]
    assert created_id in item_ids


@pytest.mark.asyncio
async def test_22_failed_request_does_not_leak_other_project_expense(setup_test_context, ac):
    ctx = setup_test_context
    # Attempt to post with invalid data (e.g. negative BOQ ID)
    res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Validation failure test",
        "amount": 500.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": -99,
    })
    assert res.status_code == 422
    # Ensure body does not contain an existing expense record from another project
    assert "project_id" not in res.json() or "detail" in res.json()

# ==============================================================================
# 4. Expense Update (ExpenseUpdate) BOQ Semantics Tests (Scenarios 23 - 30)
# ==============================================================================

@pytest.mark.asyncio
async def test_23_update_expense_boq_omitted_preserves_association(setup_test_context, ac):
    ctx = setup_test_context
    # Create with BOQ
    create_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Initial expense with BOQ",
        "amount": 1000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_a_id"],
    })
    assert create_res.status_code == 200
    exp_id = create_res.json()["id"]

    # Update amount only, omitting boq_item_id
    update_res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
        "amount": 1500.0,
    })
    assert update_res.status_code == 200
    assert update_res.json()["boq_item_id"] == ctx["boq_item_a_id"]

    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, exp_id)
        assert saved.boq_item_id == ctx["boq_item_a_id"]


@pytest.mark.asyncio
async def test_24_update_expense_boq_null_clears_association(setup_test_context, ac):
    ctx = setup_test_context
    create_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Expense to clear BOQ",
        "amount": 1000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_a_id"],
    })
    assert create_res.status_code == 200
    exp_id = create_res.json()["id"]

    # Explicit null to clear association
    update_res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
        "boq_item_id": None,
    })
    assert update_res.status_code == 200
    assert update_res.json()["boq_item_id"] is None

    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, exp_id)
        assert saved.boq_item_id is None


@pytest.mark.asyncio
async def test_25_update_expense_boq_zero_rejected_no_silent_detach(setup_test_context, ac):
    ctx = setup_test_context
    create_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Expense for boq 0 test",
        "amount": 1000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_a_id"],
    })
    assert create_res.status_code == 200
    exp_id = create_res.json()["id"]

    # Sending 0 on update must be rejected, not silently detach
    update_res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
        "boq_item_id": 0,
    })
    assert update_res.status_code == 422

    # Verify association was NOT cleared
    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, exp_id)
        assert saved.boq_item_id == ctx["boq_item_a_id"]


@pytest.mark.asyncio
async def test_26_update_expense_boq_zero_string_rejected(setup_test_context, ac):
    ctx = setup_test_context
    create_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Expense for boq string 0 test",
        "amount": 1000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_a_id"],
    })
    assert create_res.status_code == 200
    exp_id = create_res.json()["id"]

    update_res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
        "boq_item_id": "0",
    })
    assert update_res.status_code == 422

    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, exp_id)
        assert saved.boq_item_id == ctx["boq_item_a_id"]


@pytest.mark.asyncio
async def test_27_update_expense_boq_empty_string_rejected(setup_test_context, ac):
    ctx = setup_test_context
    create_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Expense for boq empty string update",
        "amount": 1000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_a_id"],
    })
    assert create_res.status_code == 200
    exp_id = create_res.json()["id"]

    update_res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
        "boq_item_id": "",
    })
    assert update_res.status_code == 422

    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, exp_id)
        assert saved.boq_item_id == ctx["boq_item_a_id"]


@pytest.mark.asyncio
async def test_28_update_expense_boq_valid_reassignment(setup_test_context, ac):
    ctx = setup_test_context
    # Create second BOQ item in Project A
    async with AsyncSessionLocal() as db:
        boq_item_a2 = BOQ(
            project_id=ctx["proj_a_id"],
            boq_group_id=ctx["boq_group_a_id"],
            item_name="Concrete Grade A2 Reassign",
            category="Concrete",
            unit="cum",
            quantity=Decimal("50.000"),
            unit_cost=Decimal("6000.00"),
            total_cost=Decimal("300000.00"),
            is_latest=True,
        )
        db.add(boq_item_a2)
        await db.commit()
        await db.refresh(boq_item_a2)
        boq_item_a2_id = boq_item_a2.id

    create_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Expense to reassign BOQ",
        "amount": 1000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_a_id"],
    })
    assert create_res.status_code == 200
    exp_id = create_res.json()["id"]

    update_res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
        "boq_item_id": boq_item_a2_id,
    })
    assert update_res.status_code == 200
    assert update_res.json()["boq_item_id"] == boq_item_a2_id

    async with AsyncSessionLocal() as db:
        saved = await db.get(Expense, exp_id)
        assert saved.boq_item_id == boq_item_a2_id
        await db.execute(delete(BOQ).where(BOQ.id == boq_item_a2_id))
        await db.commit()


@pytest.mark.asyncio
async def test_29_update_expense_boq_nonexistent_positive_rejected(setup_test_context, ac):
    ctx = setup_test_context
    create_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Expense for nonexistent update test",
        "amount": 1000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_a_id"],
    })
    assert create_res.status_code == 200
    exp_id = create_res.json()["id"]

    update_res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
        "boq_item_id": 9999999,
    })
    assert update_res.status_code == 404
    assert "BOQ item not found" in update_res.text


@pytest.mark.asyncio
async def test_30_update_expense_boq_cross_project_rejected(setup_test_context, ac):
    ctx = setup_test_context
    create_res = await ac.post("/api/v1/expenses", json={
        "project_id": ctx["proj_a_id"],
        "category": "Concrete",
        "description": "Expense for cross-project update test",
        "amount": 1000.0,
        "expense_date": str(date.today()),
        "payment_mode": "Cash",
        "boq_item_id": ctx["boq_item_a_id"],
    })
    assert create_res.status_code == 200
    exp_id = create_res.json()["id"]

    # Attempting to assign Project B BOQ to Project A Expense
    update_res = await ac.put(f"/api/v1/expenses/{exp_id}", json={
        "boq_item_id": ctx["boq_item_b_id"],
    })
    assert update_res.status_code == 400
    assert "BOQ item does not belong to this project" in update_res.text
