from decimal import Decimal
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.models.boq import BOQ
from app.models.material import MaterialTransaction
from app.models.expense import Expense
from app.models.equipment import EquipmentPurchase, EquipmentMaintenance, EquipmentRental, EquipmentUsage

async def recalculate_boq_actuals(db: AsyncSession, boq_id: int, lock: bool = True) -> BOQ:
    """
    Centralized BOQ actuals calculation.
    Aggregates all deterministic cost sources.
    Uses SELECT FOR UPDATE on the BOQ item if lock=True.
    """
    if lock:
        stmt = select(BOQ).where(BOQ.id == boq_id).with_for_update()
    else:
        stmt = select(BOQ).where(BOQ.id == boq_id)

    result = await db.execute(stmt)
    boq_item = result.scalar_one_or_none()

    if not boq_item:
        raise ValueError(f"BOQ item {boq_id} not found")

    # 1. Material Usage
    mat_usage_stmt = select(
        func.sum(MaterialTransaction.total_amount),
        func.sum(MaterialTransaction.quantity)
    ).where(
        MaterialTransaction.boq_item_id == boq_id,
        MaterialTransaction.type == "USAGE"
    )
    mat_usage_res = await db.execute(mat_usage_stmt)
    mat_usage_cost, mat_usage_qty = mat_usage_res.first()
    mat_usage_cost = Decimal(str(mat_usage_cost)) if mat_usage_cost is not None else Decimal("0")
    mat_usage_qty = Decimal(str(mat_usage_qty)) if mat_usage_qty is not None else Decimal("0")

    # 2. Expense (includes Attendance)
    expense_stmt = select(func.sum(Expense.amount)).where(Expense.boq_item_id == boq_id)
    expense_res = await db.execute(expense_stmt)
    expense_cost = expense_res.scalar()
    expense_cost = Decimal(str(expense_cost)) if expense_cost is not None else Decimal("0")

    # 3. Equipment Purchase
    eq_pur_stmt = select(func.sum(EquipmentPurchase.total_amount)).where(EquipmentPurchase.boq_item_id == boq_id)
    eq_pur_res = await db.execute(eq_pur_stmt)
    eq_pur_cost = eq_pur_res.scalar()
    eq_pur_cost = Decimal(str(eq_pur_cost)) if eq_pur_cost is not None else Decimal("0")

    # 4. Equipment Maintenance
    eq_maint_stmt = select(func.sum(EquipmentMaintenance.cost)).where(EquipmentMaintenance.boq_item_id == boq_id)
    eq_maint_res = await db.execute(eq_maint_stmt)
    eq_maint_cost = eq_maint_res.scalar()
    eq_maint_cost = Decimal(str(eq_maint_cost)) if eq_maint_cost is not None else Decimal("0")

    # 5. Equipment Rental
    eq_rent_stmt = select(func.sum(EquipmentRental.rental_cost)).where(EquipmentRental.boq_item_id == boq_id)
    eq_rent_res = await db.execute(eq_rent_stmt)
    eq_rent_cost = eq_rent_res.scalar()
    eq_rent_cost = Decimal(str(eq_rent_cost)) if eq_rent_cost is not None else Decimal("0")

    # 6. Equipment Usage (ONLY where cost IS NOT NULL)
    eq_use_stmt = select(func.sum(EquipmentUsage.cost)).where(
        EquipmentUsage.boq_item_id == boq_id,
        EquipmentUsage.cost.isnot(None)
    )
    eq_use_res = await db.execute(eq_use_stmt)
    eq_use_cost = eq_use_res.scalar()
    eq_use_cost = Decimal(str(eq_use_cost)) if eq_use_cost is not None else Decimal("0")

    # Pre-flight check: Are there legacy EquipmentUsage records?
    legacy_eq_use_stmt = select(func.count(EquipmentUsage.id)).where(
        EquipmentUsage.boq_item_id == boq_id,
        EquipmentUsage.cost.is_(None)
    )
    legacy_count_res = await db.execute(legacy_eq_use_stmt)
    legacy_count = legacy_count_res.scalar() or 0

    # If legacy records exist, we CANNOT calculate a deterministic total cost!
    # Because if we set actual_cost = calculated_cost, we overwrite (destroy) the unknown legacy portion.
    if legacy_count > 0:
        raise ValueError(f"Cannot recalculate BOQ {boq_id} due to {legacy_count} legacy EquipmentUsage records with NULL cost. Historical data must not be destroyed.")

    total_actual_cost = (
        mat_usage_cost +
        expense_cost +
        eq_pur_cost +
        eq_maint_cost +
        eq_rent_cost +
        eq_use_cost
    )

    # 7. Set Actuals
    boq_item.actual_quantity = mat_usage_qty
    boq_item.actual_cost = total_actual_cost

    # 8. Set Variance
    boq_item.variance_cost = (boq_item.total_cost or Decimal("0")) - boq_item.actual_cost

    return boq_item
