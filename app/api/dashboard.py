from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.project import compute_project_status
from app.db.session import get_db_session
from app.models.boq import BOQ
from app.models.expense import Expense
from app.models.project import (
    Comment,
    Milestone,
    Project,
    ProjectMember,
    Task,
    TaskProgress,
)
from app.core.logger import logger
from app.utils.helpers import NotFoundError
from app.models.labour import LabourAttendance
from app.models.material import Material
from app.models import project as m
from datetime import date
from app.models.invoice import Invoice
from sqlalchemy import case
from sqlalchemy import or_, and_
from app.models.material import MaterialUsage

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/client/{project_id}")
async def client_dashboard(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    project = await db.get(Project, project_id)

    if not project:
        logger.warning(f"Project not found for dashboard project_id={project_id}")
        raise NotFoundError("Project not found")

    avg_progress = await db.scalar(
        select(func.avg(Task.completion_percentage)).where(
            Task.project_id == project_id
        )
    )
    progress = float(avg_progress or 0)


    boq_total = await db.scalar(
        select(func.sum(BOQ.total_cost)).where(
            BOQ.project_id == project_id, BOQ.is_latest == True
        )
    )

    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )

    budget_total = float(boq_total or 0)
    expense_total = float(total_expense or 0)

    budget_used_percent = (expense_total / budget_total) * 100 if budget_total else 0


    total_milestones = await db.scalar(
        select(func.count(Milestone.id)).where(
            Milestone.project_id == project_id
        )
    )


    completed_milestones = 0  # Not applicable (no status field)


    total_tasks = await db.scalar(
        select(func.count(Task.id)).where(Task.project_id == project_id)
    )

    completed_tasks = await db.scalar(
        select(func.count(Task.id)).where(
            Task.project_id == project_id,
            Task.completion_percentage == 100
        )
    )

    days_remaining = None
    if project.end_date:
        days_remaining = (project.end_date - date.today()).days

    return {
        "project_id": project_id,
        "status": compute_project_status(project),
        "progress_percent": round(progress, 2),

        "budget_total": budget_total,
        "total_expense": expense_total,
        "budget_used_percent": round(budget_used_percent, 2),
        "remaining_budget": budget_total - expense_total,

        "milestones_total": total_milestones or 0,
        "milestones_completed": completed_milestones,  # always 0 (correct for now)

        "tasks_total": total_tasks or 0,
        "tasks_completed": completed_tasks or 0,

        "start_date": project.start_date,
        "end_date": project.end_date,
        "days_remaining": days_remaining,
    }



@router.get("/engineer/{project_id}")
async def engineer_dashboard(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    today = date.today()

    # ------------------ BASIC METRICS ------------------

    labour_count = await db.scalar(
        select(func.count(LabourAttendance.id)).where(
            LabourAttendance.project_id == project_id,
            LabourAttendance.attendance_date == today,
        )
    )

    material_used_today = await db.scalar(
        select(func.sum(MaterialUsage.quantity_used)).where(
            MaterialUsage.project_id == project_id,
            MaterialUsage.usage_date == today,
        )
    )

    material_used_total = await db.scalar(
        select(func.sum(Material.quantity_used)).where(
            Material.project_id == project_id
        )
    )

    total_tasks = await db.scalar(
        select(func.count(Task.id)).where(Task.project_id == project_id)
    )

    completed_tasks = await db.scalar(
        select(func.count(Task.id)).where(
            Task.project_id == project_id,
            Task.completion_percentage == 100,
        )
    )

    progress_percent = (
        (completed_tasks / total_tasks) * 100
        if total_tasks and completed_tasks is not None
        else 0
    )

    # ------------------ 📊 ANALYTICS ------------------

    # 🔹 Labour Trend
    labour_trend_result = await db.execute(
        select(
            m.DailySiteReport.report_date,
            func.sum(m.DailySiteReport.labour_count),
        )
        .where(m.DailySiteReport.project_id == project_id)
        .group_by(m.DailySiteReport.report_date)
        .order_by(m.DailySiteReport.report_date)
    )

    labour_trend = [
        {
            "date": r[0],
            "labour": int(r[1] or 0),
        }
        for r in labour_trend_result.all()
    ]

    # 🔹 Contractor Analytics
    contractor_result = await db.execute(
        select(
            m.DailySiteReport.contractor_name,
            func.count(),
        )
        .where(m.DailySiteReport.project_id == project_id)
        .group_by(m.DailySiteReport.contractor_name)
    )

    contractor_stats = [
        {
            "contractor": r[0] or "Unknown",
            "entries": r[1],
        }
        for r in contractor_result.all()
    ]

    # 🔹 Issue Analytics
    total_reports = await db.scalar(
        select(func.count()).where(
            m.DailySiteReport.project_id == project_id
        )
    )

    issue_reports = await db.scalar(
        select(func.count()).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.issues.isnot(None),
        )
    )

    issue_summary = {
        "total_reports": int(total_reports or 0),
        "reports_with_issues": int(issue_reports or 0),
    }

    # ------------------ FINAL RESPONSE ------------------

    return {
        "project_id": project_id,

        # 🔹 existing
        "labour_today": labour_count or 0,
        "material_used_today": float(material_used_today or 0),
        "material_used_total": float(material_used_total or 0),
        "tasks_done_percent": round(progress_percent, 2),

        "labour_trend": labour_trend,
        "contractor_stats": contractor_stats,
        "issue_summary": issue_summary,
    }


@router.get("/accountant")
async def accountant_dashboard(
    db: AsyncSession = Depends(get_db_session),
):

    total_revenue = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(Invoice.status == "paid")
    )

    total_invoices = await db.scalar(select(func.count(Invoice.id)))

    pending_amount = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(Invoice.status == "pending")
    )

    total_expense = await db.scalar(select(func.sum(Expense.amount)))

    return {
        "total_revenue": float(total_revenue or 0),
        "total_invoices": total_invoices or 0,
        "pending_payments": float(pending_amount or 0),
        "total_expense": float(total_expense or 0),
    }


@router.get("/manager")
async def manager_dashboard(
    db: AsyncSession = Depends(get_db_session),
):
    projects = (await db.execute(select(Project))).scalars().all()

    total_projects = len(projects)

    active_projects = 0
    delayed_projects = 0

    for p in projects:
        status = compute_project_status(p)

        if status in ["Planned", "Active"]:
            active_projects += 1

        if status == "Delayed":
            delayed_projects += 1

    total_budget = await db.scalar(
        select(func.sum(BOQ.total_cost)).where(BOQ.is_latest == True)
    )

    total_expense = await db.scalar(select(func.sum(Expense.amount)))

    utilization = (
        (float(total_expense or 0) / float(total_budget or 1)) * 100
        if total_budget
        else 0
    )

    return {
        "total_projects": total_projects,
        "active_projects": active_projects,
        "delayed_projects": delayed_projects,
        "total_budget": float(total_budget or 0),
        "total_expense": float(total_expense or 0),
        "budget_utilization_percent": round(utilization, 2),
    }


@router.get("/admin")
async def admin_dashboard(
    db: AsyncSession = Depends(get_db_session),
):


    total_projects = await db.scalar(
        select(func.count(Project.id))
    )


    total_expense = await db.scalar(
        select(func.sum(Expense.amount))
    )


    total_revenue = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(
            Invoice.status == "paid"
        )
    )


    recent_projects = (
        (await db.execute(
            select(Project).order_by(Project.created_at.desc()).limit(5)
        ))
        .scalars()
        .all()
    )

    recent_expenses = (
        (await db.execute(
            select(Expense).order_by(Expense.created_at.desc()).limit(5)
        ))
        .scalars()
        .all()
    )

    return {
        "total_projects": total_projects or 0,
        "total_expense": float(total_expense or 0),
        "total_revenue": float(total_revenue or 0),

        "recent_activity": {
            "projects": [
                {
                    "id": p.id,
                    "name": p.project_name,
                    "created_at": p.created_at,
                }
                for p in recent_projects
            ],
            "expenses": [
                {
                    "id": e.id,
                    "amount": float(e.amount),
                    "created_at": e.created_at,
                }
                for e in recent_expenses
            ],
        }
    }