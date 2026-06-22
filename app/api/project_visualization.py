from fastapi import APIRouter, Depends, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import os
import uuid
from typing import List

from app.db.session import get_db_session
from app.models.project_visualization import ProjectVisualization
from app.schemas.project_visualization import VisualizationCreate, VisualizationOut
from app.core import dependencies as d

router = APIRouter(prefix="/projects", tags=["Visualizations"])

UPLOAD_DIR = "uploads/visualizations"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.get("/{id}/visualizations", response_model=List[VisualizationOut])
async def list_visualizations(
    id: int,
    db: AsyncSession = Depends(get_db_session)
):
    query = select(ProjectVisualization).where(ProjectVisualization.project_id == id).order_by(ProjectVisualization.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()

@router.post("/{id}/visualizations", response_model=VisualizationOut)
async def upload_visualization(
    id: int,
    title: str = Form(...),
    points: int = Form(0),
    image_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session)
):
    # 1. Generate Unique ID
    viz_id = f"VIZ-{uuid.uuid4().hex[:4].upper()}"
    
    # 2. Save File
    file_ext = os.path.splitext(image_file.filename)[1]
    file_name = f"{viz_id}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, file_name)
    
    with open(file_path, "wb") as buffer:
        buffer.write(await image_file.read())
    
    image_url = f"/uploads/visualizations/{file_name}"

    # 3. Create DB Record
    viz = ProjectVisualization(
        visualization_id=viz_id,
        project_id=id,
        title=title,
        points=points,
        image_url=image_url
    )
    
    db.add(viz)
    await db.commit()
    await db.refresh(viz)
    
    return viz
