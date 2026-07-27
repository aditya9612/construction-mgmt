import io
import os
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Image,
    Flowable,
    PageBreak,
    KeepTogether,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

# Colors matching the image
NAVY_BLUE = colors.HexColor("#0B2B5C")
LIGHT_BLUE = colors.HexColor("#3498DB")
GREEN = colors.HexColor("#27AE60")
RED = colors.HexColor("#E74C3C")
ORANGE = colors.HexColor("#F39C12")
LIGHT_GRAY = colors.HexColor("#F8F9FA")
BORDER_GRAY = colors.HexColor("#E2E8F0")


def format_currency(value):
    if value is None:
        return "Rs. 0"
    return f"Rs. {value:,.2f}"


def generate_project_report_pdf(data: dict) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=cm,
        leftMargin=cm,
        topMargin=cm,
        bottomMargin=cm,
    )

    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=20,
        textColor=NAVY_BLUE,
        alignment=TA_LEFT,
        spaceAfter=15,
        fontName="Helvetica-Bold",
    )

    heading2_style = ParagraphStyle(
        "CustomH2",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=NAVY_BLUE,
        spaceAfter=10,
        fontName="Helvetica-Bold",
    )

    normal_style = ParagraphStyle(
        "CustomNormal",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.black,
        spaceAfter=5,
        fontName="Helvetica",
    )

    bold_style = ParagraphStyle(
        "CustomBold",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.black,
        spaceAfter=5,
        fontName="Helvetica-Bold",
    )

    # 1. HEADER / TITLE
    logo_path = "static/logo.png"
    logo_img = None
    if os.path.exists(logo_path):
        logo_img = Image(logo_path, width=2 * inch, height=0.75 * inch)
    else:
        logo_img = Paragraph("<b>INFRA PILOT</b>", title_style)

    header_data = [[logo_img, Paragraph("<b>PROJECT REPORT</b>", title_style)]]
    header_table = Table(header_data, colWidths=[2.5 * inch, 4.5 * inch])
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

    elements.append(header_table)
    elements.append(Spacer(1, 0.2 * inch))

    # 2. PROJECT INFO
    project_info = data.get("project", {})

    pi_data = [
        [
            Paragraph("<b>Project Name</b>", bold_style),
            project_info.get("name", "N/A"),
            Paragraph("<b>Project Code</b>", bold_style),
            project_info.get("code", "N/A"),
        ],
        [
            Paragraph("<b>Client Name</b>", bold_style),
            project_info.get("client", "N/A"),
            Paragraph("<b>Project Type</b>", bold_style),
            project_info.get("type", "N/A"),
        ],
        [
            Paragraph("<b>Location</b>", bold_style),
            project_info.get("location", "N/A"),
            Paragraph("<b>Current Status</b>", bold_style),
            project_info.get("status", "N/A"),
        ],
        [
            Paragraph("<b>Start Date</b>", bold_style),
            str(project_info.get("start_date", "N/A")),
            Paragraph("<b>Planned End Date</b>", bold_style),
            str(project_info.get("end_date", "N/A")),
        ],
        [
            Paragraph("<b>Project Manager</b>", bold_style),
            project_info.get("manager", "N/A"),
            Paragraph("<b>Site Supervisor</b>", bold_style),
            project_info.get("supervisor", "N/A"),
        ],
    ]

    pi_table = Table(pi_data, colWidths=[1.5 * inch, 2 * inch, 1.5 * inch, 2 * inch])
    pi_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_GRAY),
                ("BACKGROUND", (2, 0), (2, -1), LIGHT_GRAY),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
            ]
        )
    )

    elements.append(Paragraph("1. PROJECT INFORMATION", heading2_style))
    elements.append(pi_table)
    elements.append(Spacer(1, 0.3 * inch))

    # 3. EXECUTIVE SUMMARY
    summary = data.get("summary", {})

    elements.append(Paragraph("2. EXECUTIVE SUMMARY", heading2_style))

    sum_data = [
        ["Overall Progress", f"{summary.get('progress', 0)}%"],
        ["Total Tasks", str(summary.get("total_tasks", 0))],
        ["Completed Tasks", str(summary.get("completed_tasks", 0))],
        [
            "Milestones",
            f"{summary.get('milestones_completed', 0)} / {summary.get('milestones_total', 0)}",
        ],
        ["Team Members", str(summary.get("team_members", 0))],
    ]
    sum_table = Table(sum_data, colWidths=[3.5 * inch, 3.5 * inch])
    sum_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_GRAY),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(sum_table)
    elements.append(Spacer(1, 0.3 * inch))

    # 4. FINANCIAL OVERVIEW
    elements.append(Paragraph("3. FINANCIAL OVERVIEW (Rs.)", heading2_style))
    fin_data = [
        [
            Paragraph("<b>Particulars</b>", bold_style),
            Paragraph("<b>Amount (Rs.)</b>", bold_style),
        ],
        ["Total BOQ Value", format_currency(summary.get("boq_value", 0))],
        ["Total Invoiced", format_currency(summary.get("invoiced", 0))],
        ["Total Expenses", format_currency(summary.get("expenses", 0))],
        ["Net Profit / (Loss)", format_currency(summary.get("net_profit", 0))],
        ["Outstanding Amount", format_currency(summary.get("outstanding", 0))],
    ]
    fin_table = Table(fin_data, colWidths=[3.5 * inch, 3.5 * inch])
    fin_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(fin_table)
    elements.append(Spacer(1, 0.3 * inch))

    # 5. TASKS SUMMARY
    tasks = data.get("tasks", [])
    if tasks:
        elements.append(Paragraph("4. TASKS SUMMARY", heading2_style))
        task_table_data = [
            [
                Paragraph("<b>Task Name</b>", bold_style),
                Paragraph("<b>Assigned To</b>", bold_style),
                Paragraph("<b>Start Date</b>", bold_style),
                Paragraph("<b>End Date</b>", bold_style),
                Paragraph("<b>Status</b>", bold_style),
                Paragraph("<b>Progress</b>", bold_style),
            ]
        ]

        for t in tasks:
            task_table_data.append(
                [
                    Paragraph(t.get("name", ""), normal_style),
                    t.get("assignee", ""),
                    str(t.get("start_date", "")),
                    str(t.get("end_date", "")),
                    t.get("status", ""),
                    f"{t.get('progress', 0)}%",
                ]
            )

        task_table = Table(
            task_table_data,
            colWidths=[
                2 * inch,
                1.2 * inch,
                0.9 * inch,
                0.9 * inch,
                0.9 * inch,
                1 * inch,
            ],
        )
        task_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY_BLUE),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        elements.append(task_table)
        elements.append(Spacer(1, 0.3 * inch))

    # 6. MILESTONES
    milestones = data.get("milestones", [])
    if milestones:
        elements.append(Paragraph("5. MILESTONES", heading2_style))
        milestone_data = [
            [
                Paragraph("<b>Milestone Name</b>", bold_style),
                Paragraph("<b>Due Date</b>", bold_style),
                Paragraph("<b>Status</b>", bold_style),
                Paragraph("<b>Completion</b>", bold_style),
            ]
        ]

        for m in milestones:
            milestone_data.append(
                [
                    Paragraph(m.get("name", ""), normal_style),
                    str(m.get("end_date", "")),
                    m.get("status", ""),
                    f"{m.get('completion', 0)}%",
                ]
            )

        ms_table = Table(
            milestone_data, colWidths=[3.5 * inch, 1.2 * inch, 1.2 * inch, 1.1 * inch]
        )
        ms_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY_BLUE),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        elements.append(ms_table)
        elements.append(Spacer(1, 0.3 * inch))

    # 7. TEAM MEMBERS
    members = data.get("members", [])
    if members:
        elements.append(Paragraph("6. TEAM MEMBERS", heading2_style))
        member_data = [
            [
                Paragraph("<b>Name</b>", bold_style),
                Paragraph("<b>Role</b>", bold_style),
                Paragraph("<b>Phone</b>", bold_style),
                Paragraph("<b>Email</b>", bold_style),
            ]
        ]
        for tm in members:
            member_data.append(
                [
                    tm.get("name", ""),
                    tm.get("role", ""),
                    tm.get("phone", ""),
                    tm.get("email", ""),
                ]
            )
        mem_table = Table(
            member_data, colWidths=[2 * inch, 1.5 * inch, 1.5 * inch, 2 * inch]
        )
        mem_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY_BLUE),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        elements.append(mem_table)
        elements.append(Spacer(1, 0.3 * inch))

    # FOOTER
    def add_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setStrokeColor(NAVY_BLUE)
        canvas.setLineWidth(1)
        canvas.line(cm, 1.5 * cm, A4[0] - cm, 1.5 * cm)

        # Draw signature placeholders
        canvas.drawString(cm, 1.2 * cm, "Prepared By: ______________")
        canvas.drawString(A4[0] / 2 - 1.5 * cm, 1.2 * cm, "Reviewed By: ______________")
        canvas.drawString(A4[0] - 5 * cm, 1.2 * cm, "Approved By: ______________")

        canvas.drawString(
            cm, 0.8 * cm, "Generated by InfraPilot Construction Management System"
        )
        canvas.drawRightString(A4[0] - cm, 0.8 * cm, f"Page {doc.page}")
        canvas.restoreState()

    doc.build(elements, onFirstPage=add_footer, onLaterPages=add_footer)
    buffer.seek(0)
    return buffer


def generate_procurement_report_pdf(data: dict) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=cm,
        leftMargin=cm,
        topMargin=cm,
        bottomMargin=cm,
    )

    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ProcTitle",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=NAVY_BLUE,
        alignment=TA_LEFT,
        spaceAfter=10,
        fontName="Helvetica-Bold",
    )

    heading2_style = ParagraphStyle(
        "ProcH2",
        parent=styles["Heading2"],
        fontSize=11,
        textColor=NAVY_BLUE,
        spaceBefore=10,
        spaceAfter=6,
        fontName="Helvetica-Bold",
    )

    normal_style = ParagraphStyle(
        "ProcNormal",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.black,
        spaceAfter=4,
        fontName="Helvetica",
    )

    bold_style = ParagraphStyle(
        "ProcBold",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.black,
        spaceAfter=4,
        fontName="Helvetica-Bold",
    )

    # 1. HEADER
    logo_path = "static/logo.png"
    if os.path.exists(logo_path):
        logo_img = Image(logo_path, width=1.8 * inch, height=0.6 * inch)
    else:
        logo_img = Paragraph("<b>INFRA PILOT</b>", title_style)

    header_data = [
        [logo_img, Paragraph("<b>WEEKLY PROCUREMENT REPORT</b>", title_style)]
    ]
    header_table = Table(header_data, colWidths=[2.5 * inch, 4.5 * inch])
    header_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(header_table)
    elements.append(Spacer(1, 0.15 * inch))

    # 1. PROJECT INFORMATION
    pinfo = data.get("project", {})
    report_date = data.get("report_date", datetime.utcnow().strftime("%Y-%m-%d"))
    pi_data = [
        [
            Paragraph("<b>Project Name</b>", bold_style),
            pinfo.get("name", "N/A"),
            Paragraph("<b>Report Date</b>", bold_style),
            report_date,
        ],
        [
            Paragraph("<b>Project Code</b>", bold_style),
            pinfo.get("code", "N/A"),
            Paragraph("<b>Location</b>", bold_style),
            pinfo.get("location", "N/A"),
        ],
    ]
    pi_table = Table(pi_data, colWidths=[1.5 * inch, 2 * inch, 1.5 * inch, 2 * inch])
    pi_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_GRAY),
                ("BACKGROUND", (2, 0), (2, -1), LIGHT_GRAY),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(Paragraph("1. PROJECT INFORMATION", heading2_style))
    elements.append(pi_table)
    elements.append(Spacer(1, 0.15 * inch))

    # 2. AI PROCUREMENT SUMMARY
    ai_sum = data.get("ai_procurement_summary", {})
    elements.append(Paragraph("2. AI PROCUREMENT SUMMARY", heading2_style))
    risk = ai_sum.get("overall_risk", "LOW")
    summary_text = ai_sum.get("procurement_summary", "No critical summary.")
    action_text = ai_sum.get("recommended_action", "No action specified.")

    ai_box_data = [
        [
            Paragraph(
                f"<b>Overall Risk:</b> {risk} | <b>Estimated Budget:</b> {format_currency(ai_sum.get('estimated_budget', 0))}",
                bold_style,
            )
        ],
        [Paragraph(f"<b>Summary:</b> {summary_text}", normal_style)],
        [Paragraph(f"<b>Recommended Action:</b> {action_text}", normal_style)],
    ]
    ai_box_table = Table(ai_box_data, colWidths=[7 * inch])
    ai_box_table.setStyle(
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
    elements.append(ai_box_table)
    elements.append(Spacer(1, 0.15 * inch))

    # 3. CURRENT INVENTORY
    elements.append(Paragraph("3. CURRENT INVENTORY", heading2_style))
    inv_list = data.get("current_inventory", [])
    inv_headers = [
        Paragraph("<b>Material</b>", bold_style),
        Paragraph("<b>Category</b>", bold_style),
        Paragraph("<b>Stock</b>", bold_style),
        Paragraph("<b>Min Stock</b>", bold_style),
        Paragraph("<b>Rate</b>", bold_style),
        Paragraph("<b>Total Value</b>", bold_style),
    ]
    inv_data = [inv_headers]
    for item in inv_list[:15]:
        inv_data.append(
            [
                Paragraph(item.get("material_name", ""), normal_style),
                item.get("category", ""),
                str(item.get("remaining_stock", 0)),
                str(item.get("minimum_stock_level", 0)),
                format_currency(item.get("purchase_rate", 0)),
                format_currency(item.get("total_amount", 0)),
            ]
        )
    inv_table = Table(
        inv_data,
        colWidths=[
            1.8 * inch,
            1.1 * inch,
            0.9 * inch,
            0.9 * inch,
            1.1 * inch,
            1.2 * inch,
        ],
    )
    inv_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(inv_table)
    elements.append(Spacer(1, 0.15 * inch))

    # 4. CRITICAL MATERIALS
    elements.append(Paragraph("4. CRITICAL MATERIALS", heading2_style))
    crit_list = data.get("critical_materials", [])
    crit_headers = [
        Paragraph("<b>Material</b>", bold_style),
        Paragraph("<b>Stock</b>", bold_style),
        Paragraph("<b>Min Stock</b>", bold_style),
        Paragraph("<b>Priority</b>", bold_style),
        Paragraph("<b>Risk / Reason</b>", bold_style),
    ]
    crit_data = [crit_headers]
    for item in crit_list:
        crit_data.append(
            [
                Paragraph(item.get("material_name", ""), normal_style),
                str(item.get("current_stock", item.get("remaining_stock", 0))),
                str(item.get("minimum_stock", item.get("minimum_stock_level", 0))),
                item.get("priority", "High"),
                Paragraph(item.get("risk", item.get("reason", "")), normal_style),
            ]
        )
    if len(crit_data) == 1:
        crit_data.append(
            [Paragraph("No critical materials.", normal_style), "-", "-", "-", "-"]
        )
    crit_table = Table(
        crit_data,
        colWidths=[1.8 * inch, 0.9 * inch, 0.9 * inch, 1.0 * inch, 2.4 * inch],
    )
    crit_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(crit_table)
    elements.append(Spacer(1, 0.15 * inch))

    # 5. SUPPLIER RECOMMENDATIONS
    elements.append(Paragraph("5. AI SUPPLIER RECOMMENDATIONS", heading2_style))
    supp_recs = data.get("supplier_recommendations", [])
    supp_headers = [
        Paragraph("<b>Material</b>", bold_style),
        Paragraph("<b>Best Supplier</b>", bold_style),
        Paragraph("<b>Last Rate</b>", bold_style),
        Paragraph("<b>Avg Delivery</b>", bold_style),
        Paragraph("<b>Score</b>", bold_style),
        Paragraph("<b>Reason</b>", bold_style),
    ]
    supp_data = [supp_headers]
    for item in supp_recs:
        supp = item.get("recommended_supplier", {})
        supp_data.append(
            [
                Paragraph(item.get("material_name", ""), normal_style),
                Paragraph(supp.get("supplier_name", "N/A"), bold_style),
                format_currency(supp.get("last_purchase_rate", 0)),
                f"{supp.get('average_delivery_days', 0)} days",
                f"{supp.get('supplier_score', 0)}/100",
                Paragraph(supp.get("recommendation_reason", ""), normal_style),
            ]
        )
    if len(supp_data) == 1:
        supp_data.append(
            [
                Paragraph("No supplier recommendations.", normal_style),
                "-",
                "-",
                "-",
                "-",
                "-",
            ]
        )
    supp_table = Table(
        supp_data,
        colWidths=[
            1.4 * inch,
            1.3 * inch,
            1.0 * inch,
            0.9 * inch,
            0.7 * inch,
            1.7 * inch,
        ],
    )
    supp_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(supp_table)
    elements.append(Spacer(1, 0.15 * inch))

    # 6. ESTIMATED PROCUREMENT BUDGET & RECOMMENDATIONS
    elements.append(
        Paragraph("6. PROCUREMENT RECOMMENDATIONS & ESTIMATED BUDGET", heading2_style)
    )
    p_recs = data.get("procurement_recommendations", [])
    rec_headers = [
        Paragraph("<b>Material</b>", bold_style),
        Paragraph("<b>Recommended Quantity</b>", bold_style),
        Paragraph("<b>Est. Cost</b>", bold_style),
        Paragraph("<b>Priority</b>", bold_style),
    ]
    rec_data_table = [rec_headers]
    for item in p_recs:
        rec_data_table.append(
            [
                Paragraph(item.get("material_name", ""), normal_style),
                f"{item.get('recommended_purchase', 0)}",
                format_currency(item.get("estimated_cost", 0)),
                item.get("priority", "Medium"),
            ]
        )
    if len(rec_data_table) == 1:
        rec_data_table.append(
            [Paragraph("No active recommendations.", normal_style), "-", "-", "-"]
        )
    rec_table = Table(
        rec_data_table, colWidths=[2.5 * inch, 1.8 * inch, 1.5 * inch, 1.2 * inch]
    )
    rec_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(rec_table)
    elements.append(Spacer(1, 0.15 * inch))

    # 7. MATERIAL CONSUMPTION SUMMARY (30 DAYS)
    elements.append(
        Paragraph("7. MATERIAL CONSUMPTION SUMMARY (30 DAYS)", heading2_style)
    )
    csum = data.get("consumption_summary", {})
    h_day = csum.get("highest_consumption_day") or {}
    l_day = csum.get("lowest_consumption_day") or {}

    csum_data = [
        [
            Paragraph("<b>Average Daily Consumption</b>", bold_style),
            f"{csum.get('average_daily_consumption', 0)} units/day",
        ],
        [
            Paragraph("<b>Highest Consumption Day</b>", bold_style),
            f"{h_day.get('date', 'N/A')} ({h_day.get('quantity_used', 0)} units - {h_day.get('material_name', '')})",
        ],
        [
            Paragraph("<b>Lowest Consumption Day</b>", bold_style),
            f"{l_day.get('date', 'N/A')} ({l_day.get('quantity_used', 0)} units - {l_day.get('material_name', '')})",
        ],
    ]
    csum_table = Table(csum_data, colWidths=[2.5 * inch, 4.5 * inch])
    csum_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_GRAY),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GRAY),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(csum_table)

    def add_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setStrokeColor(NAVY_BLUE)
        canvas.setLineWidth(1)
        canvas.line(cm, 1.5 * cm, A4[0] - cm, 1.5 * cm)

        canvas.drawString(cm, 1.2 * cm, "Prepared By: ______________")
        canvas.drawString(A4[0] / 2 - 1.5 * cm, 1.2 * cm, "Reviewed By: ______________")
        canvas.drawString(A4[0] - 5 * cm, 1.2 * cm, "Approved By: ______________")

        canvas.drawString(
            cm,
            0.8 * cm,
            "Generated by InfraPilot Construction Management System - AI Procurement Module",
        )
        canvas.drawRightString(A4[0] - cm, 0.8 * cm, f"Page {doc.page}")
        canvas.restoreState()

    doc.build(elements, onFirstPage=add_footer, onLaterPages=add_footer)
    buffer.seek(0)
    return buffer
