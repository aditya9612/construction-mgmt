# RBAC Phase 2 — Batch M Audit Report: Document Management
**Module:** Document Management (`app/api/document.py`)  
**Audit Date:** September 4, 2026  
**Audit Scope:** READ-ONLY Discovery & Security Architecture Analysis  
**Batches A–L Status:** CLOSED (315 Migrated Routes)  
**Batch M Status:** READY FOR IMPLEMENTATION  

---

## 1. Executive Summary

Batches A through L of RBAC Phase 2 successfully migrated 315 production API routes across core operational domains (Alerts, CAD, Drawings, Notifications, Projects, Settings, Attendance, Dashboard, Billing, Expenses, Contractors, Equipment, QC/Safety, Materials, Labour, BOQ, Invoices, Quotations, and Client Payments).

To determine the next logical production module for **Batch M**, a comprehensive inventory of all 777 active API routes across 30+ application routers was performed.

**Document Management** (`app/api/document.py`) was selected as the optimal candidate for Batch M for the following reasons:
1. **Self-Contained & Critical Operational Domain:** Manages tenant-sensitive project files, architectural specifications, compliance records, and contracts. It handles physical disk storage (`uploads/documents/`), streaming downloads, multi-level folder trees (`parent_id`), and Redis cache invalidation.
2. **Cohesive Size:** Exactly 8 active production routes with prefix `/documents`, fitting the standard batch sizing of Phase 2 (matching Batch I with 8 routes and Batch L with 8 routes).
3. **100% Pre-Existing Permission Catalog:** All required permissions (`documents.*`, `documents.view`, `documents.create`, `documents.upload`, `documents.edit`, `documents.delete`, `documents.download`, `documents.manage`) **already exist** in the database permission catalog (`permissions` table). Zero new permissions and **zero Alembic migrations** are required.
4. **High Security Impact:** The module currently relies on legacy hardcoded role checks (`DOCUMENT_WRITE_ROLES`, `DOCUMENT_DELETE_ROLES`), exposes 4 endpoints to raw `get_current_active_user` without any RBAC permission checks, suffers from a **P0 cross-tenant/cross-project folder hierarchy injection vulnerability** via unscoped `parent_id`, and utilizes non-canonical `current_user.is_super_admin` checks.

**Audit Recommendation:** Document Management is fully scoped, architecturally verified, and **READY FOR IMPLEMENTATION**.

---

## 2. Selected Module

- **Module Name:** Document Management
- **Primary Source File:** [`app/api/document.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py)
- **Data Model:** [`app/models/document.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/models/document.py) (`Document` on table `document_management`)
- **Associated Models:** [`app/models/project.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/models/project.py) (`Project`, `ProjectMember`), [`app/models/user.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/models/user.py) (`User`)
- **Pydantic Schemas:** [`app/schemas/document.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/schemas/document.py) (`DocumentCreate`, `DocumentOut`, `DocumentUpdate`, `DocumentStats`)
- **Cache Namespace:** Redis version key `cache_version:documents`

---

## 3. Router Prefix

- **Router Prefix:** `/documents`
- **Application Mount Point:** `/api/v1/documents` (included via `api_router.include_router(document_router)` in [`app/main.py:319`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/main.py#L319))
- **OpenAPI Tag:** `["documents"]`
- **Rate Limiting:** `default_rate_limiter_dependency()` applied at router level

---

## 4. Exact Active Route Count

- **Total Active Production Routes:** **8**
- **Deprecated / Stub Routes:** 0
- **Mock / Test Routes:** 0
- **Route Breakdown by HTTP Method:**
  - `GET`: 4 routes
  - `POST`: 2 routes
  - `PUT`: 1 route
  - `DELETE`: 1 route

---

## 5. Complete Route Inventory

| # | HTTP Method | Full Endpoint Path | Function Name | Current Auth Dependency | Existing Perm | Proposed Perm | Tenant / Project Scope | IDOR Protection | Related Assert Helpers | Primary Security Risk |
|:---:|:---:|:---|:---|:---|:---:|:---:|:---|:---|:---|:---|
| 1 | `GET` | `/api/v1/documents/stats` | `get_document_stats` | `Depends(get_current_active_user)` | None | `documents.view` | Tenant (joins `Project.company_id`) | Aggregates storage, approvals, counts | None | Unprotected by RBAC; any tenant user can view company document stats |
| 2 | `POST` | `/api/v1/documents` | `create_document` | `Depends(require_roles(DOCUMENT_WRITE_ROLES))` | None | `documents.upload` | Project + Tenant (`Project.company_id`) | Checks project exists & company match | None | P0 Folder Parent IDOR: unscoped `parent_id` accepts foreign project/tenant folder |
| 3 | `POST` | `/api/v1/documents/folders` | `create_folder` | `Depends(require_roles(DOCUMENT_WRITE_ROLES))` | None | `documents.create` | Project + Tenant (`Project.company_id`) | Checks project exists & company match | None | P0 Folder Parent IDOR: unscoped `parent_id` accepts foreign project/tenant folder |
| 4 | `GET` | `/api/v1/documents` | `list_documents` | `Depends(get_current_active_user)` | None | `documents.view` | Tenant (joins `Project.company_id`) | Filters by company; Redis cached | None | Missing permission check; any active user can list company documents |
| 5 | `GET` | `/api/v1/documents/{document_id}` | `get_document` | `Depends(get_current_active_user)` | None | `documents.view` | Tenant (joins `Project.company_id`) | Joins Project; 404 if cross-tenant | None | Missing permission check; Redis cache key uses `cid or "all"` |
| 6 | `PUT` | `/api/v1/documents/{document_id}` | `update_document` | `Depends(require_roles(DOCUMENT_WRITE_ROLES))` | None | `documents.edit` | Tenant (joins `Project.company_id`) | Joins Project; status guard check | None | Non-canonical SA check; status guard check bypasses role overrides |
| 7 | `DELETE` | `/api/v1/documents/{document_id}` | `delete_document` | `Depends(require_roles(DOCUMENT_DELETE_ROLES))` | None | `documents.delete` | Tenant (joins `Project.company_id`) | Joins Project; status guard check | None | Physical disk deletion without cascading child document files |
| 8 | `GET` | `/api/v1/documents/{document_id}/download` | `download_document` | `Depends(get_current_active_user)` | None | `documents.download` | Tenant (joins `Project.company_id`) | Joins Project; physical path check | None | Missing permission check; streams file directly from disk via `FileResponse` |

---

## 6. Route → Proposed Permission Mapping

All proposed permissions map directly to pre-existing rows in the `permissions` table:

```
GET    /api/v1/documents/stats                  ──► require_permission("documents.view")
POST   /api/v1/documents                        ──► require_permission("documents.upload")
POST   /api/v1/documents/folders                ──► require_permission("documents.create")
GET    /api/v1/documents                        ──► require_permission("documents.view")
GET    /api/v1/documents/{document_id}          ──► require_permission("documents.view")
PUT    /api/v1/documents/{document_id}          ──► require_permission("documents.edit")
DELETE /api/v1/documents/{document_id}          ──► require_permission("documents.delete")
GET    /api/v1/documents/{document_id}/download ──► require_permission("documents.download")
```

---

## 7. Existing Permission Catalog Verification

Database query against `permissions` table for `module == 'documents'` reveals:

| Permission ID | Permission Code | Module | Description | Status in Catalog |
|:---:|:---|:---|:---|:---:|
| 371 | `documents.*` | `documents` | All document actions | **EXISTING (ACTIVE)** |
| 241 | `documents.view` | `documents` | view permission for documents | **EXISTING (ACTIVE)** |
| 242 | `documents.create` | `documents` | create permission for documents | **EXISTING (ACTIVE)** |
| 243 | `documents.edit` | `documents` | edit permission for documents | **EXISTING (ACTIVE)** |
| 244 | `documents.delete` | `documents` | delete permission for documents | **EXISTING (ACTIVE)** |
| 245 | `documents.approve` | `documents` | approve permission for documents | **EXISTING (ACTIVE)** |
| 246 | `documents.export` | `documents` | export permission for documents | **EXISTING (ACTIVE)** |
| 247 | `documents.manage` | `documents` | manage permission for documents | **EXISTING (ACTIVE)** |
| 248 | `documents.assign` | `documents` | assign permission for documents | **EXISTING (ACTIVE)** |
| 249 | `documents.upload` | `documents` | upload permission for documents | **EXISTING (ACTIVE)** |
| 250 | `documents.download` | `documents` | download permission for documents | **EXISTING (ACTIVE)** |

### Pre-Seeded Role Permissions (`role_permissions` Table)
- **Admin:** All 10 permissions (`documents.approve`, `documents.assign`, `documents.create`, `documents.delete`, `documents.download`, `documents.edit`, `documents.export`, `documents.manage`, `documents.upload`, `documents.view`)
- **ProjectManager:** `documents.download`, `documents.upload`, `documents.view`
- **SiteEngineer:** `documents.download`, `documents.upload`, `documents.view`
- **Client:** `documents.download`, `documents.view`
- **Contractor:** `documents.download`, `documents.view`
- **Accountant:** `documents.download`

**Conclusion:** 100% of required permissions exist in the database. **0 new permissions required. 0 migrations required.**

---

## 8. Legacy Authorization Findings

| File | Line(s) | Legacy Pattern | Usage Context | Classification | Recommended Action |
|:---|:---:|:---|:---|:---|:---|
| `app/api/document.py` | 25–29 | `DOCUMENT_WRITE_ROLES = [...]` | Hardcoded list: Admin, Project Manager, Site Engineer | **Authorization (Hardcoded Role List)** | Delete constant; replace with granular permissions |
| `app/api/document.py` | 31–34 | `DOCUMENT_DELETE_ROLES = [...]` | Hardcoded list: Admin, Project Manager | **Authorization (Hardcoded Role List)** | Delete constant; replace with granular permissions |
| `app/api/document.py` | 55 | `Depends(get_current_active_user)` | `get_document_stats` | **Authorization (Missing RBAC)** | Replace with `require_permission("documents.view")` |
| `app/api/document.py` | 108 | `Depends(require_roles(DOCUMENT_WRITE_ROLES))` | `create_document` | **Authorization (Legacy Role Check)** | Replace with `require_permission("documents.upload")` |
| `app/api/document.py` | 164 | `Depends(require_roles(DOCUMENT_WRITE_ROLES))` | `create_folder` | **Authorization (Legacy Role Check)** | Replace with `require_permission("documents.create")` |
| `app/api/document.py` | 204 | `Depends(get_current_active_user)` | `list_documents` | **Authorization (Missing RBAC)** | Replace with `require_permission("documents.view")` |
| `app/api/document.py` | 273 | `Depends(get_current_active_user)` | `get_document` | **Authorization (Missing RBAC)** | Replace with `require_permission("documents.view")` |
| `app/api/document.py` | 313 | `Depends(require_roles(DOCUMENT_WRITE_ROLES))` | `update_document` | **Authorization (Legacy Role Check)** | Replace with `require_permission("documents.edit")` |
| `app/api/document.py` | 383 | `Depends(require_roles(DOCUMENT_DELETE_ROLES))` | `delete_document` | **Authorization (Legacy Role Check)** | Replace with `require_permission("documents.delete")` |
| `app/api/document.py` | 417 | `Depends(get_current_active_user)` | `download_document` | **Authorization (Missing RBAC)** | Replace with `require_permission("documents.download")` |
| `app/api/document.py` | 83, 116, 172, 228, 289, 322, 392, 425 | `not current_user.is_super_admin` | In-handler authorization branches | **Tenant Isolation (Flawed SA Check)** | Replace with `getattr(current_user, "is_super_admin", False) is True` |
| `app/api/document.py` | 331, 399 | `if obj.status in [UNDER_REVIEW, APPROVED]` | Document mutation lock | **Business Logic** | Preserve unchanged |

---

## 9. P0 Findings

1. **P0-1: Cross-Project / Cross-Tenant Folder Hierarchy Injection (`parent_id` FK IDOR)**
   - **Locations:** [`create_document` (line 105, 140)](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py#L105), [`create_folder` (line 163, 179)](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py#L163)
   - **Vulnerability:** The endpoint accepts `parent_id: Optional[int]` directly from request payload/form data. It verifies that `project_id` belongs to the tenant, but **never validates `parent_id`**.
   - **Exploit Scenario:** An attacker in Company A creates a document or folder specifying `parent_id` of a folder in Company B (or a different, restricted project in Company A). The foreign folder is linked as parent, causing cross-tenant tree traversal leaks and database integrity corruption.
   - **Required Fix:** If `parent_id` is provided, query the parent `Document` and assert that:
     1. Parent exists and `is_deleted == False`.
     2. `parent.is_folder == True`.
     3. `parent.project_id == payload.project_id` (ensuring parent and child belong to the exact same project).

2. **P0-2: Unauthenticated / Unrestricted Document Listing and File Downloads**
   - **Locations:** [`list_documents` (line 204)](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py#L204), [`download_document` (line 417)](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py#L417), [`get_document` (line 273)](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py#L273)
   - **Vulnerability:** These endpoints only require `get_current_active_user`. Any active user in the tenant company (regardless of role or permissions) can view, list, and download all company documents, including confidential legal agreements, HR files, and financial contracts stored in the repository.
   - **Required Fix:** Protect with `require_permission("documents.view")` and `require_permission("documents.download")`.

---

## 10. P1 Findings

1. **P1-1: Non-Canonical Super Admin Identification Flag**
   - **Location:** Lines 83, 116, 172, 228, 289, 322, 392, 425
   - **Vulnerability:** Uses `if not current_user.is_super_admin:` direct boolean attribute evaluation instead of the repository-wide canonical invariant:
     ```python
     is_sa = getattr(current_user, "is_super_admin", False) is True
     ```
   - **Impact:** Fails if `current_user` is a dict, mock object, or has `is_super_admin=None`.

2. **P1-2: Redis Cache Key Namespace Collision for Unassigned / Non-SA Users**
   - **Location:** Line 209 (`cid = current_user.company_id or "all"`), Line 278 (`cid = current_user.company_id or "all"`)
   - **Vulnerability:** If `current_user.company_id` is `None` (for a non-SA user or unassigned user), `cid` defaults to `"all"`, colliding with the Super Admin cache namespace.
   - **Required Fix:** Use `cid = "global" if is_sa else str(current_user.company_id)`.

3. **P1-3: Physical Disk File Retention on Folder Deletion**
   - **Location:** [`delete_document` (line 403–407)](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py#L403)
   - **Vulnerability:** When a folder is deleted, SQL cascades delete child DB records (`ForeignKey("document_management.id", ondelete="CASCADE")`), but child physical files stored in `uploads/documents/` are NOT removed from the filesystem.
   - **Required Fix:** When deleting a folder, retrieve all descendant documents and remove their physical files from disk before deleting the database record.

---

## 11. P2 Findings

1. **P2-1: Legacy Role List Obstruction for Custom Tenant Roles**
   - Hardcoded `DOCUMENT_WRITE_ROLES` and `DOCUMENT_DELETE_ROLES` prevent tenant custom roles (e.g. "Doc Controller", "Compliance Officer") from uploading or managing documents even if granted `documents.upload` in the database.
2. **P2-2: Swagger UI Placeholder Sanitization in Handler**
   - The `is_real_value` helper manually checks `value not in (None, "", "string")`. Input sanitization should be cleanly defined at schema/validator layer.

---

## 12. Tenant Ownership Hierarchy

```
[ Company ] (companies.id)
     │
     └──[1:N]──► [ Project ] (projects.id, projects.company_id)
                      │
                      └──[1:N]──► [ Document ] (document_management.id, document_management.project_id)
                                       │
                                       └──[1:N Recursive]──► [ Child Document / File ] (parent_id)
```

**Key Architectural Fact:**
- The `document_management` table **does not have a `company_id` column**.
- Tenant ownership is **strictly indirect**, derived exclusively through:
  ```sql
  Document.project_id == Project.id AND Project.company_id == current_user.company_id
  ```
- Every single document query **must join the `projects` table** to enforce tenant boundary isolation.

---

## 13. Super Admin Findings

| Caller Context | Current Behavior | Proposed Invariant Behavior | Status |
|:---|:---|:---|:---:|
| **Super Admin** (`is_super_admin=True`) | Bypasses `Project.company_id` filter; sees documents across all companies | Retains full platform visibility; identified canonically via `getattr(current_user, "is_super_admin", False) is True` | Intended |
| **Dummy / Non-SA User** (`company_id=None`) | Handled inconsistently in cache (`cid="all"`); blocked at boundary under P0-1 | Blocked at `get_current_active_user` with HTTP 403 Forbidden | Secured via P0-1 |
| **Normal Tenant User** (`company_id=1`) | Scoped to `Project.company_id == 1`; cross-tenant project returns 404 | Scoped to `Project.company_id == 1`; cross-tenant returns 404; RBAC permissions enforced | Secure |

---

## 14. Client / Self-Service Findings

In construction management workflows, Clients often require access to architectural drawings, site specifications, and project contracts:
- In `role_permissions`, the `Client` role has:
  - `documents.view`
  - `documents.download`
- Under proposed RBAC:
  - Client can list documents (`documents.view`).
  - Client can view document details (`documents.view`).
  - Client can stream and download files (`documents.download`).
  - Client **cannot** upload documents, create folders, edit metadata, or delete files (lacks `documents.upload`, `documents.create`, `documents.edit`, `documents.delete` -> HTTP 403 Forbidden).
- Client self-service access is cleanly separated and enforced via database permissions.

---

## 15. Existing Test Coverage

Existing tests touching `documents` reside in [`tests/api/test_peripheral_security.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/tests/api/test_peripheral_security.py):
- Lines 319–325: Unauthenticated access testing on `/api/v1/documents`, `/stats`, `/1`.
- Lines 387–391: Cross-tenant isolation verification between Company A and Company B.
- **Coverage Status:** Rudimentary peripheral coverage only. **0 dedicated RBAC tests** for Document Management exist in the test suite.

---

## 16. Missing Test Coverage

The following test suites must be implemented in `tests/api/test_rbac_phase2_batch_m.py` during future implementation:
1. **Granular RBAC Enforcement Tests:**
   - User without `documents.view` denied on `GET /stats`, `GET /`, `GET /{id}` (403).
   - User without `documents.upload` denied on `POST /` (403).
   - User without `documents.create` denied on `POST /folders` (403).
   - User without `documents.edit` denied on `PUT /{id}` (403).
   - User without `documents.delete` denied on `DELETE /{id}` (403).
   - User without `documents.download` denied on `GET /{id}/download` (403).
2. **Tenant Isolation & Cross-Tenant IDOR Tests:**
   - Tenant Admin A querying Document B receives 404 Not Found.
   - Tenant Admin A downloading Document B receives 404 Not Found.
   - Tenant Admin A updating/deleting Document B receives 404 Not Found.
3. **Folder Hierarchy Injection Test (P0-1 Fix):**
   - Attempting to upload document with `parent_id` from another project or company rejected with 400/404.
4. **Super Admin Platform Access Tests:**
   - Super Admin can list, view, download, and manage documents across all projects and companies (200).
5. **Client Self-Service Tests:**
   - Client can view and download, but cannot upload or delete.

---

## 17. Migration / Schema Assessment

- **Current Alembic Head:** `e4f5a6b7c8d9 (head)`
- **Alembic Drift Status:** `No new upgrade operations detected.`
- **Assessment for Batch M:** **NO MIGRATION REQUIRED**
  - All 11 `documents` permissions are pre-seeded in the `permissions` table.
  - No new database columns, foreign keys, or enum types are required.
  - Implementation will produce **0 Alembic migrations** and **0 schema drift**.

---

## 18. Recommended Implementation Scope

During the execution phase of Batch M, the implementation will:
1. Replace `require_roles(DOCUMENT_WRITE_ROLES)` and `require_roles(DOCUMENT_DELETE_ROLES)` with `require_permission(...)`.
2. Replace raw `get_current_active_user` on `stats`, `list`, `get`, `download` with `require_permission(...)`.
3. Fix P0 Folder Parent IDOR in `create_document` and `create_folder` by verifying `parent_id` project ownership.
4. Standardize all 8 Super Admin checks to canonical `getattr(current_user, "is_super_admin", False) is True`.
5. Create comprehensive test suite `tests/api/test_rbac_phase2_batch_m.py`.

---

## 19. Exact Files Expected to Change During FUTURE Implementation

### Production Files (1 file):
- [`app/api/document.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py)

### Test Files (1 new file):
- `tests/api/test_rbac_phase2_batch_m.py`

### Migrations / Models / Schemas:
- **None.** (0 migrations, 0 models modified, 0 schemas modified).

---

## 20. Batch M Closure Criteria

Batch M will be considered complete when all of the following conditions are met:
1. **Codebase Migration:** All 8 routes in `app/api/document.py` use `require_permission(...)` and canonical Super Admin checks.
2. **Security Invariants:** P0 folder hierarchy injection closed; cross-tenant document isolation strictly enforced.
3. **Focused Suite:** `tests/api/test_rbac_phase2_batch_m.py` created and passing 100%.
4. **Regression Testing:** Full test suite (`python -m pytest -q`) passes with 0 failures (389+ passed).
5. **Schema Cleanliness:** `python -m alembic check` reports `No new upgrade operations detected.`
6. **Report Generated:** Final `RBAC_PHASE2_BATCH_M_REPORT.md` produced.

---

### Final Audit Status
**READY FOR IMPLEMENTATION**
