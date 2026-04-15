from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date

from app.db.session import get_db_session
from app.core import dependencies as d
from app.models.user import User, UserRole
from app.models.project import Project, ProjectMember
from app.models.expense import Expense
from app.models.invoice import Invoice
from app.models.labour import LabourAttendance
from app.models.material import MaterialUsage
from app.models.boq import BOQ
from app.cache import redis as r

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

VERSION_KEY = "dashboard_version"


# =========================================
# HELPER
# =========================================
async def get_user_project_ids(db, user: User):
    if user.role == UserRole.ADMIN:
        result = await db.execute(select(Project.id))
        return [r[0] for r in result.all()]

    result = await db.execute(
        select(ProjectMember.project_id).where(
            ProjectMember.user_id == user.id
        )
    )
    return [r[0] for r in result.all()]



# =========================================
# MAIN DASHBOARD
# =========================================
@router.get("")
async def get_dashboard(
    project_id: int | None = Query(default=None),
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    version = await r.get_cache_version(redis, VERSION_KEY)

    cache_key = f"dashboard:{version}:{current_user.id}:{project_id}"
    cached = await r.cache_get_json(redis, cache_key)

    if cached:
        return cached

    project_ids = await get_user_project_ids(db, current_user)

    if project_id:
        if project_id not in project_ids:
            return {"error": "Access denied"}
        project_ids = [project_id]

    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(
            Expense.project_id.in_(project_ids)
        )
    )

    total_projects = len(project_ids)

    if current_user.role == UserRole.ADMIN:
        total_revenue = await db.scalar(
            select(func.sum(Invoice.total_amount)).where(
                Invoice.status == "paid"
            )
        )

        result = {
            "role": "admin",
            "total_projects": total_projects,
            "total_expense": float(total_expense or 0),
            "total_revenue": float(total_revenue or 0),
        }

    elif current_user.role == UserRole.ACCOUNTANT:
        pending = await db.scalar(
            select(func.sum(Invoice.total_amount)).where(
                Invoice.status == "pending"
            )
        )

        result = {
            "role": "accountant",
            "total_expense": float(total_expense or 0),
            "pending_payments": float(pending or 0),
        }

    elif current_user.role == UserRole.SITE_ENGINEER:
        today = date.today()

        labour_today = await db.scalar(
            select(func.count(LabourAttendance.id)).where(
                LabourAttendance.project_id.in_(project_ids),
                LabourAttendance.attendance_date == today,
            )
        )

        material_today = await db.scalar(
            select(func.sum(MaterialUsage.quantity_used)).where(
                MaterialUsage.project_id.in_(project_ids),
                MaterialUsage.usage_date == today,
            )
        )

        result = {
            "role": "engineer",
            "labour_today": labour_today or 0,
            "material_today": float(material_today or 0),
        }

    elif current_user.role == UserRole.PROJECT_MANAGER:
        total_budget = await db.scalar(
            select(func.sum(BOQ.total_cost)).where(
                BOQ.project_id.in_(project_ids),
                BOQ.is_latest == True,
            )
        )

        utilization = (
            (float(total_expense or 0) / float(total_budget or 1)) * 100
            if total_budget
            else 0
        )

        result = {
            "role": "manager",
            "total_projects": total_projects,
            "budget_utilization": round(utilization, 2),
        }

    elif current_user.role == UserRole.CLIENT:
        result = {
            "role": "client",
            "total_projects": total_projects,
            "total_spent": float(total_expense or 0),
        }

    else:
        result = {"message": "Unsupported role"}

    await r.cache_set_json(redis, cache_key, result)

    return result


# =========================================
# GRAPH APIs
# =========================================
@router.get("/graph/labour")
async def labour_trend(
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)

    result = await db.execute(
        select(
            LabourAttendance.attendance_date,
            func.count(LabourAttendance.id),
        )
        .where(LabourAttendance.project_id.in_(project_ids))
        .group_by(LabourAttendance.attendance_date)
        .order_by(LabourAttendance.attendance_date)
    )

    return [{"date": r[0], "count": r[1]} for r in result.all()]


@router.get("/graph/expense")
async def expense_trend(
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)

    result = await db.execute(
        select(
            Expense.expense_date,
            func.sum(Expense.amount),
        )
        .where(Expense.project_id.in_(project_ids))
        .group_by(Expense.expense_date)
        .order_by(Expense.expense_date)
    )

    return [{"date": r[0], "amount": float(r[1] or 0)} for r in result.all()]