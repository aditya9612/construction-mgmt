from decimal import Decimal
from typing import Optional

from app.schemas.base import BaseSchema


class BOQCreate(BaseSchema):
    project_id: int
    item_name: str
    category: str
    description: Optional[str] = None
    quantity: Decimal = Decimal("0")
    unit: str = "unit"
    unit_cost: Decimal = Decimal("0")

    actual_quantity: Decimal = Decimal("0")
    actual_cost: Decimal = Decimal("0")

    status: Optional[str] = "Active"


class BOQUpdate(BaseSchema):
    item_name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_cost: Optional[Decimal] = None

    actual_quantity: Optional[Decimal] = None
    actual_cost: Optional[Decimal] = None

    is_completed: Optional[bool] = None
    status: Optional[str] = None


class BOQOut(BaseSchema):
    id: int
    project_id: int

    boq_group_id: int
    version_no: int
    is_latest: bool

    item_name: str
    category: str
    description: Optional[str]

    quantity: Decimal
    unit: str
    unit_cost: Decimal
    total_cost: Decimal

    actual_quantity: Decimal
    actual_cost: Decimal
    variance_cost: Decimal

    is_completed: bool
    status: str

    class Config:
        from_attributes = True