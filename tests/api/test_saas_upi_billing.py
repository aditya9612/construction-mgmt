import pytest
from datetime import datetime
from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_current_active_user, get_db_session, require_tenant_admin
from app.models.user import User, ActivityLog, UserRole
from app.models.subscription import Plan, Subscription, SubscriptionInvoice, ManualPaymentTransaction
from app.core.config import settings

client = TestClient(app)

# Test actors
tenant_a_admin = User(
    id=2001,
    email="admin@tenant-a.com",
    full_name="Tenant A Admin",
    role=UserRole.ADMIN.value,
    company_id=1,
    is_super_admin=False,
    is_active=True
)

tenant_a_normal_user = User(
    id=2002,
    email="engineer@tenant-a.com",
    full_name="Tenant A Engineer",
    role=UserRole.SITE_ENGINEER.value,
    company_id=1,
    is_super_admin=False,
    is_active=True
)

tenant_b_admin = User(
    id=2003,
    email="admin@tenant-b.com",
    full_name="Tenant B Admin",
    role=UserRole.ADMIN.value,
    company_id=2,
    is_super_admin=False,
    is_active=True
)

super_admin_user = User(
    id=9999,
    email="super@infrapilot.com",
    full_name="Super Admin",
    role=UserRole.ADMIN.value,
    company_id=None,
    is_super_admin=True,
    is_active=True
)

plan_pro = Plan(
    id=10,
    name="Pro Plan",
    code="pro_monthly",
    price=4999.0,
    currency="INR",
    billing_interval="monthly",
    is_active=True
)

plan_inactive = Plan(
    id=11,
    name="Old Plan",
    code="old_plan",
    price=999.0,
    currency="INR",
    billing_interval="monthly",
    is_active=False
)

sub_a = Subscription(
    id=101,
    company_id=1,
    plan_id=1,
    status="trial"
)

sub_b = Subscription(
    id=102,
    company_id=2,
    plan_id=1,
    status="trial"
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


class MockSaaSUPISession:
    def __init__(self):
        self.transactions = {}
        self.logs = []
        self.committed = False

    def add(self, obj):
        if isinstance(obj, ManualPaymentTransaction):
            if not obj.id:
                obj.id = len(self.transactions) + 1
            self.transactions[obj.transaction_reference] = obj
        elif isinstance(obj, ActivityLog):
            self.logs.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass

    async def rollback(self):
        pass

    async def scalar(self, stmt):
        return None

    async def execute(self, stmt):
        stmt_str = str(stmt).lower()
        if "plans" in stmt_str and ("plan_id = 10" in stmt_str or "id = :id_1" in stmt_str or "is_active" in stmt_str):
            return MockResult([plan_pro])
        if "subscriptions" in stmt_str and ("company_id = 1" in stmt_str or "company_id = :company_id_1" in stmt_str):
            return MockResult([sub_a])
        if "subscriptions" in stmt_str and "company_id = 2" in stmt_str:
            return MockResult([sub_b])
        if "manual_payment_transactions" in stmt_str:
            # Check company matching
            for txn in self.transactions.values():
                if "company_id = 2" in stmt_str and txn.company_id != 2:
                    continue
                if "company_id = 1" in stmt_str and txn.company_id != 1:
                    continue
                return MockResult([txn])
            return MockResult([])

        return MockResult([])



def test_qr_code_generation_tenant_admin():
    """Verify Tenant Admin can generate dynamic UPI QR with server-authoritative pricing."""
    mock_db = MockSaaSUPISession()
    app.dependency_overrides[get_current_active_user] = lambda: tenant_a_admin
    app.dependency_overrides[require_tenant_admin] = lambda: tenant_a_admin
    app.dependency_overrides[get_db_session] = lambda: mock_db

    response = client.get("/api/v1/saas-billing/upi/qr-code?plan_id=10")
    assert response.status_code == 200
    data = response.json()

    assert data["plan_id"] == 10
    assert data["plan_name"] == "Pro Plan"
    assert data["amount"] == 4999.0
    assert data["currency"] == "INR"
    assert data["upi_id"] == settings.SUPER_ADMIN_UPI_ID
    assert data["status"] == "pending"
    assert "upi://pay?" in data["upi_uri"]
    assert "pa=" in data["upi_uri"]
    assert "am=4999.00" in data["upi_uri"]
    assert "tr=" in data["upi_uri"]
    assert data["qr_code_base64"] is not None
    assert data["qr_code_base64"].startswith("data:image/png;base64,")

    # Invariant: QR code generation does NOT activate subscription
    assert sub_a.status == "trial"
    assert sub_a.plan_id == 1

    app.dependency_overrides.clear()


def test_qr_code_generation_normal_user_denied():
    """Verify Normal Tenant User (non-admin) cannot generate billing QR codes (403 Forbidden)."""
    app.dependency_overrides[get_current_active_user] = lambda: tenant_a_normal_user
    # require_tenant_admin will reject
    response = client.get("/api/v1/saas-billing/upi/qr-code?plan_id=10")
    assert response.status_code == 403
    assert "Tenant Admin privileges required" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_qr_code_generation_super_admin_denied():
    """Verify Super Admin cannot use tenant billing QR generation (403 Forbidden)."""
    app.dependency_overrides[get_current_active_user] = lambda: super_admin_user
    response = client.get("/api/v1/saas-billing/upi/qr-code?plan_id=10")
    assert response.status_code == 403
    app.dependency_overrides.clear()


def test_utr_submission_lifecycle():
    """Verify Tenant Admin can submit UTR reference and transaction remains pending."""
    mock_db = MockSaaSUPISession()
    # Seed pending transaction for Tenant A
    txn = ManualPaymentTransaction(
        id=1,
        company_id=1,
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-1-123456-ABCDEF",
        status="pending"
    )
    mock_db.transactions[txn.transaction_reference] = txn

    app.dependency_overrides[get_current_active_user] = lambda: tenant_a_admin
    app.dependency_overrides[require_tenant_admin] = lambda: tenant_a_admin
    app.dependency_overrides[get_db_session] = lambda: mock_db

    payload = {
        "transaction_reference": "TXN-UPI-1-123456-ABCDEF",
        "utr_reference": "UTR1234567890"
    }
    response = client.post("/api/v1/saas-billing/upi/submit", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["transaction_reference"] == "TXN-UPI-1-123456-ABCDEF"
    assert data["utr_reference"] == "UTR1234567890"
    assert data["status"] == "pending"  # MUST remain pending
    assert data["amount"] == 4999.0
    assert "Pending Super Admin verification" in data["message"]

    # Invariant: UTR submission does NOT activate subscription
    assert sub_a.status == "trial"
    assert sub_a.plan_id == 1

    app.dependency_overrides.clear()


def test_utr_submission_cross_tenant_idor_blocked():
    """Verify Tenant B cannot submit UTR for Tenant A's transaction."""
    mock_db = MockSaaSUPISession()
    txn = ManualPaymentTransaction(
        id=1,
        company_id=1,  # Belongs to Company 1
        subscription_id=101,
        plan_id=10,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference="TXN-UPI-1-123456-ABCDEF",
        status="pending"
    )
    class CrossTenantMockDB(MockSaaSUPISession):
        async def execute(self, stmt):
            # If company_id == 2 (Tenant B), return nothing
            return MockResult([]) if "company_id = 2" in str(stmt).lower() or ":company_id_1" in str(stmt) else MockResult([txn])

    app.dependency_overrides[get_current_active_user] = lambda: tenant_b_admin
    app.dependency_overrides[require_tenant_admin] = lambda: tenant_b_admin
    app.dependency_overrides[get_db_session] = lambda: CrossTenantMockDB()

    payload = {
        "transaction_reference": "TXN-UPI-1-123456-ABCDEF",
        "utr_reference": "UTR9999999999"
    }
    response = client.post("/api/v1/saas-billing/upi/submit", json=payload)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

    app.dependency_overrides.clear()


def test_utr_submission_invalid_format():
    """Verify invalid/empty UTR references are rejected with 400 Bad Request."""
    mock_db = MockSaaSUPISession()
    app.dependency_overrides[get_current_active_user] = lambda: tenant_a_admin
    app.dependency_overrides[require_tenant_admin] = lambda: tenant_a_admin
    app.dependency_overrides[get_db_session] = lambda: mock_db

    # Too short
    res1 = client.post("/api/v1/saas-billing/upi/submit", json={
        "transaction_reference": "TXN-1",
        "utr_reference": "123"
    })
    assert res1.status_code == 400

    # Special characters
    res2 = client.post("/api/v1/saas-billing/upi/submit", json={
        "transaction_reference": "TXN-1",
        "utr_reference": "UTR-12345!@#$"
    })
    assert res2.status_code == 400

    app.dependency_overrides.clear()


def test_phase_5_9b_superadmin_verification_endpoints_require_superadmin():
    """Verify Phase 5.9B Super Admin verification endpoints are protected from Tenant Admin / Normal User."""
    app.dependency_overrides[get_current_active_user] = lambda: tenant_a_admin
    res1 = client.post("/api/v1/superadmin/manual-payments/1/verify")
    assert res1.status_code == 403

    res2 = client.post("/api/v1/superadmin/manual-payments/1/reject", json={"rejection_reason": "Invalid payment"})
    assert res2.status_code == 403

    res3 = client.get("/api/v1/superadmin/manual-payments")
    assert res3.status_code == 403
    app.dependency_overrides.clear()
