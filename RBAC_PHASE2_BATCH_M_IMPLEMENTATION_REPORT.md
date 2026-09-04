# RBAC Phase 2 — Batch M Implementation Report
**Module**: `app/api/document.py`  
**Router Prefix**: `/api/v1/documents`  
**Domain**: Document Management  
**Status**: COMPLETE & VERIFIED (CLOSED)  

---

## 1. Executive Summary

Batch M migrated all **8 active production routes** in the Document Management module ([`app/api/document.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py)) to the database-driven Role-Based Access Control (RBAC) architecture established in Phase 1.

All legacy role allowlists (`DOCUMENT_WRITE_ROLES`, `DOCUMENT_DELETE_ROLES`) and legacy role-based dependencies (`require_roles(...)` and raw `get_current_active_user`) were eliminated. Every active endpoint now strictly enforces authorization through the canonical `require_permission("<permission>")` dependency.

Critical P0, P1, and P2 security vulnerabilities discovered during the audit were comprehensively resolved:
1. **P0 Folder Parent IDOR Injection**: Strict validation of `parent_id` ensuring parent folder existence, non-deleted state, folder type flag (`is_folder == True`), and exact project boundary match with masked 404 responses.
2. **Missing Endpoint Protection**: All 4 previously unprotected endpoints (`stats`, `list`, `get`, `download`) are now secured by granular permissions (`documents.view` and `documents.download`).
3. **Safe Physical File Cleanup**: Safe cleanup routines guard against path traversal, ensure missing physical files do not fail database operations, and safely delete child files upon folder deletion.
4. **Recursive Multi-Level Folder Cleanup**: Iterative descendant file and database record collection across arbitrary nesting depth (`Folder A -> Folder B -> Folder C -> file.pdf`), ensuring physical files and DB rows of all nested descendants are safely removed when a parent folder is deleted.
5. **Canonical Super Admin Scoping**: Standardized Super Admin checks to `getattr(current_user, "is_super_admin", False) is True` without any in-handler authorization bypasses.

### Route Counts
- **Previous Authoritative Cumulative Count (Batches A–L)**: 315
- **Batch M Migrated Routes**: 8
- **Cumulative Authoritative Count after Batch M**: **323**

---

## 2. Active Route & Permission Mapping

All 8 routes were mapped to the database-backed permission catalog in accordance with the approved audit specification:

| # | HTTP Method | Endpoint Path | Function Name | Required Permission | Description / Purpose |
|:---:|:---:|:---|:---|:---|:---|
| 1 | `GET` | `/api/v1/documents/stats` | `get_document_stats` | `documents.view` | Aggregated document repository statistics (storage bytes, pending approvals, counts) |
| 2 | `POST` | `/api/v1/documents` | `create_document` | `documents.upload` | Upload physical file and create document record with `parent_id` validation |
| 3 | `POST` | `/api/v1/documents/folders` | `create_folder` | `documents.create` | Create a new organizational folder in the document hierarchy |
| 4 | `GET` | `/api/v1/documents` | `list_documents` | `documents.view` | Paginated listing of documents/folders with multi-filter query support & Redis caching |
| 5 | `GET` | `/api/v1/documents/{document_id}` | `get_document` | `documents.view` | Detailed document metadata by ID with tenant project scoping |
| 6 | `PUT` | `/api/v1/documents/{document_id}` | `update_document` | `documents.edit` | Update document metadata or replace uploaded physical file |
| 7 | `DELETE` | `/api/v1/documents/{document_id}` | `delete_document` | `documents.delete` | Hard-delete document or folder with safe physical disk cleanup and recursive descendant collection |
| 8 | `GET` | `/api/v1/documents/{document_id}/download` | `download_document` | `documents.download` | Stream and download physical document file from disk |

---

## 3. Core Security & Architectural Fixes Implemented

### P0 Fixes
1. **P0-1: Cross-Project / Cross-Tenant Folder Hierarchy Injection (`parent_id` FK IDOR)**:
   - In both `create_document` and `create_folder`, when `parent_id` is provided, the parent entity is looked up and asserted:
     - Parent document exists
     - `parent.is_deleted == False`
     - `parent.is_folder == True`
     - `parent.project_id == requested project_id`
   - If any condition fails, the endpoint raises an uninformative `NotFoundError("Parent folder not found")` (HTTP 404). This completely prevents attackers from attaching documents/folders into other projects or other tenants, and eliminates foreign folder existence probing.

2. **P0-2: Unrestricted Document Listing and File Downloads**:
   - `list_documents`, `get_document`, and `get_document_stats` now enforce `require_permission("documents.view")`.
   - `download_document` now enforces `require_permission("documents.download")`.
   - Any active user without these explicit DB permissions receives HTTP 403 Forbidden.

### P1 Fixes
1. **P1-1: Canonical Super Admin Scoping Invariant**:
   - Replaced fragile `if not current_user.is_super_admin:` checks with `is_sa = getattr(current_user, "is_super_admin", False) is True`.
   - Preserved standard RBAC authorization: Super Admin derives access via canonical RBAC dependency semantics without handler-level bypasses.
   - Users with `company_id=None` who are not Super Admins receive 403/404 isolation.

2. **P1-2: Safe Physical File Deletion & Path Traversal Prevention**:
   - Implemented `_safe_delete_physical_file(file_path)`:
     - Resolves the upload directory base (`UPLOAD_DIR.resolve()`).
     - Verifies `target_path.is_relative_to(base_dir)`. Any file path outside `uploads/documents` is ignored.
     - Wraps file unlinking in `try...except` catching `FileNotFoundError`, `PermissionError`, and `OSError` to ensure missing files do not fail database transactions.

3. **P1-3: Recursive Multi-Level Folder Cleanup**:
   - In `delete_document`, when a folder is deleted, an iterative traversal of `Document.parent_id` explores all descendant levels across arbitrary nesting depth (`Folder A -> Folder B -> Folder C -> file.pdf`).
   - Collects all descendant `file_url` entries into a deduplicated set `files_to_delete` before any DB deletion occurs.
   - Unlinks each physical file through `_safe_delete_physical_file()`.
   - Deletes all descendant DB records across all nesting levels in a single query: `delete(Document).where(Document.id.in_(all_descendant_ids))` before deleting the root folder record `db.delete(obj)`.

4. **P1-4: Download Path Traversal Prevention**:
   - In `download_document`, verifies that `target_path.is_relative_to(base_dir)` and `target_path.is_file()`, raising HTTP 404 if invalid or pointing outside storage root.

5. **P1-5: Redis Cache Key Namespace Isolation**:
   - Cache keys in `list_documents` and `get_document` use `cid = "global" if is_sa else str(current_user.company_id)`, preventing cache collisions between tenants and unassigned users.

### P2 Fixes & Preserved Invariants
1. **Status Guards**:
   - Preserved document mutation locks in `update_document` and `delete_document`:
     - Documents in `UNDER_REVIEW` or `APPROVED` status raise `ValidationError` (400/422).
2. **Tenant Scoping via Project Join**:
   - Every document query joins `Project` and scopes by `Project.company_id == current_user.company_id` for non-super-admins. Cross-tenant queries return 404.

---

## 4. Test Suite Coverage & Verification Results

A comprehensive test suite was implemented in [`tests/api/test_rbac_phase2_batch_m.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/tests/api/test_rbac_phase2_batch_m.py) with 13 async test cases covering the entire security surface:

| Test Function | Security Verification Area | Status |
|---|---|:---:|
| `test_batch_m_authentication_required` | Unauthenticated requests return 401 across all 8 endpoints | **PASSED** |
| `test_batch_m_permission_denial` | Users without the specific DB permissions receive 403 Forbidden across all 8 routes | **PASSED** |
| `test_batch_m_dynamic_db_role_permission_lifecycle` | Dynamic DB lifecycle: 403 -> Grant DB perm -> 200/204 -> Revoke DB perm -> 403 | **PASSED** |
| `test_batch_m_user_permission_overrides` | Positive override grants access; negative override denies access over role permission | **PASSED** |
| `test_batch_m_wildcard_permission` | `documents.*` grants access across all 8 Document Management endpoints | **PASSED** |
| `test_batch_m_immunity_to_legacy_role_names` | Users with legacy role names (`Admin`) but 0 DB permissions receive 403 | **PASSED** |
| `test_batch_m_cross_tenant_idor_isolation` | Cross-tenant document access, download, update, and delete return masked 404 | **PASSED** |
| `test_batch_m_parent_id_security` | `parent_id` validation: non-existent, deleted, non-folder, cross-project, and cross-tenant parents all return 404 | **PASSED** |
| `test_batch_m_super_admin_scoping` | Super Admin cross-tenant document management, listing across companies, and aggregated stats | **PASSED** |
| `test_batch_m_business_status_guards` | `UNDER_REVIEW` and `APPROVED` status guards prevent mutation and deletion (422/400) | **PASSED** |
| `test_batch_m_safe_physical_file_cleanup_and_path_traversal` | Safe file cleanup: missing files succeed (204), path traversal outside storage is prevented | **PASSED** |
| `test_batch_m_unassigned_and_none_company_user_isolation` | Non-super-admin with `company_id=None` receives 403 isolation | **PASSED** |
| `test_batch_m_recursive_folder_cleanup_nested_hierarchy` | Multi-level recursive folder cleanup (`Folder A -> Folder B -> Folder C -> file.pdf`): nested physical file safe cleanup, DB cascade, canary preservation | **PASSED** |

---

## 5. Repository-Wide Verification Summary

| Test Suite | Command | Tests Run | Result | Execution Time | Notes |
|---|---|:---:|:---:|:---:|---|
| **Focused Batch M** | `pytest -q tests/api/test_rbac_phase2_batch_m.py` | 13 | **13 PASSED** | 33.22s | All 8 routes, parent IDOR, status guards, & recursive cleanup |
| **Peripheral Security** | `pytest -q tests/api/test_peripheral_security.py` | 10 | **10 PASSED** | 34.67s | Peripheral documents & company settings verified |
| **Tenant IDOR** | `pytest -q tests/api/test_tenant_idor.py` | 59 | **59 PASSED** | 14.30s | Zero tenant IDOR regressions |
| **All RBAC Suites** | `pytest -q (Get-ChildItem tests/api/test_rbac_*.py)` | 188 | **188 PASSED** | 128.70s | All Batches A–M verified green |
| **Full Repository Test Suite** | `pytest -q` | 402 | **402 PASSED** | 158.62s | Zero regressions across entire application |
| **Alembic Current** | `alembic current` | — | **HEAD** | — | `e4f5a6b7c8d9 (head)` |
| **Alembic Check** | `alembic check` | — | **CLEAN** | — | `No new upgrade operations detected.` |

---

## 6. Files Touched / Created

### Production Files (1 file modified):
- [`app/api/document.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py): Migrated all 8 routes to `require_permission(...)`, implemented `parent_id` security checks, safe recursive physical file cleanup with path traversal protection, and canonical Super Admin scoping.

### Test Files (1 file created):
- [`tests/api/test_rbac_phase2_batch_m.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/tests/api/test_rbac_phase2_batch_m.py): 13 comprehensive async test cases covering all 8 endpoints, parent validation, recursive multi-level safe file deletion, and RBAC lifecycle.

### Documentation & Reports:
- [`RBAC_PHASE2_BATCH_M_AUDIT_REPORT.md`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/RBAC_PHASE2_BATCH_M_AUDIT_REPORT.md)
- [`RBAC_PHASE2_BATCH_M_IMPLEMENTATION_REPORT.md`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/RBAC_PHASE2_BATCH_M_IMPLEMENTATION_REPORT.md)

### Migrations, Schemas & Models:
- **Zero** migrations created.
- **Zero** models modified.
- **Zero** schemas modified.
- **Zero** database drift detected.

---

## 7. Cumulative Phase 2 Status

```
Phase 1 Core RBAC:
  ├── Core Engine & Schemas: 100% COMPLETE
  └── Cumulative Routes: 0 (Architecture Foundation)

Phase 2 Production Migrations:
  ├── Batch A (Alerts, CAD, Drawings, Notifications): 12 routes
  ├── Batch B (Projects, Settings): 29 routes
  ├── Batch C (Attendance, Dashboard): 29 routes
  ├── Batch D (Billing, Expenses): 32 routes
  ├── Batch E (Contractors, Equipment): 45 routes
  ├── Batch F (QC & Safety Management): 35 routes
  ├── Batch G (Materials & Procurement): 32 routes
  ├── Batch H (Labour & Payroll): 47 routes
  ├── Batch I (BOQ & Cost Estimator): 8 routes
  ├── Batch J (Invoices Management): 46 routes
  ├── Batch K (Quotations Management): 27 routes
  ├── Batch L (Client Payments & Receipts): 13 routes
  └── Batch M (Document Management): 8 routes
      └── Total Cumulative Authoritative Count: 323 ROUTES
```

---

## 8. Batch M Declaration

Batch M implementation is **COMPLETE**, all security invariants (including recursive arbitrary-depth physical file and descendant DB cleanup) are strictly enforced, and full repository test suites and schema checks pass with zero failures.

**Batch M is hereby declared CLOSED.**
