from pydantic import BaseModel
from datetime import datetime


class CADConversionOut(BaseModel):
    id: int
    project_name: str
    file_path: str
    area: float
    created_at: datetime

    class Config:
        from_attributes = True