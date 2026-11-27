# 07 - MAT Code Concurrency Race Fix Report

## 1. Executive Summary

This report documents the resolution of the **MEDIUM-severity MAT business-code concurrency race condition** identified during the independent audit of the Bulk Material Upload feature.

The previous MAT code generation pattern relied on reading the maximum existing code (`SELECT MAX(material_code)`), parsing its integer suffix, and incrementing it. Under concurrent requests across workers or transactions, simultaneous executions read the identical maximum value and generated colliding `MAT-XXX` codes, triggering database rollbacks and `IntegrityError` exceptions.

A persistent, database-backed atomic sequence generator has been implemented using a dedicated `business_sequences` table in MySQL with row-level locking (`SELECT ... FOR UPDATE`). This guarantees race-condition free, strictly sequential allocation across all concurrent requests, bulk uploads, and background workers while preserving backward compatibility.

---

## 2. Original Race Condition & Root Cause Analysis

### A. The Defect in Prior Implementation
In `app/utils/common.py` (`generate_business_id`) and `app/services/bulk_material_service.py` (`execute_bulk_material_commit`):
```python
# Unsafe read-then-increment pattern
result = await db.execute(
    select(func.max(Material.material_code)).where(
        Material.material_code.like("MAT%")
    )
)
last_code = result.scalar()
...
new_number = last_number + 1
new_id = f"MAT{str(new_number).zfill(3)}"
```

### B. Root Causes
1. **Lack of Concurrency Control:** In standard transaction isolation levels (such as MySQL's `REPEATABLE READ` or `READ COMMITTED`), uncommitted inserts in Transaction A are not visible to Transaction B. If both transactions read `MAX(material_code)` at the same time, both observe the same `last_code`.
2. **String-Sort Ordering Anomaly:** `MAX(material_code)` performs lexicographical string comparisons. For example, `'MAT999'` evaluates as greater than `'MAT1000'`, creating an architectural limit once codes exceed 3 digits.
3. **500-Query Loop Overhead:** In bulk uploads, iterating `SELECT COUNT(*) WHERE material_code = code` ran up to 500 extra database queries per batch upload.

---

## 3. Database Sequence Architecture

### A. Dedicated Sequence Table (`business_sequences`)
A lightweight, dedicated table was created to store persistent high-water marks for business identifiers:
```sql
CREATE TABLE business_sequences (
    sequence_name VARCHAR(50) NOT NULL PRIMARY KEY,
    current_value BIGINT NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX ix_business_sequences_sequence_name (sequence_name)
) ENGINE=InnoDB;
```

### B. Numbering Scope & Existing Data Compatibility
* **Scope:** Material numbering is global across the `materials` table, formatted as `MAT{number:03d}` (e.g. `MAT001`, `MAT374`, `MAT375`).
* **Existing Data Inspection:** The local database contained 456 materials, with standard codes ranging from `MAT001` to `MAT374` along with 49 non-standard or test codes (`MAT-XXXXXX`).
* **Safe Initialization:** The sequence row was initialized to `374` using SQL `REGEXP '^MAT[0-9]+$'`, ensuring that non-standard hyphenated test fixtures do not corrupt the sequence. The first allocated code by the new engine is `MAT375`, preventing collisions with any existing record.

---

## 4. Database Atomicity & Transaction Mechanics

### A. Row-Level Exclusive Locking (`SELECT ... FOR UPDATE`)
When allocating codes in `app/services/sequence_service.py`:
```python
stmt = (
    select(BusinessSequence)
    .where(BusinessSequence.sequence_name == prefix)
    .with_for_update()
)
seq = (await db.execute(stmt)).scalar_one()

start_num = seq.current_value + 1
end_num = seq.current_value + count
seq.current_value = end_num
await db.flush()

return [f"{prefix}{str(n).zfill(padding)}" for n in range(start_num, end_num + 1)]
```

### B. Concurrency Behavior
1. **Multi-Worker Safety:** In MySQL InnoDB, `SELECT ... FOR UPDATE` on the primary key acquires an exclusive row lock (`X-lock`). If Transaction 2 requests codes while Transaction 1 is active, Transaction 2 safely blocks until Transaction 1 completes.
2. **Single Atomic Query for Bulk Uploads:** For a batch of $N$ materials, the generator increments `current_value` by $N$ in a single operation, replacing hundreds of sequential check queries with a single atomic range reservation.
3. **Transaction Rollback:** If a transaction rolls back before commit, the updated sequence number reverts as part of MySQL's transaction rollback, preventing gaps and preserving exact sequential continuity.

---

## 5. Unified Integration Across Material Workflows

The atomic generator was integrated into all confirmed material creation paths:
1. **Single Material Creation (`POST /api/v1/materials`):** Calls `generate_business_id(..., prefix="MAT", column_name="material_code")`, which delegates directly to `allocate_material_code(db)`.
2. **Material Transfer (`POST /api/v1/materials/{id}/transfers`):** When creating destination materials, delegates through `generate_business_id`.
3. **Bulk Material Upload (`POST /api/v1/materials/bulk-upload/commit` and direct):** Calls `allocate_material_codes(db, count=len(valid_items))` to atomically allocate the entire block.

---

## 6. Database Migration Details

* **Migration Script:** `alembic/versions/e3f4a5b6c7d8_create_business_sequences.py`
* **Parent Revision:** `d2e3f4a5b6c7`
* **Upgrade Strategy:**
  1. Inspects existing schema to ensure idempotency.
  2. Creates table `business_sequences` if not present.
  3. Seeds `'MAT'` row with `SELECT IFNULL(MAX(CAST(SUBSTRING(material_code, 4) AS UNSIGNED)), 0) FROM materials WHERE material_code REGEXP '^MAT[0-9]+$'`.
* **Downgrade Strategy:** `op.drop_table("business_sequences")`.
* **Application Status:** Applied to local development database; verified table contains `('MAT', 374)`.

---

## 7. Automated Test Evidence

### A. Dedicated Concurrency Suite (`tests/api/test_mat_sequence_concurrency.py`)
13 comprehensive test cases were created and executed:

| # | Test Name | Validation Purpose | Result |
| :--- | :--- | :--- | :--- |
| 1 | `test_sequential_mat_code_generation` | Confirms sequential incrementing for single-code calls | **PASSED** |
| 2 | `test_batch_allocation_matches_sequential` | Confirms batch range reservation and subsequent sequential calls | **PASSED** |
| 3 | `test_zero_and_negative_count_allocation` | Verifies edge cases (`count <= 0`) return empty list safely | **PASSED** |
| 4 | `test_genuine_concurrent_single_allocations` | 20 simultaneous workers allocating codes in parallel | **PASSED** (20/20 unique) |
| 5 | `test_genuine_concurrent_bulk_allocations` | 5 simultaneous bulk workers allocating 10 codes each (50 codes) | **PASSED** (50/50 unique) |
| 6 | `test_genuine_concurrent_single_and_bulk_mixed` | 6 single workers + 4 bulk workers (batch size 5) interleaved | **PASSED** (26/26 unique) |
| 7 | `test_transaction_rollback_behavior` | Verifies lock release and state consistency on rollback | **PASSED** |
| 8 | `test_database_unique_constraint_enforced` | Confirms MySQL unique constraint on `materials.material_code` | **PASSED** |
| 9 | `test_non_standard_codes_do_not_disrupt_sequence` | Proves hyphenated/test codes do not corrupt sequence | **PASSED** |
| 10 | `test_generate_business_id_delegates_to_atomic_sequence` | Verifies backward compatibility of generic helper function | **PASSED** |
| 11 | `test_simultaneous_initialization_race` | 5 simultaneous workers initializing an unseeded sequence key | **PASSED** (5/5 unique, 1 row) |
| 12 | `test_multi_process_allocation` | 4 separate OS Python processes allocating 5 codes each in parallel | **PASSED** (20/20 unique) |
| 13 | `test_non_mat_prefixes_unaffected` | Non-MAT prefixes (e.g. PRJ) operate through standard path | **PASSED** |

### B. Full Regression Test Run
The entire test suite was executed:
* **Sequence Concurrency Tests:** 13 Passed
* **Bulk Material Upload Tests:** 15 Passed
* **Existing Material Regression Tests:** 47 Passed
* **Total Passing Tests:** **75 / 75 Passed** (61.30 seconds, zero failures)

---

## 8. Remaining Limitations & Operating Notes

1. **Lock Wait Timeout:** Under extreme concurrency, concurrent transactions wait on the `'MAT'` row lock. MySQL's default `innodb_lock_wait_timeout` is 50 seconds, which is more than sufficient for ERP transactions.
2. **Third-Party Direct Database Inserts:** If an administrator inserts a material directly via raw SQL without going through the application API or sequence table, the application's next allocation might collide with the out-of-band record. The database unique constraint protects against silent data corruption, but direct raw SQL insertions without updating `business_sequences` should be avoided.
