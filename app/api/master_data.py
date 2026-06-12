from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.db.session import get_db_session
from app.models import master_data as m
from app.schemas import master_data as s

from app.utils.common import generate_readable_master_code
from app.utils.helpers import NotFoundError, ValidationError

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


# ===================== STATS =====================

@router.get("/stats", response_model=s.MasterDataStats)
async def get_master_stats(
    db: AsyncSession = Depends(get_db_session),
):

    materials = await db.scalar(
        select(func.count(m.MaterialMaster.id))
        .where(m.MaterialMaster.is_active == True)
    )

    labour = await db.scalar(
        select(func.count(m.LabourType.id))
        .where(m.LabourType.is_active == True)
    )

    activity = await db.scalar(
        select(func.count(m.ActivityType.id))
        .where(m.ActivityType.is_active == True)
    )

    units = await db.scalar(
        select(func.count(m.Unit.id))
        .where(m.Unit.is_active == True)
    )

    return s.MasterDataStats(
        total_materials=int(materials or 0),
        total_labour_types=int(labour or 0),
        total_activity_types=int(activity or 0),
        total_units=int(units or 0),
    )

# ===================== ALL / SEARCH =====================

@router.get(
    "/all",
    response_model=List[s.MasterDataUnified],
    response_model_exclude_none=True
)
async def get_all_master_data(
    search: Optional[str] = None,
    tag: Optional[str] = Query(
        None,
        description="MATERIAL, LABOR, ACTIVITY, UNIT"
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Returns a unified list of all master data with optional searching and tag filtering.
    """
    results = []

    # 1. Materials
    if not tag or tag == "MATERIAL":
        q = select(m.MaterialMaster).where( m.MaterialMaster.is_active == True )
        if search:
            q = q.where(
                or_(
                    m.MaterialMaster.name.ilike(f"%{search}%"),
                    m.MaterialMaster.unique_code.ilike(f"%{search}%"),
                )
            )
        res = await db.execute(q)
        for x in res.scalars().all():
            results.append(
                s.MasterDataUnified(
                    id=x.id,
                    name=x.name,
                    unique_code=x.unique_code,
                    category=x.category,
                    system_tag="MATERIAL",
                    unit=x.unit,
                )
            )

    # 2. Labor
    if not tag or tag == "LABOR":

        q = select(m.LabourType).where(
            m.LabourType.is_active == True
        )

        if search:
            q = q.where(
                or_(
                    m.LabourType.name.ilike(f"%{search}%"),
                    m.LabourType.unique_code.ilike(f"%{search}%"),
                )
            )

        res = await db.execute(q)

        for x in res.scalars().all():

            results.append(
                s.MasterDataUnified(
                    id=x.id,
                    name=x.name,
                    unique_code=x.unique_code,
                    category=x.category,
                    system_tag="LABOR",
                    skill_category=x.skill_category
                )
            )

    # 3. Activity
    if not tag or tag == "ACTIVITY":
        q = select(m.ActivityType).where(m.ActivityType.is_active == True)
        if search:
            q = q.where(
                or_(
                    m.ActivityType.name.ilike(f"%{search}%"),
                    m.ActivityType.unique_code.ilike(f"%{search}%"),
                )
            )
        res = await db.execute(q)
        for x in res.scalars().all():
            results.append(
                s.MasterDataUnified(
                    id=x.id,
                    name=x.name,
                    unique_code=x.unique_code,
                    category=x.category,
                    system_tag="ACTIVITY",
                )
            )

    # 4. Units
    if not tag or tag == "UNIT":

        q = select(m.Unit).where(
            m.Unit.is_active == True
        )

        if search:
            q = q.where(
                or_(
                    m.Unit.name.ilike(f"%{search}%"),
                    m.Unit.unique_code.ilike(f"%{search}%"),
                )
            )

        res = await db.execute(q)

        for x in res.scalars().all():

            results.append(
                s.MasterDataUnified(
                    id=x.id,
                    name=x.name,
                    unique_code=x.unique_code,
                    category=x.category,
                    system_tag="UNIT",
                )
            )

    return results


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

    result = await db.execute(select(m.Unit).where(m.Unit.is_active == True).order_by(m.Unit.name))
    data = result.scalars().all()

    response = [s.UnitOut.model_validate(x).model_dump() for x in data]

    await r.cache_set_json(redis, cache_key, response)
    return response


# ===================== CREATE UNIT =====================

@router.post("/units", response_model=s.UnitOut)
async def create_unit(
    payload: s.UnitCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    # CHECK EXISTING UNIT
    existing = await db.scalar(
        select(m.Unit).where(
            m.Unit.name == payload.name
        )
    )

    if existing:
        raise ValidationError("Unit already exists")

    # CREATE UNIT
    unique_code = await generate_readable_master_code(
        db=db,
        model=m.Unit,
        prefix="UOM",
        name=payload.name
    )

    obj = m.Unit(
        **payload.model_dump(),
        unique_code=unique_code
    )

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

    result = await db.execute(
        select(m.LabourType)
        .where(m.LabourType.is_active == True)
        .order_by(m.LabourType.name)
    )

    data = result.scalars().all()

    response = [s.LabourTypeOut.model_validate(x).model_dump() for x in data]

    await r.cache_set_json(redis, cache_key, response)

    return response


# ===================== LABOUR TYPES =====================

@router.post("/labour-types", response_model=s.LabourTypeOut)
async def create_labour_type(
    payload: s.LabourTypeCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    # CHECK EXISTING LABOUR TYPE
    existing = await db.scalar(
        select(m.LabourType).where(m.LabourType.name == payload.name)
    )

    if existing:
        raise ValidationError("Labour type already exists")

    # CREATE LABOUR TYPE
    unique_code = await generate_readable_master_code(
        db=db,
        model=m.LabourType,
        prefix="LAB",
        name=payload.name
    )

    obj = m.LabourType(
        **payload.model_dump(),
        unique_code=unique_code
    )

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

    result = await db.execute(select(m.ActivityType).where( m.ActivityType.is_active == True ).order_by(m.ActivityType.name))
    data = result.scalars().all()

    response = [s.ActivityTypeOut.model_validate(x).model_dump() for x in data]

    await r.cache_set_json(redis, cache_key, response)
    return response


# ===================== CREATE ACTIVITY TYPE =====================

@router.post("/activity-types", response_model=s.ActivityTypeOut)
async def create_activity_type(
    payload: s.ActivityTypeCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    if payload.default_unit_id:

        unit = await db.get(
            m.Unit,
            payload.default_unit_id
        )

        if not unit:
            raise ValidationError(
                "Invalid unit"
            )

    # CHECK EXISTING ACTIVITY TYPE
    existing = await db.scalar(
        select(m.ActivityType).where(
            m.ActivityType.name == payload.name
        )
    )

    if existing:
        raise ValidationError("Activity type already exists")

    # CREATE ACTIVITY TYPE
    unique_code = await generate_readable_master_code(
        db=db,
        model=m.ActivityType,
        prefix="ACT",
        name=payload.name
    )

    obj = m.ActivityType(
        **payload.model_dump(),
        unique_code=unique_code
    )

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

    result = await db.execute(
        select(m.MaterialMaster)
        .where(m.MaterialMaster.is_active == True)
        .order_by(m.MaterialMaster.name)
    )

    data = result.scalars().all()

    response = [s.MaterialMasterOut.model_validate(x).model_dump() for x in data]

    await r.cache_set_json(redis, cache_key, response)

    return response


# ===================== MATERIAL MASTER =====================

@router.post("/materials", response_model=s.MaterialMasterOut)
async def create_material_master(
    payload: s.MaterialMasterCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    # CHECK EXISTING MATERIAL
    existing = await db.scalar(
        select(m.MaterialMaster).where(m.MaterialMaster.name == payload.name)
    )

    if existing:
        raise ValidationError("Material already exists")

    # CREATE MATERIAL
    unique_code = await generate_readable_master_code(
        db=db,
        model=m.MaterialMaster,
        prefix="MAT",
        name=payload.name
    )

    obj = m.MaterialMaster(
        **payload.model_dump(),
        unique_code=unique_code
    )

    db.add(obj)

    await db.commit()

    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)

    return obj


# ===================== UPDATE =====================


@router.put("/{entity}/{id}")
async def update_master(
    entity: str,
    id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    mapping = {
        "units": (
            m.Unit,
            s.UnitUpdate
        ),

        "labour-types": (
            m.LabourType,
            s.LabourTypeUpdate
        ),

        "activity-types": (
            m.ActivityType,
            s.ActivityTypeUpdate
        ),

        "materials": (
            m.MaterialMaster,
            s.MaterialMasterUpdate
        ),
    }

    config = mapping.get(entity)

    if not config:
        raise NotFoundError("Invalid master type")

    model, schema = config

    obj = await db.get(model, id)

    if not obj:
        raise NotFoundError("Item not found")

    # VALIDATE PAYLOAD
    validated_payload = schema(**payload)

    update_data = validated_payload.model_dump(
        exclude_unset=True
    )

    if (
        entity == "activity-types"
        and "default_unit_id" in update_data
        and update_data["default_unit_id"] is not None
    ):

        unit = await db.get(
            m.Unit,
            update_data["default_unit_id"]
        )

        if not unit:
            raise ValidationError(
                "Invalid unit"
            )

    # DUPLICATE NAME CHECK
    if "name" in update_data:

        existing = await db.scalar(
            select(model).where(
                model.name == update_data["name"],
                model.id != id
            )
        )

        if existing:
            raise ValidationError(
                f"{entity} with this name already exists"
            )

    # UPDATE FIELDS
    for k, v in update_data.items():

        if hasattr(obj, k):
            setattr(obj, k, v)

    await db.commit()

    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)

    return {"message": "updated"}


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

    # SOFT DELETE
    if hasattr(obj, "is_active"):
        obj.is_active = False

    await db.commit()

    await r.bump_cache_version(redis, VERSION_KEY)

    return {"message": "deleted"}
