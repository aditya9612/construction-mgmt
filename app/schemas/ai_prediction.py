from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from app.schemas.base import BaseSchema


class AIPredictRequest(BaseSchema):
    module_name: str
    prompt: Optional[str] = None


class AIPredictResponse(BaseSchema):
    module_name: str
    prediction: Dict[str, Any]


class AIPredictionOut(BaseSchema):
    id: int
    module_name: str
    prompt: Optional[str]
    prediction: Dict[str, Any]
    created_by_user_id: Optional[int]
    predicted_at: datetime
