from decimal import Decimal
from typing import Optional

from app.schemas.base import BaseSchema


class MaterialCreate(BaseSchema):
    project_id: int
    name: str
    category: Optional[str] = None
    unit: str = "unit"
    quantity_required: Decimal = Decimal("0")
    quantity_available: Decimal = Decimal("0")
    unit_cost: Decimal = Decimal("0")
    status: Optional[str] = "Active"


class MaterialUpdate(BaseSchema):
    name: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    quantity_required: Optional[Decimal] = None
    quantity_available: Optional[Decimal] = None
    unit_cost: Optional[Decimal] = None
    status: Optional[str] = None


class MaterialOut(BaseSchema):
    id: int
    project_id: int
    name: str
    category: Optional[str]
    unit: str
    quantity_required: Decimal
    quantity_available: Decimal
    unit_cost: Decimal
    status: str

