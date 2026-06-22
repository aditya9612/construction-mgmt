from typing import Optional
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.enums import OwnerReferenceType, OwnerTransactionType
from app.db.session import get_db_session
from app.models.expense import Expense
from app.models.project import Project
from app.models.owner import OwnerTransaction
from app.schemas.expense import ExpenseCreate, ExpenseUpdate, ExpenseOut
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
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    result = await db.execute(select(Expense).order_by(Expense.created_at.desc()))
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/{id}", response_model=ExpenseOut)
async def get_expense(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    obj = await db.get(Expense, id)

    if not obj:
        raise NotFoundError("Expense not found")

    return ExpenseOut.model_validate(obj)


@router.put("/{id}", response_model=ExpenseOut)
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


@router.delete("/{id}", status_code=204)
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


@router.get("/boq-comparison/{project_id}")
async def boq_comparison(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EXPENSE_READ_ROLES)),
):
    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )

    return {
        "project_id": project_id,
        "actual_expense": total_expense or Decimal("0"),
    }
