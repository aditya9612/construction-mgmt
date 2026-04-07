from decimal import Decimal
from typing import Optional

from pydantic import ConfigDict

from app.schemas.base import BaseSchema


class MaterialCreate(BaseSchema):
    project_id: int
    material_name: str
    category: str
    unit: str = "unit"
    supplier_name: str
    purchase_rate: Decimal = Decimal("0")
    rate_type: str
    quantity_purchased: Decimal = Decimal("0")
    payment_given: Decimal = Decimal("0")


class MaterialUpdate(BaseSchema):
    material_name: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    supplier_name: Optional[str] = None
    purchase_rate: Optional[Decimal] = None
    rate_type: Optional[str] = None
    payment_given: Optional[Decimal] = None


class MaterialOut(BaseSchema):
    id: int
    project_id: int

    material_name: str
    category: str
    unit: str
    supplier_name: str

    purchase_rate: Decimal
    rate_type: str

    quantity_purchased: Decimal
    quantity_used: Decimal
    remaining_stock: Decimal

    total_amount: Decimal
    payment_given: Decimal
    payment_pending: Decimal

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={Decimal: float}
    )