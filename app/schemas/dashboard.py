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
