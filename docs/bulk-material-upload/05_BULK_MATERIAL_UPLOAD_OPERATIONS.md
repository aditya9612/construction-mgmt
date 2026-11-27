# 05 - Bulk Material Upload: Operations & User Guide

## 1. Operating Instructions

### Step 1: Download Standard Template
Retrieve the official upload template formatted in either `.xlsx` or `.csv`:
```bash
# Download Excel template (recommended)
curl -X GET "http://localhost:8000/api/v1/materials/bulk-upload/template?format=xlsx" \
     -H "Authorization: Bearer <TOKEN>" \
     --output material_template.xlsx

# Download CSV template
curl -X GET "http://localhost:8000/api/v1/materials/bulk-upload/template?format=csv" \
     -H "Authorization: Bearer <TOKEN>" \
     --output material_template.csv
```

### Step 2: Prepare Spreadsheet Data
Open the template in Excel or any spreadsheet editor. Ensure the following rules are adhered to:

| Column Name | Status | Type | Valid Values / Constraints |
| :--- | :--- | :--- | :--- |
| **Project ID** | Mandatory* | Integer | Must be an active project ID assigned to your account. (Can be omitted if supplied in form parameter). |
| **Material Master ID** | Mandatory | Integer | Must match an existing, active material master item in Master Data. |
| **Supplier ID** | Mandatory | Integer | Must match an existing, non-deleted supplier. |
| **Purchase Rate** | Mandatory | Decimal | Must be greater than 0 (e.g., `450.00`). |
| **Rate Type** | Mandatory | Text | Allowed: `FIXED`, `PER_UNIT`, `PER_KG`, `PER_TON`, `PER_BAG` (default: `FIXED`). |
| **Quantity Purchased** | Optional | Decimal | Initial purchase quantity (default: `0.000`). |
| **Payment Given** | Optional | Decimal | Cannot exceed `Quantity Purchased * Purchase Rate` (default: `0.00`). |
| **Minimum Stock Level** | Optional | Decimal | Threshold for low stock alerts (default: `0.000`). |

*Duplicate Rule:* The combination of `(Project ID, Material Master ID, Supplier ID)` must be unique both within the spreadsheet and across the database.

---

### Step 3: Preview and Validate Upload (Recommended)
Before committing changes to the database, preview the file to inspect validation results:
```bash
curl -X POST "http://localhost:8000/api/v1/materials/bulk-upload/preview" \
     -H "Authorization: Bearer <TOKEN>" \
     -F "file=@materials.xlsx"
```

**Successful Preview Response:**
```json
{
  "total_rows": 2,
  "valid_rows": 2,
  "error_rows": 0,
  "can_commit": true,
  "import_token": "mat_imp_4a1c89f2e...",
  "errors": [],
  "preview_data": [ ... ]
}
```

If errors are detected, `can_commit` will be `false`, `import_token` will be `null`, and each error will pinpoint the exact row number and invalid field:
```json
{
  "total_rows": 2,
  "valid_rows": 1,
  "error_rows": 1,
  "can_commit": false,
  "import_token": null,
  "errors": [
    {
      "row": 3,
      "column": "Purchase Rate",
      "message": "Purchase Rate must be greater than 0.",
      "value": "-10.00"
    }
  ],
  "preview_data": [ ... ]
}
```

---

### Step 4: Commit the Previewed Batch
Once preview reports `can_commit: true`, pass the generated `import_token` to finalize the import:
```bash
curl -X POST "http://localhost:8000/api/v1/materials/bulk-upload/commit" \
     -H "Authorization: Bearer <TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
       "import_token": "mat_imp_4a1c89f2e..."
     }'
```

---

### Alternative: Direct Atomic Upload
To validate and commit in a single step (ideal for CLI tools or automation):
```bash
curl -X POST "http://localhost:8000/api/v1/materials/bulk-upload" \
     -H "Authorization: Bearer <TOKEN>" \
     -F "file=@materials.xlsx"
```
*Note:* If any single row contains an error, the entire batch is rejected and zero records are inserted.

---

## 2. Limits & Constraints

* **Maximum File Size:** 5 MB (`5 * 1024 * 1024` bytes).
* **Maximum Rows:** 500 rows per single file upload.
* **Token Expiration:** Preview import tokens expire after 15 minutes (900 seconds).
* **Single-Use Tokens:** Each import token is consumed upon first use to protect against replay.
* **Required Permission:** `materials.upload` (also permitted for Administrators with wildcard `*` access).

---

## 3. Troubleshooting Common Errors

| Error Message | Cause | Resolution |
| :--- | :--- | :--- |
| `Unsupported file format '.xyz'` | File is not `.xlsx` or `.csv`. | Save and upload as `.xlsx` or `.csv`. |
| `File exceeds maximum allowed size` | File is larger than 5 MB. | Reduce file size or split into smaller batches. |
| `Duplicate column header detected` | Spreadsheet has duplicate headers. | Ensure every column header name is unique. |
| `Missing required columns` | Required headers are missing. | Use the downloadable template. |
| `Material Master with ID X not found` | The ID does not exist in master catalog. | Check Master Data catalog for the correct ID. |
| `Supplier with ID X not found or deleted` | Supplier ID is non-existent or soft-deleted. | Verify active suppliers under Suppliers module. |
| `Material already exists in Project X` | A material with this project, master, and supplier already exists. | Use purchase or update endpoints instead of re-creating. |
| `Payment Given cannot exceed Total Amount` | Advance payment is higher than purchase rate * quantity. | Correct payment amount to be <= total amount. |
| `Invalid or expired import token` | Token expired (>15 min) or was already committed. | Re-upload and preview the file to obtain a fresh token. |
