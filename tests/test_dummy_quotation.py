import pytest
import random
import string
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_current_active_user
from app.models.user import User, UserRole

# ── Auth override ──────────────────────────────────────────────────────────
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


app.dependency_overrides[get_current_active_user] = get_superadmin

# ── Helpers ────────────────────────────────────────────────────────────────

def rand_str(n=8):
    return "".join(random.choices(string.ascii_letters, k=n))


# ── Tests ──────────────────────────────────────────────────────────────────

def test_create_dummy_quotation_empty():
    """POST {} → 201, subtotal=0, no items."""
    with TestClient(app) as tc:
        resp = tc.post("/api/v1/dummy-quotations/", json={})
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert "dummy_quotation_no" in data
        assert data["subtotal"] == 0.0
        assert data["items"] == []


def test_create_dummy_quotation_minimal():
    """POST with one item (title + rate) → 201, correct totals."""
    with TestClient(app) as tc:
        payload = {"items": [{"title": "Item 1", "rate": 100.0}]}
        resp = tc.post("/api/v1/dummy-quotations/", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert "dummy_quotation_no" in data
        assert data["items"][0]["quantity"] == 1.0
        assert data["items"][0]["amount"] == 100.0
        assert data["subtotal"] == 100.0
        assert data["grand_total"] == 100.0
        assert len(data["items"]) == 1


def test_create_dummy_quotation_with_client_details():
    """POST with optional client fields → 201, client_name stored."""
    with TestClient(app) as tc:
        name = "Test Client " + rand_str()
        payload = {
            "client_name": name,
            "mobile_number": "9999999999",
            "email": "test@example.com",
            "items": [{"title": "Item 2", "rate": 200.0}],
        }
        resp = tc.post("/api/v1/dummy-quotations/", json=payload)
        assert resp.status_code == 201, resp.text
        assert "Test Client" in resp.json()["client_name"]


def test_create_dummy_quotation_with_measurements_and_cgst_sgst():
    """
    POST item with measurements + CGST/SGST.
    10×10×10 = 1000 qty × rate 10 = 10 000 subtotal
    CGST 9% = 900, SGST 9% = 900, grand_total = 11 800
    """
    with TestClient(app) as tc:
        payload = {
            "cgst_percent": 9.0,
            "sgst_percent": 9.0,
            "items": [
                {
                    "title": "Item 3",
                    "rate": 10.0,
                    "measurements": [
                        {"length": 10, "width": 10, "height": 10, "unit": "ft"}
                    ],
                }
            ],
        }
        resp = tc.post("/api/v1/dummy-quotations/", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["subtotal"] == 10000.0
        assert data["cgst_amount"] == 900.0
        assert data["sgst_amount"] == 900.0
        assert data["grand_total"] == 11800.0
        assert len(data["items"][0]["measurements"]) == 1


def test_create_dummy_quotation_with_gst_no_double_tax():
    """
    POST with gst_percent=18 (no cgst/sgst) → backend splits to cgst=9+sgst=9.
    grand_total = 11 800, NOT 12 600 (no double-taxation).
    """
    with TestClient(app) as tc:
        payload = {
            "gst_percent": 18.0,
            "items": [
                {
                    "title": "Item GST",
                    "rate": 10.0,
                    "measurements": [
                        {"length": 10, "width": 10, "height": 10, "unit": "ft"}
                    ],
                }
            ],
        }
        resp = tc.post("/api/v1/dummy-quotations/", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["subtotal"] == 10000.0
        assert data["cgst_amount"] == 900.0
        assert data["sgst_amount"] == 900.0
        assert data["grand_total"] == 11800.0


def test_preview_does_not_insert_db_row():
    """POST /preview → 200, dummy_quotation_no='PREVIEW', id=0, no DB insert."""
    with TestClient(app) as tc:
        payload = {
            "client_name": "Preview Client",
            "items": [{"title": "Item 4", "rate": 500.0}],
        }
        resp = tc.post("/api/v1/dummy-quotations/preview", json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["dummy_quotation_no"] == "PREVIEW"
        assert data["client_name"] == "Preview Client"
        assert data["grand_total"] == 500.0
        assert data["id"] == 0
        # Verify preview did NOT create a real row: querying that id=0 must 404
        check = tc.get("/api/v1/dummy-quotations/0")
        assert check.status_code == 404


def test_crud_create_get_update_delete():
    """Full CRUD cycle: create → get → update (recalculates tax) → delete → 404."""
    with TestClient(app) as tc:
        # CREATE
        create_payload = {"items": [{"title": "T1", "rate": 10.0}]}
        create_resp = tc.post("/api/v1/dummy-quotations/", json=create_payload)
        assert create_resp.status_code == 201, create_resp.text
        q_id = create_resp.json()["id"]
        assert isinstance(q_id, int) and q_id > 0

        # GET
        get_resp = tc.get(f"/api/v1/dummy-quotations/{q_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == q_id

        # UPDATE
        update_payload = {"client_name": "Updated Name", "cgst_percent": 10.0}
        upd_resp = tc.put(f"/api/v1/dummy-quotations/{q_id}", json=update_payload)
        assert upd_resp.status_code == 200
        upd = upd_resp.json()
        assert upd["client_name"] == "Updated Name"
        assert upd["cgst_percent"] == 10.0
        assert upd["cgst_amount"] == 1.0

        # DELETE
        del_resp = tc.delete(f"/api/v1/dummy-quotations/{q_id}")
        assert del_resp.status_code == 204

        # 404 after delete
        gone = tc.get(f"/api/v1/dummy-quotations/{q_id}")
        assert gone.status_code == 404


def test_invalid_id_returns_404():
    """GET /dummy-quotations/999999999 → 404."""
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/dummy-quotations/999999999")
        assert resp.status_code == 404


def test_company_isolation():
    """
    A quotation created by superadmin (company_id=None)
    is NOT accessible by a normal tenant (company_id=9999).
    """
    # First create as superadmin
    with TestClient(app) as tc:
        create_payload = {"items": [{"title": "Isol", "rate": 10.0}]}
        create_resp = tc.post("/api/v1/dummy-quotations/", json=create_payload)
        assert create_resp.status_code == 201, create_resp.text
        q_id = create_resp.json()["id"]

    # Now override auth to a different company
    restricted_user = User(
        id=1002,
        full_name="Restricted",
        email="restricted@other.com",
        role=UserRole.ADMIN,
        is_active=True,
        is_super_admin=False,
        company_id=9999,
    )
    app.dependency_overrides[get_current_active_user] = lambda: restricted_user

    try:
        with TestClient(app) as tc:
            get_resp = tc.get(f"/api/v1/dummy-quotations/{q_id}")
            assert get_resp.status_code == 403, get_resp.text
    finally:
        # Restore superadmin override
        app.dependency_overrides[get_current_active_user] = get_superadmin


def test_list_dummy_quotations():
    """GET /api/v1/dummy-quotations/ -> 200, returns list of DummyQuotationOut."""
    with TestClient(app) as tc:
        # First create one
        payload = {"items": [{"title": "List Item", "rate": 50.0}]}
        tc.post("/api/v1/dummy-quotations/", json=payload)
        
        # Now list
        resp = tc.get("/api/v1/dummy-quotations/")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "dummy_quotation_no" in data[0]
def test_dummy_quotation_transport_other_calculations():
    """Verify calculation for transport and other with Decimal arithmetic."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as tc:
        # Both NULL
        payload = {
            "items": [{"title": "T1", "rate": 10000.0}],
            "cgst_percent": 9.0,
            "sgst_percent": 9.0,
        }
        resp = tc.post("/api/v1/dummy-quotations/", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["grand_total"] == 11800.0
        assert data["transport"] is None
        assert data["other"] is None
        q_id = data["id"]
        
        # Transport only
        update_payload = {"transport": 500.0}
        resp2 = tc.put(f"/api/v1/dummy-quotations/{q_id}", json=update_payload)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["grand_total"] == 12300.0
        assert data2["transport"] == 500.0
        assert data2["other"] is None

        # Transport and Other
        update_payload2 = {"other": 300.0}
        resp3 = tc.put(f"/api/v1/dummy-quotations/{q_id}", json=update_payload2)
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3["grand_total"] == 12600.0
        assert data3["transport"] == 500.0
        assert data3["other"] == 300.0


def test_preview_pdf_success_and_db_isolation():
    """POST /preview/pdf returns valid PDF, inline disposition, and no DB records created."""
    with TestClient(app) as tc:
        list_resp_before = tc.get("/api/v1/dummy-quotations/")
        count_before = 0
        if list_resp_before.status_code == 200:
            count_before = len(list_resp_before.json()) if isinstance(list_resp_before.json(), list) else len(list_resp_before.json().get("items", []))

        payload = {
            "client_name": "PDF Preview Client",
            "items": [{"title": "Item 1", "rate": 100.0}],
        }
        resp = tc.post("/api/v1/dummy-quotations/preview/pdf", json=payload)

        assert resp.status_code == 200
        assert resp.headers.get("Content-Type") == "application/pdf"
        assert "inline" in resp.headers.get("Content-Disposition", "")
        assert "dummy_quotation_preview.pdf" in resp.headers.get("Content-Disposition", "")

        content = resp.content
        assert content.startswith(b"%PDF")

        list_resp_after = tc.get("/api/v1/dummy-quotations/")
        if list_resp_after.status_code == 200:
            count_after = len(list_resp_after.json()) if isinstance(list_resp_after.json(), list) else len(list_resp_after.json().get("items", []))
            assert count_after == count_before

def test_preview_pdf_transport_other():
    """Test that transport and other are accepted and do not break the PDF generation."""
    with TestClient(app) as tc:
        payload = {
            "client_name": "PDF Transport Client",
            "transport": 50.0,
            "other": 25.0,
            "items": [{"title": "Item 2", "rate": 100.0}],
        }
        json_resp = tc.post("/api/v1/dummy-quotations/preview", json=payload)
        assert json_resp.status_code == 200
        assert json_resp.json()["transport"] == 50.0
        assert json_resp.json()["other"] == 25.0
        assert json_resp.json()["grand_total"] == 175.0

        pdf_resp = tc.post("/api/v1/dummy-quotations/preview/pdf", json=payload)
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers.get("Content-Type") == "application/pdf"
        assert pdf_resp.content.startswith(b"%PDF")

def test_preview_pdf_invalid_payload():
    """Missing required fields or invalid types should return 422."""
    with TestClient(app) as tc:
        payload = {
            "items": [{"title": "Item 3", "rate": "invalid_string"}]
        }
        resp = tc.post("/api/v1/dummy-quotations/preview/pdf", json=payload)
        assert resp.status_code == 422

def test_preview_pdf_unauthorized():
    """Unauthorized users should be rejected."""
    original_override = app.dependency_overrides.pop(get_current_active_user, None)
    with TestClient(app) as tc:
        resp = tc.post("/api/v1/dummy-quotations/preview/pdf", json={"items": []})
        assert resp.status_code in [401, 403]

    if original_override:
        app.dependency_overrides[get_current_active_user] = original_override
