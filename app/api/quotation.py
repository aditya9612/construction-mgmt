from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from starlette import status
from app.models.billing import RABill
from app.models.contractor import Contractor
from app.models.equipment import Equipment
from app.models.labour import Labour
from datetime import date, datetime
from num2words import num2words
from app.db.session import get_db_session
from app.models.settings import CompanySettings
from sqlalchemy import select
from app.models.material import Material
from app.models.project import Project
from app.models.notification import Notification
from app.models.quotation import (
    QuotationExtraCharge,
    QuotationMaster,
    QuotationItem,
    MeasurementDetail,
    QuotationMaterial,
    QuotationStatus,
    QuotationLabour,
)

from app.models.user import User, ActivityLog, UserRole
from app.models.owner import Owner
from app.core.dependencies import get_current_active_user

import app.schemas.quotation as s
from decimal import Decimal
from app.models.work_order import WorkOrder
from app.utils.common import generate_business_id
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    Image,
    KeepTogether,
)

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quotations", tags=["Quotations"])


# =========================================================
# ITEM TYPES
# =========================================================

ITEM_SOLING = "soling"
ITEM_PLUM_CONCRETE = "plum_concrete"
ITEM_STONE_WORK = "stone_work"
ITEM_EXCAVATION = "excavation"
ITEM_RCC = "rcc"
ITEM_ROAD_WORK = "road_work"


# =========================================================
# CALCULATIONS
# =========================================================


def calculate_cubic_feet(length, width, height):
    return length * width * height


def calculate_cubic_meter(cubic_ft):
    return cubic_ft * 0.0283168


def calculate_brass(cubic_ft):
    return cubic_ft / 100


def calculate_amount(quantity, rate):
    return quantity * rate


def calculate_item(unit, length, width, height, rate):

    cubic_ft = calculate_cubic_feet(length, width, height)

    cubic_meter = calculate_cubic_meter(cubic_ft)

    brass = calculate_brass(cubic_ft)

    unit_lower = (unit or "").lower()

    if unit_lower in ["brass"]:
        quantity = brass
        formula = "brass"

    elif unit_lower in ["m3", "cum", "cubic meter"]:
        quantity = cubic_meter
        formula = "cubic_meter"

    elif unit_lower in ["cft", "ft3", "cubic feet"]:
        quantity = cubic_ft
        formula = "cubic_feet"

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported unit: {unit}")

    amount = quantity * rate

    return {
        "cubic_feet": round(cubic_ft, 2),
        "cubic_meter": round(cubic_meter, 2),
        "brass": round(brass, 2),
        "quantity": round(quantity, 2),
        "amount": round(amount, 2),
        "formula": formula,
    }


# =========================================================
# HELPERS
# =========================================================


async def get_quotation_or_404(quotation_id: int, db: AsyncSession, current_user):

    result = await db.execute(
        select(QuotationMaster)
        .options(
            selectinload(QuotationMaster.items).selectinload(
                QuotationItem.measurements
            ),
            selectinload(QuotationMaster.labour_items),
            selectinload(QuotationMaster.material_items),
            selectinload(QuotationMaster.extra_charge_items),
        )
        .where(QuotationMaster.id == quotation_id)
    )

    quotation = result.scalars().first()

    if not quotation:
        raise HTTPException(404, "Quotation not found")

    # Tenant / Client Validation
    if current_user.role == UserRole.CLIENT:
        if quotation.client_user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to access this quotation")
    else:
        # Standard tenant users must belong to the company that owns the quotation
        if not current_user.company_id or quotation.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Not authorized to access this quotation")

    return quotation


async def generate_quotation_no(db: AsyncSession):

    year = datetime.now().year

    result = await db.execute(select(func.max(QuotationMaster.id)))

    last_id = result.scalar()

    next_id = (last_id or 0) + 1

    return f"QT/{year}/{next_id:04d}"


def create_styled_table(data, col_widths, highlight_last_row=False):
    """
    Create professional styled table with:
    - Dark blue header
    - White header text
    - Alternating row colors
    - Optional green total row
    """

    table = Table(data, colWidths=col_widths)

    style = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
    ]

    # Alternating row colors
    for row in range(1, len(data)):
        if row % 2 == 0:
            style.append(
                ("BACKGROUND", (0, row), (-1, row), colors.HexColor("#F2F6FA"))
            )

    # Highlight final row (Grand Total)
    if highlight_last_row and len(data) > 1:
        last = len(data) - 1
        style.extend(
            [
                ("BACKGROUND", (0, last), (-1, last), colors.HexColor("#D9EAD3")),
                ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
            ]
        )

    table.setStyle(TableStyle(style))
    return table


# =========================================================
# QUOTATION TOTAL CALCULATION
# =========================================================


def calculate_quotation_totals(quotation: QuotationMaster):

    # =====================================================
    # ITEM TOTAL
    # =====================================================

    item_total = sum(item.amount or 0 for item in quotation.items)

    # =====================================================
    # LABOUR TOTAL
    # =====================================================

    labour_total = sum(labour.amount or 0 for labour in quotation.labour_items)

    # =====================================================
    # MATERIAL TOTAL
    # =====================================================

    material_total = sum(
        material.estimated_amount or 0 for material in quotation.material_items
    )

    # =====================================================
    # EXTRA CHARGES TOTAL
    # =====================================================

    extra_total = sum(extra.amount or 0 for extra in quotation.extra_charge_items)

    # =====================================================
    # SUBTOTAL
    # =====================================================

    subtotal = item_total + labour_total + material_total + extra_total

    # =====================================================
    # GST BREAKDOWN
    # =====================================================

    cgst_amount = (subtotal * quotation.cgst_percent) / 100

    sgst_amount = (subtotal * quotation.sgst_percent) / 100

    gross_total = subtotal + cgst_amount + sgst_amount

    # =====================================================
    # TDS DEDUCTION
    # =====================================================

    tds_amount = (gross_total * quotation.tds_percent) / 100

    # =====================================================
    # FINAL GRAND TOTAL
    # =====================================================

    grand_total = gross_total - tds_amount - quotation.discount_amount

    # =====================================================
    # BALANCE DUE
    # =====================================================

    balance_due = grand_total - quotation.advance_paid

    # =====================================================
    # SAVE VALUES
    # =====================================================

    quotation.subtotal = round(subtotal, 2)

    quotation.cgst_amount = round(cgst_amount, 2)

    quotation.sgst_amount = round(sgst_amount, 2)

    quotation.tds_amount = round(tds_amount, 2)

    # OPTIONAL OLD GST SUPPORT

    quotation.gst_amount = round(cgst_amount + sgst_amount, 2)

    quotation.grand_total = round(grand_total, 2)

    quotation.balance_due = round(balance_due, 2)


def calculate_labour_amount(
    labour_count, daily_wage, labour_days, overtime_hours, overtime_rate
):

    base_amount = labour_count * daily_wage * labour_days

    overtime_amount = overtime_hours * overtime_rate

    return round(base_amount + overtime_amount, 2)


def amount_to_words(amount):

    words = num2words(amount, lang="en_IN")

    return words.title()


from io import BytesIO
from datetime import datetime

import qrcode

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
)

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

from reportlab.graphics.barcode import qr

from app.models.quotation import QuotationMaster

# =========================================================
# GENERATE QR IMAGE
# =========================================================


def generate_upi_qr(quotation: QuotationMaster):

    if not quotation.upi_id:
        return None

    amount = quotation.grand_total

    upi_url = (
        f"upi://pay?"
        f"pa={quotation.upi_id}"
        f"&pn={quotation.company_name or 'Company'}"
        f"&am={amount}"
    )

    qr_code = qr.QrCodeWidget(upi_url)

    bounds = qr_code.getBounds()

    width = bounds[2] - bounds[0]

    height = bounds[3] - bounds[1]

    drawing = Drawing(80, 80, transform=[80 / width, 0, 0, 80 / height, 0, 0])

    drawing.add(qr_code)

    return drawing


def generate_quotation_pdf(
    quotation: QuotationMaster, company_settings: CompanySettings | None = None
):
    from io import BytesIO
    import os

    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        Image,
    )
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import (
        getSampleStyleSheet,
        ParagraphStyle,
    )
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib import colors

    buffer = BytesIO()

    # =====================================================
    # DOCUMENT SETUP
    # =====================================================

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20,
        rightMargin=20,
        topMargin=100,
        bottomMargin=110,  # Reserve space for fixed footer
    )

    styles = getSampleStyleSheet()
    elements = []

    # =====================================================
    # CUSTOM TITLE STYLE
    # =====================================================

    title_style = ParagraphStyle(
        "QuotationTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#1F4E79"),
        spaceAfter=10,
    )

    # Replace your current logo_path code with this exact code

    # =====================================================
    # LOGO + TITLE
    # =====================================================

    # =====================================================
    # DYNAMIC LOGO FROM COMPANY SETTINGS
    # Uses logo uploaded from /settings/upload-logo
    # =====================================================

    logo_path = None

    if company_settings and company_settings.company_logo:
        if os.path.exists(company_settings.company_logo):
            logo_path = company_settings.company_logo

    # Create title
    title_para = Paragraph("<b>PROJECT QUOTATION</b>", title_style)

    # Create header table
    if logo_path:

        logo = Image(logo_path, width=80, height=80)

        header_table = Table([[logo, title_para, ""]], colWidths=[100, 350, 100])

    else:

        # If no uploaded logo exists, show title only
        header_table = Table([["", title_para, ""]], colWidths=[100, 350, 100])

    # Style header table
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    # =====================================================
    # COMPANY DETAILS
    # =====================================================

    company_details = f"""
    <b>{quotation.company_name or ''}</b><br/>
    GST: {quotation.gst_number or '-'}<br/>
    Mobile: {quotation.mobile_number or '-'}<br/>
    Email: {quotation.email or '-'}
    """

    elements.append(Paragraph(company_details, styles["BodyText"]))

    elements.append(Spacer(1, 12))

    # =====================================================
    # QUOTATION INFORMATION
    # =====================================================

    quotation_info = [
        ["Field", "Value"],
        ["Quotation No", quotation.quotation_no],
        ["Date", quotation.created_at.strftime("%d-%m-%Y")],
        ["Project", quotation.project_name],
        ["Project Type", quotation.project_type],
        ["Engineer", quotation.engineer_name or "-"],
        ["Work Order", quotation.work_order_no or "-"],
    ]

    elements.append(create_styled_table(quotation_info, [150, 370]))

    elements.append(Spacer(1, 15))

    # =====================================================
    # CLIENT DETAILS
    # =====================================================

    client_info = [
        ["Field", "Value"],
        ["Client Name", quotation.client_name],
        ["Billing Address", quotation.billing_address or "-"],
        ["Site Address", quotation.site_address or "-"],
        ["Mobile", quotation.mobile_number or "-"],
        ["GST Number", quotation.gst_number or "-"],
    ]

    elements.append(
        KeepTogether(
            [
                Paragraph("<b>Client Details</b>", styles["Heading2"]),
                Spacer(1, 6),
                create_styled_table(client_info, [150, 370]),
                Spacer(1, 15),
            ]
        )
    )

    # =====================================================
    # ITEM DETAILS
    # =====================================================

    item_data = [["Item", "Qty", "Unit", "Rate", "Amount"]]

    for item in quotation.items:
        item_data.append(
            [
                item.title,
                f"{item.quantity:.2f}",
                item.unit or "-",
                f"{item.rate:.2f}",
                f"{item.amount:.2f}",
            ]
        )

    elements.append(
        KeepTogether(
            [
                Paragraph("<b>Item Details</b>", styles["Heading2"]),
                Spacer(1, 6),
                create_styled_table(item_data, [180, 70, 70, 80, 90]),
                Spacer(1, 15),
            ]
        )
    )

    # =====================================================
    # LABOUR DETAILS
    # =====================================================

    if quotation.labour_items:
        labour_data = [["Skill", "Count", "Days", "Daily Wage", "Amount"]]

        for labour in quotation.labour_items:
            labour_data.append(
                [
                    labour.skill_type,
                    str(labour.labour_count),
                    f"{labour.labour_days:.2f}",
                    f"{labour.daily_wage:.2f}",
                    f"{labour.amount:.2f}",
                ]
            )

    elements.append(
        KeepTogether(
            [
                Paragraph("<b>Labour Details</b>", styles["Heading2"]),
                Spacer(1, 6),
                create_styled_table(labour_data, [150, 80, 80, 100, 100]),
                Spacer(1, 15),
            ]
        )
    )

    # =====================================================
    # MATERIAL DETAILS
    # =====================================================

    if quotation.material_items:
        material_data = [["Material", "Qty", "Unit", "Rate", "Amount"]]

        for material in quotation.material_items:
            material_data.append(
                [
                    material.material_name,
                    f"{material.estimated_quantity:.2f}",
                    material.unit,
                    f"{material.estimated_rate:.2f}",
                    f"{material.estimated_amount:.2f}",
                ]
            )

        elements.append(
            KeepTogether(
                [
                    Paragraph("<b>Material Details</b>", styles["Heading2"]),
                    Spacer(1, 6),
                    create_styled_table(material_data, [180, 70, 70, 80, 90]),
                    Spacer(1, 15),
                ]
            )
        )

    # =====================================================
    # EXTRA CHARGES
    # =====================================================

    if quotation.extra_charge_items:
        extra_data = [["Type", "Qty", "Rate", "Amount"]]

        for extra in quotation.extra_charge_items:
            extra_data.append(
                [
                    extra.expense_type,
                    f"{extra.quantity:.2f}",
                    f"{extra.rate:.2f}",
                    f"{extra.amount:.2f}",
                ]
            )

        elements.append(
            KeepTogether(
                [
                    Paragraph("<b>Extra Charges</b>", styles["Heading2"]),
                    Spacer(1, 6),
                    create_styled_table(extra_data, [220, 90, 90, 90]),
                    Spacer(1, 15),
                ]
            )
        )

    # =====================================================
    # SUMMARY
    # =====================================================

    summary_data = [
        ["Description", "Amount"],
        ["Subtotal", f"{quotation.subtotal:.2f}"],
        ["CGST", f"{quotation.cgst_amount:.2f}"],
        ["SGST", f"{quotation.sgst_amount:.2f}"],
        ["TDS", f"{quotation.tds_amount:.2f}"],
        ["Discount", f"{quotation.discount_amount:.2f}"],
        ["Advance Paid", f"{quotation.advance_paid:.2f}"],
        ["Grand Total", f"{quotation.grand_total:.2f}"],
        ["Balance Due", f"{quotation.balance_due:.2f}"],
    ]

    elements.append(
        KeepTogether(
            [
                Paragraph("<b>Financial Summary</b>", styles["Heading2"]),
                Spacer(1, 6),
                create_styled_table(summary_data, [250, 150], highlight_last_row=True),
                Spacer(1, 20),
            ]
        )
    )

    # =====================================================
    # AMOUNT IN WORDS
    # =====================================================

    elements.append(
        Paragraph(
            f"<b>Amount in Words:</b> "
            f"{amount_to_words(int(quotation.grand_total))} Only",
            styles["BodyText"],
        )
    )

    elements.append(Spacer(1, 15))

    # =====================================================
    # TERMS & CONDITIONS
    # =====================================================

    if quotation.terms_conditions:
        elements.append(
            Paragraph(
                f"<b>Terms & Conditions</b><br/>" f"{quotation.terms_conditions}",
                styles["BodyText"],
            )
        )
        elements.append(Spacer(1, 20))

    # =====================================================
    # QR CODE + SIGNATURE (TOGETHER)
    # =====================================================

    # Create a single block so QR code and signature stay together
    payment_elements = []

    # -----------------------------
    # QR CODE
    # -----------------------------
    qr_drawing = generate_upi_qr(quotation)

    if qr_drawing:
        payment_elements.append(Paragraph("<b>Scan To Pay</b>", styles["Heading3"]))
        payment_elements.append(Spacer(1, 5))
        payment_elements.append(qr_drawing)
        payment_elements.append(Spacer(1, 15))

    # -----------------------------
    # SIGNATURE
    # -----------------------------
    company_name = (
        company_settings.company_name
        if company_settings and company_settings.company_name
        else quotation.company_name or ""
    )
    # =====================================================
    # QR CODE + SIGNATURE (SIDE BY SIDE)
    # =====================================================
    # Replace your existing QR CODE + SIGNATURE section with this code.
    # IMPORTANT: This version removes KeepTogether inside table cells,
    # which fixes the LayoutError shown in your log :contentReference[oaicite:0]{index=0}

    # Project root
    project_root = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),  # app/api or app/routers
            "..",  # app
            "..",  # project root
        )
    )

    signature_path = None

    if company_settings and company_settings.signature_image:
        if os.path.exists(company_settings.signature_image):
            signature_path = company_settings.signature_image

    # -----------------------------------------------------
    # QR CODE
    # -----------------------------------------------------
    qr_drawing = generate_upi_qr(quotation)
    if qr_drawing:
        elements.append(Paragraph("<b><i>Scan To Pay</i></b>", styles["Heading3"]))
        elements.append(Spacer(1, 5))
        qr_drawing.hAlign = "LEFT"
        elements.append(qr_drawing)
        elements.append(Spacer(1, 35))

    # -----------------------------------------------------
    # SIGNATURE
    # -----------------------------------------------------
    if (
        signature_path
        and os.path.exists(signature_path)
        and signature_path.lower().endswith((".png", ".jpg", ".jpeg"))
    ):
        signature_img = Image(signature_path, width=140, height=50)
        signature_img.hAlign = "LEFT"
        elements.append(signature_img)
        elements.append(Spacer(1, 5))

    elements.append(
        Paragraph(
            f"<b>Authorized Signature</b><br/>{company_name}",
            styles["BodyText"],
        )
    )
    elements.append(Spacer(1, 10))

    # Horizontal line
    line_table = Table([[""]], colWidths=[555])  # Full width line
    line_table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, -1), 0.5, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    elements.append(line_table)
    elements.append(Spacer(1, 15))

    # =====================================================
    # FOOTER (DRAWN DIRECTLY IN PDF)
    # =====================================================

    footer_style = ParagraphStyle(
        "FooterStyle",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        textColor=colors.black,
    )

    # =====================================================
    # FOOTER DATA FROM COMPANY SETTINGS
    # =====================================================

    mobile = (
        company_settings.mobile_number
        if company_settings and company_settings.mobile_number
        else "-"
    )

    email = (
        company_settings.email if company_settings and company_settings.email else "-"
    )

    instagram_handle = (
        company_settings.instagram_handle
        if company_settings and company_settings.instagram_handle
        else "-"
    )

    whatsapp_number = (
        company_settings.whatsapp_number
        if company_settings and company_settings.whatsapp_number
        else "-"
    )

    website = (
        company_settings.website
        if company_settings and company_settings.website
        else "-"
    )

    address = (
        company_settings.address
        if company_settings and company_settings.address
        else "-"
    )

    # =====================================================
    # ICON HELPERS
    # =====================================================

    icon_dir = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",  # app
            "..",  # project root
            "static",
        )
    )

    from reportlab.lib.utils import ImageReader

    def get_icon(filename):
        """
        Load icon from static/icons and return a fixed-size
        ReportLab Image that renders reliably in tables.
        """
        path = os.path.join(icon_dir, filename)

        if not os.path.exists(path):
            print(f"Icon not found: {path}")
            return Spacer(1, 18)  # preserve alignment if icon is missing

        try:
            # Create image with explicit dimensions
            img = Image(path, width=18, height=18)

            # Ensure proper alignment inside table cells
            img.hAlign = "CENTER"

            return img

        except Exception as e:
            print(f"Error loading icon {filename}: {e}")
            return Spacer(1, 18)

    def create_icon_text_table(icon_filename, text, col_width=150):
        """
        Creates a small 2-column table:
        [ icon ][ text ]
        """
        table = Table(
            [
                [
                    get_icon(icon_filename),
                    Paragraph(text, footer_style),
                ]
            ],
            colWidths=[24, col_width],
        )

        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )

        return table

    # =====================================================
    # FOOTER CONTENT (3 COLUMNS)
    # =====================================================

    left_column = [
        create_icon_text_table("phone.png", mobile, 126),
        Spacer(1, 8),
        create_icon_text_table("email.png", email, 126),
    ]

    center_column = [
        create_icon_text_table("instagram.png", instagram_handle, 126),
        Spacer(1, 8),
        create_icon_text_table("whatsapp.png", whatsapp_number, 126),
    ]

    right_column = [
        create_icon_text_table("location.png", address, 140),
        Spacer(1, 8),
        create_icon_text_table("website.png", website, 140),
    ]

    # =====================================================
    # MAIN FOOTER TABLE
    # =====================================================

    footer_data = [
        [
            left_column,
            center_column,
            right_column,
        ]
    ]

    footer_table = Table(
        footer_data,
        colWidths=[180, 180, 195],
    )

    footer_table.setStyle(
        TableStyle(
            [
                # Background color
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#D9DDE3")),
                # Green top border
                ("LINEABOVE", (0, 0), (-1, 0), 4, colors.HexColor("#4CAF50")),
                # Alignment
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                # Padding
                ("LEFTPADDING", (0, 0), (-1, -1), 15),
                ("RIGHTPADDING", (0, 0), (-1, -1), 15),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )

    # =====================================================
    # DRAW FIXED HEADER & FOOTER ON EVERY PAGE
    # =====================================================

    def draw_header_footer(canvas, doc):
        """
        Draw header and footer at fixed positions on every page.
        """
        canvas.saveState()

        # Draw Header
        header_table.wrapOn(canvas, doc.width, doc.topMargin)
        header_table.drawOn(canvas, doc.leftMargin, A4[1] - 90)

        # Draw Footer
        x = doc.leftMargin
        y = 15
        footer_table.wrapOn(canvas, doc.width, 80)
        footer_table.drawOn(canvas, x, y)

        canvas.restoreState()

    # =====================================================
    # BUILD PDF
    # =====================================================

    doc.build(
        elements,
        onFirstPage=draw_header_footer,
        onLaterPages=draw_header_footer,
    )

    buffer.seek(0)

    return buffer

# =====================================================
# CREATE QUOTATION
# =====================================================


@router.post("/", response_model=s.QuotationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_quotation(
    payload: s.CreateQuotation,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    try:

        # =====================================================
        # VALIDATE CLIENT
        # =====================================================

        client = await db.get(User, payload.client_user_id)

        if not client:
            raise HTTPException(
                status_code=404,
                detail="Client not found."
            )

        if client.role != UserRole.CLIENT:
            raise HTTPException(
                status_code=400,
                detail="Selected user is not a Client."
            )

        if not client.is_active:
            raise HTTPException(
                status_code=400,
                detail="Selected client is inactive."
            )

        # =====================================================
        # ALLOW ONLY ONE ACTIVE QUOTATION PER CLIENT
        # =====================================================

        existing_quotation = await db.scalar(
            select(QuotationMaster).where(
                QuotationMaster.client_user_id == payload.client_user_id,
                QuotationMaster.status.in_(
                    [
                        QuotationStatus.DRAFT,
                        QuotationStatus.SENT,
                        QuotationStatus.APPROVED,
                    ]
                ),
            )
        )

        if existing_quotation:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Client already has an active quotation "
                    f"({existing_quotation.quotation_no}). "
                    "Reject or delete it before creating another quotation."
                ),
            )

        # =====================================================
        # VALIDATE ITEMS
        # =====================================================

        if not payload.items:
            raise HTTPException(
                status_code=400,
                detail="Quotation must contain at least one item."
            )

        # =====================================================
        # VALIDATE PAYMENT MODE
        # =====================================================

        payment_mode = None

        if payload.payment_mode:

            payment_mode = payload.payment_mode.strip().upper()

            if payment_mode not in ["BANK", "UPI", "CASH"]:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid payment mode."
                )

            if payment_mode == "BANK":

                required_fields = [
                    payload.bank_name,
                    payload.account_holder_name,
                    payload.account_number,
                    payload.ifsc_code,
                ]

                if any(not value for value in required_fields):
                    raise HTTPException(
                        status_code=400,
                        detail="Bank details are required for BANK payment mode."
                    )

            elif payment_mode == "UPI":

                if not payload.upi_id:
                    raise HTTPException(
                        status_code=400,
                        detail="UPI ID is required for UPI payment mode."
                    )

        # =====================================================
        # VALIDATE GST PERCENTAGES
        # =====================================================

        if not 0 <= payload.gst_percent <= 100:
            raise HTTPException(
                status_code=400,
                detail="GST percent must be between 0 and 100."
            )

        if not 0 <= payload.cgst_percent <= 100:
            raise HTTPException(
                status_code=400,
                detail="CGST percent must be between 0 and 100."
            )

        if not 0 <= payload.sgst_percent <= 100:
            raise HTTPException(
                status_code=400,
                detail="SGST percent must be between 0 and 100."
            )

        if not 0 <= payload.tds_percent <= 100:
            raise HTTPException(
                status_code=400,
                detail="TDS percent must be between 0 and 100."
            )

        if (
            payload.gst_percent
            != payload.cgst_percent + payload.sgst_percent
        ):
            raise HTTPException(
                status_code=400,
                detail="GST must be equal to CGST + SGST."
            )

        # =====================================================
        # VALIDATE PROJECT DATES
        # =====================================================

        if (
            payload.project_start_date
            and payload.project_end_date
            and payload.project_end_date < payload.project_start_date
        ):
            raise HTTPException(
                status_code=400,
                detail="Project end date cannot be before project start date."
            )

        # =====================================================
        # VALIDATE DUE DATE
        # =====================================================

        if (
            payload.project_start_date
            and payload.due_date
            and payload.due_date < payload.project_start_date
        ):
            raise HTTPException(
                status_code=400,
                detail="Due date cannot be before project start date."
            )

        # =====================================================
        # VALIDATE LABOUR IDs
        # =====================================================

        for labour_data in payload.labour_items:

            if labour_data.labour_count <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Labour count must be greater than zero."
                )

            if labour_data.daily_wage <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Daily wage must be greater than zero."
                )

            if labour_data.labour_days <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Labour days must be greater than zero."
                )

            if labour_data.labour_id:

                labour = await db.get(
                    Labour,
                    labour_data.labour_id,
                )

                if not labour:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Labour ID {labour_data.labour_id} not found."
                    )

        # =====================================================
        # VALIDATE MATERIAL IDs
        # =====================================================

        for material_data in payload.material_items:

            if material_data.estimated_quantity <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Material quantity must be greater than zero."
                )

            if material_data.estimated_rate <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Material rate must be greater than zero."
                )

            if material_data.material_id:

                material = await db.get(
                    Material,
                    material_data.material_id,
                )

                if not material:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Material ID {material_data.material_id} not found."
                    )

        # =====================================================
        # VALIDATE EQUIPMENT IDs
        # =====================================================

        for extra_data in payload.extra_charge_items:

            if extra_data.quantity <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Extra charge quantity must be greater than zero."
                )

            if extra_data.rate <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Extra charge rate must be greater than zero."
                )

            if extra_data.equipment_id:

                equipment = await db.get(
                    Equipment,
                    extra_data.equipment_id,
                )

                if not equipment:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Equipment ID {extra_data.equipment_id} not found."
                    )

        # =====================================================
        # GENERATE QUOTATION NUMBER
        # =====================================================

        quotation_no = await generate_quotation_no(db)

                # =====================================================
        # CREATE QUOTATION MASTER
        # =====================================================

        quotation = QuotationMaster(

            quotation_no=quotation_no,
            company_id=current_user.company_id,

            # CLIENT
            client_user_id=payload.client_user_id,
            client_name=payload.client_name,
            company_name=payload.company_name,
            mobile_number=payload.mobile_number,
            email=payload.email,
            billing_address=payload.billing_address,
            site_address=payload.site_address,
            gst_number=payload.gst_number,

            # PROJECT
            project_name=payload.project_name,
            project_type=payload.project_type,
            project_start_date=payload.project_start_date,
            project_end_date=payload.project_end_date,
            engineer_name=payload.engineer_name,
            work_order_no=payload.work_order_no,

            # TAX
            gst_percent=payload.gst_percent,
            cgst_percent=payload.cgst_percent,
            sgst_percent=payload.sgst_percent,
            tds_percent=payload.tds_percent,
            discount_amount=payload.discount_amount,
            advance_paid=payload.advance_paid,

            # PAYMENT
            payment_mode=payment_mode,
            upi_id=payload.upi_id,
            bank_name=payload.bank_name,
            account_holder_name=payload.account_holder_name,
            account_number=payload.account_number,
            ifsc_code=payload.ifsc_code,
            due_date=payload.due_date,

            # EXTRA
            notes=payload.notes,
            terms_conditions=payload.terms_conditions,
        )

        db.add(quotation)

        # =====================================================
        # CREATE QUOTATION ITEMS
        # =====================================================

        for item_data in payload.items:

            if item_data.rate <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Rate must be greater than zero for '{item_data.title}'."
                )

            if not item_data.measurements:
                raise HTTPException(
                    status_code=400,
                    detail=f"Measurements are required for '{item_data.title}'."
                )

            item = QuotationItem(
                quotation=quotation,
                item_type=item_data.item_type,
                title=item_data.title,
                description=item_data.description,
                unit=item_data.unit,
                rate=item_data.rate,
            )

            db.add(item)

            total_quantity = 0.0
            total_amount = 0.0

            # =================================================
            # CREATE MEASUREMENTS
            # =================================================

            for measurement_data in item_data.measurements:

                if (
                    (measurement_data.length or 0) <= 0
                    or (measurement_data.width or 0) <= 0
                    or (measurement_data.height or 0) <= 0
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Invalid measurement values for "
                            f"'{item_data.title}'."
                        ),
                    )

                result = calculate_item(
                    unit=item_data.unit,
                    length=measurement_data.length,
                    width=measurement_data.width,
                    height=measurement_data.height,
                    rate=item_data.rate,
                )

                measurement = MeasurementDetail(
                    quotation_item=item,
                    length=measurement_data.length,
                    width=measurement_data.width,
                    height=measurement_data.height,
                    unit=measurement_data.unit,
                    cubic_feet=result["cubic_feet"],
                    cubic_meter=result["cubic_meter"],
                    brass=result["brass"],
                    quantity=result["quantity"],
                    formula_used=result["formula"],
                )

                db.add(measurement)

                total_quantity += result["quantity"]
                total_amount += result["amount"]

            item.quantity = round(total_quantity, 2)
            item.amount = round(total_amount, 2)

        # =====================================================
        # FLUSH MASTER + ITEMS
        # =====================================================

        await db.flush()

                # =====================================================
        # CREATE LABOUR ITEMS
        # =====================================================

        for labour_data in payload.labour_items:

            amount = calculate_labour_amount(
                labour_count=labour_data.labour_count,
                daily_wage=labour_data.daily_wage,
                labour_days=labour_data.labour_days,
                overtime_hours=labour_data.overtime_hours,
                overtime_rate=labour_data.overtime_rate,
            )

            labour_item = QuotationLabour(
                quotation=quotation,
                labour_id=labour_data.labour_id,
                skill_type=labour_data.skill_type,
                labour_count=labour_data.labour_count,
                daily_wage=labour_data.daily_wage,
                labour_days=labour_data.labour_days,
                overtime_hours=labour_data.overtime_hours,
                overtime_rate=labour_data.overtime_rate,
                amount=amount,
                notes=labour_data.notes,
            )

            db.add(labour_item)

        # =====================================================
        # CREATE MATERIAL ITEMS
        # =====================================================

        for material_data in payload.material_items:

            estimated_amount = round(
                material_data.estimated_quantity
                * material_data.estimated_rate,
                2,
            )

            material_item = QuotationMaterial(
                quotation=quotation,
                material_id=material_data.material_id,
                material_name=material_data.material_name,
                category=material_data.category,
                unit=material_data.unit,
                estimated_quantity=material_data.estimated_quantity,
                estimated_rate=material_data.estimated_rate,
                estimated_amount=estimated_amount,
                notes=material_data.notes,
            )

            db.add(material_item)

        # =====================================================
        # CREATE EXTRA CHARGES
        # =====================================================

        for extra_data in payload.extra_charge_items:

            amount = round(
                extra_data.quantity * extra_data.rate,
                2,
            )

            extra_charge = QuotationExtraCharge(
                quotation=quotation,
                equipment_id=extra_data.equipment_id,
                expense_type=extra_data.expense_type,
                description=extra_data.description,
                quantity=extra_data.quantity,
                rate=extra_data.rate,
                amount=amount,
                notes=extra_data.notes,
            )

            db.add(extra_charge)

        # =====================================================
        # SAVE ALL CHILD RECORDS
        # =====================================================

        await db.flush()

        # =====================================================
        # LOAD RELATIONSHIPS
        # =====================================================

        await db.refresh(
            quotation,
            attribute_names=[
                "items",
                "labour_items",
                "material_items",
                "extra_charge_items",
            ],
        )

        # =====================================================
        # CALCULATE TOTALS
        # =====================================================

        calculate_quotation_totals(quotation)

        # =====================================================
        # VALIDATE TOTALS
        # =====================================================

        if quotation.discount_amount < 0:
            raise HTTPException(
                status_code=400,
                detail="Discount amount cannot be negative."
            )

        if quotation.discount_amount > quotation.subtotal:
            raise HTTPException(
                status_code=400,
                detail="Discount amount cannot exceed subtotal."
            )

        if quotation.advance_paid < 0:
            raise HTTPException(
                status_code=400,
                detail="Advance amount cannot be negative."
            )

        if quotation.advance_paid > quotation.grand_total:
            raise HTTPException(
                status_code=400,
                detail="Advance paid cannot exceed grand total."
            )

        quotation.balance_due = round(
            quotation.grand_total - quotation.advance_paid,
            2,
        )

        # =====================================================
        # SAVE UPDATED TOTALS
        # =====================================================

        await db.flush()
                # =====================================================
        # ACTIVITY LOG
        # =====================================================

        db.add(
            ActivityLog(
                action="CREATE_QUOTATION",
                entity="quotation",
                entity_id=quotation.id,
                performed_by=current_user.id,
                details={
                    "quotation_no": quotation.quotation_no,
                    "client_id": quotation.client_user_id,
                    "client_name": quotation.client_name,
                    "status": quotation.status.value,
                    "subtotal": quotation.subtotal,
                    "grand_total": quotation.grand_total,
                },
            )
        )

        # =====================================================
        # SAVE ACTIVITY LOG
        # =====================================================

        await db.flush()

        # =====================================================
        # COMMIT TRANSACTION
        # =====================================================

        await db.commit()

        # =====================================================
        # REFRESH QUOTATION
        # =====================================================

        await db.refresh(quotation)

        # =====================================================
        # RETURN COMPLETE OBJECT
        # =====================================================

        return await get_quotation_or_404(
            quotation.id,
            db,
            current_user,
        )

    # =====================================================
    # HTTP EXCEPTION
    # =====================================================

    except HTTPException:
        await db.rollback()
        raise

    # =====================================================
    # DATABASE ERROR
    # =====================================================

    except IntegrityError as e:
        await db.rollback()

        logger.exception("Integrity error while creating quotation")

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Database integrity error. Duplicate or invalid reference detected.",
        )

    # =====================================================
    # UNEXPECTED ERROR
    # =====================================================

    except Exception as e:
        await db.rollback()

        logger.exception(
            "Unexpected error while creating quotation",
            exc_info=True,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create quotation. Please try again later.",
        )


# =========================================================
# LIST QUOTATIONS
# =========================================================


@router.get("/", response_model=list[s.QuotationOut])
async def list_quotations(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    query = select(QuotationMaster).options(
        selectinload(QuotationMaster.items).selectinload(QuotationItem.measurements),
        selectinload(QuotationMaster.labour_items),
        selectinload(QuotationMaster.material_items),
        selectinload(QuotationMaster.extra_charge_items),
    )

    if current_user.role == UserRole.CLIENT:
        query = query.where(QuotationMaster.client_user_id == current_user.id)
    elif current_user.company_id:
        query = query.where(QuotationMaster.company_id == current_user.company_id)
    else:
        return []

    if project_id:
        query = query.where(QuotationMaster.project_id == project_id)

    query = query.order_by(QuotationMaster.id.desc()).offset(skip).limit(limit)

    result = await db.execute(query)

    return result.scalars().unique().all()


# =========================================================
# GET QUOTATION
# =========================================================


@router.get("/{quotation_id}", response_model=s.QuotationOut)
async def get_quotation(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    return await get_quotation_or_404(quotation_id, db, current_user)


# =========================================================
# UPDATE QUOTATION
# =========================================================


@router.put("/{quotation_id}", response_model=s.QuotationOut)
async def update_quotation(
    quotation_id: int,
    payload: s.UpdateQuotation,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    # =====================================================
    # ONLY DRAFT QUOTATION CAN BE UPDATED
    # =====================================================

    if quotation.status != QuotationStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail="Only draft quotations can be edited."
        )

    update_data = payload.model_dump(exclude_unset=True)

    # =====================================================
    # CROSS-FIELD DATE VALIDATION AGAINST STORED VALUES
    # (Pydantic can only validate the payload in isolation,
    #  a partial update needs to be checked against the
    #  values that will actually end up persisted)
    # =====================================================

    new_start = update_data.get("project_start_date", quotation.project_start_date)
    new_end = update_data.get("project_end_date", quotation.project_end_date)

    if new_start and new_end and new_end < new_start:
        raise HTTPException(
            status_code=400,
            detail="Project end date cannot be before start date.",
        )

    for key, value in update_data.items():
        setattr(quotation, key, value)

    calculate_quotation_totals(quotation)

    db.add(
        ActivityLog(
            action="UPDATE_QUOTATION",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={"updated_fields": list(update_data.keys())},
        )
    )

    await db.commit()
    await db.refresh(quotation)

    return await get_quotation_or_404(quotation_id, db, current_user)


# =========================================================
# DELETE QUOTATION
# =========================================================


@router.delete("/{quotation_id}")
async def delete_quotation(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be deleted")

    db.add(
        ActivityLog(
            action="DELETE_QUOTATION",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={"quotation_no": quotation.quotation_no},
        )
    )

    await db.delete(quotation)

    await db.commit()

    return {"message": "Quotation deleted successfully"}


# =========================================================
# ADD ITEM
# =========================================================


@router.post("/{quotation_id}/items", response_model=s.QuotationOut)
async def add_quotation_item(
    quotation_id: int,
    payload: s.QuotationItemCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Approved quotation cannot be modified"
        )

    # =====================================================
    # CREATE ITEM
    # =====================================================

    item = QuotationItem(
        quotation=quotation,
        item_type=payload.item_type,
        title=payload.title,
        description=payload.description,
        unit=payload.unit,
        rate=payload.rate,
    )

    db.add(item)

    total_quantity = 0
    total_amount = 0

    # =====================================================
    # MEASUREMENTS
    # =====================================================

    for m in payload.measurements:

        result = calculate_item(
            unit=payload.unit,
            length=m.length or 0,
            width=m.width or 0,
            height=m.height or 0,
            rate=payload.rate,
        )

        measurement = MeasurementDetail(
            quotation_item=item,
            length=m.length,
            width=m.width,
            height=m.height,
            unit=m.unit,
            cubic_feet=result["cubic_feet"],
            cubic_meter=result["cubic_meter"],
            brass=result["brass"],
            quantity=result["quantity"],
            formula_used=result["formula"],
        )

        db.add(measurement)

        total_quantity += result["quantity"]
        total_amount += result["amount"]

    # =====================================================
    # ITEM TOTALS
    # =====================================================

    item.quantity = round(total_quantity, 2)
    item.amount = round(total_amount, 2)

    # =====================================================
    # SAVE ITEM
    # =====================================================

    await db.flush()

    calculate_quotation_totals(quotation)

    db.add(
        ActivityLog(
            action="ADD_QUOTATION_ITEM",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={
                "item_title": item.title,
                "quotation_no": quotation.quotation_no,
            },
        )
    )

    await db.commit()

    await db.refresh(quotation)

    return await get_quotation_or_404(quotation.id, db, current_user)


# =========================================================
# UPDATE ITEM
# =========================================================


@router.put("/quotation-items/{item_id}", response_model=s.QuotationOut)
async def update_quotation_item(
    item_id: int,
    payload: s.QuotationItemUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    result = await db.execute(
        select(QuotationItem)
        .options(selectinload(QuotationItem.measurements))
        .where(QuotationItem.id == item_id)
    )

    item = result.scalars().first()

    if not item:
        raise HTTPException(status_code=404, detail="Quotation item not found")

    quotation = await get_quotation_or_404(item.quotation_id, db, current_user)

    # =====================================================
    # ONLY DRAFT QUOTATION CAN BE MODIFIED
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Approved quotation cannot be modified",
        )

    update_data = payload.model_dump(exclude_unset=True)

    unit_changed = (
        "unit" in update_data
        and update_data["unit"] != item.unit
    )

    # =====================================================
    # UPDATE NORMAL FIELDS
    # =====================================================

    for key, value in update_data.items():
        if key != "measurements":
            setattr(item, key, value)

    # =====================================================
    # UPDATE MEASUREMENTS
    # =====================================================

    if payload.measurements is not None:

        # delete old measurements
        for old in item.measurements:
            await db.delete(old)

        await db.flush()

        total_quantity = 0
        total_amount = 0

        for m in payload.measurements:

            result = calculate_item(
                unit=item.unit,
                length=m.length or 0,
                width=m.width or 0,
                height=m.height or 0,
                rate=item.rate,
            )

            measurement = MeasurementDetail(
                quotation_item=item,
                length=m.length,
                width=m.width,
                height=m.height,
                unit=m.unit,
                cubic_feet=result["cubic_feet"],
                cubic_meter=result["cubic_meter"],
                brass=result["brass"],
                quantity=result["quantity"],
                formula_used=result["formula"],
            )

            db.add(measurement)

            total_quantity += result["quantity"]
            total_amount += result["amount"]

        item.quantity = round(total_quantity, 2)
        item.amount = round(total_amount, 2)

    # =====================================================
    # UNIT CHANGED
    # =====================================================

    elif unit_changed:

        if not item.measurements:
            raise HTTPException(
                status_code=400,
                detail="Cannot change unit without measurements.",
            )

        total_quantity = 0
        total_amount = 0

        for existing in item.measurements:

            result = calculate_item(
                unit=item.unit,
                length=existing.length or 0,
                width=existing.width or 0,
                height=existing.height or 0,
                rate=item.rate,
            )

            existing.cubic_feet = result["cubic_feet"]
            existing.cubic_meter = result["cubic_meter"]
            existing.brass = result["brass"]
            existing.quantity = result["quantity"]
            existing.formula_used = result["formula"]

            total_quantity += result["quantity"]
            total_amount += result["amount"]

        item.quantity = round(total_quantity, 2)
        item.amount = round(total_amount, 2)

    # =====================================================
    # ONLY RATE UPDATED
    # =====================================================

    elif "rate" in update_data:

        item.amount = round(item.quantity * item.rate, 2)

    # =====================================================
    # RECALCULATE QUOTATION TOTALS
    # =====================================================

    await db.flush()

    calculate_quotation_totals(quotation)

    db.add(
        ActivityLog(
            action="UPDATE_QUOTATION_ITEM",
            entity="quotation_item",
            entity_id=item.id,
            performed_by=current_user.id,
            details={
                "quotation_id": quotation.id,
                "item_title": item.title,
            },
        )
    )

    await db.commit()

    await db.refresh(quotation)

    return await get_quotation_or_404(quotation.id, db, current_user)

# =========================================================
# DELETE ITEM
# =========================================================


@router.delete("/quotation-items/{item_id}")
async def delete_quotation_item(
    item_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    result = await db.execute(select(QuotationItem).where(QuotationItem.id == item_id))

    item = result.scalars().first()

    if not item:
        raise HTTPException(404, "Quotation item not found")

    quotation = await get_quotation_or_404(item.quotation_id, db, current_user)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    # Unlink from the already-loaded collection first so the total
    # recalculated below doesn't still include this item's amount.
    if item in quotation.items:
        quotation.items.remove(item)

    await db.delete(item)

    await db.flush()

    # =====================================================
    # RECALCULATE TOTALS
    # =====================================================

    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Quotation item deleted successfully"}


# =========================================================
# PREVIEW
# =========================================================


@router.get("/{quotation_id}/preview", response_model=s.QuotationOut)
async def preview_quotation(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    return await get_quotation_or_404(quotation_id, db, current_user)


# =========================================================
# APPROVE
# =========================================================


@router.put("/{quotation_id}/approve")
async def approve_quotation(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    # Already approved
    if quotation.is_approved:
        raise HTTPException(status_code=400, detail="Quotation is already approved")

    # Quotation must be sent before approval
    if quotation.status != QuotationStatus.SENT:
        raise HTTPException(
            status_code=400,
            detail="Quotation must be sent to the client before approval",
        )

    quotation.is_approved = True
    quotation.approved_at = datetime.utcnow()
    quotation.status = QuotationStatus.APPROVED

    db.add(
        ActivityLog(
            action="APPROVE_QUOTATION",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={"message": f"Quotation {quotation.quotation_no} approved"},
        )
    )

    await db.commit()

    return {
        "message": "Quotation approved successfully",
        "quotation_id": quotation.id,
        "status": quotation.status,
    }


# =========================================================
# REJECT
# =========================================================


@router.put("/{quotation_id}/reject")
async def reject_quotation(
    quotation_id: int,
    payload: s.RejectQuotation,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    # Quotation must be sent first
    if quotation.status == QuotationStatus.DRAFT:
        raise HTTPException(
            status_code=400,
            detail="Please send the quotation to the client before rejecting it.",
        )

    # Already approved
    if quotation.status == QuotationStatus.APPROVED:
        raise HTTPException(
            status_code=400, detail="Approved quotation cannot be rejected."
        )

    # Already rejected
    if quotation.status == QuotationStatus.REJECTED:
        raise HTTPException(status_code=400, detail="Quotation is already rejected.")

    # Already converted - this is a terminal state and must not be reopened
    if quotation.status == QuotationStatus.CONVERTED:
        raise HTTPException(
            status_code=400, detail="Converted quotation cannot be rejected."
        )

    quotation.status = QuotationStatus.REJECTED
    quotation.rejected_reason = payload.reason

    db.add(
        ActivityLog(
            action="REJECT_QUOTATION",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={"reason": payload.reason},
        )
    )

    await db.commit()

    return {"message": "Quotation rejected successfully"}


# =========================================================
# CONVERT TO BILL
# =========================================================


@router.post("/{quotation_id}/convert-to-bill")
async def convert_to_bill(
    quotation_id: int,
    project_id: int,  # Required query parameter
    contractor_id: int,  # Required query parameter
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    # GET QUOTATION AND LOCK THE ROW so a concurrent conversion
    # request can't pass the converted_to_bill check before this
    # transaction commits (reduces, though without a DB-level
    # unique constraint on RABill.quotation_id cannot fully close,
    # the double-conversion race).

    lock_result = await db.execute(
        select(QuotationMaster)
        .where(QuotationMaster.id == quotation_id)
        .with_for_update()
    )
    locked = lock_result.scalars().first()
    if not locked:
        raise HTTPException(404, "Quotation not found")

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    # APPROVAL CHECK

    if not quotation.is_approved:
        raise HTTPException(400, "Quotation must be approved first")

    # DUPLICATE CONVERSION CHECK

    if quotation.converted_to_bill:
        raise HTTPException(400, "Already converted to bill")

    # VALIDATE PROJECT

    project = await db.get(Project, project_id)

    if not project:
        raise HTTPException(404, "Project not found")

    # VALIDATE CONTRACTOR

    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(404, "Contractor not found")

    # PREPARE AMOUNTS

    grand_total = Decimal(str(quotation.grand_total or 0))

    gst_percent = Decimal(str(quotation.gst_percent or 0))

    # CREATE RA BILL

    bill = RABill(
        # Link back to quotation
        quotation_id=quotation.id,
        # Required references selected by user
        project_id=project.id,
        contractor_id=contractor.id,
        # Optional work order linkage
        work_order_id=None,
        # Auto-generated bill number
        bill_number=f"BILL-{quotation.quotation_no}",
        # Description
        work_description=quotation.project_name,
        # Preserve quotation total
        quantity=Decimal("1"),
        rate=grand_total,
        # Financial values
        gross_amount=grand_total,
        deductions=Decimal("0"),
        net_amount=grand_total,
        gst_percent=gst_percent,
        total_amount=grand_total,
        # Bill metadata
        bill_date=date.today(),
        status="Draft",
    )

    db.add(bill)

    # Generate bill.id before commit
    await db.flush()

    # UPDATE QUOTATION

    # Save selected project for future reference
    quotation.project_id = project.id

    quotation.converted_to_bill = True
    quotation.status = QuotationStatus.CONVERTED

    db.add(
        ActivityLog(
            action="CONVERT_QUOTATION_TO_BILL",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={"bill_id": bill.id, "bill_number": bill.bill_number},
        )
    )

    await db.commit()

    return {
        "message": "Converted to bill successfully",
        "bill_id": bill.id,
        "bill_number": bill.bill_number,
        "project_id": project.id,
        "project_name": project.project_name,
        "contractor_id": contractor.id,
        "contractor_name": contractor.name,
    }


# =========================================================
# CONVERT TO WORK ORDER
# =========================================================


@router.post("/{quotation_id}/convert-to-work-order")
async def convert_to_work_order(
    quotation_id: int,
    project_id: int,  # Required query parameter
    contractor_id: int,  # Required query parameter
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    from decimal import Decimal

    from app.models.work_order import WorkOrder
    from app.models.contractor import Contractor
    from app.models.project import Project
    from app.utils.common import generate_business_id

    # =====================================================
    # LOCK THE QUOTATION ROW (see note in convert_to_bill)
    # =====================================================

    lock_result = await db.execute(
        select(QuotationMaster)
        .where(QuotationMaster.id == quotation_id)
        .with_for_update()
    )
    locked = lock_result.scalars().first()
    if not locked:
        raise HTTPException(404, "Quotation not found")

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    # =====================================================
    # APPROVAL CHECK
    # =====================================================

    if not quotation.is_approved:
        raise HTTPException(400, "Quotation must be approved first")

    # =====================================================
    # DUPLICATE CONVERSION CHECK
    # =====================================================

    if quotation.converted_to_work_order:
        raise HTTPException(400, "Already converted to work order")

    # =====================================================
    # VALIDATE PROJECT
    # =====================================================

    project = await db.get(Project, project_id)

    if not project:
        raise HTTPException(404, "Project not found")

    # =====================================================
    # VALIDATE CONTRACTOR
    # =====================================================

    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(404, "Contractor not found")

    # =====================================================
    # GENERATE WORK ORDER NUMBER
    # =====================================================

    work_order_number = await generate_business_id(
        db, WorkOrder, "work_order_number", "WO"
    )

    # =====================================================
    # CREATE WORK ORDER
    # =====================================================

    grand_total = Decimal(str(quotation.grand_total or 0))

    work_order = WorkOrder(
        quotation_id=quotation.id,
        project_id=project.id,
        contractor_id=contractor.id,
        work_order_number=work_order_number,
        work_description=(
            f"{quotation.project_name} " f"(From Quotation {quotation.quotation_no})"
        ),
        total_quantity=Decimal("1"),
        completed_quantity=Decimal("0"),
        rate=grand_total,
        total_amount=grand_total,
        status="Assigned",
    )

    db.add(work_order)

    # Generate ID
    await db.flush()

    # =====================================================
    # UPDATE QUOTATION
    # =====================================================

    # Save selected project to quotation for future reference
    quotation.project_id = project.id

    quotation.converted_to_work_order = True
    quotation.status = QuotationStatus.CONVERTED

    db.add(
        ActivityLog(
            action="CONVERT_QUOTATION_TO_WORK_ORDER",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={
                "work_order_id": work_order.id,
                "work_order_number": work_order.work_order_number,
            },
        )
    )

    await db.commit()

    return {
        "message": "Converted to work order successfully",
        "work_order_id": work_order.id,
        "work_order_number": work_order.work_order_number,
        "project_id": project.id,
        "project_name": project.project_name,
        "contractor_id": contractor.id,
        "contractor_name": contractor.name,
    }


# =========================================================
# ADD LABOUR ITEM
# =========================================================


@router.post("/{quotation_id}/labour", response_model=s.QuotationOut)
async def add_labour_item(
    quotation_id: int,
    payload: s.QuotationLabourCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    if quotation.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Approved quotation cannot be modified"
        )

    if payload.labour_id:
        labour = await db.get(Labour, payload.labour_id)

        if not labour:
            raise HTTPException(
                status_code=404,
                detail="Labour not found"
            )

    amount = calculate_labour_amount(
        labour_count=payload.labour_count,
        daily_wage=payload.daily_wage,
        labour_days=payload.labour_days,
        overtime_hours=payload.overtime_hours,
        overtime_rate=payload.overtime_rate,
    )

    labour_item = QuotationLabour(
        quotation_id=quotation.id,
        labour_id=payload.labour_id,
        skill_type=payload.skill_type,
        labour_count=payload.labour_count,
        daily_wage=payload.daily_wage,
        labour_days=payload.labour_days,
        overtime_hours=payload.overtime_hours,
        overtime_rate=payload.overtime_rate,
        amount=amount,
        notes=payload.notes,
    )

    db.add(labour_item)

    await db.flush()

    await db.refresh(quotation, attribute_names=["labour_items"])

    calculate_quotation_totals(quotation)

    db.add(
        ActivityLog(
            action="ADD_QUOTATION_LABOUR",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={
                "labour_item_id": labour_item.id,
                "skill_type": labour_item.skill_type,
            },
        )
    )

    await db.commit()

    await db.refresh(quotation)

    return await get_quotation_or_404(quotation.id, db, current_user)

# =========================================================
# UPDATE LABOUR ITEM
# =========================================================


@router.put("/labour/{labour_item_id}", response_model=s.QuotationOut)
async def update_labour_item(
    labour_item_id: int,
    payload: s.QuotationLabourUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    result = await db.execute(
        select(QuotationLabour).where(
            QuotationLabour.id == labour_item_id
        )
    )

    labour_item = result.scalars().first()

    if not labour_item:
        raise HTTPException(
            status_code=404,
            detail="Labour item not found"
        )

    quotation = await get_quotation_or_404(
        labour_item.quotation_id,
        db,
        current_user,
    )

    # =====================================================
    # APPROVED QUOTATION CANNOT BE MODIFIED
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Approved quotation cannot be modified"
        )

    # =====================================================
    # VALIDATE LABOUR ID IF UPDATED
    # =====================================================

    if (
        payload.labour_id is not None
        and payload.labour_id != labour_item.labour_id
    ):
        labour = await db.get(Labour, payload.labour_id)

        if not labour:
            raise HTTPException(
                status_code=404,
                detail="Labour not found"
            )

    # =====================================================
    # UPDATE FIELDS
    # =====================================================

    update_data = payload.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        setattr(labour_item, key, value)

    # =====================================================
    # RECALCULATE LABOUR AMOUNT
    # =====================================================

    labour_item.amount = calculate_labour_amount(
        labour_count=labour_item.labour_count,
        daily_wage=labour_item.daily_wage,
        labour_days=labour_item.labour_days,
        overtime_hours=labour_item.overtime_hours,
        overtime_rate=labour_item.overtime_rate,
    )

    await db.flush()

    await db.refresh(
        quotation,
        attribute_names=["labour_items"],
    )

    calculate_quotation_totals(quotation)

    db.add(
        ActivityLog(
            action="UPDATE_QUOTATION_LABOUR",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={
                "labour_item_id": labour_item.id,
                "skill_type": labour_item.skill_type,
            },
        )
    )

    await db.commit()

    await db.refresh(quotation)

    return await get_quotation_or_404(
        quotation.id,
        db,
        current_user,
    )

# =========================================================
# DELETE LABOUR ITEM
# =========================================================


@router.delete("/labour/{labour_item_id}")
async def delete_labour_item(
    labour_item_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    result = await db.execute(
        select(QuotationLabour).where(QuotationLabour.id == labour_item_id)
    )

    labour_item = result.scalars().first()

    if not labour_item:
        raise HTTPException(404, "Labour item not found")

    quotation = await get_quotation_or_404(labour_item.quotation_id, db, current_user)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    if labour_item in quotation.labour_items:
        quotation.labour_items.remove(labour_item)

    await db.delete(labour_item)

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Labour item deleted successfully"}


# =========================================================
# MATERIAL APIs
# =========================================================


@router.post("/{quotation_id}/materials", response_model=s.QuotationOut)
async def add_material_item(
    quotation_id: int,
    payload: s.QuotationMaterialCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Approved quotation cannot be modified"
        )

    # =====================================================
    # VALIDATE MATERIAL
    # =====================================================

    if payload.material_id:

        material = await db.get(Material, payload.material_id)

        if not material:
            raise HTTPException(
                status_code=404,
                detail="Material not found"
            )

    # =====================================================
    # CALCULATE AMOUNT
    # =====================================================

    estimated_amount = (
        payload.estimated_quantity * payload.estimated_rate
    )

    material_item = QuotationMaterial(
        quotation=quotation,
        material_id=payload.material_id,
        material_name=payload.material_name,
        category=payload.category,
        unit=payload.unit,
        estimated_quantity=payload.estimated_quantity,
        estimated_rate=payload.estimated_rate,
        estimated_amount=estimated_amount,
        notes=payload.notes,
    )

    # =====================================================
    # ADD TO SESSION (IMPORTANT)
    # =====================================================

    db.add(material_item)

    # =====================================================
    # SAVE
    # =====================================================

    await db.flush()

    calculate_quotation_totals(quotation)

    db.add(
        ActivityLog(
            action="ADD_QUOTATION_MATERIAL",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={
                "material_name": material_item.material_name
            },
        )
    )

    await db.commit()

    await db.refresh(
        quotation,
        attribute_names=[
            "items",
            "labour_items",
            "material_items",
            "extra_charge_items",
        ],
    )

    return await get_quotation_or_404(quotation.id, db, current_user)

# =========================================================
# UPDATE MATERIAL ITEM
# =========================================================


@router.put("/quotation-materials/{material_item_id}", response_model=s.QuotationOut)
async def update_material_item(
    material_item_id: int,
    payload: s.QuotationMaterialUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    result = await db.execute(
        select(QuotationMaterial).where(
            QuotationMaterial.id == material_item_id
        )
    )

    material_item = result.scalars().first()

    if not material_item:
        raise HTTPException(
            status_code=404,
            detail="Material item not found"
        )

    quotation = await get_quotation_or_404(material_item.quotation_id, db, current_user)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Approved quotation cannot be modified"
        )

    # =====================================================
    # VALIDATE MATERIAL ID (IF UPDATED)
    # =====================================================

    if payload.material_id:

        material = await db.get(Material, payload.material_id)

        if not material:
            raise HTTPException(
                status_code=404,
                detail="Material not found"
            )

    # =====================================================
    # UPDATE FIELDS
    # =====================================================

    update_data = payload.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        setattr(material_item, key, value)

    # =====================================================
    # RECALCULATE AMOUNT
    # =====================================================

    material_item.estimated_amount = round(
        material_item.estimated_quantity * material_item.estimated_rate,
        2,
    )

    # =====================================================
    # RECALCULATE QUOTATION TOTALS
    # =====================================================

    await db.flush()

    calculate_quotation_totals(quotation)

    db.add(
        ActivityLog(
            action="UPDATE_QUOTATION_MATERIAL",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={
                "material_item_id": material_item.id,
                "material_name": material_item.material_name,
            },
        )
    )

    await db.commit()

    await db.refresh(
        quotation,
        attribute_names=[
            "items",
            "labour_items",
            "material_items",
            "extra_charge_items",
        ],
    )

    return await get_quotation_or_404(quotation.id, db, current_user)

# =========================================================
# DELETE MATERIAL ITEM
# =========================================================


@router.delete("/quotation-materials/{material_item_id}")
async def delete_material_item(
    material_item_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    result = await db.execute(
        select(QuotationMaterial).where(QuotationMaterial.id == material_item_id)
    )

    material_item = result.scalars().first()

    if not material_item:
        raise HTTPException(404, "Material item not found")

    quotation = await get_quotation_or_404(material_item.quotation_id, db, current_user)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    if material_item in quotation.material_items:
        quotation.material_items.remove(material_item)

    await db.delete(material_item)

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Material item deleted successfully"}


# =========================================================
# LIST MATERIAL ITEMS
# =========================================================


@router.get("/{quotation_id}/materials")
async def list_material_items(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    return quotation.material_items


# =========================================================
# EXTRA CHARGE APIs
# =========================================================


@router.post("/{quotation_id}/extra-charges", response_model=s.QuotationOut)
async def add_extra_charge(
    quotation_id: int,
    payload: s.QuotationExtraChargeCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    if quotation.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Approved quotation cannot be modified",
        )

    # ================================================
    # Equipment Validation
    # ================================================

    if payload.equipment_id:

        equipment = await db.get(Equipment, payload.equipment_id)

        if not equipment:
            raise HTTPException(
                status_code=404,
                detail="Equipment not found",
            )

    amount = payload.quantity * payload.rate

    extra_charge = QuotationExtraCharge(
        quotation=quotation,
        equipment_id=payload.equipment_id,
        expense_type=payload.expense_type,
        description=payload.description,
        quantity=payload.quantity,
        rate=payload.rate,
        amount=amount,
        notes=payload.notes,
    )

    # IMPORTANT
    db.add(extra_charge)

    await db.flush()

    calculate_quotation_totals(quotation)

    await db.commit()

    await db.refresh(extra_charge)

    return await get_quotation_or_404(quotation_id, db, current_user)

# =========================================================
# UPDATE EXTRA CHARGE
# =========================================================


@router.put(
    "/quotation-extra-charges/{extra_charge_id}",
    response_model=s.QuotationOut,
)
async def update_extra_charge(
    extra_charge_id: int,
    payload: s.QuotationExtraChargeUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    result = await db.execute(
        select(QuotationExtraCharge).where(
            QuotationExtraCharge.id == extra_charge_id
        )
    )

    extra_charge = result.scalars().first()

    if not extra_charge:
        raise HTTPException(
            status_code=404,
            detail="Extra charge not found",
        )

    quotation = await get_quotation_or_404(extra_charge.quotation_id, db, current_user)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Approved quotation cannot be modified",
        )

    # =====================================================
    # EQUIPMENT VALIDATION
    # =====================================================

    if payload.equipment_id is not None:

        equipment = await db.get(Equipment, payload.equipment_id)

        if not equipment:
            raise HTTPException(
                status_code=404,
                detail="Equipment not found",
            )

    # =====================================================
    # UPDATE FIELDS
    # =====================================================

    update_data = payload.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        setattr(extra_charge, key, value)

    # =====================================================
    # RECALCULATE AMOUNT
    # =====================================================

    extra_charge.amount = (
        (extra_charge.quantity or 0)
        * (extra_charge.rate or 0)
    )

    await db.flush()

    calculate_quotation_totals(quotation)

    await db.commit()

    await db.refresh(extra_charge)

    return await get_quotation_or_404(quotation.id, db, current_user)


# =========================================================
# DELETE EXTRA CHARGE
# =========================================================


@router.delete("/quotation-extra-charges/{extra_charge_id}")
async def delete_extra_charge(
    extra_charge_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    result = await db.execute(
        select(QuotationExtraCharge).where(QuotationExtraCharge.id == extra_charge_id)
    )

    extra_charge = result.scalars().first()

    if not extra_charge:
        raise HTTPException(404, "Extra charge not found")

    quotation = await get_quotation_or_404(extra_charge.quotation_id, db, current_user)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    if extra_charge in quotation.extra_charge_items:
        quotation.extra_charge_items.remove(extra_charge)

    await db.delete(extra_charge)

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Extra charge deleted successfully"}


# =========================================================
# LIST EXTRA CHARGES
# =========================================================


@router.get("/{quotation_id}/extra-charges")
async def list_extra_charges(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    return quotation.extra_charge_items


# =========================================================
# PDF GENERATION
# =========================================================


@router.get("/{quotation_id}/pdf")
async def generate_pdf(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    result = await db.execute(select(CompanySettings))
    company_settings = result.scalars().first()

    pdf_buffer = generate_quotation_pdf(quotation, company_settings)

    safe_filename = quotation.quotation_no.replace("/", "-").replace("\\", "-")

    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}.pdf"'},
    )


# =========================================================
# CONVERT QUOTATION TO PROJECT
# =========================================================

from app.core.enums import ProjectStatus


@router.post(
    "/{quotation_id}/convert-to-project",
    response_model=s.QuotationToProjectConvertResponse,
)
async def convert_quotation_to_project(
    quotation_id: int,
    payload: s.QuotationToProjectConvertRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    # LOCK THE QUOTATION ROW (see note in convert_to_bill)
    lock_result = await db.execute(
        select(QuotationMaster)
        .where(QuotationMaster.id == quotation_id)
        .with_for_update()
    )
    locked = lock_result.scalars().first()
    if not locked:
        raise HTTPException(404, "Quotation not found")

    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    if not quotation.is_approved:
        raise HTTPException(
            status_code=400,
            detail="Quotation must be approved before converting to a project",
        )

    # Check if a project already exists for this quotation
    result = await db.execute(
        select(Project).where(Project.quotation_id == quotation_id)
    )
    existing_project = result.scalars().first()

    if existing_project:
        raise HTTPException(
            status_code=400,
            detail="A project has already been created for this quotation",
        )

    owner = await db.get(Owner, payload.owner_id)

    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    business_id = await generate_business_id(db, Project, "business_id", "PRJ")

    project = Project(
        business_id=business_id,
        company_id=quotation.company_id,
        project_name=quotation.project_name,
        type=quotation.project_type,
        site_address=quotation.site_address,
        start_date=quotation.project_start_date,
        end_date=quotation.project_end_date,
        budget_amount=quotation.grand_total,
        quotation_id=quotation.id,
        description=f"Created from quotation {quotation.quotation_no}",
        status=ProjectStatus.PLANNED,
        owner_id=payload.owner_id,
        location_type=payload.location_type,
        city=payload.city,
        state=payload.state,
        country=payload.country,
        pincode=payload.pincode,
        latitude=payload.latitude,
        longitude=payload.longitude,
        shift_start_time=payload.shift_start_time,
        shift_end_time=payload.shift_end_time,
        grace_period_minutes=payload.grace_period_minutes,
    )

    db.add(project)
    await db.flush()

    # TODO: Future BOQ Integration
    # Quotation -> Project -> BOQ -> Tasks
    quotation.project_id = project.id
    quotation.status = QuotationStatus.CONVERTED

    db.add(
        ActivityLog(
            action="CONVERT_QUOTATION_TO_PROJECT",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={"project_id": project.id, "project_business_id": project.business_id},
        )
    )

    await db.commit()
    await db.refresh(project)

    return s.QuotationToProjectConvertResponse(
        message="Project created successfully from quotation",
        project_id=project.id,
        project_business_id=project.business_id,
        quotation_id=quotation.id,
        budget_amount=float(project.budget_amount),
    )


# ============================================


@router.post("/{quotation_id}/send")
async def send_quotation(
    quotation_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    quotation = await get_quotation_or_404(quotation_id, db, current_user)

    # Client validation
    if not quotation.client_user_id:
        raise HTTPException(
            status_code=400,
            detail="Client is not assigned to this quotation."
        )

    # Already sent
    if quotation.status == QuotationStatus.SENT:
        raise HTTPException(
            status_code=400,
            detail="Quotation has already been sent."
        )

    # Already approved
    if quotation.status == QuotationStatus.APPROVED:
        raise HTTPException(
            status_code=400,
            detail="Approved quotation cannot be sent again."
        )

    # Already converted
    if quotation.status == QuotationStatus.CONVERTED:
        raise HTTPException(
            status_code=400,
            detail="Converted quotation cannot be sent."
        )

    # Re-send support (Rejected -> Sent)
    quotation.status = QuotationStatus.SENT
    quotation.sent_at = datetime.utcnow()
    quotation.rejected_reason = None

    notification = Notification(
        user_id=quotation.client_user_id,
        title="New Quotation Received",
        message=(
            f"Quotation {quotation.quotation_no} has been shared with you. "
            "Please review and approve or reject it."
        ),
        type="Quotation",
        link=f"/quotation/{quotation.id}",
    )

    db.add(notification)

    db.add(
        ActivityLog(
            action="SEND_QUOTATION",
            entity="quotation",
            entity_id=quotation.id,
            performed_by=current_user.id,
            details={
                "quotation_no": quotation.quotation_no,
                "client_name": quotation.client_name,
                "status": "Sent",
            },
        )
    )

    await db.commit()
    await db.refresh(quotation)

    return {
        "message": "Quotation sent successfully.",
    }