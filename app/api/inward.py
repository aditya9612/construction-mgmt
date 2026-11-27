from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.core.dependencies import get_current_active_user, require_permission
from app.models.user import User
from app.schemas.inventory_workflow import (
    GoodsReceiptNoteCreate,
    GoodsReceiptNoteQCPayload,
    GoodsReceiptNoteOut,
)
from app.services.inward_service import (
    create_goods_receipt_note,
    inspect_and_post_grn,
    get_goods_receipt_note,
    list_goods_receipt_notes,
)
from app.utils.helpers import NotFoundError

router = APIRouter(prefix="/inward", tags=["Inward / Goods Receipt Note"])


def _enforce_company_isolation(current_user: User, target_company_id: Optional[int]) -> int:
    """Area F: Strict Tenant Isolation Gatekeeper."""
    if getattr(current_user, "is_super_admin", False):
        return target_company_id or current_user.company_id or 1
    if target_company_id is not None and current_user.company_id != target_company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Cross-company access is strictly prohibited",
        )
    return current_user.company_id


@router.post("/grn/create", response_model=GoodsReceiptNoteOut, status_code=status.HTTP_201_CREATED)
async def create_grn_endpoint(
    payload: GoodsReceiptNoteCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("inward.create")),
):
    """Step 1: Creates a Goods Receipt Note (GRN) in DRAFT status."""
    eff_comp = _enforce_company_isolation(current_user, payload.company_id)
    if payload.purchase_order_id is None and (not payload.remarks or not payload.remarks.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Direct Inward (GRN without Purchase Order) requires mandatory justification remarks.",
        )
    items_dicts = [item.model_dump() for item in payload.items]
    grn = await create_goods_receipt_note(
        db=db,
        company_id=eff_comp,
        user_id=current_user.id,
        project_id=payload.project_id,
        supplier_id=payload.supplier_id,
        items=items_dicts,
        purchase_order_id=payload.purchase_order_id,
        challan_number=payload.challan_number,
        challan_date=payload.challan_date,
        vehicle_number=payload.vehicle_number,
        idempotency_key=payload.idempotency_key,
        remarks=payload.remarks,
    )
    return grn


@router.post("/grn/{grn_id}/qc-post", response_model=GoodsReceiptNoteOut)
async def qc_post_grn_endpoint(
    grn_id: int,
    payload: GoodsReceiptNoteQCPayload,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("inward.post")),
):
    """Step 2: QC Inspection and Posting: updates inventory, batches, and MWA valuation."""
    grn = await get_goods_receipt_note(db=db, grn_id=grn_id)
    if not grn:
        raise NotFoundError(f"GRN {grn_id} not found")
    _enforce_company_isolation(current_user, grn.company_id)

    qc_dicts = [item.model_dump() for item in payload.qc_items]
    return await inspect_and_post_grn(
        db=db,
        grn_id=grn_id,
        user_id=current_user.id,
        qc_items=qc_dicts,
    )


@router.get("/grn/list", response_model=List[GoodsReceiptNoteOut])
async def list_grn_endpoint(
    company_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("inward.view")),
):
    """Lists GRNs with filters."""
    eff_comp = _enforce_company_isolation(current_user, company_id)
    return await list_goods_receipt_notes(
        db=db,
        company_id=eff_comp,
        project_id=project_id,
        status=status,
        skip=skip,
        limit=limit,
    )


@router.get("/grn/{grn_id}", response_model=GoodsReceiptNoteOut)
async def get_grn_endpoint(
    grn_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("inward.view")),
):
    """Retrieves a single GRN with items."""
    grn = await get_goods_receipt_note(db=db, grn_id=grn_id)
    if not grn:
        raise NotFoundError(f"GRN {grn_id} not found")
    _enforce_company_isolation(current_user, grn.company_id)
    return grn
