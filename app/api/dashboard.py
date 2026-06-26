from fastapi import APIRouter, Depends, HTTPException, Query, logger
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select, func, case, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
import io
from typing import Optional, List, Dict, Any

from app.core.enums import (
    InvoiceStatus,
    ProjectStatus,
    IssueStatus,
    IssuePriority,
    MilestoneStatus,
    SafetyChecklistStatus,
)
from app.db.session import get_db_session
from app.core import dependencies as d
from app.models.user import User, UserRole
from app.models import project as m
from app.models.expense import Expense
from app.models.invoice import Invoice, Transaction
from app.models.accountant import Account, GSTReturn, VendorBill, JournalLine
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
    CashFlow,
    ProjectCostSummaryItem,
    OutstandingReceivable,
    PendingPayable,
    UpcomingPayment,
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
from app.utils.timezone import get_naive_utc_now
from datetime import timezone
from app.models.labour import Labour, LabourProject, LabourAttendance, LabourPayroll
from app.core.enums import TaskStatus

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
                func.sum(
                    case(
                        (
                            (m.Project.status == ProjectStatus.ONGOING.value)
                            & (
                                (m.Project.end_date >= today)
                                | (m.Project.end_date == None)
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (m.Project.status == ProjectStatus.COMPLETED.value, 1), else_=0
                    )
                ),
                func.sum(
                    case(
                        (
                            (m.Project.status == ProjectStatus.ONGOING.value)
                            & (m.Project.end_date < today),
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
        )
        total, active, completed, delayed = project_stats.one()

        # 2. Financials
        revenue = await db.scalar(
            select(func.sum(Invoice.total_amount)).where(
                Invoice.status == InvoiceStatus.PAID.value
            )
        )
        expense = await db.scalar(select(func.sum(Expense.amount)))

        # 3. Vitals
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
                DailySiteReport.material_used != None,
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
        cash_balance_query = await db.scalar(
            select(func.sum(JournalLine.debit - JournalLine.credit))
            .join(Account, JournalLine.account_id == Account.id)
            .where(Account.name.ilike("%cash%"))
        )
        cash_balance = float(cash_balance_query or 0.0)

        bank_balance_query = await db.scalar(
            select(func.sum(JournalLine.debit - JournalLine.credit))
            .join(Account, JournalLine.account_id == Account.id)
            .where(Account.name.ilike("%bank%"))
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
            target_date = datetime.utcnow() - relativedelta(months=i)
            month_str = target_date.strftime("%b")

            month_start = target_date.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            month_end = (
                (month_start + relativedelta(months=1)) - timedelta(seconds=1)
                if i != 0
                else datetime.utcnow()
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

        # 3. Cash Flow
        cash_inflow_val = (
            await db.scalar(
                select(func.sum(Transaction.amount)).where(
                    Transaction.type == "receipt",
                    Transaction.created_at
                    >= (
                        datetime.utcnow().replace(
                            day=1, hour=0, minute=0, second=0, microsecond=0
                        )
                        - relativedelta(months=5)
                    ),
                )
            )
            or 0.0
        )

        cash_outflow_val = (
            await db.scalar(
                select(func.sum(Transaction.amount)).where(
                    Transaction.type == "payment",
                    Transaction.created_at
                    >= (
                        datetime.utcnow().replace(
                            day=1, hour=0, minute=0, second=0, microsecond=0
                        )
                        - relativedelta(months=5)
                    ),
                )
            )
            or 0.0
        )

        cash_flow = CashFlow(
            cash_inflow=float(cash_inflow_val),
            cash_outflow=float(cash_outflow_val),
            closing_balance=bank_balance + float(cash_balance),
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

        # 5. Outstanding Receivables
        outstanding_receivables = []
        inv_query = await db.execute(
            select(Invoice)
            .where(Invoice.status == InvoiceStatus.PENDING.value)
            .order_by(Invoice.created_at.asc())
            .limit(5)
        )
        for inv in inv_query.scalars().all():
            outstanding_receivables.append(
                OutstandingReceivable(
                    client_invoice=f"INV-{inv.id}",
                    amount_due=float(inv.total_amount),
                    due_date=inv.created_at.date() + timedelta(days=30),
                )
            )

        # 6. Pending Payables
        pending_payables = []
        vendor_bills_query = await db.execute(
            select(VendorBill)
            .where(VendorBill.status == "PENDING")
            .order_by(VendorBill.due_date.asc())
            .limit(5)
        )
        for vb in vendor_bills_query.scalars().all():
            pending_payables.append(
                PendingPayable(
                    vendor_bill_no=vb.bill_number,
                    amount=float(vb.total_amount - vb.amount_paid),
                    due_date=vb.due_date,
                )
            )

        # 7. Upcoming Payments
        upcoming_payments = []
        today_date = datetime.utcnow().date()

        vb_today = await db.scalar(
            select(func.sum(VendorBill.total_amount - VendorBill.amount_paid)).where(
                VendorBill.status == "PENDING", VendorBill.due_date == today_date
            )
        )
        if vb_today:
            upcoming_payments.append(
                UpcomingPayment(
                    category="Today",
                    description="Vendor Payments",
                    amount=float(vb_today),
                )
            )

        tomorrow = today_date + timedelta(days=1)
        vb_tomorrow = await db.scalar(
            select(func.sum(VendorBill.total_amount - VendorBill.amount_paid)).where(
                VendorBill.status == "PENDING", VendorBill.due_date == tomorrow
            )
        )
        if vb_tomorrow:
            upcoming_payments.append(
                UpcomingPayment(
                    category="Tomorrow",
                    description="Vendor Payments",
                    amount=float(vb_tomorrow),
                )
            )

        gst_due_upcoming = await db.scalar(
            select(func.sum(GSTReturn.net_gst_payable)).where(
                GSTReturn.status == "Draft"
            )
        )
        if gst_due_upcoming:
            upcoming_payments.append(
                UpcomingPayment(
                    category="Upcoming",
                    description="GST Liability",
                    amount=float(gst_due_upcoming),
                )
            )

        # 8. Recent Activities
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
            outstanding_receivables=outstanding_receivables,
            pending_payables=pending_payables,
            upcoming_payments=upcoming_payments,
            recent_activities=recent_activities,
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
        today = date.today()
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
            await db.execute(select(Approval).where(Approval.status == "Pending"))
        ).scalars().all()
        
        expense_ids = [a.entity_id for a in all_pending_approvals if a.entity_type == "expense"]
        material_ids = [a.entity_id for a in all_pending_approvals if a.entity_type == "material"]
        bill_ids = [a.entity_id for a in all_pending_approvals if a.entity_type == "bill"]
        
        valid_reviews = 0
        if expense_ids:
            valid_reviews += (await db.scalar(select(func.count(Expense.id)).where(Expense.id.in_(expense_ids), Expense.project_id.in_(project_ids)))) or 0
        if material_ids:
            valid_reviews += (await db.scalar(select(func.count(Material.id)).where(Material.id.in_(material_ids), Material.project_id.in_(project_ids)))) or 0
        if bill_ids:
            valid_reviews += (await db.scalar(select(func.count(VendorBill.id)).where(VendorBill.id.in_(bill_ids), VendorBill.project_id.in_(project_ids)))) or 0
            
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
            + case((m.SafetyIncident.ppe_compliance == False, PPE_NON_COMPLIANCE_PENALTY), else_=0) 
            + case((m.SafetyIncident.safety_checklist_status == SafetyChecklistStatus.FAILED, CHECKLIST_FAILURE_PENALTY), else_=0)
        )

        total_penalty = await db.scalar(
            select(func.sum(penalty_expr)).where(m.SafetyIncident.project_id.in_(project_ids))
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
            if p.budget_utilization_total > 0 and p.budget_utilization_actual > p.budget_utilization_total:
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
        await db.execute(select(Approval).where(Approval.status == "Pending"))
    ).scalars().all()
    
    expense_ids = [a.entity_id for a in all_pending_approvals if a.entity_type == "expense"]
    material_ids = [a.entity_id for a in all_pending_approvals if a.entity_type == "material"]
    bill_ids = [a.entity_id for a in all_pending_approvals if a.entity_type == "bill"]
    
    pending_approvals = 0
    if expense_ids:
        pending_approvals += (await db.scalar(select(func.count(Expense.id)).where(Expense.id.in_(expense_ids), Expense.project_id.in_(project_ids)))) or 0
    if material_ids:
        pending_approvals += (await db.scalar(select(func.count(Material.id)).where(Material.id.in_(material_ids), Material.project_id.in_(project_ids)))) or 0
    if bill_ids:
        pending_approvals += (await db.scalar(select(func.count(VendorBill.id)).where(VendorBill.id.in_(bill_ids), VendorBill.project_id.in_(project_ids)))) or 0

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
    today_dt = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    todays_activities = (
        await db.scalar(
            select(func.count(ActivityLog.id)).where(
                ActivityLog.created_at >= today_dt,
                ActivityLog.entity == "project",
                ActivityLog.entity_id.in_(project_ids)
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
        budget_total = await get_waterfall_budget(db, [db_project_id])

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
                m.Milestone.status == MilestoneStatus.COMPLETED.value,
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
                m.Task.status == TaskStatus.COMPLETED.value,
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
@router.get("/engineer/details", response_model=EnhancedDashboardOut)
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
        today = date.today()
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
                    material_stock_status=[]
                ),
                today_work_summary=[],
                discipline_progress=[],
                timeline=[],
                recent_expenses=[],
                weather=None
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
        projects_query = await db.execute(select(m.Project).where(m.Project.id.in_(project_ids)))
        projects = projects_query.scalars().all()
        today = date.today()
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
            UserAttendance.in_time.is_not(None)
        )
    )
    total_labour = labor_stats.scalar() or 0

    # 2. Material Stock Status
    material_stats = await db.execute(
        select(Material.category, Material.remaining_stock, Material.minimum_stock_level)
        .where(mat_filter_cond, Material.is_deleted == False)
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
        select(Expense).where(exp_filter_cond).order_by(Expense.expense_date.desc()).limit(5)
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
    progress = await db.scalar(select(func.avg(m.Task.completion_percentage)).where(task_filter_cond))
    
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


# =========================================
# LABOUR DASHBOARD
# =========================================


@router.get("/labour", response_model=dict)
async def get_labour_dashboard(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(d.get_current_active_user),
):
    if current_user.role != UserRole.LABOUR.value:
        raise HTTPException(
            status_code=403, detail="Not authorized for Labour Dashboard"
        )

    # 1. Fetch Labour Profile
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Labour)
        .where(Labour.user_id == current_user.id)
        .options(
            selectinload(Labour.contractor),
            selectinload(Labour.labour_type),
            selectinload(Labour.user),
        )
    )
    labour = result.scalar_one_or_none()

    if not labour:
        raise HTTPException(status_code=404, detail="Labour profile not found")

    # 2. Get active project from LabourProject
    lp_result = await db.execute(
        select(LabourProject)
        .where(LabourProject.labour_id == labour.id)
        .order_by(desc(LabourProject.assigned_date))
    )
    labour_projects = lp_result.scalars().all()

    project_name = None
    if len(labour_projects) > 1:
        project_name = "Multiple Active Sites"
    elif len(labour_projects) == 1:
        project_id = labour_projects[0].project_id
        # get project name
        proj_res = await db.execute(
            select(m.Project.project_name).where(m.Project.id == project_id)
        )
        project_name = proj_res.scalar_one_or_none()

    # 3. Get Attendance Status for today
    today = date.today()
    att_res = await db.execute(
        select(UserAttendance).where(
            UserAttendance.user_id == current_user.id,
            UserAttendance.attendance_date == today,
        )
    )
    today_attendance = att_res.scalar_one_or_none()

    check_in_status = "NOT CHECKED IN"
    if today_attendance:
        if today_attendance.out_time:
            check_in_status = "CHECKED OUT"
        elif today_attendance.in_time:
            check_in_status = "CHECKED IN"

    # 4. Get Tasks (Assigned to this user)
    from sqlalchemy.orm import selectinload
    tasks_res = await db.execute(
        select(Task)
        .options(selectinload(Task.project))
        .where(Task.assignments.any(TaskAssignment.user_id == current_user.id))
        .order_by(desc(Task.start_date))
    )
    all_tasks = tasks_res.scalars().all()

    total_tasks = len(all_tasks)
    completed_tasks = sum(1 for t in all_tasks if t.status == TaskStatus.COMPLETED)
    pending_tasks = total_tasks - completed_tasks

    # Recent Tasks
    recent_tasks_models = all_tasks[:5]
    recent_tasks = [
        LabourTaskItem(
            task_id=t.id,
            title=t.title,
            status=t.status.value,
            priority=str(t.priority),
            start_date=t.start_date,
            end_date=t.end_date,
            progress=t.completion_percentage,
            project_name=t.project.project_name if getattr(t, "project", None) else "Project",
        )
        for t in recent_tasks_models
    ]

    if not project_name and all_tasks:
        active_project_names = list(set([t.project.project_name for t in all_tasks if getattr(t, "project", None)]))
        if len(active_project_names) > 1:
            project_name = "Multiple Active Sites"
        elif len(active_project_names) == 1:
            project_name = active_project_names[0]

    # 5. This Month Earnings
    current_month = today.month
    current_year = today.year
    payroll_res = await db.execute(
        select(LabourPayroll).where(
            LabourPayroll.labour_id == labour.id,
            LabourPayroll.month == current_month,
            LabourPayroll.year == current_year,
        )
    )
    payrolls = payroll_res.scalars().all()
    this_month_earnings = sum(float(p.total_wage or 0) for p in payrolls)

    # If no payroll generated, fallback to attendance
    if this_month_earnings == 0:
        att_month_res = await db.execute(
            select(UserAttendance).where(
                UserAttendance.user_id == current_user.id,
                func.extract("month", UserAttendance.attendance_date) == current_month,
                func.extract("year", UserAttendance.attendance_date) == current_year,
            )
        )
        month_attendances = att_month_res.scalars().all()
        wage = labour.effective_daily_wage
        ot_rate = labour.effective_ot_rate
        for a in month_attendances:
            this_month_earnings += float(wage) * (float(a.working_hours) / 8.0)
            this_month_earnings += float(ot_rate) * float(a.overtime_hours)

    # 6. Recent Activity
    recent_activity = []
    
    # Task Assignments
    for t in all_tasks[:2]:
        recent_activity.append({
            "title": "Task Assigned",
            "description": f"Assigned to {t.title}",
            "time": t.start_date.strftime("%d %b %Y") if t.start_date else "Recent",
            "timestamp": getattr(t, "created_at", datetime.utcnow()) or datetime.utcnow()
        })
        
    # Attendance events
    att_res = await db.execute(
        select(UserAttendance).where(
            UserAttendance.user_id == current_user.id
        ).order_by(desc(UserAttendance.attendance_date)).limit(2)
    )
    for a in att_res.scalars().all():
        recent_activity.append({
            "title": "Attendance Logged",
            "description": f"Present on {a.attendance_date.strftime('%d %b')}",
            "time": a.in_time.strftime("%I:%M %p") if a.in_time else "Logged",
            "timestamp": a.in_time if a.in_time else datetime.combine(a.attendance_date, datetime.min.time())
        })
        
    # Payroll Updates
    for p in payrolls[:2]:
        recent_activity.append({
            "title": "Payroll Generated",
            "description": f"Wage ₹{p.total_wage} for {p.month}/{p.year}",
            "time": p.created_at.strftime("%d %b") if getattr(p, "created_at", None) else "Recent",
            "timestamp": getattr(p, "created_at", datetime.utcnow()) or datetime.utcnow()
        })
        
    # Sort and take top 5
    recent_activity.sort(key=lambda x: x["timestamp"], reverse=True)
    
    recent_activity_items = [
        LabourActivityItem(
            title=item["title"],
            description=item["description"],
            time=item["time"]
        )
        for item in recent_activity[:5]
    ]

    data = LabourDashboardOut(
        user_name=current_user.full_name or "Labour User",
        project_name=project_name,
        contractor_name=labour.contractor.name if labour.contractor else None,
        check_in_status=check_in_status,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        pending_tasks=pending_tasks,
        this_month_earnings=float(this_month_earnings),
        recent_tasks=recent_tasks,
        recent_activity=recent_activity_items,
    )

    return success_response(
        message="Labour dashboard fetched successfully", data=data.model_dump()
    )


def apply_payroll_time_filter(stmt, time_filter: Optional[str], month: Optional[int], year: Optional[int]):
    if time_filter:
        today = get_naive_utc_now().replace(tzinfo=timezone.utc).date()
        start_date = None
        if time_filter == "daily":
            start_date = today
        elif time_filter == "weekly":
            start_date = today - timedelta(days=7)
        elif time_filter == "monthly":
            start_date = today - timedelta(days=30)
        elif time_filter == "3_months":
            start_date = today - timedelta(days=90)
        elif time_filter == "6_months":
            start_date = today - timedelta(days=180)
        elif time_filter == "1_year":
            start_date = today - timedelta(days=365)
            
        if start_date is not None:
            stmt = stmt.where(cast(LabourPayroll.created_at, Date) >= start_date)
    else:
        if month:
            stmt = stmt.where(LabourPayroll.month == month)
        if year:
            stmt = stmt.where(LabourPayroll.year == year)
    return stmt

@router.get("/labour/payments", response_model=dict)
async def get_labour_payments(
    month: Optional[int] = None,
    year: Optional[int] = None,
    time_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session)
):
    labour_res = await db.execute(select(Labour).where(Labour.user_id == current_user.id))
    labour = labour_res.scalars().first()
    if not labour:
        raise HTTPException(status_code=404, detail="Labour profile not found for user")

    # Metrics
    metrics_stmt = select(
        func.sum(LabourPayroll.total_wage).label("total_payout"),
        func.sum(case((LabourPayroll.total_wage > 5000, 1), else_=0)).label("high_payouts"),
        func.sum(case((LabourPayroll.total_overtime_hours > 0, 1), else_=0)).label("ot_intensive"),
        func.sum(LabourPayroll.advance_adjusted).label("advance_adjusted")
    ).where(LabourPayroll.labour_id == labour.id)
    
    metrics_stmt = apply_payroll_time_filter(metrics_stmt, time_filter, month, year)

    metrics_res = await db.execute(metrics_stmt)
    metrics_row = metrics_res.first()

    summary = {
        "total_payout": float(metrics_row.total_payout or 0),
        "high_payouts": int(metrics_row.high_payouts or 0),
        "ot_intensive": int(metrics_row.ot_intensive or 0),
        "advance_adjusted": float(metrics_row.advance_adjusted or 0)
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

    count_stmt = select(func.count(LabourPayroll.id)).where(LabourPayroll.labour_id == labour.id)
    count_stmt = apply_payroll_time_filter(count_stmt, time_filter, month, year)
        
    count_res = await db.execute(count_stmt)
    total_records = count_res.scalar() or 0

    records_stmt = records_stmt.order_by(desc(LabourPayroll.created_at)).offset((page - 1) * page_size).limit(page_size)
    records_res = await db.execute(records_stmt)

    records_data = []
    for pr, lab in records_res.all():
        date_str = pr.created_at.strftime("%d %b %Y") if pr.created_at else f"{pr.month}/{pr.year}"
        skill_type = lab.skill_category.value if hasattr(lab.skill_category, "value") else str(lab.skill_category)
        
        records_data.append({
            "id": f"{pr.id:03d}",
            "date": date_str,
            "skill_type": skill_type.capitalize(),
            "daily_wage": f"₹{lab.daily_wage_rate}" if hasattr(lab, 'daily_wage_rate') and lab.daily_wage_rate else "₹800",
            "ot_hours": f"{int(pr.total_overtime_hours)}h" if pr.total_overtime_hours else "0h",
            "total_wage_earned": f"₹{pr.total_wage:,.0f}" if pr.total_wage else "₹0",
            "remarks": pr.remarks or ("STANDARD PAYOUT" if hasattr(pr.status, "value") and pr.status.value == "PAID" else "PENDING"),
            "status": pr.status.value if hasattr(pr.status, "value") else str(pr.status)
        })

    return success_response(
        message="Labour payments fetched",
        data={
            "summary": summary,
            "records": records_data,
            "total_records": total_records,
            "page": page,
            "page_size": page_size,
            "total_pages": (total_records + page_size - 1) // page_size if page_size > 0 else 0
        }
    )

@router.get("/labour/payments/export")
async def export_labour_payments(
    month: Optional[int] = None,
    year: Optional[int] = None,
    time_filter: Optional[str] = None,
    export_format: str = Query("csv", description="csv or pdf"),
    current_user: User = Depends(d.get_current_active_user),
    db: AsyncSession = Depends(get_db_session)
):
    labour_res = await db.execute(select(Labour).where(Labour.user_id == current_user.id))
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
        writer.writerow(["ID", "Date", "Skill Type", "Daily Wage", "OT Hours", "Total Wage Earned", "Remarks", "Status"])
        for pr, lab in records:
            date_str = pr.created_at.strftime("%d %b %Y") if pr.created_at else f"{pr.month}/{pr.year}"
            skill_type = lab.skill_category.value if hasattr(lab.skill_category, "value") else str(lab.skill_category)
            writer.writerow([
                f"{pr.id:03d}",
                date_str,
                skill_type.capitalize(),
                f"₹{lab.daily_wage_rate}" if hasattr(lab, 'daily_wage_rate') and lab.daily_wage_rate else "₹800",
                f"{int(pr.total_overtime_hours)}h" if pr.total_overtime_hours else "0h",
                f"₹{pr.total_wage:,.0f}" if pr.total_wage else "₹0",
                pr.remarks or ("STANDARD PAYOUT" if hasattr(pr.status, "value") and pr.status.value == "PAID" else "PENDING"),
                pr.status.value if hasattr(pr.status, "value") else str(pr.status)
            ])
        output.seek(0)
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            iter([output.getvalue()]), 
            media_type="text/csv", 
            headers={"Content-Disposition": f"attachment; filename=labour_payments.csv"}
        )
    elif export_format == "pdf":
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from fastapi.responses import StreamingResponse

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
        elements = []
        styles = getSampleStyleSheet()
        
        elements.append(Paragraph(f"Labour Payments Report: {current_user.full_name}", styles['Heading1']))
        elements.append(Spacer(1, 0.2 * inch))
        
        data = [["ID", "Date", "Skill", "Wage", "OT", "Total", "Status"]]
        for pr, lab in records:
            date_str = pr.created_at.strftime("%d %b %Y") if pr.created_at else f"{pr.month}/{pr.year}"
            skill_type = lab.skill_category.value if hasattr(lab.skill_category, "value") else str(lab.skill_category)
            data.append([
                f"{pr.id:03d}",
                date_str,
                skill_type.capitalize()[:10],
                f"₹{lab.daily_wage_rate}" if hasattr(lab, 'daily_wage_rate') and lab.daily_wage_rate else "₹800",
                f"{int(pr.total_overtime_hours)}h" if pr.total_overtime_hours else "0h",
                f"₹{pr.total_wage:,.0f}" if pr.total_wage else "₹0",
                pr.status.value if hasattr(pr.status, "value") else str(pr.status)
            ])
            
        t = Table(data)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige),
            ('GRID', (0,0), (-1,-1), 1, colors.black)
        ]))
        elements.append(t)
        doc.build(elements)
        buffer.seek(0)
        return StreamingResponse(
            buffer, 
            media_type="application/pdf", 
            headers={"Content-Disposition": f"attachment; filename=labour_payments.pdf"}
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid export format")
