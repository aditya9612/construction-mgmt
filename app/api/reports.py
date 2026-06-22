from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timedelta
import io
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from typing import Optional
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from openpyxl import Workbook
from app.models.contractor import Contractor
from app.models.final_measurement import FinalMeasurement
from app.models.invoice import Invoice, Transaction
from app.models.master_data import LabourType
from app.models.user import UserAttendance
from app.models.material import Material
from app.utils.common import assert_project_access
from app.utils.email import send_email
from app.core.dependencies import get_current_active_user, require_roles
from app.core.enums import InvoiceStatus, IssueStatus, IssuePriority, LabourStatus, ProjectStatus, TaskStatus
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


# ===================== PROJECT REPORTS =====================

@router.get("/projects/excel")
async def export_projects_excel(
    project_id: Optional[int] = Query(None, description="Project ID to filter. If none, exports all projects."),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from app.api.project import get_reports_service
    service = get_reports_service()
    
    if project_id:
        return await service.export_excel(db, project_id, current_user)
        
    # Export ALL projects
    projects_query = select(m.Project)
    projects = (await db.execute(projects_query)).scalars().all()
    
    wb = Workbook()
    ws = wb.active
    ws.title = "All Projects Portfolio"
    
    headers = [
        "ID", "Business ID", "Project Name", "Status", "Type", 
        "Location", "Start Date", "End Date"
    ]
    ws.append(headers)
    
    for p in projects:
        ws.append([
            p.id,
            p.business_id,
            p.project_name,
            str(p.status.value if hasattr(p.status, "value") else p.status),
            str(p.type.value if hasattr(p.type, "value") else p.type) if p.type else "N/A",
            f"{p.city or ''}, {p.state or ''}".strip(", "),
            str(p.start_date) if p.start_date else "N/A",
            str(p.end_date) if p.end_date else "N/A",
        ])
        
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=all_projects_report.xlsx"},
    )

@router.get("/projects/pdf")
async def export_projects_pdf(
    project_id: Optional[int] = Query(None, description="Project ID to filter. If none, exports all projects."),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from app.api.project import get_reports_service
    service = get_reports_service()
    
    if project_id:
        return await service.export_pdf(db, project_id, current_user)
        
    # Export ALL projects
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    
    projects_query = select(m.Project)
    projects = (await db.execute(projects_query)).scalars().all()
    
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []
    
    elements.append(Paragraph("Company Portfolio Overview", styles['Title']))
    elements.append(Spacer(1, 12))
    
    elements.append(Paragraph(f"Generated on: {date.today()}", styles['Normal']))
    elements.append(Spacer(1, 12))
    
    total_projects = len(projects)
    ongoing = sum(1 for p in projects if str(getattr(p.status, "value", p.status)) == "Ongoing")
    completed = sum(1 for p in projects if str(getattr(p.status, "value", p.status)) == "Completed")
    
    elements.append(Paragraph(f"Total Projects: {total_projects}", styles['Normal']))
    elements.append(Paragraph(f"Ongoing: {ongoing}", styles['Normal']))
    elements.append(Paragraph(f"Completed: {completed}", styles['Normal']))
    elements.append(Spacer(1, 24))
    
    table_data = [["ID", "Name", "Status", "Start Date", "End Date"]]
    for p in projects:
        table_data.append([
            p.business_id,
            p.project_name[:30] + "..." if len(p.project_name) > 30 else p.project_name,
            str(getattr(p.status, "value", p.status)),
            str(p.start_date) if p.start_date else "N/A",
            str(p.end_date) if p.end_date else "N/A"
        ])
        
    t = Table(table_data)
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

    stream.seek(0)

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=all_projects_portfolio.pdf"
        }
    )

# ===================== AUDIT REPORTS =====================

@router.get("/audit/excel")
async def export_audit_excel(
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    module: Optional[str] = Query(None, description="Filter by entity/module"),
    action: Optional[str] = Query(None, description="Filter by action (CREATE, UPDATE, DELETE)"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    query = select(ActivityLog, User).outerjoin(User, ActivityLog.performed_by == User.id)
    
    if start_date:
        query = query.where(ActivityLog.created_at >= start_date)
    if end_date:
        query = query.where(ActivityLog.created_at <= end_date + timedelta(days=1))
    if user_id:
        query = query.where(ActivityLog.performed_by == user_id)
    if module:
        query = query.where(ActivityLog.entity == module)
    if action:
        query = query.where(ActivityLog.action == action)
        
    query = query.order_by(ActivityLog.created_at.desc())
    result = await db.execute(query)
    logs = result.all()
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Audit Summary"
    
    headers = ["Timestamp", "User Name", "Module", "Action", "Entity ID", "Details"]
    ws.append(headers)
    
    for log, user in logs:
        details_str = str(log.details) if log.details else ""
        user_name = user.full_name if user else "System/Unknown"
        ws.append([
            str(log.created_at),
            user_name,
            log.entity,
            log.action,
            log.entity_id or "",
            details_str
        ])
        
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=audit_summary_report.xlsx"},
    )

@router.get("/audit/pdf")
async def export_audit_pdf(
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    module: Optional[str] = Query(None, description="Filter by entity/module"),
    action: Optional[str] = Query(None, description="Filter by action (CREATE, UPDATE, DELETE)"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    
    query = select(ActivityLog, User).outerjoin(User, ActivityLog.performed_by == User.id)
    
    if start_date:
        query = query.where(ActivityLog.created_at >= start_date)
    if end_date:
        query = query.where(ActivityLog.created_at <= end_date + timedelta(days=1))
    if user_id:
        query = query.where(ActivityLog.performed_by == user_id)
    if module:
        query = query.where(ActivityLog.entity == module)
    if action:
        query = query.where(ActivityLog.action == action)
        
    query = query.order_by(ActivityLog.created_at.desc()).limit(1000) # Limit PDF to 1000 rows for performance
    result = await db.execute(query)
    logs = result.all()
    
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=landscape(letter))
    styles = getSampleStyleSheet()
    elements = []
    
    elements.append(Paragraph("System Audit Summary", styles['Title']))
    elements.append(Spacer(1, 12))
    
    filter_text = f"Generated: {date.today()}"
    if start_date or end_date:
        filter_text += f" | Period: {start_date or 'Start'} to {end_date or 'End'}"
    if module:
        filter_text += f" | Module: {module}"
    if action:
        filter_text += f" | Action: {action}"
        
    elements.append(Paragraph(filter_text, styles['Normal']))
    elements.append(Spacer(1, 12))
    
    total_logs = len(logs)
    elements.append(Paragraph(f"Total Logs Included: {total_logs} (Max 1000)", styles['Normal']))
    elements.append(Spacer(1, 24))
    
    table_data = [["Date/Time", "User", "Module", "Action", "Details"]]
    for log, user in logs:
        details_str = str(log.details)[:50] + "..." if log.details and len(str(log.details)) > 50 else (str(log.details) if log.details else "N/A")
        user_name = user.full_name if user else "System"
        table_data.append([
            log.created_at.strftime("%Y-%m-%d %H:%M"),
            user_name,
            log.entity or "N/A",
            log.action or "N/A",
            details_str
        ])
        
    t = Table(table_data, colWidths=[100, 100, 100, 100, 300])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.aliceblue),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    
    elements.append(t)
    doc.build(elements)
    
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=audit_summary_report.pdf"}
    )

# ===================== ASSET REPORTS =====================

@router.get("/assets/excel")
async def export_assets_excel(
    project_id: Optional[int] = Query(None, description="Filter by allocated project ID"),
    start_date: Optional[date] = Query(None, description="Purchase start date"),
    end_date: Optional[date] = Query(None, description="Purchase end date"),
    min_value: Optional[float] = Query(None, description="Minimum current value"),
    max_value: Optional[float] = Query(None, description="Maximum current value"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    query = select(FixedAsset, m.Project).outerjoin(m.Project, FixedAsset.project_id == m.Project.id)
    
    if project_id:
        query = query.where(FixedAsset.project_id == project_id)
    if start_date:
        query = query.where(FixedAsset.purchase_date >= start_date)
    if end_date:
        query = query.where(FixedAsset.purchase_date <= end_date)
    if min_value is not None:
        query = query.where(FixedAsset.current_value >= min_value)
    if max_value is not None:
        query = query.where(FixedAsset.current_value <= max_value)
        
    result = await db.execute(query)
    assets = result.all()
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Fixed Asset Register"
    
    headers = [
        "Asset ID", "Asset Name", "Allocated Project", "Purchase Date",
        "Purchase Value", "Depreciation Rate (%)", "Accumulated Depreciation", "Current Net Book Value"
    ]
    ws.append(headers)
    
    for asset, project in assets:
        proj_name = project.project_name if project else "Unallocated"
        purch_val = float(asset.purchase_value or 0)
        curr_val = float(asset.current_value or 0)
        depr_acc = purch_val - curr_val
        
        ws.append([
            asset.id,
            asset.name,
            proj_name,
            str(asset.purchase_date) if asset.purchase_date else "N/A",
            purch_val,
            float(asset.depreciation_rate or 0),
            depr_acc,
            curr_val
        ])
        
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=fixed_asset_register.xlsx"},
    )

@router.get("/assets/pdf")
async def export_assets_pdf(
    project_id: Optional[int] = Query(None, description="Filter by allocated project ID"),
    start_date: Optional[date] = Query(None, description="Purchase start date"),
    end_date: Optional[date] = Query(None, description="Purchase end date"),
    min_value: Optional[float] = Query(None, description="Minimum current value"),
    max_value: Optional[float] = Query(None, description="Maximum current value"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    
    query = select(FixedAsset, m.Project).outerjoin(m.Project, FixedAsset.project_id == m.Project.id)
    
    if project_id:
        query = query.where(FixedAsset.project_id == project_id)
    if start_date:
        query = query.where(FixedAsset.purchase_date >= start_date)
    if end_date:
        query = query.where(FixedAsset.purchase_date <= end_date)
    if min_value is not None:
        query = query.where(FixedAsset.current_value >= min_value)
    if max_value is not None:
        query = query.where(FixedAsset.current_value <= max_value)
        
    result = await db.execute(query)
    assets = result.all()
    
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=landscape(letter))
    styles = getSampleStyleSheet()
    elements = []
    
    elements.append(Paragraph("Fixed Asset & Depreciation Report", styles['Title']))
    elements.append(Spacer(1, 12))
    
    filter_text = f"Generated: {date.today()}"
    if project_id or start_date or end_date or min_value or max_value:
        filter_text += " | Filters Applied"
        
    elements.append(Paragraph(filter_text, styles['Normal']))
    elements.append(Spacer(1, 12))
    
    total_assets = len(assets)
    total_purchase = sum(float(a.FixedAsset.purchase_value or 0) for a in assets)
    total_current = sum(float(a.FixedAsset.current_value or 0) for a in assets)
    total_depr = total_purchase - total_current
    
    elements.append(Paragraph(f"<b>Total Assets:</b> {total_assets}", styles['Normal']))
    elements.append(Paragraph(f"<b>Total Original Value:</b> ${total_purchase:,.2f}", styles['Normal']))
    elements.append(Paragraph(f"<b>Total Accumulated Depreciation:</b> ${total_depr:,.2f}", styles['Normal']))
    elements.append(Paragraph(f"<b>Total Current Net Book Value:</b> ${total_current:,.2f}", styles['Normal']))
    elements.append(Spacer(1, 24))
    
    table_data = [["ID", "Name", "Project", "Purchase Date", "Orig Value", "Depr", "Net Book Value"]]
    for asset, project in assets:
        proj_name = project.project_name[:20] + "..." if project and len(project.project_name) > 20 else (project.project_name if project else "Unallocated")
        purch_val = float(asset.purchase_value or 0)
        curr_val = float(asset.current_value or 0)
        depr_acc = purch_val - curr_val
        
        table_data.append([
            str(asset.id),
            asset.name[:25] + "..." if len(asset.name) > 25 else asset.name,
            proj_name,
            str(asset.purchase_date) if asset.purchase_date else "N/A",
            f"${purch_val:,.2f}",
            f"${depr_acc:,.2f}",
            f"${curr_val:,.2f}"
        ])
        
    t = Table(table_data)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkgreen),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.honeydew),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    
    elements.append(t)
    doc.build(elements)
    
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=fixed_asset_depreciation_report.pdf"}
    )

# ===================== ISSUE REPORTS =====================

@router.get("/issues/excel")
async def export_issues_excel(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    status: Optional[str] = Query(None, description="Filter by status (e.g., OPEN, RESOLVED)"),
    priority: Optional[str] = Query(None, description="Filter by priority (e.g., HIGH, LOW)"),
    start_date: Optional[date] = Query(None, description="Reported start date"),
    end_date: Optional[date] = Query(None, description="Reported end date"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    query = select(m.Issue, m.Project, User).join(m.Project, m.Issue.project_id == m.Project.id).outerjoin(User, m.Issue.assigned_to == User.id)
    
    if project_id:
        query = query.where(m.Issue.project_id == project_id)
    if status:
        query = query.where(m.Issue.status == status)
    if priority:
        query = query.where(m.Issue.priority == priority)
    if start_date:
        query = query.where(m.Issue.reported_date >= start_date)
    if end_date:
        query = query.where(m.Issue.reported_date <= end_date)
        
    query = query.order_by(m.Issue.reported_date.desc())
    result = await db.execute(query)
    issues = result.all()
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Site Issue Log"
    
    headers = [
        "Issue ID", "Project Name", "Title", "Category", 
        "Reported Date", "Priority", "Status", "Assigned To", 
        "Description", "Resolution Notes"
    ]
    ws.append(headers)
    
    for issue, project, user in issues:
        assigned_name = user.full_name if user else "Unassigned"
        ws.append([
            issue.business_id or str(issue.id),
            project.project_name,
            issue.title,
            str(issue.category.value if hasattr(issue.category, "value") else issue.category),
            str(issue.reported_date) if issue.reported_date else "N/A",
            str(issue.priority.value if hasattr(issue.priority, "value") else issue.priority),
            str(issue.status.value if hasattr(issue.status, "value") else issue.status),
            assigned_name,
            issue.description or "",
            issue.resolution or ""
        ])
        
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=site_issue_log.xlsx"},
    )

@router.get("/issues/pdf")
async def export_issues_pdf(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    status: Optional[str] = Query(None, description="Filter by status (e.g., OPEN, RESOLVED)"),
    priority: Optional[str] = Query(None, description="Filter by priority (e.g., HIGH, LOW)"),
    start_date: Optional[date] = Query(None, description="Reported start date"),
    end_date: Optional[date] = Query(None, description="Reported end date"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    
    query = select(m.Issue, m.Project, User).join(m.Project, m.Issue.project_id == m.Project.id).outerjoin(User, m.Issue.assigned_to == User.id)
    
    if project_id:
        query = query.where(m.Issue.project_id == project_id)
    if status:
        query = query.where(m.Issue.status == status)
    if priority:
        query = query.where(m.Issue.priority == priority)
    if start_date:
        query = query.where(m.Issue.reported_date >= start_date)
    if end_date:
        query = query.where(m.Issue.reported_date <= end_date)
        
    query = query.order_by(m.Issue.reported_date.desc()).limit(1000)
    result = await db.execute(query)
    issues = result.all()
    
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=landscape(letter))
    styles = getSampleStyleSheet()
    elements = []
    
    elements.append(Paragraph("Executive Site Issue Report", styles['Title']))
    elements.append(Spacer(1, 12))
    
    filter_text = f"Generated: {date.today()}"
    if project_id or status or priority or start_date or end_date:
        filter_text += " | Filters Applied"
        
    elements.append(Paragraph(filter_text, styles['Normal']))
    elements.append(Spacer(1, 12))
    
    total_issues = len(issues)
    open_count = sum(1 for i in issues if str(getattr(i.Issue.status, "value", i.Issue.status)) in ["OPEN", "IN_PROGRESS"])
    resolved_count = sum(1 for i in issues if str(getattr(i.Issue.status, "value", i.Issue.status)) in ["RESOLVED", "CLOSED"])
    critical_count = sum(1 for i in issues if str(getattr(i.Issue.priority, "value", i.Issue.priority)) in ["HIGH", "CRITICAL"])
    
    elements.append(Paragraph(f"<b>Total Issues:</b> {total_issues}", styles['Normal']))
    elements.append(Paragraph(f"<b>Open/In-Progress:</b> {open_count}", styles['Normal']))
    elements.append(Paragraph(f"<b>Resolved/Closed:</b> {resolved_count}", styles['Normal']))
    elements.append(Paragraph(f"<b>High Priority:</b> {critical_count}", styles['Normal']))
    elements.append(Spacer(1, 24))
    
    table_data = [["ID", "Date", "Project", "Title", "Priority", "Status", "Assigned To"]]
    for issue, project, user in issues:
        proj_name = project.project_name[:15] + "..." if len(project.project_name) > 15 else project.project_name
        title = issue.title[:25] + "..." if len(issue.title) > 25 else issue.title
        assigned_name = user.full_name[:15] + "..." if user and len(user.full_name) > 15 else (user.full_name if user else "Unassigned")
        
        table_data.append([
            issue.business_id or str(issue.id),
            str(issue.reported_date) if issue.reported_date else "N/A",
            proj_name,
            title,
            str(getattr(issue.priority, "value", issue.priority)),
            str(getattr(issue.status, "value", issue.status)),
            assigned_name
        ])
        
    t = Table(table_data)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkred),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.mistyrose),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    
    elements.append(t)
    doc.build(elements)
    
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=executive_issue_report.pdf"}
    )

# ===================== FINANCIAL REPORTS =====================

@router.get("/finance/excel")
async def export_finance_excel(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    start_date: Optional[date] = Query(None, description="Start date for financial period"),
    end_date: Optional[date] = Query(None, description="End date for financial period"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy.future import select
    from collections import defaultdict
    
    # 1. Fetch Projects
    proj_query = select(m.Project)
    if project_id:
        proj_query = proj_query.where(m.Project.id == project_id)
    projects = (await db.execute(proj_query)).scalars().all()
    project_map = {p.id: p.project_name for p in projects}
    
    # 2. Fetch Expenses (grouped by project and category)
    exp_query = select(Expense.project_id, Expense.category, func.sum(Expense.amount)).group_by(Expense.project_id, Expense.category)
    if project_id:
        exp_query = exp_query.where(Expense.project_id == project_id)
    if start_date:
        exp_query = exp_query.where(Expense.expense_date >= start_date)
    if end_date:
        exp_query = exp_query.where(Expense.expense_date <= end_date)
        
    exp_result = await db.execute(exp_query)
    
    project_expenses = defaultdict(lambda: defaultdict(float))
    all_categories = set()
    for pid, cat, amount in exp_result.all():
        if pid in project_map:
            project_expenses[pid][cat] += float(amount or 0)
            all_categories.add(cat)
            
    # 3. Fetch Invoices (grouped by project and status)
    inv_query = select(Invoice.project_id, Invoice.status, func.sum(Invoice.total_amount)).group_by(Invoice.project_id, Invoice.status)
    if project_id:
        inv_query = inv_query.where(Invoice.project_id == project_id)
    if start_date:
        inv_query = inv_query.where(Invoice.created_at >= start_date)
    if end_date:
        # Cast created_at to Date for accurate comparison, or just add days
        inv_query = inv_query.where(Invoice.created_at <= end_date + timedelta(days=1))
        
    inv_result = await db.execute(inv_query)
    
    project_invoices = defaultdict(lambda: defaultdict(float))
    for pid, status, amount in inv_result.all():
        if pid in project_map:
            status_str = status.value if hasattr(status, "value") else str(status)
            project_invoices[pid][status_str] += float(amount or 0)
            
    # 4. Generate Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "Financial Ledger"
    
    sorted_categories = sorted(list(all_categories))
    
    headers = [
        "Project ID", "Project Name", 
        "Total Invoiced", "Amount Paid", "Amount Pending",
        "Total Expenses"
    ]
    # Add dynamic category headers for expenses
    for cat in sorted_categories:
        headers.append(f"Exp: {cat}")
        
    headers.append("Net Profit / Loss")
    headers.append("Profit Margin (%)")
    ws.append(headers)
    
    for pid, p_name in project_map.items():
        inv_totals = project_invoices[pid]
        total_inv = sum(inv_totals.values())
        paid_inv = inv_totals.get("PAID", 0.0) + inv_totals.get("PARTIAL", 0.0) # simplify
        pending_inv = inv_totals.get("PENDING", 0.0)
        
        exp_totals = project_expenses[pid]
        total_exp = sum(exp_totals.values())
        
        net_profit = total_inv - total_exp
        margin = (net_profit / total_inv * 100) if total_inv > 0 else 0.0
        
        row = [
            pid,
            p_name,
            total_inv,
            paid_inv,
            pending_inv,
            total_exp
        ]
        
        for cat in sorted_categories:
            row.append(exp_totals.get(cat, 0.0))
            
        row.append(net_profit)
        row.append(round(margin, 2))
        
        ws.append(row)
        
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=financial_ledger_report.xlsx"},
    )

@router.get("/finance/pdf")
async def export_finance_pdf(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    start_date: Optional[date] = Query(None, description="Start date for financial period"),
    end_date: Optional[date] = Query(None, description="End date for financial period"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy.future import select
    from collections import defaultdict
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    
    proj_query = select(m.Project)
    if project_id:
        proj_query = proj_query.where(m.Project.id == project_id)
    projects = (await db.execute(proj_query)).scalars().all()
    project_map = {p.id: p.project_name for p in projects}
    
    exp_query = select(Expense.project_id, func.sum(Expense.amount)).group_by(Expense.project_id)
    if project_id:
        exp_query = exp_query.where(Expense.project_id == project_id)
    if start_date:
        exp_query = exp_query.where(Expense.expense_date >= start_date)
    if end_date:
        exp_query = exp_query.where(Expense.expense_date <= end_date)
        
    exp_result = await db.execute(exp_query)
    project_expenses = {pid: float(amt or 0) for pid, amt in exp_result.all()}
    
    inv_query = select(Invoice.project_id, Invoice.status, func.sum(Invoice.total_amount)).group_by(Invoice.project_id, Invoice.status)
    if project_id:
        inv_query = inv_query.where(Invoice.project_id == project_id)
    if start_date:
        inv_query = inv_query.where(Invoice.created_at >= start_date)
    if end_date:
        inv_query = inv_query.where(Invoice.created_at <= end_date + timedelta(days=1))
        
    inv_result = await db.execute(inv_query)
    project_invoices = defaultdict(lambda: defaultdict(float))
    for pid, status, amount in inv_result.all():
        status_str = status.value if hasattr(status, "value") else str(status)
        project_invoices[pid][status_str] += float(amount or 0)
        
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=landscape(letter))
    styles = getSampleStyleSheet()
    elements = []
    
    elements.append(Paragraph("Executive Financial Summary", styles['Title']))
    elements.append(Spacer(1, 12))
    
    filter_text = f"Generated: {date.today()}"
    if project_id or start_date or end_date:
        filter_text += " | Filters Applied"
        
    elements.append(Paragraph(filter_text, styles['Normal']))
    elements.append(Spacer(1, 12))
    
    global_exp = sum(project_expenses.values())
    global_inv = sum(sum(inv.values()) for inv in project_invoices.values())
    global_profit = global_inv - global_exp
    global_margin = (global_profit / global_inv * 100) if global_inv > 0 else 0.0
    
    elements.append(Paragraph(f"<b>Total Company Expenses:</b> ${global_exp:,.2f}", styles['Normal']))
    elements.append(Paragraph(f"<b>Total Company Invoiced:</b> ${global_inv:,.2f}", styles['Normal']))
    elements.append(Paragraph(f"<b>Total Net Profit:</b> ${global_profit:,.2f}", styles['Normal']))
    elements.append(Paragraph(f"<b>Overall Profit Margin:</b> {global_margin:,.2f}%", styles['Normal']))
    elements.append(Spacer(1, 24))
    
    table_data = [["Project", "Total Expenses", "Total Invoiced", "Pending", "Net Profit", "Margin"]]
    for pid, p_name in project_map.items():
        total_exp = project_expenses.get(pid, 0.0)
        inv_totals = project_invoices[pid]
        total_inv = sum(inv_totals.values())
        pending_inv = inv_totals.get("PENDING", 0.0)
        
        net_profit = total_inv - total_exp
        margin = (net_profit / total_inv * 100) if total_inv > 0 else 0.0
        
        p_name_short = p_name[:25] + "..." if len(p_name) > 25 else p_name
        
        table_data.append([
            p_name_short,
            f"${total_exp:,.2f}",
            f"${total_inv:,.2f}",
            f"${pending_inv:,.2f}",
            f"${net_profit:,.2f}",
            f"{margin:,.2f}%"
        ])
        
    t = Table(table_data)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkcyan),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('ALIGN', (0,0), (0,-1), 'LEFT'), # Left align project name
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.lightcyan),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    
    elements.append(t)
    doc.build(elements)
    
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=executive_financial_summary.pdf"}
    )

# ===================== PROFIT & LOSS REPORTS =====================

@router.get("/profit-loss/excel")
async def export_profit_loss_excel(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    year: Optional[int] = Query(None, description="Financial Year"),
    quarter: Optional[int] = Query(None, description="Quarter (1-4)"),
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy.future import select
    from sqlalchemy import extract
    from collections import defaultdict
    import calendar
    
    # Base queries
    inv_query = select(Invoice)
    exp_query = select(Expense)
    
    if project_id:
        inv_query = inv_query.where(Invoice.project_id == project_id)
        exp_query = exp_query.where(Expense.project_id == project_id)
        
    if year:
        inv_query = inv_query.where(extract('year', Invoice.created_at) == year)
        exp_query = exp_query.where(extract('year', Expense.expense_date) == year)
        if quarter:
            q_months = {1: (1,3), 2: (4,6), 3: (7,9), 4: (10,12)}
            sm, em = q_months[quarter]
            inv_query = inv_query.where(extract('month', Invoice.created_at).between(sm, em))
            exp_query = exp_query.where(extract('month', Expense.expense_date).between(sm, em))
            
    if start_date:
        inv_query = inv_query.where(Invoice.created_at >= start_date)
        exp_query = exp_query.where(Expense.expense_date >= start_date)
    if end_date:
        inv_query = inv_query.where(Invoice.created_at <= end_date + timedelta(days=1))
        exp_query = exp_query.where(Expense.expense_date <= end_date)
        
    invoices = (await db.execute(inv_query)).scalars().all()
    expenses = (await db.execute(exp_query)).scalars().all()
    
    # Group by YYYY-MM
    monthly_data = defaultdict(lambda: {"revenue": 0.0, "cogs_labour": 0.0, "cogs_material": 0.0, "overhead": defaultdict(float)})
    all_months = set()
    
    for inv in invoices:
        month_key = inv.created_at.strftime("%Y-%m")
        all_months.add(month_key)
        amt = float(inv.total_amount or 0)
        
        # 'owner' invoices are revenue. others are COGS
        if inv.type == "owner":
            monthly_data[month_key]["revenue"] += amt
        elif inv.type == "labour":
            monthly_data[month_key]["cogs_labour"] += amt
        elif inv.type == "material":
            monthly_data[month_key]["cogs_material"] += amt
            
    for exp in expenses:
        month_key = exp.expense_date.strftime("%Y-%m")
        all_months.add(month_key)
        amt = float(exp.amount or 0)
        monthly_data[month_key]["overhead"][exp.category] += amt
        
    sorted_months = sorted(list(all_months))
    all_overhead_cats = set()
    for data in monthly_data.values():
        all_overhead_cats.update(data["overhead"].keys())
    sorted_overhead_cats = sorted(list(all_overhead_cats))
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Profit and Loss"
    
    headers = ["Category", "Total"] + sorted_months
    ws.append(headers)
    
    def append_row(name, data_dict, overhead_cat=None):
        row = [name]
        total = 0.0
        month_vals = []
        for m in sorted_months:
            if overhead_cat:
                val = data_dict[m]["overhead"].get(overhead_cat, 0.0)
            else:
                val = data_dict[m].get(name.lower().replace(" ", "_"), 0.0)
                # handle specific mappings
                if name == "Revenue": val = data_dict[m]["revenue"]
                elif name == "Labour Costs": val = data_dict[m]["cogs_labour"]
                elif name == "Material Costs": val = data_dict[m]["cogs_material"]
            total += val
            month_vals.append(val)
        row.append(total)
        row.extend(month_vals)
        ws.append(row)
        return total, month_vals
        
    ws.append(["--- REVENUE ---"])
    total_rev, rev_months = append_row("Revenue", monthly_data)
    
    ws.append([])
    ws.append(["--- COST OF GOODS SOLD (COGS) ---"])
    t_labour, m_labour = append_row("Labour Costs", monthly_data)
    t_material, m_material = append_row("Material Costs", monthly_data)
    
    total_cogs = t_labour + t_material
    cogs_months = [l + m for l, m in zip(m_labour, m_material)]
    
    gross_profit = total_rev - total_cogs
    gp_months = [r - c for r, c in zip(rev_months, cogs_months)]
    
    ws.append(["Gross Profit", gross_profit] + gp_months)
    
    ws.append([])
    ws.append(["--- OPERATING EXPENSES (OVERHEAD) ---"])
    total_op_ex = 0.0
    op_ex_months = [0.0] * len(sorted_months)
    for cat in sorted_overhead_cats:
        t_cat, m_cat = append_row(cat, monthly_data, overhead_cat=cat)
        total_op_ex += t_cat
        op_ex_months = [o + c for o, c in zip(op_ex_months, m_cat)]
        
    ws.append(["Total Operating Expenses", total_op_ex] + op_ex_months)
    
    ws.append([])
    ws.append(["--- NET INCOME ---"])
    net_income = gross_profit - total_op_ex
    ni_months = [g - o for g, o in zip(gp_months, op_ex_months)]
    ws.append(["Net Income", net_income] + ni_months)
    
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=profit_and_loss.xlsx"},
    )

@router.get("/profit-loss/pdf")
async def export_profit_loss_pdf(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    year: Optional[int] = Query(None, description="Financial Year"),
    quarter: Optional[int] = Query(None, description="Quarter (1-4)"),
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy.future import select
    from sqlalchemy import extract
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    
    inv_query = select(Invoice)
    exp_query = select(Expense)
    
    if project_id:
        inv_query = inv_query.where(Invoice.project_id == project_id)
        exp_query = exp_query.where(Expense.project_id == project_id)
        
    if year:
        inv_query = inv_query.where(extract('year', Invoice.created_at) == year)
        exp_query = exp_query.where(extract('year', Expense.expense_date) == year)
        if quarter:
            q_months = {1: (1,3), 2: (4,6), 3: (7,9), 4: (10,12)}
            sm, em = q_months[quarter]
            inv_query = inv_query.where(extract('month', Invoice.created_at).between(sm, em))
            exp_query = exp_query.where(extract('month', Expense.expense_date).between(sm, em))
            
    if start_date:
        inv_query = inv_query.where(Invoice.created_at >= start_date)
        exp_query = exp_query.where(Expense.expense_date >= start_date)
    if end_date:
        inv_query = inv_query.where(Invoice.created_at <= end_date + timedelta(days=1))
        exp_query = exp_query.where(Expense.expense_date <= end_date)
        
    invoices = (await db.execute(inv_query)).scalars().all()
    expenses = (await db.execute(exp_query)).scalars().all()
    
    revenue = 0.0
    cogs_labour = 0.0
    cogs_material = 0.0
    overhead = defaultdict(float)
    
    for inv in invoices:
        amt = float(inv.total_amount or 0)
        if inv.type == "owner": revenue += amt
        elif inv.type == "labour": cogs_labour += amt
        elif inv.type == "material": cogs_material += amt
            
    total_overhead = 0.0
    for exp in expenses:
        amt = float(exp.amount or 0)
        overhead[exp.category] += amt
        total_overhead += amt
        
    cogs = cogs_labour + cogs_material
    gross_profit = revenue - cogs
    net_income = gross_profit - total_overhead
    margin = (net_income / revenue * 100) if revenue > 0 else 0.0
    
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []
    
    elements.append(Paragraph("Profit & Loss Statement", styles['Title']))
    elements.append(Spacer(1, 12))
    
    filter_text = f"Generated: {date.today()}"
    if project_id: filter_text += f" | Project ID: {project_id}"
    if year: filter_text += f" | Year: {year}"
    if quarter: filter_text += f" | Quarter: {quarter}"
    
    elements.append(Paragraph(filter_text, styles['Normal']))
    elements.append(Spacer(1, 20))
    
    # Statement Table
    table_data = [
        ["Revenue", f"${revenue:,.2f}"],
        ["", ""],
        ["Cost of Goods Sold (COGS)", ""],
        ["  Labour Costs", f"${cogs_labour:,.2f}"],
        ["  Material Costs", f"${cogs_material:,.2f}"],
        ["Total COGS", f"${cogs:,.2f}"],
        ["", ""],
        ["Gross Profit", f"${gross_profit:,.2f}"],
        ["", ""],
        ["Operating Expenses (Overhead)", ""]
    ]
    
    for cat, amt in overhead.items():
        table_data.append([f"  {cat}", f"${amt:,.2f}"])
        
    table_data.extend([
        ["Total Operating Expenses", f"${total_overhead:,.2f}"],
        ["", ""],
        ["Net Income", f"${net_income:,.2f}"],
        ["Net Profit Margin", f"{margin:,.2f}%"]
    ])
    
    t = Table(table_data, colWidths=[300, 150])
    t.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTNAME', (0,0), (0,0), 'Helvetica-Bold'), # Revenue
        ('FONTNAME', (0,2), (0,2), 'Helvetica-Bold'), # COGS title
        ('FONTNAME', (0,5), (-1,5), 'Helvetica-Bold'), # Total COGS
        ('FONTNAME', (0,7), (-1,7), 'Helvetica-Bold'), # Gross Profit
        ('FONTNAME', (0,9), (0,9), 'Helvetica-Bold'), # OpEx Title
        ('FONTNAME', (0,-4), (-1,-4), 'Helvetica-Bold'), # Total OpEx
        ('FONTNAME', (0,-2), (-1,-1), 'Helvetica-Bold'), # Net Income / Margin
        ('LINEABOVE', (1,5), (1,5), 1, colors.black), # Line above Total COGS
        ('LINEABOVE', (1,7), (1,7), 1, colors.black), # Line above Gross Profit
        ('LINEABOVE', (1,-4), (1,-4), 1, colors.black), # Line above Total OpEx
        ('LINEABOVE', (1,-2), (1,-2), 2, colors.black), # Double line above Net Income
        ('LINEBELOW', (1,-2), (1,-2), 2, colors.black), # Double line below Net Income
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
    ]))
    
    elements.append(t)
    doc.build(elements)
    
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=profit_and_loss_statement.pdf"}
    )

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
    from app.models.labour import Labour

    result = await db.execute(
        select(LabourType.skill_category, func.count(func.distinct(Labour.id)))
        .join(Labour, Labour.labour_type_id == LabourType.id)
        .join(UserAttendance, Labour.user_id == UserAttendance.user_id)
        .where(
            UserAttendance.project_id == project_id,
            Labour.status == LabourStatus.ACTIVE,
        )
        .group_by(LabourType.skill_category)
    )

    rows = result.all()

    return {"labour_summary": [{"skill_type": row[0], "count": row[1]} for row in rows]}


# ===================== LABOUR DISTRIBUTION REPORTS =====================

@router.get("/labour-distribution/excel")
async def export_labour_distribution_excel(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    date: Optional[date] = Query(None, description="Specific date for attendance filter"),
    skill_category: Optional[str] = Query(None, description="Filter by SKILLED, UNSKILLED, etc"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy.future import select
    from app.models.labour import Labour, LabourProject
    from app.models.user import UserAttendance
    from collections import defaultdict
    
    query = select(Labour, m.Project, LabourType).join(LabourProject, Labour.id == LabourProject.labour_id).join(m.Project, LabourProject.project_id == m.Project.id).outerjoin(LabourType, Labour.labour_type_id == LabourType.id)
    
    if project_id:
        query = query.where(m.Project.id == project_id)
    if skill_category:
        query = query.where(LabourType.skill_category == skill_category)
        
    query = query.where(Labour.status == LabourStatus.ACTIVE)
    results = (await db.execute(query)).all()
    
    attendance_map = {}
    if date:
        att_query = select(UserAttendance.user_id, UserAttendance.status).where(UserAttendance.date == date)
        if project_id:
            att_query = att_query.where(UserAttendance.project_id == project_id)
        att_results = (await db.execute(att_query)).all()
        attendance_map = {user_id: status for user_id, status in att_results}
        
    wb = Workbook()
    ws_agg = wb.active
    ws_agg.title = "Distribution Summary"
    ws_agg.append(["Project Name", "Skill Category", "Trade", "Total Count"])
    
    agg_data = defaultdict(int)
    
    ws_det = wb.create_sheet(title="Detailed Roster")
    det_headers = ["Project Name", "Worker Code", "Worker Name", "Skill Category", "Trade", "Status"]
    if date: det_headers.append(f"Attendance ({date})")
    ws_det.append(det_headers)
    
    for labour, project, ltype in results:
        skill = str(getattr(ltype.skill_category, "value", ltype.skill_category)) if ltype and getattr(ltype, "skill_category", None) else "Unclassified"
        trade = ltype.name if ltype else "N/A"
        
        att_status = "Not Logged"
        if date and labour.user_id:
            att_status_raw = attendance_map.get(labour.user_id)
            att_status = str(getattr(att_status_raw, "value", att_status_raw)) if att_status_raw else "Absent"
            
        row = [
            project.project_name,
            labour.worker_code,
            labour.labour_name,
            skill,
            trade,
            str(getattr(labour.status, "value", labour.status))
        ]
        if date: row.append(att_status)
        ws_det.append(row)
        
        agg_key = (project.project_name, skill, trade)
        agg_data[agg_key] += 1
        
    for (proj, skill, trade), count in sorted(agg_data.items()):
        ws_agg.append([proj, skill, trade, count])
        
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=labour_distribution_report.xlsx"},
    )

@router.get("/labour-distribution/pdf")
async def export_labour_distribution_pdf(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    skill_category: Optional[str] = Query(None, description="Filter by SKILLED, UNSKILLED, etc"),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy.future import select
    from app.models.labour import Labour, LabourProject
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    from collections import defaultdict
    
    query = select(Labour, m.Project, LabourType).join(LabourProject, Labour.id == LabourProject.labour_id).join(m.Project, LabourProject.project_id == m.Project.id).outerjoin(LabourType, Labour.labour_type_id == LabourType.id).where(Labour.status == LabourStatus.ACTIVE)
    
    if project_id: query = query.where(m.Project.id == project_id)
    if skill_category: query = query.where(LabourType.skill_category == skill_category)
        
    results = (await db.execute(query)).all()
    
    project_stats = defaultdict(lambda: {"SKILLED": 0, "UNSKILLED": 0, "SEMI_SKILLED": 0, "OTHER": 0})
    total_workers = 0
    total_skilled = 0
    total_unskilled = 0
    
    for labour, project, ltype in results:
        skill = str(getattr(ltype.skill_category, "value", ltype.skill_category)).upper() if ltype and getattr(ltype, "skill_category", None) else "OTHER"
        if skill not in project_stats[project.project_name]: skill = "OTHER"
        
        project_stats[project.project_name][skill] += 1
        total_workers += 1
        if skill == "SKILLED": total_skilled += 1
        elif skill == "UNSKILLED": total_unskilled += 1
        
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []
    
    elements.append(Paragraph("Executive Labour Distribution Summary", styles['Title']))
    elements.append(Spacer(1, 12))
    
    filter_text = f"Generated: {datetime.now().strftime('%Y-%m-%d')}"
    if project_id: filter_text += f" | Project ID: {project_id}"
    elements.append(Paragraph(filter_text, styles['Normal']))
    elements.append(Spacer(1, 20))
    
    elements.append(Paragraph(f"<b>Total Active Workforce:</b> {total_workers}", styles['Normal']))
    if total_workers > 0:
        elements.append(Paragraph(f"<b>Skilled:</b> {total_skilled} ({total_skilled/total_workers*100:.1f}%) | <b>Unskilled:</b> {total_unskilled} ({total_unskilled/total_workers*100:.1f}%)", styles['Normal']))
    elements.append(Paragraph(f"<b>Total Active Projects:</b> {len(project_stats)}", styles['Normal']))
    elements.append(Spacer(1, 24))
    
    table_data = [["Project Name", "Skilled", "Semi", "Unskilled", "Other", "Total", "% of Company"]]
    for proj, stats in sorted(project_stats.items()):
        p_total = sum(stats.values())
        pct = (p_total / total_workers * 100) if total_workers > 0 else 0
        table_data.append([
            proj[:25] + "..." if len(proj) > 25 else proj,
            stats["SKILLED"],
            stats["SEMI_SKILLED"],
            stats["UNSKILLED"],
            stats["OTHER"],
            p_total,
            f"{pct:.1f}%"
        ])
        
    t = Table(table_data)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (0,0), (0,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.aliceblue),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))
    
    elements.append(t)
    doc.build(elements)
    stream.seek(0)
    
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=labour_distribution_summary.pdf"}
    )


# ===================== MATERIAL REPORT =====================


@router.get("/material")
async def material_report(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(select(Material).where(Material.project_id == project_id).order_by(Material.created_at.desc()))

    materials = result.scalars().all()

    return {"materials": materials}


# ===================== MATERIAL EXCEL =====================


@router.get("/material/export/excel")
async def export_material_excel(
    project_id: int,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(select(Material).where(Material.project_id == project_id).order_by(Material.created_at.desc()))
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
            m.Issue.status == IssueStatus.OPEN.value,
        )
    )

    closed_issues = await db.scalar(
        select(func.count())
        .select_from(m.Issue)
        .where(
            m.Issue.project_id == project_id,
            m.Issue.status == IssueStatus.CLOSED.value,
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
    result = await db.execute(select(m.Issue).where(m.Issue.project_id == project_id).order_by(m.Issue.created_at.desc()))
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
        .order_by(m.DailySiteReport.report_date.desc())
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
async def project_financial_summary_by_id(
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
async def quarterly_financial_audit(
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


from typing import Optional
@router.get("/project")
async def project_report(
    type: str,
    project_id: Optional[int] = None,
    report_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    month: int | None = None,
    year: int | None = None,
    quarter: int | None = None,
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = []
    if project_id is not None:
        await assert_project_access(db, project_id=project_id, current_user=current_user)
        project_ids = [project_id]
    else:
        # Get all accessible projects
        if current_user.role in [UserRole.ADMIN.value, UserRole.OWNER.value, UserRole.ACCOUNTANT.value]:
            res = await db.execute(select(m.Project.id))
        else:
            res = await db.execute(select(m.Project.id).where(m.Project.id.in_(
                select(m.ProjectAssignment.project_id).where(m.ProjectAssignment.user_id == current_user.id)
            )))
        project_ids = res.scalars().all()
        if not project_ids:
            raise HTTPException(status_code=403, detail="No accessible projects found")

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

    if project_id is not None:
        project = await db.scalar(select(m.Project).where(m.Project.id == project_id))
        project_data = {"id": project.id, "project_name": project.project_name}
    else:
        project_data = {"id": None, "project_name": "All Authorized Projects"}

    # =====================================================
    # TASK SUMMARY
    # =====================================================

    total_tasks = await db.scalar(
        select(func.count()).select_from(m.Task).where(m.Task.project_id.in_(project_ids))
    )

    completed_tasks = await db.scalar(
        select(func.count())
        .select_from(m.Task)
        .where(m.Task.project_id.in_(project_ids), m.Task.status == TaskStatus.COMPLETED.value if hasattr(TaskStatus.COMPLETED, 'value') else TaskStatus.COMPLETED)
    )

    progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id.in_(project_ids)
        )
    )

    # =====================================================
    # FINANCIALS
    # =====================================================

    total_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(Invoice.project_id.in_(project_ids))
    )

    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id.in_(project_ids))
    )

    # =====================================================
    # ISSUES
    # =====================================================

    open_issues = await db.scalar(
        select(func.count())
        .select_from(m.Issue)
        .where(m.Issue.project_id.in_(project_ids), m.Issue.status == IssueStatus.OPEN.value if hasattr(IssueStatus.OPEN, 'value') else IssueStatus.OPEN)
    )

    # =====================================================
    # DSR
    # =====================================================

    dsr_list = []
    if project_id is not None:
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
        "project": project_data,
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
    type: str,
    project_id: Optional[int] = None,
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
    type: str,
    project_id: Optional[int] = None,
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
        select(func.count(m.Project.id)).where(m.Project.status == ProjectStatus.ONGOING.value)
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
            m.Issue.priority == IssuePriority.HIGH.value,
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


# ===================== COMMERCIAL & BOQ EXECUTION ======================


@router.get("/commercial-execution")
async def commercial_execution_analytics(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
):
    from app.models.boq import BOQ
    from app.models.final_measurement import FinalMeasurement

    # BOQ Items
    boq_items = (
        (await db.execute(select(BOQ).where(BOQ.project_id == project_id)))
        .scalars()
        .all()
    )

    # Planned Cost from BOQ
    boq_total_planned_cost = sum(float(item.total_cost or 0) for item in boq_items)

    # Final Measurements
    measurements = (
        (
            await db.execute(
                select(FinalMeasurement).where(
                    FinalMeasurement.project_id == project_id,
                    FinalMeasurement.status.in_(["VERIFIED", "APPROVED", "BILLED"]),
                )
            )
        )
        .scalars()
        .all()
    )

    # Actual Certified Amount
    actual_certified_amount = sum(
        float(getattr(m, "total_amount", 0) or 0) for m in measurements
    )

    variance = boq_total_planned_cost - actual_certified_amount

    billing_efficiency = round(
        (
            (actual_certified_amount / boq_total_planned_cost * 100)
            if boq_total_planned_cost > 0
            else 0
        ),
        2,
    )

    return {
        "project_id": project_id,
        "boq_items_count": len(boq_items),
        "measurements_count": len(measurements),
        "boq_total_planned_cost": boq_total_planned_cost,
        "actual_certified_amount": actual_certified_amount,
        "variance": variance,
        "billing_efficiency": billing_efficiency,
    }


# ===================== CONTRACTOR EXECUTION =====================
@router.get("/contractor-execution")
async def contractor_execution_analytics(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(REPORT_READ_ROLES)),
):
    from app.models.billing import RABill

    bills = (
        (await db.execute(select(RABill).where(RABill.project_id == project_id)))
        .scalars()
        .all()
    )

    contractor_stats = {}
    for bill in bills:
        cid = bill.contractor_id
        if cid not in contractor_stats:
            contractor_stats[cid] = {
                "total_billed": 0,
                "paid_amount": 0,
                "bill_count": 0,
            }

        contractor_stats[cid]["total_billed"] += float(bill.gross_amount)
        contractor_stats[cid]["bill_count"] += 1
        if bill.status == "Paid":
            contractor_stats[cid]["paid_amount"] += float(bill.net_amount)

    return {"project_id": project_id, "contractor_stats": contractor_stats}
