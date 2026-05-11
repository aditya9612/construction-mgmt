from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, validator

from app.core.enums import EquipmentCondition
from app.schemas.base import BaseSchema
from typing import Optional, Dict, Any


# === EQUIPMENT SCHEMAS ===
class EquipmentCreate(BaseSchema):
    project_id: Optional[int] = None
    equipment_name: str = Field(..., max_length=255)
    equipment_code: str = Field(..., max_length=100)

    operator_name: Optional[str] = Field(None, max_length=255)
    working_hours: Optional[Decimal] = Field(None, ge=0)
    fuel_used: Optional[Decimal] = Field(None, ge=0)
    condition: Optional[EquipmentCondition] = None
    rental_cost: Optional[Decimal] = Field(None, ge=0)
    maintenance_date: Optional[date] = None

    @validator("condition")
    def normalize_condition(cls, v):
        if v:
            return EquipmentCondition(v.value.upper())
        return v

class EquipmentUpdate(BaseSchema):
    project_id: Optional[int] = None
    equipment_name: Optional[str] = Field(None, max_length=255)
    equipment_code: Optional[str] = Field(None, max_length=100)
    operator_name: Optional[str] = Field(None, max_length=255)
    working_hours: Optional[Decimal] = Field(None, ge=0)
    fuel_used: Optional[Decimal] = Field(None, ge=0)
    condition: Optional[EquipmentCondition] = None
    rental_cost: Optional[Decimal] = Field(None, ge=0)
    maintenance_date: Optional[date] = None

    @validator("condition")
    def normalize_condition(cls, v):
        if v:
            return EquipmentCondition(v.value.upper())
        return v


class EquipmentOut(BaseSchema):
    id: int
    project_id: Optional[int]
    equipment_name: str
    equipment_code: str
    operator_name: Optional[str]
    working_hours: float
    fuel_used: float
    condition: Optional[EquipmentCondition]
    rental_cost: float
    maintenance_date: Optional[date]
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


# === USAGE SCHEMAS ===
class EquipmentUsageCreate(BaseSchema):
    working_hours: Decimal = Field(..., ge=0)
    fuel_used: Decimal = Field(..., ge=0)
    usage_date: date
    notes: Optional[str] = Field(None, max_length=500)


class EquipmentUsageOut(BaseSchema):
    id: int
    equipment_id: int
    working_hours: float
    fuel_used: float
    usage_date: date
    notes: Optional[str]
    created_at: datetime


# === MAINTENANCE SCHEMAS ===
class EquipmentMaintenanceCreate(BaseSchema):
    description: str = Field(..., max_length=1000)
    maintenance_date: date
    cost: Optional[Decimal] = Field(None, ge=0)
    next_maintenance_date: Optional[date] = None


class EquipmentMaintenanceOut(BaseModel):
    id: int
    equipment_id: int
    description: str
    maintenance_date: date
    cost: float
    next_maintenance_date: Optional[date]
    created_at: datetime
    status: str

    model_config = ConfigDict(from_attributes=True, json_encoders={Decimal: float})


# === RENTAL SCHEMAS ===
class EquipmentRentalCreate(BaseSchema):
    start_date: date
    end_date: Optional[date] = None
    rental_cost: Decimal = Field(..., ge=0)
    client_name: str = Field(..., max_length=255)
    notes: Optional[str] = Field(None, max_length=1000)


class EquipmentRentalOut(BaseSchema):
    id: int
    equipment_id: int
    start_date: date
    end_date: Optional[date]
    rental_cost: float
    client_name: str
    notes: Optional[str]
    created_at: datetime
    status: Optional[str] = None
    duration: Optional[int] = None
    per_day_cost: Optional[float] = None


class EquipmentUsageReportOut(BaseModel):
    equipment_id: int
    equipment_code: str
    total_hours: float
    total_fuel: float
    avg_hours: float
    usage_count: int


class EquipmentAlertOut(BaseModel):
    equipment_id: int
    equipment_code: str
    issue: str
    model_config = {"from_attributes": True}


class EquipmentAuditLogOut(BaseSchema):
    id: int
    equipment_id: int
    action: str
    old_values: Optional[Dict] = None
    new_values: Optional[Dict] = None
    user_id: Optional[int] = None
    ip_address: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ==== ALLOCATION SCHEMAS ====
class AllocationOut(BaseSchema):
    equipment_id: int
    project_id: Optional[int]
    allocated: bool


# ====== REPORT SCHEMAS =====
class UsageReportItem(BaseSchema):
    equipment_id: int
    equipment_code: str
    total_hours: float
    total_fuel: float
    avg_hours: float
    usage_count: int


class CostReportItem(BaseSchema):
    equipment_id: int
    equipment_code: str
    total_cost: float
    rental_count: int
    avg_cost: Optional[float] = None
    total_days: Optional[int] = None
    revenue_per_day: Optional[float] = None


class AvailabilityReportItem(BaseSchema):
    equipment_id: int
    equipment_code: str
    equipment_name: str
    is_available: bool
    project_id: Optional[int]


class UtilizationReportItem(BaseSchema):
    equipment_id: int
    equipment_code: str
    total_hours: float
    utilization_rate: float


class MaintenanceAlertItem(BaseSchema):
    equipment_id: int
    equipment_code: str
    maintenance_date: date
    days_until: int
    status: str
