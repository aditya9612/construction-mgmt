from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.models.material import Material
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.material import MaterialCreate, MaterialOut, MaterialUpdate
from app.utils.helpers import NotFoundError


class MaterialService:
    def __init__(self, db: AsyncSession, redis):
        self.db = db
        self.redis = redis
        self.version_key = "cache_version:materials"

    async def create_material(self, payload: MaterialCreate) -> MaterialOut:
        obj = Material(**payload.model_dump(exclude_unset=True))
        self.db.add(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return MaterialOut.model_validate(obj)

    async def get_material(self, material_id: int) -> MaterialOut:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:materials:get:{version}:{material_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return MaterialOut.model_validate(cached)

        obj = await self.db.scalar(select(Material).where(Material.id == material_id))
        if obj is None:
            raise NotFoundError("Material not found")

        out = MaterialOut.model_validate(obj)
        await cache_set_json(self.redis, cache_key, out.model_dump())
        return out

    async def list_materials(
        self,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> PaginatedResponse[MaterialOut]:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:materials:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return PaginatedResponse[MaterialOut].model_validate(cached)

        query = select(Material)
        count_query = select(func.count()).select_from(Material)

        if search:
            like = f"%{search}%"
            query = query.where(Material.name.ilike(like))
            count_query = count_query.where(Material.name.ilike(like))

        if status:
            query = query.where(Material.status == status)
            count_query = count_query.where(Material.status == status)

        if project_id is not None:
            query = query.where(Material.project_id == project_id)
            count_query = count_query.where(Material.project_id == project_id)

        query = query.order_by(Material.id.desc()).limit(limit).offset(offset)

        total = await self.db.scalar(count_query)
        rows = (await self.db.execute(query)).scalars().all()

        items = [MaterialOut.model_validate(r).model_dump() for r in rows]
        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
        result = {"items": items, "meta": meta.model_dump()}
        await cache_set_json(self.redis, cache_key, result)
        return PaginatedResponse[MaterialOut].model_validate(result)

    async def update_material(self, material_id: int, payload: MaterialUpdate) -> MaterialOut:
        obj = await self.db.scalar(select(Material).where(Material.id == material_id))
        if obj is None:
            raise NotFoundError("Material not found")

        data = payload.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(obj, k, v)

        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return MaterialOut.model_validate(obj)

    async def delete_material(self, material_id: int) -> None:
        obj = await self.db.scalar(select(Material).where(Material.id == material_id))
        if obj is None:
            raise NotFoundError("Material not found")

        await self.db.delete(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)

