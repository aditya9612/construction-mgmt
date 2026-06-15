from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime

class MaterialStockStatus(BaseModel):
    category: str
    status: str  # OK, Low, Out of Stock

class IssueStats(BaseModel):
    total: int
    high_priority: int

class TodayWorkSummary(BaseModel):
    activity_name: str
    status: str
    start_time: Optional[str] = None
    finish_time: Optional[str] = None

class DisciplineProgress(BaseModel):
    discipline: str
    planned_percent: float
    actual_percent: float

class RecentExpense(BaseModel):
    date: date
    type: str
    category: str
    note: Optional[str]
    amount: float

class MilestoneTimelineEntry(BaseModel):
    id: int
    title: str
    status: str
    start_date: Optional[date]
    end_date: Optional[date]

class DashboardVitals(BaseModel):
    total_labour_today: int
    skilled_labour: int
    unskilled_labour: int
    active_activities: int
    open_issues: IssueStats
    material_stock_status: List[MaterialStockStatus]

class EnhancedDashboardOut(BaseModel):
    project_id: int
    project_name: str
    status: str
    progress: float
    planned_progress: float
    variance: float
    vitals: DashboardVitals
    today_work_summary: List[TodayWorkSummary]
    discipline_progress: List[DisciplineProgress]
    timeline: List[MilestoneTimelineEntry]
    recent_expenses: List[RecentExpense]
    weather: Optional[dict] = None

class AdminVitals(BaseModel):
    total_labour_today: int
    pending_approvals: int
    action_items: int  # High priority open issues
    material_used_today: int
    site_issues_open: int

class AdminProjectOverview(BaseModel):
    id: int
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    progress: float
    performance_score: float  # variance
    health: str  # Active, Delayed, etc.

class ProjectActivity(BaseModel):
    type: str  # task_completion, invoice_submission, site_photo, issue_report
    user: str
    description: str
    time: str
    project_name: Optional[str] = None

class AdminDashboardOut(BaseModel):
    project_overview: dict  # {total, active, completed, delayed}
    financial: dict       # {revenue, expense, profit}
    vitals: AdminVitals
    active_users: int
    discipline_progress: List[DisciplineProgress]
    master_projects: List[AdminProjectOverview]
    recent_activities: List[ProjectActivity]
    kpi_comparison: Optional[dict] = None

class ProjectsManagementDashboardOut(BaseModel):
    summary: dict  # {total, ongoing, completed, delayed}
    recent_activities: List[ProjectActivity]
    master_projects: List[AdminProjectOverview]


class AccountantVitals(BaseModel):
    total_revenue: float
    total_expense: float
    pending_payments_count: int
    total_invoices_count: int


class ProjectBudgetSummary(BaseModel):
    project_name: str
    budgeted: float
    actual: float
    variance_percent: float


class MonthlyTrend(BaseModel):
    month: str
    amount: float


class AccountantDashboardOut(BaseModel):
    vitals: AccountantVitals
    consumption_status: dict  # {total_budget, total_spent, percentage}
    monthly_expense_analysis: List[MonthlyTrend]
    project_cost_summary: List[ProjectBudgetSummary]
    recent_invoices: List[dict]
    recent_transactions: List[dict]


# =========================================
# PM COMMAND CENTER (NEW)
# =========================================

class PMKpiCards(BaseModel):
    total_managed_projects: int
    active_site_deployments: int
    avg_completion_percent: float
    delayed_sites_count: int
    pending_reviews_count: int

class PMProjectPerformance(BaseModel):
    id: int
    name: str
    business_id: str
    progress: float
    status: str  # ON TRACK, DELAYED, AT RISK
    start_date: Optional[date]
    end_date: Optional[date]
    budget_utilization_actual: float
    budget_utilization_total: float

class PMResourceOrchestration(BaseModel):
    user_id: int
    full_name: str
    initials: str
    assigned_project: str
    status: str  # On Site, Travelling, Off Duty
    last_seen: str  # "10 mins ago"

class PMCostTrackingItem(BaseModel):
    month: str
    actual_cost: float
    budget: float

class PMDelayRiskAnalysis(BaseModel):
    project_name: str
    risk_type: str
    priority: str  # High, Medium, Low
    status: str    # CRITICAL, WARNING, MONITORED

class PMCriticalAlert(BaseModel):
    id: int
    alert_type: str
    message: str
    project_name: str
    timestamp: datetime

class PMTaskOverview(BaseModel):
    id: int
    task_name: str
    engineer_name: str
    status: str  # In Progress, Pending, Completed
    due_date: Optional[date]

class PMCommandCenterOut(BaseModel):
    header_date: str
    kpis: PMKpiCards
    project_performance: List[PMProjectPerformance]
    quality_score: int
    safety_score: int
    resource_orchestration: List[PMResourceOrchestration]
    cost_tracking: List[PMCostTrackingItem]
    risk_analysis: List[PMDelayRiskAnalysis]
    critical_alerts: List[PMCriticalAlert]
    task_management: List[PMTaskOverview]
    recent_activities: List[ProjectActivity]

# =========================================
# LABOUR DASHBOARD (NEW)
# =========================================

class LabourTaskItem(BaseModel):
    task_id: str
    title: str
    status: str
    priority: str
    start_date: Optional[date]
    progress: float

class LabourActivityItem(BaseModel):
    title: str
    description: str
    time: str

class LabourDashboardOut(BaseModel):
    user_name: str
    project_name: Optional[str]
    contractor_name: Optional[str]
    
    check_in_status: str
    
    total_tasks: int
    completed_tasks: int
    pending_tasks: int
    this_month_earnings: float
    
    recent_tasks: List[LabourTaskItem]
    recent_activity: List[LabourActivityItem]
