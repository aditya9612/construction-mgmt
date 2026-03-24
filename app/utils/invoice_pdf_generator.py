from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from datetime import datetime


def generate_invoice_pdf(invoice_data, items, file_path):

    c = canvas.Canvas(file_path, pagesize=A4)

    width, height = A4

    # --------------------------------
    # LOGO
    # --------------------------------
    try:
        c.drawImage(
            "app/static/logo.png",
            30,
            height - 80,
            width=120,
            height=40
        )
    except:
        pass

    # --------------------------------
    # COMPANY DETAILS
    # --------------------------------
    c.setFont("Helvetica-Bold", 16)
    c.drawString(170, height - 40, "Civil Construction Management")

    c.setFont("Helvetica", 10)
    c.drawString(170, height - 60, "Pune, Maharashtra")
    c.drawString(170, height - 75, "GST: 27ABCDE1234F1Z5")
    c.drawString(170, height - 90, "Phone: +91-9876543210")

    # --------------------------------
    # INVOICE TITLE
    # --------------------------------
    c.setFont("Helvetica-Bold", 14)
    c.drawString(420, height - 40, "INVOICE")

    c.setFont("Helvetica", 10)
    c.drawString(420, height - 60, f"Invoice No: {invoice_data['invoice_number']}")
    c.drawString(420, height - 75, f"Date: {invoice_data['date']}")

    # --------------------------------
    # BILL TO
    # --------------------------------
    c.setFont("Helvetica-Bold", 12)
    c.drawString(30, height - 140, "Bill To:")

    c.setFont("Helvetica", 10)
    c.drawString(30, height - 160, invoice_data["owner_name"])
    c.drawString(30, height - 175, invoice_data["owner_mobile"])
    c.drawString(30, height - 190, invoice_data["owner_address"])

    # --------------------------------
    # PROJECT DETAILS
    # --------------------------------
    c.setFont("Helvetica-Bold", 12)
    c.drawString(300, height - 140, "Project Details")

    c.setFont("Helvetica", 10)
    c.drawString(300, height - 160, f"Project ID: {invoice_data['project_id']}")
    c.drawString(300, height - 175, f"Engineer: {invoice_data['engineer']}")

    # --------------------------------
    # TABLE HEADER
    # --------------------------------
    table_y = height - 240

    c.setFont("Helvetica-Bold", 10)

    c.drawString(30, table_y, "Description")
    c.drawString(260, table_y, "Qty")
    c.drawString(320, table_y, "Rate")
    c.drawString(420, table_y, "Amount")

    c.line(30, table_y - 5, 550, table_y - 5)

    # --------------------------------
    # TABLE ROWS
    # --------------------------------
    y = table_y - 25

    subtotal = 0

    c.setFont("Helvetica", 10)

    for item in items:

        amount = item["quantity"] * item["rate"]

        subtotal += amount

        c.drawString(30, y, item["description"])
        c.drawString(260, y, str(item["quantity"]))
        c.drawString(320, y, f"₹ {item['rate']}")
        c.drawString(420, y, f"₹ {amount}")

        y -= 20

    # --------------------------------
    # TAX CALCULATION
    # --------------------------------
    gst = subtotal * 0.18
    grand_total = subtotal + gst

    y -= 10
    c.line(300, y, 550, y)

    y -= 20

    c.drawString(320, y, "Subtotal")
    c.drawString(450, y, f"₹ {subtotal}")

    y -= 20

    c.drawString(320, y, "GST (18%)")
    c.drawString(450, y, f"₹ {gst}")

    y -= 20

    c.setFont("Helvetica-Bold", 11)
    c.drawString(320, y, "Grand Total")
    c.drawString(450, y, f"₹ {grand_total}")

    # --------------------------------
    # PAYMENT DETAILS
    # --------------------------------
    y -= 60

    c.setFont("Helvetica-Bold", 11)
    c.drawString(30, y, "Payment Details")

    y -= 20
    c.setFont("Helvetica", 10)
    c.drawString(30, y, "Bank: HDFC Bank")

    y -= 15
    c.drawString(30, y, "Account No: 1234567890")

    y -= 15
    c.drawString(30, y, "IFSC: HDFC000123")

    # --------------------------------
    # SIGNATURE
    # --------------------------------
    c.drawString(420, 120, "Authorized Signature")

    # --------------------------------
    # FOOTER
    # --------------------------------
    c.setFont("Helvetica", 8)
    c.drawString(
        30,
        40,
        "This is a system generated invoice."
    )

    c.save()

    return file_path