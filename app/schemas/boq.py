from decimal import Decimal
from typing import Optional

from pydantic import field_validator
from app.schemas.base import BaseSchema
from app.core.validators import (
    validate_positive_required,
    validate_non_empty_string,
)

class BOQCreate(BaseSchema):
    project_id: int
    item_name: str
    category: str
    description: Optional[str] = None
    quantity: Decimal = Decimal("1")
    unit: str = "unit"
    unit_cost: Decimal = Decimal("1")
    status: Optional[str] = "Active"
    activity_type_id: Optional[int] = None

    @field_validator("quantity", "unit_cost")
    @classmethod
    def positive_values_validator(cls, v):
        return validate_positive_required(v)


    @field_validator("item_name", "category", "unit")
    @classmethod
    def string_validator(cls, v):
        return validate_non_empty_string(v)


class BOQUpdate(BaseSchema):
    item_name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_cost: Optional[Decimal] = None
    is_completed: Optional[bool] = None
    status: Optional[str] = None
    activity_type_id: Optional[int] = None

    @field_validator("quantity", "unit_cost")
    @classmethod
    def positive_values_validator(cls, v):
        if v is None:
            return v
        return validate_positive_required(v)

    @field_validator("item_name", "category", "unit")
    @classmethod
    def string_validator(cls, v):
        if v is None:
            return v
        return validate_non_empty_string(v)


class BOQActualsUpdate(BaseSchema):
    actual_quantity: Decimal
    actual_cost: Decimal

    @field_validator("actual_quantity", "actual_cost")
    @classmethod
    def actuals_validator(cls, v):
        return validate_positive_required(v)


class BOQOut(BaseSchema):
    id: int
    project_id: int

    boq_group_id: int
    version_no: int
    is_latest: bool

    item_name: str
    category: str
    description: Optional[str]

    quantity: float
    unit: str
    unit_cost: float
    total_cost: float

    actual_quantity: float
    actual_cost: float
    variance_cost: float

    activity_type_id: Optional[int]

    is_completed: bool
    status: str
    approval_status: str

    class Config:
        from_attributes = True

        json_encoders = {Decimal: float}


class BOQBulkCreate(BaseSchema):
    items: list[BOQCreate]
