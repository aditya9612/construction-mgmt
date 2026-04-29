from decimal import Decimal
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.enums import PaymentMode
from app.models.accountant import Account, FixedAsset, JournalEntry, JournalLine
from app.schemas.accountant import (
    AccountCreate,
    AccountOut,
    AssetCreate,
    JournalEntryCreate,
    PayablePaymentRequest,
    ReceiptCreate,
)
from app.db.session import get_db_session
from app.models.billing import RABill
from app.models.invoice import Invoice, Transaction
from app.models.user import User
from app.core.dependencies import get_current_active_user, require_roles

from app.utils.helpers import NotFoundError, ValidationError

from app.models.user import UserRole

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

router = APIRouter(prefix="/accountant", tags=["Accountant"])


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


# ============================
#  PAYABLE VIEW (FROM RA BILL)
# ============================
@router.get("/payables")
async def list_payables(
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (await db.execute(select(RABill))).scalars().all()

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


# ============================
#  PAY CONTRACTOR (PARTIAL SUPPORTED)
# ============================


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

    if payload.amount > pending:
        raise ValidationError("Amount exceeds pending")

    #  Get Account IDs (replace with your actual codes)
    contractor_acc = await db.scalar(
        select(Account.id).where(Account.code == "CONTRACTOR_PAYABLE")
    )

    bank_acc = await db.scalar(select(Account.id).where(Account.code == "BANK"))

    if not contractor_acc or not bank_acc:
        raise ValidationError("Required accounts not configured")

    # =====================
    # 1. CREATE PAYMENT TXN
    # =====================
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

    # =====================
    # 2. JOURNAL ENTRY
    # =====================
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

    # =====================
    # 3. UPDATE RA STATUS
    # =====================
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


# ============================
#  TRANSACTIONS (ALL)
# ============================
@router.get("/transactions")
async def list_transactions(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    rows = (await db.execute(select(Transaction))).scalars().all()
    return rows


# ============================
#  PAYABLE SUMMARY
# ============================
@router.get("/payables/summary")
async def payable_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    rows = (await db.execute(select(RABill))).scalars().all()

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


# ============================
#  CASH FLOW
# ============================
@router.get("/cashflow")
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


# ============================
#  FILTER PAYABLES BY DATE
# ============================
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


@router.post("/accounts", response_model=AccountOut)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES)),
):
    obj = Account(**payload.dict())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    return (await db.execute(select(Account))).scalars().all()


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
    return (await db.execute(select(JournalEntry))).scalars().all()


@router.get("/gst/summary")
async def gst_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    gst = await db.scalar(select(func.sum(Invoice.gst_amount)))
    taxable = await db.scalar(select(func.sum(Invoice.amount)))

    return {
        "total_taxable": float(taxable or 0),
        "total_gst": float(gst or 0),
    }


@router.get("/bank/summary")
async def bank_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES)),
):
    inflow = await db.scalar(
        select(func.sum(Transaction.amount)).where(
            Transaction.type == "receipt", Transaction.mode == PaymentMode.BANK_TRANSFER
        )
    )

    outflow = await db.scalar(
        select(func.sum(Transaction.amount)).where(
            Transaction.type == "payment", Transaction.mode == PaymentMode.BANK_TRANSFER
        )
    )

    return {
        "bank_in": float(inflow or 0),
        "bank_out": float(outflow or 0),
        "balance": float((inflow or 0) - (outflow or 0)),
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
):
    # =========================
    # ACCOUNT BALANCES
    # =========================
    result = await db.execute(
        select(
            Account.id,
            Account.name,
            Account.type,
            func.sum(JournalLine.debit - JournalLine.credit).label("balance"),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .group_by(Account.id)
    )

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

        if r.type == "asset":
            assets.append(item)
            total_assets += balance

        elif r.type == "liability":
            liabilities.append(item)
            total_liabilities += balance

        elif r.type == "equity":
            equity.append(item)
            total_equity += balance

    # =========================
    # PROFIT CALCULATION
    # =========================
    income = await db.scalar(
        select(func.sum(JournalLine.credit - JournalLine.debit))
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.type == "income")
    )

    expense = await db.scalar(
        select(func.sum(JournalLine.debit - JournalLine.credit))
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.type == "expense")
    )

    income = float(income or 0)
    expense = float(expense or 0)

    profit = income - expense

    # =========================
    # ADD PROFIT TO EQUITY
    # =========================
    total_equity += profit

    equity.append({"account_name": "Retained Earnings", "balance": profit})

    # =========================
    # FINAL OUTPUT
    # =========================
    return {
        "assets": {"items": assets, "total": total_assets},
        "liabilities": {"items": liabilities, "total": total_liabilities},
        "equity": {"items": equity, "total": total_equity},
        "profit": profit,
        "is_balanced": round(total_assets, 2)
        == round(total_liabilities + total_equity, 2),
    }
