from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, File, UploadFile, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, func, select
from app.core.dependencies import get_current_user, require_roles
from app.core.enums import AttendanceStatus
from app.db.session import get_db_session
from app.models.user import User, UserAttendance
from app.models.project import Project
from app.schemas.user import UserAttendanceOut
from datetime import datetime, date, timedelta
from app.utils.helpers import calculate_distance
from app.core.validators import (
    validate_and_save_image,
    validate_and_save_document,
)
from app.models.labour import Labour
from app.models.project import ProjectOTPolicy
from app.models.expense import Expense
from app.models.owner import OwnerTransaction
from app.core.enums import OTPolicyType

router = APIRouter(prefix="/attendance", tags=["Attendance"])

# Roles allowed to approve attendance
APPROVE_ROLES = ["Admin", "ProjectManager", "SiteEngineer"]

UPLOAD_DIR = "uploads/attendance"


# ===================== CHECK-IN =====================


@router.post("/check-in", response_model=UserAttendanceOut)
async def check_in(
    attendance_date: date = Form(...),
    project_id: Optional[int] = Form(None),
    status: str = Form(AttendanceStatus.PRESENT.value),
    in_time: Optional[datetime] = Form(None),
    check_in_latitude: Optional[float] = Form(None),
    check_in_longitude: Optional[float] = Form(None),
    check_in_address: Optional[str] = Form(None),
    task_id: Optional[int] = Form(None),
    task_description: Optional[str] = Form(None),
    remarks: Optional[str] = Form(None),
    work_location_type: Optional[str] = Form(None),
    check_in_image: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    # Check if already checked in today
    result = await db.execute(
        select(UserAttendance).where(
            UserAttendance.user_id == current_user.id,
            UserAttendance.attendance_date == attendance_date,
        )
    )
    existing = result.scalars().first()

    if existing:
        raise HTTPException(status_code=400, detail="Already checked in for this date")

    actual_in_time = in_time or datetime.now()
    is_outside_geofence = False
    is_late = False
    late_minutes = 0

    if project_id:
        project_result = await db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = project_result.scalars().first()
        if project:
            # Geofencing Check - bypass if WFH
            if (
                work_location_type != "WFH"
                and project.latitude is not None
                and project.longitude is not None
                and check_in_latitude is not None
                and check_in_longitude is not None
            ):
                dist = calculate_distance(
                    lat1=check_in_latitude,
                    lon1=check_in_longitude,
                    lat2=project.latitude,
                    lon2=project.longitude,
                )
                if dist > 200:
                    is_outside_geofence = True

            # Late Detection Logic
            # Normalize timezone-aware datetime
            if actual_in_time.tzinfo is not None:
                actual_in_time = actual_in_time.astimezone().replace(tzinfo=None)

            # Late Detection Logic
            if project.shift_start_time:

                shift_start_dt = datetime.combine(
                    attendance_date, project.shift_start_time
                )

                grace_mins = project.grace_period_minutes or 15

                shift_start_with_grace = shift_start_dt + timedelta(minutes=grace_mins)

                if actual_in_time > shift_start_with_grace:

                    is_late = True

                    late_minutes = int(
                        (actual_in_time - shift_start_dt).total_seconds() / 60
                    )

    # Save check-in image if uploaded
    check_in_image_path = None
    if check_in_image and check_in_image.filename:
        check_in_image_path = await validate_and_save_image(
            check_in_image, UPLOAD_DIR, "checkin"
        )

    attendance = UserAttendance(
        user_id=current_user.id,
        project_id=project_id,
        attendance_date=attendance_date,
        status=status,
        in_time=actual_in_time,
        check_in_image=check_in_image_path,
        check_in_latitude=check_in_latitude,
        check_in_longitude=check_in_longitude,
        check_in_address=check_in_address,
        task_id=task_id,
        task_description=task_description,
        remarks=remarks,
        work_location_type=work_location_type,
        is_outside_geofence=is_outside_geofence,
        is_late=is_late,
        late_minutes=late_minutes,
    )
    db.add(attendance)
    await db.commit()
    await db.refresh(attendance)
    return attendance


# ===================== CHECK-OUT =====================


@router.put("/check-out/{attendance_id}", response_model=UserAttendanceOut)
async def check_out(
    attendance_id: int,
    out_time: Optional[datetime] = Form(None),
    check_out_latitude: Optional[float] = Form(None),
    check_out_longitude: Optional[float] = Form(None),
    check_out_address: Optional[str] = Form(None),
    work_summary: str = Form(...),
    task_deadline_reason: Optional[str] = Form(None),
    work_report_pdf: Optional[UploadFile] = File(None),
    check_out_image: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(UserAttendance).where(
            UserAttendance.id == attendance_id,
            UserAttendance.user_id == current_user.id,
        )
    )
    attendance = result.scalars().first()

    labour = await db.scalar(select(Labour).where(Labour.user_id == current_user.id))

    if not attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    if attendance.out_time:
        raise HTTPException(status_code=400, detail="Already checked out")

    actual_out_time = out_time or datetime.now()

    if actual_out_time.tzinfo is not None:
        actual_out_time = actual_out_time.replace(tzinfo=None)

    # Early Departure Detection
    is_early_departure = False
    early_minutes = 0
    if attendance.project_id:
        project_result = await db.execute(
            select(Project).where(Project.id == attendance.project_id)
        )
        project = project_result.scalars().first()
        if project and project.shift_end_time:
            shift_end_dt = datetime.combine(
                attendance.attendance_date, project.shift_end_time
            )
            if actual_out_time < shift_end_dt:
                is_early_departure = True
                early_minutes = int(
                    (shift_end_dt - actual_out_time).total_seconds() / 60
                )

    # Save check-out image if uploaded
    check_out_image_path = None
    if check_out_image and check_out_image.filename:
        check_out_image_path = await validate_and_save_image(
            check_out_image, UPLOAD_DIR, "checkout"
        )

    attendance.out_time = actual_out_time
    attendance.check_out_image = check_out_image_path
    attendance.check_out_latitude = check_out_latitude
    attendance.check_out_longitude = check_out_longitude
    attendance.check_out_address = check_out_address

    attendance.is_early_departure = is_early_departure
    attendance.early_minutes = early_minutes

    # Save new checkout fields
    attendance.work_summary = work_summary
    attendance.task_deadline_reason = task_deadline_reason

    # Save PDF
    work_report_pdf_path = None

    if work_report_pdf and work_report_pdf.filename:
        work_report_pdf_path = await validate_and_save_document(
            work_report_pdf, UPLOAD_DIR, "workreport"
        )

    attendance.work_report_pdf = work_report_pdf_path

    # Calculate working hours
    if attendance.in_time and attendance.out_time:
        delta = attendance.out_time - attendance.in_time
        attendance.working_hours = Decimal(
            str(round(delta.total_seconds() / 3600.0, 2))
        )
        # ==================================================
        # LABOUR OT + WAGE LOGIC
        # ==================================================

        if labour:

            total_hours = Decimal(str(attendance.working_hours or 0))

            working_hours = min(total_hours, Decimal("8"))

            overtime_hours = max(Decimal("0"), total_hours - working_hours)

            attendance.overtime_hours = overtime_hours

            hourly_rate = labour.effective_daily_wage / Decimal("8")

            overtime_rate = Decimal("0")

            policy = await db.scalar(
                select(ProjectOTPolicy).where(
                    ProjectOTPolicy.project_id == attendance.project_id
                )
            )

            if overtime_hours > 0:

                # =========================================
                # PRIORITY 1 + 2
                # LABOUR / LABOUR TYPE OT RATE
                # =========================================

                base_ot_rate = labour.effective_ot_rate

                # Labour custom OT
                # OR LabourType default OT
                if base_ot_rate and base_ot_rate > 0:

                    overtime_rate = base_ot_rate

                # =========================================
                # PRIORITY 3
                # PROJECT POLICY
                # =========================================
                elif policy:

                    # FIXED RATE POLICY
                    if policy.policy_type == OTPolicyType.FIXED_RATE:

                        overtime_rate = policy.fixed_ot_rate or Decimal("0")

                    # MULTIPLIER POLICY
                    else:

                        multiplier = policy.normal_day_multiplier or Decimal("1")

                        today = attendance.attendance_date.weekday()

                        # Sunday
                        if today == 6:

                            multiplier = policy.sunday_multiplier or multiplier

                        overtime_rate = hourly_rate * multiplier

            attendance.overtime_rate = overtime_rate

            total_wage = hourly_rate * working_hours + overtime_rate * overtime_hours

            total_wage = total_wage.quantize(Decimal("0.01"))

            existing_expense = await db.scalar(
                select(Expense).where(
                    Expense.project_id == attendance.project_id,
                    Expense.labour_id == labour.id,
                    Expense.expense_date == attendance.attendance_date,
                    Expense.source_type == "attendance_auto",
                )
            )

            if existing_expense:

                existing_expense.amount = total_wage

            else:

                expense = Expense(
                    project_id=attendance.project_id,
                    labour_id=labour.id,
                    category="Labour",
                    source_type="attendance_auto",
                    description=f"Labour expense - {attendance.attendance_date}",
                    amount=total_wage,
                    expense_date=attendance.attendance_date,
                    payment_mode="auto",
                )

                db.add(expense)

                await db.flush()

                project = await db.get(Project, attendance.project_id)

                if project:

                    db.add(
                        OwnerTransaction(
                            owner_id=project.owner_id,
                            project_id=attendance.project_id,
                            type="debit",
                            amount=total_wage,
                            reference_type="labour",
                            reference_id=expense.id,
                            description=f"Labour expense ({attendance.attendance_date})",
                        )
                    )

    # Auto-approve attendance upon checkout
    attendance.is_approved = True
    attendance.approved_by_id = current_user.id

    try:
        await db.commit()

    except Exception:
        await db.rollback()
        raise

    await db.refresh(attendance)

    return attendance


# ===================== TODAY'S STATUS =====================


@router.get("/today")
async def today_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get current user's attendance status for today."""
    today = date.today()

    result = await db.execute(
        select(UserAttendance).where(
            UserAttendance.user_id == current_user.id,
            UserAttendance.attendance_date == today,
        )
    )
    attendance = result.scalars().first()

    if not attendance:
        return {
            "checked_in": False,
            "checked_out": False,
            "attendance": None,
            "running_hours": 0,
            "date": today.isoformat(),
        }

    # Calculate running hours if still checked in
    running_hours = 0

    if attendance.in_time and not attendance.out_time:

        now = datetime.now()

        in_time = attendance.in_time

        if in_time and in_time.tzinfo is not None:
            in_time = in_time.replace(tzinfo=None)

        delta = now - in_time

        running_hours = round(delta.total_seconds() / 3600.0, 2)

    elif attendance.working_hours:
        running_hours = attendance.working_hours

    return {
        "checked_in": True,
        "checked_out": attendance.out_time is not None,
        "attendance": {
            "id": attendance.id,
            "user_id": attendance.user_id,
            "project_id": attendance.project_id,
            "attendance_date": attendance.attendance_date.isoformat(),
            "status": attendance.status,
            "in_time": attendance.in_time.isoformat() if attendance.in_time else None,
            "out_time": (
                attendance.out_time.isoformat() if attendance.out_time else None
            ),
            "working_hours": attendance.working_hours,
            "overtime_hours": attendance.overtime_hours,
            "check_in_image": attendance.check_in_image,
            "check_out_image": attendance.check_out_image,
            "check_in_address": attendance.check_in_address,
            "check_out_address": attendance.check_out_address,
            "task_description": attendance.task_description,
            "remarks": attendance.remarks,
            "task_deadline_reason": attendance.task_deadline_reason,
            "work_report_pdf": attendance.work_report_pdf,
            "work_location_type": attendance.work_location_type,
            "is_approved": attendance.is_approved,
            "is_outside_geofence": attendance.is_outside_geofence,
            "is_late": attendance.is_late,
            "late_minutes": attendance.late_minutes,
            "is_early_departure": attendance.is_early_departure,
            "early_minutes": attendance.early_minutes,
        },
        "running_hours": running_hours,
        "date": today.isoformat(),
    }


# ===================== LIST WITH PAGINATION =====================


@router.get("/list")
async def list_attendance(
    user_id: Optional[int] = None,
    project_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    is_approved: Optional[bool] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    # If not admin/PM/SE, they can only see their own attendance
    if current_user.role not in APPROVE_ROLES:
        user_id = current_user.id

    stmt = select(UserAttendance)
    count_stmt = select(func.count()).select_from(UserAttendance)

    # Build filters
    filters = []
    if user_id:
        filters.append(UserAttendance.user_id == user_id)
    if project_id:
        filters.append(UserAttendance.project_id == project_id)
    if start_date:
        filters.append(UserAttendance.attendance_date >= start_date)
    if end_date:
        filters.append(UserAttendance.attendance_date <= end_date)
    if is_approved is not None:
        filters.append(UserAttendance.is_approved == is_approved)
    if status:
        filters.append(UserAttendance.status == status)

    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    # Get total count
    total_result = await db.execute(count_stmt)
    total_count = total_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * page_size
    stmt = (
        stmt.order_by(desc(UserAttendance.attendance_date))
        .offset(offset)
        .limit(page_size)
    )

    result = await db.execute(stmt)
    records = result.scalars().all()

    return {
        "data": [UserAttendanceOut.model_validate(r).model_dump() for r in records],
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (
            (total_count + page_size - 1) // page_size if page_size > 0 else 0
        ),
    }
