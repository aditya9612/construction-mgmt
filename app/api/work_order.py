from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.work_order import WorkOrder
from app.models.project import Project
from app.models.contractor import Contractor

from app.schemas.work_order import (
    WorkOrderCreate,
    WorkOrderUpdate,
    WorkOrderOut,
)

from app.utils.helpers import NotFoundError, ValidationError
from app.utils.common import (
    assert_project_access,
    generate_business_id,
    validate_contractor_access,
)

from app.models.user import User, UserRole
from app.core.dependencies import require_roles, get_current_active_user

router = APIRouter(prefix="/work-orders", tags=["Work Orders"])


WORK_ORDER_CREATE_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER]
WORK_ORDER_READ_ROLES = [
    UserRole.ADMIN,
    UserRole.PROJECT_MANAGER,
    UserRole.SITE_ENGINEER,
]


@router.post("", response_model=WorkOrderOut)
async def create_work_order(
    payload: WorkOrderCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(WORK_ORDER_CREATE_ROLES)),
):
    project = await db.get(Project, payload.project_id)
    contractor = await db.get(Contractor, payload.contractor_id)

    if not project:
        raise NotFoundError("Project not found")

    if not contractor:
        raise NotFoundError("Contractor not found")

    await assert_project_access(
        db,
        project_id=payload.project_id,
        current_user=current_user,
    )

    work_order_number = await generate_business_id(
        db, WorkOrder, "work_order_number", "WO"
    )

    total_amount = payload.total_quantity * payload.rate

    obj = WorkOrder(
        **payload.model_dump(),
        work_order_number=work_order_number,
        total_amount=total_amount,
    )

    db.add(obj)
    await db.flush()

    return WorkOrderOut.model_validate(obj)


@router.get("", response_model=list[WorkOrderOut])
async def list_work_orders(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(WORK_ORDER_READ_ROLES)),
):
    query = select(WorkOrder)

    if current_user.role != UserRole.ADMIN:
        query = query.join(Project).join(Project.members).where(
            Project.members.any(user_id=current_user.id)
        )

    result = await db.execute(query.order_by(WorkOrder.id.desc()))
    rows = result.scalars().all()

    return [WorkOrderOut.model_validate(r) for r in rows]


@router.get("/{id}", response_model=WorkOrderOut)
async def get_work_order(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    obj = await db.get(WorkOrder, id)

    if not obj:
        raise NotFoundError("Work order not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    return WorkOrderOut.model_validate(obj)


@router.put("/{id}", response_model=WorkOrderOut)
async def update_work_order(
    id: int,
    payload: WorkOrderUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(WORK_ORDER_CREATE_ROLES)),
):
    obj = await db.get(WorkOrder, id)

    if not obj:
        raise NotFoundError("Work order not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    # validations
    if obj.completed_quantity and obj.completed_quantity > obj.total_quantity:
        raise ValidationError("Completed > total quantity")

    # status auto update
    if obj.completed_quantity == obj.total_quantity:
        obj.status = "Completed"
    elif obj.completed_quantity and obj.completed_quantity > 0:
        obj.status = "In Progress"

    # recalc
    obj.total_amount = obj.total_quantity * obj.rate

    await db.flush()

    return WorkOrderOut.model_validate(obj)


@router.delete("/{id}")
async def delete_work_order(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(WORK_ORDER_CREATE_ROLES)),
):
    obj = await db.get(WorkOrder, id)

    if not obj:
        raise NotFoundError("Work order not found")

    await assert_project_access(
        db,
        project_id=obj.project_id,
        current_user=current_user,
    )

    await db.delete(obj)
    await db.flush()

    return {"message": "Deleted successfully"}