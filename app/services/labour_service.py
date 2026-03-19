from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.models.labour import Labour
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.labour import LabourCreate, LabourOut, LabourUpdate
from app.utils.helpers import NotFoundError


class LabourService:
    def __init__(self, db: AsyncSession, redis):
        self.db = db
        self.redis = redis
        self.version_key = "cache_version:labour"

    async def create_labour(self, payload: LabourCreate) -> LabourOut:
        data = payload.model_dump(exclude_unset=True)
        if data.get("total_cost") is None:
            data["total_cost"] = Decimal(data.get("quantity")) * Decimal(data.get("unit_cost"))
        obj = Labour(**data)
        self.db.add(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return LabourOut.model_validate(obj)

    async def get_labour(self, labour_id: int) -> LabourOut:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:labour:get:{version}:{labour_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return LabourOut.model_validate(cached)

        obj = await self.db.scalar(select(Labour).where(Labour.id == labour_id))
        if obj is None:
            raise NotFoundError("Labour record not found")

        out = LabourOut.model_validate(obj)
        await cache_set_json(self.redis, cache_key, out.model_dump())
        return out

    async def list_labour(
        self,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> PaginatedResponse[LabourOut]:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:labour:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return PaginatedResponse[LabourOut].model_validate(cached)

        query = select(Labour)
        count_query = select(func.count()).select_from(Labour)

        if search:
            like = f"%{search}%"
            query = query.where(Labour.labour_title.ilike(like))
            count_query = count_query.where(Labour.labour_title.ilike(like))

        if status:
            query = query.where(Labour.status == status)
            count_query = count_query.where(Labour.status == status)

        if project_id is not None:
            query = query.where(Labour.project_id == project_id)
            count_query = count_query.where(Labour.project_id == project_id)

        query = query.order_by(Labour.id.desc()).limit(limit).offset(offset)

        total = await self.db.scalar(count_query)
        rows = (await self.db.execute(query)).scalars().all()

        items = [LabourOut.model_validate(r).model_dump() for r in rows]
        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
        result = {"items": items, "meta": meta.model_dump()}
        await cache_set_json(self.redis, cache_key, result)
        return PaginatedResponse[LabourOut].model_validate(result)

    async def update_labour(self, labour_id: int, payload: LabourUpdate) -> LabourOut:
        obj = await self.db.scalar(select(Labour).where(Labour.id == labour_id))
        if obj is None:
            raise NotFoundError("Labour record not found")

        data = payload.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(obj, k, v)

        if "quantity" in data or "unit_cost" in data:
            obj.total_cost = Decimal(obj.quantity) * Decimal(obj.unit_cost)

        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return LabourOut.model_validate(obj)

    async def delete_labour(self, labour_id: int) -> None:
        obj = await self.db.scalar(select(Labour).where(Labour.id == labour_id))
        if obj is None:
            raise NotFoundError("Labour record not found")

        await self.db.delete(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)

