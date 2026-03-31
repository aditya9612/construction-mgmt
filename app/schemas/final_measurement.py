from pydantic import BaseModel
from typing import Optional


class FinalMeasurementBase(BaseModel):
    project_id: int
    final_area: float
    approved_rate: float

    extra_area: float = 0
    extra_rate: float = 0


class FinalMeasurementCreate(FinalMeasurementBase):
    pass


class FinalMeasurementUpdate(BaseModel):
    final_area: Optional[float] = None
    approved_rate: Optional[float] = None
    extra_area: Optional[float] = None
    extra_rate: Optional[float] = None


class FinalMeasurementOut(BaseModel):
    id: int
    project_id: int

    final_area: float
    approved_rate: float

    extra_area: float
    extra_rate: float

    total_area: float
    total_amount: float

    class Config:
        from_attributes = True