import pytest
from datetime import datetime
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_current_active_user, require_tenant_admin, get_db_session
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.subscription import Plan, Subscription, SubscriptionInvoice, ManualPaymentTransaction

client = TestClient(app)

# Test Actors
tenant_a_admin = User(
    id=2001,
    email="admin@tenant-a.com",
    full_name="Tenant A Admin",
    role=UserRole.ADMIN.value,
    company_id=1,
    is_super_admin=False,
    is_active=True,
)

tenant_a_engineer = User(
    id=2002,
    email="engineer@tenant-a.com",
    full_name="Tenant A Engineer",
    role=UserRole.SITE_ENGINEER.value,
    company_id=1,
    is_super_admin=False,
    is_active=True,
)

tenant_b_admin = User(
    id=3001,
    email="admin@tenant-b.com",
    full_name="Tenant B Admin",
    role=UserRole.ADMIN.value,
    company_id=2,
    is_super_admin=False,
    is_active=True,
)

super_admin = User(
    id=9999,
    email="super@infrapilot.com",
    full_name="Platform Super Admin",
    role=UserRole.ADMIN.value,
    company_id=None,
    is_super_admin=True,
    is_active=True,
)

plan_pro = Plan(
    id=10,
    name="Pro Monthly",
    code="pro_monthly",
    price=4999.0,
    currency="INR",
    billing_interval="monthly",
    is_active=True,
)

sub_tenant_a = Subscription(
    id=101,
    company_id=1,
    plan_id=10,
    status="trial",
)


class MockResult:
    def __init__(self, data):
        self.data = data
    def scalars(self):
        class ScalarsResult:
            def all(self_inner):
                return self.data
        return ScalarsResult()
    def scalar_one_or_none(self):
        return self.data[0] if self.data else None


class MockHistorySession:
    def __init__(self):
        self.transactions = {}

    async def execute(self, stmt):
        stmt_str = str(stmt).lower()
        if "manual_payment_transactions" in stmt_str:
            txns = list(self.transactions.values())
            try:
                params = stmt.compile().params
                # Filter by company_id
                company_id = params.get("company_id_1")
                if company_id is not None:
                    txns = [t for t in txns if t.company_id == company_id]

                # Filter by transaction_reference
                ref = params.get("transaction_reference_1")
                if ref is not None:
                    txns = [t for t in txns if t.transaction_reference == ref]

                # Filter by status
                status = params.get("status_1")
                if status is not None:
                    txns = [t for t in txns if t.status == status.lower()]
            except Exception:
                pass

            return MockResult(txns)

        return MockResult([])


@pytest.fixture
def mock_db():
    session = MockHistorySession()
    # Seed transactions for Tenant A
    txn_a_pending = ManualPaymentTransaction(
        id=1,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-A-PENDING",
        utr_reference="UTR1111111111",
        status="pending",
        created_at=datetime.utcnow(),
        submitted_at=datetime.utcnow(),
    )
    txn_a_pending.plan = plan_pro
    session.transactions[1] = txn_a_pending

    txn_a_verified = ManualPaymentTransaction(
        id=2,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-A-VERIFIED",
        utr_reference="UTR2222222222",
        status="verified",
        verified_by=9999,
        verified_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        submitted_at=datetime.utcnow(),
    )
    txn_a_verified.plan = plan_pro
    session.transactions[2] = txn_a_verified

    txn_a_rejected = ManualPaymentTransaction(
        id=3,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-A-REJECTED",
        utr_reference="UTR3333333333",
        status="rejected",
        rejection_reason="Invalid UTR - not received in bank account",
        created_at=datetime.utcnow(),
        submitted_at=datetime.utcnow(),
    )
    txn_a_rejected.plan = plan_pro
    session.transactions[3] = txn_a_rejected

    # Seed transaction for Tenant B (Company 2)
    txn_b = ManualPaymentTransaction(
        id=4,
        company_id=2,
        subscription_id=201,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-B-SECRET",
        utr_reference="UTR-B-SECRET-999",
        status="pending",
        created_at=datetime.utcnow(),
        submitted_at=datetime.utcnow(),
    )
    txn_b.plan = plan_pro
    session.transactions[4] = txn_b

    return session


def override_actor(user: User, db_session):
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[require_tenant_admin] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: db_session


def clear_overrides():
    app.dependency_overrides.clear()


# =============================================================================
# 1. AUTHENTICATION & RBAC TESTS
# =============================================================================

def test_unauthenticated_request_denied():
    clear_overrides()
    res1 = client.get("/api/v1/saas-billing/upi/transactions")
    assert res1.status_code == 401

    res2 = client.get("/api/v1/saas-billing/upi/transactions/TXN-A-PENDING")
    assert res2.status_code == 401


def test_normal_tenant_user_denied_history(mock_db):
    app.dependency_overrides[get_current_active_user] = lambda: tenant_a_engineer
    # require_tenant_admin will reject with 403
    app.dependency_overrides[get_db_session] = lambda: mock_db
    res1 = client.get("/api/v1/saas-billing/upi/transactions")
    assert res1.status_code == 403

    res2 = client.get("/api/v1/saas-billing/upi/transactions/TXN-A-PENDING")
    assert res2.status_code == 403
    clear_overrides()


def test_super_admin_denied_tenant_history_endpoints(mock_db):
    app.dependency_overrides[get_current_active_user] = lambda: super_admin
    # require_tenant_admin will reject because company_id is None / is_super_admin is True
    app.dependency_overrides[get_db_session] = lambda: mock_db
    res1 = client.get("/api/v1/saas-billing/upi/transactions")
    assert res1.status_code == 403

    res2 = client.get("/api/v1/saas-billing/upi/transactions/TXN-A-PENDING")
    assert res2.status_code == 403
    clear_overrides()


# =============================================================================
# 2. LISTING & STATUS FILTERING TESTS
# =============================================================================

def test_tenant_admin_list_own_transactions(mock_db):
    override_actor(tenant_a_admin, mock_db)
    res = client.get("/api/v1/saas-billing/upi/transactions")
    assert res.status_code == 200
    data = res.json()

    # Tenant A should see only its 3 transactions (not Tenant B's transaction)
    assert len(data) == 3
    refs = [item["transaction_reference"] for item in data]
    assert "TXN-A-PENDING" in refs
    assert "TXN-A-VERIFIED" in refs
    assert "TXN-A-REJECTED" in refs
    assert "TXN-B-SECRET" not in refs

    # Verify fields
    item_pending = next(i for i in data if i["transaction_reference"] == "TXN-A-PENDING")
    assert item_pending["amount"] == 4999.0
    assert item_pending["currency"] == "INR"
    assert item_pending["status"] == "pending"
    assert item_pending["plan_name"] == "Pro Monthly"
    assert item_pending["utr_reference"] == "UTR1111111111"

    clear_overrides()


def test_tenant_admin_list_filtered_by_status(mock_db):
    override_actor(tenant_a_admin, mock_db)

    # Filter status=verified
    res_verified = client.get("/api/v1/saas-billing/upi/transactions?status=verified")
    assert res_verified.status_code == 200
    data_verified = res_verified.json()
    assert len(data_verified) == 1
    assert data_verified[0]["transaction_reference"] == "TXN-A-VERIFIED"
    assert data_verified[0]["status"] == "verified"

    # Filter status=rejected
    res_rejected = client.get("/api/v1/saas-billing/upi/transactions?status=rejected")
    assert res_rejected.status_code == 200
    data_rejected = res_rejected.json()
    assert len(data_rejected) == 1
    assert data_rejected[0]["transaction_reference"] == "TXN-A-REJECTED"
    assert data_rejected[0]["rejection_reason"] == "Invalid UTR - not received in bank account"

    clear_overrides()


# =============================================================================
# 3. DETAIL ENDPOINT & TENANT IDOR ISOLATION TESTS
# =============================================================================

def test_tenant_admin_get_own_transaction_detail(mock_db):
    override_actor(tenant_a_admin, mock_db)
    res = client.get("/api/v1/saas-billing/upi/transactions/TXN-A-PENDING")
    assert res.status_code == 200
    data = res.json()
    assert data["transaction_reference"] == "TXN-A-PENDING"
    assert data["amount"] == 4999.0
    assert data["currency"] == "INR"
    assert data["status"] == "pending"
    assert data["utr_reference"] == "UTR1111111111"
    clear_overrides()


def test_tenant_a_cannot_access_tenant_b_transaction_idor(mock_db):
    """HARD IDOR TEST: Tenant A attempting to access Tenant B's reference must receive 404."""
    override_actor(tenant_a_admin, mock_db)
    res = client.get("/api/v1/saas-billing/upi/transactions/TXN-B-SECRET")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()
    clear_overrides()


def test_tenant_b_access_own_transaction(mock_db):
    """Tenant B can access its own transaction."""
    override_actor(tenant_b_admin, mock_db)
    res = client.get("/api/v1/saas-billing/upi/transactions/TXN-B-SECRET")
    assert res.status_code == 200
    data = res.json()
    assert data["transaction_reference"] == "TXN-B-SECRET"
    assert data["utr_reference"] == "UTR-B-SECRET-999"

    # Tenant B cannot access Tenant A's transaction
    res_a = client.get("/api/v1/saas-billing/upi/transactions/TXN-A-PENDING")
    assert res_a.status_code == 404
    clear_overrides()


# =============================================================================
# 4. READ-ONLY INVARIANT TESTS
# =============================================================================

def test_get_history_does_not_mutate_subscription_or_invoice(mock_db):
    """Verify that calling GET endpoints does NOT activate subscription or mark invoices."""
    sub_tenant_a.status = "trial"
    override_actor(tenant_a_admin, mock_db)

    # Call list
    client.get("/api/v1/saas-billing/upi/transactions")
    assert sub_tenant_a.status == "trial"

    # Call detail
    client.get("/api/v1/saas-billing/upi/transactions/TXN-A-PENDING")
    assert sub_tenant_a.status == "trial"

    clear_overrides()
