from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from decimal import Decimal
from datetime import date
from fastapi import HTTPException
from app.models.accountant import PaymentVoucher, VendorBill, JournalEntry, JournalLine, Account, TDSDeduction
from app.models.invoice import Transaction
from app.models.user import User
from app.utils.helpers import InvalidStateError
from app.utils.common import generate_business_id

class PaymentService:
    @staticmethod
    async def create_pending_voucher(db: AsyncSession, current_user: User, **kwargs) -> PaymentVoucher:
        # Validate VendorBill
        vendor_bill_id = kwargs.get('vendor_bill_id')
        if not vendor_bill_id:
            raise HTTPException(status_code=400, detail="vendor_bill_id is required")
        
        bill = await db.get(VendorBill, vendor_bill_id)
        if not bill:
            raise HTTPException(status_code=404, detail="VendorBill not found")
        
        if kwargs.get('party_type') == 'Vendor' and not kwargs.get('supplier_id'):
            raise HTTPException(status_code=400, detail="supplier_id is required for Vendor party")
        if kwargs.get('party_type') == 'Contractor' and not kwargs.get('contractor_id'):
            raise HTTPException(status_code=400, detail="contractor_id is required for Contractor party")

        pv_number = await generate_business_id(db, PaymentVoucher, "payment_voucher_number", "PV-", padding=5)
        
        pv = PaymentVoucher(
            payment_voucher_number=pv_number,
            status="PENDING",
            created_by=current_user.id,
            **kwargs
        )
        db.add(pv)
        await db.commit()
        await db.refresh(pv)
        return pv

    @staticmethod
    async def mark_paid(db: AsyncSession, current_user: User, voucher_id: int) -> PaymentVoucher:
        # Lock Voucher and Bill
        pv_res = await db.execute(select(PaymentVoucher).where(PaymentVoucher.id == voucher_id).with_for_update())
        pv = pv_res.scalar_one_or_none()
        if not pv:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")
        
        if pv.status != "PENDING":
            raise InvalidStateError("Only PENDING vouchers can be marked PAID")

        bill_res = await db.execute(select(VendorBill).where(VendorBill.id == pv.vendor_bill_id).with_for_update())
        bill = bill_res.scalar_one_or_none()
        if not bill:
            raise HTTPException(status_code=404, detail="Associated VendorBill not found")

        outstanding = Decimal(str(bill.total_amount)) - Decimal(str(bill.amount_paid))
        if pv.gross_amount > outstanding:
             raise HTTPException(status_code=400, detail="Payment gross amount exceeds outstanding balance")
        
        # Rounding logic
        if pv.gross_amount == outstanding:
            # Exact remaining
            remaining_gst = Decimal(str(bill.gst_amount or 0)) - (Decimal(str(bill.amount_paid)) * Decimal(str(bill.gst_amount or 0)) / Decimal(str(bill.total_amount)))
            pv.gst_amount = remaining_gst
            pv.base_amount = outstanding - remaining_gst
        else:
            payment_ratio = pv.gross_amount / Decimal(str(bill.total_amount))
            pv.base_amount = round(Decimal(str(bill.total_amount - (bill.gst_amount or 0))) * payment_ratio, 2)
            pv.gst_amount = round(Decimal(str(bill.gst_amount or 0)) * payment_ratio, 2)
        
        pv.net_payable_amount = pv.gross_amount - pv.tds_amount - pv.retention_amount

        # Find payable account
        if pv.party_type == 'Vendor':
             payable_acc = await db.scalar(select(Account).where(Account.code == "VENDOR_PAYABLE"))
        else:
             payable_acc = await db.scalar(select(Account).where(Account.code.in_(["CONTRACTOR_PAYABLE", "LIA-001", "LIA-CONTRACTOR"])))
             
        if not payable_acc:
             raise HTTPException(status_code=400, detail=f"Payable account not configured for {pv.party_type}")
             
        # Bank Account
        from app.models.accountant import BankAccount
        bank_acc_obj = await db.get(BankAccount, pv.bank_account_id)
        if not bank_acc_obj or not bank_acc_obj.account_id:
             raise HTTPException(status_code=400, detail="Bank account not configured properly")

        # Create Journal Entry
        je = JournalEntry(
            description=f"Payment for {pv.payment_voucher_number} - {bill.bill_number}",
            entry_date=date.today(),
            entry_type="Payment",
            status="Posted",
            created_by=current_user.id
        )
        db.add(je)
        await db.flush()
        
        lines = []
        # Dr Payable (Gross)
        lines.append(JournalLine(entry_id=je.id, account_id=payable_acc.id, debit=pv.gross_amount, credit=Decimal(0)))
        
        # Cr Bank (Net)
        lines.append(JournalLine(entry_id=je.id, account_id=bank_acc_obj.account_id, debit=Decimal(0), credit=pv.net_payable_amount))
        
        # Cr TDS
        if pv.tds_amount > 0:
             from app.utils.accounting import resolve_tax_accounts
             tds_acc = await resolve_tax_accounts(db, 'tds_payable')
             if not tds_acc:
                  raise HTTPException(status_code=400, detail="TDS payable account not configured")
             lines.append(JournalLine(entry_id=je.id, account_id=tds_acc.id, debit=Decimal(0), credit=pv.tds_amount))
             
             party_name = "Vendor" if pv.party_type == 'Vendor' else "Contractor"
             
             tds_deduction = TDSDeduction(
                 party_name=party_name,
                 invoice_number=bill.bill_number,
                 payment_amount=pv.gross_amount,
                 tds_section="194C",
                 tds_rate=0,
                 tds_amount=pv.tds_amount,
                 status="PENDING",
                 vendor_bill_id=bill.id,
                 created_by=current_user.id
             )
             db.add(tds_deduction)

        # Cr Retention
        if pv.retention_amount > 0:
             from app.models.settings import CompanySettings
             settings = await db.scalar(select(CompanySettings))
             if not settings or not settings.retention_payable_account_id:
                  raise HTTPException(status_code=400, detail="Retention payable account not configured")
             lines.append(JournalLine(entry_id=je.id, account_id=settings.retention_payable_account_id, debit=Decimal(0), credit=pv.retention_amount))
             
        db.add_all(lines)
        
        # Transaction
        tx = Transaction(
            project_id=bill.project_id,
            type="payment",
            amount=pv.net_payable_amount,
            mode=pv.payment_method,
            reference=pv.reference_no,
            linked_to=f"vendor_bill:{bill.id}",
            journal_entry_id=je.id,
            created_by=current_user.id
        )
        db.add(tx)
        
        pv.journal_entry_id = je.id
        pv.status = "PAID"
        bill.amount_paid = Decimal(str(bill.amount_paid)) + pv.gross_amount
        
        if Decimal(str(bill.total_amount)) <= bill.amount_paid:
            bill.status = "PAID"
        else:
            bill.status = "PARTIAL"

        await db.commit()
        await db.refresh(pv)
        return pv

    @staticmethod
    async def cancel(db: AsyncSession, current_user: User, voucher_id: int) -> PaymentVoucher:
        pv_res = await db.execute(select(PaymentVoucher).where(PaymentVoucher.id == voucher_id).with_for_update())
        pv = pv_res.scalar_one_or_none()
        if not pv:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")
        
        if pv.status == "PENDING":
            pv.status = "CANCELLED"
            await db.commit()
            return pv
            
        if pv.status != "PAID":
            raise InvalidStateError("Only PENDING or PAID vouchers can be cancelled")
            
        if pv.tds_amount > 0:
            tds_check = await db.scalar(select(TDSDeduction).where(TDSDeduction.vendor_bill_id == pv.vendor_bill_id, TDSDeduction.status != "PENDING"))
            if tds_check:
                raise InvalidStateError("Cannot cancel: TDS has already been processed or remitted.")
                
        if pv.retention_amount > 0:
            raise InvalidStateError("Cannot cancel: Retention tracking mechanism requires admin override.")

        bill_res = await db.execute(select(VendorBill).where(VendorBill.id == pv.vendor_bill_id).with_for_update())
        bill = bill_res.scalar_one_or_none()
        
        orig_je = await db.get(JournalEntry, pv.journal_entry_id, options=[selectinload(JournalEntry.lines)])
        if orig_je:
            rev_je = JournalEntry(
                description=f"Reversal of Payment {pv.payment_voucher_number}",
                entry_date=date.today(),
                entry_type="Reversal",
                status="Posted",
                created_by=current_user.id
            )
            db.add(rev_je)
            await db.flush()
            
            rev_lines = []
            for line in orig_je.lines:
                rev_lines.append(JournalLine(
                    entry_id=rev_je.id,
                    account_id=line.account_id,
                    debit=line.credit,
                    credit=line.debit
                ))
            db.add_all(rev_lines)
            
        orig_tx = await db.scalar(select(Transaction).where(Transaction.linked_to == f"vendor_bill:{bill.id}", Transaction.amount == pv.net_payable_amount, Transaction.type == "payment"))
        if orig_tx:
            rev_tx = Transaction(
                project_id=bill.project_id,
                type="payment",
                amount=-orig_tx.amount,
                mode=orig_tx.mode,
                reference=f"REV-{orig_tx.reference}" if orig_tx.reference else f"REV-{pv.payment_voucher_number}",
                linked_to=orig_tx.linked_to,
                journal_entry_id=rev_je.id if orig_je else None,
                created_by=current_user.id
            )
            db.add(rev_tx)
            
        pv.status = "CANCELLED"
        if bill:
            bill.amount_paid = Decimal(str(bill.amount_paid)) - pv.gross_amount
            if bill.amount_paid <= 0:
                bill.status = "APPROVED"
            else:
                bill.status = "PARTIAL"
                
        await db.commit()
        await db.refresh(pv)
        return pv
