# Duplicate API Audit & Safe Deletion Eligibility Report

**Audit Date**: October 16, 2026  
**Auditor**: Antigravity Autonomous Audit Agent  
**Environment**: Local Development Workspace (`c:\Users\Admin\Desktop\construction-mgmt`)  
**Scope**: 3 Endpoints Under Review for Potential Redundancy / Deletion:
1. `GET /api/v1/equipment/cost-report` — Cost Report
2. `GET /api/v1/equipment/eq/availability` — Availability Report
3. `DELETE /api/v1/measurements/{id}` — Delete Measurement  
**Target Document**: `docs/duplicate_api_deletion_audit.md`

---

## 1. Executive Summary

### 1.1 Scope and Methodology
An exhaustive, read-only audit was conducted on three candidate backend endpoints to evaluate whether any of them are genuinely redundant duplicates eligible for immediate, safe deletion. In accordance with the protocol:
- Every route handler, query, dependency, and response model was inspected in source code.
- Historical commits (`git log -S`) were analyzed to understand original intent and deprecation history.
- The entire repository was searched for references, route aliases, tests, and OpenAPI dependencies.
- Automated tests were executed locally using `pytest -q --tb=short` to establish empirical baselines.

### 1.2 Summary of Classifications

| Metric | Count | Details |
|---|---|---|
| **Total Endpoints Audited** | **3** | `GET /equipment/cost-report`, `GET /equipment/eq/availability`, `DELETE /measurements/{id}` |
| **SAFE TO DELETE** | **0** | No endpoint can safely be deleted immediately without causing regressions or breaking active contracts. |
| **KEEP** | **3** | All 3 endpoints must be retained at this time. |
| **NEEDS MORE EVIDENCE** | **0** | All 3 endpoints have conclusive repository evidence justifying why they must be kept. |

### 1.3 Key Takeaway
- **Endpoint 1 (`GET /api/v1/equipment/cost-report`)**: Functionally duplicate of `GET /api/v1/equipment/cost/report` (shares the exact same Python handler function). However, it is an **intentional backward-compatibility route alias** explicitly tested in OpenAPI conformance tests (`tests/api/test_equipment_p2_openapi_operation_id.py`) and security tests (`test_equipment_security_phase_b1.py`). Deleting it immediately causes automated test failures and risks breaking unverified frontend consumers.
- **Endpoint 2 (`GET /api/v1/equipment/eq/availability`)**: Functionally duplicate of `GET /api/v1/equipment/availability` (shares the exact same Python handler function). It is the **original legacy route** added during initial development, with `/availability` added later as a clean alias. It is actively asserted in `tests/api/test_tenant_idor.py` and `tests/api/test_equipment_security_phase_b1.py`. Deleting it immediately causes automated test failures and risks breaking unverified frontend consumers.
- **Endpoint 3 (`DELETE /api/v1/measurements/{id}`)**: **NOT A DUPLICATE AT ALL.** It is the primary and only RESTful deletion endpoint for `FinalMeasurement` records. It implements critical business validations (blocking deletion of submitted/verified/approved records and invoiced measurements) and is comprehensively tested in RBAC Batch N (`tests/api/test_rbac_phase2_batch_n.py`). Deleting it would eliminate core business functionality and break measurement workflows.

---

## 2. Per-Endpoint Audit

---

### 2.1 Endpoint 1: `GET /api/v1/equipment/cost-report`

#### A. Route and Handler Specification
- **Mounted Route**: `/cost-report`
- **Prefix**: `/api/v1/equipment` (defined in `app/main.py:319` via `api_router.include_router(equipment_router)`)
- **Full Path**: `GET /api/v1/equipment/cost-report`
- **Handler Function**: `cost_report` in `app/api/equipment.py:721-828`
- **OpenAPI Operation ID**: `equipment_cost_report_alias`
- **Response Model**: `List[CostReportItem]`
- **RBAC Dependency**: `Depends(require_permission("equipment.view"))`
- **Database Session**: `Depends(get_db_session)`

#### B. Implementation and Database Dependencies
The handler aggregates equipment rental expenditures:
1. Queries `EquipmentRental` joined with `Equipment` (`Equipment.is_deleted == False`) and outer-joined with `Project`.
2. Enforces multi-tenant scoping: if the user is not Super Admin, restricts results to `or_(Project.company_id == user.company_id, Equipment.company_id == user.company_id)`.
3. Supports filtering by `equipment_id`, `company_id` (Super Admin only), `date_from`, `date_to`, with pagination (`limit`, `offset`).
4. Computes:
   - `total_cost`: `func.sum(EquipmentRental.rental_cost)`
   - `rental_count`: `func.count(EquipmentRental.id)`
   - `total_days`: `func.sum(coalesce(end_date, start_date) - start_date + 1)`
   - `avg_cost`: `total_cost / rental_count`
   - `revenue_per_day`: `total_cost / total_days`
5. Returns `List[CostReportItem]`.

#### C. Similar Endpoints Discovered
In `app/api/equipment.py:711-720`:
```python
@router.get(
    "/cost/report",
    response_model=List[CostReportItem],
    operation_id="equipment_cost_report",
)
@router.get(
    "/cost-report",
    response_model=List[CostReportItem],
    operation_id="equipment_cost_report_alias",
)
async def cost_report(...)
```
- **Confirmed Counterpart**: `GET /api/v1/equipment/cost/report`
- **Behavioral Comparison**: **100% Identical.** Both decorators attach to the exact same Python handler function `cost_report`. Parameters, authorization, database queries, mathematical calculations, ordering, and JSON serialization are byte-for-byte identical.

#### D. Git History and Architectural Context
- **June 22, 2026** (`commit 994e569`): `GET /cost/report` was originally created by Tejas Dhande.
- **September 4, 2026** (`commit cf9b8bd`): Gaurav added `@router.get("/cost-report")` as a hyphenated route alias alongside `/cost/report`.
- **September 8, 2026** (`commit 1847cac`): Explicit `operation_id="equipment_cost_report"` and `operation_id="equipment_cost_report_alias"` were added to avoid duplicate OpenAPI operation ID collision in FastAPI.
- **Dedicated Regression Suite**: `tests/api/test_equipment_p2_openapi_operation_id.py` was created to prove both routes exist in OpenAPI and function identically.

#### E. Consumer and Dependency References
- `tests/api/test_equipment_p2_openapi_operation_id.py:56-65`:
  - `dash_path = paths.get("/api/v1/equipment/cost-report", {})`
  - `assert dash_path, "Route /api/v1/equipment/cost-report not found in OpenAPI schema"`
  - `assert op2 is not None, "operationId missing for /api/v1/equipment/cost-report"`
- `tests/api/test_equipment_p2_openapi_operation_id.py:95, 103, 110, 117`:
  - Re-asserts 401, 403, and 200 responses on `/api/v1/equipment/cost-report`.
- `tests/api/test_equipment_security_phase_b1.py:439`:
  - `resp_new_cost = client.get("/api/v1/equipment/cost-report")`
  - `assert resp_new_cost.status_code == 200, "New cost report route must be functional"`
- **External / Frontend Consumers**: Frontend code is hosted outside this backend repository. Deleting this route risks breaking external frontend consumers compiled against `/api/v1/equipment/cost-report`.

#### F. Empirical Test Evidence
- **Command**: `pytest -q --tb=short tests/api/test_equipment_p2_openapi_operation_id.py`
- **Result**: `2 passed, 263 warnings in 26.21s`.
- **Command**: `pytest -q --tb=short tests/api/test_equipment_security_phase_b1.py -k test_8_route_aliases_and_direct_queries`
- **Result**: `1 passed, 7 deselected, 118 warnings in 31.87s`.

#### G. Recommendation
**KEEP**  
While functionally redundant to `/cost/report`, it is an intentional, documented, and tested route alias. Deleting it directly breaks automated CI test suites and risks breaking external web or mobile API clients without providing any performance or architectural gain (it costs only 5 lines of decorator configuration).

---

### 2.2 Endpoint 2: `GET /api/v1/equipment/eq/availability`

#### A. Route and Handler Specification
- **Mounted Route**: `/eq/availability`
- **Prefix**: `/api/v1/equipment`
- **Full Path**: `GET /api/v1/equipment/eq/availability`
- **Handler Function**: `availability_report` in `app/api/equipment.py:1009-1110`
- **Response Model**: `List[AvailabilityReportItem]`
- **RBAC Dependency**: `Depends(require_permission("equipment.view"))`
- **Database Session**: `Depends(get_db_session)`

#### B. Implementation and Database Dependencies
The handler computes live availability status across active equipment:
1. Queries all non-deleted equipment for the current tenant (`Equipment.company_id == target_company_id`).
2. Evaluates active rentals in database (`EquipmentRental.is_completed == False`, `start_date <= today <= end_date`).
3. Evaluates active pending maintenance in database (`EquipmentMaintenance.is_completed == False`, `maintenance_date <= today`).
4. Computes effective status priority:
   - `DAMAGED` if `eq.condition == EquipmentCondition.DAMAGED`
   - `MAINTENANCE` if `eq.id in pending_maintenance_ids`
   - `RENTED` if `eq.id in rented_ids`
   - `ALLOCATED` if `eq.project_id is not None`
   - `AVAILABLE` otherwise
5. Returns `AvailabilityReportItem(equipment_id, equipment_code, equipment_name, is_available, project_id)`.

#### C. Similar Endpoints Discovered
In `app/api/equipment.py:1007-1009`:
```python
@router.get("/eq/availability", response_model=List[AvailabilityReportItem])
@router.get("/availability", response_model=List[AvailabilityReportItem])
async def availability_report(...)
```
- **Confirmed Counterpart**: `GET /api/v1/equipment/availability`
- **Behavioral Comparison**: **100% Identical.** Both decorators attach to the exact same Python handler function `availability_report`.

#### D. Git History and Architectural Context
- **April 29, 2026** (`commit a210d40`): `GET /eq/availability` was originally written by Tejas Dhande as the primary equipment availability endpoint.
- **September 4, 2026** (`commit cf9b8bd`): Gaurav added `@router.get("/availability")` to provide a cleaner REST route without the redundant `/eq` path segment, while retaining `/eq/availability` for legacy compatibility.
- **Regression Tests**: Added in `tests/api/test_equipment_security_phase_b1.py:424` ("Issue #38: Direct Query Endpoints & Route Aliases") and `tests/api/test_tenant_idor.py:974`.

#### E. Consumer and Dependency References
- `tests/api/test_tenant_idor.py:974`:
  - `resp = client.get("/api/v1/equipment/eq/availability")`
  - `assert resp.status_code == 200`
- `tests/api/test_equipment_security_phase_b1.py:424`:
  - `resp_old_avail = client.get("/api/v1/equipment/eq/availability")`
  - `resp_new_avail = client.get("/api/v1/equipment/availability")`
  - `assert resp_old_avail.status_code == 200, "Old availability route must remain functional"`
- **External / Frontend Consumers**: Frontend repository is not part of this codebase. Legacy client builds or third-party integrations compiled against `/api/v1/equipment/eq/availability` will receive 404 if this route is deleted.

#### F. Empirical Test Evidence
- **Command**: `pytest -q --tb=short tests/api/test_equipment_security_phase_b1.py -k test_8_route_aliases_and_direct_queries`
- **Result**: `1 passed, 7 deselected, 118 warnings in 31.87s`.
- **Command**: `pytest -q --tb=short tests/api/test_tenant_idor.py -k test_tenant_idor`
- **Result**: Confirmed passing in test suite.

#### G. Recommendation
**KEEP**  
`GET /api/v1/equipment/eq/availability` is the original legacy route for equipment availability. It costs 1 line of decorator code to maintain. Deleting it immediately breaks existing test assertions in `test_tenant_idor.py` and `test_equipment_security_phase_b1.py`, and breaks any legacy frontend clients that have not migrated to `/availability`.

---

### 2.3 Endpoint 3: `DELETE /api/v1/measurements/{id}`

#### A. Route and Handler Specification
- **Mounted Route**: `/{id}`
- **Prefix**: `/measurements` (defined in `app/api/final_measurement.py:25`)
- **Full Path**: `DELETE /api/v1/measurements/{id}`
- **Handler Function**: `delete_measurement` in `app/api/final_measurement.py:249-296`
- **HTTP Status**: `204 No Content`
- **Response**: `None`
- **RBAC Dependency**: `Depends(require_permission("measurements.delete"))`
- **Database Session**: `Depends(get_db_session)`

#### B. Implementation and Database Dependencies
`delete_measurement` is a core RESTful data deletion endpoint for `FinalMeasurement`:
1. **Tenant Boundary Isolation**:
   - Calls `_get_scoped_measurement(db, id, current_user)`.
   - Joins `Project` and verifies `Project.company_id == current_user.company_id`. Raises `NotFoundError` (404) on unauthorized access or missing records.
2. **Business Status Guard**:
   - `if obj.status not in ["DRAFT", "REJECTED"]: raise ValidationError("Cannot modify a measurement once it has been submitted for approval.")`
   - Measurements that have transitioned to `SUBMITTED`, `VERIFIED`, `APPROVED`, or `BILLED` are legally protected and cannot be deleted.
3. **Financial Invoicing Integrity Guard**:
   - Executes `select(Invoice.id).where(Invoice.source_type == InvoiceSourceType.MEASUREMENT, Invoice.reference_id == obj.id)`.
   - If an invoice is linked to this measurement, deletion is strictly blocked: `raise ValidationError("Cannot delete measurement. Invoice already exists.")`.
4. **Physical Deletion**:
   - Executes `await db.delete(obj); await db.commit()`.

#### C. Similar Endpoints Discovered
- **Repository Search**: Searched across all routers (`billing.py`, `quotation.py`, `dummy_quotation.py`, `final_measurement.py`, `reports.py`).
- **Findings**: **Zero duplicate endpoints exist.**
- **Clarification on Similar Names**:
  - `quotation.py` and `dummy_quotation.py` contain child measurement fields on quotation line items (length, width, height calculations), but have no standalone measurement entity or measurement deletion endpoint.
  - `billing.py` references measurements for bill generation, but has no deletion endpoint.
  - `DELETE /api/v1/measurements/{id}` is the **only** measurement deletion endpoint in the entire backend application.

#### D. Consumer and Dependency References
- Extensively referenced and verified in `tests/api/test_rbac_phase2_batch_n.py`:
  - Line 459: `res = await ac.delete(f"/api/v1/measurements/{meas_id}", headers=headers)`
  - Line 608: `res_del = await ac.delete(f"/api/v1/measurements/{new_id}", headers=headers)` (verifies `measurements.*` wildcard deletion)
  - Line 678: `res_del = await ac.delete(f"/api/v1/measurements/{meas_b_id}", headers=headers_a)` (verifies cross-tenant IDOR protection)
  - Line 755: `res_del = await ac.delete(f"/api/v1/measurements/{new_sa_meas_id}", headers=headers_sa)` (verifies Super Admin deletion)
  - Line 796: `res_del = await ac.delete(f"/api/v1/measurements/{meas_submitted}", headers=headers_a)` (verifies 422 block on submitted records)
  - Line 819: `res_del = await ac.delete(f"/api/v1/measurements/{meas_invoiced}", headers=headers_a)` (verifies 422 block on invoiced records)

#### E. Empirical Test Evidence
- **Command**: `pytest -q --tb=short tests/api/test_rbac_phase2_batch_n.py -k "test_batch_n_business_status_guards or test_batch_n_invoice_lock or test_batch_n_wildcard_permission"`
- **Result**: `3 passed, 12 deselected, 118 warnings in 22.78s`.

#### F. Recommendation
**KEEP**  
This endpoint is **NOT** redundant. It is the sole CRUD deletion endpoint for the `FinalMeasurement` model, enforcing indispensable business rules, tenant boundaries, and financial invoice safeguards. Deleting it would eliminate an essential business capability and cause widespread test and operational failures.

---

## 3. Deletion Impact Matrix

| Endpoint | Confirmed Replacement | Unique Behavior | Consumer Risk | Data Risk | Recommendation |
|---|---|---|---|---|---|
| `GET /api/v1/equipment/cost-report` | `GET /api/v1/equipment/cost/report` | None (shares exact same handler function) | **High** (Breaks CI tests `test_equipment_p2_openapi_operation_id.py` and external frontend callers) | Low | **KEEP** (Intentional route alias; deprecate before deleting) |
| `GET /api/v1/equipment/eq/availability` | `GET /api/v1/equipment/availability` | None (shares exact same handler function) | **High** (Breaks CI tests `test_tenant_idor.py`, `test_equipment_security_phase_b1.py`, and legacy mobile/web clients) | Low | **KEEP** (Original legacy route; deprecate before deleting) |
| `DELETE /api/v1/measurements/{id}` | **None** (No replacement exists) | **High** (Enforces status guards, invoice locks, tenant isolation, and physical deletion) | **Critical** (Eliminates measurement deletion capability; breaks Batch N test suite) | **Critical** (Corrupts measurement lifecycle) | **KEEP** (Core business API, not a duplicate) |

---

## 4. Final Decision & Explicit Answers to Questions

### Question 1: Can `GET /api/v1/equipment/cost-report` be deleted without losing unique reporting functionality?
**Answer**: Functionally, **yes**, because `GET /api/v1/equipment/cost/report` calls the identical handler function with identical logic. However, architecturally and operationally, **no**, because it was intentionally registered as an alias and is explicitly required by `tests/api/test_equipment_p2_openapi_operation_id.py` (which validates OpenAPI operationId uniqueness and route exposure). Deleting it without updating OpenAPI specs and frontend consumers causes CI test failures and potential frontend breakage.

### Question 2: Can `GET /api/v1/equipment/eq/availability` be deleted without breaking availability checks?
**Answer**: Functionally, **yes**, because `GET /api/v1/equipment/availability` executes the exact same underlying logic. However, operationally, **no**, because `GET /api/v1/equipment/eq/availability` is the original legacy route that has been live since April 2026. It is directly asserted in `tests/api/test_tenant_idor.py` and `tests/api/test_equipment_security_phase_b1.py`. Deleting it immediately breaks automated tests and legacy API clients.

### Question 3: Can `DELETE /api/v1/measurements/{id}` be deleted without breaking measurement management or data-integrity workflows?
**Answer**: **NO.** Under no circumstances should this endpoint be deleted. It is **not a duplicate**. It is the sole RESTful deletion endpoint for `FinalMeasurement` in the entire application. It contains indispensable business logic preventing deletion of submitted, verified, approved, or invoiced measurements, and is heavily tested across RBAC Batch N.

### Question 4: What is the evidence for each decision?
- **Source Code Verification**:
  - `app/api/equipment.py:711-720`: Shows dual decorators on `cost_report`.
  - `app/api/equipment.py:1007-1009`: Shows dual decorators on `availability_report`.
  - `app/api/final_measurement.py:249-296`: Shows the single, authoritative `delete_measurement` implementation.
- **Git Commit History**:
  - `commit 994e569` and `commit cf9b8bd`: Document the evolution and purposeful introduction of route aliases for equipment cost and availability.
  - `commit 1847cac`: Demonstrates deliberate engineering effort to maintain distinct OpenAPI operation IDs for both `/cost/report` and `/cost-report`.
- **Empirical Test Execution**:
  - `tests/api/test_equipment_p2_openapi_operation_id.py` (2 passed in 26.21s) asserts both cost report paths exist in OpenAPI.
  - `tests/api/test_equipment_security_phase_b1.py` (1 passed in 31.87s) asserts both availability and cost report aliases remain functional.
  - `tests/api/test_rbac_phase2_batch_n.py` (3 passed in 22.78s) asserts status guards and invoice locks on `DELETE /api/v1/measurements/{id}`.

### Question 5: Which APIs, if any, should be retained?
**All three APIs must be retained.**
1. `GET /api/v1/equipment/cost-report` must be retained as an active route alias to preserve OpenAPI and external client compatibility.
2. `GET /api/v1/equipment/eq/availability` must be retained as a legacy route alias to protect existing web/mobile consumers and existing test suites.
3. `DELETE /api/v1/measurements/{id}` must be retained as core, non-redundant business functionality.

### Question 6: What additional information is needed before making an uncertain decision?
If the engineering team desires to decommission the two equipment alias routes (`/cost-report` and `/eq/availability`) in the future:
1. **Frontend / Mobile Client Audit**: Inspect the external frontend and mobile repositories to verify which URLs are actively called.
2. **Access Log Analytics**: Inspect production web server / Nginx / API gateway access logs over a 30-day window to quantify request volume on `/api/v1/equipment/cost-report` vs `/cost/report` and `/api/v1/equipment/eq/availability` vs `/availability`.
3. **Formal Deprecation Notice**: If traffic is negligible, mark the route in FastAPI with `deprecated=True` for one release cycle before removing the decorator and updating the associated test assertions in `test_equipment_p2_openapi_operation_id.py`, `test_equipment_security_phase_b1.py`, and `test_tenant_idor.py`.
