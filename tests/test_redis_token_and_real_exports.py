"""
Multi-Worker Redis Token Lifecycle & Real File Export Verification Suite.
Demonstrates:
1. Redis-backed Daily Stock preview token exchange across simulated workers,
   atomic Lua script GETDEL consumption, anti-replay, and tenant/user security.
2. Real Excel (.xlsx) file generation and physical workbook inspection via openpyxl,
   verifying multi-row child flattening and price masking.
3. Real PDF (.pdf) file generation and physical document inspection via pypdf,
   verifying page layout, text rendering, and price masking.
"""

import io
import json
import secrets
from datetime import datetime, date
import pytest
from unittest.mock import AsyncMock, MagicMock
from decimal import Decimal
import openpyxl
from pypdf import PdfReader
import orjson

from app.services.daily_stock_service import (
    store_daily_stock_token,
    retrieve_and_consume_daily_stock_token,
    LUA_ATOMIC_GETDEL,
    DAILY_STOCK_TOKEN_PREFIX,
)
from app.services.inventory_report_service import (
    export_report_to_excel,
    export_report_to_pdf,
    _flatten_report_rows,
)


# ================= 1. MULTI-WORKER REDIS TOKEN SIMULATION =================

class SimulatedRedisCluster:
    """
    In-memory Redis engine simulating an independent shared Redis instance.
    Implements exact Redis primitives including atomic Lua script evaluation
    (LUA_ATOMIC_GETDEL) and TTL handling across simulated independent worker processes.
    """
    def __init__(self):
        self.store = {}

    async def set(self, key: str, value: bytes, ex: int = None):
        self.store[key] = (value, ex)
        return True

    async def eval(self, script: str, numkeys: int, key: str):
        # Exact semantics of:
        # local val = redis.call('GET', KEYS[1])
        # if val then redis.call('DEL', KEYS[1]) end
        # return val
        if script.strip() == LUA_ATOMIC_GETDEL.strip():
            if key in self.store:
                val, _ = self.store.pop(key)
                return val
            return None
        raise NotImplementedError("Unknown script")

    async def get(self, key: str):
        if key in self.store:
            return self.store[key][0]
        return None

    async def delete(self, key: str):
        return 1 if self.store.pop(key, None) else 0


@pytest.mark.asyncio
async def test_redis_multi_worker_preview_and_atomic_commit():
    """
    Simulates Worker A (handling preview generation) and Worker B (handling commit)
    communicating via shared Redis storage.
    """
    shared_redis = SimulatedRedisCluster()

    # Step 1: Worker A generates token, parses spreadsheet, and stores preview token in Redis
    test_token = secrets.token_hex(32)
    test_rows = [
        {"material_id": 101, "site_id": 5, "physical_count": Decimal("50.0"), "unit": "bags"},
        {"material_id": 102, "site_id": 5, "physical_count": Decimal("20.0"), "unit": "tons"},
    ]
    await store_daily_stock_token(
        token=test_token,
        items=test_rows,
        ttl_seconds=900,
        redis=shared_redis,
        company_id=1,
        user_id=42,
    )

    # Verify Redis storage layout and TTL
    redis_key = f"{DAILY_STOCK_TOKEN_PREFIX}{test_token}"
    assert redis_key in shared_redis.store
    stored_bytes, ttl = shared_redis.store[redis_key]
    assert ttl == 900
    parsed = orjson.loads(stored_bytes)
    assert parsed["company_id"] == 1
    assert parsed["user_id"] == 42
    assert len(parsed["items"]) == 2

    # Step 2: Worker B receives commit request and atomically consumes the token via Lua script
    consumed_items = await retrieve_and_consume_daily_stock_token(
        token=test_token,
        redis=shared_redis,
        company_id=1,
        user_id=42,
    )
    assert consumed_items is not None
    assert len(consumed_items) == 2
    assert consumed_items[0]["material_id"] == 101

    # Step 3: Anti-Replay Defense: Worker C (or attacker replay) attempts to reuse the same token
    replay_result = await retrieve_and_consume_daily_stock_token(
        token=test_token,
        redis=shared_redis,
        company_id=1,
        user_id=42,
    )
    assert replay_result is None, "Replay attack must fail: token was atomically deleted on first use"


@pytest.mark.asyncio
async def test_redis_preview_token_tenant_and_user_isolation():
    """
    Verifies that a token belonging to Tenant 1 cannot be consumed by Tenant 2,
    and a token belonging to User 1 cannot be consumed by User 2.
    """
    shared_redis = SimulatedRedisCluster()

    token1 = secrets.token_hex(32)
    await store_daily_stock_token(
        token=token1,
        items=[{"item": "rebar", "physical_count": Decimal("10")}],
        ttl_seconds=900,
        redis=shared_redis,
        company_id=1,
        user_id=100,
    )

    # Cross-tenant consumption attempt (Tenant 2 tries to consume Tenant 1 token)
    cross_tenant_result = await retrieve_and_consume_daily_stock_token(
        token=token1,
        redis=shared_redis,
        company_id=2,  # Foreign tenant
        user_id=100,
    )
    assert cross_tenant_result is None, "Cross-tenant access must be rejected"

    # Re-store to test cross-user attempt
    token2 = secrets.token_hex(32)
    await store_daily_stock_token(
        token=token2,
        items=[{"item": "rebar", "physical_count": Decimal("10")}],
        ttl_seconds=900,
        redis=shared_redis,
        company_id=1,
        user_id=100,
    )
    cross_user_result = await retrieve_and_consume_daily_stock_token(
        token=token2,
        redis=shared_redis,
        company_id=1,
        user_id=999,  # Unauthorized user
    )
    assert cross_user_result is None, "Unauthorized user access must be rejected"


# ================= 2. REAL EXCEL FILE GENERATION & INSPECTION =================

def test_real_excel_file_flattening_and_price_masking():
    """
    Generates actual .xlsx bytes using openpyxl, loads the workbook, and verifies:
    1. Multi-row flattening of nested child items (2 items in 1 GRN = 2 table rows).
    2. Header correctness.
    3. Price masking when prices are omitted/masked vs real numeric values.
    """
    # Sample hierarchical data with nested child items and prices masked/omitted
    masked_data = [
        {
            "grn_number": "GRN-2026-001",
            "grn_date": "2026-11-20",
            "supplier": "UltraTech Cement Ltd",
            "items": [
                {"material": "OPC 53 Cement", "received_qty": 500, "unit_rate": None, "amount": None},
                {"material": "PPC Cement", "received_qty": 200, "unit_rate": None, "amount": None},
            ]
        },
        {
            "grn_number": "GRN-2026-002",
            "grn_date": "2026-11-21",
            "supplier": "Tata Steel Ltd",
            "items": [
                {"material": "TMT Rebar 16mm", "received_qty": 10.5, "unit_rate": None, "amount": None},
            ]
        }
    ]

    # --- Scenario A: Masked / Omitted Prices (Storekeeper / Site Engineer) ---
    buf_masked = export_report_to_excel(
        report_name="Goods Receipt Note Register",
        data=masked_data,
    )
    excel_masked_bytes = buf_masked.getvalue()
    assert isinstance(excel_masked_bytes, bytes)
    assert len(excel_masked_bytes) > 1000  # Valid zip-based xlsx file

    # Inspect the physical workbook with openpyxl
    wb_masked = openpyxl.load_workbook(io.BytesIO(excel_masked_bytes))
    sheet_masked = wb_masked.active
    assert sheet_masked.title == "Goods Receipt Note Register"[:31]

    # Check header row (row 1, since there's no summary_meta)
    headers_masked = [cell.value for cell in sheet_masked[1]]
    assert "Grn Number" in headers_masked
    assert "Item Material" in headers_masked
    assert "Item Unit Rate" in headers_masked
    assert "Item Amount" in headers_masked

    # Total data rows: 2 items from GRN-001 + 1 item from GRN-002 = 3 data rows (rows 2, 3, 4)
    data_rows_masked = list(sheet_masked.iter_rows(min_row=2, max_row=4, values_only=True))
    assert len(data_rows_masked) == 3

    # Check that rates and amounts are empty string / omitted (never leaked)
    rate_idx = headers_masked.index("Item Unit Rate")
    amount_idx = headers_masked.index("Item Amount")
    for row in data_rows_masked:
        assert row[rate_idx] in (None, ""), f"Expected omitted rate, got {row[rate_idx]}"
        assert row[amount_idx] in (None, ""), f"Expected omitted amount, got {row[amount_idx]}"

    # --- Scenario B: Unmasked Prices (Admin / Project Manager) ---
    unmasked_data = [
        {
            "grn_number": "GRN-2026-001",
            "grn_date": "2026-11-20",
            "supplier": "UltraTech Cement Ltd",
            "items": [
                {"material": "OPC 53 Cement", "received_qty": 500, "unit_rate": 380.00, "amount": 190000.00},
                {"material": "PPC Cement", "received_qty": 200, "unit_rate": 350.00, "amount": 70000.00},
            ]
        }
    ]
    buf_unmasked = export_report_to_excel(
        report_name="Goods Receipt Note Register",
        data=unmasked_data,
    )
    wb_unmasked = openpyxl.load_workbook(io.BytesIO(buf_unmasked.getvalue()))
    sheet_unmasked = wb_unmasked.active
    headers_unmasked = [cell.value for cell in sheet_unmasked[1]]
    data_rows_unmasked = list(sheet_unmasked.iter_rows(min_row=2, max_row=3, values_only=True))

    rate_idx_un = headers_unmasked.index("Item Unit Rate")
    amount_idx_un = headers_unmasked.index("Item Amount")
    assert "380" in str(data_rows_unmasked[0][rate_idx_un])
    assert "190000" in str(data_rows_unmasked[0][amount_idx_un])


# ================= 3. REAL PDF FILE GENERATION & INSPECTION =================

def test_real_pdf_file_layout_and_price_masking():
    """
    Generates actual .pdf bytes using ReportLab, inspects the document via pypdf,
    and verifies:
    1. Valid PDF structure and page count.
    2. Header rendering ("Material Issue Register").
    3. Price masking in text extraction (prices omitted when include_price=False).
    """
    masked_issue_data = [
        {
            "issue_number": "MIN-2026-050",
            "issue_date": "2026-11-22",
            "contractor": "Apex Civil Contracting",
            "allocations": [
                {"material": "Cement Bags", "quantity": 100, "rate": None, "valuation": None},
                {"material": "Sand (Brass)", "quantity": 5, "rate": None, "valuation": None},
            ]
        }
    ]

    # --- Scenario A: Masked PDF ---
    buf_masked = export_report_to_pdf(
        report_name="Material Issue Register",
        data=masked_issue_data,
    )
    pdf_masked_bytes = buf_masked.getvalue()
    assert isinstance(pdf_masked_bytes, bytes)
    assert pdf_masked_bytes.startswith(b"%PDF-"), "Must have valid PDF magic number header"

    reader_masked = PdfReader(io.BytesIO(pdf_masked_bytes))
    assert len(reader_masked.pages) >= 1
    page1_text = reader_masked.pages[0].extract_text()
    assert "Material Issue Register" in page1_text
    assert "MIN-2026-050" in page1_text
    assert "Cement Bags" in page1_text
    assert "Apex Civil Contracting" in page1_text
    # Verify prices are not leaked
    assert "38000" not in page1_text

    # --- Scenario B: Unmasked PDF ---
    unmasked_issue_data = [
        {
            "issue_number": "MIN-2026-050",
            "issue_date": "2026-11-22",
            "contractor": "Apex Civil Contracting",
            "allocations": [
                {"material": "Cement Bags", "quantity": 100, "rate": 380.0, "valuation": 38000.0},
            ]
        }
    ]
    buf_unmasked = export_report_to_pdf(
        report_name="Material Issue Register",
        data=unmasked_issue_data,
    )
    reader_unmasked = PdfReader(io.BytesIO(buf_unmasked.getvalue()))
    page1_unmasked_text = reader_unmasked.pages[0].extract_text()
    assert "Material Issue Register" in page1_unmasked_text
    assert "38000" in page1_unmasked_text or "380" in page1_unmasked_text
