# RBAC Phase 2 — Batch O Audit & Discovery Report
**Target Module**: `app/api/owner.py`  
**Router Prefix**: `/api/v1/owners`  
**Domain**: Owner & Client Management (Project Owners, Client Portfolio & Payment Tracking)  
**Status**: AUDIT COMPLETE — READY FOR IMPLEMENTATION  

---

## 1. Executive Summary

A comprehensive, read-only discovery of all remaining unmigrated API modules across the application was conducted following the closure of Batch N (cumulative authoritative count: **329 routes**).

Based on active production route count, severity of legacy authorization and authentication gaps, critical cross-tenant IDOR exposure, complete availability in the database permission catalog, and architectural independence from Batches A–N, **Owner & Client Management** ([`app/api/owner.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/owner.py)) has been selected as the optimal candidate for **RBAC Phase 2 — Batch O**.

### Key Discovery Findings
1. **Critical P0 Security Vulnerabilities**:
   - **6 endpoints are completely unauthenticated** (`Depends(get_db_session)` without any user identity or token verification).
   - Global unauthenticated financial data exposure via `/payment-tracker`, `/{owner_id}/payments`, and `/{owner_id}/ledger`.
   - Global unauthenticated export of owner financial statements via `/{owner_id}/ledger/pdf` and `/{owner_id}/ledger/excel`.
   - Anonymous creation of payment schedule milestones across arbitrary projects and owners via `POST /payment-tracker`.
2. **Missing RBAC Authorization on Authenticated Endpoints**:
   - The remaining 6 endpoints rely solely on raw `Depends(get_current_active_user)` with **zero** RBAC role or permission checks. Any authenticated caller (including Labourers and Site Engineers) can create, view, modify, or delete project owners.
3. **Severe Cross-Tenant IDOR**:
   - Sub-resource queries (`/{owner_id}/payments`, `/{owner_id}/ledger`, `/{owner_id}/ledger/pdf`, `/{owner_id}/ledger/excel`) use unscoped `await db.get(Owner, owner_id)` without validating `Owner.company_id`, allowing any caller to inspect financial records of any tenant.
   - `GET /payment-tracker` queries `select(OwnerPaymentSchedule)` globally without tenant filtering.
4. **Existing Database Permission Catalog Match**:
   - The `'owners'` module is already fully registered and seeded in the database permission catalog with 10 standard permissions (`owners.view`, `owners.create`, `owners.edit`, `owners.delete`, `owners.approve`, `owners.export`, `owners.manage`, `owners.assign`, `owners.upload`, `owners.download`).
   - **Zero** new permissions, **zero** schema changes, **zero** model changes, and **zero** migrations are required.
5. **Route Counts**:
   - **Batch O Target Routes**: Exactly **12 active production routes**.
   - **Current Authoritative Cumulative Count (Batches A–N)**: 329
   - **Projected Authoritative Cumulative Count after Batch O**: **341** (329 + 12).

---

## 2. Module Selection Rationale

| Evaluation Criterion | Evaluation Details | Verdict |
|---|---|:---:|
| **Route Count & Scope** | Exactly 12 active production routes. Sized appropriately for robust, single-batch migration and verification. | **OPTIMAL** |
| **Security Risk / Exposure** | **Critical P0**: 6 routes completely unauthenticated; platform-wide cross-tenant IDOR on financial ledgers; zero RBAC checks on owner mutation. | **HIGHEST PRIORITY** |
| **Legacy Auth Usage** | Mix of completely unauthenticated endpoints and raw `get_current_active_user` calls. | **FULL MIGRATION NEEDED** |
| **Permission Catalog Availability** | Module `'owners'` contains all 10 canonical permissions already seeded in the DB (`view`, `create`, `edit`, `delete`, `export`, etc.). | **100% AVAILABLE** |
| **Domain Relevance** | Owners/Clients represent the primary contracting entity funding construction projects; core to billing, contracts, and payment tracking. | **CRITICAL DOMAIN** |
| **Batch A–N Independence** | Fully decoupled from Batches A–N; zero overlap with previously migrated routers. | **CLEAN BOUNDARY** |

---

## 3. Active Route Inventory & Current Authorization Matrix

The module [`app/api/owner.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/owner.py) defines a router with prefix `/owners`, mounted in `app/main.py` at `/api/v1/owners`.

All 12 active production routes:

| # | HTTP Method | Route Path | Handler Function | Current Auth Dependency | Current Role / Auth Check | Proposed Canonical Permission |
|:---:|:---:|:---|:---|:---|:---|:---|
| 1 | `POST` | `/api/v1/owners` | `create_owner` | `get_current_active_user` | None (any authenticated user) | `owners.create` |
| 2 | `GET` | `/api/v1/owners` | `list_owners` | `get_current_active_user` | None (filters `company_id`) | `owners.view` |
| 3 | `GET` | `/api/v1/owners/portfolio` | `get_client_portfolio` | `get_current_active_user` | None (filters `company_id`) | `owners.view` |
| 4 | `GET` | `/api/v1/owners/payment-tracker` | `get_all_payments_tracker` | **NONE** (unauthenticated) | None (global unscoped query) | `owners.view` |
| 5 | `POST` | `/api/v1/owners/payment-tracker` | `create_payment_milestone` | **NONE** (unauthenticated) | None (anonymous insertion) | `owners.create` |
| 6 | `GET` | `/api/v1/owners/{owner_id}` | `get_owner` | `get_current_active_user` | None (checks `company_id`) | `owners.view` |
| 7 | `PUT` | `/api/v1/owners/{owner_id}` | `update_owner` | `get_current_active_user` | None (checks `company_id`) | `owners.edit` |
| 8 | `DELETE` | `/api/v1/owners/{owner_id}` | `delete_owner` | `get_current_active_user` | None (checks `company_id`) | `owners.delete` |
| 9 | `GET` | `/api/v1/owners/{owner_id}/payments` | `get_owner_payments` | **NONE** (unauthenticated) | None (`db.get` unscoped) | `owners.view` |
| 10 | `GET` | `/api/v1/owners/{owner_id}/ledger` | `get_owner_ledger` | **NONE** (unauthenticated) | None (`db.get` unscoped) | `owners.view` |
| 11 | `GET` | `/api/v1/owners/{owner_id}/ledger/pdf` | `export_owner_ledger_pdf` | **NONE** (unauthenticated) | None (`db.get` unscoped) | `owners.export` |
| 12 | `GET` | `/api/v1/owners/{owner_id}/ledger/excel` | `export_owner_ledger_excel` | **NONE** (unauthenticated) | None (`db.get` unscoped) | `owners.export` |

---

## 4. Existing Permission Catalog Mapping

The database table `permissions` contains the following 10 pre-seeded permissions under module `'owners'`:

| Permission Code | Module | Action | Description / Intended Scope |
|:---|:---|:---|:---|
| `owners.view` | `owners` | `view` | View owner profiles, lists, portfolio summary, payments, ledgers, and payment tracker |
| `owners.create` | `owners` | `create` | Create new owner entity records and payment schedule milestones |
| `owners.edit` | `owners` | `edit` | Update owner profile data, contact information, and billing details |
| `owners.delete` | `owners` | `delete` | Soft/hard delete owner records (subject to project and financial locks) |
| `owners.export` | `owners` | `export` | Generate and export owner financial ledger reports in PDF and CSV format |
| `owners.approve` | `owners` | `approve` | Approve owner verification/onboarding workflows (catalog reserved) |
| `owners.manage` | `owners` | `manage` | Administrative management over owner configurations |
| `owners.assign` | `owners` | `assign` | Assign owners to projects and contracts |
| `owners.upload` | `owners` | `upload` | Upload owner verification and KYC documents |
| `owners.download` | `owners` | `download` | Download owner profile and verification files |

### Missing Permissions
- **NONE**. All proposed permissions (`owners.view`, `owners.create`, `owners.edit`, `owners.delete`, `owners.export`) are already present in the catalog.
- Wildcard support: `owners.*` is natively supported by the Phase 1 RBAC engine.

---

## 5. Security & Vulnerability Analysis (P0 / P1 / P2)

### Critical P0 Vulnerabilities

1. **P0-1: Completely Unauthenticated Endpoints (Information Disclosure & Data Tampering)**
   - Six routes in `app/api/owner.py` completely lack authentication:
     - `GET /api/v1/owners/payment-tracker`
     - `POST /api/v1/owners/payment-tracker`
     - `GET /api/v1/owners/{owner_id}/payments`
     - `GET /api/v1/owners/{owner_id}/ledger`
     - `GET /api/v1/owners/{owner_id}/ledger/pdf`
     - `GET /api/v1/owners/{owner_id}/ledger/excel`
   - *Impact*: Any unauthenticated internet user can dump detailed financial transaction ledgers, download owner financial statements in PDF/CSV format, and insert arbitrary payment schedule milestones.
   - *Remediation*: Enforce `require_permission(...)` on all 6 routes.

2. **P0-2: Catastrophic Cross-Tenant IDOR on Sub-Resources**
   - In `get_owner_payments`, `get_owner_ledger`, `export_owner_ledger_pdf`, and `export_owner_ledger_excel`:
     ```python
     owner = await db.get(Owner, owner_id)
     if not owner:
         raise NotFoundError("Owner not found")
     ```
   - The query performs a primary key lookup on `Owner` without asserting `Owner.company_id == current_user.company_id`.
   - *Impact*: Even if authenticated, Tenant A can read transactions, calculate balances, and download financial statements of Tenant B's clients by simply changing `owner_id`.
   - *Remediation*: Implement centralized tenant-scoped lookup `_get_scoped_owner(db, owner_id, current_user)` returning masked 404 for foreign or non-existent owners.

3. **P0-3: Cross-Tenant Global Payment Tracker IDOR**
   - In `get_all_payments_tracker`:
     ```python
     query = select(OwnerPaymentSchedule)
     if owner_id: query = query.where(OwnerPaymentSchedule.owner_id == owner_id)
     if project_id: query = query.where(OwnerPaymentSchedule.project_id == project_id)
     ```
   - No tenant scoping is applied.
   - *Impact*: Returns payment schedules across all companies in the system.
   - *Remediation*: Join `Project` or `Owner` to filter by `current_user.company_id` for non-Super-Admins; validate requested `owner_id` and `project_id` belong to the user's company.

4. **P0-4: Unvalidated Cross-Project / Cross-Owner Milestone Injection**
   - In `create_payment_milestone`:
     ```python
     obj = OwnerPaymentSchedule(**payload.model_dump())
     db.add(obj)
     await db.commit()
     ```
   - `payload` accepts arbitrary `owner_id` and `project_id`. The endpoint does not verify that `owner_id` and `project_id` exist, belong to the caller's tenant, or are mutually associated.
   - *Impact*: Callers can create milestone schedules linking Project A to foreign Owner B across company boundaries.
   - *Remediation*: Validate that both `Project` and `Owner` exist, belong to `current_user.company_id`, and `Project.owner_id == payload.owner_id`.

5. **P0-5: Total Absence of RBAC Authorization on Authenticated Handlers**
   - `POST /owners`, `GET /owners`, `GET /portfolio`, `GET /{owner_id}`, `PUT /{owner_id}`, `DELETE /{owner_id}` all use `Depends(get_current_active_user)` without checking user permissions or roles.
   - *Impact*: Any active employee (e.g. Labourer, Vendor) can modify or delete client records.
   - *Remediation*: Replace `get_current_active_user` with `require_permission(...)`.

---

## 6. High P1 Vulnerabilities

1. **P1-1: Super Admin Scoping Inversion**
   - In `create_owner`:
     `obj.company_id = current_user.company_id`
     Because `current_user.company_id` is `None` for Super Admins, a Super Admin creating an owner would create an orphan owner record with `company_id=NULL`.
   - In `list_owners` and `get_client_portfolio`:
     `select(Owner).where(Owner.company_id == current_user.company_id)`
     Filters on `NULL == NULL` (evaluating to falsy in SQL), returning empty lists for Super Admins.
   - *Remediation*: Apply canonical Super Admin semantics:
     `is_sa = getattr(current_user, "is_super_admin", False) is True`
     Non-Super-Admins require `Owner.company_id == current_user.company_id`. Super Admins can query globally across companies.

2. **P1-2: Unassigned User (`company_id=None`) Isolation**
   - A non-Super-Admin user with `company_id=None` must never be permitted to read or create owner records.
   - *Remediation*: Explicitly isolate users with `company_id=None` (return 404/403).

3. **P1-3: Internal Database Exception Details Leakage**
   - `create_owner` raises generic `Exception("Failed to create owner with unique owner_code")`.
   - `update_owner`, `delete_owner`, `export_owner_ledger_pdf`, and `export_owner_ledger_excel` use bare `raise` in `except Exception:` blocks, allowing uncaught exceptions to expose internal tracebacks.
   - *Remediation*: Use structured logging (`logger.exception(...)`) and standard application exceptions (`AppError`, `HTTPException(500, "Generic message")`).

---

## 7. Medium P2 Findings

1. **P2-1: Unsanitized Filename Headers in Export Endpoints**
   - `export_owner_ledger_pdf` and `export_owner_ledger_excel` construct filenames with `owner_id`. If non-integer IDs were ever parsed, header injection could be possible. Ensure strict integer typing.
2. **P2-2: Unbounded Search Query in `list_owners`**
   - `Owner.owner_name.ilike(f"%{search}%")` is unbounded and lacks pagination parameters (`page`, `limit`).

---

## 8. Tenant Ownership & Data Hierarchy

```
Company (companies)
  │
  ├── Project (projects.company_id == Company.id)
  │     │
  │     └── OwnerPaymentSchedule (project_id -> Project.id)
  │
  └── Owner (owners.company_id == Company.id)
        │
        ├── OwnerTransaction (owner_id -> Owner.id, project_id -> Project.id)
        │
        └── OwnerPaymentSchedule (owner_id -> Owner.id)
```

- Primary Tenant Key: `Owner.company_id`
- Secondary Tenant Key (for schedules): `Project.company_id`
- Required Multi-Tenant Constraint:
  - Every `Owner` query must verify `Owner.company_id == current_user.company_id`.
  - Every `OwnerPaymentSchedule` query must verify both `Owner.company_id` and `Project.company_id`.
  - Cross-tenant requests must return masked 404 responses.

---

## 9. Business Invariants & Workflow Guards to Preserve

During Batch O implementation, the following existing business rules must be preserved:

1. **Project Linkage Guard on Owner Deletion**:
   - `delete_owner` must verify that no projects are assigned to the owner:
     ```python
     project_count = await db.scalar(select(func.count(Project.id)).where(Project.owner_id == owner_id))
     if project_count > 0:
         raise ValidationError(f"Owner cannot be deleted because {project_count} project(s) are assigned to this owner...")
     ```
2. **Financial Records Guard on Owner Deletion**:
   - `delete_owner` must verify that no payment schedules, transactions, or invoices exist for the owner:
     ```python
     if (payment_count or 0) > 0 or (transaction_count or 0) > 0 or (invoice_count or 0) > 0:
         raise ValidationError("Owner cannot be deleted because related financial records exist.")
     ```
3. **Owner Code Collision Auto-Retry**:
   - `create_owner` must retain the 3-attempt collision retry loop generating `OWN-...` business IDs via `generate_business_id`.
4. **Mobile Number Uniqueness Validation**:
   - `update_owner` must catch `IntegrityError` on duplicate mobile numbers and return `ValidationError("Mobile number already exists")`.
5. **Satisfaction Score Computation Integrity**:
   - `get_client_portfolio` dynamic satisfaction score formula (project delays, overdue milestones, pending billing) must remain intact.
6. **Ledger Balance Math**:
   - `total_credit`, `total_debit`, and `balance = total_credit - total_debit` calculations must be preserved.

---

## 10. Existing Test Coverage & Required Batch O Security Tests

### Current Repository Test Coverage
- **Zero dedicated unit or integration tests exist** for `app/api/owner.py` in the `tests/` directory.

### Mandatory Test Requirements for Batch O Implementation (`tests/api/test_rbac_phase2_batch_o.py`)

1. **Authentication Requirement**:
   - Verify unauthenticated requests return 401 across all 12 routes.
2. **Permission Denial**:
   - Verify authenticated callers without required DB permissions receive 403 Forbidden across all 12 routes.
3. **Dynamic DB Permission Lifecycle**:
   - Dynamic grant & revoke lifecycle: 403 -> DB grant -> 200/204 -> DB revoke -> 403.
4. **User Permission Overrides**:
   - Direct positive user permission override grants access.
   - Negative user override revokes access even if granted by role.
5. **Wildcard Permission**:
   - `owners.*` wildcard grants access across all 12 routes.
6. **Legacy Role Name Immunity**:
   - Users with legacy role names (`Admin`, `Project Manager`, `Site Engineer`) but zero DB permissions receive 403.
7. **Cross-Tenant IDOR Isolation**:
   - Tenant A cannot view, list, create, update, delete, fetch transactions, view ledgers, or download PDF/CSV of Tenant B's owner (all return masked 404).
8. **Cross-Project / Foreign Owner Milestone Injection Guard**:
   - Attempting to attach an `OwnerPaymentSchedule` to a foreign project or foreign owner returns masked 404.
9. **Super Admin Cross-Company Access**:
   - Super Admin can view, list, update, and manage owners across all companies using canonical scoping.
10. **Non-SA `company_id=None` Isolation**:
    - Users with `company_id=None` receive 403/404 isolation.
11. **Business Status & Integrity Guards**:
    - Prevention of owner deletion when linked projects exist.
    - Prevention of owner deletion when related financial records exist.
    - Mobile number collision detection.
    - Unique owner code generation.

---

## 11. Model, Schema & Migration Impact Analysis

- **Models**: ZERO changes required in `app/models/owner.py`.
- **Schemas**: ZERO changes required in `app/schemas/owner.py`.
- **Migrations**: ZERO Alembic migrations required.
- **Permissions Catalog**: ZERO new permissions needed. All 10 permissions already exist in the catalog.
- **Batches A–N**: ZERO modifications to any previously migrated module.

---

## 12. Route Count Reconciliation

```
Batches A–M Cumulative Count:          323 routes
Batch N (Final Measurements):          + 6 routes
-------------------------------------------------
Current Authoritative Cumulative:      329 routes

Batch O Target (Owner Management):     +12 routes
-------------------------------------------------
Projected Authoritative Cumulative:    341 routes
```

---

## 13. Verdict

### **STATUS: READY FOR IMPLEMENTATION**

The Owner & Client Management module ([`app/api/owner.py`](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/owner.py)) has been fully audited and meets all technical, architectural, and security criteria to proceed as **RBAC Phase 2 — Batch O**.
