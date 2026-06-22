from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
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
from app.models.quotation import (
    QuotationExtraCharge,
    QuotationMaster,
    QuotationItem,
    MeasurementDetail,
    QuotationMaterial,
    QuotationStatus,
    QuotationLabour,
)

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
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported unit: {unit}"
        )

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


async def get_quotation_or_404(quotation_id: int, db: AsyncSession):

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
        topMargin=20,
        bottomMargin=110,   # Reserve space for fixed footer
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
    title_para = Paragraph(
        "<b>PROJECT QUOTATION</b>",
        title_style
    )

    # Create header table
    if logo_path:

        logo = Image(
            logo_path,
            width=80,
            height=80
        )

        header_table = Table(
            [
                [logo, title_para, ""]
            ],
            colWidths=[100, 350, 100]
        )

    else:

        # If no uploaded logo exists, show title only
        header_table = Table(
            [
                ["", title_para, ""]
            ],
            colWidths=[100, 350, 100]
        )

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

    elements.append(header_table)
    elements.append(Spacer(1, 15))
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
        KeepTogether([
            Paragraph("<b>Client Details</b>", styles["Heading2"]),
            Spacer(1, 6),
            create_styled_table(client_info, [150, 370]),
            Spacer(1, 15),
        ])
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
        KeepTogether([
            Paragraph("<b>Item Details</b>", styles["Heading2"]),
            Spacer(1, 6),
            create_styled_table(item_data, [180, 70, 70, 80, 90]),
            Spacer(1, 15),
        ])
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
        KeepTogether([
            Paragraph("<b>Labour Details</b>", styles["Heading2"]),
            Spacer(1, 6),
            create_styled_table(labour_data, [150, 80, 80, 100, 100]),
            Spacer(1, 15),
        ])
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
            KeepTogether([
                Paragraph("<b>Material Details</b>", styles["Heading2"]),
                Spacer(1, 6),
                create_styled_table(material_data, [180, 70, 70, 80, 90]),
                Spacer(1, 15),
            ])
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
            KeepTogether([
                Paragraph("<b>Extra Charges</b>", styles["Heading2"]),
                Spacer(1, 6),
                create_styled_table(extra_data, [220, 90, 90, 90]),
                Spacer(1, 15),
            ])
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
        KeepTogether([
            Paragraph("<b>Financial Summary</b>", styles["Heading2"]),
            Spacer(1, 6),
            create_styled_table(summary_data, [250, 150], highlight_last_row=True),
            Spacer(1, 20),
        ])
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
        else quotation.company_name
        or ""
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
        qr_drawing.hAlign = 'LEFT'
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
    line_table = Table([[""]], colWidths=[555]) # Full width line
    line_table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, -1), 0.5, colors.grey),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
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
        company_settings.email
        if company_settings and company_settings.email
        else "-"
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
            "..",      # app
            "..",      # project root
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
            img = Image(
                path,
                width=18,
                height=18
            )

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
    # DRAW FIXED FOOTER ON EVERY PAGE
    # =====================================================

    def draw_footer(canvas, doc):
        """
        Draw footer at fixed position at the bottom of every page.
        """
        canvas.saveState()

        # Position from bottom of page
        x = doc.leftMargin
        y = 15

        # Calculate size and draw
        footer_table.wrapOn(canvas, doc.width, 80)
        footer_table.drawOn(canvas, x, y)

        canvas.restoreState()

    # =====================================================
    # BUILD PDF
    # =====================================================

    doc.build(
        elements,
        onFirstPage=draw_footer,
        onLaterPages=draw_footer,
    )

    buffer.seek(0)

    return buffer


# =========================================================
# CREATE QUOTATION
# =========================================================


@router.post("/", response_model=s.QuotationOut)
async def create_quotation(
    payload: s.CreateQuotation, db: AsyncSession = Depends(get_db_session)
):

    quotation_no = await generate_quotation_no(db)

    quotation = QuotationMaster(
        quotation_no=quotation_no,
        # CLIENT DETAILS
        client_name=payload.client_name,
        company_name=payload.company_name,
        mobile_number=payload.mobile_number,
        email=payload.email,
        billing_address=payload.billing_address,
        site_address=payload.site_address,
        gst_number=payload.gst_number,
        # PROJECT DETAILS
        project_name=payload.project_name,
        project_type=payload.project_type,
        project_start_date=payload.project_start_date,
        project_end_date=payload.project_end_date,
        engineer_name=payload.engineer_name,
        work_order_no=payload.work_order_no,
        # TAX DETAILS
        gst_percent=payload.gst_percent,
        discount_amount=payload.discount_amount,
        advance_paid=payload.advance_paid,
        cgst_percent=payload.cgst_percent,
        sgst_percent=payload.sgst_percent,
        tds_percent=payload.tds_percent,
        # PAYMENT DETAILS
        payment_mode=payload.payment_mode,
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
    # QUOTATION ITEMS
    # =====================================================

    for item_data in payload.items:

        item = QuotationItem(
            quotation=quotation,
            item_type=item_data.item_type,
            title=item_data.title,
            description=item_data.description,
            unit=item_data.unit,
            rate=item_data.rate,
        )

        db.add(item)

        total_quantity = 0
        total_amount = 0

        # =================================================
        # MEASUREMENTS
        # =================================================

        for m in item_data.measurements:

            result = calculate_item(
                unit=item_data.unit,
                length=m.length or 0,
                width=m.width or 0,
                height=m.height or 0,
                rate=item_data.rate,
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
    # LABOUR ITEMS
    # =====================================================

    for labour_data in payload.labour_items:

        # ================================================
        # OPTIONAL LABOUR VALIDATION
        # ================================================

        if labour_data.labour_id:

            labour = await db.get(Labour, labour_data.labour_id)

            if not labour:
                raise HTTPException(404, "Labour not found")

        # ================================================
        # CALCULATE LABOUR AMOUNT
        # ================================================

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
    # MATERIAL ITEMS
    # =====================================================

    for material_data in payload.material_items:

        if material_data.material_id:

            material = await db.get(Material, material_data.material_id)

            if not material:
                raise HTTPException(404, "Material not found")

        estimated_amount = (
            material_data.estimated_quantity * material_data.estimated_rate
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
    # EXTRA CHARGE ITEMS
    # =====================================================

    for extra_data in payload.extra_charge_items:

        if extra_data.equipment_id:

            equipment = await db.get(Equipment, extra_data.equipment_id)

            if not equipment:
                raise HTTPException(404, "Equipment not found")

        amount = extra_data.quantity * extra_data.rate

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
    # FINAL TOTALS
    # =====================================================
    await db.flush()

    calculate_quotation_totals(quotation)

    await db.commit()

    return await get_quotation_or_404(quotation.id, db)


# =========================================================
# LIST QUOTATIONS
# =========================================================


@router.get("/", response_model=list[s.QuotationOut])
async def list_quotations(
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    db: AsyncSession = Depends(get_db_session)
):

    query = select(QuotationMaster).options(
        selectinload(QuotationMaster.items).selectinload(
            QuotationItem.measurements
        ),
        selectinload(QuotationMaster.labour_items),
        selectinload(QuotationMaster.material_items),
        selectinload(QuotationMaster.extra_charge_items),
    )
    
    if project_id:
        query = query.where(QuotationMaster.project_id == project_id)

    result = await db.execute(query)

    return result.scalars().unique().all()


# =========================================================
# GET QUOTATION
# =========================================================


@router.get("/{quotation_id}", response_model=s.QuotationOut)
async def get_quotation(quotation_id: int, db: AsyncSession = Depends(get_db_session)):

    return await get_quotation_or_404(quotation_id, db)


# =========================================================
# UPDATE QUOTATION
# =========================================================


@router.put("/{quotation_id}", response_model=s.QuotationOut)
async def update_quotation(
    quotation_id: int,
    payload: s.UpdateQuotation,
    db: AsyncSession = Depends(get_db_session),
):

    quotation = await get_quotation_or_404(quotation_id, db)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be edited")

    update_data = payload.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        setattr(quotation, key, value)

    calculate_quotation_totals(quotation)

    await db.commit()

    return await get_quotation_or_404(quotation_id, db)


# =========================================================
# DELETE QUOTATION
# =========================================================


@router.delete("/{quotation_id}")
async def delete_quotation(
    quotation_id: int, db: AsyncSession = Depends(get_db_session)
):

    quotation = await get_quotation_or_404(quotation_id, db)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be deleted")

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
):

    quotation = await get_quotation_or_404(quotation_id, db)

    item = QuotationItem(
        quotation_id=quotation.id,
        item_type=payload.item_type,
        title=payload.title,
        description=payload.description,
        unit=payload.unit,
        rate=payload.rate,
    )

    db.add(item)

    total_quantity = 0
    total_amount = 0

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

    item.quantity = round(total_quantity, 2)

    item.amount = round(total_amount, 2)

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return await get_quotation_or_404(quotation_id, db)


# =========================================================
# UPDATE ITEM
# =========================================================


@router.put("/quotation-items/{item_id}")
async def update_quotation_item(
    item_id: int,
    payload: s.QuotationItemUpdate,
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(QuotationItem)
        .options(selectinload(QuotationItem.measurements))
        .where(QuotationItem.id == item_id)
    )

    item = result.scalars().first()

    if not item:
        raise HTTPException(404, "Quotation item not found")

    quotation = await get_quotation_or_404(item.quotation_id, db)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    update_data = payload.model_dump(exclude_unset=True)

    for key, value in update_data.items():

        if key != "measurements":
            setattr(item, key, value)

    # =====================================================
    # RECALCULATE MEASUREMENTS
    # =====================================================

    if payload.measurements is not None:

        for old in item.measurements:
            await db.delete(old)

        await db.flush()

        total_quantity = 0
        total_amount = 0

        for m in payload.measurements:

            result = calculate_item(
                unit=payload.unit or item.unit,
                length=m.length or 0,
                width=m.width or 0,
                height=m.height or 0,
                rate=payload.rate or item.rate,
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

    else:

        # =================================================
        # RATE UPDATE ONLY
        # =================================================

        item.amount = round(item.quantity * item.rate, 2)

    # =====================================================
    # RECALCULATE QUOTATION TOTALS
    # =====================================================

    await db.flush()

    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Quotation item updated successfully"}


# =========================================================
# DELETE ITEM
# =========================================================


@router.delete("/quotation-items/{item_id}")
async def delete_quotation_item(
    item_id: int, db: AsyncSession = Depends(get_db_session)
):

    result = await db.execute(select(QuotationItem).where(QuotationItem.id == item_id))

    item = result.scalars().first()

    if not item:
        raise HTTPException(404, "Quotation item not found")

    quotation = await get_quotation_or_404(item.quotation_id, db)

    # =====================================================
    # APPROVED CHECK
    # =====================================================

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

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
    quotation_id: int, db: AsyncSession = Depends(get_db_session)
):

    return await get_quotation_or_404(quotation_id, db)


# =========================================================
# APPROVE
# =========================================================


@router.put("/{quotation_id}/approve")
async def approve_quotation(
    quotation_id: int, db: AsyncSession = Depends(get_db_session)
):

    quotation = await get_quotation_or_404(quotation_id, db)

    quotation.is_approved = True

    quotation.approved_at = datetime.utcnow()

    quotation.status = QuotationStatus.APPROVED

    await db.commit()

    return {"message": "Quotation approved"}


# =========================================================
# REJECT
# =========================================================


@router.put("/{quotation_id}/reject")
async def reject_quotation(
    quotation_id: int,
    payload: s.RejectQuotation,
    db: AsyncSession = Depends(get_db_session),
):

    quotation = await get_quotation_or_404(quotation_id, db)

    quotation.status = QuotationStatus.REJECTED

    quotation.rejected_reason = payload.reason

    await db.commit()

    return {"message": "Quotation rejected"}


# =========================================================
# CONVERT TO BILL
# =========================================================


@router.post("/{quotation_id}/convert-to-bill")
async def convert_to_bill(
    quotation_id: int,
    project_id: int,  # Required query parameter
    contractor_id: int,  # Required query parameter
    db: AsyncSession = Depends(get_db_session),
):

    # GET QUOTATION

    quotation = await get_quotation_or_404(quotation_id, db)

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
# CONVERT TO INVOICE
# =========================================================
# IMPORTANT:
# Do NOT use generate_business_id() here because your Invoice model
# does not have either `invoice_number` or `invoice_no`.
#
# Your actual Invoice model (used in app/api/invoice.py) relies on:
# - id (primary key)
# - project_id
# - owner_id
# - quotation_id
# - type
# - amount
# - gst_percent
# - gst_amount
# - tax_percent
# - tax_amount
# - total_amount
# - paid_amount
# - pending_amount
# - status
# - description
#
# So create the invoice exactly like /api/v1/invoices/from-quotation does.

from app.models.invoice import Invoice
from app.models.owner import Owner, OwnerTransaction
from app.models.project import Project
from decimal import Decimal


# @router.post("/{quotation_id}/convert-to-invoice")
# async def convert_to_invoice(
#     quotation_id: int,
#     db: AsyncSession = Depends(get_db_session)
# ):
#     # =====================================================
#     # 1. GET QUOTATION
#     # =====================================================
#     quotation = await get_quotation_or_404(quotation_id, db)

#     # =====================================================
#     # 2. APPROVAL CHECK
#     # =====================================================
#     if not quotation.is_approved:
#         raise HTTPException(400, "Quotation must be approved first")

#     # =====================================================
#     # 3. DUPLICATE CHECK
#     # =====================================================
#     if quotation.converted_to_invoice:
#         raise HTTPException(400, "Already converted to invoice")

#     # =====================================================
#     # 4. PROJECT CHECK
#     # =====================================================
#     if not quotation.project_id:
#         raise HTTPException(
#             400,
#             "Quotation is not linked to any project"
#         )

#     # =====================================================
#     # 5. LOAD PROJECT
#     # =====================================================
#     project = await db.get(Project, quotation.project_id)

#     if not project:
#         raise HTTPException(404, "Project not found")

#     # =====================================================
#     # 6. PREVENT DUPLICATE INVOICE
#     # =====================================================
#     existing_invoice = await db.scalar(
#         select(Invoice).where(
#             Invoice.quotation_id == quotation.id
#         )
#     )

#     if existing_invoice:
#         raise HTTPException(
#             400,
#             "Invoice already exists for this quotation"
#         )

#     # =====================================================
#     # 7. CALCULATE GST %
#     # =====================================================
#     gst_percent = Decimal(
#         (quotation.cgst_percent or 0)
#         + (quotation.sgst_percent or 0)
#     )

#     grand_total = Decimal(str(quotation.grand_total or 0))

#     # =====================================================
#     # 8. CREATE INVOICE
#     # =====================================================
#     invoice = Invoice(
#         project_id=quotation.project_id,
#         owner_id=project.owner_id,

#         quotation_id=quotation.id,

#         type="owner",
#         reference_id=quotation.id,

#         amount=Decimal(str(quotation.subtotal or 0)),

#         gst_percent=gst_percent,
#         gst_amount=Decimal(str(quotation.gst_amount or 0)),

#         tax_percent=Decimal(str(quotation.tds_percent or 0)),
#         tax_amount=Decimal(str(quotation.tds_amount or 0)),

#         total_amount=grand_total,

#         paid_amount=Decimal("0"),
#         pending_amount=grand_total,

#         status="pending",

#         description=(
#             f"Invoice generated from quotation "
#             f"{quotation.quotation_no}"
#         ),
#     )

#     db.add(invoice)

#     # Generate invoice.id
#     await db.flush()

#     # =====================================================
#     # 9. OWNER LEDGER ENTRY
#     # =====================================================
#     owner_txn = OwnerTransaction(
#         owner_id=project.owner_id,
#         project_id=quotation.project_id,
#         type="credit",
#         amount=grand_total,
#         reference_type="invoice",
#         reference_id=invoice.id,
#         description=(
#             f"Invoice generated from quotation "
#             f"{quotation.quotation_no}"
#         ),
#     )

#     db.add(owner_txn)

#     # =====================================================
#     # 10. UPDATE QUOTATION
#     # =====================================================
#     quotation.converted_to_invoice = True
#     quotation.status = QuotationStatus.CONVERTED

#     # =====================================================
#     # 11. SAVE
#     # =====================================================
#     await db.commit()

#     # Refresh invoice
#     await db.refresh(invoice)

#     # =====================================================
#     # 12. RESPONSE
#     # =====================================================
#     return {
#         "message": "Converted to invoice successfully",
#         "invoice_id": invoice.id,
#         "invoice_total": float(invoice.total_amount),
#         "invoice_status": invoice.status,
#     }

# =========================================================
# CONVERT TO WORK ORDER
# =========================================================


@router.post("/{quotation_id}/convert-to-work-order")
async def convert_to_work_order(
    quotation_id: int,
    project_id: int,  # Required query parameter
    contractor_id: int,  # Required query parameter
    db: AsyncSession = Depends(get_db_session),
):
    from decimal import Decimal

    from app.models.work_order import WorkOrder
    from app.models.contractor import Contractor
    from app.models.project import Project
    from app.utils.common import generate_business_id

    # =====================================================
    # GET QUOTATION
    # =====================================================

    quotation = await get_quotation_or_404(quotation_id, db)

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


@router.post("/{quotation_id}/labour")
async def add_labour_item(
    quotation_id: int,
    payload: s.QuotationLabourCreate,
    db: AsyncSession = Depends(get_db_session),
):

    quotation = await get_quotation_or_404(quotation_id, db)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    if payload.labour_id:

        labour = await db.get(Labour, payload.labour_id)

        if not labour:
            raise HTTPException(404, "Labour not found")

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
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Labour item added successfully"}


# =========================================================
# UPDATE LABOUR ITEM
# =========================================================


@router.put("/labour/{labour_item_id}")
async def update_labour_item(
    labour_item_id: int,
    payload: s.QuotationLabourUpdate,
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(QuotationLabour).where(QuotationLabour.id == labour_item_id)
    )

    labour_item = result.scalars().first()

    if not labour_item:
        raise HTTPException(404, "Labour item not found")

    quotation = await get_quotation_or_404(labour_item.quotation_id, db)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    update_data = payload.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        setattr(labour_item, key, value)

    labour_item.amount = calculate_labour_amount(
        labour_count=labour_item.labour_count,
        daily_wage=labour_item.daily_wage,
        labour_days=labour_item.labour_days,
        overtime_hours=labour_item.overtime_hours,
        overtime_rate=labour_item.overtime_rate,
    )

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Labour item updated successfully"}


# =========================================================
# DELETE LABOUR ITEM
# =========================================================


@router.delete("/labour/{labour_item_id}")
async def delete_labour_item(
    labour_item_id: int, db: AsyncSession = Depends(get_db_session)
):

    result = await db.execute(
        select(QuotationLabour).where(QuotationLabour.id == labour_item_id)
    )

    labour_item = result.scalars().first()

    if not labour_item:
        raise HTTPException(404, "Labour item not found")

    quotation = await get_quotation_or_404(labour_item.quotation_id, db)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    await db.delete(labour_item)

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Labour item deleted successfully"}


# =========================================================
# MATERIAL APIs
# =========================================================


@router.post("/{quotation_id}/materials")
async def add_material_item(
    quotation_id: int,
    payload: s.QuotationMaterialCreate,
    db: AsyncSession = Depends(get_db_session),
):

    quotation = await get_quotation_or_404(quotation_id, db)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    if payload.material_id:

        material = await db.get(Material, payload.material_id)

        if not material:
            raise HTTPException(404, "Material not found")

    estimated_amount = payload.estimated_quantity * payload.estimated_rate

    material_item = QuotationMaterial(
        quotation_id=quotation.id,
        material_id=payload.material_id,
        material_name=payload.material_name,
        category=payload.category,
        unit=payload.unit,
        estimated_quantity=payload.estimated_quantity,
        estimated_rate=payload.estimated_rate,
        estimated_amount=estimated_amount,
        notes=payload.notes,
    )

    db.add(material_item)

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Material item added successfully"}


# =========================================================
# UPDATE MATERIAL ITEM
# =========================================================


@router.put("/quotation-materials/{material_item_id}")
async def update_material_item(
    material_item_id: int,
    payload: s.QuotationMaterialUpdate,
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(QuotationMaterial).where(QuotationMaterial.id == material_item_id)
    )

    material_item = result.scalars().first()

    if not material_item:
        raise HTTPException(404, "Material item not found")

    quotation = await get_quotation_or_404(material_item.quotation_id, db)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    update_data = payload.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        setattr(material_item, key, value)

    material_item.estimated_amount = (
        material_item.estimated_quantity * material_item.estimated_rate
    )

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Material item updated successfully"}


# =========================================================
# DELETE MATERIAL ITEM
# =========================================================


@router.delete("/quotation-materials/{material_item_id}")
async def delete_material_item(
    material_item_id: int, db: AsyncSession = Depends(get_db_session)
):

    result = await db.execute(
        select(QuotationMaterial).where(QuotationMaterial.id == material_item_id)
    )

    material_item = result.scalars().first()

    if not material_item:
        raise HTTPException(404, "Material item not found")

    quotation = await get_quotation_or_404(material_item.quotation_id, db)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

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
    quotation_id: int, db: AsyncSession = Depends(get_db_session)
):

    quotation = await get_quotation_or_404(quotation_id, db)

    return quotation.material_items


# =========================================================
# EXTRA CHARGE APIs
# =========================================================


@router.post("/{quotation_id}/extra-charges")
async def add_extra_charge(
    quotation_id: int,
    payload: s.QuotationExtraChargeCreate,
    db: AsyncSession = Depends(get_db_session),
):

    quotation = await get_quotation_or_404(quotation_id, db)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    if payload.equipment_id:

        equipment = await db.get(Equipment, payload.equipment_id)

        if not equipment:
            raise HTTPException(404, "Equipment not found")

    amount = payload.quantity * payload.rate

    extra_charge = QuotationExtraCharge(
        quotation_id=quotation.id,
        equipment_id=payload.equipment_id,
        expense_type=payload.expense_type,
        description=payload.description,
        quantity=payload.quantity,
        rate=payload.rate,
        amount=amount,
        notes=payload.notes,
    )

    db.add(extra_charge)

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Extra charge added successfully"}


# =========================================================
# UPDATE EXTRA CHARGE
# =========================================================


@router.put("/quotation-extra-charges/{extra_charge_id}")
async def update_extra_charge(
    extra_charge_id: int,
    payload: s.QuotationExtraChargeUpdate,
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(QuotationExtraCharge).where(QuotationExtraCharge.id == extra_charge_id)
    )

    extra_charge = result.scalars().first()

    if not extra_charge:
        raise HTTPException(404, "Extra charge not found")

    quotation = await get_quotation_or_404(extra_charge.quotation_id, db)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

    update_data = payload.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        setattr(extra_charge, key, value)

    extra_charge.amount = extra_charge.quantity * extra_charge.rate

    await db.flush()
    calculate_quotation_totals(quotation)

    await db.commit()

    return {"message": "Extra charge updated successfully"}


# =========================================================
# DELETE EXTRA CHARGE
# =========================================================


@router.delete("/quotation-extra-charges/{extra_charge_id}")
async def delete_extra_charge(
    extra_charge_id: int, db: AsyncSession = Depends(get_db_session)
):

    result = await db.execute(
        select(QuotationExtraCharge).where(QuotationExtraCharge.id == extra_charge_id)
    )

    extra_charge = result.scalars().first()

    if not extra_charge:
        raise HTTPException(404, "Extra charge not found")

    quotation = await get_quotation_or_404(extra_charge.quotation_id, db)

    if quotation.is_approved:
        raise HTTPException(400, "Approved quotation cannot be modified")

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
    quotation_id: int, db: AsyncSession = Depends(get_db_session)
):

    quotation = await get_quotation_or_404(quotation_id, db)

    return quotation.extra_charge_items


# =========================================================
# PDF GENERATION
# =========================================================


@router.get("/{quotation_id}/pdf")
async def generate_pdf(quotation_id: int, db: AsyncSession = Depends(get_db_session)):
    quotation = await get_quotation_or_404(quotation_id, db)

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

@router.post("/{quotation_id}/convert-to-project", response_model=s.QuotationToProjectConvertResponse)
async def convert_quotation_to_project(
    quotation_id: int,
    payload: s.QuotationToProjectConvertRequest,
    db: AsyncSession = Depends(get_db_session)
):
    quotation = await get_quotation_or_404(quotation_id, db)

    if not quotation.is_approved:
        raise HTTPException(status_code=400, detail="Quotation must be approved before converting to a project")

    # Check if a project already exists for this quotation
    result = await db.execute(select(Project).where(Project.quotation_id == quotation_id))
    existing_project = result.scalars().first()

    if existing_project:
        raise HTTPException(status_code=400, detail="A project has already been created for this quotation")

    owner = await db.get(Owner, payload.owner_id)

    if not owner:
        raise HTTPException(
            status_code=404,
            detail="Owner not found"
        )

    business_id = await generate_business_id(db, Project, "business_id", "PRJ")

    project = Project(
        business_id=business_id,
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
    
    await db.commit()
    await db.refresh(project)

    return s.QuotationToProjectConvertResponse(
        message="Project created successfully from quotation",
        project_id=project.id,
        project_business_id=project.business_id,
        quotation_id=quotation.id,
        budget_amount=float(project.budget_amount)
    )
