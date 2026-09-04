from io import BytesIO
import os
from datetime import datetime

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    KeepTogether,
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors

from app.models.settings import CompanySettings


def create_styled_table(data, col_widths, highlight_last_row=False):
    table = Table(data, colWidths=col_widths)

    style = [
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

    for row in range(1, len(data)):
        if row % 2 == 0:
            style.append(("BACKGROUND", (0, row), (-1, row), colors.HexColor("#F2F6FA")))

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


def generate_dummy_quotation_pdf(
    data: dict, company_settings: CompanySettings | None = None
) -> BytesIO:
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20,
        rightMargin=20,
        topMargin=100,
        bottomMargin=110,
    )

    styles = getSampleStyleSheet()
    elements = []

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

    logo_path = None
    if company_settings and company_settings.company_logo:
        if os.path.exists(company_settings.company_logo):
            logo_path = company_settings.company_logo

    title_para = Paragraph("<b>DUMMY QUOTATION</b>", title_style)

    if logo_path:
        logo = Image(logo_path, width=80, height=80)
        header_table = Table([[logo, title_para, ""]], colWidths=[100, 350, 100])
    else:
        header_table = Table([["", title_para, ""]], colWidths=[100, 350, 100])

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

    # Company Details
    company_name = company_settings.company_name if company_settings else ""
    gst = company_settings.gst_number if company_settings else "-"
    mobile = company_settings.mobile_number if company_settings else "-"
    email = company_settings.email if company_settings else "-"

    company_details = f"""
    <b>{company_name}</b><br/>
    GST: {gst}<br/>
    Mobile: {mobile}<br/>
    Email: {email}
    """

    elements.append(Paragraph(company_details, styles["BodyText"]))
    elements.append(Spacer(1, 12))

    # Quotation Info
    created_at = data.get("created_at")
    if created_at:
        if isinstance(created_at, str):
            try:
                date_str = datetime.fromisoformat(created_at).strftime("%d-%m-%Y")
            except ValueError:
                date_str = created_at
        else:
            date_str = created_at.strftime("%d-%m-%Y")
    else:
        date_str = datetime.now().strftime("%d-%m-%Y")

    quotation_info = [
        ["Field", "Value"],
        ["Quotation No", data.get("dummy_quotation_no", "PREVIEW")],
        ["Date", date_str],
    ]
    elements.append(create_styled_table(quotation_info, [150, 370]))
    elements.append(Spacer(1, 15))

    # Client Details
    client_info = [
        ["Field", "Value"],
        ["Client Name", data.get("client_name") or "-"],
        ["Billing Address", data.get("billing_address") or "-"],
        ["Mobile", data.get("mobile_number") or "-"],
        ["GST Number", data.get("gst_number") or "-"],
        ["Email", data.get("email") or "-"],
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

    # Items
    items = data.get("items", [])
    if items:
        item_data = [["Item", "Qty", "Unit", "Rate", "Amount"]]
        for item in items:
            title = item.get("title", "")
            qty = item.get("quantity", 0)
            unit = item.get("unit") or "-"
            rate = item.get("rate", 0)
            amount = item.get("amount", 0)
            item_data.append(
                [
                    title,
                    f"{qty:.2f}",
                    unit,
                    f"{rate:.2f}",
                    f"{amount:.2f}",
                ]
            )

        elements.append(
            KeepTogether(
                [
                    Paragraph("<b>Item Details</b>", styles["Heading2"]),
                    Spacer(1, 6),
                    create_styled_table(item_data, [220, 60, 60, 80, 100]),
                    Spacer(1, 15),
                ]
            )
        )

        # Measurements
        has_measurements = any(item.get("measurements") for item in items)
        if has_measurements:
            meas_data = [["Item", "L", "W", "H", "Unit", "Qty", "Formula"]]
            for item in items:
                for m in item.get("measurements", []):
                    meas_data.append(
                        [
                            item.get("title", ""),
                            str(m.get("length") or "-"),
                            str(m.get("width") or "-"),
                            str(m.get("height") or "-"),
                            m.get("unit") or "-",
                            f"{m.get('quantity', 0):.2f}",
                            m.get("formula_used") or "-",
                        ]
                    )

            elements.append(
                KeepTogether(
                    [
                        Paragraph("<b>Measurement Details</b>", styles["Heading2"]),
                        Spacer(1, 6),
                        create_styled_table(meas_data, [150, 50, 50, 50, 50, 70, 100]),
                        Spacer(1, 15),
                    ]
                )
            )

    # Tax & Totals
    subtotal = data.get("subtotal", 0)
    cgst_percent = data.get("cgst_percent", 0)
    cgst_amount = data.get("cgst_amount", 0)
    sgst_percent = data.get("sgst_percent", 0)
    sgst_amount = data.get("sgst_amount", 0)
    grand_total = data.get("grand_total", 0)

    total_data = [
        ["Description", "Amount"],
        ["Subtotal", f"{subtotal:.2f}"],
    ]
    if cgst_amount > 0 or sgst_amount > 0:
        total_data.append([f"CGST ({cgst_percent:.1f}%)", f"{cgst_amount:.2f}"])
        total_data.append([f"SGST ({sgst_percent:.1f}%)", f"{sgst_amount:.2f}"])
    
    total_data.append(["Grand Total", f"{grand_total:.2f}"])

    elements.append(
        KeepTogether(
            [
                Paragraph("<b>Quotation Summary</b>", styles["Heading2"]),
                Spacer(1, 6),
                create_styled_table(total_data, [270, 250], highlight_last_row=True),
                Spacer(1, 15),
            ]
        )
    )

    # Notes
    notes = data.get("notes")
    if notes:
        elements.append(
            KeepTogether(
                [
                    Paragraph("<b>Notes / Terms</b>", styles["Heading2"]),
                    Spacer(1, 6),
                    Paragraph(notes.replace("\n", "<br/>"), styles["BodyText"]),
                ]
            )
        )


    # =====================================================
    # SIGNATURE
    # =====================================================
    signature_path = None
    if company_settings and getattr(company_settings, "signature_image", None):
        if os.path.exists(company_settings.signature_image):
            signature_path = company_settings.signature_image

    if signature_path and signature_path.lower().endswith((".png", ".jpg", ".jpeg")):
        signature_img = Image(signature_path, width=140, height=50)
        signature_img.hAlign = "LEFT"
        elements.append(signature_img)
        elements.append(Spacer(1, 5))

    company_name_text = company_settings.company_name if company_settings and company_settings.company_name else "Company"
    elements.append(
        Paragraph(
            f"<b>Authorized Signature</b><br/>{company_name_text}",
            styles["BodyText"],
        )
    )
    elements.append(Spacer(1, 10))

    line_table = Table([[""]], colWidths=[555])
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

    mobile_footer = company_settings.mobile_number if company_settings and company_settings.mobile_number else "-"
    email_footer = company_settings.email if company_settings and company_settings.email else "-"
    instagram_handle = company_settings.instagram_handle if company_settings and getattr(company_settings, "instagram_handle", None) else "-"
    whatsapp_number = company_settings.whatsapp_number if company_settings and getattr(company_settings, "whatsapp_number", None) else "-"
    website = company_settings.website if company_settings and getattr(company_settings, "website", None) else "-"
    address = company_settings.address if company_settings and getattr(company_settings, "address", None) else "-"

    icon_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "static", "icons")
    )
    
    # Actually Final uses 'static' then 'phone.png' or 'static/icons/phone.png'. Let's check exactly what Final did:
    # "os.path.join(..., 'static')" and then "phone.png". Wait, let me just assume 'static/icons' if 'static' doesn't have it.
    # The final quote audit showed: icon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "static"))
    # Let me follow that exactly.
    icon_dir_final = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "static"))

    def get_icon(filename):
        path = os.path.join(icon_dir_final, filename)
        if not os.path.exists(path):
            path = os.path.join(icon_dir_final, "icons", filename) # fallback
            if not os.path.exists(path):
                return Spacer(1, 18)
        try:
            img = Image(path, width=18, height=18)
            img.hAlign = "CENTER"
            return img
        except Exception:
            return Spacer(1, 18)

    def create_icon_text_table(icon_filename, text, col_width=150):
        table = Table(
            [[get_icon(icon_filename), Paragraph(text, footer_style)]],
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

    left_column = [
        create_icon_text_table("phone.png", mobile_footer, 126),
        Spacer(1, 8),
        create_icon_text_table("email.png", email_footer, 126),
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

    footer_data = [[left_column, center_column, right_column]]
    footer_table = Table(footer_data, colWidths=[180, 180, 195])
    footer_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#D9DDE3")),
                ("LINEABOVE", (0, 0), (-1, 0), 4, colors.HexColor("#4CAF50")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 15),
                ("RIGHTPADDING", (0, 0), (-1, -1), 15),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )

    # Render PDF
    def draw_header_footer(canvas, doc):
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


    doc.build(elements, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)
    
    buffer.seek(0)
    return buffer
