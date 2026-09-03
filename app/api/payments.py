from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import get_db_session
from app.core.dependencies import require_roles, get_current_user
from app.models.user import User
from app.schemas.payment_voucher import PaymentVoucherCreate, PaymentVoucherOut
from app.services.payment_service import PaymentService
from app.models.accountant import PaymentVoucher
from app.utils.helpers import InvalidStateError

router = APIRouter(prefix="/payments/vouchers", tags=["Payments & Receipts"])

ACCOUNTANT_WRITE_ROLES = ["Admin", "Accountant", "SuperAdmin"]
ACCOUNTANT_READ_ROLES = ["Admin", "Accountant", "SuperAdmin", "Management"]

@router.post("", response_model=PaymentVoucherOut)
async def create_payment_voucher(
    payload: PaymentVoucherCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES))
):
    try:
        pv = await PaymentService.create_pending_voucher(
            db, 
            current_user, 
            **payload.model_dump()
        )
        return pv
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

from typing import Optional

@router.get("", response_model=list[PaymentVoucherOut])
async def list_payment_vouchers(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES))
):
    from app.models.accountant import VendorBill
    from app.models.project import Project
    from app.models.material import Supplier
    from app.models.contractor import Contractor
    from sqlalchemy.orm import aliased
    from sqlalchemy import case
    
    query = (
        select(
            PaymentVoucher,
            Project.project_name,
            case(
                (PaymentVoucher.party_type == "Supplier", Supplier.supplier_name),
                (PaymentVoucher.party_type == "Contractor", Contractor.name),
                else_=None
            ).label("party_name")
        )
        .outerjoin(VendorBill, PaymentVoucher.vendor_bill_id == VendorBill.id)
        .outerjoin(Project, VendorBill.project_id == Project.id)
        .outerjoin(Supplier, (PaymentVoucher.party_type == "Supplier") & (PaymentVoucher.supplier_id == Supplier.id))
        .outerjoin(Contractor, (PaymentVoucher.party_type == "Contractor") & (PaymentVoucher.contractor_id == Contractor.id))
        .order_by(PaymentVoucher.created_at.desc())
    )
    
    if not current_user.is_super_admin:
        query = query.where(Project.company_id == current_user.company_id)

    if status:
        query = query.where(PaymentVoucher.status == status)

        
    rows = (await db.execute(query)).all()
    
    items = []
    for row in rows:
        pv = row.PaymentVoucher
        pv_dict = pv.__dict__.copy()
        pv_dict["project_name"] = row.project_name
        pv_dict["party_name"] = row.party_name
        items.append(PaymentVoucherOut.model_validate(pv_dict))
        
    return items

@router.post("/{id}/mark-paid", response_model=PaymentVoucherOut)
async def mark_voucher_paid(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES))
):
    try:
        pv = await PaymentService.mark_paid(db, current_user, id)
        return pv
    except InvalidStateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{id}/cancel", response_model=PaymentVoucherOut)
async def cancel_voucher(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_WRITE_ROLES))
):
    try:
        pv = await PaymentService.cancel(db, current_user, id)
        return pv
    except InvalidStateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
