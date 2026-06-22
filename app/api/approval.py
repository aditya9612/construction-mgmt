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

    elif payload.entity_type == "measurement":
        from app.models.final_measurement import FinalMeasurement
        measurement = await db.get(FinalMeasurement, payload.entity_id)
        if measurement:
            measurement.status = "SUBMITTED"

    elif payload.entity_type == "purchase_order":
        from app.models.material import PurchaseOrder
        po = await db.get(PurchaseOrder, payload.entity_id)
        if not po:
            raise ValidationError("Purchase Order not found")
        if po.status not in ["CREATED", "REJECTED"]:
            raise ValidationError(f"Cannot submit PO for approval. Current status is {po.status}")
        po.status = "PENDING"

    elif payload.entity_type == "document":
        from app.models.document import Document
        from app.core.enums import DocumentStatus
        
        doc = await db.get(Document, payload.entity_id)
        if not doc:
            raise ValidationError("Document not found")
        if doc.status not in [DocumentStatus.PENDING, DocumentStatus.REJECTED]:
            raise ValidationError(f"Cannot submit document for approval. Current status is {doc.status}")
        doc.status = DocumentStatus.UNDER_REVIEW

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

    elif obj.entity_type == "measurement":
        from app.models.final_measurement import FinalMeasurement
        measurement = await db.get(FinalMeasurement, obj.entity_id)
        if measurement:
            measurement.status = "APPROVED"

    elif obj.entity_type == "purchase_order":
        from app.models.material import PurchaseOrder
        po = await db.get(PurchaseOrder, obj.entity_id)
        if po:
            if po.status != "PENDING":
                raise ValidationError(f"Cannot approve PO. Current status is {po.status}")
            po.status = "APPROVED"

    elif obj.entity_type == "document":
        from app.models.document import Document
        from app.core.enums import DocumentStatus
        
        doc = await db.get(Document, obj.entity_id)
        if doc:
            doc.status = DocumentStatus.APPROVED

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

    elif obj.entity_type == "measurement":
        from app.models.final_measurement import FinalMeasurement
        measurement = await db.get(FinalMeasurement, obj.entity_id)
        if measurement:
            measurement.status = "REJECTED"

    elif obj.entity_type == "purchase_order":
        from app.models.material import PurchaseOrder
        po = await db.get(PurchaseOrder, obj.entity_id)
        if po:
            if po.status != "PENDING":
                raise ValidationError(f"Cannot reject PO. Current status is {po.status}")
            po.status = "REJECTED"

    elif obj.entity_type == "document":
        from app.models.document import Document
        from app.core.enums import DocumentStatus
        
        doc = await db.get(Document, obj.entity_id)
        if doc:
            doc.status = DocumentStatus.REJECTED

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