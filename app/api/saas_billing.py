import uuid
import io
import base64
import qrcode
from decimal import Decimal
from datetime import datetime
from urllib.parse import quote_plus
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from typing import Dict, Any, List
from sqlalchemy import select, desc
from app.services.entitlement import get_entitlement_service, EntitlementService
from app.models.subscription import Plan, SubscriptionInvoice, Subscription, ManualPaymentTransaction
from app.models.user import ActivityLog
from app.schemas.saas_billing import (
    PlanOut, 
    SubscriptionSummaryOut, 
    UsageLimitsOut,
    SubscriptionInvoiceOut,
    BillingHistoryOut,
    UPIQRCodeOut,
    UPISubmitRequest,
    UPISubmitResponse
)

from app.db.session import get_db_session
from app.core.dependencies import get_current_active_user, require_tenant_admin
from app.models.user import User
from app.core.config import settings
from app.services.billing.mock_provider import MockPaymentProvider
from app.services.billing.razorpay_provider import RazorpayPaymentProvider
from app.services.billing.billing_service import BillingService

router = APIRouter(prefix="/saas-billing", tags=["SaaS Billing"])


def get_billing_service() -> BillingService:
    if settings.PAYMENT_PROVIDER == "razorpay":
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET or not settings.RAZORPAY_WEBHOOK_SECRET:
            raise HTTPException(status_code=503, detail="Payment provider is not properly configured.")
        provider = RazorpayPaymentProvider()
    else:
        provider = MockPaymentProvider()
    return BillingService(provider)

class CheckoutRequest(BaseModel):
    plan_id: int
    success_url: str
    cancel_url: str

class CheckoutResponse(BaseModel):
    checkout_url: str

@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    request: CheckoutRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    billing_service: BillingService = Depends(get_billing_service)
):
    if not current_user.company_id:
        raise HTTPException(status_code=403, detail="User must belong to a company to create checkout")
        
    checkout_url = await billing_service.create_checkout(
        db=db,
        company_id=current_user.company_id,
        plan_id=request.plan_id,
        user=current_user,
        success_url=request.success_url,
        cancel_url=request.cancel_url
    )
    return {"checkout_url": checkout_url}

@router.post("/webhook")
async def webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    billing_service: BillingService = Depends(get_billing_service)
):
    signature = request.headers.get("X-Mock-Signature") or request.headers.get("X-Razorpay-Signature") or request.headers.get("Stripe-Signature", "")
    payload = await request.body()
    
    return await billing_service.handle_webhook(db=db, payload=payload, signature=signature)


@router.get("/me", response_model=SubscriptionSummaryOut)
async def get_tenant_billing_summary(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    entitlement_service: EntitlementService = Depends(get_entitlement_service)
):
    if not current_user.company_id:
        raise HTTPException(status_code=403, detail="User must belong to a company")
        
    entitlements = await entitlement_service.get_company_entitlements(db, current_user.company_id)
    return entitlements

@router.get("/usage", response_model=UsageLimitsOut)
async def get_tenant_usage_limits(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    entitlement_service: EntitlementService = Depends(get_entitlement_service)
):
    if not current_user.company_id:
        raise HTTPException(status_code=403, detail="User must belong to a company")
        
    limits = await entitlement_service.get_limits(db, current_user.company_id)
    return limits

@router.get("/plans", response_model=List[PlanOut])
async def list_active_plans(
    db: AsyncSession = Depends(get_db_session)
):
    # Publicly visible active plans, safely scoped
    result = await db.execute(select(Plan).where(Plan.is_active == True))
    plans = result.scalars().all()
    return plans

@router.get("/invoices", response_model=List[SubscriptionInvoiceOut])
async def list_invoices(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session)
):
    if not current_user.company_id:
        raise HTTPException(status_code=403, detail="User must belong to a company")
        
    result = await db.execute(
        select(SubscriptionInvoice)
        .where(SubscriptionInvoice.company_id == current_user.company_id)
        .order_by(desc(SubscriptionInvoice.created_at))
    )
    return result.scalars().all()

@router.get("/invoices/{invoice_id}", response_model=SubscriptionInvoiceOut)
async def get_invoice_detail(
    invoice_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session)
):
    if not current_user.company_id:
        raise HTTPException(status_code=403, detail="User must belong to a company")
        
    result = await db.execute(
        select(SubscriptionInvoice)
        .where(
            SubscriptionInvoice.id == invoice_id,
            SubscriptionInvoice.company_id == current_user.company_id
        )
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
        
    return invoice

@router.get("/history", response_model=List[BillingHistoryOut])
async def get_billing_history(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session)
):
    if not current_user.company_id:
        raise HTTPException(status_code=403, detail="User must belong to a company")
        
    # Find the subscription for the company to properly query ActivityLogs
    sub_res = await db.execute(select(Subscription).where(Subscription.company_id == current_user.company_id))
    subscription = sub_res.scalar_one_or_none()
    
    if not subscription:
        return []
        
    # We want ActivityLogs for the Subscription entity.
    # We might also want ActivityLogs for the Invoices, but we can query them separately or just stick to Subscription logs (which cover plan changes, payment succeeded/failed).
    result = await db.execute(
        select(ActivityLog)
        .where(
            ActivityLog.entity == "Subscription",
            ActivityLog.entity_id == subscription.id
        )
        .order_by(desc(ActivityLog.created_at))
    )
    return result.scalars().all()


@router.get("/upi/qr-code", response_model=UPIQRCodeOut)
async def generate_subscription_upi_qr(
    plan_id: int,
    current_user: User = Depends(require_tenant_admin),
    db: AsyncSession = Depends(get_db_session),
):
    # 1. Authoritative active plan
    result = await db.execute(select(Plan).where(Plan.id == plan_id, Plan.is_active == True))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Active plan not found")

    # 2. Authoritative tenant subscription
    sub_res = await db.execute(select(Subscription).where(Subscription.company_id == current_user.company_id))
    subscription = sub_res.scalar_one_or_none()
    if not subscription:
        raise HTTPException(status_code=404, detail="Tenant subscription not found")

    # 3. Server-authoritative transaction creation
    txn_ref = f"TXN-UPI-{current_user.company_id}-{int(datetime.utcnow().timestamp())}-{uuid.uuid4().hex[:6].upper()}"
    amount_dec = Decimal(str(plan.price))

    txn = ManualPaymentTransaction(
        company_id=current_user.company_id,
        subscription_id=subscription.id,
        plan_id=plan.id,
        amount=amount_dec,
        currency=plan.currency,
        payment_method="UPI",
        transaction_reference=txn_ref,
        status="pending",
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)

    # 4. Generate UPI URI & QR Code
    upi_uri = (
        f"upi://pay?"
        f"pa={settings.SUPER_ADMIN_UPI_ID}"
        f"&pn={quote_plus(settings.SUPER_ADMIN_PAYEE_NAME)}"
        f"&am={plan.price:.2f}"
        f"&cu={plan.currency}"
        f"&tr={txn_ref}"
        f"&tn={quote_plus(f'InfraPilot SaaS - {plan.name}')}"
    )

    qr_b64 = None
    try:
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(upi_uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = io.BytesIO()
        img.save(buffered)
        qr_b64 = f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"
    except Exception:
        qr_b64 = None

    return UPIQRCodeOut(
        transaction_reference=txn_ref,
        plan_id=plan.id,
        plan_name=plan.name,
        amount=float(plan.price),
        currency=plan.currency,
        upi_id=settings.SUPER_ADMIN_UPI_ID,
        upi_name=settings.SUPER_ADMIN_PAYEE_NAME,
        upi_uri=upi_uri,
        qr_code_base64=qr_b64,
        status="pending",
    )


@router.post("/upi/submit", response_model=UPISubmitResponse)
async def submit_subscription_upi_utr(
    request: UPISubmitRequest,
    current_user: User = Depends(require_tenant_admin),
    db: AsyncSession = Depends(get_db_session),
):
    utr = request.utr_reference.strip()
    if not utr or len(utr) < 6 or len(utr) > 50 or not utr.isalnum():
        raise HTTPException(
            status_code=400,
            detail="Invalid UTR reference format. Must be 6-50 alphanumeric characters.",
        )

    # 1. Authoritative transaction lookup strictly scoped by company_id
    result = await db.execute(
        select(ManualPaymentTransaction).where(
            ManualPaymentTransaction.transaction_reference == request.transaction_reference,
            ManualPaymentTransaction.company_id == current_user.company_id,
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(
            status_code=404,
            detail="Payment transaction not found for this tenant",
        )

    if txn.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Transaction cannot accept UTR submission as it is already {txn.status}",
        )

    # 2. Check for duplicate UTR usage across all transactions
    existing_utr = await db.scalar(
        select(ManualPaymentTransaction).where(
            ManualPaymentTransaction.utr_reference == utr,
            ManualPaymentTransaction.id != txn.id,
        )
    )
    if existing_utr:
        raise HTTPException(
            status_code=400,
            detail="UTR reference has already been submitted or is in use.",
        )

    # 3. Update transaction record
    txn.utr_reference = utr
    txn.submitted_at = datetime.utcnow()

    # 4. Safe ActivityLog entry
    log = ActivityLog(
        performed_by=current_user.id,
        action="UPI_PAYMENT_SUBMITTED",
        entity="ManualPaymentTransaction",
        entity_id=txn.id,
        details={
            "company_id": current_user.company_id,
            "transaction_reference": txn.transaction_reference,
            "utr_reference": utr,
            "amount": float(txn.amount),
            "currency": txn.currency,
            "plan_id": txn.plan_id,
        },
    )
    db.add(log)

    try:
        await db.commit()
        await db.refresh(txn)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="UTR reference has already been submitted or is in use.",
        )

    # HARD SECURITY INVARIANT:
    # Status remains "pending". No subscription activation, no invoice marking, no entitlement changes.
    return UPISubmitResponse(
        transaction_reference=txn.transaction_reference,
        utr_reference=utr,
        status="pending",
        amount=float(txn.amount),
        currency=txn.currency,
        submitted_at=txn.submitted_at,
        message="Payment reference submitted successfully. Pending Super Admin verification.",
    )


