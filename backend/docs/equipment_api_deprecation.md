# Equipment API Alias Deprecation Documentation

**Date**: October 16, 2026  
**Status**: Implemented & Verified  
**Scope**: Backend API Route Metadata (`c:\Users\Admin\Desktop\construction-mgmt`)  
**Target Document**: `backend/docs/equipment_api_deprecation.md`

---

## 1. Executive Summary

As part of backend API rationalization, two duplicate route aliases in the Equipment module were marked as **deprecated in OpenAPI / Swagger documentation** using FastAPI's standard `deprecated=True` parameter. 

Crucially:
- **Zero routes were removed or disabled.**
- **No business logic, schemas, status codes, headers, or responses were altered.**
- **Full backward compatibility is maintained** for all existing external web, mobile, and third-party consumers.
- The unique measurement deletion endpoint `DELETE /api/v1/measurements/{id}` was **explicitly excluded** and remains completely untouched and active.

---

## 2. Deprecated Route Matrix

| HTTP Method | Deprecated Alias Route | Canonical Alternative | Handler Function | Operation ID | OpenAPI Status |
|---|---|---|---|---|---|
| `GET` | `/api/v1/equipment/cost-report` | `/api/v1/equipment/cost/report` | `cost_report` (`app/api/equipment.py:721`) | `equipment_cost_report_alias` | **Deprecated** (`deprecated: true`) |
| `GET` | `/api/v1/equipment/eq/availability` | `/api/v1/equipment/availability` | `availability_report` (`app/api/equipment.py:1009`) | `availability_report_api_v1_equipment_eq_availability_get` | **Deprecated** (`deprecated: true`) |

### Explicitly Excluded Endpoint
* **Endpoint**: `DELETE /api/v1/measurements/{id}`
* **Handler**: `delete_measurement` (`app/api/final_measurement.py:249`)
* **Status**: **ACTIVE & NOT DEPRECATED**. This is a non-duplicate core CRUD endpoint enforcing business status guards and invoice-locking safeguards. It was not modified in any way.

---

## 3. Rationale for Deprecation

1. **`GET /api/v1/equipment/cost-report`**:
   - Added in commit `cf9b8bd` (September 2026) as an alternative hyphenated path for `GET /api/v1/equipment/cost/report`.
   - Both paths share the exact same underlying Python handler `cost_report`.
   - Marking the hyphenated alias as deprecated signals to new API consumers and API client generators to use the canonical hierarchical route `/cost/report`.

2. **`GET /api/v1/equipment/eq/availability`**:
   - Original legacy route created in commit `a210d40` (April 2026).
   - In commit `cf9b8bd` (September 2026), `/availability` was added as a cleaner, standardized REST route without the redundant `/eq` path segment.
   - Marking `/eq/availability` as deprecated guides developers toward the modern canonical route `/availability` while maintaining zero disruption for legacy consumers.

---

## 4. Confirmation of Backward Compatibility & Functional Parity

Both alias endpoints remain completely functional:
1. **HTTP Status & Payload**: Returns the exact same HTTP status codes (`200 OK`, `401 Unauthorized`, `403 Forbidden`, `422 Unprocessable Entity`) and identical JSON payloads.
2. **Authentication & RBAC**: Both aliases continue to require `equipment.view` permission via `Depends(require_permission("equipment.view"))`.
3. **Multi-Tenant Isolation**: Both aliases strictly enforce tenant boundary checks (`Project.company_id == user.company_id` / `Equipment.company_id == user.company_id`).
4. **No Side Effects**: Neither warnings, error headers, nor HTTP redirects (301/302) were introduced.

---

## 5. Implementation Details

In `app/api/equipment.py`:

```diff
--- a/app/api/equipment.py
+++ b/app/api/equipment.py
@@ -717,6 +717,7 @@ async def usage_report(
     "/cost-report",
     response_model=List[CostReportItem],
     operation_id="equipment_cost_report_alias",
+    deprecated=True,
 )
 async def cost_report(
@@ -1004,7 +1005,7 @@ async def maintenance_alerts(
 # ================== "Availability" =======================
 
 
-@router.get("/eq/availability", response_model=List[AvailabilityReportItem])
+@router.get("/eq/availability", response_model=List[AvailabilityReportItem], deprecated=True)
 @router.get("/availability", response_model=List[AvailabilityReportItem])
 async def availability_report(
```

---

## 6. Verification & Test Evidence

All verification was conducted using the local test suite against isolated fixtures:

| Test Suite / Command | Scope | Result | Execution Time |
|---|---|---|---|
| `pytest -q --tb=short tests/api/test_equipment_p2_openapi_operation_id.py` | OpenAPI operation IDs, cost report route parity, and OpenAPI `deprecated=True` flags | **3 passed** | 42.50s |
| `pytest -q --tb=short tests/api/test_equipment_security_phase_b1.py -k test_8_route_aliases_and_direct_queries` | Route alias parity on `/availability` vs `/eq/availability` and `/cost/report` vs `/cost-report` | **1 passed** (7 deselected) | 19.26s |
| `pytest -q --tb=short tests/api/test_tenant_idor.py -k test_equipment_purchases_and_reports_tenant_isolation` | Multi-tenant isolation and 200 response on `/api/v1/equipment/eq/availability` | **1 passed** (58 deselected) | 20.45s |
| `pytest -q --tb=short tests/api/test_rbac_phase2_batch_n.py -k "test_batch_n_business_status_guards or test_batch_n_invoice_lock"` | Integrity guards and non-deprecated operation of `DELETE /api/v1/measurements/{id}` | **2 passed** (13 deselected) | 22.05s |

### Verified OpenAPI Schema Assertions
- `paths['/api/v1/equipment/cost-report']['get']['deprecated'] == True`
- `paths['/api/v1/equipment/cost/report']['get'].get('deprecated') is not True`
- `paths['/api/v1/equipment/eq/availability']['get']['deprecated'] == True`
- `paths['/api/v1/equipment/availability']['get'].get('deprecated') is not True`
- `paths['/api/v1/measurements/{id}']['delete'].get('deprecated') is not True`

---

## 7. Client Dependency & Risk Disclosure

> [!WARNING] External Client Usage Unverified
> Frontend web applications and mobile clients reside in separate repositories outside this backend codebase. Server/gateway access logs (Nginx/Cloudflare) were not inspected. Therefore, **active client traffic on these aliases cannot be assumed to be zero**. Under no circumstances should these aliases be removed without following the retirement checklist below.

---

## 8. Future Retirement Checklist

Before any future physical removal of `/api/v1/equipment/cost-report` or `/api/v1/equipment/eq/availability`:

- [ ] **Step 1: Frontend & Mobile Repository Audit**
  - Search all frontend web, mobile app, and third-party SDK repositories for calls to `/cost-report` and `/eq/availability`.
  - Update any identified call sites to use `/cost/report` and `/availability`.
- [ ] **Step 2: 30-Day Server Access Log Monitoring**
  - Query production reverse-proxy / API gateway logs (e.g. Datadog, CloudWatch, or Nginx logs) over a minimum 30-day window:
    - Filter for `GET /api/v1/equipment/cost-report`
    - Filter for `GET /api/v1/equipment/eq/availability`
  - Confirm request volume drops to 0 across all production environments.
- [ ] **Step 3: Consumer Communication**
  - Publish API changelog notes alerting API consumers that the deprecated aliases will be decommissioned in a future major version release.
- [ ] **Step 4: Formal Approval & Coordinated Removal**
  - Secure written approval from the engineering team lead.
  - Remove the alias `@router.get` decorators from `app/api/equipment.py`.
  - Update test assertions in `tests/api/test_equipment_p2_openapi_operation_id.py`, `tests/api/test_equipment_security_phase_b1.py`, and `tests/api/test_tenant_idor.py`.
