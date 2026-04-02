from decimal import Decimal
from typing import Optional
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, extract
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
from app.models.labour import Labour, LabourAttendance
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.labour import LabourCreate, LabourOut, LabourUpdate
from app.schemas.labour import LabourAttendanceCreate, LabourAttendanceOut
from app.utils.helpers import NotFoundError
from datetime import date
from app.models.expense import Expense
from app.models.project import Project
from app.models.owner import OwnerTransaction
from sqlalchemy import select


router = APIRouter(
    prefix="/labour", tags=["labour"], dependencies=[default_rate_limiter_dependency()]
)

VERSION_KEY = "cache_version:labour"
ATTENDANCE_VERSION_KEY = "cache_version:labour_attendance"


@router.post("", response_model=LabourOut)
async def create_labour(
    payload: LabourCreate,
    current_user: User = Depends(
        require_roles(
            [
                UserRole.ADMIN,
                UserRole.PROJECT_MANAGER,
                UserRole.SITE_ENGINEER,
                UserRole.CONTRACTOR,
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    data = payload.model_dump(exclude_unset=True)

    obj = Labour(**data)

    db.add(obj)
    await db.flush()
    await db.refresh(obj)

    await bump_cache_version(redis, VERSION_KEY)

    return LabourOut.model_validate(obj)


@router.get("", response_model=PaginatedResponse[LabourOut])
async def list_labour(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = (
        f"cache:labour:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
    )

    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[LabourOut].model_validate(cached)

    query = select(Labour)
    count_query = select(func.count()).select_from(Labour)

    if search:
        like = f"%{search}%"
        query = query.where(Labour.labour_name.ilike(like))
        count_query = count_query.where(Labour.labour_name.ilike(like))

    if status:
        query = query.where(Labour.status == status)
        count_query = count_query.where(Labour.status == status)

    if project_id is not None:
        query = query.where(Labour.project_id == project_id)
        count_query = count_query.where(Labour.project_id == project_id)

    query = query.order_by(Labour.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [LabourOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)

    result = {"items": items, "meta": meta.model_dump()}

    await cache_set_json(redis, cache_key, result)

    return PaginatedResponse[LabourOut].model_validate(result)


@router.get("/{labour_id}", response_model=LabourOut)
async def get_labour(
    labour_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:labour:get:{version}:{labour_id}"

    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return LabourOut.model_validate(cached)

    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if obj is None:
        raise NotFoundError("Labour record not found")

    out = LabourOut.model_validate(obj)

    await cache_set_json(redis, cache_key, out.model_dump())

    return out


@router.put("/{labour_id}", response_model=LabourOut)
async def update_labour(
    labour_id: int,
    payload: LabourUpdate,
    current_user: User = Depends(
        require_roles(
            [
                UserRole.ADMIN,
                UserRole.PROJECT_MANAGER,
                UserRole.SITE_ENGINEER,
                UserRole.CONTRACTOR,
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if obj is None:
        raise NotFoundError("Labour record not found")

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    await db.flush()
    await db.refresh(obj)

    await bump_cache_version(redis, VERSION_KEY)

    return LabourOut.model_validate(obj)


@router.delete("/{labour_id}", status_code=204)
async def delete_labour(
    labour_id: int,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if obj is None:
        raise NotFoundError("Labour record not found")

    await db.delete(obj)
    await db.flush()

    await bump_cache_version(redis, VERSION_KEY)

    return None



@router.post("/{labour_id}/attendance", response_model=LabourAttendanceOut)
async def create_attendance(
    labour_id: int,
    payload: LabourAttendanceCreate,
    current_user: User = Depends(
        require_roles(
            [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    existing_attendance = await db.scalar(
        select(LabourAttendance).where(
            LabourAttendance.labour_id == labour_id,
            LabourAttendance.attendance_date == payload.attendance_date,
        )
    )

    if existing_attendance:
        raise ValueError("Attendance already exists for this date")

    if payload.attendance_date > date.today():
        raise ValueError("Future attendance not allowed")

    obj = LabourAttendance(labour_id=labour_id, **payload.model_dump())
    db.add(obj)
    await db.flush()
    await db.refresh(obj)


    hourly_rate = float(labour.daily_wage_rate) / 8

    total_wage = (
        hourly_rate * float(obj.working_hours)
        + float(obj.overtime_rate) * float(obj.overtime_hours)
    )

    project = await db.get(Project, labour.project_id)
    if not project:
        raise NotFoundError("Project not found")

    existing_expense = await db.scalar(
        select(Expense).where(
            Expense.project_id == labour.project_id,
            Expense.category == "Labour",
            Expense.expense_date == obj.attendance_date,
        )
    )

    if existing_expense:
        existing_expense.amount = Decimal(existing_expense.amount) + Decimal(total_wage)
        await db.flush()
        expense = existing_expense
    else:
        expense = Expense(
            project_id=labour.project_id,
            category="Labour",
            description=f"Labour expense - {obj.attendance_date}",
            amount=total_wage,
            expense_date=obj.attendance_date,
            payment_mode="auto",
        )
        db.add(expense)
        await db.flush()

    owner_transaction = OwnerTransaction(
        owner_id=project.owner_id,
        project_id=labour.project_id,
        type="debit",
        amount=total_wage,
        reference_type="labour",
        reference_id=expense.id,
        description=f"Labour expense ({obj.attendance_date})",
    )

    db.add(owner_transaction)
    await db.commit()

    await bump_cache_version(redis, ATTENDANCE_VERSION_KEY)

    return LabourAttendanceOut(
        id=obj.id, labour_id=labour_id, total_wage=total_wage, **payload.model_dump()
    )



@router.get("/{labour_id}/attendance", response_model=list[LabourAttendanceOut])
async def get_attendance(
    labour_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, ATTENDANCE_VERSION_KEY)
    cache_key = f"cache:labour:attendance:{version}:{labour_id}"

    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return cached

    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    result = await db.execute(
        select(LabourAttendance).where(LabourAttendance.labour_id == labour_id)
    )
    rows = result.scalars().all()

    data = []
    hourly_rate = float(labour.daily_wage_rate) / 8

    for r in rows:
        total_wage = (
            hourly_rate * float(r.working_hours)
            + float(r.overtime_rate) * float(r.overtime_hours)
        )

        data.append(
            LabourAttendanceOut(
                id=r.id,
                labour_id=labour_id,
                project_id=r.project_id,
                attendance_date=r.attendance_date,
                working_hours=float(r.working_hours),
                overtime_hours=float(r.overtime_hours),
                overtime_rate=float(r.overtime_rate),
                task_description=r.task_description,
                total_wage=total_wage,
            ).model_dump()
        )

    await cache_set_json(redis, cache_key, data)
    return data


@router.get("/{labour_id}/weekly-report")
async def weekly_report(
    labour_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    result = await db.execute(
        select(
            extract("week", LabourAttendance.attendance_date).label("week"),
            func.sum(LabourAttendance.working_hours).label("hours"),
            func.sum(LabourAttendance.overtime_hours).label("ot"),
            func.sum(
                LabourAttendance.overtime_hours * LabourAttendance.overtime_rate
            ).label("ot_wage"),
        )
        .where(LabourAttendance.labour_id == labour_id)
        .group_by("week")
    )

    rows = result.all()
    hourly_rate = float(labour.daily_wage_rate) / 8

    return [
        {
            "week": int(r.week),
            "total_hours": float(r.hours or 0),
            "overtime_hours": float(r.ot or 0),
            "total_wage": (
                hourly_rate * float(r.hours or 0)
                + float(r.ot_wage or 0)
            ),
        }
        for r in rows
    ]


@router.get("/{labour_id}/monthly-report")
async def monthly_report(
    labour_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    result = await db.execute(
        select(
            extract("month", LabourAttendance.attendance_date).label("month"),
            func.sum(LabourAttendance.working_hours).label("hours"),
            func.sum(LabourAttendance.overtime_hours).label("ot"),
            func.sum(
                LabourAttendance.overtime_hours * LabourAttendance.overtime_rate
            ).label("ot_wage"),
        )
        .where(LabourAttendance.labour_id == labour_id)
        .group_by("month")
    )

    rows = result.all()
    hourly_rate = float(labour.daily_wage_rate) / 8

    return [
        {
            "month": int(r.month),
            "total_hours": float(r.hours or 0),
            "overtime_hours": float(r.ot or 0),
            "total_wage": (
                hourly_rate * float(r.hours or 0)
                + float(r.ot_wage or 0)
            ),
        }
        for r in rows
    ]