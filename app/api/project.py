from __future__ import annotations
from datetime import date
import pathlib, re, io, os, uuid
from openpyxl import Workbook
from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.enums import LabourStatus, SkillType
from app.db.session import get_db_session
from sqlalchemy.orm import selectinload
import traceback
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.cache.redis import (
    bump_cache_version,
    cache_get_json,
    cache_set_json,
    get_cache_version,
)
from fastapi import Request
from PIL import Image
from app.core.dependencies import (
    get_current_active_user,
    get_request_redis,
    require_roles,
)
from app.models.contractor import Contractor
from sqlalchemy import select, func, or_
from app.models import project as m
from app.models.contractor import Contractor
from app.models.user import User, UserRole
from app.models.owner import Owner
from app.models.expense import Expense
from app.models.invoice import Invoice
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas import project as s
from app.core.logger import logger
from fastapi.responses import StreamingResponse
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from sqlalchemy.exc import IntegrityError
from fastapi import UploadFile, File
from app.utils.helpers import (
    BadRequestError,
    DataIntegrityError,
    NotFoundError,
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from app.utils.pagination import PaginationParams
from app.utils.common import assert_project_access, generate_business_id


def compute_project_status(project):
    today = date.today()

    if project.status == s.ProjectStatus.COMPLETED:
        return "Completed"

    if project.start_date and today < project.start_date:
        return "Planned"

    if project.end_date and today > project.end_date:
        return "Delayed"

    return "Active"


def get_pagination(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset, search=search).normalized()


router = APIRouter(
    prefix="/projects",
    tags=["project_management"],
    dependencies=[default_rate_limiter_dependency()],
)


VERSION_KEY = "cache_version:projects"

PROJECT_WRITE_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER]
PROJECT_DELETE_ROLES = [UserRole.ADMIN]

TASK_WRITE_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER]
TASK_DELETE_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER]
DSR_WRITE_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER]
DSR_READ_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER]
DSR_DELETE_ROLES = [UserRole.ADMIN]
DSR_APPROVE_ROLES = [UserRole.ADMIN,UserRole.PROJECT_MANAGER,]
ISSUE_CREATE_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER]
ISSUE_UPDATE_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER]
ISSUE_DELETE_ROLES = [UserRole.ADMIN]

FINANCIAL_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT]

READ_ROLES = [
    UserRole.ADMIN,
    UserRole.PROJECT_MANAGER,
    UserRole.SITE_ENGINEER,
    UserRole.CONTRACTOR,
    UserRole.ACCOUNTANT,
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
        return await db.scalar(select(m.Project).where(m.Project.id == project_id))

    async def list_projects(
        self,
        db: AsyncSession,
        *,
        limit: int,
        offset: int,
        search: Optional[str] = None,
        status: Optional[s.ProjectStatus] = None,
    ) -> tuple[list[m.Project], int]:
        query = select(m.Project)
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
        return await db.scalar(
            select(m.Milestone).where(
                m.Milestone.project_id == project_id, m.Milestone.id == milestone_id
            )
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

        query = (
            select(m.Milestone)
            .where(m.Milestone.project_id == project_id)
            .order_by(m.Milestone.id.desc())
            .limit(limit)
            .offset(offset)
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
        return await db.scalar(
            select(m.Task).where(m.Task.project_id == project_id, m.Task.id == task_id)
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
        query = select(m.Task).where(m.Task.project_id == project_id)
        count_query = (
            select(func.count())
            .select_from(m.Task)
            .where(m.Task.project_id == project_id)
        )

        if status is not None:
            query = query.where(m.Task.status == status)
            count_query = count_query.where(m.Task.status == status)

        if assigned_user_id is not None:
            query = query.where(m.Task.assigned_user_id == assigned_user_id)
            count_query = count_query.where(m.Task.assigned_user_id == assigned_user_id)

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

    def _assert_project_mutation_role(self, current_user: User) -> None:
        if current_user.role not in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
            raise PermissionDeniedError("Insufficient permissions")

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
        self._assert_project_mutation_role(current_user)
        data = payload.model_dump(exclude_unset=True)
        data["status"] = s.ProjectStatus.PLANNED
        owner = await db.scalar(select(Owner).where(Owner.id == payload.owner_id))
        if not owner:
            raise NotFoundError("Owner not found")

        if payload.start_date and payload.end_date:
            if payload.end_date < payload.start_date:
                raise ValidationError("end_date cannot be before start_date")

        for _ in range(3):
            try:
                data["business_id"] = await generate_business_id(
                    db, m.Project, "business_id", "PRJ"
                )

                obj = await self.projects_repo.create_project(db, data)
                break

            except IntegrityError as e:
                await db.rollback()

                # Optional: if name conflict (keep your logic)
                if "project_name" in str(e.orig):
                    raise ConflictError("Project with this name already exists")

                continue
        else:
            raise Exception("Failed to generate unique project ID")

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

        if current_user.role in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
            base_query = select(m.Project)
        else:
            base_query = (
                select(m.Project)
                .join(m.ProjectMember, m.ProjectMember.project_id == m.Project.id)
                .where(m.ProjectMember.user_id == current_user.id)
            )

        if search:
            base_query = base_query.where(
                m.Project.project_name.ilike(f"%{search.strip()}%")
            )

        if status:
            base_query = base_query.where(m.Project.status == status)

        count_query = select(func.count()).select_from(
            base_query.order_by(None).subquery()
        )
        total = await db.scalar(count_query)

        query = base_query.order_by(m.Project.id.desc()).limit(limit).offset(offset)

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
        obj = await self.projects_repo.get_project(db, project_id=project_id)
        if obj is None:
            raise NotFoundError("Project not found")

        await assert_project_access(
            db,
            project_id=obj.id,
            current_user=current_user,
        )

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
        )

    async def update_project(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        payload: s.ProjectUpdate,
    ) -> s.ProjectOut:
        self._assert_project_mutation_role(current_user)
        obj = await self.projects_repo.get_project(db, project_id=project_id)
        if obj is None:
            raise NotFoundError("Project not found")
        data = payload.model_dump(exclude_unset=True)
        if "project_name" in data and data["project_name"] is None:
            raise ValidationError("project_name cannot be null")
        if "status" in data:
            if data["status"] != s.ProjectStatus.COMPLETED:
                data.pop("status")
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
        )

    async def delete_project(
        self, db: AsyncSession, current_user: User, *, project_id: int
    ) -> None:
        self._assert_project_mutation_role(current_user)
        obj = await self.projects_repo.get_project(db, project_id=project_id)
        if obj is None:
            raise NotFoundError("Project not found")
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

    def _assert_member_mutation_role(self, current_user: User) -> None:
        if current_user.role not in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
            raise PermissionDeniedError("Insufficient permissions")

    async def assign_member(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        user_id: int,
    ) -> s.ProjectMemberOut:

        self._assert_member_mutation_role(current_user)

        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        user = await db.scalar(select(User).where(User.id == user_id))
        if user is None:
            raise NotFoundError("User not found")

        existing = await self.members_repo.get_member(
            db, project_id=project_id, user_id=user_id
        )
        if existing is not None:
            raise ConflictError("User is already assigned to this project")

        try:
            await self.members_repo.assign_member(
                db, project_id=project_id, user_id=user_id
            )
        except IntegrityError:
            await db.rollback()
            raise ConflictError("User is already assigned to this project")

        role = user.role.value if hasattr(user.role, "value") else str(user.role)

        return s.ProjectMemberOut(
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            role=role,
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
        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        users, total = await self.members_repo.list_members(
            db, project_id=project_id, limit=limit, offset=offset
        )
        items: list[s.ProjectMemberOut] = []
        for user in users:
            role = user.role.value if hasattr(user.role, "value") else str(user.role)
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
        self._assert_member_mutation_role(current_user)

        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

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

    def _assert_milestone_mutation_role(self, current_user: User) -> None:
        if current_user.role not in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
            raise PermissionDeniedError("Insufficient permissions")

    async def create_milestone(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        payload: s.MilestoneCreate,
    ) -> s.MilestoneOut:
        self._assert_milestone_mutation_role(current_user)
        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        data = payload.model_dump(exclude_unset=True)

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

        return s.MilestoneOut(
            id=obj.id,
            project_id=obj.project_id,
            title=obj.title,
            description=obj.description,
            start_date=obj.start_date,
            end_date=obj.end_date,
        )

    async def list_milestones(
        self,
        db: AsyncSession,
        *,
        project_id: int,
        pagination: PaginationParams,
    ) -> PaginatedResponse[s.MilestoneOut]:

        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        rows, total = await self.milestones_repo.list_milestones(
            db,
            project_id=project_id,
            limit=pagination.limit,
            offset=pagination.offset,
        )

        items = [
            s.MilestoneOut(
                id=m.id,
                project_id=m.project_id,
                title=m.title,
                description=m.description,
                start_date=m.start_date,
                end_date=m.end_date,
            )
            for m in rows
        ]

        return PaginatedResponse(
            items=items,
            meta=PaginationMeta(
                total=total,
                limit=pagination.limit,
                offset=pagination.offset,
            ),
        )

    async def get_milestone(
        self, db: AsyncSession, *, project_id: int, milestone_id: int
    ) -> s.MilestoneOut:
        obj = await self.milestones_repo.get_milestone(
            db, project_id=project_id, milestone_id=milestone_id
        )
        if obj is None:
            raise NotFoundError("Milestone not found")
        return s.MilestoneOut(
            id=obj.id,
            project_id=obj.project_id,
            title=obj.title,
            description=obj.description,
            start_date=obj.start_date,
            end_date=obj.end_date,
        )

    async def update_milestone(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        milestone_id: int,
        payload: s.MilestoneUpdate,
    ) -> s.MilestoneOut:
        self._assert_milestone_mutation_role(current_user)
        obj = await self.milestones_repo.get_milestone(
            db, project_id=project_id, milestone_id=milestone_id
        )
        if obj is None:
            raise NotFoundError("Milestone not found")

        data = payload.model_dump(exclude_unset=True)
        if "title" in data and data["title"] is None:
            raise ValidationError("title cannot be null")

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

        return s.MilestoneOut(
            id=obj.id,
            project_id=obj.project_id,
            title=obj.title,
            description=obj.description,
            start_date=obj.start_date,
            end_date=obj.end_date,
        )

    async def delete_milestone(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        milestone_id: int,
    ) -> None:
        self._assert_milestone_mutation_role(current_user)
        obj = await self.milestones_repo.get_milestone(
            db, project_id=project_id, milestone_id=milestone_id
        )
        if obj is None:
            raise NotFoundError("Milestone not found")
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

    def _assert_task_mutation_role(self, current_user: User) -> None:
        if current_user.role not in (
            UserRole.ADMIN,
            UserRole.PROJECT_MANAGER,
            UserRole.SITE_ENGINEER,
        ):
            raise PermissionDeniedError("Insufficient permissions")

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
        if current_user.role in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
            return

        if current_user.id == task.assigned_user_id:
            return

        allowed = await self.members_repo.is_member(
            db,
            project_id=project_id,
            user_id=current_user.id,
        )

        if not allowed:
            raise PermissionDeniedError("Insufficient permissions")

    def _task_to_out(self, *, task: m.Task, is_delayed: bool) -> s.TaskOut:
        return s.TaskOut(
            id=task.id,
            project_id=task.project_id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            status=task.status,
            start_date=task.start_date,
            end_date=task.end_date,
            assigned_user_id=task.assigned_user_id,
            completion_percentage=task.completion_percentage,
            is_delayed=is_delayed,
        )

    async def create_task(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        payload: s.TaskCreate,
    ) -> s.TaskOut:
        self._assert_task_mutation_role(current_user)

        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

        if payload.assigned_user_id is not None:
            assigned_user = await db.scalar(
                select(User).where(User.id == payload.assigned_user_id)
            )
            if assigned_user is None:
                raise NotFoundError("User not found")

            is_member = await db.scalar(
                select(m.ProjectMember).where(
                    m.ProjectMember.project_id == project_id,
                    m.ProjectMember.user_id == payload.assigned_user_id,
                )
            )
            if not is_member:
                raise ValidationError("User not part of project")

        data = payload.model_dump(exclude_unset=True)

        try:
            obj = await self.tasks_repo.create_task(
                db,
                project_id=project_id,
                data=data,
            )
        except IntegrityError:
            await db.rollback()
            raise ConflictError("Task with this title already exists in this project")

        is_delayed = self._is_delayed(task=obj, current_date=date.today())
        return self._task_to_out(task=obj, is_delayed=is_delayed)

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
    ) -> PaginatedResponse[s.TaskOut]:
        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

        rows, total = await self.tasks_repo.list_tasks(
            db,
            project_id=project_id,
            status=status,
            assigned_user_id=assigned_user_id,
            limit=limit,
            offset=offset,
        )
        current_date = date.today()
        items = [
            self._task_to_out(
                task=t, is_delayed=self._is_delayed(task=t, current_date=current_date)
            )
            for t in rows
        ]
        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[s.TaskOut](items=items, meta=meta)

    async def get_task(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
    ) -> s.TaskOut:

        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

        obj = await self.tasks_repo.get_task(
            db,
            project_id=project_id,
            task_id=task_id,
        )
        if obj is None:
            raise NotFoundError("Task not found")

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
    ) -> s.TaskOut:
        self._assert_task_mutation_role(current_user)

        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
        if obj is None:
            raise NotFoundError("Task not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

        data = payload.model_dump(exclude_unset=True)

        if "title" in data and data["title"] is None:
            raise ValidationError("title cannot be null")

        if "priority" in data and data["priority"] is None:
            raise ValidationError("priority cannot be null")

        if "status" in data and data["status"] is None:
            raise ValidationError("status cannot be null")

        if "assigned_user_id" in data:
            assigned_user_id = data["assigned_user_id"]

            if assigned_user_id is None:
                raise ValidationError("assigned_user_id cannot be null")

            assigned_user = await db.scalar(
                select(User).where(User.id == assigned_user_id)
            )
            if assigned_user is None:
                raise NotFoundError("User not found")

            is_member = await db.scalar(
                select(m.ProjectMember).where(
                    m.ProjectMember.project_id == project_id,
                    m.ProjectMember.user_id == assigned_user_id,
                )
            )
            if not is_member:
                raise ValidationError("User not part of project")

        try:
            await self.tasks_repo.update_task(db, obj=obj, data=data)
            await db.refresh(obj)
        except IntegrityError:
            await db.rollback()
            raise ConflictError("Task with this title already exists in this project")
        except Exception:
            await db.rollback()
            logger.exception(f"Task update failed id={task_id}")
            raise

        is_delayed = self._is_delayed(task=obj, current_date=date.today())

        return self._task_to_out(task=obj, is_delayed=is_delayed)

    async def delete_task(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
    ) -> None:
        self._assert_task_mutation_role(current_user)
        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
        if obj is None:
            raise NotFoundError("Task not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )
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
        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
        if obj is None:
            raise NotFoundError("Task not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

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

        obj = await self.tasks_repo.get_task(
            db,
            project_id=project_id,
            task_id=task_id,
        )
        if obj is None:
            raise NotFoundError("Task not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

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
        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
        if obj is None:
            raise NotFoundError("Task not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

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

        obj = await self.tasks_repo.get_task(
            db,
            project_id=project_id,
            task_id=task_id,
        )
        if obj is None:
            raise NotFoundError("Task not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

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
    ):
        project = await self.projects_repo.get_project(db, project_id)
        if not project:
            raise NotFoundError("Project not found")

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

    async def get_schedule(self, db: AsyncSession, *, project_id: int):
        project = await self.projects_repo.get_project(db, project_id)
        if not project:
            raise NotFoundError("Project not found")

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
        today = date.today()

        if current_user.role in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
            base_query = select(m.Project).where(
                m.Project.end_date < today,
                m.Project.status != s.ProjectStatus.COMPLETED,
            )
        else:
            base_query = (
                select(m.Project)
                .join(m.ProjectMember, m.ProjectMember.project_id == m.Project.id)
                .where(
                    m.ProjectMember.user_id == current_user.id,
                    m.Project.end_date < today,
                    m.Project.status != s.ProjectStatus.COMPLETED,
                )
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
        today = date.today()

        if current_user.role in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
            base_query = select(m.Task).where(
                m.Task.end_date < today,
                m.Task.status != s.TaskStatus.COMPLETED,
            )
        else:
            base_query = (
                select(m.Task)
                .join(m.ProjectMember, m.ProjectMember.project_id == m.Task.project_id)
                .where(
                    m.ProjectMember.user_id == current_user.id,
                    m.Task.end_date < today,
                    m.Task.status != s.TaskStatus.COMPLETED,
                )
            )

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
        data = await self.get_project_data(db, project_id, current_user)

        wb = Workbook()
        ws = wb.active
        ws.title = "Project Report"

        headers = ["ID", "Name", "Status", "Start Date", "End Date"]
        ws.append(headers)

        ws.append(
            [
                data["id"],
                data["name"],
                str(data["status"]),
                str(data["start_date"]),
                str(data["end_date"]),
            ]
        )

        stream = io.BytesIO()
        wb.save(stream)
        stream.seek(0)

        return StreamingResponse(
            stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=project.xlsx"},
        )

    async def export_pdf(
        self,
        db: AsyncSession,
        project_id: int,
        current_user: User,
    ):
        data = await self.get_project_data(db, project_id, current_user)

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer)
        styles = getSampleStyleSheet()

        content = [
            Paragraph(f"Project Report", styles["Title"]),
            Paragraph(f"ID: {data['id']}", styles["Normal"]),
            Paragraph(f"Name: {data['name']}", styles["Normal"]),
            Paragraph(f"Status: {data['status']}", styles["Normal"]),
        ]

        doc.build(content)
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=project.pdf"},
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


@router.post("", response_model=s.ProjectOut)
async def create_project(
    payload: s.ProjectCreate,
    current_user: User = Depends(require_roles(PROJECT_WRITE_ROLES)),
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


@router.get("", response_model=PaginatedResponse[s.ProjectOut])
async def list_projects(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[s.ProjectStatus] = None,
    current_user: User = Depends(require_roles(READ_ROLES)),
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


@router.get("/{project_id}", response_model=s.ProjectOut)
async def get_project(
    project_id: int,
    current_user: User = Depends(require_roles(READ_ROLES)),
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
    current_user: User = Depends(require_roles(PROJECT_WRITE_ROLES)),
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


@router.post("/{project_id}/schedule")
async def set_project_schedule(
    project_id: int,
    start_date: date,
    end_date: date,
    current_user: User = Depends(require_roles(PROJECT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: SchedulingService = Depends(get_scheduling_service),
):

    result = await service.set_schedule(
        db, project_id=project_id, start_date=start_date, end_date=end_date
    )

    await bump_cache_version(redis, VERSION_KEY)

    return result


@router.get("/{project_id}/schedule")
async def get_project_schedule(
    project_id: int,
    current_user: User = Depends(require_roles(READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    service: SchedulingService = Depends(get_scheduling_service),
):
    return await service.get_schedule(db, project_id=project_id)


@router.get("/{project_id}/progress")
async def get_project_progress(
    project_id: int,
    current_user: User = Depends(require_roles(READ_ROLES)),
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
    current_user: User = Depends(require_roles(READ_ROLES)),
    service: AlertsService = Depends(get_alerts_service),
):
    return await service.get_project_alerts(db, current_user, pagination)


@router.get("/alerts/tasks")
async def get_task_alerts(
    pagination: PaginationParams = Depends(get_pagination),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(READ_ROLES)),
    service: AlertsService = Depends(get_alerts_service),
):
    return await service.get_task_alerts(db, current_user, pagination)


@router.delete("/{project_id}", status_code=200)
async def delete_project(
    project_id: int,
    current_user: User = Depends(require_roles(PROJECT_DELETE_ROLES)),
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


@router.post(
    "/{project_id}/members/{user_id}",
    response_model=s.ProjectMemberOut,
    status_code=201,
)
async def assign_project_member(
    project_id: int,
    user_id: int,
    current_user: User = Depends(require_roles(PROJECT_WRITE_ROLES)),
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
    current_user: User = Depends(require_roles(READ_ROLES)),
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
    current_user: User = Depends(require_roles(PROJECT_WRITE_ROLES)),
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


@router.get("/{project_id}/report/excel")
async def export_project_excel(
    project_id: int,
    current_user: User = Depends(require_roles(READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    service: ReportsService = Depends(get_reports_service),
):
    return await service.export_excel(db, project_id, current_user)


@router.get("/{project_id}/report/pdf")
async def export_project_pdf(
    project_id: int,
    current_user: User = Depends(require_roles(READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    service: ReportsService = Depends(get_reports_service),
):
    return await service.export_pdf(db, project_id, current_user)


@router.get("/{project_id}/logs")
async def get_project_logs(
    project_id: int,
    current_user: User = Depends(require_roles(READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await assert_project_access(
        db,
        project_id=project_id,
        current_user=current_user,
    )

    return {
        "project_id": project_id,
        "message": "Logs available in logging system (file/ELK)",
    }


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
    current_user: User = Depends(require_roles(PROJECT_WRITE_ROLES)),
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
    except Exception:
        logger.exception(f"Milestone creation failed project_id={project_id}")
        raise

    logger.info(f"Milestone created id={out.id}")

    return out


@milestones_router.get(
    "/{project_id}/milestones",
    response_model=PaginatedResponse[s.MilestoneOut],
)
async def list_milestones(
    project_id: int,
    pagination: PaginationParams = Depends(get_pagination),
    current_user: User = Depends(require_roles(READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    service: MilestonesService = Depends(get_milestones_service),
):
    return await service.list_milestones(
        db,
        project_id=project_id,
        pagination=pagination,
    )


@milestones_router.get(
    "/{project_id}/milestones/{milestone_id}", response_model=s.MilestoneOut
)
async def get_milestone(
    project_id: int,
    milestone_id: int,
    current_user: User = Depends(require_roles(READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    service: MilestonesService = Depends(get_milestones_service),
):
    return await service.get_milestone(
        db, project_id=project_id, milestone_id=milestone_id
    )


@milestones_router.put(
    "/{project_id}/milestones/{milestone_id}", response_model=s.MilestoneOut
)
async def update_milestone(
    project_id: int,
    milestone_id: int,
    payload: s.MilestoneUpdate,
    current_user: User = Depends(require_roles(PROJECT_WRITE_ROLES)),
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
    current_user: User = Depends(require_roles(PROJECT_WRITE_ROLES)),
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


@tasks_router.post("/{project_id}/tasks", response_model=s.TaskOut)
async def create_task(
    project_id: int,
    payload: s.TaskCreate,
    current_user: User = Depends(require_roles(TASK_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    logger.info(f"Creating task project_id={project_id}")

    try:
        out = await service.create_task(
            db, current_user, project_id=project_id, payload=payload
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(f"Task creation failed project_id={project_id}")
        raise

    logger.info(f"Task created id={out.id}")

    return out


@tasks_router.get("/{project_id}/tasks", response_model=PaginatedResponse[s.TaskOut])
async def list_tasks(
    project_id: int,
    status: Optional[s.TaskStatus] = Query(default=None),
    assigned_user_id: Optional[int] = Query(default=None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_roles(READ_ROLES)),
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
    )


@tasks_router.get("/{project_id}/tasks/{task_id}", response_model=s.TaskOut)
async def get_task(
    project_id: int,
    task_id: int,
    current_user: User = Depends(require_roles(READ_ROLES)),
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
    payload: s.TaskUpdate,
    current_user: User = Depends(require_roles(TASK_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    logger.info(f"Updating task id={task_id}")

    try:
        out = await service.update_task(
            db, current_user, project_id=project_id, task_id=task_id, payload=payload
        )
        await bump_cache_version(redis, VERSION_KEY)
    except Exception:
        logger.exception(f"Task update failed id={task_id}")
        raise

    logger.info(f"Task updated id={task_id}")

    return out


@tasks_router.delete("/{project_id}/tasks/{task_id}")
async def delete_task(
    project_id: int,
    task_id: int,
    current_user: User = Depends(require_roles(TASK_DELETE_ROLES)),
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
    current_user: User = Depends(require_roles(READ_ROLES)),
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
    current_user: User = Depends(require_roles(READ_ROLES)),
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
    "/{project_id}/tasks/{task_id}/comments", response_model=s.CommentOut
)
async def create_comment(
    project_id: int,
    task_id: int,
    payload: s.CommentCreate,
    current_user: User = Depends(require_roles(READ_ROLES)),
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
    current_user: User = Depends(require_roles(READ_ROLES)),
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
    current_user: User = Depends(require_roles(FINANCIAL_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):

    project = await db.get(m.Project, project_id)
    if not project:
        raise NotFoundError("Project not found")

    await assert_project_access(
        db,
        project_id=project_id,
        current_user=current_user,
    )

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
    payload: s.DSRCreate,
    current_user: User = Depends(require_roles(DSR_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(
        f"Creating DSR project_id={payload.project_id} date={payload.report_date}"
    )

    project = await db.get(m.Project, payload.project_id)
    if not project:
        raise NotFoundError("Project not found")

    await assert_project_access(
        db,
        project_id=payload.project_id,
        current_user=current_user,
    )

    existing = await db.scalar(
        select(m.DailySiteReport).where(
            m.DailySiteReport.project_id == payload.project_id,
            m.DailySiteReport.report_date == payload.report_date,
        )
    )

    if existing:
        raise BadRequestError("DSR already exists for this date")

    # Contractor validation
    if payload.contractor_id:
        contractor = await db.get(Contractor, payload.contractor_id)
        if not contractor:
            raise ValidationError("Invalid contractor_id")

    # Labour summary
    labour_result = await db.execute(
        select(
            m.Labour.skill_type,
            func.count(m.Labour.id),
        ).where(
            m.Labour.project_id == payload.project_id,
            m.Labour.status == LabourStatus.ACTIVE,
        ).group_by(m.Labour.skill_type)
    )

    skilled = 0
    unskilled = 0

    for row in labour_result:
        if row.skill_type == SkillType.SKILLED:
            skilled = row[1]
        else:
            unskilled += row[1]

    total_labour = skilled + unskilled

    data = payload.model_dump()

    data["created_by_id"] = current_user.id

    data["total_labour"] = total_labour
    data["skilled_labour"] = skilled
    data["unskilled_labour"] = unskilled

    obj = m.DailySiteReport(**data)

    try:
        db.add(obj)
        await db.flush()
        await db.refresh(obj)
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

    return dsr_out


# =========================
# GET PROJECT DSR
# =========================
@dsr_router.get("/project/{project_id}", response_model=PaginatedResponse[s.DSROut])
async def get_project_dsr(
    project_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_roles(DSR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(f"Fetching DSR for project_id={project_id}")

    try:
        project = await db.get(m.Project, project_id)
        if not project:
            raise NotFoundError("Project not found")

        await assert_project_access(
            db,
            project_id=project_id,
            current_user=current_user,
        )

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

    except Exception as e:
        traceback.print_exc() 
        raise DataIntegrityError("Data integrity issue")

# =========================
# GET DSR BY ID
# =========================
@dsr_router.get("/{id}", response_model=s.DSROut)
async def get_dsr(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(DSR_READ_ROLES)),
    redis=Depends(get_request_redis),
):
    logger.info(f"Fetching DSR id={id}")

    version = await get_cache_version(redis, "cache_version:dsr")
    cache_key = f"cache:dsr:get:{version}:{id}"

    cached = await cache_get_json(redis, cache_key)
    if cached:
        return s.DSROut.model_validate(cached)

    result = await db.execute(
        select(m.DailySiteReport)
        .options(
            selectinload(m.DailySiteReport.contractor),
            selectinload(m.DailySiteReport.created_by),
        )
        .where(m.DailySiteReport.id == id)
    )

    obj = result.scalar_one_or_none()

    if not obj:
        raise NotFoundError("DSR not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

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
    current_user: User = Depends(require_roles(DSR_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    logger.info(f"Updating DSR id={id}")

    result = await db.execute(
        select(m.DailySiteReport)
        .options(
            selectinload(m.DailySiteReport.contractor),
            selectinload(m.DailySiteReport.created_by),
        )
        .where(m.DailySiteReport.id == id)
    )

    obj = result.scalar_one_or_none()

    if not obj:
        raise NotFoundError("DSR not found")

    if obj.status == "Approved":
        raise ValidationError("Cannot update approved DSR")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

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


@dsr_router.post("/{dsr_id}/photos")
async def upload_dsr_photos(
    dsr_id: int,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(DSR_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    dsr = await db.get(m.DailySiteReport, dsr_id)
    if not dsr:
        raise NotFoundError("DSR not found")

    await assert_project_access(
        db,
        project_id=dsr.project_id,
        current_user=current_user,
    )

    upload_dir = "uploads/dsr"
    os.makedirs(upload_dir, exist_ok=True)

    if not file.content_type or not file.content_type.startswith("image/"):
        raise BadRequestError("Only image files are allowed")

    content = await file.read()

    if len(content) > 5 * 1024 * 1024:
        raise BadRequestError("File too large (max 5MB)")

    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
    except Exception:
        raise BadRequestError("Invalid image file")

    safe_name = pathlib.Path(file.filename or "file").name
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", safe_name)

    allowed_extensions = {"jpg", "jpeg", "png"}
    ext = pathlib.Path(safe_name).suffix.lower().replace(".", "")

    if ext not in allowed_extensions:
        raise BadRequestError("Only JPG, JPEG, PNG allowed")

    filename = f"{uuid.uuid4()}_{safe_name}"

    path = os.path.join(upload_dir, filename).replace("\\", "/")

    with open(path, "wb") as f:
        f.write(content)

    photo = m.DSRPhoto(dsr_id=dsr_id, file_url=path)
    db.add(photo)

    await db.flush()

    base_url = str(request.base_url).rstrip("/")
    file_url = f"{base_url}/{path}"

    return {
        "status": "success",
        "uploaded": [file_url],
    }


@dsr_router.get("/project/{project_id}/map")
async def get_dsr_map_points(
    project_id: int,
    current_user: User = Depends(require_roles(DSR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
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
    current_user: User = Depends(require_roles(DSR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    if start_date and end_date and end_date < start_date:
        raise BadRequestError("end_date cannot be before start_date")

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
    current_user: User = Depends(require_roles(DSR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    if start_date and end_date and end_date < start_date:
        raise BadRequestError("end_date cannot be before start_date")

    await assert_project_access(
        db,
        project_id=project_id,
        current_user=current_user,
    )

    query = (
        select(
            Contractor.name,
            func.count(m.DailySiteReport.id),
        )
        .select_from(m.DailySiteReport)
        .join(Contractor, Contractor.id == m.DailySiteReport.contractor_id, isouter=True)
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
    current_user: User = Depends(require_roles(DSR_DELETE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis = Depends(get_request_redis), 
):
    logger.info(f"Deleting DSR id={id}")

    obj = await db.get(m.DailySiteReport, id)

    if not obj:
        logger.warning(f"DSR not found id={id}")
        raise NotFoundError("DSR not found")

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
    current_user: User = Depends(require_roles(DSR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    dsr = await db.get(m.DailySiteReport, dsr_id)
    if not dsr:
        raise NotFoundError("DSR not found")

    result = await db.execute(select(m.DSRPhoto).where(m.DSRPhoto.dsr_id == dsr_id))
    rows = result.scalars().all()

    return [{"id": p.id, "url": p.file_url} for p in rows]


@dsr_router.delete("/photo/{photo_id}")
async def delete_dsr_photo(
    photo_id: int,
    current_user: User = Depends(require_roles(DSR_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(m.DSRPhoto, photo_id)

    if not obj:
        raise NotFoundError("Photo not found")

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
    current_user: User = Depends(require_roles(DSR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Exporting DSR Excel project_id={project_id}")

    await assert_project_access(
        db,
        project_id=project_id,
        current_user=current_user,
    )

    query = select(m.DailySiteReport).where(m.DailySiteReport.project_id == project_id)

    if start_date:
        query = query.where(m.DailySiteReport.report_date >= start_date)

    if end_date:
        query = query.where(m.DailySiteReport.report_date <= end_date)

    if contractor_name:
        contractor_name = contractor_name.strip()

        query = query.join(
            Contractor,
            Contractor.id == m.DailySiteReport.contractor_id
        ).where(
            Contractor.name.ilike(f"%{contractor_name}%")
        )

    query = query.order_by(m.DailySiteReport.report_date.desc())

    result = await db.execute(query)
    rows = result.scalars().all()

    if not rows:
        raise NotFoundError("No DSR data found")

    wb = Workbook()
    ws = wb.active
    ws.title = "DSR Report"

    headers = [
        "Date",
        "Project ID",
        "Contractor",
        "Weather",
        "Work Done",
        "Work Planned",
        "Labour Count",
        "Material Used",
        "Issues",
        "Remarks",
        "Created By",
    ]
    ws.append(headers)

    for r in rows:
        ws.append(
            [
                str(r.report_date),
                r.project_id,
                r.contractor_name,
                r.weather,
                r.work_done,
                r.work_planned,
                r.labour_count,
                r.material_used,
                r.issues,
                r.remarks,
                r.created_by.full_name if r.created_by else None,
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
    current_user: User = Depends(require_roles(DSR_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(m.DailySiteReport, id)

    if not obj:
        raise NotFoundError("DSR not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    if obj.status != "Draft":
        raise ValidationError("Only draft DSR can be submitted")

    obj.status = "Submitted"

    await db.flush()
    # await db.commit() 

    return {"message": "DSR submitted successfully"}

@dsr_router.put("/{id}/approve")
async def approve_dsr(
    id: int,
    current_user: User = Depends(require_roles(DSR_APPROVE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(m.DailySiteReport, id)

    if not obj:
        raise NotFoundError("DSR not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    if obj.status != "Submitted":
        raise ValidationError("DSR must be submitted before approval")

    obj.status = "Approved"

    await db.flush()
    # await db.commit()

    return {"message": "DSR approved successfully"}

@dsr_router.put("/{id}/reject")
async def reject_dsr(
    id: int,
    current_user: User = Depends(require_roles(DSR_APPROVE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(m.DailySiteReport, id)

    if not obj:
        raise NotFoundError("DSR not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    if obj.status != "Submitted":
        raise ValidationError("Only submitted DSR can be rejected")

    obj.status = "Draft"

    await db.flush()
    # await db.commit() 

    return {"message": "DSR rejected and moved to draft"}


issues_router = APIRouter(
    prefix="/issues",
    tags=["Issues"],
    dependencies=[default_rate_limiter_dependency()],
)


@issues_router.post("", response_model=s.IssueOut)
async def create_issue(
    payload: s.IssueCreate,
    redis=Depends(get_request_redis),
    current_user: User = Depends(require_roles(ISSUE_CREATE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Issue create start project_id={payload.project_id}")

    project = await db.get(m.Project, payload.project_id)
    if not project:
        logger.warning(f"Project not found id={payload.project_id}")
        raise NotFoundError("Project not found")

    await assert_project_access(
        db, project_id=payload.project_id, current_user=current_user
    )

    if not payload.title or not payload.title.strip():
        raise ValidationError("title is required")

    title = payload.title.strip()

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

        obj = m.Issue(**data)

        db.add(obj)
        await db.flush()
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
    category: Optional[s.IssueCategory] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("id"),
    order: Optional[str] = Query("desc"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(READ_ROLES)),
):
    pagination = pagination.normalized()

    if current_user.role in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
        base_query = select(m.Issue)
    else:
        subquery = (
            select(m.ProjectMember.project_id)
            .where(m.ProjectMember.user_id == current_user.id)
        )

        base_query = select(m.Issue).where(
            m.Issue.project_id.in_(subquery)
        )

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

    if order.lower() == "asc":
        base_query = base_query.order_by(sort_column.asc())
    else:
        base_query = base_query.order_by(sort_column.desc())

    count_query = select(func.count()).select_from(base_query.order_by(None).subquery())
    total = await db.scalar(count_query)

    query = base_query.offset(pagination.offset).limit(pagination.limit)

    rows = (await db.execute(query)).scalars().all()

    items = [s.IssueOut.model_validate(r) for r in rows]

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
    current_user: User = Depends(require_roles(READ_ROLES)),
):
    obj = await db.get(m.Issue, id)

    if not obj:
        raise NotFoundError("Issue not found")

    await assert_project_access(
        db, project_id=obj.project_id, current_user=current_user
    )

    return s.IssueOut.model_validate(obj)


@issues_router.put("/{id}", response_model=s.IssueOut)
async def update_issue(
    id: int,
    payload: s.IssueUpdate,
    current_user: User = Depends(require_roles(ISSUE_UPDATE_ROLES)),
    redis=Depends(get_request_redis),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating issue id={id}")

    obj = await db.get(m.Issue, id)

    if not obj:
        logger.warning(f"Issue not found id={id}")
        raise NotFoundError("Issue not found")

    await assert_project_access(
        db, project_id=obj.project_id, current_user=current_user
    )

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
        user = await db.scalar(select(User).where(User.id == data["assigned_to"]))
        if not user:
            raise NotFoundError("Assigned user not found")

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
    current_user: User = Depends(require_roles(ISSUE_DELETE_ROLES)),
    redis=Depends(get_request_redis),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Deleting issue id={id}")

    obj = await db.get(m.Issue, id)

    if not obj:
        logger.warning(f"Issue not found id={id}")
        raise NotFoundError("Issue not found")

    if current_user.role not in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
        is_member = await db.scalar(
            select(func.count())
            .select_from(m.ProjectMember)
            .where(
                m.ProjectMember.project_id == obj.project_id,
                m.ProjectMember.user_id == current_user.id,
            )
        )
        if not is_member:
            raise PermissionDeniedError("User is not part of this project")

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


@issues_router.get(
    "/project/{project_id}", response_model=PaginatedResponse[s.IssueOut]
)
async def issues_by_project(
    project_id: int,
    pagination: PaginationParams = Depends(),
    current_user: User = Depends(require_roles(READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    pagination = pagination.normalized()

    total = await db.scalar(
        select(func.count()).where(m.Issue.project_id == project_id)
    )

    query = (
        select(m.Issue)
        .where(m.Issue.project_id == project_id)
        .order_by(m.Issue.id.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    rows = (await db.execute(query)).scalars().all()

    items = [s.IssueOut.model_validate(r) for r in rows]

    return PaginatedResponse(
        items=items,
        meta=PaginationMeta(
            total=int(total or 0),
            limit=pagination.limit,
            offset=pagination.offset,
        ),
    )


@dsr_router.get("/project/{project_id}/analytics/issues")
async def issue_analytics(
    project_id: int,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    current_user: User = Depends(require_roles(DSR_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    if start_date and end_date and end_date < start_date:
        raise BadRequestError("end_date cannot be before start_date")

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


router.include_router(milestones_router)
router.include_router(tasks_router)
