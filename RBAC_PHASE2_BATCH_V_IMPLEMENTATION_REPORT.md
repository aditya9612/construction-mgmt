# RBAC Phase 2 — Batch V Implementation Report
**Module**: Approvals Management  
**Source File**: `app/api/approval.py`  
**Primary Files**: `app/api/approval.py`, `app/models/approval.py`, `app/schemas/approval.py`, `app/core/rbac_seed.py`  
**Mount Point**: `/api/v1/approvals`  
**Implementation Date**: September 5, 2026  
**Auditor / Implementer**: Antigravity (Advanced Agentic Coding AI)  
**Status**: COMPLETED & VERIFIED — CLOSED  

---

## 1. Executive Summary

RBAC Phase 2 — Batch V implementation for **Approvals Management** (`app/api/approval.py`) is complete. All 4 active production routes were migrated from legacy hardcoded role allowlists (`require_roles(...)` / `APPROVAL_ROLES`) to canonical database-driven RBAC dependencies (`require_permission(...)`) using the dedicated independent namespace `approvals.*` (`approvals.view`, `approvals.create`, `approvals.approve`).

Critical architectural, multi-tenant IDOR, and workflow integrity flaws have been resolved:
- **Zero Role Hardcoding**: Eradicated `require_roles(...)`, `APPROVAL_ROLES`, and role name strings from authorization decisions.
- **Tenant Isolation & IDOR Protection**: Masked all foreign approvals and foreign target entities with HTTP 404 (eliminating existence leaks). Unscoped entity queries have been converted to company-scoped queries.
- **Super Admin Semantics**: Canonical `getattr(current_user, "is_super_admin", False) is True` check applied. Cross-company management enabled for Super Admin; tenantless non-SA users are denied with HTTP 403.
- **State Machine Invariants**: Double-decision vulnerabilities eliminated; approve and reject endpoints strictly require `status == "Pending"`. Decisions on already `Approved` or `Rejected` items return HTTP 400.
- **Segregation of Duties**: Requesters are strictly prohibited from approving or rejecting their own requests (`current_user.id == obj.requested_by` -> HTTP 400).
- **Duplicate Pending Protection**: Enforced invariant of only 1 Pending approval per `(entity_type, entity_id)` -> HTTP 400.
- **Target Entity State Synchronization**: Synchronized downstream status transitions across all 7 supported entity types (`boq`, `measurement`, `purchase_order`, `document`, `drawing`, `bill`, `journal_entry`).
- **Transaction Atomicity**: Eliminated double-commit pattern; approval status mutation, target entity update, and notification creation execute atomically under a single transaction with automatic rollback on error.
- **Exception Hygiene**: Raw database/SQLAlchemy errors sanitized, logged with full traceback, and returned as clean HTTP 500 without leaking schema or SQL details.

---

## 2. Exact Route Inventory & Permission Mapping

Every active production route in `app/api/approval.py` now enforces canonical database-driven permissions:

| # | HTTP Method | Endpoint Sub-Path | Full Mounted Path | Function Name | Previous Auth | Canonical Permission |
|---|-------------|-------------------|-------------------|---------------|---------------|----------------------|
| 1 | `POST` | `/` | `/api/v1/approvals` | `create_approval` | `require_roles(APPROVAL_ROLES)` | `require_permission("approvals.create")` |
| 2 | `GET` | `/` | `/api/v1/approvals` | `list_approvals` | `require_roles(APPROVAL_ROLES)` | `require_permission("approvals.view")` |
| 3 | `PUT` | `/{id}/approve` | `/api/v1/approvals/{id}/approve` | `approve_approval` | `require_roles(APPROVAL_ROLES)` | `require_permission("approvals.approve")` |
| 4 | `PUT` | `/{id}/reject` | `/api/v1/approvals/{id}/reject` | `reject_approval` | `require_roles(APPROVAL_ROLES)` | `require_permission("approvals.approve")` |

- **Batch V Active Production Routes**: **4**
- **Non-canonical `require_roles(...)` in module**: **0** (completely removed)
- **Hardcoded role lists / strings in authorization decisions**: **0** (completely removed)

---

## 3. RBAC Seed & Dynamic Authorization

### 3.1 Permission Catalog Seed
Updated `app/core/rbac_seed.py` with the independent `approvals` namespace:
```python
"approvals": [
    "view",
    "create",
    "approve",
]
```
Seeded permissions into MySQL database:
- `approvals.view` (ID: 422)
- `approvals.create` (ID: 423)
- `approvals.approve` (ID: 424)

### 3.2 Dynamic RBAC Mechanisms
The module supports all canonical DB-driven authorization mechanisms without server restart:
1. **Dynamic DB Grants**: Adding `RolePermission` immediately enables access.
2. **Dynamic DB Revokes**: Deleting `RolePermission` immediately revokes access.
3. **Positive User Overrides**: Adding `UserPermissionOverride(is_granted=True)` grants access to users lacking role permissions.
4. **Negative User Overrides**: Adding `UserPermissionOverride(is_granted=False)` immediately denies access even if the role holds the permission.
5. **Wildcard Permissions**: Supports `approvals.*` namespace wildcard and platform-level `*` wildcard.
6. **Legacy Role Immunity**: A user with role `"Admin"` or `"Project Manager"` lacking permissions is denied access (HTTP 403); role names do not confer implicit privileges.

---

## 4. Multi-Tenant Isolation & Security Remediations

1. **Foreign Approval IDOR Masking (P0)**:
   - When querying an approval by ID for approve/reject decisions, non-SA queries join `User` and filter by `User.company_id == current_user.company_id`.
   - If the approval does not exist or belongs to another company, HTTP 404 is returned immediately (`raise HTTPException(status_code=404, detail="Approval not found")`). No 403 existence oracle is exposed.
2. **Foreign Target Entity IDOR Masking on Create (P0)**:
   - When creating an approval request, target entity existence and company ownership are validated:
     - `boq`: joins `Project`, checks `Project.company_id == current_user.company_id`.
     - `measurement` (`FinalMeasurement`): joins `Project`, checks `Project.company_id == current_user.company_id`.
     - `purchase_order`: joins `Project`, checks `Project.company_id == current_user.company_id`.
     - `document`: joins `Project`, checks `Project.company_id == current_user.company_id`.
     - `drawing` (`DrawingDocument`): joins `Project`, checks `Project.company_id == current_user.company_id`.
     - `bill` (`RABill`): joins `Project`, checks `Project.company_id == current_user.company_id`.
     - `journal_entry`: checks `JournalEntry.company_id == current_user.company_id`.
   - If target entity is foreign or does not exist, HTTP 404 is returned (`detail="Target entity not found"`).
3. **List Tenant Scoping (P0)**:
   - Non-SA list queries join `User` on `Approval.requested_by == User.id` and filter strictly by `User.company_id == current_user.company_id`.
   - Approvals from other tenants are never returned.
4. **Canonical Super Admin Semantics (P1)**:
   - Canonical check: `is_sa = getattr(current_user, "is_super_admin", False) is True`.
   - Super Admin with `company_id=None` can list cross-company approvals, create approvals, and decision approvals.
   - Tenantless non-SA users (`company_id=None`) are rejected with HTTP 403 (`"User does not belong to any company"`).

---

## 5. Workflow State Machine & Business Invariants

1. **Double-Decision Protection**:
   - Both `/approve` and `/reject` verify `obj.status == "Pending"`.
   - Any attempt to approve or reject an already `Approved` or `Rejected` approval fails with HTTP 400 (`"Only pending approvals can be processed"`).
2. **Segregation of Duties (Self-Approval Prevention)**:
   - Decision endpoints verify `obj.requested_by != current_user.id`.
   - If the requester attempts to approve or reject their own request, the action is blocked with HTTP 400 (`"Users cannot approve or reject their own requests"`).
3. **Duplicate Pending Protection**:
   - `POST /api/v1/approvals` queries for existing approvals with the same `entity_type` and `entity_id` where `status == "Pending"`.
   - If an existing pending request is found, creation is blocked with HTTP 400 (`"A pending approval already exists for this entity"`).
4. **Mandatory Rejection Remarks**:
   - `PUT /api/v1/approvals/{id}/reject` enforces non-empty `payload.remarks`. If missing or whitespace, returns HTTP 400 (`"Remarks are required for rejection"`).
5. **Deterministic Target Entity State Synchronization**:
   - **Approval**:
     - `boq` -> `approval_status = "Approved"`
     - `measurement` -> `status = "APPROVED"`
     - `purchase_order` -> `status = "APPROVED"`
     - `document` -> `status = DocumentStatus.APPROVED`
     - `drawing` -> `approval_status = DocumentStatus.APPROVED`, `approval_id = obj.id`
     - `bill` -> `status = "Approved"`
     - `journal_entry` -> `status = "Posted"`
   - **Rejection**:
     - `boq` -> `approval_status = "Rejected"`
     - `measurement` -> `status = "REJECTED"`
     - `purchase_order` -> `status = "REJECTED"`
     - `document` -> `status = DocumentStatus.REJECTED`
     - `drawing` -> `approval_status = DocumentStatus.REJECTED`, `approval_id = obj.id`
     - `bill` -> `status = "Rejected"`
     - `journal_entry` -> `status = "Rejected"`

---

## 6. Transaction Atomicity & Exception Hygiene

1. **Single Atomic Commit**:
   - Removed the double-commit pattern (`commit -> notify -> commit`).
   - Approval state change, target entity state change, and notification insertion are all queued in the active session and committed via a single `await db.commit()`.
2. **Atomic Rollback on Downstream Failure**:
   - If notification creation fails (e.g. invalid recipient or DB failure), the entire operation executes `await db.rollback()`.
   - Approval remains `Pending`, target entity remains untouched, and HTTP 500 is returned.
   - Verified via automated unit test `test_approvals_atomic_rollback_on_notification_failure`.
3. **Sanitized Exception Handling**:
   - Unexpected exceptions are caught, logged with full stack trace via `logger.exception(...)`, and masked with a generic HTTP 500 error (`"Failed to process approval"` / `"Internal server error"`).

---

## 7. Verification & Automated Test Results

### 7.1 Batch V Dedicated Test Suite (`tests/api/test_rbac_batch_v_approvals.py`)
All 22 test cases passed:
1. `test_approvals_unauthenticated`: PASSED
2. `test_approvals_missing_permissions_403`: PASSED
3. `test_approvals_runtime_db_grant_and_revoke`: PASSED
4. `test_approvals_user_permission_override_positive`: PASSED
5. `test_approvals_user_permission_override_negative`: PASSED
6. `test_approvals_wildcard_permissions`: PASSED
7. `test_approvals_global_wildcard_permission`: PASSED
8. `test_approvals_legacy_role_immunity`: PASSED
9. `test_approvals_tenantless_non_sa_denial`: PASSED
10. `test_approvals_super_admin_cross_company`: PASSED
11. `test_approvals_own_tenant_lifecycle`: PASSED
12. `test_approvals_foreign_approval_idor_masking`: PASSED
13. `test_approvals_foreign_target_entity_injection`: PASSED
14. `test_approvals_cross_tenant_list_isolation`: PASSED
15. `test_approvals_state_machine_double_decision_blocked`: PASSED
16. `test_approvals_rejection_requires_remarks`: PASSED
17. `test_approvals_self_approval_blocked`: PASSED
18. `test_approvals_duplicate_pending_blocked`: PASSED
19. `test_approvals_target_state_sync_all_entity_types`: PASSED
20. `test_approvals_atomic_rollback_on_notification_failure`: PASSED
21. `test_approvals_ast_zero_role_hardcoding`: PASSED
22. `test_approvals_target_state_sync_drawing_and_po`: PASSED

**Result**: **22 passed** in 21.16s.

### 7.2 Regression Verification Across Prior Batches
- **Batch U (Journal Management)**: `pytest tests/api/test_rbac_batch_u_journal.py` -> **43 passed** in 39.46s.
- **Batch T (Payment Vouchers)**: `pytest tests/api/test_rbac_batch_t_payment_vouchers.py` -> **15 passed** in 16.27s.
- **Batch S (Vendor Bills)**: `pytest tests/api/test_rbac_batch_s_vendor_bills.py` -> **19 passed** in 18.30s.

**Combined Tests Passed**: **99 passed**, 0 failed.

### 7.3 Alembic Schema & Migration State
- `alembic current`: `f5a6b7c8d9e0 (head)`
- `alembic check`: Clean ("No new upgrade operations detected.")
- New migrations: **0**
- Unintended schema drift: **0**

---

## 8. Route Accounting & Cumulative Progress

```
Batch U Closed Baseline           : 385 routes
Batch V (Approvals Management)    :   4 routes
------------------------------------------------
New Authoritative Cumulative Total: 389 routes
```

Active Production Route Breakdown for Batch V:
1. `POST /api/v1/approvals` -> `require_permission("approvals.create")`
2. `GET /api/v1/approvals` -> `require_permission("approvals.view")`
3. `PUT /api/v1/approvals/{id}/approve` -> `require_permission("approvals.approve")`
4. `PUT /api/v1/approvals/{id}/reject` -> `require_permission("approvals.approve")`

---

## 9. Final Verdict

```
===================================================================
BATCH V STATUS: CLOSED (READY)
===================================================================
```

All audit requirements satisfied:
- Zero `require_roles` or hardcoded role logic in `app/api/approval.py`
- 100% canonical database-driven RBAC via `require_permission`
- Dynamic grants, revokes, user overrides, and wildcards verified
- Strict tenant scoping and 404 IDOR masking enforced
- State machine invariants, self-approval prevention, duplicate pending checks enforced
- Atomic transaction and rollback verified
- 99/99 regression and batch tests passed
- Alembic clean at head `f5a6b7c8d9e0`
- Cumulative route count exactly 389.

Batch V is officially **CLOSED**. Execution terminates here; Batch W is **NOT** started.
