from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.models.boq import BOQ
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.boq import BOQCreate, BOQOut, BOQUpdate
from app.utils.helpers import NotFoundError


class BOQService:
    def __init__(self, db: AsyncSession, redis):
        self.db = db
        self.redis = redis
        self.version_key = "cache_version:boq"

    async def create_boq(self, payload: BOQCreate) -> BOQOut:
        data = payload.model_dump(exclude_unset=True)
        if data.get("total_cost") is None:
            data["total_cost"] = Decimal(data.get("quantity")) * Decimal(data.get("unit_cost"))
        obj = BOQ(**data)
        self.db.add(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return BOQOut.model_validate(obj)

    async def get_boq(self, boq_id: int) -> BOQOut:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:boq:get:{version}:{boq_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return BOQOut.model_validate(cached)

        obj = await self.db.scalar(select(BOQ).where(BOQ.id == boq_id))
        if obj is None:
            raise NotFoundError("BOQ item not found")

        out = BOQOut.model_validate(obj)
        await cache_set_json(self.redis, cache_key, out.model_dump())
        return out

    async def list_boq(
        self,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> PaginatedResponse[BOQOut]:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:boq:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return PaginatedResponse[BOQOut].model_validate(cached)

        query = select(BOQ)
        count_query = select(func.count()).select_from(BOQ)

        if search:
            like = f"%{search}%"
            query = query.where(BOQ.item_name.ilike(like))
            count_query = count_query.where(BOQ.item_name.ilike(like))

        if status:
            query = query.where(BOQ.status == status)
            count_query = count_query.where(BOQ.status == status)

        if project_id is not None:
            query = query.where(BOQ.project_id == project_id)
            count_query = count_query.where(BOQ.project_id == project_id)

        query = query.order_by(BOQ.id.desc()).limit(limit).offset(offset)

        total = await self.db.scalar(count_query)
        rows = (await self.db.execute(query)).scalars().all()

        items = [BOQOut.model_validate(r).model_dump() for r in rows]
        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
        result = {"items": items, "meta": meta.model_dump()}
        await cache_set_json(self.redis, cache_key, result)
        return PaginatedResponse[BOQOut].model_validate(result)

    async def update_boq(self, boq_id: int, payload: BOQUpdate) -> BOQOut:
        obj = await self.db.scalar(select(BOQ).where(BOQ.id == boq_id))
        if obj is None:
            raise NotFoundError("BOQ item not found")

        data = payload.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(obj, k, v)

        if "quantity" in data or "unit_cost" in data:
            obj.total_cost = Decimal(obj.quantity) * Decimal(obj.unit_cost)

        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return BOQOut.model_validate(obj)

    async def delete_boq(self, boq_id: int) -> None:
        obj = await self.db.scalar(select(BOQ).where(BOQ.id == boq_id))
        if obj is None:
            raise NotFoundError("BOQ item not found")

        await self.db.delete(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)

