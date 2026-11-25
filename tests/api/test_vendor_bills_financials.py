import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from datetime import date
from decimal import Decimal

from app.main import app
from app.core.db import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.material import Supplier
from app.models.accountant import VendorBill
from app.core.dependencies import get_current_active_user, get_current_user

mock_superadmin = User(
    id=9999,
    full_name="Test SuperAdmin",
    email="superadmin@test.com",
    role=UserRole.ADMIN,
    is_active=True,
    is_super_admin=True,
    company_id=None,
)

def get_superadmin():
    return mock_superadmin

@pytest_asyncio.fixture(autouse=True)
async def setup_auth_overrides():
    app.dependency_overrides[get_current_active_user] = get_superadmin
    app.dependency_overrides[get_current_user] = get_superadmin
    yield
    app.dependency_overrides.pop(get_current_active_user, None)
    app.dependency_overrides.pop(get_current_user, None)

@pytest_asyncio.fixture
async def supplier_id():
    async with AsyncSessionLocal() as db:
        supp = Supplier(
            company_id=1,
            supplier_name="Test Supplier Fin",
            contact_person="Contact",
            phone_email="test@fin.com",
            gst_number="27ABCDE1234F1Z5"
        )
        db.add(supp)
        await db.commit()
        await db.refresh(supp)
        return supp.id

import uuid

@pytest.mark.asyncio
async def test_vendor_bill_financial_validation(supplier_id):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        # A. Valid mathematically consistent bill
        payload_a = {
            "supplier_id": supplier_id,
            "bill_number": f"VB-FIN-{uuid.uuid4().hex[:8]}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "gross_amount": 1000.0,
            "gst_percent": 18.0,
            "gst_amount": 180.0,
            "tds_percent": 2.0,
            "tds_amount": 20.0,
            "advance_paid": 50.0,
            "total_amount": 1110.0,
            "cgst": 90.0,
            "sgst": 90.0,
            "igst": 0.0
        }
        res_a = await ac.post("/api/v1/vendor-bills", json=payload_a)
        assert res_a.status_code == 201, res_a.text

        # B. Small legitimate rounding difference (e.g. 0.50 diff)
        payload_b = {
            "supplier_id": supplier_id,
            "bill_number": f"VB-FIN-{uuid.uuid4().hex[:8]}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "gross_amount": 1000.0,
            "gst_percent": 18.0,
            "gst_amount": 180.50,
            "tds_percent": 2.0,
            "tds_amount": 20.0,
            "advance_paid": 50.0,
            "total_amount": 1110.50,
            "cgst": 90.25,
            "sgst": 90.25,
            "igst": 0.0
        }
        res_b = await ac.post("/api/v1/vendor-bills", json=payload_b)
        assert res_b.status_code == 201, res_b.text
        bill_b = res_b.json()
        assert bill_b["gst_amount"] == 180.50
        assert bill_b["total_amount"] == 1110.50

        # C. Materially inconsistent GST
        payload_c = {
            "supplier_id": supplier_id,
            "bill_number": f"VB-FIN-{uuid.uuid4().hex[:8]}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "gross_amount": 1000.0,
            "gst_percent": 18.0,
            "gst_amount": 200.0,
            "tds_percent": 2.0,
            "tds_amount": 20.0,
            "advance_paid": 50.0,
            "total_amount": 1130.0,
            "cgst": 100.0,
            "sgst": 100.0,
            "igst": 0.0
        }
        res_c = await ac.post("/api/v1/vendor-bills", json=payload_c)
        assert res_c.status_code == 400
        assert "GST amount does not reconcile" in res_c.json()["detail"]

        # D. Materially inconsistent TDS
        payload_d = {
            "supplier_id": supplier_id,
            "bill_number": f"VB-FIN-{uuid.uuid4().hex[:8]}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "gross_amount": 1000.0,
            "gst_percent": 18.0,
            "gst_amount": 180.0,
            "tds_percent": 2.0,
            "tds_amount": 50.0,
            "advance_paid": 50.0,
            "total_amount": 1080.0,
            "cgst": 90.0,
            "sgst": 90.0,
            "igst": 0.0
        }
        res_d = await ac.post("/api/v1/vendor-bills", json=payload_d)
        assert res_d.status_code == 400
        assert "TDS amount does not reconcile" in res_d.json()["detail"]

        # E. Materially inconsistent total_amount
        payload_e = {
            "supplier_id": supplier_id,
            "bill_number": f"VB-FIN-{uuid.uuid4().hex[:8]}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "gross_amount": 100.0,
            "gst_percent": 18.0,
            "gst_amount": 18.0,
            "tds_percent": 0.0,
            "tds_amount": 0.0,
            "advance_paid": 0.0,
            "total_amount": 99999.0,
            "cgst": 9.0,
            "sgst": 9.0,
            "igst": 0.0
        }
        res_e = await ac.post("/api/v1/vendor-bills", json=payload_e)
        assert res_e.status_code == 400
        assert "Total amount does not reconcile" in res_e.json()["detail"]

        # F. GST split mismatch
        payload_f = {
            "supplier_id": supplier_id,
            "bill_number": f"VB-FIN-{uuid.uuid4().hex[:8]}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "gross_amount": 1000.0,
            "gst_percent": 18.0,
            "gst_amount": 180.0,
            "tds_percent": 0.0,
            "tds_amount": 0.0,
            "advance_paid": 0.0,
            "total_amount": 1180.0,
            "cgst": 100.0,
            "sgst": 100.0,
            "igst": 0.0
        }
        res_f = await ac.post("/api/v1/vendor-bills", json=payload_f)
        assert res_f.status_code == 422

        # H. Legacy payload
        payload_h = {
            "supplier_id": supplier_id,
            "bill_number": f"VB-FIN-{uuid.uuid4().hex[:8]}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "total_amount": 500.0
        }
        res_h = await ac.post("/api/v1/vendor-bills", json=payload_h)
        assert res_h.status_code == 201
