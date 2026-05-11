from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class VisualizationBase(BaseModel):
    title: str
    points: int = 0

class VisualizationCreate(VisualizationBase):
    pass

class VisualizationOut(VisualizationBase):
    id: int
    visualization_id: str
    image_url: str
    created_at: datetime

    class Config:
        from_attributes = True
