from pydantic import BaseModel, Field
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
    active_activities: int
    open_issues: IssueStats
    material_stock_status: List[MaterialStockStatus]


class EnhancedDashboardOut(BaseModel):
    project_id: Optional[int] = None
    project_name: Optional[str] = None
    status: Optional[str] = None
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
    financial: dict  # {revenue, expense, profit}
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


class AccountantKpiCards(BaseModel):
    cash_balance: float
    bank_balance: float
    receivables: float
    payables: float
    total_budget: float
    total_spent: float
    net_profit: float
    gst_due: float


class RevenueExpenseTrend(BaseModel):
    month: str
    revenue: float
    expense: float


class CashFlowTrend(BaseModel):
    month: str
    inflow: float
    outflow: float


class ProjectCostSummaryItem(BaseModel):
    project_name: str
    budgeted: float
    spent: float
    remaining: float


class AgingBucket(BaseModel):
    period: str
    amount: float
    percentage: float


class UpcomingTransactionItem(BaseModel):
    entity_name: str
    date: str
    amount: float


class RecentActivityItem(BaseModel):
    time: str
    activity: str


class AccountantDashboardOut(BaseModel):
    kpi_cards: AccountantKpiCards
    revenue_vs_expense: List[RevenueExpenseTrend]
    cash_flow: List[CashFlowTrend]
    project_cost_summary: List[ProjectCostSummaryItem]
    receivable_aging: List[AgingBucket]
    payable_aging: List[AgingBucket]
    upcoming_payments: List[UpcomingTransactionItem]
    upcoming_collections: List[UpcomingTransactionItem]
    recent_activities: List[RecentActivityItem]
    notifications: List[str]


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
    status: str  # CRITICAL, WARNING, MONITORED


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
    cost_tracking: List[PMCostTrackingItem]
    risk_analysis: List[PMDelayRiskAnalysis]
    critical_alerts: List[PMCriticalAlert]
    task_management: List[PMTaskOverview]
    recent_activities: List[ProjectActivity]


# =========================================
# LABOUR DASHBOARD (NEW)
# =========================================


class LabourTaskItem(BaseModel):
    task_id: int
    title: str
    status: str
    priority: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    progress: float = Field(ge=0, le=100)
    project_name: Optional[str] = None


class LabourActivityItem(BaseModel):
    title: str
    description: str
    time: str


class LabourPayment(BaseModel):
    total_amount: float
    paid_amount: float
    pending_amount: float
    payment_status: Optional[str]
    is_overdue: bool


class LabourProfile(BaseModel):
    user_name: str
    profile_image: Optional[str] = None
    project_name: Optional[str] = None
    contractor_name: Optional[str] = None
    labour_type: Optional[str] = None
    skill_category: Optional[str] = None
    check_in_status: str
    is_checked_in: bool
    is_multi_project: bool = False


class LabourOverview(BaseModel):
    today_hours: float
    overtime_hours: float

    total_tasks: int
    completed_tasks: int
    pending_tasks: int

    weekly_earnings: float
    this_month_earnings: float


class LabourAttendanceSummary(BaseModel):
    present_days: int
    absent_days: int
    half_days: int
    total_days: int
    attendance_percentage: float
    attendance_streak: int


class LabourDetails(BaseModel):
    site_name: Optional[str] = None
    site_address: Optional[str] = None
    daily_wage: float
    overtime_rate: float


class LabourDashboardOut(BaseModel):
    profile: LabourProfile
    overview: LabourOverview
    attendance_summary: LabourAttendanceSummary
    labour_details: LabourDetails
    payment: LabourPayment
    recent_tasks: list[LabourTaskItem]
    recent_activity: list[LabourActivityItem]


class LabourDashboardResponse(BaseModel):
    success: bool
    message: str
    data: LabourDashboardOut


# =========================================
# PROJECT MANAGER DASHBOARD
# =========================================


class PMSummaryOut(BaseModel):
    total_projects: int
    active_projects: int
    completed_projects: int
    delayed_projects: int
    pending_approvals: int
    open_issues: int
    budget_utilized_percent: float
    todays_activities: int


# =========================================================
# CLIENT DASHBOARD
# =========================================================

from pydantic import BaseModel
from typing import List, Optional
from datetime import date


class ClientProjectInfo(BaseModel):
    project_id: int
    project_name: str
    status: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class ClientDashboardOverview(BaseModel):
    project_health: str
    budget_status: str


class ClientBudgetAnalysis(BaseModel):
    budget: float
    spent: float
    remaining: float
    spent_percent: float
    remaining_percent: float
    variance_percent: float


class ClientTimelineInfo(BaseModel):
    project_duration: int
    elapsed_days: int
    remaining_days: int
    timeline_progress: float


class ClientScheduleInfo(BaseModel):
    actual_progress: float
    expected_progress: float
    variance: float
    status: str


class ClientRiskInfo(BaseModel):
    score: int
    level: str


class ClientKPIs(BaseModel):
    overdue_tasks: int
    overdue_milestones: int
    high_priority_tasks: int


class ClientMilestoneSummaryInfo(BaseModel):
    total: int
    completed: int
    pending: int
    completion_percent: float


class ClientTaskSummaryInfo(BaseModel):
    total: int
    completed: int
    pending: int
    completion_percent: float


class ClientMilestoneItem(BaseModel):
    id: int
    title: str
    status: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    completion_percentage: float


class ClientExpenseItem(BaseModel):
    id: int
    category: str
    description: Optional[str] = None
    amount: float
    expense_date: date
    payment_mode: Optional[str] = None


class ClientExpenseTrendItem(BaseModel):
    month: str  # e.g. "2026-01"
    total_amount: float


class ClientUpcomingMilestoneItem(BaseModel):
    id: int
    title: str
    status: str
    end_date: Optional[date] = None
    days_remaining: Optional[int] = None


class ClientDashboardV2Out(BaseModel):
    project: ClientProjectInfo
    overview: ClientDashboardOverview
    budget_analysis: ClientBudgetAnalysis
    timeline: ClientTimelineInfo
    schedule: ClientScheduleInfo
    risk: ClientRiskInfo
    kpis: ClientKPIs
    milestone_summary: ClientMilestoneSummaryInfo
    task_summary: ClientTaskSummaryInfo
    recent_milestones: List[ClientMilestoneItem]
    recent_expenses: List[ClientExpenseItem]
    expense_trend: List[ClientExpenseTrendItem]
    upcoming_milestones: List[ClientUpcomingMilestoneItem]
    executive_summary: str
