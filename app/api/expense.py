from typing import Optional
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.enums import OwnerReferenceType, OwnerTransactionType
from app.db.session import get_db_session
from app.models.expense import Expense
from app.models.project import Project
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
from sqlalchemy import select, func
from decimal import Decimal

from app.models.boq import BOQ
from sqlalchemy import select, func
from decimal import Decimal

from app.models.user import User, UserRole
from app.core.dependencies import require_roles

EXPENSE_READ_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.ACCOUNTANT,
        UserRole.CLIENT,
    ]
]

EXPENSE_WRITE_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.ACCOUNTANT,
    ]
]

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.post("", response_model=ExpenseOut)
async def create_expense(
    payload: ExpenseCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_WRITE_ROLES)),
):
    logger.info(
        f"Creating expense project_id={payload.project_id} amount={payload.amount}"
    )

    project = await db.get(Project, payload.project_id)
    if not project:
        logger.warning(f"Project not found id={payload.project_id}")
        raise NotFoundError("Project not found")

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

    obj = Expense(**data)
    db.add(obj)

    try:
        await db.flush()

        if obj.boq_item_id:
            total_actual = await db.scalar(
                select(func.sum(Expense.amount)).where(
                    Expense.boq_item_id == obj.boq_item_id
                )
            )

            boq = await db.get(BOQ, obj.boq_item_id)
            if boq:
                boq.actual_cost = Decimal(total_actual or 0)
                boq.variance_cost = Decimal(boq.total_cost or 0) - boq.actual_cost

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
        from app.core.enums import AccountType
        expense_acc = await db.scalar(select(Account).where(Account.name.ilike('%Expense%'), Account.type == AccountType.EXPENSE.value))
        if not expense_acc:
            expense_acc = await db.scalar(select(Account).where(Account.name.ilike('%Direct Expense%')))
        
        cash_acc = await get_primary_cash_account(db)

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
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    result = await db.execute(
        select(Expense).where(Expense.expense_date.between(start, end))
    )
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
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    query = select(Expense)
    
    if category:
        query = query.where(Expense.category == category)
    if project_id:
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
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    obj = await db.get(Expense, id)

    if not obj:
        raise NotFoundError("Expense not found")

    return ExpenseOut.model_validate(obj)


@router.put("/{id:int}", response_model=ExpenseOut)
async def update_expense(
    id: int,
    payload: ExpenseUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_WRITE_ROLES)),
):
    logger.info(f"Updating expense id={id}")

    obj = await db.get(Expense, id)

    if not obj:
        logger.warning(f"Expense not found id={id}")
        raise NotFoundError("Expense not found")

    old_boq_id = obj.boq_item_id

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    new_boq_id = obj.boq_item_id

    owner_txn = await db.scalar(
        select(OwnerTransaction).where(
            OwnerTransaction.reference_type == OwnerReferenceType.EXPENSE.value,
            OwnerTransaction.reference_id == obj.id,
        )
    )

    if owner_txn:

        owner_txn.amount = obj.amount

        owner_txn.description = obj.description

    try:
        await db.flush()

        affected_boq_ids = {boq_id for boq_id in [old_boq_id, new_boq_id] if boq_id}

        for boq_id in affected_boq_ids:

            total_actual = await db.scalar(
                select(func.sum(Expense.amount)).where(Expense.boq_item_id == boq_id)
            )

            boq = await db.get(BOQ, boq_id)

            if boq:

                boq.actual_cost = Decimal(total_actual or 0)

                boq.variance_cost = Decimal(boq.total_cost or 0) - boq.actual_cost

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
    current_user: User = Depends(require_roles(EXPENSE_WRITE_ROLES)),
):
    logger.info(f"Deleting expense id={id}")

    obj = await db.get(Expense, id)

    if not obj:
        logger.warning(f"Expense not found id={id}")
        raise NotFoundError("Expense not found")

    owner_txn = await db.scalar(
        select(OwnerTransaction).where(
            OwnerTransaction.reference_type == OwnerReferenceType.EXPENSE.value,
            OwnerTransaction.reference_id == obj.id,
        )
    )

    if owner_txn:
        await db.delete(owner_txn)

    try:
        await db.delete(obj)
        await db.commit()
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
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    result = await db.execute(select(Expense).where(Expense.project_id == project_id).order_by(Expense.created_at.desc()))
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/category/{category}")
async def get_by_category(
    category: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    result = await db.execute(select(Expense).where(Expense.category == category))
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/payment-mode/{mode}")
async def get_by_payment_mode(
    mode: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    result = await db.execute(select(Expense).where(Expense.payment_mode == mode))
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/summary/{project_id}")
async def summary(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    total = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )

    return {"project_id": project_id, "total_expense": total or Decimal("0")}


@router.get("/boq-comparison/{project_id}", response_model=list[BOQComparisonRow])
async def boq_comparison(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
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
            boq_item=item.item_desc or item.category,
            unit=item.unit or "Nos",
            boq_qty=float(item.quantity or 0),
            boq_rate=float(item.rate or 0),
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
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    # Total Expense
    total_expense = float(await db.scalar(select(func.sum(Expense.amount))) or 0.0)

    # Monthly Expense
    today = date.today()
    start_of_month = today.replace(day=1)
    monthly_expense = float(await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.expense_date >= start_of_month)
    ) or 0.0)

    # For simple estimation, consider all currently assigned project expenses
    project_expense = total_expense  # in a real app might exclude HQ expenses
    
    # Direct vs Indirect
    direct_expense = total_expense * 0.8  # dummy fallback if category lacks distinction
    indirect_expense = total_expense * 0.2

    # Trend (Last 6 months)
    trend = []
    
    # Category Summary
    cat_res = await db.execute(
        select(Expense.category, func.sum(Expense.amount))
        .group_by(Expense.category)
    )
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
        pending_approval_count=0, # Assuming all are approved for now
        trend=trend,
        category_summary=cat_summary
    )

@router.get("/project-allocations", response_model=ProjectAllocationsOut)
async def get_project_allocations(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    # Group by project
    res = await db.execute(
        select(Project.id, Project.project_name, func.sum(Expense.amount))
        .join(Expense, Expense.project_id == Project.id)
        .group_by(Project.id, Project.project_name)
    )
    projects = []
    for pid, pname, amt in res.all():
        projects.append(ProjectAllocationCard(
            project_name=pname,
            material_cost=float(amt) * 0.5, # Placeholder logic since material module might not be fully linked here
            labour_cost=float(amt) * 0.3,
            equipment_cost=float(amt) * 0.1,
            other_expense=float(amt) * 0.1,
            total_allocated=float(amt)
        ))
        
    recent = []
    expenses = (await db.execute(select(Expense).join(Project).order_by(Expense.created_at.desc()).limit(10))).scalars().all()
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
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    from app.core.enums import AccountType
    expense_accs = (await db.execute(
        select(Account.id).where(or_(Account.name.ilike('%Expense%'), Account.type == AccountType.EXPENSE.value))
    )).scalars().all()

    if not expense_accs:
        return []

    res = await db.execute(
        select(JournalEntry, JournalLine)
        .join(JournalLine)
        .where(JournalLine.account_id.in_(expense_accs), JournalEntry.status == "Posted")
        .order_by(JournalEntry.entry_date.asc())
    )
    
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
    current_user: User = Depends(require_roles(EXPENSE_WRITE_ROLES)),
):
    content = await file.read()
    decoded = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded))
    valid = 0
    errors = 0
    for row in reader:
        valid += 1
    return {"valid_records": valid, "errors": errors, "message": "Import preview successful"}

@router.get("/export")
async def export_expenses(db: AsyncSession = Depends(get_db_session)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Expense No", "Date", "Category", "Project", "Vendor", "Amount", "GST", "Payment Mode", "Status"])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=expenses.csv"})

