from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import os
import uuid
from typing import List

from app.db.session import get_db_session
from app.models.project import Project
from app.models.project_visualization import ProjectVisualization
from app.models.user import User
from app.schemas.project_visualization import VisualizationCreate, VisualizationOut
from app.core.dependencies import get_current_active_user
from app.utils.helpers import NotFoundError

router = APIRouter(prefix="/projects", tags=["Visualizations"])

UPLOAD_DIR = "uploads/visualizations"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.get("/{id}/visualizations", response_model=List[VisualizationOut])
async def list_visualizations(
    id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    project = await db.get(Project, id)
    if not project or (not current_user.is_super_admin and project.company_id != current_user.company_id):
        raise NotFoundError("Project not found")

    query = select(ProjectVisualization).where(ProjectVisualization.project_id == id).order_by(ProjectVisualization.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/{id}/visualizations", response_model=VisualizationOut)
async def upload_visualization(
    id: int,
    title: str = Form(...),
    points: int = Form(0),
    image_file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    project = await db.get(Project, id)
    if not project or (not current_user.is_super_admin and project.company_id != current_user.company_id):
        raise NotFoundError("Project not found")

    file_ext = os.path.splitext(image_file.filename or "")[1].lower()
    if file_ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        raise HTTPException(status_code=400, detail="Only image files (.jpg, .jpeg, .png, .webp) allowed")

    # 1. Generate Unique ID
    viz_id = f"VIZ-{uuid.uuid4().hex[:4].upper()}"
    file_name = f"{viz_id}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, file_name)

    content = await image_file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image file too large (max 10MB)")

    def _save_vis():
        with open(file_path, "wb") as buffer:
            buffer.write(content)

    await run_in_threadpool(_save_vis)

    image_url = f"/uploads/visualizations/{file_name}"

    # 3. Create DB Record
    viz = ProjectVisualization(
        visualization_id=viz_id,
        project_id=id,
        title=title,
        points=points,
        image_url=image_url,
    )

    db.add(viz)
    await db.commit()
    await db.refresh(viz)

    return viz
