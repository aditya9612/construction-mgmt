from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class CADConversionOut(BaseModel):
    id: int
    company_id: Optional[int] = None
    project_name: str
    file_path: str
    area: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)