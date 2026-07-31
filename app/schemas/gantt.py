from datetime import date
from typing import List, Optional
from pydantic import BaseModel

class GanttTaskSchema(BaseModel):
    id: str
    type: str = "task"
    name: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    progress: float
    status: str

class GanttMilestoneSchema(BaseModel):
    id: str
    type: str = "milestone"
    name: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    progress: float
    status: str
    children: List[GanttTaskSchema] = []

class GanttResponseSchema(BaseModel):
    project_id: int
    project_name: str
    gantt_items: List[GanttMilestoneSchema] = []
    unassigned_tasks: List[GanttTaskSchema] = []
