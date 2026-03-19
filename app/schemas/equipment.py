from decimal import Decimal
from typing import Optional

from app.schemas.base import BaseSchema


class EquipmentCreate(BaseSchema):
    project_id: int
    equipment_name: str
    category: Optional[str] = None
    quantity: Decimal = Decimal("0")
    daily_cost: Decimal = Decimal("0")
    total_cost: Optional[Decimal] = None
    status: Optional[str] = "Active"
    notes: Optional[str] = None


class EquipmentUpdate(BaseSchema):
    equipment_name: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[Decimal] = None
    daily_cost: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class EquipmentOut(BaseSchema):
    id: int
    project_id: int
    equipment_name: str
    category: Optional[str]
    quantity: Decimal
    daily_cost: Decimal
    total_cost: Decimal
    status: str
    notes: Optional[str]

