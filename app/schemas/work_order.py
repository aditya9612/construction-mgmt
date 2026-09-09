from pydantic import BaseModel, ConfigDict, field_serializer, field_validator
from typing import Optional
from decimal import Decimal


class WorkOrderBase(BaseModel):
    project_id: int
    contractor_id: Optional[int] = None
    work_description: str
    total_quantity: Decimal
    rate: Decimal

    @field_validator("total_quantity", "rate")
    def validate_positive(cls, v):
        if v <= 0:
            raise ValueError("Must be greater than 0")
        return v


class WorkOrderCreate(WorkOrderBase):
    pass


class WorkOrderUpdate(BaseModel):
    contractor_id: Optional[int] = None
    work_description: Optional[str] = None
    total_quantity: Optional[Decimal] = None
    completed_quantity: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    status: Optional[str] = None


class WorkOrderOut(BaseModel):
    id: int
    project_id: int
    contractor_id: Optional[int] = None
    work_order_number: str
    work_description: str
    total_quantity: Decimal
    completed_quantity: Decimal
    rate: Decimal
    total_amount: Decimal
    status: str
    quotation_id: Optional[int]

    model_config = ConfigDict(from_attributes=True)

    @field_serializer(
        "total_quantity",
        "completed_quantity",
        "rate",
        "total_amount",
        when_used="json",
    )
    def serialize_decimal(self, v: Optional[Decimal]) -> Optional[float]:
        return float(v) if v is not None else None