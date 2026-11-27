# 02 - Bulk Material Upload: Technical Design & Architecture

## 1. Architectural Overview
The bulk material upload feature is designed as an isolated, production-safe, additive subsystem integrated into the existing FastAPI backend.

```
Client (.xlsx / .csv)
       │
       ├──────────────► GET /api/v1/materials/bulk-upload/template
       │                 └─ Returns standardized template with data validation and guides
       │
       ├──────────────► POST /api/v1/materials/bulk-upload/preview
       │                 ├─ File format & size validation (max 5MB, max 500 rows)
       │                 ├─ Header parsing & alias normalization
       │                 ├─ Batch resolution of foreign references (Project, Supplier, Master)
       │                 ├─ Full row-level business validation (Decimal precision)
       │                 ├─ Intra-file duplicate check & Database duplicate check
       │                 ├─ Read-only: zero database writes
       │                 └─ Caches valid items & issues single-use 'import_token' (15 min TTL)
       │
       ├──────────────► POST /api/v1/materials/bulk-upload/commit
       │                 ├─ Validates 'import_token' ownership & consumes token (replay defense)
       │                 ├─ Re-checks database duplicates under transaction
       │                 ├─ Allocates collision-safe sequential business codes ('MAT-XXX')
       │                 ├─ Ingests Material, MaterialTransaction, MaterialLedger
       │                 ├─ Atomically commits or rolls back entirely
       │                 └─ Bumps Redis materials cache version
       │
       └──────────────► POST /api/v1/materials/bulk-upload
                         └─ Direct atomic upload-and-commit in a single API call
```

---

## 2. API Specifications

### Endpoint 1: Download Template
* **URL:** `GET /api/v1/materials/bulk-upload/template`
* **Query Params:** `format` (`xlsx` [default] or `csv`)
* **Authorization:** `materials.upload`
* **Response:** File attachment (`material_bulk_upload_template.xlsx` or `.csv`).

### Endpoint 2: Preview & Validation
* **URL:** `POST /api/v1/materials/bulk-upload/preview`
* **Content-Type:** `multipart/form-data`
* **Form Fields:** `file` (`UploadFile`), optional `project_id` (`int`)
* **Authorization:** `materials.upload`
* **Response Model:** `BulkMaterialPreviewResponse`
```json
{
  "total_rows": 2,
  "valid_rows": 2,
  "error_rows": 0,
  "can_commit": true,
  "import_token": "mat_imp_9f8d1c7e2b4a...",
  "errors": [],
  "preview_data": [
    {
      "row_number": 2,
      "project_id": 1,
      "project_name": "Skyline Heights",
      "material_master_id": 101,
      "material_name": "Cement 53 Grade",
      "unit_name": "Bags",
      "category": "Cement",
      "supplier_id": 201,
      "supplier_name": "Ambuja Suppliers",
      "purchase_rate": 380.00,
      "rate_type": "FIXED",
      "quantity_purchased": 100.000,
      "payment_given": 15000.00,
      "minimum_stock_level": 10.000,
      "total_amount": 38000.00,
      "status": "VALID",
      "errors": []
    }
  ]
}
```

### Endpoint 3: Confirm & Commit
* **URL:** `POST /api/v1/materials/bulk-upload/commit`
* **Content-Type:** `application/json`
* **Payload:**
```json
{
  "import_token": "mat_imp_9f8d1c7e2b4a..."
}
```
* **Authorization:** `materials.upload`
* **Status Code:** `201 Created`
* **Response Model:** `BulkMaterialCommitResponse`
```json
{
  "imported_count": 2,
  "materials": [
    {
      "id": 1052,
      "material_code": "MAT312",
      "material_name": "Cement 53 Grade",
      "project_id": 1,
      "supplier_id": 201,
      "material_master_id": 101,
      "purchase_rate": 380.00,
      "quantity_purchased": 100.000,
      "total_amount": 38000.00,
      "payment_given": 15000.00,
      "remaining_stock": 100.000
    }
  ],
  "message": "Successfully imported 2 materials."
}
```

### Endpoint 4: Direct Atomic Upload
* **URL:** `POST /api/v1/materials/bulk-upload`
* **Content-Type:** `multipart/form-data`
* **Form Fields:** `file` (`UploadFile`), optional `project_id` (`int`)
* **Authorization:** `materials.upload`
* **Status Code:** `201 Created` (or `400 Bad Request` with row-by-row error details)

---

## 3. Security, Token Lifecycle & Replay Defense

1. **Client Isolation:**
   * The client never passes raw preview data to the commit endpoint.
   * Preview caches validated rows in Redis (with in-memory fallback) under key `bulk_upload:materials:{token}` with a 15-minute TTL.
   * Cached token payload binds to `user_id` and `company_id`.
2. **Single-Use Replay Protection:**
   * When commit is invoked, the token is popped immediately before writing to the database. Subsequent requests with the same token are immediately rejected with 400 Bad Request.
3. **Project & Tenant Access Validation:**
   * Project access is verified using `assert_project_access(db, project_id, current_user)` and `current_user.allowed_projects` for every row.
   * Suppliers and Material Masters are validated to match `current_user.company_id`.

---

## 4. Database Transaction & Rollback Strategy

1. **All-or-Nothing Atomic Guarantee:**
   * Direct upload and commit operations use a single SQLAlchemy `AsyncSession` transaction.
   * If any row fails validation or database insertion, `await db.rollback()` is executed.
   * Zero records, transactions, or ledger entries remain in the database upon error.
2. **Sequential Business ID Allocation:**
   * Business IDs are allocated using `MAT` prefix with sequential numeric formatting (`MAT-XXX`).
   * Collision check checks existing codes in the active transaction before assignment.
3. **Cache Invalidation:**
   * Cache version invalidation (`bump_cache_version`) is executed **only** after successful transaction commit.
