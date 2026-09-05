import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload
from decimal import Decimal
from datetime import date
from fastapi import HTTPException

from app.models.accountant import (
    PaymentVoucher,
    VendorBill,
    JournalEntry,
    JournalLine,
    Account,
    TDSDeduction,
    BankAccount,
)
from app.models.invoice import Transaction
from app.models.user import User
from app.models.project import Project
from app.models.material import Supplier
from app.models.contractor import Contractor
from app.models.settings import CompanySettings
from app.utils.helpers import InvalidStateError
from app.utils.common import generate_business_id
from app.core.enums import VendorBillStatus

logger = logging.getLogger(__name__)


class PaymentService:
    @staticmethod
    def _is_sa(user: User) -> bool:
        return getattr(user, "is_super_admin", False) is True

    @staticmethod
    def _check_tenant(user: User) -> bool:
        is_sa = PaymentService._is_sa(user)
        if not is_sa and user.company_id is None:
            raise HTTPException(status_code=403, detail="User must belong to a company")
        return is_sa

    @staticmethod
    async def create_pending_voucher(db: AsyncSession, current_user: User, **kwargs) -> PaymentVoucher:
        is_sa = PaymentService._check_tenant(current_user)

        # 1. Validate VendorBill
        vendor_bill_id = kwargs.get("vendor_bill_id")
        if not vendor_bill_id:
            raise HTTPException(status_code=400, detail="vendor_bill_id is required")

        bill = await db.get(VendorBill, vendor_bill_id)
        if not bill:
            raise HTTPException(status_code=404, detail="VendorBill not found")

        # Tenant isolation on bill
        if not is_sa and bill.company_id != current_user.company_id:
            raise HTTPException(status_code=404, detail="VendorBill not found")

        owning_company_id = bill.company_id

        # Validate project if present
        if bill.project_id:
            project = await db.get(Project, bill.project_id)
            if not project or project.company_id != owning_company_id:
                raise HTTPException(status_code=404, detail="VendorBill not found")

        # Validate VendorBill status invariant
        eligible_statuses = [
            VendorBillStatus.APPROVED.value if hasattr(VendorBillStatus.APPROVED, "value") else "APPROVED",
            VendorBillStatus.PARTIAL.value if hasattr(VendorBillStatus.PARTIAL, "value") else "PARTIAL",
            "APPROVED",
            "PARTIAL",
        ]
        if bill.status not in eligible_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"VendorBill must be APPROVED or PARTIAL to create payment voucher (current: {bill.status})",
            )

        # 2. Validate Party
        party_type = kwargs.get("party_type")
        if party_type == "Vendor":
            supplier_id = kwargs.get("supplier_id")
            if not supplier_id:
                raise HTTPException(status_code=400, detail="supplier_id is required for Vendor party")
            supplier = await db.get(Supplier, supplier_id)
            if not supplier or supplier.company_id != owning_company_id:
                raise HTTPException(status_code=404, detail="Supplier not found")
            if bill.supplier_id and supplier.id != bill.supplier_id:
                raise HTTPException(status_code=404, detail="Supplier does not match vendor bill")
        elif party_type == "Contractor":
            contractor_id = kwargs.get("contractor_id")
            if not contractor_id:
                raise HTTPException(status_code=400, detail="contractor_id is required for Contractor party")
            contractor = await db.get(Contractor, contractor_id)
            if not contractor or contractor.company_id != owning_company_id:
                raise HTTPException(status_code=404, detail="Contractor not found")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported party_type: {party_type}")

        # 3. Validate BankAccount
        bank_account_id = kwargs.get("bank_account_id")
        if not bank_account_id:
            raise HTTPException(status_code=400, detail="bank_account_id is required")
        bank_acc = await db.scalar(
            select(BankAccount).where(
                BankAccount.id == bank_account_id,
                BankAccount.company_id == owning_company_id,
            )
        )
        if not bank_acc:
            raise HTTPException(status_code=404, detail="Bank account not found")

        # 4. Validate Gross Amount & Balance
        try:
            gross = Decimal(str(kwargs.get("gross_amount", 0)))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid gross_amount")

        if gross <= Decimal("0"):
            raise HTTPException(status_code=400, detail="Gross amount must be greater than 0")

        outstanding = Decimal(str(bill.total_amount)) - Decimal(str(bill.amount_paid or 0))
        if gross > outstanding:
            raise HTTPException(
                status_code=400,
                detail="Payment gross amount exceeds outstanding balance",
            )

        # 5. Generate Voucher Number and Persist
        pv_number = await generate_business_id(db, PaymentVoucher, "payment_voucher_number", "PV-", padding=5)

        pv = PaymentVoucher(
            payment_voucher_number=pv_number,
            status="PENDING",
            created_by=current_user.id,
            **kwargs,
        )
        db.add(pv)
        await db.commit()
        await db.refresh(pv)
        return pv

    @staticmethod
    async def mark_paid(db: AsyncSession, current_user: User, voucher_id: int) -> PaymentVoucher:
        is_sa = PaymentService._check_tenant(current_user)

        # Step 1: Pre-resolve PaymentVoucher WITHOUT row lock to verify existence & tenant
        pre_pv = await db.get(PaymentVoucher, voucher_id)
        if not pre_pv:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        # Step 2: Pre-resolve VendorBill and owning company WITHOUT row lock
        if not pre_pv.vendor_bill_id:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        pre_bill = await db.get(VendorBill, pre_pv.vendor_bill_id)
        if not pre_bill:
            raise HTTPException(status_code=404, detail="Associated VendorBill not found")

        owning_company_id = pre_bill.company_id
        if not is_sa and owning_company_id != current_user.company_id:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        if pre_bill.project_id:
            project = await db.get(Project, pre_bill.project_id)
            if not project or project.company_id != owning_company_id:
                raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        # Step 3: Now acquire row locks safely on owned records only
        pv_res = await db.execute(
            select(PaymentVoucher).where(PaymentVoucher.id == voucher_id).with_for_update()
        )
        pv = pv_res.scalar_one_or_none()
        if not pv:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        bill_res = await db.execute(
            select(VendorBill).where(VendorBill.id == pv.vendor_bill_id).with_for_update()
        )
        bill = bill_res.scalar_one_or_none()
        if not bill:
            raise HTTPException(status_code=404, detail="Associated VendorBill not found")

        # Step 4: Validate state after lock
        if pv.status != "PENDING":
            raise InvalidStateError("Only PENDING vouchers can be marked PAID")

        outstanding = Decimal(str(bill.total_amount)) - Decimal(str(bill.amount_paid or 0))
        if pv.gross_amount > outstanding:
            raise HTTPException(
                status_code=400,
                detail="Payment gross amount exceeds outstanding balance",
            )

        # Rounding & Amount Calculations
        if pv.gross_amount == outstanding:
            remaining_gst = Decimal(str(bill.gst_amount or 0)) - (
                Decimal(str(bill.amount_paid or 0))
                * Decimal(str(bill.gst_amount or 0))
                / Decimal(str(bill.total_amount))
            )
            pv.gst_amount = remaining_gst
            pv.base_amount = outstanding - remaining_gst
        else:
            payment_ratio = pv.gross_amount / Decimal(str(bill.total_amount))
            pv.base_amount = round(
                Decimal(str(bill.total_amount - (bill.gst_amount or 0))) * payment_ratio, 2
            )
            pv.gst_amount = round(Decimal(str(bill.gst_amount or 0)) * payment_ratio, 2)

        pv.net_payable_amount = pv.gross_amount - pv.tds_amount - pv.retention_amount

        # Step 5: SCOPED Account Lookups (Strictly Tenant-Scoped)
        if pv.party_type == "Vendor":
            payable_acc = await db.scalar(
                select(Account).where(
                    Account.code == "VENDOR_PAYABLE",
                    Account.company_id == owning_company_id,
                )
            )
        else:
            payable_acc = await db.scalar(
                select(Account).where(
                    Account.code.in_(["CONTRACTOR_PAYABLE", "LIA-001", "LIA-CONTRACTOR"]),
                    Account.company_id == owning_company_id,
                )
            )

        if not payable_acc:
            raise HTTPException(
                status_code=400,
                detail=f"Payable account not configured for {pv.party_type}",
            )

        # Scoped Bank Account Lookup
        bank_acc_obj = await db.scalar(
            select(BankAccount).where(
                BankAccount.id == pv.bank_account_id,
                BankAccount.company_id == owning_company_id,
            )
        )
        if not bank_acc_obj or not bank_acc_obj.account_id:
            raise HTTPException(
                status_code=404,
                detail="Bank account not found or not configured properly",
            )

        # Verify bank ledger account is tenant-scoped
        bank_ledger_acc = await db.scalar(
            select(Account.id).where(
                Account.id == bank_acc_obj.account_id,
                Account.company_id == owning_company_id,
            )
        )
        if not bank_ledger_acc:
            raise HTTPException(
                status_code=400,
                detail="Bank ledger account does not belong to the voucher company",
            )

        # Step 6: Create Journal Entry & Lines
        je = JournalEntry(
            description=f"Payment for {pv.payment_voucher_number} - {bill.bill_number}",
            entry_date=date.today(),
            entry_type="Payment",
            status="Posted",
            created_by=current_user.id,
        )
        db.add(je)
        await db.flush()

        lines = [
            # Dr Payable (Gross)
            JournalLine(
                entry_id=je.id,
                account_id=payable_acc.id,
                debit=pv.gross_amount,
                credit=Decimal(0),
            ),
            # Cr Bank (Net)
            JournalLine(
                entry_id=je.id,
                account_id=bank_acc_obj.account_id,
                debit=Decimal(0),
                credit=pv.net_payable_amount,
            ),
        ]

        # Cr TDS (Tenant-Scoped)
        if pv.tds_amount > 0:
            tds_acc = None
            comp_settings = await db.scalar(
                select(CompanySettings).where(CompanySettings.company_id == owning_company_id)
            )
            if comp_settings and getattr(comp_settings, "tds_payable_account_id", None):
                tds_acc = await db.scalar(
                    select(Account).where(
                        Account.id == comp_settings.tds_payable_account_id,
                        Account.company_id == owning_company_id,
                    )
                )

            if not tds_acc:
                tds_acc = await db.scalar(
                    select(Account).where(
                        Account.code.in_(["TDS_PAYABLE", "TDS", "DUTIES_AND_TAXES"]),
                        Account.company_id == owning_company_id,
                    )
                )

            if not tds_acc:
                raise HTTPException(status_code=400, detail="TDS payable account not configured")

            lines.append(
                JournalLine(
                    entry_id=je.id,
                    account_id=tds_acc.id,
                    debit=Decimal(0),
                    credit=pv.tds_amount,
                )
            )

            party_name = "Vendor" if pv.party_type == "Vendor" else "Contractor"
            tds_deduction = TDSDeduction(
                party_name=party_name,
                invoice_number=bill.bill_number,
                payment_amount=pv.gross_amount,
                tds_section="194C",
                tds_rate=0,
                tds_amount=pv.tds_amount,
                status="PENDING",
                vendor_bill_id=bill.id,
                created_by=current_user.id,
            )
            db.add(tds_deduction)

        # Cr Retention (Tenant-Scoped)
        if pv.retention_amount > 0:
            comp_settings = await db.scalar(
                select(CompanySettings).where(CompanySettings.company_id == owning_company_id)
            )
            if not comp_settings or not comp_settings.retention_payable_account_id:
                raise HTTPException(
                    status_code=400,
                    detail="Retention payable account not configured",
                )

            retention_acc = await db.scalar(
                select(Account).where(
                    Account.id == comp_settings.retention_payable_account_id,
                    Account.company_id == owning_company_id,
                )
            )
            if not retention_acc:
                raise HTTPException(
                    status_code=400,
                    detail="Retention payable account not found for company",
                )

            lines.append(
                JournalLine(
                    entry_id=je.id,
                    account_id=retention_acc.id,
                    debit=Decimal(0),
                    credit=pv.retention_amount,
                )
            )

        db.add_all(lines)

        # Step 7: Create Transaction with deterministic reference linking
        tx_ref = pv.reference_no or f"PV-{pv.payment_voucher_number}"
        tx = Transaction(
            project_id=bill.project_id,
            type="payment",
            amount=pv.net_payable_amount,
            mode=pv.payment_method,
            reference=tx_ref,
            linked_to=f"vendor_bill:{bill.id}",
            journal_entry_id=je.id,
            created_by=current_user.id,
        )
        db.add(tx)

        pv.journal_entry_id = je.id
        pv.status = "PAID"
        bill.amount_paid = Decimal(str(bill.amount_paid or 0)) + pv.gross_amount

        if Decimal(str(bill.total_amount)) <= bill.amount_paid:
            bill.status = "PAID"
        else:
            bill.status = "PARTIAL"

        await db.commit()
        await db.refresh(pv)
        return pv

    @staticmethod
    async def cancel(db: AsyncSession, current_user: User, voucher_id: int) -> PaymentVoucher:
        is_sa = PaymentService._check_tenant(current_user)

        # Step 1: Pre-resolve PaymentVoucher WITHOUT row lock
        pre_pv = await db.get(PaymentVoucher, voucher_id)
        if not pre_pv:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        # Step 2: Establish tenant ownership boundary safely
        if not pre_pv.vendor_bill_id:
            # Without an owning vendor bill, fail closed to prevent tenant bypass
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        pre_bill = await db.get(VendorBill, pre_pv.vendor_bill_id)
        if not pre_bill:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        owning_company_id = pre_bill.company_id
        if not is_sa and owning_company_id != current_user.company_id:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        if pre_bill.project_id:
            project = await db.get(Project, pre_bill.project_id)
            if not project or project.company_id != owning_company_id:
                raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        # Step 3: Now acquire row lock safely on pre-verified records only
        pv_res = await db.execute(
            select(PaymentVoucher).where(PaymentVoucher.id == voucher_id).with_for_update()
        )
        pv = pv_res.scalar_one_or_none()
        if not pv:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        bill_res = await db.execute(
            select(VendorBill).where(VendorBill.id == pv.vendor_bill_id).with_for_update()
        )
        bill = bill_res.scalar_one_or_none()
        if not bill:
            raise HTTPException(status_code=404, detail="PaymentVoucher not found")

        # Step 4: Handle PENDING cancellation
        if pv.status == "PENDING":
            pv.status = "CANCELLED"
            await db.commit()
            await db.refresh(pv)
            return pv

        if pv.status != "PAID":
            raise InvalidStateError("Only PENDING or PAID vouchers can be cancelled")

        if pv.tds_amount > 0:
            tds_check = await db.scalar(
                select(TDSDeduction).where(
                    TDSDeduction.vendor_bill_id == pv.vendor_bill_id,
                    TDSDeduction.status != "PENDING",
                )
            )
            if tds_check:
                raise InvalidStateError("Cannot cancel: TDS has already been processed or remitted.")

        if pv.retention_amount > 0:
            raise InvalidStateError("Cannot cancel: Retention tracking mechanism requires admin override.")

        # Step 5: Reverse Journal Entry if posted
        rev_je = None
        if pv.journal_entry_id:
            orig_je = await db.get(
                JournalEntry,
                pv.journal_entry_id,
                options=[selectinload(JournalEntry.lines)],
            )
            if orig_je:
                rev_je = JournalEntry(
                    description=f"Reversal of Payment {pv.payment_voucher_number}",
                    entry_date=date.today(),
                    entry_type="Reversal",
                    status="Posted",
                    created_by=current_user.id,
                )
                db.add(rev_je)
                await db.flush()

                rev_lines = [
                    JournalLine(
                        entry_id=rev_je.id,
                        account_id=line.account_id,
                        debit=line.credit,
                        credit=line.debit,
                    )
                    for line in orig_je.lines
                ]
                db.add_all(rev_lines)

        # Step 6: Deterministic Transaction Lookup (Strict Linkage)
        orig_tx = None
        if pv.journal_entry_id:
            orig_tx = await db.scalar(
                select(Transaction).where(
                    Transaction.journal_entry_id == pv.journal_entry_id,
                    Transaction.type == "payment",
                )
            )

        if not orig_tx:
            target_refs = [f"PV-{pv.payment_voucher_number}"]
            if pv.reference_no:
                target_refs.append(pv.reference_no)
            orig_tx = await db.scalar(
                select(Transaction).where(
                    Transaction.reference.in_(target_refs),
                    Transaction.linked_to == f"vendor_bill:{bill.id}",
                    Transaction.type == "payment",
                )
            )

        if not orig_tx and bill.project_id:
            orig_tx = await db.scalar(
                select(Transaction).where(
                    Transaction.linked_to == f"vendor_bill:{bill.id}",
                    Transaction.amount == pv.net_payable_amount,
                    Transaction.type == "payment",
                    Transaction.project_id == bill.project_id,
                )
            )

        if orig_tx:
            rev_ref = f"REV-{orig_tx.reference or pv.payment_voucher_number}"
            rev_tx = Transaction(
                project_id=bill.project_id,
                type="payment",
                amount=-orig_tx.amount,
                mode=orig_tx.mode,
                reference=rev_ref,
                linked_to=orig_tx.linked_to,
                journal_entry_id=rev_je.id if rev_je else None,
                created_by=current_user.id,
            )
            db.add(rev_tx)

        # Step 7: Update state
        pv.status = "CANCELLED"
        bill.amount_paid = Decimal(str(bill.amount_paid or 0)) - pv.gross_amount
        if bill.amount_paid <= 0:
            bill.status = "APPROVED"
        else:
            bill.status = "PARTIAL"

        await db.commit()
        await db.refresh(pv)
        return pv
