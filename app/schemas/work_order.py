from pydantic import BaseModel, field_validator
from typing import Optional
from decimal import Decimal


class WorkOrderBase(BaseModel):
    project_id: int
    contractor_id: int
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
    work_description: Optional[str] = None
    total_quantity: Optional[Decimal] = None
    completed_quantity: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    status: Optional[str] = None


class WorkOrderOut(BaseModel):
    id: int
    project_id: int
    contractor_id: int
    work_order_number: str
    work_description: str
    total_quantity: Decimal
    completed_quantity: Decimal
    rate: Decimal
    total_amount: Decimal
    status: str

    class Config:
        from_attributes = True
        json_encoders = {Decimal: float}