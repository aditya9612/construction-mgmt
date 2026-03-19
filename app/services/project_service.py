from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_set_json, get_cache_version
from app.models.project import Project
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.utils.helpers import NotFoundError


class ProjectService:
    def __init__(self, db: AsyncSession, redis):
        self.db = db
        self.redis = redis
        self.version_key = "cache_version:projects"

    def _to_out(self, project: Project) -> Dict[str, Any]:
        return ProjectOut.model_validate(project).model_dump()

    async def create_project(self, payload: ProjectCreate) -> ProjectOut:
        # Mutation invalidates cached list/get responses.
        data = payload.model_dump(exclude_unset=True)
        obj = Project(**data)
        self.db.add(obj)
        await self.db.flush()

        await bump_cache_version(self.redis, self.version_key)
        return ProjectOut.model_validate(obj)

    async def get_project(self, project_id: int) -> ProjectOut:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:projects:get:{version}:{project_id}"
        from app.cache.redis import cache_get_json

        cached_json = await cache_get_json(self.redis, cache_key)
        if cached_json is not None:
            return ProjectOut.model_validate(cached_json)

        obj = await self.db.scalar(select(Project).where(Project.id == project_id))
        if obj is None:
            raise NotFoundError("Project not found")

        out = ProjectOut.model_validate(obj)
        await cache_set_json(self.redis, cache_key, out.model_dump())
        return out

    async def list_projects(
        self,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> PaginatedResponse[ProjectOut]:
        from app.cache.redis import cache_get_json

        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:projects:list:{version}:{limit}:{offset}:{search}:{status}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return PaginatedResponse[ProjectOut].model_validate(cached)

        query = select(Project)
        count_query = select(func.count()).select_from(Project)

        if search:
            like = f"%{search}%"
            query = query.where(Project.name.ilike(like))
            count_query = count_query.where(Project.name.ilike(like))

        if status:
            query = query.where(Project.status == status)
            count_query = count_query.where(Project.status == status)

        query = query.order_by(Project.id.desc()).limit(limit).offset(offset)

        total = await self.db.scalar(count_query)
        rows = (await self.db.execute(query)).scalars().all()

        items = [ProjectOut.model_validate(r).model_dump() for r in rows]
        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
        result = {"items": items, "meta": meta.model_dump()}

        await cache_set_json(self.redis, cache_key, result)
        return PaginatedResponse[ProjectOut].model_validate(result)

    async def update_project(self, project_id: int, payload: ProjectUpdate) -> ProjectOut:
        obj = await self.db.scalar(select(Project).where(Project.id == project_id))
        if obj is None:
            raise NotFoundError("Project not found")

        data = payload.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(obj, k, v)

        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return ProjectOut.model_validate(obj)

    async def delete_project(self, project_id: int) -> None:
        obj = await self.db.scalar(select(Project).where(Project.id == project_id))
        if obj is None:
            raise NotFoundError("Project not found")

        await self.db.delete(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)

