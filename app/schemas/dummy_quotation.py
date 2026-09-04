from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime

# =========================================================
# MEASUREMENT
# =========================================================

class DummyMeasurementCreate(BaseModel):
    length: Optional[float] = Field(None, ge=0)
    width: Optional[float] = Field(None, ge=0)
    height: Optional[float] = Field(None, ge=0)
    unit: Optional[str] = "ft"

class DummyMeasurementOut(BaseModel):
    id: int
    length: Optional[float]
    width: Optional[float]
    height: Optional[float]
    unit: Optional[str]
    cubic_feet: float
    cubic_meter: float
    brass: float
    quantity: float
    formula_used: Optional[str]

    class Config:
        from_attributes = True

# =========================================================
# ITEM
# =========================================================

class DummyQuotationItemCreate(BaseModel):
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    unit: Optional[str] = None
    rate: float = Field(0, ge=0)
    measurements: List[DummyMeasurementCreate] = []

class DummyQuotationItemOut(BaseModel):
    id: int
    title: str
    description: Optional[str]
    unit: Optional[str]
    quantity: float
    rate: float
    amount: float
    measurements: List[DummyMeasurementOut]

    class Config:
        from_attributes = True

# =========================================================
# DUMMY QUOTATION
# =========================================================

class CreateDummyQuotation(BaseModel):
    client_name: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[EmailStr] = None
    billing_address: Optional[str] = None
    gst_number: Optional[str] = None
    
    gst_percent: float = Field(0, ge=0, le=100)
    cgst_percent: float = Field(0, ge=0, le=100)
    sgst_percent: float = Field(0, ge=0, le=100)
    
    notes: Optional[str] = None
    
    items: List[DummyQuotationItemCreate] = []


class UpdateDummyQuotation(BaseModel):
    client_name: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[EmailStr] = None
    billing_address: Optional[str] = None
    gst_number: Optional[str] = None
    
    gst_percent: Optional[float] = Field(None, ge=0, le=100)
    cgst_percent: Optional[float] = Field(None, ge=0, le=100)
    sgst_percent: Optional[float] = Field(None, ge=0, le=100)
    
    notes: Optional[str] = None


class DummyQuotationOut(BaseModel):
    id: int
    dummy_quotation_no: str
    company_id: Optional[int]
    
    client_name: Optional[str]
    mobile_number: Optional[str]
    email: Optional[str]
    billing_address: Optional[str]
    gst_number: Optional[str]
    
    subtotal: float
    gst_percent: float
    cgst_percent: float
    sgst_percent: float
    cgst_amount: float
    sgst_amount: float
    grand_total: float
    
    notes: Optional[str]
    
    created_at: datetime
    
    items: List[DummyQuotationItemOut]

    class Config:
        from_attributes = True
