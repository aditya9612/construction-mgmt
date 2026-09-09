from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from decimal import Decimal

class ReportSummaryDTO(BaseModel):
    project_id: int = Field(..., description="Project identifier")
    project_name: str = Field(..., description="Project name")
    budget_amount: Decimal = Field(..., description="Project budget")
    total_spend: Decimal = Field(..., description="Total spend on vendor bills")
    budget_vs_actual: str = Field(..., description="Budget utilization percentage")

class MaterialsProcuredDTO(BaseModel):
    total_quantity: Decimal = Field(..., description="Total quantity of materials purchased")
    total_value: Decimal = Field(..., description="Total monetary value of materials purchased")

class ProcurementTotalsDTO(BaseModel):
    total_spend: Decimal = Field(..., description="Aggregate spend across all vendor bills")
    total_paid: Decimal = Field(..., description="Total amount paid to suppliers")
    total_pending: Decimal = Field(..., description="Remaining amount pending payment")
    materials_procured: MaterialsProcuredDTO = Field(..., description="Aggregated material purchase metrics")

class SupplierPerformanceDTO(BaseModel):
    supplier_id: int = Field(..., description="Supplier identifier")
    supplier_name: str = Field(..., description="Supplier name")
    bill_count: int = Field(..., description="Number of vendor bills for this supplier")
    total_spend: Decimal = Field(..., description="Total spend on this supplier")
    paid_amount: Decimal = Field(..., description="Amount already paid to this supplier")
    pending_amount: Decimal = Field(..., description="Pending amount for this supplier")
    avg_payment_days: int = Field(..., description="Average payment processing days")

class PurchaseOrdersDTO(BaseModel):
    outstanding_count: int = Field(..., description="Number of outstanding purchase orders")
    outstanding_value: Decimal = Field(..., description="Total value of outstanding purchase orders")

class FiltersAppliedDTO(BaseModel):
    project_id: int
    supplier_id: Optional[int] = None
    status: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    search: Optional[str] = None
    payment_status: Optional[str] = None

class ProcurementEfficiencyReportDTO(BaseModel):
    summary: ReportSummaryDTO
    procurement: ProcurementTotalsDTO
    suppliers: List[SupplierPerformanceDTO]
    purchase_orders: PurchaseOrdersDTO
    filters_applied: FiltersAppliedDTO


# =====================================================================
# PROJECT FINANCIAL HEALTH REPORT DTOs
# =====================================================================

class FinancialHealthSummaryDTO(BaseModel):
    project_id: int = Field(..., description="Project identifier")
    project_name: str = Field(..., description="Project name")
    budget_amount: Decimal = Field(..., description="Approved project budget")
    total_revenue: Decimal = Field(..., description="Total revenue (certified / billed)")
    total_expenses: Decimal = Field(..., description="Total expenses (vendor spend + direct expenses)")
    net_profit: Decimal = Field(..., description="Net profit (total revenue - total expenses)")
    profit_margin_percent: float = Field(..., description="Profit margin percentage")
    budget_utilization_percent: float = Field(..., description="Budget utilization percentage")
    financial_health_status: str = Field(..., description="Financial health status: HEALTHY, MODERATE, CRITICAL, OVER_BUDGET")
    health_score: int = Field(..., description="Overall financial health score (0-100)")


class StatusMetricDTO(BaseModel):
    count: int = Field(..., description="Number of bills with this status")
    amount: Decimal = Field(..., description="Total monetary amount")


class RecentBillItemDTO(BaseModel):
    id: int
    bill_number: str
    work_description: str
    bill_date: str
    total_amount: Decimal
    status: str


class BillingOverviewDTO(BaseModel):
    total_billed: Decimal = Field(..., description="Total gross/net amount billed across all RA bills")
    total_certified: Decimal = Field(..., description="Total amount certified/approved")
    total_received: Decimal = Field(..., description="Total billing amount collected / paid by client")
    total_pending_client: Decimal = Field(..., description="Total certified amount awaiting collection")
    ra_bills_count: int = Field(..., description="Total number of RA bills")
    status_breakdown: Dict[str, StatusMetricDTO] = Field(..., description="Breakdown by bill status (Draft, Submitted, Approved, Paid)")
    recent_bills: List[RecentBillItemDTO] = Field(default_factory=list, description="Recent RA bills")


class ExpenseCategoryDTO(BaseModel):
    category: str = Field(..., description="Expense category name (e.g. Materials, Labour, Equipment, Overheads)")
    amount: Decimal = Field(..., description="Total expense amount in this category")
    percentage: float = Field(..., description="Percentage of total expenses")


class RecentExpenseItemDTO(BaseModel):
    id: int
    category: str
    description: str
    amount: Decimal
    expense_date: str
    payment_mode: str


class ExpensesOverviewDTO(BaseModel):
    total_expenses: Decimal = Field(..., description="Total expenses across all sources")
    vendor_bills_spend: Decimal = Field(..., description="Total spend from vendor bills")
    direct_expenses_spend: Decimal = Field(..., description="Total spend from direct site expenses")
    by_category: List[ExpenseCategoryDTO] = Field(default_factory=list, description="Breakdown by expense category")
    recent_expenses: List[RecentExpenseItemDTO] = Field(default_factory=list, description="Recent expenses recorded")


class ReceivablesDTO(BaseModel):
    total_receivable: Decimal = Field(..., description="Total outstanding payment from client / owner")
    pending_bills_count: int = Field(..., description="Number of unpaid / pending bills")


class PayablesDTO(BaseModel):
    total_payable: Decimal = Field(..., description="Total outstanding payment to vendors & suppliers")
    vendor_payables: Decimal = Field(..., description="Outstanding vendor bills")
    pending_vendor_bills_count: int = Field(..., description="Number of unpaid vendor bills")


class PendingPaymentsDTO(BaseModel):
    receivables: ReceivablesDTO = Field(..., description="Payments receivable from project owner / client")
    payables: PayablesDTO = Field(..., description="Payments payable to vendors and contractors")
    net_cashflow_position: Decimal = Field(..., description="Net cash position (receivables - payables)")


class FinancialFiltersAppliedDTO(BaseModel):
    project_id: int
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class ProjectFinancialHealthReportDTO(BaseModel):
    summary: FinancialHealthSummaryDTO
    billing_overview: BillingOverviewDTO
    expenses_overview: ExpensesOverviewDTO
    pending_payments: PendingPaymentsDTO
    filters_applied: FinancialFiltersAppliedDTO

