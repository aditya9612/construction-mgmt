from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.models.material import Material
from app.models.project import Project
from app.models.user import User, UserRole
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.material import MaterialCreate, MaterialOut, MaterialUpdate
from app.utils.helpers import NotFoundError

router = APIRouter(prefix="/materials", tags=["materials"])

VERSION_KEY = "cache_version:materials"


# -------------------------
# 1. CREATE MATERIAL
# -------------------------
@router.post("", response_model=MaterialOut)
async def create_material(
    payload: MaterialCreate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    project = await db.scalar(select(Project).where(Project.id == payload.project_id))
    if not project:
        raise NotFoundError("Project not found")

    data = payload.model_dump()

    purchased = Decimal(str(data.get("quantity_purchased", 0)))
    rate = Decimal(str(data.get("purchase_rate", 0)))
    payment_given = Decimal(str(data.get("payment_given", 0)))

    total_cost = purchased * rate

    data["remaining_stock"] = purchased
    data["payment_pending"] = total_cost - payment_given

    obj = Material(**data)
    db.add(obj)
    await db.flush()

    await bump_cache_version(redis, VERSION_KEY)

    return MaterialOut.model_validate(obj)


# -------------------------
# 2. LIST MATERIALS
# -------------------------
@router.get("", response_model=PaginatedResponse[MaterialOut])
async def list_materials(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    project_id: Optional[int] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"materials:{version}:{limit}:{offset}:{project_id}:{category}:{search}"

    cached = await cache_get_json(redis, cache_key)
    if cached:
        return PaginatedResponse[MaterialOut].model_validate(cached)

    query = select(Material)
    count_query = select(func.count()).select_from(Material)

    if project_id:
        query = query.where(Material.project_id == project_id)
        count_query = count_query.where(Material.project_id == project_id)

    if category:
        query = query.where(Material.category == category)
        count_query = count_query.where(Material.category == category)

    if search:
        like = f"%{search}%"
        query = query.where(Material.material_name.ilike(like))
        count_query = count_query.where(Material.material_name.ilike(like))

    query = query.order_by(Material.id.desc()).limit(limit).offset(offset)

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [MaterialOut.model_validate(r).model_dump() for r in rows]

    result = {
        "items": items,
        "meta": PaginationMeta(total=int(total or 0), limit=limit, offset=offset).model_dump(),
    }

    await cache_set_json(redis, cache_key, result)

    return PaginatedResponse[MaterialOut].model_validate(result)


# -------------------------
# 3. GET SINGLE MATERIAL
# -------------------------
@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(
    material_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(select(Material).where(Material.id == material_id))

    if not obj:
        raise NotFoundError("Material not found")

    return MaterialOut.model_validate(obj)


# -------------------------
# 4. UPDATE MATERIAL
# -------------------------
@router.put("/{material_id}", response_model=MaterialOut)
async def update_material(
    material_id: int,
    payload: MaterialUpdate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Material).where(Material.id == material_id))

    if not obj:
        raise NotFoundError("Material not found")

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)

    return MaterialOut.model_validate(obj)


# -------------------------
# 5. DELETE MATERIAL
# -------------------------
@router.delete("/{material_id}", status_code=204)
async def delete_material(
    material_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(Material).where(Material.id == material_id))

    if not obj:
        raise NotFoundError("Material not found")

    await db.delete(obj)
    await db.flush()

    await bump_cache_version(redis, VERSION_KEY)

    return None


# -------------------------
# 6. PURCHASE ENTRY
# -------------------------
@router.post("/{material_id}/purchase", response_model=MaterialOut)
async def add_purchase(
    material_id: int,
    quantity: Decimal,
    payment: Decimal = Decimal("0"),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(select(Material).where(Material.id == material_id))
    if not obj:
        raise NotFoundError("Material not found")

    obj.quantity_purchased += quantity
    obj.remaining_stock += quantity

    total_cost = obj.quantity_purchased * obj.purchase_rate

    obj.payment_given += payment
    obj.payment_pending = total_cost - obj.payment_given

    await db.flush()

    return MaterialOut.model_validate(obj)


# -------------------------
# 7. USAGE ENTRY
# -------------------------
@router.post("/{material_id}/usage", response_model=MaterialOut)
async def add_usage(
    material_id: int,
    quantity: Decimal,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(select(Material).where(Material.id == material_id))
    if not obj:
        raise NotFoundError("Material not found")

    if obj.quantity_used + quantity > obj.quantity_purchased:
        raise ValueError("Usage exceeds purchased quantity")

    obj.quantity_used += quantity
    obj.remaining_stock = obj.quantity_purchased - obj.quantity_used

    await db.flush()

    return MaterialOut.model_validate(obj)


# -------------------------
# 8. STOCK SUMMARY
# -------------------------
@router.get("/summary")
async def material_summary(db: AsyncSession = Depends(get_db_session)):
    total_materials = await db.scalar(select(func.count()).select_from(Material))
    total_stock = await db.scalar(select(func.sum(Material.remaining_stock)))

    low_stock_count = await db.scalar(
        select(func.count()).where(Material.remaining_stock < 10)
    )

    return {
        "total_materials": total_materials or 0,
        "total_stock": float(total_stock or 0),
        "low_stock_count": low_stock_count or 0,
    }


# -------------------------
# 9. LOW STOCK ALERT
# -------------------------
@router.get("/low-stock", response_model=list[MaterialOut])
async def low_stock(
    threshold: Decimal = Decimal("10"),
    db: AsyncSession = Depends(get_db_session),
):
    rows = (await db.execute(
        select(Material).where(Material.remaining_stock < threshold)
    )).scalars().all()

    return [MaterialOut.model_validate(r) for r in rows]


# -------------------------
# 10. MATERIAL REPORT
# -------------------------
@router.get("/report")
async def material_report(
    project_id: Optional[int] = None,
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    query = select(Material)

    if project_id:
        query = query.where(Material.project_id == project_id)

    if category:
        query = query.where(Material.category == category)

    rows = (await db.execute(query)).scalars().all()

    return [
        {
            "material_name": r.material_name,
            "category": r.category,
            "total_cost": float(r.quantity_purchased * r.purchase_rate),
            "remaining_stock": float(r.remaining_stock),
        }
        for r in rows
    ]


# -------------------------
# 11. PROJECT-WISE MATERIAL
# -------------------------
@router.get("/project/{project_id}", response_model=list[MaterialOut])
async def materials_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    rows = (await db.execute(
        select(Material).where(Material.project_id == project_id)
    )).scalars().all()

    return [MaterialOut.model_validate(r) for r in rows]