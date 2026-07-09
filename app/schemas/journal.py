from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import date, datetime
from decimal import Decimal
from app.schemas.accountant import JournalLineCreate

class JournalLineOut(BaseModel):
    id: int
    entry_id: int
    account_id: int
    debit: Decimal
    credit: Decimal

    class Config:
        from_attributes = True

class JournalEntryOut(BaseModel):
    id: int
    description: str
    amount: Decimal
    created_at: datetime
    lines: List[JournalLineOut] = []

    class Config:
        from_attributes = True

class JournalManualCreate(BaseModel):
    entry_date: date
    description: str
    lines: List[JournalLineCreate]

class JournalAdjustmentCreate(BaseModel):
    entry_date: date
    description: str
    lines: List[JournalLineCreate]

class RecurringJournalCreate(BaseModel):
    template_name: str
    frequency: str
    next_run_date: date
    template_data: Any

class RecurringJournalOut(BaseModel):
    id: int
    template_name: str
    frequency: str
    next_run_date: date
    status: str
    template_data: Any
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class JournalEntryExtendedOut(BaseModel):
    id: int
    journal_number: Optional[str] = None
    entry_date: Optional[date] = None
    description: Optional[str] = None
    status: str
    entry_type: str
    created_by: Optional[int] = None
    created_at: datetime
    lines: List[JournalLineOut] = []

    class Config:
        from_attributes = True
