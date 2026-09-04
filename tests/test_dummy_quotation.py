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
        assert data["items"][0]["quantity"] == 0.0
        assert data["items"][0]["amount"] == 0.0
        assert data["subtotal"] == 0.0
        assert data["grand_total"] == 0.0
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
        assert data["grand_total"] == 0.0
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
        assert upd["cgst_amount"] == 0.0   # 10% of subtotal 0

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
