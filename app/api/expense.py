from typing import Optional
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File, Header
from sqlalchemy.exc import IntegrityError
import hashlib
import json
from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.enums import OwnerReferenceType, OwnerTransactionType
from app.db.session import get_db_session
from app.models.expense import Expense
from app.models.project import Project
from app.models.approval import Approval
from app.models.owner import OwnerTransaction
from app.schemas.expense import (
    ExpenseCreate, ExpenseUpdate, ExpenseOut, ExpenseDashboardOut,
    ExpenseTrendOut, ExpenseCategorySummaryOut, ProjectAllocationsOut,
    ProjectAllocationCard, ProjectAllocationRecent, ExpenseLedgerRow,
    BOQComparisonRow
)
from app.models.accountant import JournalEntry, JournalLine, Account
from app.utils.accounting import get_primary_cash_account
from app.utils.helpers import NotFoundError
from app.core.logger import logger

from app.models.boq import BOQ
from app.models.material import Supplier
from sqlalchemy import select, func
from decimal import Decimal
from fastapi.responses import StreamingResponse
import io
import csv

from app.models.company import Company
from app.models.user import User
from app.utils.boq_calc import recalculate_boq_actuals
from app.core.dependencies import require_permission

router = APIRouter(prefix="/expenses", tags=["expenses"])


def _check_tenant_access(current_user: User) -> bool:
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=403, detail="Company context required")
    return is_sa


@router.post("", response_model=ExpenseOut)
async def create_expense(
    payload: ExpenseCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.create")),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    is_sa = _check_tenant_access(current_user)
    logger.info(
        f"Creating expense project_id={payload.project_id} amount={payload.amount}"
    )

    project = await db.get(Project, payload.project_id)
    if not project or (not is_sa and project.company_id != current_user.company_id):
        logger.warning(f"Project not found id={payload.project_id}")
        raise NotFoundError("Project not found")

    if payload.boq_item_id:
        boq = await db.get(BOQ, payload.boq_item_id)
        if not boq or boq.project_id != project.id:
            raise NotFoundError("BOQ item not found")

    data = payload.model_dump()

    if not data.get("boq_item_id"):
        boq = await db.scalar(
            select(BOQ).where(
                BOQ.project_id == data["project_id"],
                BOQ.category.ilike(data["category"]),
                BOQ.is_latest == True,
            )
        )
        if boq:
            data["boq_item_id"] = boq.id

    request_hash = None
    if idempotency_key:
        payload_dict = payload.model_dump(mode="json")
        payload_str = json.dumps(payload_dict, sort_keys=True)
        request_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        data["idempotency_key"] = idempotency_key
        data["request_hash"] = request_hash

    obj = Expense(**data)
    db.add(obj)

    try:
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing_expense = await db.scalar(
                select(Expense).where(Expense.idempotency_key == idempotency_key)
            )
            if existing_expense:
                if existing_expense.request_hash != request_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key already used with a different request payload."
                    )
                return ExpenseOut.model_validate(existing_expense)
            raise

        if obj.boq_item_id:
            await recalculate_boq_actuals(db, obj.boq_item_id, lock=True)

        owner_transaction = OwnerTransaction(
            owner_id=project.owner_id,
            project_id=obj.project_id,
            type=OwnerTransactionType.DEBIT.value,
            amount=obj.amount,
            reference_type=OwnerReferenceType.EXPENSE.value,
            reference_id=obj.id,
            description="Expense added",
        )
        db.add(owner_transaction)

        # ----------------- JOURNAL POSTING -----------------
        # DR Expense Account
        # DR GST Input Account (if GST exists) -> assuming no gst field for now on Expense, UI says "GST"
        # CR Bank/Cash/Vendor Payable
        # We will dynamically find Expense account or fallback
        expense_acc = await db.scalar(
            select(Account).where(
                Account.code == 'GENERAL_EXPENSE',
                Account.company_id == project.company_id,
            )
        )
        if not expense_acc:
            raise HTTPException(status_code=400, detail="GENERAL_EXPENSE account is not configured.")

        cash_acc = await get_primary_cash_account(db, company_id=project.company_id)

        if expense_acc and cash_acc:
            je = JournalEntry(
                entry_type="Expense",
                journal_number=f"J-EXP-{obj.id}",
                entry_date=obj.expense_date,
                description=obj.description or f"Expense {obj.id}",
                status="Posted"
            )
            db.add(je)
            await db.flush()

            db.add(JournalLine(entry_id=je.id, account_id=expense_acc.id, debit=obj.amount, credit=Decimal(0)))
            db.add(JournalLine(entry_id=je.id, account_id=cash_acc.id, debit=Decimal(0), credit=obj.amount))

        await db.commit()

    except Exception:
        await db.rollback()
        logger.exception("Expense creation failed")
        raise

    await db.refresh(obj)

    logger.info(f"Expense created id={obj.id} amount={obj.amount}")

    return ExpenseOut.model_validate(obj)


@router.get("/date-range")
async def get_by_date_range(
    start: date,
    end: date,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    query = (
        select(Expense)
        .join(Project, Expense.project_id == Project.id)
        .where(Expense.expense_date.between(start, end))
    )
    if not is_sa:
        query = query.where(Project.company_id == current_user.company_id)
    result = await db.execute(query)
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("", response_model=list[ExpenseOut])
async def list_expenses(
    category: Optional[str] = Query(None, description="Filter expenses by category"),
    search: Optional[str] = Query(None),
    project_id: Optional[int] = Query(None),
    vendor_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    company_id: Optional[int] = Query(None, description="Filter by company ID (Super Admin only)"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        effective_company_id = current_user.company_id
    else:
        if company_id is not None:
            comp = await db.get(Company, company_id)
            if not comp:
                raise NotFoundError("Company not found")
            effective_company_id = company_id
        else:
            effective_company_id = None

    query = select(Expense).join(Project, Expense.project_id == Project.id)

    if effective_company_id is not None:
        query = query.where(Project.company_id == effective_company_id)

    if vendor_id:
        supplier = await db.get(Supplier, vendor_id)
        if not supplier or (effective_company_id is not None and supplier.company_id != effective_company_id):
            raise NotFoundError("Vendor not found")

    if category:
        query = query.where(Expense.category == category)
    if project_id:
        proj = await db.get(Project, project_id)
        if not proj or (effective_company_id is not None and proj.company_id != effective_company_id):
            raise NotFoundError("Project not found")
        query = query.where(Expense.project_id == project_id)
    if from_date:
        query = query.where(Expense.expense_date >= from_date)
    if to_date:
        query = query.where(Expense.expense_date <= to_date)
    if search:
        query = query.where(Expense.description.ilike(f"%{search}%"))

    offset = (page - 1) * limit
    result = await db.execute(query.order_by(Expense.created_at.desc()).offset(offset).limit(limit))
    rows = result.scalars().all()

    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/{id:int}", response_model=ExpenseOut)
async def get_expense(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    query = select(Expense).join(Project, Expense.project_id == Project.id).where(Expense.id == id)
    if not is_sa:
        query = query.where(Project.company_id == current_user.company_id)
    obj = await db.scalar(query)

    if not obj:
        raise NotFoundError("Expense not found")

    return ExpenseOut.model_validate(obj)


@router.put("/{id:int}", response_model=ExpenseOut)
async def update_expense(
    id: int,
    payload: ExpenseUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.edit")),
):
    is_sa = _check_tenant_access(current_user)
    import time
    logger.info(f"Updating expense id={id}")

    # LOCK EXPENSE
    obj = await db.scalar(select(Expense).where(Expense.id == id).with_for_update())

    if not obj:
        logger.warning(f"Expense not found id={id}")
        raise NotFoundError("Expense not found")

    # Validate parent project belongs to caller's company
    project = await db.get(Project, obj.project_id)
    if not project or (not is_sa and project.company_id != current_user.company_id):
        logger.warning(f"Expense access denied or project not found id={id}")
        raise NotFoundError("Expense not found")

    target_project = project
    if payload.project_id and payload.project_id != obj.project_id:
        new_project = await db.get(Project, payload.project_id)
        if not new_project or (not is_sa and new_project.company_id != current_user.company_id):
            raise NotFoundError("Project not found")
        target_project = new_project

    if payload.boq_item_id:
        boq = await db.get(BOQ, payload.boq_item_id)
        if not boq or boq.project_id != target_project.id:
            raise NotFoundError("BOQ item not found")

    if obj.source_type == "attendance_auto":
        raise HTTPException(
            status_code=403,
            detail="Cannot modify attendance-generated expenses directly. Please use the Attendance/Payroll modules."
        )

    # Capture original state to determine if accounting impact occurred
    old_amount = obj.amount
    old_date = obj.expense_date
    old_category = obj.category
    old_mode = obj.payment_mode
    old_boq_id = obj.boq_item_id

    # Update metadata
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    new_boq_id = obj.boq_item_id

    accounting_changed = (
        old_amount != obj.amount or
        old_date != obj.expense_date or
        old_category != obj.category or
        old_mode != obj.payment_mode
    )

    owner_txn = await db.scalar(
        select(OwnerTransaction).where(
            OwnerTransaction.reference_type == OwnerReferenceType.EXPENSE.value,
            OwnerTransaction.reference_id == obj.id,
        )
    )

    if owner_txn:
        owner_txn.amount = obj.amount
        owner_txn.description = obj.description
        if payload.project_id:
            owner_txn.project_id = target_project.id
            owner_txn.owner_id = target_project.owner_id

    try:
        if accounting_changed:
            # 1. Identify CURRENT active JournalEntry
            current_journal = await db.scalar(
                select(JournalEntry)
                .where(JournalEntry.journal_number.startswith(f"J-EXP-{obj.id}"))
                .order_by(JournalEntry.id.desc())
            )
            if not current_journal:
                raise HTTPException(status_code=500, detail="Original accounting journal not found")
            if "-REV-" in current_journal.journal_number:
                raise HTTPException(status_code=400, detail="Cannot edit an already reversed or deleted expense")

            current_lines = (await db.execute(
                select(JournalLine).where(JournalLine.entry_id == current_journal.id)
            )).scalars().all()

            timestamp = int(time.time() * 1000)

            # 2. Create Reversal Journal
            rev_je = JournalEntry(
                entry_type="Expense",
                journal_number=f"J-EXP-{obj.id}-REV-{timestamp}",
                entry_date=current_journal.entry_date,  # MUST use original date
                description=f"Reversal of {current_journal.journal_number}",
                status="Posted"
            )
            db.add(rev_je)
            await db.flush()

            rev_debit_sum = Decimal(0)
            rev_credit_sum = Decimal(0)

            for line in current_lines:
                db.add(JournalLine(
                    entry_id=rev_je.id,
                    account_id=line.account_id,
                    debit=line.credit,   # swapped
                    credit=line.debit,   # swapped
                ))
                rev_debit_sum += line.credit
                rev_credit_sum += line.debit

            if rev_debit_sum != rev_credit_sum:
                raise HTTPException(status_code=500, detail="Reversal journal unbalanced")

            # 3. Create Corrected Journal
            expense_acc = await db.scalar(
                select(Account).where(
                    Account.code == 'GENERAL_EXPENSE',
                    Account.company_id == target_project.company_id,
                )
            )
            if not expense_acc:
                raise HTTPException(status_code=400, detail="GENERAL_EXPENSE account is not configured.")
            cash_acc = await get_primary_cash_account(db, company_id=target_project.company_id)

            if expense_acc and cash_acc:
                new_je = JournalEntry(
                    entry_type="Expense",
                    journal_number=f"J-EXP-{obj.id}-V-{timestamp}",
                    entry_date=obj.expense_date, # NEW date
                    description=obj.description or f"Expense {obj.id} (Corrected)",
                    status="Posted"
                )
                db.add(new_je)
                await db.flush()

                db.add(JournalLine(entry_id=new_je.id, account_id=expense_acc.id, debit=obj.amount, credit=Decimal(0)))
                db.add(JournalLine(entry_id=new_je.id, account_id=cash_acc.id, debit=Decimal(0), credit=obj.amount))

                # Validation is implicit since we use obj.amount for both debit and credit.

        await db.flush()

        affected_boq_ids = {boq_id for boq_id in [old_boq_id, new_boq_id] if boq_id}

        for boq_id in affected_boq_ids:
            await recalculate_boq_actuals(db, boq_id, lock=True)

        await db.commit()

    except Exception:
        await db.rollback()
        logger.exception(f"Expense update failed id={id}")
        raise

    await db.refresh(obj)

    logger.info(f"Expense updated id={id}")

    return ExpenseOut.model_validate(obj)


@router.delete("/{id:int}", status_code=204)
async def delete_expense(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.delete")),
):
    is_sa = _check_tenant_access(current_user)
    logger.info(f"Deleting expense id={id}")

    try:
        # 1. Lock Expense
        obj = await db.scalar(
            select(Expense).where(Expense.id == id).with_for_update()
        )

        if not obj:
            logger.warning(f"Expense not found id={id}")
            raise NotFoundError("Expense not found")

        # Validate parent project belongs to caller's company
        project = await db.get(Project, obj.project_id)
        if not project or (not is_sa and project.company_id != current_user.company_id):
            logger.warning(f"Expense access denied or project not found id={id}")
            raise NotFoundError("Expense not found")

        # 2. Attendance Protection
        if obj.source_type == "attendance_auto":
            raise HTTPException(
                status_code=403,
                detail="Cannot modify attendance-generated expenses directly. Please use the Attendance/Payroll modules."
            )

        # 3. Find Current Active Journal
        active_journal = await db.scalar(
            select(JournalEntry)
            .where(JournalEntry.journal_number.startswith(f"J-EXP-{obj.id}"))
            .order_by(JournalEntry.id.desc())
            .limit(1)
        )

        # 4 & 5. Create Final Delete Reversal and lines if active journal exists and isn't DEL
        if active_journal and not active_journal.journal_number.endswith("-DEL"):
            final_rev_journal_num = f"J-EXP-{obj.id}-DEL"

            final_rev_je = JournalEntry(
                journal_number=final_rev_journal_num,
                entry_date=active_journal.entry_date,
                entry_type='Auto',
                description=f"Delete Reversal - Expense {obj.id}",
                status="Posted"
            )
            db.add(final_rev_je)
            await db.flush()

            active_lines = await db.scalars(
                select(JournalLine).where(JournalLine.entry_id == active_journal.id)
            )

            rev_debit_sum = Decimal(0)
            rev_credit_sum = Decimal(0)

            for line in active_lines:
                db.add(JournalLine(
                    entry_id=final_rev_je.id,
                    account_id=line.account_id,
                    debit=line.credit,
                    credit=line.debit,
                ))
                rev_debit_sum += (line.credit or Decimal(0))
                rev_credit_sum += (line.debit or Decimal(0))

            # 6. Balance Validation
            if rev_debit_sum != rev_credit_sum:
                raise HTTPException(status_code=500, detail="Reversal journal unbalanced")

        # 7. OwnerTransaction
        owner_txn = await db.scalar(
            select(OwnerTransaction).where(
                OwnerTransaction.reference_type == OwnerReferenceType.EXPENSE.value,
                OwnerTransaction.reference_id == obj.id,
            )
        )
        if owner_txn:
            await db.delete(owner_txn)

        # 8 & 9. BOQ handling and Delete Expense
        boq_id = obj.boq_item_id

        await db.delete(obj)
        await db.flush()

        if boq_id:
            await recalculate_boq_actuals(db, boq_id, lock=True)

        # 10. Commit transaction
        await db.commit()

    except HTTPException:
        await db.rollback()
        raise
    except NotFoundError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        logger.exception(f"Expense delete failed id={id}")
        raise

    logger.info(f"Expense deleted id={id}")

    return None


@router.get("/project/{project_id}")
async def get_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    project = await db.get(Project, project_id)
    if not project or (not is_sa and project.company_id != current_user.company_id):
        raise NotFoundError("Project not found")
    result = await db.execute(select(Expense).where(Expense.project_id == project_id).order_by(Expense.created_at.desc()))
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/category/{category}")
async def get_by_category(
    category: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    query = (
        select(Expense)
        .join(Project, Expense.project_id == Project.id)
        .where(Expense.category == category)
    )
    if not is_sa:
        query = query.where(Project.company_id == current_user.company_id)
    result = await db.execute(query)
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/payment-mode/{mode}")
async def get_by_payment_mode(
    mode: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    query = (
        select(Expense)
        .join(Project, Expense.project_id == Project.id)
        .where(Expense.payment_mode == mode)
    )
    if not is_sa:
        query = query.where(Project.company_id == current_user.company_id)
    result = await db.execute(query)
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/summary/{project_id}")
async def summary(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    project = await db.get(Project, project_id)
    if not project or (not is_sa and project.company_id != current_user.company_id):
        raise NotFoundError("Project not found")
    total = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )

    return {"project_id": project_id, "total_expense": total or Decimal("0")}


@router.get("/boq-comparison/{project_id}", response_model=list[BOQComparisonRow])
async def boq_comparison(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    project = await db.get(Project, project_id)
    if not project or (not is_sa and project.company_id != current_user.company_id):
        raise NotFoundError("Project not found")
    boq_items = (await db.execute(
        select(BOQ).where(BOQ.project_id == project_id, BOQ.is_latest == True)
    )).scalars().all()

    res = []
    for item in boq_items:
        boq_amount = float(item.total_cost or 0)
        actual_amount = float(item.actual_cost or 0)
        variance = boq_amount - actual_amount
        variance_percentage = (variance / boq_amount * 100) if boq_amount > 0 else 0

        res.append(BOQComparisonRow(
            boq_item=item.item_name or item.category,
            unit=item.unit or "Nos",
            boq_qty=float(item.quantity or 0),
            boq_rate=float(item.unit_cost or 0),
            boq_amount=boq_amount,
            actual_amount=actual_amount,
            variance=variance,
            variance_percentage=variance_percentage
        ))
    return res

# ------------- EXPENSE NEW ENDPOINTS -------------
import csv
import io
from fastapi import UploadFile, File
from fastapi.responses import StreamingResponse
from datetime import datetime

@router.get("/dashboard", response_model=ExpenseDashboardOut)
async def get_dashboard(
    company_id: Optional[int] = Query(None, description="Filter by company ID (Super Admin only)"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        effective_company_id = current_user.company_id
    else:
        if company_id is not None:
            comp = await db.get(Company, company_id)
            if not comp:
                raise NotFoundError("Company not found")
            effective_company_id = company_id
        else:
            effective_company_id = None

    # Total Expense
    tot_q = select(func.sum(Expense.amount)).join(Project, Expense.project_id == Project.id)

    # Monthly Expense
    today = date.today()
    start_of_month = today.replace(day=1)
    mon_q = (
        select(func.sum(Expense.amount))
        .join(Project, Expense.project_id == Project.id)
        .where(Expense.expense_date >= start_of_month)
    )

    cat_q = (
        select(Expense.category, func.sum(Expense.amount))
        .join(Project, Expense.project_id == Project.id)
        .group_by(Expense.category)
    )

    if effective_company_id is not None:
        tot_q = tot_q.where(Project.company_id == effective_company_id)
        mon_q = mon_q.where(Project.company_id == effective_company_id)
        cat_q = cat_q.where(Project.company_id == effective_company_id)

    total_expense = float(await db.scalar(tot_q) or 0.0)
    monthly_expense = float(await db.scalar(mon_q) or 0.0)
    project_expense = total_expense

    # Direct vs Indirect
    direct_expense = total_expense * 0.8
    indirect_expense = total_expense * 0.2

    # Trend (Last 6 months)
    six_months_ago = today - timedelta(days=180)
    trend_q = (
        select(Expense.expense_date, func.sum(Expense.amount))
        .join(Project, Expense.project_id == Project.id)
        .where(Expense.expense_date >= six_months_ago)
        .group_by(Expense.expense_date)
        .order_by(Expense.expense_date.asc())
    )
    if effective_company_id is not None:
        trend_q = trend_q.where(Project.company_id == effective_company_id)

    trend_res = await db.execute(trend_q)
    trend_rows = trend_res.all()

    # Fallback if no expenses in last 6 months but older expenses exist
    if not trend_rows:
        fallback_q = (
            select(Expense.expense_date, func.sum(Expense.amount))
            .join(Project, Expense.project_id == Project.id)
            .group_by(Expense.expense_date)
            .order_by(Expense.expense_date.asc())
            .limit(30)
        )
        if effective_company_id is not None:
            fallback_q = fallback_q.where(Project.company_id == effective_company_id)
        trend_rows = (await db.execute(fallback_q)).all()

    trend = [
        ExpenseTrendOut(
            date=row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0])),
            amount=round(float(row[1] or 0.0), 2),
        )
        for row in trend_rows
    ]

    # Pending approvals for expenses
    pending_appr_q = (
        select(func.count(Approval.id))
        .join(Expense, Approval.entity_id == Expense.id)
        .join(Project, Expense.project_id == Project.id)
        .where(
            func.lower(Approval.entity_type) == "expense",
            func.lower(Approval.status) == "pending",
        )
    )
    if effective_company_id is not None:
        pending_appr_q = pending_appr_q.where(Project.company_id == effective_company_id)

    pending_approval_count = int(await db.scalar(pending_appr_q) or 0)

    cat_res = await db.execute(cat_q)
    cat_summary = []
    for cat, amt in cat_res.all():
        amt_float = float(amt)
        cat_summary.append(ExpenseCategorySummaryOut(
            category=cat,
            total_amount=amt_float,
            percentage=round((amt_float / float(total_expense)) * 100, 2) if total_expense else 0
        ))

    return ExpenseDashboardOut(
        total_expense=float(total_expense),
        monthly_expense=float(monthly_expense),
        project_expense=float(project_expense),
        direct_expense=float(direct_expense),
        indirect_expense=float(indirect_expense),
        pending_approval_count=pending_approval_count,
        trend=trend,
        category_summary=cat_summary
    )

@router.get("/project-allocations", response_model=ProjectAllocationsOut)
async def get_project_allocations(
    company_id: Optional[int] = Query(None, description="Filter by company ID (Super Admin only)"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        effective_company_id = current_user.company_id
    else:
        if company_id is not None:
            comp = await db.get(Company, company_id)
            if not comp:
                raise NotFoundError("Company not found")
            effective_company_id = company_id
        else:
            effective_company_id = None

    # Group by project
    res_q = (
        select(Project.id, Project.project_name, func.sum(Expense.amount))
        .join(Expense, Expense.project_id == Project.id)
    )
    rec_q = (
        select(Expense)
        .join(Project, Expense.project_id == Project.id)
    )

    if effective_company_id is not None:
        res_q = res_q.where(Project.company_id == effective_company_id)
        rec_q = rec_q.where(Project.company_id == effective_company_id)

    res = await db.execute(res_q.group_by(Project.id, Project.project_name))
    projects = []
    for pid, pname, amt in res.all():
        projects.append(ProjectAllocationCard(
            project_name=pname,
            material_cost=float(amt) * 0.5,
            labour_cost=float(amt) * 0.3,
            equipment_cost=float(amt) * 0.1,
            other_expense=float(amt) * 0.1,
            total_allocated=float(amt)
        ))

    recent = []
    expenses = (await db.execute(rec_q.order_by(Expense.created_at.desc()).limit(10))).scalars().all()
    for e in expenses:
        proj = await db.get(Project, e.project_id)
        recent.append(ProjectAllocationRecent(
            project_name=proj.project_name if proj else "Unknown",
            expense_category=e.category,
            amount=float(e.amount),
            allocated_date=e.expense_date,
            cost_center="Main"
        ))

    return ProjectAllocationsOut(projects=projects, recent=recent)

@router.get("/ledger", response_model=list[ExpenseLedgerRow])
async def get_expense_ledger(
    company_id: Optional[int] = Query(None, description="Filter by company ID (Super Admin only)"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.view")),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        effective_company_id = current_user.company_id
    else:
        if company_id is not None:
            comp = await db.get(Company, company_id)
            if not comp:
                raise NotFoundError("Company not found")
            effective_company_id = company_id
        else:
            effective_company_id = None

    from app.core.enums import AccountType
    acc_q = select(Account.id).where(or_(Account.name.ilike('%Expense%'), Account.type == AccountType.EXPENSE.value))
    if effective_company_id is not None:
        acc_q = acc_q.where(Account.company_id == effective_company_id)
    expense_accs = (await db.execute(acc_q)).scalars().all()

    if not expense_accs:
        return []

    res_q = (
        select(JournalEntry, JournalLine)
        .join(JournalLine)
        .where(JournalLine.account_id.in_(expense_accs), JournalEntry.status == "Posted")
    )
    res = await db.execute(res_q.order_by(JournalEntry.entry_date.asc()))

    rows = []
    running_balance = 0.0
    for je, jl in res.all():
        debit = float(jl.debit or 0)
        credit = float(jl.credit or 0)
        running_balance += (debit - credit)
        rows.append(ExpenseLedgerRow(
            date=je.entry_date or date.today(),
            particular=je.description or "Expense",
            debit=debit,
            credit=credit,
            running_balance=running_balance
        ))
    return rows

@router.post("/import")
async def import_expenses(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.upload")),
):
    is_sa = _check_tenant_access(current_user)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files allowed")

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV file too large (max 5MB)")
    decoded = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded))
    valid = 0
    errors = 0
    for row in reader:
        proj_id = row.get("project_id") or row.get("Project ID")
        if not proj_id:
            errors += 1
            continue
        try:
            pid = int(proj_id)
            proj = await db.get(Project, pid)
            if not proj or (not is_sa and proj.company_id != current_user.company_id):
                errors += 1
                continue
        except (ValueError, TypeError):
            errors += 1
            continue

        boq_id = row.get("boq_item_id") or row.get("BOQ Item ID")
        if boq_id:
            try:
                bid = int(boq_id)
                boq = await db.get(BOQ, bid)
                if not boq or boq.project_id != proj.id:
                    errors += 1
                    continue
            except (ValueError, TypeError):
                errors += 1
                continue

        valid += 1
    return {"valid_records": valid, "errors": errors, "message": "Import preview successful"}

@router.get("/export")
async def export_expenses(
    company_id: Optional[int] = Query(None, description="Filter by company ID (Super Admin only)"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("expenses.export")),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        effective_company_id = current_user.company_id
    else:
        if company_id is not None:
            comp = await db.get(Company, company_id)
            if not comp:
                raise NotFoundError("Company not found")
            effective_company_id = company_id
        else:
            effective_company_id = None

    query = select(Expense, Project.project_name).join(Project, Expense.project_id == Project.id)
    if effective_company_id is not None:
        query = query.where(Project.company_id == effective_company_id)
    expenses = (await db.execute(query.order_by(Expense.expense_date.desc()))).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Expense No", "Date", "Category", "Project", "Vendor", "Amount", "GST", "Payment Mode", "Status"])
    for row in expenses:
        exp, pname = row[0], row[1]
        writer.writerow([exp.id, exp.expense_date, exp.category, pname, "", exp.amount, "", exp.payment_mode, "Approved"])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=expenses.csv"})
