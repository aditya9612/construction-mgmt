import json
import io
import csv
import uuid
import orjson
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Dict, Any, Tuple
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from starlette.concurrency import run_in_threadpool

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.cache.redis import LUA_ATOMIC_GETDEL
from app.core.logger import logger
from app.models.daily_stock import (
    DailyStockReconciliation,
    DailyStockRecountHistory,
    DailyStockIdempotencyLog,
)
from app.models.inventory import StockBatch
from app.models.material import (
    Material,
    MaterialTransaction,
    MaterialLedger,
)
from app.models.project import Project
from app.core.enums import TransactionType
from app.services.sequence_service import allocate_material_code
from app.utils.helpers import AppError, NotFoundError, is_formula_injection_risk, scan_xlsx_formula_rows


async def generate_reconciliation_number(db: AsyncSession) -> str:
    """Generates an atomic unique DSR number."""
    return await allocate_material_code(db, prefix="DSR", padding=5)


async def check_idempotency(
    db: AsyncSession,
    company_id: int,
    idempotency_key: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Checks and returns cached response if idempotency key was previously processed."""
    if not idempotency_key:
        return None
    stmt = select(DailyStockIdempotencyLog).where(
        and_(
            DailyStockIdempotencyLog.company_id == company_id,
            DailyStockIdempotencyLog.idempotency_key == idempotency_key,
        )
    )
    log_entry = (await db.execute(stmt)).scalar_one_or_none()
    if log_entry:
        return json.loads(log_entry.response_payload)
    return None


async def log_idempotency(
    db: AsyncSession,
    company_id: int,
    idempotency_key: Optional[str],
    endpoint: str,
    payload: Dict[str, Any],
    status_code: int = 200,
) -> None:
    """Stores idempotent response in DailyStockIdempotencyLog."""
    if not idempotency_key:
        return
    log_entry = DailyStockIdempotencyLog(
        company_id=company_id,
        idempotency_key=idempotency_key,
        endpoint=endpoint,
        response_status=status_code,
        response_payload=json.dumps(payload, default=str),
    )
    db.add(log_entry)
    await db.flush()


async def record_daily_stock(
    db: AsyncSession,
    company_id: int,
    user_id: int,
    project_id: int,
    material_id: int,
    physical_count: Decimal,
    reconciliation_date: Optional[date] = None,
    remarks: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> DailyStockReconciliation:
    """
    Records daily physical stock count and transitions state machine.
    - If Variance == 0: RECONCILED
    - If Variance != 0: VARIANCE_FLAGGED
    """
    rec_date = reconciliation_date or date.today()
    if rec_date > date.today():
        raise AppError(status_code=400, message="Reconciliation date cannot be in the future.")

    # 1. Validate Material and Project
    stmt_mat = select(Material).where(
        and_(
            Material.id == material_id,
            Material.project_id == project_id,
            Material.is_deleted == False,
        )
    ).with_for_update()
    material = (await db.execute(stmt_mat)).scalar_one_or_none()
    if not material:
        raise NotFoundError(f"Material {material_id} not found on project {project_id}")

    # 2. Check existing record for this date
    stmt_existing = select(DailyStockReconciliation).where(
        and_(
            DailyStockReconciliation.project_id == project_id,
            DailyStockReconciliation.material_id == material_id,
            DailyStockReconciliation.reconciliation_date == rec_date,
        )
    ).with_for_update()
    existing = (await db.execute(stmt_existing)).scalar_one_or_none()

    system_book_stock = material.remaining_stock
    variance = physical_count - system_book_stock
    status = "RECONCILED" if variance == Decimal("0.000") else "VARIANCE_FLAGGED"

    if existing:
        if existing.status in ("ADJUSTED", "UNDER_AUDIT", "CANCELLED"):
            raise AppError(
                status_code=400,
                message=(
                    f"Daily stock entry for {rec_date} is already in locked/finalized status "
                    f"'{existing.status}'. Duplicate overwrite is not allowed."
                ),
            )
        # Update unfinalized existing draft/flagged entry
        existing.physical_count = physical_count
        existing.system_book_stock = system_book_stock
        existing.variance = variance
        existing.status = status
        existing.remarks = remarks or existing.remarks
        await db.commit()
        await db.refresh(existing)
        return existing

    rec_number = await generate_reconciliation_number(db)
    dsr = DailyStockReconciliation(
        reconciliation_number=rec_number,
        company_id=company_id,
        project_id=project_id,
        material_master_id=material.material_master_id,
        material_id=material.id,
        reconciliation_date=rec_date,
        system_book_stock=system_book_stock,
        physical_count=physical_count,
        variance=variance,
        status=status,
        version=1,
        recorded_by_id=user_id,
        remarks=remarks,
    )
    db.add(dsr)
    await db.commit()
    await db.refresh(dsr)
    return dsr


async def submit_recount(
    db: AsyncSession,
    dsr_id: int,
    user_id: int,
    new_physical_count: Decimal,
    recount_notes: Optional[str] = None,
) -> DailyStockReconciliation:
    """
    Submits a recount, increments version, and archives recount history.
    """
    stmt = (
        select(DailyStockReconciliation)
        .options(selectinload(DailyStockReconciliation.recounts))
        .where(DailyStockReconciliation.id == dsr_id)
        .with_for_update()
    )
    dsr = (await db.execute(stmt)).scalar_one_or_none()
    if not dsr:
        raise NotFoundError(f"Daily stock reconciliation {dsr_id} not found")

    old_count = dsr.physical_count
    old_variance = dsr.variance

    # Archive previous count
    history = DailyStockRecountHistory(
        reconciliation_id=dsr.id,
        version=dsr.version,
        previous_count=old_count,
        new_count=new_physical_count,
        variance=old_variance,
        recounted_by_id=user_id,
        recount_notes=recount_notes,
    )
    db.add(history)

    # Update reconciliation
    dsr.version += 1
    dsr.physical_count = new_physical_count
    new_variance = new_physical_count - dsr.system_book_stock
    dsr.variance = new_variance
    dsr.status = "RECONCILED" if new_variance == Decimal("0.000") else "VARIANCE_FLAGGED"
    dsr.remarks = f"Recount version {dsr.version}: {recount_notes}" if recount_notes else dsr.remarks

    await db.commit()
    await db.refresh(dsr)
    return dsr


async def review_reconciliation(
    db: AsyncSession,
    dsr_id: int,
    reviewer_id: int,
    review_outcome: str,  # ACCEPTED_WITHOUT_ADJUSTMENT, UNDER_AUDIT, ADJUST_INVENTORY
    review_notes: Optional[str] = None,
) -> DailyStockReconciliation:
    """
    Maker-checker review step:
    - ACCEPTED_WITHOUT_ADJUSTMENT: Retains VARIANCE_FLAGGED with review_outcome set.
    - UNDER_AUDIT: Status becomes UNDER_AUDIT.
    - ADJUST_INVENTORY: Applies inventory adjustment to Material and Batches. Status becomes ADJUSTED.
    """
    stmt = (
        select(DailyStockReconciliation)
        .options(
            selectinload(DailyStockReconciliation.material),
            selectinload(DailyStockReconciliation.recounts),
        )
        .where(DailyStockReconciliation.id == dsr_id)
        .with_for_update()
    )
    dsr = (await db.execute(stmt)).scalar_one_or_none()
    if not dsr:
        raise NotFoundError(f"Daily stock reconciliation {dsr_id} not found")

    dsr.reviewed_by_id = reviewer_id
    dsr.reviewed_at = datetime.utcnow()

    if review_outcome == "ACCEPTED_WITHOUT_ADJUSTMENT":
        dsr.review_outcome = "ACCEPTED_WITHOUT_ADJUSTMENT"
        dsr.remarks = f"{dsr.remarks or ''} | Variance accepted: {review_notes or ''}"

    elif review_outcome == "UNDER_AUDIT":
        dsr.status = "UNDER_AUDIT"
        dsr.review_outcome = "UNDER_AUDIT"
        dsr.remarks = f"{dsr.remarks or ''} | Sent for audit: {review_notes or ''}"

    elif review_outcome == "ADJUST_INVENTORY":
        # Atomically adjust inventory
        material = dsr.material
        variance = dsr.variance

        if variance != Decimal("0.000"):
            material.remaining_stock += variance
            rate = material.purchase_rate or Decimal("0.00")
            adj_amount = (abs(variance) * rate).quantize(Decimal("0.01"))

            if variance > Decimal("0.000"):
                # Positive variance: create batch
                batch = StockBatch(
                    batch_number=f"ADJ-{dsr.reconciliation_number}",
                    project_id=dsr.project_id,
                    material_master_id=dsr.material_master_id,
                    initial_quantity=variance,
                    current_balance=variance,
                    purchase_rate=rate,
                    receipt_date=datetime.utcnow(),
                    batch_source="ADJUSTMENT",
                    status="ACTIVE",
                )
                db.add(batch)
            else:
                # Negative variance: FIFO deplete batches
                neg_qty = abs(variance)
                stmt_batches = (
                    select(StockBatch)
                    .where(
                        and_(
                            StockBatch.project_id == dsr.project_id,
                            StockBatch.material_master_id == dsr.material_master_id,
                            StockBatch.current_balance > Decimal("0.000"),
                            StockBatch.status == "ACTIVE",
                        )
                    )
                    .order_by(StockBatch.receipt_date.asc(), StockBatch.id.asc())
                    .with_for_update()
                )
                batches = (await db.execute(stmt_batches)).scalars().all()
                for b in batches:
                    if neg_qty <= Decimal("0.000"):
                        break
                    take = min(neg_qty, b.current_balance)
                    b.current_balance -= take
                    if b.current_balance == Decimal("0.000"):
                        b.status = "DEPLETED"
                    neg_qty -= take

            # Financial Transactions
            mat_tx = MaterialTransaction(
                material_id=material.id,
                type=TransactionType.ADJUSTMENT,
                quantity=variance,
                rate=rate,
                total_amount=adj_amount,
            )
            db.add(mat_tx)

            ledger = MaterialLedger(
                material_id=material.id,
                type=TransactionType.ADJUSTMENT,
                quantity=variance,
                rate=rate,
                total_amount=adj_amount,
            )
            db.add(ledger)

        dsr.status = "ADJUSTED"
        dsr.review_outcome = "ADJUSTMENT_APPROVED"
        dsr.remarks = f"{dsr.remarks or ''} | Adjustment approved: {review_notes or ''}"

    else:
        raise AppError(status_code=400, message=f"Invalid review outcome '{review_outcome}'")

    await db.commit()
    await db.refresh(dsr)
    return dsr


async def get_daily_stock(
    db: AsyncSession,
    dsr_id: int,
) -> Optional[DailyStockReconciliation]:
    """Retrieves a single reconciliation record."""
    stmt = (
        select(DailyStockReconciliation)
        .options(
            selectinload(DailyStockReconciliation.recounts),
            selectinload(DailyStockReconciliation.material),
            selectinload(DailyStockReconciliation.material_master),
            selectinload(DailyStockReconciliation.project),
            selectinload(DailyStockReconciliation.recorded_by),
            selectinload(DailyStockReconciliation.reviewed_by),
        )
        .where(DailyStockReconciliation.id == dsr_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_daily_stock(
    db: AsyncSession,
    company_id: Optional[int] = None,
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    reconciliation_date: Optional[date] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[DailyStockReconciliation]:
    """Lists reconciliation records with filters."""
    stmt = (
        select(DailyStockReconciliation)
        .options(
            selectinload(DailyStockReconciliation.material),
            selectinload(DailyStockReconciliation.project),
        )
    )
    conditions = []
    if company_id:
        conditions.append(DailyStockReconciliation.company_id == company_id)
    if project_id:
        conditions.append(DailyStockReconciliation.project_id == project_id)
    if status:
        conditions.append(DailyStockReconciliation.status == status)
    if reconciliation_date:
        conditions.append(DailyStockReconciliation.reconciliation_date == reconciliation_date)

    if conditions:
        stmt = stmt.where(and_(*conditions))

    stmt = stmt.order_by(DailyStockReconciliation.id.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


# ================= DAILY STOCK BULK UPLOAD =================

DAILY_STOCK_TOKEN_PREFIX = "bulk_upload:daily_stock:"
_dsr_token_store: Dict[str, Dict[str, Any]] = {}


def generate_daily_stock_template() -> io.BytesIO:
    """Generates an Excel workbook template for Daily Stock reconciliation bulk upload."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Stock Upload"

    headers = [
        "Project ID",
        "Material ID",
        "Physical Count",
        "Reconciliation Date (YYYY-MM-DD)",
        "Remarks",
    ]
    ws.append(headers)

    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    sample_date = date.today().isoformat()
    ws.append([1, 101, 250.50, sample_date, "Routine physical count"])

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = col[0].column_letter
        ws.column_dimensions[col_letter].width = max(max_len + 4, 16)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


async def parse_and_validate_daily_stock_file(
    db: AsyncSession,
    file_bytes: bytes,
    filename: str,
    company_id: int,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parses and validates Excel or CSV daily stock entries."""
    rows_data: List[Dict[str, Any]] = []
    errors: List[str] = []
    formula_flagged_rows: Dict[int, str] = {}

    if filename.lower().endswith(".xlsx"):
        try:
            formula_flagged_rows = scan_xlsx_formula_rows(file_bytes, numeric_cols={0, 1, 2})
            wb = await run_in_threadpool(load_workbook, io.BytesIO(file_bytes), data_only=True)
            ws = wb.active
            all_rows = list(ws.iter_rows(values_only=True))
            if not all_rows or len(all_rows) < 2:
                return [], ["Uploaded Excel file is empty or missing data rows."]
            for idx, row in enumerate(all_rows[1:], start=2):
                if not any(row) and idx not in formula_flagged_rows:
                    continue
                rows_data.append({
                    "row_num": idx,
                    "project_id": row[0] if len(row) > 0 else None,
                    "material_id": row[1] if len(row) > 1 else None,
                    "physical_count": row[2] if len(row) > 2 else None,
                    "reconciliation_date": row[3] if len(row) > 3 else None,
                    "remarks": str(row[4]).strip() if len(row) > 4 and row[4] is not None else None,
                })
        except Exception as e:
            return [], [f"Failed to read Excel file: {str(e)}"]
    else:
        try:
            text = file_bytes.decode("utf-8-sig", errors="replace")
            reader = csv.reader(text.splitlines())
            all_rows = list(reader)
            if not all_rows or len(all_rows) < 2:
                return [], ["Uploaded CSV file is empty or missing data rows."]
            for idx, row in enumerate(all_rows[1:], start=2):
                if not any(row):
                    continue
                rows_data.append({
                    "row_num": idx,
                    "project_id": row[0] if len(row) > 0 else None,
                    "material_id": row[1] if len(row) > 1 else None,
                    "physical_count": row[2] if len(row) > 2 else None,
                    "reconciliation_date": row[3] if len(row) > 3 else None,
                    "remarks": row[4].strip() if len(row) > 4 and row[4] else None,
                })
        except Exception as e:
            return [], [f"Failed to read CSV file: {str(e)}"]

    validated_items: List[Dict[str, Any]] = []
    seen_pairs = set()

    for r in rows_data:
        row_num = r["row_num"]
        if row_num in formula_flagged_rows:
            errors.append(formula_flagged_rows[row_num])
            continue
        if (
            is_formula_injection_risk(r.get("project_id"))
            or is_formula_injection_risk(r.get("material_id"))
            or is_formula_injection_risk(r.get("physical_count"))
            or is_formula_injection_risk(r.get("reconciliation_date"), text_only=True)
            or is_formula_injection_risk(r.get("remarks"), text_only=True)
        ):
            errors.append(f"Row {row_num}: Potentially dangerous spreadsheet formula or macro payload rejected.")
            continue

        try:
            pid = int(r["project_id"])
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Invalid or missing Project ID.")
            continue

        try:
            mid = int(r["material_id"])
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Invalid or missing Material ID.")
            continue

        pair_key = (pid, mid)
        if pair_key in seen_pairs:
            errors.append(f"Row {row_num}: Duplicate Project ID {pid} and Material ID {mid} in same upload.")
            continue
        seen_pairs.add(pair_key)

        try:
            count = Decimal(str(r["physical_count"]))
            if count < Decimal("0"):
                errors.append(f"Row {row_num}: Physical count cannot be negative.")
                continue
        except (InvalidOperation, TypeError, ValueError):
            errors.append(f"Row {row_num}: Invalid physical count '{r['physical_count']}'.")
            continue

        recon_date = date.today()
        raw_date = r["reconciliation_date"]
        if raw_date:
            if isinstance(raw_date, datetime):
                recon_date = raw_date.date()
            elif isinstance(raw_date, date):
                recon_date = raw_date
            else:
                try:
                    recon_date = datetime.strptime(str(raw_date).strip()[:10], "%Y-%m-%d").date()
                except ValueError:
                    errors.append(f"Row {row_num}: Invalid date format '{raw_date}' (expected YYYY-MM-DD).")
                    continue
        if recon_date > date.today():
            errors.append(f"Row {row_num}: Reconciliation date '{recon_date}' cannot be in the future.")
            continue

        proj = await db.get(Project, pid)
        if not proj:
            errors.append(f"Row {row_num}: Project {pid} does not exist.")
            continue
        if proj.company_id != company_id:
            errors.append(f"Row {row_num}: Project {pid} does not belong to your company.")
            continue

        mat = await db.get(Material, mid)
        if not mat or mat.is_deleted:
            errors.append(f"Row {row_num}: Material {mid} does not exist.")
            continue
        if mat.project_id != pid:
            errors.append(f"Row {row_num}: Material {mid} does not belong to Project {pid}.")
            continue

        validated_items.append({
            "row_number": row_num,
            "project_id": pid,
            "material_id": mid,
            "material_name": mat.material_name,
            "physical_count": count,
            "reconciliation_date": recon_date,
            "remarks": r["remarks"],
        })

    return validated_items, errors


async def store_daily_stock_token(
    token: str,
    items: List[Dict[str, Any]],
    ttl_seconds: int = 900,
    redis: Any = None,
    user_id: Optional[int] = None,
    company_id: Optional[int] = None,
) -> None:
    """Stores bulk upload preview token in shared Redis or process memory with TTL."""
    serialized = []
    for it in items:
        row = dict(it)
        if "physical_count" in row and isinstance(row["physical_count"], Decimal):
            row["physical_count"] = str(row["physical_count"])
        if "reconciliation_date" in row and isinstance(row["reconciliation_date"], (date, datetime)):
            row["reconciliation_date"] = row["reconciliation_date"].isoformat()[:10]
        serialized.append(row)

    payload = {
        "items": serialized,
        "user_id": user_id,
        "company_id": company_id,
        "expires_at": datetime.utcnow().timestamp() + ttl_seconds,
    }

    if redis is not None:
        key = f"{DAILY_STOCK_TOKEN_PREFIX}{token}"
        try:
            raw_bytes = orjson.dumps(payload)
            await redis.set(key, raw_bytes, ex=ttl_seconds)
            return
        except Exception as exc:
            logger.warning(f"Failed to store daily stock token in Redis: {exc}")

    _dsr_token_store[token] = {
        "items": items,
        "user_id": user_id,
        "company_id": company_id,
        "expires_at": datetime.utcnow().timestamp() + ttl_seconds,
    }


def store_daily_stock_token_sync(
    token: str,
    items: List[Dict[str, Any]],
    ttl_seconds: int = 900,
    user_id: Optional[int] = None,
    company_id: Optional[int] = None,
) -> None:
    """Synchronous fallback for store_daily_stock_token in offline/sync contexts."""
    _dsr_token_store[token] = {
        "items": items,
        "user_id": user_id,
        "company_id": company_id,
        "expires_at": datetime.utcnow().timestamp() + ttl_seconds,
    }


async def retrieve_and_consume_daily_stock_token(
    token: str,
    redis: Any = None,
    user_id: Optional[int] = None,
    company_id: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Atomically consumes a single-use daily stock token using Lua script in Redis."""
    if not token or not isinstance(token, str):
        return None

    if redis is not None:
        key = f"{DAILY_STOCK_TOKEN_PREFIX}{token}"
        try:
            raw = await redis.eval(LUA_ATOMIC_GETDEL, 1, key)
            if not raw:
                return None

            payload = orjson.loads(raw)
            if not payload or not isinstance(payload, dict):
                return None

            if company_id is not None and payload.get("company_id") is not None:
                if payload["company_id"] != company_id:
                    logger.warning(
                        f"Tenant mismatch on daily stock token consumption: expected {company_id}, got {payload.get('company_id')}"
                    )
                    return None

            if user_id is not None and payload.get("user_id") is not None:
                if payload["user_id"] != user_id:
                    logger.warning(
                        f"User mismatch on daily stock token consumption: expected {user_id}, got {payload.get('user_id')}"
                    )
                    return None

            if datetime.utcnow().timestamp() > payload.get("expires_at", 0):
                return None

            items = []
            for it in payload.get("items", []):
                row = dict(it)
                if "physical_count" in row and row["physical_count"] is not None:
                    row["physical_count"] = Decimal(str(row["physical_count"]))
                if "reconciliation_date" in row and row["reconciliation_date"] is not None:
                    row["reconciliation_date"] = datetime.strptime(
                        str(row["reconciliation_date"])[:10], "%Y-%m-%d"
                    ).date()
                items.append(row)
            return items
        except Exception as exc:
            logger.warning(f"Failed to retrieve daily stock token from Redis: {exc}")
            return None

    entry = _dsr_token_store.pop(token, None)
    if not entry:
        return None
    if datetime.utcnow().timestamp() > entry.get("expires_at", 0):
        return None
    if company_id is not None and entry.get("company_id") is not None and entry["company_id"] != company_id:
        return None
    if user_id is not None and entry.get("user_id") is not None and entry["user_id"] != user_id:
        return None
    return entry["items"]


def retrieve_and_consume_daily_stock_token_sync(
    token: str,
    user_id: Optional[int] = None,
    company_id: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Synchronous fallback for retrieve_and_consume_daily_stock_token."""
    entry = _dsr_token_store.pop(token, None)
    if not entry:
        return None
    if datetime.utcnow().timestamp() > entry.get("expires_at", 0):
        return None
    if company_id is not None and entry.get("company_id") is not None and entry["company_id"] != company_id:
        return None
    if user_id is not None and entry.get("user_id") is not None and entry["user_id"] != user_id:
        return None
    return entry["items"]
