from decimal import Decimal
from typing import Optional
from openpyxl import Workbook
from fastapi.responses import FileResponse
import tempfile
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from reportlab.lib import colors

from app.cache.redis import (
    bump_cache_version,
    cache_get_json,
    cache_set_json,
    get_cache_version,
)
from app.core.dependencies import (
    get_current_active_user,
    get_request_redis,
    require_roles,
)
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.boq import BOQ
from app.models.project import Project
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.boq import BOQCreate, BOQOut, BOQUpdate, BOQActualsUpdate
from app.utils.helpers import NotFoundError
from app.core.logger import logger
from app.models.boq import BOQAudit

router = APIRouter(
    prefix="/boq",
    tags=["boq"],
    dependencies=[default_rate_limiter_dependency()],
)

VERSION_KEY = "cache_version:boq"

# ------------------ HELPERS ------------------


def calculate_cost(
    quantity: Decimal, unit_cost: Decimal, actual_cost: Decimal = Decimal(0)
):
    total = quantity * unit_cost
    variance = total - actual_cost
    return total, variance


def validate_cost_inputs(quantity: Decimal, unit_cost: Decimal):
    if quantity <= 0:
        raise ValueError("Quantity must be greater than 0")
    if unit_cost <= 0:
        raise ValueError("Unit cost must be greater than 0")


# ------------------ CREATE ------------------


@router.post("", response_model=BOQOut)
async def create_boq(
    payload: BOQCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(f"Creating BOQ project_id={payload.project_id}")

    project = await db.scalar(select(Project).where(Project.id == payload.project_id))
    if not project:
        logger.warning(f"Project not found project_id={payload.project_id}")
        raise NotFoundError("Project not found")

    try:
        max_version = await db.scalar(
            select(func.max(BOQ.version_no)).where(BOQ.project_id == payload.project_id)
        )
        new_version = (max_version or 0) + 1

        await db.execute(
            update(BOQ)
            .where(BOQ.project_id == payload.project_id, BOQ.is_latest == True)
            .values(is_latest=False)
        )

        data = payload.model_dump(exclude_unset=True)

        quantity = Decimal(str(data.get("quantity", 0)))
        unit_cost = Decimal(str(data.get("unit_cost", 0)))

        validate_cost_inputs(quantity, unit_cost)

        total_cost, variance = calculate_cost(quantity, unit_cost)

        data.update(
            {
                "total_cost": total_cost,
                "actual_quantity": Decimal(0),
                "actual_cost": Decimal(0),
                "variance_cost": variance,
                "version_no": new_version,
                "boq_group_id": new_version,
                "is_latest": True,
            }
        )

        obj = BOQ(**data)
        db.add(obj)
        await db.flush()

        await bump_cache_version(redis, VERSION_KEY)

        logger.info(f"BOQ created id={obj.id} project_id={payload.project_id}")

        return BOQOut.model_validate(obj)

    except Exception:
        logger.exception("BOQ creation failed")
        raise


# ------------------ LIST ------------------


@router.get("", response_model=PaginatedResponse[BOQOut])
async def list_boq(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    category: Optional[str] = None,
    version_no: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)

    cache_key = f"cache:boq:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}:{category}:{version_no}"

    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[BOQOut].model_validate(cached)

    if search:
        logger.info(f"BOQ search query={search}")

    query = select(BOQ)
    count_query = select(func.count()).select_from(BOQ)

    if search:
        like = f"%{search}%"
        query = query.where(BOQ.item_name.ilike(like))
        count_query = count_query.where(BOQ.item_name.ilike(like))

    if status:
        query = query.where(BOQ.status == status)
        count_query = count_query.where(BOQ.status == status)

    if project_id is not None:
        query = query.where(BOQ.project_id == project_id)
        count_query = count_query.where(BOQ.project_id == project_id)

    if category:
        query = query.where(BOQ.category == category)
        count_query = count_query.where(BOQ.category == category)

    if version_no:
        query = query.where(BOQ.version_no == version_no)
        count_query = count_query.where(BOQ.version_no == version_no)
    else:
        query = query.where(BOQ.is_latest == True)
        count_query = count_query.where(BOQ.is_latest == True)

    query = query.order_by(BOQ.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [BOQOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)

    result = {"items": items, "meta": meta.model_dump()}

    await cache_set_json(redis, cache_key, result)

    return PaginatedResponse[BOQOut].model_validate(result)


# ------------------ GET ------------------


@router.get("/{boq_id}", response_model=BOQOut)
async def get_boq(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id, BOQ.status != "Deleted"))

    if obj is None:
        logger.warning(f"BOQ not found id={boq_id}")
        raise NotFoundError("BOQ item not found")

    return BOQOut.model_validate(obj)


# ------------------ UPDATE ------------------


@router.put("/{boq_id}", response_model=BOQOut)
async def update_boq(
    boq_id: int,
    payload: BOQUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(f"Updating BOQ id={boq_id}")

    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id, BOQ.status != "Deleted"))

    if obj is None:
        logger.warning(f"BOQ not found for update id={boq_id}")
        raise NotFoundError("BOQ item not found")

    try:
        data = payload.model_dump(exclude_unset=True)

        for k, v in data.items():
            setattr(obj, k, v)

        quantity = Decimal(str(obj.quantity))
        unit_cost = Decimal(str(obj.unit_cost))

        validate_cost_inputs(quantity, unit_cost)

        total_cost, variance = calculate_cost(
            quantity, unit_cost, obj.actual_cost or Decimal(0)
        )

        obj.total_cost = total_cost
        obj.variance_cost = variance

        await db.flush()
        await bump_cache_version(redis, VERSION_KEY)

        logger.info(f"BOQ updated id={boq_id}")

        return BOQOut.model_validate(obj)

    except Exception:
        logger.exception(f"BOQ update failed id={boq_id}")
        raise


# ------------------ DELETE ------------------


@router.delete("/{boq_id}")
async def delete_boq(
    boq_id: int,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(f"Deleting BOQ id={boq_id}")

    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id, BOQ.status != "Deleted"))

    if obj is None:
        logger.warning(f"BOQ not found for delete id={boq_id}")
        raise NotFoundError("BOQ item not found")

    obj.status = "Deleted"

    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)

    logger.info(f"BOQ soft-deleted id={boq_id}")

    return {
        "message": "BOQ deleted successfully",
        "boq_id": boq_id
    }


# ------------------ ACTUALS ------------------


@router.post("/{boq_id}/actuals", response_model=BOQOut)
async def update_actuals(
    boq_id: int,
    payload: BOQActualsUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating BOQ actuals id={boq_id}")

    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id, BOQ.status != "Deleted"))

    if not obj:
        raise NotFoundError("BOQ not found")

    obj.actual_quantity = Decimal(str(payload.actual_quantity))
    obj.actual_cost = Decimal(str(payload.actual_cost))

    _, variance = calculate_cost(obj.quantity, obj.unit_cost, payload.actual_cost)
    obj.variance_cost = variance

    await db.flush()

    logger.info(f"BOQ actuals updated id={boq_id}")

    return BOQOut.model_validate(obj)


# ------------------ SUMMARY ------------------


@router.get("/summary/{project_id}")
async def boq_summary(
    project_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(func.count(), func.sum(BOQ.total_cost), func.sum(BOQ.actual_cost)).where(
            BOQ.project_id == project_id, BOQ.is_latest == True, BOQ.status != "Deleted"
        )
    )

    total_items, estimated, actual = result.one()

    return {
        "total_items": total_items or 0,
        "estimated": float(estimated or 0),
        "actual": float(actual or 0),
        "difference": float((estimated or 0) - (actual or 0)),
    }


# ------------------ COMPARISON ------------------


@router.get("/comparison/{project_id}")
async def boq_comparison(project_id: int,
                         current_user: User = Depends(get_current_active_user),
                         db: AsyncSession = Depends(get_db_session)):
    rows = (
        (
            await db.execute(
                select(BOQ).where(
                    BOQ.project_id == project_id,
                    BOQ.is_latest == True,
                    BOQ.status != "Deleted",
                )
            )
        )
        .scalars()
        .all()
    )

    return [
        {
            "item_name": r.item_name,
            "estimated": float(r.total_cost),
            "actual": float(r.actual_cost),
            "variance": float(r.variance_cost),
        }
        for r in rows
    ]


# ------------------ REPORT ------------------


@router.get("/{boq_id}/report")
async def boq_report(boq_id: int,
                     current_user: User = Depends(get_current_active_user), 
                     db: AsyncSession = Depends(get_db_session)):
    base = await db.scalar(
        select(BOQ).where(BOQ.id == boq_id, BOQ.status != "Deleted")
    )

    if not base:
        raise NotFoundError("BOQ not found")

    rows = (
        await db.execute(
            select(BOQ).where(
                BOQ.boq_group_id == base.boq_group_id,
                BOQ.status != "Deleted"
            )
        )
    ).scalars().all()

    total_estimated = sum(float(r.total_cost) for r in rows)
    total_actual = sum(float(r.actual_cost) for r in rows)

    return {
        "total_items": len(rows),
        "estimated": total_estimated,
        "actual": total_actual,
        "difference": total_estimated - total_actual,
    }


# ------------------ ALERTS ------------------


@router.get("/{boq_id}/alerts")
async def boq_alerts(boq_id: int,
                     current_user: User = Depends(get_current_active_user),
                     db: AsyncSession = Depends(get_db_session)):
    rows = (
        (
            await db.execute(
                select(BOQ).where(BOQ.boq_group_id == boq_id, BOQ.status != "Deleted")
            )
        )
        .scalars()
        .all()
    )

    alerts = []
    for r in rows:
        if r.actual_cost > r.total_cost:
            alerts.append({"item": r.item_name, "message": "Cost exceeded estimate"})

    return {"alerts": alerts}


@router.get("/{boq_id}/versions")
async def get_versions(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    # Get base BOQ to find project
    base = await db.scalar(select(BOQ).where(BOQ.id == boq_id, BOQ.status != "Deleted"))

    if not base:
        raise NotFoundError("BOQ not found")

    result = await db.execute(
        select(BOQ.version_no)
        .where(BOQ.project_id == base.project_id, BOQ.status != "Deleted")
        .distinct()
        .order_by(BOQ.version_no.desc())
    )

    return {"versions": [v[0] for v in result.fetchall()]}


@router.get("/project/{project_id}", response_model=list[BOQOut])
async def get_boq_by_project(
    project_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (
            await db.execute(
                select(BOQ).where(
                    BOQ.project_id == project_id,
                    BOQ.is_latest == True,
                    BOQ.status != "Deleted",
                )
            )
        )
        .scalars()
        .all()
    )

    return [BOQOut.model_validate(r) for r in rows]


# ------------------ ITEMS ------------------


@router.post("/{boq_id}/items", response_model=BOQOut)
async def add_item(
    boq_id: int,
    payload: BOQCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    parent = await db.scalar(
        select(BOQ).where(BOQ.id == boq_id, BOQ.status != "Deleted")
    )

    if not parent:
        raise NotFoundError("BOQ not found")

    quantity = Decimal(str(payload.quantity))
    unit_cost = Decimal(str(payload.unit_cost))

    validate_cost_inputs(quantity, unit_cost)
    total_cost, variance = calculate_cost(quantity, unit_cost)

    obj = BOQ(
        project_id=payload.project_id,
        boq_group_id=parent.boq_group_id,
        version_no=parent.version_no,
        is_latest=True,

        item_name=payload.item_name,
        category=payload.category,
        description=payload.description,

        quantity=quantity,
        unit=payload.unit,
        unit_cost=unit_cost,

        total_cost=total_cost,
        variance_cost=variance,

        status=payload.status,
    )

    db.add(obj)
    await db.flush()

    await bump_cache_version(redis, VERSION_KEY)

    return BOQOut.model_validate(obj)


@router.get("/{boq_id}/items", response_model=list[BOQOut])
async def get_items(boq_id: int,
                    current_user: User = Depends(get_current_active_user),
                    db: AsyncSession = Depends(get_db_session)):
    rows = (
        (
            await db.execute(
                select(BOQ).where(BOQ.boq_group_id == boq_id, BOQ.status != "Deleted")
            )
        )
        .scalars()
        .all()
    )

    return [BOQOut.model_validate(r) for r in rows]


@router.put("/items/{item_id}", response_model=BOQOut)
async def update_item(
    item_id: int,
    payload: BOQUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(BOQ).where(BOQ.id == item_id, BOQ.status != "Deleted"))

    if not obj:
        raise NotFoundError("Item not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)

    quantity = Decimal(str(obj.quantity))
    unit_cost = Decimal(str(obj.unit_cost))

    validate_cost_inputs(quantity, unit_cost)
    total, variance = calculate_cost(quantity, unit_cost, obj.actual_cost or Decimal(0))

    obj.total_cost = total
    obj.variance_cost = variance

    await db.flush()

    await bump_cache_version(redis, VERSION_KEY)

    return BOQOut.model_validate(obj)


@router.delete("/items/{item_id}")
async def delete_item(
    item_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(BOQ).where(BOQ.id == item_id, BOQ.status != "Deleted"))

    if not obj:
        raise NotFoundError("Item not found")

    obj.status = "Deleted"
    await db.flush()

    await bump_cache_version(redis, VERSION_KEY)

    return {
        "message": "BOQ deleted successfully",
        "item_id": item_id
    }


# ------------------ CREATE VERSION ------------------


@router.post("/{boq_id}/versions")
async def create_version(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    base = await db.scalar(
        select(BOQ).where(BOQ.id == boq_id, BOQ.status != "Deleted")
    )

    if not base:
        raise NotFoundError("BOQ not found")

    max_version = await db.scalar(
        select(func.max(BOQ.version_no)).where(BOQ.project_id == base.project_id)
    )
    new_version = (max_version or 0) + 1

    rows = (
        (
            await db.execute(
                select(BOQ).where(
                    BOQ.boq_group_id == base.boq_group_id, BOQ.status != "Deleted"
                )
            )
        )
        .scalars()
        .all()
    )

    for r in rows:
        db.add(
            BOQ(
                project_id=r.project_id,
                boq_group_id=new_version,
                version_no=new_version,
                is_latest=True,

                item_name=r.item_name,
                category=r.category,
                description=r.description,

                quantity=r.quantity,
                unit=r.unit,           
                unit_cost=r.unit_cost,

                total_cost=r.total_cost,
                actual_quantity=Decimal(0),
                actual_cost=Decimal(0),
                variance_cost=r.total_cost,
                status="Active",
            )
        )

    await db.flush()

    await bump_cache_version(redis, VERSION_KEY)
    return {"message": "Version created", "version": new_version}


# ------------------ EXPORT ------------------


@router.get("/{boq_id}/export/json")
async def export_boq_json(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (
            await db.execute(
                select(BOQ).where(BOQ.boq_group_id == boq_id, BOQ.status != "Deleted")
            )
        )
        .scalars()
        .all()
    )

    return [BOQOut.model_validate(r).model_dump() for r in rows]


@router.get("/{boq_id}/export/excel")
async def export_boq_excel(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (
            await db.execute(
                select(BOQ).where(BOQ.boq_group_id == boq_id, BOQ.status != "Deleted")
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        raise NotFoundError("No BOQ data found")

    wb = Workbook()
    ws = wb.active

    ws.append(["Item", "Qty", "Unit Cost", "Total", "Actual", "Variance"])

    for r in rows:
        ws.append(
            [
                r.item_name,
                float(r.quantity),
                float(r.unit_cost),
                float(r.total_cost),
                float(r.actual_cost),
                float(r.variance_cost),
            ]
        )

    file = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    file_path = file.name
    wb.save(file_path)

    return FileResponse(file_path, filename="boq.xlsx")


@router.get("/{boq_id}/export/pdf")
async def export_boq_pdf(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (
            await db.execute(
                select(BOQ).where(BOQ.boq_group_id == boq_id, BOQ.status != "Deleted")
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        raise NotFoundError("No BOQ data found")

    file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    file_path = file.name

    doc = SimpleDocTemplate(file_path)

    data = [["Item", "Qty", "Unit Cost", "Total", "Actual", "Variance"]]

    for r in rows:
        data.append(
            [
                r.item_name,
                float(r.quantity),
                float(r.unit_cost),
                float(r.total_cost),
                float(r.actual_cost),
                float(r.variance_cost),
            ]
        )

    table = Table(data)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))

    doc.build([table])

    return FileResponse(file_path, filename="boq.pdf")


# ------------------ OPTIMIZE ------------------


@router.get("/{boq_id}/optimize")
async def boq_optimize(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    base = await db.scalar(
        select(BOQ).where(BOQ.id == boq_id, BOQ.status != "Deleted")
    )

    if not base:
        raise NotFoundError("BOQ not found")

    rows = (
        await db.execute(
            select(BOQ).where(
                BOQ.boq_group_id == base.boq_group_id,
                BOQ.status != "Deleted"
            )
        )
    ).scalars().all()

    suggestions = []

    for r in rows:
        if r.actual_cost > r.total_cost:
            suggestions.append({
                "item": r.item_name,
                "suggestion": "Reduce cost or renegotiate vendor",
                "over_budget_by": float(r.actual_cost - r.total_cost)
            })

    return {"suggestions": suggestions}


# ------------------ AUDIT LOGS ------------------


@router.get("/{boq_id}/logs")
async def boq_logs(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (
            await db.execute(
                select(BOQAudit)
                .where(BOQAudit.boq_id == boq_id)
                .order_by(BOQAudit.id.desc())
            )
        )
        .scalars()
        .all()
    )

    return [
        {
            "action": r.action,
            "message": r.message,
            "user_id": r.user_id,
            "timestamp": r.created_at,
            "changes": r.changes, 
        }
        for r in rows
    ]
