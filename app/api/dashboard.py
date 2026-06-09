from fastapi import APIRouter, Depends, HTTPException, Query, logger
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timedelta
import io

from app.core.enums import InvoiceStatus
from app.db.session import get_db_session
from app.core import dependencies as d
from app.models.user import User, UserRole
from app.models import project as m
from app.models.expense import Expense
from app.models.invoice import Invoice, Transaction
from app.models.user import UserAttendance
from app.models.boq import BOQ
from app.models.material import Material
from app.models.project import (
    WorkActivity,
    DailyProgressEntry,
    Issue,
    Milestone,
    Task,
    DailySiteReport,
    QCRecord,
    SafetyIncident,
)
from fastapi import APIRouter, Depends, HTTPException, Query
import logging

logger = logging.getLogger(__name__)
from app.models.approval import Approval
from app.models.user import User, UserRole, ActivityLog
from app.cache import redis as r
from app.schemas.dashboard import (
    EnhancedDashboardOut,
    DashboardVitals,
    IssueStats,
    MaterialStockStatus,
    TodayWorkSummary,
    DisciplineProgress,
    RecentExpense,
    MilestoneTimelineEntry,
    AdminDashboardOut,
    AdminVitals,
    AdminProjectOverview,
    ProjectActivity,
    AccountantDashboardOut,
    AccountantVitals,
    ProjectBudgetSummary,
    MonthlyTrend,
    PMCommandCenterOut,
    PMKpiCards,
    PMProjectPerformance,
    PMResourceOrchestration,
    PMCostTrackingItem,
    PMDelayRiskAnalysis,
    PMCriticalAlert,
    PMTaskOverview,
)

# PDF + Excel
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
import pandas as pd

from app.utils.common import assert_project_access
from app.utils.helpers import NotFoundError

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
@router.get("/admin", response_model=AdminDashboardOut)
async def admin_dashboard(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    if current_user.role != UserRole.ADMIN.value:
        return {"error": "Access denied"}

    async def logic():
        today = date.today()

        # 1. Project Overview
        project_stats = await db.execute(
            select(
                func.count(m.Project.id),
                func.sum(case((m.Project.status == "Active", 1), else_=0)),
                func.sum(case((m.Project.status == "Completed", 1), else_=0)),
                func.sum(case((m.Project.end_date < today, 1), else_=0)),
            )
        )
        total, active, completed, delayed = project_stats.one()

        # 2. Financials
        revenue = await db.scalar(
            select(func.sum(Invoice.total_amount)).where(Invoice.status == "paid")
        )
        expense = await db.scalar(select(func.sum(Expense.amount)))

        # 3. Vitals
        labour_today = await db.scalar(
            select(func.sum(DailySiteReport.total_labour)).where(
                DailySiteReport.report_date == today
            )
        )
        pending_approvals = await db.scalar(
            select(func.count(Approval.id)).where(Approval.status == "Pending")
        )
        action_items = await db.scalar(
            select(func.count(Issue.id)).where(
                Issue.priority == "HIGH", Issue.status == "OPEN"
            )
        )
        material_reports = await db.scalar(
            select(func.count(DailySiteReport.id)).where(
                DailySiteReport.report_date == today,
                DailySiteReport.material_used != None,
            )
        )
        open_issues = await db.scalar(
            select(func.count(Issue.id)).where(Issue.status == "OPEN")
        )

        vitals = AdminVitals(
            total_labour_today=int(labour_today or 0),
            pending_approvals=int(pending_approvals or 0),
            action_items=int(action_items or 0),
            material_used_today=int(material_reports or 0),
            site_issues_open=int(open_issues or 0),
        )

        # 4. Active Users
        active_users_count = await db.scalar(
            select(func.count(User.id)).where(
                User.is_active == True, User.is_deleted == False
            )
        )

        # 5. Master Projects
        projects_query = await db.execute(select(m.Project))
        projects = projects_query.scalars().all()
        master_projects = []

        for p in projects:
            # Progress
            avg_progress = (
                await db.scalar(
                    select(func.avg(m.Task.completion_percentage)).where(
                        m.Task.project_id == p.id
                    )
                )
                or 0
            )

            # Planned Progress
            planned = 0
            if p.start_date and p.end_date:
                total_days = (p.end_date - p.start_date).days
                elapsed_days = (today - p.start_date).days
                if total_days > 0:
                    planned = max(0, min(100, (elapsed_days / total_days) * 100))

            master_projects.append(
                AdminProjectOverview(
                    id=p.id,
                    name=p.project_name,
                    start_date=p.start_date,
                    end_date=p.end_date,
                    progress=round(float(avg_progress), 2),
                    performance_score=round(float(avg_progress) - planned, 2),
                    health=(
                        str(p.status.value)
                        if hasattr(p.status, "value")
                        else str(p.status)
                    ),
                )
            )

        # 6. Discipline Progress
        discipline_query = await db.execute(
            select(m.Task.discipline, func.avg(m.Task.completion_percentage)).group_by(
                m.Task.discipline
            )
        )
        discipline_progress = [
            DisciplineProgress(
                discipline=row[0] or "General",
                planned_percent=0,
                actual_percent=float(row[1] or 0),
            )
            for row in discipline_query.all()
        ]

        # 7. Recent Activities
        activities_query = await db.execute(
            select(ActivityLog, User.full_name)
            .join(User, ActivityLog.performed_by == User.id)
            .order_by(ActivityLog.created_at.desc())
            .limit(10)
        )
        recent_activities = []
        for log, user_name in activities_query.all():
            recent_activities.append(
                ProjectActivity(
                    type=log.action,
                    user=user_name or "Unknown",
                    description=(
                        str(log.details.get("message", log.action))
                        if log.details
                        else log.action
                    ),
                    time=log.created_at.strftime("%H:%M"),
                    project_name="Global",  # Could be enhanced to join with projects if entity_id is project
                )
            )

        kpi = await get_kpi_comparison(db)

        return AdminDashboardOut(
            project_overview={
                "total": total or 0,
                "active": active or 0,
                "completed": completed or 0,
                "delayed": delayed or 0,
            },
            financial={
                "revenue": float(revenue or 0),
                "expense": float(expense or 0),
                "profit": float((revenue or 0) - (expense or 0)),
            },
            vitals=vitals,
            active_users=int(active_users_count or 0),
            discipline_progress=discipline_progress,
            master_projects=master_projects,
            recent_activities=recent_activities,
            kpi_comparison=kpi,
        ).dict()

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
            select(func.count(UserAttendance.id)).where(
                UserAttendance.project_id.in_(project_ids),
                UserAttendance.attendance_date == today,
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
@router.get("/accountant", response_model=AccountantDashboardOut)
async def accountant_dashboard(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    async def logic():
        project_ids = await get_user_project_ids(db, current_user)

        # 1. Vitals
        total_revenue = await db.scalar(select(func.sum(Invoice.total_amount)))
        total_expense = await db.scalar(select(func.sum(Expense.amount)))
        pending_payments_count = await db.scalar(
            select(func.count(Invoice.id)).where(
                Invoice.status == InvoiceStatus.PENDING
            )
        )
        total_invoices_count = await db.scalar(select(func.count(Invoice.id)))

        vitals = AccountantVitals(
            total_revenue=float(total_revenue or 0),
            total_expense=float(total_expense or 0),
            pending_payments_count=int(pending_payments_count or 0),
            total_invoices_count=int(total_invoices_count or 0),
        )

        # 2. Consumption Status
        total_budget = await db.scalar(
            select(func.sum(BOQ.total_cost)).where(
                BOQ.is_latest == True, BOQ.project_id.in_(project_ids)
            )
        )
        total_spent = float(total_expense or 0)
        total_budget_val = float(total_budget or 0)
        consumption_percentage = (
            (total_spent / total_budget_val * 100) if total_budget_val else 0
        )

        consumption_status = {
            "total_budget": total_budget_val,
            "total_spent": total_spent,
            "percentage": round(consumption_percentage, 1),
        }

        # 3. Monthly Expense Analysis (Last 6 months)
        monthly_trends = []
        for i in range(5, -1, -1):
            target_date = datetime.utcnow() - timedelta(days=i * 30)
            month_str = target_date.strftime("%b")

            # Simple month-based aggregation
            month_start = target_date.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            if i == 0:
                month_end = datetime.utcnow()
            else:
                month_end = (month_start + timedelta(days=32)).replace(
                    day=1
                ) - timedelta(seconds=1)

            month_expense = await db.scalar(
                select(func.sum(Expense.amount)).where(
                    Expense.expense_date >= month_start.date(),
                    Expense.expense_date <= month_end.date(),
                )
            )
            monthly_trends.append(
                MonthlyTrend(month=month_str, amount=float(month_expense or 0))
            )

        # 4. Project Cost Summary
        project_cost_summary = []
        projects_query = await db.execute(
            select(m.Project).where(m.Project.id.in_(project_ids))
        )
        projects = projects_query.scalars().all()

        for p in projects:
            p_budget = (
                await db.scalar(
                    select(func.sum(BOQ.total_cost)).where(
                        BOQ.project_id == p.id, BOQ.is_latest == True
                    )
                )
                or 0
            )
            p_actual = (
                await db.scalar(
                    select(func.sum(Expense.amount)).where(Expense.project_id == p.id)
                )
                or 0
            )

            variance = (
                ((float(p_actual) - float(p_budget)) / float(p_budget) * 100)
                if p_budget
                else 0
            )

            project_cost_summary.append(
                ProjectBudgetSummary(
                    project_name=p.project_name,
                    budgeted=float(p_budget),
                    actual=float(p_actual),
                    variance_percent=round(variance, 1),
                )
            )

        # 5. Recent Invoices
        recent_invoices_query = await db.execute(
            select(Invoice, m.Project.project_name)
            .join(m.Project, Invoice.project_id == m.Project.id)
            .order_by(Invoice.created_at.desc())
            .limit(5)
        )
        recent_invoices = []
        for inv, proj_name in recent_invoices_query.all():
            recent_invoices.append(
                {
                    "invoice_id": inv.id,
                    "project_name": proj_name,
                    "amount": float(inv.total_amount),
                    "status": (
                        inv.status.value
                        if hasattr(inv.status, "value")
                        else str(inv.status)
                    ),
                    "date": inv.created_at.strftime("%Y-%m-%d"),
                }
            )

        # 6. Recent Transactions
        recent_tx_query = await db.execute(
            select(Transaction, m.Project.project_name)
            .join(m.Project, Transaction.project_id == m.Project.id)
            .order_by(Transaction.created_at.desc())
            .limit(5)
        )
        recent_transactions = []
        for tx, proj_name in recent_tx_query.all():
            recent_transactions.append(
                {
                    "type": tx.type,
                    "description": (
                        f"Payment for {proj_name}"
                        if tx.type == "payment"
                        else f"Receipt from {proj_name}"
                    ),
                    "amount": float(tx.amount),
                    "time": tx.created_at.strftime("%H:%M"),
                }
            )

        return {
            "vitals": vitals.dict(),
            "consumption_status": consumption_status,
            "monthly_expense_analysis": [t.dict() for t in monthly_trends],
            "project_cost_summary": [p.dict() for p in project_cost_summary],
            "recent_invoices": recent_invoices,
            "recent_transactions": recent_transactions,
        }

    version = await r.get_cache_version(redis, VERSION_KEY)
    return await cache_get_set(redis, "accountant_dashboard", version, logic)


# =========================================
# PM COMMAND CENTER (IMAGE MATCH)
# =========================================
@router.get("/pm-command-center", response_model=PMCommandCenterOut)
async def pm_command_center(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    async def logic():
        project_ids = await get_user_project_ids(db, current_user)
        today = date.today()
        now = datetime.utcnow()

        # 1. KPIs
        total_projects = len(project_ids)
        active_deployments = (
            await db.scalar(
                select(func.count(DailySiteReport.id)).where(
                    DailySiteReport.project_id.in_(project_ids),
                    DailySiteReport.report_date == today,
                )
            )
            or 0
        )

        avg_completion = (
            await db.scalar(
                select(func.avg(m.Task.completion_percentage)).where(
                    m.Task.project_id.in_(project_ids)
                )
            )
            or 0
        )

        delayed_sites = (
            await db.scalar(
                select(func.count(m.Project.id)).where(
                    m.Project.id.in_(project_ids),
                    m.Project.end_date < today,
                    m.Project.status != "Completed",
                )
            )
            or 0
        )

        pending_reviews = (
            await db.scalar(
                select(func.count(Approval.id)).where(
                    Approval.status == "Pending"
                )  # Should ideally filter by project, but model lacks project_id directly
            )
            or 0
        )

        kpis = PMKpiCards(
            total_managed_projects=total_projects,
            active_site_deployments=int(active_deployments),
            avg_completion_percent=round(float(avg_completion), 1),
            delayed_sites_count=int(delayed_sites),
            pending_reviews_count=int(pending_reviews),
        )

        # 2. Project Performance Overview
        projects_query = await db.execute(
            select(m.Project).where(m.Project.id.in_(project_ids))
        )
        projects = projects_query.scalars().all()
        performance = []

        for p in projects:
            p_progress = (
                await db.scalar(
                    select(func.avg(m.Task.completion_percentage)).where(
                        m.Task.project_id == p.id
                    )
                )
                or 0
            )

            p_budget = (
                await db.scalar(
                    select(func.sum(BOQ.total_cost)).where(
                        BOQ.project_id == p.id, BOQ.is_latest == True
                    )
                )
                or 1
            )  # avoid div by zero

            p_spent = (
                await db.scalar(
                    select(func.sum(Expense.amount)).where(Expense.project_id == p.id)
                )
                or 0
            )

            status = "ON TRACK"
            if p.end_date and p.end_date < today:
                status = "DELAYED"
            elif float(p_progress) < 20 and (today - (p.start_date or today)).days > 30:
                status = "AT RISK"

            performance.append(
                PMProjectPerformance(
                    id=p.id,
                    name=p.project_name,
                    business_id=p.business_id,
                    progress=round(float(p_progress), 1),
                    status=status,
                    start_date=p.start_date,
                    end_date=p.end_date,
                    budget_utilization_actual=float(p_spent),
                    budget_utilization_total=float(p_budget),
                )
            )

        # 3. Quality & Safety Scores
        qc_score = (
            await db.scalar(
                select(func.avg(QCRecord.result)).where(  # Assuming result is 0-100
                    QCRecord.project_id.in_(project_ids)
                )
            )
            or 85
        )  # Default high for demo if no data

        safety_score = 95  # Placeholder as SafetyIncident doesn't have a numerical score field easily aggregatable

        # 4. Resource Orchestration
        engineers_query = await db.execute(
            select(User, m.Project.project_name)
            .join(m.ProjectMember, User.id == m.ProjectMember.user_id)
            .join(m.Project, m.ProjectMember.project_id == m.Project.id)
            .where(
                User.role == UserRole.SITE_ENGINEER.value, m.Project.id.in_(project_ids)
            )
            .limit(5)
        )
        resources = []
        for eng, proj_name in engineers_query.all():
            # Infer status from activity
            last_activity = await db.scalar(
                select(ActivityLog.created_at)
                .where(ActivityLog.performed_by == eng.id)
                .order_by(ActivityLog.created_at.desc())
            )

            status = "Off Duty"
            last_seen = "Yesterday"
            if last_activity:
                diff = datetime.utcnow() - last_activity
                if diff.total_seconds() < 3600:
                    status = "On Site"
                    last_seen = f"{int(diff.total_seconds()/60)} mins ago"
                elif diff.total_seconds() < 86400:
                    status = "Travelling"
                    last_seen = f"{int(diff.total_seconds()/3600)} hours ago"

            resources.append(
                PMResourceOrchestration(
                    user_id=eng.id,
                    full_name=eng.full_name or "Unknown",
                    initials="".join([n[0] for n in (eng.full_name or "U").split()]),
                    assigned_project=proj_name,
                    status=status,
                    last_seen=last_seen,
                )
            )

        # 5. Cost Tracking (Last 7 months)
        cost_tracking = []
        for i in range(6, -1, -1):
            d_date = now - timedelta(days=i * 30)
            month_str = d_date.strftime("%b")

            actual = (
                await db.scalar(
                    select(func.sum(Expense.amount)).where(
                        Expense.project_id.in_(project_ids),
                        func.month(Expense.expense_date) == d_date.month,
                    )
                )
                or 0
            )

            # Mock budget for trend (or take from BOQ if possible)
            budget = float(actual) * 0.9 if i % 2 == 0 else float(actual) * 1.1

            cost_tracking.append(
                PMCostTrackingItem(
                    month=month_str, actual_cost=float(actual), budget=float(budget)
                )
            )

        # 6. Delay & Risk Analysis
        risks = []
        issues_query = await db.execute(
            select(Issue, m.Project.project_name)
            .join(m.Project, Issue.project_id == m.Project.id)
            .where(Issue.project_id.in_(project_ids), Issue.status == "OPEN")
            .limit(4)
        )
        for issue, proj_name in issues_query.all():
            risks.append(
                PMDelayRiskAnalysis(
                    project_name=proj_name,
                    risk_type=(
                        issue.category.value
                        if hasattr(issue.category, "value")
                        else str(issue.category)
                    ),
                    priority=(
                        issue.priority.value
                        if hasattr(issue.priority, "value")
                        else str(issue.priority)
                    ),
                    status="CRITICAL" if issue.priority == "HIGH" else "WARNING",
                )
            )

        # 7. Critical Alerts
        alerts = []
        # Budget alert check
        for p in performance:
            if p.budget_utilization_actual > p.budget_utilization_total:
                alerts.append(
                    PMCriticalAlert(
                        id=len(alerts) + 1,
                        alert_type="Budget Exceeded",
                        message=f"Actual cost is {int((p.budget_utilization_actual/p.budget_utilization_total - 1)*100)}% above forecast.",
                        project_name=p.name,
                        timestamp=now,
                    )
                )

        # Delay alert check
        for p in performance:
            if p.status == "DELAYED":
                alerts.append(
                    PMCriticalAlert(
                        id=len(alerts) + 1,
                        alert_type="Project Delay",
                        message="Foundation work is behind schedule.",
                        project_name=p.name,
                        timestamp=now,
                    )
                )

        # 8. Task Management Overview
        tasks_query = await db.execute(
            select(Task, User.full_name)
            .outerjoin(User, Task.assigned_user_id == User.id)
            .where(Task.project_id.in_(project_ids))
            .order_by(Task.end_date.asc())
            .limit(4)
        )
        task_mgmt = []
        for t, eng_name in tasks_query.all():
            task_mgmt.append(
                PMTaskOverview(
                    id=t.id,
                    task_name=t.title,
                    engineer_name=eng_name or "Unassigned",
                    status=(
                        t.status.value if hasattr(t.status, "value") else str(t.status)
                    ),
                    due_date=t.end_date,
                )
            )

        # 9. Recent Activity Feed
        activities_query = await db.execute(
            select(ActivityLog, User.full_name)
            .join(User, ActivityLog.performed_by == User.id)
            .where(
                ActivityLog.entity == "project", ActivityLog.entity_id.in_(project_ids)
            )
            .order_by(ActivityLog.created_at.desc())
            .limit(10)
        )
        recent_activities = []
        for log, user_name in activities_query.all():
            recent_activities.append(
                ProjectActivity(
                    type=log.action,
                    user=user_name or "Unknown",
                    description=(
                        str(log.details.get("message", log.action))
                        if log.details
                        else log.action
                    ),
                    time=log.created_at.strftime("%H:%M"),
                    project_name="Project",
                )
            )

        return PMCommandCenterOut(
            header_date=today.strftime("%B %d, %Y"),
            kpis=kpis,
            project_performance=performance,
            quality_score=int(qc_score),
            safety_score=int(safety_score),
            resource_orchestration=resources,
            cost_tracking=cost_tracking,
            risk_analysis=risks,
            critical_alerts=alerts,
            task_management=task_mgmt,
            recent_activities=recent_activities,
        ).dict()

    version = await r.get_cache_version(redis, VERSION_KEY)
    return await cache_get_set(redis, "pm_command_center", version, logic)


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


@router.get("/client")
async def client_dashboard(
    project_id: int,
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    if current_user.role not in [
        UserRole.CLIENT.value,
        UserRole.ADMIN.value,
    ]:
        return {"error": "Access denied"}

    async def logic():
        project_ids = await get_user_project_ids(db, current_user)

        # ========================
        # PROJECT
        # ========================
        project = await db.execute(
            select(
                m.Project.id,
                m.Project.status,
                m.Project.start_date,
                m.Project.end_date,
            ).where(m.Project.id == project_id, m.Project.id.in_(project_ids))
        )

        project = project.first()

        if not project:
            return {"error": "No project found"}

        db_project_id, status, start_date, end_date = project

        # ========================
        # PROGRESS
        # ========================
        progress = await db.scalar(
            select(func.avg(m.Task.completion_percentage)).where(
                m.Task.project_id == db_project_id
            )
        )

        # ========================
        # BUDGET
        # ========================
        budget_total = await db.scalar(
            select(func.sum(BOQ.total_cost)).where(
                BOQ.project_id == db_project_id,
                BOQ.is_latest == True,
            )
        )

        # ========================
        # EXPENSE
        # ========================
        total_expense = await db.scalar(
            select(func.sum(Expense.amount)).where(Expense.project_id == db_project_id)
        )

        budget_val = float(budget_total or 0)
        expense_val = float(total_expense or 0)

        budget_used_percent = (expense_val / budget_val) * 100 if budget_val else 0

        remaining_budget = budget_val - expense_val

        # ========================
        # MILESTONES
        # ========================
        milestones_total = await db.scalar(
            select(func.count(m.Milestone.id)).where(
                m.Milestone.project_id == db_project_id
            )
        )

        milestones_completed = await db.scalar(
            select(func.count(m.Milestone.id)).where(
                m.Milestone.project_id == db_project_id,
                m.Milestone.status == "Completed",
            )
        )

        # ========================
        # TASKS
        # ========================
        tasks_total = await db.scalar(
            select(func.count(m.Task.id)).where(m.Task.project_id == db_project_id)
        )

        tasks_completed = await db.scalar(
            select(func.count(m.Task.id)).where(
                m.Task.project_id == db_project_id,
                m.Task.status == "Completed",
            )
        )

        # ========================
        # DAYS REMAINING
        # ========================
        days_remaining = 0

        if end_date:
            days_remaining = (end_date - date.today()).days

        # ========================
        # RESPONSE
        # ========================
        return {
            "project_id": db_project_id,
            "status": status,
            "progress_percent": round(progress or 0, 2),
            "budget_total": budget_val,
            "total_expense": expense_val,
            "budget_used_percent": round(budget_used_percent, 2),
            "remaining_budget": round(remaining_budget, 2),
            "milestones_total": milestones_total or 0,
            "milestones_completed": milestones_completed or 0,
            "tasks_total": tasks_total or 0,
            "tasks_completed": tasks_completed or 0,
            "start_date": start_date,
            "end_date": end_date,
            "days_remaining": max(days_remaining, 0),
        }

    version = await r.get_cache_version(redis, VERSION_KEY)

    return await cache_get_set(
        redis,
        f"client_dashboard:{current_user.id}:{project_id}",
        version,
        logic,
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
            UserAttendance.attendance_date,
            func.count(UserAttendance.id),
        )
        .where(UserAttendance.project_id.in_(project_ids))
        .group_by(UserAttendance.attendance_date)
        .order_by(UserAttendance.attendance_date)
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
        labour_group = func.date_format(UserAttendance.attendance_date, "%Y-%m")
        expense_group = func.date_format(Expense.expense_date, "%Y-%m")

    elif group_by == "weekly":
        labour_group = func.yearweek(UserAttendance.attendance_date)
        expense_group = func.yearweek(Expense.expense_date)

    else:
        labour_group = UserAttendance.attendance_date
        expense_group = Expense.expense_date

    # =========================
    # DATE FILTER
    # =========================
    labour_filters = [UserAttendance.project_id.in_(project_ids)]
    expense_filters = [Expense.project_id.in_(project_ids)]

    if start_date:
        labour_filters.append(UserAttendance.attendance_date >= start_date)
        expense_filters.append(Expense.expense_date >= start_date)

    if end_date:
        labour_filters.append(UserAttendance.attendance_date <= end_date)
        expense_filters.append(Expense.expense_date <= end_date)

    # =========================
    #  LABOUR QUERY
    # =========================
    labour_result = await db.execute(
        select(
            labour_group.label("period"),
            func.count(UserAttendance.id),
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
        .limit(1000)  #  2. ADD LIMIT HERE ALSO
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


@router.get("/engineer/{project_id}", response_model=EnhancedDashboardOut)
async def project_engineer_dashboard(
    project_id: int,
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    # Check access
    await assert_project_access(
        db,
        project_id=project_id,
        current_user=current_user,
    )

    project = await db.get(m.Project, project_id)
    if not project:
        raise NotFoundError("Project not found")

    today = date.today()

    # 1. Labor Today
    labor_stats = await db.execute(
        select(
            func.sum(
                case(
                    (
                        m.DailySiteReport.skilled_labour > 0,
                        m.DailySiteReport.skilled_labour,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (
                        m.DailySiteReport.unskilled_labour > 0,
                        m.DailySiteReport.unskilled_labour,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (
                        m.DailySiteReport.total_labour > 0,
                        m.DailySiteReport.total_labour,
                    ),
                    else_=0,
                )
            ),
        ).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.report_date == today,
        )
    )
    skilled, unskilled, total_labour = labor_stats.one()

    # 2. Material Stock Status
    material_stats = await db.execute(
        select(
            Material.category, Material.remaining_stock, Material.minimum_stock_level
        ).where(Material.project_id == project_id, Material.is_deleted == False)
    )
    materials = []
    for cat, stock, min_level in material_stats.all():
        status = "OK"
        if stock <= 0:
            status = "Out of Stock"
        elif stock < min_level:
            status = "Low"
        materials.append(MaterialStockStatus(category=cat, status=status))

    # 3. Open Issues
    issue_stats_query = await db.execute(
        select(
            func.count(Issue.id), func.sum(case((Issue.priority == "HIGH", 1), else_=0))
        ).where(Issue.project_id == project_id, Issue.status == "OPEN")
    )
    total_issues, high_priority_issues = issue_stats_query.one()

    # 4. Today's Work Summary
    work_summary_query = await db.execute(
        select(WorkActivity.activity_name, WorkActivity.status)
        .join(DailyProgressEntry, WorkActivity.id == DailyProgressEntry.activity_id)
        .where(
            WorkActivity.project_id == project_id,
            DailyProgressEntry.entry_date == today,
        )
    )
    today_work = [
        TodayWorkSummary(activity_name=row[0], status=str(row[1]))
        for row in work_summary_query.all()
    ]

    # 5. Discipline-wise Progress
    discipline_query = await db.execute(
        select(WorkActivity.discipline, func.avg(WorkActivity.completion_percentage))
        .where(WorkActivity.project_id == project_id)
        .group_by(WorkActivity.discipline)
    )
    discipline_progress = [
        DisciplineProgress(
            discipline=row[0] or "General",
            planned_percent=0,
            actual_percent=float(row[1] or 0),
        )
        for row in discipline_query.all()
    ]

    # 6. Timeline (Milestones)
    milestones_query = await db.execute(
        select(Milestone)
        .where(Milestone.project_id == project_id)
        .order_by(Milestone.start_date)
    )
    timeline = [
        MilestoneTimelineEntry(
            id=ms.id,
            title=ms.title,
            status=str(ms.status),
            start_date=ms.start_date,
            end_date=ms.end_date,
        )
        for ms in milestones_query.scalars().all()
    ]

    # 7. Recent Expenses
    expenses_query = await db.execute(
        select(Expense)
        .where(Expense.project_id == project_id)
        .order_by(Expense.expense_date.desc())
        .limit(5)
    )
    recent_expenses = [
        RecentExpense(
            date=e.expense_date,
            type="Expense",
            category=e.category,
            note=e.remarks,
            amount=float(e.amount),
        )
        for e in expenses_query.scalars().all()
    ]

    # 8. Overall Progress & Planned
    progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(
            m.Task.project_id == project_id
        )
    )

    # Simple planned calculation based on timeline
    planned_progress = 0
    if project.start_date and project.end_date:
        total_days = (project.end_date - project.start_date).days
        elapsed_days = (today - project.start_date).days
        if total_days > 0:
            planned_progress = max(0, min(100, (elapsed_days / total_days) * 100))

    variance = float(progress or 0) - planned_progress

    # 9. Vitals Aggregation
    vitals = DashboardVitals(
        total_labour_today=int(total_labour or 0),
        skilled_labour=int(skilled or 0),
        unskilled_labour=int(unskilled or 0),
        active_activities=len(today_work),
        open_issues=IssueStats(
            total=int(total_issues or 0), high_priority=int(high_priority_issues or 0)
        ),
        material_stock_status=materials,
    )

    return EnhancedDashboardOut(
        project_id=project_id,
        project_name=project.project_name,
        status=str(project.status),
        progress=float(progress or 0),
        planned_progress=round(planned_progress, 2),
        variance=round(variance, 2),
        vitals=vitals,
        today_work_summary=today_work,
        discipline_progress=discipline_progress,
        timeline=timeline,
        recent_expenses=recent_expenses,
        weather={"condition": "Clear", "temperature": 32},  # Placeholder
    )


# =========================================
# COMMON HELPERS
# =========================================


def safe_divide(a, b):

    if not b:
        return 0

    return round(a / b, 2)


def validate_percentage(value):

    if value < 0:
        return 0

    if value > 100:
        return 100

    return round(value, 2)


def success_response(message, data=None):

    return {"success": True, "message": message, "data": data}


# =========================================
# ENTERPRISE CLIENT COMMAND CENTER
# =========================================


@router.get(
    "/client-command-center",
    summary="Enterprise Client Dashboard",
)
async def client_command_center(
    project_id: int = Query(..., gt=0, description="Project ID"),
    current_user: User = Depends(
        d.require_roles(
            [
                UserRole.ADMIN.value,
                UserRole.CLIENT.value,
                UserRole.PROJECT_MANAGER.value,
            ]
        )
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):

    logger.info(
        f"Dashboard accessed " f"user={current_user.id} " f"project={project_id}"
    )

    # =========================================
    # CACHE
    # =========================================

    cache_key = f"dashboard:" f"{project_id}:" f"{current_user.id}"

    try:

        cached = await r.cache_get_json(redis, cache_key)

        if cached:
            return cached

    except Exception as cache_error:

        logger.warning(f"Cache read failed: " f"{str(cache_error)}")

    # =========================================
    # PROJECT VALIDATION
    # =========================================

    project = await db.get(m.Project, project_id)

    if not project:

        raise HTTPException(status_code=404, detail="Project not found")

    # =========================================
    # TASK ANALYTICS
    # =========================================

    total_tasks = (
        await db.scalar(
            select(func.count(m.Task.id)).where(m.Task.project_id == project_id)
        )
        or 0
    )

    completed_tasks = (
        await db.scalar(
            select(func.count(m.Task.id)).where(
                m.Task.project_id == project_id,
                func.lower(m.Task.status) == "completed",
            )
        )
        or 0
    )

    pending_tasks = total_tasks - completed_tasks

    overall_progress = validate_percentage(
        safe_divide(completed_tasks * 100, total_tasks)
    )

    # =========================================
    # MILESTONE ANALYTICS
    # =========================================

    total_milestones = (
        await db.scalar(
            select(func.count(m.Milestone.id)).where(
                m.Milestone.project_id == project_id
            )
        )
        or 0
    )

    completed_milestones = (
        await db.scalar(
            select(func.count(m.Milestone.id)).where(
                m.Milestone.project_id == project_id,
                func.lower(m.Milestone.status) == "completed",
            )
        )
        or 0
    )

    # =========================================
    # BUDGET ANALYTICS
    # =========================================

    total_budget = (
        await db.scalar(
            select(func.sum(BOQ.total_cost)).where(
                BOQ.project_id == project_id, BOQ.is_latest == True
            )
        )
        or 0
    )

    total_expense = (
        await db.scalar(
            select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
        )
        or 0
    )

    total_budget = float(total_budget or 0)

    total_expense = float(total_expense or 0)

    remaining_budget = round(total_budget - total_expense, 2)

    budget_used_percent = validate_percentage(
        safe_divide(total_expense * 100, total_budget)
    )

    # =========================================
    # DAYS REMAINING
    # =========================================

    from datetime import date

    days_remaining = 0

    if project.end_date:

        days_remaining = (project.end_date - date.today()).days

        if days_remaining < 0:
            days_remaining = 0

    # =========================================
    # ACTIVE TASK
    # =========================================

    active_task_result = await db.execute(
        select(
            m.Task.title,
            m.Task.description,
            m.Task.status,
        )
        .where(m.Task.project_id == project_id)
        .order_by(desc(m.Task.id))
        .limit(1)
    )

    active_task = active_task_result.first()

    # =========================================
    # COMPLETED TASK
    # =========================================

    completed_task_result = await db.execute(
        select(m.Task.title)
        .where(
            m.Task.project_id == project_id, func.lower(m.Task.status) == "completed"
        )
        .order_by(desc(m.Task.id))
        .limit(1)
    )

    completed_task = completed_task_result.scalar()

    # =========================================
    # UPCOMING TASK
    # =========================================

    upcoming_task_result = await db.execute(
        select(m.Task.title)
        .where(
            m.Task.project_id == project_id, func.lower(m.Task.status) != "completed"
        )
        .order_by(m.Task.id.asc())
        .limit(1)
    )

    upcoming_task = upcoming_task_result.scalar()

    # =========================================
    # WORK PROGRESS
    # =========================================

    work_progress = {
        "progress_percent": overall_progress,
        "current_task": active_task[0] if active_task else None,
        "task_description": active_task[1] if active_task else None,
        "task_status": str(active_task[2]) if active_task else None,
        "last_completed": completed_task,
        "upcoming": upcoming_task,
    }

    # =========================================
    # LIVE EXECUTION FEED
    # =========================================

    activity_result = await db.execute(
        select(
            ActivityLog.id,
            ActivityLog.action,
            ActivityLog.created_at,
            ActivityLog.entity,
        )
        .where(ActivityLog.entity_id == project_id)
        .order_by(desc(ActivityLog.created_at))
        .limit(10)
    )

    activity_rows = activity_result.all()

    live_execution_feed = []

    for row in activity_rows:

        live_execution_feed.append(
            {
                "id": row[0],
                "action": row[1],
                "entity": row[3],
                "created_at": row[2],
            }
        )

    # =========================================
    # COST MANAGEMENT AUDIT
    # =========================================

    expense_result = await db.execute(
        select(Expense.category, func.sum(Expense.amount))
        .where(Expense.project_id == project_id)
        .group_by(Expense.category)
    )

    expense_rows = expense_result.all()

    cost_management_audit = []

    for row in expense_rows:

        actual = float(row[1] or 0)

        projected = round(actual * 1.1, 2)

        variance = round(projected - actual, 2)

        cost_management_audit.append(
            {
                "phase": row[0] or "General",
                "actual": actual,
                "projected": projected,
                "variance": variance,
            }
        )

    # =========================================
    # PROJECT HEALTH
    # =========================================

    project_status = (
        project.status.value
        if hasattr(project.status, "value")
        else str(project.status)
    )

    project_health = {
        "status": project_status,
        "overall_progress": overall_progress,
        "budget_health": "Good" if budget_used_percent < 80 else "Warning",
        "schedule_health": "On Track" if overall_progress >= 50 else "Delayed",
        "task_completion_rate": overall_progress,
        "budget_used_percent": budget_used_percent,
    }

    # =========================================
    # RESPONSE
    # =========================================

    response = success_response(
        "Client command center fetched successfully",
        {
            "project": {
                "id": project.id,
                "name": project.project_name,
                "status": project_status,
                "start_date": project.start_date,
                "end_date": project.end_date,
                "days_remaining": days_remaining,
            },
            "summary": {
                "overall_progress": overall_progress,
                "budget_total": total_budget,
                "total_expense": total_expense,
                "remaining_budget": remaining_budget,
                "budget_used_percent": budget_used_percent,
                "tasks": {
                    "completed": completed_tasks,
                    "pending": pending_tasks,
                    "total": total_tasks,
                },
                "milestones": {
                    "completed": completed_milestones,
                    "total": total_milestones,
                },
            },
            "work_progress": work_progress,
            "live_execution_feed": live_execution_feed,
            "cost_management_audit": cost_management_audit,
            "project_health": project_health,
        },
    )

    # =========================================
    # CACHE SAVE
    # =========================================

    try:

        await r.cache_set_json(redis, cache_key, response)

    except Exception as cache_error:

        logger.warning(f"Cache save failed: " f"{str(cache_error)}")

    return response
