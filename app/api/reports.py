from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timedelta
import io
from fastapi import APIRouter, Depends, BackgroundTasks
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from openpyxl import Workbook
from app.models.contractor import Contractor
from app.models.invoice import Invoice, Transaction
from app.models.labour import LabourAttendance
from app.models.material import Material
from app.utils.common import assert_project_access
from app.utils.email import send_email
from app.core.dependencies import get_current_active_user, require_roles
from app.core.enums import InvoiceStatus, LabourStatus
from app.db.session import get_db_session
from app.models import project as m
from app.models.accountant import FixedAsset
from app.models.expense import Expense
from app.models.user import User, UserRole

REPORT_READ_ROLES = [role.value for role in UserRole]

router = APIRouter(prefix="/reports", tags=["Reports"])


# ===================== DAILY REPORT =====================

@router.get("/test-email")
async def test_email():
    result = await send_email(
        to_email="your_email@gmail.com",
        subject="Test SMTP",
        body="SMTP is working "
    )

    return {"success": result}


@router.get("/daily")
async def daily_report(
    project_id: int,
    report_date: date,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    dsr = await db.scalar(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date == report_date,
        )
    )

    return {"dsr": dsr}


# ===================== DAILY REPORT PDF =====================

@router.get("/daily/export/pdf")
async def export_daily_pdf(
    project_id: int,
    report_date: date,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    dsr = await db.scalar(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date == report_date,
        )
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()

    content = []
    content.append(Paragraph(f"Daily Report - {report_date}", styles["Title"]))
    content.append(Spacer(1, 10))

    if dsr:
        content.append(Paragraph(f"Work Done: {dsr.work_done}", styles["Normal"]))
        content.append(Paragraph(f"Weather: {dsr.weather}", styles["Normal"]))
        content.append(Paragraph(f"Remarks: {dsr.remarks}", styles["Normal"]))
    else:
        content.append(Paragraph("No data available", styles["Normal"]))

    doc.build(content)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=daily_report.pdf"},
    )


# ===================== WEEKLY PROGRESS =====================

@router.get("/weekly")
async def weekly_progress(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    week_ago = datetime.utcnow() - timedelta(days=7)

    result = await db.execute(
        select(
            m.Task.id,
            func.max(m.TaskProgress.percentage)
        )
        .join(m.TaskProgress, m.Task.id == m.TaskProgress.task_id)
        .where(
            m.Task.project_id == project_id,
            m.TaskProgress.created_at >= week_ago,
        )
        .group_by(m.Task.id)
    )

    rows = result.all()

    #  safe calculation
    progress = (
        sum(float(r[1]) for r in rows if r[1] is not None) / len(rows)
        if rows else 0
    )

    return {
        "weekly_progress_percent": round(progress, 2),
        "tasks_count": len(rows) }

# ===================== LABOUR REPORT =====================

@router.get("/labour")
async def labour_report(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(
            m.Labour.skill_type,
            func.count(func.distinct(m.Labour.id))
        )
        .join(LabourAttendance, m.Labour.id == LabourAttendance.labour_id)
        .where(
            LabourAttendance.project_id == project_id,
            m.Labour.status == LabourStatus.ACTIVE
        )
        .group_by(m.Labour.skill_type)
    )

    rows = result.all()

    return {
        "labour_summary": [
            {
                "skill_type": r[0],
                "count": r[1]
            }
            for r in rows
        ]
    }

# ===================== LABOUR EXCEL =====================

@router.get("/labour/export/excel")
async def export_labour_excel(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    #  Correct query (JOIN + GROUP BY)
    result = await db.execute(
        select(
            m.Labour.skill_type,
            func.count(func.distinct(m.Labour.id))
        )
        .join(LabourAttendance, m.Labour.id == LabourAttendance.labour_id)
        .where(
            LabourAttendance.project_id == project_id,
            m.Labour.status == LabourStatus.ACTIVE
        )
        .group_by(m.Labour.skill_type)
    )

    rows = result.all()

    #  Create Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "Labour Report"

    # Headers
    ws.append(["Skill Type", "Count"])

    # Data
    for r in rows:
        ws.append([str(r[0]), int(r[1])])

    # Save to buffer
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=labour_report_project_{project_id}.xlsx"
        },
    )


# ===================== MATERIAL REPORT =====================

@router.get("/material")
async def material_report(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(Material).where(Material.project_id == project_id)
    )

    materials = result.scalars().all()

    return {"materials": materials}


# ===================== MATERIAL EXCEL =====================

@router.get("/material/export/excel")
async def export_material_excel(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(Material).where(Material.project_id == project_id)
    )
    materials = result.scalars().all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Materials"

    # Headers
    ws.append(["Name", "Quantity", "Unit"])

    # Data
    for mat in materials:
        ws.append([
            mat.material_name,
            float(mat.quantity),
            str(mat.unit)
        ])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=materials_project_{project_id}.xlsx"
        },
    )

# ===================== ISSUE REPORT =====================

@router.get("/issues")
async def issue_report(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    open_issues = await db.scalar(
        select(func.count()).select_from(m.Issue).where(
            m.Issue.project_id == project_id,
            m.Issue.status == "Open",
        )
    )

    closed_issues = await db.scalar(
        select(func.count()).select_from(m.Issue).where(
            m.Issue.project_id == project_id,
            m.Issue.status == "Closed",
        )
    )

    return {"open": open_issues, "closed": closed_issues}


# ===================== ISSUE EXCEL =====================

@router.get("/issues/export/excel")
async def export_issue_excel(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(m.Issue).where(m.Issue.project_id == project_id)
    )
    issues = result.scalars().all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Issues"

    ws.append(["Title", "Status", "Priority"])

    for i in issues:
        ws.append([i.title, i.status, i.priority])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=issues.xlsx"},
    )


# @router.post("/daily/share/email")
# async def share_daily_email(
#     project_id: int,
#     report_date: date,
#     email: str,
#     current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
#     db: AsyncSession = Depends(get_db_session),
# ):
#     #  ACCESS CONTROL
#     await assert_project_access(db, project_id=project_id, current_user=current_user)

#     # 1. Get data
#     dsr = await db.scalar(
#         select(m.DailySiteReport).where(
#             m.DailySiteReport.project_id == project_id,
#             m.DailySiteReport.report_date == report_date,
#         )
#     )

#     # 2. Generate PDF
#     import io
#     from reportlab.platypus import SimpleDocTemplate, Paragraph
#     from reportlab.lib.styles import getSampleStyleSheet

#     buffer = io.BytesIO()
#     doc = SimpleDocTemplate(buffer)
#     styles = getSampleStyleSheet()

#     content = []
#     content.append(Paragraph(f"Daily Report - {report_date}", styles["Title"]))

#     if dsr:
#         content.append(Paragraph(f"Work Done: {dsr.work_done}", styles["Normal"]))
#         content.append(Paragraph(f"Weather: {dsr.weather}", styles["Normal"]))
#     else:
#         content.append(Paragraph("No data available", styles["Normal"]))

#     doc.build(content)
#     buffer.seek(0)

#     # 3. Send Email
#     result = await send_email(
#         to_email=email,
#         subject="Daily Report",
#         body=f"Daily report for {report_date}. See attachment.",
#         attachment=buffer.read(),
#         filename="daily_report.pdf",
#     )

#     if not result:
#         raise Exception("Failed to send email")

#     return {"message": "Email sent successfully"}

from fastapi import BackgroundTasks

@router.post("/daily/share/email")
async def share_daily_email(
    project_id: int,
    report_date: date,
    email: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    dsr = await db.scalar(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date == report_date,
        )
    )

    # PDF generation (same as yours)
    import io
    from reportlab.platypus import SimpleDocTemplate, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()

    content = []
    content.append(Paragraph(f"Daily Report - {report_date}", styles["Title"]))

    if dsr:
        content.append(Paragraph(f"Work Done: {dsr.work_done}", styles["Normal"]))
        content.append(Paragraph(f"Weather: {dsr.weather}", styles["Normal"]))
    else:
        content.append(Paragraph("No data available", styles["Normal"]))

    doc.build(content)
    buffer.seek(0)

    #  Background email (NON-BLOCKING)
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2> Daily Site Report</h2>

        <p><b>Date:</b> {report_date}</p>

        <p>Please find the attached report.</p>

        <br>

        <hr>
        <p style="font-size:12px;color:gray;">
        Construction Management System
        </p>
    </body>
    </html>
    """

    background_tasks.add_task(
        send_email,
        to_email=email,
        subject="Daily Report",
        body=body,
        attachment=buffer.read(),
        filename="daily_report.pdf",
    )

    return {"message": "Email queued successfully"}


# ===================== FILTERED REPORT DOWNLOAD =====================

@router.get("/download")
async def client_report_download(
    project_id: int,
    start_date: date,
    end_date: date,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date >= start_date,
            m.DailySiteReport.report_date <= end_date,
        )
    )
    reports = result.scalars().all()

    import io
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()

    content = []
    content.append(Paragraph(f"Report ({start_date} to {end_date})", styles["Title"]))
    content.append(Spacer(1, 10))

    if not reports:
        content.append(Paragraph("No data available", styles["Normal"]))
    else:
        for r in reports:
            content.append(Paragraph(f"Date: {r.report_date}", styles["Normal"]))
            content.append(Paragraph(f"Work: {r.work_done}", styles["Normal"]))
            content.append(Spacer(1, 10))

    doc.build(content)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=filtered_report.pdf"},
    )


@router.get("/combined")
async def combined_report(
    project_id: int,
    start_date: date,
    end_date: date,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    #  Work Progress
    progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id == project_id
        )
    )

    #  Financials
    total_paid = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.status == InvoiceStatus.PAID
        )
    )

    total_pending = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.status == InvoiceStatus.PENDING
        )
    )

    #  DSR (work summary)
    reports = await db.execute(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date >= start_date,
            m.DailySiteReport.report_date <= end_date,
        )
    )
    dsr_list = reports.scalars().all()

    #  Generate PDF
    import io
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()

    content = []

    content.append(Paragraph("Combined Project Report", styles["Title"]))
    content.append(Spacer(1, 10))

    content.append(Paragraph(f"Date Range: {start_date} to {end_date}", styles["Normal"]))
    content.append(Spacer(1, 10))

    # Progress
    content.append(Paragraph(f"Progress: {round(progress or 0, 2)}%", styles["Normal"]))
    content.append(Spacer(1, 10))

    # Financial
    content.append(Paragraph(f"Total Paid: {float(total_paid or 0)}", styles["Normal"]))
    content.append(Paragraph(f"Pending: {float(total_pending or 0)}", styles["Normal"]))
    content.append(Spacer(1, 10))

    # Work Summary
    content.append(Paragraph("Work Summary:", styles["Heading2"]))

    if not dsr_list:
        content.append(Paragraph("No data available", styles["Normal"]))
    else:
        for r in dsr_list:
            content.append(Paragraph(f"{r.report_date} - {r.work_done}", styles["Normal"]))
            content.append(Spacer(1, 5))

    doc.build(content)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=combined_report.pdf"},
    )


@router.get("/contractor-performance")
async def contractor_performance(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    # 1. Total tasks
    total_tasks = await db.scalar(
        select(func.count(m.Task.id)).where(m.Task.project_id == project_id)
    )

    # 2. Avg progress
    avg_progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id == project_id
        )
    )

    # 3. Total paid invoices
    total_paid = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.status == InvoiceStatus.PAID,
        )
    )

    progress_val = float(avg_progress or 0)

    # 4. Performance logic
    if progress_val >= 75:
        rating = "Excellent"
    elif progress_val >= 50:
        rating = "Good"
    elif progress_val > 0:
        rating = "Average"
    else:
        rating = "Low"

    return {
        "project_id": project_id,
        "total_tasks": int(total_tasks or 0),
        "avg_progress": round(progress_val, 2),
        "total_paid": float(total_paid or 0),
        "performance": rating,
    }

@router.get("/profit-loss")
async def profit_loss(db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(REPORT_READ_ROLES))):

    # Income (owner invoices)
    income = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.type == "owner"
        )
    )

    # Expense (labour + material)
    expense = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.type.in_(["labour", "material"])
        )
    )

    income_val = float(income or 0)
    expense_val = float(expense or 0)

    return {
        "income": income_val,
        "expense": expense_val,
        "profit": income_val - expense_val,
    }

@router.get("/project/{project_id}")
async def project_report(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    #  Revenue (owner invoices only)
    revenue = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.type == "owner"
        )
    )

    #  Expense (labour + material invoices)
    expense = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.type.in_(["labour", "material"])
        )
    )

    revenue_val = float(revenue or 0)
    expense_val = float(expense or 0)

    return {
        "project_id": project_id,
        "revenue": revenue_val,
        "expense": expense_val,
        "profit": revenue_val - expense_val,
    }

@router.get("/cashflow")
async def cashflow(db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(REPORT_READ_ROLES))):
    inflow = await db.scalar(
        select(func.sum(Transaction.amount)).where(Transaction.type == "receipt")
    )

    outflow = await db.scalar(
        select(func.sum(Transaction.amount)).where(Transaction.type == "payment")
    )

    return {
        "inflow": float(inflow or 0),
        "outflow": float(outflow or 0),
        "balance": float((inflow or 0) - (outflow or 0)),
    }


@router.get("/assets")
async def asset_report(db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(REPORT_READ_ROLES))):
    result = await db.execute(select(FixedAsset))
    assets = result.scalars().all()

    return assets

@router.post("/combined/share/email")
async def share_combined_report_email(
    project_id: int,
    start_date: date,
    end_date: date,
    email: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    # ===== DATA =====
    progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id == project_id
        )
    )

    total_paid = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.status == InvoiceStatus.PAID
        )
    )

    total_pending = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.status == InvoiceStatus.PENDING
        )
    )

    reports = await db.execute(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date >= start_date,
            m.DailySiteReport.report_date <= end_date,
        )
    )
    dsr_list = reports.scalars().all()

    # ===== PDF =====
    import io
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()

    content = []
    content.append(Paragraph("Combined Project Report", styles["Title"]))
    content.append(Spacer(1, 10))

    content.append(Paragraph(f"{start_date} to {end_date}", styles["Normal"]))
    content.append(Spacer(1, 10))

    content.append(Paragraph(f"Progress: {round(progress or 0, 2)}%", styles["Normal"]))
    content.append(Paragraph(f"Paid: {float(total_paid or 0)}", styles["Normal"]))
    content.append(Paragraph(f"Pending: {float(total_pending or 0)}", styles["Normal"]))
    content.append(Spacer(1, 10))

    for r in dsr_list:
        content.append(Paragraph(f"{r.report_date} - {r.work_done}", styles["Normal"]))

    doc.build(content)
    buffer.seek(0)

    background_tasks.add_task(
        send_email,
        to_email=email,
        subject="Combined Project Report",
        body=f"Report from {start_date} to {end_date}",
        attachment=buffer.read(),
        filename="combined_report.pdf",
    )

    return {"message": "Email queued successfully"}


from app.utils.helpers import NotFoundError
from app.utils.whatsapp import send_report_template


@router.post("/combined/share/whatsapp")
async def share_combined_whatsapp(
    project_id: int,
    start_date: date,
    end_date: date,
    phone: str,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    #  Generate report URL (IMPORTANT)
    report_url = f"http://localhost:8000/reports/combined?project_id={project_id}&start_date={start_date}&end_date={end_date}"

    result = await send_report_template(
        to=phone,
        name="Client",
        report_url=report_url
    )

    return {
        "message": "WhatsApp message sent",
        "response": result
    }