from typing import Optional, List
from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import json

# FastAPI
from fastapi import (
    APIRouter,
    Depends,
    Query,
    HTTPException,
    BackgroundTasks,
    status,
    Request,
)
from fastapi.responses import StreamingResponse
from fastapi.encoders import jsonable_encoder

# SQLAlchemy
from sqlalchemy import exists, select, and_, or_, func, text
from sqlalchemy.ext.asyncio import AsyncSession

# Report / Excel
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

# Internal - Cache
from app.cache.redis import (
    bump_cache_version,
    cache_get_json,
    cache_set_json,
    get_cache_version,
)

# Internal - Dependencies
from app.core.dependencies import (
    get_current_active_user,
    get_request_redis,
    require_roles,
)

# Internal - DB / Models
from app.db.session import get_db_session
from app.models.equipment import (
    Equipment,
    EquipmentUsage,
    EquipmentMaintenance,
    EquipmentRental,
    EquipmentAuditLog,
)
from app.models.project import Project
from app.models.user import User, UserRole

# Internal - Enums
from app.core.enums import EquipmentCondition
from openpyxl.cell.cell import MergedCell

# Internal - Schemas
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.equipment import (
    EquipmentCreate,
    EquipmentUpdate,
    EquipmentOut,
    EquipmentUsageCreate,
    EquipmentUsageOut,
    EquipmentMaintenanceCreate,
    EquipmentMaintenanceOut,
    EquipmentRentalCreate,
    EquipmentRentalOut,
    EquipmentAuditLogOut,
    AllocationOut,
    UsageReportItem,
    CostReportItem,
    AvailabilityReportItem,
    UtilizationReportItem,
    MaintenanceAlertItem,
)

# Internal - Middleware
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from fastapi import APIRouter

public_router = APIRouter(prefix="/equipment", tags=["Public Equipment"])

# Utils
from app.utils.helpers import NotFoundError

EQUIPMENT_READ_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.ACCOUNTANT,
        UserRole.CLIENT,
    ]
]

EQUIPMENT_WRITE_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
    ]
]

router = APIRouter(
    prefix="/equipment",
    tags=["equipment"],
    dependencies=[default_rate_limiter_dependency()],
)

VERSION_KEY = "cache_version:equipment"

# === UTILITY FUNCTIONS ===
async def get_active_equipment_or_404(db: AsyncSession, equipment_id: int):
    """Get active (not deleted) equipment or 404"""
    stmt = select(Equipment).where(
        and_(Equipment.id == equipment_id, Equipment.is_deleted == False)
    )
    result = await db.execute(stmt)
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return obj


async def create_audit_log(
    db: AsyncSession,
    equipment_id: int,
    action: str,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
    user_id: Optional[int] = None,
    request: Optional[Request] = None,
):
    log = EquipmentAuditLog(
        equipment_id=equipment_id,
        action=action,
        old_values=old_values,
        new_values=new_values,
        user_id=user_id,
        ip_address=request.client.host if request else None,
    )
    db.add(log)


def serialize(data: dict):
    return {
        k: (
            v.isoformat()
            if isinstance(v, (date, datetime))
            else float(v) if isinstance(v, Decimal) else v
        )
        for k, v in data.items()
    }


def safe_parse(value):
    if value is None:
        return None

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except:
            return {"raw": value}

    return {"raw": str(value)}


def convert_decimal(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: convert_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_decimal(i) for i in obj]
    return obj


# ===================maintenance_alert=======================


@router.get("/alerts/maintenance", response_model=List[MaintenanceAlertItem])
async def maintenance_alerts(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):

    today = date.today()
    upcoming_date = today + timedelta(days=30)

    #  Subquery → nearest upcoming maintenance per equipment
    subq = (
        select(
            EquipmentMaintenance.equipment_id,
            func.min(EquipmentMaintenance.next_maintenance_date).label("next_date"),
        )
        .where(EquipmentMaintenance.next_maintenance_date.isnot(None))  # 🔥 IMPORTANT
        .group_by(EquipmentMaintenance.equipment_id)
        .subquery()
    )

    #  Main query
    stmt = (
        select(EquipmentMaintenance, Equipment)
        .join(
            subq,
            and_(
                EquipmentMaintenance.equipment_id == subq.c.equipment_id,
                EquipmentMaintenance.next_maintenance_date == subq.c.next_date,
            ),
        )
        .join(Equipment, Equipment.id == EquipmentMaintenance.equipment_id)
        .where(
            and_(
                EquipmentMaintenance.next_maintenance_date.isnot(None),  # 🔥 SAFE
                Equipment.is_deleted == False,
                or_(  #  include overdue + upcoming
                    EquipmentMaintenance.next_maintenance_date < today,
                    EquipmentMaintenance.next_maintenance_date <= upcoming_date,
                ),
            )
        )
        .order_by(EquipmentMaintenance.next_maintenance_date.asc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    alerts = []

    for maintenance, equipment in rows:
        days_until = (maintenance.next_maintenance_date - today).days

        #  status logic
        if days_until < 0:
            status = "OVERDUE"
        elif days_until == 0:
            status = "TODAY"
        elif days_until <= 3:
            status = "URGENT"
        else:
            status = "UPCOMING"

        alerts.append(
            MaintenanceAlertItem(
                equipment_id=equipment.id,
                equipment_code=equipment.equipment_code,
                maintenance_date=maintenance.next_maintenance_date,
                days_until=days_until,
                status=status,
            )
        )

    return alerts


# ================== "Availability" =======================
@router.get("/eq/availability", response_model=List[AvailabilityReportItem])
async def availability_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):
    today = date.today()

    # 🔹 get equipments
    equipments = (
        (await db.execute(select(Equipment).where(Equipment.is_deleted == False)))
        .scalars()
        .all()
    )

    #  rental ids (bulk)
    rented_ids = set(
        (
            await db.execute(
                select(EquipmentRental.equipment_id).where(
                    EquipmentRental.start_date <= today,
                    or_(
                        EquipmentRental.end_date.is_(None),
                        EquipmentRental.end_date >= today,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    #  maintenance ids (ONLY TODAY)
    maintenance_ids = set(
        (
            await db.execute(
                select(EquipmentMaintenance.equipment_id).where(
                    EquipmentMaintenance.maintenance_date == today
                )
            )
        )
        .scalars()
        .all()
    )

    response = []

    for eq in equipments:

        # 🔹 determine status
        if eq.condition == EquipmentCondition.DAMAGED:
            status = "DAMAGED"
        elif eq.id in maintenance_ids:
            status = "MAINTENANCE"
        elif eq.id in rented_ids:
            status = "RENTED"
        elif eq.project_id is not None:
            status = "ALLOCATED"
        else:
            status = "AVAILABLE"

        is_available = status == "AVAILABLE"

        response.append(
            AvailabilityReportItem(
                equipment_id=eq.id,
                equipment_code=eq.equipment_code,
                equipment_name=eq.equipment_name,
                is_available=is_available,
                project_id=eq.project_id,
            )
        )

    return response


# =========== EQUIPMENT CRUD ====================


@router.post("", response_model=EquipmentOut, status_code=status.HTTP_201_CREATED)
async def create_equipment(
    payload: EquipmentCreate,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    # Check duplicate code
    existing = await db.scalar(
        select(Equipment).where(
            and_(
                Equipment.equipment_code == payload.equipment_code,
                Equipment.is_deleted == False,
            )
        )
    )
    if existing:
        raise HTTPException(status_code=400, detail="Equipment code already exists")

    # Create project if provided
    if payload.project_id:
        project = await db.get(Project, payload.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

    obj = Equipment(**payload.model_dump())
    db.add(obj)
    await db.flush()

    # FIXED HERE
    await create_audit_log(
        db, obj.id, "CREATE", new_values=jsonable_encoder(payload.model_dump())
    )

    await bump_cache_version(redis, VERSION_KEY)
    await db.commit()
    await db.refresh(obj)
    return EquipmentOut.model_validate(obj)


@router.get("", response_model=PaginatedResponse[EquipmentOut])
async def list_equipment(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    project_id: Optional[int] = None,
    condition: Optional[str] = None,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"equipment_list:{version}:{limit}:{offset}:{search or ''}:{project_id}:{condition or ''}"

    cached = await cache_get_json(redis, cache_key)
    if cached:
        return PaginatedResponse[EquipmentOut](**cached)

    query = select(Equipment).where(Equipment.is_deleted == False)
    count_query = select(func.count(Equipment.id)).where(Equipment.is_deleted == False)

    if search:
        query = query.where(Equipment.equipment_name.ilike(f"%{search}%"))
        count_query = count_query.where(Equipment.equipment_name.ilike(f"%{search}%"))

    if project_id:
        query = query.where(Equipment.project_id == project_id)
        count_query = count_query.where(Equipment.project_id == project_id)

    if condition:
        query = query.where(func.upper(Equipment.condition) == condition.upper())
        count_query = count_query.where(
            func.upper(Equipment.condition) == condition.upper()
        )

    query = query.order_by(Equipment.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    items = [EquipmentOut.model_validate(row[0]) for row in result.all()]

    total = await db.scalar(count_query)

    response = PaginatedResponse[EquipmentOut](
        items=[item.model_dump() for item in items],
        meta=PaginationMeta(total=total or 0, limit=limit, offset=offset),
    ).model_dump()

    await cache_set_json(redis, cache_key, response)
    return PaginatedResponse[EquipmentOut].model_validate(response)


# =====================get_equipment==============================


@router.get("/{equipment_id}", response_model=EquipmentOut)
async def get_equipment(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await get_active_equipment_or_404(db, equipment_id)
    return EquipmentOut.model_validate(obj)


@router.put("/{equipment_id}", response_model=EquipmentOut)
async def update_equipment(
    equipment_id: int,
    payload: EquipmentUpdate,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    # Get existing equipment
    obj = await get_active_equipment_or_404(db, equipment_id)

    # Duplicate equipment_code check
    if payload.equipment_code and payload.equipment_code != obj.equipment_code:
        existing = await db.scalar(
            select(Equipment).where(
                and_(
                    Equipment.equipment_code == payload.equipment_code,
                    Equipment.is_deleted == False,
                    Equipment.id != equipment_id,
                )
            )
        )
        if existing:
            raise HTTPException(status_code=400, detail="Equipment code already exists")

    # Extract update data
    update_data = payload.model_dump(exclude_unset=True)

    # Capture old values
    old_data = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    # Apply updates
    for field, value in update_data.items():
        setattr(obj, field, value)

    # Update timestamp
    if hasattr(obj, "updated_at"):
        obj.updated_at = datetime.utcnow()

    # Flush changes
    await db.flush()

    # ONLY changed fields
    changed_fields = {
        k: {"old": old_data.get(k), "new": getattr(obj, k)}
        for k in update_data
        if old_data.get(k) != getattr(obj, k)
    }

    # If nothing changed → skip audit
    if not changed_fields:
        return EquipmentOut.model_validate(obj)

    # Split old & new properly
    old_values = {k: v["old"] for k, v in changed_fields.items()}
    new_values = {k: v["new"] for k, v in changed_fields.items()}

    # 🔹 Audit log (FIXED CORRECTLY)
    await create_audit_log(
        db,
        obj.id,
        "UPDATE",
        old_values=jsonable_encoder(old_values),
        new_values=jsonable_encoder(new_values),
        user_id=current_user.id,
        request=request,
    )

    # 🔹 Cache bump
    await bump_cache_version(redis, VERSION_KEY)

    # 🔹 Commit + refresh
    await db.commit()
    await db.refresh(obj)

    return EquipmentOut.model_validate(obj)


@router.delete("/{equipment_id}", status_code=204)
async def soft_delete_equipment(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await get_active_equipment_or_404(db, equipment_id)

    # old values (serialize safe)
    old_values = serialize(
        {
            "is_deleted": obj.is_deleted,
            "deleted_at": obj.deleted_at,
            "deleted_by": obj.deleted_by,
        }
    )

    # perform soft delete
    obj.is_deleted = True
    obj.deleted_at = date.today()
    obj.deleted_by = current_user.id

    # new values (serialize safe)
    new_values = serialize(
        {
            "is_deleted": obj.is_deleted,
            "deleted_at": obj.deleted_at,
            "deleted_by": obj.deleted_by,
        }
    )

    await create_audit_log(
        db,
        obj.id,
        "SOFT_DELETE",
        old_values=old_values,
        new_values=new_values,
        user_id=current_user.id,
    )

    await bump_cache_version(redis, VERSION_KEY)
    await db.commit()


# ========== ALLOCATION ===========
@router.post("/{equipment_id}/allocate", response_model=AllocationOut)
async def allocate_equipment(
    equipment_id: int,
    project_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    obj = await get_active_equipment_or_404(db, equipment_id)

    # 🔹 project check
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    today = date.today()

    #  condition check
    if obj.condition == "DAMAGED":
        raise HTTPException(400, "Damaged equipment cannot be allocated")

    #  rental check (current + future)
    rental_exists = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == equipment_id,
                or_(
                    and_(
                        EquipmentRental.start_date <= today,
                        or_(
                            EquipmentRental.end_date == None,
                            EquipmentRental.end_date >= today,
                        ),
                    ),
                    EquipmentRental.start_date > today,
                ),
            )
        )
    )

    if rental_exists:
        raise HTTPException(400, "Equipment is rented or reserved")

    #  maintenance check (only active)
    maintenance_exists = await db.scalar(
        select(
            exists().where(
                EquipmentMaintenance.equipment_id == equipment_id,
                EquipmentMaintenance.maintenance_date == today,
            )
        )
    )

    if maintenance_exists:
        raise HTTPException(400, "Equipment is under maintenance today")

    # 🔹 same project check FIRST
    if obj.project_id == project_id:
        raise HTTPException(400, "Already allocated to this project")

    # 🔹 existing allocation
    if obj.project_id is not None:
        old_project = await db.get(Project, obj.project_id)

        if old_project and old_project.end_date and old_project.end_date < today:
            # auto deallocate
            await create_audit_log(
                db,
                obj.id,
                "AUTO_DEALLOCATE",
                old_values={"project_id": obj.project_id},
                new_values={"project_id": None},
                user_id=current_user.id,
                request=request,
            )
            obj.project_id = None
            await db.flush()
        else:
            raise HTTPException(400, "Equipment already allocated to active project")

    #  allocate
    old_values = {"project_id": obj.project_id}
    obj.project_id = project_id

    await db.flush()

    await create_audit_log(
        db,
        obj.id,
        "ALLOCATE",
        old_values=old_values,
        new_values={"project_id": project_id},
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    return AllocationOut(
        equipment_id=equipment_id,
        project_id=project_id,
        allocated=True,
    )


@router.get("/{equipment_id}/allocation", response_model=AllocationOut)
async def get_allocation(
    equipment_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):
    obj = await get_active_equipment_or_404(db, equipment_id)
    return AllocationOut(
        equipment_id=obj.id,
        project_id=obj.project_id,
        allocated=obj.project_id is not None,
    )


@router.put("/{equipment_id}/deallocate", response_model=AllocationOut)
async def deallocate_equipment(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    obj = await get_active_equipment_or_404(db, equipment_id)

    # validation
    if obj.project_id is None:
        raise HTTPException(400, "Equipment is already not allocated")

    # audit old values
    old_values = {"project_id": obj.project_id}

    # deallocate
    obj.project_id = None

    # audit new values
    new_values = {"project_id": None}

    await db.flush()

    await create_audit_log(
        db,
        obj.id,
        "DEALLOCATE",
        old_values=old_values,
        new_values=new_values,
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    return AllocationOut(equipment_id=equipment_id, project_id=None, allocated=False)


# ========================== USAGE ===========================
@router.post(
    "/{equipment_id}/usage",
    response_model=EquipmentUsageOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_usage(
    equipment_id: int,
    payload: EquipmentUsageCreate,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    equipment = await get_active_equipment_or_404(db, equipment_id)

    today = date.today()

    if equipment.project_id is None:
        raise HTTPException(400, "Equipment is not allocated to any project")

    if payload.working_hours <= 0 and payload.fuel_used <= 0:
        raise HTTPException(400, "Usage cannot be zero")

    if payload.usage_date > today:
        raise HTTPException(400, "Usage date cannot be in future")

    if equipment.condition == EquipmentCondition.DAMAGED:
        raise HTTPException(400, "Equipment is damaged and cannot be used")

    rental_active = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == equipment_id,
                EquipmentRental.start_date <= payload.usage_date,
                or_(
                    EquipmentRental.end_date == None,
                    EquipmentRental.end_date >= payload.usage_date,
                ),
            )
        )
    )

    if rental_active:
        raise HTTPException(400, "Equipment is rented. Cannot log usage")

    maintenance_active = await db.scalar(
        select(
            exists().where(
                EquipmentMaintenance.equipment_id == equipment_id,
                EquipmentMaintenance.next_maintenance_date != None,
                EquipmentMaintenance.next_maintenance_date >= payload.usage_date,
            )
        )
    )

    if maintenance_active:
        raise HTTPException(400, "Equipment is under maintenance")

    obj = EquipmentUsage(equipment_id=equipment_id, **payload.model_dump())

    db.add(obj)

    equipment.working_hours += payload.working_hours
    equipment.fuel_used += payload.fuel_used

    equipment.is_in_use = True

    await db.flush()
    await db.refresh(obj)
    await db.commit()

    return EquipmentUsageOut.model_validate(obj)


@router.get("/{equipment_id}/usage", response_model=List[EquipmentUsageOut])
async def list_usage(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await get_active_equipment_or_404(db, equipment_id)

    stmt = (
        select(EquipmentUsage)
        .where(EquipmentUsage.equipment_id == equipment_id)
        .order_by(EquipmentUsage.usage_date.desc())
    )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    # safe conversion (no lazy load issues)
    return [
        EquipmentUsageOut(
            id=row.id,
            equipment_id=row.equipment_id,
            working_hours=float(row.working_hours),
            fuel_used=float(row.fuel_used),
            usage_date=row.usage_date,
            notes=row.notes,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


# ====================USAGE REPORT====================


@router.get("/usage/report", response_model=List[UsageReportItem])
async def usage_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):
    stmt = (
        select(
            EquipmentUsage.equipment_id,
            Equipment.equipment_code,
            func.sum(EquipmentUsage.working_hours).label("total_hours"),
            func.sum(EquipmentUsage.fuel_used).label("total_fuel"),
            func.avg(EquipmentUsage.working_hours).label("avg_hours"),
            func.count().label("usage_count"),
        )
        .join(Equipment)
        .where(Equipment.is_deleted == False)
        .group_by(EquipmentUsage.equipment_id, Equipment.equipment_code)
    )

    result = await db.execute(stmt)

    return [
        UsageReportItem(
            equipment_id=row.equipment_id,
            equipment_code=row.equipment_code,
            total_hours=float(row.total_hours or 0),
            total_fuel=float(row.total_fuel or 0),
            avg_hours=float(row.avg_hours or 0),
            usage_count=int(row.usage_count or 0),
        )
        for row in result.all()
    ]


# ============== MAINTENANCE =============


@router.post(
    "/{equipment_id}/maintenance",
    response_model=EquipmentMaintenanceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_maintenance(
    equipment_id: int,
    payload: EquipmentMaintenanceCreate,
    request: Request,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await get_active_equipment_or_404(db, equipment_id)

    #  Date validation FIX
    if (
        payload.next_maintenance_date
        and payload.next_maintenance_date <= payload.maintenance_date
    ):
        raise HTTPException(
            status_code=400,
            detail="Next maintenance date must be after maintenance date",
        )

    #  Create object
    obj = EquipmentMaintenance(
        **payload.model_dump(),
        equipment_id=equipment_id,
    )

    db.add(obj)
    await db.flush()
    await db.refresh(obj)

    #  Audit log safe
    await create_audit_log(
        db=db,
        equipment_id=equipment_id,
        action="MAINTENANCE_CREATE",
        old_values=None,
        new_values={
            "description": payload.description,
            "cost": float(payload.cost or 0),
            "maintenance_date": str(payload.maintenance_date),
            "next_maintenance_date": str(payload.next_maintenance_date),
        },
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    #  STATUS CALCULATION (MAIN FIX)
    today = date.today()

    if obj.next_maintenance_date:
        if obj.next_maintenance_date < today:
            status = "OVERDUE"
        elif obj.next_maintenance_date == today:
            status = "TODAY"
        else:
            status = "UPCOMING"
    else:
        status = "NO_SCHEDULE"

    #  FINAL RESPONSE
    return EquipmentMaintenanceOut(
        id=obj.id,
        equipment_id=obj.equipment_id,
        description=obj.description,
        maintenance_date=obj.maintenance_date,
        cost=float(obj.cost or 0),
        next_maintenance_date=obj.next_maintenance_date,
        created_at=obj.created_at,
        status=status,
    )


@router.get("/{equipment_id}/maintenance", response_model=List[EquipmentMaintenanceOut])
async def list_maintenance(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await get_active_equipment_or_404(db, equipment_id)

    stmt = (
        select(EquipmentMaintenance)
        .where(EquipmentMaintenance.equipment_id == equipment_id)
        .order_by(EquipmentMaintenance.maintenance_date.desc())
    )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    today = date.today()

    return [
        EquipmentMaintenanceOut(
            id=row.id,
            equipment_id=row.equipment_id,
            description=row.description,
            maintenance_date=row.maintenance_date,
            cost=float(row.cost or 0),
            next_maintenance_date=row.next_maintenance_date,
            created_at=row.created_at,
            status=(
                "OVERDUE"
                if row.maintenance_date < today
                else "TODAY" if row.maintenance_date == today else "UPCOMING"
            ),
        )
        for row in rows
    ]


# ======================= RENTAL ====================


@router.post(
    "/{equipment_id}/rental",
    response_model=EquipmentRentalOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_rental(
    equipment_id: int,
    payload: EquipmentRentalCreate,
    request: Request,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    equipment = await get_active_equipment_or_404(db, equipment_id)

    # 🔹 Date validation
    if payload.end_date and payload.end_date < payload.start_date:
        raise HTTPException(400, "End date cannot be before start date")

    start_date = payload.start_date
    end_date = payload.end_date or payload.start_date

    # 🔹 Cost validation
    if payload.rental_cost <= 0:
        raise HTTPException(400, "Rental cost must be greater than 0")

    #  UPDATED PROJECT CHECK (MAIN FIX)
    if equipment.project_id is not None:
        project = await db.get(Project, equipment.project_id)

        if project and project.end_date:
            #  Project completed → auto deallocate
            if project.end_date < date.today():
                equipment.project_id = None
                await db.flush()
            else:
                #  Active project
                raise HTTPException(
                    status_code=400,
                    detail="Equipment is currently allocated to an active project",
                )
        else:
            raise HTTPException(
                status_code=400,
                detail="Equipment is allocated to a project",
            )

    #  Overlap check
    stmt = select(EquipmentRental.id).where(
        EquipmentRental.equipment_id == equipment_id,
        or_(
            EquipmentRental.end_date.is_(None),
            and_(
                EquipmentRental.start_date <= end_date,
                EquipmentRental.end_date >= start_date,
            ),
        ),
    )

    result = await db.execute(stmt)
    exists = result.scalars().first()

    if exists:
        raise HTTPException(
            status_code=400,
            detail="Equipment already rented in this period",
        )

    #  Create rental
    data = payload.model_dump()
    data["end_date"] = end_date

    obj = EquipmentRental(
        **data,
        equipment_id=equipment_id,
    )

    db.add(obj)
    await db.flush()
    await db.refresh(obj)

    #  Audit log
    await create_audit_log(
        db=db,
        equipment_id=equipment_id,
        action="RENTAL_CREATE",
        old_values=None,
        new_values=jsonable_encoder(
            {
                "start_date": start_date,
                "end_date": end_date,
                "rental_cost": payload.rental_cost,
                "client_name": payload.client_name,
            }
        ),
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    #  Response logic
    today = date.today()
    final_end = obj.end_date or obj.start_date

    if obj.start_date > today:
        status = "UPCOMING"
    elif final_end < today:
        status = "COMPLETED"
    else:
        status = "ACTIVE"

    duration = (final_end - obj.start_date).days + 1
    per_day_cost = float(obj.rental_cost) / duration if duration else 0

    return EquipmentRentalOut(
        id=obj.id,
        equipment_id=obj.equipment_id,
        start_date=obj.start_date,
        end_date=obj.end_date,
        rental_cost=float(obj.rental_cost),
        client_name=obj.client_name,
        notes=obj.notes,
        created_at=obj.created_at,
        status=status,
        duration=duration,
        per_day_cost=round(per_day_cost, 2),
    )


@router.get("/{equipment_id}/rental", response_model=List[EquipmentRentalOut])
async def list_rental(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await get_active_equipment_or_404(db, equipment_id)

    stmt = (
        select(EquipmentRental)
        .where(EquipmentRental.equipment_id == equipment_id)
        .order_by(EquipmentRental.start_date.desc())
    )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    today = date.today()
    response = []

    for row in rows:
        # normalize end_date
        end_date = row.end_date or row.start_date

        # status logic
        if row.start_date > today:
            status = "UPCOMING"
        elif end_date < today:
            status = "COMPLETED"
        else:
            status = "ACTIVE"

        # duration
        duration = (end_date - row.start_date).days + 1

        #  per day cost
        per_day_cost = float(row.rental_cost or 0) / duration if duration else 0

        response.append(
            EquipmentRentalOut(
                id=row.id,
                equipment_id=row.equipment_id,
                start_date=row.start_date,
                end_date=row.end_date,
                rental_cost=float(row.rental_cost or 0),
                client_name=row.client_name,
                notes=row.notes,
                created_at=row.created_at,
                status=status,
                duration=duration,
                per_day_cost=round(per_day_cost, 2),
            )
        )

    return response


@router.get("/cost/report", response_model=List[CostReportItem])
async def cost_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):
    stmt = (
        select(
            EquipmentRental.equipment_id,
            Equipment.equipment_code,
            func.sum(EquipmentRental.rental_cost).label("total_cost"),
            func.count().label("rental_count"),
            func.sum(
                func.coalesce(
                    func.datediff(EquipmentRental.end_date, EquipmentRental.start_date),
                    0,
                )
                + 1
            ).label("total_days"),
        )
        .join(Equipment)
        .where(Equipment.is_deleted == False)
        .group_by(EquipmentRental.equipment_id, Equipment.equipment_code)
        .order_by(func.sum(EquipmentRental.rental_cost).desc())
    )

    result = await db.execute(stmt)

    response = []

    for row in result.all():
        total_cost = float(row.total_cost or 0)
        rental_count = int(row.rental_count or 0)
        total_days = int(row.total_days or 0)

        avg_cost = total_cost / rental_count if rental_count else 0
        revenue_per_day = total_cost / total_days if total_days else 0

        response.append(
            CostReportItem(
                equipment_id=row.equipment_id,
                equipment_code=row.equipment_code,
                total_cost=total_cost,
                rental_count=rental_count,
                avg_cost=round(avg_cost, 2),
                total_days=total_days,
                revenue_per_day=round(revenue_per_day, 2),
            )
        )

    return response


# =================== ADVANCED APIs ============


@router.get("/report/utilization", response_model=List[UtilizationReportItem])
async def utilization_report(
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    MAX_HOURS = 240  # configurable later

    stmt = (
        select(
            Equipment.id.label("equipment_id"),
            Equipment.equipment_code,
            func.coalesce(func.sum(EquipmentUsage.working_hours), 0).label(
                "total_hours"
            ),
        )
        .outerjoin(
            EquipmentUsage, Equipment.id == EquipmentUsage.equipment_id
        )  #  important fix
        .where(Equipment.is_deleted == False)
        .group_by(Equipment.id, Equipment.equipment_code)
    )

    result = await db.execute(stmt)
    rows = result.all()

    response = []

    for row in rows:
        total_hours = float(row.total_hours or 0)

        utilization_rate = (total_hours / MAX_HOURS) * 100 if MAX_HOURS else 0

        response.append(
            UtilizationReportItem(
                equipment_id=row.equipment_id,
                equipment_code=row.equipment_code,
                total_hours=round(total_hours, 2),  #  clean output
                utilization_rate=round(utilization_rate, 2),
            )
        )

    return response


# ===================equipment_alerts======================================
# 🔧 configurable limits
WORKING_HOURS_LIMIT = 1000


@router.get("/alerts/equipment", response_model=list[dict])
async def equipment_alerts(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):

    stmt = select(Equipment).where(
        and_(
            Equipment.is_deleted == False,
            or_(
                Equipment.condition == EquipmentCondition.DAMAGED,
                Equipment.working_hours > WORKING_HOURS_LIMIT,
            ),
        )
    )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    alerts = []

    for row in rows:
        issues = []
        recommendation = None

        if row.condition == EquipmentCondition.DAMAGED:
            issues.append({"type": "DAMAGED", "severity": "CRITICAL"})
            recommendation = "Stop usage and repair immediately"

        # ⚠ OVERUSED
        if row.working_hours and row.working_hours > WORKING_HOURS_LIMIT:
            if row.working_hours > WORKING_HOURS_LIMIT * 1.5:
                severity = "CRITICAL"
            else:
                severity = "HIGH"

            issues.append(
                {
                    "type": "OVERUSED",
                    "severity": severity,
                    "current_hours": float(row.working_hours),
                    "limit": WORKING_HOURS_LIMIT,
                }
            )

            if not recommendation:
                recommendation = "Schedule maintenance soon"

        alerts.append(
            {
                "equipment_id": row.id,
                "equipment_code": row.equipment_code,
                "equipment_name": row.equipment_name,
                "project_id": row.project_id,
                "issues": issues,
                "recommendation": recommendation,
            }
        )

    return alerts


# ================== AUDIT LOGS ==================


@router.get("/{equipment_id}/logs")
async def get_audit_logs(
    equipment_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    action: Optional[str] = None,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await get_active_equipment_or_404(db, equipment_id)

    base_query = select(EquipmentAuditLog).where(
        EquipmentAuditLog.equipment_id == equipment_id
    )

    #  filter
    if action:
        base_query = base_query.where(EquipmentAuditLog.action == action)

    #  total count
    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = await db.scalar(count_stmt)

    #  data query
    stmt = (
        base_query.order_by(EquipmentAuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    items = [
        EquipmentAuditLogOut(
            id=row.id,
            equipment_id=row.equipment_id,
            action=row.action,
            old_values=safe_parse(row.old_values) if row.old_values else None,
            new_values=safe_parse(row.new_values) if row.new_values else None,
            user_id=row.user_id,
            ip_address=row.ip_address,
            created_at=row.created_at,
        )
        for row in rows
    ]

    return {
        "items": items,
        "meta": {
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        },
    }


# ======================== REPORTS ========================

@router.get("/reports/pdf")
async def equipment_full_pdf_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):
    try:
        import io
        import traceback
        from datetime import datetime

        from fastapi import HTTPException
        from fastapi.responses import StreamingResponse

        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
            HRFlowable,
        )

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER

        # ================= COLORS =================
        DARK_NAVY = colors.HexColor("#1A2B4A")
        ORANGE = colors.HexColor("#F5A623")
        TABLE_HDR = colors.HexColor("#1A2B4A")
        ROW_ALT = colors.HexColor("#F5F5F5")
        ROW_WHITE = colors.white
        BORDER_CLR = colors.HexColor("#DDDDDD")
        TEXT_DARK = colors.HexColor("#222222")
        TEXT_LIGHT = colors.white
        LABEL_GREY = colors.HexColor("#555555")
        GREEN_HDR = colors.HexColor("#2E7D32")
        RED_HDR = colors.HexColor("#C62828")
        ORANGE_LIGHT = colors.HexColor("#FFF3E0")
        RED_LIGHT = colors.HexColor("#FFEBEE")

        # ================= PDF SETUP =================
        buffer = io.BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            leftMargin=36,
            rightMargin=36,
            topMargin=90,
            bottomMargin=40,
        )

        # ================= SAFE FONTS =================
        FONT = "Helvetica"
        FONT_BOLD = "Helvetica-Bold"

        # ================= STYLES =================
        styles = getSampleStyleSheet()

        def S(name, **kw):
            return ParagraphStyle(name, **kw)

        title_style = S(
            "Title",
            fontSize=16,
            textColor=TEXT_DARK,
            alignment=TA_CENTER,
            fontName=FONT_BOLD,
            spaceAfter=2,
        )

        sub_style = S(
            "Sub",
            fontSize=9,
            textColor=LABEL_GREY,
            alignment=TA_CENTER,
            spaceAfter=4,
        )

        info_label = S(
            "InfoLabel",
            fontSize=8,
            textColor=LABEL_GREY,
        )

        bold_style = S(
            "BoldStyle",
            fontSize=9,
            textColor=TEXT_DARK,
            fontName=FONT_BOLD,
        )

        # ================= FETCH DATA =================
        eq_stmt = select(Equipment).where(Equipment.is_deleted == False)
        eq_result = await db.execute(eq_stmt)
        equipments = eq_result.scalars().all() or []

        m_result = await db.execute(select(EquipmentMaintenance))
        maint = m_result.scalars().all() or []

        r_result = await db.execute(select(EquipmentRental))
        rentals = r_result.scalars().all() or []

        # ================= SAFE TOTALS =================
        total_maint_cost = sum(float(m.cost or 0) for m in maint)
        total_rental_cost = sum(float(r.rental_cost or 0) for r in rentals)
        grand_total = total_maint_cost + total_rental_cost

        # ================= DATES =================
        now_str = datetime.now().strftime("%d %b %Y")
        now_ts = datetime.now().strftime("%d/%m/%Y %I:%M %p")

        # ================= STORY =================
        story = []

        story.append(
            Paragraph("Equipment Management Report", title_style)
        )

        story.append(Spacer(1, 6))

        story.append(
            Paragraph(
                f"Pune, Maharashtra | {now_str}",
                sub_style,
            )
        )

        story.append(
            HRFlowable(
                width="100%",
                thickness=2,
                color=ORANGE,
                spaceAfter=12,
            )
        )

        # ================= SUMMARY =================
        def safe_condition(e):
            try:
                return str(getattr(e.condition, "value", e.condition or "")).upper()
            except Exception:
                return ""

        good_count = sum(
            1 for e in equipments
            if safe_condition(e) == "GOOD"
        )

        summary_data = [
            [
                Paragraph("Total Equipment", info_label),
                Paragraph("Good Condition", info_label),
                Paragraph("Maintenance Cost", info_label),
                Paragraph("Rental Cost", info_label),
                Paragraph("Grand Total", info_label),
            ],
            [
                Paragraph(str(len(equipments)), bold_style),
                Paragraph(str(good_count), bold_style),
                Paragraph(f"Rs. {total_maint_cost:,.0f}", bold_style),
                Paragraph(f"Rs. {total_rental_cost:,.0f}", bold_style),
                Paragraph(f"Rs. {grand_total:,.0f}", bold_style),
            ],
        ]

        sum_table = Table(
            summary_data,
            colWidths=[95, 95, 110, 110, 110],
        )

        sum_table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, BORDER_CLR),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_CLR),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("FONTNAME", (0, 0), (-1, -1), FONT_BOLD),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )

        story.append(sum_table)
        story.append(Spacer(1, 20))

        # ================= EQUIPMENT TABLE =================
        eq_data = [
            ["#", "ID", "Code", "Name", "Condition", "Project"]
        ]

        for i, e in enumerate(equipments, 1):

            cond = safe_condition(e)

            eq_data.append(
                [
                    str(i),
                    str(e.id or "-"),
                    str(e.equipment_code or "-"),
                    str(e.equipment_name or "-"),
                    cond or "-",
                    str(e.project_id or "-"),
                ]
            )

        eq_table = Table(
            eq_data,
            colWidths=[25, 35, 95, 160, 80, 65],
            repeatRows=1,
        )

        eq_style = TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), TABLE_HDR),
                ("TEXTCOLOR", (0, 0), (-1, 0), TEXT_LIGHT),
                ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER_CLR),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ROW_WHITE, ROW_ALT]),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ]
        )

        eq_table.setStyle(eq_style)

        story.append(eq_table)
        story.append(Spacer(1, 20))

        # ================= MAINTENANCE TABLE =================
        m_data = [["Equip ID", "Description", "Date", "Cost"]]

        for m in maint:
            m_data.append(
                [
                    str(m.equipment_id or "-"),
                    str(m.description or "-"),
                    str(m.maintenance_date or "-"),
                    f"{float(m.cost or 0):,.2f}",
                ]
            )

        m_table = Table(
            m_data,
            colWidths=[65, 220, 110, 100],
            repeatRows=1,
        )

        m_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), GREEN_HDR),
                    ("TEXTCOLOR", (0, 0), (-1, 0), TEXT_LIGHT),
                    ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_CLR),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ROW_WHITE, ROW_ALT]),
                ]
            )
        )

        story.append(m_table)
        story.append(Spacer(1, 20))

        # ================= RENTAL TABLE =================
        r_data = [["Equip ID", "Client", "Start", "End", "Cost"]]

        for r in rentals:
            r_data.append(
                [
                    str(r.equipment_id or "-"),
                    str(r.client_name or "-"),
                    str(r.start_date or "-"),
                    str(r.end_date or "-"),
                    f"{float(r.rental_cost or 0):,.2f}",
                ]
            )

        r_table = Table(
            r_data,
            colWidths=[65, 180, 80, 80, 95],
            repeatRows=1,
        )

        r_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), RED_HDR),
                    ("TEXTCOLOR", (0, 0), (-1, 0), TEXT_LIGHT),
                    ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                    ("GRID", (0, 0), (-1, -1), 0.5, BORDER_CLR),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ROW_WHITE, ROW_ALT]),
                ]
            )
        )

        story.append(r_table)

        # ================= HEADER FOOTER =================
        def draw_header_footer(canvas, doc):
            canvas.saveState()

            w, h = letter

            canvas.setFillColor(DARK_NAVY)
            canvas.rect(0, h - 52, w, 52, fill=1)

            canvas.setFont(FONT_BOLD, 18)
            canvas.setFillColor(colors.white)
            canvas.drawString(36, h - 30, "INFRA")

            canvas.setFillColor(ORANGE)
            canvas.drawString(98, h - 30, "PILOT")

            canvas.setFont(FONT, 7)
            canvas.setFillColor(colors.HexColor("#CCCCCC"))
            canvas.drawString(
                36,
                h - 44,
                "Construction Equipment Management",
            )

            canvas.setFont(FONT, 8)
            canvas.setFillColor(colors.grey)

            canvas.drawCentredString(
                w / 2,
                10,
                "Generated by Infra Pilot System",
            )

            canvas.drawRightString(
                w - 36,
                10,
                f"Page {doc.page}",
            )

            canvas.restoreState()

        # ================= BUILD PDF =================
        doc.build(
            story,
            onFirstPage=draw_header_footer,
            onLaterPages=draw_header_footer,
        )

        buffer.seek(0)

        filename = (
            f"equipment_report_{datetime.now().strftime('%Y%m%d')}.pdf"
        )

        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            },
        )

    except Exception as e:
        import traceback

        print("PDF REPORT ERROR:", str(e))
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed: {str(e)}",
        )


# ================================excel report==========================

@router.get("/reports/excel")
async def equipment_excel_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):
    try:
        import io
        import traceback
        from datetime import datetime

        from fastapi import HTTPException
        from fastapi.responses import StreamingResponse

        from openpyxl import Workbook
        from openpyxl.styles import (
            Font,
            PatternFill,
            Alignment,
            Border,
            Side,
        )
        from openpyxl.utils import get_column_letter

        # ================= FETCH DATA =================
        stmt = select(Equipment).where(Equipment.is_deleted == False)
        equipments = (await db.execute(stmt)).scalars().all() or []

        m_result = await db.execute(select(EquipmentMaintenance))
        maint = m_result.scalars().all() or []

        r_result = await db.execute(select(EquipmentRental))
        rentals = r_result.scalars().all() or []

        # ================= SAFE TOTALS =================
        total_maint_cost = sum(float(m.cost or 0) for m in maint)
        total_rental_cost = sum(float(r.rental_cost or 0) for r in rentals)
        grand_total = total_maint_cost + total_rental_cost

        now_str = datetime.now().strftime("%d %b %Y %I:%M %p")

        # ================= COLORS =================
        C_NAVY = "1A2B4A"
        C_WHITE = "FFFFFF"
        C_LIGHT = "F5F5F5"
        C_ALT = "EFF3FB"
        C_GREEN_BG = "E8F5E9"
        C_GREEN_FG = "2E7D32"
        C_GREEN_HDR = "2E7D32"
        C_RED_BG = "FFEBEE"
        C_RED_FG = "C62828"
        C_RED_HDR = "C62828"
        C_BORDER = "CCCCCC"
        C_ORANGE = "F5A623"

        CURRENCY_FMT = '"Rs." #,##0.00'
        DEC_FMT = "#,##0.00"

        # ================= STYLE HELPERS =================
        def fill(color):
            return PatternFill("solid", fgColor=color)

        def font(bold=False, color="333333", size=10):
            return Font(
                name="Arial",
                bold=bold,
                color=color,
                size=size,
            )

        def align(h="center", wrap=False):
            return Alignment(
                horizontal=h,
                vertical="center",
                wrap_text=wrap,
            )

        def border():
            s = Side(style="thin", color=C_BORDER)
            return Border(
                left=s,
                right=s,
                top=s,
                bottom=s,
            )

        def safe_condition(e):
            try:
                return str(
                    getattr(e.condition, "value", e.condition or "")
                ).upper()
            except Exception:
                return ""

        # ================= WORKBOOK =================
        wb = Workbook()

        # =========================================================
        # SUMMARY SHEET
        # =========================================================
        ws_sum = wb.active
        ws_sum.title = "Summary"

        ws_sum.merge_cells("A1:F1")
        ws_sum["A1"] = "INFRA PILOT - Equipment Report"

        ws_sum["A1"].font = font(
            bold=True,
            color=C_WHITE,
            size=14,
        )

        ws_sum["A1"].fill = fill(C_NAVY)
        ws_sum["A1"].alignment = align()

        ws_sum.merge_cells("A2:F2")
        ws_sum["A2"] = f"Generated: {now_str}"

        ws_sum["A2"].font = font(
            bold=True,
            color=C_WHITE,
            size=10,
        )

        ws_sum["A2"].fill = fill(C_ORANGE)
        ws_sum["A2"].alignment = align()

        # ================= SUMMARY DATA =================
        good_count = sum(
            1 for e in equipments
            if safe_condition(e) == "GOOD"
        )

        damaged_count = sum(
            1 for e in equipments
            if safe_condition(e) == "DAMAGED"
        )

        summary_headers = [
            "Total Equipment",
            "Good",
            "Damaged",
            "Maintenance Cost",
            "Rental Cost",
            "Grand Total",
        ]

        summary_values = [
            len(equipments),
            good_count,
            damaged_count,
            total_maint_cost,
            total_rental_cost,
            grand_total,
        ]

        for col, header in enumerate(summary_headers, 1):
            cell = ws_sum.cell(row=4, column=col, value=header)

            cell.font = font(
                bold=True,
                color=C_WHITE,
            )

            cell.fill = fill(C_NAVY)
            cell.alignment = align()
            cell.border = border()

        for col, value in enumerate(summary_values, 1):
            cell = ws_sum.cell(row=5, column=col, value=value)

            cell.font = font(bold=True)
            cell.alignment = align()
            cell.border = border()

            if col >= 4:
                cell.number_format = CURRENCY_FMT

        # Column widths
        widths = [20, 15, 15, 22, 22, 22]

        for i, w in enumerate(widths, 1):
            ws_sum.column_dimensions[get_column_letter(i)].width = w

        # =========================================================
        # EQUIPMENT SHEET
        # =========================================================
        ws_eq = wb.create_sheet("Equipment")

        headers = [
            "#",
            "ID",
            "Code",
            "Name",
            "Condition",
            "Project",
            "Working Hours",
            "Fuel Used",
            "Rental Cost",
            "Status",
        ]

        for col, header in enumerate(headers, 1):
            cell = ws_eq.cell(row=1, column=col, value=header)

            cell.font = font(
                bold=True,
                color=C_WHITE,
            )

            cell.fill = fill(C_NAVY)
            cell.alignment = align(wrap=True)
            cell.border = border()

        # ================= EQUIPMENT DATA =================
        for i, e in enumerate(equipments, start=2):

            cond = safe_condition(e)

            status = (
                "Maintenance"
                if cond == "DAMAGED"
                else "Active"
            )

            values = [
                i - 1,
                e.id or "-",
                e.equipment_code or "-",
                e.equipment_name or "-",
                cond or "-",
                e.project_id or "-",
                float(e.working_hours or 0),
                float(e.fuel_used or 0),
                float(e.rental_cost or 0),
                status,
            ]

            for col, val in enumerate(values, 1):

                cell = ws_eq.cell(
                    row=i,
                    column=col,
                    value=val,
                )

                cell.font = font()
                cell.alignment = align()
                cell.border = border()

                bg = C_WHITE if i % 2 == 0 else C_ALT
                cell.fill = fill(bg)

                if col in [7, 8]:
                    cell.number_format = DEC_FMT

                if col == 9:
                    cell.number_format = CURRENCY_FMT

                if col == 5:
                    if cond == "GOOD":
                        cell.fill = fill(C_GREEN_BG)
                        cell.font = font(
                            bold=True,
                            color=C_GREEN_FG,
                        )

                    elif cond == "DAMAGED":
                        cell.fill = fill(C_RED_BG)
                        cell.font = font(
                            bold=True,
                            color=C_RED_FG,
                        )

                if col == 10:
                    if status == "Maintenance":
                        cell.fill = fill(C_RED_BG)
                        cell.font = font(
                            bold=True,
                            color=C_RED_FG,
                        )

                    else:
                        cell.fill = fill(C_GREEN_BG)
                        cell.font = font(
                            bold=True,
                            color=C_GREEN_FG,
                        )

        eq_widths = [5, 8, 16, 24, 16, 12, 16, 16, 18, 16]

        for i, w in enumerate(eq_widths, 1):
            ws_eq.column_dimensions[get_column_letter(i)].width = w

        # =========================================================
        # MAINTENANCE SHEET
        # =========================================================
        ws_m = wb.create_sheet("Maintenance")

        m_headers = [
            "#",
            "Equipment ID",
            "Description",
            "Date",
            "Cost",
        ]

        for col, header in enumerate(m_headers, 1):

            cell = ws_m.cell(
                row=1,
                column=col,
                value=header,
            )

            cell.font = font(
                bold=True,
                color=C_WHITE,
            )

            cell.fill = fill(C_GREEN_HDR)
            cell.alignment = align()
            cell.border = border()

        for i, m in enumerate(maint, start=2):

            values = [
                i - 1,
                m.equipment_id or "-",
                m.description or "-",
                str(m.maintenance_date or "-"),
                float(m.cost or 0),
            ]

            for col, val in enumerate(values, 1):

                cell = ws_m.cell(
                    row=i,
                    column=col,
                    value=val,
                )

                cell.font = font()
                cell.alignment = align()
                cell.border = border()

                bg = C_WHITE if i % 2 == 0 else C_ALT
                cell.fill = fill(bg)

                if col == 5:
                    cell.number_format = CURRENCY_FMT

        m_widths = [5, 14, 40, 18, 18]

        for i, w in enumerate(m_widths, 1):
            ws_m.column_dimensions[get_column_letter(i)].width = w

        # =========================================================
        # RENTAL SHEET
        # =========================================================
        ws_r = wb.create_sheet("Rentals")

        r_headers = [
            "#",
            "Equipment ID",
            "Client",
            "Start Date",
            "End Date",
            "Cost",
        ]

        for col, header in enumerate(r_headers, 1):

            cell = ws_r.cell(
                row=1,
                column=col,
                value=header,
            )

            cell.font = font(
                bold=True,
                color=C_WHITE,
            )

            cell.fill = fill(C_RED_HDR)
            cell.alignment = align()
            cell.border = border()

        for i, r in enumerate(rentals, start=2):

            values = [
                i - 1,
                r.equipment_id or "-",
                r.client_name or "-",
                str(r.start_date or "-"),
                str(r.end_date or "-"),
                float(r.rental_cost or 0),
            ]

            for col, val in enumerate(values, 1):

                cell = ws_r.cell(
                    row=i,
                    column=col,
                    value=val,
                )

                cell.font = font()
                cell.alignment = align()
                cell.border = border()

                bg = C_WHITE if i % 2 == 0 else C_ALT
                cell.fill = fill(bg)

                if col == 6:
                    cell.number_format = CURRENCY_FMT

        r_widths = [5, 14, 26, 16, 16, 18]

        for i, w in enumerate(r_widths, 1):
            ws_r.column_dimensions[get_column_letter(i)].width = w

        # =========================================================
        # SAVE FILE
        # =========================================================
        output = io.BytesIO()

        wb.save(output)

        output.seek(0)

        filename = (
            f"equipment_report_{datetime.now().strftime('%Y%m%d')}.xlsx"
        )

        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": (
                    f"attachment; filename={filename}"
                )
            },
        )

    except Exception as e:
        import traceback

        print("EXCEL REPORT ERROR:", str(e))
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=f"Excel generation failed: {str(e)}",
        )