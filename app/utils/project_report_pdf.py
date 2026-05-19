import io
import os
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, Flowable, PageBreak, KeepTogether
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
        bottomMargin=cm
    )
    
    elements = []
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=NAVY_BLUE,
        alignment=TA_LEFT,
        spaceAfter=15,
        fontName='Helvetica-Bold'
    )
    
    heading2_style = ParagraphStyle(
        'CustomH2',
        parent=styles['Heading2'],
        fontSize=12,
        textColor=NAVY_BLUE,
        spaceAfter=10,
        fontName='Helvetica-Bold'
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.black,
        spaceAfter=5,
        fontName='Helvetica'
    )

    bold_style = ParagraphStyle(
        'CustomBold',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.black,
        spaceAfter=5,
        fontName='Helvetica-Bold'
    )

    # 1. HEADER / TITLE
    logo_path = "static/logo.png"
    logo_img = None
    if os.path.exists(logo_path):
        logo_img = Image(logo_path, width=2*inch, height=0.75*inch)
    else:
        logo_img = Paragraph("<b>INFRA PILOT</b>", title_style)
        
    header_data = [
        [logo_img, Paragraph("<b>PROJECT REPORT</b>", title_style)]
    ]
    header_table = Table(header_data, colWidths=[2.5*inch, 4.5*inch])
    header_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    
    elements.append(header_table)
    elements.append(Spacer(1, 0.2*inch))
    
    # 2. PROJECT INFO
    project_info = data.get("project", {})
    
    pi_data = [
        [Paragraph("<b>Project Name</b>", bold_style), project_info.get("name", "N/A"), Paragraph("<b>Project Code</b>", bold_style), project_info.get("code", "N/A")],
        [Paragraph("<b>Client Name</b>", bold_style), project_info.get("client", "N/A"), Paragraph("<b>Project Type</b>", bold_style), project_info.get("type", "N/A")],
        [Paragraph("<b>Location</b>", bold_style), project_info.get("location", "N/A"), Paragraph("<b>Current Status</b>", bold_style), project_info.get("status", "N/A")],
        [Paragraph("<b>Start Date</b>", bold_style), str(project_info.get("start_date", "N/A")), Paragraph("<b>Planned End Date</b>", bold_style), str(project_info.get("end_date", "N/A"))],
        [Paragraph("<b>Project Manager</b>", bold_style), project_info.get("manager", "N/A"), Paragraph("<b>Site Supervisor</b>", bold_style), project_info.get("supervisor", "N/A")],
    ]
    
    pi_table = Table(pi_data, colWidths=[1.5*inch, 2*inch, 1.5*inch, 2*inch])
    pi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), LIGHT_GRAY),
        ('BACKGROUND', (2, 0), (2, -1), LIGHT_GRAY),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
    ]))
    
    elements.append(Paragraph("1. PROJECT INFORMATION", heading2_style))
    elements.append(pi_table)
    elements.append(Spacer(1, 0.3*inch))
    
    # 3. EXECUTIVE SUMMARY
    summary = data.get("summary", {})
    
    elements.append(Paragraph("2. EXECUTIVE SUMMARY", heading2_style))
    
    sum_data = [
        ["Overall Progress", f"{summary.get('progress', 0)}%"],
        ["Total Tasks", str(summary.get('total_tasks', 0))],
        ["Completed Tasks", str(summary.get('completed_tasks', 0))],
        ["Milestones", f"{summary.get('milestones_completed', 0)} / {summary.get('milestones_total', 0)}"],
        ["Team Members", str(summary.get('team_members', 0))],
    ]
    sum_table = Table(sum_data, colWidths=[3.5*inch, 3.5*inch])
    sum_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), LIGHT_GRAY),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(sum_table)
    elements.append(Spacer(1, 0.3*inch))
    
    # 4. FINANCIAL OVERVIEW
    elements.append(Paragraph("3. FINANCIAL OVERVIEW (Rs.)", heading2_style))
    fin_data = [
        [Paragraph("<b>Particulars</b>", bold_style), Paragraph("<b>Amount (Rs.)</b>", bold_style)],
        ["Total BOQ Value", format_currency(summary.get("boq_value", 0))],
        ["Total Invoiced", format_currency(summary.get("invoiced", 0))],
        ["Total Expenses", format_currency(summary.get("expenses", 0))],
        ["Net Profit / (Loss)", format_currency(summary.get("net_profit", 0))],
        ["Outstanding Amount", format_currency(summary.get("outstanding", 0))],
    ]
    fin_table = Table(fin_data, colWidths=[3.5*inch, 3.5*inch])
    fin_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY_BLUE),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(fin_table)
    elements.append(Spacer(1, 0.3*inch))
    
    # 5. TASKS SUMMARY
    tasks = data.get("tasks", [])
    if tasks:
        elements.append(Paragraph("4. TASKS SUMMARY", heading2_style))
        task_table_data = [[
            Paragraph("<b>Task Name</b>", bold_style),
            Paragraph("<b>Assigned To</b>", bold_style),
            Paragraph("<b>Start Date</b>", bold_style),
            Paragraph("<b>End Date</b>", bold_style),
            Paragraph("<b>Status</b>", bold_style),
            Paragraph("<b>Progress</b>", bold_style)
        ]]
        
        for t in tasks:
            task_table_data.append([
                Paragraph(t.get('name', ''), normal_style),
                t.get('assignee', ''),
                str(t.get('start_date', '')),
                str(t.get('end_date', '')),
                t.get('status', ''),
                f"{t.get('progress', 0)}%"
            ])
            
        task_table = Table(task_table_data, colWidths=[2*inch, 1.2*inch, 0.9*inch, 0.9*inch, 0.9*inch, 1*inch])
        task_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), NAVY_BLUE),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(task_table)
        elements.append(Spacer(1, 0.3*inch))
        
    # 6. MILESTONES
    milestones = data.get("milestones", [])
    if milestones:
        elements.append(Paragraph("5. MILESTONES", heading2_style))
        milestone_data = [[
            Paragraph("<b>Milestone Name</b>", bold_style),
            Paragraph("<b>Due Date</b>", bold_style),
            Paragraph("<b>Status</b>", bold_style),
            Paragraph("<b>Completion</b>", bold_style)
        ]]
        
        for m in milestones:
            milestone_data.append([
                Paragraph(m.get('name', ''), normal_style),
                str(m.get('end_date', '')),
                m.get('status', ''),
                f"{m.get('completion', 0)}%"
            ])
            
        ms_table = Table(milestone_data, colWidths=[3.5*inch, 1.2*inch, 1.2*inch, 1.1*inch])
        ms_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), NAVY_BLUE),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(ms_table)
        elements.append(Spacer(1, 0.3*inch))
        
    # 7. TEAM MEMBERS
    members = data.get("members", [])
    if members:
        elements.append(Paragraph("6. TEAM MEMBERS", heading2_style))
        member_data = [[
            Paragraph("<b>Name</b>", bold_style),
            Paragraph("<b>Role</b>", bold_style),
            Paragraph("<b>Phone</b>", bold_style),
            Paragraph("<b>Email</b>", bold_style)
        ]]
        for tm in members:
            member_data.append([
                tm.get('name', ''),
                tm.get('role', ''),
                tm.get('phone', ''),
                tm.get('email', '')
            ])
        mem_table = Table(member_data, colWidths=[2*inch, 1.5*inch, 1.5*inch, 2*inch])
        mem_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), NAVY_BLUE),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(mem_table)
        elements.append(Spacer(1, 0.3*inch))
        
    # FOOTER
    def add_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setStrokeColor(NAVY_BLUE)
        canvas.setLineWidth(1)
        canvas.line(cm, 1.5*cm, A4[0]-cm, 1.5*cm)
        
        # Draw signature placeholders
        canvas.drawString(cm, 1.2*cm, "Prepared By: ______________")
        canvas.drawString(A4[0]/2 - 1.5*cm, 1.2*cm, "Reviewed By: ______________")
        canvas.drawString(A4[0] - 5*cm, 1.2*cm, "Approved By: ______________")
        
        canvas.drawString(cm, 0.8*cm, "Generated by InfraPilot Construction Management System")
        canvas.drawRightString(A4[0]-cm, 0.8*cm, f"Page {doc.page}")
        canvas.restoreState()
        
    doc.build(elements, onFirstPage=add_footer, onLaterPages=add_footer)
    buffer.seek(0)
    return buffer
