import csv
import io
import uuid
import orjson
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Dict, Any, Tuple
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from starlette.concurrency import run_in_threadpool

from sqlalchemy import select, and_, asc, case, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.cache.redis import LUA_ATOMIC_GETDEL
from app.core.logger import logger
from app.models.inventory import (
    MaterialIssue,
    MaterialIssueAllocation,
    StockBatch,
    ProjectMaterialValuation,
)
from app.models.material import (
    Material,
    MaterialTransaction,
    MaterialLedger,
)
from app.models.notification import Notification
from app.models.project import Project
from app.models.contractor import Contractor
from app.core.enums import TransactionType, IssueType
from app.services.sequence_service import allocate_material_code
from app.services.notification_service import create_notification, notify_company_stakeholders
from app.utils.helpers import (
    AppError,
    NotFoundError,
    is_formula_injection_risk,
    scan_xlsx_formula_rows,
    resolve_min_stock_threshold,
)


async def generate_issue_number(db: AsyncSession) -> str:
    """Generates an atomic unique MIN number."""
    return await allocate_material_code(db, prefix="MIN", padding=5)


async def create_material_issue(
    db: AsyncSession,
    company_id: int,
    user_id: int,
    project_id: int,
    material_id: int,
    quantity: Decimal,
    issue_type: str,
    issued_to: Optional[str] = None,
    contractor_id: Optional[int] = None,
    boq_item_id: Optional[int] = None,
    idempotency_key: Optional[str] = None,
    remarks: Optional[str] = None,
) -> MaterialIssue:
    """
    20-step atomic outward dispatch (MIN).
    Option A: Single outer transaction with nested savepoint duplicate recovery,
    FIFO batch depletion, Ind AS 2 MWA financial recording, and consolidated stock decrement.
    """
    if quantity <= Decimal("0.000"):
        raise AppError(status_code=400, message="Issue quantity must be strictly greater than 0")

    # 1. Idempotency Check (Pre-check)
    if idempotency_key:
        stmt = (
            select(MaterialIssue)
            .options(
                selectinload(MaterialIssue.allocations),
                selectinload(MaterialIssue.material),
            )
            .where(
                and_(
                    MaterialIssue.company_id == company_id,
                    MaterialIssue.idempotency_key == idempotency_key,
                )
            )
        )
        existing_issue = (await db.execute(stmt)).scalar_one_or_none()
        if existing_issue:
            return existing_issue

    # 2. Validate Project
    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError(f"Project {project_id} not found")
    if project.company_id != company_id:
        raise AppError(status_code=403, message="Forbidden: Project belongs to a different company")

    # 3. Lock and validate Material on site
    stmt_mat = (
        select(Material)
        .where(
            and_(
                Material.id == material_id,
                Material.project_id == project_id,
                Material.is_deleted == False,
            )
        )
        .with_for_update()
    )
    material = (await db.execute(stmt_mat)).scalar_one_or_none()
    if not material:
        raise NotFoundError(f"Material {material_id} not found on project {project_id}")

    if material.remaining_stock < quantity:
        raise AppError(
            status_code=400,
            message=(
                f"Insufficient stock for material '{material.material_name}'. "
                f"Available: {material.remaining_stock}, Requested: {quantity}"
            ),
        )

    # 4. Generate Issue Number
    issue_number = await generate_issue_number(db)

    # 5. Create MaterialIssue in nested savepoint
    savepoint = await db.begin_nested()
    try:
        issue = MaterialIssue(
            issue_number=issue_number,
            company_id=company_id,
            project_id=project_id,
            material_master_id=material.material_master_id,
            material_id=material.id,
            quantity=quantity,
            issue_type=issue_type,
            issued_to=issued_to,
            contractor_id=contractor_id,
            boq_item_id=boq_item_id,
            idempotency_key=idempotency_key,
            status="ISSUED",
            created_by_id=user_id,
            remarks=remarks,
        )
        db.add(issue)
        await db.flush()
    except IntegrityError as e:
        await savepoint.rollback()
        # Recover duplicate concurrent request
        if idempotency_key:
            stmt = (
                select(MaterialIssue)
                .options(selectinload(MaterialIssue.allocations))
                .where(
                    and_(
                        MaterialIssue.company_id == company_id,
                        MaterialIssue.idempotency_key == idempotency_key,
                    )
                )
            )
            existing_issue = (await db.execute(stmt)).scalar_one_or_none()
            if existing_issue:
                return existing_issue
        raise AppError(status_code=409, message=f"Integrity conflict during issue creation: {e}")

    # 6. FIFO Batch Depletion
    # Policy: OPENING_BALANCE batches (receipt_date IS NULL) allocated first per BD-17.
    # For post-migration GRN batches, chronological receipt_date ASC is strictly primary.
    # Deterministic chronological tie-breaker is id ASC. purchase_rate is NEVER primary.
    stmt_batches = (
        select(StockBatch)
        .where(
            and_(
                StockBatch.project_id == project_id,
                StockBatch.material_master_id == material.material_master_id,
                StockBatch.current_balance > Decimal("0.000"),
                StockBatch.status == "ACTIVE",
            )
        )
        .order_by(
            case((StockBatch.receipt_date.is_(None), 0), else_=1).asc(),
            StockBatch.receipt_date.asc(),
            StockBatch.id.asc(),
        )
        .with_for_update()
    )
    batches = (await db.execute(stmt_batches)).scalars().all()

    remaining_to_allocate = quantity
    total_financial_cost = Decimal("0.00")

    for batch in batches:
        if remaining_to_allocate <= Decimal("0.000"):
            break

        take_qty = min(remaining_to_allocate, batch.current_balance)
        batch.current_balance -= take_qty
        if batch.current_balance == Decimal("0.000"):
            batch.status = "DEPLETED"

        batch_amount = (take_qty * batch.purchase_rate).quantize(Decimal("0.01"))
        total_financial_cost += batch_amount

        allocation = MaterialIssueAllocation(
            issue_id=issue.id,
            batch_id=batch.id,
            allocated_quantity=take_qty,
            unit_rate=batch.purchase_rate,
            total_amount=batch_amount,
        )
        db.add(allocation)
        remaining_to_allocate -= take_qty

    # 7. Fallback if batches were slightly out of sync with remaining_stock (e.g. legacy records)
    if remaining_to_allocate > Decimal("0.000"):
        unit_rate = material.purchase_rate or Decimal("0.00")
        unbatched_amount = (remaining_to_allocate * unit_rate).quantize(Decimal("0.01"))
        total_financial_cost += unbatched_amount

    # 8. Decrement consolidated site stock
    material.remaining_stock -= quantity
    material.quantity_used += quantity

    # 9. Ind AS 2 Moving Weighted Average on Current On-Hand Stock (Area C)
    stmt_val = (
        select(ProjectMaterialValuation)
        .where(
            and_(
                ProjectMaterialValuation.project_id == project_id,
                ProjectMaterialValuation.material_master_id == material.material_master_id,
            )
        )
        .with_for_update()
    )
    val = (await db.execute(stmt_val)).scalar_one_or_none()

    if val and val.total_purchased_quantity > Decimal("0.000") and val.current_moving_weighted_average_rate > Decimal("0.00"):
        mwa_rate = val.current_moving_weighted_average_rate
    elif material.purchase_rate and material.purchase_rate > Decimal("0.00"):
        mwa_rate = material.purchase_rate
    elif quantity > 0:
        mwa_rate = (total_financial_cost / quantity).quantize(Decimal("0.01"))
    else:
        mwa_rate = Decimal("0.00")

    outward_cost = (quantity * mwa_rate).quantize(Decimal("0.01"))

    if val:
        rem_qty = max(Decimal("0.000"), val.total_purchased_quantity - quantity)
        rem_amt = max(Decimal("0.00"), val.total_purchased_amount - outward_cost)
        if rem_qty <= Decimal("0.000"):
            val.total_purchased_quantity = Decimal("0.000")
            val.total_purchased_amount = Decimal("0.00")
            val.current_moving_weighted_average_rate = Decimal("0.00")
        else:
            val.total_purchased_quantity = rem_qty
            val.total_purchased_amount = rem_amt
            val.current_moving_weighted_average_rate = (rem_amt / rem_qty).quantize(Decimal("0.01"))
        val.last_recalculated_at = datetime.utcnow()

    mwa_total_amount = outward_cost

    # 10. Financial Audit Ledger and MaterialTransaction
    mat_tx = MaterialTransaction(
        material_id=material.id,
        type=TransactionType.USAGE,
        quantity=quantity,
        rate=mwa_rate,
        total_amount=mwa_total_amount,
    )
    db.add(mat_tx)

    ledger_entry = MaterialLedger(
        material_id=material.id,
        type=TransactionType.USAGE,
        quantity=quantity,
        rate=mwa_rate,
        total_amount=mwa_total_amount,
    )
    db.add(ledger_entry)

    # 11. Event Notifications (Area B & E)
    await notify_company_stakeholders(
        db=db,
        company_id=company_id,
        project_id=project_id,
        actor_id=user_id,
        title=f"Material Issue Dispatched: {issue.issue_number}",
        message=f"MIN {issue.issue_number} of {quantity} units for {material.material_name} dispatched to {issued_to or 'Site'}.",
        type="info",
        link=f"/inventory/issues/{issue.id}",
    )
    min_threshold = resolve_min_stock_threshold(material)
    if material.remaining_stock <= min_threshold:
        mat_link = f"/inventory/materials/{material.id}"
        recent_alert = (
            await db.execute(
                select(Notification.id)
                .where(
                    and_(
                        Notification.type == "alert",
                        Notification.link == mat_link,
                        Notification.title.like("Low Stock Alert%"),
                        Notification.created_at >= func.date_sub(func.now(), text("INTERVAL 60 MINUTE")),
                    )
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if recent_alert is None:
            severity = "CRITICAL" if material.remaining_stock <= Decimal("0.000") else "LOW"
            await notify_company_stakeholders(
                db=db,
                company_id=company_id,
                project_id=project_id,
                actor_id=user_id,
                title=f"Low Stock Alert ({severity}): {material.material_name}",
                message=f"Material '{material.material_name}' (Code: {material.material_code}) is {severity}: {material.remaining_stock} remaining (threshold {min_threshold}).",
                type="alert",
                link=mat_link,
            )

    await db.commit()
    await db.refresh(issue)
    return issue


async def get_material_issue(
    db: AsyncSession,
    issue_id: int,
) -> Optional[MaterialIssue]:
    """Retrieves a single MaterialIssue with its allocations and relationships."""
    stmt = (
        select(MaterialIssue)
        .options(
            selectinload(MaterialIssue.allocations).selectinload(MaterialIssueAllocation.batch),
            selectinload(MaterialIssue.material),
            selectinload(MaterialIssue.material_master),
            selectinload(MaterialIssue.project),
            selectinload(MaterialIssue.contractor),
            selectinload(MaterialIssue.created_by),
        )
        .where(MaterialIssue.id == issue_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_material_issues(
    db: AsyncSession,
    company_id: Optional[int] = None,
    project_id: Optional[int] = None,
    material_id: Optional[int] = None,
    issue_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[MaterialIssue]:
    """Lists Material Issues with filters."""
    stmt = (
        select(MaterialIssue)
        .options(
            selectinload(MaterialIssue.allocations),
            selectinload(MaterialIssue.material),
            selectinload(MaterialIssue.project),
        )
    )
    conditions = []
    if company_id:
        conditions.append(MaterialIssue.company_id == company_id)
    if project_id:
        conditions.append(MaterialIssue.project_id == project_id)
    if material_id:
        conditions.append(MaterialIssue.material_id == material_id)
    if issue_type:
        conditions.append(MaterialIssue.issue_type == issue_type)

    if conditions:
        stmt = stmt.where(and_(*conditions))

    stmt = stmt.order_by(MaterialIssue.id.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


# ================= BULK OUTWARD UPLOAD =================

BULK_OUTWARD_TOKEN_PREFIX = "bulk_upload:outward:"
_bulk_outward_token_store: Dict[str, Dict[str, Any]] = {}
ALLOWED_BULK_ISSUE_TYPES = {
    "SITE_CONSUMPTION",
    "CONTRACTOR_ISSUE",
    "TRANSFER",
    "RETURN",
    "DAMAGE",
    "LOSS",
    "ADJUSTMENT",
    "SYSTEM",
    "SITE",
    "VENDOR",
}


def generate_bulk_outward_template() -> io.BytesIO:
    """Generates an Excel workbook template (.xlsx) for bulk outward material issues."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Bulk Outward Upload"

    headers = [
        "Project ID",
        "Material ID",
        "Quantity",
        "Issue Type",
        "Issued To",
        "Contractor ID",
        "BOQ Item ID",
        "Remarks",
    ]
    ws.append(headers)

    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.append([1, 101, 10.000, "SITE_CONSUMPTION", "Tower A Slab", "", "", "Routine slab dispatch"])

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = max(max_len + 4, 16)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


async def parse_and_validate_bulk_outward_file(
    db: AsyncSession,
    file_bytes: bytes,
    filename: str,
    company_id: int,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parses and validates Excel or CSV bulk outward material issue rows."""
    rows_data: List[Dict[str, Any]] = []
    errors: List[str] = []
    formula_flagged_rows: Dict[int, str] = {}

    if filename.lower().endswith(".xlsx"):
        try:
            formula_flagged_rows = scan_xlsx_formula_rows(file_bytes, numeric_cols={0, 1, 2, 5, 6})
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
                    "quantity": row[2] if len(row) > 2 else None,
                    "issue_type": row[3] if len(row) > 3 else None,
                    "issued_to": row[4] if len(row) > 4 else None,
                    "contractor_id": row[5] if len(row) > 5 else None,
                    "boq_item_id": row[6] if len(row) > 6 else None,
                    "remarks": row[7] if len(row) > 7 else None,
                })
        except Exception as e:
            return [], [f"Failed to read Excel file: {str(e)}"]
    else:
        try:
            text = file_bytes.decode("utf-8-sig", errors="replace")
            all_rows = list(csv.reader(text.splitlines()))
            if not all_rows or len(all_rows) < 2:
                return [], ["Uploaded CSV file is empty or missing data rows."]
            for idx, row in enumerate(all_rows[1:], start=2):
                if not any(row):
                    continue
                rows_data.append({
                    "row_num": idx,
                    "project_id": row[0] if len(row) > 0 else None,
                    "material_id": row[1] if len(row) > 1 else None,
                    "quantity": row[2] if len(row) > 2 else None,
                    "issue_type": row[3] if len(row) > 3 else None,
                    "issued_to": row[4] if len(row) > 4 else None,
                    "contractor_id": row[5] if len(row) > 5 else None,
                    "boq_item_id": row[6] if len(row) > 6 else None,
                    "remarks": row[7] if len(row) > 7 else None,
                })
        except Exception as e:
            return [], [f"Failed to read CSV file: {str(e)}"]

    validated_items: List[Dict[str, Any]] = []
    cumulative_mat_qty: Dict[int, Decimal] = {}

    for r in rows_data:
        row_num = r["row_num"]
        if row_num in formula_flagged_rows:
            errors.append(formula_flagged_rows[row_num])
            continue
        if (
            is_formula_injection_risk(r.get("project_id"))
            or is_formula_injection_risk(r.get("material_id"))
            or is_formula_injection_risk(r.get("quantity"))
            or is_formula_injection_risk(r.get("issue_type"), text_only=True)
            or is_formula_injection_risk(r.get("issued_to"), text_only=True)
            or is_formula_injection_risk(r.get("contractor_id"))
            or is_formula_injection_risk(r.get("boq_item_id"))
            or is_formula_injection_risk(r.get("remarks"), text_only=True)
        ):
            errors.append(f"Row {row_num}: Potentially dangerous spreadsheet formula or macro payload rejected.")
            continue

        try:
            pid = int(str(r["project_id"]).strip())
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Invalid or missing Project ID.")
            continue

        try:
            mid = int(str(r["material_id"]).strip())
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Invalid or missing Material ID.")
            continue

        try:
            qty = Decimal(str(r["quantity"]).strip())
            if qty <= Decimal("0"):
                errors.append(f"Row {row_num}: Issue quantity must be strictly greater than 0.")
                continue
        except (InvalidOperation, TypeError, ValueError):
            errors.append(f"Row {row_num}: Invalid quantity '{r['quantity']}'.")
            continue

        raw_type = str(r["issue_type"]).strip().upper() if r["issue_type"] is not None else ""
        if raw_type not in ALLOWED_BULK_ISSUE_TYPES:
            errors.append(
                f"Row {row_num}: Invalid Issue Type '{r['issue_type']}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_BULK_ISSUE_TYPES))}."
            )
            continue

        contractor_id = None
        if r.get("contractor_id") is not None and str(r["contractor_id"]).strip() != "":
            try:
                contractor_id = int(str(r["contractor_id"]).strip())
            except (ValueError, TypeError):
                errors.append(f"Row {row_num}: Invalid Contractor ID '{r['contractor_id']}'.")
                continue

        boq_item_id = None
        if r.get("boq_item_id") is not None and str(r["boq_item_id"]).strip() != "":
            try:
                boq_item_id = int(str(r["boq_item_id"]).strip())
            except (ValueError, TypeError):
                errors.append(f"Row {row_num}: Invalid BOQ Item ID '{r['boq_item_id']}'.")
                continue

        proj = await db.get(Project, pid)
        if not proj:
            errors.append(f"Row {row_num}: Project {pid} does not exist.")
            continue
        if proj.company_id != company_id:
            errors.append(f"Row {row_num}: Project {pid} belongs to a different company.")
            continue

        mat = await db.get(Material, mid)
        if not mat or mat.is_deleted:
            errors.append(f"Row {row_num}: Material {mid} does not exist.")
            continue
        if mat.project_id != pid:
            errors.append(f"Row {row_num}: Material {mid} does not belong to Project {pid}.")
            continue

        projected_total = cumulative_mat_qty.get(mid, Decimal("0.000")) + qty
        if projected_total > (mat.remaining_stock or Decimal("0.000")):
            errors.append(
                f"Row {row_num}: Insufficient stock for material {mid} "
                f"(available: {mat.remaining_stock}, cumulative requested: {projected_total})."
            )
            continue
        cumulative_mat_qty[mid] = projected_total

        if contractor_id is not None:
            contractor = await db.get(Contractor, contractor_id)
            if not contractor or (contractor.company_id is not None and contractor.company_id != company_id):
                errors.append(f"Row {row_num}: Contractor {contractor_id} not found or forbidden.")
                continue

        validated_items.append({
            "row_number": row_num,
            "project_id": pid,
            "material_id": mid,
            "material_name": mat.material_name,
            "quantity": qty,
            "issue_type": raw_type,
            "issued_to": str(r["issued_to"]).strip() if r.get("issued_to") is not None and str(r["issued_to"]).strip() else None,
            "contractor_id": contractor_id,
            "boq_item_id": boq_item_id,
            "remarks": str(r["remarks"]).strip() if r.get("remarks") is not None and str(r["remarks"]).strip() else None,
        })

    return validated_items, errors


async def store_bulk_outward_token(
    token: str,
    items: List[Dict[str, Any]],
    ttl_seconds: int = 900,
    redis: Any = None,
    user_id: Optional[int] = None,
    company_id: Optional[int] = None,
) -> None:
    """Stores bulk outward preview token in shared Redis (or in-memory fallback)."""
    serialized = []
    for it in items:
        row = dict(it)
        if isinstance(row.get("quantity"), Decimal):
            row["quantity"] = str(row["quantity"])
        serialized.append(row)

    payload = {
        "items": serialized,
        "user_id": user_id,
        "company_id": company_id,
        "expires_at": datetime.utcnow().timestamp() + ttl_seconds,
    }
    if redis is not None:
        key = f"{BULK_OUTWARD_TOKEN_PREFIX}{token}"
        try:
            await redis.set(key, orjson.dumps(payload), ex=ttl_seconds)
            return
        except Exception as exc:
            logger.warning(f"Failed to store bulk outward token in Redis: {exc}")

    _bulk_outward_token_store[token] = {
        "items": items,
        "user_id": user_id,
        "company_id": company_id,
        "expires_at": datetime.utcnow().timestamp() + ttl_seconds,
    }


async def retrieve_and_consume_bulk_outward_token(
    token: str,
    redis: Any = None,
    user_id: Optional[int] = None,
    company_id: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Atomically consumes a single-use bulk outward token using Lua GETDEL in Redis."""
    if not token or not isinstance(token, str):
        return None

    if redis is not None:
        key = f"{BULK_OUTWARD_TOKEN_PREFIX}{token}"
        try:
            raw = await redis.eval(LUA_ATOMIC_GETDEL, 1, key)
            if not raw:
                return None
            payload = orjson.loads(raw)
            if not payload or not isinstance(payload, dict):
                return None
            if company_id is not None and payload.get("company_id") is not None and payload["company_id"] != company_id:
                return None
            if user_id is not None and payload.get("user_id") is not None and payload["user_id"] != user_id:
                return None
            items = payload.get("items", [])
            for it in items:
                if "quantity" in it:
                    it["quantity"] = Decimal(str(it["quantity"]))
            return items
        except Exception as exc:
            logger.warning(f"Failed atomic Lua token consumption from Redis for bulk outward: {exc}")

    entry = _bulk_outward_token_store.pop(token, None)
    if not entry:
        return None
    if datetime.utcnow().timestamp() > entry.get("expires_at", 0):
        return None
    if company_id is not None and entry.get("company_id") is not None and entry["company_id"] != company_id:
        return None
    if user_id is not None and entry.get("user_id") is not None and entry["user_id"] != user_id:
        return None
    return entry.get("items")
