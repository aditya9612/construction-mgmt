from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional
from datetime import datetime


class AlertCreate(BaseModel):
    project_id: int
    alert_type: str
    message: str
    user_id: int

    @field_validator("alert_type", "message")
    def validate_fields(cls, v):
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v


class AlertOut(BaseModel):
    id: int
    project_id: int
    alert_type: str
    message: str
    user_id: int
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)