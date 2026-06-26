from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import io
from fastapi import Form
from pydantic import EmailStr
from app.models.master_data import LabourType
from sqlalchemy import case
from sqlalchemy.orm import selectinload
from typing import Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select, extract, tuple_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.cache import redis as r
from app.core import dependencies as d
from app.core.enums import AttendanceStatus, LabourStatus, OTPolicyType, PayrollStatus
from app.core.validators import validate_and_save_image
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.contractor import Contractor
from app.models.labour import Labour, LabourPayroll, LabourProject
from app.models.master_data import LabourType
from app.models.user import UserAttendance
from app.schemas.user import UserAttendanceOut
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas import labour as s
from app.utils.helpers import NotFoundError, PermissionDeniedError, ValidationError
from app.models.expense import Expense
from app.models.owner import OwnerTransaction
from app.models.approval import Approval
from app.core.logger import logger
import pandas as pd
from app.utils.common import assert_project_access, generate_business_id
from app.models.project import Project, ProjectMember
from sqlalchemy.exc import IntegrityError
from fastapi import File, UploadFile, Form
from app.utils.pagination import PaginationParams
from datetime import datetime
from zoneinfo import ZoneInfo
from app.models.invoice import Transaction
from app.models.accountant import JournalEntry, JournalLine
from app.models.project import ProjectOTPolicy


async def get_user_project_ids(db, user):
    if user.role == UserRole.ADMIN.value:
        result = await db.execute(select(Project.id))
        return [r[0] for r in result.all()]

    result = await db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    )
    return [r[0] for r in result.all()]


LABOUR_READ_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.ACCOUNTANT,
        UserRole.CLIENT,
    ]
]

LABOUR_WRITE_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.CONTRACTOR,
    ]
]

LABOUR_DELETE_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
    ]
]

PAYROLL_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.ACCOUNTANT,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
    ]
]

router = APIRouter(
    prefix="/labour", tags=["labour"], dependencies=[default_rate_limiter_dependency()]
)

VERSION_KEY = "cache_version:labour"
ATTENDANCE_VERSION_KEY = "cache_version:labour_attendance"


@router.post("", response_model=s.LabourOut)
async def create_labour(
    payload: s.LabourCreate = Depends(),
    profile_image: UploadFile = File(None),
    current_user: User = Depends(d.require_roles(LABOUR_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    logger.info(f"Creating labour name={payload.labour_name}")

    data = payload.model_dump(exclude_unset=True)

    # PROFILE IMAGE
    image_path = None

    if profile_image:
        image_path = await validate_and_save_image(
            file=profile_image, upload_dir="uploads/labour", prefix="labour"
        )

    data["profile_image"] = image_path

    if payload.contractor_id:
        contractor = await db.get(Contractor, payload.contractor_id)
        if not contractor:
            raise ValidationError("Invalid contractor_id")

    # =========================================
    # ADD HERE
    # =========================================

    labour_type = await db.get(LabourType, payload.labour_type_id)

    if not labour_type:
        raise ValidationError("Invalid labour_type_id")

    for _ in range(3):
        try:
            worker_code = await generate_business_id(db, Labour, "worker_code", "LAB")
            data["worker_code"] = worker_code

            # Create User for this labour to allow attendance check-in
            user = User(
                email=payload.email or f"{worker_code.lower()}@labour.local",
                mobile=payload.mobile_number,
                full_name=payload.labour_name,
                aadhaar_number=payload.aadhaar_number,
                pan_number=payload.pan_number,
                address=payload.address,
                profile_image=image_path,
                role=UserRole.LABOUR.value,
                is_active=(payload.status == LabourStatus.ACTIVE),
                created_by=current_user.id,
            )
            db.add(user)
            await db.flush()

            data["user_id"] = user.id

            obj = Labour(**data)
            db.add(obj)
            await db.flush()
            await db.refresh(
                obj,
                attribute_names=[
                    "user",
                    "contractor",
                    "labour_type",
                ],
            )

            await r.bump_cache_version(redis, "dashboard_version")

            return s.LabourOut.model_validate(obj)

        except IntegrityError as e:
            await db.rollback()

            logger.warning(f"Labour creation retry due to integrity error: {e}")

            continue

    raise Exception("Failed to create labour")


@router.get("", response_model=PaginatedResponse[s.LabourOut])
async def list_labour(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[LabourStatus] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    if project_id:
        await assert_project_access(
            db, project_id=project_id, current_user=current_user
        )

    version = await r.get_cache_version(redis, VERSION_KEY)

    cache_key = (
        f"cache:labour:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
    )

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return PaginatedResponse[s.LabourOut].model_validate(cached)

    query = (
        select(Labour).options(
            selectinload(Labour.user),
            selectinload(Labour.contractor),
            selectinload(Labour.labour_type),
        )
    ).distinct()

    count_query = select(func.count(func.distinct(Labour.id))).select_from(Labour)

    #  Subquery instead of JOIN
    if project_id:
        subq = select(LabourProject.labour_id).where(
            LabourProject.project_id == project_id
        )

        query = query.where(Labour.id.in_(subq))
        count_query = count_query.where(Labour.id.in_(subq))

    if search:
        query = query.outerjoin(Contractor, Labour.contractor_id == Contractor.id)
        count_query = count_query.outerjoin(
            Contractor, Labour.contractor_id == Contractor.id
        )

        search_filter = or_(
            Labour.labour_name.ilike(f"%{search}%"),
            Labour.worker_code.ilike(f"%{search}%"),
            Labour.aadhaar_number.ilike(f"%{search}%"),
            Contractor.name.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    if status:
        query = query.where(Labour.status == status)
        count_query = count_query.where(Labour.status == status)

    query = query.order_by(Labour.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [s.LabourOut.model_validate(r).model_dump() for r in rows]

    result = {
        "items": items,
        "meta": {"total": int(total or 0), "limit": limit, "offset": offset},
    }

    await r.cache_set_json(redis, cache_key, result)

    return PaginatedResponse[s.LabourOut].model_validate(result)


@router.get("/payroll", response_model=list[s.PayrollDetailsOut])
async def get_payroll_list(
    project_id: int,
    month: int,
    year: int,
    contractor_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    query = (
        select(LabourPayroll, Labour)
        .join(Labour, Labour.id == LabourPayroll.labour_id)
        .where(
            LabourPayroll.project_id == project_id,
            LabourPayroll.month == month,
            LabourPayroll.year == year,
        )
    )

    if contractor_id:
        query = query.where(Labour.contractor_id == contractor_id)

    if search:
        query = query.where(
            or_(
                Labour.labour_name.ilike(f"%{search}%"),
                Labour.worker_code.ilike(f"%{search}%"),
            )
        )

    result = await db.execute(query)
    rows = result.all()

    output = []
    for payroll, labour in rows:
        output.append(
            s.PayrollDetailsOut(
                id=payroll.id,
                labour_id=payroll.labour_id,
                project_id=payroll.project_id,
                month=payroll.month,
                year=payroll.year,
                total_working_hours=payroll.total_working_hours,
                total_overtime_hours=payroll.total_overtime_hours,
                total_wage=payroll.total_wage,
                paid_amount=payroll.paid_amount,
                remaining_amount=payroll.remaining_amount,
                status=payroll.status,
                # Enriched properties
                labour_name=labour.labour_name,
                worker_code=labour.worker_code,
                skill_category=labour.skill_category,
                daily_wage_rate=labour.effective_daily_wage,
                contractor_id=labour.contractor_id,
            )
        )
    return output


@router.get("/payroll/stats", response_model=s.PayrollStatsOut)
async def get_payroll_stats(
    project_id: int,
    month: int,
    year: int,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    payroll_sum = await db.execute(
        select(
            func.sum(LabourPayroll.paid_amount).label("paid"),
            func.sum(LabourPayroll.remaining_amount).label("pending"),
            func.sum(LabourPayroll.total_wage).label("budget"),
        ).where(
            LabourPayroll.project_id == project_id,
            LabourPayroll.month == month,
            LabourPayroll.year == year,
        )
    )
    res = payroll_sum.first()

    paid = res.paid if res and res.paid is not None else Decimal("0")
    pending = res.pending if res and res.pending is not None else Decimal("0")
    budget = res.budget if res and res.budget is not None else Decimal("0")

    # Count labour advances recorded as expenses
    advance_count = (
        await db.scalar(
            select(func.count(Expense.id)).where(
                Expense.project_id == project_id,
                Expense.category == "Labour Advance",
                extract("month", Expense.expense_date) == month,
                extract("year", Expense.expense_date) == year,
            )
        )
        or 0
    )

    return s.PayrollStatsOut(
        paid_this_month=paid,
        pending_due=pending,
        monthly_budget=budget,
        advance_logs=advance_count,
    )


@router.get(
    "/payroll/contractor-liability", response_model=list[s.ContractorLiabilityOut]
)
async def get_contractor_liability(
    project_id: int,
    month: int,
    year: int,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    query = (
        select(
            Labour.contractor_id,
            Contractor.name.label("contractor_name"),
            func.sum(LabourPayroll.total_wage).label("total_wage"),
            func.sum(LabourPayroll.paid_amount).label("paid_amount"),
            func.sum(LabourPayroll.remaining_amount).label("remaining_amount"),
        )
        .join(Labour, Labour.id == LabourPayroll.labour_id)
        .outerjoin(Contractor, Contractor.id == Labour.contractor_id)
        .where(
            LabourPayroll.project_id == project_id,
            LabourPayroll.month == month,
            LabourPayroll.year == year,
        )
        .group_by(Labour.contractor_id, Contractor.name)
    )

    result = await db.execute(query)
    rows = result.all()

    output = []
    for r in rows:
        output.append(
            s.ContractorLiabilityOut(
                contractor_id=r.contractor_id,
                contractor_name=r.contractor_name or "Independent",
                total_wage=r.total_wage or Decimal("0"),
                paid_amount=r.paid_amount or Decimal("0"),
                remaining_amount=r.remaining_amount or Decimal("0"),
            )
        )
    return output


@router.put("/{labour_id}", response_model=s.LabourOut)
async def update_labour(
    labour_id: int,
    aadhaar_number: Optional[str] = Form(None),
    pan_number: Optional[str] = Form(None),
    labour_name: Optional[str] = Form(None),
    mobile_number: Optional[str] = Form(None),
    email: Optional[EmailStr] = Form(None),
    address: Optional[str] = Form(None),
    labour_type_id: Optional[int] = Form(None),
    custom_daily_wage_rate: Optional[Decimal] = Form(None),
    custom_ot_rate_per_hour: Optional[Decimal] = Form(None),
    contractor_id: Optional[int] = Form(None),
    status: Optional[LabourStatus] = Form(None),
    notes: Optional[str] = Form(None),
    profile_image: UploadFile = File(None),
    current_user: User = Depends(d.require_roles(LABOUR_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if not obj:
        raise NotFoundError("Labour record not found")

    #  FIX: get ALL mappings
    mappings = await db.scalars(
        select(LabourProject.project_id).where(LabourProject.labour_id == labour_id)
    )

    project_ids = mappings.all()

    # CHECK ANY ACCESS ONLY IF ASSIGNED
    if project_ids:

        allowed = False

        for pid in project_ids:
            try:
                await assert_project_access(
                    db, project_id=pid, current_user=current_user
                )

                allowed = True
                break

            except PermissionDeniedError:
                continue

        if not allowed:
            raise PermissionDeniedError("No access to this labour")

    # data = payload.model_dump(exclude_unset=True)

    data = {
        "aadhaar_number": aadhaar_number,
        "pan_number": pan_number,
        "labour_name": labour_name,
        "mobile_number": mobile_number,
        "email": email,
        "address": address,
        "labour_type_id": labour_type_id,
        "custom_daily_wage_rate": custom_daily_wage_rate,
        "custom_ot_rate_per_hour": custom_ot_rate_per_hour,
        "contractor_id": contractor_id,
        "status": status,
        "notes": notes,
    }

    data = {k: v for k, v in data.items() if v is not None}

    if labour_type_id is not None:

        labour_type = await db.get(LabourType, labour_type_id)

        if not labour_type:
            raise ValidationError("Invalid labour_type_id")

    # PROFILE IMAGE UPDATE
    if profile_image:
        image_path = await validate_and_save_image(
            file=profile_image, upload_dir="uploads/labour", prefix="labour"
        )

        data["profile_image"] = image_path

    for k, v in data.items():
        setattr(obj, k, v)

    # SYNC USER TABLE
    if obj.user_id:
        user = await db.get(User, obj.user_id)

        if user:
            if "labour_name" in data:
                user.full_name = data["labour_name"]

            if "mobile_number" in data:
                user.mobile = data["mobile_number"]

            if "email" in data:
                user.email = data["email"]

            if "aadhaar_number" in data:
                user.aadhaar_number = data["aadhaar_number"]

            if "profile_image" in data:
                user.profile_image = data["profile_image"]

            if "status" in data:
                user.is_active = data["status"] == LabourStatus.ACTIVE

            if "pan_number" in data:
                user.pan_number = data["pan_number"]

            if "address" in data:
                user.address = data["address"]

    await db.flush()

    await db.refresh(
        obj,
        attribute_names=[
            "user",
            "contractor",
            "labour_type",
        ],
    )

    await r.bump_cache_version(redis, VERSION_KEY)
    await r.bump_cache_version(redis, "dashboard_version")

    return s.LabourOut.model_validate(obj, from_attributes=True)


@router.delete("/{labour_id}", status_code=200)
async def delete_labour(
    labour_id: int,
    current_user: User = Depends(d.require_roles(LABOUR_DELETE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    obj = await db.scalar(select(Labour).where(Labour.id == labour_id))

    if not obj:
        raise NotFoundError("Labour record not found")

    #  FIX: multi-project validation
    mappings = await db.scalars(
        select(LabourProject.project_id).where(LabourProject.labour_id == labour_id)
    )

    project_ids = mappings.all()

    # If labour assigned to projects -> validate access
    if project_ids:
        allowed = False

        for pid in project_ids:
            try:
                await assert_project_access(
                    db, project_id=pid, current_user=current_user
                )
                allowed = True
                break

            except PermissionDeniedError:
                continue

        if not allowed:
            raise PermissionDeniedError("No access to this labour")

    if obj.user_id:
        user = await db.get(User, obj.user_id)

        if user:
            user.is_active = False

    obj.status = LabourStatus.INACTIVE

    await db.flush()

    await r.bump_cache_version(redis, VERSION_KEY)
    await r.bump_cache_version(redis, "dashboard_version")

    return {"message": "Labour deactivated successfully"}


@router.post("/assign-project", response_model=s.LabourProjectOut)
async def assign_labour_to_project(
    payload: s.LabourAssignProject,
    current_user: User = Depends(d.require_roles(LABOUR_DELETE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    # check labour
    labour = await db.get(Labour, payload.labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    # check project access
    await assert_project_access(
        db, project_id=payload.project_id, current_user=current_user
    )

    # prevent duplicate assignment
    existing = await db.scalar(
        select(LabourProject).where(
            LabourProject.labour_id == payload.labour_id,
            LabourProject.project_id == payload.project_id,
        )
    )

    if existing:
        raise ValidationError("Labour already assigned to this project")

    obj = LabourProject(
        labour_id=payload.labour_id,
        project_id=payload.project_id,
    )

    db.add(obj)
    await db.flush()
    await db.refresh(obj)

    return s.LabourProjectOut.model_validate(obj)


# @router.put(
#     "/attendance/{attendance_id}/check-out", response_model=UserAttendanceOut
# )
# async def check_out(
#     attendance_id: int,
#     latitude: float = Form(...),
#     longitude: float = Form(...),
#     location_address: str = Form(...),
#     check_out_image: UploadFile = File(...),
#     current_user: User = Depends(d.require_roles(LABOUR_WRITE_ROLES)),
#     db: AsyncSession = Depends(get_db_session),
#     redis=Depends(d.get_request_redis),
# ):
#     obj = await db.get(UserAttendance, attendance_id)
#     if not obj:
#         raise NotFoundError("Attendance not found")

#     labour = await db.scalar(select(Labour).where(Labour.user_id == obj.user_id))
#     if not labour:
#         raise NotFoundError("Labour not found")

#     await assert_project_access(
#         db, project_id=obj.project_id, current_user=current_user
#     )

#     if obj.out_time:
#         raise ValidationError("Already checked-out")

#     #  ALWAYS USE IST SERVER TIME
#     now = datetime.now(ZoneInfo("Asia/Kolkata"))
#     out_time = now.time()

#     if not obj.in_time:
#         raise ValidationError("Check-in time missing")

#     in_dt = datetime.combine(obj.attendance_date, obj.in_time)
#     out_dt = datetime.combine(obj.attendance_date, out_time)

#     if out_dt < in_dt:
#         out_dt = out_dt + timedelta(days=1)

#     total_hours = (out_dt - in_dt).total_seconds() / 3600

#     if total_hours <= 0:
#         raise ValidationError("Invalid time range")

#     total_hours = round(total_hours, 2)

#     # HALF-DAY LOGIC (UNCHANGED)
#     if obj.status == AttendanceStatus.HALF_DAY:
#         working_hours = Decimal("4")
#         overtime_hours = Decimal("0")
#     else:
#         working_hours = min(Decimal(str(total_hours)), Decimal("8"))
#         calculated_ot = Decimal(str(total_hours)) - working_hours
#         overtime_hours = max( Decimal("0"), calculated_ot )

#     if working_hours + overtime_hours > 24:
#         raise ValidationError("Total hours > 24")

#     # ==================================
#     # PROJECT OT POLICY
#     # ==================================

#     project = await db.get(Project, obj.project_id)

#     if not project:
#         raise NotFoundError("Project not found")

#     policy = await db.scalar(
#         select(ProjectOTPolicy).where(
#             ProjectOTPolicy.project_id == obj.project_id
#         )
#     )

#     hourly_rate = labour.daily_wage_rate / Decimal("8")

#     overtime_rate = Decimal("0")

#     if policy and overtime_hours > 0:

#         today = obj.attendance_date.weekday()

#         # FIXED RATE
#         if policy.policy_type == OTPolicyType.FIXED_RATE:

#             overtime_rate = (
#                 policy.fixed_ot_rate
#                 or Decimal("0")
#             )

#         # MULTIPLIER
#         else:

#             multiplier = (
#                 policy.normal_day_multiplier
#                 or Decimal("1.5")
#             )

#             # Sunday
#             if today == 6:
#                 multiplier = (
#                     policy.sunday_multiplier
#                     or Decimal("2.0")
#                 )

#             # TODO: Holiday Calendar
#             # Temporary manual holiday logic

#             is_holiday = False

#             if is_holiday:
#                 multiplier = (
#                     policy.holiday_multiplier
#                     or Decimal("3.0")
#                 )

#             overtime_rate = hourly_rate * multiplier

#     # ==================================
#     # CHECKOUT IMAGE
#     # ==================================

#     check_out_path = await validate_and_save_image(
#         check_out_image,
#         "uploads/labour_attendance",
#         "checkout"
#     )
#     obj.out_time = out_time
#     obj.working_hours = working_hours
#     obj.overtime_hours = overtime_hours
#     obj.overtime_rate = overtime_rate
#     obj.check_out_image = check_out_path

#     obj.check_out_latitude = Decimal(str(latitude))
#     obj.check_out_longitude = Decimal(str(longitude))
#     obj.check_out_address = location_address

#     # Auto-approve attendance upon checkout
#     obj.is_approved = True
#     obj.approved_by_id = current_user.id

#     await db.flush()

#     total_wage = hourly_rate * working_hours + overtime_rate * overtime_hours

#     total_wage = total_wage.quantize(
#         Decimal("0.01")
#     )

#     existing_expense = await db.scalar(
#         select(Expense).where(
#             Expense.project_id == obj.project_id,
#             Expense.labour_id == labour.id,
#             Expense.category == "Labour",
#             Expense.expense_date == obj.attendance_date,
#         )
#     )

#     if existing_expense:
#         existing_expense.amount = total_wage
#         expense = existing_expense
#     else:
#         expense = Expense(
#             project_id=obj.project_id,
#             labour_id=labour.id,
#             category="Labour",
#             description=f"Labour expense - {obj.attendance_date}",
#             amount=total_wage,
#             expense_date=obj.attendance_date,
#             payment_mode="auto",
#         )
#         db.add(expense)
#         await db.flush()

#     if not project:
#         raise NotFoundError("Project not found")

#     db.add(
#         OwnerTransaction(
#             owner_id=project.owner_id,
#             project_id=obj.project_id,
#             type="debit",
#             amount=total_wage,
#             reference_type="labour",
#             reference_id=expense.id,
#             description=f"Labour expense ({obj.attendance_date})",
#         )
#     )

#     await r.bump_cache_version(redis, ATTENDANCE_VERSION_KEY)
#     await r.bump_cache_version(redis, "dashboard_version")

#     await db.refresh(obj)
#     return obj


@router.get("/{labour_id}/weekly-report")
async def weekly_report(
    labour_id: int,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from app.utils.common import assert_project_access

    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    mapping = await db.scalars(
        select(LabourProject.project_id).where(LabourProject.labour_id == labour_id)
    )

    project_ids = mapping.all()

    # Validate access only if labour assigned to projects
    if project_ids:

        allowed = False

        for pid in project_ids:
            try:
                await assert_project_access(
                    db, project_id=pid, current_user=current_user
                )

                allowed = True
                break

            except PermissionDeniedError:
                continue

        if not allowed:
            raise PermissionDeniedError("No access to this labour")

    result = await db.execute(
        select(
            extract("week", UserAttendance.attendance_date).label("week"),
            # total days
            func.count(UserAttendance.id).label("total_days"),
            # absent days
            func.sum(
                case((UserAttendance.status == AttendanceStatus.ABSENT, 1), else_=0)
            ).label("absent_days"),
            func.sum(
                case((UserAttendance.status == AttendanceStatus.HALF_DAY, 1), else_=0)
            ).label("half_days"),
            # existing
            func.sum(UserAttendance.working_hours).label("hours"),
            func.sum(UserAttendance.overtime_hours).label("ot"),
            func.sum(
                UserAttendance.overtime_hours * UserAttendance.overtime_rate
            ).label("ot_wage"),
        )
        .join(Labour, Labour.user_id == UserAttendance.user_id)
        .where(Labour.id == labour_id)
        .group_by("week")
    )

    rows = result.all()
    hourly_rate = labour.effective_daily_wage / Decimal("8")

    return [
        {
            "week": int(r.week),
            "total_days": int(r.total_days or 0),
            "absent_days": int(r.absent_days or 0),
            "half_days": int(r.half_days or 0),
            "present_days": int(
                (r.total_days or 0) - (r.absent_days or 0) - (r.half_days or 0)
            ),
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
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from app.utils.common import assert_project_access

    labour = await db.get(Labour, labour_id)
    if not labour:
        raise NotFoundError("Labour not found")

    mapping = await db.scalars(
        select(LabourProject.project_id).where(LabourProject.labour_id == labour_id)
    )

    project_ids = mapping.all()

    # Validate access only if labour assigned to projects
    if project_ids:

        allowed = False

        for pid in project_ids:
            try:
                await assert_project_access(
                    db, project_id=pid, current_user=current_user
                )

                allowed = True
                break

            except PermissionDeniedError:
                continue

        if not allowed:
            raise PermissionDeniedError("No access to this labour")

    result = await db.execute(
        select(
            extract("month", UserAttendance.attendance_date).label("month"),
            # total days
            func.count(UserAttendance.id).label("total_days"),
            # absent days
            func.sum(
                case((UserAttendance.status == AttendanceStatus.ABSENT, 1), else_=0)
            ).label("absent_days"),
            func.sum(
                case((UserAttendance.status == AttendanceStatus.HALF_DAY, 1), else_=0)
            ).label("half_days"),
            # existing
            func.sum(UserAttendance.working_hours).label("hours"),
            func.sum(UserAttendance.overtime_hours).label("ot"),
            func.sum(
                UserAttendance.overtime_hours * UserAttendance.overtime_rate
            ).label("ot_wage"),
        )
        .join(Labour, Labour.user_id == UserAttendance.user_id)
        .where(Labour.id == labour_id)
        .group_by("month")
    )

    rows = result.all()
    hourly_rate = labour.effective_daily_wage / Decimal("8")

    return [
        {
            "month": int(r.month),
            "total_days": int(r.total_days or 0),
            "absent_days": int(r.absent_days or 0),
            "half_days": int(r.half_days or 0),
            "present_days": int(
                (r.total_days or 0) - (r.absent_days or 0) - (r.half_days or 0)
            ),
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
    current_user: User = Depends(
        d.require_roles(
            [
                r.value
                for r in [
                    UserRole.ADMIN,
                    UserRole.PROJECT_MANAGER,
                    UserRole.ACCOUNTANT,
                    UserRole.SITE_ENGINEER,
                ]
            ]
        )
    ),
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
            select(UserAttendance, Labour)
            .join(Labour, Labour.user_id == UserAttendance.user_id)
            .where(
                extract("month", UserAttendance.attendance_date) == payload.month,
                extract("year", UserAttendance.attendance_date) == payload.year,
                UserAttendance.project_id.in_(project_ids),
            )
        )

        rows = result.all()
        if not rows:
            return []

        payroll_map = {}

        # =========================
        # BUILD PAYROLL DATA
        # =========================
        for row, labour in rows:

            hourly_rate = Decimal(str(labour.effective_daily_wage or 0)) / Decimal("8")

            #  STATUS-BASED WAGE FIX
            if row.status == AttendanceStatus.ABSENT:
                wage = Decimal("0")
                working_hours = Decimal("0")

            elif row.status == AttendanceStatus.HALF_DAY:

                working_hours = Decimal("4")

                ot_rate = Decimal(str(row.overtime_rate or 0))
                ot_hours = Decimal(str(row.overtime_hours or 0))

                wage = hourly_rate * working_hours + ot_rate * ot_hours

            else:

                working_hours = Decimal(str(row.working_hours or 0))

                ot_rate = Decimal(str(row.overtime_rate or 0))
                ot_hours = Decimal(str(row.overtime_hours or 0))

                wage = hourly_rate * working_hours + ot_rate * ot_hours

            key = (labour.id, row.project_id)

            if key not in payroll_map:
                payroll_map[key] = {
                    "working_hours": Decimal("0"),
                    "overtime_hours": Decimal("0"),
                    "total_wage": Decimal("0"),
                }

            payroll_map[key]["working_hours"] += working_hours
            payroll_map[key]["overtime_hours"] += row.overtime_hours or Decimal("0")
            payroll_map[key]["total_wage"] += wage

        output = []

        # =========================
        # SAVE / UPDATE PAYROLL
        # =========================
        for (labour_id, project_id), data in payroll_map.items():

            total_wage = data["total_wage"]

            #  ADVANCE ADJUSTMENT
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

            #  STATUS LOGIC
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
# PAYROLL PAYMENT
# =========================
@router.post("/payroll/pay")
async def pay_salary(
    payload: s.PayrollPayment,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):

    await assert_project_access(
        db,
        project_id=payload.project_id,
        current_user=current_user,
    )

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

    # =========================
    # UPDATE PAYROLL
    # =========================
    payroll.paid_amount += payload.amount
    payroll.remaining_amount = max(
        Decimal("0"), payroll.remaining_amount - payload.amount
    )

    payroll.status = (
        PayrollStatus.PAID if payroll.remaining_amount == 0 else PayrollStatus.PARTIAL
    )

    await db.flush()

    # =========================
    #  TRANSACTION (ONLY ONCE)
    # =========================
    txn = Transaction(
        project_id=payload.project_id,
        invoice_id=None,
        type="payment",
        amount=payload.amount,
        mode="cash",
        reference=f"payroll:{payload.labour_id}",
        created_by=current_user.id,
    )
    db.add(txn)

    # =========================
    #  JOURNAL ENTRY
    # =========================
    entry = JournalEntry(description="Salary Payment")
    db.add(entry)
    await db.flush()  # get entry.id

    #  replace with real IDs later
    EXPENSE_ACCOUNT_ID = 1  # Salary Expense
    CASH_ACCOUNT_ID = 2  # Cash / Bank

    db.add(
        JournalLine(
            entry_id=entry.id,
            account_id=EXPENSE_ACCOUNT_ID,
            debit=payload.amount,
            credit=0,
        )
    )

    db.add(
        JournalLine(
            entry_id=entry.id,
            account_id=CASH_ACCOUNT_ID,
            debit=0,
            credit=payload.amount,
        )
    )

    # =========================
    # CACHE
    # =========================
    await r.bump_cache_version(redis, "dashboard_version")

    return payroll


# =========================
# ADVANCE
# =========================
@router.post("/advance")
async def advance_payment(
    payload: s.AdvancePayment,
    current_user: User = Depends(d.require_roles(PAYROLL_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):

    await assert_project_access(
        db,
        project_id=payload.project_id,
        current_user=current_user,
    )

    labour = await db.get(Labour, payload.labour_id)
    project = await db.get(Project, payload.project_id)

    if not labour or not project:
        raise NotFoundError("Invalid labour or project")

    mapping = await db.scalar(
        select(LabourProject).where(
            LabourProject.labour_id == payload.labour_id,
            LabourProject.project_id == payload.project_id,
        )
    )

    if not mapping:
        raise ValidationError("Labour not assigned to this project")

    db.add(
        Expense(
            project_id=payload.project_id,
            labour_id=payload.labour_id,
            category="Labour Advance",
            amount=payload.amount,
            description=payload.description,
            expense_date=date.today(),
            payment_mode="CASH",
        )
    )

    await db.flush()

    await r.bump_cache_version(redis, "dashboard_version")

    return {"message": "Advance recorded"}


# =========================
# DASHBOARD
# =========================
@router.get("/attendance/dashboard")
async def attendance_dashboard(
    project_id: int,
    from_date: date,
    to_date: date,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
):

    await assert_project_access(db, project_id=project_id, current_user=current_user)

    # Total labour
    total_labour = await db.scalar(
        select(func.count())
        .select_from(LabourProject)
        .where(LabourProject.project_id == project_id)
    )

    # Present
    present_today = await db.scalar(
        select(func.count())
        .select_from(UserAttendance)
        .where(
            UserAttendance.project_id == project_id,
            UserAttendance.attendance_date.between(from_date, to_date),
            UserAttendance.status == AttendanceStatus.PRESENT,
        )
    )

    return {
        "total_labour": total_labour or 0,
        "present": present_today or 0,
    }


@router.get("/dashboard/stats")
async def dashboard_stats(
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    today = date.today()

    project_ids = await get_user_project_ids(db, current_user)

    total_labour = await db.scalar(
        select(func.count())
        .select_from(UserAttendance)
        .where(
            UserAttendance.attendance_date == today,
            UserAttendance.project_id.in_(project_ids),
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
    current_user: User = Depends(
        d.require_roles(
            [
                r.value
                for r in [
                    UserRole.ADMIN,
                    UserRole.PROJECT_MANAGER,
                    UserRole.SITE_ENGINEER,
                ]
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)

    result = await db.execute(
        select(Labour)
        .join(LabourProject, LabourProject.labour_id == Labour.id)
        .where(
            Labour.contractor_id == contractor_id,
            LabourProject.project_id.in_(project_ids),
        )
    )

    rows = result.scalars().all()

    return [s.LabourOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/summary/skill")
async def labour_skill_summary(
    project_id: int,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(
        db,
        project_id=project_id,
        current_user=current_user,
    )

    result = await db.execute(
        select(LabourType.skill_category, func.count(Labour.id))
        .join(LabourProject)
        .join(LabourType, Labour.labour_type_id == LabourType.id)
        .where(LabourProject.project_id == project_id)
        .group_by(LabourType.skill_category)
    )

    rows = result.fetchall()

    return [{"skill_type": r[0], "count": r[1]} for r in rows]


# =========================
# EXCEL EXPORT
# =========================
@router.get("/report/export")
async def export_excel(
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    project_id: int = Query(...),
    db: AsyncSession = Depends(get_db_session),
):

    await assert_project_access(
        db,
        project_id=project_id,
        current_user=current_user,
    )

    project_ids = await get_user_project_ids(db, current_user)
    result = await db.execute(
        select(Labour)
        .join(LabourProject)
        .where(
            LabourProject.project_id == project_id, Labour.status == LabourStatus.ACTIVE
        )
        .order_by(Labour.created_at.desc())
    )
    rows = result.scalars().all()

    data = [
        {
            "Name": r.labour_name,
            "Skill": r.skill_category,
            "Wage": float(r.effective_daily_wage),
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


@router.get("/attendance/export")
async def export_attendance_excel(
    project_id: int,
    from_date: date,
    to_date: date,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
):

    await assert_project_access(db, project_id=project_id, current_user=current_user)

    query = (
        select(UserAttendance, Labour)
        .join(Labour, Labour.user_id == UserAttendance.user_id)
        .where(
            UserAttendance.project_id == project_id,
            UserAttendance.attendance_date.between(from_date, to_date),
            Labour.status == LabourStatus.ACTIVE,
        )
    )

    result = await db.execute(query)
    rows = result.all()

    data = []

    for att, labour in rows:
        data.append(
            {
                "Labour ID": labour.id,
                "Name": labour.labour_name,
                "Date": att.attendance_date,
                "Check In": att.in_time,
                "Check Out": att.out_time,
                "Working Hours": float(att.working_hours or 0),
                "Overtime": float(att.overtime_hours or 0),
                "Task ID": att.task_id,
                "Check-In Location": att.check_in_address,
                "Check-Out Location": att.check_out_address,
                "Status": att.status.value,
            }
        )

    df = pd.DataFrame(data)

    stream = io.BytesIO()
    df.to_excel(stream, index=False, engine="openpyxl")
    stream.seek(0)

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=attendance.xlsx"},
    )


from typing import Optional, Literal
from datetime import date
from fastapi import Query

@router.get("/payroll/export")
async def export_payroll_excel(
    month: Optional[int] = None,
    year: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    labour_id: Optional[int] = None,
    format: Literal["excel", "pdf"] = Query("excel", description="Export format"),
    current_user: User = Depends(d.require_roles(PAYROLL_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)

    query = select(LabourPayroll, Labour).join(Labour, Labour.id == LabourPayroll.labour_id)
    query = query.where(LabourPayroll.project_id.in_(project_ids))

    if month is not None:
        query = query.where(LabourPayroll.month == month)
    if year is not None:
        query = query.where(LabourPayroll.year == year)
    if labour_id is not None:
        query = query.where(LabourPayroll.labour_id == labour_id)

    if start_date:
        start_idx = start_date.year * 12 + start_date.month
        query = query.where((LabourPayroll.year * 12 + LabourPayroll.month) >= start_idx)
    if end_date:
        end_idx = end_date.year * 12 + end_date.month
        query = query.where((LabourPayroll.year * 12 + LabourPayroll.month) <= end_idx)

    result = await db.execute(query.order_by(LabourPayroll.year.desc(), LabourPayroll.month.desc()))

    rows = result.all()

    data = []
    for payroll, labour in rows:
        data.append(
            {
                "Labour": labour.labour_name,
                "Working Hours": float(payroll.total_working_hours),
                "OT Hours": float(payroll.total_overtime_hours),
                "Total Wage": float(payroll.total_wage),
                "Paid": float(payroll.paid_amount),
                "Remaining": float(payroll.remaining_amount),
                "Status": payroll.status.value,
            }
        )

    if format == "pdf":
        import io
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
        
        table_data = [["Labour Name", "Working Hours", "OT Hours", "Total Wage", "Paid Amount", "Remaining Amount", "Status"]]
        for d in data:
            table_data.append([d["Labour"], d["Working Hours"], d["OT Hours"], d["Total Wage"], d["Paid"], d["Remaining"], d["Status"]])
            
        t = Table(table_data)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
        ]))
        
        elements = []
        styles = getSampleStyleSheet()
        elements.append(Paragraph("Payroll Export", styles['Title']))
        elements.append(Spacer(1, 12))
        elements.append(t)
        
        doc.build(elements)
        buffer.seek(0)
        
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=payroll.pdf"},
        )

    df = pd.DataFrame(data)

    stream = io.BytesIO()
    df.to_excel(stream, index=False, engine="openpyxl")
    stream.seek(0)

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=payroll.xlsx"},
    )


@router.get("/{labour_id}", response_model=s.LabourOut)
async def get_labour(
    labour_id: int,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    from app.utils.common import assert_project_access

    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:labour:get:{version}:{labour_id}"

    #  CACHE HIT
    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return s.LabourOut.model_validate(cached)

    #  FETCH LABOUR
    obj = await db.scalar(
        select(Labour)
        .options(
            selectinload(Labour.user),
            selectinload(Labour.contractor),
            selectinload(Labour.labour_type),
        )
        .where(Labour.id == labour_id)
    )

    if not obj:
        raise NotFoundError("Labour record not found")

    #  MANY-TO-MANY ACCESS CHECK
    result = await db.scalars(
        select(LabourProject.project_id).where(LabourProject.labour_id == labour_id)
    )

    project_ids = result.all()

    # If labour is assigned to projects,
    # validate access against at least one project

    if project_ids:

        allowed = False

        for pid in project_ids:
            try:
                await assert_project_access(
                    db, project_id=pid, current_user=current_user
                )

                allowed = True
                break

            except PermissionDeniedError:
                continue

        if not allowed:
            raise PermissionDeniedError("No access to this labour")

    out = s.LabourOut.model_validate(obj, from_attributes=True)

    await r.cache_set_json(redis, cache_key, out.model_dump())

    return out


@router.get("/payroll/weekly-velocity", response_model=list[s.WeeklyVelocityOut])
async def get_weekly_velocity(
    project_id: int,
    month: int,
    year: int,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    query = (
        select(
            extract("week", UserAttendance.attendance_date).label("week_number"),
            func.count(UserAttendance.id).label("attendance_count"),
            func.sum(UserAttendance.working_hours).label("working_hours"),
            func.sum(UserAttendance.overtime_hours).label("ot_hours"),
            func.sum(
                (
                    func.coalesce(
                        Labour.custom_daily_wage_rate, LabourType.default_daily_wage
                    )
                    / Decimal("8")
                )
                * UserAttendance.working_hours
                + UserAttendance.overtime_rate * UserAttendance.overtime_hours
            ).label("total_wage"),
            Labour.id.label("labour_id"),
        )
        .join(Labour, Labour.user_id == UserAttendance.user_id)
        .join(LabourType, Labour.labour_type_id == LabourType.id)
        .where(
            UserAttendance.project_id == project_id,
            extract("month", UserAttendance.attendance_date) == month,
            extract("year", UserAttendance.attendance_date) == year,
        )
        .group_by("week_number", Labour.id)
    )

    result = await db.execute(query)
    rows = result.all()

    output = []
    for r in rows:
        output.append(
            s.WeeklyVelocityOut(
                week_number=int(r.week_number),
                total_wage=r.total_wage or Decimal("0"),
                attendance_count=r.attendance_count or 0,
            )
        )
    return output


@router.get(
    "/payroll/disbursement-history", response_model=list[s.DisbursementHistoryOut]
)
async def get_disbursement_history(
    project_id: int,
    month: int,
    year: int,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    query = (
        select(Transaction)
        .where(
            Transaction.project_id == project_id,
            Transaction.type == "payment",
            Transaction.reference.like("payroll:%"),
            extract("month", Transaction.created_at) == month,
            extract("year", Transaction.created_at) == year,
        )
        .order_by(Transaction.created_at.desc())
    )

    result = await db.execute(query)
    txns = result.scalars().all()

    if not txns:
        return []

    labour_txn_map = {}
    for txn in txns:
        try:
            labour_id = int(txn.reference.replace("payroll:", ""))
            labour_txn_map[labour_id] = txn
        except ValueError:
            continue

    if not labour_txn_map:
        return []

    labour_result = await db.execute(
        select(Labour).where(Labour.id.in_(list(labour_txn_map.keys())))
    )
    labours = {l.id: l for l in labour_result.scalars().all()}

    output = []
    for labour_id, txn in labour_txn_map.items():
        labour = labours.get(labour_id)
        if not labour:
            continue
        output.append(
            s.DisbursementHistoryOut(
                id=txn.id,
                labour_id=labour.id,
                labour_name=labour.labour_name,
                amount=txn.amount,
                mode=txn.mode,
                reference=txn.reference,
                created_at=txn.created_at,
            )
        )
    return output


@router.get("/payroll/fiscal-summary", response_model=s.FiscalSummaryOut)
async def get_fiscal_summary(
    project_id: int,
    month: int,
    year: int,
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    payroll_sum = await db.execute(
        select(
            func.sum(LabourPayroll.total_wage).label("total_payout"),
            func.sum(case((LabourPayroll.total_wage > 5000, 1), else_=0)).label(
                "high_payouts"
            ),
        ).where(
            LabourPayroll.project_id == project_id,
            LabourPayroll.month == month,
            LabourPayroll.year == year,
        )
    )
    res = payroll_sum.first()

    total_payout = (
        res.total_payout if res and res.total_payout is not None else Decimal("0")
    )

    high_payouts = res.high_payouts if res and res.high_payouts is not None else 0

    # Calculate only Labour Advance expenses for the selected month
    advance_adjusted = await db.scalar(
        select(func.sum(Expense.amount)).where(
            Expense.project_id == project_id,
            Expense.category == "Labour Advance",
            extract("month", Expense.expense_date) == month,
            extract("year", Expense.expense_date) == year,
        )
    ) or Decimal("0")

    ot_count = (
        await db.scalar(
            select(func.count(func.distinct(Labour.id)))
            .select_from(UserAttendance)
            .join(Labour, Labour.user_id == UserAttendance.user_id)
            .where(
                UserAttendance.project_id == project_id,
                extract("month", UserAttendance.attendance_date) == month,
                extract("year", UserAttendance.attendance_date) == year,
                UserAttendance.overtime_hours > 0,
            )
        )
        or 0
    )

    return s.FiscalSummaryOut(
        total_payout=total_payout,
        high_payouts=high_payouts,
        ot_intensive=ot_count,
        advance_adjusted=advance_adjusted,
    )


@router.get("/payroll/momentum", response_model=list[s.PayrollMomentumOut])
async def get_payroll_momentum(
    project_id: int,
    months: int = Query(6, ge=1, le=12),
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    today = date.today()
    period_start = today - timedelta(days=30 * months)

    query = (
        select(
            LabourPayroll.month,
            LabourPayroll.year,
            func.sum(LabourPayroll.total_wage).label("total_wage"),
        )
        .where(
            LabourPayroll.project_id == project_id,
            or_(
                LabourPayroll.year > period_start.year,
                and_(
                    LabourPayroll.year == period_start.year,
                    LabourPayroll.month >= period_start.month,
                ),
            ),
        )
        .group_by(LabourPayroll.year, LabourPayroll.month)
        .order_by(LabourPayroll.year.desc(), LabourPayroll.month.desc())
        .limit(months)
    )

    result = await db.execute(query)
    rows = result.all()

    month_names = {
        1: "Jan",
        2: "Feb",
        3: "Mar",
        4: "Apr",
        5: "May",
        6: "Jun",
        7: "Jul",
        8: "Aug",
        9: "Sep",
        10: "Oct",
        11: "Nov",
        12: "Dec",
    }

    output = []
    for r in reversed(rows):
        output.append(
            s.PayrollMomentumOut(
                month=r.month,
                year=r.year,
                period_name=month_names.get(r.month, str(r.month)),
                total_wage=r.total_wage or Decimal("0"),
            )
        )
    return output


# ==========================================================
# FIX 3: Replace the ENTIRE get_aggregate_report() function
# with the version below
# ==========================================================


@router.get("/payroll/aggregate-report", response_model=list[s.AggregateReportOut])
async def get_aggregate_report(
    project_id: int,
    month: int,
    year: int,
    group_by: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    current_user: User = Depends(d.require_roles(LABOUR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(
        db,
        project_id=project_id,
        current_user=current_user,
    )

    # ------------------------------------------------------
    # Determine grouping expression
    # ------------------------------------------------------
    if group_by == "daily":
        period_expr = UserAttendance.attendance_date
    elif group_by == "weekly":
        period_expr = extract("week", UserAttendance.attendance_date)
    else:  # monthly
        # Monthly report remains labour-wise summary
        period_expr = None

    # ------------------------------------------------------
    # Base columns
    # ------------------------------------------------------
    columns = [
        Labour.id.label("labour_id"),
        Labour.labour_name,
        LabourType.skill_category.label("skill_category"),
        func.coalesce(
            Labour.custom_daily_wage_rate, LabourType.default_daily_wage
        ).label("daily_wage"),
        func.count(
            case(
                (UserAttendance.status == AttendanceStatus.PRESENT, 1),
                else_=None,
            )
        ).label("days_present"),
        func.coalesce(
            func.sum(UserAttendance.overtime_hours),
            Decimal("0"),
        ).label("ot_hours"),
        func.coalesce(
            func.sum(
                (
                    func.coalesce(
                        Labour.custom_daily_wage_rate, LabourType.default_daily_wage
                    )
                    / Decimal("8")
                )
                * UserAttendance.working_hours
                + UserAttendance.overtime_rate * UserAttendance.overtime_hours
            ),
            Decimal("0"),
        ).label("total_wage_earned"),
        Labour.status.label("status"),
    ]

    # Add period column for daily/weekly
    if period_expr is not None:
        columns.append(period_expr.label("period"))

    # ------------------------------------------------------
    # Build query
    # ------------------------------------------------------
    query = (
        select(*columns)
        .join(LabourProject, LabourProject.labour_id == Labour.id)
        .join(LabourType, Labour.labour_type_id == LabourType.id)
        .outerjoin(
            UserAttendance,
            and_(
                Labour.user_id == UserAttendance.user_id,
                UserAttendance.project_id == project_id,
                extract("month", UserAttendance.attendance_date) == month,
                extract("year", UserAttendance.attendance_date) == year,
            ),
        )
        .where(LabourProject.project_id == project_id)
    )

    # ------------------------------------------------------
    # Group by
    # ------------------------------------------------------
    group_columns = [
        Labour.id,
        Labour.labour_name,
        LabourType.skill_category,
        Labour.custom_daily_wage_rate,
        LabourType.default_daily_wage,
        Labour.status,
    ]

    if period_expr is not None:
        group_columns.append(period_expr)

    query = query.group_by(*group_columns)

    # ------------------------------------------------------
    # Ordering
    # ------------------------------------------------------
    if period_expr is not None:
        query = query.order_by("period", Labour.labour_name)
    else:
        query = query.order_by(Labour.labour_name)

    # ------------------------------------------------------
    # Execute
    # ------------------------------------------------------
    result = await db.execute(query)
    rows = result.all()

    # ------------------------------------------------------
    # Build response
    # Note: response schema remains unchanged, so "period"
    # is used only for grouping and ordering.
    # ------------------------------------------------------
    output = []

    for r in rows:
        output.append(
            s.AggregateReportOut(
                labour_id=r.labour_id,
                labour_name=r.labour_name,
                skill_category=r.skill_category,
                daily_wage=r.daily_wage,
                days_present=r.days_present or 0,
                ot_hours=r.ot_hours or Decimal("0"),
                total_wage_earned=r.total_wage_earned or Decimal("0"),
                status=(
                    r.status.value if hasattr(r.status, "value") else str(r.status)
                ),
            )
        )

    return output
