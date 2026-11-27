"""
Production-safe bulk material upload service.
Supports .xlsx and .csv ingestion, template generation, row-level validation,
duplicate detection, atomic database insertion, and cache invalidation.
"""

import io
import re
import csv
import uuid
import orjson
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, List, Tuple, Dict, Any, Set

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile, HTTPException, status

from app.models.project import Project
from app.models.master_data import MaterialMaster, Unit
from app.models.material import (
    Material,
    Supplier,
    MaterialTransaction,
    MaterialLedger,
)
from app.models.user import User, UserRole
from app.models.inventory import StockBatch, ProjectMaterialValuation
from app.core.enums import RateType, IssueType, TransactionType as DBTransactionType
from app.utils.common import assert_project_access
from app.utils.helpers import is_formula_injection_risk, scan_xlsx_formula_rows
from app.cache.redis import LUA_ATOMIC_GETDEL, bump_cache_version, cache_get_json, cache_set_json
from app.schemas.material import (
    BulkMaterialError,
    BulkMaterialRowPreview,
    BulkMaterialPreviewResponse,
    BulkMaterialCommitResponse,
    BulkMaterialImportedItem,
)
from app.services.sequence_service import allocate_material_codes
from app.core.logger import logger

# Configuration constants
ALLOWED_EXTENSIONS = {".xlsx", ".csv"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_ROWS = 500
TOKEN_TTL_SECONDS = 900  # 15 minutes
TOKEN_KEY_PREFIX = "bulk_upload:materials:"
VERSION_KEY = "cache_version:materials"

# Thread-safe in-memory fallback for test and standalone environments
_in_memory_token_store: Dict[str, Dict[str, Any]] = {}


async def store_import_token(
    redis: Any,
    token: str,
    data: Dict[str, Any],
    ttl_seconds: int = TOKEN_TTL_SECONDS,
):
    """Stores import batch data in Redis with TTL, falling back to in-memory store."""
    if redis is not None:
        try:
            await cache_set_json(redis, f"{TOKEN_KEY_PREFIX}{token}", data, ttl_seconds=ttl_seconds)
            return
        except Exception as e:
            logger.warning(f"Redis token store failed, falling back to in-memory store: {e}")

    _in_memory_token_store[token] = {
        "data": data,
        "expires_at": datetime.utcnow().timestamp() + ttl_seconds,
    }


async def retrieve_and_consume_import_token(
    redis: Any,
    token: str,
) -> Optional[Dict[str, Any]]:
    """
    Atomically retrieves and consumes (deletes) the single-use import batch data
    using a Redis Lua GETDEL script to prevent replay/race attacks.
    """
    if redis is not None:
        try:
            key = f"{TOKEN_KEY_PREFIX}{token}"
            raw = await redis.eval(LUA_ATOMIC_GETDEL, 1, key)
            if raw:
                return orjson.loads(raw)
        except Exception as e:
            logger.warning(f"Redis atomic token retrieval failed, checking in-memory store: {e}")

    entry = _in_memory_token_store.pop(token, None)
    if entry:
        if datetime.utcnow().timestamp() <= entry.get("expires_at", 0):
            return entry.get("data")
    return None


# Header mappings (normalized -> canonical field)
HEADER_ALIASES: Dict[str, str] = {
    # Project ID
    "project_id": "project_id",
    "projectid": "project_id",
    "project": "project_id",
    # Material Master ID
    "material_master_id": "material_master_id",
    "materialmasterid": "material_master_id",
    "material_id": "material_master_id",
    "materialid": "material_master_id",
    "master_id": "material_master_id",
    "masterid": "material_master_id",
    # Supplier ID
    "supplier_id": "supplier_id",
    "supplierid": "supplier_id",
    "supplier": "supplier_id",
    # Purchase Rate
    "purchase_rate": "purchase_rate",
    "purchaserate": "purchase_rate",
    "rate": "purchase_rate",
    "unit_cost": "purchase_rate",
    "unitcost": "purchase_rate",
    "price": "purchase_rate",
    # Rate Type
    "rate_type": "rate_type",
    "ratetype": "rate_type",
    "type": "rate_type",
    # Quantity Purchased
    "quantity_purchased": "quantity_purchased",
    "quantitypurchased": "quantity_purchased",
    "quantity": "quantity_purchased",
    "qty": "quantity_purchased",
    "qty_purchased": "quantity_purchased",
    # Payment Given
    "payment_given": "payment_given",
    "paymentgiven": "payment_given",
    "payment": "payment_given",
    "amount_paid": "payment_given",
    "amountpaid": "payment_given",
    "paid_amount": "payment_given",
    # Minimum Stock Level
    "minimum_stock_level": "minimum_stock_level",
    "minimumstocklevel": "minimum_stock_level",
    "min_stock": "minimum_stock_level",
    "minstock": "minimum_stock_level",
    "minimum_stock": "minimum_stock_level",
}

REQUIRED_CANONICAL_HEADERS = {"material_master_id", "supplier_id", "purchase_rate"}


def normalize_header(header: str) -> str:
    """Normalize raw header string to lowercase letters, numbers, and underscores."""
    if not header:
        return ""
    clean = re.sub(r"[^a-zA-Z0-9_]", "", header.strip().lower().replace(" ", "_"))
    return HEADER_ALIASES.get(clean, clean)


# ==============================================================================
# 1. TEMPLATE GENERATION
# ==============================================================================


def generate_material_upload_template(file_format: str = "xlsx") -> Tuple[bytes, str, str]:
    """
    Generates a production-ready downloadable template for bulk material upload.
    Returns (content_bytes, filename, media_type).
    """
    if file_format.lower() == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Project ID",
            "Material Master ID",
            "Supplier ID",
            "Purchase Rate",
            "Rate Type",
            "Quantity Purchased",
            "Payment Given",
            "Minimum Stock Level",
        ])
        writer.writerow([1, 101, 201, 450.00, "FIXED", 100.000, 20000.00, 10.000])
        writer.writerow([1, 102, 202, 120.50, "PER_BAG", 50.000, 0.00, 5.000])
        content = output.getvalue().encode("utf-8")
        return (
            content,
            "material_bulk_upload_template.csv",
            "text/csv; charset=utf-8",
        )

    # Default to Excel (.xlsx)
    wb = Workbook()

    # Sheet 1: Materials (Data Entry)
    ws = wb.active
    ws.title = "Materials"

    headers = [
        "Project ID",
        "Material Master ID",
        "Supplier ID",
        "Purchase Rate",
        "Rate Type",
        "Quantity Purchased",
        "Payment Given",
        "Minimum Stock Level",
    ]

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="0B2B5C", end_color="0B2B5C", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    border_side = Side(style="thin", color="CCCCCC")
    cell_border = Border(left=border_side, right=border_side, top=border_side, bottom=border_side)

    ws.append(headers)
    ws.row_dimensions[1].height = 28

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align
        cell.border = cell_border

    sample_rows = [
        [1, 101, 201, 450.00, "FIXED", 100.000, 20000.00, 10.000],
        [1, 102, 202, 120.50, "PER_BAG", 50.000, 0.00, 5.000],
    ]

    for row_data in sample_rows:
        ws.append(row_data)

    for row_idx in range(2, len(sample_rows) + 2):
        ws.row_dimensions[row_idx].height = 20
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.alignment = Alignment(horizontal="right" if col_idx in (1, 2, 3, 4, 6, 7, 8) else "center", vertical="center")
            cell.border = cell_border

    # Auto-adjust column widths
    column_widths = [14, 22, 16, 16, 14, 20, 16, 20]
    for idx, width in enumerate(column_widths, start=1):
        col_letter = get_column_letter(idx)
        ws.column_dimensions[col_letter].width = width

    # Sheet 2: Instructions & Reference
    ws_guide = wb.create_sheet(title="Instructions")
    guide_headers = ["Column Name", "Required", "Data Type", "Allowed Values / Format", "Description & Constraints"]
    ws_guide.append(guide_headers)
    ws_guide.row_dimensions[1].height = 24

    guide_header_fill = PatternFill(start_color="34495E", end_color="34495E", fill_type="solid")
    for col_idx in range(1, len(guide_headers) + 1):
        cell = ws_guide.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = guide_header_fill
        cell.alignment = center_align
        cell.border = cell_border

    guide_data = [
        ["Project ID", "YES*", "Integer", "Existing active project ID", "Mandatory in row unless Project ID is specified at upload."],
        ["Material Master ID", "YES", "Integer", "Existing material master ID", "Must exist in Master Data catalog and be active."],
        ["Supplier ID", "YES", "Integer", "Existing supplier ID", "Must exist in Suppliers table and not be deleted."],
        ["Purchase Rate", "YES", "Decimal (> 0)", "e.g. 450.00", "Must be greater than 0 with up to 2 decimal places."],
        ["Rate Type", "YES", "Text Enum", "FIXED, PER_UNIT, PER_KG, PER_TON, PER_BAG", "Default is FIXED if left blank."],
        ["Quantity Purchased", "NO", "Decimal (>= 0)", "e.g. 100.000", "Initial purchase quantity. Default is 0.000."],
        ["Payment Given", "NO", "Decimal (>= 0)", "e.g. 20000.00", "Cannot exceed Total Amount (Quantity Purchased * Purchase Rate)."],
        ["Minimum Stock Level", "NO", "Decimal (>= 0)", "e.g. 10.000", "Threshold for low-stock alerts. Default is 0.000."],
    ]

    for row_info in guide_data:
        ws_guide.append(row_info)

    guide_widths = [22, 12, 16, 36, 60]
    for idx, width in enumerate(guide_widths, start=1):
        ws_guide.column_dimensions[get_column_letter(idx)].width = width

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    return (
        stream.getvalue(),
        "material_bulk_upload_template.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ==============================================================================
# 2. FILE PARSING
# ==============================================================================


def parse_material_file(content: bytes, filename: str) -> List[Dict[str, Any]]:
    """
    Parses uploaded Excel or CSV content into a list of normalized row dictionaries.
    Performs file size, extension, header, and max row checks.
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Only .xlsx and .csv are supported.",
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds maximum allowed size of {MAX_FILE_SIZE // (1024 * 1024)}MB.",
        )

    rows_data: List[Dict[str, Any]] = []

    if ext == ".xlsx":
        try:
            wb = load_workbook(filename=io.BytesIO(content), data_only=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read Excel workbook: {str(e)}")

        ws = wb["Materials"] if "Materials" in wb.sheetnames else wb.active
        raw_rows = list(ws.iter_rows(values_only=True))

        if not raw_rows or len(raw_rows) < 2:
            raise HTTPException(
                status_code=400,
                detail="Excel file contains no data rows. Please use the standard template.",
            )

        header_row = raw_rows[0]
        header_map: Dict[int, str] = {}
        seen_headers: Set[str] = set()

        for col_idx, h in enumerate(header_row):
            if h is None:
                continue
            canonical = normalize_header(str(h))
            if canonical in seen_headers:
                raise HTTPException(
                    status_code=400,
                    detail=f"Duplicate column header detected in Excel: '{h}'",
                )
            if canonical:
                seen_headers.add(canonical)
                header_map[col_idx] = canonical

        # Check required columns presence
        missing_required = REQUIRED_CANONICAL_HEADERS - seen_headers
        if missing_required:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required columns in Excel: {', '.join(sorted(missing_required))}",
            )

        _numeric_cols = {
            "project_id",
            "material_master_id",
            "supplier_id",
            "purchase_rate",
            "quantity_purchased",
            "payment_given",
        }
        _numeric_indices = {idx for idx, can in header_map.items() if can in _numeric_cols}
        _sheet = "Materials" if "Materials" in wb.sheetnames else None
        _formula_flags = scan_xlsx_formula_rows(content, sheet_name=_sheet, numeric_cols=_numeric_indices)
        if _formula_flags:
            first_err = _formula_flags[min(_formula_flags)]
            raise HTTPException(status_code=400, detail=first_err)

        for row_idx, r in enumerate(raw_rows[1:], start=2):
            if not r or all(c is None or str(c).strip() == "" for c in r):
                continue  # Skip empty rows

            row_dict: Dict[str, Any] = {"_row_number": row_idx}
            for col_idx, cell_value in enumerate(r):
                canonical = header_map.get(col_idx)
                if is_formula_injection_risk(cell_value, text_only=(canonical not in _numeric_cols)):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Row {row_idx}: Potentially dangerous spreadsheet formula or macro payload rejected.",
                    )
                if canonical:
                    row_dict[canonical] = cell_value
            rows_data.append(row_dict)

    elif ext == ".csv":
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                decoded = content.decode("latin-1")
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid CSV encoding. Please use UTF-8.")

        reader = csv.reader(io.StringIO(decoded))
        raw_rows = list(reader)

        if not raw_rows or len(raw_rows) < 2:
            raise HTTPException(
                status_code=400,
                detail="CSV file contains no data rows. Please use the standard template.",
            )

        header_row = raw_rows[0]
        header_map: Dict[int, str] = {}
        seen_headers: Set[str] = set()

        for col_idx, h in enumerate(header_row):
            if not h or not h.strip():
                continue
            canonical = normalize_header(h)
            if canonical in seen_headers:
                raise HTTPException(
                    status_code=400,
                    detail=f"Duplicate column header detected in CSV: '{h.strip()}'",
                )
            if canonical:
                seen_headers.add(canonical)
                header_map[col_idx] = canonical

        missing_required = REQUIRED_CANONICAL_HEADERS - seen_headers
        if missing_required:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required columns in CSV: {', '.join(sorted(missing_required))}",
            )

        _numeric_cols_csv = {
            "project_id",
            "material_master_id",
            "supplier_id",
            "purchase_rate",
            "quantity_purchased",
            "payment_given",
        }
        for row_idx, r in enumerate(raw_rows[1:], start=2):
            if not r or all(c is None or str(c).strip() == "" for c in r):
                continue

            row_dict = {"_row_number": row_idx}
            for col_idx, cell_value in enumerate(r):
                canonical = header_map.get(col_idx)
                if is_formula_injection_risk(cell_value, text_only=(canonical not in _numeric_cols_csv)):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Row {row_idx}: Potentially dangerous spreadsheet formula or macro payload rejected.",
                    )
                if canonical:
                    row_dict[canonical] = cell_value
            rows_data.append(row_dict)

    if not rows_data:
        raise HTTPException(status_code=400, detail="File contains no valid data rows to process.")

    if len(rows_data) > MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds maximum allowed limit of {MAX_ROWS} rows. Please split the upload.",
        )

    return rows_data


# ==============================================================================
# 3. ROW-LEVEL VALIDATION ENGINE
# ==============================================================================


async def validate_bulk_material_rows(
    db: AsyncSession,
    current_user: User,
    rows: List[Dict[str, Any]],
    default_project_id: Optional[int] = None,
) -> Tuple[List[BulkMaterialRowPreview], List[BulkMaterialError]]:
    """
    Validates all parsed rows against database references, business rules,
    and intra-batch uniqueness constraints without writing to database.
    Returns (row_previews, errors).
    """
    errors: List[BulkMaterialError] = []
    previews: List[BulkMaterialRowPreview] = []

    # 1. Extract distinct foreign key IDs for batch resolution
    project_ids: Set[int] = set()
    master_ids: Set[int] = set()
    supplier_ids: Set[int] = set()

    if default_project_id:
        project_ids.add(default_project_id)

    for r in rows:
        raw_pid = r.get("project_id") or default_project_id
        if raw_pid is not None:
            try:
                pid = int(str(raw_pid).strip())
                if pid > 0:
                    project_ids.add(pid)
            except (ValueError, TypeError):
                pass

        raw_mid = r.get("material_master_id")
        if raw_mid is not None:
            try:
                mid = int(str(raw_mid).strip())
                if mid > 0:
                    master_ids.add(mid)
            except (ValueError, TypeError):
                pass

        raw_sid = r.get("supplier_id")
        if raw_sid is not None:
            try:
                sid = int(str(raw_sid).strip())
                if sid > 0:
                    supplier_ids.add(sid)
            except (ValueError, TypeError):
                pass

    # 2. Batch fetch referenced entities
    projects_map: Dict[int, Project] = {}
    if project_ids:
        proj_result = await db.execute(
            select(Project).where(Project.id.in_(project_ids))
        )
        for p in proj_result.scalars().all():
            projects_map[p.id] = p

    masters_map: Dict[int, MaterialMaster] = {}
    if master_ids:
        master_result = await db.execute(
            select(MaterialMaster).where(MaterialMaster.id.in_(master_ids))
        )
        for m in master_result.scalars().all():
            masters_map[m.id] = m

    # Fetch units for masters
    unit_ids = {m.unit_id for m in masters_map.values() if m.unit_id}
    units_map: Dict[int, str] = {}
    if unit_ids:
        unit_result = await db.execute(select(Unit).where(Unit.id.in_(unit_ids)))
        for u in unit_result.scalars().all():
            units_map[u.id] = u.name

    suppliers_map: Dict[int, Supplier] = {}
    if supplier_ids:
        sup_result = await db.execute(
            select(Supplier).where(Supplier.id.in_(supplier_ids))
        )
        for s in sup_result.scalars().all():
            suppliers_map[s.id] = s

    # 3. Query existing materials for duplicate detection
    existing_combinations: Set[Tuple[int, int, int]] = set()
    if project_ids and master_ids and supplier_ids:
        mat_result = await db.execute(
            select(
                Material.project_id,
                Material.material_master_id,
                Material.supplier_id,
            ).where(
                Material.project_id.in_(project_ids),
                Material.material_master_id.in_(master_ids),
                Material.supplier_id.in_(supplier_ids),
                Material.is_deleted == False,
            )
        )
        for pid, mid, sid in mat_result.all():
            existing_combinations.add((pid, mid, sid))

    # 4. Intra-file duplicate tracking
    seen_in_batch: Set[Tuple[int, int, int]] = set()

    # Allowed RateType values
    valid_rate_types = {rt.value for rt in RateType}

    # 5. Row-by-row comprehensive validation
    for r in rows:
        row_num = r.get("_row_number", 0)
        row_errors: List[str] = []

        # --- A. Project ID Validation ---
        raw_proj = r.get("project_id") or default_project_id
        project_id = None
        project_name = None

        if raw_proj is None or str(raw_proj).strip() == "":
            row_errors.append("Project ID is required.")
            errors.append(
                BulkMaterialError(
                    row=row_num,
                    column="Project ID",
                    message="Project ID is required.",
                    value=None,
                )
            )
        else:
            try:
                project_id = int(str(raw_proj).strip())
                if project_id <= 0:
                    row_errors.append("Project ID must be greater than 0.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Project ID",
                            message="Project ID must be greater than 0.",
                            value=str(raw_proj),
                        )
                    )
            except (ValueError, TypeError):
                row_errors.append(f"Invalid Project ID '{raw_proj}': must be an integer.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Project ID",
                        message=f"Invalid Project ID '{raw_proj}': must be an integer.",
                        value=str(raw_proj),
                    )
                )

        if project_id is not None and project_id > 0:
            if project_id not in projects_map:
                row_errors.append(f"Project with ID {project_id} does not exist.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Project ID",
                        message=f"Project with ID {project_id} does not exist.",
                        value=str(project_id),
                    )
                )
            else:
                project_obj = projects_map[project_id]
                project_name = project_obj.project_name

                # Project authorization & tenant isolation check
                try:
                    await assert_project_access(db, project_id=project_id, current_user=current_user)
                except Exception:
                    row_errors.append(f"Access denied to Project ID {project_id}.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Project ID",
                            message=f"Access denied to Project ID {project_id}.",
                            value=str(project_id),
                        )
                    )

                if current_user.role != UserRole.ADMIN.value and project_id not in (
                    current_user.allowed_projects or []
                ):
                    row_errors.append(f"User is not assigned to Project ID {project_id}.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Project ID",
                            message=f"User is not assigned to Project ID {project_id}.",
                            value=str(project_id),
                        )
                    )

        # --- B. Material Master ID Validation ---
        raw_mid = r.get("material_master_id")
        material_master_id = None
        material_name = None
        category = None
        unit_name = None

        if raw_mid is None or str(raw_mid).strip() == "":
            row_errors.append("Material Master ID is required.")
            errors.append(
                BulkMaterialError(
                    row=row_num,
                    column="Material Master ID",
                    message="Material Master ID is required.",
                    value=None,
                )
            )
        else:
            try:
                material_master_id = int(str(raw_mid).strip())
                if material_master_id <= 0:
                    row_errors.append("Material Master ID must be greater than 0.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Material Master ID",
                            message="Material Master ID must be greater than 0.",
                            value=str(raw_mid),
                        )
                    )
            except (ValueError, TypeError):
                row_errors.append(f"Invalid Material Master ID '{raw_mid}': must be an integer.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Material Master ID",
                        message=f"Invalid Material Master ID '{raw_mid}': must be an integer.",
                        value=str(raw_mid),
                    )
                )

        if material_master_id is not None and material_master_id > 0:
            if material_master_id not in masters_map:
                row_errors.append(f"Material Master with ID {material_master_id} not found.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Material Master ID",
                        message=f"Material Master with ID {material_master_id} not found.",
                        value=str(material_master_id),
                    )
                )
            else:
                mm = masters_map[material_master_id]
                material_name = mm.name
                category = mm.category or "GENERAL"
                unit_name = units_map.get(mm.unit_id, "")
                if getattr(mm, "is_deleted", False) or getattr(mm, "is_active", True) is False:
                    row_errors.append(f"Material Master '{mm.name}' is inactive or deleted.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Material Master ID",
                            message=f"Material Master '{mm.name}' is inactive or deleted.",
                            value=str(material_master_id),
                        )
                    )
                if (
                    current_user.company_id is not None
                    and mm.company_id is not None
                    and mm.company_id != current_user.company_id
                ):
                    row_errors.append("Material Master belongs to a different company.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Material Master ID",
                            message="Material Master belongs to a different company.",
                            value=str(material_master_id),
                        )
                    )

        # --- C. Supplier ID Validation ---
        raw_sid = r.get("supplier_id")
        supplier_id = None
        supplier_name = None

        if raw_sid is None or str(raw_sid).strip() == "":
            row_errors.append("Supplier ID is required.")
            errors.append(
                BulkMaterialError(
                    row=row_num,
                    column="Supplier ID",
                    message="Supplier ID is required.",
                    value=None,
                )
            )
        else:
            try:
                supplier_id = int(str(raw_sid).strip())
                if supplier_id <= 0:
                    row_errors.append("Supplier ID must be greater than 0.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Supplier ID",
                            message="Supplier ID must be greater than 0.",
                            value=str(raw_sid),
                        )
                    )
            except (ValueError, TypeError):
                row_errors.append(f"Invalid Supplier ID '{raw_sid}': must be an integer.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Supplier ID",
                        message=f"Invalid Supplier ID '{raw_sid}': must be an integer.",
                        value=str(raw_sid),
                    )
                )

        if supplier_id is not None and supplier_id > 0:
            if supplier_id not in suppliers_map:
                row_errors.append(f"Supplier with ID {supplier_id} not found.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Supplier ID",
                        message=f"Supplier with ID {supplier_id} not found.",
                        value=str(supplier_id),
                    )
                )
            else:
                sup = suppliers_map[supplier_id]
                supplier_name = sup.supplier_name
                if sup.is_deleted:
                    row_errors.append(f"Supplier '{sup.supplier_name}' is deleted.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Supplier ID",
                            message=f"Supplier '{sup.supplier_name}' is deleted.",
                            value=str(supplier_id),
                        )
                    )
                if (
                    current_user.company_id is not None
                    and sup.company_id is not None
                    and sup.company_id != current_user.company_id
                ):
                    row_errors.append("Supplier belongs to a different company.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Supplier ID",
                            message="Supplier belongs to a different company.",
                            value=str(supplier_id),
                        )
                    )

        # --- D. Purchase Rate Validation ---
        raw_rate = r.get("purchase_rate")
        purchase_rate = Decimal("0")
        if raw_rate is None or str(raw_rate).strip() == "":
            row_errors.append("Purchase Rate is required.")
            errors.append(
                BulkMaterialError(
                    row=row_num,
                    column="Purchase Rate",
                    message="Purchase Rate is required.",
                    value=None,
                )
            )
        else:
            try:
                purchase_rate = Decimal(str(raw_rate).strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if purchase_rate <= Decimal("0"):
                    row_errors.append("Purchase Rate must be greater than 0.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Purchase Rate",
                            message="Purchase Rate must be greater than 0.",
                            value=str(raw_rate),
                        )
                    )
            except (InvalidOperation, ValueError, TypeError):
                row_errors.append(f"Invalid Purchase Rate '{raw_rate}': must be a valid numeric amount.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Purchase Rate",
                        message=f"Invalid Purchase Rate '{raw_rate}': must be a valid numeric amount.",
                        value=str(raw_rate),
                    )
                )

        # --- E. Rate Type Validation ---
        raw_rt = r.get("rate_type")
        rate_type_val = RateType.FIXED
        if raw_rt is None or str(raw_rt).strip() == "":
            rate_type_val = RateType.FIXED
        else:
            rt_str = str(raw_rt).strip().upper()
            if rt_str in valid_rate_types:
                rate_type_val = RateType(rt_str)
            else:
                row_errors.append(
                    f"Invalid Rate Type '{raw_rt}'. Allowed: {', '.join(sorted(valid_rate_types))}."
                )
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Rate Type",
                        message=f"Invalid Rate Type '{raw_rt}'. Allowed: {', '.join(sorted(valid_rate_types))}.",
                        value=str(raw_rt),
                    )
                )

        # --- F. Quantity Purchased Validation ---
        raw_qty = r.get("quantity_purchased")
        quantity_purchased = Decimal("0.000")
        if raw_qty is not None and str(raw_qty).strip() != "":
            try:
                quantity_purchased = Decimal(str(raw_qty).strip()).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                if quantity_purchased < Decimal("0"):
                    row_errors.append("Quantity Purchased cannot be negative.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Quantity Purchased",
                            message="Quantity Purchased cannot be negative.",
                            value=str(raw_qty),
                        )
                    )
            except (InvalidOperation, ValueError, TypeError):
                row_errors.append(f"Invalid Quantity Purchased '{raw_qty}': must be a numeric value.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Quantity Purchased",
                        message=f"Invalid Quantity Purchased '{raw_qty}': must be a numeric value.",
                        value=str(raw_qty),
                    )
                )

        # --- G. Payment Given Validation & Limit Check ---
        raw_payment = r.get("payment_given")
        payment_given = Decimal("0.00")
        if raw_payment is not None and str(raw_payment).strip() != "":
            try:
                payment_given = Decimal(str(raw_payment).strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if payment_given < Decimal("0"):
                    row_errors.append("Payment Given cannot be negative.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Payment Given",
                            message="Payment Given cannot be negative.",
                            value=str(raw_payment),
                        )
                    )
            except (InvalidOperation, ValueError, TypeError):
                row_errors.append(f"Invalid Payment Given '{raw_payment}': must be a valid numeric amount.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Payment Given",
                        message=f"Invalid Payment Given '{raw_payment}': must be a valid numeric amount.",
                        value=str(raw_payment),
                    )
                )

        # Total Amount calculation
        total_amount = (quantity_purchased * purchase_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if payment_given > total_amount:
            msg = f"Payment Given ({payment_given}) cannot exceed Total Amount ({total_amount})."
            row_errors.append(msg)
            errors.append(
                BulkMaterialError(
                    row=row_num,
                    column="Payment Given",
                    message=msg,
                    value=str(payment_given),
                )
            )

        # --- H. Minimum Stock Level Validation ---
        raw_min_stock = r.get("minimum_stock_level")
        min_stock = Decimal("0.000")
        if raw_min_stock is not None and str(raw_min_stock).strip() != "":
            try:
                min_stock = Decimal(str(raw_min_stock).strip()).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                if min_stock < Decimal("0"):
                    row_errors.append("Minimum Stock Level cannot be negative.")
                    errors.append(
                        BulkMaterialError(
                            row=row_num,
                            column="Minimum Stock Level",
                            message="Minimum Stock Level cannot be negative.",
                            value=str(raw_min_stock),
                        )
                    )
            except (InvalidOperation, ValueError, TypeError):
                row_errors.append(f"Invalid Minimum Stock Level '{raw_min_stock}': must be numeric.")
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Minimum Stock Level",
                        message=f"Invalid Minimum Stock Level '{raw_min_stock}': must be numeric.",
                        value=str(raw_min_stock),
                    )
                )

        # --- I. Duplicate Detection ---
        if project_id and material_master_id and supplier_id:
            comb_key = (project_id, material_master_id, supplier_id)

            # Intra-batch duplicate check
            if comb_key in seen_in_batch:
                msg = f"Duplicate material row in file: Project {project_id}, Master {material_master_id}, Supplier {supplier_id}."
                row_errors.append(msg)
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Duplicate In File",
                        message=msg,
                        value=f"({project_id}, {material_master_id}, {supplier_id})",
                    )
                )
            else:
                seen_in_batch.add(comb_key)

            # Database duplicate check
            if comb_key in existing_combinations:
                msg = f"Material already exists in Project {project_id} for Supplier {supplier_id}."
                row_errors.append(msg)
                errors.append(
                    BulkMaterialError(
                        row=row_num,
                        column="Database Duplicate",
                        message=msg,
                        value=f"({project_id}, {material_master_id}, {supplier_id})",
                    )
                )

        preview_status = "ERROR" if row_errors else "VALID"
        previews.append(
            BulkMaterialRowPreview(
                row_number=row_num,
                project_id=project_id or 0,
                project_name=project_name,
                material_master_id=material_master_id or 0,
                material_name=material_name,
                unit_name=unit_name,
                category=category,
                supplier_id=supplier_id or 0,
                supplier_name=supplier_name,
                purchase_rate=purchase_rate,
                rate_type=rate_type_val,
                quantity_purchased=quantity_purchased,
                payment_given=payment_given,
                minimum_stock_level=min_stock,
                total_amount=total_amount,
                status=preview_status,
                errors=row_errors,
            )
        )

    return previews, errors


# ==============================================================================
# 4. ATOMIC DATABASE COMMIT ENGINE
# ==============================================================================


async def execute_bulk_material_commit(
    db: AsyncSession,
    current_user: User,
    valid_items: List[Dict[str, Any]],
    redis: Any = None,
) -> BulkMaterialCommitResponse:
    """
    Executes atomic database insertion for a batch of validated material rows.
    Creates Material, MaterialTransaction, and MaterialLedger entries.
    Rolls back entirely on any failure.
    Invalidates Redis cache on success.
    """
    if not valid_items:
        raise HTTPException(status_code=400, detail="No items to import.")

    # 1. Re-check for database duplicates within the active transaction lock
    tuples_to_check = [
        (it["project_id"], it["material_master_id"], it["supplier_id"])
        for it in valid_items
    ]

    p_ids = {t[0] for t in tuples_to_check}
    m_ids = {t[1] for t in tuples_to_check}
    s_ids = {t[2] for t in tuples_to_check}

    concurrent_check = await db.execute(
        select(Material.project_id, Material.material_master_id, Material.supplier_id).where(
            Material.project_id.in_(p_ids),
            Material.material_master_id.in_(m_ids),
            Material.supplier_id.in_(s_ids),
            Material.is_deleted == False,
        )
    )
    existing_in_db = set(concurrent_check.all())
    for t in tuples_to_check:
        if t in existing_in_db:
            raise HTTPException(
                status_code=400,
                detail=f"Concurrent conflict: Material already exists for project {t[0]}, master {t[1]}, supplier {t[2]}.",
            )

    # 2. Atomic Database Sequence Allocation (race-condition free)
    allocated_codes = await allocate_material_codes(
        db=db,
        count=len(valid_items),
        prefix="MAT",
        padding=3,
    )

    imported_materials: List[BulkMaterialImportedItem] = []

    try:
        # Preload master data to fill unit_id, category, material_name
        masters_res = await db.execute(
            select(MaterialMaster).where(MaterialMaster.id.in_(m_ids))
        )
        masters_lookup = {mm.id: mm for mm in masters_res.scalars().all()}

        for idx, item in enumerate(valid_items):
            code = allocated_codes[idx]

            mm = masters_lookup.get(item["material_master_id"])
            if not mm:
                raise HTTPException(
                    status_code=400,
                    detail=f"Material Master {item['material_master_id']} disappeared during commit.",
                )

            qty = Decimal(str(item["quantity_purchased"]))
            rate = Decimal(str(item["purchase_rate"]))
            payment = Decimal(str(item["payment_given"]))
            total_amt = (qty * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            min_stock = Decimal(str(item["minimum_stock_level"]))

            remaining_stock = qty
            payment_pending = max(total_amt - payment, Decimal("0.00"))
            advance_amount = max(payment - total_amt, Decimal("0.00"))

            obj = Material(
                material_code=code,
                project_id=item["project_id"],
                material_master_id=item["material_master_id"],
                supplier_id=item["supplier_id"],
                material_name=mm.name,
                category=mm.category or "GENERAL",
                unit_id=mm.unit_id,
                rate_type=item["rate_type"],
                purchase_rate=rate,
                quantity_purchased=qty,
                quantity_used=Decimal("0.000"),
                remaining_stock=remaining_stock,
                payment_given=payment,
                payment_pending=payment_pending,
                advance_amount=advance_amount,
                total_amount=total_amt,
                minimum_stock_level=min_stock,
                is_deleted=False,
            )

            db.add(obj)
            await db.flush()

            # Create opening stock transaction & ledger entry if quantity > 0
            if qty > Decimal("0"):
                ref = f"INIT-{uuid.uuid4().hex[:8]}"
                tx = MaterialTransaction(
                    material_id=obj.id,
                    type=DBTransactionType.PURCHASE,
                    quantity=qty,
                    rate=rate,
                    total_amount=total_amt,
                    amount_paid=payment,
                    payment_pending=payment_pending,
                    issue_type=IssueType.PURCHASE,
                    project_id=obj.project_id,
                    remarks="Initial material entry (bulk upload)",
                    reference_id=ref,
                )
                ledger = MaterialLedger(
                    material_id=obj.id,
                    type=DBTransactionType.PURCHASE,
                    quantity=qty,
                    rate=rate,
                    total_amount=total_amt,
                    amount_paid=payment,
                    payment_pending=payment_pending,
                    project_id=obj.project_id,
                    remarks="Initial material entry (bulk upload)",
                    reference_id=ref,
                )
                db.add(tx)
                db.add(ledger)

                # Initialize StockBatch and ProjectMaterialValuation to prevent stock divergence
                if obj.material_master_id:
                    batch = StockBatch(
                        batch_number=f"BATCH-INIT-{obj.id}",
                        project_id=obj.project_id,
                        material_master_id=obj.material_master_id,
                        supplier_id=obj.supplier_id,
                        initial_quantity=qty,
                        current_balance=qty,
                        purchase_rate=rate,
                        receipt_date=datetime.utcnow(),
                        batch_source="OPENING_BALANCE",
                        status="ACTIVE",
                    )
                    db.add(batch)

                    stmt_val = (
                        select(ProjectMaterialValuation)
                        .where(
                            and_(
                                ProjectMaterialValuation.project_id == obj.project_id,
                                ProjectMaterialValuation.material_master_id == obj.material_master_id,
                            )
                        )
                        .with_for_update()
                    )
                    val = (await db.execute(stmt_val)).scalar_one_or_none()
                    if not val:
                        val = ProjectMaterialValuation(
                            project_id=obj.project_id,
                            material_master_id=obj.material_master_id,
                            total_purchased_quantity=qty,
                            total_purchased_amount=total_amt,
                            current_moving_weighted_average_rate=rate,
                        )
                        db.add(val)
                    else:
                        new_qty = val.total_purchased_quantity + qty
                        new_amt = val.total_purchased_amount + total_amt
                        new_mwa = (new_amt / new_qty).quantize(Decimal("0.01")) if new_qty > Decimal("0") else rate
                        val.total_purchased_quantity = new_qty
                        val.total_purchased_amount = new_amt
                        val.current_moving_weighted_average_rate = new_mwa
                        val.last_recalculated_at = datetime.utcnow()

            imported_materials.append(
                BulkMaterialImportedItem(
                    id=obj.id,
                    material_code=obj.material_code,
                    material_name=obj.material_name,
                    project_id=obj.project_id,
                    supplier_id=obj.supplier_id,
                    material_master_id=obj.material_master_id,
                    purchase_rate=obj.purchase_rate,
                    quantity_purchased=obj.quantity_purchased,
                    total_amount=obj.total_amount,
                    payment_given=obj.payment_given,
                    remaining_stock=obj.remaining_stock,
                )
            )

        # Commit entire transaction atomically
        await db.commit()

        # Invalidate Redis material cache version
        if redis:
            try:
                await bump_cache_version(redis, VERSION_KEY)
            except Exception as e:
                logger.warning(f"Failed to bump material cache version: {e}")

    except Exception as e:
        await db.rollback()
        logger.exception(f"Bulk material import failed and rolled back: {e}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=400,
            detail=f"Database transaction failed during bulk material import: {str(e)}",
        )

    return BulkMaterialCommitResponse(
        imported_count=len(imported_materials),
        materials=imported_materials,
        message=f"Successfully imported {len(imported_materials)} materials.",
    )
