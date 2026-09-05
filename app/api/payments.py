import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, case

from app.db.session import get_db_session
from app.core.dependencies import require_permission
from app.models.user import User
from app.schemas.payment_voucher import PaymentVoucherCreate, PaymentVoucherOut
from app.services.payment_service import PaymentService
from app.models.accountant import PaymentVoucher, VendorBill
from app.models.project import Project
from app.models.material import Supplier
from app.models.contractor import Contractor
from app.utils.helpers import InvalidStateError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments/vouchers", tags=["Payments & Receipts"])


def _check_tenant_access(current_user: User) -> bool:
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=403, detail="User must belong to a company")
    return is_sa


@router.post("", response_model=PaymentVoucherOut)
async def create_payment_voucher(
    payload: PaymentVoucherCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payment_vouchers.create")),
):
    _check_tenant_access(current_user)
    try:
        pv = await PaymentService.create_pending_voucher(
            db,
            current_user,
            **payload.model_dump(),
        )
        return pv
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create payment voucher: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create payment voucher")


@router.get("", response_model=list[PaymentVoucherOut])
async def list_payment_vouchers(
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payment_vouchers.view")),
):
    is_sa = _check_tenant_access(current_user)

    query = (
        select(
            PaymentVoucher,
            Project.project_name,
            case(
                (PaymentVoucher.party_type == "Supplier", Supplier.supplier_name),
                (PaymentVoucher.party_type == "Contractor", Contractor.name),
                else_=None,
            ).label("party_name"),
        )
        .join(VendorBill, PaymentVoucher.vendor_bill_id == VendorBill.id)
        .outerjoin(Project, VendorBill.project_id == Project.id)
        .outerjoin(Supplier, (PaymentVoucher.party_type == "Supplier") & (PaymentVoucher.supplier_id == Supplier.id))
        .outerjoin(Contractor, (PaymentVoucher.party_type == "Contractor") & (PaymentVoucher.contractor_id == Contractor.id))
        .order_by(PaymentVoucher.created_at.desc())
    )

    if not is_sa:
        query = query.where(VendorBill.company_id == current_user.company_id)

    if status:
        query = query.where(PaymentVoucher.status == status)

    try:
        rows = (await db.execute(query)).all()
    except Exception as e:
        logger.exception("Failed to query payment vouchers: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve payment vouchers")

    items = []
    for row in rows:
        if isinstance(row, PaymentVoucher):
            pv = row
            proj_name = None
            party_name = None
        elif hasattr(row, "PaymentVoucher"):
            pv = row.PaymentVoucher
            proj_name = getattr(row, "project_name", None)
            party_name = getattr(row, "party_name", None)
        else:
            pv = row[0]
            proj_name = row[1] if len(row) > 1 else None
            party_name = row[2] if len(row) > 2 else None

        pv_dict = pv.__dict__.copy()
        pv_dict["project_name"] = proj_name
        pv_dict["party_name"] = party_name
        items.append(PaymentVoucherOut.model_validate(pv_dict))

    return items


@router.post("/{id}/mark-paid", response_model=PaymentVoucherOut)
async def mark_voucher_paid(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payment_vouchers.pay")),
):
    _check_tenant_access(current_user)
    try:
        pv = await PaymentService.mark_paid(db, current_user, id)
        return pv
    except (HTTPException, InvalidStateError) as e:
        if isinstance(e, InvalidStateError):
            raise HTTPException(status_code=400, detail=str(e))
        raise
    except Exception as e:
        logger.exception("Failed to mark payment voucher %s paid: %s", id, e)
        raise HTTPException(status_code=500, detail="Failed to process voucher payment")


@router.post("/{id}/cancel", response_model=PaymentVoucherOut)
async def cancel_voucher(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payment_vouchers.delete")),
):
    _check_tenant_access(current_user)
    try:
        pv = await PaymentService.cancel(db, current_user, id)
        return pv
    except (HTTPException, InvalidStateError) as e:
        if isinstance(e, InvalidStateError):
            raise HTTPException(status_code=400, detail=str(e))
        raise
    except Exception as e:
        logger.exception("Failed to cancel payment voucher %s: %s", id, e)
        raise HTTPException(status_code=500, detail="Failed to cancel payment voucher")
