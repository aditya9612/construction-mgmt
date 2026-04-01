from typing import Optional
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db_session
from app.models.expense import Expense
from app.models.project import Project
from app.models.owner import OwnerTransaction
from app.schemas.expense import ExpenseCreate, ExpenseUpdate, ExpenseOut
from app.utils.helpers import NotFoundError

router = APIRouter(prefix="/expenses", tags=["expenses"])

from app.models.boq import BOQ
from sqlalchemy import select, func
from decimal import Decimal

from app.models.boq import BOQ
from sqlalchemy import select, func
from decimal import Decimal

@router.post("", response_model=ExpenseOut)
async def create_expense(
    payload: ExpenseCreate, db: AsyncSession = Depends(get_db_session)
):
    project = await db.get(Project, payload.project_id)
    if not project:
        raise NotFoundError("Project not found")

    data = payload.model_dump()


    if not data.get("boq_item_id"):
        boq = await db.scalar(
            select(BOQ).where(
                BOQ.project_id == data["project_id"],
                BOQ.category.ilike(data["category"]),
                BOQ.is_latest == True
            )
        )
        if boq:
            data["boq_item_id"] = boq.id

    obj = Expense(**data)

    db.add(obj)
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
        type="debit",
        amount=obj.amount,
        reference_type="expense",
        reference_id=obj.id,
        description="Expense added",
    )
    db.add(owner_transaction)

    await db.commit()
    await db.refresh(obj)

    return ExpenseOut.model_validate(obj)

@router.get("", response_model=list[ExpenseOut])
async def list_expenses(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Expense))
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/{id}", response_model=ExpenseOut)
async def get_expense(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(Expense, id)

    if not obj:
        raise NotFoundError("Expense not found")

    return ExpenseOut.model_validate(obj)


@router.put("/{id}", response_model=ExpenseOut)
async def update_expense(
    id: int, payload: ExpenseUpdate, db: AsyncSession = Depends(get_db_session)
):
    obj = await db.get(Expense, id)

    if not obj:
        raise NotFoundError("Expense not found")

    old_boq_id = obj.boq_item_id

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    await db.flush()

    if old_boq_id:
        total_actual = await db.scalar(
            select(func.sum(Expense.amount)).where(Expense.boq_item_id == old_boq_id)
        )

        boq = await db.get(BOQ, old_boq_id)
        if boq:
            boq.actual_cost = Decimal(total_actual or 0)
            boq.variance_cost = Decimal(boq.total_cost or 0) - boq.actual_cost

    await db.commit()
    await db.refresh(obj)

    return ExpenseOut.model_validate(obj)


@router.delete("/{id}", status_code=204)
async def delete_expense(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(Expense, id)

    if not obj:
        raise NotFoundError("Expense not found")

    await db.delete(obj)
    await db.commit()

    return None


@router.get("/project/{project_id}")
async def get_by_project(project_id: int, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Expense).where(Expense.project_id == project_id))
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/category/{category}")
async def get_by_category(category: str, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Expense).where(Expense.category == category))
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/date-range")
async def get_by_date_range(
    start: date, end: date, db: AsyncSession = Depends(get_db_session)
):
    result = await db.execute(
        select(Expense).where(Expense.expense_date.between(start, end))
    )
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/payment-mode/{mode}")
async def get_by_payment_mode(mode: str, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Expense).where(Expense.payment_mode == mode))
    rows = result.scalars().all()
    return [ExpenseOut.model_validate(r) for r in rows]


@router.get("/summary/{project_id}")
async def summary(project_id: int, db: AsyncSession = Depends(get_db_session)):
    total = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )

    return {"project_id": project_id, "total_expense": float(total or 0)}


@router.get("/boq-comparison/{project_id}")
async def boq_comparison(project_id: int, db: AsyncSession = Depends(get_db_session)):
    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )

    return {
        "project_id": project_id,
        "actual_expense": float(total_expense or 0),
    }
