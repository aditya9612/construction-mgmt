from decimal import Decimal

from pydantic import BaseModel
from datetime import date
from typing import Optional


class ExpenseBase(BaseModel):
    project_id: int
    category: str
    description: str
    amount: Decimal
    expense_date: date
    payment_mode: str


class ExpenseCreate(ExpenseBase):
    boq_item_id: Optional[int] = None


class ExpenseUpdate(BaseModel):
    category: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[Decimal] = None
    expense_date: Optional[date] = None
    payment_mode: Optional[str] = None
    boq_item_id: Optional[int] = None


class ExpenseOut(ExpenseBase):
    id: int

    class Config:
        from_attributes = True
        
    boq_item_id: Optional[int]

class ExpenseTrendOut(BaseModel):
    date: date
    amount: float

class ExpenseCategorySummaryOut(BaseModel):
    category: str
    total_amount: float
    percentage: float

class ExpenseDashboardOut(BaseModel):
    total_expense: float
    monthly_expense: float
    project_expense: float
    direct_expense: float
    indirect_expense: float
    pending_approval_count: int
    trend: list[ExpenseTrendOut]
    category_summary: list[ExpenseCategorySummaryOut]

class ProjectAllocationCard(BaseModel):
    project_name: str
    material_cost: float
    labour_cost: float
    equipment_cost: float
    other_expense: float
    total_allocated: float

class ProjectAllocationRecent(BaseModel):
    project_name: str
    expense_category: str
    amount: float
    allocated_date: date
    cost_center: str

class ProjectAllocationsOut(BaseModel):
    projects: list[ProjectAllocationCard]
    recent: list[ProjectAllocationRecent]

class ExpenseLedgerRow(BaseModel):
    date: date
    particular: str
    debit: float
    credit: float
    running_balance: float

class BOQComparisonRow(BaseModel):
    boq_item: str
    unit: str
    boq_qty: float
    boq_rate: float
    boq_amount: float
    actual_amount: float
    variance: float
    variance_percentage: float