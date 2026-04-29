from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models import master_data as m
from app.schemas import master_data as s

from app.utils.helpers import NotFoundError

from app.core.dependencies import (
    get_request_redis,
    require_roles,
)

from app.models.user import User, UserRole

from app.cache import redis as r

router = APIRouter(prefix="/master", tags=["Master Data"])

CACHE_KEY = "master"
VERSION_KEY = "master_version"

#  FIX: define once (NO inline callable)
admin_required = require_roles([UserRole.ADMIN])


# ===================== UNITS =====================
@router.get("/units", response_model=list[s.UnitOut])
async def get_units(
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = f"{CACHE_KEY}:{version}:units"

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return cached

    result = await db.execute(select(m.Unit).order_by(m.Unit.name))
    data = result.scalars().all()

    response = [s.UnitOut.model_validate(x).model_dump() for x in data]

    await r.cache_set_json(redis, cache_key, response)
    return response


@router.post("/units", response_model=s.UnitOut)
async def create_unit(
    payload: s.UnitCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):
    obj = m.Unit(**payload.model_dump())

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)
    return obj


# ===================== LABOUR TYPES =====================
@router.get("/labour-types", response_model=list[s.LabourTypeOut])
async def get_labour_types(
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = f"{CACHE_KEY}:{version}:labour-types"

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return cached

    result = await db.execute(select(m.LabourType).order_by(m.LabourType.name))
    data = result.scalars().all()

    response = [s.LabourTypeOut.model_validate(x).model_dump() for x in data]

    await r.cache_set_json(redis, cache_key, response)
    return response


@router.post("/labour-types", response_model=s.LabourTypeOut)
async def create_labour_type(
    payload: s.LabourTypeCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):
    obj = m.LabourType(**payload.model_dump())

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)
    return obj


# ===================== ACTIVITY TYPES =====================
@router.get("/activity-types", response_model=list[s.ActivityTypeOut])
async def get_activity_types(
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = f"{CACHE_KEY}:{version}:activity-types"

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return cached

    result = await db.execute(select(m.ActivityType).order_by(m.ActivityType.name))
    data = result.scalars().all()

    response = [s.ActivityTypeOut.model_validate(x).model_dump() for x in data]

    await r.cache_set_json(redis, cache_key, response)
    return response


@router.post("/activity-types", response_model=s.ActivityTypeOut)
async def create_activity_type(
    payload: s.ActivityTypeCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):
    obj = m.ActivityType(**payload.model_dump())

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)
    return obj


# ===================== MATERIAL MASTER =====================
@router.get("/materials", response_model=list[s.MaterialMasterOut])
async def get_material_master(
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await r.get_cache_version(redis, VERSION_KEY)
    cache_key = f"{CACHE_KEY}:{version}:materials"

    cached = await r.cache_get_json(redis, cache_key)
    if cached:
        return cached

    result = await db.execute(select(m.MaterialMaster).order_by(m.MaterialMaster.name))
    data = result.scalars().all()

    response = [s.MaterialMasterOut.model_validate(x).model_dump() for x in data]

    await r.cache_set_json(redis, cache_key, response)
    return response


@router.post("/materials", response_model=s.MaterialMasterOut)
async def create_material_master(
    payload: s.MaterialMasterCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):
    obj = m.MaterialMaster(**payload.model_dump())

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)
    return obj


# ===================== DELETE =====================
@router.delete("/{entity}/{id}")
async def delete_master(
    entity: str,
    id: int,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):
    mapping = {
        "units": m.Unit,
        "labour-types": m.LabourType,
        "activity-types": m.ActivityType,
        "materials": m.MaterialMaster,
    }

    model = mapping.get(entity)

    if not model:
        raise NotFoundError("Invalid master type")

    obj = await db.get(model, id)

    if not obj:
        raise NotFoundError("Item not found")

    await db.delete(obj)
    await db.commit()

    await r.bump_cache_version(redis, VERSION_KEY)
    return {"message": "deleted"}