import logging
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, File, UploadFile, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc, func, select
from app.core.dependencies import get_current_user, require_roles
from app.core.enums import AttendanceStatus
from app.db.session import get_db_session
from app.models.user import User, UserAttendance, ActivityLog
from app.models.project import Project
from app.schemas.user import UserAttendanceOut, ProxyBulkCheckInForm, ProxyBulkCheckOutForm
from datetime import datetime, date, timedelta, timezone
from app.utils.helpers import calculate_distance
from app.utils.timezone import get_naive_utc_now, localize_datetime, make_naive_utc
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
logger = logging.getLogger(__name__)

# Roles allowed to approve attendance
APPROVE_ROLES = ["Admin", "ProjectManager", "SiteEngineer"]

UPLOAD_DIR = "uploads/attendance"


# ===================== CHECK-IN =====================


@router.post("/check-in", response_model=UserAttendanceOut)
async def check_in(
    project_id: Optional[int] = Form(None),
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
    # Auto-generate UTC in_time
    actual_in_time = get_naive_utc_now()
    
    # Auto-generate local attendance_date from the server timestamp
    actual_in_time_aware = actual_in_time.replace(tzinfo=timezone.utc)
    actual_in_time_local = localize_datetime(actual_in_time_aware).replace(tzinfo=None)
    attendance_date = actual_in_time_local.date()

    # Hardcode status
    status = AttendanceStatus.PRESENT.value

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
            if project.shift_start_time:

                shift_start_dt_local = datetime.combine(
                    attendance_date, project.shift_start_time
                )
                
                actual_in_time_aware = actual_in_time.replace(tzinfo=timezone.utc)
                actual_in_time_local = localize_datetime(actual_in_time_aware).replace(tzinfo=None)

                grace_mins = project.grace_period_minutes or 15

                shift_start_with_grace = shift_start_dt_local + timedelta(minutes=grace_mins)

                if actual_in_time_local > shift_start_with_grace:

                    is_late = True

                    late_minutes = int(
                        (actual_in_time_local - shift_start_dt_local).total_seconds() / 60
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

    # Auto-generate out_time from secure server clock
    actual_out_time = get_naive_utc_now()

    # Early Departure Detection
    is_early_departure = False
    early_minutes = 0
    if attendance.project_id:
        project_result = await db.execute(
            select(Project).where(Project.id == attendance.project_id)
        )
        project = project_result.scalars().first()
        if project and project.shift_end_time:
            shift_end_dt_local = datetime.combine(
                attendance.attendance_date, project.shift_end_time
            )
            
            actual_out_time_aware = actual_out_time.replace(tzinfo=timezone.utc)
            actual_out_time_local = localize_datetime(actual_out_time_aware).replace(tzinfo=None)

            if actual_out_time_local < shift_end_dt_local:
                is_early_departure = True
                early_minutes = int(
                    (shift_end_dt_local - actual_out_time_local).total_seconds() / 60
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
                base_ot_rate = labour.effective_ot_rate or Decimal("0")

                if policy and policy.policy_type == OTPolicyType.FIXED_RATE:
                    overtime_rate = policy.fixed_ot_rate or Decimal("0")
                else:
                    if base_ot_rate <= 0:
                        logger.warning(f"Labour {labour.id} has missing or zero base OT rate. OT calculated as 0.")
                        overtime_rate = Decimal("0")
                    elif policy:
                        multiplier = policy.normal_day_multiplier or Decimal("1")
                        today = attendance.attendance_date.weekday()

                        # TODO: Holiday Calendar integration
                        # is_holiday = False  # Implementation needed
                        # if is_holiday:
                        #     multiplier = policy.holiday_multiplier or multiplier
                        # elif today == 6:
                        if today == 6:
                            multiplier = policy.sunday_multiplier or multiplier

                        overtime_rate = base_ot_rate * multiplier
                    else:
                        overtime_rate = base_ot_rate

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
                
                existing_transaction = await db.scalar(
                    select(OwnerTransaction).where(
                        OwnerTransaction.reference_id == existing_expense.id,
                        OwnerTransaction.reference_type == "labour",
                        OwnerTransaction.project_id == attendance.project_id
                    )
                )
                if existing_transaction:
                    existing_transaction.amount = total_wage

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
    # today = date.today()

    today = localize_datetime(
        get_naive_utc_now().replace(tzinfo=timezone.utc)
    ).date()

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

        now = get_naive_utc_now()

        in_time = attendance.in_time

        if in_time and in_time.tzinfo is not None:
            in_time = in_time.astimezone(timezone.utc).replace(tzinfo=None)

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

# ===================== PROXY BULK CHECK-IN =====================
@router.post("/proxy-check-in", dependencies=[Depends(require_roles(APPROVE_ROLES))])
async def proxy_check_in(
    payload: ProxyBulkCheckInForm,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    actual_in_time = get_naive_utc_now()
    actual_in_time_aware = actual_in_time.replace(tzinfo=timezone.utc)
    actual_in_time_local = localize_datetime(actual_in_time_aware).replace(tzinfo=None)
    attendance_date = actual_in_time_local.date()

    checked_in_count = 0
    for uid in payload.user_ids:
        # Check if already checked in today
        result = await db.execute(
            select(UserAttendance).where(
                UserAttendance.user_id == uid,
                UserAttendance.attendance_date == attendance_date,
            )
        )
        existing = result.scalars().first()

        if not existing:
            new_att = UserAttendance(
                user_id=uid,
                project_id=payload.project_id,
                attendance_date=attendance_date,
                in_time=actual_in_time,
                status=AttendanceStatus.PRESENT.value,
                remarks=payload.remarks,
                is_outside_geofence=False,  # Proxy bypasses geofencing
                is_late=False,
            )
            db.add(new_att)
            checked_in_count += 1

    # Activity Log
    if checked_in_count > 0:
        log = ActivityLog(
            action="BULK_CHECK_IN",
            entity="Attendance",
            performed_by=current_user.id,
        )
        db.add(log)
    
    await db.flush()
    return {"success": True, "message": f"Checked in {checked_in_count} users successfully."}


# ===================== PROXY BULK CHECK-OUT =====================
@router.put("/proxy-check-out", dependencies=[Depends(require_roles(APPROVE_ROLES))])
async def proxy_check_out(
    payload: ProxyBulkCheckOutForm,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    actual_out_time = get_naive_utc_now()
    checked_out_count = 0

    for aid in payload.attendance_ids:
        result = await db.execute(
            select(UserAttendance).where(UserAttendance.id == aid)
        )
        att = result.scalars().first()

        if att and not att.out_time:
            att.out_time = actual_out_time
            if payload.remarks:
                att.remarks = (att.remarks + " | " + payload.remarks) if att.remarks else payload.remarks
            
            # Recalculate hours
            if att.in_time:
                delta = actual_out_time - att.in_time
                hrs = delta.total_seconds() / 3600.0
                att.working_hours = round(hrs, 2)

                if hrs > 8.0:
                    att.overtime_hours = round(hrs - 8.0, 2)
            
            checked_out_count += 1

    # Activity Log
    if checked_out_count > 0:
        log = ActivityLog(
            action="BULK_CHECK_OUT",
            entity="Attendance",
            performed_by=current_user.id,
        )
        db.add(log)

    await db.flush()
    return {"success": True, "message": f"Checked out {checked_out_count} users successfully."}

import io
import csv
import pandas as pd
from fastapi.responses import StreamingResponse
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Image as RLImage, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch

# ===================== EXPORT APIS =====================

@router.get("/export/csv")
async def export_attendance_csv(
    start_date: date,
    end_date: date,
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    role: Optional[str] = None,
    is_approved: Optional[bool] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in APPROVE_ROLES:
        user_id = current_user.id

    query = select(UserAttendance, User, Project).join(User, UserAttendance.user_id == User.id).outerjoin(Project, UserAttendance.project_id == Project.id)
    
    query = query.where(UserAttendance.attendance_date >= start_date)
    query = query.where(UserAttendance.attendance_date <= end_date)
    
    if project_id:
        query = query.where(UserAttendance.project_id == project_id)
    if user_id:
        query = query.where(UserAttendance.user_id == user_id)
    if role:
        query = query.where(User.role == role)
    if is_approved is not None:
        query = query.where(UserAttendance.is_approved == is_approved)
        
    result = await db.execute(query.order_by(UserAttendance.attendance_date.desc()))
    records = result.all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "Employee ID", "Name", "Role", "Project Name", 
        "Status", "In-Time", "Out-Time", "Total Hours", "Overtime Hours", "Overtime Payout", "Remarks"
    ])
    
    for att, user, proj in records:
        proj_name = proj.project_name if proj else "N/A"
        payout = (att.overtime_hours or 0) * (att.overtime_rate or 0)
        in_t = att.in_time.strftime("%H:%M:%S") if att.in_time else ""
        out_t = att.out_time.strftime("%H:%M:%S") if att.out_time else ""
        
        writer.writerow([
            att.attendance_date,
            user.id,
            user.full_name,
            user.role,
            proj_name,
            att.status,
            in_t,
            out_t,
            round(att.working_hours or 0, 2),
            round(att.overtime_hours or 0, 2),
            round(payout, 2),
            att.remarks or ""
        ])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]), 
        media_type="text/csv", 
        headers={"Content-Disposition": f"attachment; filename=attendance_{start_date}_to_{end_date}.csv"}
    )


@router.get("/export/pdf/audit")
async def export_attendance_pdf_audit(
    start_date: date,
    end_date: date,
    project_id: int,
    user_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in APPROVE_ROLES:
        user_id = current_user.id

    query = select(UserAttendance, User, Project).join(User, UserAttendance.user_id == User.id).outerjoin(Project, UserAttendance.project_id == Project.id)
    query = query.where(
        UserAttendance.attendance_date >= start_date,
        UserAttendance.attendance_date <= end_date,
        UserAttendance.project_id == project_id
    )
    if user_id:
        query = query.where(UserAttendance.user_id == user_id)
        
    result = await db.execute(query.order_by(UserAttendance.attendance_date.desc()))
    records = result.all()
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
    elements = []
    styles = getSampleStyleSheet()
    
    title = Paragraph(f"Audit Report: Project {project_id} ({start_date} to {end_date})", styles['Heading1'])
    elements.append(title)
    elements.append(Spacer(1, 0.2 * inch))
    
    data = [["Date", "Name", "Check-In Time", "Check-In Location", "Check-Out Time", "Check-Out Location"]]
    
    for att, user, proj in records:
        in_t = att.in_time.strftime("%H:%M:%S") if att.in_time else "N/A"
        out_t = att.out_time.strftime("%H:%M:%S") if att.out_time else "N/A"
        
        in_loc = att.check_in_address or "N/A"
        out_loc = att.check_out_address or "N/A"
        
        data.append([
            str(att.attendance_date),
            user.full_name,
            in_t,
            in_loc,
            out_t,
            out_loc
        ])
    
    t = Table(data)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    elements.append(t)
    
    doc.build(elements)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer, 
        media_type="application/pdf", 
        headers={"Content-Disposition": f"attachment; filename=audit_{project_id}_{start_date}.pdf"}
    )


@router.get("/export/payroll")
async def export_attendance_payroll(
    start_date: date,
    end_date: date,
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    role: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in APPROVE_ROLES:
        user_id = current_user.id

    query = select(
        User.id,
        User.full_name,
        User.role,
        func.sum(UserAttendance.working_hours).label('total_hours'),
        func.sum(UserAttendance.overtime_hours).label('total_overtime'),
        func.sum(UserAttendance.overtime_hours * UserAttendance.overtime_rate).label('total_payout')
    ).join(UserAttendance, UserAttendance.user_id == User.id)
    
    query = query.where(
        UserAttendance.attendance_date >= start_date,
        UserAttendance.attendance_date <= end_date
    )
    
    if project_id:
        query = query.where(UserAttendance.project_id == project_id)
    if user_id:
        query = query.where(User.id == user_id)
    if role:
        query = query.where(User.role == role)
        
    query = query.group_by(User.id, User.full_name, User.role)
    result = await db.execute(query)
    records = result.all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee ID", "Name", "Role", "Total Hours", "Total Overtime Hours", "Overtime Payout"])
    
    for row in records:
        writer.writerow([
            row.id,
            row.full_name,
            row.role,
            round(row.total_hours or 0, 2),
            round(row.total_overtime or 0, 2),
            round(row.total_payout or 0, 2)
        ])
        
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]), 
        media_type="text/csv", 
        headers={"Content-Disposition": f"attachment; filename=payroll_{start_date}_to_{end_date}.csv"}
    )
