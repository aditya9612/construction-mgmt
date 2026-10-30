import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy import select, func
from sqlalchemy.orm import aliased

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.approval import Approval
from app.schemas.approval import ApprovalCreate, ApprovalOut, ApprovalAction
from app.models.user import User, UserRole
from app.core.dependencies import require_permission
from app.utils.common import assert_project_access
from app.services.notification_service import create_notification
from app.core.enums import DocumentStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["Approvals"])


def _check_tenant_access(current_user: User) -> bool:
    """
    Validates tenant access according to canonical Super Admin and multi-tenant rules.
    - Canonical SA check: getattr(current_user, "is_super_admin", False) is True
    - Non-SA users with company_id=None are strictly denied with HTTP 403.
    Returns True if user is Super Admin, False otherwise.
    """
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User must belong to a company to access approvals.",
        )
    return is_sa


async def verify_approval_entity_access(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    current_user: User,
    is_sa: bool,
):
    """
    Validates existence and tenant ownership of the target entity.
    Foreign/nonexistent target entities are masked with HTTP 404 to prevent enumeration.
    Super Admins are granted cross-company operational access.
    """
    entity_type_lower = entity_type.lower()

    if entity_type_lower == "boq":
        from app.models.boq import BOQ

        entity = await db.get(BOQ, entity_id)
        if not entity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="BOQ not found",
            )
        project_id = entity.project_id

    elif entity_type_lower == "measurement":
        from app.models.final_measurement import FinalMeasurement

        entity = await db.get(FinalMeasurement, entity_id)
        if not entity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Measurement not found",
            )
        project_id = entity.project_id

    elif entity_type_lower == "purchase_order":
        from app.models.material import PurchaseOrder

        entity = await db.get(PurchaseOrder, entity_id)
        if not entity or getattr(entity, "is_deleted", False):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase Order not found",
            )
        project_id = entity.project_id

    elif entity_type_lower == "document":
        from app.models.document import Document

        entity = await db.get(Document, entity_id)
        if not entity or getattr(entity, "is_deleted", False):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            )
        project_id = entity.project_id

    elif entity_type_lower == "drawing":
        from app.models.project import DrawingDocument

        entity = await db.get(DrawingDocument, entity_id)
        if not entity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Drawing not found",
            )
        project_id = entity.project_id

    elif entity_type_lower == "bill":
        from app.models.billing import RABill

        entity = await db.get(RABill, entity_id)
        if not entity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bill not found",
            )
        project_id = entity.project_id

    elif entity_type_lower == "journal_entry":
        from app.models.accountant import JournalEntry

        entity = await db.get(JournalEntry, entity_id)
        if not entity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Journal Entry not found",
            )
        if entity.created_by is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Journal Entry not found",
            )
        owner_user = await db.get(User, entity.created_by)
        if not owner_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Journal Entry not found",
            )
        if not is_sa and owner_user.company_id != current_user.company_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Journal Entry not found",
            )
        return entity

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported entity type: '{entity_type}'. Supported types are: boq, measurement, purchase_order, document, drawing, bill, journal_entry",
        )

    # For project-scoped entities: validate project ownership
    if project_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type} not found",
        )

    from app.models.project import Project

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type} not found",
        )

    if not is_sa and project.company_id != current_user.company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type} not found",
        )

    await assert_project_access(db, project_id=project_id, current_user=current_user)

    return entity


@router.post("", response_model=ApprovalOut)
async def create_approval(
    payload: ApprovalCreate,
    current_user: User = Depends(require_permission("approvals.create")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)

    # Verify entity ownership and existence
    entity = await verify_approval_entity_access(
        db, payload.entity_type, payload.entity_id, current_user, is_sa
    )

    # Prevent duplicate Pending approvals for the same entity
    existing = await db.scalar(
        select(Approval).where(
            func.lower(Approval.entity_type) == payload.entity_type.lower(),
            Approval.entity_id == payload.entity_id,
            Approval.status == "Pending",
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A pending approval request already exists for this entity.",
        )

    obj = Approval(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        requested_by=current_user.id,
        remarks=payload.remarks,
        status="Pending",
    )
    db.add(obj)
    await db.flush()

    entity_type_lower = payload.entity_type.lower()
    if entity_type_lower == "boq":
        entity.approval_status = "Pending"

    elif entity_type_lower == "measurement":
        entity.status = "SUBMITTED"

    elif entity_type_lower == "purchase_order":
        if entity.status not in ["CREATED", "REJECTED"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot submit PO for approval. Current status is {entity.status}",
            )
        entity.status = "PENDING"

    elif entity_type_lower == "document":
        if entity.status not in [DocumentStatus.PENDING, DocumentStatus.REJECTED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot submit document for approval. Current status is {entity.status}",
            )
        entity.status = DocumentStatus.UNDER_REVIEW

    elif entity_type_lower == "drawing":
        entity.approval_status = DocumentStatus.UNDER_REVIEW
        entity.approval_id = obj.id

    try:
        await db.commit()
        await db.refresh(obj)
    except Exception as exc:
        await db.rollback()
        logger.exception(f"Failed to create approval request: {exc}")
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating the approval.",
        )

    out = ApprovalOut.model_validate(obj)
    out.requested_by_name = current_user.full_name
    return out


async def _filter_approvals_for_user(db: AsyncSession, current_user: User, rows: list, is_sa: bool):
    if is_sa or current_user.role in (UserRole.ADMIN.value, UserRole.ACCOUNTANT.value):
        return rows

    from app.models.project import ProjectMember, DrawingDocument
    from app.models.boq import BOQ
    from app.models.final_measurement import FinalMeasurement
    from app.models.material import PurchaseOrder
    from app.models.document import Document
    from app.models.billing import RABill

    user_project_ids = set(
        (
            await db.scalars(
                select(ProjectMember.project_id).where(
                    ProjectMember.user_id == current_user.id
                )
            )
        ).all()
    )

    drawing_ids = [a.entity_id for a, _, _ in rows if a.entity_type == "drawing"]
    boq_ids = [a.entity_id for a, _, _ in rows if a.entity_type == "boq"]
    meas_ids = [a.entity_id for a, _, _ in rows if a.entity_type == "measurement"]
    po_ids = [a.entity_id for a, _, _ in rows if a.entity_type == "purchase_order"]
    doc_ids = [a.entity_id for a, _, _ in rows if a.entity_type == "document"]
    bill_ids = [a.entity_id for a, _, _ in rows if a.entity_type == "bill"]

    entity_project_map = {}
    if drawing_ids:
        res = await db.execute(select(DrawingDocument.id, DrawingDocument.project_id).where(DrawingDocument.id.in_(drawing_ids)))
        for eid, pid in res.all():
            entity_project_map[("drawing", eid)] = pid
    if boq_ids:
        res = await db.execute(select(BOQ.id, BOQ.project_id).where(BOQ.id.in_(boq_ids)))
        for eid, pid in res.all():
            entity_project_map[("boq", eid)] = pid
    if meas_ids:
        res = await db.execute(select(FinalMeasurement.id, FinalMeasurement.project_id).where(FinalMeasurement.id.in_(meas_ids)))
        for eid, pid in res.all():
            entity_project_map[("measurement", eid)] = pid
    if po_ids:
        res = await db.execute(select(PurchaseOrder.id, PurchaseOrder.project_id).where(PurchaseOrder.id.in_(po_ids)))
        for eid, pid in res.all():
            entity_project_map[("purchase_order", eid)] = pid
    if doc_ids:
        res = await db.execute(select(Document.id, Document.project_id).where(Document.id.in_(doc_ids)))
        for eid, pid in res.all():
            entity_project_map[("document", eid)] = pid
    if bill_ids:
        res = await db.execute(select(RABill.id, RABill.project_id).where(RABill.id.in_(bill_ids)))
        for eid, pid in res.all():
            entity_project_map[("bill", eid)] = pid

    filtered_rows = []
    for row in rows:
        approval = row[0]
        if approval.requested_by == current_user.id:
            filtered_rows.append(row)
        else:
            proj_id = entity_project_map.get((approval.entity_type, approval.entity_id))
            if proj_id is not None and proj_id in user_project_ids:
                filtered_rows.append(row)
    return filtered_rows


@router.get("", response_model=list[ApprovalOut])
async def list_approvals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("approvals.view")),
):
    is_sa = _check_tenant_access(current_user)

    Requester = aliased(User, name="requester")
    Approver = aliased(User, name="approver")

    stmt = (
        select(
            Approval,
            Requester.full_name.label("requested_by_name"),
            Approver.full_name.label("approved_by_name"),
        )
        .outerjoin(Requester, Approval.requested_by == Requester.id)
        .outerjoin(Approver, Approval.approved_by == Approver.id)
    )
    if not is_sa:
        stmt = stmt.where(Requester.company_id == current_user.company_id)
    elif current_user.company_id is not None:
        stmt = stmt.where(Requester.company_id == current_user.company_id)

    stmt = stmt.order_by(Approval.id.desc())
    result = await db.execute(stmt)
    rows = result.all()
    rows = await _filter_approvals_for_user(db, current_user, rows, is_sa)

    items = []
    for approval, req_name, app_name in rows:
        items.append(
            ApprovalOut(
                id=approval.id,
                entity_type=approval.entity_type,
                entity_id=approval.entity_id,
                status=approval.status,
                requested_by=approval.requested_by,
                requested_by_name=req_name,
                approved_by=approval.approved_by,
                approved_by_name=app_name,
                remarks=approval.remarks,
            )
        )
    return items


@router.get("/export")
async def export_approvals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("approvals.view")),
):
    is_sa = _check_tenant_access(current_user)

    Requester = aliased(User, name="requester")
    Approver = aliased(User, name="approver")

    stmt = (
        select(
            Approval,
            Requester.full_name.label("requested_by_name"),
            Approver.full_name.label("approved_by_name"),
        )
        .outerjoin(Requester, Approval.requested_by == Requester.id)
        .outerjoin(Approver, Approval.approved_by == Approver.id)
    )
    if not is_sa:
        stmt = stmt.where(Requester.company_id == current_user.company_id)
    elif current_user.company_id is not None:
        stmt = stmt.where(Requester.company_id == current_user.company_id)

    stmt = stmt.order_by(Approval.id.desc())
    result = await db.execute(stmt)
    rows = result.all()
    rows = await _filter_approvals_for_user(db, current_user, rows, is_sa)

    import io
    import csv
    from fastapi.responses import StreamingResponse

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Approval ID",
        "Entity Type",
        "Entity ID",
        "Status",
        "Requested By",
        "Approved By",
        "Remarks",
        "Created At",
    ])
    for approval, req_name, app_name in rows:
        writer.writerow([
            approval.id,
            approval.entity_type,
            approval.entity_id,
            approval.status,
            req_name or f"User #{approval.requested_by}",
            app_name or (f"User #{approval.approved_by}" if approval.approved_by else ""),
            approval.remarks or "",
            approval.created_at.strftime("%Y-%m-%d %H:%M:%S") if approval.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=approvals_export.csv"},
    )


@router.put("/{id}/approve")
async def approve(
    id: int,
    payload: ApprovalAction,
    current_user: User = Depends(require_permission("approvals.approve")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)

    obj = await db.get(Approval, id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval not found",
        )

    # Verify approval tenant ownership
    requester = await db.get(User, obj.requested_by)
    if not requester:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval not found",
        )
    if not is_sa and requester.company_id != current_user.company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval not found",
        )

    # Segregation of duties: requester cannot approve their own request
    if current_user.id == obj.requested_by:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requester cannot approve their own approval request.",
        )

    # State machine: only Pending approvals can be approved
    if obj.status != "Pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve request with status '{obj.status}'. Only 'Pending' requests can be approved.",
        )

    # Verify Target Entity ownership and existence
    entity = await verify_approval_entity_access(
        db, obj.entity_type, obj.entity_id, current_user, is_sa
    )

    entity_type_lower = obj.entity_type.lower()
    if entity_type_lower == "bill":
        entity.status = "Approved"

    elif entity_type_lower == "journal_entry":
        entity.status = "Posted"

    elif entity_type_lower == "boq":
        entity.approval_status = "Approved"

    elif entity_type_lower == "measurement":
        entity.status = "APPROVED"

    elif entity_type_lower == "purchase_order":
        if entity.status != "PENDING":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve PO. Current status is {entity.status}",
            )
        entity.status = "APPROVED"

    elif entity_type_lower == "document":
        entity.status = DocumentStatus.APPROVED

    elif entity_type_lower == "drawing":
        entity.approval_status = DocumentStatus.APPROVED
        entity.approval_id = obj.id

    obj.status = "Approved"
    obj.approved_by = current_user.id
    obj.remarks = payload.remarks

    try:
        # Notification created within the same atomic transaction
        await create_notification(
            db,
            user_id=obj.requested_by,
            title="Approval Granted",
            message=f"Your {obj.entity_type} approval request has been Approved.",
            type="success",
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception(f"Unexpected error committing approval {id}: {exc}")
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the approval.",
        )

    return {"message": "Approved"}


@router.put("/{id}/reject")
async def reject(
    id: int,
    payload: ApprovalAction,
    current_user: User = Depends(require_permission("approvals.approve")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = _check_tenant_access(current_user)

    obj = await db.get(Approval, id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval not found",
        )

    # Verify approval tenant ownership
    requester = await db.get(User, obj.requested_by)
    if not requester:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval not found",
        )
    if not is_sa and requester.company_id != current_user.company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval not found",
        )

    # Segregation of duties: requester cannot reject their own request
    if current_user.id == obj.requested_by:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requester cannot reject their own approval request.",
        )

    # State machine: only Pending approvals can be rejected
    if obj.status != "Pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reject request with status '{obj.status}'. Only 'Pending' requests can be rejected.",
        )

    # Remarks are mandatory for rejection
    if not payload.remarks or not payload.remarks.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Remarks required for rejection",
        )

    # Verify Target Entity ownership and existence
    entity = await verify_approval_entity_access(
        db, obj.entity_type, obj.entity_id, current_user, is_sa
    )

    entity_type_lower = obj.entity_type.lower()
    if entity_type_lower == "bill":
        entity.status = "Rejected"

    elif entity_type_lower == "journal_entry":
        entity.status = "Rejected"

    elif entity_type_lower == "boq":
        entity.approval_status = "Rejected"

    elif entity_type_lower == "measurement":
        entity.status = "REJECTED"

    elif entity_type_lower == "purchase_order":
        if entity.status != "PENDING":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reject PO. Current status is {entity.status}",
            )
        entity.status = "REJECTED"

    elif entity_type_lower == "document":
        entity.status = DocumentStatus.REJECTED

    elif entity_type_lower == "drawing":
        entity.approval_status = DocumentStatus.REJECTED
        entity.approval_id = obj.id

    obj.status = "Rejected"
    obj.approved_by = current_user.id
    obj.remarks = payload.remarks.strip()

    try:
        # Notification created within the same atomic transaction
        await create_notification(
            db,
            user_id=obj.requested_by,
            title="Approval Rejected",
            message=f"Your {obj.entity_type} approval request was Rejected.",
            type="alert",
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception(f"Unexpected error committing rejection {id}: {exc}")
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the rejection.",
        )

    return {"message": "Rejected"}
