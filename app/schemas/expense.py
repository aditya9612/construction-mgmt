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