from pydantic import BaseModel, Field
from typing import List, Optional
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
