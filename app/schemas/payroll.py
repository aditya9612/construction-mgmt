from decimal import Decimal
from typing import Optional
from datetime import date
from pydantic import BaseModel, Field, model_validator
from datetime import datetime
from app.core.enums import WagePeriodType

# ================= STAFF SALARY =================
class StaffSalaryProcessRequest(BaseModel):
    user_id: int
    project_id: int
    month_year: str = Field(..., description="Format YYYY-MM")
    gross_salary: Decimal
    deductions: Decimal
    net_salary: Decimal
    payment_mode: str = Field(..., description="cash or bank")
    bank_account_id: Optional[int] = None

class StaffSalaryRegisterOut(BaseModel):
    user_id: int
    full_name: Optional[str]
    role: str
    designation: Optional[str]

# ================= LABOUR PAYROLL =================
class LabourWageGenerateRequest(BaseModel):
    labour_id: int
    project_id: int
    period_type: WagePeriodType
    start_date: date
    end_date: date
    payment_mode: str = Field(..., description="cash or bank")
    bank_account_id: Optional[int] = None
    # the backend will calculate the amount internally

    @model_validator(mode='after')
    def validate_dates(self) -> 'LabourWageGenerateRequest':
        if self.start_date > self.end_date:
            raise ValueError("start_date must be less than or equal to end_date")
        if self.period_type == WagePeriodType.DAILY:
            if self.start_date != self.end_date:
                raise ValueError("For Daily wage, start_date and end_date must be the same")
        return self

# ================= CONTRACTOR PAYROLL =================
class ContractorPayRequest(BaseModel):
    rabill_id: int
    paid_amount: Decimal
    total_deductions: Decimal
    payment_mode: str = Field(..., description="cash or bank")
    bank_account_id: Optional[int] = None

class LabourWageOut(BaseModel):
    id: int
    labour_id: int
    labour_name: Optional[str] = None
    project_id: int
    period_type: str
    start_date: date
    end_date: date
    gross_wage: Decimal
    net_wage: Decimal
    payment_mode: Optional[str] = None
    bank_account_id: Optional[int] = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class LabourWageRegisterOut(BaseModel):
    id: int
    labour_name: Optional[str]
    labour_type: Optional[str]
    period: str
    gross_wage: Decimal
    net_wage: Decimal
    status: str

class LabourWageStatsOut(BaseModel):
    pending_payroll: Decimal
    paid_payroll: Decimal
    advance_given: Decimal
    contractor_payment: Decimal
