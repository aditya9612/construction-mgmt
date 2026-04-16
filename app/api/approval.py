from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.approval import Approval
from app.schemas.approval import ApprovalCreate, ApprovalOut, ApprovalAction

from app.models.user import User
from app.core.dependencies import get_current_active_user, require_roles

from app.utils.helpers import NotFoundError, ValidationError

router = APIRouter(prefix="/approvals", tags=["Approvals"])


@router.post("", response_model=ApprovalOut)
async def create_approval(
    payload: ApprovalCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    existing = await db.scalar(
        select(Approval).where(
            Approval.entity_type == payload.entity_type,
            Approval.entity_id == payload.entity_id,
        )
    )
    if existing:
        raise ValidationError("Approval already exists")

    obj = Approval(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        requested_by=current_user.id,
        remarks=payload.remarks,
        status="Pending",
    )

    db.add(obj)
    await db.flush()

    return ApprovalOut.model_validate(obj)

@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(select(Approval).order_by(Approval.id.desc()))
    rows = result.scalars().all()

    return [ApprovalOut.model_validate(r) for r in rows]


@router.put("/{id}/approve")
async def approve(
    id: int,
    payload: ApprovalAction,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(Approval, id)
    if not obj:
        raise NotFoundError("Approval not found")

    if obj.status == "Approved":
        raise ValidationError("Already approved")

    obj.status = "Approved"
    obj.approved_by = current_user.id
    obj.remarks = payload.remarks

    if obj.entity_type == "bill":
        from app.models.billing import RABill

        bill = await db.get(RABill, obj.entity_id)
        if bill:
            bill.status = "Approved"

    await db.flush()

    return {"message": "Approved"}


@router.put("/{id}/reject")
async def reject(
    id: int,
    payload: ApprovalAction,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(Approval, id)
    if not obj:
        raise NotFoundError("Approval not found")

    if obj.status == "Rejected":
        raise ValidationError("Already rejected")

    obj.status = "Rejected"
    obj.approved_by = current_user.id
    obj.remarks = payload.remarks

    if obj.entity_type == "bill":
        from app.models.billing import RABill

        bill = await db.get(RABill, obj.entity_id)
        if bill:
            bill.status = "Rejected"

    await db.flush()

    return {"message": "Rejected"}