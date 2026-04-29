from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timedelta
import io

from app.db.session import get_db_session
from app.core import dependencies as d
from app.models.user import User, UserRole
from app.models import project as m
from app.models.expense import Expense
from app.models.invoice import Invoice, Transaction
from app.models.labour import LabourAttendance
from app.models.boq import BOQ
from app.cache import redis as r

# PDF + Excel
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
import pandas as pd

DASHBOARD_READ_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.ACCOUNTANT,
        UserRole.CLIENT,
    ]
]

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

VERSION_KEY = "dashboard_version"
CACHE_TTL = 300  #  5 min auto refresh


# =========================================
# HELPER
# =========================================
async def get_user_project_ids(db, user: User):
    if user.role == UserRole.ADMIN.value:
        result = await db.execute(select(m.Project.id))
        return [r[0] for r in result.all()]

    result = await db.execute(
        select(m.ProjectMember.project_id).where(m.ProjectMember.user_id == user.id)
    )
    return [r[0] for r in result.all()]


async def cache_get_set(redis, key, version, func):
    cache_key = f"{key}:{version}"
    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return cached

    result = await func()
    await r.cache_set_json(redis, cache_key, result)
    return result


# =========================================
# KPI COMPARISON (NEW)
# =========================================
async def get_kpi_comparison(db):
    now = datetime.utcnow()
    last_month = now - timedelta(days=30)

    current = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.created_at >= last_month)
    )

    previous = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.created_at < last_month)
    )

    return {
        "current_month": float(current or 0),
        "previous_month": float(previous or 0),
        "difference": float((current or 0) - (previous or 0)),
    }


# =========================================
# ADMIN DASHBOARD
# =========================================
@router.get("/admin")
async def admin_dashboard(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    if current_user.role != UserRole.ADMIN.value:
        return {"error": "Access denied"}

    async def logic():
        today = date.today()

        project_stats = await db.execute(
            select(
                func.count(m.Project.id),
                func.sum(case((m.Project.status == "Active", 1), else_=0)),
                func.sum(case((m.Project.status == "Completed", 1), else_=0)),
                func.sum(case((m.Project.end_date < today, 1), else_=0)),
            )
        )
        total, active, completed, delayed = project_stats.one()

        revenue = await db.scalar(
            select(func.sum(Invoice.total_amount)).where(Invoice.status == "paid")
        )

        expense = await db.scalar(select(func.sum(Expense.amount)))
        project_ids = await get_user_project_ids(db, current_user)

        budget = await db.scalar(
            select(func.sum(BOQ.total_cost)).where(
                BOQ.is_latest == True, BOQ.project_id.in_(project_ids)
            )
        )
        progress = await db.scalar(
            select(func.avg(m.Task.completion_percentage)).where(
                m.Task.project_id.in_(project_ids)
            )
        )
        kpi = await get_kpi_comparison(db)

        return {
            "role": "admin",
            "project_overview": {
                "total": total or 0,
                "active": active or 0,
                "completed": completed or 0,
                "delayed": delayed or 0,
            },
            "financial": {
                "revenue": float(revenue or 0),
                "expense": float(expense or 0),
                "profit": float((revenue or 0) - (expense or 0)),
            },
            "budget_variance": float((budget or 0) - (expense or 0)),
            "efficiency": round(progress or 0, 2),
            "kpi_comparison": kpi,
        }

    version = await r.get_cache_version(redis, VERSION_KEY)
    return await cache_get_set(redis, "admin_dashboard", version, logic)


# =========================================
# ENGINEER DASHBOARD
# =========================================
@router.get("/engineer")
async def engineer_dashboard(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    # if current_user.role != UserRole.SITE_ENGINEER:
    #     return {"error": "Access denied"}

    async def logic():
        project_ids = await get_user_project_ids(db, current_user)
        today = date.today()

        labour = await db.scalar(
            select(func.count(LabourAttendance.id)).where(
                LabourAttendance.project_id.in_(project_ids),
                LabourAttendance.attendance_date == today,
            )
        )

        progress = await db.scalar(
            select(func.avg(m.Task.completion_percentage)).where(
                m.Task.project_id.in_(project_ids)
            )
        )

        return {
            "role": "engineer",
            "labour_today": labour or 0,
            "progress": round(progress or 0, 2),
        }

    version = await r.get_cache_version(redis, VERSION_KEY)
    return await cache_get_set(redis, "engineer_dashboard", version, logic)


# =========================================
# MANAGER DASHBOARD
# =========================================
@router.get("/manager")
async def manager_dashboard(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    # if current_user.role != UserRole.PROJECT_MANAGER:
    #     return {"error": "Access denied"}

    async def logic():
        project_ids = await get_user_project_ids(db, current_user)

        # ========================
        # BUDGET
        # ========================
        budget = await db.scalar(
            select(func.sum(BOQ.total_cost)).where(
                BOQ.project_id.in_(project_ids), BOQ.is_latest == True
            )
        )

        # ========================
        # SPENT
        # ========================
        spent = await db.scalar(
            select(func.sum(Expense.amount)).where(Expense.project_id.in_(project_ids))
        )

        # ========================
        # SAFE CALCULATION
        # ========================
        budget_val = float(budget or 0)
        spent_val = float(spent or 0)

        utilization = (spent_val / budget_val * 100) if budget_val else 0

        # ========================
        # RESPONSE
        # ========================
        return {
            "role": "manager",
            "budget": budget_val,
            "spent": spent_val,
            "budget_utilization": round(utilization, 2),
        }

    version = await r.get_cache_version(redis, VERSION_KEY)
    return await cache_get_set(redis, "manager_dashboard", version, logic)


# =========================================
# ACCOUNTANT DASHBOARD
# =========================================
@router.get("/accountant")
async def accountant_dashboard(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    # if current_user.role != UserRole.ACCOUNTANT:
    #     return {"error": "Access denied"}

    async def logic():
        # total_budget = await db.scalar(select(func.sum(m.Project.budget)))
        project_ids = await get_user_project_ids(db, current_user)

        total_budget = await db.scalar(
            select(func.sum(BOQ.total_cost)).where(
                BOQ.is_latest == True, BOQ.project_id.in_(project_ids)
            )
        )

        total_spent = await db.scalar(
            select(func.sum(Expense.amount)).where(Expense.project_id.in_(project_ids))
        )

        receivables = await db.scalar(select(func.sum(Invoice.pending_amount)))

        inflow = await db.scalar(
            select(func.sum(Transaction.amount)).where(Transaction.type == "receipt")
        )
        outflow = await db.scalar(
            select(func.sum(Transaction.amount)).where(Transaction.type == "payment")
        )

        kpi = await get_kpi_comparison(db)

        return {
            "role": "accountant",
            "total_budget": float(total_budget or 0),
            "total_spent": float(total_spent or 0),
            "receivables": float(receivables or 0),
            "cash_balance": float((inflow or 0) - (outflow or 0)),
            "kpi_comparison": kpi,
        }

    version = await r.get_cache_version(redis, VERSION_KEY)
    return await cache_get_set(redis, "accountant_dashboard", version, logic)


# =========================================
# EXPORT API (PDF + EXCEL)
# =========================================
@router.get("/export")
async def export_dashboard(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):

    data = {
        "user": current_user.id,
        "role": current_user.role,
        "date": str(datetime.utcnow()),
    }

    # ===== PDF =====
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()

    content = [
        Paragraph("Dashboard Export", styles["Title"]),
        Paragraph(str(data), styles["Normal"]),
    ]

    doc.build(content)
    buffer.seek(0)

    # ===== EXCEL =====
    df = pd.DataFrame([data])
    excel_buffer = io.BytesIO()
    df.to_excel(excel_buffer, index=False)
    excel_buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=dashboard.pdf"},
    )


# =========================================
# GRAPH APIs
# =========================================
@router.get("/graph/labour")
async def labour_trend(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = f"dashboard:{version}:labour:{current_user.id}"

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return cached

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

    response = [{"date": r[0], "count": r[1]} for r in result.all()]

    await r.cache_set_json(redis, cache_key, response)

    return response


@router.get("/graph/expense")
async def expense_trend(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = f"dashboard:{version}:expense:{current_user.id}"

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return cached

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

    response = [{"date": r[0], "amount": float(r[1] or 0)} for r in result.all()]

    await r.cache_set_json(redis, cache_key, response)

    return response


@router.get("/graph/combined")
async def dashboard_graph(
    start_date: date | None = None,
    end_date: date | None = None,
    group_by: str = "daily",
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):

    # =========================
    #  1. VALIDATION (ADD HERE - TOP)
    # =========================
    if group_by not in ["daily", "weekly", "monthly"]:
        return {"error": "Invalid group_by"}

    version = await r.get_cache_version(redis, VERSION_KEY)

    cache_key = f"dashboard:{version}:graph:{current_user.id}:{start_date}:{end_date}:{group_by}"

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return cached

    project_ids = await get_user_project_ids(db, current_user)

    # =========================
    # GROUPING LOGIC
    # =========================
    if group_by == "monthly":
        labour_group = func.date_format(LabourAttendance.attendance_date, "%Y-%m")
        expense_group = func.date_format(Expense.expense_date, "%Y-%m")

    elif group_by == "weekly":
        labour_group = func.yearweek(LabourAttendance.attendance_date)
        expense_group = func.yearweek(Expense.expense_date)

    else:
        labour_group = LabourAttendance.attendance_date
        expense_group = Expense.expense_date

    # =========================
    # DATE FILTER
    # =========================
    labour_filters = [LabourAttendance.project_id.in_(project_ids)]
    expense_filters = [Expense.project_id.in_(project_ids)]

    if start_date:
        labour_filters.append(LabourAttendance.attendance_date >= start_date)
        expense_filters.append(Expense.expense_date >= start_date)

    if end_date:
        labour_filters.append(LabourAttendance.attendance_date <= end_date)
        expense_filters.append(Expense.expense_date <= end_date)

    # =========================
    #  LABOUR QUERY
    # =========================
    labour_result = await db.execute(
        select(
            labour_group.label("period"),
            func.count(LabourAttendance.id),
        )
        .where(*labour_filters)
        .group_by("period")
        .order_by("period")
        .limit(1000)
    )

    labour_data = {str(r[0]): r[1] for r in labour_result.all()}

    # =========================
    #  EXPENSE QUERY
    # =========================
    expense_result = await db.execute(
        select(
            expense_group.label("period"),
            func.sum(Expense.amount),
        )
        .where(*expense_filters)
        .group_by("period")
        .order_by("period")
        .limit(1000)  # ✅ 2. ADD LIMIT HERE ALSO
    )

    expense_data = {str(r[0]): float(r[1] or 0) for r in expense_result.all()}

    # =========================
    # MERGE
    # =========================
    all_keys = sorted(set(labour_data.keys()) | set(expense_data.keys()))

    response = [
        {
            "period": k,
            "labour_count": labour_data.get(k, 0),
            "expense_amount": expense_data.get(k, 0),
        }
        for k in all_keys
    ]

    # =========================
    #  3. CACHE TTL (ADD HERE - END)
    # =========================
    await r.cache_set_json(redis, cache_key, response)

    return response


@router.get("/graph/forecast")
async def expense_forecast(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)

    result = await db.execute(
        select(
            func.month(Expense.expense_date),
            func.sum(Expense.amount),
        )
        .where(Expense.project_id.in_(project_ids))
        .group_by(func.month(Expense.expense_date))
        .order_by(func.month(Expense.expense_date))
    )

    rows = result.all()

    # =========================
    #  DATA PREP
    # =========================
    months = [r[0] for r in rows]
    values = [float(r[1] or 0) for r in rows]

    if len(values) < 2:
        return {
            "message": "Not enough data",
            "forecast": 0,
            "confidence": 0,
        }

    # =========================
    #  TREND CALCULATION
    # =========================
    last = values[-1]
    prev = values[-2]

    growth = (last - prev) / prev if prev else 0
    forecast = last * (1 + growth)

    # =========================
    #  TREND LABEL
    # =========================
    if growth > 0.05:
        trend = "increasing"
    elif growth < -0.05:
        trend = "decreasing"
    else:
        trend = "stable"

    # =========================
    #  CONFIDENCE SCORE
    # =========================
    # Based on variance (simple + effective)
    avg = sum(values) / len(values)
    variance = sum((v - avg) ** 2 for v in values) / len(values)

    # Lower variance → higher confidence
    confidence = max(0, min(100, int(100 - (variance / (avg + 1)) * 100)))

    # =========================
    #  CHART DATA (IMPORTANT)
    # =========================
    chart_data = []

    for i in range(len(months)):
        chart_data.append(
            {
                "period": months[i],
                "actual": values[i],
                "forecast": None,
            }
        )

    # Add future prediction point
    next_month = (months[-1] or 0) + 1

    chart_data.append(
        {
            "period": next_month,
            "actual": None,
            "forecast": round(forecast, 2),
        }
    )

    # =========================
    # FINAL RESPONSE
    # =========================
    return {
        "summary": {
            "last_month": last,
            "predicted_next_month": round(forecast, 2),
            "growth_rate": round(growth, 2),
            "trend": trend,
            "confidence_percent": confidence,
        },
        "chart": chart_data,
    }


from collections import defaultdict
from statistics import mean


@router.get("/graph/advanced-forecast")
async def advanced_forecast(
    project_id: int | None = None,
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)

    if project_id:
        project_ids = [project_id]

    result = await db.execute(
        select(
            func.year(Expense.expense_date),
            func.month(Expense.expense_date),
            func.sum(Expense.amount),
        )
        .where(Expense.project_id.in_(project_ids))
        .group_by(
            func.year(Expense.expense_date),
            func.month(Expense.expense_date),
        )
        .order_by(
            func.year(Expense.expense_date),
            func.month(Expense.expense_date),
        )
    )

    rows = result.all()

    # =========================
    #  DATA STRUCTURE
    # =========================
    monthly_data = []
    values = []

    for y, mth, amt in rows:
        val = float(amt or 0)
        monthly_data.append({"year": y, "month": mth, "value": val})
        values.append(val)

    if len(values) < 3:
        return {"message": "Not enough data"}

    # ========================
    #  ROLLING 3-MONTH AVG
    # =========================
    rolling_forecast = mean(values[-3:])

    # =========================
    #  TREND (growth-based)
    # =========================
    growth = (values[-1] - values[-2]) / values[-2] if values[-2] else 0
    next_month_pred = values[-1] * (1 + growth)

    # =========================
    #  SEASONAL TREND (YEARLY)
    # =========================
    seasonal_map = defaultdict(list)

    for row in monthly_data:
        seasonal_map[row["month"]].append(row["value"])

    seasonal_avg = {month: round(mean(vals), 2) for month, vals in seasonal_map.items()}

    next_month = (monthly_data[-1]["month"] % 12) + 1
    seasonal_prediction = seasonal_avg.get(next_month, rolling_forecast)

    # =========================
    #  ANOMALY DETECTION
    # =========================
    avg_val = mean(values)
    threshold = avg_val * 1.5

    anomalies = [
        {
            "month": m["month"],
            "year": m["year"],
            "value": m["value"],
        }
        for m in monthly_data
        if m["value"] > threshold
    ]

    # =========================
    #  PER-PROJECT FORECAST
    # =========================
    per_project = []

    if not project_id:
        proj_result = await db.execute(
            select(
                Expense.project_id,
                func.sum(Expense.amount),
            ).group_by(Expense.project_id)
        )

        for p_id, amt in proj_result.all():
            per_project.append(
                {
                    "project_id": p_id,
                    "total_spent": float(amt or 0),
                }
            )

    # =========================
    #  FINAL CHART DATA
    # =========================
    chart = [
        {
            "year": m["year"],
            "month": m["month"],
            "actual": m["value"],
            "forecast": None,
        }
        for m in monthly_data
    ]

    chart.append(
        {
            "year": monthly_data[-1]["year"],
            "month": next_month,
            "actual": None,
            "forecast": round(seasonal_prediction, 2),
        }
    )

    # =========================
    # FINAL RESPONSE
    # =========================
    return {
        "summary": {
            "last_value": values[-1],
            "next_month_prediction": round(next_month_pred, 2),
            "rolling_3_month_avg": round(rolling_forecast, 2),
            "seasonal_prediction": round(seasonal_prediction, 2),
            "growth_rate": round(growth, 2),
        },
        "seasonal_trend": seasonal_avg,
        "anomalies": anomalies,
        "per_project": per_project,
        "chart": chart,
    }


import numpy as np


@router.get("/graph/ml-forecast")
async def ml_forecast(
    project_id: int | None = None,
    periods: int = 3,
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)

    if project_id:
        project_ids = [project_id]

    result = await db.execute(
        select(
            func.year(Expense.expense_date),
            func.month(Expense.expense_date),
            func.sum(Expense.amount),
        )
        .where(Expense.project_id.in_(project_ids))
        .group_by(
            func.year(Expense.expense_date),
            func.month(Expense.expense_date),
        )
        .order_by(
            func.year(Expense.expense_date),
            func.month(Expense.expense_date),
        )
    )

    rows = result.all()

    if len(rows) < 3:
        return {"message": "Not enough data for ML forecast"}

    # =========================
    #  PREP DATA
    # =========================
    values = [float(r[2] or 0) for r in rows]

    # X = time index (0,1,2,...)
    X = np.arange(len(values))
    y = np.array(values)

    # =========================
    #  LINEAR REGRESSION
    # =========================
    slope, intercept = np.polyfit(X, y, 1)

    # =========================
    #  FUTURE PREDICTION
    # =========================
    future_x = np.arange(len(values), len(values) + periods)
    predictions = slope * future_x + intercept

    # =========================
    #  CHART DATA
    # =========================
    chart = []

    for i in range(len(values)):
        chart.append(
            {
                "index": i,
                "actual": values[i],
                "predicted": None,
            }
        )

    for i, val in zip(future_x, predictions):
        chart.append(
            {
                "index": int(i),
                "actual": None,
                "predicted": round(float(val), 2),
            }
        )

    # =========================
    #  MODEL QUALITY (R² SCORE)
    # =========================
    y_pred = slope * X + intercept
    ss_total = np.sum((y - np.mean(y)) ** 2)
    ss_residual = np.sum((y - y_pred) ** 2)

    r2_score = 1 - (ss_residual / ss_total) if ss_total else 0

    # =========================
    # FINAL RESPONSE
    # =========================
    return {
        "model": "linear_regression",
        "accuracy_r2": round(float(r2_score), 3),
        "trend_slope": round(float(slope), 2),
        "predictions": [round(float(p), 2) for p in predictions],
        "chart": chart,
    }
