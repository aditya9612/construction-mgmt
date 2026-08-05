from dateutil.utils import today
from pyrate_limiter import abstracts
from pyrate_limiter import abstracts
from pyrate_limiter import abstracts
from pyrate_limiter import abstracts
from fastapi import APIRouter, Depends, HTTPException, Query, logger
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select, func, case, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
import io
from typing import Optional, List, Dict, Any

from app.core.enums import (
    PRIORITY_MAP,
    AttendanceStatus,
    InvoiceStatus,
    ProjectStatus,
    IssueStatus,
    IssuePriority,
    MilestoneStatus,
    SafetyChecklistStatus,
    TaskPriority,
)
from app.db.session import get_db_session
from app.core import dependencies as d
from app.models.settings import UserSettings
from app.models.user import User, UserRole
from app.models.owner import Owner
from app.models.material import Supplier
from app.models import project as m
from app.models.expense import Expense
from app.models.invoice import Invoice, Transaction
from app.models.accountant import (
    Account,
    GSTReturn,
    VendorBill,
    JournalLine,
    JournalEntry,
)
from app.models.user import UserAttendance
from app.models.boq import BOQ
from app.models.quotation import QuotationMaster
from app.models.material import Material
from app.models.project import (
    TaskAssignment,
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
from app.models.owner import Owner
from app.models.material import Supplier
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
    AccountantKpiCards,
    RevenueExpenseTrend,
    CashFlowTrend,
    ProjectCostSummaryItem,
    AgingBucket,
    UpcomingTransactionItem,
    RecentActivityItem,
    PMCommandCenterOut,
    PMKpiCards,
    PMProjectPerformance,
    PMResourceOrchestration,
    PMCostTrackingItem,
    PMDelayRiskAnalysis,
    PMCriticalAlert,
    PMTaskOverview,
    LabourDashboardOut,
    LabourTaskItem,
    LabourActivityItem,
    PMSummaryOut,
)

# PDF + Excel
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
import pandas as pd
import csv

from app.utils.common import assert_project_access
from app.utils.helpers import NotFoundError, safe_divide, validate_percentage
from app.utils.timezone import get_naive_utc_now, get_naive_local_now, localize_datetime
from datetime import timezone
from app.models.labour import Labour, LabourProject, LabourAttendance, LabourPayroll
from app.core.enums import TaskStatus
from app.models.contractor import Contractor, ContractorProject

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


async def get_waterfall_budget(db: AsyncSession, project_ids: list[int]) -> float:
    if not project_ids:
        return 0.0
    boq_res = await db.execute(
        select(BOQ.project_id, func.sum(BOQ.total_cost))
        .where(BOQ.project_id.in_(project_ids), BOQ.is_latest == True)
        .group_by(BOQ.project_id)
    )
    boq_totals = {row[0]: float(row[1] or 0) for row in boq_res.all()}
    proj_res = await db.execute(
        select(m.Project.id, m.Project.budget_amount, QuotationMaster.grand_total)
        .outerjoin(QuotationMaster, m.Project.quotation_id == QuotationMaster.id)
        .where(m.Project.id.in_(project_ids))
    )
    total = 0.0
    for row in proj_res.all():
        pid = row[0]
        budget_amt = float(row[1] or 0)
        quotation_amt = float(row[2] or 0)
        boq_amt = boq_totals.get(pid, 0.0)
        if boq_amt > 0:
            total += boq_amt
        elif quotation_amt > 0:
            total += quotation_amt
        else:
            total += budget_amt
    return total


async def get_waterfall_budget_dict(
    db: AsyncSession, project_ids: list[int] = None
) -> dict[int, float]:
    boq_query = (
        select(BOQ.project_id, func.sum(BOQ.total_cost))
        .where(BOQ.is_latest == True)
        .group_by(BOQ.project_id)
    )
    if project_ids is not None:
        if not project_ids:
            return {}
        boq_query = boq_query.where(BOQ.project_id.in_(project_ids))
    boq_res = await db.execute(boq_query)
    boq_totals = {row[0]: float(row[1] or 0) for row in boq_res.all()}

    proj_query = select(
        m.Project.id, m.Project.budget_amount, QuotationMaster.grand_total
    ).outerjoin(QuotationMaster, m.Project.quotation_id == QuotationMaster.id)
    if project_ids is not None:
        proj_query = proj_query.where(m.Project.id.in_(project_ids))
    proj_res = await db.execute(proj_query)

    result = {}
    for row in proj_res.all():
        pid = row[0]
        budget_amt = float(row[1] or 0)
        quotation_amt = float(row[2] or 0)
        boq_amt = boq_totals.get(pid, 0.0)
        if boq_amt > 0:
            result[pid] = boq_amt
        elif quotation_amt > 0:
            result[pid] = quotation_amt
        else:
            result[pid] = budget_amt
    return result


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
    now = get_naive_local_now()
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
# ADMIN DASHBOARD (FIXED)
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
        today = get_naive_local_now().date()

        # ==========================================
        # 1. Project Overview
        # ==========================================
        project_stats = await db.execute(
            select(
                func.count(m.Project.id),
                # Active: started, not completed, end date not crossed (or none)
                func.sum(
                    case(
                        (
                            (
                                (m.Project.start_date <= today)
                                & (
                                    (m.Project.end_date == None)
                                    | (m.Project.end_date >= today)
                                )
                                & (m.Project.status != ProjectStatus.COMPLETED.value)
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                # Completed
                func.sum(
                    case(
                        (m.Project.status == ProjectStatus.COMPLETED.value, 1),
                        else_=0,
                    )
                ),
                # Delayed: end date crossed, not completed
                func.sum(
                    case(
                        (
                            (
                                (m.Project.end_date != None)
                                & (m.Project.end_date < today)
                                & (m.Project.status != ProjectStatus.COMPLETED.value)
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
        )

        total, active, completed, delayed = project_stats.one()

        project_overview = {
            "total": int(total or 0),
            "active": int(active or 0),
            "completed": int(completed or 0),
            "delayed": int(delayed or 0),
        }

        # ==========================================
        # 2. Financial
        # ==========================================
        revenue = await db.scalar(
            select(func.sum(Invoice.total_amount)).where(
                Invoice.status == InvoiceStatus.PAID.value
            )
        )

        expense = await db.scalar(select(func.sum(Expense.amount)))

        financial = {
            "revenue": float(revenue or 0),
            "expense": float(expense or 0),
            "profit": float((revenue or 0) - (expense or 0)),
        }

        # ==========================================
        # 3. Vitals
        # ==========================================
        labour_today = await db.scalar(
            select(func.count(func.distinct(UserAttendance.user_id)))
            .join(User, User.id == UserAttendance.user_id)
            .where(
                UserAttendance.attendance_date == today,
                UserAttendance.status != "absent",
                User.role == UserRole.LABOUR.value,
            )
        )

        pending_approvals = await db.scalar(
            select(func.count(Approval.id)).where(Approval.status == "Pending")
        )

        action_items = await db.scalar(
            select(func.count(Issue.id)).where(
                Issue.priority == IssuePriority.HIGH.value,
                Issue.status == IssueStatus.OPEN.value,
            )
        )

        material_reports = await db.scalar(
            select(func.count(DailySiteReport.id)).where(
                DailySiteReport.report_date == today,
                DailySiteReport.material_used.is_not(None),
            )
        )

        open_issues = await db.scalar(
            select(func.count(Issue.id)).where(Issue.status == IssueStatus.OPEN.value)
        )

        vitals = AdminVitals(
            total_labour_today=int(labour_today or 0),
            pending_approvals=int(pending_approvals or 0),
            action_items=int(action_items or 0),
            material_used_today=int(material_reports or 0),
            site_issues_open=int(open_issues or 0),
        )

        # ==========================================
        # 4. Master Projects
        # (optimized: avg progress fetched in ONE grouped
        #  query instead of N queries inside the loop)
        # ==========================================
        projects_query = await db.execute(
            select(m.Project).order_by(m.Project.id.asc())
        )
        projects = projects_query.scalars().all()

        avg_progress_rows = await db.execute(
            select(
                m.Task.project_id,
                func.avg(m.Task.completion_percentage),
            ).group_by(m.Task.project_id)
        )
        avg_progress_map = {
            pid: float(avg or 0) for pid, avg in avg_progress_rows.all()
        }

        master_projects = []

        for p in projects:
            avg_progress = avg_progress_map.get(p.id, 0.0)

            # Planned Progress
            planned = 0.0
            if p.start_date and p.end_date and p.start_date <= today:
                total_days = (p.end_date - p.start_date).days
                elapsed_days = (today - p.start_date).days
                if total_days > 0:
                    elapsed_days = max(0, min(elapsed_days, total_days))
                    planned = (elapsed_days / total_days) * 100

            # Performance Score = Actual - Planned
            performance_score = round(avg_progress - planned, 2)
            performance_score = max(-100, min(100, performance_score))

            # Dynamic Health (same logic as PM Dashboard)
            health = "ON TRACK"

            if p.status == ProjectStatus.COMPLETED.value:
                health = "COMPLETED"
            elif p.status == ProjectStatus.ON_HOLD.value:
                health = "ON_HOLD"
            elif p.end_date and p.end_date < today:
                health = "DELAYED"
            elif p.start_date and p.end_date and p.start_date <= today:
                total_days = (p.end_date - p.start_date).days
                elapsed_days = (today - p.start_date).days
                if total_days > 0:
                    expected_progress = (
                        max(0, min(elapsed_days, total_days)) / total_days
                    ) * 100
                    if expected_progress >= 20 and avg_progress < (
                        expected_progress - 20
                    ):
                        health = "AT RISK"
            elif p.status == ProjectStatus.PLANNED.value:
                health = "PLANNED"

            master_projects.append(
                AdminProjectOverview(
                    id=p.id,
                    name=p.project_name,
                    start_date=p.start_date,
                    end_date=p.end_date,
                    progress=round(avg_progress, 2),
                    performance_score=performance_score,
                    health=health,
                )
            )

        # ==========================================
        # Active Users
        # ==========================================
        active_users = (
            await db.scalar(select(func.count(User.id)).where(User.is_active == True))
            or 0
        )

        # ==========================================
        # 5. Recent Activities
        # ==========================================
        activities_query = await db.execute(
            select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(10)
        )

        recent_activities = [
            ProjectActivity(
                type=act.action,
                user="System",
                description=act.action,
                time=act.created_at.strftime("%d %b %H:%M"),
                project_name="Global",
            )
            for act in activities_query.scalars().all()
        ]

        # ==========================================
        # Discipline Progress
        # ==========================================
        discipline_rows = await db.execute(
            select(
                m.Task.discipline,
                func.avg(m.Task.completion_percentage),
            )
            .where(m.Task.discipline.is_not(None))
            .group_by(m.Task.discipline)
        )

        discipline_progress = []

        for discipline, actual in discipline_rows.all():
            discipline_progress.append(
                DisciplineProgress(
                    discipline=discipline,
                    planned_percent=100.0,
                    actual_percent=float(actual or 0),
                )
            )

        # ==========================================
        # 6. KPI Comparison
        # ==========================================
        kpi_comparison = await get_kpi_comparison(db)

        # ==========================================
        # 7. Final Response
        # ==========================================
        return AdminDashboardOut(
            project_overview=project_overview,
            financial=financial,
            vitals=vitals,
            active_users=int(active_users),
            discipline_progress=discipline_progress,
            master_projects=master_projects,
            recent_activities=recent_activities,
            kpi_comparison=kpi_comparison,
        )

    # ==========================================
    # Cache wrapper
    # ==========================================
    version = await r.get_cache_version(redis, VERSION_KEY)

    return await cache_get_set(
        redis,
        "admin_dashboard",
        version,
        logic,
    )


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
        today = get_naive_local_now().date()

        labour = await db.scalar(
            select(func.count(func.distinct(UserAttendance.user_id)))
            .join(User, User.id == UserAttendance.user_id)
            .where(
                UserAttendance.project_id.in_(project_ids),
                UserAttendance.attendance_date == today,
                UserAttendance.status != "absent",
                User.role == UserRole.LABOUR.value,
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
        budget = await get_waterfall_budget(db, project_ids)

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

        # 1. KPIs
        from app.utils.accounting import get_primary_cash_account

        try:
            cash_acc = await get_primary_cash_account(db)
            cash_balance_query = await db.scalar(
                select(func.sum(JournalLine.debit - JournalLine.credit))
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(JournalLine.account_id == cash_acc.id)
                .where(JournalEntry.status == "Posted")
            )
            cash_balance = float(cash_balance_query or 0.0)
        except ValueError:
            cash_balance = 0.0

        bank_balance_query = await db.scalar(
            select(func.sum(JournalLine.debit - JournalLine.credit))
            .join(Account, JournalLine.account_id == Account.id)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(Account.name.ilike("%bank%"))
            .where(JournalEntry.status == "Posted")
        )
        bank_balance = float(bank_balance_query or 0.0)

        receivables = (
            await db.scalar(
                select(func.sum(Invoice.total_amount)).where(
                    Invoice.status == InvoiceStatus.PENDING.value
                )
            )
            or 1200000.0
        )

        payables_query = await db.scalar(
            select(func.sum(VendorBill.total_amount - VendorBill.amount_paid)).where(
                VendorBill.status == "PENDING"
            )
        )
        payables = float(payables_query or 0.0)

        total_budget = (
            await db.scalar(
                select(func.sum(BOQ.total_cost)).where(BOQ.is_latest == True)
            )
            or 0
        )
        total_spent = await db.scalar(select(func.sum(Expense.amount))) or 0

        net_profit = float(total_budget) - float(total_spent)

        gst_due = (
            await db.scalar(
                select(func.sum(GSTReturn.net_gst_payable)).where(
                    GSTReturn.status == "Draft"
                )
            )
            or 0
        )

        kpis = AccountantKpiCards(
            cash_balance=float(cash_balance),
            bank_balance=bank_balance,
            receivables=float(receivables),
            payables=payables,
            total_budget=float(total_budget),
            total_spent=float(total_spent),
            net_profit=net_profit,
            gst_due=float(gst_due),
        )

        # 2. Revenue vs Expense Trend
        rev_exp_trends = []
        for i in range(5, -1, -1):
            target_date = get_naive_local_now() - relativedelta(months=i)
            month_str = target_date.strftime("%b")

            month_start = target_date.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            month_end = (
                (month_start + relativedelta(months=1)) - timedelta(seconds=1)
                if i != 0
                else get_naive_local_now()
            )

            month_expense = (
                await db.scalar(
                    select(func.sum(Expense.amount)).where(
                        Expense.expense_date >= month_start.date(),
                        Expense.expense_date <= month_end.date(),
                    )
                )
                or 0
            )

            month_revenue = (
                await db.scalar(
                    select(func.sum(Invoice.total_amount)).where(
                        Invoice.created_at >= month_start,
                        Invoice.created_at <= month_end,
                    )
                )
                or 0
            )

            rev_exp_trends.append(
                RevenueExpenseTrend(
                    month=month_str,
                    revenue=float(month_revenue),
                    expense=float(month_expense),
                )
            )

            # 3. Cash Flow (Monthly Trend)
        cash_flow = []
        for i in range(5, -1, -1):
            target_date = get_naive_local_now() - relativedelta(months=i)
            month_str = target_date.strftime("%b")

            month_start = target_date.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            month_end = (
                (month_start + relativedelta(months=1)) - timedelta(seconds=1)
                if i != 0
                else get_naive_local_now()
            )

            c_inflow = (
                await db.scalar(
                    select(func.sum(Transaction.amount)).where(
                        Transaction.type == "receipt",
                        Transaction.created_at >= month_start,
                        Transaction.created_at <= month_end,
                    )
                )
                or 0.0
            )

            c_outflow = (
                await db.scalar(
                    select(func.sum(Transaction.amount)).where(
                        Transaction.type == "payment",
                        Transaction.created_at >= month_start,
                        Transaction.created_at <= month_end,
                    )
                )
                or 0.0
            )

            cash_flow.append(
                CashFlowTrend(
                    month=month_str, inflow=float(c_inflow), outflow=float(c_outflow)
                )
            )

        # 4. Project Cost Summary
        project_cost_summary = []
        projects_query = await db.execute(
            select(m.Project).where(m.Project.id.in_(project_ids))
        )

        budget_query = await db.execute(
            select(BOQ.project_id, func.sum(BOQ.total_cost))
            .where(BOQ.project_id.in_(project_ids), BOQ.is_latest == True)
            .group_by(BOQ.project_id)
        )
        acc_budget_map = {row[0]: row[1] or 0 for row in budget_query.all()}

        actual_query = await db.execute(
            select(Expense.project_id, func.sum(Expense.amount))
            .where(Expense.project_id.in_(project_ids))
            .group_by(Expense.project_id)
        )
        acc_actual_map = {row[0]: row[1] or 0 for row in actual_query.all()}

        for p in projects_query.scalars().all():
            p_budget = acc_budget_map.get(p.id, 0)
            p_actual = acc_actual_map.get(p.id, 0)

            project_cost_summary.append(
                ProjectCostSummaryItem(
                    project_name=p.project_name,
                    budgeted=float(p_budget),
                    spent=float(p_actual),
                    remaining=float(p_budget) - float(p_actual),
                )
            )

        # 5. Receivable Aging
        today_date = get_naive_local_now().date()
        inv_query = await db.execute(
            select(Invoice).where(Invoice.status == InvoiceStatus.PENDING.value)
        )

        r_buckets = {
            "0-30 Days": 0.0,
            "31-60 Days": 0.0,
            "61-90 Days": 0.0,
            "> 90 Days": 0.0,
        }
        total_r = 0.0
        for inv in inv_query.scalars().all():
            days_old = (today_date - inv.created_at.date()).days
            amt = float(inv.pending_amount if inv.pending_amount else inv.total_amount)
            total_r += amt
            if days_old <= 30:
                r_buckets["0-30 Days"] += amt
            elif days_old <= 60:
                r_buckets["31-60 Days"] += amt
            elif days_old <= 90:
                r_buckets["61-90 Days"] += amt
            else:
                r_buckets["> 90 Days"] += amt

        receivable_aging = [
            AgingBucket(
                period=k,
                amount=v,
                percentage=round((v / total_r) * 100) if total_r > 0 else 0,
            )
            for k, v in r_buckets.items()
        ]

        # 6. Payable Aging
        vb_query = await db.execute(
            select(VendorBill).where(VendorBill.status == "PENDING")
        )
        p_buckets = {
            "0-30 Days": 0.0,
            "31-60 Days": 0.0,
            "61-90 Days": 0.0,
            "> 90 Days": 0.0,
        }
        total_p = 0.0
        for vb in vb_query.scalars().all():
            days_old = (today_date - vb.bill_date).days
            amt = float(vb.total_amount - vb.amount_paid)
            total_p += amt
            if days_old <= 30:
                p_buckets["0-30 Days"] += amt
            elif days_old <= 60:
                p_buckets["31-60 Days"] += amt
            elif days_old <= 90:
                p_buckets["61-90 Days"] += amt
            else:
                p_buckets["> 90 Days"] += amt

        payable_aging = [
            AgingBucket(
                period=k,
                amount=v,
                percentage=round((v / total_p) * 100) if total_p > 0 else 0,
            )
            for k, v in p_buckets.items()
        ]

        # 7. Upcoming Payments
        upcoming_payments_query = await db.execute(
            select(VendorBill, Supplier)
            .join(Supplier, VendorBill.supplier_id == Supplier.id)
            .where(VendorBill.status == "PENDING")
            .order_by(VendorBill.due_date.asc())
            .limit(5)
        )
        upcoming_payments = []
        for vb, sup in upcoming_payments_query.all():
            upcoming_payments.append(
                UpcomingTransactionItem(
                    entity_name=sup.supplier_name,
                    date=vb.due_date.strftime("%d %b %Y"),
                    amount=float(vb.total_amount - vb.amount_paid),
                )
            )

        # 8. Upcoming Collections
        upcoming_cols_query = await db.execute(
            select(Invoice, Owner)
            .join(Owner, Invoice.owner_id == Owner.id)
            .where(Invoice.status == InvoiceStatus.PENDING.value)
            .order_by(Invoice.created_at.asc())
            .limit(5)
        )
        upcoming_collections = []
        for inv, own in upcoming_cols_query.all():
            due = (inv.created_at + timedelta(days=30)).strftime("%d %b %Y")
            upcoming_collections.append(
                UpcomingTransactionItem(
                    entity_name=own.owner_name,
                    date=due,
                    amount=float(
                        inv.pending_amount if inv.pending_amount else inv.total_amount
                    ),
                )
            )

        # 9. Notifications
        notifications = []
        gst_due_upcoming = await db.scalar(
            select(func.count(GSTReturn.id)).where(GSTReturn.status == "Draft")
        )
        if gst_due_upcoming and gst_due_upcoming > 0:
            notifications.append(
                f"GST Return filing due for {gst_due_upcoming} periods."
            )

        pending_approvals = await db.scalar(
            select(func.count(Approval.id)).where(Approval.status == "Pending")
        )
        if pending_approvals and pending_approvals > 0:
            notifications.append(f"Pending approval for {pending_approvals} vouchers.")

        # 10. Recent Activities
        recent_activities = []
        activities_query = await db.execute(
            select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(5)
        )
        for act in activities_query.scalars().all():
            recent_activities.append(
                RecentActivityItem(
                    time=act.created_at.strftime("%I:%M %p"), activity=act.action
                )
            )

        return AccountantDashboardOut(
            kpi_cards=kpis,
            revenue_vs_expense=rev_exp_trends,
            cash_flow=cash_flow,
            project_cost_summary=project_cost_summary,
            receivable_aging=receivable_aging,
            payable_aging=payable_aging,
            upcoming_payments=upcoming_payments,
            upcoming_collections=upcoming_collections,
            recent_activities=recent_activities,
            notifications=notifications,
        )

    version = await r.get_cache_version(redis, VERSION_KEY)
    return await cache_get_set(redis, "accountant_dashboard", version, logic)


# =========================================
# PROJECT MANAGER DASHBOARD
# =========================================


@router.get("/pm-command-center", response_model=PMCommandCenterOut)
async def pm_command_center(
    current_user: User = Depends(d.require_roles([UserRole.PROJECT_MANAGER.value])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    async def logic():
        project_ids = await get_user_project_ids(db, current_user)
        today = get_naive_local_now().date()
        now = datetime.utcnow()

        # 1. KPIs
        total_projects = len(project_ids)
        active_deployments = (
            await db.scalar(
                select(func.count(func.distinct(DailySiteReport.project_id))).where(
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
                    m.Project.status != ProjectStatus.COMPLETED.value,
                )
            )
            or 0
        )

        # Pending Reviews - Application Level Filtering
        all_pending_approvals = (
            (await db.execute(select(Approval).where(Approval.status == "Pending")))
            .scalars()
            .all()
        )

        expense_ids = [
            a.entity_id for a in all_pending_approvals if a.entity_type == "expense"
        ]
        material_ids = [
            a.entity_id for a in all_pending_approvals if a.entity_type == "material"
        ]
        bill_ids = [
            a.entity_id for a in all_pending_approvals if a.entity_type == "bill"
        ]

        valid_reviews = 0
        if expense_ids:
            valid_reviews += (
                await db.scalar(
                    select(func.count(Expense.id)).where(
                        Expense.id.in_(expense_ids), Expense.project_id.in_(project_ids)
                    )
                )
            ) or 0
        if material_ids:
            valid_reviews += (
                await db.scalar(
                    select(func.count(Material.id)).where(
                        Material.id.in_(material_ids),
                        Material.project_id.in_(project_ids),
                    )
                )
            ) or 0
        if bill_ids:
            valid_reviews += (
                await db.scalar(
                    select(func.count(VendorBill.id)).where(
                        VendorBill.id.in_(bill_ids),
                        VendorBill.project_id.in_(project_ids),
                    )
                )
            ) or 0

        pending_reviews = valid_reviews

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

        # Bulk Queries
        progress_query = await db.execute(
            select(m.Task.project_id, func.avg(m.Task.completion_percentage))
            .where(m.Task.project_id.in_(project_ids))
            .group_by(m.Task.project_id)
        )
        progress_map = {row[0]: row[1] or 0 for row in progress_query.all()}

        budget_query = await db.execute(
            select(BOQ.project_id, func.sum(BOQ.total_cost))
            .where(BOQ.project_id.in_(project_ids), BOQ.is_latest == True)
            .group_by(BOQ.project_id)
        )
        budget_map = {row[0]: row[1] or 0 for row in budget_query.all()}

        spent_query = await db.execute(
            select(Expense.project_id, func.sum(Expense.amount))
            .where(Expense.project_id.in_(project_ids))
            .group_by(Expense.project_id)
        )
        spent_map = {row[0]: row[1] or 0 for row in spent_query.all()}

        for p in projects:
            p_progress = progress_map.get(p.id, 0)
            p_budget = budget_map.get(p.id, 1)
            p_spent = spent_map.get(p.id, 0)

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

        # -------------------------------------------------------------------------
        # Safety Score Calculation
        #
        # Business Rule:
        # - Every project starts with a Safety Score of 100.
        # - Each SafetyIncident represents an actual safety violation/incident.
        # - Deduct penalty points for every incident.
        # - Apply additional penalties for PPE non-compliance and failed safety
        #   checklists.
        # - The final score is clamped between 0 and 100.
        #
        # Note:
        # These penalty values are business weights and can be adjusted in the
        # future if the organization defines a different safety scoring policy.
        # -------------------------------------------------------------------------
        BASE_INCIDENT_PENALTY = 5
        PPE_NON_COMPLIANCE_PENALTY = 3
        CHECKLIST_FAILURE_PENALTY = 2

        penalty_expr = (
            BASE_INCIDENT_PENALTY
            + case(
                (m.SafetyIncident.ppe_compliance == False, PPE_NON_COMPLIANCE_PENALTY),
                else_=0,
            )
            + case(
                (
                    m.SafetyIncident.safety_checklist_status
                    == SafetyChecklistStatus.FAILED,
                    CHECKLIST_FAILURE_PENALTY,
                ),
                else_=0,
            )
        )

        total_penalty = await db.scalar(
            select(func.sum(penalty_expr)).where(
                m.SafetyIncident.project_id.in_(project_ids)
            )
        )

        safety_score = max(0, 100 - int(total_penalty or 0))

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
            .where(
                Issue.project_id.in_(project_ids),
                Issue.status == IssueStatus.OPEN.value,
            )
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
            if (
                p.budget_utilization_total > 0
                and p.budget_utilization_actual > p.budget_utilization_total
            ):
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
                        message=f"Project deadline ({p.end_date}) has passed.",
                        project_name=p.name,
                        timestamp=now,
                    )
                )

        from sqlalchemy.orm import selectinload, joinedload
        from app.models.project import TaskAssignment

        # 8. Task Management Overview
        tasks_query = await db.execute(
            select(Task)
            .options(selectinload(Task.assignments).joinedload(TaskAssignment.user))
            .where(Task.project_id.in_(project_ids))
            .order_by(Task.end_date.asc())
            .limit(4)
        )
        task_mgmt = []
        for t in tasks_query.scalars().unique().all():
            engineers = [
                a.user.full_name for a in t.assignments if a.user and a.user.full_name
            ]
            eng_name = ", ".join(engineers) if engineers else "Unassigned"
            task_mgmt.append(
                PMTaskOverview(
                    id=t.id,
                    task_name=t.title,
                    engineer_name=eng_name,
                    status=(
                        t.status.value if hasattr(t.status, "value") else str(t.status)
                    ),
                    due_date=t.end_date,
                )
            )

        # 9. Recent Activity Feed
        activities_query = await db.execute(
            select(ActivityLog, User.full_name, m.Project.project_name)
            .join(User, ActivityLog.performed_by == User.id)
            .outerjoin(m.Project, ActivityLog.entity_id == m.Project.id)
            .where(
                ActivityLog.entity == "project", ActivityLog.entity_id.in_(project_ids)
            )
            .order_by(ActivityLog.created_at.desc())
            .limit(10)
        )
        recent_activities = []
        for log, user_name, proj_name in activities_query.all():
            recent_activities.append(
                ProjectActivity(
                    type=log.action,
                    user=user_name or "Unknown",
                    description=(
                        str(log.details.get("message", log.action))
                        if log.details
                        else log.action
                    ),
                    time=log.created_at.strftime("%b %d, %H:%M"),
                    project_name=proj_name or "Project",
                )
            )

        return PMCommandCenterOut(
            header_date=today.strftime("%B %d, %Y"),
            kpis=kpis,
            project_performance=performance,
            quality_score=int(qc_score),
            safety_score=int(safety_score),
            cost_tracking=cost_tracking,
            risk_analysis=risks,
            critical_alerts=alerts,
            task_management=task_mgmt,
            recent_activities=recent_activities,
        ).dict()

    version = await r.get_cache_version(redis, VERSION_KEY)
    return await cache_get_set(redis, "pm_command_center", version, logic)


@router.get("/project-manager-summary", response_model=PMSummaryOut)
async def pm_summary(
    current_user: User = Depends(d.require_roles([UserRole.PROJECT_MANAGER.value])),
    db: AsyncSession = Depends(get_db_session),
):
    project_ids = await get_user_project_ids(db, current_user)
    if not project_ids:
        return PMSummaryOut(
            total_projects=0,
            active_projects=0,
            completed_projects=0,
            delayed_projects=0,
            pending_approvals=0,
            open_issues=0,
            budget_utilized_percent=0.0,
            todays_activities=0,
        )

    # Project Counts
    projects = await db.scalars(
        select(m.Project.status).where(m.Project.id.in_(project_ids))
    )
    p_statuses = list(projects)

    total = len(p_statuses)
    active = sum(1 for s in p_statuses if s == ProjectStatus.ONGOING.value)
    completed = sum(1 for s in p_statuses if s == ProjectStatus.COMPLETED.value)
    delayed = sum(
        1 for s in p_statuses if s in ("DELAYED", ProjectStatus.ON_HOLD.value)
    )

    # Approvals
    all_pending_approvals = (
        (await db.execute(select(Approval).where(Approval.status == "Pending")))
        .scalars()
        .all()
    )

    expense_ids = [
        a.entity_id for a in all_pending_approvals if a.entity_type == "expense"
    ]
    material_ids = [
        a.entity_id for a in all_pending_approvals if a.entity_type == "material"
    ]
    bill_ids = [a.entity_id for a in all_pending_approvals if a.entity_type == "bill"]

    pending_approvals = 0
    if expense_ids:
        pending_approvals += (
            await db.scalar(
                select(func.count(Expense.id)).where(
                    Expense.id.in_(expense_ids), Expense.project_id.in_(project_ids)
                )
            )
        ) or 0
    if material_ids:
        pending_approvals += (
            await db.scalar(
                select(func.count(Material.id)).where(
                    Material.id.in_(material_ids), Material.project_id.in_(project_ids)
                )
            )
        ) or 0
    if bill_ids:
        pending_approvals += (
            await db.scalar(
                select(func.count(VendorBill.id)).where(
                    VendorBill.id.in_(bill_ids), VendorBill.project_id.in_(project_ids)
                )
            )
        ) or 0

    # Issues
    open_issues = await db.scalar(
        select(func.count(Issue.id)).where(
            Issue.project_id.in_(project_ids), Issue.status == IssueStatus.OPEN.value
        )
    )

    # Budget Utilized
    total_budget = (
        await db.scalar(
            select(func.sum(BOQ.total_cost)).where(
                BOQ.project_id.in_(project_ids), BOQ.is_latest == True
            )
        )
        or 0
    )
    total_expense = (
        await db.scalar(
            select(func.sum(Expense.amount)).where(Expense.project_id.in_(project_ids))
        )
        or 0
    )

    budget_utilized_percent = 0.0
    if total_budget > 0:
        budget_utilized_percent = float(total_expense / total_budget) * 100.0

    # Today's Activities
    today_dt = get_naive_local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    todays_activities = (
        await db.scalar(
            select(func.count(ActivityLog.id)).where(
                ActivityLog.created_at >= today_dt,
                ActivityLog.entity == "project",
                ActivityLog.entity_id.in_(project_ids),
            )
        )
        or 0
    )

    return PMSummaryOut(
        total_projects=total,
        active_projects=active,
        completed_projects=completed,
        delayed_projects=delayed,
        pending_approvals=pending_approvals or 0,
        open_issues=open_issues or 0,
        budget_utilized_percent=round(budget_utilized_percent, 2),
        todays_activities=todays_activities,
    )


# =========================================
# REFRESH DASHBOARD
# =========================================
@router.post("/refresh")
async def refresh_dashboard(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    redis=Depends(d.get_request_redis),
):
    await r.bump_cache_version(redis, VERSION_KEY)
    return {"message": "Dashboard cache invalidated successfully"}


# =========================================
# EXPORT API (CSV)
# =========================================
import csv
import io


@router.get("/accountant/export")
async def export_dashboard(
    current_user: User = Depends(d.require_roles(DASHBOARD_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
):
    dash_out = await accountant_dashboard(current_user=current_user, db=db, redis=redis)

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    # A) Financial Summary
    writer.writerow(["=== FINANCIAL SUMMARY ==="])
    kpi = dash_out.kpi_cards
    writer.writerow(["Cash", "Bank", "Receivable", "Payable", "GST Due", "Profit"])
    writer.writerow(
        [
            kpi.cash_balance,
            kpi.bank_balance,
            kpi.receivables,
            kpi.payables,
            kpi.gst_due,
            kpi.net_profit,
        ]
    )
    writer.writerow([])

    # B) Revenue vs Expense
    writer.writerow(["=== REVENUE VS EXPENSE ==="])
    writer.writerow(["Month", "Revenue", "Expense"])
    for row in dash_out.revenue_vs_expense:
        writer.writerow([row.month, row.revenue, row.expense])
    writer.writerow([])

    # C) Cash Flow
    writer.writerow(["=== CASH FLOW ==="])
    writer.writerow(["Month", "Inflow", "Outflow"])
    for row in dash_out.cash_flow:
        writer.writerow([row.month, row.inflow, row.outflow])
    writer.writerow([])

    # D) Aging Reports
    writer.writerow(["=== RECEIVABLE AGING ==="])
    writer.writerow(["Period", "Amount", "% of Total"])
    for row in dash_out.receivable_aging:
        writer.writerow([row.period, row.amount, f"{row.percentage}%"])
    writer.writerow([])

    writer.writerow(["=== PAYABLE AGING ==="])
    writer.writerow(["Period", "Amount", "% of Total"])
    for row in dash_out.payable_aging:
        writer.writerow([row.period, row.amount, f"{row.percentage}%"])
    writer.writerow([])

    # E) Project Cost Summary
    writer.writerow(["=== PROJECT COST SUMMARY ==="])
    writer.writerow(["Project", "Budget", "Expense", "Remaining"])
    for row in dash_out.project_cost_summary:
        writer.writerow([row.project_name, row.budgeted, row.spent, row.remaining])
    writer.writerow([])

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=accountant_dashboard.csv"
        },
    )


@router.get("/admin/projects/export/csv")
async def export_master_projects_csv(
    current_user: User = Depends(d.require_roles([UserRole.ADMIN.value])),
    db: AsyncSession = Depends(get_db_session),
):
    projects_query = await db.execute(select(m.Project))
    projects = projects_query.scalars().all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Site/Project", "Dates", "Total Progress", "Health"])

    for p in projects:
        avg_progress = (
            await db.scalar(
                select(func.avg(m.Task.completion_percentage)).where(
                    m.Task.project_id == p.id
                )
            )
            or 0
        )
        dates_str = f"{p.start_date or 'N/A'} - {p.end_date or 'N/A'}"
        health = str(p.status.value) if hasattr(p.status, "value") else str(p.status)
        writer.writerow(
            [p.project_name, dates_str, f"{round(float(avg_progress), 2)}%", health]
        )

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=master_projects.csv"},
    )


@router.get("/admin/projects/export/pdf")
async def export_master_projects_pdf(
    current_user: User = Depends(d.require_roles([UserRole.ADMIN.value])),
    db: AsyncSession = Depends(get_db_session),
):
    projects_query = await db.execute(select(m.Project))
    projects = projects_query.scalars().all()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()

    elements = []
    elements.append(Paragraph("Master Projects Overview", styles["Title"]))

    data = [["Site/Project", "Dates", "Total Progress", "Health"]]

    for p in projects:
        avg_progress = (
            await db.scalar(
                select(func.avg(m.Task.completion_percentage)).where(
                    m.Task.project_id == p.id
                )
            )
            or 0
        )
        dates_str = f"{p.start_date or 'N/A'} - {p.end_date or 'N/A'}"
        health = str(p.status.value) if hasattr(p.status, "value") else str(p.status)
        data.append(
            [p.project_name, dates_str, f"{round(float(avg_progress), 2)}%", health]
        )

    table = Table(data)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=master_projects.pdf"},
    )


# ============================================
# client_dashboard
# ============================================


from fastapi import HTTPException
from sqlalchemy import select, func, case
from app.schemas.dashboard import (
    ClientDashboardV2Out,
    ClientProjectInfo,
    ClientDashboardOverview,
    ClientBudgetAnalysis,
    ClientTimelineInfo,
    ClientScheduleInfo,
    ClientRiskInfo,
    ClientKPIs,
    ClientMilestoneSummaryInfo,
    ClientTaskSummaryInfo,
    ClientMilestoneItem,
    ClientExpenseItem,
    ClientExpenseTrendItem,
    ClientUpcomingMilestoneItem,
)

# Computed once at import time instead of on every request — PRIORITY_MAP
# is a static mapping, no need to rebuild this list on every call.
_HIGH_PRIORITY_KEYS = [
    key for key, value in PRIORITY_MAP.items() if value == TaskPriority.HIGH
]


@router.get("/client", response_model=ClientDashboardV2Out)
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
        raise HTTPException(status_code=403, detail="Access denied")

    async def logic():
        project_ids = await get_user_project_ids(db, current_user)

        # ========================
        # PROJECT
        # ------------------------
        # budget_amount is fetched here too now (was a separate query
        # further down) — one fewer SQL round trip per request.
        # ========================
        project_row = await db.execute(
            select(
                m.Project.id,
                m.Project.project_name,
                m.Project.status,
                m.Project.start_date,
                m.Project.end_date,
                m.Project.budget_amount,
            ).where(m.Project.id == project_id, m.Project.id.in_(project_ids))
        )
        project_row = project_row.first()

        if not project_row:
            raise HTTPException(status_code=404, detail="No project found")

        (
            db_project_id,
            project_name,
            status,
            start_date,
            end_date,
            budget_amount,
        ) = project_row

        today = get_naive_local_now().date()
        status_value = status.value if hasattr(status, "value") else str(status)

        # ========================
        # PERFORMANCE NOTE
        # ------------------------
        # AsyncSession does not support concurrent operations on a single
        # shared session (asyncio.gather over db.execute() calls on the
        # same `db` will intermittently raise InvalidRequestError / cursor
        # corruption). Round trips are minimized instead by folding many
        # separate COUNT()/AVG() calls into a small number of single-pass
        # conditional-aggregation (CASE/SUM) queries below.
        # ========================

        # ---- TASK AGGREGATES (1 query) ----
        # Includes overdue_high_priority_tasks: used only for risk scoring
        # (see RISK SCORE below) — riding on this same aggregate query
        # instead of firing an extra one.
        task_stats_row = await db.execute(
            select(
                func.count(m.Task.id),
                func.sum(
                    case((m.Task.status == TaskStatus.COMPLETED.value, 1), else_=0)
                ),
                func.avg(m.Task.completion_percentage),
                func.sum(
                    case(
                        (
                            (m.Task.end_date < today)
                            & (m.Task.status != TaskStatus.COMPLETED.value),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(case((m.Task.priority.in_(_HIGH_PRIORITY_KEYS), 1), else_=0)),
                func.sum(
                    case(
                        (
                            m.Task.priority.in_(_HIGH_PRIORITY_KEYS)
                            & (m.Task.end_date < today)
                            & (m.Task.status != TaskStatus.COMPLETED.value),
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).where(m.Task.project_id == db_project_id)
        )
        (
            tasks_total,
            tasks_completed,
            avg_task_progress,
            overdue_tasks,
            high_priority_tasks,
            overdue_high_priority_tasks,
        ) = task_stats_row.one()

        tasks_total = tasks_total or 0
        tasks_completed = tasks_completed or 0
        tasks_pending = tasks_total - tasks_completed
        overdue_tasks = overdue_tasks or 0
        high_priority_tasks = high_priority_tasks or 0
        overdue_high_priority_tasks = overdue_high_priority_tasks or 0
        task_completion_percent = (
            round((tasks_completed / tasks_total) * 100, 2) if tasks_total else 0.0
        )

        # ---- MILESTONE AGGREGATES (1 query) ----
        # NOTE: Milestone.completion_percentage is a Python @property
        # (derived from loaded child tasks), not a DB column, so it can't
        # be used in a SQL aggregate. completion_percent here is a
        # count-based ratio (completed / total).
        milestone_stats_row = await db.execute(
            select(
                func.count(m.Milestone.id),
                func.sum(
                    case(
                        (m.Milestone.status == MilestoneStatus.COMPLETED.value, 1),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (
                            (m.Milestone.end_date < today)
                            & (m.Milestone.status != MilestoneStatus.COMPLETED.value),
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).where(m.Milestone.project_id == db_project_id)
        )
        milestones_total, milestones_completed, overdue_milestones = (
            milestone_stats_row.one()
        )

        milestones_total = milestones_total or 0
        milestones_completed = milestones_completed or 0
        milestones_pending = milestones_total - milestones_completed
        overdue_milestones = overdue_milestones or 0
        milestone_completion_percent = (
            round((milestones_completed / milestones_total) * 100, 2)
            if milestones_total
            else 0.0
        )

        # ---- BUDGET / EXPENSE ----
        # budget_amount already fetched in the PROJECT query above — the
        # standalone `select(m.Project.budget_amount)` query is gone.

        total_expense = await db.scalar(
            select(func.sum(Expense.amount)).where(Expense.project_id == db_project_id)
        )

        budget_val = float(budget_amount or 0)
        expense_val = float(total_expense or 0)

        # Remaining Budget (single source of truth — computed once,
        # reused everywhere below instead of being re-derived/re-rounded
        # for every schema section).
        remaining_budget = max(budget_val - expense_val, 0)
        remaining_budget_rounded = round(remaining_budget, 2)

        # Budget Utilization
        budget_used_percent = (
            round((expense_val / budget_val) * 100, 2) if budget_val else 0.0
        )

        budget_variance_percent = (
            round((remaining_budget / budget_val) * 100, 2) if budget_val else 0.0
        )

        remaining_percent = (
            round((remaining_budget / budget_val) * 100, 2) if budget_val else 0.0
        )

        # Budget Status
        if expense_val > budget_val:
            budget_status = "Over Budget"
        elif budget_used_percent >= 90:
            budget_status = "Critical"
        elif budget_used_percent >= 75:
            budget_status = "Warning"
        elif budget_used_percent >= 40:
            budget_status = "Normal"
        else:
            budget_status = "Healthy"

        # ---- TIMELINE / SCHEDULE ----
        project_duration = 0
        elapsed_days = 0
        remaining_days = 0
        timeline_progress = 0.0

        if start_date and end_date:
            project_duration = max((end_date - start_date).days, 0)
            elapsed_days = max((today - start_date).days, 0)
            remaining_days = max((end_date - today).days, 0)
            if project_duration > 0:
                timeline_progress = round(
                    min(100.0, (elapsed_days / project_duration) * 100), 2
                )
        elif end_date:
            remaining_days = max((end_date - today).days, 0)

        actual_progress = round(float(avg_task_progress or 0), 2)
        expected_progress = timeline_progress
        schedule_variance = round(actual_progress - expected_progress, 2)

        if schedule_variance >= 5:
            schedule_status = "Ahead of Schedule"
        elif schedule_variance >= -5:
            schedule_status = "On Track"
        else:
            schedule_status = "Behind Schedule"

        # ---- PROJECT HEALTH ----
        if (
            schedule_variance >= -5
            and budget_used_percent <= 90
            and overdue_tasks == 0
            and overdue_milestones == 0
        ):
            project_health = "Good"

        elif (
            schedule_variance >= -15
            and budget_used_percent <= 110
            and overdue_tasks <= 5
            and overdue_milestones <= 1
        ):
            # 2+ overdue milestones is a strong enough client-facing red
            # flag on its own to block "At Risk" and fall through to
            # "Critical", even when schedule/budget numbers look tolerable.
            project_health = "At Risk"

        else:
            project_health = "Critical"

        # ---- RISK SCORE ----
        risk_score = 0
        if budget_used_percent > 100:
            risk_score += 30
        elif budget_used_percent > 90:
            risk_score += 15

        if schedule_variance < -15:
            risk_score += 30
        elif schedule_variance < -5:
            risk_score += 15

        risk_score += min(overdue_tasks * 3, 20)
        risk_score += min(overdue_milestones * 5, 20)
        # Weighted by *overdue* high-priority tasks, not the raw
        # high-priority count — a large, on-track project can have many
        # high-priority tasks without being risky; what matters is
        # whether important work is actually late.
        risk_score += min(overdue_high_priority_tasks * 4, 20)
        risk_score = min(risk_score, 100)

        if risk_score < 30:
            risk_level = "Low"
        elif risk_score < 60:
            risk_level = "Medium"
        else:
            risk_level = "High"

        # ---- RECENT MILESTONES (latest 5) ----
        # No created_at column exists on Milestone, so "recent" is
        # approximated by primary key descending (insertion order).
        recent_milestones_result = await db.execute(
            select(m.Milestone)
            .where(m.Milestone.project_id == db_project_id)
            .order_by(m.Milestone.id.desc())
            .limit(5)
        )
        recent_milestones = [
            ClientMilestoneItem(
                id=ms.id,
                title=ms.title,
                status=(
                    ms.status.value if hasattr(ms.status, "value") else str(ms.status)
                ),
                start_date=ms.start_date,
                end_date=ms.end_date,
                completion_percentage=round(ms.completion_percentage, 2),
            )
            for ms in recent_milestones_result.scalars().all()
        ]

        # ---- RECENT EXPENSES (latest 5) ----
        recent_expenses_result = await db.execute(
            select(Expense)
            .where(Expense.project_id == db_project_id)
            .order_by(Expense.expense_date.desc(), Expense.id.desc())
            .limit(5)
        )
        recent_expenses = [
            ClientExpenseItem(
                id=e.id,
                category=e.category,
                description=e.description,
                amount=float(e.amount),
                expense_date=e.expense_date,
                payment_mode=e.payment_mode,
            )
            for e in recent_expenses_result.scalars().all()
        ]

        # ---- EXPENSE TREND (monthly, last 6 months) ----
        six_months_ago = (today.replace(day=1)) - relativedelta(months=5)
        expense_trend_result = await db.execute(
            select(
                func.date_format(Expense.expense_date, "%Y-%m").label("month"),
                func.sum(Expense.amount),
            )
            .where(
                Expense.project_id == db_project_id,
                Expense.expense_date >= six_months_ago,
            )
            .group_by("month")
            .order_by("month")
        )
        expense_trend = [
            ClientExpenseTrendItem(month=row[0], total_amount=float(row[1] or 0))
            for row in expense_trend_result.all()
        ]

        # ---- UPCOMING MILESTONES ----
        upcoming_milestones_result = await db.execute(
            select(m.Milestone)
            .where(
                m.Milestone.project_id == db_project_id,
                m.Milestone.status != MilestoneStatus.COMPLETED.value,
                m.Milestone.end_date >= today,
            )
            .order_by(m.Milestone.end_date.asc())
            .limit(5)
        )
        upcoming_milestones = [
            ClientUpcomingMilestoneItem(
                id=ms.id,
                title=ms.title,
                status=(
                    ms.status.value if hasattr(ms.status, "value") else str(ms.status)
                ),
                end_date=ms.end_date,
                days_remaining=(ms.end_date - today).days if ms.end_date else None,
            )
            for ms in upcoming_milestones_result.scalars().all()
        ]

        # ---- EXECUTIVE SUMMARY ----
        executive_summary = (
            f"Project {project_name} is {actual_progress:.2f}% complete. "
            f"Budget utilization is {budget_used_percent:.2f}%. "
            f"Project health is {project_health}. "
            f"There are {tasks_pending} pending tasks and "
            f"{milestones_pending} pending milestones."
        )

        spent_percent = (
            round((expense_val / budget_val) * 100, 2) if budget_val else 0.0
        )

        if end_date:
            days_remaining = (end_date - get_naive_local_now().date()).days

        # ========================
        # RESPONSE
        # ========================
        return ClientDashboardV2Out(
            project=ClientProjectInfo(
                project_id=db_project_id,
                project_name=project_name,
                status=status_value,
                start_date=start_date,
                end_date=end_date,
                days_remaining=remaining_days,
            ),
            overview=ClientDashboardOverview(
                progress_percent=actual_progress,
                project_health=project_health,
                budget_total=budget_val,
                total_expense=expense_val,
                remaining_budget=remaining_budget_rounded,
                budget_used_percent=budget_used_percent,
                budget_status=budget_status,
            ),
            budget_analysis=ClientBudgetAnalysis(
                budget=budget_val,
                spent=expense_val,
                remaining=remaining_budget_rounded,
                spent_percent=spent_percent,
                remaining_percent=remaining_percent,
                variance_percent=budget_variance_percent,
            ),
            timeline=ClientTimelineInfo(
                project_duration=project_duration,
                elapsed_days=elapsed_days,
                remaining_days=remaining_days,
                timeline_progress=timeline_progress,
            ),
            schedule=ClientScheduleInfo(
                actual_progress=actual_progress,
                expected_progress=expected_progress,
                variance=schedule_variance,
                status=schedule_status,
            ),
            risk=ClientRiskInfo(score=risk_score, level=risk_level),
            kpis=ClientKPIs(
                progress=actual_progress,
                budget_used=budget_used_percent,
                remaining_budget=remaining_budget_rounded,
                overdue_tasks=overdue_tasks,
                overdue_milestones=overdue_milestones,
                high_priority_tasks=high_priority_tasks,
            ),
            milestone_summary=ClientMilestoneSummaryInfo(
                total=milestones_total,
                completed=milestones_completed,
                pending=milestones_pending,
                completion_percent=milestone_completion_percent,
            ),
            task_summary=ClientTaskSummaryInfo(
                total=tasks_total,
                completed=tasks_completed,
                pending=tasks_pending,
                completion_percent=task_completion_percent,
            ),
            recent_milestones=recent_milestones,
            recent_expenses=recent_expenses,
            expense_trend=expense_trend,
            upcoming_milestones=upcoming_milestones,
            executive_summary=executive_summary,
        ).dict()

    version = await r.get_cache_version(redis, VERSION_KEY)

    return await cache_get_set(
        redis,
        f"client_dashboard_v2:{current_user.id}:{project_id}",
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

    # X = time index (0₹,2,...)
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
# @router.get("/engineer/details", response_model=EnhancedDashboardOut)
async def site_engineer_dashboard(
    current_user: User = Depends(d.require_roles([UserRole.SITE_ENGINEER.value])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(d.get_request_redis),
    project_id: Optional[int] = None,
):
    project_ids = await get_user_project_ids(db, current_user)

    if project_id is not None:
        if project_id not in project_ids:
            raise NotFoundError("Project not found or access denied")
        filter_cond = m.DailySiteReport.project_id == project_id
        att_filter_cond = UserAttendance.project_id == project_id
        issue_filter_cond = Issue.project_id == project_id
        task_filter_cond = m.Task.project_id == project_id
        mat_filter_cond = Material.project_id == project_id
        wa_filter_cond = WorkActivity.project_id == project_id
        ms_filter_cond = Milestone.project_id == project_id
        exp_filter_cond = Expense.project_id == project_id

        project = await db.get(m.Project, project_id)
        if not project:
            raise NotFoundError("Project not found")
        project_name = project.project_name
        status = str(project.status)

        # Planned progress for single project
        planned_progress = 0
        today = get_naive_local_now().date()
        if project.start_date and project.end_date:
            total_days = (project.end_date - project.start_date).days
            elapsed_days = (today - project.start_date).days
            if total_days > 0:
                planned_progress = max(0, min(100, (elapsed_days / total_days) * 100))
    else:
        if not project_ids:
            # User has no projects
            return EnhancedDashboardOut(
                project_id=0,
                project_name="All Assigned Projects",
                status="N/A",
                progress=0.0,
                planned_progress=0.0,
                variance=0.0,
                vitals=DashboardVitals(
                    total_labour_today=0,
                    active_activities=0,
                    open_issues=IssueStats(total=0, high_priority=0),
                    material_stock_status=[],
                ),
                today_work_summary=[],
                discipline_progress=[],
                timeline=[],
                recent_expenses=[],
                weather=None,
            )
        filter_cond = m.DailySiteReport.project_id.in_(project_ids)
        att_filter_cond = UserAttendance.project_id.in_(project_ids)
        issue_filter_cond = Issue.project_id.in_(project_ids)
        task_filter_cond = m.Task.project_id.in_(project_ids)
        mat_filter_cond = Material.project_id.in_(project_ids)
        wa_filter_cond = WorkActivity.project_id.in_(project_ids)
        ms_filter_cond = Milestone.project_id.in_(project_ids)
        exp_filter_cond = Expense.project_id.in_(project_ids)

        project_name = "All Assigned Projects"
        status = "Multiple"
        project_id = 0

        # Calculate avg planned progress across all assigned projects
        projects_query = await db.execute(
            select(m.Project).where(m.Project.id.in_(project_ids))
        )
        projects = projects_query.scalars().all()
        today = get_naive_local_now().date()
        total_planned = 0
        valid_projs = 0
        for p in projects:
            if p.start_date and p.end_date:
                t_days = (p.end_date - p.start_date).days
                e_days = (today - p.start_date).days
                if t_days > 0:
                    total_planned += max(0, min(100, (e_days / t_days) * 100))
                    valid_projs += 1
        planned_progress = total_planned / valid_projs if valid_projs > 0 else 0

    # 1. Labor Today
    labor_stats = await db.execute(
        select(func.count(UserAttendance.id)).where(
            att_filter_cond,
            UserAttendance.attendance_date == today,
            UserAttendance.in_time.is_not(None),
        )
    )
    total_labour = labor_stats.scalar() or 0

    # 2. Material Stock Status
    material_stats = await db.execute(
        select(
            Material.category, Material.remaining_stock, Material.minimum_stock_level
        ).where(mat_filter_cond, Material.is_deleted == False)
    )
    materials = []
    for cat, stock, min_level in material_stats.all():
        m_status = "OK"
        if stock <= 0:
            m_status = "Out of Stock"
        elif stock < min_level:
            m_status = "Low"
        materials.append(MaterialStockStatus(category=cat, status=m_status))

    # 3. Open Issues
    issue_stats_query = await db.execute(
        select(
            func.count(Issue.id),
            func.sum(case((Issue.priority == IssuePriority.HIGH.value, 1), else_=0)),
        ).where(issue_filter_cond, Issue.status == IssueStatus.OPEN.value)
    )
    total_issues, high_priority_issues = issue_stats_query.one()

    # 4. Today's Work Summary
    work_summary_query = await db.execute(
        select(WorkActivity.activity_name, WorkActivity.status)
        .join(DailyProgressEntry, WorkActivity.id == DailyProgressEntry.activity_id)
        .where(
            wa_filter_cond,
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
        .where(wa_filter_cond)
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
        select(Milestone).where(ms_filter_cond).order_by(Milestone.start_date)
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
        .where(exp_filter_cond)
        .order_by(Expense.expense_date.desc())
        .limit(5)
    )
    recent_expenses = [
        RecentExpense(
            date=e.expense_date,
            type="Expense",
            category=e.category,
            note=e.description,
            amount=float(e.amount),
        )
        for e in expenses_query.scalars().all()
    ]

    # 8. Overall Progress & Planned
    progress = await db.scalar(
        select(func.avg(m.Task.completion_percentage)).where(task_filter_cond)
    )

    variance = float(progress or 0) - planned_progress

    # 9. Vitals Aggregation
    vitals = DashboardVitals(
        total_labour_today=int(total_labour or 0),
        active_activities=len(today_work),
        open_issues=IssueStats(
            total=int(total_issues or 0), high_priority=int(high_priority_issues or 0)
        ),
        material_stock_status=materials,
    )

    return EnhancedDashboardOut(
        project_id=project_id,
        project_name=project_name,
        status=status,
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


def success_response(message, data=None):

    return {"success": True, "message": message, "data": data}


# =========================================
# LABOUR DASHBOARD
# =========================================

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import (
    select,
    func,
    case,
    desc,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# from app.core.time import get_naive_local_now  # adjust import path to actual location
from app.db.session import get_db_session
from app.models.user import User
from app.models.labour import Labour, LabourProject, LabourPayroll
from app.models.user import UserAttendance
from app.models.project import Project, Task, TaskAssignment
from app.schemas.dashboard import (
    LabourDashboardOut,
    LabourDashboardResponse,
    LabourProfile,
    LabourOverview,
    LabourAttendanceSummary,
    LabourPayment,
    LabourTaskItem,
    LabourActivityItem,
    LabourDetails,
)
from app.models.user import UserRole
from app.core.enums import (
    TaskStatus,
    TaskPriority,
    PRIORITY_MAP,
    AttendanceStatus,
    PayrollStatus,
)
from app.models.settings import UserSettings
from app.models.contractor import Contractor, ContractorProject

RECENT_TASKS_LIMIT = 5
RECENT_ACTIVITY_TASK_LIMIT = 3
STREAK_LOOKBACK_DAYS = 45  # enough to cover a running streak across month boundaries


def _scope_to_project(query, column, project):
    """Apply project filter only when a project context exists."""
    return query.where(column == project.id) if project else query


@router.get(
    "/labour",
    response_model=LabourDashboardResponse,
)
async def labour_dashboard(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(d.get_current_active_user),
):

    if current_user.role != UserRole.LABOUR.value:
        raise HTTPException(
            status_code=403,
            detail="Not authorized",
        )

    today = date.today()
    current_month = today.month
    current_year = today.year
    month_start = today.replace(day=1)

    labour = await db.scalar(
        select(Labour)
        .options(
            selectinload(Labour.user),
            selectinload(Labour.contractor),
            selectinload(Labour.labour_type),
        )
        .where(Labour.user_id == current_user.id)
    )

    if labour is None:
        raise HTTPException(
            status_code=404,
            detail="Labour profile not found",
        )

    user_settings = await db.scalar(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )

    # =========================================
    # PROJECT RESOLUTION (joined, single query per branch)
    # =========================================

    project = None

    if user_settings and user_settings.default_project_id:
        project = await db.scalar(
            select(Project)
            .join(LabourProject, LabourProject.project_id == Project.id)
            .where(
                LabourProject.labour_id == labour.id,
                LabourProject.project_id == user_settings.default_project_id,
            )
        )

    if project is None:
        project = await db.scalar(
            select(Project)
            .join(LabourProject, LabourProject.project_id == Project.id)
            .where(LabourProject.labour_id == labour.id)
            .order_by(LabourProject.assigned_date.desc(), LabourProject.id.desc())
            .limit(1)
        )

    project_count = await db.scalar(
        select(func.count(func.distinct(LabourProject.project_id))).where(
            LabourProject.labour_id == labour.id
        )
    )
    is_multi_project = (project_count or 0) > 1

    contractors = []
    contractor_name = None

    if project:
        contractors = (
            (
                await db.execute(
                    select(Contractor)
                    .distinct()
                    .join(
                        ContractorProject,
                        ContractorProject.contractor_id == Contractor.id,
                    )
                    .where(ContractorProject.project_id == project.id)
                    .order_by(Contractor.name.asc())
                )
            )
            .scalars()
            .all()
        )

        if contractors:
            contractor_name = ", ".join(contractor.name for contractor in contractors)

    # Fallback to the labourer's own contractor if the project has none mapped
    if contractor_name is None and labour.contractor:
        contractor_name = labour.contractor.name

    profile = LabourProfile(
        user_name=current_user.full_name,
        profile_image=current_user.profile_image,
        project_name=(project.project_name if project else None),
        contractor_name=contractor_name,
        labour_type=labour.labour_type_name,
        skill_category=labour.skill_category,
        check_in_status="NOT CHECKED IN",
        is_checked_in=False,
        is_multi_project=is_multi_project,
    )

    # =========================================
    # ATTENDANCE (single windowed fetch on UserAttendance, keyed by labour_id)
    # =========================================

    streak_lookback_start = today - timedelta(days=STREAK_LOOKBACK_DAYS)

    attendance_query = select(UserAttendance).where(
        UserAttendance.user_id == current_user.id,
        UserAttendance.attendance_date <= today,
        UserAttendance.attendance_date >= streak_lookback_start,
    )

    attendance_query = _scope_to_project(
        attendance_query, UserAttendance.project_id, project
    )
    attendance_query = attendance_query.order_by(UserAttendance.attendance_date.desc())

    attendance_rows = (await db.execute(attendance_query)).scalars().all()

    today_attendance = next(
        (row for row in attendance_rows if row.attendance_date == today),
        None,
    )

    if today_attendance:
        if today_attendance.out_time:
            profile.check_in_status = "CHECKED OUT"
            profile.is_checked_in = False
        elif today_attendance.in_time:
            profile.check_in_status = "CHECKED IN"
            profile.is_checked_in = True

    month_rows = [
        row
        for row in attendance_rows
        if row.attendance_date.month == current_month
        and row.attendance_date.year == current_year
    ]

    total_days = len(month_rows)
    present_days = sum(1 for r in month_rows if r.status == AttendanceStatus.PRESENT)
    absent_days = sum(1 for r in month_rows if r.status == AttendanceStatus.ABSENT)
    half_days = sum(1 for r in month_rows if r.status == AttendanceStatus.HALF_DAY)

    attendance_percentage = (
        round((present_days / total_days) * 100, 2) if total_days else 0
    )

    today_hours = (
        float(today_attendance.working_hours or 0) if today_attendance else 0.0
    )
    today_ot = float(today_attendance.overtime_hours or 0) if today_attendance else 0.0

    # Attendance streak — rows already sorted desc by date
    attendance_streak = 0
    for row in attendance_rows:
        if row.status == AttendanceStatus.PRESENT:
            attendance_streak += 1
        else:
            break

    # Weekly earnings derived from the same fetched window (current ISO week)
    current_week = today.isocalendar()[1]
    weekly_present = sum(
        1
        for r in attendance_rows
        if r.status == AttendanceStatus.PRESENT
        and r.attendance_date.isocalendar()[1] == current_week
        and r.attendance_date.year == today.year
    )
    weekly_earnings = weekly_present * float(labour.effective_daily_wage)

    # =========================================
    # TASKS — conditional aggregation + limited recent list
    # =========================================

    task_filters = [Task.assignments.any(TaskAssignment.user_id == current_user.id)]
    if project:
        task_filters.append(Task.project_id == project.id)

    task_counts = (
        await db.execute(
            select(
                func.count(Task.id).label("total"),
                func.sum(case((Task.status == TaskStatus.COMPLETED, 1), else_=0)).label(
                    "completed"
                ),
            ).where(*task_filters)
        )
    ).one()

    total_tasks = task_counts.total or 0
    completed_tasks = task_counts.completed or 0
    pending_tasks = total_tasks - completed_tasks

    recent_task_rows = (
        (
            await db.execute(
                select(Task)
                .options(selectinload(Task.project))
                .where(*task_filters)
                .order_by(desc(Task.start_date))
                .limit(RECENT_TASKS_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    recent_tasks = [
        LabourTaskItem(
            task_id=task.id,
            title=task.title,
            status=task.status.value if task.status else "-",
            priority=PRIORITY_MAP.get(task.priority, TaskPriority.LOW).value,
            start_date=task.start_date,
            end_date=task.end_date,
            progress=float(task.completion_percentage or 0),
            project_name=(task.project.project_name if task.project else None),
        )
        for task in recent_task_rows
    ]

    attendance_summary = LabourAttendanceSummary(
        present_days=present_days,
        absent_days=absent_days,
        half_days=half_days,
        total_days=total_days,
        attendance_percentage=attendance_percentage,
        attendance_streak=attendance_streak,
    )

    # =========================================
    # PAYROLL
    # =========================================

    payroll_query = select(LabourPayroll).where(
        LabourPayroll.labour_id == labour.id,
        LabourPayroll.month == current_month,
        LabourPayroll.year == current_year,
    )
    payroll_query = _scope_to_project(payroll_query, LabourPayroll.project_id, project)

    payroll = await db.scalar(payroll_query)

    paid_amount = 0.0
    pending_amount = 0.0
    this_month_earnings = 0.0
    payment_status = None

    if payroll:
        paid_amount = float(payroll.paid_amount or 0)
        pending_amount = float(payroll.remaining_amount or 0)
        this_month_earnings = float(payroll.total_wage or 0)
        payment_status = payroll.status.value if payroll and payroll.status else None

    # LabourPayroll has no due_date column today, so "overdue" is derived
    # from status + pending balance rather than a date comparison.
    is_overdue = bool(
        payroll and payroll.status == PayrollStatus.PENDING and pending_amount > 0
    )

    overview = LabourOverview(
        today_hours=today_hours,
        overtime_hours=today_ot,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        pending_tasks=pending_tasks,
        weekly_earnings=weekly_earnings,
        this_month_earnings=this_month_earnings,
    )

    labour_details = LabourDetails(
        site_name=project.project_name if project else None,
        site_address=project.site_address if project else None,
        daily_wage=float(labour.effective_daily_wage),
        overtime_rate=float(labour.effective_ot_rate),
    )

    payment = LabourPayment(
        total_amount=paid_amount + pending_amount,
        paid_amount=paid_amount,
        pending_amount=pending_amount,
        payment_status=payment_status,
        is_overdue=is_overdue,
    )

    # =========================================
    # RECENT ACTIVITY
    # =========================================

    recent_activity = []

    if today_attendance:
        recent_activity.append(
            LabourActivityItem(
                title="Checked In",
                description="Attendance marked",
                time=(
                    today_attendance.in_time.strftime("%d %b %Y %I:%M %p")
                    if today_attendance.in_time
                    else "-"
                ),
            )
        )

    for task in recent_task_rows[:RECENT_ACTIVITY_TASK_LIMIT]:
        recent_activity.append(
            LabourActivityItem(
                title="Task Assigned",
                description=task.title,
                time=(task.start_date.strftime("%d %b %Y") if task.start_date else "-"),
            )
        )

    dashboard = LabourDashboardOut(
        profile=profile,
        overview=overview,
        attendance_summary=attendance_summary,
        labour_details=labour_details,
        payment=payment,
        recent_tasks=recent_tasks,
        recent_activity=recent_activity,
    )

    return LabourDashboardResponse(
        success=True,
        message="Labour dashboard loaded successfully",
        data=dashboard,
    )


# =============================================
# get_labour_payments
# ============================================


@router.get("/labour/payments", response_model=dict)
async def get_labour_payments(
    month: Optional[int] = None,
    year: Optional[int] = None,
    time_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    labour_res = await db.execute(
        select(Labour).where(Labour.user_id == current_user.id)
    )
    labour = labour_res.scalars().first()
    if not labour:
        raise HTTPException(status_code=404, detail="Labour profile not found for user")

    # Metrics
    metrics_stmt = select(
        func.sum(LabourPayroll.total_wage).label("total_payout"),
        func.sum(case((LabourPayroll.total_wage > 5000, 1), else_=0)).label(
            "high_payouts"
        ),
        func.sum(case((LabourPayroll.total_overtime_hours > 0, 1), else_=0)).label(
            "ot_intensive"
        ),
        func.sum(LabourPayroll.advance_adjusted).label("advance_adjusted"),
    ).where(LabourPayroll.labour_id == labour.id)

    metrics_stmt = apply_payroll_time_filter(metrics_stmt, time_filter, month, year)

    metrics_res = await db.execute(metrics_stmt)
    metrics_row = metrics_res.first()

    summary = {
        "total_payout": float(metrics_row.total_payout or 0),
        "high_payouts": int(metrics_row.high_payouts or 0),
        "ot_intensive": int(metrics_row.ot_intensive or 0),
        "advance_adjusted": float(metrics_row.advance_adjusted or 0),
    }

    # Records
    from sqlalchemy.orm import selectinload

    records_stmt = (
        select(LabourPayroll, Labour)
        .join(Labour, LabourPayroll.labour_id == Labour.id)
        .options(selectinload(Labour.labour_type))
        .where(LabourPayroll.labour_id == labour.id)
    )
    records_stmt = apply_payroll_time_filter(records_stmt, time_filter, month, year)

    count_stmt = select(func.count(LabourPayroll.id)).where(
        LabourPayroll.labour_id == labour.id
    )
    count_stmt = apply_payroll_time_filter(count_stmt, time_filter, month, year)

    count_res = await db.execute(count_stmt)
    total_records = count_res.scalar() or 0

    records_stmt = (
        records_stmt.order_by(desc(LabourPayroll.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    records_res = await db.execute(records_stmt)

    records_data = []
    for pr, lab in records_res.all():
        date_str = (
            pr.created_at.strftime("%d %b %Y")
            if pr.created_at
            else f"{pr.month}/{pr.year}"
        )
        skill_type = (
            lab.skill_category.value
            if hasattr(lab.skill_category, "value")
            else str(lab.skill_category)
        )

        records_data.append(
            {
                "id": f"{pr.id:03d}",
                "date": date_str,
                "skill_type": skill_type.capitalize(),
                "daily_wage": (
                    f"₹{lab.daily_wage_rate}"
                    if hasattr(lab, "daily_wage_rate") and lab.daily_wage_rate
                    else "₹800"
                ),
                "ot_hours": (
                    f"{int(pr.total_overtime_hours)}h"
                    if pr.total_overtime_hours
                    else "0h"
                ),
                "total_wage_earned": (
                    f"₹{pr.total_wage:,.0f}" if pr.total_wage else "₹0"
                ),
                "remarks": pr.remarks
                or (
                    "STANDARD PAYOUT"
                    if hasattr(pr.status, "value") and pr.status.value == "PAID"
                    else "PENDING"
                ),
                "status": (
                    pr.status.value if hasattr(pr.status, "value") else str(pr.status)
                ),
            }
        )

    return success_response(
        message="Labour payments fetched",
        data={
            "summary": summary,
            "records": records_data,
            "total_records": total_records,
            "page": page,
            "page_size": page_size,
            "total_pages": (
                (total_records + page_size - 1) // page_size if page_size > 0 else 0
            ),
        },
    )


# =========================================

from sqlalchemy import extract


def apply_payroll_time_filter(query, time_filter=None, month=None, year=None):
    today = get_naive_local_now().date()

    if time_filter == "today":
        query = query.where(LabourPayroll.payroll_date == today)

    elif time_filter == "this_week":
        start = today - timedelta(days=today.weekday())
        query = query.where(LabourPayroll.payroll_date >= start)

    elif time_filter == "this_month":
        query = query.where(
            extract("month", LabourPayroll.payroll_date) == today.month,
            extract("year", LabourPayroll.payroll_date) == today.year,
        )

    elif time_filter == "this_year":
        query = query.where(extract("year", LabourPayroll.payroll_date) == today.year)

    elif month and year:
        query = query.where(
            extract("month", LabourPayroll.payroll_date) == month,
            extract("year", LabourPayroll.payroll_date) == year,
        )

    return query


@router.get("/labour/payments/export")
async def export_labour_payments(
    month: Optional[int] = None,
    year: Optional[int] = None,
    time_filter: Optional[str] = None,
    export_format: str = Query("csv", description="csv or pdf"),
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    labour_res = await db.execute(
        select(Labour).where(Labour.user_id == current_user.id)
    )
    labour = labour_res.scalars().first()
    if not labour:
        raise HTTPException(status_code=404, detail="Labour profile not found for user")

    from sqlalchemy.orm import selectinload

    records_stmt = (
        select(LabourPayroll, Labour)
        .join(Labour, LabourPayroll.labour_id == Labour.id)
        .options(selectinload(Labour.labour_type))
        .where(LabourPayroll.labour_id == labour.id)
    )
    records_stmt = apply_payroll_time_filter(records_stmt, time_filter, month, year)
    records_stmt = records_stmt.order_by(desc(LabourPayroll.created_at))
    records_res = await db.execute(records_stmt)
    records = records_res.all()

    import io, csv

    if export_format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "ID",
                "Date",
                "Skill Type",
                "Daily Wage",
                "OT Hours",
                "Total Wage Earned",
                "Remarks",
                "Status",
            ]
        )
        for pr, lab in records:
            date_str = (
                pr.created_at.strftime("%d %b %Y")
                if pr.created_at
                else f"{pr.month}/{pr.year}"
            )
            skill_type = (
                lab.skill_category.value
                if hasattr(lab.skill_category, "value")
                else str(lab.skill_category)
            )
            writer.writerow(
                [
                    f"{pr.id:03d}",
                    date_str,
                    skill_type.capitalize(),
                    (
                        f"₹{lab.daily_wage_rate}"
                        if hasattr(lab, "daily_wage_rate") and lab.daily_wage_rate
                        else "₹800"
                    ),
                    (
                        f"{int(pr.total_overtime_hours)}h"
                        if pr.total_overtime_hours
                        else "0h"
                    ),
                    f"₹{pr.total_wage:,.0f}" if pr.total_wage else "₹0",
                    pr.remarks
                    or (
                        "STANDARD PAYOUT"
                        if hasattr(pr.status, "value") and pr.status.value == "PAID"
                        else "PENDING"
                    ),
                    pr.status.value if hasattr(pr.status, "value") else str(pr.status),
                ]
            )
        output.seek(0)
        from fastapi.responses import StreamingResponse

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=labour_payments.csv"
            },
        )
    elif export_format == "pdf":
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Table,
            TableStyle,
            Spacer,
        )
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from fastapi.responses import StreamingResponse

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=30,
            leftMargin=30,
            topMargin=30,
            bottomMargin=18,
        )
        elements = []
        styles = getSampleStyleSheet()

        elements.append(
            Paragraph(
                f"Labour Payments Report: {current_user.full_name}", styles["Heading1"]
            )
        )
        elements.append(Spacer(1, 0.2 * inch))

        data = [["ID", "Date", "Skill", "Wage", "OT", "Total", "Status"]]
        for pr, lab in records:
            date_str = (
                pr.created_at.strftime("%d %b %Y")
                if pr.created_at
                else f"{pr.month}/{pr.year}"
            )
            skill_type = (
                lab.skill_category.value
                if hasattr(lab.skill_category, "value")
                else str(lab.skill_category)
            )
            data.append(
                [
                    f"{pr.id:03d}",
                    date_str,
                    skill_type.capitalize()[:10],
                    (
                        f"₹{lab.daily_wage_rate}"
                        if hasattr(lab, "daily_wage_rate") and lab.daily_wage_rate
                        else "₹800"
                    ),
                    (
                        f"{int(pr.total_overtime_hours)}h"
                        if pr.total_overtime_hours
                        else "0h"
                    ),
                    f"₹{pr.total_wage:,.0f}" if pr.total_wage else "₹0",
                    pr.status.value if hasattr(pr.status, "value") else str(pr.status),
                ]
            )

        t = Table(data)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ]
            )
        )
        elements.append(t)
        doc.build(elements)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=labour_payments.pdf"
            },
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid export format")
