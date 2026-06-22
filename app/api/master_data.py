from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.db.session import get_db_session
from app.models import master_data as m
from app.schemas import master_data as s
from sqlalchemy.orm import selectinload
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
        select(func.count(m.MaterialMaster.id)).where(
            m.MaterialMaster.is_active == True
        )
    )

    labour = await db.scalar(
        select(func.count(m.LabourType.id)).where(m.LabourType.is_active == True)
    )

    activity = await db.scalar(
        select(func.count(m.ActivityType.id)).where(m.ActivityType.is_active == True)
    )

    units = await db.scalar(
        select(func.count(m.Unit.id)).where(m.Unit.is_active == True)
    )

    return s.MasterDataStats(
        total_materials=int(materials or 0),
        total_labour_types=int(labour or 0),
        total_activity_types=int(activity or 0),
        total_units=int(units or 0),
    )


# ===================== ALL / SEARCH =====================


@router.get(
    "/all", response_model=List[s.MasterDataUnified], response_model_exclude_none=True
)
async def get_all_master_data(
    search: Optional[str] = None,
    tag: Optional[str] = Query(None, description="MATERIAL, LABOR, ACTIVITY, UNIT"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Returns a unified list of all master data with optional searching and tag filtering.
    """
    results = []

    # 1. Materials
    if not tag or tag == "MATERIAL":
        q = (
            select(m.MaterialMaster)
            .options(selectinload(m.MaterialMaster.unit))
            .where(m.MaterialMaster.is_active == True)
        )
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
                    unit=x.unit.name if x.unit else None,
                )
            )

    # 2. Labor
    if not tag or tag == "LABOR":

        q = select(m.LabourType).where(m.LabourType.is_active == True)

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
                    skill_category=x.skill_category,
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

        q = select(m.Unit).where(m.Unit.is_active == True)

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

    result = await db.execute(
        select(m.Unit).where(m.Unit.is_active == True).order_by(m.Unit.name)
    )
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
        select(m.Unit).where(func.lower(m.Unit.name) == payload.name.strip().lower())
    )

    if existing:
        raise ValidationError("Unit already exists")

    # CREATE UNIT
    unique_code = await generate_readable_master_code(
        db=db, model=m.Unit, prefix="UOM", name=payload.name
    )

    obj = m.Unit(**payload.model_dump(), unique_code=unique_code)

    db.add(obj)

    await db.commit()

    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)

    return obj


# =================update_unit============


@router.put("/units/{id}", response_model=s.UnitOut)
async def update_unit(
    id: int,
    payload: s.UnitUpdate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    obj = await db.get(m.Unit, id)

    if not obj:
        raise NotFoundError("Unit not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data:
        existing = await db.scalar(
            select(m.Unit).where(
                func.lower(m.Unit.name) == update_data["name"].strip().lower(),
                m.Unit.id != id,
            )
        )

        if existing:
            raise ValidationError("Unit already exists")

    for key, value in update_data.items():
        setattr(obj, key, value)

    await db.commit()
    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)

    return obj


# ==========delete_unit=========================


@router.delete("/units/{id}")
async def delete_unit(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):
    obj = await db.get(m.Unit, id)

    if not obj:
        raise NotFoundError("Unit not found")

    obj.is_active = False

    await db.commit()

    await r.bump_cache_version(redis, VERSION_KEY)

    return {
        "message": "Unit deleted",
        "id": obj.id,
    }


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
        db=db, model=m.LabourType, prefix="LAB", name=payload.name
    )

    obj = m.LabourType(**payload.model_dump(), unique_code=unique_code)

    db.add(obj)

    await db.commit()

    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)

    return obj


# ======================update_labour_type====================


@router.put(
    "/labour-types/{id}",
    response_model=s.LabourTypeOut,
)
async def update_labour_type(
    id: int,
    payload: s.LabourTypeUpdate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    obj = await db.get(
        m.LabourType,
        id,
    )

    if not obj:
        raise NotFoundError("Labour type not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data:
        existing = await db.scalar(
            select(m.LabourType).where(
                func.lower(m.LabourType.name) == update_data["name"].strip().lower(),
                m.LabourType.id != id,
            )
        )

        if existing:
            raise ValidationError("Labour type already exists")

    for key, value in update_data.items():
        setattr(obj, key, value)

    await db.commit()
    await db.refresh(obj)

    await r.bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return obj


# ===============delete labour================


@router.delete("/labour-types/{id}")
async def delete_labour_type(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):
    obj = await db.get(m.LabourType, id)

    if not obj:
        raise NotFoundError("Labour type not found")

    obj.is_active = False

    await db.commit()

    await r.bump_cache_version(redis, VERSION_KEY)

    return {
        "message": "Labour type deleted",
        "id": obj.id,
    }


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

    result = await db.execute(
        select(m.ActivityType)
        .where(m.ActivityType.is_active == True)
        .order_by(m.ActivityType.name)
    )
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

        unit = await db.get(m.Unit, payload.default_unit_id)

        if not unit:
            raise ValidationError("Invalid unit")

    # CHECK EXISTING ACTIVITY TYPE
    existing = await db.scalar(
        select(m.ActivityType).where(m.ActivityType.name == payload.name)
    )

    if existing:
        raise ValidationError("Activity type already exists")

    # CREATE ACTIVITY TYPE
    unique_code = await generate_readable_master_code(
        db=db, model=m.ActivityType, prefix="ACT", name=payload.name
    )

    obj = m.ActivityType(**payload.model_dump(), unique_code=unique_code)

    db.add(obj)

    await db.commit()

    await db.refresh(obj)

    await r.bump_cache_version(redis, VERSION_KEY)

    return obj


# ============Update_activity_type==============


@router.put(
    "/activity-types/{id}",
    response_model=s.ActivityTypeOut,
)
async def update_activity_type(
    id: int,
    payload: s.ActivityTypeUpdate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    obj = await db.get(
        m.ActivityType,
        id,
    )

    if not obj:
        raise NotFoundError("Activity type not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "default_unit_id" in update_data and update_data["default_unit_id"] is not None:
        unit = await db.get(
            m.Unit,
            update_data["default_unit_id"],
        )

        if not unit:
            raise ValidationError("Invalid unit")

    if "name" in update_data:
        existing = await db.scalar(
            select(m.ActivityType).where(
                func.lower(m.ActivityType.name) == update_data["name"].strip().lower(),
                m.ActivityType.id != id,
            )
        )

        if existing:
            raise ValidationError("Activity type already exists")

    for key, value in update_data.items():
        setattr(obj, key, value)

    await db.commit()
    await db.refresh(obj)

    await r.bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return obj


# ================== delete_activity_type =====================


@router.delete("/activity-types/{id}")
async def delete_activity_type(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):
    obj = await db.get(m.ActivityType, id)

    if not obj:
        raise NotFoundError("Activity type not found")

    obj.is_active = False

    await db.commit()

    await r.bump_cache_version(redis, VERSION_KEY)

    return {
        "message": "Activity type deleted",
        "id": obj.id,
    }


# ===================== MATERIAL MASTER =====================


@router.get(
    "/materials",
    response_model=list[s.MaterialMasterOut],
)
async def get_material_master(
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):

    version = await r.get_cache_version(
        redis,
        VERSION_KEY,
    )

    cache_key = f"{CACHE_KEY}:{version}:materials"

    cached = await r.cache_get_json(
        redis,
        cache_key,
    )

    if cached:
        return cached

    result = await db.execute(
        select(m.MaterialMaster)
        .options(selectinload(m.MaterialMaster.unit))
        .where(m.MaterialMaster.is_active == True)
        .order_by(m.MaterialMaster.name)
    )

    materials = result.scalars().all()

    response = [
        s.MaterialMasterOut(
            id=obj.id,
            name=obj.name,
            category=obj.category,
            unit_id=obj.unit_id,
            unit_name=(obj.unit.name if obj.unit else None),
            brand=obj.brand,
            unique_code=obj.unique_code,
            specification=obj.specification,
            hsn_code=obj.hsn_code,
            default_rate=obj.default_rate,
            minimum_stock_level=obj.minimum_stock_level,
            is_active=obj.is_active,
        ).model_dump()
        for obj in materials
    ]

    await r.cache_set_json(
        redis,
        cache_key,
        response,
    )

    return response


# ===================== CREATE MATERIAL MASTER =====================


@router.post("/materials", response_model=s.MaterialMasterOut)
async def create_material_master(
    payload: s.MaterialMasterCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    # ===== CHECK UNIT =====
    unit = await db.get(
        m.Unit,
        payload.unit_id,
    )

    if not unit:
        raise HTTPException(
            status_code=404,
            detail="Unit not found",
        )

    # ===== CHECK EXISTING MATERIAL =====
    existing = await db.scalar(
        select(m.MaterialMaster).where(
            func.lower(m.MaterialMaster.name) == payload.name.strip().lower()
        )
    )

    if existing:
        raise ValidationError("Material already exists")

    # ===== GENERATE CODE =====
    unique_code = await generate_readable_master_code(
        db=db,
        model=m.MaterialMaster,
        prefix="MAT",
        name=payload.name,
    )

    # ===== CREATE MATERIAL MASTER =====
    obj = m.MaterialMaster(
        name=payload.name.strip(),
        category=payload.category,
        unit_id=payload.unit_id,
        brand=payload.brand,
        specification=payload.specification,
        hsn_code=payload.hsn_code,
        default_rate=payload.default_rate,
        minimum_stock_level=payload.minimum_stock_level,
        is_active=payload.is_active,
        unique_code=unique_code,
    )

    db.add(obj)

    await db.commit()

    await db.refresh(obj)

    await r.bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return s.MaterialMasterOut(
        id=obj.id,
        name=obj.name,
        category=obj.category,
        unit_id=obj.unit_id,
        unit_name=unit.name,
        brand=obj.brand,
        unique_code=obj.unique_code,
        specification=obj.specification,
        hsn_code=obj.hsn_code,
        default_rate=obj.default_rate,
        minimum_stock_level=obj.minimum_stock_level,
        is_active=obj.is_active,
    )


# ===================== UPDATE =====================


@router.put(
    "/materials/{id}",
    response_model=s.MaterialMasterOut,
)
async def update_material_master(
    id: int,
    payload: s.MaterialMasterUpdate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):

    obj = await db.get(
        m.MaterialMaster,
        id,
    )

    if not obj:
        raise NotFoundError("Material not found")

    update_data = payload.model_dump(exclude_unset=True)

    # ===== UNIT VALIDATION =====
    if "unit_id" in update_data and update_data["unit_id"] is not None:

        unit = await db.get(
            m.Unit,
            update_data["unit_id"],
        )

        if not unit:
            raise ValidationError("Invalid unit")

    # ===== DUPLICATE NAME CHECK =====
    if "name" in update_data:

        existing = await db.scalar(
            select(m.MaterialMaster).where(
                func.lower(m.MaterialMaster.name)
                == update_data["name"].strip().lower(),
                m.MaterialMaster.id != id,
            )
        )

        if existing:
            raise ValidationError("Material already exists")

    # ===== UPDATE FIELDS =====
    for key, value in update_data.items():
        setattr(obj, key, value)

    await db.commit()
    await db.refresh(obj)

    # ===== FETCH UNIT NAME SAFELY =====
    unit_name = None

    if obj.unit_id:
        unit_obj = await db.get(
            m.Unit,
            obj.unit_id,
        )

        unit_name = unit_obj.name if unit_obj else None

    await r.bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return s.MaterialMasterOut(
        id=obj.id,
        name=obj.name,
        category=obj.category,
        unit_id=obj.unit_id,
        unit_name=unit_name,
        brand=obj.brand,
        unique_code=obj.unique_code,
        specification=obj.specification,
        hsn_code=obj.hsn_code,
        default_rate=obj.default_rate,
        minimum_stock_level=obj.minimum_stock_level,
        is_active=obj.is_active,
    )


# ===================== DELETE MATERIAL MASTER =====================


@router.delete("/materials/{id}")
async def delete_material_master(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    current_user: User = Depends(admin_required),
):
    obj = await db.get(m.MaterialMaster, id)

    if not obj:
        raise NotFoundError("Material master not found")

    obj.is_active = False

    await db.commit()

    await r.bump_cache_version(redis, VERSION_KEY)

    return {
        "message": "Material master deleted",
        "id": obj.id,
    }
