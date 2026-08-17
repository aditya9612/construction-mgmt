from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
from datetime import date
from decimal import Decimal

from app.core.db import AsyncSessionLocal
from app.db.session import get_db_session
from app.core.dependencies import require_roles
from app.models.user import User
from app.models.accountant import VendorBill, VendorBillItem, JournalEntry, JournalLine, Account
from app.models.invoice import Transaction
from app.models.material import Supplier
from app.models.project import ProjectMember
from app.services.notification_service import create_notification
from app.core.logger import logger
from app.core.enums import VendorBillStatus
from app.schemas.accountant import (
    VendorBillCreate,
    VendorBillOut,
    VendorBillApprovalRequest,
    VendorBillPaymentRequest
)

router = APIRouter(prefix="/vendor-bills", tags=["vendor-bills"])

ACCOUNTANT_READ_ROLES = ["Admin", "Accountant", "Project Manager"]
ACCOUNTANT_WRITE_ROLES = ["Admin", "Accountant"]

@router.post("", response_model=VendorBillOut, status_code=201)
async def create_vendor_bill(
    payload: VendorBillCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES))
):
    # Check if bill number already exists
    existing = await db.scalar(select(VendorBill).where(VendorBill.bill_number == payload.bill_number))
    if existing:
        raise HTTPException(status_code=400, detail="Bill number already exists")
        
    bill = VendorBill(
        supplier_id=payload.supplier_id,
        project_id=payload.project_id,
        purchase_order_id=payload.purchase_order_id,
        bill_number=payload.bill_number,
        bill_date=payload.bill_date,
        due_date=payload.due_date,
        grn_number=payload.grn_number,
        gross_amount=payload.gross_amount,
        gst_percent=payload.gst_percent,
        gst_amount=payload.gst_amount,
        tds_percent=payload.tds_percent,
        tds_amount=payload.tds_amount,
        advance_paid=payload.advance_paid,
        total_amount=payload.total_amount,
        vendor_invoice_url=payload.vendor_invoice_url,
        po_copy_url=payload.po_copy_url,
        grn_copy_url=payload.grn_copy_url,
        supporting_docs_url=payload.supporting_docs_url,
        status=VendorBillStatus.PENDING.value,
        amount_paid=0.0
    )
    
    db.add(bill)
    await db.flush()
    
    for item in payload.items:
        bill_item = VendorBillItem(
            vendor_bill_id=bill.id,
            material_name=item.material_name,
            category=item.category,
            quantity=item.quantity,
            unit=item.unit,
            rate=item.rate,
            total=item.total
        )
        db.add(bill_item)
        
    await db.commit()
    await db.refresh(bill)

    try:
        async with AsyncSessionLocal() as notif_db:
            members = await notif_db.scalars(
                select(ProjectMember.user_id).where(ProjectMember.project_id == payload.project_id)
            )
            for member_id in members.all():
                await create_notification(
                    db=notif_db,
                    user_id=member_id,
                    title="Vendor Bill Created",
                    message=f"Vendor Bill {bill.bill_number} has been created for project.",
                    type="INFO"
                )
            await notif_db.commit()
    except Exception as e:
        logger.error(f"Failed to create notification for vendor bill creation: {e}")
    
    return await _get_bill_with_details(db, bill.id)

@router.get("", response_model=List[VendorBillOut])
async def list_vendor_bills(
    status: str = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES))
):
    query = select(VendorBill, Supplier.name.label("supplier_name")).outerjoin(Supplier, Supplier.id == VendorBill.supplier_id)
    if status:
        query = query.where(VendorBill.status == status)
    
    query = query.order_by(VendorBill.created_at.desc())
    
    results = await db.execute(query)
    
    out = []
    for bill, supplier_name in results:
        bill_out = VendorBillOut.from_orm(bill)
        bill_out.supplier_name = supplier_name
        
        items = await db.scalars(select(VendorBillItem).where(VendorBillItem.vendor_bill_id == bill.id))
        bill_out.items = list(items.all())
        out.append(bill_out)
        
    return out

@router.get("/{id}", response_model=VendorBillOut)
async def get_vendor_bill(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES))
):
    bill_out = await _get_bill_with_details(db, id)
    if not bill_out:
        raise HTTPException(status_code=404, detail="Vendor Bill not found")
    return bill_out

@router.post("/{id}/approve")
async def approve_vendor_bill(
    id: int,
    payload: VendorBillApprovalRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES))
):
    from sqlalchemy.orm import with_for_update
    from app.models.accountant import Account, JournalEntry, JournalLine
    from datetime import date
    from decimal import Decimal
    
    # Lock for update
    result = await db.execute(select(VendorBill).where(VendorBill.id == id).with_for_update())
    bill = result.scalar_one_or_none()
    
    if not bill:
        raise HTTPException(status_code=404, detail="Vendor Bill not found")
        
    if bill.status not in [VendorBillStatus.PENDING.value]:
        raise HTTPException(status_code=400, detail="Only pending bills can be approved or rejected")
        
    if payload.status not in [VendorBillStatus.APPROVED.value, VendorBillStatus.REJECTED.value]:
        raise HTTPException(status_code=400, detail="Invalid status. Must be APPROVED or REJECTED.")
        
    if payload.status == VendorBillStatus.APPROVED.value and not bill.accrued_journal_id:
        # Create Accrual Journal
        # Dr Expense, Dr INPUT_GST, Cr VENDOR_PAYABLE
        vendor_acc = await db.scalar(select(Account).where(Account.code == "VENDOR_PAYABLE"))
        expense_acc = await db.scalar(select(Account).where(Account.code == "EXPENSE")) 
        gst_acc = await db.scalar(select(Account).where(Account.code == "INPUT_GST"))
        
        if not vendor_acc:
             raise HTTPException(status_code=400, detail="VENDOR_PAYABLE account is not configured.")
        if not expense_acc:
             raise HTTPException(status_code=400, detail="EXPENSE account is not configured.")
        
        base_amount = Decimal(str(bill.total_amount - (bill.gst_amount or 0)))
        gst_amount = Decimal(str(bill.gst_amount or 0))
        gross_amount = Decimal(str(bill.total_amount))
        
        je = JournalEntry(
            description=f"Accrual for Vendor Bill {bill.bill_number}",
            entry_date=date.today(),
            entry_type="Auto",
            status="Posted",
            created_by=current_user.id
        )
        db.add(je)
        await db.flush()
        
        lines = []
        # Dr Expense
        lines.append(JournalLine(entry_id=je.id, account_id=expense_acc.id, debit=base_amount, credit=Decimal(0)))
        
        # Dr GST
        if gst_amount > 0 and gst_acc:
            lines.append(JournalLine(entry_id=je.id, account_id=gst_acc.id, debit=gst_amount, credit=Decimal(0)))
        elif gst_amount > 0 and not gst_acc:
            raise HTTPException(status_code=400, detail="INPUT_GST account is not configured but bill has GST.")
            
        # Cr Payable
        lines.append(JournalLine(entry_id=je.id, account_id=vendor_acc.id, debit=Decimal(0), credit=gross_amount))
        
        db.add_all(lines)
        await db.flush()
        
        bill.accrued_journal_id = je.id

    bill.status = payload.status

    await db.commit()

    try:
        async with AsyncSessionLocal() as notif_db:
            members = await notif_db.scalars(
                select(ProjectMember.user_id).where(ProjectMember.project_id == bill.project_id)
            )
            for member_id in members.all():
                await create_notification(
                    db=notif_db,
                    user_id=member_id,
                    title=f"Vendor Bill {payload.status.capitalize()}",
                    message=f"Vendor Bill {bill.bill_number} has been {payload.status.lower()}.",
                    type="SUCCESS" if payload.status == VendorBillStatus.APPROVED.value else "WARNING"
                )
            await notif_db.commit()
    except Exception as e:
        logger.error(f"Failed to create notification for vendor bill approval: {e}")
    
    return {"message": f"Bill {payload.status.lower()} successfully", "status": bill.status}

@router.post("/{id}/pay")
async def pay_vendor_bill(
    id: int,
    payload: VendorBillPaymentRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES))
):
    bill = await db.get(VendorBill, id)
    if not bill:
        raise HTTPException(status_code=404, detail="Vendor Bill not found")
        
    if bill.status not in [VendorBillStatus.APPROVED.value, VendorBillStatus.PARTIAL.value, VendorBillStatus.PAID.value]:
        raise HTTPException(status_code=400, detail="Bill must be approved before payment")
        
    if bill.accrued_journal_id is not None:
        raise HTTPException(status_code=400, detail="This bill uses accrual accounting. Please use the Payment Voucher module to settle it.")
        
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid payment amount")
        
    pending = Decimal(str(bill.total_amount)) - Decimal(str(bill.amount_paid))
    req_amount = Decimal(str(payload.amount))
    
    if req_amount > pending:
        raise HTTPException(status_code=400, detail="Payment amount exceeds pending amount")
        
    from app.core.enums import AccountType as AT
    
    vendor_acc = await db.scalar(select(Account.id).where(Account.code == "VENDOR_PAYABLE"))
    if not vendor_acc:
        raise HTTPException(status_code=400, detail="Vendor liability account is not configured.")
        
    bank_acc = await db.scalar(select(Account.id).where(Account.code == "BANK"))
    if not bank_acc:
        bank_acc = await db.scalar(select(Account.id).where(Account.type == AT.ASSET))
        
    if not vendor_acc or not bank_acc:
        raise HTTPException(status_code=400, detail="Required accounts not configured for journal entry")
        
    entry = JournalEntry(description=f"Payment for Vendor Bill {bill.bill_number}")
    db.add(entry)
    await db.flush()
    
    txn = Transaction(
        project_id=bill.project_id,
        type="payment",
        amount=payload.amount,
        mode=payload.mode.value if hasattr(payload.mode, 'value') else payload.mode,
        reference=payload.reference,
        linked_to=f"vendor_bill:{bill.id}",
        created_by=current_user.id,
        journal_entry_id=entry.id
    )
    db.add(txn)
    
    db.add_all([
        JournalLine(entry_id=entry.id, account_id=vendor_acc, debit=payload.amount, credit=0),
        JournalLine(entry_id=entry.id, account_id=bank_acc, debit=0, credit=payload.amount)
    ])
    
    new_paid = Decimal(str(bill.amount_paid)) + req_amount
    bill.amount_paid = float(new_paid)
    
    if new_paid >= Decimal(str(bill.total_amount)):
        bill.status = VendorBillStatus.PAID.value
    else:
        bill.status = VendorBillStatus.PARTIAL.value
        
    try:
        await db.commit()
        await db.refresh(bill)
    except Exception:
        await db.rollback()
        logger.exception(
            "Vendor payment failed bill_id=%s",
            id,
        )
        raise
    
    try:
        async with AsyncSessionLocal() as notif_db:
            member_ids = await notif_db.scalars(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == bill.project_id
                )
            )

            for member_id in member_ids.unique().all():
                await create_notification(
                    db=notif_db,
                    user_id=member_id,
                    title="Vendor Bill Paid",
                    message=f"Payment of {payload.amount} made for Vendor Bill {bill.bill_number}.",
                    type="SUCCESS"
                )
            await notif_db.commit()
    except Exception:
        logger.exception(
            "Failed to create notification for vendor bill payment. "
            "bill_id=%s",
            bill.id,
        )

    return {
        "message": "Payment recorded",
        "paid": float(new_paid),
        "pending": float(Decimal(str(bill.total_amount)) - new_paid),
        "status": bill.status
    }


@router.post("/{bill_id}/reverse-payment/{transaction_id}")
async def reverse_vendor_payment(
    bill_id: int,
    transaction_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    # ----------------------------------------
    # Validate Vendor Bill
    # ----------------------------------------
    bill = await db.get(VendorBill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Vendor Bill not found")

    # ----------------------------------------
    # Validate Transaction (with row-level lock)
    # ----------------------------------------
    txn = await db.scalar(
        select(Transaction)
        .where(Transaction.id == transaction_id)
        .with_for_update()
    )
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if txn.type != "payment":
        raise HTTPException(
            status_code=400,
            detail="Transaction is not a payment",
        )

    if txn.linked_to != f"vendor_bill:{bill.id}":
        raise HTTPException(
            status_code=400,
            detail="Transaction does not belong to this vendor bill",
        )

    # Legacy payments created before Phase-1
    if txn.journal_entry_id is None:
        raise HTTPException(
            status_code=400,
            detail="Legacy payment cannot be reversed automatically.",
        )

    # ----------------------------------------
    # Validate Journal Entry
    # ----------------------------------------
    entry = await db.get(JournalEntry, txn.journal_entry_id)
    if not entry:
        logger.error(
            "JournalEntry not found during vendor payment reversal. "
            "bill_id=%s transaction_id=%s journal_entry_id=%s",
            bill_id,
            transaction_id,
            txn.journal_entry_id,
        )
        raise HTTPException(
            status_code=404,
            detail="Journal Entry not found",
        )

    # ----------------------------------------
    # Reverse Accounting Transaction
    # ----------------------------------------
    try:
        
        # Keep existing project behaviour
        new_paid = max(
            Decimal("0.00"),
            Decimal(str(bill.amount_paid)) - Decimal(str(txn.amount))
        )

        bill.amount_paid = float(new_paid)

        if new_paid == Decimal("0.00"):
            bill.status = VendorBillStatus.PENDING.value
        elif new_paid >= Decimal(str(bill.total_amount)):
            bill.status = VendorBillStatus.PAID.value
        else:
            bill.status = VendorBillStatus.PARTIAL.value

        # Delete payment transaction
        await db.delete(txn)

        # JournalLines are removed automatically
        # through cascade="all, delete-orphan"
        await db.delete(entry)

        await db.commit()
        await db.refresh(bill)

    except Exception:
        await db.rollback()

        logger.exception(
            "Vendor payment reversal failed. "
            "bill_id=%s transaction_id=%s",
            bill_id,
            transaction_id,
        )

        raise

    # ----------------------------------------
    # Notification (Independent Transaction)
    # ----------------------------------------
    try:
        async with AsyncSessionLocal() as notif_db:
            member_ids = await notif_db.scalars(
                select(ProjectMember.user_id).where(
                    ProjectMember.project_id == bill.project_id
                )
            )

            # unique() prevents duplicate notifications
            for member_id in member_ids.unique().all():
                await create_notification(
                    db=notif_db,
                    user_id=member_id,
                    title="Vendor Payment Reversed",
                    message=(
                        f"Payment of ₹{txn.amount} "
                        f"for Vendor Bill {bill.bill_number} "
                        f"has been reversed."
                    ),
                    type="WARNING",
                )

            await notif_db.commit()

    except Exception:
        logger.exception(
            "Failed to create notification for vendor payment reversal. "
            "bill_id=%s transaction_id=%s",
            bill_id,
            transaction_id,
        )

    return {
        "message": "Payment reversed successfully",
        "paid": bill.amount_paid,
        "pending": float(
            Decimal(str(bill.total_amount))
            - Decimal(str(bill.amount_paid))
        ),
        "status": bill.status,
    }

async def _get_bill_with_details(db: AsyncSession, bill_id: int):
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(VendorBill, Supplier.supplier_name.label("supplier_name"))
        .options(selectinload(VendorBill.items))
        .outerjoin(Supplier, Supplier.id == VendorBill.supplier_id)
        .where(VendorBill.id == bill_id)
    )
    row = result.first()
    if not row:
        return None
        
    bill, supplier_name = row
    bill_out = VendorBillOut.from_orm(bill)
    bill_out.supplier_name = supplier_name
    
    items = await db.scalars(select(VendorBillItem).where(VendorBillItem.vendor_bill_id == bill.id))
    bill_out.items = list(items.all())
    
    return bill_out
