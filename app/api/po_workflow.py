from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.core.dependencies import get_current_active_user, require_permission
from app.models.user import User
from app.schemas.inventory_workflow import (
    PurchaseOrderCreate,
    PurchaseOrderOut,
)
from app.services.po_service import (
    create_purchase_order,
    approve_purchase_order,
    get_purchase_order,
    list_purchase_orders,
)
from app.utils.helpers import NotFoundError

router = APIRouter(prefix="/purchase-orders", tags=["Purchase Orders Workflow"])


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


@router.post("/create", response_model=PurchaseOrderOut, status_code=status.HTTP_201_CREATED)
async def create_po_endpoint(
    payload: PurchaseOrderCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("po.create")),
):
    """Creates a multi-item Purchase Order with single-item backward compatibility."""
    eff_comp = _enforce_company_isolation(current_user, payload.company_id)
    items_dicts = [item.model_dump() for item in payload.items]
    po = await create_purchase_order(
        db=db,
        company_id=eff_comp,
        user_id=current_user.id,
        supplier_id=payload.supplier_id,
        items=items_dicts,
        project_id=payload.project_id,
        is_company_wide=payload.is_company_wide,
        boq_item_id=payload.boq_item_id,
        po_number=payload.po_number,
    )
    return po


@router.post("/{po_id}/approve", response_model=PurchaseOrderOut)
async def approve_po_endpoint(
    po_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("po.approve")),
):
    """Approves a created Purchase Order."""
    po = await get_purchase_order(db=db, po_id=po_id)
    if not po:
        raise NotFoundError(f"Purchase Order {po_id} not found")
    _enforce_company_isolation(current_user, po.company_id)
    return await approve_purchase_order(db=db, po_id=po_id, user_id=current_user.id)


@router.get("/list", response_model=List[PurchaseOrderOut])
async def list_po_endpoint(
    company_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("po.view")),
):
    """Lists purchase orders with filters."""
    eff_comp = _enforce_company_isolation(current_user, company_id)
    return await list_purchase_orders(
        db=db,
        company_id=eff_comp,
        project_id=project_id,
        status=status,
        skip=skip,
        limit=limit,
    )


@router.get("/{po_id}", response_model=PurchaseOrderOut)
async def get_po_endpoint(
    po_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("po.view")),
):
    """Retrieves a single Purchase Order with items."""
    po = await get_purchase_order(db=db, po_id=po_id)
    if not po:
        raise NotFoundError(f"Purchase Order {po_id} not found")
    _enforce_company_isolation(current_user, po.company_id)
    return po
