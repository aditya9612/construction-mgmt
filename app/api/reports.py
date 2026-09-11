# ======================================================================
# SECTION 1: REPORT THEME (PdfReportBuilder / ExcelReportBuilder)
# ======================================================================

from app.models.final_measurement import FinalMeasurement
import json
import os
from datetime import datetime
from typing import Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import inch, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Image,
)

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


# ─────────────────────────── COLOR PALETTE ──────────────────────────────────
NAVY_BLUE = colors.HexColor("#0B2B5C")
LIGHT_GRAY = colors.HexColor("#F8F9FA")
BORDER_GRAY = colors.HexColor("#E2E8F0")
GREEN = colors.HexColor("#27AE60")
RED = colors.HexColor("#E74C3C")
ORANGE = colors.HexColor("#F39C12")

NAVY_HEX = "0B2B5C"
LIGHT_GRAY_HEX = "F8F9FA"
BORDER_GRAY_HEX = "E2E8F0"
GREEN_HEX = "27AE60"
RED_HEX = "E74C3C"
ORANGE_HEX = "F39C12"

LOGO_PATH = "static/logo.png"


# ═══════════════════════════ PDF HELPERS ═════════════════════════════════════
class PdfReportBuilder:
    """
    Usage:
        b = PdfReportBuilder("EQUIPMENT MANAGEMENT REPORT", landscape_mode=True)
        b.add_info_table([
            ("Report Date", "2026-07-28", "Report Type", "Audit Summary"),
        ])
        b.add_summary_box("2. SUMMARY", [
            "<b>Total Logs:</b> 128 | <b>Period:</b> 2026-07-01 to 2026-07-28",
        ])
        b.add_section_table("3. LOG DETAILS", headers, rows, col_widths)
        pdf_bytes_io = b.build()  # BytesIO, ready for StreamingResponse
    """

    def __init__(
        self, title: str, landscape_mode: bool = False, subtitle: Optional[str] = None
    ):
        import io

        self.stream = io.BytesIO()
        pagesize = landscape(A4) if landscape_mode else A4
        self.doc = SimpleDocTemplate(
            self.stream,
            pagesize=pagesize,
            rightMargin=cm,
            leftMargin=cm,
            topMargin=cm,
            bottomMargin=cm,
        )
        self.page_width = pagesize[0]
        self.usable_width = self.page_width - 2 * cm
        self._section_no = 0
        self.elements = []

        styles = getSampleStyleSheet()
        self.title_style = ParagraphStyle(
            "RptTitle",
            parent=styles["Heading1"],
            fontSize=18,
            textColor=NAVY_BLUE,
            alignment=0,
            spaceAfter=15,
            fontName="Helvetica-Bold",
        )
        self.heading2_style = ParagraphStyle(
            "RptH2",
            parent=styles["Heading2"],
            fontSize=12,
            textColor=NAVY_BLUE,
            spaceBefore=6,
            spaceAfter=10,
            fontName="Helvetica-Bold",
        )
        self.normal_style = ParagraphStyle(
            "RptNormal",
            fontSize=9,
            textColor=colors.black,
            fontName="Helvetica",
            leading=11,
        )
        self.bold_style = ParagraphStyle(
            "RptBold",
            fontSize=9,
            textColor=colors.black,
            fontName="Helvetica-Bold",
            leading=11,
        )
        self.small_style = ParagraphStyle(
            "RptSmall",
            fontSize=7.5,
            textColor=colors.black,
            fontName="Helvetica",
            leading=9,
        )

        self._add_header(title, subtitle)

    # ---------------------------------------------------------------
    def _add_header(self, title: str, subtitle: Optional[str]):
        if os.path.exists(LOGO_PATH):
            logo_img = Image(LOGO_PATH, width=2 * inch, height=0.75 * inch)
        else:
            logo_img = Paragraph("<b>INFRA PILOT</b>", self.title_style)

        title_para = Paragraph(f"<b>{title}</b>", self.title_style)
        header_data = [[logo_img, title_para]]
        header_table = Table(
            header_data, colWidths=[2.5 * inch, self.usable_width - 2.5 * inch]
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        self.elements.append(header_table)
        if subtitle:
            self.elements.append(Paragraph(subtitle, self.normal_style))
        self.elements.append(Spacer(1, 0.15 * inch))

    def _next_no(self) -> int:
        self._section_no += 1
        return self._section_no

    # ---------------------------------------------------------------
    def add_info_table(
        self, rows: Sequence[tuple], heading: str = "REPORT INFORMATION"
    ):
        """rows: list of 4-tuples (label, value, label, value)."""
        no = self._next_no()
        data = []
        for label1, value1, label2, value2 in rows:
            data.append(
                [
                    Paragraph(f"<b>{label1}</b>", self.bold_style),
                    str(value1),
                    Paragraph(f"<b>{label2}</b>", self.bold_style),
                    str(value2),
                ]
            )
        col_w = self.usable_width / 6
        table = Table(data, colWidths=[col_w, 2 * col_w, col_w, 2 * col_w])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), LIGHT_GRAY),
                    ("BACKGROUND", (2, 0), (2, -1), LIGHT_GRAY),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        self.elements.append(Paragraph(f"{no}. {heading}", self.heading2_style))
        self.elements.append(table)
        self.elements.append(Spacer(1, 0.2 * inch))
        return self

    def add_summary_box(self, heading: str, lines: Sequence[str]):
        """lines: list of HTML strings (already containing <b> tags as needed)."""
        no = self._next_no()
        data = [
            [Paragraph(line, self.bold_style if i == 0 else self.normal_style)]
            for i, line in enumerate(lines)
        ]
        box = Table(data, colWidths=[self.usable_width])
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
                    ("BOX", (0, 0), (-1, -1), 1, NAVY_BLUE),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        self.elements.append(Paragraph(f"{no}. {heading}", self.heading2_style))
        self.elements.append(box)
        self.elements.append(Spacer(1, 0.2 * inch))
        return self

    def add_section_table(
        self,
        heading: str,
        headers: Sequence[str],
        rows: Sequence[Sequence],
        col_widths: Optional[Sequence[float]] = None,
        empty_text: str = "No records found.",
        total_row: Optional[Sequence] = None,
    ):
        no = self._next_no()
        self.elements.append(Paragraph(f"{no}. {heading}", self.heading2_style))
        if not rows:
            self.elements.append(Paragraph(empty_text, self.normal_style))
            self.elements.append(Spacer(1, 0.2 * inch))
            return self

        if col_widths is None:
            col_widths = [self.usable_width / len(headers)] * len(headers)

        header_row = [Paragraph(f"<b>{h}</b>", self.small_style) for h in headers]
        data_rows = [[Paragraph(str(v), self.small_style) for v in row] for row in rows]
        table_data = [header_row] + data_rows

        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, len(data_rows)),
                [colors.white, LIGHT_GRAY],
            ),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]

        if total_row is not None:
            total_row_p = [
                Paragraph(f"<b>{v}</b>", self.small_style) for v in total_row
            ]
            table_data.append(total_row_p)
            total_idx = len(table_data) - 1
            style_cmds.append(
                ("BACKGROUND", (0, total_idx), (-1, total_idx), LIGHT_GRAY)
            )
            style_cmds.append(
                ("LINEABOVE", (0, total_idx), (-1, total_idx), 1, NAVY_BLUE)
            )

        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle(style_cmds))
        self.elements.append(table)
        self.elements.append(Spacer(1, 0.2 * inch))
        return self

    def add_paragraph(self, text: str, bold: bool = False):
        self.elements.append(
            Paragraph(text, self.bold_style if bold else self.normal_style)
        )
        return self

    def add_spacer(self, height: float = 0.15):
        self.elements.append(Spacer(1, height * inch))
        return self

    # ---------------------------------------------------------------
    def build(
        self,
        footer_note: str = "Generated by InfraPilot Construction Management System",
    ):
        page_w = self.page_width

        def add_footer(canvas, doc_):
            canvas.saveState()
            canvas.setFont("Helvetica", 8)
            canvas.setStrokeColor(NAVY_BLUE)
            canvas.setLineWidth(1)
            canvas.line(cm, 1.5 * cm, page_w - cm, 1.5 * cm)

            canvas.drawString(cm, 1.2 * cm, "Prepared By: ______________")
            canvas.drawString(
                page_w / 2 - 1.5 * cm, 1.2 * cm, "Reviewed By: ______________"
            )
            canvas.drawString(page_w - 5 * cm, 1.2 * cm, "Approved By: ______________")

            canvas.drawString(cm, 0.8 * cm, footer_note)
            canvas.drawRightString(page_w - cm, 0.8 * cm, f"Page {doc_.page}")
            canvas.restoreState()

        self.doc.build(self.elements, onFirstPage=add_footer, onLaterPages=add_footer)
        self.stream.seek(0)
        return self.stream


# ═══════════════════════════ EXCEL HELPERS ═══════════════════════════════════
class ExcelReportBuilder:
    """
    Usage:
        b = ExcelReportBuilder("Audit Summary Report", project_line="All Projects")
        b.add_summary_sheet([("Total Logs", 128), ("Period", "Jul 2026")])
        b.add_data_sheet("Logs", headers, rows, currency_cols=[5])
        xlsx_bytes_io = b.build()
    """

    def __init__(self, title: str, project_line: Optional[str] = None):
        self.title = title
        self.project_line = project_line
        self.wb = Workbook()
        self.wb.remove(self.wb.active)

        self.header_font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        self.header_fill = PatternFill("solid", fgColor=NAVY_HEX)
        self.title_font = Font(name="Arial", bold=True, size=14, color=NAVY_HEX)
        self.subtitle_font = Font(name="Arial", italic=True, size=9, color="6B7280")
        self.label_font = Font(name="Arial", bold=True, size=10, color=NAVY_HEX)
        self.cell_font = Font(name="Arial", size=10)
        thin = Side(style="thin", color=BORDER_GRAY_HEX)
        self.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        self.center = Alignment(horizontal="center", vertical="center")
        self.accent_fill = PatternFill("solid", fgColor=LIGHT_GRAY_HEX)
        self.currency_fmt = '"Rs." #,##0.00'

        self._summary_rows = []

    def add_summary_row(self, label: str, value, is_currency: bool = False):
        self._summary_rows.append((label, value, is_currency))
        return self

    def build_summary_sheet(self):
        ws = self.wb.create_sheet("Summary", 0)
        ws.merge_cells("A1:B1")
        ws["A1"] = self.title.upper()
        ws["A1"].font = self.title_font
        row = 2
        if self.project_line:
            ws.merge_cells(f"A{row}:B{row}")
            ws[f"A{row}"] = self.project_line
            ws[f"A{row}"].font = self.subtitle_font
            row += 1
        ws.merge_cells(f"A{row}:B{row}")
        ws[f"A{row}"] = f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}"
        ws[f"A{row}"].font = self.subtitle_font
        row += 2

        for label, value, is_currency in self._summary_rows:
            label_cell = ws.cell(row=row, column=1, value=label)
            label_cell.font = self.label_font
            label_cell.fill = self.accent_fill
            value_cell = ws.cell(row=row, column=2, value=value)
            value_cell.font = self.cell_font
            if is_currency:
                value_cell.number_format = self.currency_fmt
            row += 1

        row += 2
        ws.cell(row=row, column=1, value="Prepared By: ______________").font = (
            self.cell_font
        )
        row += 1
        ws.cell(row=row, column=1, value="Reviewed By: ______________").font = (
            self.cell_font
        )
        row += 1
        ws.cell(row=row, column=1, value="Approved By: ______________").font = (
            self.cell_font
        )

        ws.column_dimensions["A"].width = 38
        ws.column_dimensions["B"].width = 24
        return self

    def add_data_sheet(
        self,
        sheet_name: str,
        headers: Sequence[str],
        rows: Sequence[Sequence],
        currency_cols: Optional[Sequence[int]] = None,
        title: Optional[str] = None,
    ):
        currency_cols = currency_cols or []
        ws = self.wb.create_sheet(sheet_name[:31])  # Excel sheet name limit
        start_row = 1

        if title:
            ws.merge_cells(
                start_row=1, start_column=1, end_row=1, end_column=max(len(headers), 1)
            )
            ws.cell(row=1, column=1, value=title).font = self.title_font
            start_row = 3

        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=start_row, column=col, value=header)
            cell.font = self.header_font
            cell.fill = self.header_fill
            cell.alignment = self.center
            cell.border = self.border

        for r, row_values in enumerate(rows, start_row + 1):
            fill = self.accent_fill if (r - start_row) % 2 == 0 else None
            for col, val in enumerate(row_values, 1):
                cell = ws.cell(row=r, column=col, value=val)
                cell.font = self.cell_font
                cell.alignment = self.center
                cell.border = self.border
                if fill:
                    cell.fill = fill
                if col in currency_cols:
                    cell.number_format = self.currency_fmt

        for col, header in enumerate(headers, 1):
            max_len = len(str(header))
            for row_values in rows:
                val = row_values[col - 1]
                max_len = max(max_len, len(str(val)))
            ws.column_dimensions[get_column_letter(col)].width = min(
                max(max_len + 4, 12), 40
            )

        ws.freeze_panes = f"A{start_row + 1}"
        return self

    def build(self):
        import io

        if "Summary" not in self.wb.sheetnames:
            self.build_summary_sheet()
        output = io.BytesIO()
        self.wb.save(output)
        output.seek(0)
        return output


# ======================================================================
# SECTION 2: REPORT API ENDPOINTS
# ======================================================================

from collections import defaultdict
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Optional
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, case, extract
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.dependencies import require_permission
from app.core.enums import (
    InvoiceStatus,
    IssueStatus,
    IssuePriority,
    LabourStatus,
    ProjectStatus,
    TaskStatus,
)
from app.db.session import get_db_session
from app.models import project as m
from app.models.accountant import FixedAsset
from app.models.expense import Expense
from app.models.invoice import Invoice, Transaction
from app.models.master_data import LabourType, MaterialMaster
from app.models.material import Material
from app.models.user import User, UserRole, UserAttendance, ActivityLog
from app.utils.common import assert_project_access
from app.utils.helpers import NotFoundError
from app.schemas.report import ProjectFinancialHealthReportDTO


from app.core.dependencies import require_feature


router = APIRouter(
    prefix="/reports",
    tags=["Reports"],
    dependencies=[Depends(require_feature("advanced_reports", "Reports & Analytics"))],
)


def _check_tenant_access(current_user: User) -> bool:
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=403, detail="Company context required")
    return is_sa


async def _validate_project_access_404(db: AsyncSession, project_id: int, current_user: User) -> m.Project:
    is_sa = getattr(current_user, "is_super_admin", False) is True
    project = await db.get(m.Project, project_id)
    if not project:
        raise NotFoundError("Project not found")
    if not is_sa and project.company_id != current_user.company_id:
        raise NotFoundError("Project not found")
    return project


# ===================== PROJECT REPORTS =====================


@router.get("/projects/excel")
async def export_projects_excel(
    project_id: Optional[int] = Query(
        None,
        description="Project ID to filter. If none, exports all projects.",
    ),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    from app.api.project import get_reports_service

    service = get_reports_service()

    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
        return await service.export_excel(
            db=db,
            project_id=project_id,
            current_user=current_user,
        )

    # Company Portfolio Report
    if current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="Super Admin cannot export company portfolio directly",
        )

    projects_query = (
        select(m.Project)
        .where(m.Project.company_id == current_user.company_id)
        .order_by(m.Project.id.desc())
    )

    projects = (await db.execute(projects_query)).scalars().all()

    total_projects = len(projects)
    ongoing = sum(
        1 for p in projects if str(getattr(p.status, "value", p.status)) == "Ongoing"
    )
    completed = sum(
        1 for p in projects if str(getattr(p.status, "value", p.status)) == "Completed"
    )

    eb = ExcelReportBuilder("Company Portfolio Overview")
    eb.add_summary_row("Total Projects", total_projects)
    eb.add_summary_row("Ongoing", ongoing)
    eb.add_summary_row("Completed", completed)
    eb.build_summary_sheet()

    headers = ["Business ID", "Project Name", "Status", "Start Date", "End Date"]
    rows = [
        [
            p.business_id,
            (
                p.project_name[:30] + "..."
                if p.project_name and len(p.project_name) > 30
                else (p.project_name or "N/A")
            ),
            (p.status.value if hasattr(p.status, "value") else str(p.status)),
            str(p.start_date) if p.start_date else "N/A",
            str(p.end_date) if p.end_date else "N/A",
        ]
        for p in projects
    ]
    eb.add_data_sheet("Portfolio", headers, rows, title="Company Portfolio")

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": ("attachment; filename=all_projects_report.xlsx")
        },
    )


@router.get("/projects/pdf")
async def export_projects_pdf(
    project_id: Optional[int] = Query(
        None, description="Project ID to filter. If none, exports all projects."
    ),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    from app.api.project import get_reports_service

    service = get_reports_service()

    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
        return await service.export_pdf(db, project_id, current_user)

    # Export ALL projects
    if current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="Super Admin cannot export company portfolio directly",
        )

    projects_query = (
        select(m.Project)
        .where(m.Project.company_id == current_user.company_id)
        .order_by(m.Project.id.desc())
    )
    projects = (await db.execute(projects_query)).scalars().all()

    total_projects = len(projects)
    ongoing = sum(
        1 for p in projects if str(getattr(p.status, "value", p.status)) == "Ongoing"
    )
    completed = sum(
        1 for p in projects if str(getattr(p.status, "value", p.status)) == "Completed"
    )

    b = PdfReportBuilder("COMPANY PORTFOLIO OVERVIEW")
    b.add_info_table(
        [
            ("Generated On", str(date.today()), "Report Type", "Company Portfolio"),
        ]
    )
    b.add_summary_box(
        "SUMMARY",
        [
            f"<b>Total Projects:</b> {total_projects} | "
            f"<b>Ongoing:</b> {ongoing} | <b>Completed:</b> {completed}",
        ],
    )

    headers = ["ID", "Name", "Status", "Start Date", "End Date"]
    rows = [
        [
            p.business_id,
            (
                p.project_name[:30] + "..."
                if p.project_name and len(p.project_name) > 30
                else (p.project_name or "N/A")
            ),
            str(getattr(p.status, "value", p.status)),
            str(p.start_date) if p.start_date else "N/A",
            str(p.end_date) if p.end_date else "N/A",
        ]
        for p in projects
    ]
    b.add_section_table("PROJECT LIST", headers, rows)

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=all_projects_portfolio.pdf"
        },
    )


@router.get("/audit/excel")
async def export_audit_excel(
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    module: Optional[str] = Query(None, description="Filter by entity/module"),
    action: Optional[str] = Query(
        None, description="Filter by action (CREATE, UPDATE, DELETE)"
    ),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    query = select(ActivityLog, User).join(
        User, ActivityLog.performed_by == User.id
    )

    if not is_sa:
        query = query.where(User.company_id == current_user.company_id)

    if user_id:
        target_user = await db.get(User, user_id)
        if not target_user or (not is_sa and target_user.company_id != current_user.company_id):
            raise NotFoundError("User not found")
        query = query.where(ActivityLog.performed_by == user_id)

    if start_date:
        query = query.where(ActivityLog.created_at >= start_date)
    if end_date:
        query = query.where(ActivityLog.created_at <= end_date + timedelta(days=1))
    if module:
        query = query.where(ActivityLog.entity == module)
    if action:
        query = query.where(ActivityLog.action == action)

    query = query.order_by(ActivityLog.created_at.desc())
    result = await db.execute(query)
    logs = result.all()

    eb = ExcelReportBuilder("System Audit Summary Report")
    eb.add_summary_row("Total Logs", len(logs))
    if start_date or end_date:
        eb.add_summary_row("Period", f"{start_date or 'Start'} to {end_date or 'End'}")
    if module:
        eb.add_summary_row("Module", module)
    if action:
        eb.add_summary_row("Action", action)
    eb.build_summary_sheet()

    headers = ["Log ID", "Timestamp", "User", "Role", "Action", "Entity", "Details"]
    rows = []
    for log, usr in logs:
        details_val = (
            json.dumps(log.details)
            if isinstance(log.details, (dict, list))
            else (str(log.details) if log.details is not None else "")
        )
        rows.append(
            [
                log.id,
                log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else "N/A",
                getattr(usr, "full_name", None) or getattr(usr, "username", None) or getattr(usr, "email", "System") if usr else "System",
                usr.role.value if usr and hasattr(usr.role, "value") else (usr.role if usr else "N/A"),
                log.action,
                log.entity,
                details_val,
            ]
        )
    eb.add_data_sheet("Audit Logs", headers, rows, title="Audit Trail")

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=audit_summary_report.xlsx"},
    )


@router.get("/procurement-efficiency")
async def procurement_efficiency_report(
    project_id: int = Query(..., description="Project ID (required)"),
    supplier_id: Optional[int] = Query(None, description="Filter by supplier ID"),
    status: Optional[str] = Query(None, description="VendorBill status filter"),
    payment_status: Optional[str] = Query(None, description="PAID|PARTIAL|UNPAID"),
    date_from: Optional[date] = Query(None, description="Start date filter"),
    date_to: Optional[date] = Query(None, description="End date filter"),
    search: Optional[str] = Query(None, description="Search term for bill number or supplier name"),
    format: str = Query("json", description="Response format: json|pdf|csv"),
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    """Generate Procurement Efficiency Report."""
    is_sa = _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    if supplier_id:
        from app.models.material import Supplier
        supp = await db.get(Supplier, supplier_id)
        if not supp or (not is_sa and supp.company_id != current_user.company_id):
            raise NotFoundError("Supplier not found")

    filters: Dict[str, Any] = {
        "project_id": project_id,
        "supplier_id": supplier_id,
        "status": status,
        "payment_status": payment_status,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "search": search,
    }
    filters = {k: v for k, v in filters.items() if v is not None}
    from app.services.report_service import ReportService
    try:
        report_dto = await ReportService.get_procurement_efficiency_report(db, filters)
    except Exception as e:
        if str(e) == "Report data missing":
            raise HTTPException(status_code=404, detail="Project not found")
        raise
    if format.lower() == "json":
        return report_dto
    if format.lower() == "pdf":
        b = PdfReportBuilder("Procurement Efficiency Report")
        b.add_info_table([
            (
                "Project", report_dto.summary.project_name,
                "Budget", str(report_dto.summary.budget_amount)
            ),
            (
                "Total Spend", str(report_dto.summary.total_spend),
                "Budget Utilisation", report_dto.summary.budget_vs_actual
            ),
        ])
        b.add_section_table(
            "Totals",
            ["Metric", "Amount"],
            [
                ["Total Spend", str(report_dto.procurement.total_spend)],
                ["Total Paid", str(report_dto.procurement.total_paid)],
                ["Total Pending", str(report_dto.procurement.total_pending)],
                ["Bill Count", str(report_dto.procurement.bill_count)],
            ],
            [3.5 * inch, 3.5 * inch],
        )
        pdf_bytes = b.build()
        return StreamingResponse(
            pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=procurement_efficiency_{project_id}.pdf"},
        )
    if format.lower() == "csv":
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Bill Number", "Supplier", "Invoice Date", "Due Date", "Total Amount", "Status", "Payment Status"])
        for b_item in report_dto.bills:
            writer.writerow([
                b_item.bill_number,
                b_item.supplier_name,
                b_item.invoice_date,
                b_item.due_date,
                b_item.total_amount,
                b_item.status,
                b_item.payment_status,
            ])
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=procurement_efficiency_{project_id}.csv"},
        )
    raise HTTPException(status_code=400, detail="Invalid format. Supported: json, pdf, csv")


@router.get(
    "/project-financial-health",
    response_model=ProjectFinancialHealthReportDTO,
    responses={
        200: {
            "content": {
                "application/json": {},
                "application/pdf": {},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "text/csv": {},
            }
        }
    },
)
@router.get(
    "/financial-health",
    response_model=ProjectFinancialHealthReportDTO,
    include_in_schema=False,
)
async def project_financial_health_report(
    project_id: int = Query(..., description="Project ID (required)"),
    date_from: Optional[date] = Query(None, description="Start date filter"),
    date_to: Optional[date] = Query(None, description="End date filter"),
    format: str = Query("json", description="Export format: json, pdf, excel, xlsx, csv"),
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    """Generate comprehensive Project Financial Health Report."""
    is_sa = _check_tenant_access(current_user)
    # 1. Date range validation
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=400,
            detail="date_from cannot be after date_to",
        )

    # 2. Format validation
    fmt = format.lower().strip()
    if fmt not in ("json", "pdf", "excel", "xlsx", "csv"):
        raise HTTPException(
            status_code=400,
            detail="Invalid format. Supported: json, pdf, excel, xlsx, csv",
        )

    # 3. Project lookup & Tenant Isolation / Project Access Check
    project = await _validate_project_access_404(db, project_id, current_user)

    # 4. Generate report via ReportService
    from app.services.report_service import ReportService

    filters = {
        "project_id": project_id,
        "date_from": date_from,
        "date_to": date_to,
    }
    report_dto = await ReportService.get_project_financial_health_report(db, filters)

    # 5. Format responses
    if fmt == "json":
        return report_dto

    if fmt == "pdf":
        b = PdfReportBuilder(
            title="PROJECT FINANCIAL HEALTH REPORT",
            subtitle=f"Project: {report_dto.summary.project_name} (ID: {project_id})",
        )
        b.add_info_table(
            [
                (
                    "Project Name", report_dto.summary.project_name,
                    "Project ID", str(report_dto.summary.project_id),
                ),
                (
                    "Budget Amount", f"Rs. {report_dto.summary.budget_amount:,.2f}",
                    "Financial Status", report_dto.summary.financial_health_status,
                ),
            ]
        )
        b.add_summary_box(
            "FINANCIAL HEALTH EXECUTIVE SUMMARY",
            [
                f"<b>Total Certified Revenue:</b> Rs. {report_dto.cashflow.total_certified_revenue:,.2f}",
                f"<b>Total Incurred Cost:</b> Rs. {report_dto.cashflow.total_incurred_cost:,.2f}",
                f"<b>Net Cashflow:</b> Rs. {report_dto.cashflow.net_cashflow:,.2f}",
                f"<b>Cost Variance (CV):</b> Rs. {report_dto.earned_value.cost_variance:,.2f} ({report_dto.earned_value.cost_performance_status})",
                f"<b>Schedule Variance (SV):</b> Rs. {report_dto.earned_value.schedule_variance:,.2f} ({report_dto.earned_value.schedule_performance_status})",
                f"<b>CPI:</b> {report_dto.earned_value.cpi:.2f} | <b>SPI:</b> {report_dto.earned_value.spi:.2f}",
            ],
        )

        headers = ["Contractor", "Total Certified", "Total Retained", "Net Payable", "Total Paid", "Pending"]
        rows = [
            [
                c.contractor_name,
                f"Rs. {c.total_certified:,.2f}",
                f"Rs. {c.total_retained:,.2f}",
                f"Rs. {c.net_payable:,.2f}",
                f"Rs. {c.total_paid:,.2f}",
                f"Rs. {c.pending_amount:,.2f}",
            ]
            for c in report_dto.contractor_commitments
        ]
        b.add_section_table("CONTRACTOR LIABILITIES & COMMITMENTS", headers, rows)

        stream = b.build()
        return StreamingResponse(
            stream,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=project_{project_id}_financial_health.pdf"},
        )

    if fmt in ("excel", "xlsx"):
        eb = ExcelReportBuilder("Project Financial Health Report")
        eb.add_summary_row("Project Name", report_dto.summary.project_name)
        eb.add_summary_row("Project ID", report_dto.summary.project_id)
        eb.add_summary_row("Budget Amount", report_dto.summary.budget_amount, is_currency=True)
        eb.add_summary_row("Financial Status", report_dto.summary.financial_health_status)
        eb.add_summary_row("Total Revenue", report_dto.cashflow.total_certified_revenue, is_currency=True)
        eb.add_summary_row("Total Incurred Cost", report_dto.cashflow.total_incurred_cost, is_currency=True)
        eb.add_summary_row("Net Cashflow", report_dto.cashflow.net_cashflow, is_currency=True)
        eb.add_summary_row("CPI", report_dto.earned_value.cpi)
        eb.add_summary_row("SPI", report_dto.earned_value.spi)
        eb.build_summary_sheet()

        headers = ["Contractor Name", "Total Certified", "Total Retained", "Net Payable", "Total Paid", "Pending"]
        rows = [
            [
                c.contractor_name,
                c.total_certified,
                c.total_retained,
                c.net_payable,
                c.total_paid,
                c.pending_amount,
            ]
            for c in report_dto.contractor_commitments
        ]
        eb.add_data_sheet("Contractor Commitments", headers, rows, currency_cols=[1, 2, 3, 4, 5])
        stream = eb.build()
        return StreamingResponse(
            stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=project_{project_id}_financial_health.xlsx"},
        )

    if fmt == "csv":
        import io, csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Project ID", "Project Name", "Budget", "Total Revenue", "Total Incurred Cost", "Net Cashflow", "CPI", "SPI", "Financial Health"])
        writer.writerow([
            report_dto.summary.project_id,
            report_dto.summary.project_name,
            report_dto.summary.budget_amount,
            report_dto.cashflow.total_certified_revenue,
            report_dto.cashflow.total_incurred_cost,
            report_dto.cashflow.net_cashflow,
            report_dto.earned_value.cpi,
            report_dto.earned_value.spi,
            report_dto.summary.financial_health_status,
        ])
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=project_{project_id}_financial_health.csv"},
        )


@router.get("/audit/pdf")
async def export_audit_pdf(
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    module: Optional[str] = Query(None, description="Filter by entity/module"),
    action: Optional[str] = Query(None, description="Filter by action"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    query = select(ActivityLog, User).join(
        User, ActivityLog.performed_by == User.id
    )

    if not is_sa:
        query = query.where(User.company_id == current_user.company_id)

    if user_id:
        target_user = await db.get(User, user_id)
        if not target_user or (not is_sa and target_user.company_id != current_user.company_id):
            raise NotFoundError("User not found")
        query = query.where(ActivityLog.performed_by == user_id)

    if start_date:
        query = query.where(ActivityLog.created_at >= start_date)
    if end_date:
        query = query.where(ActivityLog.created_at <= end_date + timedelta(days=1))
    if module:
        query = query.where(ActivityLog.entity == module)
    if action:
        query = query.where(ActivityLog.action == action)

    query = query.order_by(ActivityLog.created_at.desc())
    result = await db.execute(query)
    logs = result.all()

    b = PdfReportBuilder("SYSTEM AUDIT TRAIL REPORT", landscape_mode=True)
    b.add_info_table(
        [
            ("Generated On", str(date.today()), "Total Records", str(len(logs))),
            ("Module Filter", module or "All Modules", "Action Filter", action or "All Actions"),
        ]
    )

    headers = ["ID", "Timestamp", "User", "Role", "Action", "Entity", "Details"]
    rows = []
    for log, usr in logs:
        details_val = (
            json.dumps(log.details)
            if isinstance(log.details, (dict, list))
            else (str(log.details) if log.details is not None else "N/A")
        )
        rows.append(
            [
                str(log.id),
                log.created_at.strftime("%Y-%m-%d %H:%M") if log.created_at else "N/A",
                getattr(usr, "full_name", None) or getattr(usr, "username", None) or getattr(usr, "email", "System") if usr else "System",
                usr.role.value if usr and hasattr(usr.role, "value") else (usr.role if usr else "N/A"),
                log.action,
                log.entity,
                (details_val[:40] + "...") if len(details_val) > 40 else details_val,
            ]
        )

    b.add_section_table(
        "AUDIT LOGS",
        headers,
        rows,
        col_widths=[0.6 * inch, 1.4 * inch, 1.4 * inch, 1.1 * inch, 1.0 * inch, 1.1 * inch, 3.0 * inch],
    )

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=audit_summary_report.pdf"},
    )


@router.get("/assets/excel")
async def export_assets_excel(
    project_id: Optional[int] = Query(
        None, description="Filter by allocated project ID"
    ),
    start_date: Optional[date] = Query(None, description="Purchase start date"),
    end_date: Optional[date] = Query(None, description="Purchase end date"),
    min_value: Optional[float] = Query(None, description="Minimum current value"),
    max_value: Optional[float] = Query(None, description="Maximum current value"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    query = select(FixedAsset, m.Project).join(
        m.Project, FixedAsset.project_id == m.Project.id
    )

    if not is_sa:
        query = query.where(m.Project.company_id == current_user.company_id)

    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
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

    headers = [
        "Asset ID",
        "Asset Name",
        "Allocated Project",
        "Purchase Date",
        "Purchase Value",
        "Depreciation Rate (%)",
        "Current Book Value",
    ]

    rows = []
    total_purchase = 0.0
    total_current = 0.0

    for asset, proj in assets:
        p_val = float(asset.purchase_value or 0)
        c_val = float(asset.current_value or 0)
        total_purchase += p_val
        total_current += c_val

        rows.append(
            [
                asset.id,
                asset.name,
                proj.project_name if proj else "Unallocated",
                str(asset.purchase_date) if asset.purchase_date else "N/A",
                p_val,
                float(asset.depreciation_rate or 0),
                c_val,
            ]
        )

    eb = ExcelReportBuilder("Fixed Asset Management Report")
    eb.add_summary_row("Total Assets Tracked", len(assets))
    eb.add_summary_row("Total Initial Investment", round(total_purchase, 2), is_currency=True)
    eb.add_summary_row("Total Current Book Value", round(total_current, 2), is_currency=True)
    eb.build_summary_sheet()
    eb.add_data_sheet("Fixed Assets", headers, rows, currency_cols=[4, 6])

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=fixed_assets_report.xlsx"},
    )


@router.get("/assets/pdf")
async def export_assets_pdf(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    start_date: Optional[date] = Query(None, description="Purchase start date"),
    end_date: Optional[date] = Query(None, description="Purchase end date"),
    min_value: Optional[float] = Query(None, description="Minimum current value"),
    max_value: Optional[float] = Query(None, description="Maximum current value"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    query = select(FixedAsset, m.Project).join(
        m.Project, FixedAsset.project_id == m.Project.id
    )

    if not is_sa:
        query = query.where(m.Project.company_id == current_user.company_id)

    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
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

    total_purchase = sum(float(a.purchase_value or 0) for a, _ in assets)
    total_current = sum(float(a.current_value or 0) for a, _ in assets)

    b = PdfReportBuilder("FIXED ASSET INVENTORY REPORT", landscape_mode=True)
    b.add_info_table(
        [
            ("Generated On", str(date.today()), "Total Assets", str(len(assets))),
            (
                "Initial Value",
                f"Rs. {total_purchase:,.2f}",
                "Current Book Value",
                f"Rs. {total_current:,.2f}",
            ),
        ]
    )

    headers = [
        "ID",
        "Asset Name",
        "Allocated Project",
        "Purchase Date",
        "Dep. Rate",
        "Original Value",
        "Current Value",
    ]
    rows = [
        [
            str(a.id),
            a.name,
            p.project_name if p else "Unallocated",
            str(a.purchase_date) if a.purchase_date else "N/A",
            f"{float(a.depreciation_rate or 0)}%",
            f"Rs. {float(a.purchase_value or 0):,.2f}",
            f"Rs. {float(a.current_value or 0):,.2f}",
        ]
        for a, p in assets
    ]

    b.add_section_table(
        "ASSET INVENTORY",
        headers,
        rows,
        col_widths=[0.6 * inch, 2.2 * inch, 2.0 * inch, 1.2 * inch, 1.0 * inch, 1.4 * inch, 1.4 * inch],
    )

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=fixed_assets_report.pdf"},
    )


@router.get("/issues/excel")
async def export_issues_excel(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    status: Optional[str] = Query(
        None, description="Filter by status (e.g., OPEN, RESOLVED)"
    ),
    priority: Optional[str] = Query(
        None, description="Filter by priority (e.g., HIGH, LOW)"
    ),
    start_date: Optional[date] = Query(None, description="Reported start date"),
    end_date: Optional[date] = Query(None, description="Reported end date"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    query = (
        select(m.Issue, m.Project, User)
        .join(m.Project, m.Issue.project_id == m.Project.id)
        .outerjoin(User, m.Issue.assigned_to == User.id)
    )

    if not is_sa:
        query = query.where(m.Project.company_id == current_user.company_id)

    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
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

    headers = [
        "Issue ID",
        "Title",
        "Project",
        "Category",
        "Priority",
        "Status",
        "Assigned To",
        "Reported Date",
        "Description",
    ]
    rows = [
        [
            issue.business_id or str(issue.id),
            issue.title,
            proj.project_name if proj else "N/A",
            str(getattr(issue.category, "value", issue.category)),
            str(getattr(issue.priority, "value", issue.priority)),
            str(getattr(issue.status, "value", issue.status)),
            usr.full_name or usr.username if usr else "Unassigned",
            str(issue.reported_date) if issue.reported_date else "N/A",
            issue.description or "",
        ]
        for issue, proj, usr in issues
    ]

    eb = ExcelReportBuilder("Project Issues & Risk Report")
    eb.add_summary_row("Total Issues", len(rows))
    open_count = sum(1 for i, _, _ in issues if str(getattr(i.status, "value", i.status)) == "OPEN")
    resolved_count = sum(1 for i, _, _ in issues if str(getattr(i.status, "value", i.status)) == "RESOLVED")
    eb.add_summary_row("Open Issues", open_count)
    eb.add_summary_row("Resolved Issues", resolved_count)
    eb.build_summary_sheet()
    eb.add_data_sheet("Issues", headers, rows)

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=project_issues_report.xlsx"},
    )


@router.get("/issues/pdf")
async def export_issues_pdf(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    start_date: Optional[date] = Query(None, description="Reported start date"),
    end_date: Optional[date] = Query(None, description="Reported end date"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    query = (
        select(m.Issue, m.Project, User)
        .join(m.Project, m.Issue.project_id == m.Project.id)
        .outerjoin(User, m.Issue.assigned_to == User.id)
    )

    if not is_sa:
        query = query.where(m.Project.company_id == current_user.company_id)

    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
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

    b = PdfReportBuilder("PROJECT ISSUES & RISKS SUMMARY", landscape_mode=True)
    b.add_info_table(
        [
            ("Generated On", str(date.today()), "Total Issues", str(len(issues))),
            ("Status Filter", status or "All Statuses", "Priority Filter", priority or "All Priorities"),
        ]
    )

    headers = ["ID", "Title", "Project", "Category", "Priority", "Status", "Assigned To", "Reported"]
    rows = [
        [
            issue.business_id or str(issue.id),
            (issue.title[:25] + "...") if issue.title and len(issue.title) > 25 else (issue.title or "N/A"),
            (proj.project_name[:20] + "...") if proj and len(proj.project_name) > 20 else (proj.project_name if proj else "N/A"),
            str(getattr(issue.category, "value", issue.category)),
            str(getattr(issue.priority, "value", issue.priority)),
            str(getattr(issue.status, "value", issue.status)),
            usr.full_name or usr.username if usr else "Unassigned",
            str(issue.reported_date) if issue.reported_date else "N/A",
        ]
        for issue, proj, usr in issues
    ]

    b.add_section_table(
        "ISSUES LOG",
        headers,
        rows,
        col_widths=[1.0 * inch, 2.0 * inch, 1.8 * inch, 1.2 * inch, 1.0 * inch, 1.0 * inch, 1.2 * inch, 1.0 * inch],
    )

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=project_issues_report.pdf"},
    )


@router.get("/finance/excel")
async def export_finance_excel(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    start_date: Optional[date] = Query(
        None, description="Start date for financial period"
    ),
    end_date: Optional[date] = Query(None, description="End date for financial period"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    # 1. Fetch Projects
    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
        proj_query = select(m.Project).where(m.Project.id == project_id)
    else:
        if not is_sa:
            proj_query = select(m.Project).where(m.Project.company_id == current_user.company_id)
        else:
            proj_query = select(m.Project)
            if current_user.company_id is not None:
                proj_query = proj_query.where(m.Project.company_id == current_user.company_id)

    projects = (await db.execute(proj_query)).scalars().all()
    project_map = {p.id: p.project_name for p in projects}
    project_ids = list(project_map.keys())

    # 2. Fetch Expenses (grouped by project and category)
    exp_query = select(
        Expense.project_id, Expense.category, func.sum(Expense.amount)
    ).group_by(Expense.project_id, Expense.category)

    if project_ids:
        exp_query = exp_query.where(Expense.project_id.in_(project_ids))
    else:
        exp_query = exp_query.where(Expense.project_id == -1)

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
    inv_query = select(
        Invoice.project_id,
        Invoice.status,
        func.sum(Invoice.total_amount),
        func.sum(Invoice.paid_amount),
    ).group_by(Invoice.project_id, Invoice.status)

    if project_ids:
        inv_query = inv_query.where(Invoice.project_id.in_(project_ids))
    else:
        inv_query = inv_query.where(Invoice.project_id == -1)

    if start_date:
        inv_query = inv_query.where(Invoice.created_at >= start_date)
    if end_date:
        inv_query = inv_query.where(Invoice.created_at <= end_date + timedelta(days=1))

    inv_result = await db.execute(inv_query)

    project_invoices = defaultdict(lambda: {"total": 0.0, "paid": 0.0})
    for pid, status_val, total, paid in inv_result.all():
        if pid in project_map:
            project_invoices[pid]["total"] += float(total or 0)
            project_invoices[pid]["paid"] += float(paid or 0)

    eb = ExcelReportBuilder("Project Financials Summary Report")

    # Sheet 1: Project Level Overview
    overview_headers = [
        "Project ID",
        "Project Name",
        "Total Invoiced",
        "Total Collected",
        "Total Expenses",
        "Net Margin",
    ]
    overview_rows = []
    grand_invoiced = 0.0
    grand_collected = 0.0
    grand_expenses = 0.0

    for pid, pname in project_map.items():
        inv_tot = project_invoices[pid]["total"]
        inv_paid = project_invoices[pid]["paid"]
        exp_tot = sum(project_expenses[pid].values())

        grand_invoiced += inv_tot
        grand_collected += inv_paid
        grand_expenses += exp_tot

        overview_rows.append(
            [
                pid,
                pname,
                inv_tot,
                inv_paid,
                exp_tot,
                inv_tot - exp_tot,
            ]
        )

    eb.add_summary_row("Total Active Projects", len(project_map))
    eb.add_summary_row("Grand Total Invoiced", round(grand_invoiced, 2), is_currency=True)
    eb.add_summary_row("Grand Total Collected", round(grand_collected, 2), is_currency=True)
    eb.add_summary_row("Grand Total Expenses", round(grand_expenses, 2), is_currency=True)
    eb.add_summary_row(
        "Net Company Profit",
        round(grand_invoiced - grand_expenses, 2),
        is_currency=True,
    )
    eb.build_summary_sheet()

    eb.add_data_sheet(
        "Financial Overview",
        overview_headers,
        overview_rows,
        currency_cols=[2, 3, 4, 5],
    )

    # Sheet 2: Expense Breakdown by Category
    sorted_categories = sorted(list(all_categories))
    cat_headers = ["Project ID", "Project Name"] + sorted_categories + ["Total Project Expenses"]
    cat_rows = []
    for pid, pname in project_map.items():
        row = [pid, pname]
        p_total = 0.0
        for cat in sorted_categories:
            amt = project_expenses[pid][cat]
            row.append(amt)
            p_total += amt
        row.append(p_total)
        cat_rows.append(row)

    if sorted_categories:
        c_cols = list(range(2, len(cat_headers)))
        eb.add_data_sheet(
            "Expense Breakdown",
            cat_headers,
            cat_rows,
            currency_cols=c_cols,
            title="Expenses by Category",
        )

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=project_financials_report.xlsx"},
    )


@router.get("/finance/pdf")
async def export_finance_pdf(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
        proj_query = select(m.Project).where(m.Project.id == project_id)
    else:
        if not is_sa:
            proj_query = select(m.Project).where(m.Project.company_id == current_user.company_id)
        else:
            proj_query = select(m.Project)
            if current_user.company_id is not None:
                proj_query = proj_query.where(m.Project.company_id == current_user.company_id)

    projects = (await db.execute(proj_query)).scalars().all()
    project_map = {p.id: p.project_name for p in projects}
    project_ids = list(project_map.keys())

    exp_query = select(Expense.project_id, func.sum(Expense.amount)).group_by(
        Expense.project_id
    )
    if project_ids:
        exp_query = exp_query.where(Expense.project_id.in_(project_ids))
    else:
        exp_query = exp_query.where(Expense.project_id == -1)

    if start_date:
        exp_query = exp_query.where(Expense.expense_date >= start_date)
    if end_date:
        exp_query = exp_query.where(Expense.expense_date <= end_date)

    exp_result = (await db.execute(exp_query)).all()
    project_expenses = {pid: float(amt or 0) for pid, amt in exp_result}

    inv_query = select(
        Invoice.project_id,
        func.sum(Invoice.total_amount),
        func.sum(Invoice.paid_amount),
    ).group_by(Invoice.project_id)
    if project_ids:
        inv_query = inv_query.where(Invoice.project_id.in_(project_ids))
    else:
        inv_query = inv_query.where(Invoice.project_id == -1)

    if start_date:
        inv_query = inv_query.where(Invoice.created_at >= start_date)
    if end_date:
        inv_query = inv_query.where(Invoice.created_at <= end_date + timedelta(days=1))

    inv_result = (await db.execute(inv_query)).all()
    project_invoices = {
        pid: (float(tot or 0), float(paid or 0)) for pid, tot, paid in inv_result
    }

    grand_invoiced = sum(v[0] for v in project_invoices.values())
    grand_collected = sum(v[1] for v in project_invoices.values())
    grand_expenses = sum(project_expenses.values())
    net_margin = grand_invoiced - grand_expenses

    b = PdfReportBuilder("FINANCIAL OVERVIEW REPORT", landscape_mode=True)
    b.add_info_table(
        [
            ("Generated On", str(date.today()), "Total Projects", str(len(project_map))),
            (
                "Period",
                f"{start_date or 'Start'} to {end_date or 'End'}",
                "Net Margin",
                f"Rs. {net_margin:,.2f}",
            ),
        ]
    )

    b.add_summary_box(
        "FINANCIAL TOTALS",
        [
            f"<b>Total Invoiced:</b> Rs. {grand_invoiced:,.2f} | "
            f"<b>Total Collected:</b> Rs. {grand_collected:,.2f} | "
            f"<b>Total Expenses:</b> Rs. {grand_expenses:,.2f}",
        ],
    )

    headers = [
        "ID",
        "Project Name",
        "Total Invoiced",
        "Total Collected",
        "Total Expenses",
        "Net Profit",
    ]
    rows = []
    for pid, pname in project_map.items():
        inv_tot, inv_paid = project_invoices.get(pid, (0.0, 0.0))
        exp_tot = project_expenses.get(pid, 0.0)
        rows.append(
            [
                str(pid),
                pname,
                f"Rs. {inv_tot:,.2f}",
                f"Rs. {inv_paid:,.2f}",
                f"Rs. {exp_tot:,.2f}",
                f"Rs. {(inv_tot - exp_tot):,.2f}",
            ]
        )

    b.add_section_table(
        "PROJECT BREAKDOWN",
        headers,
        rows,
        col_widths=[0.6 * inch, 2.6 * inch, 1.6 * inch, 1.6 * inch, 1.6 * inch, 1.6 * inch],
    )

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=project_financials_report.pdf"},
    )


@router.get("/profit-loss/excel")
async def export_profit_loss_excel(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    year: Optional[int] = Query(None, description="Financial Year"),
    quarter: Optional[int] = Query(None, description="Quarter (1-4)"),
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
        inv_query = select(Invoice).where(Invoice.project_id == project_id)
        exp_query = select(Expense).where(Expense.project_id == project_id)
    else:
        if not is_sa:
            inv_query = select(Invoice).join(m.Project, Invoice.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)
            exp_query = select(Expense).join(m.Project, Expense.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)
        else:
            inv_query = select(Invoice)
            exp_query = select(Expense)
            if current_user.company_id is not None:
                inv_query = inv_query.join(m.Project, Invoice.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)
                exp_query = exp_query.join(m.Project, Expense.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)

    if year:
        inv_query = inv_query.where(extract("year", Invoice.created_at) == year)
        exp_query = exp_query.where(extract("year", Expense.expense_date) == year)
        if quarter:
            q_months = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
            sm, em = q_months[quarter]
            inv_query = inv_query.where(
                extract("month", Invoice.created_at).between(sm, em)
            )
            exp_query = exp_query.where(
                extract("month", Expense.expense_date).between(sm, em)
            )

    if start_date:
        inv_query = inv_query.where(Invoice.created_at >= start_date)
        exp_query = exp_query.where(Expense.expense_date >= start_date)
    if end_date:
        inv_query = inv_query.where(Invoice.created_at <= end_date + timedelta(days=1))
        exp_query = exp_query.where(Expense.expense_date <= end_date)

    invoices = (await db.execute(inv_query)).scalars().all()
    expenses = (await db.execute(exp_query)).scalars().all()

    # Calculate Totals
    income_by_type = defaultdict(float)
    total_income = 0.0
    for inv in invoices:
        amt = float(inv.total_amount or 0)
        total_income += amt
        income_by_type[inv.type or "General Invoice"] += amt

    expense_by_cat = defaultdict(float)
    total_expense = 0.0
    for exp in expenses:
        amt = float(exp.amount or 0)
        total_expense += amt
        expense_by_cat[exp.category or "General Expense"] += amt

    net_profit = total_income - total_expense
    margin = (net_profit / total_income * 100) if total_income > 0 else 0.0

    eb = ExcelReportBuilder("Profit and Loss Statement")
    eb.add_summary_row("Total Revenue", round(total_income, 2), is_currency=True)
    eb.add_summary_row("Total Operating Expenses", round(total_expense, 2), is_currency=True)
    eb.add_summary_row("Net Profit", round(net_profit, 2), is_currency=True)
    eb.add_summary_row("Net Margin (%)", f"{round(margin, 2)}%")
    eb.build_summary_sheet()

    pnl_headers = ["Account Category", "Classification", "Total Amount"]
    pnl_rows = []
    for itype, amt in sorted(income_by_type.items()):
        pnl_rows.append([itype, "Operating Revenue", amt])
    pnl_rows.append(["TOTAL REVENUE", "Operating Revenue", total_income])

    for ecat, amt in sorted(expense_by_cat.items()):
        pnl_rows.append([ecat, "Operating Expense", amt])
    pnl_rows.append(["TOTAL EXPENSES", "Operating Expense", total_expense])

    pnl_rows.append(["NET PROFIT / (LOSS)", "Net Result", net_profit])

    eb.add_data_sheet("P&L Statement", pnl_headers, pnl_rows, currency_cols=[2])

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=profit_loss_report.xlsx"},
    )


@router.get("/profit-loss/pdf")
async def export_profit_loss_pdf(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    year: Optional[int] = Query(None, description="Financial Year"),
    quarter: Optional[int] = Query(None, description="Quarter (1-4)"),
    start_date: Optional[date] = Query(None, description="Start date"),
    end_date: Optional[date] = Query(None, description="End date"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
        inv_query = select(Invoice).where(Invoice.project_id == project_id)
        exp_query = select(Expense).where(Expense.project_id == project_id)
    else:
        if not is_sa:
            inv_query = select(Invoice).join(m.Project, Invoice.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)
            exp_query = select(Expense).join(m.Project, Expense.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)
        else:
            inv_query = select(Invoice)
            exp_query = select(Expense)
            if current_user.company_id is not None:
                inv_query = inv_query.join(m.Project, Invoice.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)
                exp_query = exp_query.join(m.Project, Expense.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)

    if year:
        inv_query = inv_query.where(extract("year", Invoice.created_at) == year)
        exp_query = exp_query.where(extract("year", Expense.expense_date) == year)
        if quarter:
            q_months = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
            sm, em = q_months[quarter]
            inv_query = inv_query.where(
                extract("month", Invoice.created_at).between(sm, em)
            )
            exp_query = exp_query.where(
                extract("month", Expense.expense_date).between(sm, em)
            )

    if start_date:
        inv_query = inv_query.where(Invoice.created_at >= start_date)
        exp_query = exp_query.where(Expense.expense_date >= start_date)
    if end_date:
        inv_query = inv_query.where(Invoice.created_at <= end_date + timedelta(days=1))
        exp_query = exp_query.where(Expense.expense_date <= end_date)

    invoices = (await db.execute(inv_query)).scalars().all()
    expenses = (await db.execute(exp_query)).scalars().all()

    total_income = sum(float(i.total_amount or 0) for i in invoices)
    total_expense = sum(float(e.amount or 0) for e in expenses)
    net_profit = total_income - total_expense
    margin = (net_profit / total_income * 100) if total_income > 0 else 0.0

    b = PdfReportBuilder("PROFIT & LOSS STATEMENT")
    period_str = (
        f"FY {year} Q{quarter}"
        if (year and quarter)
        else (f"FY {year}" if year else "Custom Date Range")
    )

    b.add_info_table(
        [
            ("Report Date", str(date.today()), "Reporting Period", period_str),
            ("Scope", f"Project ID: {project_id}" if project_id else "All Active Projects", "Status", "Audited Financials"),
        ]
    )

    b.add_summary_box(
        "EXECUTIVE SUMMARY",
        [
            f"<b>Total Revenue:</b> Rs. {total_income:,.2f}",
            f"<b>Total Expenses:</b> Rs. {total_expense:,.2f}",
            f"<b>Net Profit:</b> Rs. {net_profit:,.2f} ({margin:.1f}% Margin)",
        ],
    )

    headers = ["Category", "Type", "Amount"]
    rows = []

    income_by_type = defaultdict(float)
    for inv in invoices:
        income_by_type[inv.type or "General"] += float(inv.total_amount or 0)
    for itype, amt in sorted(income_by_type.items()):
        rows.append([f"Revenue: {itype}", "Income", f"Rs. {amt:,.2f}"])

    exp_by_cat = defaultdict(float)
    for exp in expenses:
        exp_by_cat[exp.category or "General"] += float(exp.amount or 0)
    for ecat, amt in sorted(exp_by_cat.items()):
        rows.append([f"Expense: {ecat}", "Cost", f"Rs. {amt:,.2f}"])

    b.add_section_table(
        "INCOME & EXPENDITURE BREAKDOWN",
        headers,
        rows,
        col_widths=[3.5 * inch, 1.5 * inch, 2.0 * inch],
    )

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=profit_loss_report.pdf"},
    )


@router.get("/daily")
async def daily_report(
    project_id: int,
    report_date: date,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    dsr = await db.scalar(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date == report_date,
        )
    )

    return {"dsr": dsr}


@router.get("/daily/export/pdf")
async def export_daily_pdf(
    project_id: int,
    report_date: date,
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    dsr = await db.scalar(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date == report_date,
        )
    )

    b = PdfReportBuilder(f"Daily Report - {report_date}")
    b.add_info_table([("Project ID", str(project_id), "Report Date", str(report_date))])

    if dsr:
        b.add_summary_box(
            "SITE DETAILS",
            [
                f"<b>Work Done:</b> {dsr.work_done}",
                f"Weather: {dsr.weather}",
                f"Remarks: {dsr.remarks}",
            ],
        )
    else:
        b.add_summary_box("SITE DETAILS", ["No data available"])

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=daily_report.pdf"},
    )


@router.get("/weekly")
async def weekly_progress(
    project_id: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
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

    progress = (
        sum(float(r[1]) for r in rows if r[1] is not None) / len(rows) if rows else 0
    )

    return {"weekly_progress_percent": round(progress, 2), "tasks_count": len(rows)}


@router.get("/labour")
async def labour_report(
    project_id: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
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


@router.get("/labour-distribution/excel")
async def export_labour_distribution_excel(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    date: Optional[date] = Query(
        None, description="Specific date for attendance filter"
    ),
    skill_category: Optional[str] = Query(
        None, description="Filter by SKILLED, UNSKILLED, etc"
    ),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    from app.models.labour import Labour, LabourProject

    query = (
        select(Labour, m.Project, LabourType)
        .join(LabourProject, Labour.id == LabourProject.labour_id)
        .join(m.Project, LabourProject.project_id == m.Project.id)
        .outerjoin(LabourType, Labour.labour_type_id == LabourType.id)
    )

    if not is_sa:
        query = query.where(m.Project.company_id == current_user.company_id)

    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
        query = query.where(m.Project.id == project_id)
    if skill_category:
        query = query.where(LabourType.skill_category == skill_category)

    query = query.where(Labour.status == LabourStatus.ACTIVE)
    results = (await db.execute(query)).all()

    attendance_map = {}
    if date:
        att_query = select(UserAttendance.user_id, UserAttendance.status).where(
            UserAttendance.date == date
        )
        if not is_sa:
            att_query = att_query.join(m.Project, UserAttendance.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)
        if project_id:
            att_query = att_query.where(UserAttendance.project_id == project_id)
        att_records = (await db.execute(att_query)).all()
        attendance_map = {r[0]: r[1] for r in att_records}

    headers = [
        "Labour ID",
        "Labour Name",
        "Skill Category",
        "Labour Type",
        "Project",
        "Daily Wage Rate",
        "Attendance Status",
    ]
    rows = []
    for lab, proj, ltype in results:
        status_str = attendance_map.get(lab.user_id, "N/A") if date else "Active"
        rows.append(
            [
                lab.worker_code or str(lab.id),
                lab.labour_name or "",
                ltype.skill_category if ltype else "Uncategorized",
                ltype.name if ltype else "N/A",
                proj.project_name if proj else "N/A",
                float(lab.custom_daily_wage_rate or (ltype.daily_wage_rate if ltype else 0.0) or 0.0),
                status_str,
            ]
        )

    eb = ExcelReportBuilder("Labour Distribution Report")
    eb.add_summary_row("Total Active Labour", len(rows))
    if date:
        eb.add_summary_row("Date of Record", str(date))
    eb.build_summary_sheet()
    eb.add_data_sheet("Labour Roster", headers, rows, currency_cols=[5])

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=labour_distribution_report.xlsx"},
    )


@router.get("/labour-distribution/pdf")
async def export_labour_distribution_pdf(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    date: Optional[date] = Query(None, description="Date for attendance filter"),
    skill_category: Optional[str] = Query(None, description="Skill category filter"),
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    from app.models.labour import Labour, LabourProject

    query = (
        select(Labour, m.Project, LabourType)
        .join(LabourProject, Labour.id == LabourProject.labour_id)
        .join(m.Project, LabourProject.project_id == m.Project.id)
        .outerjoin(LabourType, Labour.labour_type_id == LabourType.id)
    )

    if not is_sa:
        query = query.where(m.Project.company_id == current_user.company_id)

    if project_id:
        await _validate_project_access_404(db, project_id, current_user)
        query = query.where(m.Project.id == project_id)
    if skill_category:
        query = query.where(LabourType.skill_category == skill_category)

    query = query.where(Labour.status == LabourStatus.ACTIVE)
    results = (await db.execute(query)).all()

    attendance_map = {}
    if date:
        att_query = select(UserAttendance.user_id, UserAttendance.status).where(
            UserAttendance.date == date
        )
        if not is_sa:
            att_query = att_query.join(m.Project, UserAttendance.project_id == m.Project.id).where(m.Project.company_id == current_user.company_id)
        if project_id:
            att_query = att_query.where(UserAttendance.project_id == project_id)
        att_records = (await db.execute(att_query)).all()
        attendance_map = {r[0]: r[1] for r in att_records}

    b = PdfReportBuilder("LABOUR DISTRIBUTION & ROSTER", landscape_mode=True)
    b.add_info_table(
        [
            ("Generated On", str(date if date else date.today()), "Total Workers", str(len(results))),
            ("Project", str(project_id) if project_id else "All Projects", "Skill Category", skill_category or "All Skills"),
        ]
    )

    headers = ["ID", "Name", "Skill Category", "Designation", "Project", "Wage Rate", "Status"]
    rows = [
        [
            lab.worker_code or str(lab.id),
            lab.labour_name or "",
            ltype.skill_category if ltype else "Uncategorized",
            ltype.name if ltype else "N/A",
            proj.project_name if proj else "N/A",
            f"Rs. {float(lab.custom_daily_wage_rate or (ltype.daily_wage_rate if ltype else 0) or 0):,.2f}",
            attendance_map.get(lab.user_id, "Active") if date else "Active",
        ]
        for lab, proj, ltype in results
    ]

    b.add_section_table(
        "ACTIVE WORKFORCE",
        headers,
        rows,
        col_widths=[1.0 * inch, 2.0 * inch, 1.4 * inch, 1.6 * inch, 1.8 * inch, 1.2 * inch, 1.0 * inch],
    )

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=labour_distribution_report.pdf"},
    )


@router.get("/material")
async def material_report(
    project_id: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    result = await db.execute(
        select(Material)
        .where(Material.project_id == project_id)
        .order_by(Material.created_at.desc())
    )

    materials = result.scalars().all()

    return {"materials": materials}


@router.get("/material/export/excel")
async def export_material_excel(
    project_id: int,
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    stmt = (
        select(Material)
        .options(joinedload(Material.material_master).joinedload(MaterialMaster.unit))
        .where(Material.project_id == project_id)
        .order_by(Material.created_at.desc())
    )

    result = await db.execute(stmt)
    materials = result.scalars().all()

    headers = [
        "Material Name",
        "Category",
        "Unit",
        "Purchased Qty",
        "Used Qty",
        "Remaining Stock",
        "Purchase Rate",
        "Total Amount",
    ]

    rows = []
    total_amount = 0.0
    for mat in materials:
        unit_name = ""
        if mat.material_master and mat.material_master.unit:
            unit_name = mat.material_master.unit.name

        amt = float(mat.total_amount or 0)
        total_amount += amt

        rows.append(
            [
                mat.material_name or "",
                mat.category or "",
                unit_name,
                float(mat.quantity_purchased or 0),
                float(mat.quantity_used or 0),
                float(mat.remaining_stock or 0),
                float(mat.purchase_rate or 0),
                amt,
            ]
        )

    eb = ExcelReportBuilder("Material Report")
    eb.add_summary_row("Total Materials", len(rows))
    eb.add_summary_row("Total Amount", round(total_amount, 2), is_currency=True)
    eb.build_summary_sheet()
    eb.add_data_sheet("Materials", headers, rows, currency_cols=[7, 8])

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=material_report_{project_id}.xlsx"
        },
    )


@router.get("/issues")
async def issue_report(
    project_id: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
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


@router.get("/issues/export/excel")
async def export_issue_excel(
    project_id: int,
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    result = await db.execute(
        select(m.Issue)
        .where(m.Issue.project_id == project_id)
        .order_by(m.Issue.created_at.desc())
    )
    issues = result.scalars().all()

    headers = [
        "Issue ID",
        "Title",
        "Category",
        "Priority",
        "Status",
        "Reported Date",
        "Description",
    ]
    rows = [
        [
            issue.business_id or str(issue.id),
            issue.title,
            str(getattr(issue.category, "value", issue.category)),
            str(getattr(issue.priority, "value", issue.priority)),
            str(getattr(issue.status, "value", issue.status)),
            str(issue.reported_date) if issue.reported_date else "N/A",
            issue.description or "",
        ]
        for issue in issues
    ]

    eb = ExcelReportBuilder("Project Issue Report")
    eb.add_summary_row("Total Issues", len(rows))
    eb.build_summary_sheet()
    eb.add_data_sheet("Issues", headers, rows)

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=project_{project_id}_issues.xlsx"
        },
    )


@router.get("/download")
async def client_report_download(
    project_id: int,
    start_date: date,
    end_date: date,
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    result = await db.execute(
        select(m.DailySiteReport)
        .where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date >= start_date,
            m.DailySiteReport.report_date <= end_date,
        )
        .order_by(m.DailySiteReport.report_date.desc())
    )
    reports = result.scalars().all()

    b = PdfReportBuilder(f"Report ({start_date} to {end_date})")
    b.add_info_table(
        [("Project ID", str(project_id), "Period", f"{start_date} to {end_date}")]
    )

    headers = ["Date", "Work Done"]
    rows = [[str(r.report_date), r.work_done] for r in reports]
    b.add_section_table(
        "DAILY SITE REPORTS", headers, rows, empty_text="No data available"
    )

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=filtered_report.pdf"},
    )


@router.get("/combined")
async def combined_report(
    project_id: int,
    start_date: date,
    end_date: date,
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)

    progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id == project_id
        )
    )

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

    reports = await db.execute(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date >= start_date,
            m.DailySiteReport.report_date <= end_date,
        )
    )
    dsr_list = reports.scalars().all()

    b = PdfReportBuilder("Combined Project Report")
    b.add_info_table(
        [("Project ID", str(project_id), "Period", f"{start_date} to {end_date}")]
    )
    b.add_summary_box(
        "PROGRESS & FINANCIAL SUMMARY",
        [
            f"<b>Progress:</b> {round(progress or 0, 2)}%",
            f"Total Paid: Rs. {float(total_paid or 0):,.2f}",
            f"Pending: Rs. {float(total_pending or 0):,.2f}",
        ],
    )

    headers = ["Date", "Work Done"]
    rows = [[str(r.report_date), r.work_done] for r in dsr_list]
    b.add_section_table("WORK SUMMARY", headers, rows, empty_text="No data available")

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=combined_report.pdf"},
    )


@router.get("/contractor-performance")
async def contractor_performance(
    project_id: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)

    total_tasks = await db.scalar(
        select(func.count(m.Task.id)).where(m.Task.project_id == project_id)
    )

    avg_progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id == project_id
        )
    )

    total_paid = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id,
            Invoice.status == InvoiceStatus.PAID,
        )
    )

    progress_val = float(avg_progress or 0)

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
    current_user: User = Depends(require_permission("reports.view")),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        income = await db.scalar(
            select(func.sum(Invoice.total_amount))
            .join(m.Project, Invoice.project_id == m.Project.id)
            .where(
                Invoice.type == "owner",
                m.Project.company_id == current_user.company_id,
            )
        )
        expense = await db.scalar(
            select(func.sum(Invoice.total_amount))
            .join(m.Project, Invoice.project_id == m.Project.id)
            .where(
                Invoice.type.in_(["labour", "material"]),
                m.Project.company_id == current_user.company_id,
            )
        )
    else:
        income_q = select(func.sum(Invoice.total_amount)).where(Invoice.type == "owner")
        expense_q = select(func.sum(Invoice.total_amount)).where(
            Invoice.type.in_(["labour", "material"])
        )
        if current_user.company_id is not None:
            income_q = income_q.join(m.Project, Invoice.project_id == m.Project.id).where(
                m.Project.company_id == current_user.company_id
            )
            expense_q = expense_q.join(m.Project, Invoice.project_id == m.Project.id).where(
                m.Project.company_id == current_user.company_id
            )
        income = await db.scalar(income_q)
        expense = await db.scalar(expense_q)

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
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    revenue = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.project_id == project_id, Invoice.type == "owner"
        )
    )

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
    current_user: User = Depends(require_permission("reports.view")),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        inflow = await db.scalar(
            select(func.sum(Transaction.amount))
            .join(m.Project, Transaction.project_id == m.Project.id)
            .where(
                Transaction.type == "receipt",
                m.Project.company_id == current_user.company_id,
            )
        )
        outflow = await db.scalar(
            select(func.sum(Transaction.amount))
            .join(m.Project, Transaction.project_id == m.Project.id)
            .where(
                Transaction.type == "payment",
                m.Project.company_id == current_user.company_id,
            )
        )
    else:
        inflow_q = select(func.sum(Transaction.amount)).where(Transaction.type == "receipt")
        outflow_q = select(func.sum(Transaction.amount)).where(Transaction.type == "payment")
        if current_user.company_id is not None:
            inflow_q = inflow_q.join(m.Project, Transaction.project_id == m.Project.id).where(
                m.Project.company_id == current_user.company_id
            )
            outflow_q = outflow_q.join(m.Project, Transaction.project_id == m.Project.id).where(
                m.Project.company_id == current_user.company_id
            )
        inflow = await db.scalar(inflow_q)
        outflow = await db.scalar(outflow_q)

    return {
        "inflow": float(inflow or 0),
        "outflow": float(outflow or 0),
        "balance": float((inflow or 0) - (outflow or 0)),
    }


@router.get("/assets")
async def asset_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("reports.view")),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        result = await db.execute(
            select(FixedAsset)
            .join(m.Project, FixedAsset.project_id == m.Project.id)
            .where(m.Project.company_id == current_user.company_id)
        )
    else:
        query = select(FixedAsset)
        if current_user.company_id is not None:
            query = query.join(m.Project, FixedAsset.project_id == m.Project.id).where(
                m.Project.company_id == current_user.company_id
            )
        result = await db.execute(query)
    assets = result.scalars().all()

    return assets


@router.get("/financial-summary")
async def financial_summary(
    project_id: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
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


@router.get("/quarterly-audit-summary")
async def quarterly_financial_audit(
    project_id: int,
    year: int,
    quarter: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
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


@router.get("/work-summary")
async def work_summary(
    project_id: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    result = await db.execute(select(m.Task).where(m.Task.project_id == project_id))
    tasks = result.scalars().all()
    summary = []
    for task in tasks:
        actual = round(float(task.completion_percentage or 0), 2)
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


@router.get("/audit-pdf")
async def audit_pdf(
    project_id: int,
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
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

    total_tasks = await db.scalar(
        select(func.count()).select_from(m.Task).where(m.Task.project_id == project_id)
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

    progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id == project_id
        )
    )

    project = await db.get(m.Project, project_id)

    b = PdfReportBuilder(
        title="PROJECT AUDIT REPORT",
        subtitle=f"Generated on {date.today().strftime('%d %b %Y')}",
    )

    b.add_info_table(
        [
            ("Project Name", project.project_name if project else f"Project #{project_id}", "Project ID", str(project_id)),
            ("Audit Date", str(date.today()), "Status", project.status.value if project and hasattr(project.status, 'value') else "Active"),
        ]
    )

    expense_val = float(total_expense or 0)
    invoice_val = float(total_invoice or 0)
    paid_val = float(paid_invoice or 0)
    pending_val = float(pending_invoice or 0)
    b.add_summary_box(
        "EXECUTIVE AUDIT SUMMARY",
        [
            f"<b>Overall Progress:</b> {round(float(progress or 0), 2)}%",
            f"<b>Total Revenue:</b> Rs. {invoice_val:,.2f} | <b>Collected:</b> Rs. {paid_val:,.2f}",
            f"<b>Total Expenses:</b> Rs. {expense_val:,.2f} | <b>Net Margin:</b> Rs. {(invoice_val - expense_val):,.2f}",
            f"<b>Tasks Completed:</b> {int(completed_tasks or 0)} of {int(total_tasks or 0)} | <b>Delayed Tasks:</b> {int(delayed_tasks or 0)}",
        ],
    )

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=project_{project_id}_audit.pdf"},
    )


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
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)
    project_ids = []
    if project_id is not None:
        await _validate_project_access_404(db, project_id, current_user)
        project_ids = [project_id]
    else:
        if not is_sa:
            res = await db.execute(
                select(m.Project.id).where(m.Project.company_id == current_user.company_id)
            )
        else:
            if current_user.company_id is not None:
                res = await db.execute(
                    select(m.Project.id).where(m.Project.company_id == current_user.company_id)
                )
            else:
                res = await db.execute(select(m.Project.id))
        project_ids = res.scalars().all()
        if not project_ids:
            raise HTTPException(status_code=403, detail="No accessible projects found")

    if type not in ["daily", "weekly", "monthly", "quarterly"]:
        raise HTTPException(status_code=400, detail="Invalid report type")

    if type == "daily" and not report_date:
        raise HTTPException(
            status_code=400, detail="report_date is required for daily report"
        )

    if type == "weekly" and (not start_date or not end_date):
        raise HTTPException(
            status_code=400,
            detail="start_date and end_date are required for weekly report",
        )

    if type == "monthly" and (not month or not year):
        raise HTTPException(
            status_code=400, detail="month and year are required for monthly report"
        )

    if type == "quarterly" and (not quarter or not year):
        raise HTTPException(
            status_code=400, detail="quarter and year are required for quarterly report"
        )

    if type == "daily":
        start_date = report_date
        end_date = report_date

    elif type == "weekly":
        pass

    elif type == "monthly":
        days = monthrange(year, month)[1]
        start_date = date(year, month, 1)
        end_date = date(year, month, days)

    elif type == "quarterly":
        quarter_map = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
        start_month, end_month = quarter_map[quarter]
        days = monthrange(year, end_month)[1]
        start_date = date(year, start_month, 1)
        end_date = date(year, end_month, days)

    dsr_result = await db.execute(
        select(m.DailySiteReport)
        .where(
            m.DailySiteReport.project_id.in_(project_ids),
            m.DailySiteReport.report_date >= start_date,
            m.DailySiteReport.report_date <= end_date,
        )
        .order_by(m.DailySiteReport.report_date.desc())
    )
    dsr_list = dsr_result.scalars().all()

    overall_progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id.in_(project_ids)
        )
    )
    total_tasks = await db.scalar(
        select(func.count(m.Task.id)).where(m.Task.project_id.in_(project_ids))
    )
    completed_tasks = await db.scalar(
        select(func.count(m.Task.id)).where(
            m.Task.project_id.in_(project_ids),
            m.Task.status == TaskStatus.COMPLETED.value,
        )
    )

    open_issues = await db.scalar(
        select(func.count(m.Issue.id)).where(
            m.Issue.project_id.in_(project_ids),
            m.Issue.status == IssueStatus.OPEN.value,
        )
    )

    single_project = None
    if project_id is not None:
        p = await db.get(m.Project, project_id)
        if p:
            single_project = {
                "id": p.id,
                "project_name": p.project_name,
                "status": p.status.value if p.status else None,
            }

    return {
        "project": single_project,
        "type": type,
        "period": {"start_date": start_date, "end_date": end_date},
        "summary": {
            "overall_progress": round(float(overall_progress or 0), 2),
            "total_tasks": int(total_tasks or 0),
            "completed_tasks": int(completed_tasks or 0),
            "open_issues": int(open_issues or 0),
        },
        "daily_reports": [
            {
                "date": r.report_date,
                "work_done": r.work_done,
                "issues": r.issues,
                "site_location": r.site_location,
            }
            for r in dsr_list
        ],
    }


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
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
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

    b = PdfReportBuilder(f"{type.title()} Project Report")
    proj_name = response["project"]["project_name"] if response.get("project") else "Portfolio"
    b.add_info_table(
        [
            (
                "Project",
                proj_name,
                "Report Type",
                type.title(),
            )
        ]
    )
    b.add_summary_box(
        "SUMMARY",
        [
            f"<b>Progress:</b> {response['summary']['overall_progress']}%",
            f"Completed Tasks: {response['summary']['completed_tasks']}",
            f"Open Issues: {response['summary']['open_issues']}",
        ],
    )

    headers = ["Date", "Work Done"]
    rows = [[str(r["date"]), r["work_done"]] for r in response["daily_reports"]]
    b.add_section_table(
        "DAILY WORK LOGS", headers, rows, empty_text="No data available"
    )

    stream = b.build()

    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={type}_project_report.pdf"
        },
    )


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
    current_user: User = Depends(require_permission("reports.export")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
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

    eb = ExcelReportBuilder(f"{type.title()} Project Report")
    proj_name = response["project"]["project_name"] if response.get("project") else "Portfolio"
    eb.add_summary_row("Project", proj_name)
    eb.add_summary_row("Report Type", type.title())
    eb.add_summary_row("Overall Progress", f"{response['summary']['overall_progress']}%")
    eb.add_summary_row("Completed Tasks", response['summary']['completed_tasks'])
    eb.add_summary_row("Open Issues", response['summary']['open_issues'])
    eb.build_summary_sheet()

    headers = ["Date", "Work Done", "Issues Reported", "Site Location"]
    rows = [
        [
            str(r["date"]),
            r["work_done"] or "",
            r["issues"] or "",
            r["site_location"] or "",
        ]
        for r in response["daily_reports"]
    ]
    eb.add_data_sheet("Daily Logs", headers, rows)

    stream = eb.build()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={type}_project_report.xlsx"
        },
    )


@router.get("/business-intelligence")
async def business_intelligence_kpis(
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    if current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="Super Admin cannot access company business intelligence",
        )

    revenue = await db.scalar(
        select(func.sum(Invoice.total_amount))
        .join(m.Project, m.Project.id == Invoice.project_id)
        .where(
            Invoice.type == "owner",
            Invoice.status == InvoiceStatus.PAID,
            m.Project.company_id == current_user.company_id,
        )
    )

    expense = await db.scalar(
        select(func.sum(Expense.amount))
        .join(m.Project, m.Project.id == Expense.project_id)
        .where(m.Project.company_id == current_user.company_id)
    )

    documented_reports = await db.scalar(
        select(func.count(m.DailySiteReport.id))
        .join(m.Project, m.DailySiteReport.project_id == m.Project.id)
        .where(m.Project.company_id == current_user.company_id)
    )

    active_sites = await db.scalar(
        select(func.count(m.Project.id)).where(
            m.Project.status == ProjectStatus.ONGOING.value,
            m.Project.company_id == current_user.company_id,
        )
    )

    revenue_val = float(revenue or 0)
    expense_val = float(expense or 0)
    net_profit = revenue_val - expense_val

    return {
        "revenue_focus": net_profit,
        "expenditure": expense_val,
        "activity_log": int(documented_reports or 0),
        "efficiency": f"Syncing from {active_sites or 0} sites",
    }


@router.get("/work-category")
async def work_category_summary(
    project_id: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)

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


@router.get("/audit-summary")
async def quarterly_audit_summary(
    project_id: int,
    current_user: User = Depends(require_permission("reports.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)

    today = date.today()
    current_quarter = (today.month - 1) // 3 + 1
    start_month = 3 * current_quarter - 2
    start_date = date(today.year, start_month, 1)

    critical_issues = await db.scalar(
        select(func.count(m.Issue.id)).where(
            m.Issue.project_id == project_id,
            m.Issue.priority == IssuePriority.HIGH.value,
            m.Issue.created_at >= start_date,
        )
    )

    audit_logs = await db.scalar(
        select(func.count(ActivityLog.id)).where(
            ActivityLog.entity == "project",
            ActivityLog.entity_id == project_id,
            ActivityLog.created_at >= start_date,
        )
    )

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


@router.get("/commercial-execution")
async def commercial_execution_analytics(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("reports.view")),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
    from app.models.boq import BOQ

    boq_items = (
        (await db.execute(select(BOQ).where(BOQ.project_id == project_id)))
        .scalars()
        .all()
    )

    boq_total_planned_cost = sum(float(item.total_cost or 0) for item in boq_items)

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

    actual_certified_amount = sum(
        float(getattr(meas, "total_amount", 0) or 0) for meas in measurements
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


@router.get("/contractor-execution")
async def contractor_execution_analytics(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("reports.view")),
):
    _check_tenant_access(current_user)
    await _validate_project_access_404(db, project_id, current_user)
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


# NOTE: The following email/WhatsApp sharing endpoints were commented out in the
# original implementation and relied on send_email / send_report_template /
# BackgroundTasks. They are preserved here as-is (still disabled) for reference.
# Re-enable by uncommenting and restoring the relevant imports if needed.

# @router.post("/combined/share/email")
# async def share_combined_report_email(
#     project_id: int,
#     start_date: date,
#     end_date: date,
#     email: str,
#     background_tasks: BackgroundTasks,
#     current_user: User = Depends(require_permission("reports.export")),
#     db: AsyncSession = Depends(get_db_session),
# ):
#     await assert_project_access(db, project_id=project_id, current_user=current_user)
#
#     progress = await db.scalar(
#         select(func.avg(m.Task.completion_percentage)).where(
#             m.Task.project_id == project_id
#         )
#     )
#
#     total_paid = await db.scalar(
#         select(func.sum(Invoice.total_amount)).where(
#             Invoice.project_id == project_id,
#             Invoice.status == InvoiceStatus.PAID
#         )
#     )
#
#     total_pending = await db.scalar(
#         select(func.sum(Invoice.total_amount)).where(
#             Invoice.project_id == project_id,
#             Invoice.status == InvoiceStatus.PENDING
#         )
#     )
#
#     reports = await db.execute(
#         select(m.DailySiteReport).where(
#             m.DailySiteReport.project_id == project_id,
#             m.DailySiteReport.report_date >= start_date,
#             m.DailySiteReport.report_date <= end_date,
#         )
#     )
#     dsr_list = reports.scalars().all()
#
#     b = PdfReportBuilder("Combined Project Report")
#     b.add_info_table([("Project ID", str(project_id), "Period", f"{start_date} to {end_date}")])
#     b.add_summary_box("SUMMARY", [
#         f"Progress: {round(progress or 0, 2)}%",
#         f"Paid: {float(total_paid or 0)}",
#         f"Pending: {float(total_pending or 0)}",
#     ])
#     headers = ["Date", "Work Done"]
#     rows = [[str(r.report_date), r.work_done] for r in dsr_list]
#     b.add_section_table("WORK SUMMARY", headers, rows)
#     buffer = b.build()
#
#     background_tasks.add_task(
#         send_email,
#         to_email=email,
#         subject="Combined Project Report",
#         body=f"Report from {start_date} to {end_date}",
#         attachment=buffer.read(),
#         filename="combined_report.pdf",
#     )
#
#     return {"message": "Email queued successfully"}


# @router.post("/combined/share/whatsapp")
# async def share_combined_whatsapp(
#     project_id: int,
#     start_date: date,
#     end_date: date,
#     phone: str,
#     current_user: User = Depends(require_permission("reports.export")),
#     db: AsyncSession = Depends(get_db_session),
# ):
#     await assert_project_access(db, project_id=project_id, current_user=current_user)
#
#     report_url = f"http://localhost:8000/reports/combined?project_id={project_id}&start_date={start_date}&end_date={end_date}"
#
#     result = await send_report_template(
#         to=phone,
#         name="Client",
#         report_url=report_url
#     )
#
#     return {
#         "message": "WhatsApp message sent",
#         "response": result
#     }


# @router.post("/daily/share/email")
# async def share_daily_email(
#     project_id: int,
#     report_date: date,
#     email: str,
#     background_tasks: BackgroundTasks,
#     current_user: User = Depends(require_permission("reports.export")),
#     db: AsyncSession = Depends(get_db_session),
# ):
#     await assert_project_access(db, project_id=project_id, current_user=current_user)
#
#     dsr = await db.scalar(
#         select(m.DailySiteReport).where(
#             m.DailySiteReport.project_id == project_id,
#             m.DailySiteReport.report_date == report_date,
#         )
#     )
#
#     b = PdfReportBuilder(f"Daily Report - {report_date}")
#     if dsr:
#         b.add_summary_box("SITE DETAILS", [
#             f"Work Done: {dsr.work_done}",
#             f"Weather: {dsr.weather}",
#         ])
#     else:
#         b.add_summary_box("SITE DETAILS", ["No data available"])
#     buffer = b.build()
#
#     body = f"""
#     <html>
#     <body style="font-family: Arial, sans-serif;">
#         <h2>Daily Site Report</h2>
#         <p><b>Date:</b> {report_date}</p>
#         <p>Please find the attached report.</p>
#         <hr>
#         <p style="font-size:12px;color:gray;">Construction Management System</p>
#     </body>
#     </html>
#     """
#
#     background_tasks.add_task(
#         send_email,
#         to_email=email,
#         subject="Daily Report",
#         body=body,
#         attachment=buffer.read(),
#         filename="daily_report.pdf",
#     )
#
#     return {"message": "Email queued successfully"}
