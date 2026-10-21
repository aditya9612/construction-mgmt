from pydantic import BaseModel, ConfigDict, field_validator
from app.core.enums import MeasurementStatus
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

    @field_validator("status", mode="before")
    @classmethod
    def validate_initial_status(cls, v: Optional[str]) -> str:
        if v is None:
            return MeasurementStatus.DRAFT.value
        v_str = str(v).strip().upper()
        valid = [s.value for s in MeasurementStatus]
        if v_str not in valid:
            raise ValueError(f"Invalid status. Must be one of: {valid}")
        if v_str != MeasurementStatus.DRAFT.value:
            raise ValueError(
                f"Cannot set initial status to '{v}'. New measurements must start in 'DRAFT' status."
            )
        return MeasurementStatus.DRAFT.value


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

    @field_validator("status", mode="before")
    @classmethod
    def reject_status_update(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            raise ValueError(
                "Status cannot be modified via general measurement update. Use dedicated workflow endpoints."
            )
        return None


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

    model_config = ConfigDict(from_attributes=True)


class MeasurementDeleteOut(BaseModel):
    message: str = "Measurement deleted successfully"
