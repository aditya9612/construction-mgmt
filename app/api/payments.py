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

@router.get("", response_model=list[PaymentVoucherOut])
async def list_payment_vouchers(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(ACCOUNTANT_READ_ROLES))
):
    result = await db.scalars(select(PaymentVoucher).order_by(PaymentVoucher.created_at.desc()))
    return result.all()

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
