from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.models.equipment import Equipment
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.equipment import EquipmentCreate, EquipmentOut, EquipmentUpdate
from app.utils.helpers import NotFoundError


class EquipmentService:
    def __init__(self, db: AsyncSession, redis):
        self.db = db
        self.redis = redis
        self.version_key = "cache_version:equipment"

    async def create_equipment(self, payload: EquipmentCreate) -> EquipmentOut:
        data = payload.model_dump(exclude_unset=True)
        if data.get("total_cost") is None:
            data["total_cost"] = Decimal(data.get("quantity")) * Decimal(data.get("daily_cost"))
        obj = Equipment(**data)
        self.db.add(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return EquipmentOut.model_validate(obj)

    async def get_equipment(self, equipment_id: int) -> EquipmentOut:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:equipment:get:{version}:{equipment_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return EquipmentOut.model_validate(cached)

        obj = await self.db.scalar(select(Equipment).where(Equipment.id == equipment_id))
        if obj is None:
            raise NotFoundError("Equipment record not found")

        out = EquipmentOut.model_validate(obj)
        await cache_set_json(self.redis, cache_key, out.model_dump())
        return out

    async def list_equipment(
        self,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> PaginatedResponse[EquipmentOut]:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:equipment:list:{version}:{limit}:{offset}:{search}:{status}:{project_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return PaginatedResponse[EquipmentOut].model_validate(cached)

        query = select(Equipment)
        count_query = select(func.count()).select_from(Equipment)

        if search:
            like = f"%{search}%"
            query = query.where(Equipment.equipment_name.ilike(like))
            count_query = count_query.where(Equipment.equipment_name.ilike(like))

        if status:
            query = query.where(Equipment.status == status)
            count_query = count_query.where(Equipment.status == status)

        if project_id is not None:
            query = query.where(Equipment.project_id == project_id)
            count_query = count_query.where(Equipment.project_id == project_id)

        query = query.order_by(Equipment.id.desc()).limit(limit).offset(offset)

        total = await self.db.scalar(count_query)
        rows = (await self.db.execute(query)).scalars().all()

        items = [EquipmentOut.model_validate(r).model_dump() for r in rows]
        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
        result = {"items": items, "meta": meta.model_dump()}
        await cache_set_json(self.redis, cache_key, result)
        return PaginatedResponse[EquipmentOut].model_validate(result)

    async def update_equipment(self, equipment_id: int, payload: EquipmentUpdate) -> EquipmentOut:
        obj = await self.db.scalar(select(Equipment).where(Equipment.id == equipment_id))
        if obj is None:
            raise NotFoundError("Equipment record not found")

        data = payload.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(obj, k, v)

        if "quantity" in data or "daily_cost" in data:
            obj.total_cost = Decimal(obj.quantity) * Decimal(obj.daily_cost)

        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return EquipmentOut.model_validate(obj)

    async def delete_equipment(self, equipment_id: int) -> None:
        obj = await self.db.scalar(select(Equipment).where(Equipment.id == equipment_id))
        if obj is None:
            raise NotFoundError("Equipment record not found")

        await self.db.delete(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)

