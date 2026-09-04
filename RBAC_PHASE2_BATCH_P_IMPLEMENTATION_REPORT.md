# RBAC Phase 2 — Batch P Implementation Report
**Module**: Payroll & Compensation Management  
**Source File**: `app/api/payroll.py`  
**Test Suite**: `tests/api/test_rbac_phase2_batch_p.py`  
**Implementation Date**: September 4, 2026  
**Auditor / Implementer**: Antigravity (Advanced Agentic Coding AI)  
**Status**: CLOSED & VERIFIED  

---

## 1. Executive Summary

Batch P of RBAC Phase 2 successfully migrates **Payroll & Compensation Management** (`app/api/payroll.py`) to the database-driven Role-Based Access Control (RBAC) architecture. 

All 11 active production routes under `/api/v1/accountant/payroll` now declare permissions exclusively via `require_permission(...)`. All legacy inline role checks (`current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]`) have been completely eradicated. 

Furthermore, comprehensive multi-tenant boundary enforcement and IDOR masking were implemented across all queries (`LabourPayroll`, `Labour`, `User`, `Transaction`, `RABill`, `BankAccount`). Pre-mutation validation on `POST /staff/process` strictly guarantees that staff user, project, and bank account ownership are validated against caller tenant before any financial transaction or double-entry journal line is written.

---

## 2. Exact Active Production Route Inventory (11 Routes)

All routes are mounted at `/api/v1/accountant/payroll`:

| # | HTTP Method | Endpoint | Canonical RBAC Dependency | Description |
|---|-------------|----------|---------------------------|-------------|
| 1 | `GET` | `/summary` | `require_permission("payroll.view")` | Aggregate payroll summary metrics |
| 2 | `GET` | `/payslip/export` | `require_permission("payroll.export")` | Export labour payroll payslips as CSV |
| 3 | `GET` | `/staff/register` | `require_permission("payroll.view")` | Staff member eligibility register |
| 4 | `POST` | `/staff/process` | `require_permission("payroll.create")` | Disburse staff salary & post journal entry |
| 5 | `GET` | `/staff/history` | `require_permission("payroll.view")` | Historical staff salary payment transactions |
| 6 | `GET` | `/labour/wages` | `require_permission("payroll.view")` | Calculate dynamic labour wages by date range |
| 7 | `GET` | `/contractor/bills` | `require_permission("payroll.view")` | Unpaid contractor running account (RA) bills |
| 8 | `GET` | `/staff/export` | `require_permission("payroll.export")` | Export staff salary register as CSV |
| 9 | `GET` | `/contractor/export` | `require_permission("payroll.export")` | Export contractor bills as CSV |
| 10 | `GET` | `/register/export` | `require_permission("payroll.export")` | Export unified payroll register as CSV |
| 11 | `GET` | `/register` | `require_permission("payroll.view")` | List unified payroll payment transactions |

---

## 3. Permission Mapping

Target permissions were sourced directly from the pre-existing database catalog (`module = 'payroll'`):

| Action | Target Permission Code | Catalog ID | Target Routes |
|--------|------------------------|------------|---------------|
| **View** | `payroll.view` | 171 | 6 routes (`/summary`, `/staff/register`, `/staff/history`, `/labour/wages`, `/contractor/bills`, `/register`) |
| **Create** | `payroll.create` | 172 | 1 route (`/staff/process`) |
| **Export** | `payroll.export` | 176 | 4 routes (`/payslip/export`, `/staff/export`, `/contractor/export`, `/register/export`) |

---

## 4. RBAC Changes

1. **Replaced Raw Dependencies**:
   - `GET /summary`: Replaced unauthenticated/raw auth with `require_permission("payroll.view")`.
   - 10 other routes: Removed all hardcoded inline checks `if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]: raise HTTPException(status_code=403, detail="Not authorized")`.
2. **Preserved Subscription Entitlement**:
   - Router-level dependency `dependencies=[Depends(require_feature("payroll", "Payroll Module"))]` is preserved intact.
3. **Database-Driven Dynamic Authority**:
   - Role permissions, positive user overrides (`is_granted=True`), negative user overrides (`is_granted=False`), and wildcard permissions (`payroll.*`) take immediate effect at runtime without restarting the application.
4. **Zero Role-Based Immunity**:
   - Users with role name "Admin" or "Accountant" lacking database permission assignments receive 403 Forbidden.

---

## 5. Tenant Isolation Changes

1. **Company Scoping on Queries**:
   - **`LabourPayroll`**: Joined with `Project` and filtered by `Project.company_id == current_user.company_id` for non-SA callers.
   - **`User` (Staff Register & Export)**: Filtered by `User.company_id == current_user.company_id`.
   - **`Transaction` (Staff History, Register, Register Export)**: Joined with `Project` and filtered by `Project.company_id == current_user.company_id`.
   - **`Labour` (Labour Wages, Payslip/Register Export)**: Filtered by `Labour.company_id == current_user.company_id`.
   - **`RABill` (Contractor Bills & Export)**: Joined with `Project` and filtered by `Project.company_id == current_user.company_id`.
2. **Missing Company Isolation**:
   - Non-SA callers with `current_user.company_id is None` are immediately blocked with `403 Forbidden ("User does not belong to any company")`.

---

## 6. Super Admin Semantics

1. **Canonical Check**:
   - Implemented via `is_sa = getattr(current_user, "is_super_admin", False) is True`.
   - Role name string checks ("Super Admin") were strictly avoided.
2. **Cross-Company Global View**:
   - When `is_sa` is True, tenant join-filters are bypassed, permitting cross-company platform-level payroll aggregation, staff registers, and exports.
3. **Null Tenant Mutation Protection**:
   - SA mutation does not create NULL-company or orphan payroll records.

---

## 7. IDOR Fixes & Resource Validation

In `POST /staff/process`, strict pre-mutation validation occurs before any financial record or ledger entry is written:
1. **Staff User Validation**:
   - Resolved via `select(User).where(User.id == payload.user_id)` scoped to `User.company_id == current_user.company_id` for non-SA.
   - Cross-tenant or nonexistent staff returns masked `404 Not Found ("Staff user not found")`.
2. **Project Validation**:
   - Resolved via `select(Project).where(Project.id == payload.project_id)` scoped to `Project.company_id == current_user.company_id` for non-SA.
   - Cross-tenant or nonexistent project returns masked `404 Not Found ("Project not found")`.
3. **Bank Account Validation**:
   - When `payment_mode != "cash"`, `BankAccount` is joined with `Account` and filtered by `Account.company_id == current_user.company_id` for non-SA.
   - Cross-tenant or nonexistent bank account returns masked `404 Not Found ("Bank account not found")`.

---

## 8. Exception Handling & Error Sanitization

1. **Masked 404s**: Foreign tenant resources return generic 404 without leaking resource existence in another company.
2. **Exception Sanitization**:
   - Removed all instances of raw exception details (`detail=str(e)`).
   - Wrapped database mutation in `try ... except HTTPException: raise except Exception:` with `logger.exception(...)` and generic safe 500 responses (`"Failed to process staff salary"`).

---

## 9. Business Invariant Preservation

All core payroll domain invariants are preserved intact:
1. **Duplicate Salary Protection**: Enforced using deterministic linked ID `STAFF-SALARY:{user_id}:{month_year}`; re-submission returns `400 Bad Request ("Salary already processed for this month")`.
2. **Eligible Staff Roles**: `get_allowed_staff_roles()` preserved as business eligibility validation; non-staff roles return `400 Bad Request ("Invalid staff user")`.
3. **Balanced Double-Entry Accounting**: Creates balanced `JournalLine` entries (Debit Staff Salary Account, Credit Cash/Bank Account) linked to `JournalEntry`.
4. **Transaction Reference Formatting**: Serialized as `gross:{gross_salary}|deduct:{deductions}`.
5. **Labour Wage Calculation**: Preserved dynamic working hours calculation (`working_hours * 50`).
6. **Contractor Active Bill Filtering**: Active listing filters `RABill.status != "Paid"`.
7. **CSV Export Contracts**: Exact column names, ordering, and `text/csv` streaming responses preserved across all 4 export endpoints.

---

## 10. Test Results

The dedicated test suite `tests/api/test_rbac_phase2_batch_p.py` covers 14 comprehensive test categories:

```
====================== 14 passed, 169 warnings in 21.70s ======================
```

Test coverage includes:
- `test_batch_p_401_no_token_all_routes`: PASSED (all 11 routes return 401 without auth)
- `test_batch_p_403_authenticated_zero_permissions_all_routes`: PASSED (all 11 routes return 403)
- `test_batch_p_dynamic_db_grant_and_revoke`: PASSED (403 -> DB grant -> 200 -> DB revoke -> 403)
- `test_batch_p_positive_and_negative_user_overrides`: PASSED (positive override 200, negative override 403)
- `test_batch_p_wildcard_permission`: PASSED (`payroll.*` enables view, create, export)
- `test_batch_p_legacy_role_immunity`: PASSED (Admin role string alone returns 403)
- `test_batch_p_own_tenant_success_all_routes`: PASSED (all 11 routes succeed for own company)
- `test_batch_p_cross_tenant_idor_staff_project_bank`: PASSED (masked 404 for foreign staff/project/bank)
- `test_batch_p_tenant_data_isolation`: PASSED (strict multi-tenant data isolation on summary, lists, exports)
- `test_batch_p_super_admin_cross_company_access`: PASSED (cross-company global summary aggregation)
- `test_batch_p_non_sa_company_id_none`: PASSED (403 denied for user without tenant company)
- `test_batch_p_business_invariants_duplicate_and_eligibility`: PASSED (duplicate guard & role eligibility)
- `test_batch_p_balanced_journal_and_transaction_verification`: PASSED (balanced double-entry lines & transaction link)
- `test_batch_p_csv_headers_and_integrity`: PASSED (CSV formatting, media type, disposition headers)

---

## 11. Full Repository & Cumulative Suite Verification

1. **Peripheral Security Suite**:
   ```
   python -m pytest tests/api/test_peripheral_security.py -q
   10 passed, 169 warnings in 12.03s
   ```
2. **Tenant IDOR Suite**:
   ```
   python -m pytest tests/api/test_tenant_idor.py -q
   59 passed, 169 warnings in 25.99s
   ```
3. **Cumulative Phase 2 RBAC Suites (Batches A through P)**:
   ```
   python -m pytest (Get-ChildItem tests/api/test_rbac_*.py) -q
   241 passed, 170 warnings in 178.04s (0:02:58)
   ```

Zero regressions across Batches A–O.

---

## 12. Alembic Verification

- Current Head: `e4f5a6b7c8d9`
- `alembic check`:
  ```
  No new upgrade operations detected.
  ```
- Migrations Created: **0**

---

## 13. Files Changed

| File | Change Type | Purpose |
|------|-------------|---------|
| `app/api/payroll.py` | MODIFIED | Implemented `require_permission()`, tenant isolation, IDOR masking, SA semantics |
| `tests/api/test_rbac_phase2_batch_p.py` | NEW | 14 test functions verifying all security and invariant requirements |

---

## 14. Route Count Accounting

$$\text{Previous Authoritative Cumulative Route Count (Batches A–O)} = 341$$
$$\text{Batch P Active Production Routes} = 11$$
$$\text{New Authoritative Cumulative Route Count} = 341 + 11 = \mathbf{352}$$

---

## 15. Final Acceptance & Sign-off

- [x] All 11 routes declare canonical `require_permission(...)`.
- [x] Zero hardcoded role allowlists or legacy role string checks remain in `app/api/payroll.py`.
- [x] All tenant-owned queries (`LabourPayroll`, `User`, `Transaction`, `Labour`, `RABill`) are strictly scoped.
- [x] Pre-mutation validation checks staff user, project, and bank account ownership before writing ledger entries.
- [x] Cross-tenant resources return masked 404 Not Found.
- [x] Canonical Super Admin global semantics verified.
- [x] Non-SA `company_id=None` callers denied with 403.
- [x] Dynamic database grants, revokes, user overrides, and wildcard permissions verified.
- [x] All business invariants (duplicate guard, staff roles, balanced double-entry, calculations, CSV contracts) preserved.
- [x] Batch P test suite passes (14/14 passed).
- [x] Peripheral security tests pass (10/10 passed).
- [x] Tenant IDOR tests pass (59/59 passed).
- [x] Cumulative Phase 2 RBAC test suites pass (241/241 passed).
- [x] Alembic head remains `e4f5a6b7c8d9` with 0 new migrations.

**VERDICT: Batch P is officially CLOSED.**
