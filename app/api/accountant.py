from decimal import Decimal
from datetime import date, datetime
from typing import Optional

from fastapi.responses import FileResponse, StreamingResponse
from app.models.accountant import RedevelopmentOffer
from app.schemas.accountant import OfferCreate, OfferOut
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, extract
from app.core.enums import AccountType, PaymentMode
from app.models.accountant import Account, FixedAsset, JournalEntry, JournalLine, BankTransaction, FundTransfer, GSTReturn, BankAccount, TDSDeduction, VendorBill
from app.schemas.accountant import (
    AccountCreate,
    AccountOut,
    AccountTreeOut,
    AccountDetailOut,
    AccountUpdate,
    AssetCreate,
    JournalEntryCreate,
    PayablePaymentRequest,
    ReceiptCreate,
    BankTransactionCreate, BankTransactionOut,
    FundTransferCreate, FundTransferOut,
    GSTReturnCreate, GSTReturnOut,
    BankAccountCreate, BankAccountUpdate, BankAccountOut, BankLedgerLine, ReconciliationDashboardOut,
    TDSDeductionCreate, TDSDeductionOut, GSTRegisterItem, GSTDashboardOut, GSTReconciliationMismatch,
    GSTReturnStatus, GSTRecentFiling, GSTImportResult
)
from app.db.session import get_db_session
from app.models.billing import RABill
from app.models.invoice import Invoice, Transaction
from app.models.user import User
from app.core.dependencies import require_roles

from app.utils.helpers import NotFoundError, ValidationError
from app.utils.qr import generate_qr

from app.models.user import UserRole

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
import os


ACCOUNTANT_READ_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.ACCOUNTANT,
    ]
]

ACCOUNTANT_WRITE_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.ACCOUNTANT,
    ]
]


def generate_offer_pdf(offer):
    import os
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
    logo_path = os.path.join(BASE_DIR, "static", "logo.png")
    stamp_path = os.path.join(BASE_DIR, "static", "stamp.png")
    phone_icon = os.path.join(BASE_DIR, "static", "phone.png")
    email_icon = os.path.join(BASE_DIR, "static", "email.png")
    loc_icon = os.path.join(BASE_DIR, "static", "location.png")

    file_path = f"media/offers/offer_{offer.id}.pdf"
    os.makedirs("media/offers", exist_ok=True)

    doc = SimpleDocTemplate(file_path, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=80, bottomMargin=50)

    styles = getSampleStyleSheet()

    normal = ParagraphStyle(name='NormalSmall', fontSize=10, leading=16, spaceAfter=6)
    bold = ParagraphStyle(name='Bold', fontSize=10, leading=16, spaceAfter=8, spaceBefore=6)
    title = ParagraphStyle(name='Title', alignment=1, fontSize=14, spaceAfter=18)

    content = []

    date_val = offer.created_at.strftime("%d-%m-%Y") if offer.created_at else "-"

    # ================= HEADER =================
    header = Table([
        [
            Image(logo_path, width=130, height=60) if os.path.exists(logo_path) else "",
            Paragraph("<para align=right>Date : ______________</para>", normal)
        ]
    ], colWidths=[220, 275])

    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    content.append(header)
    content.append(Spacer(1, 16))

    # ================= TITLE =================
    content.append(Paragraph("<b><u>OFFER LETTER</u></b>", title))
    content.append(Spacer(1, 8))

    # ================= SECOND DATE =================
    content.append(
        Paragraph(f"<para align=right>Date: {date_val}</para>", normal)
    )
    content.append(Spacer(1, 12))

    # ================= ADDRESS =================
    content.append(Paragraph("<b>To,</b>", bold))
    content.append(Paragraph(offer.society_name or "-", normal))
    content.append(Paragraph(offer.address or "-", normal))

    content.append(Spacer(1, 14))

    # ================= SUBJECT =================
    content.append(Paragraph("<b>Subject:- REDEVELOPMENT OFFER LETTER</b>", bold))

    # ================= BODY =================
    content.append(Paragraph("Dear Sir/Madam,", normal))

    content.append(Paragraph(
        "It is my pleasure to write this letter and express my intent of re-developing your society/property.",
        normal
    ))

    content.append(Paragraph(
        f"To brief you about my company, <b>{offer.developer_name}</b> is a well-established name in business. "
        "Its presence in central suburbs like Sinhagad Road, Pune. Since more than a decade, "
        f"<b>{offer.developer_name}</b> has successfully ventured into real estate development.",
        normal
    ))

    content.append(Paragraph(
        f"<b>{offer.developer_name}</b>, a well-crafted initiative by visionary leadership.",
        normal
    ))

    content.append(Paragraph(
        "We have proudly made more than 1000+ families happy with commercial and residential spaces. "
        "The purpose of this offer letter is to set forth our offers which are described below:",
        normal
    ))

    content.append(Paragraph(
        "If there is any query in any of the offer terms, amenities, requests, demands etc., "
        "feel free to reach out to us and we will definitely resolve it.",
        normal
    ))

    content.append(Paragraph("<b>Our Re-Development offer includes:</b>", bold))
    content.append(Spacer(1, 12))

    # ================= TABLE =================
    table = Table([
        ["CARPET AREA", f"EXISTING FLAT OWNER WILL GET {offer.extra_carpet_percent}% EXTRA CARPET AREA."]
    ], colWidths=[150, 340])

    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))

    content.append(table)
    content.append(Spacer(1, 16))

    # ================= NOTE =================
    content.append(Paragraph(
        "<b>Note:</b> Corpus fund, rent, shifting charges, and other expenses shall be mutually finalized.",
        normal
    ))

    content.append(Spacer(1, 20))

    # ================= FOOTER =================
    content.append(Spacer(1, 12))  # small gap
    
    phone_img = f'<img src="{phone_icon}" width="10" height="10"/>' if os.path.exists(phone_icon) else ""
    email_img = f'<img src="{email_icon}" width="10" height="10"/>' if os.path.exists(email_icon) else ""
    loc_img = f'<img src="{loc_icon}" width="10" height="10"/>' if os.path.exists(loc_icon) else ""

    footer = Table([
        [
            Image(stamp_path, width=90, height=90) if os.path.exists(stamp_path) else "",
            Paragraph(
                f"<para align=right>"
                f"{offer.contact_phone or '-'} {phone_img}<br/>"
                f"{offer.contact_email or '-'} {email_img}<br/>"
                f"SITE ADD: S.No.57/6B, Plot No.03, Abhiruchi Mall, Pune {loc_img}"
                f"</para>",
                normal
            )
        ]
    ], colWidths=[180, 310])

    footer.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (0, 0), 50),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    content.append(footer)

    # ================= BACKGROUND =================
    def draw_background(canvas, doc):
        from reportlab.lib import colors

        W, H = doc.pagesize

        # 1) WATERMARK
        if os.path.exists(logo_path):
            canvas.saveState()
            canvas.drawImage(logo_path, W/2 - 250, H/2 - 120, width=500, height=240, preserveAspectRatio=True, anchor='c')
            canvas.setFillColor(colors.white)
            canvas.setFillAlpha(0.88)
            canvas.rect(0, 0, W, H, fill=1, stroke=0)
            canvas.restoreState()

        # 2) CORNER RIBBONS
        canvas.saveState()
        
        gold = colors.HexColor("#D4AF37")
        grey = colors.HexColor("#666666")

        # Top Right
        canvas.setFillColor(gold)
        p = canvas.beginPath()
        p.moveTo(W, H-40)
        p.lineTo(W-40, H)
        p.lineTo(W-100, H)
        p.lineTo(W, H-100)
        p.close()
        canvas.drawPath(p, fill=1, stroke=0)

        canvas.setFillColor(grey)
        p = canvas.beginPath()
        p.moveTo(W, H-120)
        p.lineTo(W-120, H)
        p.lineTo(W-140, H)
        p.lineTo(W, H-140)
        p.close()
        canvas.drawPath(p, fill=1, stroke=0)

        # Bottom Left
        canvas.setFillColor(gold)
        p = canvas.beginPath()
        p.moveTo(0, 40)
        p.lineTo(40, 0)
        p.lineTo(100, 0)
        p.lineTo(0, 100)
        p.close()
        canvas.drawPath(p, fill=1, stroke=0)

        canvas.setFillColor(grey)
        p = canvas.beginPath()
        p.moveTo(0, 120)
        p.lineTo(120, 0)
        p.lineTo(140, 0)
        p.lineTo(0, 140)
        p.close()
        canvas.drawPath(p, fill=1, stroke=0)

        canvas.restoreState()

    doc.build(content, onFirstPage=draw_background)

    return file_path


router = APIRouter(prefix="/accountant", tags=["Accountant"])


@router.post("/accounts", response_model=AccountOut)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    from sqlalchemy.exc import IntegrityError as SAIntegrityError
    # Normalize type to lowercase to match AccountType enum values
    data = payload.dict()
    if isinstance(data.get("type"), str):
        data["type"] = data["type"].lower()
    obj = Account(**data)
    db.add(obj)
    try:
        await db.commit()
    except (SAIntegrityError, Exception) as e:
        await db.rollback()
        if "Duplicate" in str(e) or "UNIQUE" in str(e).upper() or "1062" in str(e):
            # Return existing account with same code
            existing = await db.scalar(
                select(Account).where(Account.code == payload.code)
            )
            if existing:
                out = AccountOut.from_orm(existing)
                out.status = "Active"
                out.current_balance = 0.0
                out.opening_balance = 0.0
                return out
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    await db.refresh(obj)
    
    out = AccountOut.from_orm(obj)
    out.status = "Active"
    out.current_balance = 0.0
    out.opening_balance = 0.0
    return out

@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    type: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    limit: int = 100,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    # Base query
    query = select(Account)
    
    if type:
        query = query.where(Account.type == type)
        
    if search:
        query = query.where(Account.name.ilike(f"%{search}%") | Account.code.ilike(f"%{search}%"))
        
    if status == "Active":
        query = query.where(~Account.name.endswith("[Inactive]"))
    elif status == "Inactive":
        query = query.where(Account.name.endswith("[Inactive]"))
        
    query = query.order_by(Account.id).offset((page - 1) * limit).limit(limit)
    accounts = (await db.execute(query)).scalars().all()
    
    # Calculate balances
    out_list = []
    for acc in accounts:
        # Determine status
        acc_status = "Inactive" if acc.name.endswith("[Inactive]") else "Active"
        
        # Strip suffix for display if needed, but we can just leave it as is or strip it
        display_name = acc.name.replace(" [Inactive]", "") if acc.name.endswith(" [Inactive]") else acc.name
        
        # Calculate balance
        bal_query = await db.execute(
            select(
                func.sum(JournalLine.debit).label("debit"),
                func.sum(JournalLine.credit).label("credit")
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == acc.id)
            .where(JournalEntry.status == "Posted")
        )
        bal_row = bal_query.first()
        debit = bal_row.debit or 0
        credit = bal_row.credit or 0
        
        if acc.type in [AccountType.ASSET, AccountType.EXPENSE]:
            balance = float(debit - credit)
        else:
            balance = float(credit - debit)
            
        out = AccountOut.from_orm(acc)
        out.name = display_name
        out.status = acc_status
        out.current_balance = balance
        out.opening_balance = 0.0 # Setup opening balance logic if we had a dedicated field, else 0
        
        out_list.append(out)
        
    return out_list

@router.get("/accounts/tree", response_model=list[AccountTreeOut])
async def get_accounts_tree(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    accounts = (await db.execute(select(Account).order_by(Account.id))).scalars().all()
    
    # Build tree
    acc_map = {}
    for acc in accounts:
        display_name = acc.name.replace(" [Inactive]", "") if acc.name.endswith(" [Inactive]") else acc.name
        acc_status = "Inactive" if acc.name.endswith("[Inactive]") else "Active"
        
        out = AccountTreeOut.from_orm(acc)
        out.name = display_name
        out.status = acc_status
        out.current_balance = 0.0
        out.opening_balance = 0.0
        out.children = []
        acc_map[acc.id] = out
        
    tree = []
    for acc in accounts:
        if acc.parent_id and acc.parent_id in acc_map:
            acc_map[acc.parent_id].children.append(acc_map[acc.id])
        else:
            tree.append(acc_map[acc.id])
            
    return tree

@router.get("/accounts/export")
async def export_accounts(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    import io, csv
    from fastapi.responses import StreamingResponse
    accounts = (await db.execute(select(Account).order_by(Account.id))).scalars().all()
    
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Account Code", "Account Name", "Type", "Parent Account", "Opening Balance", "Current Balance", "Status"])
    
    parent_map = {acc.id: acc.name for acc in accounts}
    
    for acc in accounts:
        display_name = acc.name.replace(" [Inactive]", "") if acc.name.endswith(" [Inactive]") else acc.name
        acc_status = "Inactive" if acc.name.endswith("[Inactive]") else "Active"
        
        bal_query = await db.execute(
            select(
                func.sum(JournalLine.debit).label("debit"),
                func.sum(JournalLine.credit).label("credit")
            )
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == acc.id)
            .where(JournalEntry.status == "Posted")
        )
        bal_row = bal_query.first()
        debit = bal_row.debit or 0
        credit = bal_row.credit or 0
        
        if acc.type in [AccountType.ASSET, AccountType.EXPENSE]:
            balance = float(debit - credit)
        else:
            balance = float(credit - debit)
            
        parent_name = parent_map.get(acc.parent_id, "")
        
        writer.writerow([
            acc.code,
            display_name,
            acc.type.value if hasattr(acc.type, 'value') else str(acc.type),
            parent_name,
            0.0,
            balance,
            acc_status
        ])
        
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=chart_of_accounts.csv"},
    )

@router.post("/accounts/import")
async def import_accounts(
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    import csv
    from app.core.enums import AccountType
    from sqlalchemy import select

    content = await file.read()
    text = content.decode('utf-8')
    lines = text.splitlines()
    
    valid = 0
    errors = []
    
    reader = csv.reader(lines)
    try:
        next(reader)
    except StopIteration:
        pass

    rows_data = []
    codes_to_check = set()
    for i, parts in enumerate(reader, start=2):
        rows_data.append((i, parts))
        if parts and any(parts) and len(parts) >= 3:
            codes_to_check.add(parts[1].strip())
            
    existing_codes = set()
    if codes_to_check:
        existing_accounts = await db.scalars(select(Account.code).where(Account.code.in_(codes_to_check)))
        existing_codes = set(existing_accounts.all())

    for i, parts in rows_data:
        if not parts or not any(parts):
            continue
        if len(parts) < 3:
            errors.append(f"Line {i}: Invalid format")
            continue
            
        name = parts[0].strip()
        code = parts[1].strip()
        type_str = parts[2].strip()
        parent_id_str = parts[3].strip() if len(parts) > 3 else None

        try:
            acc_type = AccountType(type_str.lower())
        except ValueError:
            errors.append(f"Line {i}: Invalid account type '{type_str}'")
            continue
            
        if code in existing_codes:
            errors.append(f"Line {i}: Account with code '{code}' already exists")
            continue
            
        existing_codes.add(code)
            
        parent_id = None
        if parent_id_str:
            try:
                parent_id = int(parent_id_str)
            except ValueError:
                errors.append(f"Line {i}: Invalid parent ID '{parent_id_str}'")
                continue

        acc = Account(name=name, code=code, type=acc_type, parent_id=parent_id)
        db.add(acc)
        valid += 1
            
    if valid > 0 and len(errors) == 0:
        await db.commit()
    else:
        await db.rollback()
        
    return {
        "valid_records": valid if len(errors) == 0 else 0,
        "errors": errors,
        "message": "Import successful" if len(errors) == 0 else "Import failed due to errors"
    }

@router.get("/accounts/{id}", response_model=AccountDetailOut)
async def get_account_detail(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    acc = await db.get(Account, id)
    if not acc:
        raise NotFoundError("Account not found")
        
    display_name = acc.name.replace(" [Inactive]", "") if acc.name.endswith(" [Inactive]") else acc.name
    acc_status = "Inactive" if acc.name.endswith("[Inactive]") else "Active"
    
    bal_query = await db.execute(
        select(
            func.sum(JournalLine.debit).label("debit"),
            func.sum(JournalLine.credit).label("credit")
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalLine.account_id == acc.id)
        .where(JournalEntry.status == "Posted")
    )
    bal_row = bal_query.first()
    debit = bal_row.debit or 0
    credit = bal_row.credit or 0
    
    if acc.type in [AccountType.ASSET, AccountType.EXPENSE]:
        balance = float(debit - credit)
    else:
        balance = float(credit - debit)
        
    out = AccountDetailOut.from_orm(acc)
    out.name = display_name
    out.status = acc_status
    out.current_balance = balance
    out.opening_balance = 0.0
    
    if acc.parent_id:
        parent = await db.get(Account, acc.parent_id)
        if parent:
            p_out = AccountOut.from_orm(parent)
            p_out.name = parent.name.replace(" [Inactive]", "") if parent.name.endswith(" [Inactive]") else parent.name
            p_out.status = "Inactive" if parent.name.endswith("[Inactive]") else "Active"
            out.parent = p_out
            
    children = (await db.execute(select(Account).where(Account.parent_id == acc.id))).scalars().all()
    for child in children:
        c_out = AccountOut.from_orm(child)
        c_out.name = child.name.replace(" [Inactive]", "") if child.name.endswith(" [Inactive]") else child.name
        c_out.status = "Inactive" if child.name.endswith("[Inactive]") else "Active"
        out.children.append(c_out)
        
    return out

@router.patch("/accounts/{id}", response_model=AccountOut)
async def update_account(
    id: int,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    acc = await db.get(Account, id)
    if not acc:
        raise NotFoundError("Account not found")
        
    # Check parent cycle
    if payload.parent_id is not None:
        if payload.parent_id == id:
            raise ValidationError("Account cannot be its own parent")
        # Could do full cycle check here, keeping it simple for now
        
    is_inactive = acc.name.endswith(" [Inactive]")
    base_name = acc.name.replace(" [Inactive]", "") if is_inactive else acc.name
    
    if payload.name:
        base_name = payload.name
        
    if payload.status:
        is_inactive = (payload.status == "Inactive")
        
    acc.name = f"{base_name} [Inactive]" if is_inactive else base_name
    
    if payload.code:
        acc.code = payload.code
        
    if payload.parent_id is not None:
        acc.parent_id = payload.parent_id
        
    await db.commit()
    await db.refresh(acc)
    
    out = AccountOut.from_orm(acc)
    out.name = base_name
    out.status = "Inactive" if is_inactive else "Active"
    return out

@router.delete("/accounts/{id}")
async def delete_account(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    acc = await db.get(Account, id)
    if not acc:
        raise NotFoundError("Account not found")
        
    # Check journal history
    usage_count = await db.scalar(select(func.count(JournalLine.id)).where(JournalLine.account_id == acc.id))
    
    if usage_count and usage_count > 0:
        # Soft delete by renaming
        if not acc.name.endswith(" [Inactive]"):
            acc.name = f"{acc.name} [Inactive]"
            await db.commit()
        return {"message": "Account marked inactive (has financial history)"}
    else:
        # Hard delete safely
        from sqlalchemy.exc import IntegrityError
        from fastapi import HTTPException
        try:
            await db.delete(acc)
            await db.commit()
            return {"message": "Account deleted permanently"}
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=400, detail="Cannot delete account with existing transactions or dependencies")

@router.get("/accounts/{id}/ledger")
async def get_account_ledger(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    acc = await db.get(Account, id)
    if not acc:
        raise NotFoundError("Account not found")
        
    query = (
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalLine.account_id == id)
        .where(JournalEntry.status == "Posted")
        .order_by(JournalEntry.created_at.asc())
    )
    
    results = await db.execute(query)
    
    ledger = []
    running_balance = 0.0
    
    is_normal_debit = acc.type in [AccountType.ASSET, AccountType.EXPENSE]
    
    for line, entry in results:
        if is_normal_debit:
            running_balance += float(line.debit - line.credit)
        else:
            running_balance += float(line.credit - line.debit)
            
        ledger.append({
            "date": entry.created_at.date().isoformat() if entry.created_at else None,
            "voucher_no": f"JV-{entry.id}",
            "description": entry.description,
            "debit": float(line.debit),
            "credit": float(line.credit),
            "balance": running_balance
        })
        
    return ledger

@router.post("/receipts")
async def create_receipt(
    payload: ReceiptCreate,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    if payload.amount <= 0:
        raise ValidationError("Invalid amount")

    txn = Transaction(
        project_id=payload.project_id,
        invoice_id=None,
        type="receipt",
        amount=payload.amount,
        mode=payload.mode,
        reference=payload.reference,
        created_by=current_user.id,
    )

    db.add(txn)
    await db.commit()

    return {
        "message": "Receipt recorded",
        "amount": float(payload.amount),
    }


@router.get("/receipts")
async def list_receipts(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (await db.execute(select(Transaction).where(Transaction.type == "receipt")))
        .scalars()
        .all()
    )

    return rows


@router.get("/receipts/summary")
async def receipt_summary(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    total = await db.scalar(
        select(func.sum(Transaction.amount)).where(Transaction.type == "receipt")
    )

    return {"total_receipts": float(total or 0)}

@router.get("/payables")
async def list_payables(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (await db.execute(select(RABill).order_by(RABill.created_at.desc()))).scalars().all()

    #  fetch all payments in one go
    paid_map = dict(
        (
            await db.execute(
                select(Transaction.linked_to, func.sum(Transaction.amount))
                .where(Transaction.linked_to.like("ra:%"))
                .group_by(Transaction.linked_to)
            )
        ).all()
    )

    result = []

    for ra in rows:
        key = f"ra:{ra.id}"
        paid = paid_map.get(key, 0) or Decimal(0)

        pending = Decimal(ra.total_amount) - paid

        if pending == 0:
            status = "paid"
        elif paid > 0:
            status = "partial"
        else:
            status = "pending"

        result.append(
            {
                "ra_id": ra.id,
                "project_id": ra.project_id,
                "contractor_id": ra.contractor_id,
                "total_amount": float(ra.total_amount),
                "paid_amount": float(paid),
                "pending_amount": float(pending),
                "status": status,
            }
        )

    return result


@router.post("/payables/{ra_id}/pay")
async def pay_contractor(
    ra_id: int,
    payload: PayablePaymentRequest,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    ra = await db.get(RABill, ra_id)

    if not ra:
        raise NotFoundError("RA Bill not found")

    if ra.status not in ["Approved", "Partial", "Paid"]:
        raise ValidationError("Bill must be approved")

    paid = await db.scalar(
        select(func.sum(Transaction.amount)).where(
            Transaction.linked_to == f"ra:{ra.id}"
        )
    ) or Decimal(0)

    pending = Decimal(ra.total_amount) - paid

    if payload.amount <= 0:
        raise ValidationError("Invalid amount")

    req_amount = Decimal(str(payload.amount)).quantize(Decimal('0.01'))
    pending_amount = pending.quantize(Decimal('0.01'))

    if req_amount > pending_amount:
        raise ValidationError("Amount exceeds pending")

    #  Get Account IDs (replace with your actual codes)
    # Try multiple code patterns for contractor payable account
    contractor_acc = None
    for code in ["CONTRACTOR_PAYABLE", "LIA-001", "LIA-CONTRACTOR"]:
        contractor_acc = await db.scalar(
            select(Account.id).where(Account.code == code)
        )
        if contractor_acc:
            break
    if not contractor_acc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Contractor liability account is not configured.")

    # Try multiple code patterns for bank account
    bank_acc = None
    for code in ["BANK", "BANK-001", "CASH-001"]:
        bank_acc = await db.scalar(
            select(Account.id).where(Account.code == code)
        )
        if bank_acc:
            break
    if not bank_acc:
        # Fall back to any asset account
        from app.core.enums import AccountType as AT
        bank_acc = await db.scalar(
            select(Account.id).where(Account.type == AT.ASSET)
        )

    if not contractor_acc or not bank_acc:
        raise ValidationError("Required accounts not configured")

    txn = Transaction(
        project_id=ra.project_id,
        type="payment",
        amount=payload.amount,
        mode=payload.mode,
        reference=payload.reference,
        linked_to=f"ra:{ra.id}",
        created_by=current_user.id,
    )
    db.add(txn)

    entry = JournalEntry(description=f"Payment for RA {ra.id}")
    db.add(entry)
    await db.flush()  # get entry.id

    db.add_all(
        [
            JournalLine(
                entry_id=entry.id,
                account_id=contractor_acc,
                debit=payload.amount,
                credit=0,
            ),
            JournalLine(
                entry_id=entry.id, account_id=bank_acc, debit=0, credit=payload.amount
            ),
        ]
    )

    new_paid = paid + payload.amount
    new_pending = Decimal(ra.total_amount) - new_paid

    ra.status = "Paid" if new_pending == 0 else "Partial"

    #  IMPORTANT
    await db.commit()

    return {
        "message": "Payment recorded",
        "paid": str(new_paid),
        "pending": str(new_pending),
        "status": ra.status,
    }


@router.get("/transactions")
async def list_transactions(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    rows = (await db.execute(select(Transaction).order_by(Transaction.created_at.desc()))).scalars().all()
    return rows


@router.get("/payables/summary")
async def payable_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    rows = (await db.execute(select(RABill).order_by(RABill.created_at.desc()))).scalars().all()

    #  single query for all payments
    paid_map = dict(
        (
            await db.execute(
                select(Transaction.linked_to, func.sum(Transaction.amount))
                .where(Transaction.linked_to.like("ra:%"))
                .group_by(Transaction.linked_to)
            )
        ).all()
    )

    total = Decimal(0)
    paid = Decimal(0)
    pending = Decimal(0)

    for ra in rows:
        total += Decimal(ra.total_amount)

        key = f"ra:{ra.id}"
        paid_amt = paid_map.get(key, 0) or Decimal(0)

        pending_amt = Decimal(ra.total_amount) - paid_amt

        paid += paid_amt
        pending += pending_amt

    return {
        "total": str(total),  # keep precision
        "paid": str(paid),
        "pending": str(pending),
    }


async def cashflow(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
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



@router.get("/payables/date-range")
async def payables_by_date(
    start: date,
    end: date,
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (await db.execute(select(RABill).where(RABill.bill_date.between(start, end))))
        .scalars()
        .all()
    )

    return rows


# NOTE: POST /accounts is already defined above (create_account).
# This duplicate was removed to avoid shadowing.

# NOTE: GET /accounts is already defined above (list_accounts).
# This duplicate was removed to avoid shadowing.

# ============================
# BANK ACCOUNTS
# ============================
@router.post("/bank-accounts", response_model=BankAccountOut)
async def create_bank_account(
    payload: BankAccountCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    # Verify account_id exists and is an ASSET
    account = await db.get(Account, payload.account_id)
    if not account:
        raise NotFoundError("Account not found")
    if account.type != AccountType.ASSET:
        raise ValidationError("Bank account must be linked to an ASSET account")
        
    parent_name = ""
    if account.parent_id:
        parent = await db.get(Account, account.parent_id)
        if parent:
            parent_name = parent.name.lower()
            
    if "bank" not in account.name.lower() and "bank" not in parent_name:
        raise ValidationError("Account name or parent must contain 'Bank'")
    
    # Check if a BankAccount already exists for this account
    existing = await db.scalar(select(BankAccount).where(BankAccount.account_id == payload.account_id))
    if existing:
        raise ValidationError("This account is already linked to a bank account")
        
    # Check if account_number is unique
    existing_acc_num = await db.scalar(select(BankAccount).where(BankAccount.account_number == payload.account_number))
    if existing_acc_num:
        raise ValidationError("Account number already exists")

    obj = BankAccount(**payload.dict())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    
    # Build out payload
    out = BankAccountOut.from_orm(obj)
    out.ledger_name = account.name
    # balance is 0 for new account
    out.balance = 0.0
    return out

@router.get("/bank-accounts", response_model=list[BankAccountOut])
async def list_bank_accounts(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    query = (
        select(
            BankAccount, 
            Account.name.label("ledger_name"),
            func.sum(JournalLine.debit).label("total_debit"),
            func.sum(JournalLine.credit).label("total_credit")
        )
        .join(Account, Account.id == BankAccount.account_id)
        .outerjoin(JournalLine, JournalLine.account_id == BankAccount.account_id)
        .group_by(BankAccount.id, Account.id)
    )
    results = await db.execute(query)
    
    out_list = []
    for bank_acc, ledger_name, debit, credit in results:
        balance = float((debit or 0) - (credit or 0))
        
        out = BankAccountOut.from_orm(bank_acc)
        out.ledger_name = ledger_name
        out.balance = balance
        out_list.append(out)
        
    return out_list

@router.get("/bank-accounts/export")
async def export_bank_accounts(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(["Admin", "Accountant"])),
):
    import io, csv
    from fastapi.responses import StreamingResponse
    query = (
        select(
            BankAccount, 
            Account.name.label("ledger_name"),
            func.sum(JournalLine.debit).label("total_debit"),
            func.sum(JournalLine.credit).label("total_credit")
        )
        .join(Account, Account.id == BankAccount.account_id)
        .outerjoin(JournalLine, JournalLine.account_id == BankAccount.account_id)
        .group_by(BankAccount.id, Account.id)
    )
    results = await db.execute(query)
    
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Account Name", "Bank Name", "Account Number", "Current Balance", "Status", "Created Date"])
    
    for bank_acc, ledger_name, debit, credit in results:
        balance = float((debit or 0) - (credit or 0))
        writer.writerow([
            ledger_name or "",
            bank_acc.bank_name or "",
            bank_acc.account_number or "",
            balance,
            "Active" if bank_acc.is_active else "Inactive",
            bank_acc.created_at.strftime("%Y-%m-%d %H:%M:%S") if bank_acc.created_at else ""
        ])
        
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bank_accounts_export.csv"},
    )

@router.post("/bank-accounts/import")
async def import_bank_accounts(
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    import csv
    from sqlalchemy import select
    from app.core.validators import validate_ifsc
    from app.core.enums import AccountType

    content = await file.read()
    text = content.decode('utf-8')
    lines = text.splitlines()
    
    valid = 0
    errors = []
    
    reader = csv.reader(lines)
    try:
        next(reader)
    except StopIteration:
        pass

    for i, parts in enumerate(reader, start=2):
        if not parts or not any(parts):
            continue
        if len(parts) < 3:
            errors.append(f"Line {i}: Invalid format")
            continue
            
        account_id_str = parts[0].strip()
        bank_name = parts[1].strip()
        account_number = parts[2].strip()
        ifsc_code = parts[3].strip() if len(parts) > 3 else None

        try:
            account_id = int(account_id_str)
        except ValueError:
            errors.append(f"Line {i}: Invalid account ID '{account_id_str}'")
            continue
            
        acc = await db.get(Account, account_id)
        if not acc or acc.type != AccountType.ASSET:
            errors.append(f"Line {i}: Account must be a valid ASSET account")
            continue

        if "bank" not in acc.name.lower() and (acc.parent and "bank" not in acc.parent.name.lower()):
            errors.append(f"Line {i}: Account name or parent must contain 'Bank'")
            continue

        if ifsc_code:
            try:
                validate_ifsc(ifsc_code)
            except ValueError as e:
                errors.append(f"Line {i}: {str(e)}")
                continue

        bank_acc = BankAccount(
            account_id=account_id,
            bank_name=bank_name,
            account_number=account_number,
            ifsc_code=ifsc_code
        )
        db.add(bank_acc)
        valid += 1
            
    if valid > 0 and len(errors) == 0:
        await db.commit()
    else:
        await db.rollback()
        
    return {
        "valid_records": valid if len(errors) == 0 else 0,
        "errors": errors,
        "message": "Import successful" if len(errors) == 0 else "Import failed due to errors"
    }

@router.get("/bank-accounts/{id}", response_model=BankAccountOut)
async def get_bank_account(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    query = (
        select(BankAccount, Account.name.label("ledger_name"))
        .join(Account, Account.id == BankAccount.account_id)
        .where(BankAccount.id == id)
    )
    result = await db.execute(query)
    row = result.first()
    if not row:
        raise NotFoundError("Bank account not found")
        
    bank_acc, ledger_name = row
    
    bal_query = select(
        func.sum(JournalLine.debit),
        func.sum(JournalLine.credit)
    ).join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(JournalLine.account_id == bank_acc.account_id).where(JournalEntry.status == "Posted")
    
    bal_result = await db.execute(bal_query)
    debit, credit = bal_result.first()
    
    out = BankAccountOut.from_orm(bank_acc)
    out.ledger_name = ledger_name
    out.balance = float((debit or 0) - (credit or 0))
    return out

@router.patch("/bank-accounts/{id}", response_model=BankAccountOut)
async def update_bank_account(
    id: int,
    payload: BankAccountUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    bank_acc = await db.get(BankAccount, id)
    if not bank_acc:
        raise NotFoundError("Bank account not found")
        
    if payload.account_number and payload.account_number != bank_acc.account_number:
        existing = await db.scalar(select(BankAccount).where(BankAccount.account_number == payload.account_number))
        if existing:
            raise ValidationError("Account number already exists")
            
    for k, v in payload.dict(exclude_unset=True).items():
        setattr(bank_acc, k, v)
        
    await db.commit()
    await db.refresh(bank_acc)
    
    return await get_bank_account(id, db, current_user)

async def _build_ledger(db: AsyncSession, account_id: int) -> list[BankLedgerLine]:
    query = (
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalLine.account_id == account_id)
        .where(JournalEntry.status == "Posted")
        .order_by(JournalEntry.created_at.asc())
    )
    
    results = await db.execute(query)
    
    ledger = []
    running_balance = 0.0
    
    for line, entry in results:
        running_balance += float(line.debit - line.credit)
        ledger.append(BankLedgerLine(
            date=entry.created_at.date(),
            voucher_no=f"JV-{entry.id}",
            description=entry.description,
            debit=float(line.debit),
            credit=float(line.credit),
            balance=running_balance
        ))
        
    return ledger

@router.get("/bank-accounts/{id}/ledger", response_model=list[BankLedgerLine])
async def get_bank_account_ledger(
    id: int,
    skip: int = 0,
    limit: int = 1000,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    bank_acc = await db.get(BankAccount, id)
    if not bank_acc:
        raise NotFoundError("Bank account not found")
        
    return await _build_ledger(db, bank_acc.account_id)

@router.get("/cash-book/ledger", response_model=list[BankLedgerLine])
async def get_cash_book_ledger(
    skip: int = 0,
    limit: int = 1000,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    from app.utils.accounting import get_primary_cash_account
    from fastapi import HTTPException
    try:
        cash_acc = await get_primary_cash_account(db)
    except ValueError:
        raise HTTPException(status_code=400, detail="Primary cash account not configured")
        
    return await _build_ledger(db, cash_acc.id)

@router.get("/cash-book/export")
async def export_cash_book(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    import io, csv
    from fastapi.responses import StreamingResponse
    from app.utils.accounting import get_primary_cash_account
    from fastapi import HTTPException
    try:
        cash_acc = await get_primary_cash_account(db)
    except ValueError:
        raise HTTPException(status_code=400, detail="Primary cash account not configured")
        
    ledger = await _build_ledger(db, cash_acc.id)
    
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Voucher No", "Type", "Debit", "Credit", "Balance"])
    
    for line in ledger:
        # Determine type based on debit/credit
        tx_type = "Deposit" if line.debit > 0 else "Payment" if line.credit > 0 else "Journal"
        writer.writerow([
            line.date.isoformat() if line.date else "",
            line.voucher_no,
            tx_type,
            line.debit,
            line.credit,
            line.balance
        ])
        
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cash_book_export.csv"},
    )

@router.post("/cash-book/import")
async def import_cash_book(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files allowed")
    
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV file too large (max 5MB)")
    text = content.decode('utf-8')
    lines = text.splitlines()
    
    valid = 0
    errors = []
    for i, line in enumerate(lines[1:], start=2):
        if not line.strip(): continue
        parts = line.split(',')
        if len(parts) < 4:
            errors.append(f"Line {i}: Invalid format")
        else:
            valid += 1
            
    return {
        "valid_records": valid,
        "errors": errors
    }


@router.get("/petty-cash/ledger", response_model=list[BankLedgerLine])
async def get_petty_cash_ledger(
    skip: int = 0,
    limit: int = 1000,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    from app.utils.accounting import get_petty_cash_account
    from fastapi import HTTPException
    try:
        cash_acc = await get_petty_cash_account(db)
    except ValueError:
        raise HTTPException(status_code=400, detail="Petty cash account not configured")
        
    return await _build_ledger(db, cash_acc.id)

async def _build_consolidated_bank_ledger(db: AsyncSession) -> list[dict]:
    # Gets consolidated ledger across ALL bank accounts
    query = (
        select(JournalLine, JournalEntry)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(Account, JournalLine.account_id == Account.id)
        .where(Account.name.ilike("%bank%"))
        .where(JournalEntry.status == "Posted")
        .order_by(JournalEntry.created_at.asc())
    )
    
    results = await db.execute(query)
    
    ledger = []
    running_balance = 0.0
    
    for line, entry in results:
        running_balance += float(line.debit - line.credit)
        ledger.append({
            "date": entry.created_at.date().isoformat() if entry.created_at else None,
            "reference": f"JV-{entry.id}",
            "details": entry.description,
            "withdrawal": float(line.credit),
            "deposit": float(line.debit),
            "balance": running_balance
        })
        
    return ledger

@router.get("/bank-book/ledger")
async def get_bank_book_ledger(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    return await _build_consolidated_bank_ledger(db)

@router.get("/bank-book/export")
async def export_bank_book(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    import io, csv
    from fastapi.responses import StreamingResponse
    ledger = await _build_consolidated_bank_ledger(db)
    
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Reference", "Details", "Withdrawal", "Deposit", "Balance"])
    
    for line in ledger:
        writer.writerow([
            line.get("date", ""),
            line.get("reference", ""),
            line.get("details", ""),
            line.get("withdrawal", 0),
            line.get("deposit", 0),
            line.get("balance", 0)
        ])
        
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bank_book_export.csv"},
    )

@router.post("/bank-book/import")
async def import_bank_book(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files allowed")
    
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV file too large (max 5MB)")
    text = content.decode('utf-8')
    lines = text.splitlines()
    
    valid = 0
    errors = []
    for i, line in enumerate(lines[1:], start=2):
        if not line.strip(): continue
        parts = line.split(',')
        if len(parts) < 4:
            errors.append(f"Line {i}: Invalid format")
        else:
            valid += 1
            
    return {
        "valid_records": valid,
        "errors": errors
    }


@router.post("/journal")
async def create_journal_entry(
    payload: JournalEntryCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    total_debit = sum(line.debit for line in payload.lines)
    total_credit = sum(line.credit for line in payload.lines)

    if total_debit != total_credit:
        raise ValidationError("Debit and Credit must be equal")

    entry = JournalEntry(description=payload.description)
    db.add(entry)
    await db.flush()

    for line in payload.lines:
        if line.debit == 0 and line.credit == 0:
            raise ValidationError("Line cannot have both debit and credit = 0")
        db.add(
            JournalLine(
                entry_id=entry.id,
                account_id=line.account_id,
                debit=line.debit,
                credit=line.credit,
            )
        )

    await db.commit()

    return {"message": "Journal entry created"}


@router.get("/journal")
async def list_journal(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    return (await db.execute(select(JournalEntry).order_by(JournalEntry.created_at.desc()))).scalars().all()


@router.get("/gst/summary", response_model=GSTDashboardOut)
async def gst_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    from app.utils.accounting import resolve_tax_accounts
    from sqlalchemy import case, extract
    import calendar
    
    try:
        input_gst_acc = await resolve_tax_accounts(db, 'input_gst')
        output_gst_acc = await resolve_tax_accounts(db, 'output_gst')
        
        # Calculate Input GST
        input_gst = await db.scalar(
            select(func.sum(JournalLine.debit - JournalLine.credit))
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == input_gst_acc.id)
            .where(JournalEntry.status == "Posted")
        )
        input_gst = float(input_gst or 0.0)
        
        # Calculate Output GST
        output_gst = await db.scalar(
            select(func.sum(JournalLine.credit - JournalLine.debit))
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == output_gst_acc.id)
            .where(JournalEntry.status == "Posted")
        )
        output_gst = float(output_gst or 0.0)
    except ValueError:
        input_gst = 0.0
        output_gst = 0.0

    tds_collected = await db.scalar(
        select(func.sum(TDSDeduction.tds_amount))
        .where(TDSDeduction.status == "Posted")
    )
    tds_collected = float(tds_collected or 0.0)

    upcoming_return = "GSTR-3B due 20th"

    # Monthly Trend (Mocking 6 months for example, could be done via group by)
    monthly_trend = []
    
    # Return Status
    returns = await db.scalars(select(GSTReturn).order_by(GSTReturn.created_at.desc()))
    returns = returns.all()
    
    return_status = []
    recent_filings = []
    
    # Create some defaults based on returns
    for r in returns:
        return_status.append(GSTReturnStatus(
            return_type=r.return_type,
            filing_period=r.filing_period,
            status=r.status,
            due_date=date.today(), # Normally calculated based on rules
            filing_date=r.filing_date
        ))
        recent_filings.append(GSTRecentFiling(
            return_type=r.return_type,
            filing_period=r.filing_period,
            filing_date=r.filing_date,
            status=r.status
        ))
    
    return GSTDashboardOut(
        input_gst=input_gst,
        output_gst=output_gst,
        net_gst=output_gst - input_gst,
        tds_collected=tds_collected,
        upcoming_return=upcoming_return,
        monthly_trend=monthly_trend,
        return_status=return_status,
        recent_filings=recent_filings
    )


@router.get("/bank/summary")
async def bank_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    from app.utils.accounting import get_primary_cash_account
    try:
        cash_acc = await get_primary_cash_account(db)
        cash_balance_query = await db.scalar(
            select(func.sum(JournalLine.debit - JournalLine.credit))
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.account_id == cash_acc.id)
            .where(JournalEntry.status == "Posted")
        )
        cash_balance = float(cash_balance_query or 0.0)
    except ValueError:
        cash_balance = 0.0

    bank_balance_query = await db.scalar(
        select(func.sum(JournalLine.debit - JournalLine.credit))
        .join(Account, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(Account.name.ilike("%bank%"))
        .where(JournalEntry.status == "Posted")
    )
    bank_balance = float(bank_balance_query or 0.0)

    today_deposit_query = await db.scalar(
        select(func.sum(JournalLine.credit))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(Account, JournalLine.account_id == Account.id)
        .where(JournalEntry.status == "Posted")
        .where(JournalEntry.entry_date == date.today())
        .where(Account.name.ilike("%bank%") | (Account.id == cash_acc.id if 'cash_acc' in locals() else False))
    )
    today_deposit = float(today_deposit_query or 0.0)

    today_withdrawal_query = await db.scalar(
        select(func.sum(JournalLine.debit))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(Account, JournalLine.account_id == Account.id)
        .where(JournalEntry.status == "Posted")
        .where(JournalEntry.entry_date == date.today())
        .where(Account.name.ilike("%bank%") | (Account.id == cash_acc.id if 'cash_acc' in locals() else False))
    )
    today_withdrawal = float(today_withdrawal_query or 0.0)

    return {
        "total_bank_balance": bank_balance,
        "available_cash": cash_balance,
        "today_deposit": today_deposit,
        "today_withdrawal": today_withdrawal,
    }


@router.post("/assets")
async def create_asset(
    payload: AssetCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):

    if payload.purchase_value <= 0:
        raise ValidationError("Invalid purchase value")

    obj = FixedAsset(
        name=payload.name,
        purchase_value=payload.purchase_value,
        purchase_date=payload.purchase_date,
        depreciation_rate=payload.depreciation_rate,
        project_id=payload.project_id,
        current_value=payload.purchase_value,  # auto set
    )

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    return obj


@router.get("/assets/{id}/qr", response_class=StreamingResponse)
async def generate_asset_qr(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    asset = await db.get(FixedAsset, id)

    if not asset:
        raise NotFoundError("Asset not found")

    qr_buf = generate_qr(entity_type="AST", entity_id=asset.id)

    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'inline; filename="asset_{asset.id}.png"'
    }

    return StreamingResponse(
        qr_buf,
        media_type="image/png",
        headers=headers,
    )

@router.post("/assets/{id}/depreciate")
async def depreciate_asset(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    asset = await db.get(FixedAsset, id)

    if not asset:
        raise NotFoundError("Asset not found")

    rate = asset.depreciation_rate or 0

    depreciation = asset.current_value * (rate / 100)

    asset.current_value -= depreciation

    await db.commit()

    return {
        "asset_id": asset.id,
        "depreciation": float(depreciation),
        "new_value": float(asset.current_value),
    }


@router.get("/reports/trial-balance")
async def trial_balance(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    result = await db.execute(
        select(
            Account.id,
            Account.name,
            Account.type,
            func.sum(JournalLine.debit).label("debit"),
            func.sum(JournalLine.credit).label("credit"),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.status == "Posted")
        .group_by(Account.id)
    )

    rows = result.all()

    output = []
    total_debit = 0
    total_credit = 0

    for r in rows:
        debit = float(r.debit or 0)
        credit = float(r.credit or 0)

        total_debit += debit
        total_credit += credit

        output.append(
            {
                "account_id": r.id,
                "account_name": r.name,
                "type": r.type,
                "debit": debit,
                "credit": credit,
            }
        )

    return {
        "accounts": output,
        "total_debit": total_debit,
        "total_credit": total_credit,
    }


@router.get("/reports/balance-sheet")
async def balance_sheet(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    as_of: date | None = None,
):

    query = (
        select(
            Account.id,
            Account.name,
            Account.type,
            func.sum(JournalLine.debit - JournalLine.credit).label("balance"),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.status == "Posted")
    )
    if as_of:
        query = query.where(func.date(JournalEntry.entry_date) <= as_of)
    query = query.group_by(Account.id)
    
    result = await db.execute(query)

    rows = result.all()

    assets = []
    liabilities = []
    equity = []

    total_assets = 0
    total_liabilities = 0
    total_equity = 0

    for r in rows:
        balance = float(r.balance or 0)

        item = {"account_id": r.id, "account_name": r.name, "balance": balance}

        if r.type == AccountType.ASSET.value:
            assets.append(item)
            total_assets += balance

        elif r.type == AccountType.LIABILITY.value:
            liabilities.append(item)
            total_liabilities += balance

        elif r.type == AccountType.EQUITY.value:
            equity.append(item)
            total_equity += balance


    income = await db.scalar(
        select(func.sum(JournalLine.credit - JournalLine.debit))
        .join(Account, Account.id == JournalLine.account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(Account.type == AccountType.INCOME.value)
        .where(JournalEntry.status == "Posted")
    )

    expense = await db.scalar(
        select(func.sum(JournalLine.debit - JournalLine.credit))
        .join(Account, Account.id == JournalLine.account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(Account.type == AccountType.EXPENSE.value)
        .where(JournalEntry.status == "Posted")
    )

    income = float(income or 0)
    expense = float(expense or 0)

    profit = income - expense


    total_equity += profit

    equity.append({"account_name": "Retained Earnings", "balance": profit})


    return {
        "assets": {"items": assets, "total": total_assets},
        "liabilities": {"items": liabilities, "total": total_liabilities},
        "equity": {"items": equity, "total": total_equity},
        "profit": profit,
        "is_balanced": round(total_assets, 2)
        == round(total_liabilities + total_equity, 2),
    }


@router.post("/offers", response_model=OfferOut)
async def create_offer(
    payload: OfferCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    obj = RedevelopmentOffer(**payload.dict())

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    return obj

@router.get("/offers/{offer_id}/generate")
async def generate_offer_letter(
    offer_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    offer = await db.get(RedevelopmentOffer, offer_id)

    if not offer:
        raise NotFoundError("Offer not found")

    date_val = offer.created_at.date() if offer.created_at else "-"

    letter = f"""
RAJVEER CONSTRUCTION

Date: {date_val}

To,
{offer.society_name}
{offer.address}

Subject: Redevelopment Offer Letter

Dear Sir/Madam,

We are pleased to express our intent to redevelop your property.

{offer.developer_name} is a well-established name in real estate development.

We have successfully delivered multiple residential and commercial projects.

Our Offer Includes:
- Existing flat owners will get {offer.extra_carpet_percent}% extra carpet area

Note:
{offer.note or "Details will be finalized mutually."}

Contact:
{offer.contact_phone or '-'}
{offer.contact_email or '-'}
"""

    return {
        "offer_id": offer.id,
        "letter": letter
    }


@router.get("/offers/{offer_id}/pdf")
async def download_offer_pdf(
    offer_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    offer = await db.get(RedevelopmentOffer, offer_id)

    if not offer:
        raise NotFoundError("Offer not found")

    #  Generate only once (but still works locally)
    if not getattr(offer, "pdf_path", None):
        file_path = generate_offer_pdf(offer)
        offer.pdf_path = file_path
        await db.commit()
        await db.refresh(offer)
    else:
        file_path = offer.pdf_path

    return FileResponse(
        path=file_path,
        filename=os.path.basename(file_path),
        media_type="application/pdf"
    )


# ===================== BANK RECONCILIATION =====================

@router.post("/bank/reconciliation/import")
async def import_bank_transactions(
    bank_account_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    import csv
    import codecs
    from fastapi import HTTPException
    
    bank_acc = await db.get(BankAccount, bank_account_id)
    if not bank_acc:
        raise HTTPException(status_code=404, detail="Bank account not found")

    ledger_account_id = bank_acc.account_id

    try:
        csvReader = csv.DictReader(codecs.iterdecode(file.file, 'utf-8'))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid CSV format")
    
    added = 0
    skipped = 0

    rows_data = list(csvReader)
    parsed_rows = []
    
    for row in rows_data:
        try:
            txn_date_str = row.get("date", "").strip()
            amount_str = row.get("amount", "0").strip()
            txn_type = row.get("type", "").strip()
            desc = row.get("description", "").strip()
            ref = row.get("reference_number", "").strip()
            
            if not txn_date_str or not amount_str or not txn_type:
                continue
                
            txn_date = datetime.strptime(txn_date_str, "%Y-%m-%d").date()
            amount = Decimal(amount_str)
            parsed_rows.append((txn_date, amount, txn_type, desc, ref))
        except Exception:
            continue

    existing_keys = set()
    if parsed_rows:
        dates = {r[0] for r in parsed_rows}
        stmt = select(
            BankTransaction.transaction_date, 
            BankTransaction.amount, 
            BankTransaction.reference_number
        ).where(
            BankTransaction.bank_account_id == ledger_account_id,
            BankTransaction.transaction_date.in_(dates)
        )
        existing_txns = await db.execute(stmt)
        for t_date, t_amount, t_ref in existing_txns:
            existing_keys.add((t_date, t_amount, t_ref))

    for txn_date, amount, txn_type, desc, ref in parsed_rows:
        key = (txn_date, amount, ref)
        if key in existing_keys:
            skipped += 1
            continue
            
        existing_keys.add(key)
        
        bt = BankTransaction(
            bank_account_id=ledger_account_id,
            transaction_date=txn_date,
            amount=amount,
            type=txn_type,
            description=desc,
            reference_number=ref,
            is_reconciled=0
        )
        db.add(bt)
        added += 1
            
    await db.commit()
    
    return {"added": added, "skipped": skipped}


@router.post("/bank/reconciliation/run")
async def auto_run_bank_reconciliation(
    bank_account_id: int,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    from fastapi import HTTPException
    from sqlalchemy.orm import aliased, selectinload
    
    bank_acc = await db.get(BankAccount, bank_account_id)
    if not bank_acc:
        raise HTTPException(status_code=404, detail="Bank account not found")

    ledger_account_id = bank_acc.account_id

    # Get all pending bank transactions
    pending_bt_result = await db.scalars(
        select(BankTransaction)
        .where(BankTransaction.bank_account_id == ledger_account_id, BankTransaction.is_reconciled == 0)
    )
    pending_bts = pending_bt_result.all()

    # Get all potential journal lines (unreconciled loosely by checking if any bank transaction matched it)
    # Actually, JournalLine doesn't have an is_reconciled flag, so we look for lines that are not matched.
    matched_je_ids_result = await db.scalars(
        select(BankTransaction.matched_journal_id)
        .where(BankTransaction.matched_journal_id.isnot(None))
    )
    matched_je_ids = set(matched_je_ids_result.all())

    # Get journal entries that contain a line for this bank account and aren't fully matched
    jl = aliased(JournalLine)
    je = aliased(JournalEntry)
    
    unmatched_jes_result = await db.scalars(
        select(je)
        .join(jl, jl.entry_id == je.id)
        .where(jl.account_id == ledger_account_id)
        .options(selectinload(je.lines))
    )
    
    # Simple algorithm: index JEs by amount (and optionally date/reference)
    potential_matches = {}
    for entry in unmatched_jes_result.all():
        if entry.id in matched_je_ids:
            continue
            
        # Find the line that hits this bank account
        for line in entry.lines:
            if line.account_id == ledger_account_id:
                amount = line.debit if line.debit > 0 else line.credit
                potential_matches.setdefault(amount, []).append(entry)
                break
                
    matched_count = 0
    
    for bt in pending_bts:
        candidates = potential_matches.get(bt.amount, [])
        if not candidates:
            continue
            
        best_match = None
        for cand in candidates:
            # check date proximity
            if getattr(cand, "entry_date", None):
                date_diff = abs((bt.transaction_date - cand.entry_date).days)
                if date_diff <= 3:
                    best_match = cand
                    break
            else:
                best_match = cand
                break
                
        if best_match:
            bt.is_reconciled = 1
            bt.matched_journal_id = best_match.id
            matched_count += 1
            # Remove from candidates so it isn't matched twice
            candidates.remove(best_match)
            matched_je_ids.add(best_match.id)
            
    await db.commit()
    return {"reconciled_count": matched_count}

@router.post("/bank/transactions", response_model=BankTransactionOut)
async def create_bank_transaction(
    payload: BankTransactionCreate,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    bt = BankTransaction(**payload.model_dump())
    db.add(bt)
    await db.commit()
    await db.refresh(bt)
    return bt

@router.get("/bank/reconciliation/pending", response_model=list[BankTransactionOut])
async def get_pending_reconciliations(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    result = await db.scalars(
        select(BankTransaction).where(BankTransaction.is_reconciled == 0)
    )
    return result.all()

@router.post("/bank/reconciliation/{transaction_id}/match/{journal_id}")
async def match_bank_transaction(
    transaction_id: int,
    journal_id: int,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    from fastapi import HTTPException
    
    bt = await db.get(BankTransaction, transaction_id)
    je = await db.get(JournalEntry, journal_id)
    if not bt or not je:
        raise HTTPException(status_code=404, detail="Transaction or Journal Entry not found")
        
    # Validate Amount
    je_line = await db.scalar(
        select(JournalLine).where(
            JournalLine.entry_id == je.id,
            JournalLine.account_id == bt.bank_account_id
        ).limit(1)
    )
    if not je_line:
        raise HTTPException(status_code=400, detail="Journal entry does not affect this bank account")
    
    je_amount = float(je_line.debit) if je_line.debit > 0 else float(je_line.credit)
    if float(bt.amount) != je_amount:
        raise HTTPException(status_code=400, detail=f"Amount mismatch: Bank {bt.amount} vs Journal {je_amount}")

    bt.is_reconciled = 1
    bt.matched_journal_id = je.id
    await db.commit()
    return {"message": "Matched successfully"}

@router.get("/bank/reconciliation/history", response_model=list[BankTransactionOut])
async def get_reconciliation_history(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    result = await db.scalars(
        select(BankTransaction)
        .where(BankTransaction.is_reconciled == 1)
        .order_by(BankTransaction.updated_at.desc())
        .limit(1000)
    )
    return result.all()

@router.get("/bank/reconciliation/export")
async def export_reconciliation_csv(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    import io, csv
    
    result = await db.scalars(
        select(BankTransaction)
        .where(BankTransaction.is_reconciled == 1)
        .order_by(BankTransaction.updated_at.desc())
    )
    transactions = result.all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ID", "Date", "Amount", "Type", "Description", "Reference", "Reconciled", "Matched Journal ID"])

    for tx in transactions:
        writer.writerow([
            tx.id,
            tx.transaction_date,
            tx.amount,
            tx.type,
            tx.description or "",
            tx.reference_number or "",
            "Yes" if tx.is_reconciled else "No",
            tx.matched_journal_id or ""
        ])

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bank_reconciliation.csv"},
    )

@router.get("/bank/reconciliation/dashboard", response_model=ReconciliationDashboardOut)
async def get_reconciliation_dashboard(
    bank_account_id: Optional[int] = None,
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    from fastapi import HTTPException
    
    if not bank_account_id:
        raise HTTPException(status_code=400, detail="bank_account_id is required.")
        
    bank = await db.get(BankAccount, bank_account_id)
    if not bank:
        raise HTTPException(status_code=404, detail="Bank account not found")
        
    ledger_account_id = bank.account_id

    # Calculate System Balance
    system_balance_query = await db.scalar(
        select(func.sum(JournalLine.debit - JournalLine.credit))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalLine.account_id == ledger_account_id)
        .where(JournalEntry.status == "Posted")
    )
    system_balance = float(system_balance_query or 0.0)

    # Calculate Bank Balance from imported statements (Sum of all BankTransactions)
    # Assuming BankTransaction.type == "Credit" increases balance and "Debit" decreases it.
    bank_credits = await db.scalar(
        select(func.sum(BankTransaction.amount)).where(BankTransaction.type == "Credit", BankTransaction.bank_account_id == ledger_account_id)
    )
    bank_debits = await db.scalar(
        select(func.sum(BankTransaction.amount)).where(BankTransaction.type == "Debit", BankTransaction.bank_account_id == ledger_account_id)
    )
    bank_balance = float(bank_credits or 0.0) - float(bank_debits or 0.0)
    
    unreconciled_amount = system_balance - bank_balance

    return ReconciliationDashboardOut(
        system_balance=system_balance,
        bank_balance=bank_balance,
        unreconciled_amount=unreconciled_amount
    )

# ===================== FUND TRANSFERS =====================

@router.post("/transfers", response_model=FundTransferOut)
async def create_fund_transfer(
    payload: FundTransferCreate,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    from app.utils.accounting import auto_post_journal
    
    # Validation logic to ensure accounts exist
    from_acc = await db.get(Account, payload.from_account_id)
    to_acc = await db.get(Account, payload.to_account_id)
    
    if not from_acc or not to_acc:
        raise NotFoundError("Accounts not found")

    # Post auto journal
    je = await auto_post_journal(
        db, 
        amount=payload.amount, 
        debit_code=to_acc.code, 
        credit_code=from_acc.code, 
        description=f"Fund transfer: {payload.remarks}"
    )

    ft = FundTransfer(**payload.model_dump())
    if je:
        ft.journal_entry_id = je.id
        
    db.add(ft)
    await db.commit()
    await db.refresh(ft)
    return ft

@router.get("/transfers", response_model=list[FundTransferOut])
async def list_fund_transfers(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    result = await db.scalars(select(FundTransfer))
    return result.all()

# ===================== GST & TAXATION =====================

@router.post("/gst/returns", response_model=GSTReturnOut)
async def create_gst_return(
    payload: GSTReturnCreate,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    gstr = GSTReturn(**payload.model_dump())
    db.add(gstr)
    await db.commit()
    await db.refresh(gstr)
    return gstr

@router.get("/gst/returns", response_model=list[GSTReturnOut])
async def list_gst_returns(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    result = await db.scalars(select(GSTReturn).order_by(GSTReturn.created_at.desc()))
    return result.all()

@router.post("/tds/deductions", response_model=TDSDeductionOut)
async def create_tds_deduction(
    payload: TDSDeductionCreate,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    from app.utils.accounting import resolve_tax_accounts, auto_post_journal
    
    tds = TDSDeduction(**payload.model_dump())
    tds.created_by = current_user.id
    
    db.add(tds)
    
    # Process Auto Journal if posted directly
    if tds.status == "Posted":
        try:
            tds_acc = await resolve_tax_accounts(db, 'tds_payable')
            vendor_acc = await db.scalar(select(Account).where(Account.code == "VENDOR_PAYABLE"))
            if not vendor_acc:
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail="Vendor liability account is not configured.")
            
            if tds_acc and vendor_acc:
                je = JournalEntry(
                    entry_date=date.today(),
                    description=f"TDS Deduction for {tds.party_name}",
                    entry_type="Auto",
                    status="Posted",
                    created_by=current_user.id
                )
                db.add(je)
                await db.flush()
                
                # Debit Vendor, Credit TDS
                jl_debit = JournalLine(entry_id=je.id, account_id=vendor_acc.id, debit=tds.tds_amount, credit=0.0)
                jl_credit = JournalLine(entry_id=je.id, account_id=tds_acc.id, debit=0.0, credit=tds.tds_amount)
                db.add_all([jl_debit, jl_credit])
        except ValueError:
            pass # Or handle

    await db.commit()
    await db.refresh(tds)
    return tds

@router.get("/gst/invoice-register", response_model=list[GSTRegisterItem])
async def gst_invoice_register(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    # Combine Invoice and VendorBill
    items = []
    
    invoices = await db.scalars(select(Invoice))
    for inv in invoices:
        items.append(GSTRegisterItem(
            date=inv.created_at.date(), # assuming created_at as date for now
            invoice_no=str(inv.id),
            type='SALES',
            party_name='Customer', # Would join customer/project
            taxable_amount=float(inv.amount),
            gst_amount=float(inv.gst_amount),
            invoice_total=float(inv.total_amount)
        ))
        
    vendor_bills = await db.scalars(select(VendorBill))
    for vb in vendor_bills:
        items.append(GSTRegisterItem(
            date=vb.bill_date,
            invoice_no=vb.bill_number,
            type='PURCHASE',
            party_name='Vendor', # Would join Vendor
            taxable_amount=float(vb.total_amount), # Simplified
            gst_amount=0.0, # Simplified
            invoice_total=float(vb.total_amount)
        ))
        
    return items

@router.get("/gst/returns/generate", response_model=GSTReturnOut)
async def generate_gst_return(
    filing_period: str,
    return_type: str,
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    # Calculate from existing tables for the filing period
    # Assuming filing_period format like "2026-07"
    try:
        year, month = map(int, filing_period.split("-"))
    except ValueError:
        year, month = date.today().year, date.today().month

    # Taxable Value & GST Liability from Invoices
    sales = await db.execute(
        select(func.sum(Invoice.amount), func.sum(Invoice.gst_amount))
        .where(extract('year', Invoice.created_at) == year)
        .where(extract('month', Invoice.created_at) == month)
    )
    taxable_value, gst_liability = sales.one()
    taxable_value = float(taxable_value or 0.0)
    gst_liability = float(gst_liability or 0.0)

    # ITC Available from Vendor Bills
    purchases = await db.execute(
        select(func.sum(VendorBill.total_amount)) # Simplified as VendorBill lacks gst_amount currently
        .where(extract('year', VendorBill.bill_date) == year)
        .where(extract('month', VendorBill.bill_date) == month)
    )
    itc_available = float(purchases.scalar() or 0.0) * 0.18 # Mock 18% ITC for demo

    gst = GSTReturn(
        filing_period=filing_period,
        return_type=return_type,
        taxable_value=taxable_value,
        gst_liability=gst_liability,
        itc_available=itc_available,
        net_gst_payable=gst_liability - itc_available,
        status="Draft"
    )
    db.add(gst)
    await db.commit()
    await db.refresh(gst)
    return gst

@router.post("/gst/reconciliation/match", response_model=list[GSTReconciliationMismatch])
async def reconcile_gst(
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    # In-memory comparison
    content = await file.read()
    text = content.decode('utf-8')
    lines = text.splitlines()
    
    mismatches = []
    # Assume CSV: invoice_no, vendor, gst_amount
    for line in lines[1:]: # Skip header
        if not line.strip(): continue
        parts = line.split(',')
        if len(parts) >= 3:
            inv_no = parts[0].strip()
            vendor = parts[1].strip()
            try:
                portal_gst = float(parts[2].strip())
            except ValueError:
                portal_gst = 0.0
                
            # Query ERP
            erp_bill = await db.scalar(select(VendorBill).where(VendorBill.bill_number == inv_no))
            if not erp_bill:
                mismatches.append(GSTReconciliationMismatch(
                    invoice_no=inv_no, vendor=vendor, erp_gst=0.0, portal_gst=portal_gst,
                    difference=portal_gst, status="MISSING_IN_ERP"
                ))
            else:
                erp_gst = float(erp_bill.total_amount) * 0.18 # Mock ERP GST
                diff = abs(erp_gst - portal_gst)
                if diff > 1.0: # Tolerance
                    mismatches.append(GSTReconciliationMismatch(
                        invoice_no=inv_no, vendor=vendor, erp_gst=erp_gst, portal_gst=portal_gst,
                        difference=diff, status="MISMATCH"
                    ))
    return mismatches

# ===================== GST & TAXATION =====================

@router.post("/gst/returns", response_model=GSTReturnOut)
async def create_gst_return(
    payload: GSTReturnCreate,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    gstr = GSTReturn(**payload.model_dump())
    db.add(gstr)
    await db.commit()
    await db.refresh(gstr)
    return gstr

@router.get("/gst/returns", response_model=list[GSTReturnOut])
async def list_gst_returns(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    result = await db.scalars(select(GSTReturn).order_by(GSTReturn.created_at.desc()))
    return result.all()

@router.post("/tds/deductions", response_model=TDSDeductionOut)
async def create_tds_deduction(
    payload: TDSDeductionCreate,
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    from app.utils.accounting import resolve_tax_accounts, auto_post_journal
    
    tds = TDSDeduction(**payload.model_dump())
    tds.created_by = current_user.id
    
    db.add(tds)
    
    # Process Auto Journal if posted directly
    if tds.status == "Posted":
        try:
            tds_acc = await resolve_tax_accounts(db, 'tds_payable')
            vendor_acc = await db.scalar(select(Account).where(Account.code == "VENDOR_PAYABLE"))
            if not vendor_acc:
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail="Vendor liability account is not configured.")
            
            if tds_acc and vendor_acc:
                je = JournalEntry(
                    entry_date=date.today(),
                    description=f"TDS Deduction for {tds.party_name}",
                    entry_type="Auto",
                    status="Posted",
                    created_by=current_user.id
                )
                db.add(je)
                await db.flush()
                
                # Debit Vendor, Credit TDS
                jl_debit = JournalLine(entry_id=je.id, account_id=vendor_acc.id, debit=tds.tds_amount, credit=0.0)
                jl_credit = JournalLine(entry_id=je.id, account_id=tds_acc.id, debit=0.0, credit=tds.tds_amount)
                db.add_all([jl_debit, jl_credit])
        except ValueError:
            pass # Or handle

    await db.commit()
    await db.refresh(tds)
    return tds

@router.get("/gst/invoice-register", response_model=list[GSTRegisterItem])
async def gst_invoice_register(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    # Combine Invoice and VendorBill
    items = []
    
    invoices = await db.scalars(select(Invoice))
    for inv in invoices:
        items.append(GSTRegisterItem(
            date=inv.created_at.date(), # assuming created_at as date for now
            invoice_no=str(inv.id),
            type='SALES',
            party_name='Customer', # Would join customer/project
            taxable_amount=float(inv.amount),
            gst_amount=float(inv.gst_amount),
            invoice_total=float(inv.total_amount)
        ))
        
    vendor_bills = await db.scalars(select(VendorBill))
    for vb in vendor_bills:
        items.append(GSTRegisterItem(
            date=vb.bill_date,
            invoice_no=vb.bill_number,
            type='PURCHASE',
            party_name='Vendor', # Would join Vendor
            taxable_amount=float(vb.total_amount), # Simplified
            gst_amount=0.0, # Simplified
            invoice_total=float(vb.total_amount)
        ))
        
    return items

@router.get("/gst/returns/generate", response_model=GSTReturnOut)
async def generate_gst_return(
    filing_period: str,
    return_type: str,
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    # Calculate from existing tables for the filing period
    # Assuming filing_period format like "2026-07"
    try:
        year, month = map(int, filing_period.split("-"))
    except ValueError:
        year, month = date.today().year, date.today().month

    # Taxable Value & GST Liability from Invoices
    sales = await db.execute(
        select(func.sum(Invoice.amount), func.sum(Invoice.gst_amount))
        .where(extract('year', Invoice.created_at) == year)
        .where(extract('month', Invoice.created_at) == month)
    )
    taxable_value, gst_liability = sales.one()
    taxable_value = float(taxable_value or 0.0)
    gst_liability = float(gst_liability or 0.0)

    # ITC Available from Vendor Bills
    purchases = await db.execute(
        select(func.sum(VendorBill.total_amount)) # Simplified as VendorBill lacks gst_amount currently
        .where(extract('year', VendorBill.bill_date) == year)
        .where(extract('month', VendorBill.bill_date) == month)
    )
    itc_available = float(purchases.scalar() or 0.0) * 0.18 # Mock 18% ITC for demo

    gst = GSTReturn(
        filing_period=filing_period,
        return_type=return_type,
        taxable_value=taxable_value,
        gst_liability=gst_liability,
        itc_available=itc_available,
        net_gst_payable=gst_liability - itc_available,
        status="Draft"
    )
    db.add(gst)
    await db.commit()
    await db.refresh(gst)
    return gst

@router.post("/gst/reconciliation/match", response_model=list[GSTReconciliationMismatch])
async def reconcile_gst(
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    # In-memory comparison
    content = await file.read()
    text = content.decode('utf-8')
    lines = text.splitlines()
    
    mismatches = []
    # Assume CSV: invoice_no, vendor, gst_amount
    for line in lines[1:]: # Skip header
        if not line.strip(): continue
        parts = line.split(',')
        if len(parts) >= 3:
            inv_no = parts[0].strip()
            vendor = parts[1].strip()
            try:
                portal_gst = float(parts[2].strip())
            except ValueError:
                portal_gst = 0.0
                
            # Query ERP
            erp_bill = await db.scalar(select(VendorBill).where(VendorBill.bill_number == inv_no))
            if not erp_bill:
                mismatches.append(GSTReconciliationMismatch(
                    invoice_no=inv_no, vendor=vendor, erp_gst=0.0, portal_gst=portal_gst,
                    difference=portal_gst, status="MISSING_IN_ERP"
                ))
            else:
                erp_gst = float(erp_bill.total_amount) * 0.18 # Mock ERP GST
                diff = abs(erp_gst - portal_gst)
                if diff > 1.0: # 1 rupee tolerance
                    mismatches.append(GSTReconciliationMismatch(
                        invoice_no=inv_no, vendor=vendor, erp_gst=erp_gst, portal_gst=portal_gst,
                        difference=diff, status="MISMATCH"
                    ))
                    
    return mismatches

@router.get("/gst/export")
async def export_gst(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    import io
    import csv
    
    # Combine Invoice and VendorBill
    invoices = await db.scalars(select(Invoice))
    vendor_bills = await db.scalars(select(VendorBill))
    
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow([
        "Invoice No", "Date", "Type", "Party", "GSTIN",
        "Taxable Amount", "GST Amount", "Total Amount", "Status"
    ])
    
    for inv in invoices:
        writer.writerow([
            str(inv.id),
            inv.created_at.date().isoformat(),
            'SALES',
            'Customer', # Simplified
            '',
            float(inv.amount),
            float(inv.gst_amount),
            float(inv.total_amount),
            inv.status
        ])
        
    for vb in vendor_bills:
        writer.writerow([
            vb.bill_number,
            vb.bill_date.isoformat() if vb.bill_date else '',
            'PURCHASE',
            'Vendor', # Simplified
            '',
            float(vb.total_amount),
            0.0, # Simplified
            float(vb.total_amount),
            vb.status
        ])
        
    response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=gst_register.csv"
    return response

@router.post("/gst/import", response_model=GSTImportResult)
async def import_gst(
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session)
):
    content = await file.read()
    text = content.decode('utf-8')
    lines = text.splitlines()
    
    total = max(0, len(lines) - 1)
    valid = 0
    errors = []
    
    for i, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = line.split(',')
        if len(parts) < 3:
            errors.append(f"Line {i}: Invalid format")
        else:
            valid += 1
            
    # Do NOT mutate database here
    
    return GSTImportResult(
        total_records=total,
        valid_records=valid,
        errors=errors
    )
