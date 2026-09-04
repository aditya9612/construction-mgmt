# RBAC Phase 2 — Batch Q Audit Report
**Module**: Agreement & Contract Management  
**Source File**: `app/api/agreement.py`  
**Audit Date**: September 4, 2026  
**Auditor**: Antigravity (Advanced Agentic Coding AI)  
**Status**: AUDITED — READY FOR IMPLEMENTATION  

---

## 1. Executive Summary

A comprehensive, read-only architectural, security, and multi-tenant IDOR audit of `app/api/agreement.py` was conducted to establish the exact scope, route inventory, permission mapping, security vulnerabilities, and test requirements for **RBAC Phase 2 — Batch Q**.

### Key Findings:
1. **Module Scope**: The module governs contract and agreement lifecycle management, including legal agreement uploads, document indexing, project/client linkages, aggregate portfolio agreement statistics, and secure agreement document file downloads.
2. **Exact Route Count**: Exactly **4 active production routes** are registered under the `/agreements` prefix, mounted at `/api/v1/agreements`.
3. **Security Posture (CRITICAL / P0)**:
   - **Non-Canonical RBAC**: Endpoints currently utilize a non-standard list-based dependency `d.require_permissions([...])` rather than the canonical, single-string `require_permission(...)` dependency enforced across Batches A–P.
   - **Catastrophic Multi-Tenant IDOR Vulnerabilities**: All 4 routes execute queries against `Agreement`, `Project`, and `Owner` with **zero `company_id` scoping**. Any authenticated tenant can list, view, and download confidential legal agreements belonging to another company.
   - **Cross-Tenant Legal Document Exfiltration (P0)**: `GET /{agreement_id}/download` performs an unscoped `db.scalar(select(Agreement).where(Agreement.id == agreement_id))` lookup, allowing malicious callers to exfiltrate proprietary contracts, land leases, and confidential financial agreements across company boundaries.
   - **Cross-Tenant Resource Injection (P0)**: `POST /` accepts `owner_id` and `project_id` via form data without verifying tenant ownership or checking if the project belongs to the specified owner, enabling cross-tenant contract attachment and milestone distortion.
   - **Global Statistics Leakage (P1)**: `GET /stats` performs unscoped global database counts and shared filesystem directory size computations, leaking global tenant agreement counts, active contract counts, and disk storage metrics.
   - **Missing Canonical Super Admin & Tenantless Protections**: Handlers lack canonical `is_sa` checks and do not reject tenantless non-SA users (`company_id=None`).
4. **Zero Migration Impact**: The database permissions catalog already contains all 10 canonical permissions for the `agreements` module (`agreements.view`, `agreements.create`, `agreements.edit`, `agreements.delete`, `agreements.approve`, `agreements.export`, `agreements.manage`, `agreements.assign`, `agreements.upload`, `agreements.download`). Exactly zero database schema changes, model modifications, or Alembic migrations are required.
5. **Cumulative Accounting**: Previous authoritative count is 352. Batch Q contains 4 routes. The projected cumulative route count post-migration is **356**.
6. **Audit Verdict**: **READY FOR IMPLEMENTATION**.

---

## 2. Selected Module

- **Functional Domain**: Agreement & Contract Management (Client Legal Contracts, Land Lease Agreements, File Uploads, Document Tracking, and Contract Downloads)
- **Source File**: `app/api/agreement.py`
- **Module Tag**: `["Agreements"]`
- **Associated Models**:
  - `Agreement` (`app/models/agreement.py`): Legal agreement records, file URLs, and document identifiers
  - `Owner` (`app/models/owner.py`): Client/owner resource to which agreements belong (`owner_id`)
  - `Project` (`app/models/project.py`): Project resource associated with agreements (`project_id`)
  - `User` (`app/models/user.py`): Authenticated user entity performing agreement operations

---

## 3. Router / Mount Information

- **Router Instance**: `router` defined in `app/api/agreement.py:19`
- **Router Prefix**: `/agreements`
- **Router Level Dependencies**: None currently declared
- **Mount Point**: Mounted in `app/main.py:342` via `api_router.include_router(agreement_router)`
- **Top-Level API Mount**: `application.include_router(api_router, prefix="/api/v1")` in `app/main.py:355`
- **Full Active URL Prefix**: `/api/v1/agreements`

---

## 4. Exact Active Production Route Inventory

Every active route in `app/api/agreement.py` was forensically verified against the live FastAPI application routing table:

| # | HTTP Method | Endpoint Sub-Path | Full Mounted Path | Function Name | Current Auth | Current Authorization | Target Canonical Permission |
|---|-------------|-------------------|-------------------|---------------|--------------|-----------------------|-----------------------------|
| 1 | `GET` | `/` | `/api/v1/agreements/` | `list_agreements` | `d.require_permissions(["agreements.view"])` | Non-canonical list dependency; unscoped | `require_permission("agreements.view")` |
| 2 | `POST` | `/` | `/api/v1/agreements/` | `upload_agreement` | `d.require_permissions(["agreements.create"])` | Non-canonical list dependency; unscoped | `require_permission("agreements.create")` |
| 3 | `GET` | `/stats` | `/api/v1/agreements/stats` | `get_agreement_stats` | `d.require_permissions(["agreements.view"])` | Non-canonical list dependency; global metrics | `require_permission("agreements.view")` |
| 4 | `GET` | `/{agreement_id}/download` | `/api/v1/agreements/{agreement_id}/download` | `download_agreement` | `d.require_permissions(["agreements.view"])` | Non-canonical list dependency; IDOR exfiltration | `require_permission("agreements.download")` |

---

## 5. Exact Route Count

- Active Production Routes in `app/api/agreement.py`: **4**
- Commented-out / dead routes: **0**
- Duplicate routes: **0**
- Total Active Production Route Count: **4**

---

## 6. Current Authentication State

- All 4 routes declare:
  ```python
  current_user: User = Depends(d.require_permissions([...]))
  ```
- Internally, `d.require_permissions` delegates to `get_current_active_user`, which validates JWT credentials and rejects unauthenticated requests with `401 Unauthorized`.
- However, the dependency is non-standard and does not align with the Phase 2 canonical signature.

---

## 7. Current Authorization State

- **Non-Canonical Dependency Wrapper**:
  The module uses `d.require_permissions([permission_code])` from `app/core/dependencies.py:396`.
  - When permission is denied, it raises an HTTP 403 with a structured dictionary `detail={"message": "Insufficient permissions", "required": [...], "missing": [...]}` rather than the canonical string format `Permission denied: {permission} required`.
  - All Phase 2 batches (A–P) strictly enforce the single-string canonical `require_permission("module.action")`.
- **Inconsistent Permission Granularity on Downloads**:
  Route 4 (`GET /{agreement_id}/download`) currently gates download access behind `agreements.view` rather than the dedicated canonical permission `agreements.download` already provisioned in the database catalog.

---

## 8. Existing Permission Catalog

The database permissions catalog contains all 10 standard canonical permissions under module `agreements`:

| Permission ID | Module | Action | Canonical Code | Description |
|---------------|--------|--------|----------------|-------------|
| 21 | `agreements` | `view` | `agreements.view` | view permission for agreements |
| 22 | `agreements` | `create` | `agreements.create` | create permission for agreements |
| 23 | `agreements` | `edit` | `agreements.edit` | edit permission for agreements |
| 24 | `agreements` | `delete` | `agreements.delete` | delete permission for agreements |
| 25 | `agreements` | `approve` | `agreements.approve` | approve permission for agreements |
| 26 | `agreements` | `export` | `agreements.export` | export permission for agreements |
| 27 | `agreements` | `manage` | `agreements.manage` | manage permission for agreements |
| 28 | `agreements` | `assign` | `agreements.assign` | assign permission for agreements |
| 29 | `agreements` | `upload` | `agreements.upload` | upload permission for agreements |
| 30 | `agreements` | `download` | `agreements.download` | download permission for agreements |

- Wildcard permission `agreements.*` is supported by the core RBAC engine.
- Reusable permissions for Batch Q:
  - `agreements.view` &rarr; Route 1 (`GET /`), Route 3 (`GET /stats`)
  - `agreements.create` &rarr; Route 2 (`POST /`)
  - `agreements.download` &rarr; Route 4 (`GET /{agreement_id}/download`)
- New permissions required: **0**.

---

## 9. Tenant Ownership Hierarchy

The data ownership hierarchy for agreements is established through the `Owner` and `Project` entities:

```
Company (Tenant Root)
  ├── Owner (Owner.company_id == current_user.company_id)
  │     └── Agreement (Agreement.owner_id == Owner.id) [MANDATORY]
  └── Project (Project.company_id == current_user.company_id)
        └── Agreement (Agreement.project_id == Project.id) [OPTIONAL]
```

### Relational Integrity Rules:
1. Every `Agreement` has a mandatory foreign key to `Owner` (`owner_id NOT NULL`).
2. If `project_id` is supplied, `Project` must belong to the caller's company AND `Project.owner_id == Agreement.owner_id`.
3. For non-Super Admin callers:
   - `Agreement` records must be queried via join on `Owner` where `Owner.company_id == current_user.company_id`.
   - If `project_id` is joined, `Project.company_id == current_user.company_id`.

---

## 10. IDOR Findings

### 10.1 P0 — Unscoped Agreement Document Download (`GET /{agreement_id}/download`)
- **Flaw**:
  ```python
  agreement = await db.scalar(select(Agreement).where(Agreement.id == agreement_id))
  if not agreement:
      raise NotFoundError("Agreement not found")
  ```
  The lookup is completely un-scoped by company or owner. Any authenticated user possessing `agreements.view` or `agreements.download` can pass any integer `agreement_id` and download contracts belonging to other tenants.
- **Remediation**:
  Join `Owner` and enforce `Owner.company_id == current_user.company_id` for non-SA. If the agreement belongs to a foreign company, return masked `404 Not Found`.

### 10.2 P0 — Cross-Tenant Agreement Listing (`GET /`)
- **Flaw**:
  `list_agreements` executes:
  ```python
  query = (
      select(Agreement, Project.project_name, Owner.owner_name)
      .join(Project, Agreement.project_id == Project.id, isouter=True)
      .join(Owner, Agreement.owner_id == Owner.id)
  )
  ```
  No `WHERE Owner.company_id == current_user.company_id` clause exists. Every tenant's agreements are returned. Furthermore, passing foreign `owner_id` or `project_id` filters allows cross-tenant enumeration.
- **Remediation**:
  Enforce `Owner.company_id == current_user.company_id` on the base query for non-SA. If `owner_id` or `project_id` query parameters are provided, validate they belong to the caller's company or return masked 404.

### 10.3 P0 — Cross-Tenant Resource Injection (`POST /`)
- **Flaw**:
  `upload_agreement` accepts `owner_id: int = Form(...)` and `project_id: Optional[int] = Form(None)` directly into `Agreement(...)` without:
  1. Validating `Owner` exists and belongs to `current_user.company_id`.
  2. Validating `Project` exists and belongs to `current_user.company_id` (if provided).
  3. Validating `Project.owner_id == owner_id`.
- **Remediation**:
  Pre-validate `Owner` and `Project` ownership before writing the uploaded file to disk or committing the record. Return masked `404 Not Found` on foreign or non-existent resources.

### 10.4 P1 — Unscoped Metrics Leakage (`GET /stats`)
- **Flaw**:
  `get_agreement_stats` performs global aggregates:
  ```python
  total = await db.scalar(select(func.count(Agreement.id)))
  active = await db.scalar(select(func.count(Agreement.id)).where(Agreement.status == "Active"))
  recent = await db.scalar(select(func.count(Agreement.id)).where(Agreement.uploaded_at >= first_of_month))
  owners_count = await db.scalar(select(func.count(Owner.id)))
  owners_with_aggr = await db.scalar(select(func.count(func.distinct(Agreement.owner_id))))
  ```
  All 5 counts compute system-wide aggregates. Non-SA users see confidential metrics of competitors and other tenants.
- **Remediation**:
  For non-SA, join `Owner` and filter `Owner.company_id == current_user.company_id` across all metric queries. For SA, global metrics are allowed.

---

## 11. RBAC Findings

1. **Non-Canonical Wrapper**: Replace `d.require_permissions([...])` with `require_permission(...)` on all 4 endpoints.
2. **Granular Download Authorization**: Route 4 (`GET /{agreement_id}/download`) must require `agreements.download` rather than generic `agreements.view`.
3. **Database-Driven Execution**: Ensure role-based grants, revokes, user-level overrides, and wildcards (`agreements.*`) operate strictly through the database engine without code restarts.
4. **Legacy Role Immunity**: Users with role `Admin`, `Project Manager`, etc., but 0 database permissions must receive HTTP 403.

---

## 12. Super Admin Findings

1. **Canonical SA Definition**:
   ```python
   is_sa = getattr(current_user, "is_super_admin", False) is True
   ```
2. **Behavioral Expectations**:
   - SA callers bypass tenant filtering on queries (`GET /`, `GET /stats`, `GET /{agreement_id}/download`).
   - Non-SA callers are strictly restricted to `current_user.company_id`.
   - Non-SA users with `company_id=None` must be denied with HTTP 403 (`User does not belong to any company`).
   - SA callers creating agreements must provide valid tenant `Owner` and `Project` IDs; agreements must never be created with NULL or detached tenant linkages.

---

## 13. Business Logic Invariants

The following business rules and data behaviors MUST be preserved during migration:

1. **Unique Document Identifier Format**:
   ```python
   doc_id = f"AGR-{uuid.uuid4().hex[:4].upper()}"
   ```
   Agreements use the prefix `AGR-` with a 4-character hex suffix.
2. **Default Status**:
   New agreements must be created with `status="Active"`.
3. **File Storage Contract**:
   Uploaded files are stored under `uploads/agreements/` as `{doc_id}{file_ext}`, and exposed via `file_url = f"/uploads/agreements/{file_name}"`.
4. **Response Schemas**:
   - `list_agreements` returns `List[AgreementOut]`, including resolved `project_name` and `owner_name`.
   - `upload_agreement` returns `AgreementOut`, including resolved `project_name` and `owner_name`.
   - `get_agreement_stats` returns `AgreementStats` with fields: `total_agreements`, `active_contracts`, `storage_used`, `missing_docs`, `recent_uploads`.
   - `download_agreement` returns `FileResponse` with `media_type="application/octet-stream"`.
5. **Missing Documents Metric Calculation**:
   Calculated as `max(0, (owners_count or 0) - (owners_with_aggr or 0))`.
6. **Storage Used Formatting**:
   Formatted as `f"{round(total_size / (1024 * 1024), 2)} MB"`.

---

## 14. Error Leakage Findings

1. **Masked 404 on IDOR**:
   Cross-tenant lookups for `agreement_id`, `owner_id`, or `project_id` must return a generic masked `NotFoundError("Agreement not found")`, `NotFoundError("Owner not found")`, or `NotFoundError("Project not found")`. Never leak whether the resource exists in another tenant.
2. **Exception Sanitization**:
   File operations and database transactions must not leak raw SQLAlchemy or OS error messages via `detail=str(e)`. All unexpected exceptions must be logged with `logger.exception(...)` and return safe generic HTTP 500 responses.
3. **Disk File Not Found Semantics**:
   When the database record exists but the file is missing from disk, raise `NotFoundError("Agreement file not found on disk")`.

---

## 15. Performance / Secondary Security Findings

1. **Upload File Validation**:
   - `upload_agreement` accepts any file extension. While not modifying the schema, the upload handler should sanitize file extensions and use safe basename handling.
   - Pre-validation of tenant resources must precede physical disk writes to prevent orphaned files when validation fails.
2. **Synchronous File System Traversal in Stats**:
   `get_agreement_stats` traverses `os.listdir(UPLOAD_DIR)`. For non-SA, scanning the shared directory is both an information leak and inefficient. Physical storage tracking should only sum file sizes belonging to the caller company's agreements.

---

## 16. Severity Classification

| Issue | Vulnerability Type | Affected Route(s) | Severity |
|-------|-------------------|-------------------|----------|
| Unscoped Agreement File Download | Cross-Tenant IDOR / Data Exfiltration | `GET /{agreement_id}/download` | **P0 Critical** |
| Cross-Tenant Agreement Listing | Multi-Tenant Scoping Bypass | `GET /` | **P0 Critical** |
| Cross-Tenant Owner/Project Injection | Unauthorized Foreign Resource Attachment | `POST /` | **P0 Critical** |
| Global Aggregate Metrics Leakage | Tenant Data Enumeration / Information Leakage | `GET /stats` | **P1 High** |
| Non-Canonical RBAC Dependency | Architecture / Non-Standard Authorization | All 4 routes | **P1 High** |
| Inconsistent Download Permission | Authorization Inconsistency (`.view` vs `.download`) | `GET /{agreement_id}/download` | **P2 Medium** |
| Tenantless Non-SA Handling | Authorization Boundary Weakness | All 4 routes | **P2 Medium** |
| Shared Directory Traversal | Performance & Information Leakage | `GET /stats` | **P3 Low** |

---

## 17. Required Implementation Scope

1. **Replace Dependencies**:
   - Migrate `list_agreements` to `Depends(require_permission("agreements.view"))`.
   - Migrate `upload_agreement` to `Depends(require_permission("agreements.create"))`.
   - Migrate `get_agreement_stats` to `Depends(require_permission("agreements.view"))`.
   - Migrate `download_agreement` to `Depends(require_permission("agreements.download"))`.
2. **Tenant Scoping on Queries**:
   - In `list_agreements`: For non-SA, scope query via `Owner.company_id == current_user.company_id`. If `project_id` is supplied, verify `Project.company_id == current_user.company_id`.
   - In `upload_agreement`: Validate `Owner` exists and belongs to caller's company; if `project_id` is provided, validate `Project` belongs to caller's company and `Project.owner_id == owner_id`. Execute validations before writing file to disk.
   - In `get_agreement_stats`: For non-SA, scope all count queries through `Owner.company_id == current_user.company_id`. Scope storage size calculation to caller's agreement records.
   - In `download_agreement`: For non-SA, verify agreement's owner belongs to `current_user.company_id`. Return masked 404 on cross-tenant lookup.
3. **Super Admin Semantics**:
   - Canonical `is_sa` check.
   - Non-SA with `company_id=None` rejected with HTTP 403.
4. **Exception Handling**:
   - Sanitize error messages; replace raw exception details with structured logging and generic HTTP 500 responses.

---

## 18. Required Test Scope

A dedicated test suite `tests/api/test_rbac_phase2_batch_q.py` must be constructed covering:

1. **401 Unauthorized**: Requests without authentication tokens rejected across all 4 routes.
2. **403 Forbidden**: Authenticated users with 0 permissions rejected across all 4 routes.
3. **Dynamic DB Grant**: 403 &rarr; DB grant of required permission &rarr; 200/201 success.
4. **Dynamic DB Revoke**: Success &rarr; DB revoke &rarr; 403.
5. **Positive User Permission Override**: User granted permission directly succeeds.
6. **Negative User Permission Override**: Role has permission, but user negative override revokes access &rarr; 403.
7. **Wildcard Permission**: Grant of `agreements.*` authorizes all 4 endpoints.
8. **Legacy Role Immunity**: Caller with role `Admin` or `Project Manager` but 0 DB permissions receives 403.
9. **Own-Tenant Success**: Authorized caller successfully lists, uploads, views stats, and downloads own agreements.
10. **Cross-Tenant IDOR Masked 404 (Download)**: Downloading foreign tenant agreement returns masked 404.
11. **Cross-Tenant Listing Isolation**: `GET /` returns only caller company's agreements; foreign agreements are excluded.
12. **Foreign Resource Injection Prevention (Upload)**:
    - Foreign `owner_id` returns masked 404.
    - Foreign `project_id` returns masked 404.
    - Project owner mismatch (`Project.owner_id != payload.owner_id`) returns masked 404.
13. **Stats Tenant Isolation**: `GET /stats` computes metrics strictly within the caller's company data.
14. **Super Admin Cross-Company Access**: Super Admin caller can view and download agreements across all companies.
15. **Non-SA `company_id=None` Denial**: Authenticated user without company ID receives 403.
16. **Unique Document ID & Status Invariants**: Verify generated `doc_id` follows `AGR-XXXX` format and default status is `Active`.
17. **File Download Integrity**: Downloaded file matches uploaded content and returns correct media type.
18. **Exception Detail Sanitization**: Unhandled server errors return generic 500 without leaking stack traces or database errors.

---

## 19. Permission / Migration Impact

- **Database Migrations Required**: **0**
- **Model Modifications Required**: **0**
- **Schema Modifications Required**: **0**
- **Permission Catalog Modifications**: **0** (all 10 permissions already exist under module `agreements`)
- **Alembic Head**: Preserved at `e4f5a6b7c8d9`

---

## 20. Projected Cumulative Route Count

- **Previous Authoritative Count (Batches A–P)**: **352**
- **Batch Q Active Production Routes**: **4**
- **Projected Cumulative Authoritative Count**: **356**

```
Batches A–P Cumulative : 352 routes
Batch Q (Agreements)   :   4 routes
------------------------------------
Authoritative Total    : 356 routes
```

---

## 21. Audit Verdict

```
===================================================================
AUDIT VERDICT: READY FOR IMPLEMENTATION
===================================================================
```

All 4 routes in `app/api/agreement.py` have been forensically inventoried and mapped to pre-existing permissions in the database catalog. All multi-tenant IDOR vulnerabilities, cross-tenant file download risks, and non-canonical authorization patterns are documented with clear remediation specifications. No database migrations, permission catalog additions, or architectural blockers exist.

---
