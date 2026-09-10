from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db_session
from app.models.work_order import WorkOrder
from app.models.project import Project
from app.models.contractor import Contractor
from app.models.user import User

from app.schemas.work_order import (
    WorkOrderCreate,
    WorkOrderUpdate,
    WorkOrderOut,
)

from app.utils.helpers import NotFoundError, ValidationError, PermissionDeniedError
from app.utils.common import (
    assert_project_access,
    generate_business_id,
)
from app.core.dependencies import (
    require_permission,
    get_effective_user_permissions,
    has_permission,
)
from app.core.logger import logger

router = APIRouter(prefix="/work-orders", tags=["Work Orders"])


async def _get_scoped_work_order(
    db: AsyncSession,
    work_order_id: int,
    current_user: User,
) -> WorkOrder:
    """Retrieve WorkOrder enforcing tenant boundary isolation through Project.company_id."""
    is_sa = getattr(current_user, "is_super_admin", False) is True

    query = (
        select(WorkOrder)
        .join(Project, WorkOrder.project_id == Project.id)
        .where(WorkOrder.id == work_order_id)
    )

    if not is_sa:
        if current_user.company_id is None:
            raise NotFoundError("Work order not found")
        query = query.where(Project.company_id == current_user.company_id)

    obj = await db.scalar(query)
    if not obj:
        raise NotFoundError("Work order not found")

    return obj


# ================================================================
# 1. CREATE WORK ORDER
# ================================================================


@router.post("", response_model=WorkOrderOut)
async def create_work_order(
    payload: WorkOrderCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("work_orders.create")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True

    if not is_sa and current_user.company_id is None:
        raise PermissionDeniedError("User does not belong to any company")

    # Validate Project tenant ownership
    project = await db.get(Project, payload.project_id)
    if not project:
        raise NotFoundError("Project not found")

    if not is_sa and project.company_id != current_user.company_id:
        raise NotFoundError("Project not found")

    # Validate Contractor tenant ownership (optional)
    if payload.contractor_id:
        contractor = await db.get(Contractor, payload.contractor_id)
        if not contractor:
            raise NotFoundError("Contractor not found")

        if not is_sa and contractor.company_id != current_user.company_id:
            raise NotFoundError("Contractor not found")

    await assert_project_access(
        db,
        project_id=payload.project_id,
        current_user=current_user,
    )

    work_order_number = await generate_business_id(
        db,
        WorkOrder,
        "work_order_number",
        "WO",
    )

    total_amount = payload.total_quantity * payload.rate

    obj = WorkOrder(
        **payload.model_dump(),
        work_order_number=work_order_number,
        total_amount=total_amount,
    )

    db.add(obj)
    try:
        await db.flush()
    except SQLAlchemyError as exc:
        logger.exception(f"Database error creating work order: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Database error creating work order",
        )

    return WorkOrderOut.model_validate(obj)


# ================================================================
# 2. LIST WORK ORDERS
# ================================================================


@router.get("", response_model=list[WorkOrderOut])
async def list_work_orders(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("work_orders.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True

    if not is_sa and current_user.company_id is None:
        raise PermissionDeniedError("User does not belong to any company")

    query = select(WorkOrder).join(Project, WorkOrder.project_id == Project.id)

    if not is_sa:
        query = query.where(Project.company_id == current_user.company_id)

        effective_perms = await get_effective_user_permissions(db, current_user)
        has_manage = has_permission(effective_perms, "work_orders.manage")

        if not has_manage:
            query = query.where(Project.members.any(user_id=current_user.id))

    result = await db.execute(query.order_by(WorkOrder.id.desc()))
    rows = result.scalars().all()

    return [WorkOrderOut.model_validate(r) for r in rows]


# ================================================================
# 3. GET WORK ORDER DETAIL
# ================================================================


@router.get("/{id}", response_model=WorkOrderOut)
async def get_work_order(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("work_orders.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True

    if not is_sa and current_user.company_id is None:
        raise PermissionDeniedError("User does not belong to any company")

    obj = await _get_scoped_work_order(db, id, current_user)

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    return WorkOrderOut.model_validate(obj)


# ================================================================
# 4. UPDATE WORK ORDER
# ================================================================


@router.put("/{id}", response_model=WorkOrderOut)
async def update_work_order(
    id: int,
    payload: WorkOrderUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("work_orders.edit")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True

    if not is_sa and current_user.company_id is None:
        raise PermissionDeniedError("User does not belong to any company")

    obj = await _get_scoped_work_order(db, id, current_user)

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    data = payload.model_dump(exclude_unset=True)

    # ==========================
    # Contractor Validation
    # ==========================
    if "contractor_id" in data and data["contractor_id"] is not None:
        contractor = await db.get(Contractor, data["contractor_id"])

        if not contractor:
            raise NotFoundError("Contractor not found")

        if not is_sa and contractor.company_id != current_user.company_id:
            raise NotFoundError("Contractor not found")

    # ==========================
    # Update Fields
    # ==========================
    for k, v in data.items():
        setattr(obj, k, v)

    # ==========================
    # Validations
    # ==========================
    if (
        obj.completed_quantity is not None
        and obj.completed_quantity > obj.total_quantity
    ):
        raise ValidationError("Completed quantity cannot exceed total quantity")

    # ==========================
    # Auto Status Update
    # ==========================
    if obj.completed_quantity == obj.total_quantity:
        obj.status = "Completed"

    elif obj.completed_quantity is not None and obj.completed_quantity > 0:
        obj.status = "In Progress"

    # ==========================
    # Recalculate Amount
    # ==========================
    obj.total_amount = obj.total_quantity * obj.rate

    try:
        await db.flush()
        await db.refresh(obj)
    except SQLAlchemyError as exc:
        logger.exception(f"Database error updating work order: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Database error updating work order",
        )

    return WorkOrderOut.model_validate(obj)


# ================================================================
# 5. DELETE WORK ORDER
# ================================================================


@router.delete("/{id}")
async def delete_work_order(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("work_orders.delete")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True

    if not is_sa and current_user.company_id is None:
        raise PermissionDeniedError("User does not belong to any company")

    obj = await _get_scoped_work_order(db, id, current_user)

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    try:
        await db.delete(obj)
        await db.flush()
    except SQLAlchemyError as exc:
        logger.exception(f"Database error deleting work order: {exc}")
        raise HTTPException(
            status_code=500,
            detail="Database error deleting work order",
        )

    return {"message": "Deleted successfully"}
