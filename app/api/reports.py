from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timedelta
import io
from fastapi import APIRouter, Depends, BackgroundTasks
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from openpyxl import Workbook
from app.models.contractor import Contractor
from app.models.invoice import Invoice, Transaction
from app.models.user import UserAttendance
from app.models.material import Material
from app.utils.common import assert_project_access
from app.utils.email import send_email
from app.core.dependencies import get_current_active_user, require_roles
from app.core.enums import InvoiceStatus, IssueStatus, LabourStatus, TaskStatus
from app.db.session import get_db_session
from app.models import project as m
from app.models.accountant import FixedAsset
from app.models.expense import Expense
from app.models.user import User, UserRole, ActivityLog
from fastapi import BackgroundTasks
from app.utils.helpers import NotFoundError
from app.utils.whatsapp import send_report_template

REPORT_READ_ROLES = [role.value for role in UserRole]

router = APIRouter(prefix="/reports", tags=["Reports"])


# ===================== DAILY REPORT =====================


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
        select(m.Task.id, func.max(m.TaskProgress.percentage))
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
        sum(float(r[1]) for r in rows if r[1] is not None) / len(rows) if rows else 0
    )

    return {"weekly_progress_percent": round(progress, 2), "tasks_count": len(rows)}


# ===================== LABOUR REPORT =====================


@router.get("/labour")
async def labour_report(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(m.Labour.skill_type, func.count(func.distinct(m.Labour.id)))
        .join(UserAttendance, m.Labour.user_id == UserAttendance.user_id)
        .where(
            UserAttendance.project_id == project_id,
            m.Labour.status == LabourStatus.ACTIVE,
        )
        .group_by(m.Labour.skill_type)
    )

    rows = result.all()

    return {"labour_summary": [{"skill_type": r[0], "count": r[1]} for r in rows]}


# ===================== LABOUR EXCEL =====================


@router.get("/labour/export/excel")
async def export_labour_excel(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    #  Correct query (JOIN + GROUP BY)
    result = await db.execute(
        select(m.Labour.skill_type, func.count(func.distinct(m.Labour.id)))
        .join(UserAttendance, m.Labour.user_id == UserAttendance.user_id)
        .where(
            UserAttendance.project_id == project_id,
            m.Labour.status == LabourStatus.ACTIVE,
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
    result = await db.execute(select(Material).where(Material.project_id == project_id))

    materials = result.scalars().all()

    return {"materials": materials}


# ===================== MATERIAL EXCEL =====================


@router.get("/material/export/excel")
async def export_material_excel(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(select(Material).where(Material.project_id == project_id))
    materials = result.scalars().all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Materials"

    # Headers
    ws.append(
        [
            "Material Name",
            "Category",
            "Unit",
            "Purchased Qty",
            "Used Qty",
            "Remaining Stock",
            "Purchase Rate",
            "Total Amount",
        ]
    )

    # Data
    for mat in materials:
        ws.append(
            [
                mat.material_name or "",
                mat.category or "",
                mat.unit or "",
                float(mat.quantity_purchased or 0),
                float(mat.quantity_used or 0),
                float(mat.remaining_stock or 0),
                float(mat.purchase_rate or 0),
                float(mat.total_amount or 0),
            ]
        )

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
        select(func.count())
        .select_from(m.Issue)
        .where(
            m.Issue.project_id == project_id,
            m.Issue.status == "Open",
        )
    )

    closed_issues = await db.scalar(
        select(func.count())
        .select_from(m.Issue)
        .where(
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
    result = await db.execute(select(m.Issue).where(m.Issue.project_id == project_id))
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
#     background_tasks: BackgroundTasks,
#     current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
#     db: AsyncSession = Depends(get_db_session),
# ):
#     await assert_project_access(db, project_id=project_id, current_user=current_user)

#     dsr = await db.scalar(
#         select(m.DailySiteReport).where(
#             m.DailySiteReport.project_id == project_id,
#             m.DailySiteReport.report_date == report_date,
#         )
#     )

#     # PDF generation (same as yours)
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

#     #  Background email (NON-BLOCKING)
#     body = f"""
#     <html>
#     <body style="font-family: Arial, sans-serif;">
#         <h2> Daily Site Report</h2>

#         <p><b>Date:</b> {report_date}</p>

#         <p>Please find the attached report.</p>

#         <br>

#         <hr>
#         <p style="font-size:12px;color:gray;">
#         Construction Management System
#         </p>
#     </body>
#     </html>
#     """

#     background_tasks.add_task(
#         send_email,
#         to_email=email,
#         subject="Daily Report",
#         body=body,
#         attachment=buffer.read(),
#         filename="daily_report.pdf",
#     )

#     return {"message": "Email queued successfully"}


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
            Invoice.project_id == project_id, Invoice.status == InvoiceStatus.PAID
        )
    )

    total_pending = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id, Invoice.status == InvoiceStatus.PENDING
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

    content.append(
        Paragraph(f"Date Range: {start_date} to {end_date}", styles["Normal"])
    )
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
            content.append(
                Paragraph(f"{r.report_date} - {r.work_done}", styles["Normal"])
            )
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
async def profit_loss(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
):

    # Income (owner invoices)
    income = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(Invoice.type == "owner")
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
    db: AsyncSession = Depends(get_db_session),
):
    #  Revenue (owner invoices only)
    revenue = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id, Invoice.type == "owner"
        )
    )

    #  Expense (labour + material invoices)
    expense = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id, Invoice.type.in_(["labour", "material"])
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
async def cashflow(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
):
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
async def asset_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
):
    result = await db.execute(select(FixedAsset))
    assets = result.scalars().all()

    return assets


# @router.post("/combined/share/email")
# async def share_combined_report_email(
#     project_id: int,
#     start_date: date,
#     end_date: date,
#     email: str,
#     background_tasks: BackgroundTasks,
#     current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
#     db: AsyncSession = Depends(get_db_session),
# ):
#     await assert_project_access(db, project_id=project_id, current_user=current_user)

#     # ===== DATA =====
#     progress = await db.scalar(
#         select(func.avg(m.Task.completion_percentage)).where(
#             m.Task.project_id == project_id
#         )
#     )

#     total_paid = await db.scalar(
#         select(func.sum(Invoice.total_amount)).where(
#             Invoice.project_id == project_id,
#             Invoice.status == InvoiceStatus.PAID
#         )
#     )

#     total_pending = await db.scalar(
#         select(func.sum(Invoice.total_amount)).where(
#             Invoice.project_id == project_id,
#             Invoice.status == InvoiceStatus.PENDING
#         )
#     )

#     reports = await db.execute(
#         select(m.DailySiteReport).where(
#             m.DailySiteReport.project_id == project_id,
#             m.DailySiteReport.report_date >= start_date,
#             m.DailySiteReport.report_date <= end_date,
#         )
#     )
#     dsr_list = reports.scalars().all()

#     # ===== PDF =====
#     import io
#     from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
#     from reportlab.lib.styles import getSampleStyleSheet

#     buffer = io.BytesIO()
#     doc = SimpleDocTemplate(buffer)
#     styles = getSampleStyleSheet()

#     content = []
#     content.append(Paragraph("Combined Project Report", styles["Title"]))
#     content.append(Spacer(1, 10))

#     content.append(Paragraph(f"{start_date} to {end_date}", styles["Normal"]))
#     content.append(Spacer(1, 10))

#     content.append(Paragraph(f"Progress: {round(progress or 0, 2)}%", styles["Normal"]))
#     content.append(Paragraph(f"Paid: {float(total_paid or 0)}", styles["Normal"]))
#     content.append(Paragraph(f"Pending: {float(total_pending or 0)}", styles["Normal"]))
#     content.append(Spacer(1, 10))

#     for r in dsr_list:
#         content.append(Paragraph(f"{r.report_date} - {r.work_done}", styles["Normal"]))

#     doc.build(content)
#     buffer.seek(0)

#     background_tasks.add_task(
#         send_email,
#         to_email=email,
#         subject="Combined Project Report",
#         body=f"Report from {start_date} to {end_date}",
#         attachment=buffer.read(),
#         filename="combined_report.pdf",
#     )

#     return {"message": "Email queued successfully"}


# @router.post("/combined/share/whatsapp")
# async def share_combined_whatsapp(
#     project_id: int,
#     start_date: date,
#     end_date: date,
#     phone: str,
#     current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
#     db: AsyncSession = Depends(get_db_session),
# ):
#     await assert_project_access(db, project_id=project_id, current_user=current_user)

#     #  Generate report URL (IMPORTANT)
#     report_url = f"http://localhost:8000/reports/combined?project_id={project_id}&start_date={start_date}&end_date={end_date}"

#     result = await send_report_template(
#         to=phone,
#         name="Client",
#         report_url=report_url
#     )

#     return {
#         "message": "WhatsApp message sent",
#         "response": result
#     }


# =========================================================
# FINANCIAL SUMMARY
# =========================================================
@router.get("/financial-summary")
async def financial_summary(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)
    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )
    total_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(Invoice.project_id == project_id)
    )
    paid_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id, Invoice.status == InvoiceStatus.PAID
        )
    )
    pending_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id, Invoice.status == InvoiceStatus.PENDING
        )
    )
    expense_val = round(float(total_expense or 0), 2)
    invoice_val = round(float(total_invoice or 0), 2)
    return {
        "project_id": project_id,
        "total_expense": expense_val,
        "total_invoice": invoice_val,
        "paid_invoice": round(float(paid_invoice or 0), 2),
        "pending_invoice": round(float(pending_invoice or 0), 2),
        "profit": round(invoice_val - expense_val, 2),
    }


# =========================================================
# QUARTERLY AUDIT SUMMARY
# =========================================================
@router.get("/quarterly-audit-summary")
async def quarterly_audit_summary(
    project_id: int,
    year: int,
    quarter: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)
    if quarter not in [1, 2, 3, 4]:
        raise HTTPException(status_code=400, detail="Quarter must be between 1 and 4")
    quarter_map = {
        1: (1, 3),
        2: (4, 6),
        3: (7, 9),
        4: (10, 12),
    }
    start_month, end_month = quarter_map[quarter]
    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(
            Expense.project_id == project_id,
            func.extract("year", Expense.expense_date) == year,
            func.extract("month", Expense.expense_date).between(start_month, end_month),
        )
    )
    total_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            func.extract("year", Invoice.created_at) == year,
            func.extract("month", Invoice.created_at).between(start_month, end_month),
        )
    )
    completed_tasks = await db.scalar(
        select(func.count())
        .select_from(m.Task)
        .where(m.Task.project_id == project_id, m.Task.status == TaskStatus.COMPLETED)
    )
    delayed_tasks = await db.scalar(
        select(func.count())
        .select_from(m.Task)
        .where(
            m.Task.project_id == project_id,
            m.Task.end_date.isnot(None),
            m.Task.end_date < date.today(),
            m.Task.status != TaskStatus.COMPLETED,
        )
    )
    return {
        "project_id": project_id,
        "quarter": f"Q{quarter}",
        "year": year,
        "total_expense": round(float(total_expense or 0), 2),
        "total_invoice": round(float(total_invoice or 0), 2),
        "completed_tasks": int(completed_tasks or 0),
        "delayed_tasks": int(delayed_tasks or 0),
    }


# =========================================================
# WORK SUMMARY
# =========================================================
@router.get("/work-summary")
async def work_summary(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)
    result = await db.execute(select(m.Task).where(m.Task.project_id == project_id))
    tasks = result.scalars().all()
    summary = []
    for task in tasks:
        actual = round(float(task.completion_percentage or 0), 2)
        # Future ready
        planned = 100
        if actual >= 90:
            efficiency = "HIGH"
        elif actual >= 60:
            efficiency = "MEDIUM"
        else:
            efficiency = "LOW"
        summary.append(
            {
                "task_id": task.id,
                "category": task.title,
                "plan_percentage": planned,
                "actual_percentage": actual,
                "efficiency": efficiency,
                "status": task.status.value if task.status else None,
            }
        )
    return {
        "project_id": project_id,
        "total_tasks": len(summary),
        "work_summary": summary,
    }


# =========================================================
# AUDIT PDF
# =========================================================
@router.get("/audit-pdf")
async def audit_pdf(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)
    # ================= FINANCIAL =================
    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )
    total_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(Invoice.project_id == project_id)
    )
    paid_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id, Invoice.status == InvoiceStatus.PAID
        )
    )
    pending_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id, Invoice.status == InvoiceStatus.PENDING
        )
    )
    # ================= TASKS =================
    total_tasks = await db.scalar(
        select(func.count()).select_from(m.Task).where(m.Task.project_id == project_id)
    )
    completed_tasks = await db.scalar(
        select(func.count())
        .select_from(m.Task)
        .where(m.Task.project_id == project_id, m.Task.status == TaskStatus.COMPLETED)
    )
    in_progress_tasks = await db.scalar(
        select(func.count())
        .select_from(m.Task)
        .where(m.Task.project_id == project_id, m.Task.status == TaskStatus.IN_PROGRESS)
    )
    # ================= ISSUES =================
    open_issues = await db.scalar(
        select(func.count())
        .select_from(m.Issue)
        .where(m.Issue.project_id == project_id, m.Issue.status == IssueStatus.OPEN)
    )
    closed_issues = await db.scalar(
        select(func.count())
        .select_from(m.Issue)
        .where(m.Issue.project_id == project_id, m.Issue.status == IssueStatus.CLOSED)
    )
    # ================= PROGRESS =================
    progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id == project_id
        )
    )
    # ================= PDF =================
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()
    content = []
    # TITLE
    content.append(Paragraph("Detailed Audit Report", styles["Title"]))
    content.append(Spacer(1, 20))
    # FINANCIAL
    content.append(Paragraph("Financial Summary", styles["Heading1"]))
    content.append(
        Paragraph(
            f"Total Expense: Rs. {round(float(total_expense or 0), 2)}",
            styles["Normal"],
        )
    )
    content.append(
        Paragraph(
            f"Total Invoice: Rs. {round(float(total_invoice or 0), 2)}",
            styles["Normal"],
        )
    )
    content.append(
        Paragraph(
            f"Paid Invoice: Rs. {round(float(paid_invoice or 0), 2)}", styles["Normal"]
        )
    )
    content.append(
        Paragraph(
            f"Pending Invoice: Rs. {round(float(pending_invoice or 0), 2)}",
            styles["Normal"],
        )
    )
    content.append(
        Paragraph(
            f"Profit: Rs. {round(float((total_invoice or 0) - (total_expense or 0)), 2)}",
            styles["Normal"],
        )
    )
    content.append(Spacer(1, 20))
    # WORK
    content.append(Paragraph("Work Summary", styles["Heading1"]))
    content.append(Paragraph(f"Total Tasks: {int(total_tasks or 0)}", styles["Normal"]))
    content.append(
        Paragraph(f"Completed Tasks: {int(completed_tasks or 0)}", styles["Normal"])
    )
    content.append(
        Paragraph(f"In Progress Tasks: {int(in_progress_tasks or 0)}", styles["Normal"])
    )
    content.append(Spacer(1, 20))
    # ISSUES
    content.append(Paragraph("Issue Summary", styles["Heading1"]))
    content.append(Paragraph(f"Open Issues: {int(open_issues or 0)}", styles["Normal"]))
    content.append(
        Paragraph(f"Closed Issues: {int(closed_issues or 0)}", styles["Normal"])
    )
    content.append(Spacer(1, 20))
    # PROGRESS
    content.append(Paragraph("Project Progress", styles["Heading1"]))
    content.append(
        Paragraph(
            f"Overall Progress: {round(float(progress or 0), 2)} %", styles["Normal"]
        )
    )
    content.append(Spacer(1, 20))
    # FOOTER
    content.append(
        Paragraph(
            f"Generated On: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}",
            styles["Italic"],
        )
    )
    # BUILD
    doc.build(content)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=audit_report_{project_id}.pdf"
        },
    )


# =========================================================
# UNIFIED PROJECT REPORT
# =========================================================

from calendar import monthrange

# =========================================================
# UNIFIED PROJECT REPORT
# =========================================================

from calendar import monthrange


@router.get("/project")
async def project_report(
    project_id: int,
    type: str,
    report_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    month: int | None = None,
    year: int | None = None,
    quarter: int | None = None,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    # =====================================================
    # VALIDATION
    # =====================================================

    if type not in ["daily", "weekly", "monthly", "quarterly"]:
        raise HTTPException(status_code=400, detail="Invalid report type")

    if type == "daily" and not report_date:
        raise HTTPException(
            status_code=400, detail="report_date is required for daily report"
        )

    if type == "weekly" and (not start_date or not end_date):
        raise HTTPException(
            status_code=400, detail="start_date and end_date required for weekly report"
        )

    if type == "monthly":
        if not month or not year:
            raise HTTPException(
                status_code=400, detail="month and year required for monthly report"
            )

        start_date = date(year, month, 1)
        end_date = date(year, month, monthrange(year, month)[1])

    # =====================================================
    # QUARTERLY
    # =====================================================

    if type == "quarterly":

        if not quarter or not year:
            raise HTTPException(
                status_code=400, detail="quarter and year required for quarterly report"
            )

        if quarter not in [1, 2, 3, 4]:
            raise HTTPException(
                status_code=400, detail="quarter must be between 1 and 4"
            )

        quarter_map = {
            1: (1, 3),
            2: (4, 6),
            3: (7, 9),
            4: (10, 12),
        }

        start_month, end_month = quarter_map[quarter]

        start_date = date(year, start_month, 1)

        end_date = date(year, end_month, monthrange(year, end_month)[1])

    if type == "daily":
        start_date = report_date
        end_date = report_date

    # =====================================================
    # PROJECT
    # =====================================================

    project = await db.scalar(select(m.Project).where(m.Project.id == project_id))

    # =====================================================
    # TASK SUMMARY
    # =====================================================

    total_tasks = await db.scalar(
        select(func.count()).select_from(m.Task).where(m.Task.project_id == project_id)
    )

    completed_tasks = await db.scalar(
        select(func.count())
        .select_from(m.Task)
        .where(m.Task.project_id == project_id, m.Task.status == TaskStatus.COMPLETED)
    )

    progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id == project_id
        )
    )

    # =====================================================
    # FINANCIALS
    # =====================================================

    total_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(Invoice.project_id == project_id)
    )

    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )

    # =====================================================
    # ISSUES
    # =====================================================

    open_issues = await db.scalar(
        select(func.count())
        .select_from(m.Issue)
        .where(m.Issue.project_id == project_id, m.Issue.status == IssueStatus.OPEN)
    )

    # =====================================================
    # DSR
    # =====================================================

    dsr_result = await db.execute(
        select(m.DailySiteReport)
        .where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date >= start_date,
            m.DailySiteReport.report_date <= end_date,
        )
        .order_by(m.DailySiteReport.report_date.desc())
    )

    dsr_list = dsr_result.scalars().all()

    # =====================================================
    # RESPONSE
    # =====================================================

    return {
        "project": {
            "id": project.id,
            "project_name": project.project_name,
        },
        "report_type": type,
        "quarter": f"Q{quarter}" if type == "quarterly" else None,
        "date_range": {
            "start_date": start_date,
            "end_date": end_date,
        },
        "summary": {
            "total_tasks": int(total_tasks or 0),
            "completed_tasks": int(completed_tasks or 0),
            "open_issues": int(open_issues or 0),
            "overall_progress": round(float(progress or 0), 2),
        },
        "financials": {
            "total_invoice": round(float(total_invoice or 0), 2),
            "total_expense": round(float(total_expense or 0), 2),
            "profit": round(float((total_invoice or 0) - (total_expense or 0)), 2),
        },
        "daily_reports": [
            {
                "date": r.report_date,
                "work_done": r.work_done,
                "weather": r.weather,
                "remarks": r.remarks,
            }
            for r in dsr_list
        ],
        "generated_at": datetime.utcnow(),
    }


# =========================================================
# EXPORT PDF
# =========================================================


@router.get("/project/export/pdf")
@router.get("/project/export/pdf")
async def export_project_report_pdf(
    project_id: int,
    type: str,
    report_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    month: int | None = None,
    year: int | None = None,
    quarter: int | None = None,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    response = await project_report(
        project_id=project_id,
        type=type,
        report_date=report_date,
        start_date=start_date,
        end_date=end_date,
        month=month,
        year=year,
        quarter=quarter,
        current_user=current_user,
        db=db,
    )

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(buffer)

    styles = getSampleStyleSheet()

    content = []

    content.append(Paragraph(f"{type.title()} Project Report", styles["Title"]))

    content.append(Spacer(1, 20))

    Paragraph(f"Project: {response['project']['project_name']}", styles["Heading2"])

    content.append(
        Paragraph(
            f"Progress: {response['summary']['overall_progress']}%", styles["Normal"]
        )
    )

    content.append(
        Paragraph(
            f"Completed Tasks: {response['summary']['completed_tasks']}",
            styles["Normal"],
        )
    )

    content.append(
        Paragraph(
            f"Open Issues: {response['summary']['open_issues']}", styles["Normal"]
        )
    )

    content.append(Spacer(1, 20))

    content.append(Paragraph("Daily Work Logs", styles["Heading2"]))

    for r in response["daily_reports"]:
        content.append(Paragraph(f"{r['date']} - {r['work_done']}", styles["Normal"]))

    doc.build(content)

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={type}_project_report.pdf"
        },
    )


# =========================================================
# EXPORT EXCEL
# =========================================================


@router.get("/project/export/excel")
async def export_project_report_excel(
    project_id: int,
    type: str,
    report_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    month: int | None = None,
    year: int | None = None,
    quarter: int | None = None,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):

    response = await project_report(
        project_id=project_id,
        type=type,
        report_date=report_date,
        start_date=start_date,
        end_date=end_date,
        month=month,
        year=year,
        quarter=quarter,
        current_user=current_user,
        db=db,
    )

    wb = Workbook()

    ws = wb.active

    ws.title = "Project Report"

    # =====================================================
    # SUMMARY
    # =====================================================

    ws.append(["Project", response["project"]["project_name"]])
    ws.append(["Report Type", response["report_type"]])
    ws.append([])

    ws.append(["Overall Progress", response["summary"]["overall_progress"]])
    ws.append(["Completed Tasks", response["summary"]["completed_tasks"]])
    ws.append(["Open Issues", response["summary"]["open_issues"]])

    ws.append([])

    ws.append(["Total Invoice", response["financials"]["total_invoice"]])
    ws.append(["Total Expense", response["financials"]["total_expense"]])
    ws.append(["Profit", response["financials"]["profit"]])

    ws.append([])

    # =====================================================
    # DSR
    # =====================================================

    ws.append(
        [
            "Date",
            "Work Done",
            "Weather",
            "Remarks",
        ]
    )

    for r in response["daily_reports"]:
        ws.append(
            [
                str(r["date"]),
                r["work_done"],
                r["weather"],
                r["remarks"],
            ]
        )

    buffer = io.BytesIO()

    wb.save(buffer)

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={type}_project_report.xlsx"
        },
    )


# ===================== BUSINESS INTELLIGENCE KPIs =====================
@router.get("/business-intelligence")
async def business_intelligence_kpis(
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    # Revenue (owner invoices)
    revenue = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.type == "owner", Invoice.status == InvoiceStatus.PAID
        )
    )

    # Expenditure (all expenses)
    expense = await db.scalar(select(func.sum(Expense.amount)))

    revenue_val = float(revenue or 0)
    expense_val = float(expense or 0)
    net_profit = revenue_val - expense_val

    # Activity Log
    documented_reports = await db.scalar(select(func.count(m.DailySiteReport.id)))

    # Efficiency (Active Sites)
    active_sites = await db.scalar(
        select(func.count(m.Project.id)).where(m.Project.status == "Active")
    )

    return {
        "revenue_focus": net_profit,
        "expenditure": expense_val,
        "activity_log": int(documented_reports or 0),
        "efficiency": f"Syncing from {active_sites or 0} sites",
    }


# ===================== WORK CATEGORY SUMMARY =====================
@router.get("/work-category")
async def work_category_summary(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    result = await db.execute(
        select(
            m.Task.discipline,
            func.count(m.Task.id).label("total_tasks"),
            func.sum(case((m.Task.status == TaskStatus.COMPLETED, 1), else_=0)).label(
                "completed_tasks"
            ),
            func.avg(m.Task.completion_percentage).label("avg_progress"),
        )
        .where(m.Task.project_id == project_id)
        .group_by(m.Task.discipline)
    )

    categories = []
    for row in result.all():
        discipline, total_tasks, completed_tasks, avg_progress = row
        categories.append(
            {
                "category": discipline or "General",
                "total_tasks": int(total_tasks or 0),
                "completed_tasks": int(completed_tasks or 0),
                "avg_progress": round(float(avg_progress or 0), 2),
            }
        )

    return {"work_categories": categories}


# ===================== QUARTERLY AUDIT SUMMARY =====================
@router.get("/audit-summary")
async def quarterly_audit_summary(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    # Calculate current quarter bounds
    today = date.today()
    current_quarter = (today.month - 1) // 3 + 1
    start_month = 3 * current_quarter - 2
    start_date = date(today.year, start_month, 1)

    # High Priority Issues in Quarter
    critical_issues = await db.scalar(
        select(func.count(m.Issue.id)).where(
            m.Issue.project_id == project_id,
            m.Issue.priority == "HIGH",
            m.Issue.created_at >= start_date,
        )
    )

    # Audit trail activities
    audit_logs = await db.scalar(
        select(func.count(ActivityLog.id)).where(
            ActivityLog.entity == "project",
            ActivityLog.entity_id == project_id,
            ActivityLog.created_at >= start_date,
        )
    )

    # Expense Audits
    quarterly_expenses = await db.scalar(
        select(func.sum(Expense.amount)).where(
            Expense.project_id == project_id, Expense.expense_date >= start_date
        )
    )

    return {
        "quarter": f"Q{current_quarter} {today.year}",
        "critical_issues_found": int(critical_issues or 0),
        "audit_activities_logged": int(audit_logs or 0),
        "quarterly_expenses_audited": float(quarterly_expenses or 0),
        "compliance_status": (
            "Passed" if (critical_issues or 0) < 5 else "Review Needed"
        ),
    }
