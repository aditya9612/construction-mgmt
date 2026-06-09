from pydantic import BaseModel
from typing import Optional


class FinalMeasurementBase(BaseModel):
    project_id: int
    task_id: Optional[int] = None
    boq_item_id: Optional[int] = None
    
    final_area: float
    approved_rate: float

    extra_area: float = 0
    extra_rate: float = 0
    
    measured_qty: float = 0
    certified_qty: float = 0
    rejected_qty: float = 0
    retention_amount: float = 0
    status: str = "DRAFT"


class FinalMeasurementCreate(FinalMeasurementBase):
    pass


class FinalMeasurementUpdate(BaseModel):
    task_id: Optional[int] = None
    boq_item_id: Optional[int] = None
    final_area: Optional[float] = None
    approved_rate: Optional[float] = None
    extra_area: Optional[float] = None
    extra_rate: Optional[float] = None
    measured_qty: Optional[float] = None
    certified_qty: Optional[float] = None
    rejected_qty: Optional[float] = None
    retention_amount: Optional[float] = None
    status: Optional[str] = None


class FinalMeasurementOut(BaseModel):
    id: int
    project_id: int
    task_id: Optional[int]
    boq_item_id: Optional[int]

    final_area: float
    approved_rate: float

    extra_area: float
    extra_rate: float

    total_area: float
    total_amount: float
    
    measured_qty: float
    certified_qty: float
    rejected_qty: float
    retention_amount: float
    status: str

    class Config:
        from_attributes = True