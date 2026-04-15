from decimal import Decimal
import io
from typing import Optional
from datetime import date
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, extract
from sqlalchemy.ext.asyncio import AsyncSession
from app.cache import redis as r
from app.core import dependencies as d
from app.core.enums import AttendanceStatus, LabourStatus, PayrollStatus
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.contractor import Contractor
from app.models.labour import Labour, LabourAttendance, LabourPayroll
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas import labour as s
from app.utils.helpers import NotFoundError, ValidationError
from app.models.expense import Expense
from app.models.owner import OwnerTransaction
from app.core.logger import logger
import pandas as pd
from app.utils.common import assert_project_access , generate_business_id
from app.models.project import Project, ProjectMember


async def get_user_project_ids(db, user):
    if user.role == UserRole.ADMIN:
        result = await db.execute(select(Project.id))
        return [r[0] for r in result.all()]

    result = await db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    )
    return [r[0] for r in result.all()]


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
    logger.info(f"Creating labour name={payload.labour_name}")

    data = payload.model_dump(exclude_unset=True)

    data["worker_code"] = await generate_business_id(
        db,
        Labour,
        "worker_code",
        "LAB"
    )

    if payload.contractor_id:
        contractor = await db.get(Contractor, payload.contractor_id)
        if not contractor:
            logger.warning(f"Invalid contractor_id={payload.contractor_id}")
            raise ValidationError("Invalid contractor_id")

    await assert_project_access(
        db,
        project_id=payload.project_id,
        current_user=current_user,
    )

    obj = Labour(**data)
    db.add(obj)

    try:
        await db.flush()
        await db.refresh(obj)

        await r.bump_cache_version(redis, "dashboard_version")

    except Exception:
        await db.rollback()
        logger.exception("Labour creation failed")
        raise

    logger.info(f"Labour created id={obj.id}")

    return s.LabourOut.model_validate(obj)

@router.get("", response_model=PaginatedResponse[s.LabourOut])
async def list_labour(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[LabourStatus] = None, 
    project_id: Optional[int] = None,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    from app.utils.common import assert_project_access

    if project_id:
        await assert_project_access(db, project_id, current_user)

    version = await r.get_cache_version(redis, VERSION_KEY)

    status_key = status.value if status else None

    cache_key = (
        f"cache:labour:list:{version}:{limit}:{offset}:{search}:{status_key}:{project_id}"
    )

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return PaginatedResponse[s.LabourOut].model_validate(cached)

    query = select(Labour)
    count_query = select(func.count()).select_from(Labour)

    project_ids = await get_user_project_ids(db, current_user)
    query = query.where(Labour.project_id.in_(project_ids))
    count_query = count_query.where(Labour.project_id.in_(project_ids))

    if search:
        like = f"%{search}%"
        query = query.where(Labour.labour_name.ilike(like))
        count_query = count_query.where(Labour.labour_name.ilike(like))

    if status:
        query = query.where(Labour.status == status)
        count_query = count_query.where(Labour.status == status)

    if project_id:
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
    from app.utils.common import assert_project_access

    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:labour:get:{version}:{labour_id}"

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return s.LabourOut.model_validate(cached)

    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if not obj:
        raise NotFoundError("Labour record not found")

    await assert_project_access(db, obj.project_id, current_user)

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
    from app.utils.common import assert_project_access

    logger.info(f"Updating labour id={labour_id}")

    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if not obj:
        raise NotFoundError("Labour record not found")

    await assert_project_access(db, obj.project_id, current_user)

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    try:
        await db.flush()
        await db.refresh(obj)

        await r.bump_cache_version(redis, VERSION_KEY)
        await r.bump_cache_version(redis, "dashboard_version")

    except Exception:
        await db.rollback()
        logger.exception(f"Labour update failed id={labour_id}")
        raise

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
    from app.utils.common import assert_project_access

    logger.info(f"Deleting labour id={labour_id}")

    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if not obj:
        raise NotFoundError("Labour record not found")

    await assert_project_access(db, obj.project_id, current_user)

    try:
        await db.delete(obj)
        await db.flush()

        await r.bump_cache_version(redis, VERSION_KEY)
        await r.bump_cache_version(redis, "dashboard_version")

    except Exception:
        await db.rollback()
        logger.exception(f"Labour delete failed id={labour_id}")
        raise

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
    logger.info(f"Creating attendance labour_id={labour_id}")

    labour = await db.get(Labour, labour_id)
    if not labour:
        logger.warning(f"Labour not found id={labour_id}")
        raise NotFoundError("Labour not found")

    await assert_project_access(
        db,
        project_id=labour.project_id,
        current_user=current_user,
    )

    existing = await db.scalar(
        select(LabourAttendance).where(
            LabourAttendance.labour_id == labour_id,
            LabourAttendance.attendance_date == payload.attendance_date,
        )
    )
    if existing:
        raise ValidationError("Attendance already exists for this date")

    if payload.attendance_date > date.today():
        raise ValidationError("Future attendance not allowed")

    if payload.working_hours > 24:
        raise ValidationError("Working hours cannot exceed 24")

    if payload.overtime_hours > 24:
        raise ValidationError("Overtime hours cannot exceed 24")

    if payload.overtime_rate < 0:
        raise ValidationError("Overtime rate cannot be negative")


    if payload.working_hours + payload.overtime_hours > 24:
        raise ValidationError("Total hours cannot exceed 24")

    obj = LabourAttendance(labour_id=labour_id, **payload.model_dump())
    db.add(obj)

    try:
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
            amount=total_wage,
            reference_type="labour",
            reference_id=expense.id,
            description=f"Labour expense ({obj.attendance_date})",
        )
        db.add(owner_txn)

        await r.bump_cache_version(redis, ATTENDANCE_VERSION_KEY)
        await r.bump_cache_version(redis, "dashboard_version")

    except Exception:
        await db.rollback()
        logger.exception(f"Attendance creation failed labour_id={labour_id}")
        raise

    logger.info(f"Attendance created labour_id={labour_id}")

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
    from app.utils.common import assert_project_access

    version = await r.get_cache_version(redis, ATTENDANCE_VERSION_KEY)
    cache_key = f"cache:labour:attendance:{version}:{labour_id}"

    cached = await r.cache_get_json(redis, cache_key)
    if cached is not None:
        return [s.LabourAttendanceOut.model_validate(i) for i in cached]

    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    await assert_project_access(db, labour.project_id, current_user)

    result = await db.execute(
        select(LabourAttendance).where(LabourAttendance.labour_id == labour_id)
    )
    rows = result.scalars().all()

    hourly_rate = labour.daily_wage_rate / Decimal("8")

    data = []
    for row in rows:
        total_wage = (
            hourly_rate * row.working_hours + row.overtime_rate * row.overtime_hours
        )

        data.append(
            s.LabourAttendanceOut(
                id=row.id,
                labour_id=labour_id,
                project_id=row.project_id,
                attendance_date=row.attendance_date,
                status=row.status,
                in_time=row.in_time,
                out_time=row.out_time,
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
    from app.utils.common import assert_project_access

    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    await assert_project_access(db, labour.project_id, current_user)

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
    from app.utils.common import assert_project_access

    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    await assert_project_access(db, labour.project_id, current_user)

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
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    logger.info(f"Generating payroll month={payload.month} year={payload.year}")

    if payload.month < 1 or payload.month > 12:
        raise ValidationError("Invalid month")

    if payload.year < 2000:
        raise ValidationError("Invalid year")

    project_ids = await get_user_project_ids(db, current_user)

    try:
        result = await db.execute(
            select(LabourAttendance).where(
                extract("month", LabourAttendance.attendance_date) == payload.month,
                extract("year", LabourAttendance.attendance_date) == payload.year,
                LabourAttendance.project_id.in_(project_ids),
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
                status = PayrollStatus.PAID
            elif advance > 0:
                status = PayrollStatus.PARTIAL
            else:
                status = PayrollStatus.PENDING

            existing = await db.scalar(
                select(LabourPayroll).where(
                    LabourPayroll.month == payload.month,
                    LabourPayroll.year == payload.year,
                    LabourPayroll.project_id == project_id,
                    LabourPayroll.labour_id == labour_id,
                )
            )


            if existing:
                existing.total_working_hours = data["working_hours"]
                existing.total_overtime_hours = data["overtime_hours"]
                existing.total_wage = total_wage

                existing.paid_amount = advance

                existing.remaining_amount = remaining_salary
                existing.status = status

                await db.flush()

                output.append(existing)

                continue

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

        await r.bump_cache_version(redis, VERSION_KEY)
        await r.bump_cache_version(redis, "dashboard_version")

    except Exception:
        await db.rollback()
        logger.exception("Payroll generation failed")
        raise

    return output


# =========================
# BULK ATTENDANCE
# =========================
@router.post("/attendance/bulk")
async def bulk_attendance(
    payload: s.BulkAttendanceCreate,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    success = []
    errors = []

    for idx, item in enumerate(payload.items):

        try:
            await assert_project_access(db, item.project_id, current_user)

            labour = await db.get(Labour, item.labour_id)
            if not labour:
                raise ValidationError("Labour not found")

            existing = await db.scalar(
                select(LabourAttendance).where(
                    LabourAttendance.labour_id == item.labour_id,
                    LabourAttendance.attendance_date == item.attendance_date,
                )
            )
            if existing:
                raise ValidationError("Attendance already exists")

            if item.attendance_date > date.today():
                raise ValidationError("Future attendance not allowed")

            if item.working_hours < 0 or item.overtime_hours < 0:
                raise ValidationError("Hours cannot be negative")

            if item.working_hours > 24:
                raise ValidationError("Working hours > 24")

            if item.overtime_hours > 24:
                raise ValidationError("Overtime > 24")

            if item.working_hours + item.overtime_hours > 24:
                raise ValidationError("Total hours > 24")

            if item.overtime_rate < 0:
                raise ValidationError("Invalid overtime rate")

            obj = LabourAttendance(**item.model_dump())
            db.add(obj)
            await db.flush()

            hourly = labour.daily_wage_rate / Decimal("8")
            wage = hourly * obj.working_hours + obj.overtime_rate * obj.overtime_hours

            expense = Expense(
                project_id=obj.project_id,
                labour_id=obj.labour_id,
                category="Labour",
                amount=wage,
                expense_date=obj.attendance_date,
            )
            db.add(expense)
            await db.flush()

            project = await db.get(Project, obj.project_id)

            if project:
                db.add(
                    OwnerTransaction(
                        owner_id=project.owner_id,
                        project_id=obj.project_id,
                        type="debit",
                        amount=wage,
                        reference_type="labour",
                        reference_id=expense.id,
                        description=f"Labour expense ({obj.attendance_date})",
                    )
                )

            success.append(
                {
                    "index": idx,
                    "labour_id": item.labour_id,
                    "date": str(item.attendance_date),
                }
            )

        except Exception as e:
            errors.append(
                {
                    "index": idx,
                    "labour_id": item.labour_id,
                    "date": str(item.attendance_date),
                    "error": str(e),
                }
            )

    if success:
        await r.bump_cache_version(redis, ATTENDANCE_VERSION_KEY)
        await r.bump_cache_version(redis, "dashboard_version")

    return {
        "success_count": len(success),
        "failed_count": len(errors),
        "success": success,
        "errors": errors,
    }


# =========================
# PAYROLL PAYMENT
# =========================
@router.post("/payroll/pay")
async def pay_salary(
    payload: s.PayrollPayment,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):

    await assert_project_access(db, payload.project_id, current_user)

    payroll = await db.scalar(
        select(LabourPayroll).where(
            LabourPayroll.labour_id == payload.labour_id,
            LabourPayroll.project_id == payload.project_id,
            LabourPayroll.month == payload.month,
            LabourPayroll.year == payload.year,
        )
    )

    if not payroll:
        raise NotFoundError("Payroll not found")

    if payload.amount > payroll.remaining_amount:
        raise ValidationError("Amount exceeds remaining salary")

    payroll.paid_amount += payload.amount
    payroll.remaining_amount = max(
        Decimal("0"), payroll.remaining_amount - payload.amount
    )

    payroll.status = (
        PayrollStatus.PAID
        if payroll.remaining_amount == 0
        else PayrollStatus.PARTIAL
    )

    await db.flush()

    await r.bump_cache_version(redis, "dashboard_version")

    return payroll


# =========================
# ADVANCE
# =========================
@router.post("/advance")
async def advance_payment(
    payload: s.AdvancePayment,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):

    await assert_project_access(db, payload.project_id, current_user)

    labour = await db.get(Labour, payload.labour_id)
    project = await db.get(Project, payload.project_id)

    if not labour or not project:
        raise NotFoundError("Invalid labour or project")

    db.add(
        Expense(
            project_id=payload.project_id,
            labour_id=payload.labour_id,
            category="Labour Advance",
            amount=payload.amount,
            description=payload.description,
            expense_date=date.today(),
        )
    )

    await db.flush()

    await r.bump_cache_version(redis, "dashboard_version")

    return {"message": "Advance recorded"}


# =========================
# DASHBOARD
# =========================
@router.get("/dashboard/stats")
async def dashboard_stats(
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    today = date.today()

    project_ids = await get_user_project_ids(db, current_user)

    total_labour = await db.scalar(
        select(func.count())
        .select_from(LabourAttendance)
        .where(
            LabourAttendance.attendance_date == today,
            LabourAttendance.project_id.in_(project_ids),
        )
    )

    total_cost = await db.scalar(
        select(func.sum(Expense.amount)).where(
            Expense.category == "Labour",
            Expense.expense_date == today,
            Expense.project_id.in_(project_ids),
        )
    )

    return {
        "total_labour_today": total_labour or 0,
        "total_cost_today": float(total_cost or 0),
    }


@router.get("/contractor/{contractor_id}")
async def get_labour_by_contractor(
    contractor_id: int,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)

    result = await db.execute(
        select(Labour).where(
            Labour.contractor_id == contractor_id,
            Labour.project_id.in_(project_ids),
        )
    )

    rows = result.scalars().all()

    for r in rows:
        await assert_project_access(db, r.project_id, current_user)

    return [s.LabourOut.model_validate(r) for r in rows]


@router.get("/summary/skill")
async def labour_skill_summary(
    project_id: int,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id, current_user)

    result = await db.execute(
        select(Labour.skill_type, func.count(Labour.id))
        .where(Labour.project_id == project_id)
        .group_by(Labour.skill_type)
    )

    return [{"skill_type": r.skill_type, "count": r[1]} for r in result]


# =========================
# EXCEL EXPORT
# =========================
@router.get("/report/export")
async def export_excel(
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)

    result = await db.execute(select(Labour).where(Labour.project_id.in_(project_ids)))
    rows = result.scalars().all()

    data = [
        {
            "Name": r.labour_name,
            "Skill": r.skill_type,
            "Wage": float(r.daily_wage_rate),
        }
        for r in rows
    ]

    df = pd.DataFrame(data)

    stream = io.BytesIO()
    df.to_excel(stream, index=False)
    stream.seek(0)

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=labour.xlsx"},
    )
