from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import (
    ConfigDict,
    Field,
    field_validator,
)

from app.schemas.base import BaseSchema
from app.core.enums import (
    EquipmentCondition,
    EquipmentStatus,
    PurchaseType,
)
from app.core.validators import (
    validate_client_name,
    validate_equipment_name,
    validate_equipment_code,
    validate_operator_name,
    validate_notes,
)

# =====================================================
# EQUIPMENT
# =====================================================


class EquipmentCreate(BaseSchema):

    project_id: Optional[int] = Field(
        None,
        gt=0,
    )

    equipment_name: str = Field(
        ...,
        max_length=255,
    )

    equipment_code: str = Field(
        ...,
        max_length=100,
    )

    operator_name: Optional[str] = Field(
        None,
        max_length=255,
    )

    condition: EquipmentCondition = EquipmentCondition.GOOD

    rental_cost: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    maintenance_date: Optional[date] = None

    @field_validator("equipment_name")
    @classmethod
    def validate_name(cls, v):
        return validate_equipment_name(v)

    @field_validator("equipment_code")
    @classmethod
    def validate_code(cls, v):
        return validate_equipment_code(v)

    @field_validator("operator_name")
    @classmethod
    def validate_operator(cls, v):
        return validate_operator_name(v)

    @field_validator("maintenance_date")
    @classmethod
    def validate_date(cls, v):

        if v and v.year < 2000:
            raise ValueError("Invalid maintenance date")

        return v


class EquipmentUpdate(BaseSchema):

    project_id: Optional[int] = Field(
        None,
        gt=0,
    )

    equipment_name: Optional[str] = Field(
        None,
        max_length=255,
    )

    equipment_code: Optional[str] = Field(
        None,
        max_length=100,
    )

    operator_name: Optional[str] = Field(
        None,
        max_length=255,
    )

    condition: Optional[EquipmentCondition] = None

    rental_cost: Optional[Decimal] = Field(
        None,
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    maintenance_date: Optional[date] = None

    @field_validator("equipment_name")
    @classmethod
    def validate_name(cls, v):

        if v is None:
            return v

        return validate_equipment_name(v)

    @field_validator("equipment_code")
    @classmethod
    def validate_code(cls, v):

        if v is None:
            return v

        return validate_equipment_code(v)

    @field_validator("operator_name")
    @classmethod
    def validate_operator(cls, v):

        if v is None:
            return v

        return validate_operator_name(v)

    @field_validator("maintenance_date")
    @classmethod
    def validate_date(cls, v):

        if v is None:
            return v

        if v.year < 2000:
            raise ValueError("Invalid maintenance date")

        return v


class EquipmentOut(BaseSchema):

    id: int

    project_id: Optional[int]

    equipment_name: str

    equipment_code: str

    operator_name: Optional[str]

    working_hours: Optional[float]

    fuel_used: Optional[float]

    condition: Optional[EquipmentCondition]

    status: EquipmentStatus

    rental_cost: Optional[float]

    maintenance_date: Optional[date]

    is_deleted: bool

    created_at: datetime

    updated_at: datetime


# =====================================================
# EQUIPMENT USAGE
# =====================================================


class EquipmentUsageCreate(BaseSchema):

    working_hours: Decimal = Field(
        ...,
        ge=0,
        max_digits=10,
        decimal_places=2,
    )

    fuel_used: Decimal = Field(
        ...,
        ge=0,
        max_digits=10,
        decimal_places=2,
    )

    usage_date: date

    notes: Optional[str] = Field(
        None,
        max_length=500,
    )

    boq_item_id: Optional[int] = Field(
        None,
        gt=0,
    )

    @field_validator("usage_date")
    @classmethod
    def validate_usage_date(cls, v):

        if v > date.today():
            raise ValueError("Usage date cannot be in the future")

        if v.year < 2000:
            raise ValueError("Invalid usage date")

        return v

    @field_validator("notes")
    @classmethod
    def validate_usage_notes(cls, v):
        return validate_notes(v)


class EquipmentUsageUpdate(BaseSchema):

    working_hours: Optional[Decimal] = Field(
        None,
        ge=0,
        max_digits=10,
        decimal_places=2,
    )

    fuel_used: Optional[Decimal] = Field(
        None,
        ge=0,
        max_digits=10,
        decimal_places=2,
    )

    usage_date: Optional[date] = None

    notes: Optional[str] = Field(
        None,
        max_length=500,
    )

    boq_item_id: Optional[int] = Field(
        None,
        gt=0,
    )

    @field_validator("usage_date")
    @classmethod
    def validate_usage_date(cls, v):

        if v is None:
            return v

        if v > date.today():
            raise ValueError("Usage date cannot be in the future")

        if v.year < 2000:
            raise ValueError("Invalid usage date")

        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v):

        if v is None:
            return v

        return validate_notes(v)


class EquipmentUsageOut(BaseSchema):

    id: int

    equipment_id: int

    working_hours: float

    fuel_used: float

    usage_date: date

    notes: Optional[str]

    created_at: datetime

    boq_item_id: Optional[int]


# =====================================================
# REPORT
# =====================================================


class EquipmentUsageReportOut(BaseSchema):

    equipment_id: int

    equipment_code: str

    total_hours: float

    total_fuel: float

    avg_hours: float

    usage_count: int


# =====================================================
# EQUIPMENT MAINTENANCE
# =====================================================


class EquipmentMaintenanceCreate(BaseSchema):

    description: str = Field(
        ...,
        min_length=3,
        max_length=1000,
    )

    maintenance_date: date

    cost: Optional[Decimal] = Field(
        default=None,
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    next_maintenance_date: Optional[date] = None

    project_id: int = Field(
        ...,
        gt=0,
    )

    boq_item_id: Optional[int] = Field(
        None,
        gt=0,
    )

    @field_validator("description")
    @classmethod
    def validate_description(cls, v):

        v = " ".join(v.strip().split())

        if not v:
            raise ValueError("Description is required.")

        return v

    @field_validator("maintenance_date")
    @classmethod
    def validate_maintenance_date(cls, v):

        if v.year < 2000:
            raise ValueError("Invalid maintenance date.")

        return v

    @field_validator("next_maintenance_date")
    @classmethod
    def validate_next_date(cls, v, info):

        maintenance_date = info.data.get("maintenance_date")

        if v:

            if v.year < 2000:
                raise ValueError("Invalid next maintenance date.")

            if maintenance_date and v < maintenance_date:
                raise ValueError(
                    "Next maintenance date cannot be before maintenance date."
                )

        return v


# =====================================================
# UPDATE
# =====================================================


class EquipmentMaintenanceUpdate(BaseSchema):

    description: Optional[str] = Field(
        None,
        min_length=3,
        max_length=1000,
    )

    maintenance_date: Optional[date] = None

    cost: Optional[Decimal] = Field(
        None,
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    next_maintenance_date: Optional[date] = None

    project_id: Optional[int] = Field(
        None,
        gt=0,
    )

    boq_item_id: Optional[int] = Field(
        None,
        gt=0,
    )

    @field_validator("description")
    @classmethod
    def validate_description(cls, v):

        if v is None:
            return v

        v = " ".join(v.strip().split())

        if not v:
            raise ValueError("Description is required.")

        return v

    @field_validator("maintenance_date")
    @classmethod
    def validate_maintenance_date(cls, v):

        if v is None:
            return v

        if v.year < 2000:
            raise ValueError("Invalid maintenance date.")

        return v

    @field_validator("next_maintenance_date")
    @classmethod
    def validate_next_date(cls, v, info):

        if v is None:
            return v

        maintenance_date = info.data.get("maintenance_date")

        if v.year < 2000:
            raise ValueError("Invalid next maintenance date.")

        if maintenance_date and v < maintenance_date:
            raise ValueError("Next maintenance date cannot be before maintenance date.")

        return v


# =====================================================
# RESPONSE
# =====================================================


class EquipmentMaintenanceOut(BaseSchema):

    id: int

    project_id: int

    boq_item_id: Optional[int]

    equipment_id: int

    description: str

    maintenance_date: date

    cost: Optional[float]

    next_maintenance_date: Optional[date]

    is_completed: bool = False

    completed_at: Optional[datetime] = None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={Decimal: lambda v: round(float(v), 2)},
    )


# =====================================================
# EQUIPMENT RENTAL CREATE
# =====================================================


class EquipmentRentalCreate(BaseSchema):

    start_date: date

    end_date: Optional[date] = None

    rental_cost: Decimal = Field(
        ...,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    client_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
    )

    notes: Optional[str] = Field(
        None,
        max_length=1000,
    )

    project_id: Optional[int] = Field(
        None,
        gt=0,
    )

    boq_item_id: Optional[int] = Field(
        None,
        gt=0,
    )

    @field_validator("client_name")
    @classmethod
    def validate_client(cls, v):
        return validate_client_name(v)

    @field_validator("notes")
    @classmethod
    def validate_notes_field(cls, v):
        return validate_notes(v)

    @field_validator("start_date")
    @classmethod
    def validate_start(cls, v):

        if v.year < 2000:
            raise ValueError("Invalid start date.")

        return v

    @field_validator("end_date")
    @classmethod
    def validate_end(cls, v, info):

        if v is None:
            return v

        start_date = info.data.get("start_date")

        if v.year < 2000:
            raise ValueError("Invalid end date.")

        if start_date and v < start_date:
            raise ValueError("End date cannot be before start date.")

        return v


# =====================================================
# EQUIPMENT RENTAL UPDATE
# =====================================================


class EquipmentRentalUpdate(BaseSchema):

    start_date: Optional[date] = None

    end_date: Optional[date] = None

    rental_cost: Optional[Decimal] = Field(
        None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    client_name: Optional[str] = Field(
        None,
        max_length=255,
    )

    notes: Optional[str] = Field(
        None,
        max_length=1000,
    )

    project_id: Optional[int] = Field(
        None,
        gt=0,
    )

    boq_item_id: Optional[int] = Field(
        None,
        gt=0,
    )

    @field_validator("client_name")
    @classmethod
    def validate_client(cls, v):

        if v is None:
            return v

        return validate_client_name(v)

    @field_validator("notes")
    @classmethod
    def validate_notes_field(cls, v):

        if v is None:
            return v

        return validate_notes(v)

    @field_validator("start_date")
    @classmethod
    def validate_start(cls, v):

        if v is None:
            return v

        if v.year < 2000:
            raise ValueError("Invalid start date.")

        return v

    @field_validator("end_date")
    @classmethod
    def validate_end(cls, v, info):

        if v is None:
            return v

        start_date = info.data.get("start_date")

        if v.year < 2000:
            raise ValueError("Invalid end date.")

        if start_date and v < start_date:
            raise ValueError("End date cannot be before start date.")

        return v


# =====================================================
# EQUIPMENT RENTAL RESPONSE
# =====================================================


class EquipmentRentalOut(BaseSchema):

    id: int

    equipment_id: int

    start_date: date

    end_date: Optional[date]

    rental_cost: float

    client_name: str

    notes: Optional[str]

    created_at: datetime

    status: Optional[str]

    duration: Optional[int]

    per_day_cost: Optional[float]

    project_id: Optional[int]

    boq_item_id: Optional[int]

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={Decimal: lambda v: round(float(v), 2)},
    )


# =====================================================
# DELETE RENTAL RESPONSE
# =====================================================


class DeleteRentalResponse(BaseSchema):

    message: str

    rental_id: int

    equipment_id: int

    equipment_status: EquipmentStatus


# =====================================================
# PURCHASE
# =====================================================


class EquipmentPurchaseCreate(BaseSchema):

    purchase_type: PurchaseType

    asset_id: Optional[int] = Field(
        None,
        gt=0,
    )

    purchase_date: date

    vendor_name: str = Field(
        ...,
        min_length=2,
        max_length=255,
    )

    invoice_number: str = Field(
        ...,
        min_length=2,
        max_length=100,
    )

    quantity: int = Field(
        ...,
        gt=0,
    )

    unit_price: Decimal = Field(
        ...,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    warranty_end_date: Optional[date] = None

    notes: Optional[str] = Field(
        None,
        max_length=1000,
    )

    project_id: int = Field(
        ...,
        gt=0,
    )

    boq_item_id: Optional[int] = Field(
        None,
        gt=0,
    )

    @field_validator("vendor_name")
    @classmethod
    def validate_vendor(cls, v):
        return " ".join(v.strip().split())

    @field_validator("invoice_number")
    @classmethod
    def validate_invoice(cls, v):
        return v.strip()

    @field_validator("notes")
    @classmethod
    def validate_purchase_notes(cls, v):
        return validate_notes(v)

    @field_validator("purchase_date")
    @classmethod
    def validate_purchase_date(cls, v):

        if v.year < 2000:
            raise ValueError("Invalid purchase date.")

        return v

    @field_validator("warranty_end_date")
    @classmethod
    def validate_warranty(cls, v):

        if v and v.year < 2000:
            raise ValueError("Invalid warranty date.")

        return v


class EquipmentPurchaseUpdate(BaseSchema):

    vendor_name: Optional[str] = Field(
        None,
        max_length=255,
    )

    invoice_number: Optional[str] = Field(
        None,
        max_length=100,
    )

    quantity: Optional[int] = Field(
        None,
        gt=0,
    )

    unit_price: Optional[Decimal] = Field(
        None,
        gt=0,
        max_digits=12,
        decimal_places=2,
    )

    warranty_end_date: Optional[date] = None

    notes: Optional[str] = None

    project_id: Optional[int] = Field(
        None,
        gt=0,
    )

    boq_item_id: Optional[int] = Field(
        None,
        gt=0,
    )

    @field_validator("vendor_name")
    @classmethod
    def validate_vendor(cls, v):

        if v is None:
            return v

        return " ".join(v.strip().split())

    @field_validator("invoice_number")
    @classmethod
    def validate_invoice(cls, v):

        if v is None:
            return v

        return v.strip()

    @field_validator("notes")
    @classmethod
    def validate_notes_field(cls, v):

        if v is None:
            return v

        return validate_notes(v)


class EquipmentPurchaseOut(BaseSchema):

    id: int

    project_id: int

    boq_item_id: Optional[int]

    purchase_type: PurchaseType

    asset_id: Optional[int]

    asset_name: Optional[str]

    purchase_date: date

    vendor_name: str

    invoice_number: str

    quantity: int

    unit_price: float

    total_amount: float

    warranty_end_date: Optional[date]

    notes: Optional[str]

    created_at: datetime


# =====================================================
# TRANSFER
# =====================================================


class EquipmentTransferRequest(BaseSchema):

    equipment_id: int = Field(
        ...,
        gt=0,
    )

    to_project_id: int = Field(
        ...,
        gt=0,
    )


# =====================================================
# BULK ALLOCATION
# =====================================================


class EquipmentAllocateRequest(BaseSchema):

    equipment_ids: list[int] = Field(
        ...,
        min_length=1,
    )

    project_id: int = Field(
        ...,
        gt=0,
    )


class EquipmentDeallocateRequest(BaseSchema):

    equipment_ids: list[int] = Field(
        ...,
        min_length=1,
    )

    project_id: int = Field(
        ...,
        gt=0,
    )


class EquipmentAllocateResponse(BaseSchema):

    equipment_ids: list[int]

    project_id: int

    success_count: int

    failed_count: int

    allocated_ids: list[int]

    failed: list[dict] = Field(default_factory=list)


class EquipmentDeallocateResponse(BaseSchema):

    project_id: int

    success_count: int

    failed_count: int

    deallocated_ids: list[int]

    failed: list[dict] = Field(default_factory=list)


class AllocationOut(BaseSchema):

    equipment_id: int

    project_id: Optional[int]

    allocated: bool


# =====================================================
# USAGE REPORT
# =====================================================


class UsageReportItem(BaseSchema):

    equipment_id: int

    equipment_code: str

    total_hours: float

    total_fuel: float

    avg_hours: float

    usage_count: int


# =====================================================
# COST REPORT
# =====================================================


class CostReportItem(BaseSchema):

    equipment_id: int

    equipment_code: str

    total_cost: float

    rental_count: int

    avg_cost: Optional[float] = None

    total_days: Optional[int] = None

    revenue_per_day: Optional[float] = None


# =====================================================
# AVAILABILITY REPORT
# =====================================================


class AvailabilityReportItem(BaseSchema):

    equipment_id: int

    equipment_code: str

    equipment_name: str

    is_available: bool

    project_id: Optional[int]


# =====================================================
# UTILIZATION REPORT
# =====================================================


class UtilizationReportItem(BaseSchema):

    equipment_id: int

    equipment_code: str

    total_hours: float

    utilization_rate: float


# =====================================================
# PURCHASE REPORT
# =====================================================


class EquipmentPurchaseReportItem(BaseSchema):

    purchase_type: PurchaseType

    asset_id: Optional[int]

    asset_name: Optional[str]

    purchase_count: int

    total_quantity: int

    total_purchase_amount: float


# =====================================================
# MAINTENANCE ALERT
# =====================================================


class MaintenanceAlertItem(BaseSchema):

    equipment_id: int

    equipment_code: str

    maintenance_date: date

    days_until: int

    status: str


# =====================================================
# EQUIPMENT ALERT
# =====================================================


class EquipmentAlertOut(BaseSchema):

    equipment_id: int

    equipment_code: str

    issue: str


# =====================================================
# AUDIT LOG
# =====================================================


class EquipmentAuditLogOut(BaseSchema):

    id: int

    equipment_id: int

    action: str

    old_values: Optional[dict[str, Any]] = None

    new_values: Optional[dict[str, Any]] = None

    user_id: Optional[int] = None

    ip_address: Optional[str] = None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )


# =====================================================
# KPI
# =====================================================


class EquipmentKPIOut(BaseSchema):

    total_equipment: int

    available: int

    allocated: int

    rented: int

    maintenance: int

    damaged: int

    utilization_rate: float

    total_rental_revenue: float

    total_maintenance_cost: float


class EquipmentTransferHistoryOut(BaseSchema):

    id: int

    equipment_id: int

    from_project_id: Optional[int]

    from_project_name: Optional[str]

    to_project_id: Optional[int]

    to_project_name: Optional[str]

    transferred_at: datetime

    transferred_by: Optional[int]


class EquipmentAvailabilityOut(BaseSchema):

    equipment_id: int

    equipment_name: str

    equipment_code: str

    status: EquipmentStatus

    project_name: Optional[str]

class DeleteUsageResponse(BaseSchema):
    message: str
    usage_id: int
    equipment_id: int