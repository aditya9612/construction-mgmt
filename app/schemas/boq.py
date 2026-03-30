from decimal import Decimal
from typing import Optional

from app.schemas.base import BaseSchema


class BOQCreate(BaseSchema):
    project_id: int
    item_name: str
    description: Optional[str] = None
    category: str = "Civil"
    quantity: Decimal = Decimal("0")
    unit: str = "unit"
    unit_cost: Decimal = Decimal("0")
    total_cost: Optional[Decimal] = None
    status: Optional[str] = "Active"


class BOQUpdate(BaseSchema):
    item_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_cost: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    status: Optional[str] = None


class BOQOut(BaseSchema):
    id: int
    project_id: int
    item_name: str
    description: Optional[str]
    category: str
    quantity: Decimal
    unit: str
    unit_cost: Decimal
    total_cost: Decimal
    status: str

    class Config:
        from_attributes = True