from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.approval import Approval
from app.schemas.approval import ApprovalCreate, ApprovalOut, ApprovalAction

from app.models.user import User, UserRole
from app.core.dependencies import get_current_active_user, require_roles

from app.utils.helpers import NotFoundError, ValidationError
from app.services.notification_service import create_notification

APPROVAL_ROLES = [role.value for role in UserRole]

router = APIRouter(prefix="/approvals", tags=["Approvals"])


@router.post("", response_model=ApprovalOut)
async def create_approval(
    payload: ApprovalCreate,
    current_user: User = Depends(require_roles(APPROVAL_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    existing = await db.scalar(
        select(Approval).where(
            Approval.entity_type == payload.entity_type,
            Approval.entity_id == payload.entity_id,
            Approval.status == "Pending",
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

    if payload.entity_type == "boq":
        from app.models.boq import BOQ

        boq = await db.get(BOQ, payload.entity_id)

        if boq:
            boq.approval_status = "Pending"

    elif obj.entity_type == "drawing":
        from app.models.project import DrawingDocument
        from app.core.enums import DocumentStatus

        drawing = await db.get(
            DrawingDocument,
            obj.entity_id
        )

        if drawing:
            drawing.approval_status = DocumentStatus.UNDER_REVIEW
            drawing.approval_id = obj.id
            
    await db.flush()
    await db.commit()
    await db.refresh(obj)

    return ApprovalOut.model_validate(obj)

@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(APPROVAL_ROLES)),
):
    result = await db.execute(select(Approval).order_by(Approval.id.desc()))
    rows = result.scalars().all()

    return [ApprovalOut.model_validate(r) for r in rows]


@router.put("/{id}/approve")
async def approve(
    id: int,
    payload: ApprovalAction,
    current_user: User = Depends(require_roles(APPROVAL_ROLES)),
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

    elif obj.entity_type == "boq":
        from app.models.boq import BOQ

        boq = await db.get(BOQ, obj.entity_id)

        if boq:
            boq.approval_status = "Approved"

    elif obj.entity_type == "drawing":
        from app.models.project import DrawingDocument
        from app.core.enums import DocumentStatus

        drawing = await db.get(
            DrawingDocument,
            obj.entity_id
        )

        if drawing:
            drawing.approval_status = DocumentStatus.APPROVED
            drawing.approval_id = obj.id

    await db.flush()
    await db.commit()
    
    await create_notification(
        db,
        user_id=obj.requested_by,
        title="Approval Granted",
        message=f"Your {obj.entity_type} approval request has been Approved.",
        type="success"
    )
    await db.commit()
    
    return {"message": "Approved"}


@router.put("/{id}/reject")
async def reject(
    id: int,
    payload: ApprovalAction,
    current_user: User = Depends(require_roles(APPROVAL_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(Approval, id)
    if not obj:
        raise NotFoundError("Approval not found")

    if obj.status == "Rejected":
        raise ValidationError("Already rejected")


    if not payload.remarks:
        raise ValidationError(
            "Remarks required for rejection"
        )

    obj.status = "Rejected"
    obj.approved_by = current_user.id
    obj.remarks = payload.remarks

    if obj.entity_type == "bill":
        from app.models.billing import RABill

        bill = await db.get(RABill, obj.entity_id)
        if bill:
            bill.status = "Rejected"

    elif obj.entity_type == "boq":
        from app.models.boq import BOQ

        boq = await db.get(BOQ, obj.entity_id)

        if boq:
            boq.approval_status = "Rejected"

    elif obj.entity_type == "drawing":
        from app.models.project import DrawingDocument
        from app.core.enums import DocumentStatus

        drawing = await db.get(
            DrawingDocument,
            obj.entity_id
        )

        if drawing:
            drawing.approval_status = DocumentStatus.REJECTED
            drawing.approval_id = obj.id

    await db.flush()
    await db.commit()
    
    await create_notification(
        db,
        user_id=obj.requested_by,
        title="Approval Rejected",
        message=f"Your {obj.entity_type} approval request was Rejected.",
        type="alert"
    )
    await db.commit()
    
    return {"message": "Rejected"}