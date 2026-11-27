from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.core.dependencies import get_current_active_user, require_permission
from app.models.user import User
from app.schemas.inventory_workflow import (
    CancellationOTPRequest,
    CancellationOTPVerify,
    CancellationRequestOut,
)
from app.services.cancellation_service import (
    request_cancellation_otp,
    verify_and_execute_cancellation,
)

router = APIRouter(prefix="/cancellation", tags=["Document OTP Cancellation"])


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


@router.post("/request-otp", status_code=status.HTTP_201_CREATED)
async def request_otp_endpoint(
    payload: CancellationOTPRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("transaction.cancel.request")),
):
    """
    Maker step: Requests a cancellation OTP for PO, GRN, MIN, or Daily Stock.
    Dispatches cryptographic OTP securely out-of-band to designated checkers.
    """
    eff_comp = _enforce_company_isolation(current_user, payload.company_id)
    req, _raw_otp = await request_cancellation_otp(
        db=db,
        company_id=eff_comp,
        user_id=current_user.id,
        document_type=payload.document_type,
        document_id=payload.document_id,
        reason=payload.reason,
    )
    return {
        "message": "Cancellation OTP requested successfully and dispatched to authorized checkers.",
        "request_id": req.id,
        "document_type": req.document_type,
        "document_id": req.document_id,
        "document_number": req.document_number,
        "expires_at": req.expires_at.isoformat(),
        "status": req.status,
    }


@router.post("/verify-otp", response_model=CancellationRequestOut)
async def verify_otp_endpoint(
    payload: CancellationOTPVerify,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("transaction.cancel.approve")),
):
    """
    Checker step: Verifies keyed HMAC-SHA256 OTP and executes atomic stock reversal.
    Strict 2-person rule enforced: Checker cannot be the Maker.
    """
    eff_comp = _enforce_company_isolation(current_user, payload.company_id)
    return await verify_and_execute_cancellation(
        db=db,
        company_id=eff_comp,
        user_id=current_user.id,
        request_id=payload.request_id,
        otp=payload.otp,
    )
