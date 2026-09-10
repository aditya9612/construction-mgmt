import pytest
import io
from decimal import Decimal
from datetime import datetime
from fastapi.testclient import TestClient

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, get_db_session
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.project import Project
from app.models.document import Document
from app.models.settings import CompanySettings
from app.models.accountant import VendorBill, PaymentVoucher, BankAccount
from app.models.cad_conversion import CADConversion

client = TestClient(app)

# Test Actors
user_tenant_a = User(
    id=2001,
    email="admin@tenant-a.com",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)

user_tenant_b = User(
    id=3001,
    email="admin@tenant-b.com",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=2,
)

user_super = User(
    id=9999,
    email="super@infrapilot.com",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=True,
    company_id=None,
)

# Test Data
comp_settings_a = CompanySettings(
    id=1,
    company_id=1,
    company_name="Tenant A Corp",
)

comp_settings_b = CompanySettings(
    id=2,
    company_id=2,
    company_name="Tenant B Corp",
)

project_b = Project(
    id=201,
    business_id="PRJ-B-001",
    project_name="Tenant B Hospital",
    company_id=2,
    status="active",
)

doc_b = Document(
    id=501,
    project_id=201,
    title="Confidential_Tenant_B.pdf",
    file_size=10240,
    status="PENDING",
    is_folder=False,
    is_deleted=False,
)

cad_b = CADConversion(
    id=701,
    project_name="Tenant B Survey",
    file_path="uploads/cad/tenant_b_survey.dxf",
    area=450.5,
    company_id=2,
    created_at=datetime.utcnow(),
)

bank_b = BankAccount(
    id=801,
    account_id=1,
    bank_name="HDFC",
    account_number="HDFC000123456",
)

bill_b = VendorBill(
    id=901,
    bill_number="VB-TENANT-B-901",
    project_id=201,
    supplier_id=1,
    company_id=2,
    total_amount=Decimal("50000.00"),
    amount_paid=Decimal("0.00"),
    status="PENDING",
    bill_date=datetime.utcnow().date(),
    due_date=datetime.utcnow().date(),
)

pv_b = PaymentVoucher(
    id=1001,
    payment_voucher_number="PV-TENANT-B-1001",
    payment_date=datetime.utcnow(),
    party_type="Supplier",
    supplier_id=1,
    vendor_bill_id=901,
    base_amount=Decimal("50000.00"),
    gst_amount=Decimal("0.00"),
    gross_amount=Decimal("50000.00"),
    tds_amount=Decimal("0.00"),
    retention_amount=Decimal("0.00"),
    net_payable_amount=Decimal("50000.00"),
    payment_method="BANK_TRANSFER",
    bank_account_id=801,
    status="PENDING",
    created_by=3001,
)


class MockResult:
    def __init__(self, data):
        self.data = data

    def scalars(self):
        class ScalarsResult:
            def __init__(self, items):
                self.items = items
            def all(self):
                return self.items
            def first(self):
                return self.items[0] if self.items else None
        return ScalarsResult(self.data)

    def scalar_one_or_none(self):
        if not self.data:
            return None
        item = self.data[0]
        if isinstance(item, tuple):
            return item[0]
        return item

    def scalar(self):
        return self.scalar_one_or_none()

    def first(self):
        return self.data[0] if self.data else None

    def all(self):
        return self.data


class MockPeripheralSession:
    def __init__(self):
        self.settings = {1: comp_settings_a, 2: comp_settings_b}
        self.projects = {201: project_b}
        self.documents = {501: doc_b}
        self.cad_conversions = {701: cad_b}
        self.vendor_bills = {901: bill_b}
        self.payment_vouchers = {1001: pv_b}
        self.committed = False

    async def scalar(self, stmt):
        res = await self.execute(stmt)
        return res.scalar_one_or_none()

    async def execute(self, stmt):
        stmt_str = str(stmt).lower()

        # CompanySettings query
        if "company_settings" in stmt_str:
            params = stmt.compile().params
            cid = params.get("company_id_1")
            if cid is not None:
                matches = [s for s in self.settings.values() if s.company_id == cid]
                return MockResult(matches)
            return MockResult(list(self.settings.values()))

        # Project query
        if "projects" in stmt_str:
            params = stmt.compile().params
            pid = params.get("id_1")
            cid = params.get("company_id_1")
            items = list(self.projects.values())
            if pid is not None:
                items = [p for p in items if p.id == pid]
            if cid is not None:
                items = [p for p in items if p.company_id == cid]
            return MockResult(items)

        # Document query
        if "documents" in stmt_str:
            if "count(" in stmt_str:
                return MockResult([0])
            params = stmt.compile().params
            cid = params.get("company_id_1")
            did = params.get("id_1")
            items = []
            for d in self.documents.values():
                proj = self.projects.get(d.project_id)
                proj_name = proj.project_name if proj else "Unknown"
                proj_cid = proj.company_id if proj else None
                if cid is not None and proj_cid != cid:
                    continue
                if did is not None and d.id != did:
                    continue
                items.append((d, proj_name))
            return MockResult(items)

        # CAD query
        if "cad_conversions" in stmt_str:
            params = stmt.compile().params
            cid = params.get("company_id_1")
            items = list(self.cad_conversions.values())
            if cid is not None:
                items = [c for c in items if c.company_id == cid]
            return MockResult(items)

        # Payment vouchers query
        if "payment_vouchers" in stmt_str:
            params = stmt.compile().params
            cid = params.get("company_id_1")
            items = list(self.payment_vouchers.values())
            if cid is not None:
                filtered = []
                for pv in items:
                    bill = self.vendor_bills.get(pv.vendor_bill_id)
                    if bill and self.projects.get(bill.project_id) and self.projects[bill.project_id].company_id == cid:
                        filtered.append(pv)
                return MockResult(filtered)
            return MockResult(items)

        # RBAC permissions resolution for Tenant Admin
        if "role_permissions" in stmt_str:
            return MockResult(["*"])

        return MockResult([])

    async def get(self, model, ident):
        if model == Project:
            return self.projects.get(ident)
        if model == Document:
            return self.documents.get(ident)
        if model == CompanySettings:
            return self.settings.get(ident)
        if model == VendorBill:
            return self.vendor_bills.get(ident)
        return None

    def add(self, obj):
        if isinstance(obj, CADConversion):
            if not obj.id:
                obj.id = len(self.cad_conversions) + 1
            self.cad_conversions[obj.id] = obj
        elif isinstance(obj, CompanySettings):
            if not obj.id:
                obj.id = len(self.settings) + 1
            self.settings[obj.id] = obj

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass


@pytest.fixture(autouse=True)
def cleanup():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


# =============================================================================
# 1. AUTHENTICATION REQUIREMENTS (401 FOR UNAUTHENTICATED)
# =============================================================================

def test_unauthenticated_cad_endpoints():
    app.dependency_overrides.clear()
    res1 = client.get("/api/v1/cad/logs")
    assert res1.status_code == 401

    res2 = client.post("/api/v1/cad/csv-to-dxf", files={"file": ("test.csv", "x,y\n1,2\n3,4")})
    assert res2.status_code == 401


def test_unauthenticated_project_visualization_endpoints():
    app.dependency_overrides.clear()
    res1 = client.get("/api/v1/projects/1/visualizations")
    assert res1.status_code == 401

    res2 = client.post("/api/v1/projects/1/visualizations", data={"title": "Test"}, files={"image_file": ("test.jpg", b"fake", "image/jpeg")})
    assert res2.status_code == 401


def test_unauthenticated_company_settings_endpoints():
    app.dependency_overrides.clear()
    res1 = client.get("/api/v1/settings/company")
    assert res1.status_code == 401

    res2 = client.put("/api/v1/settings/company", json={"company_name": "Hack Corp"})
    assert res2.status_code == 401

    res3 = client.post("/api/v1/settings/upload-logo", files={"file": ("logo.png", b"fake", "image/png")})
    assert res3.status_code == 401


def test_unauthenticated_document_endpoints():
    app.dependency_overrides.clear()
    res1 = client.get("/api/v1/documents")
    assert res1.status_code == 401

    res2 = client.get("/api/v1/documents/stats")
    assert res2.status_code == 401

    res3 = client.get("/api/v1/documents/1")
    assert res3.status_code == 401


def test_unauthenticated_payment_vouchers_endpoints():
    app.dependency_overrides.clear()
    res = client.get("/api/v1/payments/vouchers")
    assert res.status_code == 401


# =============================================================================
# 2. TENANT IDOR & ISOLATION TESTS
# =============================================================================

def test_company_settings_tenant_isolation():
    """Verify Tenant A only sees and updates Tenant A settings, never Tenant B."""
    mock_session = MockPeripheralSession()
    app.dependency_overrides[get_current_user] = lambda: user_tenant_a
    app.dependency_overrides[get_current_active_user] = lambda: user_tenant_a
    app.dependency_overrides[get_db_session] = lambda: mock_session

    res_get = client.get("/api/v1/settings/company")
    assert res_get.status_code == 200
    data = res_get.json()
    assert data["company_name"] == "Tenant A Corp"
    assert data["company_id"] == 1

    # Update Tenant A settings
    res_put = client.put("/api/v1/settings/company", json={"company_name": "Tenant A Updated"})
    assert res_put.status_code == 200

    # Tenant B settings untouched
    assert comp_settings_b.company_name == "Tenant B Corp"


def test_project_visualization_tenant_isolation():
    """Verify Tenant A receives 404 when attempting to access Tenant B's project visualizations."""
    mock_session = MockPeripheralSession()
    app.dependency_overrides[get_current_user] = lambda: user_tenant_a
    app.dependency_overrides[get_current_active_user] = lambda: user_tenant_a
    app.dependency_overrides[get_db_session] = lambda: mock_session

    # Project 201 belongs to Tenant B (company_id=2)
    res_get = client.get(f"/api/v1/projects/{project_b.id}/visualizations")
    assert res_get.status_code == 404

    res_post = client.post(
        f"/api/v1/projects/{project_b.id}/visualizations",
        data={"title": "Hacked Viz"},
        files={"image_file": ("viz.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
    )
    assert res_post.status_code == 404


def test_documents_tenant_isolation_and_cache():
    """Verify Tenant A receives 404 for Tenant B's document and does not see it in document listings."""
    mock_session = MockPeripheralSession()
    app.dependency_overrides[get_current_user] = lambda: user_tenant_a
    app.dependency_overrides[get_current_active_user] = lambda: user_tenant_a
    app.dependency_overrides[get_db_session] = lambda: mock_session

    # Detail view of Tenant B's document
    res_detail = client.get(f"/api/v1/documents/{doc_b.id}")
    assert res_detail.status_code == 404

    # List view for Tenant A
    res_list = client.get("/api/v1/documents")
    assert res_list.status_code == 200
    doc_ids = [d["id"] for d in res_list.json()["items"]]
    assert doc_b.id not in doc_ids


def test_cad_logs_tenant_isolation():
    """Verify Tenant A only sees CAD logs belonging to company_id=1."""
    mock_session = MockPeripheralSession()
    app.dependency_overrides[get_current_user] = lambda: user_tenant_a
    app.dependency_overrides[get_current_active_user] = lambda: user_tenant_a
    app.dependency_overrides[get_db_session] = lambda: mock_session

    res = client.get("/api/v1/cad/logs")
    assert res.status_code == 200
    logs = res.json()
    cad_ids = [c["id"] for c in logs]
    # Tenant B CAD (701) must NOT be present
    assert cad_b.id not in cad_ids


def test_payment_vouchers_tenant_isolation():
    """Verify Tenant A cannot see Tenant B's payment vouchers."""
    mock_session = MockPeripheralSession()
    app.dependency_overrides[get_current_user] = lambda: user_tenant_a
    app.dependency_overrides[get_current_active_user] = lambda: user_tenant_a
    app.dependency_overrides[get_db_session] = lambda: mock_session

    res_list = client.get("/api/v1/payments/vouchers")
    assert res_list.status_code == 200
    vouchers = res_list.json()
    pv_ids = [pv["id"] for pv in vouchers]
    assert pv_b.id not in pv_ids
