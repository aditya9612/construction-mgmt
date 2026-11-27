# 03 - Bulk Material Upload: Implementation Details

## 1. Summary of Changes

To keep the application modular, maintainable, and backward-compatible:
* Created a dedicated service module: `app/services/bulk_material_service.py`.
* Added new Pydantic request/response schemas to `app/schemas/material.py`.
* Integrated 4 additive routes into `app/api/material.py` mounted cleanly on the existing `/materials` router.
* Created a comprehensive automated test suite in `tests/api/test_bulk_material_upload.py`.
* Preserved 100% of existing single-material creation behavior, endpoints, contracts, and schema definitions.
* Zero database schema modifications or migrations were introduced.

---

## 2. Inventory of Created & Modified Files

### Files Created
1. `app/services/bulk_material_service.py` (New dedicated service containing template builder, parser, validation engine, and commit executor).
2. `tests/api/test_bulk_material_upload.py` (New automated test suite containing 15 test scenarios).
3. `docs/bulk-material-upload/01_BULK_MATERIAL_UPLOAD_AUDIT.md`
4. `docs/bulk-material-upload/02_BULK_MATERIAL_UPLOAD_DESIGN.md`
5. `docs/bulk-material-upload/03_BULK_MATERIAL_UPLOAD_IMPLEMENTATION.md`
6. `docs/bulk-material-upload/04_BULK_MATERIAL_UPLOAD_TEST_REPORT.md`
7. `docs/bulk-material-upload/05_BULK_MATERIAL_UPLOAD_OPERATIONS.md`
8. `docs/bulk-material-upload/06_BULK_MATERIAL_UPLOAD_FINAL_VERIFICATION.md`

### Files Modified
1. `app/schemas/material.py` (Added `BulkMaterialError`, `BulkMaterialRowPreview`, `BulkMaterialPreviewResponse`, `BulkMaterialCommitRequest`, `BulkMaterialImportedItem`, and `BulkMaterialCommitResponse`).
2. `app/api/material.py` (Added routes for template download, preview, commit, and direct bulk upload).

---

## 3. Detailed Component Breakdown

### A. Template Generator (`generate_material_upload_template`)
* Location: `app/services/bulk_material_service.py`
* Supports `.xlsx` with two styled worksheets:
  1. `Materials`: Formatted table with headers, sample data rows, column widths, and cell borders.
  2. `Instructions`: Detailed field definitions, mandatory flags, allowed data types, and business constraints.
* Supports `.csv` with standard comma-separated headers and sample rows.

### B. File Parser (`parse_material_file`)
* Location: `app/services/bulk_material_service.py`
* Enforces file size (`MAX_FILE_SIZE = 5MB`) and row limit (`MAX_ROWS = 500`).
* Normalizes headers using a canonical dictionary mapping case-insensitive aliases.
* Rejects duplicate headers, missing required columns, and unsupported extensions (`.xlsm`, `.exe`, etc.).
* Uses `openpyxl.load_workbook(data_only=True)` to safely ignore formulas and macros.
* Handles CSV encoding with UTF-8, UTF-8-sig (BOM), and Latin-1 fallback.

### C. Validation Engine (`validate_bulk_material_rows`)
* Location: `app/services/bulk_material_service.py`
* Batch pre-loads `Project`, `MaterialMaster`, `Unit`, and `Supplier` records to prevent N+1 queries.
* Pre-checks existing `Material` combinations `(project_id, material_master_id, supplier_id)`.
* Verifies intra-file duplicate rows.
* Enforces project access, user allowed projects, company tenant isolation, active states, and non-deleted flags.
* Validates numeric bounds using `Decimal`: `purchase_rate > 0`, `quantity_purchased >= 0`, `payment_given >= 0`, `payment_given <= total_amount`, `minimum_stock_level >= 0`.
* Returns detailed, structured error objects including exact row numbers, column names, values, and messages.

### D. Token Store & Replay Protection
* Location: `app/services/bulk_material_service.py`
* Functions: `store_import_token` and `retrieve_and_consume_import_token`.
* Uses Redis (`bulk_upload:materials:{token}`) with a 15-minute TTL.
* Automatically falls back to an in-memory TTL dictionary for environments where Redis is disabled or unconfigured.
* Consumes (deletes) the token immediately upon retrieval to eliminate replay vulnerabilities.

### E. Atomic Database Commit Engine (`execute_bulk_material_commit`)
* Location: `app/services/bulk_material_service.py`
* Re-checks uniqueness against database in active transaction lock.
* Allocates collision-safe sequential business codes (`MAT-XXX`).
* Instantiates `Material` with auto-populated fields from `MaterialMaster`.
* Automatically creates initial `MaterialTransaction` and `MaterialLedger` entries with `reference_id = f"INIT-{uuid}"` whenever `quantity_purchased > 0`.
* Commits the entire session in one atomic operation.
* Invalidates the Redis materials cache version on commit success.
* Executes full session rollback on any exception.
