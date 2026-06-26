from typing import Optional, List
from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import json
from app.models.boq import BOQ

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
from app.core import db
from app.core.dependencies import (
    get_current_active_user,
    get_request_redis,
    require_roles,
)

# Internal - DB / Models
from app.db.session import get_db_session
from app.models.equipment import (
    Equipment,
    EquipmentPurchase,
    EquipmentUsage,
    EquipmentMaintenance,
    EquipmentRental,
    EquipmentAuditLog,
)
from app.models.project import Project
from app.models.user import User, UserRole

# Internal - Enums
from app.core.enums import EquipmentCondition, EquipmentStatus
from openpyxl.cell.cell import MergedCell

# Internal - Schemas
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.equipment import (
    DeleteRentalResponse,
    DeleteUsageResponse,
    EquipmentAllocateRequest,
    EquipmentAllocateResponse,
    EquipmentCreate,
    EquipmentDeallocateRequest,
    EquipmentDeallocateResponse,
    EquipmentKPIOut,
    EquipmentMaintenanceUpdate,
    EquipmentPurchaseCreate,
    EquipmentPurchaseOut,
    EquipmentPurchaseUpdate,
    EquipmentPurchaseReportItem,
    EquipmentRentalUpdate,
    EquipmentTransferRequest,
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
    EquipmentUsageUpdate,
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


from datetime import date


def status_from_row(row):
    today = date.today()

    if row.is_completed:
        return "COMPLETED"
    elif row.next_maintenance_date is None:
        return "NO_SCHEDULE"
    elif row.next_maintenance_date < today:
        return "OVERDUE"
    elif row.next_maintenance_date == today:
        return "TODAY"
    return "UPCOMING"


def convert_decimal(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: convert_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_decimal(i) for i in obj]
    return obj


async def recalculate_equipment_status(
    db: AsyncSession,
    equipment: Equipment,
):
    today = date.today()

    # 1. Damaged check
    if equipment.condition == EquipmentCondition.DAMAGED:
        equipment.status = EquipmentStatus.DAMAGED
        return

    # 2. Pending maintenance check
    pending_maintenance = await db.scalar(
        select(
            exists().where(
                EquipmentMaintenance.equipment_id == equipment.id,
                EquipmentMaintenance.is_completed.is_(False),
                EquipmentMaintenance.maintenance_date <= today,
            )
        )
    )

    if pending_maintenance:
        equipment.status = EquipmentStatus.MAINTENANCE
        return

    # 3. Project allocation check
    if equipment.project_id:
        equipment.status = EquipmentStatus.IN_PROJECT
        return

    active_rental = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == equipment.id,
                EquipmentRental.start_date <= today,
                or_(
                    EquipmentRental.end_date.is_(None),
                    EquipmentRental.end_date >= today,
                ),
            )
        )
    )

    if active_rental:
        equipment.status = EquipmentStatus.RENTED
        return

    future_rental = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == equipment.id,
                EquipmentRental.start_date > today,
            )
        )
    )

    if future_rental:
        equipment.status = EquipmentStatus.IDLE
        return

    equipment.status = EquipmentStatus.AVAILABLE


# ============================== EQUIPMENT KPI ========================

MAX_MONTHLY_HOURS = 240


@router.get("/kpi", response_model=EquipmentKPIOut)
async def equipment_kpi(
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):

    total_equipment = await db.scalar(
        select(func.count()).select_from(Equipment).where(Equipment.is_deleted == False)
    )

    available = await db.scalar(
        select(func.count())
        .select_from(Equipment)
        .where(
            Equipment.status == EquipmentStatus.AVAILABLE,
            Equipment.is_deleted == False,
        )
    )

    allocated = await db.scalar(
        select(func.count())
        .select_from(Equipment)
        .where(
            Equipment.status == EquipmentStatus.IN_PROJECT,
            Equipment.is_deleted == False,
        )
    )

    rented = await db.scalar(
        select(func.count())
        .select_from(Equipment)
        .where(
            Equipment.status == EquipmentStatus.RENTED,
            Equipment.is_deleted == False,
        )
    )

    maintenance = await db.scalar(
        select(func.count())
        .select_from(Equipment)
        .where(
            Equipment.status == EquipmentStatus.MAINTENANCE,
            Equipment.is_deleted == False,
        )
    )

    damaged = await db.scalar(
        select(func.count())
        .select_from(Equipment)
        .where(
            Equipment.condition == EquipmentCondition.DAMAGED,
            Equipment.is_deleted == False,
        )
    )

    total_hours = (
        await db.scalar(
            select(func.sum(EquipmentUsage.working_hours))
            .join(
                Equipment,
                Equipment.id == EquipmentUsage.equipment_id,
            )
            .where(
                Equipment.is_deleted == False,
            )
        )
        or 0
    )

    max_possible_hours = (total_equipment or 0) * MAX_MONTHLY_HOURS

    utilization_rate = (
        (float(total_hours) / max_possible_hours) * 100 if max_possible_hours else 0
    )

    rental_revenue = (
        await db.scalar(
            select(func.sum(EquipmentRental.rental_cost))
            .join(
                Equipment,
                Equipment.id == EquipmentRental.equipment_id,
            )
            .where(
                Equipment.is_deleted == False,
            )
        )
        or 0
    )

    maintenance_cost = (
        await db.scalar(
            select(func.sum(EquipmentMaintenance.cost))
            .join(
                Equipment,
                Equipment.id == EquipmentMaintenance.equipment_id,
            )
            .where(
                Equipment.is_deleted == False,
            )
        )
        or 0
    )

    return EquipmentKPIOut(
        total_equipment=total_equipment or 0,
        available=available or 0,
        allocated=allocated or 0,
        rented=rented or 0,
        maintenance=maintenance or 0,
        damaged=damaged or 0,
        utilization_rate=round(utilization_rate, 2),
        total_rental_revenue=float(rental_revenue),
        total_maintenance_cost=float(maintenance_cost),
    )


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


# ========================== COST REPORT ===========================


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
            func.count(EquipmentRental.id).label("rental_count"),
            func.sum(
                (
                    func.coalesce(
                        EquipmentRental.end_date,
                        EquipmentRental.start_date,
                    )
                    - EquipmentRental.start_date
                    + 1
                )
            ).label("total_days"),
        )
        .join(
            Equipment,
            Equipment.id == EquipmentRental.equipment_id,
        )
        .where(
            Equipment.is_deleted == False,
        )
        .group_by(
            EquipmentRental.equipment_id,
            Equipment.equipment_code,
        )
        .order_by(
            func.sum(EquipmentRental.rental_cost).desc(),
        )
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
                total_cost=round(total_cost, 2),
                rental_count=rental_count,
                avg_cost=round(avg_cost, 2),
                total_days=total_days,
                revenue_per_day=round(revenue_per_day, 2),
            )
        )

    return response


# ============================== PURCHASE REPORT ========================


@router.get(
    "/purchase/report",
    response_model=List[EquipmentPurchaseReportItem],
)
async def purchase_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):
    stmt = (
        select(
            EquipmentPurchase.purchase_type,
            EquipmentPurchase.asset_id,
            Equipment.equipment_name.label("asset_name"),
            func.count(EquipmentPurchase.id).label("purchase_count"),
            func.sum(EquipmentPurchase.quantity).label("total_quantity"),
            func.sum(EquipmentPurchase.total_amount).label("total_purchase_amount"),
        )
        .join(
            Equipment,
            Equipment.id == EquipmentPurchase.asset_id,
        )
        .group_by(
            EquipmentPurchase.purchase_type,
            EquipmentPurchase.asset_id,
            Equipment.equipment_name,
        )
        .order_by(func.sum(EquipmentPurchase.total_amount).desc())
    )

    result = await db.execute(stmt)

    return [
        EquipmentPurchaseReportItem(
            purchase_type=row.purchase_type,
            asset_id=row.asset_id,
            asset_name=row.asset_name,
            purchase_count=row.purchase_count,
            total_quantity=row.total_quantity,
            total_purchase_amount=float(row.total_purchase_amount or 0),
        )
        for row in result.all()
    ]


# ===================maintenance_alert=======================


@router.get("/alerts/maintenance", response_model=List[MaintenanceAlertItem])
async def maintenance_alerts(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):

    today = date.today()
    upcoming_date = today + timedelta(days=30)

    # Nearest pending maintenance per equipment
    subq = (
        select(
            EquipmentMaintenance.equipment_id,
            func.min(EquipmentMaintenance.next_maintenance_date).label("next_date"),
        )
        .where(
            EquipmentMaintenance.next_maintenance_date.isnot(None),
            EquipmentMaintenance.is_completed == False,
        )
        .group_by(EquipmentMaintenance.equipment_id)
        .subquery()
    )

    stmt = (
        select(EquipmentMaintenance, Equipment)
        .join(
            subq,
            and_(
                EquipmentMaintenance.equipment_id == subq.c.equipment_id,
                EquipmentMaintenance.next_maintenance_date == subq.c.next_date,
            ),
        )
        .join(
            Equipment,
            Equipment.id == EquipmentMaintenance.equipment_id,
        )
        .where(
            and_(
                EquipmentMaintenance.next_maintenance_date.isnot(None),
                EquipmentMaintenance.is_completed == False,
                Equipment.is_deleted == False,
                # Show all overdue and upcoming maintenance within next 30 days
                EquipmentMaintenance.next_maintenance_date <= upcoming_date,
            )
        )
        .order_by(EquipmentMaintenance.next_maintenance_date.asc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    alerts = []

    for maintenance, equipment in rows:

        days_until = (maintenance.next_maintenance_date - today).days

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

    # Get all active equipments
    equipments = (
        (await db.execute(select(Equipment).where(Equipment.is_deleted == False)))
        .scalars()
        .all()
    )

    # Active rentals
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

    response = []

    for eq in equipments:

        if eq.condition == EquipmentCondition.DAMAGED:
            status = "DAMAGED"

        elif eq.status == EquipmentStatus.MAINTENANCE:
            status = "MAINTENANCE"

        elif eq.id in rented_ids:
            status = "RENTED"

        elif eq.project_id is not None:
            status = "ALLOCATED"

        else:
            status = "AVAILABLE"

        response.append(
            AvailabilityReportItem(
                equipment_id=eq.id,
                equipment_code=eq.equipment_code,
                equipment_name=eq.equipment_name,
                is_available=(status == "AVAILABLE"),
                project_id=eq.project_id,
            )
        )

    return response


# ========== ALLOCATION ===========
@router.post(
    "/allocate",
    response_model=EquipmentAllocateResponse,
)
async def allocate_equipment(
    payload: EquipmentAllocateRequest,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    today = date.today()

    project = await db.get(
        Project,
        payload.project_id,
    )

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    # Prevent allocation to completed project
    if project.end_date and project.end_date < today:
        raise HTTPException(
            status_code=400,
            detail="Cannot allocate equipment to completed project",
        )

    allocated_ids = []
    failed = []

    for equipment_id in payload.equipment_ids:

        obj = await db.scalar(
            select(Equipment).where(
                Equipment.id == equipment_id,
                Equipment.is_deleted == False,
            )
        )

        if not obj:
            failed.append(
                {
                    "equipment_id": equipment_id,
                    "reason": "Equipment not found",
                }
            )
            continue

        # ================= DAMAGED CHECK =================

        if obj.condition == EquipmentCondition.DAMAGED:
            failed.append(
                {
                    "equipment_id": equipment_id,
                    "reason": "Damaged equipment",
                }
            )
            continue

        # ================= RENTAL CHECK =================

        rental_exists = await db.scalar(
            select(
                exists().where(
                    EquipmentRental.equipment_id == equipment_id,
                    or_(
                        # Active rental
                        and_(
                            EquipmentRental.start_date <= today,
                            or_(
                                EquipmentRental.end_date.is_(None),
                                EquipmentRental.end_date >= today,
                            ),
                        ),
                        # Future rental
                        EquipmentRental.start_date > today,
                    ),
                )
            )
        )

        if rental_exists:
            failed.append(
                {
                    "equipment_id": equipment_id,
                    "reason": "Equipment rented or reserved",
                }
            )
            continue

        # ================= MAINTENANCE CHECK =================

        maintenance_exists = await db.scalar(
            select(
                exists().where(
                    EquipmentMaintenance.equipment_id == equipment_id,
                    EquipmentMaintenance.maintenance_date >= today,
                )
            )
        )

        if maintenance_exists:
            failed.append(
                {
                    "equipment_id": equipment_id,
                    "reason": "Maintenance scheduled",
                }
            )
            continue

        # ================= SAME PROJECT =================

        if obj.project_id == payload.project_id:
            failed.append(
                {
                    "equipment_id": equipment_id,
                    "reason": "Already allocated to same project",
                }
            )
            continue

        # ================= EXISTING PROJECT =================

        if obj.project_id is not None:

            old_project = await db.get(
                Project,
                obj.project_id,
            )

            if old_project and old_project.end_date and old_project.end_date < today:
                old_project_id = obj.project_id

                obj.project_id = None
                obj.status = EquipmentStatus.AVAILABLE

                await create_audit_log(
                    db=db,
                    equipment_id=obj.id,
                    action="AUTO_DEALLOCATE",
                    old_values={
                        "project_id": old_project_id,
                        "status": EquipmentStatus.IN_PROJECT.value,
                    },
                    new_values={
                        "project_id": None,
                        "status": EquipmentStatus.AVAILABLE.value,
                    },
                    user_id=current_user.id,
                    request=request,
                )

                await db.flush()

            else:
                failed.append(
                    {
                        "equipment_id": equipment_id,
                        "reason": "Already allocated",
                    }
                )
                continue

        # ================= ALLOCATE =================

        old_values = {
            "project_id": obj.project_id,
            "status": obj.status.value if obj.status else None,
        }

        obj.project_id = payload.project_id
        obj.status = EquipmentStatus.IN_PROJECT

        await create_audit_log(
            db=db,
            equipment_id=obj.id,
            action="ALLOCATE",
            old_values=old_values,
            new_values={
                "project_id": payload.project_id,
                "status": EquipmentStatus.IN_PROJECT.value,
            },
            user_id=current_user.id,
            request=request,
        )

        allocated_ids.append(obj.id)

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return EquipmentAllocateResponse(
        equipment_ids=payload.equipment_ids,
        project_id=payload.project_id,
        success_count=len(allocated_ids),
        failed_count=len(failed),
        allocated_ids=allocated_ids,
        failed=failed,
    )


# ================== DEALLOCATE ==================


@router.put(
    "/deallocate",
    response_model=EquipmentDeallocateResponse,
)
async def deallocate_equipment(
    payload: EquipmentDeallocateRequest,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    deallocated_ids = []
    failed = []

    today = date.today()

    for equipment_id in payload.equipment_ids:

        obj = await db.scalar(
            select(Equipment).where(
                Equipment.id == equipment_id,
                Equipment.is_deleted == False,
            )
        )

        if not obj:
            failed.append(
                {
                    "equipment_id": equipment_id,
                    "reason": "Equipment not found",
                }
            )
            continue

        if obj.project_id is None:
            failed.append(
                {
                    "equipment_id": equipment_id,
                    "reason": "Equipment not allocated",
                }
            )
            continue

        # NEW VALIDATION
        if obj.project_id != payload.project_id:
            failed.append(
                {
                    "equipment_id": equipment_id,
                    "reason": "Equipment not allocated to given project",
                }
            )
            continue

        old_values = {
            "project_id": obj.project_id,
            "status": obj.status.value,
        }

        obj.project_id = None

        future_rental = await db.scalar(
            select(
                exists().where(
                    EquipmentRental.equipment_id == equipment_id,
                    EquipmentRental.start_date > today,
                )
            )
        )

        if future_rental:
            obj.status = EquipmentStatus.IDLE
        else:
            obj.status = EquipmentStatus.AVAILABLE

        await create_audit_log(
            db=db,
            equipment_id=obj.id,
            action="DEALLOCATE",
            old_values=old_values,
            new_values={
                "project_id": None,
                "status": obj.status.value,
            },
            user_id=current_user.id,
            request=request,
        )

        deallocated_ids.append(obj.id)

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return EquipmentDeallocateResponse(
        project_id=payload.project_id,
        success_count=len(deallocated_ids),
        failed_count=len(failed),
        deallocated_ids=deallocated_ids,
        failed=failed,
    )


# ================== GET ALLOCATION STATUS ==================


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


# =========== EQUIPMENT CRUD ====================


@router.post(
    "",
    response_model=EquipmentOut,
    status_code=status.HTTP_201_CREATED,
)
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
        raise HTTPException(
            status_code=400,
            detail="Equipment code already exists",
        )

    # Validate project if provided
    if payload.project_id:
        project = await db.get(
            Project,
            payload.project_id,
        )

        if not project:
            raise HTTPException(
                status_code=404,
                detail="Project not found",
            )

    obj = Equipment(**payload.model_dump())

    db.add(obj)

    await db.flush()

    # Auto set status based on project/rental/condition
    await recalculate_equipment_status(
        db,
        obj,
    )

    await create_audit_log(
        db=db,
        equipment_id=obj.id,
        action="CREATE",
        new_values=jsonable_encoder(payload.model_dump()),
        user_id=current_user.id,
    )

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    await db.refresh(obj)

    return EquipmentOut.model_validate(obj)


# ================== LIST EQUIPMENT ==================


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

    cache_key = (
        f"equipment_list:{version}:{limit}:{offset}:"
        f"{search or ''}:{project_id}:{condition or ''}"
    )

    cached = await cache_get_json(redis, cache_key)

    if cached:
        return PaginatedResponse[EquipmentOut](**cached)

    query = select(Equipment).where(Equipment.is_deleted.is_(False))

    count_query = select(func.count(Equipment.id)).where(
        Equipment.is_deleted.is_(False)
    )

    # ================= SEARCH =================

    if search:
        query = query.where(Equipment.equipment_name.ilike(f"%{search}%"))

        count_query = count_query.where(Equipment.equipment_name.ilike(f"%{search}%"))

    # ================= PROJECT FILTER =================

    if project_id:
        query = query.where(Equipment.project_id == project_id)

        count_query = count_query.where(Equipment.project_id == project_id)

    # ================= CONDITION FILTER =================

    if condition:

        try:
            enum_condition = EquipmentCondition(condition.upper())

        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid condition '{condition}'. "
                    f"Allowed values: "
                    f"{', '.join([c.value for c in EquipmentCondition])}"
                ),
            )

        query = query.where(Equipment.condition == enum_condition)

        count_query = count_query.where(Equipment.condition == enum_condition)

    # ================= PAGINATION =================

    query = query.order_by(Equipment.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)

    items = [EquipmentOut.model_validate(row[0]) for row in result.all()]

    total = await db.scalar(count_query)

    response = PaginatedResponse[EquipmentOut](
        items=[item.model_dump() for item in items],
        meta=PaginationMeta(
            total=total or 0,
            limit=limit,
            offset=offset,
        ),
    ).model_dump()

    await cache_set_json(
        redis,
        cache_key,
        response,
    )

    return PaginatedResponse[EquipmentOut].model_validate(response)


# ================== SOFT DELETE ==================


@router.delete("/{equipment_id}", status_code=204)
async def soft_delete_equipment(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await get_active_equipment_or_404(
        db,
        equipment_id,
    )

    today = date.today()

    # ================= PROJECT VALIDATION =================

    if obj.project_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete allocated equipment",
        )

    # ================= MAINTENANCE VALIDATION =================

    if obj.status == EquipmentStatus.MAINTENANCE:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete equipment under maintenance",
        )

    # ================= RENTAL VALIDATION =================

    rental_exists = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == equipment_id,
                or_(
                    # Future rental
                    EquipmentRental.start_date > today,
                    # Active rental
                    and_(
                        EquipmentRental.start_date <= today,
                        or_(
                            EquipmentRental.end_date.is_(None),
                            EquipmentRental.end_date >= today,
                        ),
                    ),
                ),
            )
        )
    )

    if rental_exists:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete rented or reserved equipment",
        )

    # ================= AUDIT OLD VALUES =================

    old_values = serialize(
        {
            "is_deleted": obj.is_deleted,
            "deleted_at": obj.deleted_at,
            "deleted_by": obj.deleted_by,
        }
    )

    # ================= SOFT DELETE =================

    obj.is_deleted = True
    obj.deleted_at = date.today()
    obj.deleted_by = current_user.id

    # ================= AUDIT NEW VALUES =================

    new_values = serialize(
        {
            "is_deleted": obj.is_deleted,
            "deleted_at": obj.deleted_at,
            "deleted_by": obj.deleted_by,
        }
    )

    await create_audit_log(
        db=db,
        equipment_id=obj.id,
        action="SOFT_DELETE",
        old_values=old_values,
        new_values=new_values,
        user_id=current_user.id,
    )

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )


# ========================= CREATE USAGE===========================


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
    redis=Depends(get_request_redis),
):

    equipment = await get_active_equipment_or_404(
        db,
        equipment_id,
    )

    today = date.today()

    # ================= BASIC VALIDATIONS =================

    if equipment.project_id is None:
        raise HTTPException(
            status_code=400,
            detail="Equipment is not allocated to any project",
        )

    if payload.working_hours <= 0 and payload.fuel_used <= 0:
        raise HTTPException(
            status_code=400,
            detail="Usage cannot be zero",
        )

    if payload.usage_date > today:
        raise HTTPException(
            status_code=400,
            detail="Usage date cannot be in future",
        )

    if equipment.condition == EquipmentCondition.DAMAGED:
        raise HTTPException(
            status_code=400,
            detail="Equipment is damaged and cannot be used",
        )

    # ================= BOQ VALIDATION =================

    boq_item = None

    if payload.boq_item_id:

        boq_item = await db.get(
            BOQ,
            payload.boq_item_id,
        )

        if not boq_item:
            raise HTTPException(
                status_code=404,
                detail="BOQ item not found",
            )

        if boq_item.project_id != equipment.project_id:
            raise HTTPException(
                status_code=400,
                detail="BOQ item does not belong to equipment project",
            )

    # ================= DUPLICATE USAGE =================

    usage_exists = await db.scalar(
        select(
            exists().where(
                EquipmentUsage.equipment_id == equipment_id,
                EquipmentUsage.usage_date == payload.usage_date,
            )
        )
    )

    if usage_exists:
        raise HTTPException(
            status_code=400,
            detail="Usage already exists for this date",
        )

    # ================= RENTAL VALIDATION =================

    rental_active = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == equipment_id,
                EquipmentRental.start_date <= payload.usage_date,
                or_(
                    EquipmentRental.end_date.is_(None),
                    EquipmentRental.end_date >= payload.usage_date,
                ),
            )
        )
    )

    if rental_active:
        raise HTTPException(
            status_code=400,
            detail="Equipment is rented. Cannot log usage",
        )

    # ================= MAINTENANCE VALIDATION =================

    maintenance_active = await db.scalar(
        select(
            exists().where(
                EquipmentMaintenance.equipment_id == equipment_id,
                EquipmentMaintenance.maintenance_date == payload.usage_date,
                EquipmentMaintenance.is_completed.is_(False),
            )
        )
    )

    if maintenance_active:
        raise HTTPException(
            status_code=400,
            detail="Equipment is under maintenance",
        )

    # ================= CREATE USAGE =================

    obj = EquipmentUsage(
        equipment_id=equipment_id,
        **payload.model_dump(),
    )

    db.add(obj)

    old_hours = equipment.working_hours or Decimal("0")
    old_fuel = equipment.fuel_used or Decimal("0")

    equipment.working_hours = old_hours + payload.working_hours

    equipment.fuel_used = old_fuel + payload.fuel_used

    equipment.status = EquipmentStatus.IN_PROJECT

    # ================= BOQ ACTUAL COST UPDATE =================

    usage_cost = Decimal("0")

    if boq_item:

        usage_cost = payload.working_hours * (equipment.rental_cost or Decimal("0"))

        boq_item.actual_cost = (boq_item.actual_cost or Decimal("0")) + usage_cost

    await db.flush()

    # ================= AUDIT LOG =================

    await create_audit_log(
        db=db,
        equipment_id=equipment.id,
        action="USAGE_CREATE",
        old_values={
            "working_hours": float(old_hours),
            "fuel_used": float(old_fuel),
        },
        new_values={
            "working_hours": float(equipment.working_hours),
            "fuel_used": float(equipment.fuel_used),
            "usage_hours_added": float(payload.working_hours),
            "fuel_added": float(payload.fuel_used),
            "usage_date": str(payload.usage_date),
            "boq_item_id": payload.boq_item_id,
            "usage_cost": float(usage_cost),
        },
        user_id=current_user.id,
    )

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    await db.refresh(obj)

    return EquipmentUsageOut.model_validate(obj)


# ========================= LIST USAGE===========================


@router.get(
    "/{equipment_id}/usage",
    response_model=List[EquipmentUsageOut],
)
async def list_usage(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    await get_active_equipment_or_404(
        db,
        equipment_id,
    )

    stmt = (
        select(EquipmentUsage)
        .where(
            EquipmentUsage.equipment_id == equipment_id,
        )
        .order_by(
            EquipmentUsage.usage_date.desc(),
        )
    )

    result = await db.execute(stmt)

    usages = result.scalars().all()

    return [
        EquipmentUsageOut(
            id=row.id,
            boq_item_id=row.boq_item_id,
            equipment_id=row.equipment_id,
            working_hours=float(row.working_hours or 0),
            fuel_used=float(row.fuel_used or 0),
            usage_date=row.usage_date,
            notes=row.notes,
            created_at=row.created_at,
        )
        for row in usages
    ]


# ======================== UPDATE USAGE===========================


@router.put(
    "/usage/{usage_id}",
    response_model=EquipmentUsageOut,
)
async def update_usage(
    usage_id: int,
    payload: EquipmentUsageUpdate,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    usage = await db.get(
        EquipmentUsage,
        usage_id,
    )

    if not usage:
        raise HTTPException(
            status_code=404,
            detail="Usage record not found",
        )

    equipment = await get_active_equipment_or_404(
        db,
        usage.equipment_id,
    )

    # ================= OLD VALUES =================

    old_usage_hours = usage.working_hours or Decimal("0")
    old_usage_fuel = usage.fuel_used or Decimal("0")

    old_total_hours = equipment.working_hours or Decimal("0")
    old_total_fuel = equipment.fuel_used or Decimal("0")

    old_boq_item_id = usage.boq_item_id

    update_data = payload.model_dump(exclude_unset=True)

    # ================= DUPLICATE DATE CHECK =================

    if "usage_date" in update_data and update_data["usage_date"] != usage.usage_date:
        duplicate = await db.scalar(
            select(
                exists().where(
                    EquipmentUsage.equipment_id == usage.equipment_id,
                    EquipmentUsage.usage_date == update_data["usage_date"],
                    EquipmentUsage.id != usage_id,
                )
            )
        )

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Usage already exists for this date",
            )

    # ================= APPLY UPDATE =================

    for field, value in update_data.items():
        setattr(usage, field, value)

    # ================= EQUIPMENT TOTALS UPDATE =================

    equipment.working_hours = max(
        Decimal("0"),
        old_total_hours - old_usage_hours + (usage.working_hours or Decimal("0")),
    )

    equipment.fuel_used = max(
        Decimal("0"),
        old_total_fuel - old_usage_fuel + (usage.fuel_used or Decimal("0")),
    )

    # ================= BOQ COST RECALCULATION =================

    rental_rate = equipment.rental_cost or Decimal("0")

    old_usage_cost = old_usage_hours * rental_rate
    new_usage_cost = (usage.working_hours or Decimal("0")) * rental_rate

    new_boq_item_id = usage.boq_item_id

    # ================= BOQ CHANGED =================

    if old_boq_item_id != new_boq_item_id:

        if old_boq_item_id:

            old_boq = await db.get(
                BOQ,
                old_boq_item_id,
            )

            if old_boq:
                old_boq.actual_cost = max(
                    Decimal("0"),
                    (old_boq.actual_cost or Decimal("0")) - old_usage_cost,
                )

        if new_boq_item_id:

            new_boq = await db.get(
                BOQ,
                new_boq_item_id,
            )

            if not new_boq:
                raise HTTPException(
                    status_code=404,
                    detail="BOQ item not found",
                )

            if equipment.project_id != new_boq.project_id:
                raise HTTPException(
                    status_code=400,
                    detail="BOQ item does not belong to equipment project",
                )

            new_boq.actual_cost = (new_boq.actual_cost or Decimal("0")) + new_usage_cost

    # ================= SAME BOQ =================

    elif new_boq_item_id:

        boq_item = await db.get(
            BOQ,
            new_boq_item_id,
        )

        if boq_item:
            boq_item.actual_cost = (
                (boq_item.actual_cost or Decimal("0")) - old_usage_cost + new_usage_cost
            )

    # ================= AUDIT LOG =================

    await create_audit_log(
        db=db,
        equipment_id=equipment.id,
        action="USAGE_UPDATE",
        old_values={
            "working_hours": float(old_usage_hours),
            "fuel_used": float(old_usage_fuel),
            "boq_item_id": old_boq_item_id,
            "usage_cost": float(old_usage_cost),
        },
        new_values={
            "working_hours": float(usage.working_hours or 0),
            "fuel_used": float(usage.fuel_used or 0),
            "usage_date": str(usage.usage_date),
            "boq_item_id": usage.boq_item_id,
            "usage_cost": float(new_usage_cost),
        },
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    await db.refresh(usage)

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return EquipmentUsageOut.model_validate(usage)


# ========================= DELETE USAGE===========================


@router.delete(
    "/usage/{usage_id}",
    response_model=DeleteUsageResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_usage(
    usage_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    usage = await db.get(
        EquipmentUsage,
        usage_id,
    )

    if not usage:
        raise HTTPException(
            status_code=404,
            detail="Usage record not found",
        )

    equipment = await get_active_equipment_or_404(
        db,
        usage.equipment_id,
    )

    # ================= BOQ COST ROLLBACK =================

    usage_cost = Decimal("0")

    if usage.boq_item_id:

        boq_item = await db.get(
            BOQ,
            usage.boq_item_id,
        )

        if boq_item:

            usage_cost = usage.working_hours * (equipment.rental_cost or Decimal("0"))

            boq_item.actual_cost = max(
                Decimal("0"),
                (boq_item.actual_cost or Decimal("0")) - usage_cost,
            )

    # ================= EQUIPMENT TOTALS UPDATE =================

    equipment.working_hours = max(
        Decimal("0"),
        (equipment.working_hours or Decimal("0"))
        - (usage.working_hours or Decimal("0")),
    )

    equipment.fuel_used = max(
        Decimal("0"),
        (equipment.fuel_used or Decimal("0")) - (usage.fuel_used or Decimal("0")),
    )

    # ================= AUDIT LOG =================

    await create_audit_log(
        db=db,
        equipment_id=equipment.id,
        action="USAGE_DELETE",
        old_values={
            "usage_id": usage.id,
            "boq_item_id": usage.boq_item_id,
            "working_hours": float(usage.working_hours or 0),
            "fuel_used": float(usage.fuel_used or 0),
            "usage_date": str(usage.usage_date),
            "usage_cost": float(usage_cost),
        },
        new_values={
            "boq_cost_rolled_back": float(usage_cost),
        },
        user_id=current_user.id,
        request=request,
    )

    # ================= DELETE USAGE =================

    await db.delete(usage)

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return DeleteUsageResponse(
        message="Usage deleted successfully",
        usage_id=usage_id,
        equipment_id=equipment.id,
    )


# ==============CREATE MAINTENANCE =============


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
    redis=Depends(get_request_redis),
):
    equipment = await get_active_equipment_or_404(
        db,
        equipment_id,
    )

    today = date.today()

    # ================= DATE VALIDATION =================

    if (
        payload.next_maintenance_date
        and payload.next_maintenance_date <= payload.maintenance_date
    ):
        raise HTTPException(
            status_code=400,
            detail="Next maintenance date must be after maintenance date",
        )

    # ================= PROJECT CHECK =================

    if equipment.project_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Equipment is currently allocated to a project",
        )

    # ================= RENTAL VALIDATION =================

    rental_exists = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == equipment_id,
                EquipmentRental.start_date <= payload.maintenance_date,
                or_(
                    EquipmentRental.end_date.is_(None),
                    EquipmentRental.end_date >= payload.maintenance_date,
                ),
            )
        )
    )

    if rental_exists:
        raise HTTPException(
            status_code=400,
            detail="Equipment is currently rented during maintenance date",
        )

    # ================= DUPLICATE MAINTENANCE =================

    maintenance_exists = await db.scalar(
        select(
            exists().where(
                EquipmentMaintenance.equipment_id == equipment_id,
                EquipmentMaintenance.maintenance_date == payload.maintenance_date,
            )
        )
    )

    if maintenance_exists:
        raise HTTPException(
            status_code=400,
            detail="Maintenance already exists for this date",
        )

    # ================= BOQ VALIDATION =================

    boq_item = None

    if payload.boq_item_id:

        boq_item = await db.get(
            BOQ,
            payload.boq_item_id,
        )

        if not boq_item:
            raise HTTPException(
                status_code=404,
                detail="BOQ item not found",
            )

        if boq_item.project_id != payload.project_id:
            raise HTTPException(
                status_code=400,
                detail="BOQ item does not belong to selected project",
            )

    # ================= CREATE MAINTENANCE =================

    old_status = equipment.status

    obj = EquipmentMaintenance(
        **payload.model_dump(),
        equipment_id=equipment_id,
    )

    db.add(obj)

    await db.flush()

    # ================= BOQ ACTUAL COST UPDATE =================

    maintenance_cost = Decimal(str(payload.cost or 0))

    if boq_item and maintenance_cost > 0:

        boq_item.actual_cost = (boq_item.actual_cost or Decimal("0")) + maintenance_cost

    # ================= STATUS RECALCULATION =================

    await recalculate_equipment_status(
        db,
        equipment,
    )

    # ================= AUDIT LOG =================

    await create_audit_log(
        db=db,
        equipment_id=equipment_id,
        action="MAINTENANCE_CREATE",
        old_values={
            "status": old_status.value if old_status else None,
            "project_id": equipment.project_id,
        },
        new_values={
            "maintenance_id": obj.id,
            "description": payload.description,
            "cost": float(payload.cost or 0),
            "maintenance_date": str(payload.maintenance_date),
            "next_maintenance_date": (
                str(payload.next_maintenance_date)
                if payload.next_maintenance_date
                else None
            ),
            "boq_item_id": payload.boq_item_id,
            "status": equipment.status.value,
        },
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    await db.refresh(obj)
    await db.refresh(equipment)

    # ================= RESPONSE STATUS =================

    if obj.is_completed:
        status = "COMPLETED"

    elif obj.next_maintenance_date:

        if obj.next_maintenance_date < today:
            status = "OVERDUE"

        elif obj.next_maintenance_date == today:
            status = "TODAY"

        else:
            status = "UPCOMING"

    else:
        status = "NO_SCHEDULE"

    return EquipmentMaintenanceOut(
        id=obj.id,
        project_id=obj.project_id,
        boq_item_id=obj.boq_item_id,
        equipment_id=obj.equipment_id,
        description=obj.description,
        maintenance_date=obj.maintenance_date,
        cost=float(obj.cost or 0),
        next_maintenance_date=obj.next_maintenance_date,
        is_completed=obj.is_completed,
        completed_at=obj.completed_at,
        created_at=obj.created_at,
        status=status,
    )


# ======================== UPDATE MAINTENANCE =========================


@router.put(
    "/maintenance/{maintenance_id}",
    response_model=EquipmentMaintenanceOut,
)
async def update_maintenance(
    maintenance_id: int,
    payload: EquipmentMaintenanceUpdate,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    maintenance = await db.get(
        EquipmentMaintenance,
        maintenance_id,
    )

    if not maintenance:
        raise HTTPException(
            status_code=404,
            detail="Maintenance record not found",
        )

    # ================= SAVE OLD VALUES =================

    old_cost = maintenance.cost or Decimal("0")
    old_boq_id = maintenance.boq_item_id

    update_data = payload.model_dump(exclude_unset=True)

    old_values = {}

    for field, value in update_data.items():
        old_values[field] = getattr(maintenance, field)
        setattr(maintenance, field, value)

    # ================= VALIDATION =================

    if (
        maintenance.next_maintenance_date
        and maintenance.next_maintenance_date <= maintenance.maintenance_date
    ):
        raise HTTPException(
            status_code=400,
            detail="Next maintenance date must be after maintenance date",
        )

    # ================= BOQ RECALCULATION =================

    new_cost = maintenance.cost or Decimal("0")
    new_boq_id = maintenance.boq_item_id

    # BOQ changed
    if old_boq_id != new_boq_id:

        if old_boq_id:

            old_boq = await db.get(
                BOQ,
                old_boq_id,
            )

            if old_boq:
                old_boq.actual_cost = max(
                    Decimal("0"),
                    (old_boq.actual_cost or Decimal("0")) - old_cost,
                )

        if new_boq_id:

            new_boq = await db.get(
                BOQ,
                new_boq_id,
            )

            if not new_boq:
                raise HTTPException(
                    status_code=404,
                    detail="BOQ item not found",
                )

            new_boq.actual_cost = (new_boq.actual_cost or Decimal("0")) + new_cost

    # Same BOQ → adjust cost difference
    elif new_boq_id:

        boq_item = await db.get(
            BOQ,
            new_boq_id,
        )

        if boq_item:
            boq_item.actual_cost = (
                (boq_item.actual_cost or Decimal("0")) - old_cost + new_cost
            )

    # ================= AUDIT =================

    await create_audit_log(
        db=db,
        equipment_id=maintenance.equipment_id,
        action="MAINTENANCE_UPDATE",
        old_values=jsonable_encoder(old_values),
        new_values=jsonable_encoder(update_data),
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    await db.refresh(maintenance)

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    today = date.today()

    if maintenance.is_completed:
        status = "COMPLETED"

    elif maintenance.next_maintenance_date:

        if maintenance.next_maintenance_date < today:
            status = "OVERDUE"

        elif maintenance.next_maintenance_date == today:
            status = "TODAY"

        else:
            status = "UPCOMING"

    else:
        status = "NO_SCHEDULE"

    return EquipmentMaintenanceOut(
        id=maintenance.id,
        project_id=maintenance.project_id,
        boq_item_id=maintenance.boq_item_id,
        equipment_id=maintenance.equipment_id,
        description=maintenance.description,
        maintenance_date=maintenance.maintenance_date,
        cost=float(maintenance.cost or 0),
        next_maintenance_date=maintenance.next_maintenance_date,
        created_at=maintenance.created_at,
        status=status,
        is_completed=maintenance.is_completed,
        completed_at=maintenance.completed_at,
    )


# ===================== COMPLETE MAINTENANCE =====================
from datetime import date


@router.put(
    "/maintenance/{maintenance_id}/complete",
    response_model=EquipmentMaintenanceOut,
)
async def complete_maintenance(
    maintenance_id: int,
    request: Request,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    maintenance = await db.get(
        EquipmentMaintenance,
        maintenance_id,
    )

    if not maintenance:
        raise HTTPException(
            status_code=404,
            detail="Maintenance record not found",
        )

    if maintenance.is_completed:
        raise HTTPException(
            status_code=400,
            detail="Maintenance already completed",
        )

    equipment = await get_active_equipment_or_404(
        db,
        maintenance.equipment_id,
    )

    old_status = equipment.status

    # Mark maintenance completed
    maintenance.is_completed = True
    maintenance.completed_at = datetime.utcnow()

    # Recalculate equipment status
    await recalculate_equipment_status(
        db,
        equipment,
    )

    await create_audit_log(
        db=db,
        equipment_id=equipment.id,
        action="MAINTENANCE_COMPLETE",
        old_values={
            "status": old_status.value if old_status else None,
            "is_completed": False,
        },
        new_values={
            "status": equipment.status.value,
            "is_completed": True,
            "completed_at": str(maintenance.completed_at),
        },
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    await db.refresh(maintenance)
    await db.refresh(equipment)

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return EquipmentMaintenanceOut(
        id=maintenance.id,
        project_id=maintenance.project_id,
        boq_item_id=maintenance.boq_item_id,
        equipment_id=maintenance.equipment_id,
        description=maintenance.description,
        maintenance_date=maintenance.maintenance_date,
        cost=float(maintenance.cost or 0),
        next_maintenance_date=maintenance.next_maintenance_date,
        is_completed=maintenance.is_completed,
        completed_at=maintenance.completed_at,
        created_at=maintenance.created_at,
        status="COMPLETED",
    )


# ======================== DELETE MAINTENANCE =====================


@router.delete(
    "/maintenance/{maintenance_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_maintenance(
    maintenance_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    maintenance = await db.get(
        EquipmentMaintenance,
        maintenance_id,
    )

    if not maintenance:
        raise HTTPException(
            status_code=404,
            detail="Maintenance record not found",
        )

    equipment = await get_active_equipment_or_404(
        db,
        maintenance.equipment_id,
    )

    # ================= BOQ COST ROLLBACK =================

    if maintenance.boq_item_id and maintenance.cost:

        boq_item = await db.get(
            BOQ,
            maintenance.boq_item_id,
        )

        if boq_item:

            boq_item.actual_cost = max(
                Decimal("0"),
                (boq_item.actual_cost or Decimal("0"))
                - (maintenance.cost or Decimal("0")),
            )

    # ================= AUDIT LOG =================

    await create_audit_log(
        db=db,
        equipment_id=equipment.id,
        action="MAINTENANCE_DELETE",
        old_values={
            "maintenance_id": maintenance.id,
            "boq_item_id": maintenance.boq_item_id,
            "description": maintenance.description,
            "maintenance_date": str(maintenance.maintenance_date),
            "cost": float(maintenance.cost or 0),
        },
        user_id=current_user.id,
        request=request,
    )

    # ================= DELETE =================

    await db.delete(maintenance)

    # ================= STATUS RECALCULATE =================

    await recalculate_equipment_status(
        db,
        equipment,
    )

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return {
        "message": "Maintenance deleted successfully",
        "maintenance_id": maintenance_id,
        "equipment_id": equipment.id,
        "boq_cost_rolled_back": float(maintenance.cost or 0),
    }


# ===================== LIST MAINTENANCE HISTORY =====================


@router.get(
    "/{equipment_id}/maintenance",
    response_model=List[EquipmentMaintenanceOut],
)
async def list_maintenance(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    # Validate equipment exists
    await get_active_equipment_or_404(
        db,
        equipment_id,
    )

    stmt = (
        select(EquipmentMaintenance)
        .where(EquipmentMaintenance.equipment_id == equipment_id)
        .order_by(EquipmentMaintenance.maintenance_date.desc())
    )

    result = await db.execute(stmt)

    maintenances = result.scalars().all()

    return [
        EquipmentMaintenanceOut(
            id=row.id,
            project_id=row.project_id,
            boq_item_id=row.boq_item_id,
            equipment_id=row.equipment_id,
            description=row.description,
            maintenance_date=row.maintenance_date,
            cost=float(row.cost or 0),
            next_maintenance_date=row.next_maintenance_date,
            is_completed=row.is_completed,
            completed_at=row.completed_at,
            created_at=row.created_at,
            status=status_from_row(row),
        )
        for row in maintenances
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
    redis=Depends(get_request_redis),
):
    equipment = await get_active_equipment_or_404(
        db,
        equipment_id,
    )

    today = date.today()

    start_date = payload.start_date
    end_date = payload.end_date or payload.start_date

    # ================= DATE VALIDATION =================

    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="End date cannot be before start date",
        )

    # ================= RENTAL COST =================

    if payload.rental_cost <= 0:
        raise HTTPException(
            status_code=400,
            detail="Rental cost must be greater than 0",
        )

    # ================= DAMAGED CHECK =================

    if equipment.condition == EquipmentCondition.DAMAGED:
        raise HTTPException(
            status_code=400,
            detail="Damaged equipment cannot be rented",
        )

    # ================= MAINTENANCE STATUS CHECK =================

    if equipment.status == EquipmentStatus.MAINTENANCE:
        raise HTTPException(
            status_code=400,
            detail="Equipment is under maintenance",
        )

    # ================= MAINTENANCE OVERLAP =================

    maintenance_exists = await db.scalar(
        select(
            exists().where(
                EquipmentMaintenance.equipment_id == equipment_id,
                EquipmentMaintenance.maintenance_date >= start_date,
                EquipmentMaintenance.maintenance_date <= end_date,
                EquipmentMaintenance.is_completed == False,
            )
        )
    )

    if maintenance_exists:
        raise HTTPException(
            status_code=400,
            detail="Equipment maintenance scheduled during rental period",
        )

    # ================= PROJECT ALLOCATION CHECK =================

    if equipment.project_id is not None:

        project = await db.get(
            Project,
            equipment.project_id,
        )

        if project:

            if project.end_date and project.end_date < today:

                old_project_id = equipment.project_id

                equipment.project_id = None
                equipment.status = EquipmentStatus.AVAILABLE

                await create_audit_log(
                    db=db,
                    equipment_id=equipment.id,
                    action="AUTO_DEALLOCATE",
                    old_values={
                        "project_id": old_project_id,
                        "status": EquipmentStatus.IN_PROJECT.value,
                    },
                    new_values={
                        "project_id": None,
                        "status": EquipmentStatus.AVAILABLE.value,
                    },
                    user_id=current_user.id,
                    request=request,
                )

                await db.flush()

            else:
                raise HTTPException(
                    status_code=400,
                    detail="Equipment is currently allocated to an active project",
                )

        else:
            raise HTTPException(
                status_code=400,
                detail="Equipment is allocated to a project",
            )

    # ================= RENTAL OVERLAP CHECK =================

    overlap_exists = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == equipment_id,
                EquipmentRental.start_date <= end_date,
                or_(
                    EquipmentRental.end_date.is_(None),
                    EquipmentRental.end_date >= start_date,
                ),
            )
        )
    )

    if overlap_exists:
        raise HTTPException(
            status_code=400,
            detail="Equipment already rented during this period",
        )

    # ================= CREATE RENTAL =================

    rental = EquipmentRental(
        project_id=payload.project_id,
        boq_item_id=payload.boq_item_id,
        equipment_id=equipment_id,
        start_date=start_date,
        end_date=end_date,
        rental_cost=payload.rental_cost,
        client_name=payload.client_name,
        notes=payload.notes,
    )

    db.add(rental)

    # ================= BOQ VALIDATION & COST UPDATE =================

    boq_item = None

    if payload.boq_item_id:

        boq_item = await db.get(
            BOQ,
            payload.boq_item_id,
        )

        if not boq_item:
            raise HTTPException(
                status_code=404,
                detail="BOQ item not found",
            )

        if payload.project_id and boq_item.project_id != payload.project_id:
            raise HTTPException(
                status_code=400,
                detail="BOQ item does not belong to project",
            )

        boq_item.actual_cost = (
            boq_item.actual_cost or Decimal("0")
        ) + payload.rental_cost

    old_status = equipment.status

    # ================= STATUS UPDATE =================

    if start_date <= today <= end_date:
        equipment.status = EquipmentStatus.RENTED

    elif start_date > today:
        equipment.status = EquipmentStatus.IDLE

    elif equipment.project_id:
        equipment.status = EquipmentStatus.IN_PROJECT

    else:
        equipment.status = EquipmentStatus.AVAILABLE

    await db.flush()

    # ================= AUDIT LOG =================

    await create_audit_log(
        db=db,
        equipment_id=equipment.id,
        action="RENTAL_CREATE",
        old_values={
            "status": old_status.value if old_status else None,
        },
        new_values={
            "start_date": str(start_date),
            "end_date": str(end_date),
            "rental_cost": float(payload.rental_cost),
            "client_name": payload.client_name,
            "status": equipment.status.value,
        },
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    # ================= CACHE VERSION =================

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    await db.refresh(rental)

    # ================= RESPONSE STATUS =================

    if start_date > today:
        rental_status = "UPCOMING"

    elif end_date < today:
        rental_status = "COMPLETED"

    else:
        rental_status = "ACTIVE"

    duration = (end_date - start_date).days + 1

    per_day_cost = float(rental.rental_cost) / duration if duration > 0 else 0

    return EquipmentRentalOut(
        id=rental.id,
        project_id=rental.project_id,
        boq_item_id=rental.boq_item_id,
        equipment_id=rental.equipment_id,
        start_date=rental.start_date,
        end_date=rental.end_date,
        rental_cost=float(rental.rental_cost),
        client_name=rental.client_name,
        notes=rental.notes,
        created_at=rental.created_at,
        status=rental_status,
        duration=duration,
        per_day_cost=round(per_day_cost, 2),
    )


# ========================== RENTAL LIST ===========================


@router.get(
    "/{equipment_id}/rental",
    response_model=List[EquipmentRentalOut],
)
async def list_rental(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    equipment = await get_active_equipment_or_404(
        db,
        equipment_id,
    )

    stmt = (
        select(EquipmentRental)
        .where(
            EquipmentRental.equipment_id == equipment.id,
        )
        .order_by(
            EquipmentRental.start_date.desc(),
            EquipmentRental.created_at.desc(),
        )
    )

    result = await db.execute(stmt)

    rentals = result.scalars().all()

    today = date.today()

    return [
        EquipmentRentalOut(
            id=rental.id,
            project_id=rental.project_id,
            boq_item_id=rental.boq_item_id,
            equipment_id=rental.equipment_id,
            start_date=rental.start_date,
            end_date=rental.end_date,
            rental_cost=float(rental.rental_cost or 0),
            client_name=rental.client_name,
            notes=rental.notes,
            created_at=rental.created_at,
            status=(
                "UPCOMING"
                if rental.start_date > today
                else (
                    "COMPLETED"
                    if (rental.end_date or rental.start_date) < today
                    else "ACTIVE"
                )
            ),
            duration=(
                ((rental.end_date or rental.start_date) - rental.start_date).days + 1
            ),
            per_day_cost=round(
                float(rental.rental_cost or 0)
                / (
                    ((rental.end_date or rental.start_date) - rental.start_date).days
                    + 1
                ),
                2,
            ),
        )
        for rental in rentals
    ]


# =============================== RENTAL GET ========================


@router.get(
    "/rental/{rental_id}",
    response_model=EquipmentRentalOut,
)
async def get_rental(
    rental_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    rental = await db.get(
        EquipmentRental,
        rental_id,
    )

    if not rental:
        raise HTTPException(
            status_code=404,
            detail="Rental not found",
        )

    today = date.today()

    end_date = rental.end_date or rental.start_date

    duration = (end_date - rental.start_date).days + 1

    # Rental Status
    if rental.start_date > today:
        rental_status = "UPCOMING"

    elif end_date < today:
        rental_status = "COMPLETED"

    else:
        rental_status = "ACTIVE"

    per_day_cost = float(rental.rental_cost) / duration if duration > 0 else 0

    return EquipmentRentalOut(
        id=rental.id,
        project_id=rental.project_id,
        boq_item_id=rental.boq_item_id,
        equipment_id=rental.equipment_id,
        start_date=rental.start_date,
        end_date=rental.end_date,
        rental_cost=float(rental.rental_cost),
        client_name=rental.client_name,
        notes=rental.notes,
        created_at=rental.created_at,
        status=rental_status,
        duration=duration,
        per_day_cost=round(per_day_cost, 2),
    )


# ============================== RENTAL UPDATE ========================


@router.put(
    "/rental/{rental_id}",
    response_model=EquipmentRentalOut,
)
async def update_rental(
    rental_id: int,
    payload: EquipmentRentalUpdate,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    rental = await db.get(
        EquipmentRental,
        rental_id,
    )

    if not rental:
        raise HTTPException(
            status_code=404,
            detail="Rental not found",
        )

    equipment = await get_active_equipment_or_404(
        db,
        rental.equipment_id,
    )

    # ================= OLD VALUES =================

    old_boq_item_id = rental.boq_item_id
    old_rental_cost = rental.rental_cost

    update_data = payload.model_dump(exclude_unset=True)

    old_values = {}

    for field, value in update_data.items():
        old_values[field] = getattr(rental, field)
        setattr(rental, field, value)

    start_date = rental.start_date
    end_date = rental.end_date or rental.start_date

    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="End date cannot be before start date",
        )

    overlap_exists = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == rental.equipment_id,
                EquipmentRental.id != rental.id,
                EquipmentRental.start_date <= end_date,
                or_(
                    EquipmentRental.end_date.is_(None),
                    EquipmentRental.end_date >= start_date,
                ),
            )
        )
    )

    if overlap_exists:
        raise HTTPException(
            status_code=400,
            detail="Rental overlap found",
        )

    # ================= BOQ COST UPDATE =================

    if old_boq_item_id:

        old_boq = await db.get(
            BOQ,
            old_boq_item_id,
        )

        if old_boq:
            old_boq.actual_cost = max(
                Decimal("0"),
                (old_boq.actual_cost or Decimal("0")) - old_rental_cost,
            )

    if rental.boq_item_id:

        new_boq = await db.get(
            BOQ,
            rental.boq_item_id,
        )

        if not new_boq:
            raise HTTPException(
                status_code=404,
                detail="BOQ item not found",
            )

        new_boq.actual_cost = (new_boq.actual_cost or Decimal("0")) + rental.rental_cost

    # ================= STATUS =================

    await recalculate_equipment_status(
        db,
        equipment,
    )

    await create_audit_log(
        db=db,
        equipment_id=equipment.id,
        action="RENTAL_UPDATE",
        old_values=jsonable_encoder(old_values),
        new_values=jsonable_encoder(update_data),
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    await db.refresh(rental)

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    today = date.today()

    if rental.start_date > today:
        rental_status = "UPCOMING"

    elif rental.end_date and rental.end_date < today:
        rental_status = "COMPLETED"

    else:
        rental_status = "ACTIVE"

    duration = ((rental.end_date or rental.start_date) - rental.start_date).days + 1

    return EquipmentRentalOut(
        id=rental.id,
        project_id=rental.project_id,
        boq_item_id=rental.boq_item_id,
        equipment_id=rental.equipment_id,
        start_date=rental.start_date,
        end_date=rental.end_date,
        rental_cost=float(rental.rental_cost),
        client_name=rental.client_name,
        notes=rental.notes,
        created_at=rental.created_at,
        status=rental_status,
        duration=duration,
        per_day_cost=round(
            float(rental.rental_cost) / duration,
            2,
        ),
    )


# =============================== RENTAL DELETE ========================


from pydantic import BaseModel


@router.delete(
    "/rental/{rental_id}",
    response_model=DeleteRentalResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_rental(
    rental_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    rental = await db.get(
        EquipmentRental,
        rental_id,
    )

    if not rental:
        raise HTTPException(
            status_code=404,
            detail="Rental not found",
        )

    equipment = await get_active_equipment_or_404(
        db,
        rental.equipment_id,
    )

    old_status = equipment.status

    # ================= AUDIT LOG =================

    await create_audit_log(
        db=db,
        equipment_id=equipment.id,
        action="RENTAL_DELETE",
        old_values={
            "rental_id": rental.id,
            "start_date": str(rental.start_date),
            "end_date": (str(rental.end_date) if rental.end_date else None),
            "rental_cost": float(rental.rental_cost or 0),
            "client_name": rental.client_name,
            "boq_item_id": rental.boq_item_id,
            "status": (old_status.value if old_status else None),
        },
        user_id=current_user.id,
        request=request,
    )

    # ================= BOQ COST ROLLBACK =================

    if rental.boq_item_id:

        boq_item = await db.get(
            BOQ,
            rental.boq_item_id,
        )

        if boq_item:

            boq_item.actual_cost = max(
                Decimal("0"),
                (boq_item.actual_cost or Decimal("0"))
                - (rental.rental_cost or Decimal("0")),
            )

    # ================= DELETE RENTAL =================

    await db.delete(rental)

    # Flush delete before status recalculation
    await db.flush()

    # ================= STATUS RECALCULATE =================

    await recalculate_equipment_status(
        db,
        equipment,
    )

    # ================= COMMIT =================

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return DeleteRentalResponse(
        message="Rental deleted successfully",
        rental_id=rental_id,
        equipment_id=equipment.id,
        equipment_status=equipment.status.value,
    )


# =================== ADVANCED APIs ============


@router.get("/report/utilization", response_model=List[UtilizationReportItem])
async def utilization_report(
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    MAX_HOURS = 26 * 8  # configurable later

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


# ========================CREATE PURCHASE APIs ========================


@router.post(
    "/purchase",
    response_model=EquipmentPurchaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_purchase(
    payload: EquipmentPurchaseCreate,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    # ================= PROJECT VALIDATION =================

    project = await db.get(
        Project,
        payload.project_id,
    )

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    # ================= EQUIPMENT VALIDATION =================

    equipment = await db.get(
        Equipment,
        payload.asset_id,
    )

    if not equipment or equipment.is_deleted:
        raise HTTPException(
            status_code=404,
            detail="Equipment not found",
        )

    # ================= PROJECT MATCH VALIDATION =================

    if equipment.project_id and equipment.project_id != payload.project_id:
        raise HTTPException(
            status_code=400,
            detail="Equipment belongs to another project",
        )

    # ================= PURCHASE TYPE VALIDATION =================

    ALLOWED_PURCHASE_TYPES = {
        "PURCHASE",
        "LEASE",
        "REPLACEMENT",
    }

    if payload.purchase_type.upper() not in ALLOWED_PURCHASE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid purchase type. Allowed: {', '.join(ALLOWED_PURCHASE_TYPES)}",
        )

    # ================= QUANTITY VALIDATION =================

    if payload.quantity <= 0:
        raise HTTPException(
            status_code=400,
            detail="Quantity must be greater than zero",
        )

    # ================= INVOICE VALIDATION =================

    duplicate = await db.scalar(
        select(EquipmentPurchase).where(
            EquipmentPurchase.invoice_number == payload.invoice_number
        )
    )

    if duplicate:
        raise HTTPException(
            status_code=400,
            detail="Invoice number already exists",
        )

    # ================= WARRANTY VALIDATION =================

    if payload.warranty_end_date and payload.warranty_end_date <= payload.purchase_date:
        raise HTTPException(
            status_code=400,
            detail="Warranty end date must be after purchase date",
        )

    # ================= BOQ VALIDATION =================

    boq_item = None

    if payload.boq_item_id:

        boq_item = await db.get(
            BOQ,
            payload.boq_item_id,
        )

        if not boq_item:
            raise HTTPException(
                status_code=404,
                detail="BOQ item not found",
            )

        if boq_item.project_id != payload.project_id:
            raise HTTPException(
                status_code=400,
                detail="BOQ item does not belong to selected project",
            )

    # ================= TOTAL AMOUNT =================

    total_amount = Decimal(payload.quantity) * Decimal(payload.unit_price)

    try:

        # ================= CREATE PURCHASE =================

        purchase = EquipmentPurchase(
            **payload.model_dump(),
            total_amount=total_amount,
        )

        db.add(purchase)

        await db.flush()

        # ================= BOQ UPDATE =================

        if boq_item:

            boq_item.actual_cost = (boq_item.actual_cost or Decimal("0")) + total_amount

            boq_item.variance_cost = (
                boq_item.total_cost or Decimal("0")
            ) - boq_item.actual_cost

        # ================= AUDIT LOG =================

        await create_audit_log(
            db=db,
            equipment_id=payload.asset_id,
            action="PURCHASE_CREATE",
            new_values={
                "purchase_id": purchase.id,
                "project_id": payload.project_id,
                "boq_item_id": payload.boq_item_id,
                "asset_id": payload.asset_id,
                "purchase_type": payload.purchase_type,
                "vendor_name": payload.vendor_name,
                "invoice_number": payload.invoice_number,
                "quantity": payload.quantity,
                "unit_price": float(payload.unit_price),
                "total_amount": float(total_amount),
            },
            user_id=current_user.id,
            request=request,
        )

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    await db.refresh(purchase)

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return EquipmentPurchaseOut(
        id=purchase.id,
        project_id=purchase.project_id,
        boq_item_id=purchase.boq_item_id,
        purchase_type=purchase.purchase_type,
        asset_id=purchase.asset_id,
        asset_name=equipment.equipment_name,
        purchase_date=purchase.purchase_date,
        vendor_name=purchase.vendor_name,
        invoice_number=purchase.invoice_number,
        quantity=purchase.quantity,
        unit_price=float(purchase.unit_price or 0),
        total_amount=float(purchase.total_amount or 0),
        warranty_end_date=purchase.warranty_end_date,
        notes=purchase.notes,
        created_at=purchase.created_at,
    )


# =============================== PURCHASE LIST ========================


@router.get(
    "/purchase",
    response_model=PaginatedResponse[EquipmentPurchaseOut],
)
async def list_purchase(
    purchase_type: Optional[str] = None,
    asset_id: Optional[int] = None,
    project_id: Optional[int] = None,
    boq_item_id: Optional[int] = None,
    vendor_name: Optional[str] = None,
    purchase_date_from: Optional[date] = None,
    purchase_date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):

    stmt = (
        select(
            EquipmentPurchase,
            Equipment.equipment_name,
        )
        .join(
            Equipment,
            Equipment.id == EquipmentPurchase.asset_id,
        )
        .where(Equipment.is_deleted.is_(False))
    )

    count_stmt = (
        select(func.count())
        .select_from(EquipmentPurchase)
        .join(
            Equipment,
            Equipment.id == EquipmentPurchase.asset_id,
        )
        .where(Equipment.is_deleted.is_(False))
    )

    if purchase_type:
        stmt = stmt.where(EquipmentPurchase.purchase_type == purchase_type)
        count_stmt = count_stmt.where(EquipmentPurchase.purchase_type == purchase_type)

    if asset_id:
        stmt = stmt.where(EquipmentPurchase.asset_id == asset_id)
        count_stmt = count_stmt.where(EquipmentPurchase.asset_id == asset_id)

    if project_id:
        stmt = stmt.where(EquipmentPurchase.project_id == project_id)
        count_stmt = count_stmt.where(EquipmentPurchase.project_id == project_id)

    if boq_item_id:
        stmt = stmt.where(EquipmentPurchase.boq_item_id == boq_item_id)
        count_stmt = count_stmt.where(EquipmentPurchase.boq_item_id == boq_item_id)

    if vendor_name:
        stmt = stmt.where(EquipmentPurchase.vendor_name.ilike(f"%{vendor_name}%"))
        count_stmt = count_stmt.where(
            EquipmentPurchase.vendor_name.ilike(f"%{vendor_name}%")
        )

    if purchase_date_from:
        stmt = stmt.where(EquipmentPurchase.purchase_date >= purchase_date_from)
        count_stmt = count_stmt.where(
            EquipmentPurchase.purchase_date >= purchase_date_from
        )

    if purchase_date_to:
        stmt = stmt.where(EquipmentPurchase.purchase_date <= purchase_date_to)
        count_stmt = count_stmt.where(
            EquipmentPurchase.purchase_date <= purchase_date_to
        )

    stmt = (
        stmt.order_by(
            EquipmentPurchase.purchase_date.desc(),
            EquipmentPurchase.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(stmt)
    rows = result.all()

    total = await db.scalar(count_stmt)

    items = [
        EquipmentPurchaseOut(
            id=purchase.id,
            project_id=purchase.project_id,
            boq_item_id=purchase.boq_item_id,
            purchase_type=str(purchase.purchase_type),
            asset_id=purchase.asset_id,
            asset_name=equipment_name,
            purchase_date=purchase.purchase_date,
            vendor_name=purchase.vendor_name,
            invoice_number=purchase.invoice_number,
            quantity=purchase.quantity,
            unit_price=float(purchase.unit_price),
            total_amount=float(purchase.total_amount),
            warranty_end_date=purchase.warranty_end_date,
            notes=purchase.notes,
            created_at=purchase.created_at,
        )
        for purchase, equipment_name in rows
    ]

    return PaginatedResponse[EquipmentPurchaseOut](
        items=items,
        meta=PaginationMeta(
            total=total or 0,
            limit=limit,
            offset=offset,
        ),
    )


# =============================== PURCHASE GET ========================


@router.get(
    "/purchase/{purchase_id}",
    response_model=EquipmentPurchaseOut,
)
async def get_purchase(
    purchase_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(
            EquipmentPurchase,
            Equipment.equipment_name,
        )
        .join(
            Equipment,
            Equipment.id == EquipmentPurchase.asset_id,
        )
        .where(
            EquipmentPurchase.id == purchase_id,
        )
    )

    row = result.first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Purchase not found",
        )

    purchase, equipment_name = row

    return EquipmentPurchaseOut(
        id=purchase.id,
        project_id=purchase.project_id,
        boq_item_id=purchase.boq_item_id,
        purchase_type=purchase.purchase_type,
        asset_id=purchase.asset_id,
        asset_name=equipment_name,
        purchase_date=purchase.purchase_date,
        vendor_name=purchase.vendor_name,
        invoice_number=purchase.invoice_number,
        quantity=purchase.quantity,
        unit_price=float(purchase.unit_price or 0),
        total_amount=float(purchase.total_amount or 0),
        warranty_end_date=purchase.warranty_end_date,
        notes=purchase.notes,
        created_at=purchase.created_at,
    )


# =============================== PURCHASE UPDATE ========================


@router.put(
    "/purchase/{purchase_id}",
    response_model=EquipmentPurchaseOut,
)
async def update_purchase(
    purchase_id: int,
    payload: EquipmentPurchaseUpdate,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    purchase = await db.get(
        EquipmentPurchase,
        purchase_id,
    )

    if not purchase:
        raise HTTPException(
            status_code=404,
            detail="Purchase not found",
        )

    # ================= OLD VALUES =================

    old_total_amount = purchase.total_amount or Decimal("0")
    old_boq_item_id = purchase.boq_item_id

    # ================= INVOICE VALIDATION =================

    if payload.invoice_number and payload.invoice_number != purchase.invoice_number:
        duplicate = await db.scalar(
            select(EquipmentPurchase).where(
                EquipmentPurchase.invoice_number == payload.invoice_number,
                EquipmentPurchase.id != purchase_id,
            )
        )

        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="Invoice number already exists",
            )

    # ================= UPDATE DATA =================

    update_data = payload.model_dump(exclude_unset=True)

    old_values = {}

    for field, value in update_data.items():
        old_values[field] = getattr(purchase, field)
        setattr(purchase, field, value)

    # ================= WARRANTY VALIDATION =================

    if (
        purchase.warranty_end_date
        and purchase.purchase_date
        and purchase.warranty_end_date <= purchase.purchase_date
    ):
        raise HTTPException(
            status_code=400,
            detail="Warranty end date must be after purchase date",
        )

    # ================= RECALCULATE TOTAL =================

    if purchase.quantity is not None and purchase.unit_price is not None:
        purchase.total_amount = purchase.quantity * purchase.unit_price

    new_total_amount = purchase.total_amount or Decimal("0")
    new_boq_item_id = purchase.boq_item_id

    # ================= BOQ SYNC =================

    if old_boq_item_id != new_boq_item_id:

        # Rollback old BOQ
        if old_boq_item_id:

            old_boq = await db.get(
                BOQ,
                old_boq_item_id,
            )

            if old_boq:

                old_boq.actual_cost = max(
                    Decimal("0"),
                    (old_boq.actual_cost or Decimal("0")) - old_total_amount,
                )

        # Add amount to new BOQ
        if new_boq_item_id:

            new_boq = await db.get(
                BOQ,
                new_boq_item_id,
            )

            if not new_boq:
                raise HTTPException(
                    status_code=404,
                    detail="BOQ item not found",
                )

            if purchase.project_id and new_boq.project_id != purchase.project_id:
                raise HTTPException(
                    status_code=400,
                    detail="BOQ item does not belong to project",
                )

            new_boq.actual_cost = (
                new_boq.actual_cost or Decimal("0")
            ) + new_total_amount

    # Same BOQ -> adjust amount difference only
    elif new_boq_item_id:

        boq_item = await db.get(
            BOQ,
            new_boq_item_id,
        )

        if boq_item:

            boq_item.actual_cost = max(
                Decimal("0"),
                (
                    (boq_item.actual_cost or Decimal("0"))
                    - old_total_amount
                    + new_total_amount
                ),
            )

    # ================= EQUIPMENT =================

    equipment = await db.get(
        Equipment,
        purchase.asset_id,
    )

    # ================= AUDIT LOG =================

    await create_audit_log(
        db=db,
        equipment_id=purchase.asset_id,
        action="PURCHASE_UPDATE",
        old_values=jsonable_encoder(convert_decimal(old_values)),
        new_values={
            **jsonable_encoder(convert_decimal(update_data)),
            "old_total_amount": float(old_total_amount),
            "new_total_amount": float(new_total_amount),
            "old_boq_item_id": old_boq_item_id,
            "new_boq_item_id": new_boq_item_id,
        },
        user_id=current_user.id,
        request=request,
    )

    # ================= COMMIT =================

    await db.commit()

    await db.refresh(purchase)

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    # ================= RESPONSE =================

    return EquipmentPurchaseOut(
        id=purchase.id,
        project_id=purchase.project_id,
        boq_item_id=purchase.boq_item_id,
        purchase_type=str(purchase.purchase_type),
        asset_id=purchase.asset_id,
        asset_name=(equipment.equipment_name if equipment else None),
        purchase_date=purchase.purchase_date,
        vendor_name=purchase.vendor_name,
        invoice_number=purchase.invoice_number,
        quantity=purchase.quantity,
        unit_price=float(purchase.unit_price or 0),
        total_amount=float(purchase.total_amount or 0),
        warranty_end_date=purchase.warranty_end_date,
        notes=purchase.notes,
        created_at=purchase.created_at,
    )


# =============================== PURCHASE DELETE ========================


@router.delete(
    "/purchase/{purchase_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_purchase(
    purchase_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    purchase = await db.get(
        EquipmentPurchase,
        purchase_id,
    )

    if not purchase:
        raise HTTPException(
            status_code=404,
            detail="Purchase not found",
        )

    # ================= BOQ ROLLBACK =================

    if purchase.boq_item_id:

        boq_item = await db.get(
            BOQ,
            purchase.boq_item_id,
        )

        if boq_item:

            boq_item.actual_cost = max(
                Decimal("0"),
                (boq_item.actual_cost or Decimal("0"))
                - (purchase.total_amount or Decimal("0")),
            )

    # ================= AUDIT LOG =================

    await create_audit_log(
        db=db,
        equipment_id=purchase.asset_id,
        action="PURCHASE_DELETE",
        old_values={
            "purchase_id": purchase.id,
            "asset_id": purchase.asset_id,
            "boq_item_id": purchase.boq_item_id,
            "invoice_number": purchase.invoice_number,
            "vendor_name": purchase.vendor_name,
            "quantity": purchase.quantity,
            "unit_price": float(purchase.unit_price or 0),
            "total_amount": float(purchase.total_amount or 0),
        },
        user_id=current_user.id,
        request=request,
    )

    asset_id = purchase.asset_id
    invoice_number = purchase.invoice_number
    boq_item_id = purchase.boq_item_id

    await db.delete(purchase)

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return {
        "message": "Purchase deleted successfully",
        "purchase_id": purchase_id,
        "asset_id": asset_id,
        "boq_item_id": boq_item_id,
        "invoice_number": invoice_number,
    }


# ============================== EQUIPMENT TRANSFER ========================


@router.post("/transfer")
async def transfer_equipment(
    payload: EquipmentTransferRequest,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    equipment = await get_active_equipment_or_404(
        db,
        payload.equipment_id,
    )

    if equipment.condition == EquipmentCondition.DAMAGED:
        raise HTTPException(
            status_code=400,
            detail="Damaged equipment cannot be transferred",
        )

    if equipment.status == EquipmentStatus.MAINTENANCE:
        raise HTTPException(
            status_code=400,
            detail="Equipment under maintenance",
        )

    if equipment.project_id is None:
        raise HTTPException(
            status_code=400,
            detail="Equipment not allocated",
        )

    if equipment.project_id == payload.to_project_id:
        raise HTTPException(
            status_code=400,
            detail="Already allocated to same project",
        )

    project = await db.get(
        Project,
        payload.to_project_id,
    )

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Target project not found",
        )

    today = date.today()

    if project.end_date and project.end_date < today:
        raise HTTPException(
            status_code=400,
            detail="Cannot transfer to completed project",
        )

    rental_exists = await db.scalar(
        select(
            exists().where(
                EquipmentRental.equipment_id == equipment.id,
                EquipmentRental.start_date <= today,
                or_(
                    EquipmentRental.end_date.is_(None),
                    EquipmentRental.end_date >= today,
                ),
            )
        )
    )

    if rental_exists:
        raise HTTPException(
            status_code=400,
            detail="Equipment currently rented",
        )

    old_project = equipment.project_id

    equipment.project_id = payload.to_project_id

    await create_audit_log(
        db=db,
        equipment_id=equipment.id,
        action="TRANSFER",
        old_values={
            "project_id": old_project,
        },
        new_values={
            "project_id": payload.to_project_id,
        },
        user_id=current_user.id,
        request=request,
    )

    await db.commit()

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return {
        "message": "Equipment transferred successfully",
        "equipment_id": equipment.id,
        "from_project": old_project,
        "to_project": payload.to_project_id,
    }


# =====================get_equipment==============================


@router.get("/{equipment_id}", response_model=EquipmentOut)
async def get_equipment(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await get_active_equipment_or_404(
        db,
        equipment_id,
    )

    # Recalculate latest status
    await recalculate_equipment_status(
        db,
        obj,
    )

    await db.commit()
    await db.refresh(obj)

    return EquipmentOut.model_validate(obj)


# =============================update_equipment=======================


@router.put("/{equipment_id}", response_model=EquipmentOut)
async def update_equipment(
    equipment_id: int,
    payload: EquipmentUpdate,
    current_user: User = Depends(require_roles(EQUIPMENT_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
    request: Request = None,
):
    obj = await get_active_equipment_or_404(db, equipment_id)

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
            raise HTTPException(
                status_code=400,
                detail="Equipment code already exists",
            )

    update_data = payload.model_dump(exclude_unset=True)

    old_data = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    # Apply updates
    for field, value in update_data.items():
        setattr(obj, field, value)

    # 🔥 FIX
    await recalculate_equipment_status(
        db,
        obj,
    )

    if hasattr(obj, "updated_at"):
        obj.updated_at = datetime.utcnow()

    await db.flush()

    changed_fields = {
        k: {"old": old_data.get(k), "new": getattr(obj, k)}
        for k in update_data
        if old_data.get(k) != getattr(obj, k)
    }

    if not changed_fields:
        return EquipmentOut.model_validate(obj)

    old_values = {k: v["old"] for k, v in changed_fields.items()}

    new_values = {k: v["new"] for k, v in changed_fields.items()}

    await create_audit_log(
        db,
        obj.id,
        "UPDATE",
        old_values=jsonable_encoder(old_values),
        new_values=jsonable_encoder(new_values),
        user_id=current_user.id,
        request=request,
    )

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    await db.commit()
    await db.refresh(obj)

    return EquipmentOut.model_validate(obj)


# ============================== EQUIPMENT PURCHASE HISTORY ========================


@router.get(
    "/purchase/equipment/{equipment_id}",
    response_model=List[EquipmentPurchaseOut],
)
async def get_equipment_purchase_history(
    equipment_id: int,
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    equipment = await get_active_equipment_or_404(
        db,
        equipment_id,
    )

    if not equipment:
        raise HTTPException(
            status_code=404,
            detail="Equipment not found",
        )

    result = await db.execute(
        select(EquipmentPurchase)
        .where(
            EquipmentPurchase.asset_id == equipment_id,
        )
        .order_by(
            EquipmentPurchase.purchase_date.desc(),
            EquipmentPurchase.created_at.desc(),
        )
    )

    purchases = result.scalars().all()

    return [
        EquipmentPurchaseOut(
            id=purchase.id,
            project_id=purchase.project_id,
            boq_item_id=purchase.boq_item_id,
            purchase_type=str(purchase.purchase_type),
            asset_id=purchase.asset_id,
            asset_name=equipment.equipment_name,
            purchase_date=purchase.purchase_date,
            vendor_name=purchase.vendor_name,
            invoice_number=purchase.invoice_number,
            quantity=purchase.quantity,
            unit_price=float(purchase.unit_price),
            total_amount=float(purchase.total_amount),
            warranty_end_date=purchase.warranty_end_date,
            notes=purchase.notes,
            created_at=purchase.created_at,
        )
        for purchase in purchases
    ]


# ======================== REPORTS PDF========================


@router.get("/reports/pdf")
async def equipment_full_pdf_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):
    """
    NOTE: Make sure these are imported at the top of this router file
    (alongside Equipment, EquipmentMaintenance, EquipmentRental):
        from app.models.equipment import EquipmentUsage, EquipmentPurchase
    """
    try:
        import io
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
        from reportlab.lib.enums import TA_CENTER, TA_LEFT

        # ================= SIMPLE, CONSISTENT COLORS =================
        TABLE_HDR = colors.HexColor("#305496")  # one header color for every table
        ROW_ALT = colors.HexColor("#F5F5F5")
        ROW_WHITE = colors.white
        BORDER_CLR = colors.HexColor("#DDDDDD")
        TEXT_DARK = colors.HexColor("#222222")
        TEXT_LIGHT = colors.white
        LABEL_GREY = colors.HexColor("#666666")

        FONT = "Helvetica"
        FONT_BOLD = "Helvetica-Bold"

        # ================= PDF SETUP =================
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            leftMargin=36,
            rightMargin=36,
            topMargin=40,
            bottomMargin=36,
        )

        # ================= STYLES =================
        title_style = ParagraphStyle(
            "Title",
            fontSize=16,
            leading=20,
            textColor=TEXT_DARK,
            alignment=TA_CENTER,
            fontName=FONT_BOLD,
            spaceAfter=6,
        )
        sub_style = ParagraphStyle(
            "Sub",
            fontSize=9,
            leading=12,
            textColor=LABEL_GREY,
            alignment=TA_CENTER,
            spaceBefore=2,
            spaceAfter=4,
        )
        empty_style = ParagraphStyle(
            "Empty",
            fontSize=9,
            textColor=LABEL_GREY,
            fontName="Helvetica-Oblique",
            spaceAfter=10,
        )
        section_style = ParagraphStyle(
            "Section",
            fontSize=11,
            textColor=TEXT_DARK,
            fontName=FONT_BOLD,
            alignment=TA_LEFT,
            spaceBefore=14,
            spaceAfter=6,
        )
        info_label = ParagraphStyle("InfoLabel", fontSize=8, textColor=LABEL_GREY)
        bold_style = ParagraphStyle(
            "BoldStyle", fontSize=9, textColor=TEXT_DARK, fontName=FONT_BOLD
        )

        # ================= FETCH DATA =================
        eq_result = await db.execute(
            select(Equipment).where(Equipment.is_deleted == False)
        )
        equipments = eq_result.scalars().all() or []

        usages = (await db.execute(select(EquipmentUsage))).scalars().all() or []
        maint = (await db.execute(select(EquipmentMaintenance))).scalars().all() or []
        rentals = (await db.execute(select(EquipmentRental))).scalars().all() or []
        purchases = (await db.execute(select(EquipmentPurchase))).scalars().all() or []

        # ================= SAFE TOTALS =================
        total_maint_cost = sum(float(m.cost or 0) for m in maint)
        total_rental_cost = sum(float(r.rental_cost or 0) for r in rentals)
        total_purchase_cost = sum(float(p.total_amount or 0) for p in purchases)
        grand_total = total_maint_cost + total_rental_cost + total_purchase_cost

        now_str = datetime.now().strftime("%d %b %Y")

        # ================= HELPERS =================
        def safe_val(obj, attr):
            v = getattr(obj, attr, None)
            if v is None:
                return "-"
            return str(getattr(v, "value", v)).upper()

        TABLE_HEADER_STYLE = [
            ("BACKGROUND", (0, 0), (-1, 0), TABLE_HDR),
            ("TEXTCOLOR", (0, 0), (-1, 0), TEXT_LIGHT),
            ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_CLR),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ROW_WHITE, ROW_ALT]),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]

        def make_table(data, col_widths):
            t = Table(data, colWidths=col_widths, repeatRows=1)
            t.setStyle(TableStyle(TABLE_HEADER_STYLE))
            return t

        def render_section(title_text, headers, rows, col_widths, empty_text):
            story.append(Paragraph(title_text, section_style))
            if rows:
                story.append(make_table([headers] + rows, col_widths))
            else:
                story.append(Paragraph(empty_text, empty_style))

        # ================= STORY =================
        story = []

        story.append(Paragraph("Equipment Management Report", title_style))
        story.append(Paragraph(f"Generated on {now_str}", sub_style))
        story.append(
            HRFlowable(width="100%", thickness=1, color=BORDER_CLR, spaceAfter=12)
        )

        # ---------------- Summary ----------------
        good_count = sum(1 for e in equipments if safe_val(e, "condition") == "GOOD")
        damaged_count = sum(
            1 for e in equipments if safe_val(e, "condition") == "DAMAGED"
        )

        summary_data = [
            [
                Paragraph("Total Equipment", info_label),
                Paragraph("Good", info_label),
                Paragraph("Damaged", info_label),
                Paragraph("Maint. Cost", info_label),
                Paragraph("Rental Cost", info_label),
                Paragraph("Purchase Cost", info_label),
                Paragraph("Grand Total", info_label),
            ],
            [
                Paragraph(str(len(equipments)), bold_style),
                Paragraph(str(good_count), bold_style),
                Paragraph(str(damaged_count), bold_style),
                Paragraph(f"Rs. {total_maint_cost:,.0f}", bold_style),
                Paragraph(f"Rs. {total_rental_cost:,.0f}", bold_style),
                Paragraph(f"Rs. {total_purchase_cost:,.0f}", bold_style),
                Paragraph(f"Rs. {grand_total:,.0f}", bold_style),
            ],
        ]
        sum_table = Table(summary_data, colWidths=[65, 50, 60, 75, 75, 80, 80])
        sum_table.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, BORDER_CLR),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_CLR),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(sum_table)

        # ---------------- Equipment ----------------
        eq_headers = [
            "#",
            "Code",
            "Name",
            "Operator",
            "Condition",
            "Status",
            "Hours",
            "Fuel",
            "Rental Cost",
        ]
        eq_rows = []
        for i, e in enumerate(equipments, 1):
            eq_rows.append(
                [
                    str(i),
                    str(e.equipment_code or "-"),
                    str(e.equipment_name or "-"),
                    str(e.operator_name or "-"),
                    safe_val(e, "condition"),
                    safe_val(e, "status"),
                    f"{float(e.working_hours or 0):,.1f}",
                    f"{float(e.fuel_used or 0):,.1f}",
                    f"{float(e.rental_cost or 0):,.2f}",
                ]
            )
        render_section(
            "Equipment",
            eq_headers,
            eq_rows,
            [20, 55, 95, 75, 60, 65, 45, 45, 65],
            "No equipment found.",
        )

        # ---------------- Usage ----------------
        u_headers = ["Equip ID", "Hours", "Fuel", "Date", "Notes"]
        u_rows = []
        for u in usages:
            u_rows.append(
                [
                    str(u.equipment_id or "-"),
                    f"{float(u.working_hours or 0):,.1f}",
                    f"{float(u.fuel_used or 0):,.1f}",
                    str(u.usage_date or "-"),
                    str(u.notes or "-"),
                ]
            )
        render_section(
            "Usage Records",
            u_headers,
            u_rows,
            [55, 55, 55, 80, 280],
            "No usage records found.",
        )

        # ---------------- Maintenance ----------------
        m_headers = ["Equip ID", "Description", "Date", "Cost", "Next Due"]
        m_rows = []
        for m in maint:
            m_rows.append(
                [
                    str(m.equipment_id or "-"),
                    str(m.description or "-"),
                    str(m.maintenance_date or "-"),
                    f"{float(m.cost or 0):,.2f}",
                    str(m.next_maintenance_date or "-"),
                ]
            )
        render_section(
            "Maintenance Records",
            m_headers,
            m_rows,
            [55, 195, 80, 70, 80],
            "No maintenance records found.",
        )

        # ---------------- Rentals ----------------
        r_headers = ["Equip ID", "Client", "Start", "End", "Cost"]
        r_rows = []
        for r in rentals:
            r_rows.append(
                [
                    str(r.equipment_id or "-"),
                    str(r.client_name or "-"),
                    str(r.start_date or "-"),
                    str(r.end_date or "-"),
                    f"{float(r.rental_cost or 0):,.2f}",
                ]
            )
        render_section(
            "Rental Records",
            r_headers,
            r_rows,
            [55, 180, 80, 80, 85],
            "No rental records found.",
        )

        # ---------------- Purchases ----------------
        p_headers = [
            "Asset ID",
            "Type",
            "Vendor",
            "Invoice",
            "Qty",
            "Unit Price",
            "Total",
            "Date",
        ]
        p_rows = []
        for p in purchases:
            p_rows.append(
                [
                    str(p.asset_id or "-"),
                    str(p.purchase_type or "-"),
                    str(p.vendor_name or "-"),
                    str(p.invoice_number or "-"),
                    str(p.quantity or 0),
                    f"{float(p.unit_price or 0):,.2f}",
                    f"{float(p.total_amount or 0):,.2f}",
                    str(p.purchase_date or "-"),
                ]
            )
        render_section(
            "Purchase Records",
            p_headers,
            p_rows,
            [50, 55, 100, 75, 35, 65, 65, 70],
            "No purchase records found.",
        )

        # ================= SIMPLE FOOTER (page number only) =================
        def draw_footer(canvas, doc):
            canvas.saveState()
            w, h = letter
            canvas.setFont(FONT, 8)
            canvas.setFillColor(colors.grey)
            canvas.drawCentredString(w / 2, 20, f"Page {doc.page}")
            canvas.restoreState()

        # ================= BUILD PDF =================
        doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)

        buffer.seek(0)
        filename = f"equipment_report_{datetime.now().strftime('%Y%m%d')}.pdf"

        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")


# ================================ EXCEL REPORT ================================


@router.get("/reports/excel")
async def equipment_excel_report(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(EQUIPMENT_READ_ROLES)),
):
    """
    NOTE: Make sure these are imported at the top of this router file
    (alongside Equipment, EquipmentMaintenance, EquipmentRental):
        from app.models.equipment import EquipmentUsage, EquipmentPurchase, EquipmentAuditLog
    """
    try:
        import io
        from datetime import datetime

        from fastapi import HTTPException
        from fastapi.responses import StreamingResponse

        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        # ================= FETCH DATA =================
        stmt = select(Equipment).where(Equipment.is_deleted == False)
        equipments = (await db.execute(stmt)).scalars().all() or []

        usages = (await db.execute(select(EquipmentUsage))).scalars().all() or []
        maint = (await db.execute(select(EquipmentMaintenance))).scalars().all() or []
        rentals = (await db.execute(select(EquipmentRental))).scalars().all() or []
        purchases = (await db.execute(select(EquipmentPurchase))).scalars().all() or []

        # ================= SAFE TOTALS =================
        total_maint_cost = sum(float(m.cost or 0) for m in maint)
        total_rental_cost = sum(float(r.rental_cost or 0) for r in rentals)
        total_purchase_cost = sum(float(p.total_amount or 0) for p in purchases)

        now_str = datetime.now().strftime("%d %b %Y %I:%M %p")
        CURRENCY_FMT = '"Rs." #,##0.00'

        # ================= SIMPLE, CONSISTENT STYLES =================
        HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        HEADER_FILL = PatternFill("solid", fgColor="305496")
        TITLE_FONT = Font(name="Arial", bold=True, size=14)
        LABEL_FONT = Font(name="Arial", bold=True, size=10)
        CELL_FONT = Font(name="Arial", size=10)
        THIN = Side(style="thin", color="D9D9D9")
        BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
        CENTER = Alignment(horizontal="center", vertical="center")

        def safe_val(obj, attr):
            """Returns enum.value if it's an enum, else the raw value, else '-'."""
            v = getattr(obj, attr, None)
            if v is None:
                return "-"
            return getattr(v, "value", v)

        def write_table(ws, headers, rows, currency_cols=None):
            """Writes one header row + data rows with simple, uniform styling."""
            currency_cols = currency_cols or []

            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col, value=header)
                cell.font = HEADER_FONT
                cell.fill = HEADER_FILL
                cell.alignment = CENTER
                cell.border = BORDER

            for r, row_values in enumerate(rows, 2):
                for col, val in enumerate(row_values, 1):
                    cell = ws.cell(row=r, column=col, value=val)
                    cell.font = CELL_FONT
                    cell.alignment = CENTER
                    cell.border = BORDER
                    if col in currency_cols:
                        cell.number_format = CURRENCY_FMT

            # simple autosize based on header + content length
            for col, header in enumerate(headers, 1):
                max_len = len(str(header))
                for row_values in rows:
                    val = row_values[col - 1]
                    max_len = max(max_len, len(str(val)))
                ws.column_dimensions[get_column_letter(col)].width = min(
                    max(max_len + 4, 12), 40
                )

            ws.freeze_panes = "A2"

        # ================= WORKBOOK =================
        wb = Workbook()
        wb.remove(wb.active)  # we'll add named sheets explicitly

        # ---------------- Equipment ----------------
        ws = wb.create_sheet("Equipment")
        headers = [
            "ID",
            "Project ID",
            "Code",
            "Name",
            "Operator",
            "Condition",
            "Status",
            "Working Hours",
            "Fuel Used",
            "Rental Cost",
            "Maintenance Date",
        ]
        rows = [
            [
                e.id,
                e.project_id or "-",
                e.equipment_code or "-",
                e.equipment_name or "-",
                e.operator_name or "-",
                safe_val(e, "condition"),
                safe_val(e, "status"),
                float(e.working_hours or 0),
                float(e.fuel_used or 0),
                float(e.rental_cost or 0),
                str(e.maintenance_date) if e.maintenance_date else "-",
            ]
            for e in equipments
        ]
        write_table(ws, headers, rows, currency_cols=[10])

        # ---------------- Usage ----------------
        ws = wb.create_sheet("Usage")
        headers = ["Equip ID", "Working Hours", "Fuel Used", "Usage Date", "Notes"]
        rows = [
            [
                u.equipment_id,
                float(u.working_hours or 0),
                float(u.fuel_used or 0),
                str(u.usage_date or "-"),
                u.notes or "-",
            ]
            for u in usages
        ]
        write_table(ws, headers, rows)

        # ---------------- Maintenance ----------------
        ws = wb.create_sheet("Maintenance")
        headers = ["Equip ID", "Description", "Date", "Cost", "Next Due"]
        rows = [
            [
                m.equipment_id,
                m.description or "-",
                str(m.maintenance_date or "-"),
                float(m.cost or 0),
                str(m.next_maintenance_date or "-"),
            ]
            for m in maint
        ]
        write_table(ws, headers, rows, currency_cols=[4])

        # ---------------- Rentals ----------------
        ws = wb.create_sheet("Rentals")
        headers = ["Equip ID", "Client", "Start", "End", "Cost", "Notes"]
        rows = [
            [
                r.equipment_id,
                r.client_name or "-",
                str(r.start_date or "-"),
                str(r.end_date or "-"),
                float(r.rental_cost or 0),
                r.notes or "-",
            ]
            for r in rentals
        ]
        write_table(ws, headers, rows, currency_cols=[5])

        # ---------------- Purchases ----------------
        ws = wb.create_sheet("Purchases")
        headers = [
            "Asset ID",
            "Type",
            "Vendor",
            "Invoice",
            "Qty",
            "Unit Price",
            "Total",
            "Purchase Date",
            "Warranty End",
        ]
        rows = [
            [
                p.asset_id,
                p.purchase_type or "-",
                p.vendor_name or "-",
                p.invoice_number or "-",
                p.quantity or 0,
                float(p.unit_price or 0),
                float(p.total_amount or 0),
                str(p.purchase_date or "-"),
                str(p.warranty_end_date or "-"),
            ]
            for p in purchases
        ]
        write_table(ws, headers, rows, currency_cols=[6, 7])

        # ---------------- Summary (placed first) ----------------
        good_count = sum(1 for e in equipments if safe_val(e, "condition") == "GOOD")
        damaged_count = sum(
            1 for e in equipments if safe_val(e, "condition") == "DAMAGED"
        )

        ws_summary = wb.create_sheet("Summary", 0)
        ws_summary["A1"] = "Equipment Management Report"
        ws_summary["A1"].font = TITLE_FONT
        ws_summary["A2"] = f"Generated: {now_str}"
        ws_summary["A2"].font = Font(name="Arial", italic=True, size=9, color="666666")

        summary_rows = [
            ("Total Equipment", len(equipments)),
            ("Good Condition", good_count),
            ("Damaged", damaged_count),
            ("Usage Records", len(usages)),
            ("Maintenance Records", len(maint)),
            ("Rental Records", len(rentals)),
            ("Purchase Records", len(purchases)),
            ("Total Maintenance Cost", total_maint_cost),
            ("Total Rental Cost", total_rental_cost),
            ("Total Purchase Cost", total_purchase_cost),
            (
                "Grand Total (Maint + Rental + Purchase)",
                total_maint_cost + total_rental_cost + total_purchase_cost,
            ),
        ]

        row_num = 4
        for label, value in summary_rows:
            ws_summary.cell(row=row_num, column=1, value=label).font = LABEL_FONT
            cell = ws_summary.cell(row=row_num, column=2, value=value)
            cell.font = CELL_FONT
            if "Cost" in label or "Total" in label and isinstance(value, float):
                cell.number_format = CURRENCY_FMT
            row_num += 1

        ws_summary.column_dimensions["A"].width = 36
        ws_summary.column_dimensions["B"].width = 18

        # ================= SAVE FILE =================
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"equipment_report_{datetime.now().strftime('%Y%m%d')}.xlsx"

        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(
            status_code=500, detail=f"Excel generation failed: {str(e)}"
        )
