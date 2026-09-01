import pytest
from datetime import datetime
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_super_admin, get_db_session
from app.models.user import User, ActivityLog, UserRole
from app.models.company import Company
from app.models.subscription import Plan, Subscription, SubscriptionInvoice, ManualPaymentTransaction
from app.services.entitlement import EntitlementService

client = TestClient(app)

# Actors
super_admin = User(
    id=1025,
    email="superadmin21@gmail.com",
    full_name="Platform Super Admin",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=True,
    company_id=None,
)

tenant_admin = User(
    id=2001,
    email="admin@tenant-a.com",
    full_name="Tenant A Admin",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)

normal_tenant_user = User(
    id=2002,
    email="engineer@tenant-a.com",
    full_name="Tenant A Engineer",
    role=UserRole.SITE_ENGINEER.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)

# Entities
company_1 = Company(id=1, name="Tenant Corp A", subdomain="tenant-a", is_active=True)
company_2 = Company(id=2, name="Tenant Corp B", subdomain="tenant-b", is_active=True)

plan_pro = Plan(
    id=10,
    name="Pro Monthly",
    code="pro_monthly",
    price=4999.0,
    currency="INR",
    billing_interval="monthly",
    features={"max_users": 50, "max_projects": 25, "ai_features": True},
    is_active=True,
)

plan_inactive = Plan(
    id=11,
    name="Deprecated Plan",
    code="old_plan",
    price=999.0,
    currency="INR",
    billing_interval="monthly",
    features={"max_users": 5},
    is_active=False,
)

sub_1 = Subscription(
    id=101,
    company_id=1,
    plan_id=1,
    status="trial",
    auto_renew=True,
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


class MockManualPaymentsSession:
    def __init__(self):
        self.transactions = {}
        self.invoices = {}
        self.subscriptions = {101: sub_1}
        self.companies = {1: company_1, 2: company_2}
        self.plans = {10: plan_pro, 11: plan_inactive}
        self.logs = []
        self.committed = False
        self.rollbacked = False

    def add(self, obj):
        if isinstance(obj, ManualPaymentTransaction):
            if not obj.id:
                obj.id = len(self.transactions) + 1
            self.transactions[obj.id] = obj
        elif isinstance(obj, SubscriptionInvoice):
            if not obj.id:
                obj.id = len(self.invoices) + 1
            self.invoices[obj.id] = obj
        elif isinstance(obj, ActivityLog):
            self.logs.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass

    async def rollback(self):
        self.rollbacked = True

    async def get(self, model, ident):
        if model == Company:
            return self.companies.get(ident)
        if model == Plan:
            return self.plans.get(ident)
        if model == Subscription:
            return self.subscriptions.get(ident)
        if model == SubscriptionInvoice:
            return self.invoices.get(ident)
        if model == ManualPaymentTransaction:
            return self.transactions.get(ident)
        return None

    async def scalar(self, stmt):
        stmt_str = str(stmt).lower()
        if "count" in stmt_str and "manual_payment_transactions" in stmt_str:
            try:
                params = stmt.compile().params
                status_filter = params.get("status_1")
                if status_filter:
                    return sum(1 for t in self.transactions.values() if t.status == status_filter.lower())
            except Exception:
                pass
            return len(self.transactions)
        if "from manual_payment_transactions" in stmt_str:
            for txn in self.transactions.values():
                return txn
            return None
        return None

    async def execute(self, stmt):
        stmt_str = str(stmt).lower()
        if "manual_payment_transactions" in stmt_str:
            txns = list(self.transactions.values())
            try:
                params = stmt.compile().params
                status_filter = params.get("status_1")
                if status_filter:
                    txns = [t for t in txns if t.status == status_filter.lower()]
            except Exception:
                pass
            return MockResult(txns)

        return MockResult([])



def override_superadmin(mock_db=None):
    app.dependency_overrides[get_current_user] = lambda: super_admin
    app.dependency_overrides[get_current_active_user] = lambda: super_admin
    app.dependency_overrides[require_super_admin] = lambda: super_admin
    if mock_db:
        app.dependency_overrides[get_db_session] = lambda: mock_db


def clear_overrides():
    app.dependency_overrides.clear()


# =============================================================================
# 1. AUTHENTICATION & RBAC ISOLATION TESTS
# =============================================================================

def test_unauthenticated_requests_denied():
    clear_overrides()
    assert client.get("/api/v1/superadmin/manual-payments").status_code == 401
    assert client.post("/api/v1/superadmin/manual-payments/1/verify").status_code == 401
    assert client.post("/api/v1/superadmin/manual-payments/1/reject", json={"rejection_reason": "Invalid"}).status_code == 401


def test_tenant_admin_denied_superadmin_manual_payments():
    app.dependency_overrides[get_current_user] = lambda: tenant_admin
    app.dependency_overrides[get_current_active_user] = lambda: tenant_admin
    # require_super_admin will reject with 403
    assert client.get("/api/v1/superadmin/manual-payments").status_code == 403
    assert client.post("/api/v1/superadmin/manual-payments/1/verify").status_code == 403
    assert client.post("/api/v1/superadmin/manual-payments/1/reject", json={"rejection_reason": "Invalid"}).status_code == 403
    clear_overrides()


def test_normal_tenant_user_denied_superadmin_manual_payments():
    app.dependency_overrides[get_current_user] = lambda: normal_tenant_user
    app.dependency_overrides[get_current_active_user] = lambda: normal_tenant_user
    assert client.get("/api/v1/superadmin/manual-payments").status_code == 403
    assert client.post("/api/v1/superadmin/manual-payments/1/verify").status_code == 403
    assert client.post("/api/v1/superadmin/manual-payments/1/reject", json={"rejection_reason": "Invalid"}).status_code == 403
    clear_overrides()


# =============================================================================
# 2. LISTING & FILTERING TESTS
# =============================================================================

def test_superadmin_list_manual_payments_with_filtering():
    mock_db = MockManualPaymentsSession()
    txn1 = ManualPaymentTransaction(
        id=1,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-1",
        utr_reference="UTR1234567890",
        status="pending",
        created_at=datetime.utcnow()
    )
    txn1.company = company_1
    txn1.plan = plan_pro
    mock_db.transactions[1] = txn1

    override_superadmin(mock_db)

    # 1. List all
    res = client.get("/api/v1/superadmin/manual-payments")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "meta" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["transaction_reference"] == "TXN-UPI-1"
    assert data["items"][0]["company_name"] == "Tenant Corp A"
    assert data["items"][0]["plan_name"] == "Pro Monthly"
    assert data["items"][0]["amount"] == 4999.0
    assert data["items"][0]["status"] == "pending"

    # 2. Filter status=pending
    res_pending = client.get("/api/v1/superadmin/manual-payments?status=pending")
    assert res_pending.status_code == 200
    assert len(res_pending.json()["items"]) == 1

    # 3. Filter status=verified (empty)
    res_verified = client.get("/api/v1/superadmin/manual-payments?status=verified")
    assert res_verified.status_code == 200
    assert len(res_verified.json()["items"]) == 0

    clear_overrides()


# =============================================================================
# 3. VERIFICATION & FINANCIAL ACTIVATION TESTS
# =============================================================================

def test_superadmin_verify_pending_transaction_success():
    """Verify atomic activation, invoice creation, and subscription update."""
    mock_db = MockManualPaymentsSession()
    # Reset sub_1 state
    sub_1.status = "trial"
    sub_1.plan_id = 1

    txn = ManualPaymentTransaction(
        id=1,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-1",
        utr_reference="UTR1234567890",
        status="pending",
        created_at=datetime.utcnow(),
        submitted_at=datetime.utcnow()
    )
    txn.company = company_1
    txn.plan = plan_pro
    mock_db.transactions[1] = txn

    override_superadmin(mock_db)

    # Empty payload verification - server-authoritative
    res = client.post("/api/v1/superadmin/manual-payments/1/verify")
    assert res.status_code == 200
    data = res.json()

    # 1. Transaction state
    assert data["status"] == "verified"
    assert data["verified_by"] == super_admin.id
    assert data["verified_at"] is not None
    assert data["invoice_id"] is not None

    # 2. Subscription state updated
    assert sub_1.status == "active"
    assert sub_1.plan_id == 10

    # 3. Invoice created and marked paid
    assert len(mock_db.invoices) == 1
    inv = list(mock_db.invoices.values())[0]
    assert inv.status == "paid"
    assert inv.total_amount == Decimal("4999.00")
    assert inv.currency == "INR"
    assert inv.company_id == 1
    assert inv.subscription_id == 101
    assert inv.invoice_number.startswith("INV-UPI-1-")

    # 4. ActivityLog created
    assert any(log.action == "UPI_PAYMENT_VERIFIED" and log.entity == "ManualPaymentTransaction" for log in mock_db.logs)

    clear_overrides()


def test_superadmin_verify_already_verified_rejected():
    mock_db = MockManualPaymentsSession()
    txn = ManualPaymentTransaction(
        id=2,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-2",
        utr_reference="UTR1234567891",
        status="verified",
        verified_by=super_admin.id,
        verified_at=datetime.utcnow()
    )
    mock_db.transactions[2] = txn

    override_superadmin(mock_db)
    res = client.post("/api/v1/superadmin/manual-payments/2/verify")
    assert res.status_code == 400
    assert "already been verified" in res.json()["detail"].lower()
    clear_overrides()


def test_superadmin_verify_rejected_transaction_rejected():
    mock_db = MockManualPaymentsSession()
    txn = ManualPaymentTransaction(
        id=3,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-3",
        utr_reference="UTR1234567892",
        status="rejected",
        rejection_reason="Invalid UTR",
        created_at=datetime.utcnow()
    )
    mock_db.transactions[3] = txn

    override_superadmin(mock_db)
    res = client.post("/api/v1/superadmin/manual-payments/3/verify")
    assert res.status_code == 400
    assert "rejected" in res.json()["detail"].lower()
    clear_overrides()


def test_superadmin_verify_amount_tampering_blocked():
    """Verify that if txn.amount was tampered/mismatched from Plan.price, verification fails."""
    mock_db = MockManualPaymentsSession()
    txn = ManualPaymentTransaction(
        id=4,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("100.00"),  # Tampered: Pro plan is 4999.00
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-4",
        utr_reference="UTR1234567894",
        status="pending",
    )
    txn.company = company_1
    txn.plan = plan_pro
    mock_db.transactions[4] = txn

    override_superadmin(mock_db)
    res = client.post("/api/v1/superadmin/manual-payments/4/verify")
    assert res.status_code == 400
    assert "amount" in res.json()["detail"].lower()
    clear_overrides()


def test_superadmin_verify_currency_tampering_blocked():
    """Verify that if txn.currency was tampered from Plan.currency, verification fails."""
    mock_db = MockManualPaymentsSession()
    txn = ManualPaymentTransaction(
        id=5,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="USD",  # Tampered: Pro plan is INR
        payment_method="UPI",
        transaction_reference="TXN-UPI-5",
        utr_reference="UTR1234567895",
        status="pending",
    )
    txn.company = company_1
    txn.plan = plan_pro
    mock_db.transactions[5] = txn

    override_superadmin(mock_db)
    res = client.post("/api/v1/superadmin/manual-payments/5/verify")
    assert res.status_code == 400
    assert "currency" in res.json()["detail"].lower()
    clear_overrides()


def test_superadmin_verify_inactive_plan_blocked():
    """Verify that verifying a transaction for an inactive/deprecated plan fails."""
    mock_db = MockManualPaymentsSession()
    txn = ManualPaymentTransaction(
        id=6,
        company_id=1,
        subscription_id=101,
        plan_id=11,  # Inactive plan
        amount=Decimal("999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-6",
        utr_reference="UTR1234567896",
        status="pending",
    )
    txn.company = company_1
    txn.plan = plan_inactive
    mock_db.transactions[6] = txn

    override_superadmin(mock_db)
    res = client.post("/api/v1/superadmin/manual-payments/6/verify")
    assert res.status_code == 400
    assert "active" in res.json()["detail"].lower()
    clear_overrides()


def test_superadmin_verify_terminal_subscription_lifecycle_protection():
    """Verify that cancelled or expired subscriptions cannot be blindly activated."""
    mock_db = MockManualPaymentsSession()
    cancelled_sub = Subscription(id=202, company_id=1, plan_id=1, status="cancelled")
    mock_db.subscriptions[202] = cancelled_sub

    txn = ManualPaymentTransaction(
        id=7,
        company_id=1,
        subscription_id=202,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-7",
        utr_reference="UTR1234567897",
        status="pending",
    )
    txn.company = company_1
    txn.plan = plan_pro
    mock_db.transactions[7] = txn

    override_superadmin(mock_db)
    res = client.post("/api/v1/superadmin/manual-payments/7/verify")
    assert res.status_code == 400
    assert "cancelled" in res.json()["detail"].lower()
    clear_overrides()


def test_superadmin_verify_cross_company_mismatch_blocked():
    """Verify cross-company transaction / subscription mismatch is rejected."""
    mock_db = MockManualPaymentsSession()
    # Subscription belongs to Company 2, transaction claims Company 1
    sub_comp_2 = Subscription(id=303, company_id=2, plan_id=1, status="trial")
    mock_db.subscriptions[303] = sub_comp_2

    txn = ManualPaymentTransaction(
        id=8,
        company_id=1,
        subscription_id=303,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-8",
        utr_reference="UTR1234567898",
        status="pending",
    )
    txn.company = company_1
    txn.plan = plan_pro
    mock_db.transactions[8] = txn

    override_superadmin(mock_db)
    res = client.post("/api/v1/superadmin/manual-payments/8/verify")
    assert res.status_code == 400
    assert "company" in res.json()["detail"].lower()
    clear_overrides()


# =============================================================================
# 4. REJECTION TESTS
# =============================================================================

def test_superadmin_reject_transaction_success():
    mock_db = MockManualPaymentsSession()
    sub_1.status = "trial"
    sub_1.plan_id = 1
    txn = ManualPaymentTransaction(
        id=9,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-9",
        utr_reference="UTR0000000000",
        status="pending",
        created_at=datetime.utcnow()
    )
    txn.company = company_1
    txn.plan = plan_pro
    mock_db.transactions[9] = txn

    override_superadmin(mock_db)
    res = client.post("/api/v1/superadmin/manual-payments/9/reject", json={"rejection_reason": "Fake UTR submitted"})
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "rejected"
    assert data["rejection_reason"] == "Fake UTR submitted"
    assert data["verified_by"] is None
    assert data["verified_at"] is None

    # Subscription and invoices unchanged
    assert sub_1.status == "trial"
    assert len(mock_db.invoices) == 0

    # ActivityLog created
    assert any(log.action == "UPI_PAYMENT_REJECTED" for log in mock_db.logs)

    clear_overrides()


def test_superadmin_reject_validation_enforced():
    """Verify rejection reason validation (cannot be empty or too short)."""
    mock_db = MockManualPaymentsSession()
    txn = ManualPaymentTransaction(
        id=10,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-10",
        status="pending",
    )
    mock_db.transactions[10] = txn

    override_superadmin(mock_db)
    # Empty reason
    res1 = client.post("/api/v1/superadmin/manual-payments/10/reject", json={"rejection_reason": ""})
    assert res1.status_code in [400, 422]

    # Too short reason (1 char)
    res2 = client.post("/api/v1/superadmin/manual-payments/10/reject", json={"rejection_reason": "a"})
    assert res2.status_code in [400, 422]

    clear_overrides()


@pytest.mark.asyncio
async def test_entitlement_service_reflects_verified_subscription():
    """Verify EntitlementService derives active features dynamically once verified."""
    service = EntitlementService()
    # Mock active subscription with pro plan
    class MockEntitlementDB:
        async def scalar(self, stmt):
            return Subscription(
                id=101,
                company_id=1,
                plan_id=10,
                plan=plan_pro,
                status="active"
            )

    entitlements = await service.get_company_entitlements(MockEntitlementDB(), 1)
    assert entitlements["is_active"] is True
    assert entitlements["status"] == "active"
    assert entitlements["plan_id"] == 10
    assert entitlements["plan_name"] == "Pro Monthly"
    assert entitlements["max_users"] == 50
    assert entitlements["max_projects"] == 25
    assert entitlements["ai_features"] is True
