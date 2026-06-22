import io
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from num2words import num2words
import qrcode
from PIL import Image as PILImage

def build_infapilot_invoice(invoice_data):
    """
    Builds a beautifully styled PDF invoice mirroring the INFAPILOT design.
    invoice_data should be a dict containing:
    {
        "invoice_no": "INV/2024-25/0001",
        "date": "20/05/2024",
        "due_date": "19/06/2024",
        "work_order_no": "GW/2024/001",
        "client_name": "Sandeep Sir",
        "client_address": "Indore, Madhya Pradesh",
        "client_mobile": "9876543210",
        "client_gst": "23ABCDE1234F1Z5",
        "items": [
            {"desc_title": "Soling", "desc_subtitle": "Soling Work (As Per Measurement)", "unit": "Brass", "qty": 30.00, "rate": 10000.00, "amount": 300000.00},
            ...
        ],
        "subtotal": 3859422.00,
        "cgst": 0.00,
        "sgst": 0.00,
        "discount": 0.00,
        "grand_total": 3859422.00,
        "advance_paid": 0.00,
        "balance_due": 3859422.00
    }
    """
    buffer = io.BytesIO()
    
    # Page setup
    margins = 30
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=margins, leftMargin=margins, topMargin=margins, bottomMargin=margins)
    
    styles = getSampleStyleSheet()
    
    # Custom Styles
    styles.add(ParagraphStyle(name='CompanyName', fontSize=24, fontName='Helvetica-Bold', textColor=colors.HexColor('#002C5F')))
    styles.add(ParagraphStyle(name='CompanySub', fontSize=10, fontName='Helvetica', textColor=colors.gray))
    styles.add(ParagraphStyle(name='InfoText', fontSize=9, fontName='Helvetica', leading=12))
    styles.add(ParagraphStyle(name='InvoiceTitleBadge', fontSize=20, fontName='Helvetica-Bold', textColor=colors.white, alignment=2)) # Right align
    styles.add(ParagraphStyle(name='ItemTitle', fontSize=10, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='ItemSub', fontSize=8, fontName='Helvetica', textColor=colors.gray))
    styles.add(ParagraphStyle(name='AmountWords', fontSize=9, fontName='Helvetica', leading=14))
    
    elements = []
    
    # --- HEADER ---
    
    # Logo / Company placeholder
    # For now, just text if no logo
    comp_name_str = invoice_data.get('company', {}).get('name', 'INFAPILOT')
    comp_name = Paragraph(f"<b><font color='#002C5F'>{comp_name_str}</font></b>", styles['CompanyName'])
    comp_sub = Paragraph("Construction Billing Software", styles['CompanySub'])
    
    # Top Right Invoice Badge
    invoice_badge = Paragraph("INVOICE", styles['InvoiceTitleBadge'])
    badge_table = Table([[invoice_badge]], colWidths=[2*inch], rowHeights=[0.4*inch])
    badge_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), colors.HexColor('#002C5F')),
        ('ALIGN', (0,0), (0,0), 'CENTER'),
        ('VALIGN', (0,0), (0,0), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (0,0), 6),
    ]))
    
    # Details under invoice
    inv_details = [
        [Paragraph("Invoice No.", styles['InfoText']), Paragraph(f"<b>{invoice_data['invoice_no']}</b>", styles['InfoText'])],
        [Paragraph("Date :", styles['InfoText']), Paragraph(invoice_data['date'], styles['InfoText'])],
    ]
    inv_details_t = Table(inv_details, colWidths=[0.8*inch, 1.2*inch])
    inv_details_t.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    
    right_header = Table([[badge_table], [inv_details_t]], colWidths=[2.2*inch])
    right_header.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    
    # Company Info (Left)
    comp_info_text = f"""
    {invoice_data['company']['address']}<br/>
    {invoice_data['company']['mobile']}<br/>
    {invoice_data['company']['email']}<br/>
    {invoice_data['company']['website']}
    """
    comp_info = Paragraph(comp_info_text, styles['InfoText'])
    
    # Generate QR Code dynamically
    qr = qrcode.QRCode(version=1, box_size=3, border=1)
    qr.add_data(f"Invoice: {invoice_data['invoice_no']} | Total: {invoice_data['grand_total']}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    qr_buffer = io.BytesIO()
    img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    qr_img = Image(qr_buffer, width=1*inch, height=1*inch)
    
    # Assemble Header
    left_header = Table([[comp_name], [comp_sub], [Spacer(1,10)], [comp_info]], colWidths=[3.5*inch])
    left_header.setStyle(TableStyle([
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    
    header_table = Table([[left_header, qr_img, right_header]], colWidths=[3.5*inch, 1.2*inch, 2.5*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (2,0), (2,0), 'RIGHT'),
        ('ALIGN', (1,0), (1,0), 'RIGHT'),
    ]))
    elements.append(header_table)
    
    # Divider Line
    elements.append(Spacer(1, 10))
    divider = Table([['']], colWidths=[7.2*inch])
    divider.setStyle(TableStyle([('LINEABOVE', (0,0), (-1,0), 1, colors.HexColor('#002C5F'))]))
    elements.append(divider)
    elements.append(Spacer(1, 10))
    
    # --- BILL TO ---
    bill_to_text = f"""
    <font color='gray'>BILL TO</font><br/>
    <font size=12><b>{invoice_data['client_name']}</b></font><br/>
    <font color='gray'>{invoice_data['client_address']}</font><br/>
    Mobile: {invoice_data['client_mobile']}<br/>
    GST No.: {invoice_data['client_gst']}
    """
    bill_to_p = Paragraph(bill_to_text, styles['InfoText'])
    
    meta_info_data = [
        ["Invoice Date", ":", invoice_data['date']],
        ["Due Date", ":", invoice_data['due_date']],
        ["Payment Terms", ":", invoice_data.get('payment_terms', '30 Days')],
        ["Work Order No.", ":", invoice_data['work_order_no']],
    ]
    meta_info_t = Table(meta_info_data, colWidths=[1.2*inch, 0.2*inch, 1.5*inch])
    meta_info_t.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('TEXTCOLOR', (0,0), (0,-1), colors.gray),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    
    bill_to_table = Table([[bill_to_p, '', meta_info_t]], colWidths=[3.5*inch, 0.5*inch, 3.2*inch])
    bill_to_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LINEBEFORE', (2,0), (2,0), 0.5, colors.lightgrey),
        ('LEFTPADDING', (2,0), (2,0), 20),
    ]))
    elements.append(bill_to_table)
    elements.append(Spacer(1, 15))
    
    # --- ITEMS TABLE ---
    item_headers = ['#', 'Item / Work Description', 'Unit', 'Quantity', 'Rate (₹)', 'Amount (₹)']
    table_data = [item_headers]
    
    for i, item in enumerate(invoice_data['items']):
        desc = [Paragraph(item['desc_title'], styles['ItemTitle'])]
        if item.get('desc_subtitle'):
            desc.append(Paragraph(item['desc_subtitle'], styles['ItemSub']))
            
        row = [
            str(i+1),
            desc,
            item['unit'],
            f"{item['qty']:.2f}",
            f"{item['rate']:,.2f}",
            f"{item['amount']:,.2f}"
        ]
        table_data.append(row)
        
    col_widths = [0.4*inch, 3*inch, 0.8*inch, 0.8*inch, 1.0*inch, 1.2*inch]
    items_t = Table(table_data, colWidths=col_widths, repeatRows=1)
    
    items_style = TableStyle([
        # Header style
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#002C5F')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (-1,0), 'CENTER'),
        ('VALIGN', (0,0), (-1,0), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,0), 10),
        ('TOPPADDING', (0,0), (-1,0), 10),
        
        # Grid
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        
        # Alignment
        ('ALIGN', (0,1), (0,-1), 'CENTER'), # ID
        ('ALIGN', (2,1), (3,-1), 'CENTER'), # Unit, Qty
        ('ALIGN', (4,1), (5,-1), 'RIGHT'),  # Rate, Amount
        ('VALIGN', (0,1), (-1,-1), 'MIDDLE'),
        
        # Padding
        ('TOPPADDING', (0,1), (-1,-1), 12),
        ('BOTTOMPADDING', (0,1), (-1,-1), 12),
        ('LEFTPADDING', (1,1), (1,-1), 10),
        ('RIGHTPADDING', (4,1), (5,-1), 10),
    ])
    items_t.setStyle(items_style)
    elements.append(items_t)
    elements.append(Spacer(1, 15))
    
    # --- SUMMARY SECTION ---
    # Convert total to words
    amt_words = num2words(invoice_data['grand_total'], lang='en_IN').replace(',', '').title()
    amt_text = f"<b>Amount in Words:</b><br/>Rupees {amt_words} Only"
    amt_p = Paragraph(amt_text, styles['AmountWords'])
    
    summary_data = [
        ["Sub Total", f"₹ {invoice_data['subtotal']:,.2f}"],
        ["CGST (0%)", f"₹ {invoice_data['cgst']:,.2f}"],
        ["SGST (0%)", f"₹ {invoice_data['sgst']:,.2f}"],
        ["Discount", f"₹ {invoice_data['discount']:,.2f}"],
        ["GRAND TOTAL", f"₹ {invoice_data['grand_total']:,.2f}"],
        ["Advance Paid", f"₹ {invoice_data['advance_paid']:,.2f}"],
        ["BALANCE DUE", f"₹ {invoice_data['balance_due']:,.2f}"],
    ]
    
    summary_t = Table(summary_data, colWidths=[1.8*inch, 1.4*inch])
    summary_t.setStyle(TableStyle([
        ('ALIGN', (0,0), (0,-1), 'LEFT'),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('FONTNAME', (0,4), (-1,4), 'Helvetica-Bold'), # Grand Total
        ('FONTNAME', (0,6), (-1,6), 'Helvetica-Bold'), # Balance Due
        ('TEXTCOLOR', (0,4), (-1,4), colors.HexColor('#002C5F')),
        ('TEXTCOLOR', (0,6), (-1,6), colors.HexColor('#27AE60')), # Green balance
        ('BACKGROUND', (0,6), (-1,6), colors.HexColor('#F4F6F6')),
        ('LINEABOVE', (0,4), (-1,4), 1, colors.HexColor('#002C5F')), # Line above grand total
        ('LINEBELOW', (0,4), (-1,4), 1, colors.HexColor('#002C5F')), # Line below grand total
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    
    bottom_split = Table([[amt_p, summary_t]], colWidths=[4*inch, 3.2*inch])
    bottom_split.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LINEBEFORE', (1,0), (1,0), 0.5, colors.lightgrey),
        ('LEFTPADDING', (1,0), (1,0), 10),
    ]))
    elements.append(bottom_split)
    elements.append(Spacer(1, 20))
    
    # --- FOOTER SECTION ---
    # Bank Details Box
    bank_details_title = Paragraph("<b>BANK DETAILS</b>", styles['ItemTitle'])
    bank_data = [
        ["Bank Name", ":", invoice_data['company'].get('bank_name', 'Not Available')],
        ["A/c Number", ":", invoice_data['company'].get('account_number', 'Not Available')],
        ["IFSC Code", ":", invoice_data['company'].get('ifsc', 'Not Available')],
        ["Branch", ":", invoice_data['company'].get('branch', 'Head Office')],
    ]
    bank_t = Table(bank_data, colWidths=[1*inch, 0.2*inch, 2*inch])
    bank_t.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    
    bank_box = Table([[bank_details_title], [bank_t]], colWidths=[3.5*inch])
    bank_box.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('ROUNDEDCORNERS', [3, 3, 3, 3]),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    elements.append(bank_box)
    elements.append(Spacer(1, 20))
    
    # Terms & Signature
    terms_text = f"""
    <b>TERMS & CONDITIONS</b><br/><br/>
    {invoice_data['company']['terms'].replace(chr(10), '<br/>')}
    """
    terms_p = Paragraph(terms_text, styles['ItemSub'])
    
    sig_text = f"""
    For {invoice_data['company']['name']}<br/><br/><br/><br/>
    <b>Authorized Signatory</b>
    """
    sig_p = Paragraph(sig_text, styles['ItemSub'])
    sig_p.alignment = 2 # Right align
    
    footer_t = Table([[terms_p, sig_p]], colWidths=[4*inch, 3.2*inch])
    footer_t.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LINEABOVE', (0,0), (-1,0), 0.5, colors.lightgrey),
        ('TOPPADDING', (0,0), (-1,-1), 10),
    ]))
    elements.append(footer_t)
    
    doc.build(elements)
    buffer.seek(0)
    return buffer
