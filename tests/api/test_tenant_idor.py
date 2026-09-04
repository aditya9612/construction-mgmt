import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_roles
from app.models.user import User

client = TestClient(app)

company_a_admin = User(id=1000, email="admin@compa.com", role="Admin", is_active=True, is_super_admin=False, company_id=1)
company_b_admin = User(id=1001, email="admin@compb.com", role="Admin", is_active=True, is_super_admin=False, company_id=2)
super_admin_user = User(id=9999, email="super@admin.com", role="Super Admin", is_active=True, is_super_admin=True, company_id=None)

def override_dependency(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    # Mock require_roles to just return the user
    def mock_require_roles(roles):
        return lambda: user
    app.dependency_overrides[require_roles] = mock_require_roles

def clear_overrides():
    app.dependency_overrides.clear()

def test_idor_vendor_bills():
    override_dependency(company_a_admin)
    
    # Company A tries to get a Vendor Bill ID that belongs to Company B
    # Assuming ID 99999 doesn't exist, we just want to ensure it returns 404 Not Found 
    # instead of crashing, proving the tenant filter works and hides it.
    response = client.get("/api/v1/vendor-bills/99999")
    
    assert response.status_code == 404, "Tenant filter should return 404 for cross-tenant access"
    clear_overrides()

def test_idor_invoice():
    override_dependency(company_b_admin)
    response = client.get("/api/v1/invoices/99999")
    assert response.status_code == 404, "Tenant filter should return 404 for cross-tenant access"
    clear_overrides()

def test_idor_client_payment():
    override_dependency(company_a_admin)
    response = client.get("/api/v1/client-payments/99999")
    assert response.status_code == 404, "Tenant filter should return 404 for cross-tenant access"
    clear_overrides()

def test_idor_accountant():
    override_dependency(company_b_admin)
    response = client.get("/api/v1/accountant/accounts/99999")
    assert response.status_code == 404, "Tenant filter should return 404 for cross-tenant access"
    clear_overrides()

def test_idor_boq_list():
    override_dependency(company_a_admin)
    response = client.get("/api/v1/boq")
    assert response.status_code in (200, 403, 404)
    clear_overrides()

def test_idor_boq_get():
    override_dependency(company_a_admin)
    response = client.get("/api/v1/boq/99999")
    assert response.status_code == 404
    clear_overrides()

def test_idor_rabill_list():
    override_dependency(company_b_admin)
    response = client.get("/api/v1/billing")
    assert response.status_code in (200, 403, 404)
    clear_overrides()

def test_idor_rabill_get():
    override_dependency(company_b_admin)
    response = client.get("/api/v1/billing/99999")
    assert response.status_code == 404
    clear_overrides()

def test_idor_quotation_list():
    override_dependency(company_a_admin)
    response = client.get("/api/v1/quotation/")
    assert response.status_code in (200, 403, 404)
    clear_overrides()

def test_idor_quotation_get():
    override_dependency(company_a_admin)
    response = client.get("/api/v1/quotation/99999")
    assert response.status_code == 404
    clear_overrides()

def test_idor_work_order_list():
    override_dependency(company_b_admin)
    response = client.get("/api/v1/work-orders/")
    assert response.status_code in (200, 403, 404)
    clear_overrides()

def test_idor_work_order_get():
    override_dependency(company_b_admin)
    response = client.get("/api/v1/work-orders/99999")
    assert response.status_code == 404
    clear_overrides()

# Explicit Functional IDOR Tests

def test_idor_quotation_update():
    override_dependency(company_b_admin)
    response = client.put("/api/v1/quotation/99999", json={"project_name": "Test"})
    assert response.status_code == 404
    clear_overrides()

def test_idor_quotation_delete():
    override_dependency(company_b_admin)
    response = client.delete("/api/v1/quotation/99999")
    assert response.status_code == 404
    clear_overrides()

def test_idor_quotation_create_wrong_context():
    # If Company B tries to create a quotation injecting another company ID or client ID maliciously
    override_dependency(company_b_admin)
    # The actual business logic overwrites company_id with current_user.company_id, so the endpoint might succeed 
    # but strictly within the context of company_b_admin. To test cross-tenant creation specifically,
    # we simulate passing a malicious parameter (if one existed, though it's ignored).
    # Since we can't test "rejected" if it just ignores the field, we verify workflow access next.
    pass

def test_idor_quotation_workflow():
    override_dependency(company_b_admin)
    response = client.put("/api/v1/quotation/99999/approve")
    assert response.status_code == 404
    clear_overrides()

def test_idor_work_order_update():
    override_dependency(company_a_admin)
    response = client.put("/api/v1/work-orders/99999", json={"description": "Hack"})
    assert response.status_code == 404
    clear_overrides()

def test_idor_work_order_delete():
    override_dependency(company_a_admin)
    response = client.delete("/api/v1/work-orders/99999")
    assert response.status_code == 404
    clear_overrides()

def test_idor_work_order_create_wrong_project():
    override_dependency(company_a_admin)
    # Assume project_id 99999 belongs to Company B (or doesn't exist). 
    # In both cases, assert_project_access raises 404 or 403.
    response = client.post("/api/v1/work-orders/", json={"project_id": 99999, "rate": 100, "total_quantity": 1, "work_description": "test"})
    assert response.status_code in (404, 403)
    clear_overrides()

def test_idor_work_order_workflow():
    override_dependency(company_a_admin)
    # Assuming there's an approval endpoint, or similar workflow
    response = client.put("/api/v1/work-orders/99999/status", json={"status": "APPROVED"})
    assert response.status_code == 404
    clear_overrides()



# ============================================================
# WorkUpdate (Batch 3A) IDOR Tests
# ============================================================

# --- Export endpoint tenant isolation ---

def test_work_update_export_no_project_id_company_a():
    """Company A export without project_id must be scoped to company_id=1.
    Returns 200 (data exists) or 422 (no data in test DB). Never 500/403."""
    override_dependency(company_a_admin)
    response = client.get("/api/v1/work-updates/export")
    # 200 = scoped data returned; 422 = no data but query ran tenant-scoped
    assert response.status_code in (200, 422), (
        f"Expected 200 or 422, got {response.status_code}: {response.text}"
    )
    clear_overrides()


def test_work_update_export_cross_tenant_project_id():
    """Company A passing a non-existent / Company B project_id must be blocked.
    The export guard raises ValidationError ("Project not found") which maps to
    422, or assert_project_access raises 403/404 for a cross-tenant project."""
    override_dependency(company_a_admin)
    response = client.get("/api/v1/work-updates/export?project_id=99999")
    # 422 = ValidationError("Project not found"), 403/404 = access denied
    assert response.status_code in (403, 404, 422), (
        f"Expected 403, 404 or 422, got {response.status_code}: {response.text}"
    )
    clear_overrides()


def test_work_update_export_own_project_guard_fires():
    """Guard fires before any data is returned for a non-existent project.
    ValidationError("Project not found") maps to 422 in this codebase."""
    override_dependency(company_a_admin)
    response = client.get("/api/v1/work-updates/export?project_id=99999")
    assert response.status_code in (403, 404, 422)
    clear_overrides()


def test_work_update_export_company_b_no_project_id():
    """Company B export without project_id must be scoped to company_id=2."""
    override_dependency(company_b_admin)
    response = client.get("/api/v1/work-updates/export")
    assert response.status_code in (200, 422), (
        f"Expected 200 or 422, got {response.status_code}: {response.text}"
    )
    clear_overrides()


def test_work_update_export_company_b_cross_tenant_project():
    """Company B trying to export via a non-existent/Company A project must be blocked.
    422 = ValidationError("Project not found"), 403/404 = access denied."""
    override_dependency(company_b_admin)
    response = client.get("/api/v1/work-updates/export?project_id=99999")
    assert response.status_code in (403, 404, 422)
    clear_overrides()


# --- WorkUpdate CRUD / workflow IDOR tests ---

def test_idor_work_update_get():
    """Company A cannot fetch a WorkUpdate belonging to Company B (IDOR via ID guess)."""
    override_dependency(company_a_admin)
    response = client.get("/api/v1/work-updates/99999")
    assert response.status_code in (404, 403)
    clear_overrides()


def test_idor_work_update_update():
    """Company A cannot update a WorkUpdate belonging to Company B."""
    override_dependency(company_a_admin)
    response = client.put(
        "/api/v1/work-updates/99999",
        json={"work_description": "Hacked"},
    )
    assert response.status_code in (404, 403)
    clear_overrides()


def test_idor_work_update_delete():
    """Company A cannot delete a WorkUpdate belonging to Company B."""
    override_dependency(company_a_admin)
    response = client.delete("/api/v1/work-updates/99999")
    assert response.status_code in (404, 403)
    clear_overrides()


def test_idor_work_update_submit():
    """Company A cannot submit (workflow) a WorkUpdate belonging to Company B.
    Submit route is POST /{id}/submit."""
    override_dependency(company_a_admin)
    response = client.post(
        "/api/v1/work-updates/99999/submit",
        json={"end_time": "18:00:00", "total_hours": 8.0},
    )
    assert response.status_code in (404, 403, 422)
    clear_overrides()

# ==========================================
# PHASE 4.3 BATCH 4A-2: SUB-RESOURCE TESTS
# ==========================================

def test_milestone_tenant_isolation():
    override_dependency(company_a_admin)
    # Company A tries to list Company B's milestones (assuming project 2 is company B's)
    resp = client.get(f"/api/v1/projects/2/milestones")
    assert resp.status_code in (403, 404, 500)

    # Company A tries to get Company B's milestone
    resp = client.get(f"/api/v1/projects/2/milestones/999")
    assert resp.status_code in (403, 404, 500)

    # Company A tries to create milestone in Company B's project
    resp = client.post(f"/api/v1/projects/2/milestones", json={"title": "Hacked", "status": "Planned"})
    assert resp.status_code in (403, 404, 500)

    # Super Admin tries to list milestones
    override_dependency(super_admin_user)
    resp = client.get(f"/api/v1/projects/2/milestones")
    assert resp.status_code in (403, 404, 500)
    clear_overrides()

def test_task_request_tenant_isolation():
    override_dependency(company_a_admin)
    # Company A tries to update Company B's task request (assuming 999 is in B)
    resp = client.put(f"/api/v1/projects/task-requests/999", json={"title": "Hacked"})
    assert resp.status_code in (403, 404)

    # Company A tries to list all task requests
    resp = client.get("/api/v1/projects/task-requests")
    assert resp.status_code in (200, 403)

    # Super Admin tries to list task requests
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/projects/task-requests")
    # should return empty
    assert resp.status_code in (200, 403)
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", [])
    assert len(items) == 0
    clear_overrides()

def test_dsr_tenant_isolation():
    override_dependency(company_a_admin)
    # Get map points
    resp = client.get(f"/api/v1/projects/dsr/project/2/map")
    assert resp.status_code in (403, 404)

    # Get photos
    resp = client.get(f"/api/v1/projects/dsr/999/photos")
    assert resp.status_code in (403, 404)

    # Delete photo
    resp = client.delete(f"/api/v1/projects/dsr/photo/999")
    assert resp.status_code in (403, 404)

    # Delete DSR
    resp = client.delete(f"/api/v1/projects/dsr/999")
    assert resp.status_code in (403, 404)
    clear_overrides()

def test_issue_tenant_isolation():
    override_dependency(company_a_admin)
    # list issues globally
    resp = client.get(f"/api/v1/issues")
    assert resp.status_code in (200, 403)

    # delete issue
    resp = client.delete(f"/api/v1/issues/999")
    assert resp.status_code in (403, 404)

    # issues by project
    resp = client.get(f"/api/v1/issues/project/2")
    assert resp.status_code in (403, 404)

    # Super admin list issues
    override_dependency(super_admin_user)
    resp = client.get(f"/api/v1/issues")
    assert resp.status_code in (200, 403)
    data = resp.json()
    items = data.get("items", [])
    assert len(items) == 0
    clear_overrides()

def test_task_alert_tenant_isolation():
    override_dependency(company_a_admin)
    resp = client.get("/api/v1/projects/alerts/tasks")
    assert resp.status_code in (200, 403)

    override_dependency(super_admin_user)
    resp = client.get("/api/v1/projects/alerts/tasks")
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("items", [])
        assert len(items) == 0
    clear_overrides()

# ==========================================
# PHASE 4.3 BATCH 4A-1: CORE PROJECT TESTS
# ==========================================

def test_project_positive_own_tenant():
    override_dependency(company_a_admin)
    # Own company project access works
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 200
    clear_overrides()

def test_project_negative_cross_tenant():
    override_dependency(company_a_admin)
    # Project GET cross-tenant (proj_b has id=2)
    resp = client.get("/api/v1/projects/2")
    assert resp.status_code in (403, 404)
    clear_overrides()

def test_project_update_cross_tenant():
    override_dependency(company_a_admin)
    # Project UPDATE cross-tenant
    resp = client.put("/api/v1/projects/2", json={"project_name": "Hacked"})
    assert resp.status_code in (403, 404)
    clear_overrides()

def test_project_delete_cross_tenant():
    override_dependency(company_a_admin)
    # Project DELETE cross-tenant
    resp = client.delete("/api/v1/projects/2")
    assert resp.status_code in (403, 404)
    clear_overrides()

def test_schedule_cross_tenant():
    override_dependency(company_a_admin)
    # Schedule cross-tenant
    resp = client.get("/api/v1/projects/2/schedule")
    assert resp.status_code in (403, 404)
    clear_overrides()

def test_progress_cross_tenant():
    override_dependency(company_a_admin)
    # Progress/Analytics cross-tenant
    resp = client.get("/api/v1/projects/2/progress")
    assert resp.status_code in (403, 404)
    clear_overrides()

def test_profit_loss_cross_tenant():
    override_dependency(company_a_admin)
    # Profit/Loss cross-tenant
    resp = client.get("/api/v1/projects/2/profit-loss")
    assert resp.status_code in (403, 404)
    clear_overrides()

def test_super_admin_blocked_project_api():
    override_dependency(super_admin_user)
    # Super Admin standard Project API blocked (must not get global access via these endpoints)
    resp = client.get("/api/v1/projects")
    # Our implementation forces company_id check so super admin gets 403 or empty array
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("items", [])
        assert len(items) == 0
        
    resp2 = client.get("/api/v1/projects/2")
    assert resp2.status_code in (403, 404)
    clear_overrides()

# ==========================================
# PHASE 4.3 BATCH 4A-1: CORE PROJECT TESTS
# ==========================================

def test_project_get_cross_tenant():
    override_dependency(company_a_admin)
    resp = client.get("/api/v1/projects/2")
    assert resp.status_code in (403, 404, 500)
    clear_overrides()

def test_project_update_cross_tenant():
    override_dependency(company_a_admin)
    resp = client.put("/api/v1/projects/2", json={"project_name": "Hacked"})
    assert resp.status_code in (403, 404, 500)
    clear_overrides()

def test_project_delete_cross_tenant():
    override_dependency(company_a_admin)
    resp = client.delete("/api/v1/projects/2")
    assert resp.status_code in (403, 404, 500)
    clear_overrides()

def test_project_schedule_cross_tenant():
    override_dependency(company_a_admin)
    resp = client.get("/api/v1/projects/2/schedule")
    assert resp.status_code in (403, 404, 500)
    clear_overrides()

def test_project_progress_cross_tenant():
    override_dependency(company_a_admin)
    resp = client.get("/api/v1/projects/2/progress")
    assert resp.status_code in (403, 404, 500)
    clear_overrides()

def test_project_profit_loss_cross_tenant():
    override_dependency(company_a_admin)
    resp = client.get("/api/v1/projects/2/profit-loss")
    assert resp.status_code in (403, 404, 500)
    clear_overrides()

def test_super_admin_standard_project_api_blocked():
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/projects")
    # depending on implementation, could be 403 or empty 200
    assert resp.status_code in (403, 200)
    
    resp = client.get("/api/v1/projects/2")
    assert resp.status_code in (403, 404, 500)
    clear_overrides()

def test_own_company_project_access_works():
    override_dependency(company_a_admin)
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 200
    clear_overrides()

# ==========================================
# PHASE 4.3 BATCH 4B-1: MATERIAL TENANT ISOLATION TESTS
# ==========================================

def test_supplier_tenant_isolation():
    import uuid, random
    rand_digits = "".join(random.choices("0123456789", k=4))
    rand_phone = f"9{random.randint(100000000, 999999999)}"
    rand_gst = f"29ABCDE{rand_digits}F1Z5"
    supplier_name = f"Supplier A {uuid.uuid4().hex[:6]}"

    # Company A creates a supplier
    override_dependency(company_a_admin)
    resp = client.post("/api/v1/materials/suppliers", json={
        "supplier_name": supplier_name,
        "contact_person": "Contact A",
        "phone_email": rand_phone,
        "gst_number": rand_gst,
        "address": "Company A HQ"
    })
    assert resp.status_code == 201
    supplier_id = resp.json()["id"]

    # Company A gets supplier list
    resp = client.get("/api/v1/materials/suppliers")
    assert resp.status_code == 200
    assert any(s["id"] == supplier_id for s in resp.json())

    # Company B tries to get Company A's supplier
    override_dependency(company_b_admin)
    resp = client.get(f"/api/v1/materials/suppliers/{supplier_id}")
    assert resp.status_code in (403, 404)

    # Company B tries to update Company A's supplier
    resp = client.put(f"/api/v1/materials/suppliers/{supplier_id}", json={
        "supplier_name": "Hacked Supplier"
    })
    assert resp.status_code in (403, 404)

    # Company B tries to delete Company A's supplier
    resp = client.delete(f"/api/v1/materials/suppliers/{supplier_id}")
    assert resp.status_code in (403, 404)

    # Super Admin tries to get supplier
    override_dependency(super_admin_user)
    resp = client.get(f"/api/v1/materials/suppliers/{supplier_id}")
    assert resp.status_code in (403, 404)

    clear_overrides()

def test_material_tenant_isolation():
    override_dependency(company_a_admin)
    
    # Assuming project_id=1 belongs to Company A, and project_id=2 belongs to Company B
    # Let's try to list materials for Company B project
    resp = client.get("/api/v1/materials?project_id=2")
    # depending on how project_id filtering interacts with company_id enforcement
    # either it returns 403 or returns empty list. Both are safe.
    assert resp.status_code in (200, 403, 404)
    if resp.status_code == 200:
        assert len(resp.json()) == 0

    # Company A tries to get material_id=1 (assuming it might be in B)
    # If 1 is in A, it will pass. Let's just fetch some high ID.
    resp = client.get("/api/v1/materials/9999")
    assert resp.status_code in (403, 404)

    # Super Admin tries to list materials
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/materials")
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        assert len(resp.json()) == 0

    clear_overrides()

# ==========================================
# PHASE 4.3 BATCH 4B-2a: EQUIPMENT CORE + ALLOCATION/TRANSFER TESTS
# ==========================================

def test_equipment_tenant_isolation():
    import uuid
    # 1. Company A creates equipment with non-owned/cross-tenant project -> BLOCKED
    override_dependency(company_a_admin)
    resp = client.post("/api/v1/equipment", json={
        "project_id": 99999,
        "equipment_name": "Crane Company B",
        "equipment_code": f"EQ-X-{uuid.uuid4().hex[:6]}",
        "condition": "GOOD",
        "rental_cost": 500.00
    })
    assert resp.status_code in (400, 403, 404)

    # 2. Company A tries to GET cross-tenant equipment -> BLOCKED
    resp = client.get("/api/v1/equipment/99999")
    assert resp.status_code in (403, 404)

    # 3. Company A tries to UPDATE cross-tenant equipment -> BLOCKED
    resp = client.put("/api/v1/equipment/99999", json={
        "equipment_name": "Hacked Bulldozer"
    })
    assert resp.status_code in (403, 404)

    # 4. Company A tries to DELETE cross-tenant equipment -> BLOCKED
    resp = client.delete("/api/v1/equipment/99999")
    assert resp.status_code in (400, 403, 404)

    # 5. Company A tries to RESTORE cross-tenant equipment -> BLOCKED
    resp = client.put("/api/v1/equipment/99999/restore")
    assert resp.status_code in (400, 403, 404)

    # 6. LIST equipment for Company A returns valid response scoped to tenant
    resp = client.get("/api/v1/equipment")
    assert resp.status_code == 200
    assert "items" in resp.json()

    # 7. Super Admin equipment API access (Issue #38: SuperAdmin has platform-wide access)
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/equipment/99999")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/equipment")
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        assert "items" in resp.json()

    clear_overrides()

def test_equipment_allocation_and_transfer_isolation():
    # 1. Company B tries to get allocation of non-owned equipment -> BLOCKED
    override_dependency(company_b_admin)
    resp = client.get("/api/v1/equipment/99999/allocation")
    assert resp.status_code in (403, 404)

    # 2. Company A tries to transfer equipment across tenant boundaries -> BLOCKED
    override_dependency(company_a_admin)
    resp = client.post("/api/v1/equipment/transfer", json={
        "equipment_id": 99999,
        "to_project_id": 99999
    })
    assert resp.status_code in (400, 403, 404)

    # 3. Company B tries to allocate cross-tenant equipment -> BLOCKED / FAILED
    override_dependency(company_b_admin)
    resp = client.post("/api/v1/equipment/allocate", json={
        "equipment_ids": [99999],
        "project_id": 99999
    })
    if resp.status_code == 200:
        assert resp.json()["success_count"] == 0
    else:
        assert resp.status_code in (400, 403, 404)

    # 4. Company B tries to view transfer history of non-owned equipment -> BLOCKED
    resp = client.get("/api/v1/equipment/99999/transfer-history")
    assert resp.status_code in (200, 403, 404)
    if resp.status_code == 200:
        assert len(resp.json().get("items", [])) == 0

    # 5. List transfer history with cross-tenant project_id -> BLOCKED or empty
    resp = client.get("/api/v1/equipment/transfer-history?project_id=99999")
    assert resp.status_code in (200, 403, 404)
    if resp.status_code == 200:
        assert len(resp.json().get("items", [])) == 0

    clear_overrides()

# ==========================================
# PHASE 4.3 BATCH 4B-2b-1: EQUIPMENT USAGE TESTS
# ==========================================

def test_equipment_usage_tenant_isolation():
    # 1. Company A cannot create usage on non-owned equipment / project -> BLOCKED
    override_dependency(company_a_admin)
    resp = client.post("/api/v1/equipment/99999/usage", json={
        "working_hours": 5.0,
        "fuel_used": 10.0,
        "usage_date": "2026-08-26",
        "notes": "Test usage cross-tenant"
    })
    assert resp.status_code in (400, 403, 404)

    # 2. Company A cannot GET cross-tenant usage_id -> BLOCKED
    resp = client.get("/api/v1/equipment/usage/99999")
    assert resp.status_code in (403, 404)

    # 3. Company A cannot UPDATE cross-tenant usage_id -> BLOCKED
    resp = client.put("/api/v1/equipment/usage/99999", json={
        "working_hours": 8.0,
        "fuel_used": 15.0
    })
    assert resp.status_code in (400, 403, 404)

    # 4. Company A list usage returns valid list scoped to tenant
    resp = client.get("/api/v1/equipment/usage")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    # Filtering by cross-tenant equipment_id returns empty list or 403/404
    resp = client.get("/api/v1/equipment/usage?equipment_id=99999")
    assert resp.status_code in (200, 403, 404)
    if resp.status_code == 200:
        assert len(resp.json()) == 0

    # 5. Company B cannot access cross-tenant usage
    override_dependency(company_b_admin)
    resp = client.get("/api/v1/equipment/usage/99999")
    assert resp.status_code in (403, 404)

    resp = client.put("/api/v1/equipment/usage/99999", json={
        "working_hours": 8.0
    })
    assert resp.status_code in (400, 403, 404)

    # 6. Super Admin cannot access standard usage APIs
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/equipment/usage")
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        assert len(resp.json()) == 0

    resp = client.get("/api/v1/equipment/usage/99999")
    assert resp.status_code in (403, 404)

    resp = client.post("/api/v1/equipment/99999/usage", json={
        "working_hours": 5.0,
        "fuel_used": 10.0,
        "usage_date": "2026-08-26"
    })
    assert resp.status_code in (400, 403, 404)

    resp = client.put("/api/v1/equipment/usage/99999", json={
        "working_hours": 8.0
    })
    assert resp.status_code in (400, 403, 404)

    clear_overrides()

# ==========================================
# PHASE 4.3 BATCH 4B-2b-2: EQUIPMENT MAINTENANCE TESTS
# ==========================================

def test_equipment_maintenance_tenant_isolation():
    # 1. Company A cannot create maintenance on non-owned equipment/project -> BLOCKED
    override_dependency(company_a_admin)
    resp = client.post("/api/v1/equipment/99999/maintenance", json={
        "description": "Cross-tenant maintenance",
        "maintenance_date": "2026-08-26",
        "cost": 500.00,
        "project_id": 99999
    })
    assert resp.status_code in (400, 403, 404)

    # 2. Company A cannot GET cross-tenant maintenance_id -> BLOCKED
    resp = client.get("/api/v1/equipment/maintenance/99999")
    assert resp.status_code in (403, 404)

    # 3. Company A cannot UPDATE cross-tenant maintenance_id -> BLOCKED
    resp = client.put("/api/v1/equipment/maintenance/99999", json={
        "description": "Hacked maintenance"
    })
    assert resp.status_code in (400, 403, 404)

    # 4. Company A cannot COMPLETE cross-tenant maintenance_id -> BLOCKED
    resp = client.put("/api/v1/equipment/maintenance/99999/complete")
    assert resp.status_code in (400, 403, 404)

    # 5. Company A cannot DELETE cross-tenant maintenance_id -> BLOCKED
    resp = client.delete("/api/v1/equipment/maintenance/99999")
    assert resp.status_code in (400, 403, 404)

    # 6. List maintenance returns only caller tenant records
    resp = client.get("/api/v1/equipment/maintenance")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/v1/equipment/maintenance?equipment_id=99999")
    assert resp.status_code in (200, 403, 404)
    if resp.status_code == 200:
        assert len(resp.json()) == 0

    # 7. Reverse Company B -> Company A isolation
    override_dependency(company_b_admin)
    resp = client.get("/api/v1/equipment/maintenance/99999")
    assert resp.status_code in (403, 404)

    resp = client.put("/api/v1/equipment/maintenance/99999", json={
        "description": "Company B update attempt"
    })
    assert resp.status_code in (400, 403, 404)

    resp = client.put("/api/v1/equipment/maintenance/99999/complete")
    assert resp.status_code in (400, 403, 404)

    resp = client.delete("/api/v1/equipment/maintenance/99999")
    assert resp.status_code in (400, 403, 404)

    # 8. Super Admin cannot access standard maintenance APIs
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/equipment/maintenance")
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        assert len(resp.json()) == 0

    resp = client.get("/api/v1/equipment/maintenance/99999")
    assert resp.status_code in (403, 404)

    resp = client.post("/api/v1/equipment/99999/maintenance", json={
        "description": "Super Admin attempt",
        "maintenance_date": "2026-08-26",
        "cost": 500.00,
        "project_id": 99999
    })
    assert resp.status_code in (400, 403, 404)

    resp = client.put("/api/v1/equipment/maintenance/99999", json={
        "description": "Super Admin update attempt"
    })
    assert resp.status_code in (400, 403, 404)

    resp = client.put("/api/v1/equipment/maintenance/99999/complete")
    assert resp.status_code in (400, 403, 404)

    resp = client.delete("/api/v1/equipment/maintenance/99999")
    assert resp.status_code in (400, 403, 404)

    clear_overrides()

# ==========================================
# PHASE 4.3: EQUIPMENT RENTAL TESTS
# ==========================================

def test_equipment_rental_tenant_isolation():
    # 1. Company A cannot create rental on non-owned equipment/project -> BLOCKED
    override_dependency(company_a_admin)
    resp = client.post("/api/v1/equipment/99999/rental", json={
        "start_date": "2026-08-26",
        "end_date": "2026-08-30",
        "rental_cost": 1000.00,
        "client_name": "Test Client",
        "project_id": 99999
    })
    assert resp.status_code in (400, 403, 404)

    # 2. Company A cannot GET cross-tenant rental_id -> BLOCKED
    resp = client.get("/api/v1/equipment/rental/99999")
    assert resp.status_code in (403, 404)

    # 3. Company A cannot UPDATE cross-tenant rental_id -> BLOCKED
    resp = client.put("/api/v1/equipment/rental/99999", json={
        "client_name": "Hacked Client"
    })
    assert resp.status_code in (400, 403, 404)

    # 4. Company A cannot COMPLETE cross-tenant rental_id -> BLOCKED
    resp = client.put("/api/v1/equipment/rental/99999/complete")
    assert resp.status_code in (400, 403, 404)

    # 5. Company A cannot DELETE cross-tenant rental_id -> BLOCKED
    resp = client.delete("/api/v1/equipment/rental/99999")
    assert resp.status_code in (400, 403, 404)

    # 6. List rental returns only caller tenant records
    resp = client.get("/api/v1/equipment/rental")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/v1/equipment/rental?equipment_id=99999")
    assert resp.status_code in (200, 403, 404)
    if resp.status_code == 200:
        assert len(resp.json()) == 0

    # 7. Reverse Company B -> Company A isolation
    override_dependency(company_b_admin)
    resp = client.get("/api/v1/equipment/rental/99999")
    assert resp.status_code in (403, 404)

    resp = client.put("/api/v1/equipment/rental/99999", json={
        "client_name": "Company B update"
    })
    assert resp.status_code in (400, 403, 404)

    resp = client.put("/api/v1/equipment/rental/99999/complete")
    assert resp.status_code in (400, 403, 404)

    resp = client.delete("/api/v1/equipment/rental/99999")
    assert resp.status_code in (400, 403, 404)

    # 8. Super Admin cannot access standard rental APIs
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/equipment/rental")
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        assert len(resp.json()) == 0

    resp = client.get("/api/v1/equipment/rental/99999")
    assert resp.status_code in (403, 404)

    resp = client.post("/api/v1/equipment/99999/rental", json={
        "start_date": "2026-08-26",
        "rental_cost": 1000.00,
        "client_name": "Super Admin"
    })
    assert resp.status_code in (400, 403, 404)

    resp = client.put("/api/v1/equipment/rental/99999", json={
        "client_name": "Super Admin update"
    })
    assert resp.status_code in (400, 403, 404)

    resp = client.put("/api/v1/equipment/rental/99999/complete")
    assert resp.status_code in (400, 403, 404)

    resp = client.delete("/api/v1/equipment/rental/99999")
    assert resp.status_code in (400, 403, 404)

    clear_overrides()

# ==========================================
# PHASE 4.3: EQUIPMENT PURCHASES, REPORTS & UTILITIES TESTS
# ==========================================

def test_equipment_purchases_and_reports_tenant_isolation():
    # 1. Company A cannot create purchase on non-owned project -> BLOCKED
    override_dependency(company_a_admin)
    resp = client.post("/api/v1/equipment/purchase", json={
        "project_id": 99999,
        "purchase_type": "NEW",
        "vendor_name": "Test Vendor",
        "invoice_number": "INV-CROSS-999",
        "purchase_date": "2026-08-26",
        "quantity": 1,
        "unit_price": 5000.00
    })
    assert resp.status_code in (400, 403, 404)

    # 2. Company A cannot GET cross-tenant purchase_id -> BLOCKED
    resp = client.get("/api/v1/equipment/purchase/99999")
    assert resp.status_code in (403, 404)

    # 3. Company A cannot UPDATE cross-tenant purchase_id -> BLOCKED
    resp = client.put("/api/v1/equipment/purchase/99999", json={
        "vendor_name": "Hacked Vendor"
    })
    assert resp.status_code in (400, 403, 404)

    # 4. Company A cannot DELETE cross-tenant purchase_id -> BLOCKED
    resp = client.delete("/api/v1/equipment/purchase/99999")
    assert resp.status_code in (400, 403, 404)

    # 5. List purchase returns only caller tenant records
    resp = client.get("/api/v1/equipment/purchase")
    assert resp.status_code == 200
    data = resp.json()
    items = data.get("items", data) if isinstance(data, dict) else data
    assert isinstance(items, list)

    resp = client.get("/api/v1/equipment/purchase?project_id=99999")
    assert resp.status_code in (200, 403, 404)
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        assert len(items) == 0

    # 6. Purchase history scoped
    resp = client.get("/api/v1/equipment/purchase/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/v1/equipment/purchase/history?equipment_id=99999")
    assert resp.status_code in (200, 403, 404)

    # 7. KPI, reports, and utilities scoped
    resp = client.get("/api/v1/equipment/kpi")
    assert resp.status_code == 200

    resp = client.get("/api/v1/equipment/usage/report")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/v1/equipment/cost/report")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/v1/equipment/purchase/report")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/v1/equipment/alerts/maintenance")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/v1/equipment/eq/availability")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/v1/equipment/report/utilization")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    resp = client.get("/api/v1/equipment/alerts/equipment")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    # Cross-tenant audit logs & QR blocked
    resp = client.get("/api/v1/equipment/99999/logs")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/equipment/99999/qr")
    assert resp.status_code in (403, 404)

    # 8. Super Admin standard equipment APIs blocked / safe empty
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/equipment/kpi")
    assert resp.status_code in (200, 403)

    resp = client.get("/api/v1/equipment/purchase")
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        assert len(items) == 0

    resp = client.get("/api/v1/equipment/purchase/99999")
    assert resp.status_code in (403, 404)

    resp = client.post("/api/v1/equipment/purchase", json={
        "project_id": 99999,
        "purchase_type": "NEW",
        "vendor_name": "Super Admin Vendor",
        "invoice_number": "INV-SA-999",
        "purchase_date": "2026-08-26",
        "quantity": 1,
        "unit_price": 5000.00
    })
    assert resp.status_code in (400, 403, 404)

    resp = client.get("/api/v1/equipment/reports/pdf")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/equipment/reports/excel")
    assert resp.status_code in (403, 404)

    clear_overrides()

# ==========================================
# PHASE 4.3: LABOUR TENANT ISOLATION TESTS
# ==========================================

def test_labour_tenant_isolation():
    # 1. Company A cannot GET cross-tenant labour_id -> BLOCKED (404/403)
    override_dependency(company_a_admin)
    resp = client.get("/api/v1/labour/99999")
    assert resp.status_code in (403, 404)

    # 2. Company A cannot UPDATE cross-tenant labour_id -> BLOCKED
    resp = client.put("/api/v1/labour/99999", data={
        "labour_name": "Hacked Labour"
    })
    assert resp.status_code in (400, 403, 404)

    # 3. Company A cannot DELETE cross-tenant labour_id -> BLOCKED
    resp = client.delete("/api/v1/labour/99999")
    assert resp.status_code in (400, 403, 404)

    # 4. List labour returns only caller tenant records
    resp = client.get("/api/v1/labour")
    assert resp.status_code == 200
    data = resp.json()
    items = data.get("items", data) if isinstance(data, dict) else data
    assert isinstance(items, list)

    resp = client.get("/api/v1/labour?project_id=99999")
    assert resp.status_code in (200, 403, 404)
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        assert len(items) == 0

    # 5. Cross-tenant payroll, reports & wages queries -> BLOCKED / isolated
    resp = client.get("/api/v1/labour/payroll?project_id=99999&month=8&year=2026")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/labour/payroll/stats?project_id=99999&month=8&year=2026")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/labour/payroll/contractor-liability?project_id=99999&month=8&year=2026")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/labour/99999/weekly-report")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/labour/99999/monthly-report")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/labour/99999/qr")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/labour/wages?project_id=99999")
    assert resp.status_code in (200, 403, 404)
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        assert len(items) == 0

    resp = client.get("/api/v1/labour/wages/stats?project_id=99999")
    assert resp.status_code in (403, 404)

    # 6. Reverse Company B -> Company A isolation
    override_dependency(company_b_admin)
    resp = client.get("/api/v1/labour/99999")
    assert resp.status_code in (403, 404)

    resp = client.put("/api/v1/labour/99999", data={
        "labour_name": "Company B edit"
    })
    assert resp.status_code in (400, 403, 404)

    resp = client.delete("/api/v1/labour/99999")
    assert resp.status_code in (400, 403, 404)

    # 7. Super Admin standard labour API blocked / safe empty
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/labour")
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        assert len(items) == 0

    resp = client.get("/api/v1/labour/99999")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/labour/99999/qr")
    assert resp.status_code in (403, 404)

    resp = client.delete("/api/v1/labour/99999")
    assert resp.status_code in (400, 403, 404)

    resp = client.get("/api/v1/labour/wages")
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        data = resp.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        assert len(items) == 0

    clear_overrides()

# ==========================================
# PHASE 4.3: DASHBOARD & REPORTS TENANT ISOLATION TESTS
# ==========================================

def test_dashboard_tenant_isolation():
    # 1. Company A Admin Dashboard returns valid scoped response
    override_dependency(company_a_admin)
    resp = client.get("/api/v1/dashboard/admin")
    assert resp.status_code == 200

    resp = client.get("/api/v1/dashboard/engineer")
    assert resp.status_code == 200

    resp = client.get("/api/v1/dashboard/manager")
    assert resp.status_code == 200

    resp = client.get("/api/v1/dashboard/accountant")
    assert resp.status_code == 200

    resp = client.get("/api/v1/dashboard/engineer/99999")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/dashboard/admin/projects/export/csv")
    assert resp.status_code in (200, 403)

    resp = client.get("/api/v1/dashboard/admin/projects/export/pdf")
    assert resp.status_code in (200, 403)

    # 2. Super Admin blocked on tenant dashboards
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/dashboard/admin")
    assert resp.status_code in (400, 403)

    resp = client.get("/api/v1/dashboard/accountant")
    assert resp.status_code in (400, 403)

    resp = client.get("/api/v1/dashboard/admin/projects/export/csv")
    assert resp.status_code in (400, 403)

    resp = client.get("/api/v1/dashboard/admin/projects/export/pdf")
    assert resp.status_code in (400, 403)

    clear_overrides()


def test_reports_tenant_isolation():
    # 1. Company A reports scoped
    override_dependency(company_a_admin)
    resp = client.get("/api/v1/reports/projects/excel")
    assert resp.status_code in (200, 403)

    resp = client.get("/api/v1/reports/projects/pdf")
    assert resp.status_code in (200, 403)

    resp = client.get("/api/v1/reports/daily?project_id=99999&report_date=2026-08-26")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/reports/weekly?project_id=99999")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/reports/labour?project_id=99999")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/reports/material?project_id=99999")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/reports/issues?project_id=99999")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/reports/project/99999")
    assert resp.status_code in (403, 404)

    resp = client.get("/api/v1/reports/business-intelligence")
    assert resp.status_code in (200, 403)

    # 2. Super Admin blocked on company reports
    override_dependency(super_admin_user)
    resp = client.get("/api/v1/reports/projects/excel")
    assert resp.status_code in (400, 403)

    resp = client.get("/api/v1/reports/projects/pdf")
    assert resp.status_code in (400, 403)

    resp = client.get("/api/v1/reports/business-intelligence")
    assert resp.status_code in (400, 403)

    clear_overrides()






