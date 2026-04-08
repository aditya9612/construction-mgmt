from decimal import Decimal
from typing import Optional
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, extract
from sqlalchemy.ext.asyncio import AsyncSession
from app.cache import redis as r
from app.core import dependencies as d
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.contractor import Contractor
from app.models.labour import Labour, LabourAttendance, LabourPayroll
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas import labour as s
from app.utils.helpers import NotFoundError
from app.models.expense import Expense
from app.models.project import Project
from app.models.owner import OwnerTransaction

router = APIRouter(
    prefix="/labour", tags=["labour"], dependencies=[default_rate_limiter_dependency()]
)

VERSION_KEY = "cache_version:labour"
ATTENDANCE_VERSION_KEY = "cache_version:labour_attendance"


@router.post("", response_model=s.LabourOut)
async def create_labour(
    payload: s.LabourCreate,
    current_user: User = Depends(
        d.require_roles(
            [
                UserRole.ADMIN,
                UserRole.PROJECT_MANAGER,
                UserRole.SITE_ENGINEER,
                UserRole.CONTRACTOR,
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    data = payload.model_dump(exclude_unset=True)

    if payload.contractor_id:
        contractor = await db.get(Contractor, payload.contractor_id)
        if not contractor:
            raise ValueError("Invalid contractor_id")

    obj = Labour(**data)

    db.add(obj)
    await db.flush()
    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)

    return s.LabourOut.model_validate(obj)


@router.get("", response_model=PaginatedResponse[s.LabourOut])
async def list_labour(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = (
        f"cache:labour:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
    )

    cached = await r.cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[s.LabourOut].model_validate(cached)

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

    items = [s.LabourOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)

    result = {"items": items, "meta": meta.model_dump()}

    await r.cache_set_json(redis, cache_key, result)

    return PaginatedResponse[s.LabourOut].model_validate(result)


@router.get("/{labour_id}", response_model=s.LabourOut)
async def get_labour(
    labour_id: int,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:labour:get:{version}:{labour_id}"

    cached = await r.cache_get_json(redis, cache_key)
    if cached is not None:
        return s.LabourOut.model_validate(cached)

    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if obj is None:
        raise NotFoundError("Labour record not found")

    out = s.LabourOut.model_validate(obj)

    await r.cache_set_json(redis, cache_key, out.model_dump())

    return out


@router.put("/{labour_id}", response_model=s.LabourOut)
async def update_labour(
    labour_id: int,
    payload: s.LabourUpdate,
    current_user: User = Depends(
        d.require_roles(
            [
                UserRole.ADMIN,
                UserRole.PROJECT_MANAGER,
                UserRole.SITE_ENGINEER,
                UserRole.CONTRACTOR,
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if obj is None:
        raise NotFoundError("Labour record not found")

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    await db.flush()
    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)

    return s.LabourOut.model_validate(obj)


@router.delete("/{labour_id}", status_code=204)
async def delete_labour(
    labour_id: int,
    current_user: User = Depends(
        d.require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if obj is None:
        raise NotFoundError("Labour record not found")

    await db.delete(obj)
    await db.flush()

    await r.bump_cache_version(redis, VERSION_KEY)

    return None


@router.post("/{labour_id}/attendance", response_model=s.LabourAttendanceOut)
async def create_attendance(
    labour_id: int,
    payload: s.LabourAttendanceCreate,
    current_user: User = Depends(
        d.require_roles(
            [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    existing = await db.scalar(
        select(LabourAttendance).where(
            LabourAttendance.labour_id == labour_id,
            LabourAttendance.attendance_date == payload.attendance_date,
        )
    )
    if existing:
        raise ValueError("Attendance already exists for this date")

    if payload.attendance_date > date.today():
        raise ValueError("Future attendance not allowed")

    if payload.working_hours > 24:
        raise ValueError("Working hours cannot exceed 24")

    if payload.overtime_hours > 24:
        raise ValueError("Overtime hours cannot exceed 24")

    if payload.overtime_rate < 0:
        raise ValueError("Overtime rate cannot be negative")

    if payload.working_hours + payload.overtime_hours > 24:
        raise ValueError("Total hours cannot exceed 24")

    obj = LabourAttendance(labour_id=labour_id, **payload.model_dump())
    db.add(obj)
    await db.flush()
    await db.refresh(obj)

    hourly_rate = labour.daily_wage_rate / Decimal("8")

    total_wage = (
        hourly_rate * obj.working_hours + obj.overtime_rate * obj.overtime_hours
    )

    project = await db.get(Project, labour.project_id)
    if not project:
        raise NotFoundError("Project not found")

    existing_expense = await db.scalar(
        select(Expense).where(
            Expense.project_id == labour.project_id,
            Expense.labour_id == labour_id,
            Expense.category == "Labour",
            Expense.expense_date == obj.attendance_date,
        )
    )

    if existing_expense:
        existing_expense.amount = Decimal(existing_expense.amount) + total_wage
        await db.flush()
        expense = existing_expense
    else:
        expense = Expense(
            project_id=labour.project_id,
            labour_id=labour_id,
            category="Labour",
            description=f"Labour expense - {obj.attendance_date}",
            amount=total_wage,
            expense_date=obj.attendance_date,
            payment_mode="auto",
        )
        db.add(expense)
        await db.flush()

    owner_txn = OwnerTransaction(
        owner_id=project.owner_id,
        project_id=labour.project_id,
        type="debit",
        amount=float(total_wage),
        reference_type="labour",
        reference_id=expense.id,
        description=f"Labour expense ({obj.attendance_date})",
    )
    db.add(owner_txn)

    await db.commit()

    await r.bump_cache_version(redis, ATTENDANCE_VERSION_KEY)

    return s.LabourAttendanceOut.model_validate(
        {
            "id": obj.id,
            "labour_id": labour_id,
            "total_wage": total_wage,
            **payload.model_dump(),
        }
    )


@router.get("/{labour_id}/attendance", response_model=list[s.LabourAttendanceOut])
async def get_attendance(
    labour_id: int,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    version = await r.get_cache_version(redis, ATTENDANCE_VERSION_KEY)
    cache_key = f"cache:labour:attendance:{version}:{labour_id}"

    cached = await r.cache_get_json(redis, cache_key)
    if cached is not None:
        return [s.LabourAttendanceOut.model_validate(i) for i in cached]

    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    result = await db.execute(
        select(LabourAttendance).where(LabourAttendance.labour_id == labour_id)
    )
    rows = result.scalars().all()

    hourly_rate = labour.daily_wage_rate / Decimal("8")

    data = []
    for row in rows:
        total_wage = hourly_rate * row.working_hours + row.overtime_rate * row.overtime_hours

        data.append(
            s.LabourAttendanceOut(
                id=row.id,
                labour_id=labour_id,
                project_id=row.project_id,
                attendance_date=row.attendance_date,
                working_hours=row.working_hours,
                overtime_hours=row.overtime_hours,
                overtime_rate=row.overtime_rate,
                task_description=row.task_description,
                total_wage=total_wage,
            ).model_dump()
        )

    await r.cache_set_json(redis, cache_key, data)
    return data


@router.get("/{labour_id}/weekly-report")
async def weekly_report(
    labour_id: int,
    current_user: User = Depends(d.get_current_active_user),
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
    hourly_rate = labour.daily_wage_rate / Decimal("8")

    return [
        {
            "week": int(r.week),
            "total_hours": float(r.hours or 0),
            "overtime_hours": float(r.ot or 0),
            "total_wage": float(
                hourly_rate * Decimal(r.hours or 0) + Decimal(r.ot_wage or 0)
            ),
        }
        for r in rows
    ]


@router.get("/{labour_id}/monthly-report")
async def monthly_report(
    labour_id: int,
    current_user: User = Depends(d.get_current_active_user),
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
    hourly_rate = labour.daily_wage_rate / Decimal("8")

    return [
        {
            "month": int(r.month),
            "total_hours": float(r.hours or 0),
            "overtime_hours": float(r.ot or 0),
            "total_wage": float(
                hourly_rate * Decimal(r.hours or 0) + Decimal(r.ot_wage or 0)
            ),
        }
        for r in rows
    ]


@router.post("/payroll/generate", response_model=list[s.PayrollOut])
async def generate_payroll(
    payload: s.PayrollGenerate,
    db: AsyncSession = Depends(get_db_session),
):
    if payload.month < 1 or payload.month > 12:
        raise ValueError("Invalid month")

    if payload.year < 2000:
        raise ValueError("Invalid year")

    result = await db.execute(
        select(LabourAttendance).where(
            extract("month", LabourAttendance.attendance_date) == payload.month,
            extract("year", LabourAttendance.attendance_date) == payload.year,
        )
    )

    rows = result.scalars().all()

    if not rows:
        return []

    payroll_map = {}
    labour_cache = {}

    for r in rows:
        if r.labour_id not in labour_cache:
            labour_cache[r.labour_id] = await db.get(Labour, r.labour_id)

        labour = labour_cache[r.labour_id]

        hourly_rate = labour.daily_wage_rate / Decimal("8")

        wage = hourly_rate * r.working_hours + r.overtime_rate * r.overtime_hours

        key = (r.labour_id, r.project_id)

        if key not in payroll_map:
            payroll_map[key] = {
                "working_hours": Decimal("0"),
                "overtime_hours": Decimal("0"),
                "total_wage": Decimal("0"),
            }

        payroll_map[key]["working_hours"] += r.working_hours
        payroll_map[key]["overtime_hours"] += r.overtime_hours
        payroll_map[key]["total_wage"] += wage

    output = []

    for (labour_id, project_id), data in payroll_map.items():

        existing = await db.scalar(
            select(LabourPayroll).where(
                LabourPayroll.month == payload.month,
                LabourPayroll.year == payload.year,
                LabourPayroll.project_id == project_id,
                LabourPayroll.labour_id == labour_id,
            )
        )
        if existing:
            raise ValueError(
                f"Payroll already exists for labour {labour_id} in project {project_id}"
            )

        total_wage = data["total_wage"]

        advance = await db.scalar(
            select(func.sum(Expense.amount)).where(
                Expense.labour_id == labour_id,
                Expense.project_id == project_id,
                Expense.category == "Labour Advance",
                extract("month", Expense.expense_date) == payload.month,
                extract("year", Expense.expense_date) == payload.year,
            )
        ) or Decimal("0")

        remaining_salary = total_wage - advance

        if remaining_salary < 0:
            remaining_salary = Decimal("0")

        if remaining_salary == 0:
            status = "Paid"
        elif advance > 0:
            status = "Partial"
        else:
            status = "Pending"

        obj = LabourPayroll(
            labour_id=labour_id,
            project_id=project_id,
            month=payload.month,
            year=payload.year,
            total_working_hours=data["working_hours"],
            total_overtime_hours=data["overtime_hours"],
            total_wage=total_wage,
            paid_amount=advance,
            remaining_amount=remaining_salary,
            status=status,
        )

        db.add(obj)
        await db.flush()

        output.append(obj)

    await db.commit()

    return output
