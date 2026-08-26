from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, List
from datetime import datetime
from decimal import Decimal

class PlanOut(BaseModel):
    id: int
    name: str
    code: str
    description: Optional[str] = None
    price: float
    billing_interval: str
    currency: str
    features: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)

class SubscriptionSummaryOut(BaseModel):
    plan_id: Optional[int]
    plan_name: str
    plan_code: str
    status: str
    is_active: bool
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    auto_renew: bool
    max_users: int
    max_projects: int
    storage_gb: int
    advanced_reports: bool
    payroll: bool
    equipment: bool
    ai_features: bool
    features: Dict[str, Any]

class UsageOut(BaseModel):
    users: int
    projects: int
    storage_bytes: float
    storage_gb: float

class UsageLimitsOut(BaseModel):
    entitlements: SubscriptionSummaryOut
    usage: UsageOut

class SubscriptionInvoiceOut(BaseModel):
    id: int
    invoice_number: str
    billing_period_start: Optional[datetime] = None
    billing_period_end: Optional[datetime] = None
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    currency: str
    status: str
    issued_at: Optional[datetime] = None
    due_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, json_encoders={Decimal: float})

class BillingHistoryOut(BaseModel):
    id: int
    action: str
    entity: str
    entity_id: Optional[int] = None
    details: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UPIQRCodeOut(BaseModel):
    transaction_reference: str
    plan_id: int
    plan_name: str
    amount: float
    currency: str
    upi_id: str
    upi_name: str
    upi_uri: str
    qr_code_base64: Optional[str] = None
    status: str

    model_config = ConfigDict(from_attributes=True)


class UPISubmitRequest(BaseModel):
    transaction_reference: str
    utr_reference: str


class UPISubmitResponse(BaseModel):
    transaction_reference: str
    utr_reference: str
    status: str
    amount: float
    currency: str
    submitted_at: datetime
    message: str

    model_config = ConfigDict(from_attributes=True)

