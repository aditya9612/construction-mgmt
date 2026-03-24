from sqlalchemy import or_
from sqlalchemy.sql import Select
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass


# -------------------------
# PAGINATION PARAMS (NEW)
# -------------------------
@dataclass(frozen=True)
class PaginationParams:
    limit: int = 20
    offset: int = 0
    search: Optional[str] = None

    def normalized(self) -> "PaginationParams":
        limit = max(1, min(self.limit, 100))   # max cap
        offset = max(0, self.offset)
        return PaginationParams(limit=limit, offset=offset, search=self.search)


# -------------------------
# TYPE CASTING
# -------------------------
def cast_value(value: Any):
    if not isinstance(value, str):
        return value

    value = value.strip()

    if value.lower() in ["true", "false"]:
        return value.lower() == "true"

    if value.isdigit():
        return int(value)

    try:
        return float(value)
    except ValueError:
        pass

    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        pass

    return value


# -------------------------
# GLOBAL SEARCH
# -------------------------
def apply_global_search(
    query: Select,
    model,
    search: Optional[str],
    search_fields: List[str]
):
    if not search:
        return query

    conditions = []

    for field in search_fields:
        if hasattr(model, field):
            column = getattr(model, field)
            conditions.append(column.ilike(f"%{search}%"))

    if conditions:
        query = query.where(or_(*conditions))

    return query


# -------------------------
# ADVANCED FILTERS
# -------------------------
def apply_dynamic_filters(
    query: Select,
    model,
    params: Optional[Dict[str, Any]],
    allowed_filters: Optional[List[str]] = None,
    alias_map: Optional[Dict[str, str]] = None
):
    if not params:
        return query

    for field, value in params.items():

        if value is None or value == "":
            continue

        # exclude non-filter params
        if field in ["offset", "limit", "sort_by", "order", "search"]:
            continue

        # alias support
        if alias_map and field in alias_map:
            field = alias_map[field]

        operator = "eq"

        if "__" in field:
            field, operator = field.split("__", 1)

        if allowed_filters and field not in allowed_filters:
            continue

        if not hasattr(model, field):
            continue

        column = getattr(model, field)
        value = cast_value(value)

        # -------------------------
        # APPLY OPERATORS
        # -------------------------

        if operator == "eq":
            query = query.where(column == value)

        elif operator == "ne":
            query = query.where(column != value)

        elif operator == "gt":
            query = query.where(column > value)

        elif operator == "lt":
            query = query.where(column < value)

        elif operator == "gte":
            query = query.where(column >= value)

        elif operator == "lte":
            query = query.where(column <= value)

        elif operator == "in":
            values = [cast_value(v) for v in str(value).split(",")]
            query = query.where(column.in_(values))

        elif operator == "between":
            try:
                start, end = str(value).split(",")
                query = query.where(
                    column.between(cast_value(start), cast_value(end))
                )
            except ValueError:
                pass

        elif operator == "like":
            query = query.where(column.ilike(f"%{value}%"))

    return query


# -------------------------
# SORTING
# -------------------------
def apply_sorting(
    query: Select,
    model,
    params: Optional[Dict[str, Any]]
):
    if not params:
        return query

    sort_by = params.get("sort_by", None)
    order = params.get("order", "asc")

    if sort_by and hasattr(model, sort_by):
        column = getattr(model, sort_by)

        if str(order).lower() == "desc":
            query = query.order_by(column.desc())
        else:
            query = query.order_by(column.asc())

    return query


# -------------------------
# PAGINATION (UPDATED)
# -------------------------
def apply_pagination(
    query: Select,
    pagination: PaginationParams
):
    pagination = pagination.normalized()
    return query.offset(pagination.offset).limit(pagination.limit)