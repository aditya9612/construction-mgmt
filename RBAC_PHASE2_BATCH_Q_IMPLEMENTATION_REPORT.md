# RBAC Phase 2 — Batch Q Implementation Report
**Module**: Agreement & Contract Management  
**Source File**: `app/api/agreement.py`  
**Test Suite**: `tests/api/test_rbac_phase2_batch_q.py`  
**Implementation Date**: September 4, 2026  
**Auditor / Implementer**: Antigravity (Advanced Agentic Coding AI)  
**Status**: CLOSED & VERIFIED  

---

## 1. Executive Summary

Batch Q of RBAC Phase 2 successfully migrates **Agreement & Contract Management** (`app/api/agreement.py`) to the database-driven Role-Based Access Control (RBAC) architecture.

All 4 active production routes under `/api/v1/agreements` now strictly declare permissions using the canonical `require_permission(...)` dependency. Non-canonical list wrappers (`d.require_permissions([...])`) have been completely replaced. The download endpoint has been segregated from read permissions to require `agreements.download`.

Furthermore, comprehensive multi-tenant boundary enforcement, IDOR protection, and pre-mutation resource validation were implemented. In `POST /agreements/`, `Owner` and `Project` ownership and relational consistency (`Project.owner_id == owner_id`) are strictly validated against caller tenant scope **before** any physical file is written to the filesystem or any database record is committed. Cross-tenant resources return masked `404 Not Found`.

---

## 2. Exact Active Production Route Inventory (4 Routes)

All routes are mounted under prefix `/api/v1/agreements`:

| # | HTTP Method | Endpoint | Canonical RBAC Dependency | Description |
|---|-------------|----------|---------------------------|-------------|
| 1 | `GET` | `/api/v1/agreements/` | `require_permission("agreements.view")` | Paginated list of agreements scoped to caller's company |
| 2 | `POST` | `/api/v1/agreements/` | `require_permission("agreements.create")` | Upload new agreement file and register metadata record |
| 3 | `GET` | `/api/v1/agreements/stats` | `require_permission("agreements.view")` | Agreement statistics and tenant-scoped disk storage metrics |
| 4 | `GET` | `/api/v1/agreements/{agreement_id}/download` | `require_permission("agreements.download")` | Download physical contract file with traversal protection |

---

## 3. Permission Mapping

Target permissions were sourced directly from the pre-existing database permission catalog (`module = 'agreements'`):

| Action | Target Permission Code | Catalog ID | Target Routes |
|--------|------------------------|------------|---------------|
| **View** | `agreements.view` | 21 | 2 routes (`GET /api/v1/agreements/`, `GET /api/v1/agreements/stats`) |
| **Create** | `agreements.create` | 22 | 1 route (`POST /api/v1/agreements/`) |
| **Download** | `agreements.download` | 25 | 1 route (`GET /api/v1/agreements/{agreement_id}/download`) |

*Note*: Previously, the download route incorrectly required `agreements.view`. It is now properly hardened to require `agreements.download`.

---

## 4. RBAC Changes

1. **Replaced Non-Canonical Dependencies**:
   - Replaced `d.require_permissions(["agreements.view"])` on `GET /` and `GET /stats` with canonical `require_permission("agreements.view")`.
   - Replaced `d.require_permissions(["agreements.create"])` on `POST /` with canonical `require_permission("agreements.create")`.
   - Replaced `d.require_permissions(["agreements.view"])` on `GET /{agreement_id}/download` with canonical `require_permission("agreements.download")`.
2. **Zero Hardcoded Role Authorization**:
   - No role name allowlists or strings (`"Admin"`, `"Super Admin"`, etc.) are checked for authorization.
3. **Database-Driven Dynamic Authority**:
   - Role permissions, positive user overrides (`is_granted=True`), negative user overrides (`is_granted=False`), and module wildcards (`agreements.*`) take effect immediately in real time without server restart.
4. **Legacy Role Immunity**:
   - Legacy role strings without explicit database permission records strictly receive `403 Forbidden`.

---

## 5. Multi-Tenant Isolation & IDOR Protection

1. **Tenant Root Scoping**:
   - Tenant root is `Company`.
   - Ownership relationship: `Agreement -> Owner (mandatory)`, `Agreement -> Project (optional)`.
   - For Non-SA callers:
     - All agreement queries join `Owner` and filter by `Owner.company_id == current_user.company_id`.
     - Project joins check `Project.company_id == current_user.company_id`.
2. **Filter Query Validation (`GET /api/v1/agreements/`)**:
   - If `owner_id` query parameter is provided: verified to exist and belong to caller's company. Nonexistent or foreign owner returns masked `404 Not Found ("Owner not found")`.
   - If `project_id` query parameter is provided: verified to exist and belong to caller's company. Nonexistent or foreign project returns masked `404 Not Found ("Project not found")`.
3. **Download Scoping (`GET /{agreement_id}/download`)**:
   - Resolves `Agreement` joining `Owner` scoped to `Owner.company_id == current_user.company_id`.
   - Cross-tenant or nonexistent agreement returns masked `404 Not Found ("Agreement not found")`.
4. **Tenantless Caller Denial**:
   - Non-SA callers with `current_user.company_id is None` are immediately rejected with `403 Forbidden ("User does not belong to any company")`.

---

## 6. Pre-Mutation Validation & File Safety

In `POST /api/v1/agreements/`:
1. **Validation Before Disk Write**:
   - `Owner` must exist and belong to `current_user.company_id` (non-SA).
   - If `project_id` is provided, `Project` must exist, belong to `current_user.company_id` (non-SA), and satisfy `project.owner_id == owner_id`.
   - If any validation fails, masked `404 Not Found` is raised **before** `buffer.write(...)` is invoked.
2. **Path Traversal Prevention**:
   - Filename is generated deterministically using `doc_id = f"AGR-{uuid.uuid4().hex[:4].upper()}"` and sanitized file extension.
   - Destination absolute path is validated to remain inside `UPLOAD_DIR` (`uploads/agreements`).
   - Download endpoint validates `actual_path.startswith(upload_dir_abs)` before streaming.
3. **Atomic Disk Cleanup**:
   - If database record insertion fails after physical write, the physical file is removed from disk to prevent orphan storage leaks.

---

## 7. Metrics & Storage Scoping (`GET /agreements/stats`)

All metrics returned by `/stats` are strictly scoped to the caller's tenant:
- `total_agreements`: Count of agreements where `Owner.company_id == current_user.company_id`.
- `active_contracts`: Count of active agreements where `Owner.company_id == current_user.company_id`.
- `recent_uploads`: Count of agreements uploaded in current month for caller's company.
- `owners_count`: Count of owners belonging to caller's company.
- `missing_docs`: Preserves `max(0, owners_count - owners_with_aggr)`.
- `storage_used`: Storage size is computed **strictly** from files belonging to caller's company agreements, formatted as `f"{round(total_size / (1024 * 1024), 2)} MB"`. Global storage is never exposed to non-SA users.

---

## 8. Super Admin Semantics

1. **Canonical Check**:
   - Evaluated strictly via `is_sa = getattr(current_user, "is_super_admin", False) is True`.
2. **Global Access**:
   - Super Admin with permissions bypasses tenant scoping on `GET /`, `GET /stats`, and `GET /{id}/download`.
   - Super Admin cannot create detached/orphan agreements (`owner_id` must reference a valid tenant owner).

---

## 9. Exception Handling & Error Hygiene

- Eliminated any leakage of internal errors or raw exceptions (`detail=str(e)`).
- Wrapped file operations and database commits in `try ... except` blocks with `logger.exception(...)`.
- Failed mutations raise generic safe `500 Internal Server Error ("An error occurred while uploading agreement")`.
- Missing physical files on disk return safe `404 Not Found ("Agreement file not found on disk")`.

---

## 10. Business Invariants Preserved

- Document ID format: `AGR-{4 hex chars}` (e.g., `AGR-A1B2`).
- Default status: `"Active"`.
- Response model: `AgreementOut` with `owner_name` and `project_name`.
- Stats calculation: `AgreementStats` schema with missing document formula and MB storage formatting.
- File streaming: `FileResponse` with `media_type="application/octet-stream"`.

---

## 11. Test Results

Dedicated test suite `tests/api/test_rbac_phase2_batch_q.py` covers 15 rigorous test scenarios:

```
====================== 15 passed, 196 warnings in 16.12s ======================
```

Summary of tests:
1. `test_batch_q_authentication_required`: 401 Unauthorized across all 4 endpoints without token.
2. `test_batch_q_permission_denial`: 403 Forbidden for authenticated user with 0 DB permissions across all 4 endpoints.
3. `test_batch_q_dynamic_grant_revoke_lifecycle`: Dynamic DB grant (200) and revoke (403) without application restart.
4. `test_batch_q_positive_user_override`: Positive user permission override grants access.
5. `test_batch_q_negative_user_override`: Negative user permission override revokes access despite role permission.
6. `test_batch_q_wildcard_permission`: Wildcard `agreements.*` authorizes list, upload, stats, and download.
7. `test_batch_q_legacy_role_immunity`: Legacy Admin role without DB permissions denied with 403.
8. `test_batch_q_own_tenant_operations`: Full CRUD lifecycle success with `AGR-XXXX` ID format, `Active` status, and proper responses.
9. `test_batch_q_cross_tenant_download_idor_404`: Foreign agreement download returns masked 404.
10. `test_batch_q_cross_tenant_list_isolation`: Company A list strictly excludes Company B agreements; foreign filter IDs return 404.
11. `test_batch_q_cross_tenant_upload_injection_404`: Foreign `owner_id`, foreign `project_id`, or mismatched `project.owner_id` rejected with 404 before file write.
12. `test_batch_q_stats_tenant_isolation`: Stats and disk storage calculations strictly isolated between Company A and Company B.
13. `test_batch_q_super_admin_cross_company_access`: Super Admin with permissions accesses global list, stats, and downloads across companies.
14. `test_batch_q_non_sa_company_id_none`: Non-SA user with `company_id=None` denied with 403.
15. `test_batch_q_file_safety_and_exception_hygiene`: Missing disk file returns safe 404 without internal path exposure.

---

## 12. Peripheral & Regression Test Verification

1. **Batch Q Suite**:
   ```
   python -m pytest tests/api/test_rbac_phase2_batch_q.py -q
   15 passed, 196 warnings in 16.12s
   ```
2. **Peripheral Security Suite**:
   ```
   python -m pytest tests/api/test_peripheral_security.py -q
   10 passed, 169 warnings in 10.45s
   ```
3. **Tenant IDOR Suite**:
   ```
   python -m pytest tests/api/test_tenant_idor.py -q
   59 passed, 169 warnings in 15.69s
   ```

---

## 13. Alembic State

- Current Head: `e4f5a6b7c8d9` (and `f5a6b7c8d9e0`)
- `alembic check`:
  ```
  No new upgrade operations detected.
  ```
- New migrations created: **0**

---

## 14. Files Changed

| File | Change Type | Description |
|------|-------------|-------------|
| `app/api/agreement.py` | MODIFIED | Implemented canonical `require_permission(...)`, tenant scoping, pre-mutation checks, and file traversal protection |
| `tests/api/test_rbac_phase2_batch_q.py` | NEW | 15 test scenarios verifying authentication, authorization, IDOR, SA semantics, and invariants |
| `RBAC_PHASE2_BATCH_Q_IMPLEMENTATION_REPORT.md` | NEW | Authoritative implementation report |

---

## 15. Route Accounting

$$\text{Previous Authoritative Cumulative Route Count (Batches A–P)} = 352$$
$$\text{Batch Q Active Production Routes} = 4$$
$$\text{New Authoritative Cumulative Route Count} = 352 + 4 = \mathbf{356}$$

---

## 16. Final Acceptance & Sign-off

- [x] 4/4 routes declare canonical `require_permission(...)`.
- [x] Zero hardcoded role allowlists or legacy role string checks.
- [x] List, stats, and download queries strictly scoped by `Owner.company_id`.
- [x] Upload validates `Owner` and `Project` ownership and relational integrity (`Project.owner_id == owner_id`) before file write.
- [x] Foreign resources return masked 404 Not Found.
- [x] Canonical Super Admin global semantics verified.
- [x] Non-SA `company_id=None` callers denied with 403.
- [x] Dynamic database grants, revokes, user overrides, and wildcard permissions verified.
- [x] Path traversal prevented on upload and download.
- [x] Exception details sanitized (no `detail=str(e)`).
- [x] All business invariants preserved (`AGR-XXXX`, `Active`, `FileResponse`, storage formula).
- [x] Batch Q test suite passes (15/15 passed).
- [x] Peripheral security tests pass (10/10 passed).
- [x] Tenant IDOR tests pass (59/59 passed).
- [x] Alembic clean with 0 new migrations.

**VERDICT: Batch Q is officially CLOSED.**
