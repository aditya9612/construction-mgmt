from pydantic import BaseModel, Field, field_validator

from typing import Optional, Literal

from datetime import date, datetime, time

# =========================================================
# MATERIAL
# =========================================================


class QuotationMaterialCreate(BaseModel):

    material_id: Optional[int] = None

    material_name: str

    category: Optional[str] = None

    unit: str

    estimated_quantity: float = Field(..., ge=0)

    estimated_rate: float = Field(..., ge=0)

    notes: Optional[str] = None


class QuotationMaterialUpdate(BaseModel):

    material_id: Optional[int] = None

    material_name: Optional[str] = None

    category: Optional[str] = None

    unit: Optional[str] = None

    estimated_quantity: Optional[float] = Field(None, ge=0)

    estimated_rate: Optional[float] = Field(None, ge=0)

    notes: Optional[str] = None


class QuotationMaterialOut(BaseModel):

    id: int

    material_id: Optional[int]

    material_name: str

    category: Optional[str]

    unit: str

    estimated_quantity: float

    estimated_rate: float

    estimated_amount: float

    notes: Optional[str]

    class Config:
        from_attributes = True


# =========================================================
# LABOUR
# =========================================================


class QuotationLabourCreate(BaseModel):

    labour_id: Optional[int] = None

    skill_type: str

    labour_count: int = Field(..., ge=1)

    daily_wage: float = Field(..., ge=0)

    labour_days: float = Field(..., ge=0)

    overtime_hours: float = Field(0, ge=0)

    overtime_rate: float = Field(0, ge=0)

    notes: Optional[str] = None


class QuotationLabourUpdate(BaseModel):

    labour_id: Optional[int] = None

    skill_type: Optional[str] = None

    labour_count: Optional[int] = Field(None, ge=1)

    daily_wage: Optional[float] = Field(None, ge=0)

    labour_days: Optional[float] = Field(None, ge=0)

    overtime_hours: Optional[float] = Field(None, ge=0)

    overtime_rate: Optional[float] = Field(None, ge=0)

    notes: Optional[str] = None


class QuotationLabourOut(BaseModel):

    id: int

    labour_id: Optional[int]

    skill_type: str

    labour_count: int

    daily_wage: float

    labour_days: float

    overtime_hours: float

    overtime_rate: float

    amount: float

    notes: Optional[str]

    class Config:
        from_attributes = True


# =========================================================
# MEASUREMENT
# =========================================================


class MeasurementCreate(BaseModel):

    length: Optional[float] = Field(None, ge=0)

    width: Optional[float] = Field(None, ge=0)

    height: Optional[float] = Field(None, ge=0)

    unit: Optional[str] = "ft"

    @field_validator("height")
    @classmethod
    def validate_measurement(cls, v, info):

        length = info.data.get("length")
        width = info.data.get("width")

        if (length or 0) == 0 and (width or 0) == 0 and (v or 0) == 0:
            raise ValueError("At least one dimension must be greater than 0")

        return v


class MeasurementOut(MeasurementCreate):

    id: int

    cubic_feet: float
    cubic_meter: float
    brass: float
    quantity: float

    formula_used: Optional[str]

    class Config:
        from_attributes = True


class QuotationExtraChargeCreate(BaseModel):
    equipment_id: Optional[int] = None
    expense_type: str
    description: Optional[str] = None
    quantity: float = Field(1, ge=0)
    rate: float = Field(0, ge=0)
    amount: float = 0
    notes: Optional[str] = None


class QuotationExtraChargeUpdate(BaseModel):

    equipment_id: Optional[int] = None

    expense_type: Optional[str] = None

    description: Optional[str] = None

    quantity: Optional[float] = Field(None, ge=0)

    rate: Optional[float] = Field(None, ge=0)

    amount: Optional[float] = Field(None, ge=0)

    notes: Optional[str] = None


class QuotationExtraChargeOut(QuotationExtraChargeCreate):
    id: int
    quotation_id: int

    model_config = {"from_attributes": True}


# =========================================================
# ITEM
# =========================================================


class QuotationItemCreate(BaseModel):

    item_type: Literal[
        "soling", "plum_concrete", "stone_work", "excavation", "rcc", "road_work"
    ]

    title: str

    description: Optional[str] = None

    unit: Optional[str] = None

    rate: float = Field(..., ge=0)

    measurements: list[MeasurementCreate] = []


class QuotationItemUpdate(BaseModel):

    item_type: Optional[
        Literal[
            "soling", "plum_concrete", "stone_work", "excavation", "rcc", "road_work"
        ]
    ] = None

    title: Optional[str] = None

    description: Optional[str] = None

    unit: Optional[str] = None

    rate: Optional[float] = Field(None, ge=0)

    measurements: Optional[list[MeasurementCreate]] = None


class QuotationItemOut(BaseModel):

    id: int

    item_type: str

    title: str

    description: Optional[str]

    unit: Optional[str]

    quantity: float
    rate: float
    amount: float

    measurements: list[MeasurementOut]

    class Config:
        from_attributes = True


# =========================================================
# QUOTATION
# =========================================================


class CreateQuotation(BaseModel):

    client_name: str

    company_name: Optional[str] = None

    mobile_number: str

    email: Optional[str] = None

    billing_address: Optional[str] = None

    site_address: Optional[str] = None

    gst_number: Optional[str] = None

    project_id: Optional[int] = None

    project_name: str

    project_type: str

    project_start_date: Optional[date] = None

    project_end_date: Optional[date] = None

    engineer_name: Optional[str] = None

    work_order_no: Optional[str] = None

    labour_items: list[QuotationLabourCreate] = []

    material_items: list[QuotationMaterialCreate] = []

    gst_percent: float = Field(0, ge=0, le=100)

    cgst_percent: float = Field(0, ge=0, le=100)

    sgst_percent: float = Field(0, ge=0, le=100)

    tds_percent: float = Field(0, ge=0, le=100)

    discount_amount: float = Field(0, ge=0)

    advance_paid: float = Field(0, ge=0)

    payment_mode: Optional[str] = None

    upi_id: Optional[str] = None

    bank_name: Optional[str] = None

    account_holder_name: Optional[str] = None

    account_number: Optional[str] = None

    ifsc_code: Optional[str] = None

    due_date: Optional[date] = None

    notes: Optional[str] = None

    terms_conditions: Optional[str] = None

    items: list[QuotationItemCreate]

    extra_charge_items: list[QuotationExtraChargeCreate] = []

    @field_validator("items")
    @classmethod
    def validate_items(cls, v):

        if not v:
            raise ValueError("At least one item is required")

        return v

    @field_validator("project_end_date")
    @classmethod
    def validate_project_dates(cls, v, info):

        start = info.data.get("project_start_date")

        if start and v and v < start:
            raise ValueError("Project end date cannot be before start date")

        return v

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile_number(cls, v):

        cleaned = v.strip()

        if not cleaned.isdigit():
            raise ValueError(
                "Mobile number must contain digits only"
            )

        if len(cleaned) < 10 or len(cleaned) > 15:
            raise ValueError(
                "Mobile number must be between 10 and 15 digits"
            )

        return cleaned


class UpdateQuotation(BaseModel):

    client_name: Optional[str] = None
    company_name: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[str] = None
    billing_address: Optional[str] = None
    site_address: Optional[str] = None
    cgst_percent: Optional[float] = Field(None, ge=0, le=100)

    sgst_percent: Optional[float] = Field(None, ge=0, le=100)

    tds_percent: Optional[float] = Field(None, ge=0, le=100)

    gst_percent: Optional[float] = Field(None, ge=0, le=100)
    project_id: Optional[int] = None
    project_name: Optional[str] = None
    project_type: Optional[str] = None

    project_start_date: Optional[date] = None
    project_end_date: Optional[date] = None

    engineer_name: Optional[str] = None
    work_order_no: Optional[str] = None

    discount_amount: Optional[float] = Field(None, ge=0)

    advance_paid: Optional[float] = Field(None, ge=0)

    payment_mode: Optional[str] = None

    upi_id: Optional[str] = None

    bank_name: Optional[str] = None

    account_holder_name: Optional[str] = None

    account_number: Optional[str] = None

    ifsc_code: Optional[str] = None

    due_date: Optional[date] = None

    notes: Optional[str] = None

    terms_conditions: Optional[str] = None

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile_number(cls, v):

        cleaned = v.strip()

        if not cleaned.isdigit():
            raise ValueError(
                "Mobile number must contain digits only"
            )

        if len(cleaned) < 10 or len(cleaned) > 15:
            raise ValueError(
                "Mobile number must be between 10 and 15 digits"
            )

        return cleaned

class RejectQuotation(BaseModel):
    reason: str


class QuotationOut(BaseModel):

    id: int

    quotation_no: str

    client_name: str

    company_name: Optional[str]

    mobile_number: str

    email: Optional[str]

    billing_address: Optional[str]

    site_address: Optional[str]

    project_id: Optional[int]

    project_name: str

    project_type: str

    subtotal: float

    gst_percent: float

    gst_amount: float

    cgst_percent: float

    sgst_percent: float

    tds_percent: float

    cgst_amount: float

    sgst_amount: float

    tds_amount: float

    discount_amount: float

    grand_total: float

    advance_paid: float

    balance_due: float

    payment_mode: Optional[str]

    upi_id: Optional[str]

    bank_name: Optional[str]

    account_holder_name: Optional[str]

    account_number: Optional[str]

    ifsc_code: Optional[str]

    due_date: Optional[date]

    is_approved: bool

    status: str

    created_at: datetime

    items: list[QuotationItemOut]

    labour_items: list[QuotationLabourOut]

    material_items: list[QuotationMaterialOut]

    extra_charge_items: list[QuotationExtraChargeOut]

    class Config:
        from_attributes = True


# =========================================================
# QUOTATION CONVERT TO PROJECT
# =========================================================

class QuotationToProjectConvertRequest(BaseModel):
    owner_id: int
    location_type: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    shift_start_time: Optional[time] = None
    shift_end_time: Optional[time] = None
    grace_period_minutes: int = 15

class QuotationToProjectConvertResponse(BaseModel):
    message: str
    project_id: int
    project_business_id: str
    quotation_id: int
    budget_amount: float
