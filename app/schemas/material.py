from decimal import Decimal
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.core.enums import TransactionType, RateType, IssueType
import re


# ================= BASE =================
class BaseSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={Decimal: lambda v: round(float(v), 2)},  
    )


# ================= MATERIAL =================
class MaterialCreate(BaseSchema):
    project_id: int
    material_name: str
    category: str
    unit: str
    supplier_id: int
    purchase_rate: Decimal
    rate_type: RateType
    quantity_purchased: Decimal = Decimal("0")
    payment_given: Decimal = Decimal("0")
    minimum_stock_level: Decimal = Decimal("0")

    @field_validator("material_name")
    def validate_name(cls, v):
        if not v.strip():
            raise ValueError("Material name required")
        return v.strip()

    @field_validator(
        "quantity_purchased", "payment_given", "purchase_rate", "minimum_stock_level"
    )
    def non_negative(cls, v):
        if v < 0:
            raise ValueError("Value cannot be negative")
        return v


class MaterialUpdate(BaseSchema):
    material_name: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    supplier_id: Optional[int] = None
    purchase_rate: Optional[Decimal] = None
    rate_type: Optional[RateType] = None
    minimum_stock_level: Optional[Decimal] = None


class MaterialOut(BaseSchema):
    id: int
    material_code: str
    project_id: int
    material_name: str
    category: str
    unit: str

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
    extra_paid: float = Field(default=0.0, validation_alias="advance_amount")
    minimum_stock_level: float = 0.0
    alert_type: str


# ================= PURCHASE =================
class PurchaseMaterial(BaseSchema):
    quantity: Decimal
    amount_paid: Decimal
    project_id: int
    issue_type: Optional[IssueType] = None

    @field_validator("quantity", "amount_paid")
    def positive(cls, v):
        if v <= 0:
            raise ValueError("Must be greater than 0")
        return v


# ================= USAGE =================
class UsageMaterial(BaseSchema):
    quantity: Decimal
    project_id: int
    issue_type: Optional[IssueType] = None

    @field_validator("quantity")
    def positive(cls, v):
        if v <= 0:
            raise ValueError("Must be greater than 0")
        return v


# ================= SUPPLIER =================
class SupplierCreate(BaseSchema):
    name: str
    contact: Optional[str] = None

    @field_validator("name")
    def validate_name(cls, v):
        if not v or len(v.strip()) < 3:
            raise ValueError("Supplier name must be at least 3 characters")
        return v.strip().title()  

    @field_validator("contact")
    def validate_contact(cls, v):
        if v is None:
            return v
        if not re.fullmatch(r"\d{10}", v):
            raise ValueError("Contact must be 10 digit number")
        return v


class SupplierOut(BaseSchema):
    id: int
    name: str
    contact: Optional[str] = None


# ================= PURCHASE ORDER =================
class PurchaseOrderCreate(BaseSchema):
    supplier_id: int
    project_id: int
    material_id: int
    quantity: Decimal
    rate: Decimal

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
    material_id: int
    from_project_id: int
    to_project_id: int
    quantity: Decimal

    @field_validator("quantity")
    def positive(cls, v):
        if v <= 0:
            raise ValueError("Must be greater than 0")
        return v


class TransferOut(BaseSchema):
    id: int
    material: Optional[TransferMaterial] = None
    from_project: Optional[TransferProject] = None
    to_project: Optional[TransferProject] = None
    quantity: float   # ✅ FIX
    status: str
    created_at: Optional[datetime] = None


# ================= INVENTORY =================
class InventoryAdjustRequest(BaseSchema):
    material_id: int = Field(..., gt=0)
    new_stock: Decimal = Field(..., ge=0)
    reason: str = Field(..., min_length=3, max_length=255)


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
    created_at: Optional[datetime] = None


# ================= SUMMARY =================
class SummaryOut(BaseSchema):
    total_materials: int
    total_stock_value: float
    total_pending_payments: float


# ================= REPORT =================
class MaterialReport(BaseSchema):
    material_id: int
    material_name: str
    total_purchased: float      
    total_used: float           
    remaining_stock: float      
    total_cost: float           
    payment_pending: float      


# ================= PRICE HISTORY =================
class PriceHistoryOut(BaseSchema):
    rate: float
    date: datetime


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