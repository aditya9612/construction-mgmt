from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime
from decimal import Decimal

class PaymentVoucherBase(BaseModel):
    payment_date: datetime
    party_type: str = Field(..., description="Vendor or Contractor")
    supplier_id: Optional[int] = None
    contractor_id: Optional[int] = None
    vendor_bill_id: int
    
    base_amount: Decimal = Field(default=Decimal(0))
    gst_amount: Decimal = Field(default=Decimal(0))
    gross_amount: Decimal = Field(default=Decimal(0))
    tds_amount: Decimal = Field(default=Decimal(0))
    retention_amount: Decimal = Field(default=Decimal(0))
    net_payable_amount: Decimal = Field(default=Decimal(0))
    
    payment_method: str
    bank_account_id: int
    reference_no: Optional[str] = None

class PaymentVoucherCreate(PaymentVoucherBase):
    pass

class PaymentVoucherUpdate(BaseModel):
    status: str

class PaymentVoucherOut(PaymentVoucherBase):
    id: int
    payment_voucher_number: str
    status: str
    journal_entry_id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
