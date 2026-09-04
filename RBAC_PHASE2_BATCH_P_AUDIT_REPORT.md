# RBAC Phase 2 — Batch P Audit Report
**Module**: Payroll & Compensation Management  
**Source File**: `app/api/payroll.py`  
**Audit Date**: September 4, 2026  
**Auditor**: Antigravity (Advanced Agentic Coding AI)  
**Status**: AUDITED — READY FOR IMPLEMENTATION  

---

## 1. Executive Summary

A comprehensive, read-only architectural, security, and multi-tenant IDOR audit of `app/api/payroll.py` was conducted to establish the exact scope, permission mapping, security vulnerabilities, and test requirements for **RBAC Phase 2 — Batch P**.

### Key Findings:
1. **Module Scope**: The module governs staff salary registers, monthly salary processing with double-entry journal and transaction logging, labour wage calculations, contractor bill tracking, and financial export registers.
2. **Exact Route Count**: Exactly **11 active production routes** are registered under the `/accountant/payroll` prefix, mounted at `/api/v1/accountant/payroll`.
3. **Security Posture (CRITICAL)**:
   - **Zero RBAC Protection**: Not a single route currently uses `require_permission(...)`.
   - **Hardcoded Role Allowlists**: 10 of 11 routes gate access with inline checks: `if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]: raise HTTPException(403)`. These endpoints are completely immune to database-driven role grants, revokes, and user-level overrides.
   - **Completely Unauthenticated Endpoint / Raw Auth**: 1 route (`GET /summary`) has zero role checks, allowing any authenticated user from any tenant to view company-wide payroll aggregate metrics.
   - **Catastrophic Multi-Tenant IDOR Vulnerabilities**: All 11 routes execute completely unscoped queries against `LabourPayroll`, `Labour`, `User`, `Transaction`, and `RABill` without filtering by `company_id`. Company A's users can view, calculate, export, and even process salaries against Company B's staff and projects.
4. **Zero Migration Impact**: The database permissions catalog already contains all 10 canonical permissions for the `payroll` module (`payroll.view`, `payroll.create`, `payroll.edit`, `payroll.delete`, `payroll.approve`, `payroll.export`, `payroll.manage`, `payroll.assign`, `payroll.upload`, `payroll.download`). Zero database schema changes, model modifications, or Alembic migrations are required.
5. **Cumulative Accounting**: Previous authoritative count is 341. Batch P contains 11 routes. The projected cumulative route count post-migration is **352**.

---

## 2. Selected Module

- **Functional Domain**: Payroll & Compensation Management (Staff Salaries, Labour Wages, Contractor Payments, and Payroll Ledgers)
- **Source File**: `app/api/payroll.py`
- **Module Tag**: `["Accountant Payroll"]`
- **Associated Models**:
  - `User` (`app/models/user.py`): Staff employee records
  - `Labour`, `LabourAttendance`, `LabourPayroll` (`app/models/labour.py`): Labour wage tracking and attendance
  - `Project` (`app/models/project.py`): Project ownership and allocation
  - `Transaction` (`app/models/invoice.py`): Cash/bank payment disbursements
  - `JournalEntry`, `JournalLine`, `Account`, `BankAccount` (`app/models/accountant.py`): Double-entry bookkeeping
  - `RABill` (`app/models/billing.py`): Contractor running account bills

---

## 3. Router / Mount Information

- **Router Instance**: `router` defined in `app/api/payroll.py:26`
- **Router Prefix**: `/accountant/payroll`
- **Router Level Dependencies**:
  - `dependencies=[Depends(require_feature("payroll", "Payroll Module"))]` (enforces active subscription plan feature entitlement)
- **Mount Point**: Mounted in `app/main.py:346` via `api_router.include_router(payroll_router)`
- **Top-Level API Mount**: `application.include_router(api_router, prefix="/api/v1")` in `app/main.py:355`
- **Full Active URL Prefix**: `/api/v1/accountant/payroll`

---

## 4. Exact Active Production Route Inventory

Every active route in `app/api/payroll.py` was forensically verified against the live FastAPI application routing table:

| # | HTTP Method | Endpoint Sub-Path | Full Mounted Path | Function Name | Current Auth | Current Authorization | Target Canonical Permission |
|---|-------------|-------------------|-------------------|---------------|--------------|-----------------------|-----------------------------|
| 1 | `GET` | `/summary` | `/api/v1/accountant/payroll/summary` | `payroll_summary` | `get_current_active_user` | None (RAW_AUTH) | `require_permission("payroll.view")` |
| 2 | `GET` | `/payslip/export` | `/api/v1/accountant/payroll/payslip/export` | `export_payslips` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.export")` |
| 3 | `GET` | `/staff/register` | `/api/v1/accountant/payroll/staff/register` | `get_staff_register` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.view")` |
| 4 | `POST` | `/staff/process` | `/api/v1/accountant/payroll/staff/process` | `process_staff_salary` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.create")` |
| 5 | `GET` | `/staff/history` | `/api/v1/accountant/payroll/staff/history` | `get_staff_history` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.view")` |
| 6 | `GET` | `/labour/wages` | `/api/v1/accountant/payroll/labour/wages` | `get_labour_wages` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.view")` |
| 7 | `GET` | `/contractor/bills` | `/api/v1/accountant/payroll/contractor/bills` | `get_contractor_bills` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.view")` |
| 8 | `GET` | `/staff/export` | `/api/v1/accountant/payroll/staff/export` | `export_staff_payroll` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.export")` |
| 9 | `GET` | `/contractor/export` | `/api/v1/accountant/payroll/contractor/export` | `export_contractor_payroll` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.export")` |
| 10 | `GET` | `/register/export` | `/api/v1/accountant/payroll/register/export` | `export_payroll_register` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.export")` |
| 11 | `GET` | `/register` | `/api/v1/accountant/payroll/register` | `get_payroll_register` | `get_current_active_user` | Hardcoded Admin/Accountant | `require_permission("payroll.view")` |

---

## 5. Route Count

- Active Production Routes in `app/api/payroll.py`: **11**
- Commented-out / dead routes: **0**
- Duplicate routes: **0**
- Total Active Production Route Count: **11**

---

## 6. Current Authentication State

- All 11 routes declare `current_user: User = Depends(get_current_active_user)`.
- Router-level dependency declares `Depends(require_feature("payroll", "Payroll Module"))`.
- However, zero routes declare `require_permission(...)`.
- Unauthenticated requests currently return 401 Unauthorized via `get_current_active_user` -> `get_current_user`.

---

## 7. Current Authorization State

- **Hardcoded In-Handler Role Checks (10 routes)**:
  ```python
  if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
      raise HTTPException(status_code=403, detail="Not authorized")
  ```
  Found on routes: 2, 3, 4, 5, 6, 7, 8, 9, 10, 11.
  - Flaw 1: Bypasses the RBAC engine entirely.
  - Flaw 2: Ignores `role_permissions` database mapping.
  - Flaw 3: Ignores `user_permission_overrides` table (both positive grants and negative revocations).
  - Flaw 4: Immune to wildcard permissions (`payroll.*`, `*`).
- **Raw Authentication with Zero Role Check (1 route)**:
  - `GET /summary` (line 40): Has NO role check whatsoever. Any logged-in user (Site Engineer, Contractor, Client) can query company-wide payroll financial summaries.

---

## 8. Existing Permission Catalog

The database `permissions` table was forensically inspected for module `payroll`:

| Permission Code | Permission ID | Module | Action | Description | Status |
|-----------------|:-------------:|:------:|:------:|-------------|:------:|
| `payroll.view` | 171 | `payroll` | `view` | View payroll records, summaries, registers, and history | **EXISTS IN DB** |
| `payroll.create` | 172 | `payroll` | `create` | Process staff salaries and disbursements | **EXISTS IN DB** |
| `payroll.edit` | 173 | `payroll` | `edit` | Modify payroll records | **EXISTS IN DB** |
| `payroll.delete` | 174 | `payroll` | `delete` | Delete payroll records | **EXISTS IN DB** |
| `payroll.approve` | 175 | `payroll` | `approve` | Approve payroll registers and disbursements | **EXISTS IN DB** |
| `payroll.export` | 176 | `payroll` | `export` | Export payroll, payslips, and salary sheets to CSV | **EXISTS IN DB** |
| `payroll.manage` | 177 | `payroll` | `manage` | Administrative payroll management | **EXISTS IN DB** |
| `payroll.assign` | 178 | `payroll` | `assign` | Assign payroll structures | **EXISTS IN DB** |
| `payroll.upload` | 179 | `payroll` | `upload` | Upload payroll documents | **EXISTS IN DB** |
| `payroll.download` | 180 | `payroll` | `download` | Download payroll documents | **EXISTS IN DB** |

### Permission Mapping Summary:
- **`payroll.view`**: 6 routes (`/summary`, `/staff/register`, `/staff/history`, `/labour/wages`, `/contractor/bills`, `/register`)
- **`payroll.create`**: 1 route (`/staff/process`)
- **`payroll.export`**: 4 routes (`/payslip/export`, `/staff/export`, `/contractor/export`, `/register/export`)
- **New Permissions Required**: **0**
- **Alembic Migrations Required**: **0**

---

## 9. Tenant Ownership Hierarchy

The data relationships governing payroll records follow this ownership graph:

```
Company (Tenant Root)
  ├── User (Staff) [User.company_id == Company.id]
  ├── Account (Chart of Accounts) [Account.company_id == Company.id]
  │     └── BankAccount [BankAccount.account_id == Account.id]
  ├── Project [Project.company_id == Company.id]
  │     ├── Transaction [Transaction.project_id == Project.id]
  │     ├── RABill [RABill.project_id == Project.id]
  │     └── LabourPayroll [LabourPayroll.project_id == Project.id]
  └── Labour [Labour.company_id == Company.id]
        ├── LabourAttendance [LabourAttendance.labour_id == Labour.id]
        └── LabourPayroll [LabourPayroll.labour_id == Labour.id]
```

### Critical Scoping Rules:
1. **Non-Super-Admin**:
   - `User` queries must be scoped to `User.company_id == current_user.company_id`.
   - `Labour` queries must be scoped to `Labour.company_id == current_user.company_id`.
   - `Project` queries must be scoped to `Project.company_id == current_user.company_id`.
   - `LabourPayroll` queries must be scoped via join on `Project` or `Labour` matching `current_user.company_id`.
   - `Transaction` queries must be scoped via join on `Project` matching `current_user.company_id`.
   - `RABill` queries must be scoped via join on `Project` matching `current_user.company_id`.
   - `Account` and `BankAccount` lookups must verify `Account.company_id == current_user.company_id`.
2. **Super Admin**:
   - Evaluated exclusively via `getattr(current_user, "is_super_admin", False) is True`.
   - Permitted global platform-wide access across all tenant records.
3. **Non-Super-Admin with `company_id=None`**:
   - Must be denied and isolated (HTTP 403 / 404).

---

## 10. IDOR Findings

### P0-1: Global Unscoped Labour Payroll Exposure (`/summary`, `/payslip/export`)
- **Evidence**: `app/api/payroll.py:48-64` and `app/api/payroll.py:82-84` execute:
  ```python
  pending_payroll_query = await db.scalar(
      select(func.sum(LabourPayroll.total_wage - LabourPayroll.paid_amount))
      .where(LabourPayroll.status == PayrollStatus.PENDING)
  )
  stmt = select(LabourPayroll).options(selectinload(LabourPayroll.labour)).order_by(LabourPayroll.created_at.desc())
  ```
- **Vulnerability**: Zero filtering on tenant company. All labour payroll figures, wages, advances, and payslips from all companies in the multi-tenant system are aggregated and exported.
- **Remediation**: Join `LabourPayroll` with `Project` and filter `Project.company_id == current_user.company_id` for non-SA callers.

### P0-2: Cross-Tenant Staff Directory Leakage (`/staff/register`)
- **Evidence**: `app/api/payroll.py:121`:
  ```python
  stmt = select(User).where(User.role.in_(get_allowed_staff_roles()))
  ```
- **Vulnerability**: Fetches all users in Admin, PM, SE, and Accountant roles platform-wide. An accountant in Company A can inspect names, roles, and designations of all staff in competitor companies.
- **Remediation**: Add `User.company_id == current_user.company_id` for non-SA callers.

### P0-3: Cross-Tenant Staff Salary Processing & Unauthorized Fund Manipulation (`/staff/process`)
- **Evidence**: `app/api/payroll.py:137-164`:
  ```python
  staff = await db.get(User, payload.user_id)
  ...
  bank = await db.get(BankAccount, payload.bank_account_id)
  ```
- **Vulnerability**:
  - `staff` lookup does NOT verify `staff.company_id == current_user.company_id`.
  - `payload.project_id` does NOT verify `Project.company_id == current_user.company_id`.
  - `payload.bank_account_id` does NOT verify `BankAccount -> Account.company_id == current_user.company_id`.
  - An attacker in Company A can disburse salaries against another company's staff member, charge another company's project, and post transactions/journal entries to foreign bank accounts.
- **Remediation**: Verify that `staff`, `project`, and `bank_account` all belong to `current_user.company_id` (return masked 404 on mismatch).

### P0-4: Cross-Tenant Transaction Ledger Leakage (`/staff/history`, `/register`, `/register/export`)
- **Evidence**: `app/api/payroll.py:198, 339, 402`:
  ```python
  stmt = select(Transaction).where(Transaction.linked_to.like("STAFF-SALARY:%"))
  stmt = select(Transaction).where(
      Transaction.linked_to.like("STAFF-SALARY:%") |
      Transaction.linked_to.like("LABOUR-WAGE:%") |
      Transaction.linked_to.like("CONTRACTOR-PAY:%")
  )
  ```
- **Vulnerability**: Transactions are queried without joining `Project` or checking tenant ownership. Full financial disbursement history of all tenants is returned and exported.
- **Remediation**: Join `Transaction.project_id == Project.id` and filter `Project.company_id == current_user.company_id` for non-SA callers.

### P0-5: Cross-Tenant Labour Wage Leakage (`/labour/wages`)
- **Evidence**: `app/api/payroll.py:215`:
  ```python
  stmt = select(Labour)
  labours = (await db.execute(stmt)).scalars().all()
  ```
- **Vulnerability**: Queries every labour worker in the entire database regardless of tenant.
- **Remediation**: Filter `Labour.company_id == current_user.company_id` for non-SA callers.

### P0-6: Cross-Tenant Contractor RA Bills Exposure (`/contractor/bills`, `/contractor/export`)
- **Evidence**: `app/api/payroll.py:252, 310`:
  ```python
  stmt = select(RABill).where(RABill.status != "Paid")
  stmt = select(RABill)
  ```
- **Vulnerability**: Queries `RABill` globally without tenant joins.
- **Remediation**: Join `RABill.project_id == Project.id` and filter `Project.company_id == current_user.company_id` for non-SA callers.

---

## 11. RBAC Findings

1. **Complete Absence of Granular Permissions**: Zero endpoints declare canonical `require_permission(...)`.
2. **Hardcoded Role Enforcement**: Handlers check `current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]`.
3. **No Dynamic Runtime Permission Support**: Assigning or revoking `payroll.view`, `payroll.create`, or `payroll.export` in the database currently has zero effect.
4. **No Wildcard Support**: Grants of `payroll.*` or `*` are completely ignored by the hardcoded role checks.
5. **No User Override Support**: `user_permission_overrides` records have zero effect on any route in this module.

---

## 12. Super Admin Findings

1. **No Canonical SA Evaluation**: Routes do not check `is_super_admin`.
2. **Global Access Block**: Because of the hardcoded `current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]`, a Super Admin whose role string is `"Super Admin"` is blocked with a 403 Forbidden!
3. **Requirement**: Apply canonical evaluation:
   ```python
   is_sa = getattr(current_user, "is_super_admin", False) is True
   ```
   Super Admin must be authorized through the canonical RBAC engine (`require_permission` grants global bypass for SA), and handlers must allow cross-tenant visibility for Super Admin.

---

## 13. Business Logic Invariants

The following invariants MUST be preserved during migration:

1. **Duplicate Salary Protection**:
   `linked_id = f"STAFF-SALARY:{payload.user_id}:{payload.month_year}"` prevents duplicate salary processing for the same user in the same month.
2. **Eligible Staff Roles**:
   `get_allowed_staff_roles()`: `[ADMIN, PROJECT_MANAGER, SITE_ENGINEER, ACCOUNTANT]` must be preserved for staff salary register and processing.
3. **Double-Entry Bookkeeping Integration**:
   - `get_payroll_account(db, "staff_salary_account_id")`
   - Primary cash account or selected bank account
   - Balanced `JournalEntry` with debit to staff salary account and credit to cash/bank account.
   - `Transaction` creation with `reference = f"gross:{payload.gross_salary}|deduct:{payload.deductions}"`.
4. **Dynamic Labour Wage Calculation**:
   Sum of `working_hours` across `LabourAttendance` within the `start_date` to `end_date` interval multiplied by the effective wage rate.
5. **Contractor RA Bills Status Filtering**:
   `RABill.status != "Paid"` for active bills listing.
6. **CSV Export Specifications**:
   - `payslips_export.csv`: Columns `["Employee/Labour Name", "Period", "Gross Pay", "Deduction", "Net Pay", "Status", "Payment Date"]`
   - `staff_salary.csv`: Columns `["Staff Name", "Role", "Department", "Designation", "Month", "Gross Salary", "Deductions", "Net Salary", "Payment Status", "Payment Date"]`
   - `contractor_payments.csv`: Columns `["Contractor", "Project", "Bill Number", "Gross Amount", "Deductions", "Net Payable", "Payment Status", "Payment Date"]`
   - `payroll_register.csv`: Columns `["Name", "Payroll Type", "Period", "Gross", "Deduction", "Net Amount", "Status", "Payment Date"]`
7. **Feature Entitlement Dependency**:
   Preserve `require_feature("payroll", "Payroll Module")` on the router.

---

## 14. Error Leakage Findings

1. **Information Leakage via 400 Errors**:
   - In `/staff/process`, `raise HTTPException(status_code=400, detail="Invalid staff user")` and `raise HTTPException(status_code=400, detail="Bank account not found")` allow cross-tenant resource enumeration. Foreign resources must return a masked `404 Not Found`.
2. **Uncaught Database Exceptions**:
   - In `/staff/process`, `await db.commit()` is called without try/except handling. Uncaught DB or constraint failures will leak raw database error messages unless protected with generic 500 error responses and `logger.exception(...)`.

---

## 15. Performance / Secondary Security Findings

1. **N+1 Query in `/labour/wages`**:
   - Loops over all labourers and executes a separate `select(LabourAttendance)` query for each one. Adding `Labour.company_id == current_user.company_id` reduces the blast radius to the caller's tenant.
2. **Unbounded Full Table Scans in Exports**:
   - `/staff/export` and `/register/export` load all users and all transactions into Python memory. Scoping queries to the caller's `company_id` bounds memory usage and database load.

---

## 16. Severity Classification

| Severity | Issue | Impact |
|:---:|:---|:---|
| **P0** | Global Unscoped Payroll Data (`/summary`, `/payslip/export`, `/staff/register`, `/staff/history`, `/labour/wages`, `/contractor/bills`, `/register`) | Massive cross-tenant financial information leakage. Every tenant's wage and salary data is exposed. |
| **P0** | Cross-Tenant Staff Salary Processing (`/staff/process`) | An attacker can trigger financial disbursements against other companies' staff, projects, and bank accounts. |
| **P0** | Completely Open Summary Route (`/summary`) | Authenticated users with any role (Client, Labour, Site Engineer) can view company-wide payroll aggregate metrics. |
| **P1** | Hardcoded Role Checks Across 10 Routes | Completely blocks database-driven RBAC, role grants/revokes, wildcard permissions, and user-level overrides. |
| **P1** | Super Admin Broken Authorization | Super Admin users with role `"Super Admin"` are rejected with 403 on 10 routes due to hardcoded role allowlists. |
| **P2** | Non-SA `company_id=None` Unhandled | Users without a company can view or process cross-tenant data. |
| **P2** | Cross-Tenant Resource Enumeration | Non-masked 400 errors leak resource existence. |
| **P3** | N+1 Labour Attendance Queries | Performance degradation during wage calculation. |

---

## 17. Required Implementation Scope

### Step 1: Remove Hardcoded Roles & Raw Auth
- Remove all `if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]` checks.
- Remove all raw `Depends(get_current_active_user)` route dependencies.

### Step 2: Apply Canonical `require_permission(...)`
- `GET /summary` -> `require_permission("payroll.view")`
- `GET /payslip/export` -> `require_permission("payroll.export")`
- `GET /staff/register` -> `require_permission("payroll.view")`
- `POST /staff/process` -> `require_permission("payroll.create")`
- `GET /staff/history` -> `require_permission("payroll.view")`
- `GET /labour/wages` -> `require_permission("payroll.view")`
- `GET /contractor/bills` -> `require_permission("payroll.view")`
- `GET /staff/export` -> `require_permission("payroll.export")`
- `GET /contractor/export` -> `require_permission("payroll.export")`
- `GET /register/export` -> `require_permission("payroll.export")`
- `GET /register` -> `require_permission("payroll.view")`

### Step 3: Implement Tenant Scoping & Masked 404s
- Create/reuse helper functions to verify that `User`, `Project`, `Labour`, `BankAccount`, and `Transaction` belong to the caller's company.
- Add `Project.company_id == current_user.company_id` join-filters on `LabourPayroll`, `Transaction`, and `RABill`.
- Add `User.company_id == current_user.company_id` on staff queries.
- Add `Labour.company_id == current_user.company_id` on labour queries.
- Mask foreign resource lookups with `NotFoundError("Staff user not found")`, `NotFoundError("Project not found")`, `NotFoundError("Bank account not found")`.

### Step 4: Super Admin Semantics
- Ensure canonical evaluation `is_sa = getattr(current_user, "is_super_admin", False) is True`.
- Non-SA users with `company_id=None` must be denied/isolated.

### Step 5: Exception Shielding
- Wrap database operations in `try/except` blocks, logging errors with `logger.exception(...)` and returning generic 500 error responses.

---

## 18. Required Test Scope

A dedicated test suite `tests/api/test_rbac_phase2_batch_p.py` must be created covering:

1. **401 Unauthorized**: No token across all 11 routes.
2. **403 Forbidden**: Authenticated user with 0 DB permissions across all 11 routes.
3. **Dynamic DB Grant**: 403 -> DB grant -> 200 -> DB revoke -> 403 across view, create, and export permissions.
4. **Positive User Override**: User without role permissions granted access via `UserPermissionOverride(is_granted=True)`.
5. **Negative User Override**: User with role permissions denied access via `UserPermissionOverride(is_granted=False)`.
6. **Wildcard Permission**: Granting `payroll.*` provides access across view, create, and export.
7. **Legacy Role Immunity**: User with role name "Admin" or "Accountant" but zero DB permissions receives 403.
8. **Own-Tenant Success**: Company A admin with proper permissions succeeds across all routes.
9. **Cross-Tenant Staff IDOR (404)**: Processing salary for Company B staff returns masked 404.
10. **Cross-Tenant Project IDOR (404)**: Processing salary with Company B project returns masked 404.
11. **Cross-Tenant Bank Account IDOR (404)**: Processing salary with Company B bank account returns masked 404.
12. **Cross-Tenant Summary Isolation**: Company A caller sees only Company A's labour payroll summary.
13. **Cross-Tenant Staff Register Isolation**: Company A caller sees only Company A's staff members.
14. **Cross-Tenant Staff History Isolation**: Company A caller sees only Company A's staff salary transactions.
15. **Cross-Tenant Labour Wages Isolation**: Company A caller calculates wages only for Company A's labourers.
16. **Cross-Tenant Contractor Bills Isolation**: Company A caller sees only Company A's contractor RA bills.
17. **Cross-Tenant Export Isolation**: CSV exports for payslips, staff, contractor bills, and register contain only Company A records.
18. **Super Admin Cross-Company Access**: Super Admin can view cross-company payroll records.
19. **Non-SA `company_id=None` Isolation**: Non-SA user without company is completely denied/isolated.
20. **Duplicate Salary Processing Guard**: Attempting to process salary twice for the same user and month returns 400.
21. **Staff Role Eligibility Validation**: Attempting to process salary for non-staff role returns 400/422.
22. **Double-Entry Journal & Transaction Creation**: Verify creation of balanced `JournalEntry` and `Transaction`.
23. **CSV Export Integrity**: Verify correct headers, media type `text/csv`, and non-empty output.
24. **Exception Detail Leakage Prevention**: Simulated internal errors return generic 500 without leaking database details.

---

## 19. Permission / Migration Impact

- **Database Migrations Required**: **0**
- **New Permissions Required**: **0**
- **Model Modifications Required**: **0**
- **Schema Modifications Required**: **0**
- All 10 permissions already exist in `permissions` table under module `payroll`.

---

## 20. Projected Cumulative Route Count

$$\text{Previous Cumulative Count} = 341$$
$$\text{Batch P Active Routes} = 11$$
$$\text{Projected Cumulative Count} = 341 + 11 = \mathbf{352}$$

---

## 21. Audit Verdict

**READY FOR IMPLEMENTATION**

The module is completely isolated, self-contained within `app/api/payroll.py`, fully supported by pre-existing database permissions, and presents critical multi-tenant IDOR vulnerabilities that make it the highest-priority candidate for Batch P.
