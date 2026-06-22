from decimal import Decimal
from typing import Optional
from datetime import datetime

import re

from PIL.XVThumbImagePlugin import r
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.enums import (
    TransactionType,
    RateType,
    IssueType,
)

from app.core.validators import (
    validate_material_name,
    validate_material_string,
    validate_material_number,
)


# ================= BASE =================
class BaseSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={Decimal: lambda v: round(float(v), 2)},
    )


# ================= MATERIAL =================
class MaterialCreate(BaseSchema):

    project_id: int = Field(..., gt=0)

    material_master_id: int = Field(
        ...,
        gt=0,
    )

    supplier_id: int = Field(
        ...,
        gt=0,
    )

    purchase_rate: Decimal = Field(
        ...,
        gt=0,
        max_digits=18,
        decimal_places=2,
    )

    rate_type: RateType

    quantity_purchased: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        max_digits=18,
        decimal_places=3,
    )

    payment_given: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        max_digits=18,
        decimal_places=2,
    )

    minimum_stock_level: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        max_digits=18,
        decimal_places=3,
    )

    @field_validator(
        "quantity_purchased",
        "payment_given",
        "purchase_rate",
        "minimum_stock_level",
    )
    def non_negative(cls, v, info):
        return validate_material_number(
            v,
            info.field_name,
        )


class MaterialUpdate(BaseSchema):

    material_master_id: Optional[int] = Field(
        None,
        gt=0,
    )

    supplier_id: Optional[int] = Field(
        None,
        gt=0,
    )

    purchase_rate: Optional[Decimal] = Field(
        None,
        gt=0,
        max_digits=18,
        decimal_places=2,
    )

    minimum_stock_level: Optional[Decimal] = Field(
        None,
        ge=0,
        max_digits=18,
        decimal_places=3,
    )

    rate_type: Optional[RateType] = None

    @field_validator(
        "purchase_rate",
        "minimum_stock_level",
    )
    def validate_numbers(cls, v, info):
        return validate_material_number(
            v,
            info.field_name,
        )


class MaterialOut(BaseSchema):

    id: int
    material_code: str

    project_id: int

    material_master_id: int
    material_master_name: Optional[str] = None

    # Add these
    material_master_brand: Optional[str] = None
    material_master_specification: Optional[str] = None
    material_master_hsn_code: Optional[str] = None

    material_name: str
    category: str

    unit_id: int
    unit_name: str

    supplier_id: int
    supplier_name: Optional[str] = None

    purchase_rate: float
    rate_type: RateType

    quantity_purchased: float
    quantity_used: float
    remaining_stock: float

    total_amount: float

    payment_given: float
    payment_pending: float

    extra_paid: float = 0.0

    minimum_stock_level: float

    alert_type: str


# ================= PURCHASE =================
class PurchaseMaterial(BaseSchema):

    quantity: Decimal = Field(
        ...,
        gt=0,
        max_digits=18,
        decimal_places=3,
    )

    rate: Decimal = Field(
        ...,
        gt=0,
        max_digits=18,
        decimal_places=2,
    )

    amount_paid: Decimal = Field(
        ...,
        ge=0,
        max_digits=18,
        decimal_places=2,
    )

    project_id: int = Field(
        ...,
        gt=0,
    )

    issue_type: Optional[IssueType] = None

    @field_validator("quantity")
    def quantity_positive(cls, v):

        if v <= 0:
            raise ValueError("Quantity must be > 0")

        return v

    @field_validator("amount_paid")
    def amount_valid(cls, v):

        if v < 0:
            raise ValueError("Payment cannot be negative")

        return v


# ================= USAGE =================
class UsageMaterial(BaseSchema):

    quantity: Decimal = Field(
        ...,
        gt=0,
        max_digits=18,
        decimal_places=3,
    )

    project_id: int = Field(
        ...,
        gt=0,
    )
    task_id: Optional[int] = None

    issue_type: Optional[IssueType] = None

    @field_validator("quantity")
    def positive(cls, v):

        if v <= 0:
            raise ValueError("Must be greater than 0")

        return v


# ================= SUPPLIER =================
class SupplierCreate(BaseSchema):

    supplier_name: str = Field(
        ...,
        min_length=3,
        max_length=255,
    )

    contact_person: Optional[str] = Field(
        None,
        max_length=255,
    )

    phone_email: Optional[str] = Field(
        None,
        max_length=100,
    )

    gst_number: Optional[str] = Field(
        None,
        max_length=20,
    )

    address: Optional[str] = Field(
        None,
        max_length=255,
    )

    @field_validator("supplier_name")
    def validate_name(cls, v):

        if not v or len(v.strip()) < 3:
            raise ValueError("Supplier name must be at least 3 characters")

        v = " ".join(v.strip().split())

        if not re.match(
            r"^[A-Za-z0-9\s&.,()-]+$",
            v,
        ):
            raise ValueError("Invalid supplier name")

        return v.title()

    @field_validator("contact_person")
    def validate_contact_person(cls, v):

        if v is None:
            return v

        v = " ".join(v.strip().split())

        if not re.match(
            r"^[A-Za-z. ]+$",
            v,
        ):
            raise ValueError("Invalid contact person name")

        return v.title()

    @field_validator("phone_email")
    def validate_phone(cls, v):

        if v is None:
            return v

        v = v.strip()

        if v.isdigit():

            if not re.fullmatch(
                r"[6-9]\d{9}",
                v,
            ):
                raise ValueError("Invalid phone number")

        return v

    @field_validator("gst_number")
    def validate_gst(cls, v):

        if v is None:
            return v

        v = v.strip().upper()

        if not re.fullmatch(
            r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]",
            v,
        ):
            raise ValueError("Invalid GST number format")

        return v

    @field_validator("address")
    def validate_address(cls, v):

        if v is None:
            return v

        return " ".join(v.strip().split())


class SupplierOut(BaseSchema):

    id: int
    supplier_name: str
    contact_person: Optional[str]
    phone_email: Optional[str]
    gst_number: Optional[str]
    address: Optional[str]


class SupplierUpdate(BaseSchema):
    supplier_name: Optional[str] = Field(None, min_length=3, max_length=255)
    contact_person: Optional[str] = Field(None, max_length=255)
    phone_email: Optional[str] = Field(None, max_length=100)
    gst_number: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = Field(None, max_length=255)


# ================= PURCHASE ORDER =================
class PurchaseOrderCreate(BaseSchema):

    supplier_id: int = Field(
        ...,
        gt=0,
    )

    project_id: int = Field(
        ...,
        gt=0,
    )

    material_id: int = Field(
        ...,
        gt=0,
    )

    quantity: Decimal = Field(
        ...,
        gt=0,
        max_digits=18,
        decimal_places=3,
    )

    rate: Decimal = Field(
        ...,
        gt=0,
        max_digits=18,
        decimal_places=2,
    )

    @field_validator("quantity", "rate")
    def positive(cls, v):

        if v <= 0:
            raise ValueError("Must be greater than 0")

        return v


class PurchaseOrderOut(BaseSchema):

    id: int

    material_id: int
    supplier_id: int
    project_id: int

    material_name: str

    quantity: float
    rate: float

    total_amount: float

    status: Optional[str] = "CREATED"


# ================= TRANSFER =================
class TransferMaterial(BaseSchema):

    id: int
    name: str


class TransferProject(BaseSchema):

    id: int
    name: str


class TransferCreate(BaseSchema):

    material_id: int = Field(
        ...,
        gt=0,
    )

    from_project_id: int = Field(
        ...,
        gt=0,
    )

    to_project_id: int = Field(
        ...,
        gt=0,
    )

    quantity: Decimal = Field(
        ...,
        gt=0,
        max_digits=18,
        decimal_places=3,
    )

    @field_validator("quantity")
    def positive(cls, v):

        if v <= 0:
            raise ValueError("Must be greater than 0")

        return v

    @field_validator("to_project_id")
    def validate_project_difference(
        cls,
        v,
        info,
    ):

        from_project_id = info.data.get("from_project_id")

        if from_project_id and from_project_id == v:
            raise ValueError("From and To project cannot be same")

        return v


class TransferOut(BaseSchema):

    id: int

    material: Optional[TransferMaterial] = None

    from_project: Optional[TransferProject] = None

    to_project: Optional[TransferProject] = None

    quantity: float

    status: str

    created_at: Optional[datetime] = None


# ================= INVENTORY =================
class InventoryAdjustRequest(BaseSchema):

    material_id: int = Field(
        ...,
        gt=0,
    )

    new_stock: Decimal = Field(
        ...,
        ge=0,
        max_digits=18,
        decimal_places=3,
    )

    reason: str = Field(
        ...,
        min_length=3,
        max_length=255,
    )

    @field_validator("reason")
    def validate_reason(cls, v):

        if not v or not v.strip():
            raise ValueError("Reason required")

        return " ".join(v.strip().split())


class InventoryOut(BaseSchema):

    material_id: int

    total_purchased: float
    total_used: float

    remaining_stock: float


# ================= LOG =================
class MaterialLogOut(BaseSchema):

    id: int

    material_id: int

    type: TransactionType

    quantity: float
    rate: float

    avg_rate: Optional[float] = 0.0

    total_amount: float

    amount_paid: float
    payment_pending: float

    issue_type: Optional[str] = None

    project_id: Optional[int] = None
    task_id: Optional[int] = None

    created_at: Optional[datetime] = None


# ================= SUMMARY =================
class SummaryOut(BaseSchema):

    total_materials: int

    total_stock_value: float

    total_pending_payments: float


# ================= REPORT =================
class MaterialReport(BaseSchema):
    material_id: int
    material_code: Optional[str] = None
    material_master_id: Optional[int] = None
    material_master_name: Optional[str] = None

    material_master_brand: Optional[str] = None
    material_master_specification: Optional[str] = None
    material_master_hsn_code: Optional[str] = None
    unit_id: Optional[int] = None
    unit_name: Optional[str] = None
    material_name: str
    category: str

    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None

    project_id: int

    total_purchased: float
    total_used: float
    remaining_stock: float

    avg_rate: float
    stock_value: float

    payment_given: float
    payment_pending: float

    minimum_stock_level: float

    alert_type: str


class MaterialReportSummary(BaseSchema):

    total_materials: int

    total_purchased: float
    total_used: float
    total_remaining: float

    total_stock_value: float

    total_payment_given: float
    total_payment_pending: float

    in_stock_count: int
    low_stock_count: int
    out_of_stock_count: int


class MaterialReportResponse(BaseSchema):

    summary: MaterialReportSummary

    materials: list[MaterialReport]


# ================= PRICE HISTORY =================
class PriceHistoryOut(BaseSchema):

    rate: float

    date: str


# ================= LOW STOCK =================
class LowStockResponse(BaseSchema):

    material_id: int

    material_name: str

    total_purchased: float
    total_used: float

    remaining_stock: float

    total_cost: float

    payment_pending: float

    unit: str

    project_id: int


class PurchaseOrderUpdate(BaseSchema):
    quantity: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    status: Optional[str] = None


class PurchaseMaterialOut(BaseSchema):
    id: int
    material_id: int
    quantity: float
    rate: float
    total_amount: float
    amount_paid: float
    created_at: datetime


class UsageMaterialOut(BaseSchema):
    id: int
    material_id: int
    quantity: float
    project_id: int
    task_id: Optional[int]
    issue_type: Optional[str]
    created_at: datetime


class MaterialLedgerOut(BaseSchema):
    id: int
    material_id: int
    transaction_type: TransactionType
    quantity: float
    balance_stock: float
    created_at: datetime


class MaterialTransactionOut(BaseSchema):
    id: int
    material_id: int
    type: TransactionType
    quantity: float
    rate: float
    total_amount: float
    created_at: datetime


class InventoryAdjustResponse(BaseSchema):
    material_id: int
    material_name: str

    old_stock: float
    new_stock: float
    difference: float

    avg_rate: float

    reason: str
    reference_id: str

    message: str


class ProjectTransactionOut(BaseSchema):
    id: int

    type: str

    material_id: int
    material_name: str

    supplier_name: Optional[str]

    quantity: float
    total_amount: float

    project_id: int

    created_at: datetime


class InventoryItemOut(BaseSchema):
    material_id: int
    material_name: str

    remaining_stock: float

    unit: Optional[str]

    avg_rate: float

    total_value: float

    project_id: int
