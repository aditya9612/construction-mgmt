from decimal import Decimal
from typing import Optional
from datetime import date
from pydantic import BaseModel, Field

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
    start_date: date
    end_date: date
    payment_mode: str = Field(..., description="cash or bank")
    bank_account_id: Optional[int] = None
    # the backend will calculate the amount internally

# ================= CONTRACTOR PAYROLL =================
class ContractorPayRequest(BaseModel):
    rabill_id: int
    paid_amount: Decimal
    total_deductions: Decimal
    payment_mode: str = Field(..., description="cash or bank")
    bank_account_id: Optional[int] = None
