from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency

from app.cache.redis import (
    bump_cache_version,
    cache_get_json,
    cache_set_json,
    get_cache_version,
)

from app.core.dependencies import (
    get_current_active_user,
    get_request_redis,
    require_roles,
)

from app.models.project import (
    Comment,
    Milestone,
    Project,
    ProjectMember,
    Task,
    TaskProgress,
)
from app.models.user import User, UserRole
from app.models.owner import Owner
from app.models.expense import Expense
from app.models.invoice import Invoice

from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.project import (
    CommentCreate,
    CommentOut,
    MilestoneCreate,
    MilestoneOut,
    MilestoneUpdate,
    ProjectCreate,
    ProjectMemberAssign,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdate,
    TaskCreate,
    TaskOut,
    TaskProgressOut,
    TaskProgressUpdate,
    TaskUpdate,
)

from app.utils.helpers import (
    NotFoundError,
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)

def compute_project_status(project):
    today = date.today()

    # Manual override
    if project.status == "Completed":
        return "Completed"

    if project.start_date and today < project.start_date:
        return "Planned"

    if project.end_date and today > project.end_date:
        return "Delayed"

    return "Active"

router = APIRouter(
    prefix="/projects",tags=["project_management"],
    dependencies=[default_rate_limiter_dependency()],
)

VERSION_KEY = "cache_version:projects"

class ProjectsRepository:
    async def create_project(self, db: AsyncSession, data: dict) -> Project:
        obj = Project(**data)
        db.add(obj)
        await db.flush()
        return obj

    async def get_project(self, db: AsyncSession, project_id: int) -> Optional[Project]:
        return await db.scalar(select(Project).where(Project.id == project_id))

    async def list_projects(
        self,
        db: AsyncSession,
        *,
        limit: int,
        offset: int,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> tuple[list[Project], int]:
        query = select(Project)
        count_query = select(func.count()).select_from(Project)

        if search:
            like = f"%{search}%"
            query = query.where(Project.project_name.ilike(like))
            count_query = count_query.where(Project.project_name.ilike(like))

        if status:
            query = query.where(Project.status == status)
            count_query = count_query.where(Project.status == status)

        query = query.order_by(Project.id.desc()).limit(limit).offset(offset)

        total = await db.scalar(count_query)
        rows = (await db.execute(query)).scalars().all()
        return rows, int(total or 0)

    async def update_project(
        self, db: AsyncSession, obj: Project, data: dict
    ) -> Project:
        for k, v in data.items():
            setattr(obj, k, v)
        await db.flush()
        return obj

    async def delete_project(self, db: AsyncSession, obj: Project) -> None:
        await db.delete(obj)
        await db.flush()


class ProjectMembersRepository:
    async def get_member(
        self, db: AsyncSession, *, project_id: int, user_id: int
    ) -> Optional[ProjectMember]:
        return await db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
            )
        )

    async def assign_member(
        self, db: AsyncSession, *, project_id: int, user_id: int
    ) -> ProjectMember:
        obj = ProjectMember(project_id=project_id, user_id=user_id)
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
            .select_from(ProjectMember)
            .where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
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
            .select_from(ProjectMember)
            .where(ProjectMember.project_id == project_id)
        )

        query = (
            select(User)
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .where(ProjectMember.project_id == project_id)
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
    ) -> Milestone:
        obj = Milestone(project_id=project_id, **data)
        db.add(obj)
        await db.flush()
        return obj

    async def get_milestone(
        self, db: AsyncSession, *, project_id: int, milestone_id: int
    ) -> Optional[Milestone]:
        return await db.scalar(
            select(Milestone).where(
                Milestone.project_id == project_id, Milestone.id == milestone_id
            )
        )

    async def list_milestones(
        self, db: AsyncSession, *, project_id: int
    ) -> list[Milestone]:
        query = (
            select(Milestone)
            .where(Milestone.project_id == project_id)
            .order_by(Milestone.id.desc())
        )
        return (await db.execute(query)).scalars().all()

    async def update_milestone(
        self, db: AsyncSession, *, obj: Milestone, data: dict
    ) -> Milestone:
        for k, v in data.items():
            setattr(obj, k, v)
        await db.flush()
        return obj

    async def delete_milestone(self, db: AsyncSession, *, obj: Milestone) -> None:
        await db.delete(obj)
        await db.flush()


class TasksRepository:
    async def create_task(
        self, db: AsyncSession, *, project_id: int, data: dict
    ) -> Task:
        obj = Task(project_id=project_id, **data)
        db.add(obj)
        await db.flush()
        return obj

    async def get_task(
        self, db: AsyncSession, *, project_id: int, task_id: int
    ) -> Optional[Task]:
        return await db.scalar(
            select(Task).where(Task.project_id == project_id, Task.id == task_id)
        )

    async def list_tasks(
        self,
        db: AsyncSession,
        *,
        project_id: int,
        status: Optional[str],
        assigned_user_id: Optional[int],
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        query = select(Task).where(Task.project_id == project_id)
        count_query = (
            select(func.count()).select_from(Task).where(Task.project_id == project_id)
        )

        if status is not None:
            query = query.where(Task.status == status)
            count_query = count_query.where(Task.status == status)

        if assigned_user_id is not None:
            query = query.where(Task.assigned_user_id == assigned_user_id)
            count_query = count_query.where(Task.assigned_user_id == assigned_user_id)

        query = query.order_by(Task.id.desc()).limit(limit).offset(offset)

        total = await db.scalar(count_query)
        rows = (await db.execute(query)).scalars().all()
        return rows, int(total or 0)

    async def update_task(self, db: AsyncSession, *, obj: Task, data: dict) -> Task:
        for k, v in data.items():
            setattr(obj, k, v)
        await db.flush()
        return obj

    async def delete_task(self, db: AsyncSession, *, obj: Task) -> None:
        await db.delete(obj)
        await db.flush()

    async def list_task_completion_by_project_ids(
        self, db: AsyncSession, project_ids: list[int]
    ) -> list[tuple[int, int]]:
        if not project_ids:
            return []

        query = select(Task.project_id, Task.completion_percentage).where(
            Task.project_id.in_(project_ids)
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
    ) -> TaskProgress:
        obj = TaskProgress(
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
    ) -> tuple[list[TaskProgress], int]:
        count_query = (
            select(func.count())
            .select_from(TaskProgress)
            .where(TaskProgress.task_id == task_id)
        )
        query = (
            select(TaskProgress)
            .where(TaskProgress.task_id == task_id)
            .order_by(TaskProgress.created_at.desc())
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
    ) -> Comment:
        obj = Comment(task_id=task_id, author_user_id=author_user_id, content=content)
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
    ) -> tuple[list[Comment], int]:
        count_query = (
            select(func.count()).select_from(Comment).where(Comment.task_id == task_id)
        )
        query = (
            select(Comment)
            .where(Comment.task_id == task_id)
            .order_by(Comment.id.desc())
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
        self, db: AsyncSession, current_user: User, payload: ProjectCreate
    ) -> ProjectOut:
        self._assert_project_mutation_role(current_user)
        data = payload.model_dump(exclude_unset=True)
        data["status"] = "Planned"
        owner = await db.scalar(select(Owner).where(Owner.id == payload.owner_id))
        if not owner:
            raise NotFoundError("Owner not found")

        if payload.start_date and payload.end_date:
            if payload.end_date < payload.start_date:
                raise ValidationError("end_date cannot be before start_date")

        obj = await self.projects_repo.create_project(db, data)
        completion_map = await self._compute_completion_percentage_by_project_ids(
            db, [obj.id]
        )
        completion = completion_map.get(obj.id, 0.0)
        return ProjectOut(
            id=obj.id,
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
        limit: int,
        offset: int,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> PaginatedResponse[ProjectOut]:
        rows, total = await self.projects_repo.list_projects(
            db, limit=limit, offset=offset, search=search, status=status
        )
        project_ids = [p.id for p in rows]
        completion_map = await self._compute_completion_percentage_by_project_ids(
            db, project_ids
        )

        items = [
            ProjectOut(
                id=p.id,
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
        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[ProjectOut](items=items, meta=meta)

    async def get_project(self, db: AsyncSession, project_id: int) -> ProjectOut:
        obj = await self.projects_repo.get_project(db, project_id=project_id)
        if obj is None:
            raise NotFoundError("Project not found")
        completion_map = await self._compute_completion_percentage_by_project_ids(
            db, [obj.id]
        )
        completion = completion_map.get(obj.id, 0.0)
        return ProjectOut(
            id=obj.id,
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
        payload: ProjectUpdate,
    ) -> ProjectOut:
        self._assert_project_mutation_role(current_user)
        obj = await self.projects_repo.get_project(db, project_id=project_id)
        if obj is None:
            raise NotFoundError("Project not found")
        data = payload.model_dump(exclude_unset=True)
        if "project_name" in data and data["project_name"] is None:
            raise ValidationError("project_name cannot be null")
        if "status" in data:
            if data["status"] != "Completed":
                data.pop("status")
        await self.projects_repo.update_project(db, obj, data)
        completion_map = await self._compute_completion_percentage_by_project_ids(
            db, [obj.id]
        )
        completion = completion_map.get(obj.id, 0.0)
        return ProjectOut(
            id=obj.id,
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
        await self.projects_repo.delete_project(db, obj)


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
    ) -> ProjectMemberOut:
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

        await self.members_repo.assign_member(
            db, project_id=project_id, user_id=user_id
        )
        role = user.role.value if hasattr(user.role, "value") else str(user.role)
        return ProjectMemberOut(
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
    ) -> PaginatedResponse[ProjectMemberOut]:
        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        users, total = await self.members_repo.list_members(
            db, project_id=project_id, limit=limit, offset=offset
        )
        items: list[ProjectMemberOut] = []
        for user in users:
            role = user.role.value if hasattr(user.role, "value") else str(user.role)
            items.append(
                ProjectMemberOut(
                    user_id=user.id,
                    full_name=user.full_name,
                    email=user.email,
                    role=role,
                )
            )
        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[ProjectMemberOut](items=items, meta=meta)

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
        payload: MilestoneCreate,
    ) -> MilestoneOut:
        self._assert_milestone_mutation_role(current_user)
        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        data = payload.model_dump(exclude_unset=True)
        obj = await self.milestones_repo.create_milestone(
            db, project_id=project_id, data=data
        )
        return MilestoneOut(
            id=obj.id,
            project_id=obj.project_id,
            title=obj.title,
            description=obj.description,
            start_date=obj.start_date,
            end_date=obj.end_date,
        )

    async def list_milestones(
        self, db: AsyncSession, *, project_id: int
    ) -> list[MilestoneOut]:
        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        rows = await self.milestones_repo.list_milestones(db, project_id=project_id)
        return [
            MilestoneOut(
                id=m.id,
                project_id=m.project_id,
                title=m.title,
                description=m.description,
                start_date=m.start_date,
                end_date=m.end_date,
            )
            for m in rows
        ]

    async def get_milestone(
        self, db: AsyncSession, *, project_id: int, milestone_id: int
    ) -> MilestoneOut:
        obj = await self.milestones_repo.get_milestone(
            db, project_id=project_id, milestone_id=milestone_id
        )
        if obj is None:
            raise NotFoundError("Milestone not found")
        return MilestoneOut(
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
        payload: MilestoneUpdate,
    ) -> MilestoneOut:
        self._assert_milestone_mutation_role(current_user)
        obj = await self.milestones_repo.get_milestone(
            db, project_id=project_id, milestone_id=milestone_id
        )
        if obj is None:
            raise NotFoundError("Milestone not found")

        data = payload.model_dump(exclude_unset=True)
        if "title" in data and data["title"] is None:
            raise ValidationError("title cannot be null")
        await self.milestones_repo.update_milestone(db, obj=obj, data=data)
        return MilestoneOut(
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
        await self.milestones_repo.delete_milestone(db, obj=obj)


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
        if current_user.role not in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
            raise PermissionDeniedError("Insufficient permissions")

    def _is_delayed(self, *, task: Task, current_date: date) -> bool:
        if task.end_date is None:
            return False
        return (current_date > task.end_date) and (task.status != "Completed")

    async def _assert_progress_or_comment_auth(
        self,
        db: AsyncSession,
        *,
        current_user: User,
        project_id: int,
        task: Task,
    ) -> None:
        if current_user.id == task.assigned_user_id:
            return
        allowed = await self.members_repo.is_member(
            db, project_id=project_id, user_id=current_user.id
        )
        if not allowed:
            raise PermissionDeniedError("Insufficient permissions")

    def _task_to_out(self, *, task: Task, is_delayed: bool) -> TaskOut:
        return TaskOut(
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
        payload: TaskCreate,
    ) -> TaskOut:
        self._assert_task_mutation_role(current_user)
        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

        if payload.assigned_user_id:
            assigned_user = await db.scalar(
                select(User).where(User.id == payload.assigned_user_id)
            )
            if assigned_user is None:
                raise NotFoundError("User not found")

        data = payload.model_dump(exclude_unset=True)
        obj = await self.tasks_repo.create_task(db, project_id=project_id, data=data)
        is_delayed = self._is_delayed(task=obj, current_date=date.today())
        return self._task_to_out(task=obj, is_delayed=is_delayed)

    async def list_tasks(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        status: Optional[str],
        assigned_user_id: Optional[int],
        limit: int,
        offset: int,
    ) -> PaginatedResponse[TaskOut]:
        project = await self.projects_repo.get_project(db, project_id=project_id)
        if project is None:
            raise NotFoundError("Project not found")

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
        return PaginatedResponse[TaskOut](items=items, meta=meta)

    async def get_task(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
    ) -> TaskOut:
        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
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
        payload: TaskUpdate,
    ) -> TaskOut:
        self._assert_task_mutation_role(current_user)
        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
        if obj is None:
            raise NotFoundError("Task not found")

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
            if payload.assigned_user_id:
                assigned_user = await db.scalar(
                    select(User).where(User.id == payload.assigned_user_id)
                )
                if assigned_user is None:
                    raise NotFoundError("User not found")

        await self.tasks_repo.update_task(db, obj=obj, data=data)
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
        await self.tasks_repo.delete_task(db, obj=obj)

    async def update_task_progress(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
        payload: TaskProgressUpdate,
    ) -> TaskProgressOut:
        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
        if obj is None:
            raise NotFoundError("Task not found")

        await self._assert_progress_or_comment_auth(
            db, current_user=current_user, project_id=project_id, task=obj
        )

        if payload.percentage < 0 or payload.percentage > 100:
            raise ValidationError("percentage must be between 0 and 100")

        progress_obj = await self.progress_repo.create_progress(
            db,
            task_id=obj.id,
            percentage=int(payload.percentage),
            remarks=payload.remarks,
            created_by_user_id=current_user.id,
        )

        await db.refresh(progress_obj)

        # Side effect: store latest completion on the task row.
        await self.tasks_repo.update_task(
            db, obj=obj, data={"completion_percentage": int(payload.percentage)}
        )

        return TaskProgressOut(
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
    ) -> PaginatedResponse[TaskProgressOut]:
        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
        if obj is None:
            raise NotFoundError("Task not found")

        rows, total = await self.progress_repo.list_progress_history(
            db, task_id=obj.id, limit=limit, offset=offset
        )
        items = [
            TaskProgressOut(
                id=p.id,
                task_id=p.task_id,
                percentage=p.percentage,
                remarks=p.remarks,
                created_at=p.created_at,
            )
            for p in rows
        ]
        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[TaskProgressOut](items=items, meta=meta)

    async def create_comment(
        self,
        db: AsyncSession,
        current_user: User,
        *,
        project_id: int,
        task_id: int,
        payload: CommentCreate,
    ) -> CommentOut:
        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
        if obj is None:
            raise NotFoundError("Task not found")

        await self._assert_progress_or_comment_auth(
            db, current_user=current_user, project_id=project_id, task=obj
        )

        comment_obj = await self.comments_repo.create_comment(
            db,
            task_id=obj.id,
            author_user_id=current_user.id,
            content=payload.content,
        )
        return CommentOut(
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
    ) -> PaginatedResponse[CommentOut]:
        obj = await self.tasks_repo.get_task(db, project_id=project_id, task_id=task_id)
        if obj is None:
            raise NotFoundError("Task not found")

        rows, total = await self.comments_repo.list_comments(
            db, task_id=obj.id, limit=limit, offset=offset
        )
        items = [
            CommentOut(
                id=c.id,
                task_id=c.task_id,
                author_user_id=c.author_user_id,
                content=c.content,
            )
            for c in rows
        ]
        meta = PaginationMeta(total=int(total), limit=limit, offset=offset)
        return PaginatedResponse[CommentOut](items=items, meta=meta)

def get_tasks_service():
    return TasksService(
        ProjectsRepository(),
        ProjectMembersRepository(),
        TasksRepository(),
        TaskProgressRepository(),
        CommentsRepository(),
    )

@router.post("", response_model=ProjectOut)
async def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = ProjectsService(ProjectsRepository(), TasksRepository())
    out = await service.create_project(db, current_user, payload=payload)
    await bump_cache_version(redis, VERSION_KEY)
    return out


@router.get("", response_model=PaginatedResponse[ProjectOut])
async def list_projects(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:projects:list:{version}:{limit}:{offset}:{search}:{status}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        # Backward-compatible: older cache entries won't include `completion_percentage`.
        items = cached.get("items") if isinstance(cached, dict) else None
        if items and isinstance(items, list) and "completion_percentage" in items[0]:
            return PaginatedResponse[ProjectOut].model_validate(cached)

    service = ProjectsService(ProjectsRepository(), TasksRepository())
    result = await service.list_projects(
        db, limit=limit, offset=offset, search=search, status=status
    )
    await cache_set_json(redis, cache_key, result.model_dump())
    return result


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:projects:get:{version}:{project_id}"
    cached_json = await cache_get_json(redis, cache_key)
    if (
        cached_json is not None
        and isinstance(cached_json, dict)
        and "completion_percentage" in cached_json
    ):
        return ProjectOut.model_validate(cached_json)

    service = ProjectsService(ProjectsRepository(), TasksRepository())
    out = await service.get_project(db, project_id=project_id)
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = ProjectsService(ProjectsRepository(), TasksRepository())
    out = await service.update_project(
        db, current_user, project_id=project_id, payload=payload
    )
    await bump_cache_version(redis, VERSION_KEY)
    return out


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = ProjectsService(ProjectsRepository(), TasksRepository())
    await service.delete_project(db, current_user, project_id=project_id)
    await bump_cache_version(redis, VERSION_KEY)
    return None


@router.post(
    "/{project_id}/members/{user_id}", response_model=ProjectMemberOut, status_code=201
)
async def assign_project_member(
    project_id: int,
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = ProjectMembersService(ProjectsRepository(), ProjectMembersRepository())
    out = await service.assign_member(
        db, current_user, project_id=project_id, user_id=user_id
    )
    await bump_cache_version(redis, VERSION_KEY)
    return out


@router.get("/{project_id}/members", response_model=PaginatedResponse[ProjectMemberOut])
async def list_project_members(
    project_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    service = ProjectMembersService(ProjectsRepository(), ProjectMembersRepository())
    return await service.list_members(
        db, current_user, project_id=project_id, limit=limit, offset=offset
    )


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_project_member(
    project_id: int,
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = ProjectMembersService(ProjectsRepository(), ProjectMembersRepository())
    await service.remove_member(
        db, current_user, project_id=project_id, user_id=user_id
    )
    await bump_cache_version(redis, VERSION_KEY)
    return None

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


@milestones_router.post("/{project_id}/milestones", response_model=MilestoneOut)
async def create_milestone(
    project_id: int,
    payload: MilestoneCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = MilestonesService(ProjectsRepository(), MilestonesRepository())
    out = await service.create_milestone(
        db, current_user, project_id=project_id, payload=payload
    )
    await bump_cache_version(redis, VERSION_KEY)
    return out


@milestones_router.get("/{project_id}/milestones", response_model=list[MilestoneOut])
async def list_milestones(
    project_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    service = MilestonesService(ProjectsRepository(), MilestonesRepository())
    return await service.list_milestones(db, project_id=project_id)


@milestones_router.get(
    "/{project_id}/milestones/{milestone_id}", response_model=MilestoneOut
)
async def get_milestone(
    project_id: int,
    milestone_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    service = MilestonesService(ProjectsRepository(), MilestonesRepository())
    return await service.get_milestone(
        db, project_id=project_id, milestone_id=milestone_id
    )


@milestones_router.put(
    "/{project_id}/milestones/{milestone_id}", response_model=MilestoneOut
)
async def update_milestone(
    project_id: int,
    milestone_id: int,
    payload: MilestoneUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = MilestonesService(ProjectsRepository(), MilestonesRepository())
    out = await service.update_milestone(
        db,
        current_user,
        project_id=project_id,
        milestone_id=milestone_id,
        payload=payload,
    )
    await bump_cache_version(redis, VERSION_KEY)
    return out


@milestones_router.delete("/{project_id}/milestones/{milestone_id}", status_code=204)
async def delete_milestone(
    project_id: int,
    milestone_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = MilestonesService(ProjectsRepository(), MilestonesRepository())
    await service.delete_milestone(
        db, current_user, project_id=project_id, milestone_id=milestone_id
    )
    await bump_cache_version(redis, VERSION_KEY)
    return None


@tasks_router.post("/{project_id}/tasks", response_model=TaskOut)
async def create_task(
    project_id: int,
    payload: TaskCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    out = await service.create_task(
        db, current_user, project_id=project_id, payload=payload
    )
    await bump_cache_version(redis, VERSION_KEY)
    return out


@tasks_router.get("/{project_id}/tasks", response_model=PaginatedResponse[TaskOut])
async def list_tasks(
    project_id: int,
    status: Optional[str] = Query(default=None),
    assigned_user_id: Optional[int] = Query(default=None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_active_user),
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


@tasks_router.get("/{project_id}/tasks/{task_id}", response_model=TaskOut)
async def get_task(
    project_id: int,
    task_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    service: TasksService = Depends(get_tasks_service),
):

    return await service.get_task(
        db, current_user, project_id=project_id, task_id=task_id
    )


@tasks_router.put("/{project_id}/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    project_id: int,
    task_id: int,
    payload: TaskUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    out = await service.update_task(
        db, current_user, project_id=project_id, task_id=task_id, payload=payload
    )
    await bump_cache_version(redis, VERSION_KEY)
    return out


@tasks_router.delete("/{project_id}/tasks/{task_id}", status_code=204)
async def delete_task(
    project_id: int,
    task_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):
    await service.delete_task(
        db, current_user, project_id=project_id, task_id=task_id
    )
    await bump_cache_version(redis, VERSION_KEY)
    return None


@tasks_router.post(
    "/{project_id}/tasks/{task_id}/progress", response_model=TaskProgressOut
)
async def update_task_progress(
    project_id: int,
    task_id: int,
    payload: TaskProgressUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),

):
    out = await service.update_task_progress(
        db, current_user, project_id=project_id, task_id=task_id, payload=payload
    )
    await bump_cache_version(redis, VERSION_KEY)
    return out


@tasks_router.get(
    "/{project_id}/tasks/{task_id}/progress",
    response_model=PaginatedResponse[TaskProgressOut],
)
async def list_task_progress_history(
    project_id: int,
    task_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_active_user),
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


@tasks_router.post("/{project_id}/tasks/{task_id}/comments", response_model=CommentOut)
async def create_comment(
    project_id: int,
    task_id: int,
    payload: CommentCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    service: TasksService = Depends(get_tasks_service),
):

    out = await service.create_comment(
        db, current_user, project_id=project_id, task_id=task_id, payload=payload
    )
    await bump_cache_version(redis, VERSION_KEY)
    return out


@tasks_router.get(
    "/{project_id}/tasks/{task_id}/comments",
    response_model=PaginatedResponse[CommentOut],
)
async def list_comments(
    project_id: int,
    task_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_active_user),
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
    db: AsyncSession = Depends(get_db_session),
):

    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project not found")

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


router.include_router(milestones_router)
router.include_router(tasks_router)