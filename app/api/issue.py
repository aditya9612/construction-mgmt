from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.issue import Issue
from app.models.project import Project
from app.schemas.issue import IssueCreate, IssueUpdate, IssueOut
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/issues", tags=["Issues"])


# -------------------------
# CREATE
# -------------------------
@router.post("", response_model=IssueOut)
async def create_issue(
    payload: IssueCreate,
    db: AsyncSession = Depends(get_db_session),
):
    project = await db.get(Project, payload.project_id)
    if not project:
        raise NotFoundError("Project not found")

    obj = Issue(**payload.model_dump())

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    return IssueOut.model_validate(obj)


# -------------------------
# LIST
# -------------------------
@router.get("", response_model=list[IssueOut])
async def list_issues(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Issue))
    rows = result.scalars().all()
    return [IssueOut.model_validate(r) for r in rows]


# -------------------------
# GET
# -------------------------
@router.get("/{id}", response_model=IssueOut)
async def get_issue(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(Issue, id)

    if not obj:
        raise NotFoundError("Issue not found")

    return IssueOut.model_validate(obj)


# -------------------------
# UPDATE
# -------------------------
@router.put("/{id}", response_model=IssueOut)
async def update_issue(
    id: int,
    payload: IssueUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(Issue, id)

    if not obj:
        raise NotFoundError("Issue not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    await db.commit()
    await db.refresh(obj)

    return IssueOut.model_validate(obj)


# -------------------------
# DELETE
# -------------------------
@router.delete("/{id}", status_code=204)
async def delete_issue(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(Issue, id)

    if not obj:
        raise NotFoundError("Issue not found")

    await db.delete(obj)
    await db.commit()

    return None


# -------------------------
# BY PROJECT
# -------------------------
@router.get("/project/{project_id}")
async def issues_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(Issue).where(Issue.project_id == project_id)
    )
    rows = result.scalars().all()

    return [IssueOut.model_validate(r) for r in rows]