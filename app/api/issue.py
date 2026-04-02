from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from app.db.session import get_db_session
from app.models.issue import Issue
from app.models.project import Project
from app.schemas.issue import IssueCreate, IssueUpdate, IssueOut
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/issues", tags=["Issues"])


@router.post("", response_model=IssueOut)
async def create_issue(
    payload: IssueCreate,
    db: AsyncSession = Depends(get_db_session),
):
    project = await db.get(Project, payload.project_id)
    if not project:
        raise NotFoundError("Project not found")

    if payload.priority not in ["Low", "Medium", "High", "Critical"]:
        raise HTTPException(status_code=400, detail="Invalid priority")

    if payload.reported_date > date.today():
        raise HTTPException(status_code=400, detail="Future date not allowed")

    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    obj = Issue(**payload.model_dump())

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    return IssueOut.model_validate(obj)


@router.get("", response_model=list[IssueOut])
async def list_issues(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Issue))
    rows = result.scalars().all()
    return [IssueOut.model_validate(r) for r in rows]


@router.get("/{id}", response_model=IssueOut)
async def get_issue(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(Issue, id)

    if not obj:
        raise NotFoundError("Issue not found")

    return IssueOut.model_validate(obj)


@router.put("/{id}", response_model=IssueOut)
async def update_issue(
    id: int,
    payload: IssueUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(Issue, id)

    if not obj:
        raise NotFoundError("Issue not found")

    if payload.priority and payload.priority not in [
        "Low",
        "Medium",
        "High",
        "Critical",
    ]:
        raise HTTPException(status_code=400, detail="Invalid priority")

    if payload.reported_date and payload.reported_date > date.today():
        raise HTTPException(status_code=400, detail="Future date not allowed")

    if payload.title is not None and not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    await db.commit()
    await db.refresh(obj)

    return IssueOut.model_validate(obj)


@router.delete("/{id}", status_code=204)
async def delete_issue(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(Issue, id)

    if not obj:
        raise NotFoundError("Issue not found")

    await db.delete(obj)
    await db.commit()

    return None


@router.get("/project/{project_id}")
async def issues_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(select(Issue).where(Issue.project_id == project_id))
    rows = result.scalars().all()

    return [IssueOut.model_validate(r) for r in rows]