from dataclasses import dataclass
from typing import Optional


@dataclass
class AppError(Exception):
    status_code: int
    message: str

    def __str__(self):
        return self.message


class NotFoundError(AppError):
    def __init__(self, message: str = "Not found"):
        super().__init__(status_code=404, message=message)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflict"):
        super().__init__(status_code=409, message=message)


class PermissionDeniedError(AppError):
    def __init__(self, message: str = "Permission denied"):
        super().__init__(status_code=403, message=message)


class ValidationError(AppError):
    def __init__(self, message: str = "Validation error"):
        super().__init__(status_code=422, message=message)


class BadRequestError(AppError):
    def __init__(self, message: str = "Bad request"):
        super().__init__(status_code=400, message=message)


class AuthenticationError(AppError):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(status_code=401, message=message)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Access forbidden"):
        super().__init__(status_code=403, message=message)


class AlreadyExistsError(AppError):
    def __init__(self, message: str = "Resource already exists"):
        super().__init__(status_code=409, message=message)


class InvalidStateError(AppError):
    def __init__(self, message: str = "Invalid state for this operation"):
        super().__init__(status_code=400, message=message)


class RateLimitError(AppError):
    def __init__(self, message: str = "Too many requests"):
        super().__init__(status_code=429, message=message)


class ServiceUnavailableError(AppError):
    def __init__(self, message: str = "Service temporarily unavailable"):
        super().__init__(status_code=503, message=message)


class DataIntegrityError(AppError):
    def __init__(self, message: str = "Data integrity issue"):
        super().__init__(status_code=500, message=message)


import re as _re


def is_formula_injection_risk(value, text_only: bool = False) -> bool:
    """
    Detects spreadsheet formula/macro injection payloads in uploaded cells.
    Allows signed plain numeric literals (e.g. '-5', '-10.50') on numeric cells
    unless `text_only=True`.
    """
    if not isinstance(value, str):
        return False
    stripped = value.lstrip()
    if not stripped:
        return False
    if stripped[0] in ("=", "@", "\t", "\r"):
        return True
    if stripped[0] in ("+", "-"):
        if text_only:
            return True
        if not _re.fullmatch(r"[+-]\d+(?:\.\d+)?", stripped):
            return True
    return False


def scan_xlsx_formula_rows(
    file_bytes: bytes,
    sheet_name: Optional[str] = None,
    numeric_cols: Optional[set] = None,
) -> dict:
    """
    Scans raw (data_only=False) Excel cells so uncached formulas like '=SUM(1,2)' or '=CMD|...'
    are caught even though openpyxl `data_only=True` evaluates them to None.
    Returns {1_based_row_index: error_message}.
    """
    import io
    from openpyxl import load_workbook

    wb_raw = load_workbook(io.BytesIO(file_bytes), data_only=False)
    ws_raw = wb_raw[sheet_name] if (sheet_name and sheet_name in wb_raw.sheetnames) else wb_raw.active
    flagged = {}
    allowed_num = numeric_cols or set()
    for row_idx, row in enumerate(ws_raw.iter_rows(values_only=True), start=1):
        if row_idx == 1 or not row or not any(row):
            continue
        for col_idx, val in enumerate(row):
            if is_formula_injection_risk(val, text_only=(col_idx not in allowed_num)):
                flagged[row_idx] = (
                    f"Row {row_idx}: Potentially dangerous spreadsheet formula or macro payload rejected."
                )
                break
    return flagged


def resolve_min_stock_threshold(material, fallback=None):
    """Returns effective minimum stock threshold, ignoring unset zero defaults."""
    from decimal import Decimal

    fb = Decimal(str(fallback)) if fallback is not None else Decimal("10.000")
    mat_thresh = getattr(material, "minimum_stock_level", None)
    if mat_thresh is not None and Decimal(str(mat_thresh)) > Decimal("0.000"):
        return Decimal(str(mat_thresh))
    master = getattr(material, "material_master", None)
    master_thresh = getattr(master, "minimum_stock_level", None) if master is not None else None
    if master_thresh is not None and Decimal(str(master_thresh)) > Decimal("0.000"):
        return Decimal(str(master_thresh))
    return fb

import math

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance in meters between two points
    on the earth (specified in decimal degrees)
    """
    if None in (lat1, lon1, lat2, lon2):
        return float('inf')

    # Convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])

    # Haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    r = 6371 # Radius of earth in kilometers
    return c * r * 1000 # Convert to meters

def safe_divide(numerator, denominator):
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)

def validate_percentage(value):
    if value < 0:
        return 0.0
    if value > 100:
        return 100.0
    return float(value)