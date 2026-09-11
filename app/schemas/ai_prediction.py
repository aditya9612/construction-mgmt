from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import ConfigDict, Field

from app.schemas.base import BaseSchema


class AIPredictRequest(BaseSchema):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    module_name: str = Field(..., min_length=1, max_length=100)
    prompt: Optional[str] = Field(None, max_length=5000)
    company_id: Optional[int] = Field(None, description="Optional target company ID for Super Admin")


class AIPredictResponse(BaseSchema):
    module_name: str
    prediction: Dict[str, Any]


class AIPredictionUpdate(BaseSchema):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    module_name: Optional[str] = Field(None, min_length=1, max_length=100)
    prompt: Optional[str] = Field(None, max_length=5000)
    prediction: Optional[Dict[str, Any]] = None


class AIPredictionOut(BaseSchema):
    id: int
    module_name: str
    prompt: Optional[str]
    prediction: Dict[str, Any]
    created_by_user_id: Optional[int]
    predicted_at: datetime

