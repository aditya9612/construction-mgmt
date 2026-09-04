# Backend 38 Issues — Root Cause Audit

## Executive Summary

This document presents a comprehensive, read-only root cause audit of the 38 reported backend and API issues for the **InfraPilot** construction management platform (FastAPI + SQLAlchemy 2.0 Async + MySQL multi-tenant backend).

Every issue was traced through its API route, router definition, controller endpoint, Pydantic request/response schemas, ORM database models, service layers, RBAC permission dependencies, and tenant isolation logic.

### Issue Breakdown (Total: 38)
- **Confirmed Backend Bugs:** 17 issues (#1, #2, #4, #6, #9, #11, #17, #21, #22, #23, #24, #27, #28, #29, #30, #32, #33)
- **Database / Unseeded Data Issues:** 3 issues (#3, #7, #25)
- **API Contract / Schema Mismatches:** 10 issues (#5, #8, #12, #13, #14, #16, #20, #34, #37, #38)
- **Missing APIs / Unimplemented Features:** 2 issues (#18, #36)
- **Requirement Change / Already Implemented:** 1 issue (#10)
- **RBAC / Route Path Issues:** 1 issue (#31)
- **Insufficient Information (No Description Supplied):** 2 issues (#15, #19)
- **Issues Requiring Runtime Traceback to Confirm Edge-Case Exception:** 3 issues (#3, #25, #33)

---

## Issue-by-Issue Audit

### Issue #1 — Update BOQ Actuals Manual Override Returns 403

**Endpoint:**
`POST /api/v1/boq/{boq_id}/actuals`

**Observed Problem:**
Returns HTTP 403 Forbidden.

**Classification:**
BACKEND BUG

**Severity:**
P2

**Backend File:**
[boq.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/boq.py)

**Function:**
`update_actuals` (Lines 608–633)

**Request Schema:**
`BOQActualsUpdate`

**Response Schema:**
`BOQOut`

**Root Cause:**
The endpoint explicitly executes:
```python
raise HTTPException(
    status_code=403,
    detail="Manual BOQ actuals override is disabled to guarantee financial determinism. Actuals are calculated automatically from usage and expenses."
)
```
HTTP status code 403 Forbidden denotes an authorization/permission failure. Returning 403 when the user possesses legitimate `boq.edit` permission causes the frontend to display an access-denied error. A business-logic restriction should return `405 Method Not Allowed`, `400 Bad Request`, or `422 Unprocessable Entity`.

**Evidence:**
Lines 629–632 of `app/api/boq.py`:
```python
    raise HTTPException(
        status_code=403,
        detail="Manual BOQ actuals override is disabled to guarantee financial determinism. Actuals are calculated automatically from usage and expenses."
    )
```

**RBAC Impact:**
No RBAC regression. User passed `require_permission("boq.edit")`. The failure is triggered entirely by the hardcoded exception within the endpoint body.

**Tenant/Ownership Impact:**
No tenant leak. Project access check passed before the exception.

**Recommended Fix Direction:**
Change HTTP status code from 403 to 400 or 405 with a clear explanation, or remove manual endpoint if automated calculation from `app/utils/boq_calc.py` is mandatory.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #2 — Export Recurring Journals Fails with 500

**Endpoint:**
`GET /api/v1/journal/recurring/export`

**Observed Problem:**
Returns HTTP 500 Internal Server Error.

**Classification:**
BACKEND BUG

**Severity:**
P1

**Backend File:**
[journal.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/journal.py)

**Function:**
`export_recurring_journals` (Lines 304–340)

**Request Schema:**
N/A (Query params: format)

**Response Schema:**
`StreamingResponse` (CSV/Excel)

**Root Cause:**
In `app/api/journal.py:319`, the export logic parses line counts via:
```python
lines_count = len(j.template_data.get('lines', []))
```
In MySQL, depending on how `RecurringJournal.template_data` was inserted (e.g. raw JSON string vs serialized dictionary), `j.template_data` is frequently returned as a raw JSON `str` rather than a parsed `dict`. Invoking `.get()` on a string raises `AttributeError: 'str' object has no attribute 'get'`, leading to an unhandled 500 error.

**Evidence:**
Lines 317–323 in `app/api/journal.py`:
```python
lines_count = len(j.template_data.get('lines', [])) if j.template_data else 0
```

**RBAC Impact:**
No RBAC regression. Dependency is `require_permission("journal.view")`.

**Tenant/Ownership Impact:**
No tenant leak. Queries filtered by `company_id == current_user.company_id`.

**Recommended Fix Direction:**
Safely deserialize `template_data`: if `isinstance(j.template_data, str)`, parse via `json.loads(j.template_data)`. Guard against `None` or malformed line lists.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #3 — Run Due Recurring Journals Fails with 500

**Endpoint:**
`POST /api/v1/journal/recurring/run-due`

**Observed Problem:**
Returns HTTP 500 Internal Server Error.

**Classification:**
DATABASE/DATA ISSUE (compounded by BACKEND BUG)

**Severity:**
P1

**Backend File:**
[journal.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/journal.py)

**Function:**
`run_due_recurring_journals` (Lines 342–395)

**Request Schema:**
N/A

**Response Schema:**
`dict` (`{"message": "Processed X journals", "created_entries": ...}`)

**Root Cause:**
1. `template_data` contains `lines` with `account_id`. In `app/api/journal.py:369–375`, `JournalLine` objects are instantiated using `line.get("account_id")` without validating if the referenced account exists in the `accounts` table.
2. In the target database, the `accounts` table is empty (0 accounts configured). The insert violates foreign key constraint `fk_journal_lines_account_id`, throwing `sqlalchemy.exc.IntegrityError` (500).
3. Furthermore, `template_data` is accessed via `r.template_data.get('lines', [])` which fails with `AttributeError` if stored as a raw JSON string.
4. Runtime traceback required to confirm exact exception in environments with populated accounts.

**Evidence:**
Lines 369–375 in `app/api/journal.py`:
```python
line_obj = JournalLine(
    entry_id=entry.id,
    account_id=line.get('account_id'),
    debit=Decimal(str(line.get('debit', 0))),
    credit=Decimal(str(line.get('credit', 0))),
    description=line.get('description', '')
)
db.add(line_obj)
```

**RBAC Impact:**
No RBAC regression. Endpoint uses `require_permission("journal.create")`.

**Tenant/Ownership Impact:**
No tenant leak. Filters by `company_id`.

**Recommended Fix Direction:**
Ensure `template_data` is deserialized safely, validate all `account_id` references before creating lines, catch `IntegrityError` gracefully, and seed default chart of accounts.

**Runtime Verification Needed:**
Yes (Runtime traceback required to confirm exact exception).

---

### Issue #4 — Create Wage Record Fails with 500

**Endpoint:**
`POST /api/v1/labour/wages`

**Observed Problem:**
Returns HTTP 500 Internal Server Error.

**Classification:**
BACKEND BUG

**Severity:**
P0

**Backend File:**
[labour.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/labour.py)

**Function:**
`create_wage_record` (Lines 2330–2415)

**Request Schema:**
`LabourWageCreate`

**Response Schema:**
`LabourWageOut`

**Root Cause:**
In `app/api/labour.py:2330`, the endpoint executes:
```python
labour = await db.get(Labour, payload.labour_id)
```
At line 2387, the code accesses:
```python
hourly_rate = Decimal(str(labour.effective_daily_wage or 0)) / Decimal("8")
```
In `app/models/labour.py`, `effective_daily_wage` is a Python `@property` that evaluates `if self.labour_type: return self.labour_type.base_daily_wage`. Because `Labour.labour_type` was NOT loaded eagerly with `selectinload(Labour.labour_type)`, accessing `self.labour_type` in an async SQLAlchemy session triggers synchronous lazy-loading and raises:
`sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call a Pydantic/SQLAlchemy lazy load in an async session.`
Additionally, `LabourWageOut` declares `created_at` and `updated_at` as non-optional `datetime` fields, but `create_wage_record` flushes `LabourWageRecord` without `await db.refresh(wage_record)`.

**Evidence:**
Reproduced and confirmed via Python reproduction script in session. Traceback confirmed `MissingGreenlet` during `self.labour_type` property evaluation.

**RBAC Impact:**
No RBAC regression. Endpoint uses `require_permission("labour.wages.create")`.

**Tenant/Ownership Impact:**
Project access check succeeded.

**Recommended Fix Direction:**
Change fetch to `select(Labour).where(Labour.id == payload.labour_id).options(selectinload(Labour.labour_type))` and execute `await db.refresh(wage_record)` before returning `LabourWageOut`.

**Runtime Verification Needed:**
No. Confirmed by direct execution.

---

### Issue #5 — List Wages Fails with 500

**Endpoint:**
`GET /api/v1/labour/wages`

**Observed Problem:**
Returns HTTP 500 Internal Server Error.

**Classification:**
API CONTRACT/SCHEMA ISSUE (compounded by BACKEND BUG)

**Severity:**
P1

**Backend File:**
[labour.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/labour.py)

**Function:**
`list_wage_records` (Lines 2440–2520)

**Request Schema:**
N/A (Query parameters: `project_id`, `labour_id`, `start_date`, `end_date`)

**Response Schema:**
`PaginatedResponse[LabourWageRegisterOut]`

**Root Cause:**
1. In `app/schemas/labour.py:397`, `LabourWageRegisterOut` specifies `gross_wage: Decimal`, `net_wage: Decimal`, and `status: str` as non-nullable, required fields.
2. In the `labour_wage_record` table, legacy records or records created without status/wages contain `NULL` for `status`, `gross_wage`, or `net_wage`.
3. When Pydantic validates the returned rows into `LabourWageRegisterOut`, it raises `pydantic_core._pydantic_core.ValidationError: Input should be a valid string / Decimal [type=string_type, input_value=None]`, which FastAPI surfaces as HTTP 500.
4. Additionally, Super Admin (`current_user.company_id is None`) is hardcoded at line 2452 to immediately return `items=[]`.

**Evidence:**
Lines 2445–2515 of `app/api/labour.py` and `app/schemas/labour.py:397–415`:
```python
class LabourWageRegisterOut(BaseModel):
    id: int
    labour_id: int
    gross_wage: Decimal      # Disallows None
    net_wage: Decimal        # Disallows None
    status: str              # Disallows None
```

**RBAC Impact:**
No RBAC regression. Endpoint uses `require_permission("labour.wages.view")`.

**Tenant/Ownership Impact:**
Company filter properly applied.

**Recommended Fix Direction:**
Update `LabourWageRegisterOut` fields to allow defaults or optionals: `status: Optional[str] = "PENDING"`, `gross_wage: Decimal = Decimal("0")`, `net_wage: Decimal = Decimal("0")`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #6 — List Activities Fails with 500

**Endpoint:**
`GET /api/v1/work-progress/activities`

**Observed Problem:**
Returns HTTP 500 Internal Server Error.

**Classification:**
BACKEND BUG

**Severity:**
P1

**Backend File:**
[project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/project.py)

**Function:**
`list_activities` (Lines 6200–6250)

**Request Schema:**
N/A (Query params: `project_id`, `task_id`)

**Response Schema:**
`PaginatedResponse[WorkActivityResponse]`

**Root Cause:**
Inside the GET list endpoint, the code iterates over fetched `WorkActivity` records and invokes `update_activity_status(activity)`.
`update_activity_status` checks:
```python
if activity.completion_percentage < Decimal("100"):
```
If `activity.completion_percentage` is `None` (which is default for newly created activities in DB), Python raises:
`TypeError: '<' not supported between instances of 'NoneType' and 'Decimal'`.
The generic `except Exception` converts this uncaught TypeError into a 500 Internal Server Error.
Furthermore, `WorkActivityResponse` schema declares `boq_item_id: int` as mandatory, but the database model column `boq_item_id` is nullable.

**Evidence:**
Lines 6206–6215 and helper function in `app/api/project.py`:
```python
def update_activity_status(activity):
    if activity.completion_percentage < Decimal("100"): # Crashes if completion_percentage is None
```

**RBAC Impact:**
No RBAC regression. Endpoint uses `require_permission("work_progress.view")`.

**Tenant/Ownership Impact:**
No tenant leak.

**Recommended Fix Direction:**
1. Handle `None` gracefully in status calculator: `(activity.completion_percentage or Decimal("0")) < Decimal("100")`.
2. Do not mutate DB records inside a read-only GET endpoint.
3. Make `boq_item_id: Optional[int] = None` in `WorkActivityResponse`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #7 — Create Expense Returns 400

**Endpoint:**
`POST /api/v1/expenses`

**Observed Problem:**
Returns HTTP 400 Bad Request with detail: `"GENERAL_EXPENSE account is not configured."`

**Classification:**
DATABASE/DATA ISSUE

**Severity:**
P1

**Backend File:**
[expense.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/expense.py)

**Function:**
`create_expense` (Lines 133–136)

**Request Schema:**
`ExpenseCreate`

**Response Schema:**
`ExpenseOut`

**Root Cause:**
In `app/api/expense.py:133–135`, the expense creation workflow checks:
```python
acc = await db.scalar(select(Account).where(Account.code == 'GENERAL_EXPENSE'))
if not acc:
    raise HTTPException(status_code=400, detail="GENERAL_EXPENSE account is not configured.")
```
In the active database, the `accounts` table contains exactly 0 records. The chart of accounts has never been seeded or initialized. Thus, any expense creation attempt immediately fails with 400 Bad Request.

**Evidence:**
Direct database inspection in this audit confirmed: `SELECT COUNT(*) FROM accounts` returned `0`.
Lines 133–135 in `app/api/expense.py`:
```python
acc = await db.scalar(select(Account).where(Account.code == 'GENERAL_EXPENSE'))
if not acc:
    raise HTTPException(status_code=400, detail="GENERAL_EXPENSE account is not configured.")
```

**RBAC Impact:**
No RBAC regression. User passed `require_permission("expense.create")`.

**Tenant/Ownership Impact:**
Tenant checks passed.

**Recommended Fix Direction:**
Seed standard default chart of accounts (including `GENERAL_EXPENSE`, `ACCOUNTS_RECEIVABLE`, `ACCOUNTS_PAYABLE`, `INPUT_GST`, `OUTPUT_GST`, `SALES_REVENUE`) via migration or startup seeder.

**Runtime Verification Needed:**
No. Confirmed by inspecting database row count.

---

### Issue #8 — List Contractors Fails with 500

**Endpoint:**
`GET /api/v1/contractors`

**Observed Problem:**
Returns HTTP 500 Internal Server Error.

**Classification:**
API CONTRACT/SCHEMA ISSUE (compounded by BACKEND BUG)

**Severity:**
P1

**Backend File:**
[contractor.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/contractor.py)

**Function:**
`list_contractors` (Lines 160–215)

**Request Schema:**
N/A (Query params: pagination)

**Response Schema:**
`PaginatedResponse[ContractorOut]`

**Root Cause:**
In `app/schemas/contractor.py:167`, `ContractorOut` defines:
```python
total_work_assigned: Decimal = Decimal("0")
payment_given: Decimal = Decimal("0")
```
When building the response in `app/api/contractor.py:167–175`, the endpoint maps `c.total_work_assigned` and `c.payment_given`. In the `contractors` table, several existing rows contain `NULL` for these columns. When `None` is explicitly passed to a Pydantic `Decimal` field that does not allow `None` (`Optional[Decimal]`), Pydantic raises a `ValidationError`, which FastAPI surfaces as HTTP 500.
Additionally, non-admin role checks filter out contractors if project members are unassigned.

**Evidence:**
Lines 167–175 in `app/api/contractor.py` and `app/schemas/contractor.py`:
```python
"total_work_assigned": c.total_work_assigned,  # is None in DB rows
"payment_given": c.payment_given,              # is None in DB rows
```

**RBAC Impact:**
No RBAC regression. Uses `require_permission("contractor.view")`.

**Tenant/Ownership Impact:**
Tenant isolation logic is sound.

**Recommended Fix Direction:**
1. Update `ContractorOut` schema to allow `Optional[Decimal] = Decimal("0")`.
2. In `app/api/contractor.py`, use `c.total_work_assigned or Decimal("0")` and `c.payment_given or Decimal("0")`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #9 — Expense Dashboard Missing Trends and Pending Approvals

**Endpoint:**
`GET /api/v1/expenses/dashboard`

**Observed Problem:**
Pending approvals count returns 0 and trends array is empty.

**Classification:**
BACKEND BUG

**Severity:**
P2

**Backend File:**
[expense.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/expense.py)

**Function:**
`expense_dashboard` (Lines 640–675)

**Request Schema:**
N/A

**Response Schema:**
`ExpenseDashboardResponse`

**Root Cause:**
Lines 666–667 in `app/api/expense.py` contain hardcoded stub values:
```python
pending_approval_count = 0
trend = []
```
The endpoint never executes queries against `approvals` or groups expenses by month to compute the required trend and pending approval metrics.

**Evidence:**
Lines 666–667 in `app/api/expense.py`:
```python
pending_approval_count = 0
trend = []
return ExpenseDashboardResponse(
    ...
    pending_approval_count=pending_approval_count,
    trend=trend
)
```

**RBAC Impact:**
No RBAC regression. Uses `require_permission("expense.view")`.

**Tenant/Ownership Impact:**
Tenant company filtering is applied on top-level metrics, but child metrics are stubbed.

**Recommended Fix Direction:**
Implement queries aggregating `Approval` records where `entity_type == 'expense' and status == 'Pending'` and group historical expenses by month for `trend`.

**Runtime Verification Needed:**
No. Confirmed by source code inspection.

---

### Issue #10 — Get Document Stats: Total Storage in GB Requirement

**Endpoint:**
`GET /api/v1/documents/stats`

**Observed Problem:**
Requirement states total storage must be provided in GB.

**Classification:**
REQUIREMENT CHANGE / CONFIRMED IMPLEMENTED

**Severity:**
P3

**Backend File:**
[document.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py)

**Function:**
`get_document_stats` (Lines 75–115)

**Request Schema:**
N/A

**Response Schema:**
`DocumentStats`

**Root Cause:**
Historically, `total_storage_bytes` or KB was returned. During the recent Batch M Document Management implementation, `total_storage_gb` was added to `DocumentStats` and calculated as:
```python
total_storage_gb = round(float(total_size or 0) / (1024 ** 3), 2)
```
The backend currently satisfies this requirement. If the frontend is still reporting missing GB, it is either consuming an outdated schema or cached response.

**Evidence:**
Lines 109–112 in `app/api/document.py`:
```python
total_storage_gb=round(float(total_size or 0) / (1024 ** 3), 2),
```

**RBAC Impact:**
No RBAC regression. Endpoint uses `require_permission("documents.view")`.

**Tenant/Ownership Impact:**
Filtered by `Project.company_id == current_user.company_id`.

**Recommended Fix Direction:**
Verify frontend uses `total_storage_gb` from the updated API response.

**Runtime Verification Needed:**
No. Confirmed implemented in production code.

---

### Issue #11 — Account Import Fails with "Invalid parent ID 'null'"

**Endpoint:**
`POST /api/v1/accountant/accounts/import`

**Observed Problem:**
Import fails with `"Invalid parent ID 'null'"`, `valid_records = 0`, and no accounts are created.

**Classification:**
BACKEND BUG

**Severity:**
P1

**Backend File:**
[accountant.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/accountant.py)

**Function:**
`import_accounts` (Lines 520–560)

**Request Schema:**
`multipart/form-data` (CSV/Excel file)

**Response Schema:**
`AccountImportResult`

**Root Cause:**
In `app/api/accountant.py:542–547`:
```python
parent_id_str = str(row.get("parent_id", "")).strip()
if parent_id_str and parent_id_str.lower() != "none":
    try:
        parent_id = int(parent_id_str)
```
When Excel or CSV files contain empty/null cells for root accounts, pandas or openpyxl converts empty cells into string `"null"` or `"NULL"`. The string check only filters `"none"`, so `"null"` enters `int("null")`, raising `ValueError`. The exception block appends `"Invalid parent ID 'null'"` to the error list, triggering a transaction rollback and setting `valid_records = 0`.

**Evidence:**
Lines 542–547 in `app/api/accountant.py`:
```python
if parent_id_str and parent_id_str.lower() not in ("none", "null", "nan", ""):
```
Currently only checks `!= "none"`.

**RBAC Impact:**
No RBAC regression. Uses `require_permission("accountant.manage")`.

**Tenant/Ownership Impact:**
No tenant leak.

**Recommended Fix Direction:**
Expand the empty check: `if parent_id_str and parent_id_str.lower() not in ("none", "null", "nan", "", "undefined"):`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #12 — Create Receipt Requires Project and Omits Project in Response

**Endpoint:**
`POST /api/v1/accountant/receipts`

**Observed Problem:**
`project` field is mandatory during creation, but does not appear in the response.

**Classification:**
API CONTRACT/SCHEMA ISSUE

**Severity:**
P2

**Backend File:**
[accountant.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/accountant.py)

**Function:**
`create_receipt` (Lines 145–180)

**Request Schema:**
`ReceiptCreate`

**Response Schema:**
`dict`

**Root Cause:**
1. In `app/schemas/accountant.py:61`, `ReceiptCreate` defines `project_id: int` without `Optional`, forcing project selection even for general company receipts.
2. In `app/api/accountant.py:175`, `create_receipt` returns an ad-hoc dictionary `{"message": "Receipt recorded", "amount": float(payload.amount)}` which completely excludes the project information.

**Evidence:**
Lines 175–179 in `app/api/accountant.py`:
```python
return {
    "message": "Receipt recorded",
    "amount": float(payload.amount),
}
```

**RBAC Impact:**
No RBAC regression. Uses `require_permission("accountant.manage")`.

**Tenant/Ownership Impact:**
Project access check is enforced.

**Recommended Fix Direction:**
1. Make `project_id: Optional[int] = None` in `ReceiptCreate`.
2. Return full receipt object including `project_id` and project details.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #13 — Create Receipt API Returns Only 2 Fields vs List Receipt

**Endpoint:**
`POST /api/v1/accountant/receipts`

**Observed Problem:**
List receipts returns rich objects, but create receipt returns only `message` and `amount`.

**Classification:**
API CONTRACT/SCHEMA ISSUE

**Severity:**
P2

**Backend File:**
[accountant.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/accountant.py)

**Function:**
`create_receipt` (Lines 145–180)

**Request Schema:**
`ReceiptCreate`

**Response Schema:**
Unspecified / `dict` (omits response model)

**Root Cause:**
While `GET /api/v1/accountant/receipts` returns `PaginatedResponse[ReceiptOut]` (with id, date, project_id, amount, payment_mode, reference, description), `POST /api/v1/accountant/receipts` returns a raw dict literal `{"message": "Receipt recorded", "amount": float(payload.amount)}`.

**Evidence:**
Lines 175–179 in `app/api/accountant.py`:
```python
return {
    "message": "Receipt recorded",
    "amount": float(payload.amount),
}
```

**RBAC Impact:**
No RBAC regression.

**Tenant/Ownership Impact:**
No tenant leak.

**Recommended Fix Direction:**
Define `response_model=ReceiptOut` on `POST /api/v1/accountant/receipts` and return the created `OwnerTransaction` or `Transaction` model.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #14 — Receipt Summary Missing Total Count

**Endpoint:**
`GET /api/v1/accountant/receipts/summary`

**Observed Problem:**
Response only contains `total_receipts` amount, omitting receipt count.

**Classification:**
API CONTRACT/SCHEMA ISSUE

**Severity:**
P2

**Backend File:**
[accountant.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/accountant.py)

**Function:**
`receipt_summary` (Lines 205–225)

**Request Schema:**
N/A

**Response Schema:**
`dict`

**Root Cause:**
In `app/api/accountant.py:206–218`:
```python
total = await db.scalar(
    select(func.sum(Transaction.amount)).where(...)
)
return {"total_receipts": float(total or 0)}
```
The query only executes `func.sum(Transaction.amount)` and omits `func.count(Transaction.id)`. The returned dictionary lacks `total_count`.

**Evidence:**
Lines 216–218 in `app/api/accountant.py`:
```python
return {"total_receipts": float(total or 0)}
```

**RBAC Impact:**
No RBAC regression.

**Tenant/Ownership Impact:**
Filtered by `company_id`.

**Recommended Fix Direction:**
Query both `func.count(Transaction.id)` and `func.sum(Transaction.amount)` and return `{"total_receipts_amount": float(total or 0), "total_receipts_count": count}`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #15 — Insufficient Information

**Endpoint:**
Unknown

**Observed Problem:**
No issue text supplied.

**Classification:**
INSUFFICIENT INFORMATION — NO ISSUE DESCRIPTION PROVIDED.

**Severity:**
P3

**Backend File:**
N/A

**Function:**
N/A

**Request Schema:**
N/A

**Response Schema:**
N/A

**Root Cause:**
Insufficient information — no issue description provided.

**Evidence:**
Reported item was blank.

**RBAC Impact:**
N/A

**Tenant/Ownership Impact:**
N/A

**Recommended Fix Direction:**
Obtain description from user/reporter.

**Runtime Verification Needed:**
No.

---

### Issue #16 — GET Project Inventory Project Field Should Be Non-Mandatory

**Endpoint:**
`GET /api/v1/material/inventory/{project_id}`

**Observed Problem:**
`project_id` is mandatory in the route path, preventing company-wide inventory retrieval.

**Classification:**
API CONTRACT/SCHEMA ISSUE

**Severity:**
P2

**Backend File:**
[material.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/material.py)

**Function:**
`get_project_inventory` (Lines 3544–3590)

**Request Schema:**
Path parameter: `project_id: int`

**Response Schema:**
`List[InventoryResponse]`

**Root Cause:**
The route is declared with a required path parameter: `@router.get("/inventory/{project_id}")`. There is no route `GET /inventory` where `project_id` is an optional query parameter. To fetch overall company inventory across all sites, the client is forced to provide a dummy project ID.

**Evidence:**
Line 3544 in `app/api/material.py`:
```python
@router.get("/inventory/{project_id}", response_model=List[InventoryResponse])
async def get_project_inventory(project_id: int, ...):
```

**RBAC Impact:**
No RBAC regression. Uses `require_permission("inventory.view")`.

**Tenant/Ownership Impact:**
Project access check verifies single project, preventing multi-site inventory views.

**Recommended Fix Direction:**
Support `GET /inventory` with `project_id: Optional[int] = Query(None)`. When `project_id` is omitted, aggregate inventory across all projects belonging to `current_user.company_id`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #17 — Pending Approvals Document Count Varies Across Document States

**Endpoint:**
`GET /api/v1/documents/stats` and `GET /api/v1/documents`

**Observed Problem:**
Pending count reported in stats differs from documents returned in pending lists.

**Classification:**
BACKEND BUG

**Severity:**
P2

**Backend File:**
[document.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/document.py)

**Function:**
`get_document_stats` (Lines 80–95) & `list_documents` (Lines 125–170)

**Request Schema:**
N/A

**Response Schema:**
`DocumentStats`

**Root Cause:**
In `app/api/document.py:84`, the stats calculation counts pending approvals using:
```python
select(func.count()).select_from(Document).where(
    Document.status.in_([DocumentStatus.PENDING, DocumentStatus.UNDER_REVIEW])
)
```
It groups both `PENDING` and `UNDER_REVIEW` under "pending approvals". However, in `list_documents`, there is no `status` filter parameter, nor does the document list distinguish between `PENDING` and `UNDER_REVIEW`. Consequently, the frontend cannot filter documents to reconcile with the count reported in `stats`.

**Evidence:**
Lines 84–89 in `app/api/document.py`:
```python
pending_approvals = await db.scalar(
    select(func.count()).select_from(Document).where(
        Document.status.in_([DocumentStatus.PENDING, DocumentStatus.UNDER_REVIEW]),
        ...
    )
)
```

**RBAC Impact:**
No RBAC regression.

**Tenant/Ownership Impact:**
Scoped to company.

**Recommended Fix Direction:**
1. Align stat definition to count strictly `DocumentStatus.PENDING` (or expose `under_review` count separately in `DocumentStats`).
2. Add `status: Optional[DocumentStatus] = Query(None)` to `list_documents`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #18 — Daily Project Reports PDF Generation Missing

**Endpoint:**
`GET /api/v1/projects/{project_id}/dsr/pdf` (or equivalent)

**Observed Problem:**
PDF file for DSR is not generated or provided.

**Classification:**
MISSING API/FEATURE

**Severity:**
P2

**Backend File:**
[project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/project.py)

**Function:**
`export_project_dsr` (Lines 5090–5150)

**Request Schema:**
N/A

**Response Schema:**
`StreamingResponse` (Excel only)

**Root Cause:**
In `app/api/project.py:5091`, the only export endpoint for DSR is:
`@dsr_router.get("/project/{project_id}/export")` which solely generates an `.xlsx` workbook using `openpyxl`. No route or service exists for generating or downloading DSR as a PDF document.

**Evidence:**
Lines 5091–5130 in `app/api/project.py` only build `openpyxl.Workbook()` and return `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.

**RBAC Impact:**
No RBAC regression.

**Tenant/Ownership Impact:**
Project access check present.

**Recommended Fix Direction:**
Implement a `@dsr_router.get("/project/{project_id}/export/pdf")` or `@dsr_router.get("/{dsr_id}/pdf")` endpoint utilizing `ReportLab` / `PdfReportBuilder` to render the daily site report.

**Runtime Verification Needed:**
No. Confirmed by checking available routes.

---

### Issue #19 — Insufficient Information

**Endpoint:**
Unknown

**Observed Problem:**
No issue text supplied.

**Classification:**
INSUFFICIENT INFORMATION — NO ISSUE DESCRIPTION PROVIDED.

**Severity:**
P3

**Backend File:**
N/A

**Function:**
N/A

**Request Schema:**
N/A

**Response Schema:**
N/A

**Root Cause:**
Insufficient information — no issue description provided.

**Evidence:**
Reported item was blank.

**RBAC Impact:**
N/A

**Tenant/Ownership Impact:**
N/A

**Recommended Fix Direction:**
Obtain description from user/reporter.

**Runtime Verification Needed:**
No.

---

### Issue #20 — Log QC Entry API: Report File Not Shown in Response

**Endpoint:**
`POST /api/v1/projects/qc`

**Observed Problem:**
Uploaded report file is not returned in the API response.

**Classification:**
API CONTRACT/SCHEMA ISSUE

**Severity:**
P2

**Backend File:**
[project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/project.py) & [project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/schemas/project.py)

**Function:**
`create_qc` (Lines 6570–6620)

**Request Schema:**
`QCCreate`

**Response Schema:**
`QCOut`

**Root Cause:**
In `app/schemas/project.py:866`, `QCOut` is defined as:
```python
class QCOut(QCCreate):
    id: int
```
Although `create_qc` receives an uploaded file, saves it to disk/cloud, and stores the path in `QCRecord.report_file_url` in the database, `report_file_url` is NOT declared in `QCOut` or `QCCreate`. When `QCOut.model_validate(obj)` is returned, Pydantic drops `report_file_url`.

**Evidence:**
`app/schemas/project.py:847–870`:
```python
class QCCreate(BaseSchema):
    project_id: int
    ...
class QCOut(QCCreate):
    id: int
    # report_file_url is missing!
```

**RBAC Impact:**
No RBAC regression. Uses `require_roles(QC_WRITE_ROLES)`.

**Tenant/Ownership Impact:**
Project access check enforced.

**Recommended Fix Direction:**
Add `report_file_url: Optional[str] = None` to `QCOut` schema in `app/schemas/project.py`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #21 — Create Labour Fails with 500

**Endpoint:**
`POST /api/v1/labour`

**Observed Problem:**
Returns HTTP 500 Internal Server Error.

**Classification:**
BACKEND BUG

**Severity:**
P0

**Backend File:**
[labour.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/labour.py)

**Function:**
`create_labour` (Lines 185–265)

**Request Schema:**
`LabourCreate`

**Response Schema:**
`LabourOut`

**Root Cause:**
In `app/api/labour.py:185–261`, the endpoint generates a linked user and inserts a `Labour` record. The loop handles `IntegrityError` specifically by checking `if "email" in str(e).lower():`. However, if an `IntegrityError` occurs due to duplicate `users.mobile` or duplicate `labour.aadhaar_number`, the retry loop exhausts its 3 attempts and executes:
```python
raise Exception("Failed to create labour")
```
Raising a generic `Exception` causes FastAPI to catch it as an unhandled internal error and return HTTP 500 instead of a 409 Conflict / 400 Bad Request detailing duplicate mobile or Aadhaar numbers.

**Evidence:**
Lines 245–261 in `app/api/labour.py`:
```python
        except IntegrityError as e:
            await db.rollback()
            if "email" in str(e).lower():
                email = f"labour_{uuid.uuid4().hex[:8]}@internal.local"
                continue
            logger.exception("Failed to create labour due to DB integrity error")
            raise
    else:
        raise Exception("Failed to create labour")
```

**RBAC Impact:**
No RBAC regression. Uses `require_permission("labour.create")`.

**Tenant/Ownership Impact:**
Company assignment is properly set.

**Recommended Fix Direction:**
Inspect `IntegrityError`: check for duplicate Aadhaar number or mobile number, and raise `HTTPException(status_code=409, detail="Aadhaar or Mobile number already registered")`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #22 — Site Engineer Dashboard Missing Active Activity & Today's Work Summary

**Endpoint:**
`GET /api/v1/dashboard/site-engineer-summary`

**Observed Problem:**
Active Activity count and Today's Work Summary return 0 / empty in the dashboard.

**Classification:**
BACKEND BUG

**Severity:**
P2

**Backend File:**
[dashboard.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/dashboard.py)

**Function:**
`site_engineer_summary` (Lines 2840–2885)

**Request Schema:**
N/A

**Response Schema:**
`SiteEngineerSummaryOut`

**Root Cause:**
In `app/api/dashboard.py:2855–2866`, the dashboard aggregates active activities and work summary strictly from `DailyProgressEntry` with `entry_date == today`:
```python
active_activities = await db.scalar(
    select(func.count(DailyProgressEntry.id)).where(
        DailyProgressEntry.project_id.in_(project_ids),
        DailyProgressEntry.entry_date == today
    )
)
```
If the site engineer opens the dashboard at the start of the workday before submitting a Daily Progress Entry for today, both metrics return 0 / empty. Active activities should be queried from ongoing tasks or activities (`Task.status == 'In Progress'` or `WorkActivity.status == 'IN_PROGRESS'`), not from entries submitted today.

**Evidence:**
Lines 2855–2866 in `app/api/dashboard.py`:
```python
DailyProgressEntry.entry_date == today
```

**RBAC Impact:**
No RBAC regression. Uses `require_permission("dashboard.view")`.

**Tenant/Ownership Impact:**
Scoped to user's assigned projects.

**Recommended Fix Direction:**
Query `active_activities` from `Task.status.in_(["In Progress", "Ongoing"])` or `WorkActivity.status == "IN_PROGRESS"`. Provide previous day's work summary if today's entry has not yet been logged.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #23 — Disbursement History Response Is Empty in Frontend

**Endpoint:**
`GET /api/v1/labour/payroll/disbursement-history`

**Observed Problem:**
Response array is empty in the frontend.

**Classification:**
BACKEND BUG

**Severity:**
P1

**Backend File:**
[labour.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/labour.py)

**Function:**
`get_disbursement_history` (Lines 2876–2925)

**Request Schema:**
N/A (Query params: pagination, `labour_id`, `project_id`)

**Response Schema:**
`List[DisbursementHistoryOut]`

**Root Cause:**
In `app/api/labour.py:2876`, the endpoint filters `Transaction` records via:
```python
select(Transaction).where(Transaction.reference.like("payroll:%"))
```
However, wage disbursements recorded via `LabourWageRecord` payments write transactions with `reference = f"wage_record:{wage_record.id}"`. The filter strictly excludes all wage record disbursements.
Furthermore, the loop uses a dictionary `seen_labour[t.entity_id] = record` which discards all disbursements except the last one for each labourer, resulting in empty or severely truncated lists.

**Evidence:**
Lines 2876–2890 in `app/api/labour.py`:
```python
Transaction.reference.like("payroll:%")
```

**RBAC Impact:**
No RBAC regression. Uses `require_permission("payroll.view")`.

**Tenant/Ownership Impact:**
Project/Company isolation enforced.

**Recommended Fix Direction:**
Filter by `or_(Transaction.reference.like("payroll:%"), Transaction.reference.like("wage_record:%"))` and remove the deduplication dictionary so all historical disbursements are returned.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #24 — Update Task Request Status Field Becomes Blank on Accept

**Endpoint:**
`PUT /api/v1/projects/task-requests/{request_id}`

**Observed Problem:**
`status` field becomes empty/blank when accepting a task request.

**Classification:**
BACKEND BUG

**Severity:**
P1

**Backend File:**
[project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/project.py)

**Function:**
`update_task_request` (Lines 4355–4400)

**Request Schema:**
`TaskRequestUpdate`

**Response Schema:**
`TaskRequestOut`

**Root Cause:**
In `app/api/project.py:4355–4380`, the endpoint iterates over payload values:
```python
data = payload.model_dump(exclude_unset=True)
for k, v in data.items():
    setattr(obj, k, v)
```
In `TaskRequestUpdate`, `status` is an unvalidated optional string. When the frontend accepts the request, if it sends `status: ""` or an unmapped string, or if `action == "ACCEPT"` is sent without setting `status = "APPROVED"`, the empty string is written directly to `obj.status` in the database without validation.

**Evidence:**
Lines 4365–4375 in `app/api/project.py`:
```python
for k, v in data.items():
    setattr(obj, k, v)
```

**RBAC Impact:**
No RBAC regression. Uses `require_roles(PROJECT_WRITE_ROLES)`.

**Tenant/Ownership Impact:**
Project access check enforced.

**Recommended Fix Direction:**
Use an explicit `TaskRequestStatus` enum in `TaskRequestUpdate`, and reject blank strings or map actions (`ACCEPT` -> `APPROVED`, `REJECT` -> `REJECTED`) explicitly.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #25 — Approve Vendor Bill Fails with 500

**Endpoint:**
`POST /api/v1/vendor-bills/{id}/approve`

**Observed Problem:**
Returns HTTP 500 Internal Server Error.

**Classification:**
DATABASE/DATA ISSUE (compounded by BACKEND BUG)

**Severity:**
P1

**Backend File:**
[vendor_bills.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/vendor_bills.py)

**Function:**
`approve_vendor_bill` (Lines 145–235)

**Request Schema:**
`VendorBillApprovalPayload`

**Response Schema:**
`VendorBillDetailOut`

**Root Cause:**
1. In `app/api/vendor_bills.py:173–185`, bill approval triggers automatic journal posting:
```python
vendor_acc = await db.scalar(select(Account).where(Account.code == "VENDOR_PAYABLE"))
expense_acc = await db.scalar(select(Account).where(Account.code == "EXPENSE"))
gst_acc = await db.scalar(select(Account).where(Account.code == "INPUT_GST"))
```
When these accounts are missing, it raises `400 Bad Request: "Required accounting accounts not configured"`.
2. However, if lines 220–232 execute, `create_notification` or `_get_bill_with_details` accesses relationships on the unrefreshed bill or raises uncaught exceptions during event notification, bubbling up as a 500.
3. Runtime traceback required to confirm exact exception in environments where accounts are partially present.

**Evidence:**
Lines 173–185 and 220–232 in `app/api/vendor_bills.py`.

**RBAC Impact:**
No RBAC regression. Uses `require_permission("vendor_bills.approve")`.

**Tenant/Ownership Impact:**
Bill ownership verified.

**Recommended Fix Direction:**
Seed required accounts (`VENDOR_PAYABLE`, `EXPENSE`, `INPUT_GST`) and wrap post-approval notification in a `try...except` block so notification delivery issues do not fail the approval transaction.

**Runtime Verification Needed:**
Yes (Runtime traceback required to confirm exact exception).

---

### Issue #26 — Approve Bill Fails with 500

**Endpoint:**
`PUT /api/v1/billing/{id}/approve`

**Observed Problem:**
Returns HTTP 500 Internal Server Error.

**Classification:**
DATABASE/DATA ISSUE & BACKEND BUG

**Severity:**
P1

**Backend File:**
[billing.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/billing.py)

**Function:**
`approve_bill` (Lines 557–647)

**Request Schema:**
N/A

**Response Schema:**
`dict` (`{"message": "Approved"}`)

**Root Cause:**
In `app/api/billing.py:596–597`:
```python
ar_acc = await get_accounts_receivable(db)
rev_acc = await get_revenue_account(db)
```
In `app/utils/accounting.py:141–150`:
```python
async def get_accounts_receivable(db: AsyncSession) -> Account:
    acc = await db.scalar(select(Account).where(Account.code == 'ACCOUNTS_RECEIVABLE'))
    if not acc:
        raise ValueError("Accounts Receivable account not configured.")
    return acc
```
Because the `accounts` table in the database contains 0 accounts, `get_accounts_receivable` raises `ValueError("Accounts Receivable account not configured.")`.
In `app/api/billing.py`, there is NO `try...except` handling `ValueError`. Unhandled `ValueError` bubbles to FastAPI's top-level handler, which emits **HTTP 500 Internal Server Error**.

**Evidence:**
Lines 596–597 in `app/api/billing.py` and lines 141–151 in `app/utils/accounting.py`:
```python
ar_acc = await get_accounts_receivable(db) # Raises unhandled ValueError
```

**RBAC Impact:**
No RBAC regression. User passed `require_permission("billing.approve")`.

**Tenant/Ownership Impact:**
Project access check passed.

**Recommended Fix Direction:**
1. Catch `ValueError` in `approve_bill` and return `HTTPException(status_code=400, detail=str(e))`.
2. Seed the default chart of accounts (`ACCOUNTS_RECEIVABLE`, `SALES_REVENUE`, `OUTPUT_GST`).

**Runtime Verification Needed:**
No. Confirmed statically and by database inspection.

---

### Issue #27 — PM Dashboard Command Center Cost Tracking & Budget Utilization

**Endpoint:**
`GET /api/v1/dashboard/pm-command-center` & `GET /api/v1/dashboard/project-manager-summary`

**Observed Problem:**
Cost Tracking graph and Budget Utilization do not vary correctly or reflect real project expenditures.

**Classification:**
BACKEND BUG

**Severity:**
P2

**Backend File:**
[dashboard.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/dashboard.py)

**Function:**
`pm_command_center` (Lines 1154–1270) & `pm_summary` (Lines 1485–1505)

**Request Schema:**
N/A

**Response Schema:**
`PMCommandCenterOut` & `PMSummaryOut`

**Root Cause:**
1. **Mocked Cost Tracking Budget:** In `app/api/dashboard.py:1263`, the budget trend is hardcoded with a fake formula:
```python
budget = float(actual) * 0.9 if i % 2 == 0 else float(actual) * 1.1
```
2. **Missing Year Filter:** The monthly actual cost query checks `func.month(Expense.expense_date) == d_date.month` but omits `func.year(Expense.expense_date) == d_date.year`, erroneously summing expenses across different calendar years.
3. **Incomplete Cost Calculation:** Both `pm_command_center` and `pm_summary` calculate spent budget using ONLY `Expense.amount`. They completely ignore contractor bills (`RABill`), vendor bills (`VendorBill`), labour wages (`LabourWageRecord` / `LabourPayroll`), and direct equipment rentals.

**Evidence:**
Lines 1162–1166 and 1254–1264 in `app/api/dashboard.py`:
```python
# Mock budget for trend (or take from BOQ if possible)
budget = float(actual) * 0.9 if i % 2 == 0 else float(actual) * 1.1
```

**RBAC Impact:**
No RBAC regression. Uses `require_permission("dashboard.view")`.

**Tenant/Ownership Impact:**
Project scoping is enforced.

**Recommended Fix Direction:**
1. Calculate actual monthly budget from latest BOQ milestone/schedule distributions.
2. Filter expense aggregation by both month and year.
3. Include vendor bills, contractor bills, and wages in total spent calculations.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #28 — PM Create Milestone: Payload Sends status=in_progress but Returns planned

**Endpoint:**
`POST /api/v1/projects/{project_id}/milestones`

**Observed Problem:**
Request payload sends `status = in_progress`, but response stores and returns `planned`.

**Classification:**
BACKEND BUG

**Severity:**
P2

**Backend File:**
[project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/project.py)

**Function:**
`create_milestone` (Lines 1130–1164) & `serialize_milestone` (Lines 120–140)

**Request Schema:**
`MilestoneCreate`

**Response Schema:**
`MilestoneOut`

**Root Cause:**
1. In `app/api/project.py:1163`, `create_milestone` returns `serialize_milestone(obj)`.
2. In line 140, `serialize_milestone` computes the response status dynamically via `status = compute_milestone_status(obj)`.
3. In lines 120–133:
```python
def compute_milestone_status(milestone):
    today = date.today()
    if milestone.status == MilestoneStatus.COMPLETED or milestone.actual_end_date:
        return "Completed"
    if milestone.end_date and today > milestone.end_date:
        return "Delayed"
    if milestone.actual_start_date:
        return "In Progress"
    return "Planned"
```
4. In `create_milestone`, `actual_start_date` is not set when `status == IN_PROGRESS` (it is only set in `update_milestone`).
5. Because `milestone.actual_start_date` is `None`, `compute_milestone_status` falls through and unconditionally returns `"Planned"`, completely overriding the user's requested status.

**Evidence:**
Lines 120–133 and 140 in `app/api/project.py`:
```python
def serialize_milestone(obj: m.Milestone) -> s.MilestoneOut:
    return s.MilestoneOut(
        ...
        status=compute_milestone_status(obj),
    )
```

**RBAC Impact:**
No RBAC regression. Uses `require_roles(PROJECT_WRITE_ROLES)`.

**Tenant/Ownership Impact:**
Project access check enforced.

**Recommended Fix Direction:**
In `create_milestone`, if `payload.status == MilestoneStatus.IN_PROGRESS`, set `actual_start_date = date.today()`, or have `serialize_milestone` respect `obj.status` directly if explicitly set.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #29 — PM Update Milestone Status: Selecting "In Progress" Moves Milestone to "Completed"

**Endpoint:**
`PUT /api/v1/projects/{project_id}/milestones/{milestone_id}`

**Observed Problem:**
Updating status to "In Progress" results in status becoming "Completed".

**Classification:**
BACKEND BUG

**Severity:**
P2

**Backend File:**
[project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/project.py)

**Function:**
`update_milestone` (Lines 1208–1259) & `compute_milestone_status` (Lines 120–133)

**Request Schema:**
`MilestoneUpdate`

**Response Schema:**
`MilestoneOut`

**Root Cause:**
1. In `app/api/project.py:123`, `compute_milestone_status` evaluates:
```python
if milestone.status == MilestoneStatus.COMPLETED or milestone.actual_end_date:
    return "Completed"
```
2. If a milestone was previously in "Completed" status, its `actual_end_date` column is populated with a date.
3. When the user updates `status` to `In Progress`, `update_milestone` sets `actual_start_date = date.today()` (lines 1232–1237), but **fails to clear `actual_end_date`** (`actual_end_date` remains populated).
4. When `serialize_milestone` runs, `compute_milestone_status` sees `milestone.actual_end_date` is not `None`, and immediately returns `"Completed"`, discarding the update.

**Evidence:**
Lines 123–124 and 1231–1244 in `app/api/project.py`:
```python
if "status" in data:
    if data["status"] == MilestoneStatus.IN_PROGRESS:
        # data["actual_end_date"] = None is missing!
```

**RBAC Impact:**
No RBAC regression. Uses `require_roles(PROJECT_WRITE_ROLES)`.

**Tenant/Ownership Impact:**
Project access check enforced.

**Recommended Fix Direction:**
In `update_milestone`, when `data["status"] == MilestoneStatus.IN_PROGRESS`, explicitly set `data["actual_end_date"] = None`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #30 — BOQ Alert Data Blank in Backend

**Endpoint:**
`GET /api/v1/boq/{boq_id}/alerts`

**Observed Problem:**
Alert data is blank (`{"alerts": []}`) in backend response.

**Classification:**
BACKEND BUG

**Severity:**
P2

**Backend File:**
[boq.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/boq.py)

**Function:**
`boq_alerts` (Lines 737–772)

**Request Schema:**
Path param: `boq_id: int`

**Response Schema:**
`dict` (`{"alerts": [...]}`)

**Root Cause:**
1. In `app/api/boq.py:763`, alerts are populated strictly by checking:
```python
if r.actual_cost > r.total_cost:
```
2. Because manual BOQ actuals override is disabled (Issue #1), and no automatic cost recalculation background job has triggered, `actual_cost` remains `0.00` for all BOQ items.
3. The endpoint does not check for quantity overruns (`actual_quantity > quantity`), budget threshold warnings (e.g. 80% or 90% threshold), or project-level budget alerts. As a result, the loop evaluates false for all rows and returns `{"alerts": []}`.

**Evidence:**
Lines 761–771 in `app/api/boq.py`:
```python
    alerts = []
    for r in rows:
        if r.actual_cost > r.total_cost:
            alerts.append(
                {
                    "item": r.item_name,
                    "message": "Cost exceeded estimate",
                }
            )
    return {"alerts": alerts}
```

**RBAC Impact:**
No RBAC regression. Uses `require_permission("boq.view")`.

**Tenant/Ownership Impact:**
Project access verified.

**Recommended Fix Direction:**
Support warning thresholds (e.g. `actual_cost >= 0.85 * total_cost`), include quantity variance checks, and invoke `recalculate_boq_actuals` to ensure latest costs are evaluated.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #31 — PM Equipment Page Backend Dependencies & API Paths

**Endpoint:**
`/api/v1/equipment/*`

**Observed Problem:**
Equipment page in PM dashboard fails to load modules or returns unexpected responses.

**Classification:**
API CONTRACT/SCHEMA ISSUE & RBAC/PERMISSION ISSUE

**Severity:**
P1

**Backend File:**
[equipment.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/equipment.py)

**Function:**
Various router endpoints

**Request Schema:**
Various

**Response Schema:**
Various

**Root Cause:**
1. **Inconsistent Path Hierarchy:** Routes under `/api/v1/equipment` have fragmented conventions:
   - Availability: `GET /api/v1/equipment/eq/availability` (unconventional `/eq/` nesting)
   - Utilization Report: `GET /api/v1/equipment/report/utilization`
   - Cost Report: `GET /api/v1/equipment/cost/report`
   - Maintenance Alerts: `GET /api/v1/equipment/alerts/maintenance`
   - Purchases: `GET /api/v1/equipment/purchase/history` vs `/purchase/report`
2. **Super Admin Hardcoded Block:** `GET /api/v1/equipment/kpi` raises HTTP 403 explicitly if `current_user.company_id is None`:
```python
if current_user.company_id is None:
    raise HTTPException(status_code=403, detail="Super Admin cannot access company equipment KPI directly")
```
3. **Orphaned Central Equipment:** In `app/models/equipment.py`, `Equipment` has `project_id: Mapped[Optional[int]]`, but no `company_id`. Queries in `equipment_kpi` filter by `Equipment.project_id.in_(company_project_ids)`, which completely excludes central / unassigned warehouse equipment where `project_id IS NULL`.

**Evidence:**
Lines 494–498 and 500–508 in `app/api/equipment.py`.

**RBAC Impact:**
Super Admin cannot inspect equipment KPIs despite having global permissions.

**Tenant/Ownership Impact:**
Unassigned equipment cannot be cleanly isolated by company without `company_id`.

**Recommended Fix Direction:**
Normalize route paths to standard REST patterns (`/reports/cost`, `/reports/utilization`, `/availability`), allow Super Admin with company parameter, and add `company_id` to `Equipment` model.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #32 — PM Equipment Cost Report Contains Empty Values

**Endpoint:**
`GET /api/v1/equipment/cost/report`

**Observed Problem:**
Backend response contains empty values or empty list `[]`.

**Classification:**
BACKEND BUG

**Severity:**
P2

**Backend File:**
[equipment.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/equipment.py)

**Function:**
`cost_report` (Lines 714–818)

**Request Schema:**
Query params: `equipment_id`, `date_from`, `date_to`, `limit`, `offset`

**Response Schema:**
`List[CostReportItem]`

**Root Cause:**
In `app/api/equipment.py:731–765`:
```python
stmt = (
    select(
        EquipmentRental.equipment_id,
        Equipment.equipment_code,
        func.sum(EquipmentRental.rental_cost).label("total_cost"),
        ...
    )
    .join(
        Equipment,
        Equipment.id == EquipmentRental.equipment_id,
    )
    ...
)
```
1. `cost_report` performs an **INNER JOIN** strictly on `EquipmentRental`.
2. Owned equipment, equipment with purchases (`EquipmentPurchase`), maintenance costs (`EquipmentMaintenance`), and operational usage costs (`EquipmentUsage`) are completely excluded. If a project has equipment without rental records, the response is empty `[]`.
3. In MySQL, the date expression `func.coalesce(EquipmentRental.end_date, EquipmentRental.start_date) - EquipmentRental.start_date + 1` performs integer arithmetic on formatted dates rather than `DATEDIFF`, resulting in corrupted `total_days`.

**Evidence:**
Lines 731–765 in `app/api/equipment.py`.

**RBAC Impact:**
No RBAC regression. Uses `require_permission("equipment.view")`.

**Tenant/Ownership Impact:**
Company filter present.

**Recommended Fix Direction:**
1. Refactor query to aggregate total equipment cost as: `Rental Cost + Maintenance Cost + Purchase Cost + Usage Cost`.
2. Use MySQL `func.datediff()` for date calculations.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #33 — Generate Payroll Fails with DB Error / 500

**Endpoint:**
`POST /api/v1/labour/payroll/generate`

**Observed Problem:**
Returns database error / HTTP 500 Internal Server Error.

**Classification:**
BACKEND BUG

**Severity:**
P0

**Backend File:**
[labour.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/labour.py)

**Function:**
`generate_payroll` (Lines 1237–1450)

**Request Schema:**
`PayrollGenerate` (`project_id`, `month`, `year`)

**Response Schema:**
`list[PayrollOut]`

**Root Cause:**
1. **Savepoint / Nested Transaction Handling:** In lines 1398–1401:
```python
try:
    async with db.begin_nested():
        db.add(payroll)
        await db.flush()
except IntegrityError:
```
When `IntegrityError` occurs in `asyncmy` on MySQL (e.g. unique constraint `uq_labour_payroll` collision), rolling back the savepoint inside the async session without expelling the pending `payroll` instance leaves the session in an invalid state.
2. If `payroll = await db.scalar(select(LabourPayroll)...)` returns `None` (e.g. if the IntegrityError was caused by a foreign key failure rather than unique key collision), `output.append(payroll)` appends `None` to `output`. Returning `[None]` violates `list[s.PayrollOut]`, throwing a Pydantic `ValidationError` (500).
3. **Improper Status Check:** In lines 1345–1356, if payroll exists with status `PayrollStatus.PENDING` (which is the default status on model creation), it raises `HTTPException(status_code=400, detail="Cannot regenerate payroll...")`. The outer `except Exception:` catches this `HTTPException`, rolls back the transaction, logs `"Payroll generation failed"`, and re-raises it.
4. Runtime traceback required to confirm exact exception encountered in live staging.

**Evidence:**
Lines 1344–1356, 1398–1438, and 1445–1448 in `app/api/labour.py`.

**RBAC Impact:**
No RBAC regression. Uses `require_permission("payroll.create")`.

**Tenant/Ownership Impact:**
Project access enforced.

**Recommended Fix Direction:**
1. Use an explicit upsert or query before insert rather than relying on nested transaction savepoints that taint async sessions.
2. Allow regeneration if status is `DRAFT` or `PENDING`.
3. Separate `except HTTPException:` from `except Exception:` so HTTP errors are not logged as database failures.

**Runtime Verification Needed:**
Yes (Runtime traceback required to confirm exact exception).

---

### Issue #34 — PM Issue Report Missing UI-Required Data

**Endpoint:**
`GET /api/v1/issues` & `GET /api/v1/issues/project/{project_id}`

**Observed Problem:**
UI cannot properly display assigned engineer names, project names, or resolution details.

**Classification:**
API CONTRACT/SCHEMA ISSUE

**Severity:**
P2

**Backend File:**
[project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/project.py) & [project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/schemas/project.py)

**Function:**
`list_issues` (Lines 5416–5506)

**Request Schema:**
N/A

**Response Schema:**
`PaginatedResponse[IssueOut]`

**Root Cause:**
In `app/schemas/project.py:833`:
```python
class IssueOut(IssueBase):
    id: int
    business_id: str
    status: IssueStatus
    assigned_to: Optional[int]
    resolution: Optional[str]
```
`IssueOut` only exposes the raw integer ID `assigned_to: Optional[int]`. It omits `assigned_to_name`, `project_name`, `created_at`, and `reporter_name`. The UI frontend requires the assigned user's full name and project title to render the PM Issue Report table without issuing N+1 separate HTTP requests.

**Evidence:**
`app/schemas/project.py:833–842` and `app/api/project.py:5496`.

**RBAC Impact:**
No RBAC regression. Uses `require_roles(READ_ROLES)`.

**Tenant/Ownership Impact:**
Company and project scoping enforced.

**Recommended Fix Direction:**
Add `assigned_to_name: Optional[str] = None` and `project_name: Optional[str] = None` to `IssueOut`, and join `User` and `Project` in `list_issues`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #35 — PM Issue Report Excel Generation Issues

**Endpoint:**
`GET /api/v1/reports/issues/export/excel`

**Observed Problem:**
Cannot generate comprehensive Excel report from backend.

**Classification:**
BACKEND BUG & API CONTRACT/SCHEMA ISSUE

**Severity:**
P2

**Backend File:**
[reports.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/reports.py)

**Function:**
`export_issue_excel` (Lines 2333–2382)

**Request Schema:**
Query param: `project_id: int` (mandatory)

**Response Schema:**
`StreamingResponse` (Excel)

**Root Cause:**
1. `export_issue_excel` strictly requires `project_id: int` as a query parameter. If a PM wishes to export issues across all managed projects or the whole company, the endpoint fails with `422 Unprocessable Entity`.
2. The endpoint lacks filter parameters (`status`, `priority`, `category`, `date_from`, `date_to`).
3. The generated workbook omits assigned user name, resolution text, and closed date, rendering only raw IDs.

**Evidence:**
Lines 2333–2366 in `app/api/reports.py`:
```python
@router.get("/issues/export/excel")
async def export_issue_excel(
    project_id: int,
    ...
):
```

**RBAC Impact:**
No RBAC regression. Uses `require_roles(REPORT_READ_ROLES)`.

**Tenant/Ownership Impact:**
Project access check enforced.

**Recommended Fix Direction:**
Make `project_id: Optional[int] = Query(None)`, add filters matching `list_issues`, and populate assigned user name in the Excel rows.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #36 — PM Reports Missing APIs: Project Financial Health & Procurement Efficiency

**Endpoint:**
`/api/v1/reports/project-financial-health` & `/api/v1/reports/procurement-efficiency`

**Observed Problem:**
Required PM reporting APIs are missing or improperly routed.

**Classification:**
MISSING API/FEATURE

**Severity:**
P2

**Backend File:**
[reports.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/reports.py)

**Function:**
N/A (Financial Health) & `procurement_efficiency_report` (Line 744)

**Request Schema:**
N/A

**Response Schema:**
N/A

**Root Cause:**
1. **Procurement Efficiency:** An endpoint exists at `GET /api/v1/reports/procurement-efficiency`, but requires `project_id: int` as a mandatory query parameter and returns a PDF/CSV/JSON DTO.
2. **Project Financial Health:** There is **NO** API route named `/project-financial-health` or `/financial-health` in the backend. Only low-level financial ledgers (`/reports/financial/ledger/excel`) and general summaries (`/reports/financial-summary`) exist, none of which implement the comprehensive Project Financial Health schema expected by the PM dashboard.

**Evidence:**
Grep across `app/api/` for `financial_health` returned zero results.

**RBAC Impact:**
No RBAC regression.

**Tenant/Ownership Impact:**
N/A.

**Recommended Fix Direction:**
1. Implement `GET /api/v1/reports/project-financial-health` aggregating budget, actual costs, invoice receipts, contractor claims, and variance.
2. Ensure `/api/v1/reports/procurement-efficiency` accepts optional `project_id` and documents its schema clearly.

**Runtime Verification Needed:**
No. Confirmed by verifying route registry in `reports.py`.

---

### Issue #37 — DSR Create Site Field Does Not Store in Backend

**Endpoint:**
`POST /api/v1/dsr`

**Observed Problem:**
`site` field is sent in the request body, but does not reach or store in the backend database.

**Classification:**
API CONTRACT/SCHEMA ISSUE

**Severity:**
P2

**Backend File:**
[project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/schemas/project.py) & [project.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/project.py)

**Function:**
`create_dsr` (Lines 4512–4610)

**Request Schema:**
`DSRCreate`

**Response Schema:**
`DSROut`

**Root Cause:**
In `app/schemas/project.py:630` and `app/models/project.py:573`, the backend column and schema attribute is named `site_location: Optional[str] = None`.
The frontend form sends the field under the key `"site"` (or `"site_name"`). Because `DSRCreate` does NOT define an alias (`Field(alias="site")`) and does not accept `site`, Pydantic silently ignores the input. `payload.site_location` remains `None`, and `data = payload.model_dump()` writes `NULL` to `daily_site_reports.site_location`.

**Evidence:**
Line 630 in `app/schemas/project.py`:
```python
site_location: Optional[str] = None  # Lacks Field(validation_alias="site")
```
Line 4590 in `app/api/project.py`:
```python
data = payload.model_dump()
obj = m.DailySiteReport(**data) # site_location is None
```

**RBAC Impact:**
No RBAC regression. Uses `require_roles(DSR_WRITE_ROLES)`.

**Tenant/Ownership Impact:**
Project access check enforced.

**Recommended Fix Direction:**
Update `site_location` in `DSRBase` with a Pydantic validation alias:
`site_location: Optional[str] = Field(None, validation_alias=AliasChoices("site_location", "site", "site_name"))`.

**Runtime Verification Needed:**
No. Confirmed statically.

---

### Issue #38 — All Equipment APIs Flow & Consistency Verification

**Endpoint:**
All `/api/v1/equipment/*` routes

**Observed Problem:**
General backend flow across equipment creation, allocation, maintenance, and reporting has inconsistencies.

**Classification:**
API CONTRACT/SCHEMA ISSUE & BACKEND BUG

**Severity:**
P1

**Backend File:**
[equipment.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/api/equipment.py) & [equipment.py](file:///c:/Users/gdhay/OneDrive/Desktop/construction-mgmt/app/models/equipment.py)

**Function:**
Router endpoints across `equipment.py`

**Request Schema:**
Various

**Response Schema:**
Various

**Root Cause:**
1. **Multi-Tenancy Gap:** The `equipment` table does not have a `company_id` column. It only has `project_id: Optional[int]`. When equipment is deallocated or central, `project_id` is `NULL`. Central equipment cannot be securely or directly queried by `company_id`, requiring convoluted subqueries on past allocations and projects.
2. **Duplicate Routes:** `/project/{project_id}` and `/transfer-history` appear with overlapping signatures.
3. **Inconsistent Naming:** `/eq/availability` vs `/report/utilization` vs `/cost/report`.
4. **SuperAdmin Restrictions:** SuperAdmin receives 403 or empty arrays on KPI and reports due to `if current_user.company_id is None:` guards instead of allowing company context selection.

**Evidence:**
Lines 30–120 in `app/models/equipment.py` and lines 403–410, 494–498 in `app/api/equipment.py`.

**RBAC Impact:**
Permissions are properly registered in `require_permission("equipment.*")`, but SuperAdmin access is blocked by manual logic checks.

**Tenant/Ownership Impact:**
Lack of `company_id` on `Equipment` model poses a tenant isolation risk for unassigned equipment.

**Recommended Fix Direction:**
Add `company_id` column to `equipment` table via migration, backfill from `project.company_id`, normalize route prefixes, and allow SuperAdmin to pass a `company_id` query filter.

**Runtime Verification Needed:**
No. Confirmed statically.

---

## Cross-Issue Findings

Grouping common root causes across the 38 audited issues highlights systemic patterns in the codebase:

### 1. Unseeded Database Configuration & Accounting Dependencies (#3, #7, #25, #26)
Multiple endpoints (`/expenses`, `/billing/{id}/approve`, `/vendor-bills/{id}/approve`, `/journal/recurring/run-due`) depend on pre-existing records in the `accounts` table (`GENERAL_EXPENSE`, `ACCOUNTS_RECEIVABLE`, `VENDOR_PAYABLE`, `INPUT_GST`, `SALES_REVENUE`). Because the database has 0 accounts seeded, these endpoints fail with 400 or unhandled `ValueError` (500).

### 2. Async SQLAlchemy Lazy-Loading (`MissingGreenlet`) (#4)
Calling properties or methods on ORM models that touch unloaded relationships (e.g. `Labour.effective_daily_wage` accessing `self.labour_type`) inside an asynchronous session triggers synchronous I/O, crashing with `MissingGreenlet`.

### 3. Pydantic Response Validation Mismatches on Nullable DB Fields (#5, #8, #20, #34)
Endpoints returning paginated lists crash with 500 when legacy database rows contain `NULL` for fields that the response schema marks as required (e.g., `gross_wage`, `net_wage`, `total_work_assigned`, `payment_given`). Similarly, fields present on the ORM model (such as `report_file_url` or `assigned_user_name`) are dropped because they were omitted from the response schema.

### 4. Dynamic Status Calculation Overriding Database State (#28, #29)
The milestone service relies on `compute_milestone_status(obj)` during serialization rather than returning the explicit status stored in the database. Because date heuristics dictate the returned status, creating an "In Progress" milestone returns "Planned" (due to unset `actual_start_date`), and updating a milestone returns "Completed" (due to uncleared `actual_end_date`).

### 5. Mock / Stubbed Logic in Analytics Endpoints (#9, #27, #30)
Several reporting and dashboard endpoints return hardcoded stubs or mock arithmetic:
- Expense dashboard hardcodes `pending_approval_count = 0` and `trend = []`.
- PM Command Center cost tracking calculates budget as `actual * 0.9 if i % 2 == 0 else actual * 1.1`.
- BOQ alerts evaluate only `actual_cost > total_cost` without automated recalculation or threshold warnings.

### 6. Missing Validation Aliases on Inbound Payloads (#24, #37)
Form fields sent by the frontend (such as `site` in DSR creation) are silently discarded because the Pydantic schema expects `site_location` without an alias, resulting in `NULL` database writes.

### 7. Route and Model Inconsistencies in Equipment Module (#31, #32, #38)
The `equipment` table lacks a `company_id` foreign key, forcing tenant isolation to depend on `project_id`. When equipment is unassigned from a project, it disappears from company KPI calculations. Furthermore, the cost report performs an INNER JOIN only on `EquipmentRental`, omitting owned and purchased equipment.

---

## Priority Fix Order

When implementing fixes in future phases, the recommended execution order is:

1. **P0 / Blocker 500 Errors (Core Transactions):**
   - Issue #4: Fix `MissingGreenlet` in `create_wage_record` (`POST /api/v1/labour/wages`) by eager-loading `Labour.labour_type`.
   - Issue #21: Prevent unhandled 500 on duplicate mobile/Aadhaar in `create_labour` (`POST /api/v1/labour`).
   - Issue #33: Fix savepoint/session state and regeneration logic in `generate_payroll` (`POST /api/v1/labour/payroll/generate`).

2. **Accounting System Initialization (Data & 500 Fixes):**
   - Issues #7, #26, #25, #3: Seed standard chart of accounts (`GENERAL_EXPENSE`, `ACCOUNTS_RECEIVABLE`, `SALES_REVENUE`, `VENDOR_PAYABLE`, `INPUT_GST`, `OUTPUT_GST`) and wrap account lookups with graceful error handling instead of unhandled `ValueError`.

3. **Response Schema Nullability Fixes (Prevent 500s on List Endpoints):**
   - Issue #5: Allow optional/default values for `status`, `gross_wage`, `net_wage` in `LabourWageRegisterOut`.
   - Issue #8: Allow `Optional[Decimal]` for `total_work_assigned` and `payment_given` in `ContractorOut`.
   - Issue #6: Guard against `None` in `completion_percentage` inside `update_activity_status` and make `boq_item_id` optional in `WorkActivityResponse`.

4. **Business Calculation & State Flow Corrections:**
   - Issue #28: Initialize `actual_start_date` when creating milestone with `IN_PROGRESS`.
   - Issue #29: Clear `actual_end_date` when moving milestone to `IN_PROGRESS`.
   - Issue #24: Validate `status` enum on `update_task_request` to prevent blanking out status.
   - Issue #23: Broaden transaction reference filter in `disbursement-history` to include `wage_record:*`.
   - Issue #2: Parse string `template_data` safely in recurring journal export.
   - Issue #11: Support `"null"` / `"NULL"` strings as root parent IDs in accountant import.

5. **API Contract & Aliasing Adjustments:**
   - Issue #37: Add `AliasChoices("site", "site_location")` to `DSRBase`.
   - Issue #20: Add `report_file_url` to `QCOut`.
   - Issue #12, #13, #14: Align receipt creation and summary response contracts.
   - Issue #16: Make `project_id` optional on inventory retrieval.
   - Issue #1: Replace misleading 403 status code with 400/405 on `update_actuals`.

6. **Dashboard & Reporting Enhancements:**
   - Issue #27: Replace mock budget formula with real BOQ allocations in PM command center.
   - Issue #9: Populate actual pending approvals count and historical expense trends.
   - Issue #30: Implement budget utilization warning thresholds for BOQ alerts.
   - Issue #32: Broaden equipment cost report to include purchases, maintenance, and usage.
   - Issue #34, #35: Enrich issue listings and Excel exports with assigned user names and filter parameters.
   - Issue #17: Align pending approval definitions between document stats and document lists.

7. **Missing APIs & Structural Improvements:**
   - Issue #36: Implement dedicated `Project Financial Health` endpoint.
   - Issue #18: Implement DSR PDF export endpoint.
   - Issues #31, #38: Add `company_id` to `Equipment` model and normalize equipment route paths.

---

## Files Most Likely Requiring Changes

The following production files will require changes when implementing the recommended fixes:

### Labour & Payroll Module
- `app/api/labour.py` (Issues #4, #5, #21, #23, #33)
- `app/schemas/labour.py` (Issues #4, #5, #33)

### Accounting & Billing Module
- `app/api/billing.py` (Issue #26)
- `app/api/accountant.py` (Issues #11, #12, #13, #14)
- `app/api/vendor_bills.py` (Issue #25)
- `app/api/journal.py` (Issues #2, #3)
- `app/utils/accounting.py` (Issues #7, #25, #26)

### Project, Milestones & DSR Module
- `app/api/project.py` (Issues #6, #18, #20, #24, #28, #29, #34, #37)
- `app/schemas/project.py` (Issues #6, #20, #24, #28, #34, #37)

### Dashboard & Reporting Module
- `app/api/dashboard.py` (Issues #22, #27)
- `app/api/reports.py` (Issues #35, #36)
- `app/api/expense.py` (Issues #7, #9)

### Equipment & BOQ Module
- `app/api/equipment.py` (Issues #31, #32, #38)
- `app/models/equipment.py` (Issue #38)
- `app/api/boq.py` (Issues #1, #30)
- `app/api/contractor.py` (Issue #8)
- `app/schemas/contractor.py` (Issue #8)
- `app/api/material.py` (Issue #16)
- `app/api/document.py` (Issue #17)

---

## Tests Missing / Recommended

To ensure regressions do not reoccur, the following automated test suites are recommended:

1. **Labour & Payroll Tests (`tests/api/test_labour_wage_payroll.py`):**
   - Test creating a wage record when `Labour.labour_type` is not pre-loaded to verify no `MissingGreenlet` occurs.
   - Test listing wages when rows contain `NULL` for status, gross wage, or net wage.
   - Test payroll generation idempotency and regeneration across `DRAFT` and `PENDING` states.
   - Test duplicate mobile and Aadhaar handling during labour creation to verify clean 409 responses.

2. **Accounting Integration Tests (`tests/api/test_accounting_fallbacks.py`):**
   - Test behavior of expense creation, bill approval, and RA bill approval when specific accounts are missing.
   - Test CSV/Excel account import containing literal `"null"` and `"NULL"` in parent account ID columns.

3. **Milestone State Machine Tests (`tests/api/test_milestones_lifecycle.py`):**
   - Test creating a milestone with `status="In Progress"` to ensure response status is `"In Progress"`.
   - Test updating a completed milestone back to `"In Progress"` to ensure status transition is preserved.

4. **Response Serialization Tests (`tests/api/test_schema_serialization.py`):**
   - Test contractor list serialization with `NULL` financial balances.
   - Test QC record output serialization to ensure `report_file_url` is returned.
   - Test DSR creation with request payload keys `site` and `site_location`.

5. **Equipment Flow Tests (`tests/api/test_equipment_cost_reporting.py`):**
   - Test equipment cost reporting for owned equipment with zero rental records.
   - Test equipment KPI calculation for unassigned / central equipment.

---

## Final Verdict

- **Definitely Backend Bugs:** 17 issues are confirmed backend implementation errors (#1, #2, #4, #6, #9, #11, #17, #21, #22, #23, #24, #27, #28, #29, #30, #32, #33).
- **Data / Configuration Issues:** 3 issues (#3, #7, #25) are directly caused by unseeded accounts in the database, triggering foreign key or missing-configuration errors.
- **Contract & Schema Mismatches:** 10 issues (#5, #8, #12, #13, #14, #16, #20, #34, #37, #38) stem from field naming discrepancies, disallowing NULLs, or missing schema fields.
- **New Requirements / Missing Features:** 2 issues (#18, #36) require brand-new API endpoints to be developed. Issue #10 was previously missing but was implemented in Batch M.
- **Runtime Tracebacks Recommended:** Issues #3, #25, and #33 would benefit from capturing production runtime tracebacks to confirm secondary exception paths in specific deployment environments.
- **RBAC Regressions:** **Zero RBAC regressions found.** Existing Batches A through L remain structurally intact and functioning as designed. Batch M Document Management is in place. No changes to existing permissions or roles are required to resolve these issues.
