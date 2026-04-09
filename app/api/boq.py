from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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

router = APIRouter(
    prefix="/boq",
    tags=["boq"],
    dependencies=[default_rate_limiter_dependency()],
)

VERSION_KEY = "cache_version:boq"


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
        logger.warning(f"Project not found for BOQ creation project_id={payload.project_id}")
        raise NotFoundError("Project not found")

    try:
        max_version = await db.scalar(
            select(func.max(BOQ.version_no)).where(BOQ.project_id == payload.project_id)
        )
        new_version = (max_version or 0) + 1

        await db.execute(
            update(BOQ).where(BOQ.project_id == payload.project_id).values(is_latest=False)
        )

        data = payload.model_dump(exclude_unset=True)

        quantity = Decimal(str(data.get("quantity", 0)))
        unit_cost = Decimal(str(data.get("unit_cost", 0)))

        if quantity <= 0:
            raise ValueError("Quantity must be greater than 0")

        if unit_cost <= 0:
            raise ValueError("Unit cost must be greater than 0")

        total_cost = quantity * unit_cost

        data.update(
            {
                "total_cost": total_cost,
                "actual_quantity": Decimal(0),
                "actual_cost": Decimal(0),
                "variance_cost": total_cost,
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


@router.get("/{boq_id}", response_model=BOQOut)
async def get_boq(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id))

    if obj is None:
        logger.warning(f"BOQ not found id={boq_id}")
        raise NotFoundError("BOQ item not found")

    return BOQOut.model_validate(obj)


@router.get("/project/{project_id}", response_model=list[BOQOut])
async def get_boq_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (
            await db.execute(
                select(BOQ).where(BOQ.project_id == project_id, BOQ.is_latest == True)
            )
        )
        .scalars()
        .all()
    )

    return [BOQOut.model_validate(r) for r in rows]


@router.get("/versions/{project_id}")
async def get_versions(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(BOQ.version_no)
        .where(BOQ.project_id == project_id)
        .distinct()
        .order_by(BOQ.version_no.desc())
    )

    return {"versions": [v[0] for v in result.fetchall()]}


@router.post("/{boq_id}/actuals", response_model=BOQOut)
async def update_actuals(
    boq_id: int,
    payload: BOQActualsUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating BOQ actuals id={boq_id}")

    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id))

    if not obj:
        logger.warning(f"BOQ not found for actual update id={boq_id}")
        raise NotFoundError("BOQ not found")

    obj.actual_quantity = payload.actual_quantity
    obj.actual_cost = payload.actual_cost
    obj.variance_cost = obj.total_cost - payload.actual_cost

    await db.flush()

    logger.info(f"BOQ actuals updated id={boq_id}")

    return BOQOut.model_validate(obj)


@router.get("/summary/{project_id}")
async def boq_summary(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    total_items = await db.scalar(
        select(func.count()).where(BOQ.project_id == project_id, BOQ.is_latest == True)
    )

    estimated = await db.scalar(
        select(func.sum(BOQ.total_cost)).where(
            BOQ.project_id == project_id, BOQ.is_latest == True
        )
    )

    actual = await db.scalar(
        select(func.sum(BOQ.actual_cost)).where(
            BOQ.project_id == project_id, BOQ.is_latest == True
        )
    )

    return {
        "total_items": total_items or 0,
        "estimated": float(estimated or 0),
        "actual": float(actual or 0),
        "difference": float((estimated or 0) - (actual or 0)),
    }


@router.get("/comparison/{project_id}")
async def boq_comparison(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (
            await db.execute(
                select(BOQ).where(BOQ.project_id == project_id, BOQ.is_latest == True)
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


@router.get("/analysis/{project_id}")
async def boq_analysis(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    boq_total = await db.scalar(
        select(func.sum(BOQ.total_cost)).where(
            BOQ.project_id == project_id, BOQ.is_latest == True
        )
    )

    actual_total = await db.scalar(
        select(func.sum(BOQ.actual_cost)).where(
            BOQ.project_id == project_id, BOQ.is_latest == True
        )
    )

    return {
        "boq_total": float(boq_total or 0),
        "actual_total": float(actual_total or 0),
        "difference": float((boq_total or 0) - (actual_total or 0)),
    }


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

    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id))

    if obj is None:
        logger.warning(f"BOQ not found for update id={boq_id}")
        raise NotFoundError("BOQ item not found")

    try:
        data = payload.model_dump(exclude_unset=True)

        for k, v in data.items():
            setattr(obj, k, v)

        quantity = Decimal(str(obj.quantity))
        unit_cost = Decimal(str(obj.unit_cost))

        if quantity <= 0:
            raise ValueError("Quantity must be greater than 0")

        if unit_cost <= 0:
            raise ValueError("Unit cost must be greater than 0")
        obj.total_cost = quantity * unit_cost

        obj.variance_cost = obj.total_cost - (obj.actual_cost or Decimal(0))

        await db.flush()
        await bump_cache_version(redis, VERSION_KEY)

        logger.info(f"BOQ updated id={boq_id}")

        return BOQOut.model_validate(obj)

    except Exception:
        logger.exception(f"BOQ update failed id={boq_id}")
        raise


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

    obj = await db.scalar(select(BOQ).where(BOQ.id == boq_id))

    if obj is None:
        logger.warning(f"BOQ not found for delete id={boq_id}")
        raise NotFoundError("BOQ item not found")

    await db.delete(obj)
    await db.flush()

    await bump_cache_version(redis, VERSION_KEY)

    logger.info(f"BOQ deleted id={boq_id}")

    return {"message": "BOQ deleted successfully"}