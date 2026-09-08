from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import mimetypes
import pathlib, re, io, os, uuid
from ezdxf import colors
from openpyxl import Workbook
from typing import Annotated, List, Optional, Union
from fastapi import APIRouter, Body, Depends, Path, Query, Request, Form
from openpyxl.utils import get_column_letter
from starlette.concurrency import run_in_threadpool
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.enums import (
    PRIORITY_MAP,
    REVERSE_PRIORITY_MAP,
    DocumentStatus,
    LabourStatus,
    MilestoneStatus,
    ProjectStatus,
    SkillType,
    TaskPriority,
    TaskStatus,
    WorkActivityStatus,
)
from app.models.boq import BOQ
from app.models.work_order import WorkOrder
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.core.validators import validate_drawing_file
from app.db.session import get_db_session
from sqlalchemy.orm import selectinload
import traceback
from app.models.approval import Approval
from app.models.labour import Labour
from app.models.user import UserAttendance, ActivityLog
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from fastapi import APIRouter, Depends, Query, Request, Form, status, HTTPException
from app.cache.redis import (
    bump_cache_version,
    cache_get_json,
    cache_set_json,
    get_cache_version,
)
from sqlalchemy.orm import aliased
from PIL import Image
from app.core.dependencies import (
    get_current_active_user,
    get_request_redis,
    require_roles,
    require_permission,
)
from app.utils.common import assert_project_access, assert_task_project
import shutil
import uuid
import os
from app.services.notification_service import create_notification
from app.models.contractor import Contractor
from sqlalchemy import and_, case, delete, select, func, or_, update
from app.models import project as m
from app.models.user import User, UserRole
from app.models.owner import Owner
from app.models.expense import Expense
from app.models.invoice import Invoice
from app.models.master_data import ActivityType, LabourType
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas import project as s
from app.core.logger import logger
from fastapi.responses import FileResponse, StreamingResponse
from reportlab.platypus import (
    PageBreak,
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table as PdfTable,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors as pdf_colors
from sqlalchemy.exc import IntegrityError
from fastapi import UploadFile, File
from app.utils.helpers import (
    AppError,
    BadRequestError,
    DataIntegrityError,
    ForbiddenError,
    NotFoundError,
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from app.utils.pagination import PaginationParams
from app.utils.common import (
    assert_project_access,
    generate_business_id,
    assert_task_project,
)
from app.utils.qr import generate_qr
from app.models.labour import LabourProject

def compute_project_status(project):
    today = date.today()

    if project.status == s.ProjectStatus.COMPLETED:
        return "Completed"

    if project.status == s.ProjectStatus.ON_HOLD:
        return "On Hold"

    if project.status == s.ProjectStatus.PLANNED:
        return "Planned"

    if (
        project.status == s.ProjectStatus.ONGOING
        and project.end_date
        and today > project.end_date
    ):
        return "Delayed"

    return "Ongoing"


def compute_milestone_status(milestone):
    today = date.today()

    if milestone.status == MilestoneStatus.COMPLETED:
        return "Completed"

    tasks = getattr(milestone, "__dict__", {}).get("tasks", []) or []
    if tasks and all(getattr(t, "status", None) == TaskStatus.COMPLETED for t in tasks):
        return "Completed"

    if milestone.status == MilestoneStatus.PLANNED:
        if milestone.end_date and today > milestone.end_date:
            return "Delayed"
        return "Planned"

    if milestone.status == MilestoneStatus.DELAYED:
        return "Delayed"

    if milestone.status == MilestoneStatus.IN_PROGRESS:
        if milestone.end_date and today > milestone.end_date:
            return "Delayed"
        return "In Progress"

    if milestone.actual_end_date:
        return "Completed"

    if milestone.end_date and today > milestone.end_date:
        return "Delayed"

    if milestone.actual_start_date:
        return "In Progress"

    return "Planned"


def serialize_milestone(obj: m.Milestone) -> s.MilestoneOut:
    return s.MilestoneOut(
        id=obj.id,
        project_id=obj.project_id,
        title=obj.title,
        status=compute_milestone_status(obj),
        description=obj.description,
        start_date=obj.start_date,
        end_date=obj.end_date,
        actual_start_date=obj.actual_start_date,
        actual_end_date=obj.actual_end_date,
        total_tasks=obj.total_tasks,
        completed_tasks=obj.completed_tasks,
        pending_tasks=obj.pending_tasks,
        delayed_tasks=obj.delayed_tasks,
        is_delayed=obj.is_delayed,
        completion_percentage=obj.completion_percentage,
        execution_completion_percentage=obj.execution_completion_percentage,
    )


def get_pagination(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset, search=search).normalized()


def _check_batch_y_tenant_access(current_user: User) -> None:
    is_sa = getattr(current_user, "is_super_admin", False) is True

    if not is_sa and getattr(current_user, "company_id", None) is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any tenant company",
        )


async def _get_scoped_project(
    db: AsyncSession,
    project_id: int,
    current_user: User,
    *,
    for_update: bool = False,
    load_relations: bool = True,
) -> m.Project:
    _check_batch_y_tenant_access(current_user)
    query = select(m.Project).where(m.Project.id == project_id)
    if getattr(current_user, "is_super_admin", False) is not True:
        query = query.where(m.Project.company_id == current_user.company_id)
    if load_relations:
        query = query.options(
            selectinload(m.Project.milestones).selectinload(m.Milestone.tasks),
            selectinload(m.Project.tasks),
        )
    if for_update:
        query = query.with_for_update()
    project = await db.scalar(query)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _get_scoped_milestone(
    db: AsyncSession,
    project_id: int,
    milestone_id: int,
    current_user: User,
    *,
    for_update: bool = False,
) -> m.Milestone:
    await _get_scoped_project(db, project_id, current_user, load_relations=False)
    query = select(m.Milestone).where(
        m.Milestone.id == milestone_id,
        m.Milestone.project_id == project_id,
    )
    if for_update:
        query = query.with_for_update()
    else:
        query = query.options(selectinload(m.Milestone.tasks))
    milestone = await db.scalar(query)
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return milestone


async def _get_scoped_task(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    current_user: User,
    *,
    for_update: bool = False,
) -> m.Task:
    await _get_scoped_project(db, project_id, current_user, load_relations=False)
    query = (
        select(m.Task)
        .options(selectinload(m.Task.assignments).joinedload(m.TaskAssignment.user))
        .where(
            m.Task.id == task_id,
            m.Task.project_id == project_id,
        )
    )
    if for_update:
        query = query.with_for_update()
    task = await db.scalar(query)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def _get_scoped_task_request(
    db: AsyncSession,
    request_id: int,
    current_user: User,
    *,
    for_update: bool = False,
) -> m.TaskRequest:
    _check_batch_y_tenant_access(current_user)
    query = (
        select(m.TaskRequest)
        .join(m.Project, m.TaskRequest.project_id == m.Project.id)
        .where(
            m.TaskRequest.id == request_id,
            m.TaskRequest.is_deleted == False,
        )
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        query = query.where(m.Project.company_id == current_user.company_id)
    if for_update:
        query = query.with_for_update()
    req = await db.scalar(query)
    if not req:
        raise HTTPException(status_code=404, detail="Task request not found")
    return req


async def _get_scoped_issue(
    db: AsyncSession,
    issue_id: int,
    current_user: User,
    *,
    for_update: bool = False,
) -> m.Issue:
    _check_batch_y_tenant_access(current_user)
    query = (
        select(m.Issue)
        .join(m.Project, m.Issue.project_id == m.Project.id)
        .where(m.Issue.id == issue_id)
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        query = query.where(m.Project.company_id == current_user.company_id)
    if for_update:
        query = query.with_for_update()
    issue = await db.scalar(query)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue


async def _get_scoped_dsr(
    db: AsyncSession,
    dsr_id: int,
    current_user: User,
    *,
    for_update: bool = False,
    load_relations: bool = False,
) -> m.DailySiteReport:
    _check_batch_y_tenant_access(current_user)
    query = (
        select(m.DailySiteReport)
        .join(m.Project, m.DailySiteReport.project_id == m.Project.id)
        .where(m.DailySiteReport.id == dsr_id)
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        query = query.where(m.Project.company_id == current_user.company_id)
    if load_relations:
        query = query.options(
            selectinload(m.DailySiteReport.contractor),
            selectinload(m.DailySiteReport.created_by),
        )
    if for_update:
        query = query.with_for_update()
    dsr = await db.scalar(query)
    if not dsr:
        raise HTTPException(status_code=404, detail="DSR not found")
    return dsr


async def _get_scoped_site_request(
    db: AsyncSession,
    request_id: int,
    current_user: User,
    *,
    for_update: bool = False,
) -> m.SiteRequest:
    _check_batch_y_tenant_access(current_user)
    query = (
        select(m.SiteRequest)
        .join(m.Project, m.SiteRequest.project_id == m.Project.id)
        .where(m.SiteRequest.id == request_id)
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        query = query.where(m.Project.company_id == current_user.company_id)
    if for_update:
        query = query.with_for_update()
    req = await db.scalar(query)
    if not req:
        raise HTTPException(status_code=404, detail="Site request not found")
    return req


async def _get_scoped_site_photo(
    db: AsyncSession,
    photo_id: int,
    current_user: User,
    *,
    for_update: bool = False,
) -> m.SitePhoto:
    _check_batch_y_tenant_access(current_user)
    query = (
        select(m.SitePhoto)
        .join(m.Project, m.SitePhoto.project_id == m.Project.id)
        .where(m.SitePhoto.id == photo_id)
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        query = query.where(m.Project.company_id == current_user.company_id)
    if for_update:
        query = query.with_for_update()
    photo = await db.scalar(query)
    if not photo:
        raise HTTPException(status_code=404, detail="Site photo not found")
    return photo



router = APIRouter(
    prefix="/projects",
    tags=["project_management"],
    dependencies=[default_rate_limiter_dependency()],
)


VERSION_KEY = "cache_version:projects"

PROJECT_WRITE_ROLES = [r.value for r in [UserRole.ADMIN, UserRole.PROJECT_MANAGER]]
PROJECT_DELETE_ROLES = [UserRole.ADMIN.value]

TASK_WRITE_ROLES = [
    r.value for r in [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER]
]
TASK_DELETE_ROLES = [r.value for r in [UserRole.ADMIN, UserRole.PROJECT_MANAGER]]

TASK_REQUEST_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.LABOUR,
    ]
]

DSR_WRITE_ROLES = [
    r.value for r in [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER]
]
DSR_READ_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.CLIENT,
    ]
]
DSR_DELETE_ROLES = [UserRole.ADMIN.value]
DSR_APPROVE_ROLES = [
    r.value for r in [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.CLIENT]
]

ISSUE_CREATE_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.CLIENT,
    ]
]
ISSUE_UPDATE_ROLES = [r.value for r in [UserRole.ADMIN, UserRole.PROJECT_MANAGER]]
ISSUE_DELETE_ROLES = [UserRole.ADMIN.value]

FINANCIAL_ROLES = [
    r.value for r in [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT]
]

READ_ROLES = [r.value for r in UserRole]

DRAWING_WRITE_ROLES = TASK_WRITE_ROLES
DRAWING_READ_ROLES = READ_ROLES

DRAWING_DELETE_ROLES = [
    UserRole.ADMIN.value,
    UserRole.PROJECT_MANAGER.value,
]


class ProjectsRepository:
    async def create_project(self, db: AsyncSession, data: dict) -> m.Project:
        obj = m.Project(**data)
        db.add(obj)
        await db.flush()
        return obj

    async def get_project(
        self, db: AsyncSession, project_id: int
    ) -> Optional[m.Project]:
        return await db.scalar(
            select(m.Project)
            .options(
                selectinload(m.Project.milestones).selectinload(m.Milestone.tasks),
                selectinload(m.Project.tasks),
            )
            .where(m.Project.id == project_id)
        )

    async def list_projects(
        self,
        db: AsyncSession,
        *,
        limit: int,
        offset: int,
        search: Optional[str] = None,
        status: Optional[s.ProjectStatus] = None,
    ) -> tuple[list[m.Project], int]:
        query = select(m.Project).options(
            selectinload(m.Project.milestones).selectinload(m.Milestone.tasks),
            selectinload(m.Project.tasks),
        )
        count_query = select(func.count()).select_from(m.Project)

        if search:
            like = f"%{search}%"
            query = query.where(m.Project.project_name.ilike(like))
            count_query = count_query.where(m.Project.project_name.ilike(like))

        if status:
            query = query.where(m.Project.status == status)
            count_query = count_query.where(m.Project.status == status)

        query = query.order_by(m.Project.id.desc()).limit(limit).offset(offset)

        total = await db.scalar(count_query)
        rows = (await db.execute(query)).scalars().all()
        return rows, int(total or 0)

    async def update_project(
        self, db: AsyncSession, obj: m.Project, data: dict
    ) -> m.Project:
        for k, v in data.items():
            setattr(obj, k, v)
        await db.flush()
        return obj

    async def delete_project(self, db: AsyncSession, obj: m.Project) -> None:
        await db.delete(obj)
        await db.flush()


class ProjectMembersRepository:
    async def get_member(
        self, db: AsyncSession, *, project_id: int, user_id: int
    ) -> Optional[m.ProjectMember]:
        return await db.scalar(
            select(m.ProjectMember).where(
                m.ProjectMember.project_id == project_id,
                m.ProjectMember.user_id == user_id,
            )
        )

    async def assign_member(
        self, db: AsyncSession, *, project_id: int, user_id: int
    ) -> m.ProjectMember:
        obj = m.ProjectMember(project_id=project_id, user_id=user_id)
        db.add(obj)
        await db.flush()
        return obj

    async def remove_member(
        self, db: AsyncSession, *, project_id: int, user_id: int
    ) -> None:
        obj = await self.get_member(db, project_id=project_id, user_id=user_id)
        if obj is None:
            return
        await db.delete(obj)
        await db.flush()

    async def is_member(
        self, db: AsyncSession, *, project_id: int, user_id: int
    ) -> bool:
        exists_query = (
            select(func.count())
            .select_from(m.ProjectMember)
            .where(
                m.ProjectMember.project_id == project_id,
                m.ProjectMember.user_id == user_id,
            )
        )
        count = await db.scalar(exists_query)
        return bool(count and int(count) > 0)

    async def list_members(
        self,
        db: AsyncSession,
        *,
        project_id: int,
        limit: int,
        offset: int,
    ) -> tuple[list[User], int]:
        from app.models.user import User  # local import to avoid circulars

        count_query = (
            select(func.count())
            .select_from(m.ProjectMember)
            .where(m.ProjectMember.project_id == project_id)
        )

        query = (
            select(User)
            .join(m.ProjectMember, m.ProjectMember.user_id == User.id)
            .where(m.ProjectMember.project_id == project_id)
            .order_by(User.id.desc())
            .limit(limit)
            .offset(offset)
        )

        total = await db.scalar(count_query)
        rows = (await db.execute(query)).scalars().all()
        return rows, int(total or 0)


class MilestonesRepository:
    async def create_milestone(
        self, db: AsyncSession, *, project_id: int, data: dict
    ) -> m.Milestone:
        obj = m.Milestone(project_id=project_id, **data)
        db.add(obj)
        await db.flush()
        return obj

    async def get_milestone(
        self, db: AsyncSession, *, project_id: int, milestone_id: int
    ) -> Optional[m.Milestone]:
        from sqlalchemy.orm import selectinload

        return await db.scalar(
            select(m.Milestone)
            .where(m.Milestone.project_id == project_id, m.Milestone.id == milestone_id)
            .options(selectinload(m.Milestone.tasks))
        )

    async def list_milestones(
        self,
        db: AsyncSession,
        *,
        project_id: int,
        limit: int,
        offset: int,
    ) -> tuple[list[m.Milestone], int]:

        count_query = select(func.count()).where(m.Milestone.project_id == project_id)

        from sqlalchemy.orm import selectinload

        query = (
            select(m.Milestone)
            .where(m.Milestone.project_id == project_id)
            .order_by(m.Milestone.id.desc())
            .limit(limit)
            .offset(offset)
            .options(selectinload(m.Milestone.tasks))
        )

        total = await db.scalar(count_query)
        rows = (await db.execute(query)).scalars().all()

        return rows, int(total or 0)

    async def update_milestone(
        self, db: AsyncSession, *, obj: m.Milestone, data: dict
    ) -> m.Milestone:
        for k, v in data.items():
            setattr(obj, k, v)
        await db.flush()
        return obj

    async def delete_milestone(self, db: AsyncSession, *, obj: m.Milestone) -> None:
        await db.delete(obj)
        await db.flush()


class TasksRepository:
    async def create_task(
        self, db: AsyncSession, *, project_id: int, data: dict
    ) -> m.Task:
        obj = m.Task(project_id=project_id, **data)
        db.add(obj)
        await db.flush()
        return obj

    async def get_task(
        self, db: AsyncSession, *, project_id: int, task_id: int
    ) -> Optional[m.Task]:
        from sqlalchemy.orm import selectinload, joinedload

        return await db.scalar(
            select(m.Task)
            .options(selectinload(m.Task.assignments).joinedload(m.TaskAssignment.user))
            .where(m.Task.project_id == project_id, m.Task.id == task_id)
        )

    async def list_tasks(
        self,
        db: AsyncSession,
        *,
        project_id: int,
        status: Optional[s.TaskStatus],
        assigned_user_id: Optional[int],
        limit: int,
        offset: int,
    ) -> tuple[list[m.Task], int]:
        from sqlalchemy.orm import selectinload, joinedload

        query = (
            select(m.Task)
            .options(selectinload(m.Task.assignments).joinedload(m.TaskAssignment.user))
            .where(m.Task.project_id == project_id)
        )
        count_query = (
            select(func.count(m.Task.id.distinct()))
            .select_from(m.Task)
            .where(m.Task.project_id == project_id)
        )

        if status is not None:
            query = query.where(m.Task.status == status)
            count_query = count_query.where(m.Task.status == status)

        if assigned_user_id is not None:
            query = query.where(
                m.Task.assignments.any(m.TaskAssignment.user_id == assigned_user_id)
            )
            count_query = count_query.where(
                m.Task.assignments.any(m.TaskAssignment.user_id == assigned_user_id)
            )

        query = query.order_by(m.Task.id.desc()).limit(limit).offset(offset)

        total = await db.scalar(count_query)
        rows = (await db.execute(query)).scalars().all()
        return rows, int(total or 0)

    async def update_task(self, db: AsyncSession, *, obj: m.Task, data: dict) -> m.Task:
        for k, v in data.items():
            setattr(obj, k, v)
        await db.flush()
        return obj

    async def delete_task(self, db: AsyncSession, *, obj: m.Task) -> None:
        await db.delete(obj)
        await db.flush()

    async def list_task_completion_by_project_ids(
        self, db: AsyncSession, project_ids: list[int]
    ) -> list[tuple[int, int]]:
        if not project_ids:
            return []

        query = select(m.Task.project_id, m.Task.completion_percentage).where(
            m.Task.project_id.in_(project_ids)
        )
        rows = (await db.execute(query)).all()
        return [(int(pid), int(pct)) for pid, pct in rows]


class TaskProgressRepository:
    async def create_progress(
        self,
        db: AsyncSession,
        *,
        task_id: int,
        percentage: int,
        remarks: Optional[str],
        created_by_user_id: Optional[int],
    ) -> m.TaskProgress:
        obj = m.TaskProgress(
            task_id=task_id,
            percentage=percentage,
            remarks=remarks,
            created_by_user_id=created_by_user_id,
        )
        db.add(obj)
        await db.flush()
        return obj

    async def list_progress_history(
        self,
        db: AsyncSession,
        *,
        task_id: int,
        limit: int,
        offset: int,
    ) -> tuple[list[m.TaskProgress], int]:
        count_query = (
            select(func.count())
            .select_from(m.TaskProgress)
            .where(m.TaskProgress.task_id == task_id)
        )
        query = (
            select(m.TaskProgress)
            .where(m.TaskProgress.task_id == task_id)
            .order_by(m.TaskProgress.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        total = await db.scalar(count_query)
        rows = (await db.execute(query)).scalars().all()
        return rows, int(total or 0)


class CommentsRepository:
    async def create_comment(
        self,
        db: AsyncSession,
        *,
        task_id: int,
        author_user_id: int,
        content: str,
    ) -> m.Comment:
        obj = m.Comment(task_id=task_id, author_user_id=author_user_id, content=content)
        db.add(obj)
        await db.flush()
        return obj

    async def list_comments(
        self,
        db: AsyncSession,
        *,
        task_id: int,
        limit: int,
        offset: int,
    ) -> tuple[list[m.Comment], int]:
        count_query = (
            select(func.count())
            .select_from(m.Comment)
            .where(m.Comment.task_id == task_id)
        )
        query = (
            select(m.Comment)
            .where(m.Comment.task_id == task_id)
            .order_by(m.Comment.id.desc())
            .limit(limit)
            .offset(offset)
        )
        total = await db.scalar(count_query)
        rows = (await db.execute(query)).scalars().all()
        return rows, int(total or 0)


class ProjectsService:
    def __init__(
        self,
        projects_repo: ProjectsRepository,
        tasks_repo: TasksRepository,
    ) -> None:
        self.projects_repo = projects_repo
        self.tasks_repo = tasks_repo

    async def _compute_completion_percentage_by_project_ids(
        self, db: AsyncSession, project_ids: list[int]
    ) -> dict[int, float]:
        completion_rows = await self.tasks_repo.list_task_completion_by_project_ids(
            db, project_ids
        )
        completion_map: dict[int, list[int]] = {pid: [] for pid in project_ids}
        for pid, pct in completion_rows:
            completion_map[pid].append(pct)

        out: dict[int, float] = {}
        for pid in project_ids:
            values = completion_map.get(pid) or []
            out[pid] = (float(sum(values)) / len(values)) if values else 0.0
        return out

    async def create_project(
        self, db: AsyncSession, current_user: User, payload: s.ProjectCreate
    ) -> s.ProjectOut:
        _check_batch_y_tenant_access(current_user)

        if current_user.company_id:
            from app.services.entitlement import get_entitlement_service
            entitlement_service = get_entitlement_service()
            await entitlement_service.assert_can_create_project(db, current_user.company_id)

        data = payload.model_dump(exclude_unset=True)
        if "status" not in data:
            data["status"] = s.ProjectStatus.PLANNED

        owner_query = select(Owner).where(Owner.id == payload.owner_id)
        if getattr(current_user, "is_super_admin", False) is not True:
            owner_query = owner_query.where(Owner.company_id == current_user.company_id)
        owner = await db.scalar(owner_query)
        if not owner:
            raise NotFoundError("Owner not found")

        if payload.start_date and payload.end_date:
            if payload.end_date < payload.start_date:
                raise ValidationError("end_date cannot be before start_date")

        for attempt in range(3):
            try:
                data["business_id"] = await generate_business_id(
                    db, m.Project, "business_id", "PRJ"
                )
                data["company_id"] = current_user.company_id

                obj = await self.projects_repo.create_project(db, data)

                db.add(
                    ActivityLog(
                        action="CREATE_PROJECT",
                        entity="project",
                        entity_id=obj.id,
                        performed_by=current_user.id,
                        details={"message": f"Project '{obj.project_name}' created"},
                    )
                )
                await db.flush()
                break

            except IntegrityError as e:
                await db.rollback()
                if "project_name" in str(e.orig):
                    raise ConflictError("Project with this name already exists")
                continue
            except Exception as exc:
                await db.rollback()
                logger.warning(f"Project creation retry attempt {attempt} failed: {exc}")
                import uuid
                data["business_id"] = f"PRJ-{uuid.uuid4().hex[:6].upper()}"
                try:
                    data["company_id"] = current_user.company_id
                    obj = await self.projects_repo.create_project(db, data)
                    db.add(
                        ActivityLog(
                            action="CREATE_PROJECT",
                            entity="project",
                            entity_id=obj.id,
                            performed_by=current_user.id,
                            details={"message": f"Project '{obj.project_name}' created"},
                        )
                    )
                    await db.flush()
                    break
                except Exception as inner_exc:
                    logger.exception(f"Fallback project creation failed: {inner_exc}")
                    raise
        else:
            raise AppError(status_code=400, message="Could not create project due to conflicting data.")

        completion_map = await self._compute_completion_percentage_by_project_ids(
            db, [obj.id]
        )
        completion = completion_map.get(obj.id, 0.0)
        return s.ProjectOut(
            id=obj.id,
            business_id=obj.business_id,
            project_name=obj.project_name,
            owner_id=obj.owner_id,
            description=obj.description,
            start_date=obj.start_date,
            end_date=obj.end_date,
            status=compute_project_status(obj),
            completion_percentage=completion,
            type=obj.type,
            location_type=obj.location_type,
            site_address=obj.site_address,
            city=obj.city,
            state=obj.state,
            country=obj.country,
            pincode=obj.pincode,
            latitude=obj.latitude,
            longitude=obj.longitude,
            shift_start_time=obj.shift_start_time,
            shift_end_time=obj.shift_end_time,
            grace_period_minutes=obj.grace_period_minutes,
            quotation_id=obj.quotation_id,
            budget_amount=float(obj.budget_amount),
        )

    async def list_projects(
        self,
        db: AsyncSession,
        *,
        current_user: User,
        limit: int,
        offset: int,
        search: Optional[str] = None,
        status: Optional[s.ProjectStatus] = None,
    ) -> PaginatedResponse[s.ProjectOut]:
        _check_batch_y_tenant_access(current_user)

        base_query = select(m.Project)
        if getattr(current_user, "is_super_admin", False) is not True:
            base_query = base_query.where(m.Project.company_id == current_user.company_id)
        elif not current_user.company_id:
            base_query = base_query.where(m.Project.company_id == None)

        if search:
            base_query = base_query.where(
                m.Project.project_name.ilike(f"%{search.strip()}%")
            )

        today = date.today()

        if status:

            if status == s.ProjectStatus.DELAYED:

                base_query = base_query.where(
                    m.Project.status == s.ProjectStatus.ONGOING,
                    m.Project.end_date.is_not(None),
                    m.Project.end_date < today,
                )

            else:
                base_query = base_query.where(m.Project.status == status)

        count_query = select(func.count()).select_from(
            base_query.order_by(None).subquery()
        )
        total = await db.scalar(count_query)

        from sqlalchemy.orm import selectinload

        query = (
            base_query.order_by(m.Project.id.desc())
            .limit(limit)
            .offset(offset)
            .options(
                selectinload(m.Project.milestones).selectinload(m.Milestone.tasks),
                selectinload(m.Project.tasks),
            )
        )

        rows = (await db.execute(query)).scalars().all()

        project_ids = [p.id for p in rows]
        completion_map = await self._compute_completion_percentage_by_project_ids(
            db, project_ids
        )

        items = [
            s.ProjectOut(
                id=p.id,
                business_id=p.business_id,
                project_name=p.project_name,
                owner_id=p.owner_id,
                description=p.description,
                start_date=p.start_date,
                end_date=p.end_date,
                status=compute_project_status(p),
                completion_percentage=completion_map.get(p.id, 0.0),
                execution_completion_percentage=p.execution_completion_percentage,
                total_milestones=p.total_milestones,
                total_tasks=p.total_tasks,
                completed_tasks=p.completed_tasks,
                delayed_tasks=p.delayed_tasks,
                type=p.type,
                location_type=p.location_type,
                site_address=p.site_address,
                city=p.city,
                state=p.state,
                country=p.country,
                pincode=p.pincode,
                latitude=p.latitude,
                longitude=p.longitude,
                shift_start_time=p.shift_start_time,
                shift_end_time=p.shift_end_time,
                grace_period_minutes=p.grace_period_minutes,
                quotation_id=p.quotation_id,
                budget_amount=float(p.budget_amount),
            )
            for p in rows
        ]

        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)

        return PaginatedResponse[s.ProjectOut](items=items, meta=meta)

    async def get_project(
        self,
        db: AsyncSession,
        project_id: int,
        current_user: User,
    ) -> s.ProjectOut:
        obj = await _get_scoped_project(db, project_id, current_user)

        completion_map = await self._compute_completion_percentage_by_project_ids(
            db, [obj.id]
        )
        completion = completion_map.get(obj.id, 0.0)

        return s.ProjectOut(
            id=obj.id,
            business_id=obj.business_id,
            project_name=obj.project_name,
            owner_id=obj.owner_id,
            description=obj.description,
            start_date=obj.start_date,
            end_date=obj.end_date,
            status=compute_project_status(obj),
            completion_percentage=completion,
            execution_completion_percentage=obj.execution_completion_percentage,
            total_milestones=obj.total_milestones,
            total_tasks=obj.total_tasks,
            completed_tasks=obj.completed_tasks,
            delayed_tasks=obj.delayed_tasks,
            type=obj.type,
            location_type=obj.location_type,
            site_address=obj.site_address,
            city=obj.city,
            state=obj.state,
            country=obj.country,
            pincode=obj.pincode,
            latitude=obj.latitude,
            longitude=obj.longitude,
            shift_start_time=obj.shift_start_time,
            shift_end_time=obj.shift_end_time,
            grace_period_minutes=obj.grace_period_minutes,
            quotation_id=obj.quotation_id,
            budget_amount=float(obj.budget_amount),
        )

    async def update_project(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        payload: s.ProjectUpdate,
    ) -> s.ProjectOut:
        obj = await _get_scoped_project(db, project_id, current_user, for_update=True)
        data = payload.model_dump(exclude_unset=True)
        if "project_name" in data and data["project_name"] is None:
            raise ValidationError("project_name cannot be null")
        if "status" in data:
            allowed_statuses = [
                s.ProjectStatus.PLANNED,
                s.ProjectStatus.ONGOING,
                s.ProjectStatus.ON_HOLD,
                s.ProjectStatus.COMPLETED,
            ]

            if data["status"] not in allowed_statuses:
                raise ValidationError("Invalid project status")
        try:
            await self.projects_repo.update_project(db, obj, data)
            await db.refresh(obj)
        except Exception:
            await db.rollback()
            logger.exception(f"Project update failed id={project_id}")
            raise
        completion_map = await self._compute_completion_percentage_by_project_ids(
            db, [obj.id]
        )
        completion = completion_map.get(obj.id, 0.0)
        return s.ProjectOut(
            id=obj.id,
            business_id=obj.business_id,
            project_name=obj.project_name,
            owner_id=obj.owner_id,
            description=obj.description,
            start_date=obj.start_date,
            end_date=obj.end_date,
            status=compute_project_status(obj),
            completion_percentage=completion,
            type=obj.type,
            location_type=obj.location_type,
            site_address=obj.site_address,
            city=obj.city,
            state=obj.state,
            country=obj.country,
            pincode=obj.pincode,
            latitude=obj.latitude,
            longitude=obj.longitude,
            shift_start_time=obj.shift_start_time,
            shift_end_time=obj.shift_end_time,
            grace_period_minutes=obj.grace_period_minutes,
        )

    async def delete_project(
        self, db: AsyncSession, current_user: User, *, project_id: int
    ) -> None:
        obj = await _get_scoped_project(db, project_id, current_user, for_update=True)
        try:
            await self.projects_repo.delete_project(db, obj)
        except Exception:
            await db.rollback()
            logger.exception(f"Project delete failed id={project_id}")
            raise


class ProjectMembersService:
    def __init__(
        self,
        projects_repo: ProjectsRepository,
        members_repo: ProjectMembersRepository,
    ) -> None:
        self.projects_repo = projects_repo
        self.members_repo = members_repo

    async def assign_member(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        user_id: int,
    ) -> s.ProjectMemberOut:
        project = await _get_scoped_project(db, project_id, current_user, for_update=True)

        user_query = select(User).where(User.id == user_id)
        if getattr(current_user, "is_super_admin", False) is not True:
            user_query = user_query.where(User.company_id == current_user.company_id)
        user = await db.scalar(user_query)
        if user is None:
            raise NotFoundError("User not found")

        existing = await self.members_repo.get_member(
            db,
            project_id=project_id,
            user_id=user_id,
        )
        if existing is not None:
            raise ConflictError("User is already assigned to this project")

        try:
            # Create Project Member
            await self.members_repo.assign_member(
                db,
                project_id=project_id,
                user_id=user_id,
            )

            # If the assigned user is Labour, create LabourProject mapping
            labour = await db.scalar(select(Labour).where(Labour.user_id == user_id))

            if labour:
                existing_labour_project = await db.scalar(
                    select(LabourProject).where(
                        LabourProject.labour_id == labour.id,
                        LabourProject.project_id == project_id,
                    )
                )

                if existing_labour_project is None:
                    db.add(
                        LabourProject(
                            labour_id=labour.id,
                            project_id=project_id,
                        )
                    )

            await db.flush()

        except IntegrityError:
            await db.rollback()
            raise ConflictError("User is already assigned to this project")

        return s.ProjectMemberOut(
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            role=user.role,
        )

    async def list_members(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        limit: int,
        offset: int,
    ) -> PaginatedResponse[s.ProjectMemberOut]:
        project = await _get_scoped_project(db, project_id, current_user)

        users, total = await self.members_repo.list_members(
            db, project_id=project_id, limit=limit, offset=offset
        )
        items: list[s.ProjectMemberOut] = []
        for user in users:
            role = user.role
            items.append(
                s.ProjectMemberOut(
                    user_id=user.id,
                    full_name=user.full_name,
                    email=user.email,
                    role=role,
                )
            )
        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[s.ProjectMemberOut](items=items, meta=meta)

    async def remove_member(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        user_id: int,
    ) -> None:
        project = await _get_scoped_project(db, project_id, current_user, for_update=True)

        existing = await self.members_repo.get_member(
            db, project_id=project_id, user_id=user_id
        )
        if existing is None:
            raise NotFoundError("Member not found")

        await self.members_repo.remove_member(
            db, project_id=project_id, user_id=user_id
        )


class MilestonesService:
    def __init__(
        self, projects_repo: ProjectsRepository, milestones_repo: MilestonesRepository
    ) -> None:
        self.projects_repo = projects_repo
        self.milestones_repo = milestones_repo

    async def create_milestone(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        payload: s.MilestoneCreate,
    ) -> s.MilestoneOut:
        project = await _get_scoped_project(db, project_id, current_user)

        data = payload.model_dump(exclude_unset=True)

        if "status" not in data or data["status"] is None:
            data["status"] = MilestoneStatus.PLANNED

        from datetime import date

        if data["status"] == MilestoneStatus.PLANNED:
            data["actual_start_date"] = None
            data["actual_end_date"] = None
        elif data["status"] == MilestoneStatus.IN_PROGRESS:
            data["actual_end_date"] = None
            if data.get("actual_start_date") is None:
                data["actual_start_date"] = date.today()
        elif data["status"] == MilestoneStatus.COMPLETED:
            if data.get("actual_end_date") is None:
                data["actual_end_date"] = date.today()
            if data.get("actual_start_date") is None:
                data["actual_start_date"] = data.get("start_date") or date.today()
        elif data["status"] == MilestoneStatus.DELAYED:
            data["actual_end_date"] = None

        try:
            obj = await self.milestones_repo.create_milestone(
                db, project_id=project_id, data=data
            )
        except IntegrityError:
            await db.rollback()
            raise ConflictError(
                "Milestone with this title already exists in this project"
            )
        except Exception:
            await db.rollback()
            logger.exception(f"Milestone create failed")
            raise

        return serialize_milestone(obj)

    async def list_milestones(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        pagination: PaginationParams,
    ) -> PaginatedResponse[s.MilestoneOut]:
        project = await _get_scoped_project(db, project_id, current_user)

        rows, total = await self.milestones_repo.list_milestones(
            db,
            project_id=project_id,
            limit=pagination.limit,
            offset=pagination.offset,
        )

        items = [serialize_milestone(m) for m in rows]

        return PaginatedResponse(
            items=items,
            meta=PaginationMeta(
                total=total,
                limit=pagination.limit,
                offset=pagination.offset,
            ),
        )

    async def get_milestone(
        self, db: AsyncSession, current_user: User, *, project_id: int, milestone_id: int
    ) -> s.MilestoneOut:
        obj = await _get_scoped_milestone(db, project_id, milestone_id, current_user)
        return serialize_milestone(obj)

    async def update_milestone(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        milestone_id: int,
        payload: s.MilestoneUpdate,
    ) -> s.MilestoneOut:
        obj = await _get_scoped_milestone(db, project_id, milestone_id, current_user, for_update=True)

        data = payload.model_dump(exclude_unset=True)
        if "title" in data and data["title"] is None:
            raise ValidationError("title cannot be null")

        from datetime import date

        if "status" in data and data["status"] is not None:
            if data["status"] == MilestoneStatus.IN_PROGRESS:
                data["actual_end_date"] = None
                if (
                    data.get("actual_start_date") is None
                    and obj.actual_start_date is None
                ):
                    data["actual_start_date"] = date.today()
            elif data["status"] == MilestoneStatus.COMPLETED:
                if (
                    data.get("actual_end_date") is None
                    and obj.actual_end_date is None
                ):
                    data["actual_end_date"] = date.today()
                if (
                    data.get("actual_start_date") is None
                    and obj.actual_start_date is None
                ):
                    data["actual_start_date"] = obj.start_date or date.today()
            elif data["status"] == MilestoneStatus.PLANNED:
                data["actual_start_date"] = None
                data["actual_end_date"] = None
            elif data["status"] == MilestoneStatus.DELAYED:
                data["actual_end_date"] = None

        try:
            await self.milestones_repo.update_milestone(db, obj=obj, data=data)
            await db.refresh(obj)
        except IntegrityError:
            await db.rollback()
            raise ConflictError(
                "Milestone with this title already exists in this project"
            )
        except Exception:
            await db.rollback()
            logger.exception(f"Milestone update failed id={milestone_id}")
            raise

        refreshed = await self.milestones_repo.get_milestone(
            db, project_id=project_id, milestone_id=milestone_id
        )
        return serialize_milestone(refreshed or obj)

    async def delete_milestone(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        milestone_id: int,
    ) -> None:
        obj = await _get_scoped_milestone(db, project_id, milestone_id, current_user, for_update=True)
        try:
            await self.milestones_repo.delete_milestone(db, obj=obj)
        except Exception:
            await db.rollback()
            logger.exception(f"Milestone delete failed id={milestone_id}")
            raise


class TasksService:
    def __init__(
        self,
        projects_repo: ProjectsRepository,
        members_repo: ProjectMembersRepository,
        tasks_repo: TasksRepository,
        progress_repo: TaskProgressRepository,
        comments_repo: CommentsRepository,
    ) -> None:
        self.projects_repo = projects_repo
        self.members_repo = members_repo
        self.tasks_repo = tasks_repo
        self.progress_repo = progress_repo
        self.comments_repo = comments_repo

    def _is_delayed(self, *, task: m.Task, current_date: date) -> bool:
        if task.end_date is None:
            return False
        return (current_date > task.end_date) and (
            task.status != s.TaskStatus.COMPLETED
        )

    async def _assert_progress_or_comment_auth(
        self,
        db: AsyncSession,
        *,
        current_user: User,
        project_id: int,
        task: m.Task,
    ) -> None:
        if getattr(current_user, "is_super_admin", False) is True:
            return

        if current_user.role in (
            UserRole.ADMIN.value,
            UserRole.PROJECT_MANAGER.value,
        ):
            return

        if any(a.user_id == current_user.id for a in task.assignments):
            return

        allowed = await self.members_repo.is_member(
            db,
            project_id=project_id,
            user_id=current_user.id,
        )

        if not allowed:
            raise PermissionDeniedError("Insufficient permissions")

    def _task_to_out(self, *, task: m.Task, is_delayed: bool) -> s.TaskOut:
        assigned_users_list = (
            [
                s.AssignedUserOut(
                    id=a.user.id,
                    name=a.user.full_name or str(a.user.id),
                    role=(
                        a.user.role.value
                        if hasattr(a.user.role, "value")
                        else str(a.user.role)
                    ),
                )
                for a in task.assignments
            ]
            if hasattr(task, "assignments")
            else []
        )

        return s.TaskOut(
            id=task.id,
            project_id=task.project_id,
            milestone_id=task.milestone_id,
            boq_id=task.boq_id,
            title=task.title,
            description=task.description,
            priority=PRIORITY_MAP.get(task.priority, TaskPriority.MEDIUM),
            status=task.status,
            start_date=task.start_date,
            end_date=task.end_date,
            created_by_user_id=task.created_by_user_id,
            assigned_users=assigned_users_list,
            completion_percentage=task.completion_percentage,
            is_delayed=is_delayed,
            audio_instruction_url=task.audio_instruction_url,
            instruction_image_url=task.instruction_image_url,
            task_icon=task.task_icon,
        )

    async def create_task(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        payload: s.TaskCreate,
        audio_instruction_url: Optional[str] = None,
        instruction_image_url: Optional[str] = None,
    ) -> s.TaskOut:
        project = await _get_scoped_project(db, project_id, current_user)

        if payload.milestone_id is not None:
            await _get_scoped_milestone(db, project_id, payload.milestone_id, current_user)

        # =========================
        # MASTER DATA VALIDATION
        # =========================
        if payload.activity_type_id is not None:
            activity = await db.get(ActivityType, payload.activity_type_id)
            if not activity:
                raise NotFoundError("Invalid activity type")

        data = payload.model_dump(exclude_unset=True)

        # =========================
        # MEDIA FILES
        # =========================

        data["audio_instruction_url"] = audio_instruction_url

        data["instruction_image_url"] = instruction_image_url

        if "priority" in data:
            if isinstance(data["priority"], TaskPriority):
                data["priority"] = REVERSE_PRIORITY_MAP[data["priority"]]
            elif isinstance(data["priority"], str):
                try:
                    enum_val = TaskPriority(data["priority"])
                    data["priority"] = REVERSE_PRIORITY_MAP[enum_val]
                except ValueError:
                    raise ValidationError("Invalid priority value")

        # =========================
        # MULTI-ASSIGN LOGIC (FIXED)
        # =========================
        assigned_ids = payload.assigned_user_ids

        data.pop("assigned_user_ids", None)

        if assigned_ids == []:
            raise ValidationError("assigned_user_ids cannot be empty")

        # Resolve all unique user IDs from either input method
        final_user_ids = set()

        if assigned_ids is not None:
            for uid in assigned_ids:
                if uid is not None:
                    final_user_ids.add(uid)

        final_user_ids_list = list(final_user_ids)

        # Validate all users
        for uid in final_user_ids_list:
            user_query = select(User).where(User.id == uid)
            if getattr(current_user, "is_super_admin", False) is not True:
                user_query = user_query.where(User.company_id == current_user.company_id)
            assigned_user = await db.scalar(user_query)
            if assigned_user is None:
                raise NotFoundError(f"User {uid} not found")

            is_member = await db.scalar(
                select(m.ProjectMember).where(
                    m.ProjectMember.project_id == project_id,
                    m.ProjectMember.user_id == uid,
                )
            )
            if not is_member:
                raise ValidationError(f"User {uid} not part of project")

        data["created_by_user_id"] = current_user.id

        # 1. Create ONE Task
        try:
            obj = await self.tasks_repo.create_task(
                db,
                project_id=project_id,
                data=data,
            )
        except IntegrityError:
            await db.rollback()
            raise ConflictError("Task with this title already exists in this project")

        # 2. Create TaskAssignments
        for uid in final_user_ids_list:
            assignment = m.TaskAssignment(
                task_id=obj.id, user_id=uid, assigned_by_user_id=current_user.id
            )
            db.add(assignment)

            await create_notification(
                db,
                user_id=uid,
                title="New Task Assigned",
                message=f"You have been assigned a new task: {obj.title}",
                type="info",
            )

        await db.flush()

        from sqlalchemy.orm import selectinload, joinedload

        # Refetch with relations
        obj = await db.scalar(
            select(m.Task)
            .options(selectinload(m.Task.assignments).joinedload(m.TaskAssignment.user))
            .where(m.Task.id == obj.id)
        )

        return self._task_to_out(
            task=obj,
            is_delayed=self._is_delayed(task=obj, current_date=date.today()),
        )

    async def list_tasks(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        status: Optional[s.TaskStatus],
        assigned_user_id: Optional[int],
        limit: int,
        offset: int,
        search: Optional[str] = None,
        view: Optional[str] = None,
    ) -> PaginatedResponse[s.TaskOut]:
        project = await _get_scoped_project(db, project_id, current_user)

        from sqlalchemy.orm import selectinload, joinedload

        #  base query
        query = select(m.Task).options(
            selectinload(m.Task.assignments).joinedload(m.TaskAssignment.user)
        )
        count_query = select(func.count(m.Task.id.distinct())).select_from(m.Task)

        #  mandatory filter
        query = query.where(m.Task.project_id == project_id)
        count_query = count_query.where(m.Task.project_id == project_id)

        #  optional filters

        if status:
            query = query.where(m.Task.status == status)
            count_query = count_query.where(m.Task.status == status)

        if assigned_user_id is not None:
            query = query.where(
                m.Task.assignments.any(m.TaskAssignment.user_id == assigned_user_id)
            )
            count_query = count_query.where(
                m.Task.assignments.any(m.TaskAssignment.user_id == assigned_user_id)
            )

        if search:
            query = query.where(m.Task.title.ilike(f"%{search}%"))
            count_query = count_query.where(m.Task.title.ilike(f"%{search}%"))

        if view == "created":
            query = query.where(m.Task.created_by_user_id == current_user.id)
            count_query = count_query.where(
                m.Task.created_by_user_id == current_user.id
            )

        elif view == "received":
            query = query.where(
                m.Task.assignments.any(m.TaskAssignment.user_id == current_user.id)
            )
            count_query = count_query.where(
                m.Task.assignments.any(m.TaskAssignment.user_id == current_user.id)
            )

        #  ordering + pagination
        query = query.order_by(m.Task.id.desc()).limit(limit).offset(offset)

        #  execute
        rows = (await db.execute(query)).scalars().unique().all()
        total = await db.scalar(count_query)

        current_date = date.today()

        items = [
            self._task_to_out(
                task=t,
                is_delayed=self._is_delayed(task=t, current_date=current_date),
            )
            for t in rows
        ]

        meta = PaginationMeta(
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

        return PaginatedResponse[s.TaskOut](items=items, meta=meta)

    async def get_task(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
    ) -> s.TaskOut:
        obj = await _get_scoped_task(db, project_id, task_id, current_user)
        is_delayed = self._is_delayed(task=obj, current_date=date.today())
        return self._task_to_out(task=obj, is_delayed=is_delayed)

    async def update_task(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
        payload: s.TaskUpdate,
        audio_instruction_url: Optional[str] = None,
        instruction_image_url: Optional[str] = None,
        remove_audio: bool = False,
        remove_image: bool = False,
    ) -> s.TaskOut:
        obj = await _get_scoped_task(db, project_id, task_id, current_user, for_update=True)

        data = payload.model_dump(exclude_unset=True)

        if data.get("milestone_id") is not None:
            await _get_scoped_milestone(db, project_id, data["milestone_id"], current_user)

        # =====================================
        # MEDIA FILES
        # =====================================

        if audio_instruction_url:
            data["audio_instruction_url"] = audio_instruction_url

        if instruction_image_url:
            data["instruction_image_url"] = instruction_image_url

        if remove_audio:
            data["audio_instruction_url"] = None

        if remove_image:
            data["instruction_image_url"] = None

        if "priority" in data:
            if isinstance(data["priority"], TaskPriority):
                data["priority"] = REVERSE_PRIORITY_MAP[data["priority"]]
            elif isinstance(data["priority"], str):
                try:
                    enum_val = TaskPriority(data["priority"])
                    data["priority"] = REVERSE_PRIORITY_MAP[enum_val]
                except ValueError:
                    raise ValidationError("Invalid priority value")

        if "title" in data and data["title"] is None:
            raise ValidationError("title cannot be null")

        if "priority" in data and data["priority"] is None:
            raise ValidationError("priority cannot be null")

        if "status" in data and data["status"] is None:
            raise ValidationError("status cannot be null")

        # Resolve user IDs
        assigned_ids = data.pop("assigned_user_ids", None)

        has_assignment_update = assigned_ids is not None

        if has_assignment_update:
            final_user_ids = set()
            if assigned_ids is not None:
                for uid in assigned_ids:
                    if uid is not None:
                        final_user_ids.add(uid)

            final_user_ids_list = list(final_user_ids)

            if not final_user_ids_list:
                raise ValidationError("assigned_user_ids cannot be empty")

            for uid in final_user_ids_list:
                user_query = select(User).where(User.id == uid)
                if getattr(current_user, "is_super_admin", False) is not True:
                    user_query = user_query.where(User.company_id == current_user.company_id)
                assigned_user = await db.scalar(user_query)
                if assigned_user is None:
                    raise NotFoundError(f"User {uid} not found")

                is_member = await db.scalar(
                    select(m.ProjectMember).where(
                        m.ProjectMember.project_id == project_id,
                        m.ProjectMember.user_id == uid,
                    )
                )
                if not is_member:
                    raise ValidationError(f"User {uid} not part of project")

        try:
            await self.tasks_repo.update_task(db, obj=obj, data=data)
            await db.refresh(obj)

            # Sync Assignments
            if has_assignment_update:
                from sqlalchemy.orm import selectinload

                # Load current assignments
                obj_with_assignments = await db.scalar(
                    select(m.Task)
                    .options(selectinload(m.Task.assignments))
                    .where(m.Task.id == obj.id)
                )

                current_uids = {a.user_id for a in obj_with_assignments.assignments}
                target_uids = set(final_user_ids_list)

                to_remove = current_uids - target_uids
                to_add = target_uids - current_uids

                if to_remove:
                    await db.execute(
                        m.TaskAssignment.__table__.delete().where(
                            m.TaskAssignment.task_id == obj.id,
                            m.TaskAssignment.user_id.in_(to_remove),
                        )
                    )

                if to_add:
                    for uid in to_add:
                        assignment = m.TaskAssignment(
                            task_id=obj.id,
                            user_id=uid,
                            assigned_by_user_id=current_user.id,
                        )
                        db.add(assignment)

                        await create_notification(
                            db,
                            user_id=uid,
                            title="New Task Assigned",
                            message=f"You have been assigned a new task: {obj.title}",
                            type="info",
                        )
                await db.flush()

        except IntegrityError:
            await db.rollback()
            raise ConflictError("Task with this title already exists in this project")
        except Exception:
            await db.rollback()
            logger.exception(f"Task update failed id={task_id}")
            raise

        from sqlalchemy.orm import selectinload, joinedload  # noqa: F401

        obj = await db.scalar(
            select(m.Task)
            .options(selectinload(m.Task.assignments).joinedload(m.TaskAssignment.user))
            .where(m.Task.id == obj.id)
        )

        is_delayed = self._is_delayed(task=obj, current_date=date.today())

        return self._task_to_out(task=obj, is_delayed=is_delayed)

    async def pass_task(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
        new_user_id: int,
    ):
        obj = await _get_scoped_task(db, project_id, task_id, current_user, for_update=True)

        user_query = select(User).where(User.id == new_user_id)
        if getattr(current_user, "is_super_admin", False) is not True:
            user_query = user_query.where(User.company_id == current_user.company_id)
        new_user = await db.scalar(user_query)
        if not new_user:
            raise NotFoundError("User not found")

        is_member = await db.scalar(
            select(m.ProjectMember).where(
                m.ProjectMember.project_id == project_id,
                m.ProjectMember.user_id == new_user_id,
            )
        )
        if not is_member:
            raise ValidationError("User not part of project")

        # Overwrite assignments with this single new user
        await db.execute(
            m.TaskAssignment.__table__.delete().where(
                m.TaskAssignment.task_id == obj.id,
            )
        )
        assignment = m.TaskAssignment(
            task_id=obj.id, user_id=new_user_id, assigned_by_user_id=current_user.id
        )
        db.add(assignment)
        await db.flush()

        from sqlalchemy.orm import selectinload, joinedload

        obj = await db.scalar(
            select(m.Task)
            .options(selectinload(m.Task.assignments).joinedload(m.TaskAssignment.user))
            .where(m.Task.id == obj.id)
        )

        return self._task_to_out(
            task=obj,
            is_delayed=self._is_delayed(task=obj, current_date=date.today()),
        )

    async def update_task_status(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
        status: s.TaskStatus,
    ):
        obj = await _get_scoped_task(db, project_id, task_id, current_user, for_update=True)

        await self.tasks_repo.update_task(
            db,
            obj=obj,
            data={"status": status},
        )

        if (
            (hasattr(status, "value") and status.value.upper() == "COMPLETED")
            or str(status).upper() == "COMPLETED"
            or str(status).upper() == "TASKSTATUS.COMPLETED"
        ):
            db.add(
                ActivityLog(
                    action="TASK_COMPLETED",
                    entity="project",
                    entity_id=project_id,
                    performed_by=current_user.id,
                    details={"message": f"Task '{obj.title}' completed"},
                )
            )
            await db.flush()

        await db.refresh(obj)

        return self._task_to_out(
            task=obj,
            is_delayed=self._is_delayed(task=obj, current_date=date.today()),
        )

    async def delete_task(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
    ) -> None:
        obj = await _get_scoped_task(db, project_id, task_id, current_user, for_update=True)
        try:
            await self.tasks_repo.delete_task(db, obj=obj)
        except Exception:
            await db.rollback()
            logger.exception(f"Task delete failed id={task_id}")
            raise

    async def update_task_progress(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
        payload: s.TaskProgressUpdate,
    ) -> s.TaskProgressOut:
        obj = await _get_scoped_task(db, project_id, task_id, current_user, for_update=True)

        await self._assert_progress_or_comment_auth(
            db, current_user=current_user, project_id=project_id, task=obj
        )

        if payload.percentage < obj.completion_percentage:
            raise ValidationError("Progress cannot decrease")

        progress_obj = await self.progress_repo.create_progress(
            db,
            task_id=obj.id,
            percentage=int(payload.percentage),
            remarks=payload.remarks,
            created_by_user_id=current_user.id,
        )

        await db.refresh(progress_obj)

        await self.tasks_repo.update_task(
            db,
            obj=obj,
            data={"completion_percentage": int(payload.percentage)},
        )

        return s.TaskProgressOut(
            id=progress_obj.id,
            task_id=progress_obj.task_id,
            percentage=progress_obj.percentage,
            remarks=progress_obj.remarks,
            created_at=progress_obj.created_at,
        )

    async def list_task_progress_history(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
        limit: int,
        offset: int,
    ) -> PaginatedResponse[s.TaskProgressOut]:
        obj = await _get_scoped_task(db, project_id, task_id, current_user)

        rows, total = await self.progress_repo.list_progress_history(
            db,
            task_id=obj.id,
            limit=limit,
            offset=offset,
        )

        items = [
            s.TaskProgressOut(
                id=p.id,
                task_id=p.task_id,
                percentage=p.percentage,
                remarks=p.remarks,
                created_at=p.created_at,
            )
            for p in rows
        ]

        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)

        return PaginatedResponse[s.TaskProgressOut](items=items, meta=meta)

    async def create_comment(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
        payload: s.CommentCreate,
    ) -> s.CommentOut:
        obj = await _get_scoped_task(db, project_id, task_id, current_user)

        await self._assert_progress_or_comment_auth(
            db,
            current_user=current_user,
            project_id=project_id,
            task=obj,
        )

        comment_obj = await self.comments_repo.create_comment(
            db,
            task_id=obj.id,
            author_user_id=current_user.id,
            content=payload.content,
        )

        return s.CommentOut(
            id=comment_obj.id,
            task_id=comment_obj.task_id,
            author_user_id=comment_obj.author_user_id,
            content=comment_obj.content,
        )

    async def list_comments(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
        limit: int,
        offset: int,
    ) -> PaginatedResponse[s.CommentOut]:
        obj = await _get_scoped_task(db, project_id, task_id, current_user)

        rows, total = await self.comments_repo.list_comments(
            db,
            task_id=obj.id,
            limit=limit,
            offset=offset,
        )

        items = [
            s.CommentOut(
                id=c.id,
                task_id=c.task_id,
                author_user_id=c.author_user_id,
                content=c.content,
            )
            for c in rows
        ]

        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)

        return PaginatedResponse[s.CommentOut](items=items, meta=meta)


class SchedulingService:
    def __init__(self, projects_repo: ProjectsRepository):
        self.projects_repo = projects_repo

    async def set_schedule(
        self,
        db: AsyncSession,
        *,
        project_id: int,
        start_date: date,
        end_date: date,
        current_user: User,
    ):
        project = await _get_scoped_project(db, project_id, current_user, for_update=True)

        if end_date < start_date:
            raise ValidationError("End date cannot be before start date")

        try:
            await self.projects_repo.update_project(
                db,
                project,
                {"start_date": start_date, "end_date": end_date},
            )
        except Exception:
            await db.rollback()
            logger.exception(f"Schedule update failed project_id={project_id}")
            raise

        return {
            "project_id": project_id,
            "start_date": start_date,
            "end_date": end_date,
        }

    async def get_schedule(self, db: AsyncSession, *, project_id: int, current_user: User):
        project = await _get_scoped_project(db, project_id, current_user)

        return {
            "project_id": project_id,
            "start_date": project.start_date,
            "end_date": project.end_date,
        }


class AlertsService:
    def __init__(self, projects_repo: ProjectsRepository, tasks_repo: TasksRepository):
        self.projects_repo = projects_repo
        self.tasks_repo = tasks_repo

    async def get_project_alerts(
        self,
        db: AsyncSession,
        current_user: User,
        pagination: PaginationParams,
    ):
        _check_batch_y_tenant_access(current_user)
        today = date.today()

        base_query = select(m.Project).where(
            m.Project.end_date < today,
            m.Project.status != s.ProjectStatus.COMPLETED,
        )
        if getattr(current_user, "is_super_admin", False) is not True:
            base_query = base_query.where(
                m.Project.company_id == current_user.company_id
            )

        base_query = base_query.distinct()

        count_query = select(func.count()).select_from(
            base_query.order_by(None).subquery()
        )
        total = await db.scalar(count_query)

        query = (
            base_query.order_by(m.Project.end_date.asc())
            .limit(pagination.limit)
            .offset(pagination.offset)
        )

        rows = (await db.execute(query)).scalars().all()

        items = [
            {
                "project_id": p.id,
                "project_name": p.project_name,
                "end_date": p.end_date,
                "status": "Delayed",
            }
            for p in rows
        ]

        return PaginatedResponse(
            items=items,
            meta=PaginationMeta(
                total=int(total or 0),
                limit=pagination.limit,
                offset=pagination.offset,
            ),
        )

    async def get_task_alerts(
        self,
        db: AsyncSession,
        current_user: User,
        pagination: PaginationParams,
    ):
        _check_batch_y_tenant_access(current_user)
        today = date.today()

        base_query = (
            select(m.Task)
            .join(m.Project, m.Task.project_id == m.Project.id)
            .where(
                m.Task.end_date < today,
                m.Task.status != s.TaskStatus.COMPLETED,
            )
        )
        if getattr(current_user, "is_super_admin", False) is not True:
            base_query = base_query.where(
                m.Project.company_id == current_user.company_id
            )

        base_query = base_query.distinct()

        count_query = select(func.count()).select_from(
            base_query.order_by(None).subquery()
        )
        total = await db.scalar(count_query)

        query = (
            base_query.order_by(m.Task.end_date.asc())
            .limit(pagination.limit)
            .offset(pagination.offset)
        )

        rows = (await db.execute(query)).scalars().all()

        items = [
            {
                "task_id": t.id,
                "project_id": t.project_id,
                "title": t.title,
                "end_date": t.end_date,
                "status": "Delayed",
            }
            for t in rows
        ]

        return PaginatedResponse(
            items=items,
            meta=PaginationMeta(
                total=int(total or 0),
                limit=pagination.limit,
                offset=pagination.offset,
            ),
        )

        items = [
            {
                "task_id": t.id,
                "project_id": t.project_id,
                "title": t.title,
                "end_date": t.end_date,
                "status": "Delayed",
            }
            for t in rows
        ]

        return PaginatedResponse(
            items=items,
            meta=PaginationMeta(
                total=int(total or 0),
                limit=pagination.limit,
                offset=pagination.offset,
            ),
        )


class ReportsService:
    def __init__(self, projects_repo: ProjectsRepository):
        self.projects_repo = projects_repo

    async def get_project_data(
        self,
        db: AsyncSession,
        project_id: int,
        current_user: User,
    ):
        project = await self.projects_repo.get_project(db, project_id)
        if not project:
            raise NotFoundError("Project not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

        return {
            "id": project.id,
            "name": project.project_name,
            "status": project.status,
            "start_date": project.start_date,
            "end_date": project.end_date,
        }

    async def export_excel(
        self,
        db: AsyncSession,
        project_id: int,
        current_user: User,
    ):
        from app.models.boq import BOQ
        from app.models.expense import Expense
        from app.models.invoice import Invoice
        from app.models.owner import Owner
        from sqlalchemy import select, func
        from sqlalchemy.orm import selectinload
        from app.models import project as m
        from app.models.user import User as UserModel, UserRole
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment

        project = await self.projects_repo.get_project(db, project_id)

        if not project:
            raise NotFoundError("Project not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

        owner = None

        if project.owner_id:
            owner = await db.scalar(select(Owner).where(Owner.id == project.owner_id))

        # =====================================
        # Financial Data
        # =====================================

        total_boq = await db.scalar(
            select(func.sum(BOQ.total_cost)).where(BOQ.project_id == project_id)
        )

        total_invoice = await db.scalar(
            select(func.sum(Invoice.total_amount)).where(
                Invoice.project_id == project_id
            )
        )

        total_expense = await db.scalar(
            select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
        )

        boq_value = float(total_boq or 0)
        invoice_value = float(total_invoice or 0)
        expense_value = float(total_expense or 0)

        profit = invoice_value - expense_value
        outstanding = boq_value - invoice_value

        # =====================================
        # Tasks
        # =====================================

        tasks = (
            (
                await db.execute(
                    select(m.Task)
                    .options(
                        selectinload(m.Task.assignments).joinedload(
                            m.TaskAssignment.user
                        )
                    )
                    .where(m.Task.project_id == project_id)
                )
            )
            .scalars()
            .unique()
            .all()
        )

        total_tasks = len(tasks)

        completed_tasks = sum(
            1
            for t in tasks
            if (hasattr(t.status, "value") and t.status.value == "Completed")
            or str(t.status) == "Completed"
        )

        pending_tasks = sum(
            1
            for t in tasks
            if (
                hasattr(t.status, "value")
                and t.status.value
                in [
                    "Pending",
                    "In Progress",
                ]
            )
            or str(t.status)
            in [
                "Pending",
                "In Progress",
            ]
        )

        delayed_tasks = sum(
            1
            for t in tasks
            if (hasattr(t.status, "value") and t.status.value == "Delayed")
            or str(t.status) == "Delayed"
        )

        average_progress = (
            sum(
                getattr(
                    t,
                    "completion_percentage",
                    0,
                )
                for t in tasks
            )
            / total_tasks
            if total_tasks
            else 0
        )

        # =====================================
        # Milestones
        # =====================================

        milestones = (
            (
                await db.execute(
                    select(m.Milestone).where(m.Milestone.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )

        total_milestones = len(milestones)

        completed_milestones = sum(
            1
            for ms in milestones
            if (hasattr(ms.status, "value") and ms.status.value == "Completed")
            or str(ms.status) == "Completed"
        )

        # =====================================
        # Team Members
        # =====================================

        members_query = (
            select(UserModel)
            .join(
                m.ProjectMember,
                m.ProjectMember.user_id == UserModel.id,
            )
            .where(m.ProjectMember.project_id == project_id)
        )

        members_result = await db.execute(members_query)

        members = []

        manager = "N/A"
        supervisor = "N/A"

        for user in members_result.scalars().all():

            role = user.role.value if hasattr(user.role, "value") else str(user.role)

            members.append(
                {
                    "name": user.full_name,
                    "role": role,
                    "phone": getattr(
                        user,
                        "phone",
                        "N/A",
                    ),
                    "email": user.email,
                }
            )

            if role == UserRole.PROJECT_MANAGER.value:
                manager = user.full_name

            elif role == UserRole.SITE_ENGINEER.value:
                supervisor = user.full_name

        # =====================================
        # Workbook
        # =====================================

        wb = Workbook()
        ws = wb.active
        ws.title = "Project Report"

        # =====================================
        # Styles
        # =====================================

        title_font = Font(
            bold=True,
            size=18,
            color="1F4E78",
        )

        section_font = Font(
            bold=True,
            color="FFFFFF",
        )

        header_font = Font(
            bold=True,
        )

        blue_fill = PatternFill(
            fill_type="solid",
            fgColor="1F4E78",
        )

        header_fill = PatternFill(
            fill_type="solid",
            fgColor="D9EAD3",
        )

        center = Alignment(
            horizontal="center",
            vertical="center",
        )

        left = Alignment(
            horizontal="left",
            vertical="center",
        )

        # =====================================
        # Title
        # =====================================

        ws.merge_cells("A1:F1")

        cell = ws["A1"]
        cell.value = "PROJECT REPORT"
        cell.font = title_font
        cell.alignment = center

        row = 3

        # =====================================
        # Project Information
        # =====================================

        ws.merge_cells(f"A{row}:F{row}")

        cell = ws[f"A{row}"]
        cell.value = "1. PROJECT INFORMATION"
        cell.fill = blue_fill
        cell.font = section_font

        row += 1

        project_rows = [
            [
                "Project Name",
                project.project_name,
                "Project Code",
                project.business_id,
            ],
            [
                "Client Name",
                owner.owner_name if owner else "N/A",
                "Project Type",
                str(project.type) if project.type else "N/A",
            ],
            [
                "Location",
                f"{project.city or ''}, {project.state or ''}",
                "Current Status",
                str(project.status),
            ],
            [
                "Start Date",
                str(project.start_date),
                "Planned End Date",
                str(project.end_date),
            ],
            [
                "Project Manager",
                manager,
                "Site Supervisor",
                supervisor,
            ],
        ]

        for data in project_rows:

            ws.append(data)

            current = ws.max_row

            ws[f"A{current}"].font = header_font
            ws[f"C{current}"].font = header_font

        # =====================================
        # Executive Summary
        # =====================================

        row = ws.max_row + 2

        ws.merge_cells(f"A{row}:F{row}")

        cell = ws[f"A{row}"]
        cell.value = "2. EXECUTIVE SUMMARY"
        cell.fill = blue_fill
        cell.font = section_font

        row += 1

        summary_rows = [
            [
                "Overall Progress",
                f"{round(average_progress)}%",
            ],
            [
                "Total Tasks",
                total_tasks,
            ],
            [
                "Completed Tasks",
                completed_tasks,
            ],
            [
                "Milestones",
                f"{completed_milestones} / {total_milestones}",
            ],
            [
                "Team Members",
                len(members),
            ],
        ]

        for item in summary_rows:

            ws.append(item)

            current = ws.max_row

            ws[f"A{current}"].font = header_font

        # =====================================
        # Financial Overview
        # =====================================

        row = ws.max_row + 2

        ws.merge_cells(f"A{row}:F{row}")

        cell = ws[f"A{row}"]
        cell.value = "3. FINANCIAL OVERVIEW"
        cell.fill = blue_fill
        cell.font = section_font

        row += 1

        financial_rows = [
            [
                "Total BOQ Value",
                boq_value,
            ],
            [
                "Total Invoiced",
                invoice_value,
            ],
            [
                "Total Expenses",
                expense_value,
            ],
            [
                "Net Profit",
                profit,
            ],
            [
                "Outstanding Amount",
                outstanding,
            ],
        ]

        for item in financial_rows:

            ws.append(item)

            current = ws.max_row

            ws[f"A{current}"].font = header_font

        # =====================================
        # Tasks
        # =====================================

        row = ws.max_row + 2

        ws.merge_cells(f"A{row}:F{row}")

        cell = ws[f"A{row}"]
        cell.value = "4. TASK DETAILS"
        cell.fill = blue_fill
        cell.font = section_font

        row += 1

        task_headers = [
            "Task",
            "Assigned To",
            "Start Date",
            "End Date",
            "Status",
            "Progress %",
        ]

        ws.append(task_headers)

        header_row = ws.max_row

        for col in range(1, 7):
            c = ws.cell(row=header_row, column=col)
            c.font = header_font
            c.fill = header_fill
            c.alignment = center

        for task in tasks:

            assignee = (
                ", ".join(
                    [
                        a.user.full_name
                        for a in task.assignments
                        if a.user and a.user.full_name
                    ]
                )
                if task.assignments
                else "Unassigned"
            )

            ws.append(
                [
                    task.title,
                    assignee,
                    str(task.start_date or ""),
                    str(task.end_date or ""),
                    (
                        task.status.value
                        if hasattr(task.status, "value")
                        else str(task.status)
                    ),
                    getattr(task, "completion_percentage", 0),
                ]
            )

        # =====================================
        # Milestones
        # =====================================

        row = ws.max_row + 2

        ws.merge_cells(f"A{row}:E{row}")

        cell = ws[f"A{row}"]
        cell.value = "5. MILESTONES"
        cell.fill = blue_fill
        cell.font = section_font

        row += 1

        milestone_headers = [
            "Milestone",
            "End Date",
            "Status",
            "Completion %",
        ]

        ws.append(milestone_headers)

        header_row = ws.max_row

        for col in range(1, 5):
            c = ws.cell(row=header_row, column=col)
            c.font = header_font
            c.fill = header_fill
            c.alignment = center

        for ms in milestones:

            ws.append(
                [
                    ms.title,
                    str(ms.end_date or ""),
                    ms.status.value if hasattr(ms.status, "value") else str(ms.status),
                    getattr(ms, "completion_percentage", 0),
                ]
            )

        # =====================================
        # Team Members
        # =====================================

        row = ws.max_row + 2

        ws.merge_cells(f"A{row}:E{row}")

        cell = ws[f"A{row}"]
        cell.value = "6. TEAM MEMBERS"
        cell.fill = blue_fill
        cell.font = section_font

        row += 1

        member_headers = [
            "Name",
            "Role",
            "Phone",
            "Email",
        ]

        ws.append(member_headers)

        header_row = ws.max_row

        for col in range(1, 5):
            c = ws.cell(row=header_row, column=col)
            c.font = header_font
            c.fill = header_fill
            c.alignment = center

        for member in members:

            ws.append(
                [
                    member["name"],
                    member["role"],
                    member["phone"],
                    member["email"],
                ]
            )

        # =====================================
        # Auto Width
        # =====================================
        from openpyxl.utils import get_column_letter

        for col in range(1, ws.max_column + 1):

            max_length = 0
            column_letter = get_column_letter(col)

            for row in range(1, ws.max_row + 1):

                cell = ws.cell(row=row, column=col)

                if cell.value is not None:

                    max_length = max(max_length, len(str(cell.value)))

            ws.column_dimensions[column_letter].width = min(
                max_length + 4,
                40,
            )

        # =====================================
        # Freeze Header
        # =====================================

        ws.freeze_panes = "A2"

        # =====================================
        # Save Workbook
        # =====================================

        stream = io.BytesIO()

        wb.save(stream)

        stream.seek(0)

        return StreamingResponse(
            stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": (
                    f"attachment; "
                    f"filename=Project_Report_{project.business_id}.xlsx"
                )
            },
        )

    async def export_pdf(
        self,
        db: AsyncSession,
        project_id: int,
        current_user: User,
    ):
        from app.utils.project_report_pdf import generate_project_report_pdf
        from app.models.boq import BOQ
        from app.models.expense import Expense
        from app.models.invoice import Invoice
        from app.models.owner import Owner
        from sqlalchemy import select, func
        from app.models import project as m
        from app.models.user import User as UserModel, UserRole

        project = await self.projects_repo.get_project(db, project_id)
        if not project:
            raise NotFoundError("Project not found")

        await assert_project_access(
            db, project_id=project_id, current_user=current_user
        )

        owner = None
        if getattr(project, "owner_id", None):
            owner = await db.scalar(select(Owner).where(Owner.id == project.owner_id))

        # Financials
        total_boq = await db.scalar(
            select(func.sum(BOQ.total_cost)).where(BOQ.project_id == project_id)
        )
        total_invoiced = await db.scalar(
            select(func.sum(Invoice.total_amount)).where(
                Invoice.project_id == project_id
            )
        )
        total_expenses = await db.scalar(
            select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
        )

        boq_val = float(total_boq or 0)
        invoiced_val = float(total_invoiced or 0)
        expense_val = float(total_expenses or 0)
        profit = invoiced_val - expense_val
        outstanding = boq_val - invoiced_val

        # Tasks
        from sqlalchemy.orm import selectinload

        tasks = (
            (
                await db.execute(
                    select(m.Task)
                    .options(
                        selectinload(m.Task.assignments).joinedload(
                            m.TaskAssignment.user
                        )
                    )
                    .where(m.Task.project_id == project_id)
                )
            )
            .scalars()
            .unique()
            .all()
        )
        total_tasks = len(tasks)
        completed_tasks = sum(
            1
            for t in tasks
            if str(t.status) == "Completed"
            or (hasattr(t.status, "value") and t.status.value == "Completed")
        )
        pending_tasks = sum(
            1
            for t in tasks
            if str(t.status) in ["Pending", "In Progress"]
            or (
                hasattr(t.status, "value")
                and t.status.value in ["Pending", "In Progress"]
            )
        )
        delayed_tasks = sum(
            1
            for t in tasks
            if str(t.status) == "Delayed"
            or (hasattr(t.status, "value") and t.status.value == "Delayed")
        )
        avg_progress = (
            sum(getattr(t, "completion_percentage", 0) for t in tasks) / total_tasks
            if total_tasks
            else 0
        )

        # Milestones
        milestones = (
            (
                await db.execute(
                    select(m.Milestone).where(m.Milestone.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        total_milestones = len(milestones)
        completed_milestones = sum(
            1
            for m_obj in milestones
            if str(m_obj.status) == "Completed"
            or (hasattr(m_obj.status, "value") and m_obj.status.value == "Completed")
        )

        # Members
        members_query = (
            select(UserModel)
            .join(m.ProjectMember, m.ProjectMember.user_id == UserModel.id)
            .where(m.ProjectMember.project_id == project_id)
        )
        members_result = await db.execute(members_query)
        members_list = []
        manager = "N/A"
        supervisor = "N/A"
        for user in members_result.scalars().all():
            role_str = (
                user.role.value if hasattr(user.role, "value") else str(user.role)
            )
            members_list.append(
                {
                    "name": user.full_name,
                    "role": role_str,
                    "phone": getattr(user, "phone", "N/A"),
                    "email": user.email,
                }
            )
            if role_str == UserRole.PROJECT_MANAGER.value:
                manager = user.full_name
            elif role_str == UserRole.SITE_ENGINEER.value:
                supervisor = user.full_name

        data = {
            "project": {
                "name": project.project_name,
                "code": project.business_id,
                "client": owner.owner_name if owner else "N/A",
                "type": getattr(project, "type", "Residential"),
                "location": getattr(
                    project,
                    "location",
                    getattr(project, "address", "Ranchi, Jharkhand"),
                ),
                "start_date": project.start_date,
                "end_date": project.end_date,
                "status": "In Progress" if avg_progress < 100 else "Completed",
                "manager": manager,
                "supervisor": supervisor,
            },
            "summary": {
                "progress": round(avg_progress),
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "pending_tasks": pending_tasks,
                "delayed_tasks": delayed_tasks,
                "milestones_total": total_milestones,
                "milestones_completed": completed_milestones,
                "team_members": len(members_list),
                "boq_value": boq_val,
                "invoiced": invoiced_val,
                "expenses": expense_val,
                "net_profit": profit,
                "outstanding": outstanding,
            },
            "tasks": [
                {
                    "name": t.title,
                    "assignee": (
                        ", ".join(
                            [
                                a.user.full_name
                                for a in t.assignments
                                if a.user and a.user.full_name
                            ]
                        )
                        if t.assignments
                        else "Unassigned"
                    ),
                    "start_date": t.start_date,
                    "end_date": t.end_date,
                    "status": (
                        t.status.value if hasattr(t.status, "value") else str(t.status)
                    ),
                    "progress": getattr(t, "completion_percentage", 0),
                }
                for t in tasks
            ],
            "milestones": [
                {
                    "name": ms.title,
                    "end_date": ms.end_date,
                    "status": (
                        ms.status.value
                        if hasattr(ms.status, "value")
                        else str(ms.status)
                    ),
                    "completion": (
                        ms.completion_percentage
                        if hasattr(ms, "completion_percentage")
                        else (
                            100
                            if (
                                hasattr(ms.status, "value")
                                and ms.status.value == "Completed"
                            )
                            or str(ms.status) == "Completed"
                            else 0
                        )
                    ),
                }
                for ms in milestones
            ],
            "members": members_list,
        }

        buffer = generate_project_report_pdf(data)

        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=Project_Report_{project.business_id}.pdf"
            },
        )


def get_tasks_service():
    return TasksService(
        ProjectsRepository(),
        ProjectMembersRepository(),
        TasksRepository(),
        TaskProgressRepository(),
        CommentsRepository(),
    )


def get_projects_service():
    return ProjectsService(ProjectsRepository(), TasksRepository())


def get_milestones_service():
    return MilestonesService(ProjectsRepository(), MilestonesRepository())


def get_scheduling_service():
    return SchedulingService(ProjectsRepository())


def get_alerts_service():
    return AlertsService(ProjectsRepository(), TasksRepository())


def get_project_members_service():
    return ProjectMembersService(ProjectsRepository(), ProjectMembersRepository())


def get_reports_service():
    return ReportsService(ProjectsRepository())


@router.get("/module-summary", response_model=s.ProjectsModuleResponse)
async def projects_module_summary(
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_batch_y_tenant_access(current_user)
    today = date.today()

    # 1. Summary
    q_total = select(func.count(m.Project.id))
    q_ongoing = select(func.count(m.Project.id)).where(
        m.Project.status == ProjectStatus.ONGOING.value
    )
    q_completed = select(func.count(m.Project.id)).where(
        m.Project.status == ProjectStatus.COMPLETED.value
    )
    q_delayed = select(func.count(m.Project.id)).where(
        m.Project.status == ProjectStatus.ONGOING.value, m.Project.end_date < today
    )

    if getattr(current_user, "is_super_admin", False) is not True:
        q_total = q_total.where(m.Project.company_id == current_user.company_id)
        q_ongoing = q_ongoing.where(m.Project.company_id == current_user.company_id)
        q_completed = q_completed.where(m.Project.company_id == current_user.company_id)
        q_delayed = q_delayed.where(m.Project.company_id == current_user.company_id)

    total = await db.scalar(q_total)
    ongoing = await db.scalar(q_ongoing)
    completed = await db.scalar(q_completed)
    delayed = await db.scalar(q_delayed)

    summary = s.ProjectsModuleSummary(
        total_projects=total or 0,
        ongoing_sites=ongoing or 0,
        completed_projects=completed or 0,
        delayed_projects=delayed or 0,
    )

    # 2. Activities (Aggregated Feed)
    activities = []

    # a. Task Progress
    q_task_p = (
        select(m.TaskProgress, m.Task.title, m.Project.project_name, User.full_name)
        .join(m.Task, m.TaskProgress.task_id == m.Task.id)
        .join(m.Project, m.Task.project_id == m.Project.id)
        .join(User, m.TaskProgress.created_by_user_id == User.id)
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        q_task_p = q_task_p.where(m.Project.company_id == current_user.company_id)
    task_p = await db.execute(
        q_task_p.order_by(m.TaskProgress.created_at.desc()).limit(5)
    )
    for row in task_p.all():
        activities.append(
            s.ProjectActivityItem(
                type="task_completion",
                user_name=row[3],
                description=f"updated progress on {row[1]} to {row[0].percentage}%",
                project_name=row[2],
                timestamp=row[0].created_at,
            )
        )

    # b. Invoices
    q_invoices = (
        select(Invoice, m.Project.project_name)
        .join(m.Project, Invoice.project_id == m.Project.id)
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        q_invoices = q_invoices.where(m.Project.company_id == current_user.company_id)
    invoices = await db.execute(
        q_invoices.order_by(Invoice.created_at.desc()).limit(5)
    )
    for row in invoices.all():
        activities.append(
            s.ProjectActivityItem(
                type="invoice",
                user_name="Financial Team",
                description=f"submitted Invoice #{row[0].id} for {row[0].total_amount}",
                project_name=row[1],
                timestamp=row[0].created_at,
            )
        )

    # c. Site Photos
    q_photos = (
        select(m.SitePhoto, m.Project.project_name)
        .join(m.Project, m.SitePhoto.project_id == m.Project.id)
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        q_photos = q_photos.where(m.Project.company_id == current_user.company_id)
    photos = await db.execute(
        q_photos.order_by(m.SitePhoto.created_at.desc()).limit(5)
    )
    for row in photos.all():
        activities.append(
            s.ProjectActivityItem(
                type="photo",
                user_name="Site Bot",
                description="uploaded a new site photo",
                project_name=row[1],
                timestamp=row[0].created_at,
            )
        )

    # d. Issues
    q_issues = (
        select(m.Issue, m.Project.project_name)
        .join(m.Project, m.Issue.project_id == m.Project.id)
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        q_issues = q_issues.where(m.Project.company_id == current_user.company_id)
    issues = await db.execute(
        q_issues.order_by(m.Issue.created_at.desc()).limit(5)
    )
    for row in issues.all():
        activities.append(
            s.ProjectActivityItem(
                type="issue",
                user_name="Site Manager",
                description=f"reported {row[0].priority} issue: {row[0].title}",
                project_name=row[1],
                timestamp=row[0].created_at,
            )
        )

    # Sort and return
    activities.sort(key=lambda x: x.timestamp, reverse=True)

    return s.ProjectsModuleResponse(summary=summary, activities=activities[:15])


@router.post("", response_model=s.ProjectOut)
async def create_project(
    payload: s.ProjectCreate,
    current_user: User = Depends(require_permission("projects.create")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: ProjectsService = Depends(get_projects_service),
):
    logger.info(f"Creating project name={payload.project_name}")

    try:
        out = await service.create_project(db, current_user, payload=payload)
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception("Project creation failed")
        raise

    logger.info(f"Project created id={out.id}")

    return out


# =========================================
# PM DASHBOARD ENDPOINTS
# =========================================


@router.get("/calendar", response_model=s.PMCalendarOut)
async def get_pm_calendar(
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
):
    _check_batch_y_tenant_access(current_user)

    from app.models.project import Task, Milestone

    proj_query = select(m.Project.id)
    if getattr(current_user, "is_super_admin", False) is not True:
        proj_query = proj_query.where(m.Project.company_id == current_user.company_id)
    projs = await db.scalars(proj_query)
    project_ids = list(projs.all())

    events = []

    if project_ids:
        # Tasks (Due Dates)
        tasks = await db.scalars(
            select(Task).where(
                Task.project_id.in_(project_ids), Task.end_date.isnot(None)
            )
        )
        for t in tasks:
            events.append(s.CalendarEvent(title=t.title, date=t.end_date, type="Task"))

        # Milestones (Due Dates)
        milestones = await db.scalars(
            select(Milestone).where(
                Milestone.project_id.in_(project_ids), Milestone.end_date.isnot(None)
            )
        )
        for ml in milestones:
            events.append(
                s.CalendarEvent(title=ml.title, date=ml.end_date, type="Milestone")
            )

    return s.PMCalendarOut(events=events)


@router.get(
    "/{project_id}/resource-summary", response_model=s.ProjectResourceSummaryOut
)
async def get_project_resource_summary(
    project_id: int,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_project(db, project_id, current_user, load_relations=False)

    from app.models.labour import LabourProject
    from app.models.equipment import Equipment
    from app.models.expense import Expense

    labour_count = await db.scalar(
        select(func.count(LabourProject.labour_id)).where(
            LabourProject.project_id == project_id
        )
    )

    equipment_count = await db.scalar(
        select(func.count(Equipment.id)).where(Equipment.project_id == project_id)
    )

    material_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(
            Expense.project_id == project_id, Expense.category == "Material"
        )
    )

    return s.ProjectResourceSummaryOut(
        labour=labour_count or 0,
        equipment=equipment_count or 0,
        materials_cost=float(material_expense or 0.0),
    )


@router.get("/{project_id}/health-score", response_model=s.ProjectHealthScoreOut)
async def get_project_health_score(
    project_id: int,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
):
    project = await _get_scoped_project(db, project_id, current_user, load_relations=False)

    from app.models.project import Issue

    score = 100
    if (
        project.status == ProjectStatus.ONGOING.value
        and project.end_date
        and project.end_date < date.today()
    ):
        score -= 20
    elif project.status == ProjectStatus.ON_HOLD.value:
        score -= 15

    open_critical_issues = await db.scalar(
        select(func.count(Issue.id)).where(
            Issue.project_id == project_id,
            Issue.status == "Open",
            Issue.priority == "High",
        )
    )
    if open_critical_issues:
        score -= open_critical_issues * 5

    score = max(0, min(100, score))

    status_str = "Good"
    if score < 50:
        status_str = "Poor"
    elif score < 80:
        status_str = "At Risk"

    return s.ProjectHealthScoreOut(health=score, status=status_str)


@router.get("", response_model=PaginatedResponse[s.ProjectOut])
async def list_projects(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[s.ProjectStatus] = None,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: ProjectsService = Depends(get_projects_service),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:projects:list:{version}:{current_user.id}:{current_user.role}:{limit}:{offset}:{search}:{status}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        items = cached.get("items") if isinstance(cached, dict) else None
        if items and isinstance(items, list) and "completion_percentage" in items[0]:
            return PaginatedResponse[s.ProjectOut].model_validate(cached)

    result = await service.list_projects(
        db,
        current_user=current_user,
        limit=limit,
        offset=offset,
        search=search,
        status=status,
    )
    await cache_set_json(redis, cache_key, result.model_dump())
    return result


@router.post("/{project_id}/schedule")
async def set_project_schedule(
    project_id: int,
    start_date: date,
    end_date: date,
    current_user: User = Depends(require_permission("projects.edit")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: SchedulingService = Depends(get_scheduling_service),
):
    result = await service.set_schedule(
        db,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
    )
    await bump_cache_version(redis, VERSION_KEY)
    return result


@router.get("/{project_id}/schedule")
async def get_project_schedule(
    project_id: int,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
    service: SchedulingService = Depends(get_scheduling_service),
):
    return await service.get_schedule(db, project_id=project_id, current_user=current_user)


@router.get("/{project_id}/progress")
async def get_project_progress(
    project_id: int,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
    service: ProjectsService = Depends(get_projects_service),
):
    project = await service.get_project(
        db,
        project_id=project_id,
        current_user=current_user,
    )

    return {
        "project_id": project_id,
        "completion_percentage": project.completion_percentage,
        "status": project.status,
    }


@router.get("/alerts/projects")
async def get_project_alerts(
    pagination: PaginationParams = Depends(get_pagination),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("projects.view")),
    service: AlertsService = Depends(get_alerts_service),
):
    return await service.get_project_alerts(db, current_user, pagination)


@router.get("/alerts/tasks")
async def get_task_alerts(
    pagination: PaginationParams = Depends(get_pagination),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("tasks.view")),
    service: AlertsService = Depends(get_alerts_service),
):
    return await service.get_task_alerts(db, current_user, pagination)


@router.post(
    "/{project_id}/members/{user_id}",
    response_model=s.ProjectMemberOut,
    status_code=201,
)
async def assign_project_member(
    project_id: int,
    user_id: int,
    current_user: User = Depends(require_permission("projects.edit")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: ProjectMembersService = Depends(get_project_members_service),
):
    logger.info(f"Assigning member user_id={user_id} project_id={project_id}")

    try:
        out = await service.assign_member(
            db, current_user, project_id=project_id, user_id=user_id
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(
            f"Assign member failed user_id={user_id} project_id={project_id}"
        )
        raise

    logger.info(f"Member assigned user_id={user_id} project_id={project_id}")

    return out


@router.get(
    "/{project_id}/members", response_model=PaginatedResponse[s.ProjectMemberOut]
)
async def list_project_members(
    project_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
    service: ProjectMembersService = Depends(get_project_members_service),
):
    return await service.list_members(
        db, current_user, project_id=project_id, limit=limit, offset=offset
    )


@router.delete("/{project_id}/members/{user_id}", status_code=200)
async def remove_project_member(
    project_id: int,
    user_id: int,
    current_user: User = Depends(require_permission("projects.edit")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: ProjectMembersService = Depends(get_project_members_service),
):
    logger.info(f"Removing member user_id={user_id} project_id={project_id}")

    try:
        await service.remove_member(
            db, current_user, project_id=project_id, user_id=user_id
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(
            f"Remove member failed user_id={user_id} project_id={project_id}"
        )
        raise

    logger.info(f"Member removed user_id={user_id} project_id={project_id}")

    return {"success": True, "message": "Member Remove successfully"}


@router.get("/{project_id}/logs", response_model=list[s.ProjectLogItem])
async def get_project_logs(
    project_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_project(db, project_id, current_user, load_relations=False)

    from app.models.project import ActivityHistory, WorkActivity
    from app.models.boq import BOQAudit, BOQ
    from app.models.equipment import EquipmentAuditLog, Equipment

    logs = []

    # 1. ActivityHistory (via WorkActivity)
    act_res = await db.execute(
        select(ActivityHistory, WorkActivity.activity_name)
        .join(WorkActivity, ActivityHistory.activity_id == WorkActivity.id)
        .where(WorkActivity.project_id == project_id)
        .order_by(ActivityHistory.created_at.desc())
        .limit(limit)
    )
    for act, title in act_res.all():
        logs.append(
            s.ProjectLogItem(
                timestamp=act.created_at,
                module="Activity",
                action=act.action,
                message=f"Activity '{title}' updated",
                user_id=act.changed_by,
                details={"remarks": act.remarks},
            )
        )

    # 2. BOQAudit
    boq_res = await db.execute(
        select(BOQAudit)
        .join(BOQ, BOQAudit.boq_id == BOQ.id)
        .where(BOQ.project_id == project_id)
        .order_by(BOQAudit.created_at.desc())
        .limit(limit)
    )
    for ba in boq_res.scalars().all():
        logs.append(
            s.ProjectLogItem(
                timestamp=ba.created_at,
                module="BOQ",
                action=ba.action,
                message=ba.message,
                user_id=ba.user_id,
                details=ba.changes,
            )
        )

    # 3. EquipmentAuditLog
    eq_res = await db.execute(
        select(EquipmentAuditLog, Equipment.equipment_name)
        .join(Equipment, EquipmentAuditLog.equipment_id == Equipment.id)
        .where(Equipment.project_id == project_id)
        .order_by(EquipmentAuditLog.created_at.desc())
        .limit(limit)
    )
    for ea, eq_name in eq_res.all():
        logs.append(
            s.ProjectLogItem(
                timestamp=ea.created_at,
                module="Equipment",
                action=ea.action,
                message=f"Equipment '{eq_name}' updated",
                user_id=ea.user_id,
                details={"old": ea.old_values, "new": ea.new_values},
            )
        )

    # Sort all by timestamp descending and slice
    logs.sort(key=lambda x: x.timestamp, reverse=True)

    return logs[offset : offset + limit]


@router.get("/{project_id}/photos")
async def get_project_photos(
    project_id: int,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_project(db, project_id, current_user, load_relations=False)

    result = await db.execute(
        select(m.SitePhoto)
        .where(m.SitePhoto.project_id == project_id)
        .order_by(m.SitePhoto.date.desc())
    )

    photos = result.scalars().all()

    return [
        {
            "id": p.id,
            "photo_url": p.photo_url,
            "date": p.date,
            "activity": p.activity_tag,
            "description": p.description,
        }
        for p in photos
    ]


@router.put(
    "/{project_id}/ot-policy",
    response_model=s.ProjectOTPolicyOut,
)
async def create_or_update_ot_policy(
    project_id: int,
    payload: s.ProjectOTPolicyCreate,
    current_user: User = Depends(require_permission("projects.edit")),
    db: AsyncSession = Depends(get_db_session),
):
    project = await _get_scoped_project(db, project_id, current_user, for_update=True, load_relations=False)

    # EXISTING POLICY
    policy = await db.scalar(
        select(m.ProjectOTPolicy).where(m.ProjectOTPolicy.project_id == project_id)
    )

    data = payload.model_dump(exclude_unset=True)

    # UPDATE
    if policy:
        for k, v in data.items():
            setattr(policy, k, v)

    # CREATE
    else:
        policy = m.ProjectOTPolicy(project_id=project_id, **data)
        db.add(policy)

    await db.flush()
    await db.refresh(policy)

    return s.ProjectOTPolicyOut.model_validate(policy, from_attributes=True)


milestones_router = APIRouter(
    prefix="",
    tags=["project_management"],
    dependencies=[default_rate_limiter_dependency()],
)
tasks_router = APIRouter(
    prefix="",
    tags=["project_management"],
    dependencies=[default_rate_limiter_dependency()],
)


@milestones_router.post("/{project_id}/milestones", response_model=s.MilestoneOut)
async def create_milestone(
    project_id: int,
    payload: s.MilestoneCreate,
    current_user: User = Depends(require_permission("milestones.create")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: MilestonesService = Depends(get_milestones_service),
):
    logger.info(f"Creating milestone project_id={project_id}")

    try:
        out = await service.create_milestone(
            db, current_user, project_id=project_id, payload=payload
        )
        await bump_cache_version(redis, VERSION_KEY)
    except (AppError, HTTPException):
        raise
    except Exception as e:
        logger.exception(f"Milestone creation failed project_id={project_id}")
        raise HTTPException(status_code=500, detail="Failed to create milestone")

    logger.info(f"Milestone created id={out.id}")

    return out


@milestones_router.get(
    "/{project_id}/milestones",
    response_model=PaginatedResponse[s.MilestoneOut],
)
async def list_milestones(
    project_id: int,
    pagination: PaginationParams = Depends(get_pagination),
    current_user: User = Depends(require_permission("milestones.view")),
    db: AsyncSession = Depends(get_db_session),
    service: MilestonesService = Depends(get_milestones_service),
):
    return await service.list_milestones(
        db,
        current_user=current_user,
        project_id=project_id,
        pagination=pagination,
    )


@milestones_router.get(
    "/{project_id}/milestones/{milestone_id}", response_model=s.MilestoneOut
)
async def get_milestone(
    project_id: int,
    milestone_id: int,
    current_user: User = Depends(require_permission("milestones.view")),
    db: AsyncSession = Depends(get_db_session),
    service: MilestonesService = Depends(get_milestones_service),
):
    return await service.get_milestone(
        db, current_user=current_user, project_id=project_id, milestone_id=milestone_id
    )


@milestones_router.put(
    "/{project_id}/milestones/{milestone_id}", response_model=s.MilestoneOut
)
async def update_milestone(
    project_id: int,
    milestone_id: int,
    payload: s.MilestoneUpdate,
    current_user: User = Depends(require_permission("milestones.edit")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: MilestonesService = Depends(get_milestones_service),
):
    logger.info(f"Updating milestone id={milestone_id}")

    try:
        out = await service.update_milestone(
            db,
            current_user,
            project_id=project_id,
            milestone_id=milestone_id,
            payload=payload,
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(f"Milestone update failed id={milestone_id}")
        raise

    logger.info(f"Milestone updated id={milestone_id}")

    return out


@milestones_router.delete("/{project_id}/milestones/{milestone_id}")
async def delete_milestone(
    project_id: int,
    milestone_id: int,
    current_user: User = Depends(require_permission("milestones.delete")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: MilestonesService = Depends(get_milestones_service),
):
    logger.info(f"Deleting milestone id={milestone_id}")

    try:
        await service.delete_milestone(
            db, current_user, project_id=project_id, milestone_id=milestone_id
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(f"Milestone delete failed id={milestone_id}")
        raise

    logger.info(f"Milestone deleted id={milestone_id}")

    return {
        "success": True,
        "message": f"Milestone_id {milestone_id}  deleted successfully",
    }


IMAGE_DIR = "uploads/task_images"

os.makedirs(IMAGE_DIR, exist_ok=True)

AUDIO_DIR = "uploads/task_audio"

os.makedirs(AUDIO_DIR, exist_ok=True)


@tasks_router.post("/{project_id}/tasks", response_model=s.TaskOut)
async def create_task(
    project_id: int,
    payload: s.TaskCreateForm = Depends(),
    audio_file: Optional[UploadFile] = File(None),
    instruction_image: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_permission("tasks.create")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    logger.info(f"Creating task project_id={project_id}")

    task_payload = payload.to_schema()

    # =========================================
    # SAVE AUDIO FILE
    # =========================================

    audio_instruction_url = None

    if audio_file:
        from app.core.validators import validate_and_save_audio

        audio_instruction_url = await validate_and_save_audio(
            file=audio_file, upload_dir=AUDIO_DIR, prefix="audio"
        )
        audio_instruction_url = audio_instruction_url.replace("\\", "/")

    # =========================================
    # SAVE IMAGE FILE
    # =========================================

    instruction_image_url = None

    if instruction_image:
        from app.core.validators import validate_and_save_image

        instruction_image_url = await validate_and_save_image(
            file=instruction_image, upload_dir=IMAGE_DIR, prefix="img"
        )
        instruction_image_url = instruction_image_url.replace("\\", "/")

    try:
        out = await service.create_task(
            db,
            current_user,
            project_id=project_id,
            payload=task_payload,
            audio_instruction_url=audio_instruction_url,
            instruction_image_url=instruction_image_url,
        )
        await bump_cache_version(redis, VERSION_KEY)

    except Exception:
        logger.exception(f"Task creation failed project_id={project_id}")
        raise

    if isinstance(out, list):
        logger.info(f"Tasks created count={len(out)}")
    else:
        logger.info(f"Task created id={out.id}")

    return out


@tasks_router.get("/{project_id}/tasks", response_model=PaginatedResponse[s.TaskOut])
async def list_tasks(
    project_id: int,
    status: Optional[s.TaskStatus] = Query(None),
    assigned_user_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    view: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("tasks.view")),
    db: AsyncSession = Depends(get_db_session),
    service: TasksService = Depends(get_tasks_service),
):
    return await service.list_tasks(
        db,
        current_user,
        project_id=project_id,
        status=status,
        assigned_user_id=assigned_user_id,
        limit=limit,
        offset=offset,
        search=search,
        view=view,
    )


@tasks_router.get("/{project_id}/tasks/{task_id}", response_model=s.TaskOut)
async def get_task(
    project_id: int,
    task_id: int,
    current_user: User = Depends(require_permission("tasks.view")),
    db: AsyncSession = Depends(get_db_session),
    service: TasksService = Depends(get_tasks_service),
):
    return await service.get_task(
        db, current_user, project_id=project_id, task_id=task_id
    )


@tasks_router.put("/{project_id}/tasks/{task_id}", response_model=s.TaskOut)
async def update_task(
    project_id: int,
    task_id: int,
    payload: s.TaskUpdateForm = Depends(),
    audio_file: Optional[UploadFile] = File(None),
    instruction_image: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_permission("tasks.edit")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    logger.info(f"Updating task id={task_id}")

    task_payload = payload.to_schema()

    # =========================================
    # SAVE AUDIO FILE
    # =========================================

    audio_instruction_url = None

    if audio_file:
        from app.core.validators import validate_and_save_audio

        audio_instruction_url = await validate_and_save_audio(
            file=audio_file, upload_dir=AUDIO_DIR, prefix="audio"
        )
        audio_instruction_url = audio_instruction_url.replace("\\", "/")

    # =========================================
    # SAVE IMAGE FILE
    # =========================================

    instruction_image_url = None
    if instruction_image:
        from app.core.validators import validate_and_save_image

        instruction_image_url = await validate_and_save_image(
            file=instruction_image, upload_dir=IMAGE_DIR, prefix="img"
        )
        instruction_image_url = instruction_image_url.replace("\\", "/")

    try:
        out = await service.update_task(
            db,
            current_user,
            project_id=project_id,
            task_id=task_id,
            payload=task_payload,
            audio_instruction_url=audio_instruction_url,
            instruction_image_url=instruction_image_url,
            remove_audio=payload.remove_audio,
            remove_image=payload.remove_image,
        )

        await bump_cache_version(redis, VERSION_KEY)

    except Exception:
        logger.exception(f"Task update failed id={task_id}")
        raise

    logger.info(f"Task updated id={task_id}")

    return out


@tasks_router.patch("/{project_id}/tasks/{task_id}/status")
async def update_status(
    project_id: int,
    task_id: int,
    payload: s.TaskStatusUpdate,
    current_user: User = Depends(require_permission("tasks.edit")),
    db: AsyncSession = Depends(get_db_session),
    service: TasksService = Depends(get_tasks_service),
):
    return await service.update_task_status(
        db,
        current_user,
        project_id=project_id,
        task_id=task_id,
        status=payload.status,
    )


@tasks_router.post("/{project_id}/tasks/{task_id}/pass")
async def pass_task(
    project_id: int,
    task_id: int,
    payload: s.TaskPass,
    current_user: User = Depends(require_permission("tasks.edit")),
    db: AsyncSession = Depends(get_db_session),
    service: TasksService = Depends(get_tasks_service),
):
    return await service.pass_task(
        db,
        current_user,
        project_id=project_id,
        task_id=task_id,
        new_user_id=payload.new_user_id,
    )


@tasks_router.delete("/{project_id}/tasks/{task_id}")
async def delete_task(
    project_id: int,
    task_id: int,
    current_user: User = Depends(require_permission("tasks.delete")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    logger.info(f"Deleting task id={task_id}")

    try:
        await service.delete_task(
            db, current_user, project_id=project_id, task_id=task_id
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(f"Task delete failed id={task_id}")
        raise

    logger.info(f"Task deleted id={task_id}")

    return {"success": True, "message": f"Task_id {task_id} deleted successfully"}


@tasks_router.post(
    "/{project_id}/tasks/{task_id}/progress", response_model=s.TaskProgressOut
)
async def update_task_progress(
    project_id: int,
    task_id: int,
    payload: s.TaskProgressUpdate,
    current_user: User = Depends(require_permission("tasks.edit")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    logger.info(f"Updating task progress task_id={task_id}")

    try:
        out = await service.update_task_progress(
            db, current_user, project_id=project_id, task_id=task_id, payload=payload
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(f"Task progress update failed task_id={task_id}")
        raise

    logger.info(f"Task progress updated task_id={task_id}")

    return out


@tasks_router.get(
    "/{project_id}/tasks/{task_id}/progress",
    response_model=PaginatedResponse[s.TaskProgressOut],
)
async def list_task_progress_history(
    project_id: int,
    task_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("tasks.view")),
    db: AsyncSession = Depends(get_db_session),
    service: TasksService = Depends(get_tasks_service),
):
    return await service.list_task_progress_history(
        db,
        current_user,
        project_id=project_id,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )


@tasks_router.post(
    "/task-requests",
    response_model=s.TaskRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_request(
    form: s.TaskRequestCreateForm = Depends(),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("tasks.create")),
):
    request = form.to_schema()
    await _get_scoped_project(db, project_id=request.project_id, current_user=current_user, load_relations=False)

    attachment_url = None

    if form.attachment and form.attachment.filename and form.attachment.filename.strip():
        try:
            os.makedirs("uploads/task_requests", exist_ok=True)
            safe_filename = form.attachment.filename.replace(" ", "_")
            unique_name = f"{uuid4().hex[:8]}_{safe_filename}"
            file_path = os.path.join("uploads/task_requests", unique_name)
            content = await form.attachment.read()
            with open(file_path, "wb") as f:
                f.write(content)
            attachment_url = f"/uploads/task_requests/{unique_name}"
        except Exception:
            attachment_url = form.attachment.filename

    data = request.model_dump(exclude={"attachment_url"})
    data["attachment_url"] = attachment_url

    db_obj = m.TaskRequest(**data)

    db.add(db_obj)
    await db.commit()
    await db.refresh(db_obj)

    return db_obj


@tasks_router.get(
    "/task-requests",
    response_model=PaginatedResponse[s.TaskRequestResponse],
)
async def list_task_requests(
    project_id: int | None = Query(None),
    status: str | None = Query(None),
    priority: str | None = Query(None),
    search: str | None = Query(None, description="Search by title"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("tasks.view")),
):
    _check_batch_y_tenant_access(current_user)

    query = select(m.TaskRequest).join(m.Project, m.TaskRequest.project_id == m.Project.id).where(m.TaskRequest.is_deleted == False)
    if getattr(current_user, "is_super_admin", False) is not True:
        query = query.where(m.Project.company_id == current_user.company_id)
    elif not current_user.company_id:
        query = query.where(m.Project.company_id == None)

    # Filters
    if project_id:
        query = query.where(m.TaskRequest.project_id == project_id)

    if status:
        query = query.where(m.TaskRequest.status == status)

    if priority:
        query = query.where(m.TaskRequest.priority == priority)

    if search:
        query = query.where(m.TaskRequest.title.ilike(f"%{search}%"))

    # Total Count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)

    # Pagination
    offset = (page - 1) * page_size

    query = (
        query.order_by(m.TaskRequest.created_at.desc()).offset(offset).limit(page_size)
    )

    result = await db.execute(query)
    records = result.scalars().all()

    return PaginatedResponse(
        items=records,
        meta=PaginationMeta(
            total=total or 0,
            limit=page_size,
            offset=offset,
        ),
    )


@tasks_router.put("/task-requests/{request_id}", response_model=s.TaskRequestResponse)
async def update_task_request(
    request_id: int,
    request: s.TaskRequestUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("tasks.edit")),
):
    db_obj = await _get_scoped_task_request(db, request_id, current_user, for_update=True)

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_obj, key, value)

    await db.commit()
    await db.refresh(db_obj)
    return db_obj


@tasks_router.delete("/task-requests/{request_id}", status_code=204)
async def delete_task_request(
    request_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("tasks.delete")),
):
    db_obj = await _get_scoped_task_request(db, request_id, current_user, for_update=True)

    # Soft delete
    db_obj.is_deleted = True
    await db.commit()

    return None


@tasks_router.post(
    "/{project_id}/tasks/{task_id}/comments", response_model=s.CommentOut
)
async def create_comment(
    project_id: int,
    task_id: int,
    payload: s.CommentCreate,
    current_user: User = Depends(require_permission("tasks.view")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    logger.info(f"Creating comment task_id={task_id}")

    try:
        out = await service.create_comment(
            db,
            current_user,
            project_id=project_id,
            task_id=task_id,
            payload=payload,
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(f"Comment creation failed task_id={task_id}")
        raise

    logger.info(f"Comment created id={out.id}")

    return out


@tasks_router.get(
    "/{project_id}/tasks/{task_id}/comments",
    response_model=PaginatedResponse[s.CommentOut],
)
async def list_comments(
    project_id: int,
    task_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("tasks.view")),
    db: AsyncSession = Depends(get_db_session),
    service: TasksService = Depends(get_tasks_service),
):
    return await service.list_comments(
        db,
        current_user,
        project_id=project_id,
        task_id=task_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}/profit-loss")
async def project_profit_loss(
    project_id: int,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
):
    project = await _get_scoped_project(db, project_id, current_user, load_relations=False)

    total_expense = await db.scalar(
        select(func.sum(Expense.amount)).where(Expense.project_id == project_id)
    )

    total_invoice = await db.scalar(
        select(func.sum(Invoice.total_amount)).where(Invoice.project_id == project_id)
    )

    total_expense = float(total_expense or 0)
    total_invoice = float(total_invoice or 0)

    profit = total_invoice - total_expense

    return {
        "project_id": project_id,
        "total_invoice": total_invoice,
        "total_expense": total_expense,
        "profit": profit,
        "status": "profit" if profit >= 0 else "loss",
    }


dsr_router = APIRouter(
    prefix="/dsr",
    tags=["DSR"],
    dependencies=[default_rate_limiter_dependency()],
)


# =========================
# CREATE DSR
# =========================
@dsr_router.post("", response_model=s.DSROut)
async def create_dsr(
    request: Request,
    payload: s.DSRCreate = Depends(),
    photos: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_permission("dsr.create")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(
        f"Creating DSR project_id={payload.project_id} date={payload.report_date}"
    )

    project = await _get_scoped_project(db, payload.project_id, current_user, load_relations=False)

    # Validate photo file if provided before processing DSR
    if photos and photos.filename:
        if not photos.content_type or not photos.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid image")
        safe_name = pathlib.Path(photos.filename or "file").name
        ext = pathlib.Path(safe_name).suffix.lower().replace(".", "")
        if ext not in {"jpg", "jpeg", "png"}:
            raise HTTPException(status_code=400, detail="Invalid photo extension")

    existing = await db.scalar(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == payload.project_id,
            m.DailySiteReport.report_date == payload.report_date,
        )
    )

    if existing:
        raise BadRequestError("DSR already exists for this date")

    # Contractor validation
    contractor = None
    if payload.contractor_id:
        contractor = await db.scalar(
            select(Contractor).where(
                Contractor.id == payload.contractor_id,
                Contractor.company_id == current_user.company_id,
            )
        )
        if not contractor:
            raise HTTPException(
                status_code=404,
                detail="Contractor not found",
            )

    labour_result = await db.execute(
        select(
            LabourType.skill_category,
            func.count(func.distinct(Labour.id)),
        )
        .join(
            LabourType,
            Labour.labour_type_id == LabourType.id,
        )
        .join(
            UserAttendance,
            Labour.user_id == UserAttendance.user_id,
        )
        .where(
            UserAttendance.project_id == payload.project_id,
            Labour.status == LabourStatus.ACTIVE,
        )
        .group_by(LabourType.skill_category)
    )

    skilled = 0
    unskilled = 0

    for skill_category, count in labour_result.all():
        if skill_category == SkillType.SKILLED:
            skilled += count
        else:
            unskilled += count

    total_labour = skilled + unskilled

    data = payload.model_dump()
    data["created_by_id"] = current_user.id
    data["total_labour"] = total_labour
    data["skilled_labour"] = skilled
    data["unskilled_labour"] = unskilled

    for _ in range(3):
        try:
            data["business_id"] = await generate_business_id(
                db, m.DailySiteReport, "business_id", "DSR"
            )
            obj = m.DailySiteReport(**data)
            db.add(obj)
            await db.flush()
            break
        except IntegrityError:
            await db.rollback()
            continue
    else:
        raise Exception("Failed to generate unique DSR ID")

    # Handle Photos
    if photos and photos.filename:
        upload_dir = "uploads/dsr"
        os.makedirs(upload_dir, exist_ok=True)

        content = await photos.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Photo file too large")

        try:
            img = Image.open(io.BytesIO(content))
            img.verify()
        except Exception:
            raise HTTPException(status_code=400, detail="Corrupted image file")

        safe_name = pathlib.Path(photos.filename or "file").name
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", safe_name)
        ext = pathlib.Path(safe_name).suffix.lower().replace(".", "")
        filename = f"{uuid.uuid4()}_{safe_name}"
        path = os.path.join(upload_dir, filename).replace("\\", "/")

        def _save_dsr():
            with open(path, "wb") as f:
                f.write(content)

        await run_in_threadpool(_save_dsr)
        photo = m.DSRPhoto(dsr_id=obj.id, file_url=path)
        db.add(photo)

    try:
        await db.flush()
        await db.refresh(obj)

        result = await db.execute(
            select(m.DailySiteReport)
            .options(
                selectinload(m.DailySiteReport.contractor),
                selectinload(m.DailySiteReport.created_by),
            )
            .where(m.DailySiteReport.id == obj.id)
        )
        obj = result.scalar_one()
        await bump_cache_version(redis, "cache_version:dsr")
    except Exception:
        await db.rollback()
        logger.exception("DSR creation failed")
        raise

    dsr_out = s.DSROut.model_validate(obj)
    if obj.contractor:
        dsr_out.contractor_name = obj.contractor.name
    if obj.created_by:
        dsr_out.created_by_name = obj.created_by.full_name

    base_url = str(request.base_url).rstrip("/")
    result_photos = await db.execute(
        select(m.DSRPhoto).where(m.DSRPhoto.dsr_id == obj.id)
    )
    dsr_out.photos = [f"{base_url}/{p.file_url}" for p in result_photos.scalars().all()]
    return dsr_out


# =========================
# GET PROJECT DSR
# =========================
@dsr_router.get("/project/{project_id}", response_model=PaginatedResponse[s.DSROut])
async def get_project_dsr(
    project_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("dsr.view")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(f"Fetching DSR for project_id={project_id}")
    project = await _get_scoped_project(db, project_id, current_user, load_relations=False)

    version = await get_cache_version(redis, "cache_version:dsr")
    cache_key = f"cache:dsr:list:{version}:{project_id}:{limit}:{offset}"

    cached = await cache_get_json(redis, cache_key)
    if cached:
        return PaginatedResponse[s.DSROut].model_validate(cached)

    query = (
        select(m.DailySiteReport)
        .options(
            selectinload(m.DailySiteReport.contractor),
            selectinload(m.DailySiteReport.created_by),
        )
        .where(m.DailySiteReport.project_id == project_id)
        .order_by(m.DailySiteReport.report_date.desc())
        .limit(limit)
        .offset(offset)
    )

    count_query = (
        select(func.count())
        .select_from(m.DailySiteReport)
        .where(m.DailySiteReport.project_id == project_id)
    )

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = []
    for row in rows:
        dsr = s.DSROut.model_validate(row, from_attributes=True)
        if row.contractor:
            dsr.contractor_name = row.contractor.name
        if row.created_by:
            dsr.created_by_name = row.created_by.full_name
        items.append(dsr.model_dump())

    meta = PaginationMeta(
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )

    result = {
        "items": items,
        "meta": meta.model_dump(),
    }

    await cache_set_json(redis, cache_key, result)
    return PaginatedResponse[s.DSROut].model_validate(result)


# =========================
# GET DSR BY ID
# =========================
@dsr_router.get("/{id}", response_model=s.DSROut)
async def get_dsr(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("dsr.view")),
    redis=Depends(get_request_redis),
):
    logger.info(f"Fetching DSR id={id}")

    version = await get_cache_version(redis, "cache_version:dsr")
    cache_key = f"cache:dsr:get:{version}:{id}"

    cached = await cache_get_json(redis, cache_key)
    if cached:
        return s.DSROut.model_validate(cached)

    obj = await _get_scoped_dsr(db, id, current_user, load_relations=True)
    dsr_out = s.DSROut.model_validate(obj, from_attributes=True)

    if obj.contractor:
        dsr_out.contractor_name = obj.contractor.name
    if obj.created_by:
        dsr_out.created_by_name = obj.created_by.full_name

    await cache_set_json(redis, cache_key, dsr_out.model_dump())
    return dsr_out


# =========================
# UPDATE DSR
# =========================
@dsr_router.put("/{id}", response_model=s.DSROut)
async def update_dsr(
    id: int,
    payload: s.DSRUpdate,
    current_user: User = Depends(require_permission("dsr.edit")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(f"Updating DSR id={id}")
    obj = await _get_scoped_dsr(db, id, current_user, for_update=True, load_relations=True)

    if obj.status == "Approved":
        raise ValidationError("Cannot update approved DSR")

    if payload.contractor_id:
        contractor = await db.scalar(
            select(Contractor).where(
                Contractor.id == payload.contractor_id,
                Contractor.company_id == current_user.company_id,
            )
        )
        if not contractor:
            raise HTTPException(status_code=404, detail="Contractor not found")

    if payload.report_date:
        existing = await db.scalar(
            select(m.DailySiteReport).where(
                m.DailySiteReport.project_id == obj.project_id,
                m.DailySiteReport.report_date == payload.report_date,
                m.DailySiteReport.id != id,
            )
        )
        if existing:
            raise BadRequestError("DSR already exists for this date")

    update_data = payload.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        if k not in ["project_id", "created_by_id"]:
            setattr(obj, k, v)

    try:
        await db.flush()
    except Exception:
        await db.rollback()
        logger.exception(f"DSR update failed id={id}")
        raise

    await db.refresh(obj)
    await bump_cache_version(redis, "cache_version:dsr")

    dsr_out = s.DSROut.model_validate(obj, from_attributes=True)
    if obj.contractor:
        dsr_out.contractor_name = obj.contractor.name
    if obj.created_by:
        dsr_out.created_by_name = obj.created_by.full_name

    return dsr_out


@dsr_router.get("/project/{project_id}/map")
async def get_dsr_map_points(
    project_id: int,
    current_user: User = Depends(require_permission("dsr.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_project(db, project_id, current_user, load_relations=False)
    result = await db.execute(
        select(
            m.DailySiteReport.latitude,
            m.DailySiteReport.longitude,
            m.DailySiteReport.report_date,
        ).where(
            m.DailySiteReport.project_id == project_id,
            m.DailySiteReport.latitude.isnot(None),
            m.DailySiteReport.longitude.isnot(None),
        )
    )
    rows = result.all()
    return [
        {
            "lat": r[0],
            "lng": r[1],
            "date": r[2],
        }
        for r in rows
    ]


@dsr_router.get("/project/{project_id}/analytics/labour")
async def labour_trend(
    project_id: int,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(require_permission("dsr.view")),
    db: AsyncSession = Depends(get_db_session),
):
    if start_date and end_date and end_date < start_date:
        raise BadRequestError("end_date cannot be before start_date")

    await _get_scoped_project(db, project_id, current_user, load_relations=False)

    query = select(
        m.DailySiteReport.report_date,
        func.sum(m.DailySiteReport.total_labour),
    ).where(m.DailySiteReport.project_id == project_id)

    if start_date:
        query = query.where(m.DailySiteReport.report_date >= start_date)
    if end_date:
        query = query.where(m.DailySiteReport.report_date <= end_date)

    query = query.group_by(m.DailySiteReport.report_date).order_by(
        m.DailySiteReport.report_date
    )

    result = await db.execute(query)
    rows = result.all()
    return [
        {
            "date": r[0],
            "labour": int(r[1] or 0),
        }
        for r in rows
    ]


@dsr_router.get("/project/{project_id}/analytics/contractor")
async def contractor_analytics(
    project_id: int,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(require_permission("dsr.view")),
    db: AsyncSession = Depends(get_db_session),
):
    if start_date and end_date and end_date < start_date:
        raise BadRequestError("end_date cannot be before start_date")

    await _get_scoped_project(db, project_id, current_user, load_relations=False)

    query = (
        select(
            Contractor.name,
            func.count(m.DailySiteReport.id),
        )
        .select_from(m.DailySiteReport)
        .join(
            Contractor, Contractor.id == m.DailySiteReport.contractor_id, isouter=True
        )
        .where(m.DailySiteReport.project_id == project_id)
    )

    if start_date:
        query = query.where(m.DailySiteReport.report_date >= start_date)
    if end_date:
        query = query.where(m.DailySiteReport.report_date <= end_date)

    query = query.group_by(Contractor.name)
    result = await db.execute(query)
    rows = result.all()
    return [
        {
            "contractor": r[0] or "Unknown",
            "entries": r[1],
        }
        for r in rows
    ]


@dsr_router.delete("/{id}")
async def delete_dsr(
    id: int,
    current_user: User = Depends(require_permission("dsr.delete")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(f"Deleting DSR id={id}")
    obj = await _get_scoped_dsr(db, id, current_user, for_update=True)

    if obj.status == "Approved":
        raise ValidationError("Cannot delete approved DSR")

    try:
        await db.delete(obj)
        await db.flush()
        await bump_cache_version(redis, "cache_version:dsr")
    except Exception:
        await db.rollback()
        logger.exception(f"DSR delete failed id={id}")
        raise

    logger.info(f"DSR deleted id={id}")
    return {"success": True, "message": "DSR deleted successfully"}


@dsr_router.get("/{dsr_id}/photos")
async def get_dsr_photos(
    dsr_id: int,
    current_user: User = Depends(require_permission("dsr.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_dsr(db, dsr_id, current_user)
    result = await db.execute(select(m.DSRPhoto).where(m.DSRPhoto.dsr_id == dsr_id))
    rows = result.scalars().all()
    return [{"id": p.id, "url": p.file_url} for p in rows]


@dsr_router.delete("/photo/{photo_id}")
async def delete_dsr_photo(
    photo_id: int,
    current_user: User = Depends(require_permission("dsr.delete")),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(m.DSRPhoto, photo_id)
    if not obj:
        raise NotFoundError("Photo not found")
    await _get_scoped_dsr(db, obj.dsr_id, current_user, for_update=True)

    try:
        await db.delete(obj)
        await db.flush()
    except Exception:
        await db.rollback()
        raise

    return {"status": "success"}


@dsr_router.get("/project/{project_id}/export")
async def export_dsr_excel(
    project_id: int,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    contractor_name: Optional[str] = Query(default=None),
    current_user: User = Depends(require_permission("dsr.export")),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Exporting DSR Excel project_id={project_id}")
    project = await _get_scoped_project(db, project_id, current_user, load_relations=False)

    query = (
        select(m.DailySiteReport, Contractor.name, User.full_name)
        .join(
            Contractor, Contractor.id == m.DailySiteReport.contractor_id, isouter=True
        )
        .join(User, User.id == m.DailySiteReport.created_by_id, isouter=True)
        .where(m.DailySiteReport.project_id == project_id)
    )

    if start_date:
        query = query.where(m.DailySiteReport.report_date >= start_date)
    if end_date:
        query = query.where(m.DailySiteReport.report_date <= end_date)
    if contractor_name:
        contractor_name = contractor_name.strip()
        query = query.where(Contractor.name.ilike(f"%{contractor_name}%"))

    query = query.order_by(m.DailySiteReport.report_date.desc())
    result = await db.execute(query)
    rows = result.all()

    if not rows:
        raise NotFoundError("No DSR data found")

    project_name = project.project_name if project else str(project_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "DSR Report"

    headers = [
        "Date",
        "Project Name",
        "Contractor",
        "Weather",
        "Work Done",
        "Work Planned",
        "Total Labour",
        "Skilled Labour",
        "Unskilled Labour",
        "Material Used",
        "Issues",
        "Remarks",
        "Created By",
    ]
    ws.append(headers)

    for r, c_name, u_name in rows:
        ws.append(
            [
                str(r.report_date),
                project_name,
                c_name,
                r.weather,
                r.work_done,
                r.work_planned,
                r.total_labour,
                r.skilled_labour,
                r.unskilled_labour,
                r.material_used,
                r.issues,
                r.remarks,
                u_name,
            ]
        )

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=dsr_project_{project_id}.xlsx"
        },
    )


@dsr_router.put("/{id}/submit")
async def submit_dsr(
    id: int,
    current_user: User = Depends(require_permission("dsr.edit")),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await _get_scoped_dsr(db, id, current_user, for_update=True)
    if obj.status != "Draft":
        raise ValidationError("Only draft DSR can be submitted")

    obj.status = "Submitted"
    db.add(
        ActivityLog(
            action="SUBMIT_DSR",
            entity="project",
            entity_id=obj.project_id,
            performed_by=current_user.id,
            details={"message": f"Daily Site Report submitted for {obj.report_date}"},
        )
    )
    await db.flush()
    return {"message": "DSR submitted successfully"}


@dsr_router.put("/{id}/approve")
async def approve_dsr(
    id: int,
    current_user: User = Depends(require_permission("dsr.approve")),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await _get_scoped_dsr(db, id, current_user, for_update=True)
    if obj.status != "Submitted":
        raise ValidationError("DSR must be submitted before approval")

    if obj.created_by_id == current_user.id and getattr(current_user, "is_super_admin", False) is not True:
        raise HTTPException(status_code=400, detail="Cannot approve your own DSR")

    obj.status = "Approved"
    db.add(
        ActivityLog(
            action="APPROVE_DSR",
            entity="project",
            entity_id=obj.project_id,
            performed_by=current_user.id,
            details={"message": f"Daily Site Report approved for {obj.report_date}"},
        )
    )
    await db.commit()
    await db.refresh(obj)
    return {"message": "DSR approved successfully"}


@dsr_router.put("/{id}/reject")
async def reject_dsr(
    id: int,
    current_user: User = Depends(require_permission("dsr.approve")),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await _get_scoped_dsr(db, id, current_user, for_update=True)
    if obj.status != "Submitted":
        raise ValidationError("Only submitted DSR can be rejected")

    obj.status = "Draft"
    db.add(
        ActivityLog(
            action="REJECT_DSR",
            entity="project",
            entity_id=obj.project_id,
            performed_by=current_user.id,
            details={"message": f"Daily Site Report rejected for {obj.report_date}"},
        )
    )
    await db.commit()
    await db.refresh(obj)
    return {"message": "DSR rejected and moved to draft"}


@dsr_router.get("/project/{project_id}/analytics/issues")
async def issue_analytics(
    project_id: int,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(require_permission("dsr.view")),
    db: AsyncSession = Depends(get_db_session),
):
    if start_date and end_date and end_date < start_date:
        raise BadRequestError("end_date cannot be before start_date")
    await _get_scoped_project(db, project_id, current_user, load_relations=False)

    base_query = select(m.DailySiteReport).where(
        m.DailySiteReport.project_id == project_id
    )

    if start_date:
        base_query = base_query.where(m.DailySiteReport.report_date >= start_date)

    if end_date:
        base_query = base_query.where(m.DailySiteReport.report_date <= end_date)

    total = await db.scalar(select(func.count()).select_from(base_query.subquery()))
    issues = await db.scalar(
        select(func.count()).select_from(
            base_query.where(m.DailySiteReport.issues.isnot(None)).subquery()
        )
    )

    return {
        "total_reports": int(total or 0),
        "reports_with_issues": int(issues or 0),
    }


# =========================
# ISSUES ROUTER
# =========================
issues_router = APIRouter(
    prefix="/issues",
    tags=["Issues"],
    dependencies=[default_rate_limiter_dependency()],
)


@issues_router.post("", response_model=s.IssueOut)
async def create_issue(
    payload: s.IssueCreate,
    redis=Depends(get_request_redis),
    current_user: User = Depends(require_permission("issues.create")),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Issue create start project_id={payload.project_id}")

    project = await _get_scoped_project(db, payload.project_id, current_user, load_relations=False)

    if not payload.title or not payload.title.strip():
        raise ValidationError("title is required")

    title = payload.title.strip()

    assigned_to_val = getattr(payload, "assigned_to", None)
    if assigned_to_val:
        assigned_user = await db.scalar(
            select(User).where(
                User.id == assigned_to_val,
                User.company_id == current_user.company_id,
            )
        )
        if not assigned_user:
            raise HTTPException(status_code=404, detail="Assigned user not found")

    existing = await db.scalar(
        select(m.Issue).where(
            m.Issue.project_id == payload.project_id, m.Issue.title == title
        )
    )
    if existing:
        raise ConflictError("Issue with same title already exists in this project")

    try:
        data = payload.model_dump()
        data["title"] = title

        for _ in range(3):
            try:
                data["business_id"] = await generate_business_id(
                    db, m.Issue, "business_id", "ISS"
                )

                obj = m.Issue(**data)
                db.add(obj)
                await db.flush()

                db.add(
                    ActivityLog(
                        action="RAISE_ISSUE",
                        entity="project",
                        entity_id=payload.project_id,
                        performed_by=current_user.id,
                        details={"message": f"Issue '{obj.title}' raised"},
                    )
                )
                await db.flush()

                if getattr(obj.priority, "value", str(obj.priority)) == "HIGH":
                    pm = await db.scalar(
                        select(m.ProjectMember.user_id)
                        .join(User, User.id == m.ProjectMember.user_id)
                        .where(
                            m.ProjectMember.project_id == payload.project_id,
                            User.role == UserRole.PROJECT_MANAGER.value,
                        )
                        .limit(1)
                    )
                    if pm:
                        await create_notification(
                            db,
                            user_id=pm,
                            title="Critical Issue Logged",
                            message=f"CRITICAL ISSUE: {obj.title} logged at {project.project_name}",
                            type="alert",
                        )

                break
            except IntegrityError:
                await db.rollback()
                continue
        else:
            raise Exception("Failed to generate unique ISSUE ID")

        await db.refresh(obj)

    except IntegrityError:
        await db.rollback()
        raise ConflictError("Issue with this title already exists in this project")
    except Exception:
        await db.rollback()
        logger.exception("Issue creation failed")
        raise

    logger.info(f"Issue created id={obj.id}")
    await bump_cache_version(redis, VERSION_KEY)
    return s.IssueOut.model_validate(obj)


@issues_router.get("", response_model=PaginatedResponse[s.IssueOut])
async def list_issues(
    pagination: PaginationParams = Depends(),
    status: Optional[s.IssueStatus] = Query(None),
    priority: Optional[s.IssuePriority] = Query(None),
    assigned_to: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    category: Optional[s.IssueCategory] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("id"),
    order: Optional[str] = Query("desc"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("issues.view")),
):
    _check_batch_y_tenant_access(current_user)
    pagination = pagination.normalized()

    base_query = (
        select(m.Issue)
        .join(m.Project, m.Issue.project_id == m.Project.id)
    )
    if getattr(current_user, "is_super_admin", False) is not True:
        base_query = base_query.where(m.Project.company_id == current_user.company_id)

    if project_id is not None:
        await _get_scoped_project(db, project_id, current_user, load_relations=False)
        base_query = base_query.where(m.Issue.project_id == project_id)

    if status is not None:
        base_query = base_query.where(m.Issue.status == status)

    if priority is not None:
        base_query = base_query.where(m.Issue.priority == priority)

    if assigned_to is not None:
        base_query = base_query.where(m.Issue.assigned_to == assigned_to)

    if category is not None:
        base_query = base_query.where(m.Issue.category == category)

    if search and search.strip():
        search_term = f"%{search.strip()}%"
        base_query = base_query.where(
            or_(
                m.Issue.title.ilike(search_term),
                func.coalesce(m.Issue.description, "").ilike(search_term),
            )
        )

    sort_mapping = {
        "id": m.Issue.id,
        "priority": m.Issue.priority,
        "reported_date": m.Issue.reported_date,
        "status": m.Issue.status,
    }
    sort_column = sort_mapping.get(sort_by, m.Issue.id)

    if order and order.lower() == "asc":
        base_query = base_query.order_by(sort_column.asc())
    else:
        base_query = base_query.order_by(sort_column.desc())

    count_query = select(func.count()).select_from(base_query.order_by(None).subquery())
    total = await db.scalar(count_query)

    query = base_query.offset(pagination.offset).limit(pagination.limit)
    rows = (await db.execute(query)).scalars().all()
    items = [s.IssueOut.model_validate(row) for row in rows]

    return PaginatedResponse(
        items=items,
        meta=PaginationMeta(
            total=int(total or 0),
            limit=pagination.limit,
            offset=pagination.offset,
        ),
    )


@issues_router.get(
    "/project/{project_id}", response_model=PaginatedResponse[s.IssueOut]
)
async def get_issues_by_project(
    project_id: int,
    pagination: PaginationParams = Depends(),
    current_user: User = Depends(require_permission("issues.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_project(db, project_id, current_user, load_relations=False)
    pagination = pagination.normalized()

    total = await db.scalar(
        select(func.count())
        .select_from(m.Issue)
        .where(m.Issue.project_id == project_id)
    )

    query = (
        select(m.Issue)
        .where(m.Issue.project_id == project_id)
        .order_by(m.Issue.id.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    rows = (await db.execute(query)).scalars().all()
    items = [s.IssueOut.model_validate(row) for row in rows]

    return PaginatedResponse(
        items=items,
        meta=PaginationMeta(
            total=int(total or 0),
            limit=pagination.limit,
            offset=pagination.offset,
        ),
    )


@issues_router.get("/{id}", response_model=s.IssueOut)
async def get_issue(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("issues.view")),
):
    obj = await _get_scoped_issue(db, id, current_user)
    return s.IssueOut.model_validate(obj)


@issues_router.put("/{id}", response_model=s.IssueOut)
async def update_issue(
    id: int,
    payload: s.IssueUpdate,
    current_user: User = Depends(require_permission("issues.edit")),
    redis=Depends(get_request_redis),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating issue id={id}")
    obj = await _get_scoped_issue(db, id, current_user, for_update=True)

    data = payload.model_dump(exclude_unset=True)

    if "title" in data and data["title"]:
        title = data["title"].strip()
        existing = await db.scalar(
            select(m.Issue).where(
                m.Issue.project_id == obj.project_id,
                m.Issue.title == title,
                m.Issue.id != obj.id,
            )
        )
        if existing:
            raise ConflictError("Issue with same title already exists in this project")
        data["title"] = title

    if "assigned_to" in data and data["assigned_to"] is not None:
        user = await db.scalar(
            select(User).where(
                User.id == data["assigned_to"],
                User.company_id == current_user.company_id,
            )
        )
        if not user:
            raise HTTPException(status_code=404, detail="Assigned user not found")

        is_member = await db.scalar(
            select(func.count())
            .select_from(m.ProjectMember)
            .where(
                m.ProjectMember.project_id == obj.project_id,
                m.ProjectMember.user_id == data["assigned_to"],
            )
        )
        if not is_member:
            raise ValidationError("Assigned user is not part of this project")

    if data.get("status") == s.IssueStatus.CLOSED and not data.get("resolution"):
        raise ValidationError("Resolution is required to close the issue")

    for k, v in data.items():
        setattr(obj, k, v)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ConflictError("Issue with this title already exists in this project")
    except Exception:
        await db.rollback()
        logger.exception(f"Issue update failed id={id}")
        raise

    await db.refresh(obj)
    logger.info(f"Issue updated id={id}")
    await bump_cache_version(redis, VERSION_KEY)
    return s.IssueOut.model_validate(obj)


@issues_router.delete("/{id}")
async def delete_issue(
    id: int,
    current_user: User = Depends(require_permission("issues.delete")),
    redis=Depends(get_request_redis),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Deleting issue id={id}")
    obj = await _get_scoped_issue(db, id, current_user, for_update=True)

    try:
        await db.delete(obj)
        await db.flush()
    except Exception:
        await db.rollback()
        logger.exception(f"Issue delete failed id={id}")
        raise

    logger.info(f"Issue deleted id={id}")
    await bump_cache_version(redis, VERSION_KEY)
    return {"success": True, "message": "Issue deleted successfully"}


# ==========================================================
# Batch X: Work Progress RBAC & Tenant Scoping Helpers
# ==========================================================


def _check_batch_x_wp_tenant_access(current_user: User) -> bool:
    """Check tenant access for Batch X Work Progress routes.
    
    Returns True if Super Admin, False if regular tenant user.
    Raises HTTPException(403) if non-SA user has company_id is None.
    """
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and getattr(current_user, "company_id", None) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to any tenant company",
        )
    return is_sa


async def _get_scoped_project_for_wp(
    db: AsyncSession,
    project_id: int,
    current_user: User,
) -> m.Project:
    """Retrieve project scoped to current tenant (or cross-company if SA).
    
    Returns 404 if not found or belongs to another tenant.
    """
    is_sa = _check_batch_x_wp_tenant_access(current_user)
    stmt = select(m.Project).where(m.Project.id == project_id)
    if not is_sa:
        stmt = stmt.where(m.Project.company_id == current_user.company_id)
    result = await db.execute(stmt)
    project = result.scalars().first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


async def _get_scoped_work_activity(
    db: AsyncSession,
    activity_id: int,
    current_user: User,
    for_update: bool = False,
    load_relations: bool = False,
) -> m.WorkActivity:
    """Retrieve work activity scoped to current tenant via Project.
    
    Returns 404 if not found or belongs to another tenant.
    """
    is_sa = _check_batch_x_wp_tenant_access(current_user)
    stmt = (
        select(m.WorkActivity)
        .join(m.Project, m.Project.id == m.WorkActivity.project_id)
        .where(m.WorkActivity.id == activity_id)
    )
    if not is_sa:
        stmt = stmt.where(m.Project.company_id == current_user.company_id)
    if load_relations:
        stmt = stmt.options(
            selectinload(m.WorkActivity.project),
            selectinload(m.WorkActivity.work_order),
            selectinload(m.WorkActivity.boq_item),
            selectinload(m.WorkActivity.engineer),
        )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    activity = result.scalar_one_or_none()
    if not activity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work activity not found",
        )
    return activity


async def _get_scoped_daily_entry(
    db: AsyncSession,
    entry_id: int,
    current_user: User,
    for_update: bool = False,
) -> m.DailyProgressEntry:
    """Retrieve daily progress entry scoped to current tenant via WorkActivity -> Project.
    
    Returns 404 if not found or belongs to another tenant.
    """
    is_sa = _check_batch_x_wp_tenant_access(current_user)
    stmt = (
        select(m.DailyProgressEntry)
        .join(m.WorkActivity, m.WorkActivity.id == m.DailyProgressEntry.activity_id)
        .join(m.Project, m.Project.id == m.WorkActivity.project_id)
        .where(m.DailyProgressEntry.id == entry_id)
        .options(selectinload(m.DailyProgressEntry.activity))
    )
    if not is_sa:
        stmt = stmt.where(m.Project.company_id == current_user.company_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Daily progress entry not found",
        )
    return entry


async def _validate_scoped_engineer(
    db: AsyncSession,
    engineer_id: int,
    project_id: int,
    current_user: User,
) -> User:
    """Validate engineer exists, belongs to tenant (or SA), is active, is SITE_ENGINEER, and assigned to project."""
    is_sa = _check_batch_x_wp_tenant_access(current_user)
    stmt = select(User).where(User.id == engineer_id)
    if not is_sa:
        stmt = stmt.where(User.company_id == current_user.company_id)
    res = await db.execute(stmt)
    engineer = res.scalar_one_or_none()
    if not engineer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Engineer not found",
        )
    if engineer.role != UserRole.SITE_ENGINEER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected user is not a Site Engineer",
        )
    if not engineer.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Engineer is inactive",
        )
    member_stmt = select(m.ProjectMember).where(
        m.ProjectMember.project_id == project_id,
        m.ProjectMember.user_id == engineer.id,
    )
    member_result = await db.execute(member_stmt)
    if not member_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Engineer is not assigned to this project",
        )
    return engineer


async def _validate_scoped_work_order(
    db: AsyncSession,
    work_order_id: int,
    project_id: int,
    current_user: User,
) -> WorkOrder:
    """Validate work order exists and belongs to project and tenant."""
    is_sa = _check_batch_x_wp_tenant_access(current_user)
    stmt = (
        select(WorkOrder)
        .join(m.Project, m.Project.id == WorkOrder.project_id)
        .where(WorkOrder.id == work_order_id)
    )
    if not is_sa:
        stmt = stmt.where(m.Project.company_id == current_user.company_id)
    res = await db.execute(stmt)
    wo = res.scalar_one_or_none()
    if not wo or wo.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work order not found",
        )
    return wo


async def _validate_scoped_boq(
    db: AsyncSession,
    boq_item_id: int,
    project_id: int,
    current_user: User,
) -> BOQ:
    """Validate BOQ exists and belongs to project and tenant."""
    is_sa = _check_batch_x_wp_tenant_access(current_user)
    stmt = (
        select(BOQ)
        .join(m.Project, m.Project.id == BOQ.project_id)
        .where(BOQ.id == boq_item_id)
    )
    if not is_sa:
        stmt = stmt.where(m.Project.company_id == current_user.company_id)
    res = await db.execute(stmt)
    boq = res.scalar_one_or_none()
    if not boq or boq.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="BOQ item not found",
        )
    return boq


# ==========================================================
# work-progress router
# ==========================================================
work_progress_router = APIRouter(
    prefix="/work-progress",
    tags=["Work Progress"],
    dependencies=[default_rate_limiter_dependency()],
)


def json_serializer(obj):

    if isinstance(obj, Decimal):
        return float(obj)

    if isinstance(obj, (date, datetime)):
        return obj.isoformat()

    if hasattr(obj, "value"):
        return obj.value

    return str(obj)


async def create_activity_log(
    db,
    activity_id,
    action,
    changed_by,
    old_value=None,
    new_value=None,
    remarks=None,
):

    log = m.ActivityHistory(
        activity_id=activity_id,
        action=action,
        old_value=(
            json.loads(json.dumps(old_value, default=json_serializer))
            if old_value
            else None
        ),
        new_value=(
            json.loads(json.dumps(new_value, default=json_serializer))
            if new_value
            else None
        ),
        changed_by=changed_by,
        remarks=remarks,
    )

    db.add(log)


def calculate_activity_status(activity) -> WorkActivityStatus:
    """Pure, side-effect-free status resolver for WorkActivity.
    
    Evaluates business status based on completion_percentage and schedule end_date.
    If completion_percentage is NULL (unset/no progress), it is treated as no progress
    for status determination (DELAY if past deadline, else NOT_STARTED), without
    mutating any attribute on activity.
    """
    raw_pct = getattr(activity, "completion_percentage", None)
    if raw_pct is None:
        pct = Decimal("0.00")
    elif isinstance(raw_pct, Decimal):
        pct = raw_pct
    else:
        pct = Decimal(str(raw_pct))

    end_date = getattr(activity, "end_date", None)
    if pct >= Decimal("100"):
        return WorkActivityStatus.COMPLETED
    elif end_date and end_date < date.today() and pct < Decimal("100"):
        return WorkActivityStatus.DELAY
    elif pct > Decimal("0"):
        return WorkActivityStatus.ON_TRACK
    else:
        return WorkActivityStatus.NOT_STARTED


def update_activity_status(activity):
    """Mutates activity.status on the ORM object for write/persistence workflows."""
    activity.status = calculate_activity_status(activity)


# ======================================================
# 1. create_activity
# ======================================================


@work_progress_router.post(
    "/activities",
    response_model=s.WorkActivityCreateResponse,
)
async def create_activity(
    data: s.WorkActivityCreate,
    current_user: User = Depends(require_permission("work_progress.create")),
    db: AsyncSession = Depends(get_db_session),
):

    try:
        project = await _get_scoped_project_for_wp(db, data.project_id, current_user)
        boq = await _validate_scoped_boq(db, data.boq_item_id, data.project_id, current_user)

        work_order = None
        if data.work_order_id is not None:
            work_order = await _validate_scoped_work_order(db, data.work_order_id, data.project_id, current_user)

        if data.engineer_id is not None:
            await _validate_scoped_engineer(db, data.engineer_id, data.project_id, current_user)

        duplicate_stmt = select(m.WorkActivity).where(
            m.WorkActivity.project_id == data.project_id,
            m.WorkActivity.boq_item_id == data.boq_item_id,
        )
        duplicate_result = await db.execute(duplicate_stmt)
        existing_activity = duplicate_result.scalars().first()
        if existing_activity:
            raise HTTPException(
                status_code=400,
                detail="Activity already exists for this BOQ item",
            )

        activity = m.WorkActivity(
            project_id=data.project_id,
            boq_item_id=data.boq_item_id,
            work_order_id=data.work_order_id,
            activity_name=boq.item_name,
            discipline=boq.category,
            planned_quantity=boq.quantity,
            unit=boq.unit,
            engineer_id=data.engineer_id,
            start_date=data.start_date,
            end_date=data.end_date,
            total_completed=Decimal("0.00"),
            remaining_quantity=boq.quantity.quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            ),
            completion_percentage=Decimal("0.00"),
        )

        update_activity_status(activity)
        db.add(activity)
        await db.flush()

        await create_activity_log(
            db=db,
            activity_id=activity.id,
            action="CREATE",
            changed_by=current_user.id,
            new_value={
                "project_id": activity.project_id,
                "boq_item_id": activity.boq_item_id,
                "work_order_id": activity.work_order_id,
                "activity_name": activity.activity_name,
                "planned_quantity": str(activity.planned_quantity),
                "unit": activity.unit,
                "engineer_id": activity.engineer_id,
                "status": activity.status.value,
            },
            remarks="Work Activity Created",
        )

        await db.commit()
        await db.refresh(activity)

        return s.WorkActivityCreateResponse(
            message="Work Activity created successfully",
            data=activity,
        )

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Duplicate or invalid database record",
        )

    except Exception as e:
        await db.rollback()
        logger.exception("Failed to create work activity: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail="Failed to create work activity",
        )


# =========================================================
# 2. LIST ACTIVITIES
# =========================================================


@work_progress_router.get(
    "/activities",
    response_model=s.WorkActivityListResponse,
)
async def list_activities(
    project_id: int = Query(..., gt=0),
    work_order_id: int | None = Query(default=None, gt=0),
    engineer_id: int | None = Query(default=None, gt=0),
    status: WorkActivityStatus | None = None,
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_permission("work_progress.view")),
    db: AsyncSession = Depends(get_db_session),
):

    try:
        await _get_scoped_project_for_wp(db, project_id, current_user)

        stmt = select(m.WorkActivity).options(
            selectinload(m.WorkActivity.project),
            selectinload(m.WorkActivity.work_order),
            selectinload(m.WorkActivity.boq_item),
            selectinload(m.WorkActivity.engineer),
        )
        count_stmt = select(func.count()).select_from(m.WorkActivity)
        filters = [
            m.WorkActivity.project_id == project_id,
        ]

        if work_order_id is not None:
            await _validate_scoped_work_order(db, work_order_id, project_id, current_user)
            filters.append(m.WorkActivity.work_order_id == work_order_id)

        if engineer_id is not None:
            await _validate_scoped_engineer(db, engineer_id, project_id, current_user)
            filters.append(m.WorkActivity.engineer_id == engineer_id)

        if status is not None:
            filters.append(m.WorkActivity.status == status)

        if search:
            search = search.strip()
            filters.append(
                or_(
                    m.WorkActivity.activity_name.ilike(f"%{search}%"),
                    m.WorkActivity.discipline.ilike(f"%{search}%"),
                )
            )

        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)
        stmt = stmt.order_by(m.WorkActivity.created_at.desc())
        stmt = stmt.offset(offset).limit(limit)

        result = await db.execute(stmt)
        activities = result.scalars().unique().all()
        total_result = await db.execute(count_stmt)
        total_count = total_result.scalar() or 0

        response_data = []
        for act in activities:
            act_out = s.WorkActivityResponse.model_validate(act)
            act_out.status = calculate_activity_status(act)
            response_data.append(act_out)

        return s.WorkActivityListResponse(
            success=True,
            limit=limit,
            offset=offset,
            page_count=len(response_data),
            total_count=total_count,
            data=response_data,
        )

    except HTTPException:
        raise

    except IntegrityError as e:
        logger.exception("Database error while listing work activities: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail="Database error occurred",
        )

    except Exception as e:
        logger.exception("Failed to list work activities: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail="Something went wrong",
        )


# =========================================================
# 3. GET SINGLE ACTIVITY
# =========================================================


@work_progress_router.get(
    "/activities/{activity_id}",
    response_model=s.WorkActivityResponse,
)
async def get_activity(
    activity_id: int = Path(..., gt=0),
    current_user: User = Depends(require_permission("work_progress.view")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        activity = await _get_scoped_work_activity(
            db=db,
            activity_id=activity_id,
            current_user=current_user,
            load_relations=True,
        )
        act_out = s.WorkActivityResponse.model_validate(activity)
        act_out.status = calculate_activity_status(activity)
        return act_out

    except HTTPException:
        raise

    except IntegrityError as e:
        logger.exception("Database error while fetching work activity %s", activity_id, exc_info=e)
        raise HTTPException(
            status_code=500,
            detail="Database error occurred",
        )

    except Exception as e:
        logger.exception("Failed to fetch work activity %s", activity_id, exc_info=e)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch work activity",
        )


# =========================================================
# 4. UPDATE ACTIVITY
# =========================================================


@work_progress_router.put(
    "/activities/{activity_id}",
    response_model=s.WorkActivityUpdateResponse,
)
async def update_activity(
    activity_id: int = Path(..., gt=0),
    data: s.WorkActivityUpdate = Body(...),
    current_user: User = Depends(require_permission("work_progress.edit")),
    db: AsyncSession = Depends(get_db_session),
):

    try:
        activity = await _get_scoped_work_activity(
            db=db,
            activity_id=activity_id,
            current_user=current_user,
            load_relations=True,
        )

        start_date = (
            data.start_date if data.start_date is not None else activity.start_date
        )
        end_date = data.end_date if data.end_date is not None else activity.end_date

        if end_date < start_date:
            raise HTTPException(
                status_code=400,
                detail="End date cannot be before start date",
            )

        old_data = {
            "engineer_id": activity.engineer_id,
            "work_order_id": activity.work_order_id,
            "start_date": activity.start_date,
            "end_date": activity.end_date,
            "status": activity.status.value,
        }

        if data.work_order_id is not None:
            await _validate_scoped_work_order(db, data.work_order_id, activity.project_id, current_user)

        if data.engineer_id is not None:
            await _validate_scoped_engineer(db, data.engineer_id, activity.project_id, current_user)

        update_data = data.model_dump(
            exclude_unset=True,
        )

        if (
            "planned_quantity" in update_data
            and update_data["planned_quantity"] is not None
            and update_data["planned_quantity"] < activity.total_completed
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"planned_quantity ({update_data['planned_quantity']}) cannot "
                    f"be less than already completed quantity "
                    f"({activity.total_completed})."
                ),
            )

        for field, value in update_data.items():
            setattr(
                activity,
                field,
                value,
            )

        activity.remaining_quantity = max(
            Decimal("0.00"),
            (activity.planned_quantity - activity.total_completed),
        ).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        if activity.planned_quantity > Decimal("0"):
            completion = (
                (activity.total_completed / activity.planned_quantity) * Decimal("100")
            ).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            activity.completion_percentage = min(
                completion,
                Decimal("100.00"),
            )
        else:
            activity.completion_percentage = Decimal("0.00")

        update_activity_status(activity)

        new_data = {
            "engineer_id": activity.engineer_id,
            "work_order_id": activity.work_order_id,
            "start_date": activity.start_date,
            "end_date": activity.end_date,
            "status": activity.status.value,
        }

        changed_old = {}
        changed_new = {}
        for key in new_data:
            if old_data.get(key) != new_data.get(key):
                changed_old[key] = old_data.get(key)
                changed_new[key] = new_data.get(key)

        if changed_new:
            await create_activity_log(
                db=db,
                activity_id=activity.id,
                action="UPDATE",
                changed_by=current_user.id,
                old_value=changed_old,
                new_value=changed_new,
                remarks="Work Activity Updated",
            )

        await db.commit()
        await db.refresh(activity)

        return s.WorkActivityUpdateResponse(
            message="Work Activity updated successfully",
            data=activity,
        )

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as e:
        await db.rollback()
        logger.exception(
            "Database error while updating activity %s: %s",
            activity_id,
            str(e),
        )
        raise HTTPException(
            status_code=400,
            detail="Database integrity error",
        )

    except Exception as e:
        await db.rollback()
        logger.exception(
            "Failed to update activity %s: %s",
            activity_id,
            str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to update work activity",
        )


# =========================================================
# 5. DELETE ACTIVITY
# =========================================================


@work_progress_router.delete(
    "/activities/{activity_id}",
    response_model=s.WorkActivityDeleteResponse,
)
async def delete_activity(
    activity_id: int = Path(..., gt=0),
    current_user: User = Depends(require_permission("work_progress.delete")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        activity = await _get_scoped_work_activity(
            db=db,
            activity_id=activity_id,
            current_user=current_user,
        )

        progress_stmt = select(m.DailyProgressEntry.id).where(
            m.DailyProgressEntry.activity_id == activity.id,
        )
        progress_result = await db.execute(progress_stmt)

        if progress_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Cannot delete activity because daily progress records exist.",
            )

        await create_activity_log(
            db=db,
            activity_id=activity.id,
            action="DELETE",
            changed_by=current_user.id,
            old_value={
                "activity_name": activity.activity_name,
                "planned_quantity": str(activity.planned_quantity),
                "total_completed": str(activity.total_completed),
                "remaining_quantity": str(activity.remaining_quantity),
                "completion_percentage": str(activity.completion_percentage),
                "status": activity.status.value,
                "engineer_id": activity.engineer_id,
                "work_order_id": activity.work_order_id,
                "boq_item_id": activity.boq_item_id,
            },
            remarks="Work Activity Deleted",
        )

        await db.flush()

        await db.execute(
            delete(m.ActivityHistory).where(
                m.ActivityHistory.activity_id == activity.id,
            )
        )

        await db.delete(activity)
        await db.commit()

        return s.WorkActivityDeleteResponse(
            message="Work Activity deleted successfully",
        )

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as e:
        await db.rollback()
        logger.exception(
            "Database integrity error while deleting activity %s",
            activity_id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=400,
            detail="Unable to delete work activity",
        )

    except Exception as e:
        await db.rollback()
        logger.exception(
            "Failed to delete work activity %s",
            activity_id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to delete work activity",
        )


# =========================================================
# 6. ADD DAILY PROGRESS
# =========================================================


@work_progress_router.post(
    "/daily-entry",
    response_model=s.DailyProgressWithActivityResponse,
)
async def add_daily_progress(
    data: s.DailyProgressCreate,
    current_user: User = Depends(require_permission("work_progress.create")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        if data.activity_id <= 0:
            raise HTTPException(
                status_code=400,
                detail="Invalid activity ID",
            )

        if data.today_progress <= Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail="Today's progress must be greater than zero",
            )

        if data.remarks:
            data.remarks = data.remarks.strip()

        activity = await _get_scoped_work_activity(
            db=db,
            activity_id=data.activity_id,
            current_user=current_user,
            for_update=True,
        )

        if data.entry_date > date.today():
            raise HTTPException(
                status_code=400,
                detail="Progress date cannot be in the future",
            )

        if data.entry_date < activity.start_date:
            raise HTTPException(
                status_code=400,
                detail="Progress date cannot be before activity start date",
            )

        if activity.end_date and data.entry_date > activity.end_date:
            raise HTTPException(
                status_code=400,
                detail="Progress date cannot be after activity end date",
            )

        if activity.status == WorkActivityStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail="Activity is already completed",
            )

        duplicate_stmt = select(m.DailyProgressEntry).where(
            m.DailyProgressEntry.activity_id == data.activity_id,
            m.DailyProgressEntry.entry_date == data.entry_date,
        )
        duplicate_result = await db.execute(duplicate_stmt)
        duplicate_entry = duplicate_result.scalar_one_or_none()

        if duplicate_entry:
            raise HTTPException(
                status_code=400,
                detail="Progress entry already exists for this date",
            )

        old_data = {
            "total_completed": str(activity.total_completed),
            "remaining_quantity": str(activity.remaining_quantity),
            "completion_percentage": str(activity.completion_percentage),
            "status": activity.status.value,
        }

        current_completed = Decimal(str(activity.total_completed or 0))
        planned_quantity = Decimal(str(activity.planned_quantity or 0))
        today_progress = Decimal(str(data.today_progress))
        remaining_quantity = planned_quantity - current_completed

        if today_progress > remaining_quantity:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Today's progress exceeds remaining quantity "
                    f"({remaining_quantity})."
                ),
            )

        new_completed = (current_completed + today_progress).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        new_remaining = (planned_quantity - new_completed).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        if planned_quantity > 0:
            completion_percentage = (
                (new_completed / planned_quantity) * Decimal("100")
            ).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        else:
            completion_percentage = Decimal("0.00")

        completion_percentage = min(
            completion_percentage,
            Decimal("100.00"),
        )

        progress_entry = m.DailyProgressEntry(
            activity_id=activity.id,
            entry_date=data.entry_date,
            today_progress=today_progress,
            remarks=data.remarks,
            created_by=current_user.id,
        )
        db.add(progress_entry)

        activity.total_completed = new_completed
        activity.remaining_quantity = new_remaining
        activity.completion_percentage = completion_percentage
        update_activity_status(activity)

        work_order = None
        if activity.work_order_id:
            work_order = await db.get(
                WorkOrder,
                activity.work_order_id,
            )
            if work_order:
                total_stmt = select(
                    func.coalesce(
                        func.sum(m.WorkActivity.total_completed),
                        0,
                    )
                ).where(
                    m.WorkActivity.work_order_id == activity.work_order_id,
                )
                total_result = await db.execute(total_stmt)
                work_order.completed_quantity = Decimal(
                    str(total_result.scalar_one())
                ).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )

        new_data = {
            "today_progress": str(today_progress),
            "total_completed": str(activity.total_completed),
            "remaining_quantity": str(activity.remaining_quantity),
            "completion_percentage": str(activity.completion_percentage),
            "status": activity.status.value,
        }

        await create_activity_log(
            db=db,
            activity_id=activity.id,
            action="DAILY_PROGRESS_ADD",
            changed_by=current_user.id,
            old_value=old_data,
            new_value=new_data,
            remarks=(
                f"Added {today_progress} "
                f"{activity.unit} progress on "
                f"{data.entry_date}"
            ),
        )

        await db.flush()
        await db.commit()
        await db.refresh(progress_entry)
        await db.refresh(activity)

        if activity.work_order_id and work_order:
            await db.refresh(work_order)

        return s.DailyProgressWithActivityResponse(
            message="Daily progress added successfully",
            progress=progress_entry,
            activity=activity,
        )

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as e:
        await db.rollback()
        logger.exception(
            "Integrity error while adding daily progress for activity %s",
            data.activity_id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "A progress entry already exists "
                "for this activity on the selected date."
            ),
        )

    except Exception as e:
        await db.rollback()
        logger.exception(
            "Failed to add daily progress for activity %s",
            data.activity_id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to add daily progress",
        )


# =========================================================
# 7. LIST DAILY ENTRIES
# =========================================================


@work_progress_router.get(
    "/daily-entry",
    response_model=s.DailyProgressListResponse,
)
async def list_daily_entries(
    project_id: int = Query(..., gt=0),
    activity_id: int | None = Query(default=None, gt=0),
    work_order_id: int | None = Query(default=None, gt=0),
    engineer_id: int | None = Query(default=None, gt=0),
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_permission("work_progress.view")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        await _get_scoped_project_for_wp(db, project_id, current_user)

        if from_date and to_date and from_date > to_date:
            raise HTTPException(
                status_code=400,
                detail="From date cannot be greater than To date",
            )

        stmt = (
            select(m.DailyProgressEntry)
            .join(
                m.WorkActivity,
                m.DailyProgressEntry.activity_id == m.WorkActivity.id,
            )
            .options(
                selectinload(m.DailyProgressEntry.activity).load_only(
                    m.WorkActivity.id,
                    m.WorkActivity.activity_name,
                    m.WorkActivity.status,
                    m.WorkActivity.engineer_id,
                    m.WorkActivity.project_id,
                    m.WorkActivity.work_order_id,
                )
            )
        )

        count_stmt = (
            select(func.count())
            .select_from(m.DailyProgressEntry)
            .join(
                m.WorkActivity,
                m.DailyProgressEntry.activity_id == m.WorkActivity.id,
            )
        )

        filters = [
            m.WorkActivity.project_id == project_id,
        ]

        if activity_id is not None:
            await _get_scoped_work_activity(db, activity_id, current_user)
            filters.append(m.DailyProgressEntry.activity_id == activity_id)

        if work_order_id is not None:
            await _validate_scoped_work_order(db, work_order_id, project_id, current_user)
            filters.append(m.WorkActivity.work_order_id == work_order_id)

        if engineer_id is not None:
            await _validate_scoped_engineer(db, engineer_id, project_id, current_user)
            filters.append(m.WorkActivity.engineer_id == engineer_id)

        if from_date:
            filters.append(m.DailyProgressEntry.entry_date >= from_date)

        if to_date:
            filters.append(m.DailyProgressEntry.entry_date <= to_date)

        stmt = stmt.where(*filters).order_by(
            m.DailyProgressEntry.entry_date.desc(),
            m.DailyProgressEntry.created_at.desc(),
        )

        count_stmt = count_stmt.where(*filters)
        stmt = stmt.offset(offset).limit(limit)

        result = await db.execute(stmt)
        entries = result.scalars().all()

        total_result = await db.execute(count_stmt)
        total_count = total_result.scalar() or 0

        return s.DailyProgressListResponse(
            success=True,
            limit=limit,
            offset=offset,
            page_count=len(entries),
            total_count=total_count,
            data=entries,
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.exception("Failed to list daily progress entries: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch daily progress entries",
        )


# =========================================================
# 8. UPDATE DAILY ENTRY
# =========================================================


@work_progress_router.put(
    "/daily-entry/{id}",
    response_model=s.DailyProgressWithActivityResponse,
)
async def update_daily_entry(
    data: s.DailyProgressUpdate,
    id: int = Path(..., gt=0),
    current_user: User = Depends(require_permission("work_progress.edit")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        if data.today_progress is not None and data.today_progress <= Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail="Today's progress must be greater than zero",
            )

        if data.remarks is not None:
            data.remarks = data.remarks.strip()

        entry = await _get_scoped_daily_entry(db, id, current_user, for_update=True)
        activity = entry.activity

        if activity is None:
            raise HTTPException(
                status_code=404,
                detail="Work activity not found",
            )

        activity_result = await db.execute(
            select(m.WorkActivity)
            .where(m.WorkActivity.id == activity.id)
            .with_for_update()
        )
        activity = activity_result.scalar_one()

        if activity.status == WorkActivityStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail="Completed activity cannot be updated",
            )

        if data.entry_date is not None:
            if data.entry_date > date.today():
                raise HTTPException(
                    status_code=400,
                    detail="Progress date cannot be in the future",
                )

            if data.entry_date < activity.start_date:
                raise HTTPException(
                    status_code=400,
                    detail="Progress date cannot be before activity start date",
                )

            if activity.end_date and data.entry_date > activity.end_date:
                raise HTTPException(
                    status_code=400,
                    detail="Progress date cannot be after activity end date",
                )

            duplicate_stmt = select(m.DailyProgressEntry).where(
                m.DailyProgressEntry.activity_id == activity.id,
                m.DailyProgressEntry.entry_date == data.entry_date,
                m.DailyProgressEntry.id != entry.id,
            )
            duplicate_result = await db.execute(duplicate_stmt)

            if duplicate_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=400,
                    detail="Progress entry already exists for this date",
                )

        old_data = {
            "entry_date": (str(entry.entry_date)),
            "today_progress": str(entry.today_progress),
            "total_completed": str(activity.total_completed),
            "remaining_quantity": str(activity.remaining_quantity),
            "completion_percentage": str(activity.completion_percentage),
            "status": activity.status.value,
        }

        old_progress = Decimal(str(entry.today_progress or 0))
        current_total = Decimal(str(activity.total_completed or 0))
        planned_quantity = Decimal(str(activity.planned_quantity or 0))

        update_data = data.model_dump(
            exclude_unset=True,
        )

        for field, value in update_data.items():
            setattr(entry, field, value)

        new_progress = Decimal(str(entry.today_progress or 0))
        difference = new_progress - old_progress
        updated_total = current_total + difference

        if updated_total < Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail="Invalid progress calculation",
            )

        if updated_total > planned_quantity:
            remaining = planned_quantity - current_total + old_progress
            raise HTTPException(
                status_code=400,
                detail=(
                    "Updated progress exceeds remaining " f"quantity ({remaining})."
                ),
            )

        activity.total_completed = updated_total.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        activity.remaining_quantity = (planned_quantity - updated_total).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        if planned_quantity > 0:
            activity.completion_percentage = min(
                ((updated_total / planned_quantity) * Decimal("100")).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                ),
                Decimal("100.00"),
            )
        else:
            activity.completion_percentage = Decimal("0.00")

        update_activity_status(activity)

        work_order = None
        if activity.work_order_id:
            work_order = await db.get(
                WorkOrder,
                activity.work_order_id,
            )
            if work_order:
                total_stmt = select(
                    func.coalesce(
                        func.sum(m.WorkActivity.total_completed),
                        Decimal("0"),
                    )
                ).where(
                    m.WorkActivity.work_order_id == activity.work_order_id,
                )
                total_result = await db.execute(total_stmt)
                work_order.completed_quantity = Decimal(
                    str(total_result.scalar_one())
                ).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )

        new_data = {
            "entry_date": str(entry.entry_date),
            "today_progress": str(entry.today_progress),
            "total_completed": str(activity.total_completed),
            "remaining_quantity": str(activity.remaining_quantity),
            "completion_percentage": str(activity.completion_percentage),
            "status": activity.status.value,
        }

        await create_activity_log(
            db=db,
            activity_id=activity.id,
            action="DAILY_PROGRESS_UPDATE",
            changed_by=current_user.id,
            old_value=old_data,
            new_value=new_data,
            remarks=f"Updated daily progress for {entry.entry_date}",
        )

        await db.commit()
        await db.refresh(entry)
        await db.refresh(activity)

        if work_order:
            await db.refresh(work_order)

        return s.DailyProgressWithActivityResponse(
            message="Daily progress updated successfully",
            progress=entry,
            activity=activity,
        )

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as e:
        await db.rollback()
        logger.exception(
            "Integrity error while updating daily progress entry %s",
            id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=400,
            detail="A progress entry already exists for the selected date.",
        )

    except Exception as e:
        await db.rollback()
        logger.exception(
            "Failed to update daily progress entry %s",
            id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to update daily progress",
        )


# =========================================================
# 9. DELETE DAILY ENTRY
# =========================================================


@work_progress_router.delete(
    "/daily-entry/{id}",
    response_model=s.DailyProgressDeleteResponse,
)
async def delete_daily_entry(
    id: int = Path(..., gt=0),
    current_user: User = Depends(require_permission("work_progress.delete")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        entry = await _get_scoped_daily_entry(db, id, current_user, for_update=True)
        activity = entry.activity

        if activity is None:
            raise HTTPException(
                status_code=404,
                detail="Work activity not found",
            )

        activity_result = await db.execute(
            select(m.WorkActivity)
            .where(
                m.WorkActivity.id == activity.id,
            )
            .with_for_update()
        )
        activity = activity_result.scalar_one()

        old_data = {
            "entry_date": str(entry.entry_date),
            "today_progress": str(entry.today_progress),
            "total_completed": str(activity.total_completed),
            "remaining_quantity": str(activity.remaining_quantity),
            "completion_percentage": str(activity.completion_percentage),
            "status": activity.status.value,
        }

        today_progress = Decimal(str(entry.today_progress))
        current_total = Decimal(str(activity.total_completed))
        planned_quantity = Decimal(str(activity.planned_quantity))
        updated_total = current_total - today_progress

        if updated_total < Decimal("0"):
            updated_total = Decimal("0")

        activity.total_completed = updated_total.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        activity.remaining_quantity = (planned_quantity - updated_total).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        if planned_quantity > 0:
            activity.completion_percentage = min(
                ((updated_total / planned_quantity) * Decimal("100")).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                ),
                Decimal("100.00"),
            )
        else:
            activity.completion_percentage = Decimal("0.00")

        update_activity_status(activity)
        await db.delete(entry)

        work_order = None
        if activity.work_order_id:
            work_order = await db.get(
                WorkOrder,
                activity.work_order_id,
            )
            if work_order:
                total_stmt = select(
                    func.coalesce(
                        func.sum(m.WorkActivity.total_completed),
                        Decimal("0"),
                    )
                ).where(
                    m.WorkActivity.work_order_id == activity.work_order_id,
                )
                total_result = await db.execute(total_stmt)
                work_order.completed_quantity = Decimal(
                    str(total_result.scalar_one())
                ).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )

        new_data = {
            "entry_date": str(entry.entry_date),
            "today_progress": "0",
            "total_completed": str(activity.total_completed),
            "remaining_quantity": str(activity.remaining_quantity),
            "completion_percentage": str(activity.completion_percentage),
            "status": activity.status.value,
        }

        await create_activity_log(
            db=db,
            activity_id=activity.id,
            action="DAILY_PROGRESS_DELETE",
            changed_by=current_user.id,
            old_value=old_data,
            new_value=new_data,
            remarks=f"Deleted daily progress entry dated {entry.entry_date}",
        )

        await db.commit()
        await db.refresh(activity)

        if work_order:
            await db.refresh(work_order)

        return s.DailyProgressDeleteResponse(
            message="Daily progress deleted successfully",
        )

    except HTTPException:
        await db.rollback()
        raise

    except IntegrityError as e:
        await db.rollback()
        logger.exception(
            "Integrity error while deleting daily progress entry %s",
            id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=400,
            detail="Unable to delete daily progress entry",
        )

    except Exception as e:
        await db.rollback()
        logger.exception(
            "Failed to delete daily progress entry %s",
            id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to delete daily progress entry",
        )


# =========================================================
# 10. PROJECT SUMMARY (WORK ORDER)
# =========================================================


@work_progress_router.get(
    "/work-order/{work_order_id}/progress-summary",
    response_model=s.WorkOrderProgressSummaryResponse,
)
async def get_work_order_progress_summary(
    work_order_id: int = Path(..., gt=0),
    current_user: User = Depends(require_permission("work_progress.view")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        is_sa = _check_batch_x_wp_tenant_access(current_user)

        stmt = (
            select(WorkOrder)
            .join(m.Project, m.Project.id == WorkOrder.project_id)
            .where(
                WorkOrder.id == work_order_id,
            )
            .options(
                selectinload(
                    WorkOrder.project,
                )
            )
        )
        if not is_sa:
            stmt = stmt.where(m.Project.company_id == current_user.company_id)

        result = await db.execute(stmt)
        work_order = result.scalar_one_or_none()

        if work_order is None:
            raise HTTPException(
                status_code=404,
                detail="Work order not found",
            )

        activity_stmt = (
            select(
                m.WorkActivity,
            )
            .where(
                m.WorkActivity.work_order_id == work_order_id,
            )
        )
        activity_result = await db.execute(activity_stmt)
        activities = activity_result.scalars().all()

        total_activities = len(activities)
        completed_activities = 0
        on_track_activities = 0
        delayed_activities = 0
        not_started_activities = 0
        planned_quantity = Decimal("0.00")
        completed_quantity = Decimal("0.00")
        remaining_quantity = Decimal("0.00")
        total_completion_pct = Decimal("0.00")

        for act in activities:
            st = calculate_activity_status(act)
            if st == WorkActivityStatus.COMPLETED:
                completed_activities += 1
            elif st == WorkActivityStatus.ON_TRACK:
                on_track_activities += 1
            elif st == WorkActivityStatus.DELAY:
                delayed_activities += 1
            else:
                not_started_activities += 1

            if act.planned_quantity:
                planned_quantity += act.planned_quantity
            if act.total_completed:
                completed_quantity += act.total_completed
            if act.remaining_quantity:
                remaining_quantity += act.remaining_quantity
            if act.completion_percentage:
                total_completion_pct += act.completion_percentage

        average_progress = (
            (total_completion_pct / Decimal(total_activities)).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            if total_activities > 0
            else Decimal("0.00")
        )

        planned_quantity = planned_quantity.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        completed_quantity = completed_quantity.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        remaining_quantity = remaining_quantity.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        completion_percentage = Decimal("0.00")
        if planned_quantity > 0:
            completion_percentage = (
                (completed_quantity / planned_quantity) * Decimal("100")
            ).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            completion_percentage = min(
                completion_percentage,
                Decimal("100.00"),
            )

        if total_activities == 0:
            work_order_status = WorkActivityStatus.NOT_STARTED
        elif completion_percentage >= Decimal("100.00"):
            work_order_status = WorkActivityStatus.COMPLETED
        elif delayed_activities > 0:
            work_order_status = WorkActivityStatus.DELAY
        elif completed_quantity > Decimal("0"):
            work_order_status = WorkActivityStatus.ON_TRACK
        else:
            work_order_status = WorkActivityStatus.NOT_STARTED

        return s.WorkOrderProgressSummaryResponse(
            message="Work order progress fetched successfully",
            work_order=s.WorkOrderProgressSummary(
                id=work_order.id,
                work_order_number=work_order.work_order_number,
                project_id=work_order.project_id,
                planned_quantity=planned_quantity,
                completed_quantity=completed_quantity,
                remaining_quantity=remaining_quantity,
                completion_percentage=completion_percentage,
                average_progress=average_progress,
                status=work_order_status,
            ),
            activities=s.WorkOrderActivitySummary(
                total=total_activities,
                completed=completed_activities,
                on_track=on_track_activities,
                delayed=delayed_activities,
                not_started=not_started_activities,
            ),
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(
            "Failed to fetch work order progress summary for work order %s",
            work_order_id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch work order progress summary",
        )


# =========================================================
# 11. SITE ENGINEER TODAY PROGRESS
# =========================================================


@work_progress_router.get(
    "/site-engineer/today-progress",
    response_model=s.TodayProgressResponse,
)
async def today_progress(
    engineer_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_permission("work_progress.view")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        is_sa = _check_batch_x_wp_tenant_access(current_user)

        if engineer_id is not None and not is_sa:
            eng_stmt = select(User).where(User.id == engineer_id, User.company_id == current_user.company_id)
            eng_res = await db.execute(eng_stmt)
            if not eng_res.scalar_one_or_none():
                raise HTTPException(status_code=404, detail="Engineer not found")

        stmt = (
            select(m.DailyProgressEntry)
            .join(
                m.WorkActivity,
                m.WorkActivity.id == m.DailyProgressEntry.activity_id,
            )
            .join(
                m.Project,
                m.Project.id == m.WorkActivity.project_id,
            )
            .options(
                selectinload(m.DailyProgressEntry.activity),
            )
            .where(
                m.DailyProgressEntry.entry_date == date.today(),
            )
        )

        count_stmt = (
            select(func.count())
            .select_from(m.DailyProgressEntry)
            .join(
                m.WorkActivity,
                m.WorkActivity.id == m.DailyProgressEntry.activity_id,
            )
            .join(
                m.Project,
                m.Project.id == m.WorkActivity.project_id,
            )
            .where(
                m.DailyProgressEntry.entry_date == date.today(),
            )
        )

        # Non-Super Admin: MUST scope to Project.company_id == current_user.company_id
        if not is_sa:
            stmt = stmt.where(m.Project.company_id == current_user.company_id)
            count_stmt = count_stmt.where(m.Project.company_id == current_user.company_id)

        # Site Engineer -> only own entries
        if current_user.role == UserRole.SITE_ENGINEER:
            stmt = stmt.where(
                m.WorkActivity.engineer_id == current_user.id,
            )
            count_stmt = count_stmt.where(
                m.WorkActivity.engineer_id == current_user.id,
            )

        # Admin / PM / Others -> optional engineer filter
        elif engineer_id:
            stmt = stmt.where(
                m.WorkActivity.engineer_id == engineer_id,
            )
            count_stmt = count_stmt.where(
                m.WorkActivity.engineer_id == engineer_id,
            )

        stmt = (
            stmt.order_by(
                m.DailyProgressEntry.created_at.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        result = await db.execute(stmt)
        entries = result.scalars().all()

        total_result = await db.execute(count_stmt)
        total_count = total_result.scalar() or 0

        return s.TodayProgressResponse(
            success=True,
            message="Today's progress fetched successfully",
            engineer_id=engineer_id if engineer_id else current_user.id,
            entry_date=date.today(),
            limit=limit,
            offset=offset,
            page_count=len(entries),
            total_count=total_count,
            data=entries,
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(
            "Failed to fetch today's progress",
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch today's progress",
        )


# =========================================================
# 12. ACTIVITY HISTORY
# =========================================================


@work_progress_router.get(
    "/progress-history",
    response_model=s.WorkProgressHistoryResponse,
)
async def get_work_progress_history(
    activity_id: int | None = Query(default=None, gt=0),
    project_id: int | None = Query(default=None, gt=0),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_permission("work_progress.view")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        _check_batch_x_wp_tenant_access(current_user)

        if from_date and to_date and from_date > to_date:
            raise HTTPException(
                status_code=400,
                detail="from_date cannot be greater than to_date",
            )

        if not activity_id and not project_id:
            raise HTTPException(
                status_code=400,
                detail="Must provide either activity_id or project_id",
            )

        if project_id:
            await _get_scoped_project_for_wp(db, project_id, current_user)

        if activity_id:
            await _get_scoped_work_activity(db, activity_id, current_user)

        filters = []
        if activity_id:
            filters.append(m.DailyProgressEntry.activity_id == activity_id)

        if project_id:
            filters.append(m.WorkActivity.project_id == project_id)

        if from_date:
            filters.append(m.DailyProgressEntry.entry_date >= from_date)

        if to_date:
            filters.append(m.DailyProgressEntry.entry_date <= to_date)

        history_stmt = (
            select(
                m.DailyProgressEntry,
                m.WorkActivity,
            )
            .join(m.WorkActivity, m.DailyProgressEntry.activity_id == m.WorkActivity.id)
            .where(*filters)
            .order_by(
                m.WorkActivity.id.asc(),
                m.DailyProgressEntry.entry_date.asc(),
                m.DailyProgressEntry.id.asc(),
            )
        )

        count_stmt = (
            select(func.count())
            .select_from(m.DailyProgressEntry)
            .join(m.WorkActivity, m.DailyProgressEntry.activity_id == m.WorkActivity.id)
            .where(*filters)
        )

        history_stmt = history_stmt.offset(offset).limit(limit)
        history_result = await db.execute(history_stmt)
        progress_entries = history_result.all()

        total_result = await db.execute(count_stmt)
        total_count = total_result.scalar() or 0

        running_totals = {}
        history_items = []

        for progress, activity in progress_entries:
            if activity.id not in running_totals:
                running_totals[activity.id] = Decimal("0.00")

            today_progress_val = Decimal(str(progress.today_progress or 0))
            running_totals[activity.id] += today_progress_val

            remaining_quantity = Decimal(str(activity.planned_quantity)) - running_totals[activity.id]
            if remaining_quantity < Decimal("0"):
                remaining_quantity = Decimal("0.00")

            history_items.append(
                s.WorkProgressHistoryItem(
                    id=progress.id,
                    activity_id=activity.id,
                    activity_name=activity.activity_name,
                    entry_date=progress.entry_date,
                    today_progress=progress.today_progress,
                    running_total=running_totals[activity.id].quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    ),
                    remaining_quantity=remaining_quantity.quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    ),
                    remarks=progress.remarks,
                    created_at=progress.created_at,
                )
            )

        pagination = s.PaginationResponse(
            total=total_count,
            limit=limit,
            offset=offset,
            page_count=len(history_items),
        )

        return s.WorkProgressHistoryResponse(
            message="Work progress history fetched successfully",
            history=history_items,
            pagination=pagination,
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(
            "Failed to fetch progress history for activity %s",
            activity_id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch activity progress history",
        )


# =========================================================
# 13. PROJECT PROGRESS SUMMARY
# =========================================================


@work_progress_router.get(
    "/project/{project_id}/summary",
    response_model=s.WorkProgressProjectSummaryResponse,
    summary="Project Progress Summary",
)
async def project_progress_summary(
    project_id: int = Path(..., gt=0),
    current_user: User = Depends(require_permission("work_progress.view")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        project = await _get_scoped_project_for_wp(db, project_id, current_user)

        summary_stmt = select(
            func.count(m.WorkActivity.id).label("total_activities"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            m.WorkActivity.status == WorkActivityStatus.COMPLETED,
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("completed_activities"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            m.WorkActivity.status == WorkActivityStatus.ON_TRACK,
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("on_track_activities"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            m.WorkActivity.status == WorkActivityStatus.DELAY,
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("delayed_activities"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            m.WorkActivity.status == WorkActivityStatus.NOT_STARTED,
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("not_started_activities"),
            func.coalesce(
                func.sum(m.WorkActivity.planned_quantity),
                Decimal("0.00"),
            ).label("planned_quantity"),
            func.coalesce(
                func.sum(m.WorkActivity.total_completed),
                Decimal("0.00"),
            ).label("completed_quantity"),
            func.coalesce(
                func.sum(m.WorkActivity.remaining_quantity),
                Decimal("0.00"),
            ).label("remaining_quantity"),
            func.coalesce(
                func.avg(m.WorkActivity.completion_percentage),
                Decimal("0.00"),
            ).label("average_progress"),
        ).where(
            m.WorkActivity.project_id == project_id,
        )

        result = await db.execute(summary_stmt)
        (
            total_activities,
            completed_activities,
            on_track_activities,
            delayed_activities,
            not_started_activities,
            planned_quantity,
            completed_quantity,
            remaining_quantity,
            average_progress,
        ) = result.one()

        if planned_quantity > Decimal("0"):
            overall_progress = (
                (completed_quantity / planned_quantity) * Decimal("100")
            ).quantize(Decimal("0.01"))
        else:
            overall_progress = Decimal("0.00")

        project_info = s.ProjectInfoResponse(
            id=project.id,
            project_name=project.project_name,
        )

        summary = s.ProjectProgressSummaryData(
            total_activities=total_activities,
            completed_activities=completed_activities,
            on_track_activities=on_track_activities,
            delayed_activities=delayed_activities,
            not_started_activities=not_started_activities,
            planned_quantity=Decimal(str(planned_quantity or 0)).quantize(
                Decimal("0.01")
            ),
            completed_quantity=Decimal(str(completed_quantity or 0)).quantize(
                Decimal("0.01")
            ),
            remaining_quantity=Decimal(str(remaining_quantity or 0)).quantize(
                Decimal("0.01")
            ),
            overall_progress_percentage=overall_progress,
            average_activity_progress=Decimal(str(average_progress or 0)).quantize(
                Decimal("0.01")
            ),
        )

        logger.info(
            "Project summary generated successfully. "
            "Project=%s Activities=%s Progress=%s%%",
            project.id,
            total_activities,
            overall_progress,
        )

        return s.WorkProgressProjectSummaryResponse(
            success=True,
            message="Project progress summary fetched successfully",
            project=project_info,
            summary=summary,
        )

    except HTTPException:
        raise

    except IntegrityError as e:
        logger.exception(
            "Database error while fetching project progress summary %s",
            project_id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail="Database error occurred",
        )

    except Exception as e:
        logger.exception(
            "Failed to fetch project summary %s : %s",
            project_id,
            str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch project progress summary",
        )


# =========================================================
# 14. DELAYED ACTIVITIES
# =========================================================


@work_progress_router.get(
    "/project/{project_id}/delayed-activities",
    response_model=s.DelayedActivityListResponse,
    summary="Get Delayed Activities",
)
async def get_delayed_activities(
    project_id: int = Path(..., gt=0),
    engineer_id: int | None = Query(None),
    work_order_id: int | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("work_progress.view")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        await _get_scoped_project_for_wp(db, project_id, current_user)

        filters = [
            m.WorkActivity.project_id == project_id,
            or_(
                m.WorkActivity.status == WorkActivityStatus.DELAY,
                and_(
                    m.WorkActivity.end_date < date.today(),
                    m.WorkActivity.status != WorkActivityStatus.COMPLETED,
                ),
            ),
        ]

        if engineer_id:
            await _validate_scoped_engineer(db, engineer_id, project_id, current_user)
            filters.append(m.WorkActivity.engineer_id == engineer_id)

        if work_order_id:
            await _validate_scoped_work_order(db, work_order_id, project_id, current_user)
            filters.append(m.WorkActivity.work_order_id == work_order_id)

        count_stmt = select(func.count(m.WorkActivity.id)).where(*filters)
        total_count = await db.scalar(count_stmt)

        stmt = (
            select(m.WorkActivity)
            .where(*filters)
            .options(
                selectinload(m.WorkActivity.engineer),
                selectinload(m.WorkActivity.work_order),
            )
            .order_by(
                m.WorkActivity.end_date.asc(),
                m.WorkActivity.created_at.asc(),
            )
            .offset(offset)
            .limit(limit)
        )

        result = await db.execute(stmt)
        activities = result.scalars().all()

        delayed_list = []
        for activity in activities:
            delayed_days = (
                max(
                    0,
                    (date.today() - activity.end_date).days,
                )
                if activity.end_date
                else 0
            )

            delayed_list.append(
                s.DelayedActivityResponse(
                    id=activity.id,
                    activity_name=activity.activity_name,
                    discipline=activity.discipline,
                    work_order_id=activity.work_order_id,
                    engineer_id=activity.engineer_id,
                    planned_quantity=(
                        activity.planned_quantity or Decimal("0.00")
                    ).quantize(Decimal("0.01")),
                    completed_quantity=(
                        activity.total_completed or Decimal("0.00")
                    ).quantize(Decimal("0.01")),
                    remaining_quantity=(
                        activity.remaining_quantity or Decimal("0.00")
                    ).quantize(Decimal("0.01")),
                    completion_percentage=(
                        activity.completion_percentage or Decimal("0.00")
                    ).quantize(Decimal("0.01")),
                    start_date=activity.start_date,
                    end_date=activity.end_date,
                    delayed_days=delayed_days,
                    status=activity.status,
                )
            )

        page_count = len(delayed_list)

        return s.DelayedActivityListResponse(
            success=True,
            message="Delayed activities fetched successfully",
            limit=limit,
            offset=offset,
            page_count=page_count,
            total_count=total_count or 0,
            data=delayed_list,
        )

    except HTTPException:
        raise

    except IntegrityError as e:
        logger.exception(
            "Database error while fetching delayed activities for project %s",
            project_id,
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail="Database error occurred",
        )

    except Exception as e:
        logger.exception(
            "Failed to fetch delayed activities for project %s: %s",
            project_id,
            str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch delayed activities",
        )


# ==================================
# 15. work progress pdf report
# ==================================


@work_progress_router.get("/reports/pdf")
async def work_progress_pdf_report(
    project_id: int = Query(..., gt=0),
    current_user: User = Depends(require_permission("work_progress.export")),
    db: AsyncSession = Depends(get_db_session),
):

    try:
        project = await _get_scoped_project_for_wp(db, project_id, current_user)

        activity_result = await db.execute(
            select(m.WorkActivity)
            .where(m.WorkActivity.project_id == project_id)
            .order_by(m.WorkActivity.created_at.desc())
        )
        activities = activity_result.scalars().all()

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            rightMargin=20,
            leftMargin=20,
            topMargin=20,
            bottomMargin=20,
        )
        styles = getSampleStyleSheet()
        elements = []

        project_name = (
            getattr(project, "project_name", None)
            or getattr(project, "name", None)
            or getattr(project, "title", None)
            or f"Project-{project.id}"
        )

        elements.append(Paragraph("WORK PROGRESS REPORT", styles["Title"]))
        elements.append(Spacer(1, 10))
        elements.append(Paragraph(f"Project : {project_name}", styles["Heading2"]))
        elements.append(Paragraph(f"Project ID : {project.id}", styles["Normal"]))
        elements.append(
            Paragraph(
                f"Generated On : {datetime.now().strftime('%d-%m-%Y %H:%M')}",
                styles["Normal"],
            )
        )
        elements.append(Spacer(1, 20))

        table_data = [["Activity", "Planned", "Completed", "Remaining", "%", "Status"]]
        total_planned = Decimal("0")
        total_completed = Decimal("0")
        delayed_activities = []

        for activity in activities:
            planned_qty = Decimal(str(activity.planned_quantity or 0))
            completed_qty = Decimal(str(activity.total_completed or 0))
            remaining_qty = Decimal(str(activity.remaining_quantity or 0))
            completion_pct = Decimal(str(activity.completion_percentage or 0))
            status_value = (
                activity.status.value
                if hasattr(activity.status, "value")
                else str(activity.status or "UNKNOWN")
            )
            total_planned += planned_qty
            total_completed += completed_qty

            table_data.append(
                [
                    str(activity.activity_name or ""),
                    str(planned_qty),
                    str(completed_qty),
                    str(remaining_qty),
                    str(completion_pct),
                    status_value,
                ]
            )

            if status_value == WorkActivityStatus.DELAY.value:
                delayed_activities.append(activity)

        table = PdfTable(table_data)
        table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, 0),
                        pdf_colors.HexColor("#1F4E78"),
                    ),
                    (
                        "TEXTCOLOR",
                        (0, 0),
                        (-1, 0),
                        pdf_colors.white,
                    ),
                    (
                        "GRID",
                        (0, 0),
                        (-1, -1),
                        1,
                        pdf_colors.black,
                    ),
                    (
                        "FONTNAME",
                        (0, 0),
                        (-1, 0),
                        "Helvetica-Bold",
                    ),
                    (
                        "FONTSIZE",
                        (0, 0),
                        (-1, -1),
                        9,
                    ),
                ]
            )
        )

        elements.append(table)
        elements.append(Spacer(1, 20))

        overall_completion = Decimal("0")
        if total_planned > 0:
            overall_completion = (
                (total_completed / total_planned) * Decimal("100")
            ).quantize(Decimal("0.01"))

        elements.append(Paragraph("PROJECT SUMMARY", styles["Heading2"]))
        elements.append(
            Paragraph(f"Total Activities : {len(activities)}", styles["Normal"])
        )
        elements.append(
            Paragraph(f"Total Planned Quantity : {total_planned}", styles["Normal"])
        )
        elements.append(
            Paragraph(f"Total Completed Quantity : {total_completed}", styles["Normal"])
        )
        elements.append(
            Paragraph(
                f"Overall Completion : {overall_completion}%",
                styles["Normal"],
            )
        )

        elements.append(PageBreak())
        elements.append(Paragraph("DELAYED ACTIVITIES", styles["Heading1"]))

        delay_table = [
            [
                "Activity",
                "End Date",
                "Completion %",
            ]
        ]

        for item in delayed_activities:
            delay_table.append(
                [
                    str(item.activity_name),
                    str(item.end_date),
                    str(item.completion_percentage),
                ]
            )

        if len(delay_table) > 1:
            dt = PdfTable(delay_table)
            dt.setStyle(
                TableStyle(
                    [
                        (
                            "BACKGROUND",
                            (0, 0),
                            (-1, 0),
                            pdf_colors.lightgrey,
                        ),
                        (
                            "GRID",
                            (0, 0),
                            (-1, -1),
                            1,
                            pdf_colors.black,
                        ),
                        (
                            "FONTNAME",
                            (0, 0),
                            (-1, 0),
                            "Helvetica-Bold",
                        ),
                    ]
                )
            )
            elements.append(dt)
        else:
            elements.append(Paragraph("No delayed activities found.", styles["Normal"]))

        doc.build(elements)
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=work_progress_{project_id}.pdf"
            },
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(
            "Failed to generate PDF report for project %s",
            project_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to generate work progress PDF report",
        )


# ==========================================
# 16. work progress excel report
# ==========================================


@work_progress_router.get("/reports/excel")
async def work_progress_excel_report(
    project_id: int = Query(..., gt=0),
    current_user: User = Depends(require_permission("work_progress.export")),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        project = await _get_scoped_project_for_wp(db, project_id, current_user)

        project_name = (
            getattr(project, "project_name", None)
            or getattr(project, "name", None)
            or getattr(project, "title", None)
            or f"Project-{project.id}"
        )

        result = await db.execute(
            select(m.WorkActivity)
            .where(m.WorkActivity.project_id == project_id)
            .order_by(m.WorkActivity.created_at.desc())
        )
        activities = result.scalars().all()

        wb = Workbook()
        ws = wb.active
        ws.title = "Activities"

        headers = [
            "Activity Name",
            "Planned Qty",
            "Completed Qty",
            "Remaining Qty",
            "Completion %",
            "Status",
            "Start Date",
            "End Date",
        ]

        header_fill = PatternFill(
            start_color="1F4E78",
            end_color="1F4E78",
            fill_type="solid",
        )

        header_font = Font(
            bold=True,
            color="FFFFFF",
        )

        for col_num, header in enumerate(headers, start=1):
            cell = ws.cell(
                row=1,
                column=col_num,
            )
            cell.value = header
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")

        row = 2
        for activity in activities:
            status_value = (
                activity.status.value
                if hasattr(activity.status, "value")
                else str(activity.status or "")
            )

            ws.cell(
                row=row,
                column=1,
            ).value = (
                activity.activity_name or ""
            )

            ws.cell(
                row=row,
                column=2,
            ).value = float(activity.planned_quantity or 0)

            ws.cell(
                row=row,
                column=3,
            ).value = float(activity.total_completed or 0)

            ws.cell(
                row=row,
                column=4,
            ).value = float(activity.remaining_quantity or 0)

            ws.cell(
                row=row,
                column=5,
            ).value = float(activity.completion_percentage or 0)

            ws.cell(
                row=row,
                column=6,
            ).value = status_value

            ws.cell(
                row=row,
                column=7,
            ).value = (
                activity.start_date.strftime("%d-%m-%Y") if activity.start_date else ""
            )

            ws.cell(
                row=row,
                column=8,
            ).value = (
                activity.end_date.strftime("%d-%m-%Y") if activity.end_date else ""
            )

            row += 1

        summary_sheet = wb.create_sheet(title="Summary")
        total_activities = len(activities)
        total_planned = sum(float(x.planned_quantity or 0) for x in activities)
        total_completed = sum(float(x.total_completed or 0) for x in activities)
        total_remaining = sum(float(x.remaining_quantity or 0) for x in activities)
        total_delayed = len(
            [
                x
                for x in activities
                if (
                    hasattr(x.status, "value")
                    and x.status.value == WorkActivityStatus.DELAY.value
                )
            ]
        )

        completion_percentage = 0
        if total_planned > 0:
            completion_percentage = round(
                (total_completed / total_planned) * 100,
                2,
            )

        summary_sheet.append(["Project Name", project_name])
        summary_sheet.append(["Project ID", project.id])
        summary_sheet.append(["Total Activities", total_activities])
        summary_sheet.append(["Total Planned Qty", total_planned])
        summary_sheet.append(["Total Completed Qty", total_completed])
        summary_sheet.append(["Total Remaining Qty", total_remaining])
        summary_sheet.append(["Completion %", completion_percentage])
        summary_sheet.append(["Delayed Activities", total_delayed])

        delay_sheet = wb.create_sheet(title="Delayed Activities")
        delay_sheet.append(
            [
                "Activity",
                "End Date",
                "Completion %",
                "Status",
            ]
        )

        for item in activities:
            status_value = (
                item.status.value
                if hasattr(item.status, "value")
                else str(item.status or "")
            )

            if status_value == WorkActivityStatus.DELAY.value:
                delay_sheet.append(
                    [
                        item.activity_name or "",
                        (item.end_date.strftime("%d-%m-%Y") if item.end_date else ""),
                        float(item.completion_percentage or 0),
                        status_value,
                    ]
                )

        for sheet in wb.worksheets:
            for column in sheet.columns:
                max_length = 0
                column_letter = get_column_letter(column[0].column)
                for cell in column:
                    try:
                        if cell.value:
                            max_length = max(
                                max_length,
                                len(str(cell.value)),
                            )
                    except Exception:
                        pass
                sheet.column_dimensions[column_letter].width = max_length + 5

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        return StreamingResponse(
            output,
            media_type=(
                "application/vnd.openxmlformats-" "officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": f"attachment; filename=work_progress_{project_id}.xlsx"
            },
        )

    except HTTPException:
        raise

    except Exception as e:
        logger.exception(
            "Failed to generate Excel report for project %s",
            project_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to generate work progress Excel report",
        )



# ===================== QC =====================

from fastapi import Form, File, UploadFile, Depends, HTTPException
from uuid import uuid4
import os, shutil

UPLOAD_DIR_QC = "uploads/qc"
os.makedirs(UPLOAD_DIR_QC, exist_ok=True)


def save_qc_file(file: UploadFile) -> str:
    ext = file.filename.split(".")[-1].lower()
    filename = f"{uuid4()}.{ext}"

    path = os.path.join(UPLOAD_DIR_QC, filename)

    with open(path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return f"/uploads/qc/{filename}"


async def validate_and_save_qc_file(file: UploadFile) -> str:
    allowed_extensions = {"jpg", "jpeg", "png", "webp", "pdf"}
    ext = file.filename.split(".")[-1].lower() if file.filename and "." in file.filename else ""

    content_type = file.content_type or ""
    is_valid_type = content_type.startswith("image/") or content_type in ("application/pdf", "application/x-pdf") or ext == "pdf"

    if not is_valid_type or ext not in allowed_extensions:
        raise AppError(400, "Only image (jpg, jpeg, png, webp) and PDF files allowed")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise AppError(400, "File too large (max 10MB)")

    file.file.seek(0)

    return await run_in_threadpool(save_qc_file, file)


qc_router = APIRouter(prefix="/qc", tags=["QC"])


@qc_router.post("", response_model=s.QCOut)
async def create_qc(
    payload: s.QCCreate = Depends(),
    report_file: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_permission("qc.create")),
    db: AsyncSession = Depends(get_db_session),
):
    if current_user.company_id is None and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="User without company cannot create QC records")

    try:
        await assert_project_access(db, project_id=payload.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Project not found")

    await assert_task_project(db, payload.task_id, payload.project_id)

    file_url = None
    if report_file and report_file.filename and report_file.filename.strip():
        file_url = await validate_and_save_qc_file(report_file)

    obj = m.QCRecord(**payload.model_dump(), report_file_url=file_url)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@qc_router.get("/{qc_id}", response_model=s.QCOut)
async def get_qc(
    qc_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("qc.view")),
):
    obj = await db.get(m.QCRecord, qc_id)
    if not obj:
        raise HTTPException(status_code=404, detail="QC record not found")
    try:
        await assert_project_access(db, project_id=obj.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="QC record not found")
    return obj


@qc_router.get("", response_model=PaginatedResponse[s.QCOut])
async def list_qc(
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(require_permission("qc.view")),
    db: AsyncSession = Depends(get_db_session),
):
    if project_id is not None:
        try:
            await assert_project_access(db, project_id=project_id, current_user=current_user)
        except Exception:
            raise HTTPException(status_code=404, detail="Project not found")
        query = select(m.QCRecord).where(m.QCRecord.project_id == project_id)
    else:
        if current_user.company_id is not None:
            query = (
                select(m.QCRecord)
                .join(m.Project, m.QCRecord.project_id == m.Project.id)
                .where(m.Project.company_id == current_user.company_id)
            )
        elif current_user.is_super_admin:
            return PaginatedResponse(items=[], meta=PaginationMeta(total=0, limit=limit, offset=offset))
        else:
            return PaginatedResponse(items=[], meta=PaginationMeta(total=0, limit=limit, offset=offset))

    if task_id:
        query = query.where(m.QCRecord.task_id == task_id)
    if status:
        query = query.where(m.QCRecord.status == status)

    count = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.execute(query.limit(limit).offset(offset))).scalars().all()

    return PaginatedResponse(
        items=rows, meta=PaginationMeta(total=count, limit=limit, offset=offset)
    )


@qc_router.put("/{qc_id}", response_model=s.QCOut)
async def update_qc(
    qc_id: int,
    data: s.QCCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("qc.edit")),
):
    obj = await db.get(m.QCRecord, qc_id)
    if not obj:
        raise HTTPException(status_code=404, detail="QC record not found")
    try:
        await assert_project_access(db, project_id=obj.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="QC record not found")

    if data.project_id != obj.project_id:
        try:
            await assert_project_access(db, project_id=data.project_id, current_user=current_user)
        except Exception:
            raise HTTPException(status_code=404, detail="Target project not found")

    if data.task_id:
        await assert_task_project(db, data.task_id, data.project_id)

    for k, v in data.dict().items():
        setattr(obj, k, v)

    await db.commit()
    await db.refresh(obj)
    return obj


@qc_router.delete("/{qc_id}")
async def delete_qc(
    qc_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("qc.delete")),
):
    obj = await db.get(m.QCRecord, qc_id)
    if not obj:
        raise HTTPException(status_code=404, detail="QC record not found")
    try:
        await assert_project_access(db, project_id=obj.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="QC record not found")

    await db.delete(obj)
    await db.commit()
    return {"message": "QC deleted"}


# ===================== SAFETY =====================

safety_router = APIRouter(prefix="/safety", tags=["Safety"])


@safety_router.post("", response_model=s.SafetyOut)
async def create_incident(
    data: s.SafetyCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("safety.create")),
):
    if current_user.company_id is None and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="User without company cannot log safety incidents")

    try:
        await assert_project_access(db, project_id=data.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Project not found")

    await assert_task_project(db, data.task_id, data.project_id)
    obj = m.SafetyIncident(**data.dict())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@safety_router.get("/{id}", response_model=s.SafetyOut)
async def get_incident(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("safety.view")),
):
    incident = await db.get(m.SafetyIncident, id)
    if not incident:
        raise HTTPException(status_code=404, detail="Safety incident not found")
    try:
        await assert_project_access(db, project_id=incident.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Safety incident not found")
    return incident


@safety_router.get("", response_model=PaginatedResponse[s.SafetyOut])
async def list_incidents(
    project_id: Optional[int] = None,
    violation_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(require_permission("safety.view")),
    db: AsyncSession = Depends(get_db_session),
):
    if project_id is not None:
        try:
            await assert_project_access(db, project_id=project_id, current_user=current_user)
        except Exception:
            raise HTTPException(status_code=404, detail="Project not found")
        query = select(m.SafetyIncident).where(m.SafetyIncident.project_id == project_id)
    else:
        if current_user.company_id is not None:
            query = (
                select(m.SafetyIncident)
                .join(m.Project, m.SafetyIncident.project_id == m.Project.id)
                .where(m.Project.company_id == current_user.company_id)
            )
        elif current_user.is_super_admin:
            return PaginatedResponse(items=[], meta=PaginationMeta(total=0, limit=limit, offset=offset))
        else:
            return PaginatedResponse(items=[], meta=PaginationMeta(total=0, limit=limit, offset=offset))

    if violation_type:
        query = query.where(m.SafetyIncident.violation_type == violation_type)

    count = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.execute(query.limit(limit).offset(offset))).scalars().all()

    return PaginatedResponse(
        items=rows, meta=PaginationMeta(total=count, limit=limit, offset=offset)
    )


@safety_router.put("/{id}", response_model=s.SafetyOut)
async def update_incident(
    id: int,
    data: s.SafetyCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("safety.edit")),
):
    obj = await db.get(m.SafetyIncident, id)
    if not obj:
        raise HTTPException(status_code=404, detail="Safety incident not found")
    try:
        await assert_project_access(db, project_id=obj.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Safety incident not found")

    if data.project_id != obj.project_id:
        try:
            await assert_project_access(db, project_id=data.project_id, current_user=current_user)
        except Exception:
            raise HTTPException(status_code=404, detail="Target project not found")

    if data.task_id:
        await assert_task_project(db, data.task_id, data.project_id)

    for k, v in data.dict().items():
        setattr(obj, k, v)

    await db.commit()
    await db.refresh(obj)
    return obj


@safety_router.delete("/{id}")
async def delete_incident(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("safety.delete")),
):
    obj = await db.get(m.SafetyIncident, id)
    if not obj:
        raise HTTPException(status_code=404, detail="Safety incident not found")
    try:
        await assert_project_access(db, project_id=obj.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Safety incident not found")

    await db.delete(obj)
    await db.commit()
    return {"message": "Incident deleted"}


# ===================== CHECKLIST =====================

checklist_router = APIRouter(prefix="/checklists", tags=["Checklist"])


@checklist_router.get("/logs", response_model=PaginatedResponse[s.ChecklistLogOut])
async def list_logs(
    project_id: Optional[int] = None,
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(require_permission("checklists.view")),
    db: AsyncSession = Depends(get_db_session),
):
    if project_id is not None:
        try:
            await assert_project_access(db, project_id=project_id, current_user=current_user)
        except Exception:
            raise HTTPException(status_code=404, detail="Project not found")
        query = select(m.ChecklistLog).where(m.ChecklistLog.project_id == project_id)
    else:
        if current_user.company_id is not None:
            query = (
                select(m.ChecklistLog)
                .join(m.Project, m.ChecklistLog.project_id == m.Project.id)
                .where(m.Project.company_id == current_user.company_id)
            )
        elif current_user.is_super_admin:
            return PaginatedResponse(items=[], meta=PaginationMeta(total=0, limit=limit, offset=offset))
        else:
            return PaginatedResponse(items=[], meta=PaginationMeta(total=0, limit=limit, offset=offset))

    count = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    items = [s.ChecklistLogOut.model_validate(x) for x in rows]

    return PaginatedResponse(
        items=items, meta=PaginationMeta(total=count, limit=limit, offset=offset)
    )


@checklist_router.post("")
async def create_checklist(
    data: s.ChecklistCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.create")),
):
    if current_user.company_id is None and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="User without company cannot create checklists")

    try:
        await assert_project_access(db, project_id=data.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Project not found")

    obj = m.Checklist(**data.dict())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@checklist_router.get("/{id}")
async def get_checklist(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.view")),
):
    checklist = await db.get(m.Checklist, id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    try:
        await assert_project_access(db, project_id=checklist.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Checklist not found")
    return checklist


@checklist_router.put("/{id}")
async def update_checklist(
    id: int,
    data: s.ChecklistUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.edit")),
):
    checklist = await db.get(m.Checklist, id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    try:
        await assert_project_access(db, project_id=checklist.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Checklist not found")

    for key, value in data.dict(exclude_unset=True).items():
        setattr(checklist, key, value)

    await db.commit()
    await db.refresh(checklist)
    return checklist


@checklist_router.delete("/{id}")
async def delete_checklist(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.delete")),
):
    checklist = await db.get(m.Checklist, id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    try:
        await assert_project_access(db, project_id=checklist.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Checklist not found")

    await db.delete(checklist)
    await db.commit()
    return {"message": "Checklist deleted successfully"}


@checklist_router.post("/items")
async def add_item(
    data: s.ChecklistItemCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.edit")),
):
    checklist = await db.get(m.Checklist, data.checklist_id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    try:
        await assert_project_access(db, project_id=checklist.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Checklist not found")

    existing = await db.scalar(
        select(m.ChecklistItem).where(
            m.ChecklistItem.checklist_id == data.checklist_id,
            m.ChecklistItem.item == data.item,
        )
    )
    if existing:
        raise HTTPException(status_code=400, detail="Checklist item already exists")

    obj = m.ChecklistItem(checklist_id=data.checklist_id, item=data.item)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@checklist_router.get("/{id}/items")
async def get_items(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.view")),
):
    checklist = await db.get(m.Checklist, id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    try:
        await assert_project_access(db, project_id=checklist.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Checklist not found")

    result = await db.execute(
        select(m.ChecklistItem)
        .where(m.ChecklistItem.checklist_id == id)
        .order_by(m.ChecklistItem.id)
    )
    return result.scalars().all()


@checklist_router.put("/items/{item_id}")
async def update_item(
    item_id: int,
    data: s.ChecklistItemUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.edit")),
):
    item = await db.get(m.ChecklistItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    checklist = await db.get(m.Checklist, item.checklist_id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    try:
        await assert_project_access(db, project_id=checklist.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Checklist item not found")

    for key, value in data.dict(exclude_unset=True).items():
        setattr(item, key, value)

    await db.commit()
    await db.refresh(item)
    return item


@checklist_router.get("/items/{checklist_id}")
async def list_items(
    checklist_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.view")),
):
    checklist = await db.get(m.Checklist, checklist_id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    try:
        await assert_project_access(db, project_id=checklist.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Checklist not found")

    result = await db.execute(
        select(m.ChecklistItem)
        .where(m.ChecklistItem.checklist_id == checklist_id)
        .order_by(m.ChecklistItem.id)
    )
    return result.scalars().all()


@checklist_router.delete("/items/{item_id}")
async def delete_item(
    item_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.delete")),
):
    item = await db.get(m.ChecklistItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    checklist = await db.get(m.Checklist, item.checklist_id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    try:
        await assert_project_access(db, project_id=checklist.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Checklist item not found")

    await db.delete(item)
    await db.commit()
    return {"message": "Checklist item deleted"}


@checklist_router.get("")
async def list_checklists(
    project_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.view")),
):
    if project_id is not None:
        try:
            await assert_project_access(db, project_id=project_id, current_user=current_user)
        except Exception:
            raise HTTPException(status_code=404, detail="Project not found")
        query = select(m.Checklist).where(m.Checklist.project_id == project_id)
    else:
        if current_user.company_id is not None:
            query = (
                select(m.Checklist)
                .join(m.Project, m.Checklist.project_id == m.Project.id)
                .where(m.Project.company_id == current_user.company_id)
            )
        elif current_user.is_super_admin:
            return []
        else:
            return []

    return (await db.execute(query)).scalars().all()


@checklist_router.post("/{id}/execute")
async def execute_checklist(
    id: int,
    data: s.ChecklistLogCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("checklists.create")),
):
    checklist = await db.scalar(
        select(m.Checklist)
        .options(selectinload(m.Checklist.items))
        .where(m.Checklist.id == id)
    )
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")

    # P1-3: Verify execution project matches checklist project
    if data.project_id != checklist.project_id:
        raise HTTPException(status_code=400, detail="Checklist does not belong to specified project")

    try:
        await assert_project_access(db, project_id=checklist.project_id, current_user=current_user)
    except Exception:
        raise HTTPException(status_code=404, detail="Checklist not found")

    if not checklist.items:
        raise HTTPException(status_code=400, detail="Cannot execute empty checklist")

    log = m.ChecklistLog(
        checklist_id=id,
        project_id=data.project_id,
        remarks=data.remarks,
        status=data.status,
        executed_by=current_user.id,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


# ===================== Site Photos =====================
UPLOAD_DIR = "uploads/site_photos"
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


site_photo_router = APIRouter(prefix="/site-photos", tags=["Site Photos"])


#  Upload Photo
@site_photo_router.post("/upload", response_model=s.SitePhotoOut)
async def upload_photo(
    project_id: int = Form(...),
    task_id: Optional[int] = Form(None),
    dsr_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    date: Optional[date] = Form(None),
    activity_tag: Optional[str] = Form(None),
    location_tag: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    current_user: User = Depends(require_permission("site_photos.create")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_project(db, project_id, current_user, load_relations=False)
    if task_id:
        await _get_scoped_task(db, project_id, task_id, current_user)
    if dsr_id:
        await _get_scoped_dsr(db, dsr_id, current_user)

    ext = file.filename.split(".")[-1].lower() if file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Invalid file type")

    #  Validate size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "File too large")

    file.file.seek(0)

    #  Unique filename
    filename = f"{uuid4()}.{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    #  Save file
    def _save_site_photo():
        with open(file_path, "wb") as f:
            f.write(content)

    await run_in_threadpool(_save_site_photo)

    #  Store URL (NOT raw path)
    file_url = f"/uploads/site_photos/{filename}"

    obj = m.SitePhoto(
        project_id=project_id,
        task_id=task_id,
        dsr_id=dsr_id,
        photo_url=file_url,
        date=date,
        activity_tag=activity_tag,
        location_tag=location_tag,
        description=description,
    )

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    return obj


#  Filter Photos (IMPORTANT FEATURE)
@site_photo_router.get("", response_model=list[s.SitePhotoOut])
async def list_photos(
    project_id: int,
    activity_tag: Optional[str] = None,
    location_tag: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    current_user: User = Depends(require_permission("site_photos.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_project(db, project_id, current_user, load_relations=False)
    query = select(m.SitePhoto).where(m.SitePhoto.project_id == project_id)

    if activity_tag:
        query = query.where(m.SitePhoto.activity_tag == activity_tag)

    if location_tag:
        query = query.where(m.SitePhoto.location_tag == location_tag)

    #  Date range filter
    if start_date:
        query = query.where(m.SitePhoto.date >= start_date)

    if end_date:
        query = query.where(m.SitePhoto.date <= end_date)

    query = query.order_by(m.SitePhoto.created_at.desc())
    result = (await db.execute(query)).scalars().all()
    return result


#  Delete
@site_photo_router.delete("/{photo_id}")
async def delete_photo(
    photo_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("site_photos.delete")),
):
    obj = await _get_scoped_site_photo(db, photo_id, current_user, for_update=True)

    #  Delete file from disk
    if obj.photo_url:
        file_path = obj.photo_url.replace("/uploads/", "uploads/").lstrip("/")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

    await db.delete(obj)
    await db.commit()

    return {"message": "Photo deleted"}


# ===================== Drawings & Documents =====================

drawing_router = APIRouter(prefix="/drawings", tags=["Drawings"])


# ===================== Upload =====================


# ===================== DRAWING FOLDERS =====================
@drawing_router.post("/folders", response_model=s.DrawingFolderOut)
async def create_folder(
    data: s.DrawingFolderCreate,
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("drawings.create")),
):
    obj = m.DrawingDocument(
        project_id=project_id,
        drawing_name=data.folder_name,
        is_folder=True,
        parent_id=data.parent_id,
        approval_status=DocumentStatus.APPROVED,
        is_latest_version=True,
    )

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    return s.DrawingFolderOut(
        id=obj.id,
        project_id=obj.project_id,
        folder_name=obj.drawing_name,
        parent_id=obj.parent_id,
        is_folder=obj.is_folder,
    )


@drawing_router.post("/upload", response_model=s.DrawingOut)
async def upload_drawing(
    project_id: int = Form(...),
    drawing_name: str = Form(...),
    version: str = Form(...),
    date: Optional[date] = Form(None),
    remarks: Optional[str] = Form(None),
    parent_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("drawings.upload")),
    db: AsyncSession = Depends(get_db_session),
):
    os.makedirs("uploads/drawings", exist_ok=True)

    await validate_drawing_file(file)

    MAX_DRAWING_SIZE = 20 * 1024 * 1024

    content = await file.read()

    if len(content) > MAX_DRAWING_SIZE:
        raise HTTPException(status_code=400, detail="Drawing size cannot exceed 20 MB")

    await file.seek(0)

    ext = os.path.splitext(file.filename)[1].lower()

    unique_name = f"{uuid.uuid4().hex}{ext}"

    file_path = f"uploads/drawings/{unique_name}"

    try:

        # ================= OLD LATEST VERSION FALSE =================

        await db.execute(
            update(m.DrawingDocument)
            .where(
                m.DrawingDocument.project_id == project_id,
                m.DrawingDocument.drawing_name == drawing_name,
                m.DrawingDocument.is_latest_version == True,
            )
            .values(is_latest_version=False)
        )

        # ================= SAVE FILE =================

        def _save_drawing():
            with open(file_path, "wb") as f:
                f.write(file.file.read())

        await run_in_threadpool(_save_drawing)

        # ================= GET NEXT REVISION =================

        latest_revision = await db.scalar(
            select(func.max(m.DrawingDocument.revision_no)).where(
                m.DrawingDocument.project_id == project_id,
                m.DrawingDocument.drawing_name == drawing_name,
            )
        )

        next_revision = (latest_revision or 0) + 1

        # ================= CREATE DRAWING =================

        obj = m.DrawingDocument(
            project_id=project_id,
            drawing_name=drawing_name,
            version=version,
            file_url=file_path,
            date=date,
            remarks=remarks,
            approval_status=DocumentStatus.UNDER_REVIEW,
            revision_no=next_revision,
            is_latest_version=True,
            parent_id=parent_id,
            is_folder=False,
        )

        db.add(obj)

        await db.flush()

        # ================= CREATE APPROVAL =================

        approval = Approval(
            entity_type="drawing",
            entity_id=obj.id,
            requested_by=current_user.id,
            remarks=f"Approval requested for drawing: {drawing_name}",
            status="Pending",
        )

        db.add(approval)

        await db.flush()

        # ================= UPDATE DRAWING APPROVAL REF =================

        obj.approval_id = approval.id

        await db.commit()

        await db.refresh(obj)

        return obj

    except Exception:

        await db.rollback()

        if os.path.exists(file_path):
            os.remove(file_path)

        raise


# ===================== Update =====================


@drawing_router.put("/{id}", response_model=s.DrawingOut)
async def update_drawing(
    id: int,
    payload: s.DrawingUpdate,
    current_user: User = Depends(require_permission("drawings.edit")),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(m.DrawingDocument, id)

    if not obj:
        raise NotFoundError("Drawing not found")

    # ================= LOCK APPROVED DRAWINGS =================

    if obj.approval_status == DocumentStatus.APPROVED:
        raise ValidationError("Approved drawing cannot be edited. Create new revision.")

    update_data = payload.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(obj, field, value)

    await db.commit()

    await db.refresh(obj)

    return obj


# ===================== Approval History =====================


@drawing_router.get("/{id}/approval-history")
async def get_drawing_approval_history(
    id: int,
    current_user: User = Depends(require_permission("drawings.view")),
    db: AsyncSession = Depends(get_db_session),
):
    drawing = await db.get(m.DrawingDocument, id)

    if not drawing:
        raise NotFoundError("Drawing not found")

    result = await db.execute(
        select(Approval)
        .where(
            Approval.entity_type == "drawing",
            Approval.entity_id == id,
        )
        .order_by(Approval.id.desc())
    )

    approvals = result.scalars().all()

    return [
        {
            "id": approval.id,
            "entity_type": approval.entity_type,
            "entity_id": approval.entity_id,
            "requested_by": approval.requested_by,
            "approved_by": approval.approved_by,
            "status": approval.status,
            "remarks": approval.remarks,
            "created_at": approval.created_at,
            "updated_at": approval.updated_at,
        }
        for approval in approvals
    ]


# ===================== List Drawings =====================
@drawing_router.get("", response_model=PaginatedResponse[s.DrawingOut])
async def list_drawings(
    project_id: int = Query(..., gt=0),
    parent_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    approval_status: Optional[DocumentStatus] = Query(None),
    latest_only: bool = Query(True),
    is_folder: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_permission("drawings.view")),
    db: AsyncSession = Depends(get_db_session),
):
    stmt = select(m.DrawingDocument).where(m.DrawingDocument.project_id == project_id)
    count_stmt = (
        select(func.count())
        .select_from(m.DrawingDocument)
        .where(m.DrawingDocument.project_id == project_id)
    )
    if parent_id is not None:
        stmt = stmt.where(m.DrawingDocument.parent_id == parent_id)
        count_stmt = count_stmt.where(m.DrawingDocument.parent_id == parent_id)
    if search:
        stmt = stmt.where(m.DrawingDocument.drawing_name.ilike(f"%{search}%"))
        count_stmt = count_stmt.where(
            m.DrawingDocument.drawing_name.ilike(f"%{search}%")
        )
    if approval_status:
        stmt = stmt.where(m.DrawingDocument.approval_status == approval_status)
        count_stmt = count_stmt.where(
            m.DrawingDocument.approval_status == approval_status
        )
    if latest_only:
        stmt = stmt.where(m.DrawingDocument.is_latest_version.is_(True))
        count_stmt = count_stmt.where(m.DrawingDocument.is_latest_version.is_(True))
    if is_folder is not None:
        stmt = stmt.where(m.DrawingDocument.is_folder == is_folder)
        count_stmt = count_stmt.where(m.DrawingDocument.is_folder == is_folder)
    stmt = (
        stmt.order_by(
            m.DrawingDocument.is_folder.desc(),
            m.DrawingDocument.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    items = result.scalars().all()
    total = await db.scalar(count_stmt)
    return PaginatedResponse[s.DrawingOut](
        items=items,
        meta=PaginationMeta(
            total=total or 0,
            limit=limit,
            offset=offset,
        ),
    )


# ===================== Version History =====================


# @drawing_router.get("/versions", response_model=list[s.DrawingOut])
@drawing_router.get("/{project_id}/versions", response_model=list[s.DrawingOut])
async def get_versions(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("drawings.view")),
    project_id: Optional[int] = None,
    parent_id: Optional[int] = Query(None),
    skip: int = 0,
    limit: int = 50,
):
    query = select(m.DrawingDocument)
    if project_id is not None:
        query = query.where(m.DrawingDocument.project_id == project_id)

    if parent_id is not None:
        query = query.where(m.DrawingDocument.parent_id == parent_id)
    else:
        query = query.where(m.DrawingDocument.parent_id == None)

    result = await db.execute(
        query.order_by(
            m.DrawingDocument.drawing_name.asc(),
            m.DrawingDocument.revision_no.desc(),
            m.DrawingDocument.id.desc(),
        )
        .offset(skip)
        .limit(limit)
    )

    drawings = result.scalars().all()

    return drawings


# ===================== Latest =====================


# @drawing_router.get("/latest", response_model=list[s.DrawingOut])
@drawing_router.get("/{project_id}/latest", response_model=list[s.DrawingOut])
async def get_latest(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("drawings.view")),
    project_id: Optional[int] = None,
    parent_id: Optional[int] = Query(None),
):
    query = select(m.DrawingDocument).where(
        m.DrawingDocument.is_latest_version == True,
    )
    if project_id is not None:
        query = query.where(m.DrawingDocument.project_id == project_id)

    if parent_id is not None:
        query = query.where(m.DrawingDocument.parent_id == parent_id)
    else:
        query = query.where(m.DrawingDocument.parent_id == None)

    result = await db.execute(
        query.order_by(
            m.DrawingDocument.drawing_name.asc(),
            m.DrawingDocument.revision_no.desc(),
        )
    )

    drawings = result.scalars().all()

    if not drawings:
        raise HTTPException(status_code=404, detail="No drawings found")

    return drawings


# ===================== Delete =====================


@drawing_router.delete("/{id}")
async def delete_drawing(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("drawings.delete")),
):
    obj = await db.get(m.DrawingDocument, id)

    if not obj:
        raise NotFoundError("Drawing not found")

    if obj.file_url and os.path.exists(obj.file_url):
        os.remove(obj.file_url)

    await db.execute(
        delete(Approval).where(
            Approval.entity_type == "drawing",
            Approval.entity_id == id,
        )
    )

    await db.delete(obj)

    await db.commit()

    return {"message": "Deleted"}


# ===================== Download =====================


@drawing_router.get("/documents/download/{id}")
async def download_document(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("drawings.download")),
):
    doc = await db.get(m.DrawingDocument, id)

    if not doc:
        raise NotFoundError("Document not found")

    if not os.path.exists(doc.file_url):
        raise NotFoundError("File not found on server")

    return FileResponse(
        path=doc.file_url,
        filename=os.path.basename(doc.file_url),
        media_type="application/octet-stream",
    )


# ===================== View =====================


@drawing_router.get("/documents/view/{id}")
async def view_document(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("drawings.view")),
):
    doc = await db.get(m.DrawingDocument, id)

    if not doc:
        raise NotFoundError("Document not found")

    if not os.path.exists(doc.file_url):
        raise NotFoundError("File not found on server")

    media_type, _ = mimetypes.guess_type(doc.file_url)

    return FileResponse(
        path=doc.file_url,
        filename=os.path.basename(doc.file_url),
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": "inline"},
    )


# ===================== Site Requests =====================


site_request_router = APIRouter(prefix="/site-requests", tags=["Site Requests"])


@site_request_router.post("", response_model=s.SiteRequestOut)
async def create_request(
    payload: s.SiteRequestCreate,
    current_user: User = Depends(require_permission("site_requests.create")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_project(db, payload.project_id, current_user, load_relations=False)
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    obj = m.SiteRequest(
        **data,
        requested_by=current_user.id,
        status="Pending",
    )

    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@site_request_router.get("", response_model=list[s.SiteRequestOut])
async def list_requests(
    project_id: int,
    current_user: User = Depends(require_permission("site_requests.view")),
    db: AsyncSession = Depends(get_db_session),
):
    await _get_scoped_project(db, project_id, current_user, load_relations=False)
    result = await db.execute(
        select(m.SiteRequest)
        .where(m.SiteRequest.project_id == project_id)
        .order_by(m.SiteRequest.created_at.desc())
    )
    return result.scalars().all()


@site_request_router.put("/{id}/approve")
async def approve_request(
    id: int,
    current_user: User = Depends(require_permission("site_requests.approve")),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await _get_scoped_site_request(db, id, current_user, for_update=True)
    obj.status = "Approved"
    obj.approved_by = current_user.id

    await db.commit()
    return {"message": "Approved"}


@site_request_router.put("/{id}/reject")
async def reject_request(
    id: int,
    current_user: User = Depends(require_permission("site_requests.approve")),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await _get_scoped_site_request(db, id, current_user, for_update=True)
    obj.status = "Rejected"
    obj.approved_by = current_user.id

    await db.commit()
    return {"message": "Rejected"}


router.include_router(milestones_router)
router.include_router(tasks_router)


# Moved dynamic routes to bottom
@router.get("/{project_id}/qr", response_class=StreamingResponse)
async def generate_project_qr(
    project_id: int,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
    service: ProjectsService = Depends(get_projects_service),
):
    out = await service.get_project(
        db,
        project_id=project_id,
        current_user=current_user,
    )

    qr_buf = generate_qr(entity_type="PRJ", entity_id=out.id)

    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'inline; filename="project_{out.id}.png"',
    }

    return StreamingResponse(qr_buf, media_type="image/png", headers=headers)


@router.get("/{project_id}", response_model=s.ProjectOut)
async def get_project(
    project_id: int,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: ProjectsService = Depends(get_projects_service),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:projects:get:{version}:{current_user.id}:{current_user.role}:{project_id}"
    cached_json = await cache_get_json(redis, cache_key)
    if (
        cached_json is not None
        and isinstance(cached_json, dict)
        and "completion_percentage" in cached_json
    ):
        return s.ProjectOut.model_validate(cached_json)

    out = await service.get_project(
        db,
        project_id=project_id,
        current_user=current_user,
    )
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


@router.put("/{project_id}", response_model=s.ProjectOut)
async def update_project(
    project_id: int,
    payload: s.ProjectUpdate,
    current_user: User = Depends(require_permission("projects.edit")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: ProjectsService = Depends(get_projects_service),
):
    logger.info(f"Updating project id={project_id}")

    try:
        out = await service.update_project(
            db, current_user, project_id=project_id, payload=payload
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(f"Project update failed id={project_id}")
        raise

    logger.info(f"Project updated id={project_id}")

    return out


@router.delete("/{project_id}", status_code=200)
async def delete_project(
    project_id: int,
    current_user: User = Depends(require_permission("projects.delete")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: ProjectsService = Depends(get_projects_service),
):
    logger.info(f"Deleting project id={project_id}")

    try:
        await service.delete_project(db, current_user, project_id=project_id)
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(f"Project delete failed id={project_id}")
        raise

    logger.info(f"Project deleted id={project_id}")

    return {"success": True, "message": f"Project_id {project_id} deleted successfully"}


from app.schemas import gantt as s_gantt


@router.get("/{project_id}/gantt", response_model=s_gantt.GanttResponseSchema)
async def get_project_gantt(
    project_id: int,
    current_user: User = Depends(require_permission("projects.view")),
    db: AsyncSession = Depends(get_db_session),
    service: ProjectsService = Depends(get_projects_service),
):
    obj = await _get_scoped_project(db, project_id, current_user, load_relations=True)

    gantt_items = []
    for mstone in obj.milestones:
        children = []
        for t in mstone.tasks:
            children.append(
                s_gantt.GanttTaskSchema(
                    id=f"t_{t.id}",
                    name=t.title,
                    start_date=t.start_date,
                    end_date=t.end_date,
                    progress=t.completion_percentage or 0.0,
                    status=(
                        t.status.value if hasattr(t.status, "value") else str(t.status)
                    ),
                )
            )

        gantt_items.append(
            s_gantt.GanttMilestoneSchema(
                id=f"m_{mstone.id}",
                name=mstone.title,
                start_date=mstone.start_date,
                end_date=mstone.end_date,
                progress=mstone.completion_percentage or 0.0,
                status=(
                    mstone.status.value
                    if hasattr(mstone.status, "value")
                    else str(mstone.status)
                ),
                children=children,
            )
        )

    unassigned_tasks = []
    for t in obj.tasks:
        if not t.milestone_id:
            unassigned_tasks.append(
                s_gantt.GanttTaskSchema(
                    id=f"t_{t.id}",
                    name=t.title,
                    start_date=t.start_date,
                    end_date=t.end_date,
                    progress=t.completion_percentage or 0.0,
                    status=(
                        t.status.value if hasattr(t.status, "value") else str(t.status)
                    ),
                )
            )

    return s_gantt.GanttResponseSchema(
        project_id=obj.id,
        project_name=obj.project_name,
        gantt_items=gantt_items,
        unassigned_tasks=unassigned_tasks,
    )
