import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from fastapi import HTTPException

from app.models.subscription import Subscription, Plan, SubscriptionInvoice, BillingWebhookEvent
from app.models.company import Company
from app.models.user import User, ActivityLog
from app.services.billing.provider_base import PaymentProviderInterface

logger = logging.getLogger(__name__)


class BillingService:
    def __init__(self, provider: PaymentProviderInterface):
        self.provider = provider

    async def create_checkout(
        self,
        db: AsyncSession,
        company_id: int,
        plan_id: int,
        user: User,
        success_url: str,
        cancel_url: str,
    ) -> str:
        company = await db.get(Company, company_id)
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")

        plan = await db.get(Plan, plan_id)
        if not plan or not plan.is_active:
            raise HTTPException(status_code=400, detail="Invalid or inactive plan")

        result = await db.execute(
            select(Subscription).where(Subscription.company_id == company_id)
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            raise HTTPException(
                status_code=400, detail="Subscription record missing for company"
            )

        if not subscription.external_customer_id:
            customer_id = await self.provider.create_customer(
                company_id=company_id, company_name=company.name, user_email=user.email
            )
            subscription.external_customer_id = customer_id
            await db.commit()
            await db.refresh(subscription)

        checkout_url = await self.provider.create_checkout_session(
            company_id=company_id,
            plan_id=plan_id,
            customer_id=subscription.external_customer_id,
            price=plan.price,
            currency=plan.currency,
            success_url=success_url,
            cancel_url=cancel_url,
        )

        log = ActivityLog(
            action="BILLING_CHECKOUT_CREATED",
            entity="Subscription",
            entity_id=subscription.id,
            performed_by=user.id,
            details={"provider": self.provider.provider_name}
        )
        db.add(log)
        await db.commit()

        return checkout_url

    async def _resolve_company_id(self, db: AsyncSession, event_data: dict) -> Optional[int]:
        data = event_data.get("data", {}).get("object", {})
        customer_id = data.get("customer")
        subscription_id = data.get("subscription")
        invoice_number = data.get("invoice_number")

        if customer_id:
            sub_comp = await db.scalar(
                select(Subscription.company_id).where(
                    Subscription.external_customer_id == customer_id
                )
            )
            if sub_comp:
                return sub_comp

        if subscription_id:
            sub_comp = await db.scalar(
                select(Subscription.company_id).where(
                    Subscription.external_subscription_id == subscription_id
                )
            )
            if sub_comp:
                return sub_comp

        if invoice_number:
            inv_comp = await db.scalar(
                select(SubscriptionInvoice.company_id).where(
                    SubscriptionInvoice.invoice_number == invoice_number
                )
            )
            if inv_comp:
                return inv_comp

        return None

    async def handle_webhook(
        self, db: AsyncSession, payload: bytes, signature: str
    ) -> Dict[str, Any]:
        """
        Idempotently process an incoming provider webhook.
        """
        if not self.provider.verify_webhook_signature(payload, signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

        try:
            event_data = json.loads(payload)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        event_data = self.provider.normalize_webhook_event(event_data)
        event_id = event_data.get("id")
        event_type = event_data.get("type")

        if not event_id or not event_type:
            raise HTTPException(
                status_code=400, detail="Malformed webhook: missing event id or type"
            )

        existing_event = await db.execute(
            select(BillingWebhookEvent).where(
                and_(
                    BillingWebhookEvent.provider == self.provider.provider_name,
                    BillingWebhookEvent.event_id == event_id,
                )
            )
        )
        if existing_event.scalar_one_or_none():
            logger.info(f"Webhook {event_id} already processed. Skipping.")
            return {"status": "success", "message": "already processed"}

        try:
            resolved_company_id = await self._resolve_company_id(db, event_data)

            if event_type == "invoice.payment_succeeded":
                await self._handle_payment_succeeded(db, event_data)
            elif event_type == "invoice.payment_failed":
                await self._handle_payment_failed(db, event_data)
            elif event_type == "customer.subscription.deleted":
                await self._handle_subscription_deleted(db, event_data)
            elif event_type in ("refund.processed", "invoice.payment_refunded"):
                await self._handle_refund_processed(db, event_data)

            webhook_event = BillingWebhookEvent(
                company_id=resolved_company_id,
                provider=self.provider.provider_name,
                event_id=event_id,
                event_type=event_type,
                payload_summary=event_data,
                status="processed",
            )
            db.add(webhook_event)
            
            log = ActivityLog(
                action="BILLING_WEBHOOK_PROCESSED",
                entity="BillingWebhookEvent",
                performed_by=None,
                details={"event_id": event_id, "event_type": event_type, "provider": self.provider.provider_name}
            )
            db.add(log)
            await db.commit()
            return {"status": "success"}

        except Exception as e:
            await db.rollback()
            logger.error(f"Error processing webhook {event_id}: {e}")
            
            # If this was a concurrent duplicate, the unique constraint (provider, event_id) will throw IntegrityError
            from sqlalchemy.exc import IntegrityError
            if isinstance(e, IntegrityError):
                logger.info(f"Concurrent duplicate webhook {event_id} rejected safely via IntegrityError.")
                return {"status": "success", "message": "already processed concurrently"}

            webhook_event = BillingWebhookEvent(
                provider=self.provider.provider_name,
                event_id=event_id,
                event_type=event_type,
                payload_summary=event_data,
                status="failed",
                error_message=str(e),
            )
            db.add(webhook_event)
            
            log = ActivityLog(
                action="BILLING_WEBHOOK_REJECTED",
                entity="BillingWebhookEvent",
                performed_by=None,
                details={"event_id": event_id, "event_type": event_type, "error": str(e), "provider": self.provider.provider_name}
            )
            db.add(log)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                
            raise HTTPException(
                status_code=500, detail="Internal server error processing webhook"
            )

    def _extract_event_timestamp(self, event_data: dict) -> Optional[datetime]:
        data = event_data.get("data", {}).get("object", {}) if isinstance(event_data.get("data"), dict) else {}
        raw_ts = (
            event_data.get("created_at")
            or event_data.get("created")
            or data.get("created_at")
            or data.get("created")
            or data.get("paid_at")
        )
        if raw_ts is None:
            return None
        if isinstance(raw_ts, (int, float)):
            try:
                return datetime.utcfromtimestamp(raw_ts)
            except Exception:
                return None
        if isinstance(raw_ts, str):
            try:
                return datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass
            try:
                return datetime.utcfromtimestamp(float(raw_ts))
            except Exception:
                return None
        if isinstance(raw_ts, datetime):
            return raw_ts.replace(tzinfo=None)
        return None

    async def _handle_payment_succeeded(self, db: AsyncSession, event_data: dict):
        data = event_data.get("data", {}).get("object", {})
        customer_id = data.get("customer")

        if not customer_id:
            return

        result = await db.execute(
            select(Subscription).where(
                Subscription.external_customer_id == customer_id
            )
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            logger.warning(f"Subscription not found for customer {customer_id}")
            return
            
        if subscription.status in ("cancelled", "expired"):
            logger.warning(f"Ignoring stale payment for {subscription.status} subscription {subscription.id}")
            return

        event_ts = self._extract_event_timestamp(event_data)
        
        # Out-of-order protection: If subscription is past_due, ensure this success event is not older
        # than the latest failed invoice timestamp or latest failure ActivityLog.
        if subscription.status == "past_due" and event_ts is not None:
            latest_failure_ts = await db.scalar(
                select(func.max(SubscriptionInvoice.created_at)).where(
                    SubscriptionInvoice.subscription_id == subscription.id,
                    SubscriptionInvoice.status == "failed"
                )
            )
            if not latest_failure_ts:
                latest_failure_ts = await db.scalar(
                    select(func.max(ActivityLog.created_at)).where(
                        ActivityLog.entity == "Subscription",
                        ActivityLog.entity_id == subscription.id,
                        ActivityLog.action == "SUBSCRIPTION_PAYMENT_FAILED"
                    )
                )
            if latest_failure_ts:
                fail_ts = latest_failure_ts.replace(tzinfo=None) if latest_failure_ts.tzinfo else latest_failure_ts
                if event_ts < fail_ts:
                    logger.warning(
                        f"Ignoring stale payment success ({event_ts}) for past_due subscription {subscription.id} "
                        f"with newer failure ({fail_ts})"
                    )
                    return

        old_status = subscription.status
        subscription.status = "active"
        subscription.external_subscription_id = data.get("subscription")
        
        # Safely upgrade/downgrade plan
        plan_id = data.get("plan_id")
        plan_changed = False
        old_plan_id = subscription.plan_id
        active_plan = None
        if plan_id:
            try:
                plan_id_int = int(plan_id)
                if plan_id_int != subscription.plan_id:
                    plan_res = await db.execute(select(Plan).where(Plan.id == plan_id_int, Plan.is_active == True))
                    active_plan = plan_res.scalar_one_or_none()
                    if active_plan:
                        subscription.plan_id = plan_id_int
                        plan_changed = True
            except ValueError:
                pass

        invoice_id = data.get("invoice_number")
        paid_timestamp = event_ts or datetime.utcnow()
        if invoice_id:
            inv_res = await db.execute(
                select(SubscriptionInvoice).where(
                    and_(
                        SubscriptionInvoice.invoice_number == invoice_id,
                        SubscriptionInvoice.company_id == subscription.company_id,
                    )
                )
            )
            invoice = inv_res.scalar_one_or_none()
            if invoice:
                invoice.status = "paid"
                invoice.paid_at = paid_timestamp
            else:
                # Retroactive invoice creation
                amount = 0
                currency = "INR"
                if active_plan:
                    amount = active_plan.price
                    currency = active_plan.currency
                else:
                    curr_plan = await db.get(Plan, subscription.plan_id)
                    if curr_plan:
                        amount = curr_plan.price
                        currency = curr_plan.currency
                        
                new_inv = SubscriptionInvoice(
                    company_id=subscription.company_id,
                    subscription_id=subscription.id,
                    invoice_number=invoice_id,
                    status="paid",
                    paid_at=paid_timestamp,
                    total_amount=amount,
                    subtotal=amount,
                    currency=currency,
                    created_at=paid_timestamp
                )
                db.add(new_inv)

        log_action = "SUBSCRIPTION_RENEWED" if (old_status == "active" and not plan_changed) else "SUBSCRIPTION_PAYMENT_SUCCEEDED"
        log = ActivityLog(
            action=log_action,
            entity="Subscription",
            entity_id=subscription.id,
            performed_by=None,
            details={"invoice_id": invoice_id, "provider": self.provider.provider_name}
        )
        db.add(log)
        
        if plan_changed:
            plan_log = ActivityLog(
                action="PLAN_CHANGED",
                entity="Subscription",
                entity_id=subscription.id,
                performed_by=None,
                details={"old_plan_id": old_plan_id, "new_plan_id": subscription.plan_id}
            )
            db.add(plan_log)

    async def _handle_payment_failed(self, db: AsyncSession, event_data: dict):
        data = event_data.get("data", {}).get("object", {})
        customer_id = data.get("customer")
        invoice_id = data.get("invoice_number")

        if not customer_id:
            return

        result = await db.execute(
            select(Subscription).where(
                Subscription.external_customer_id == customer_id
            )
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            logger.warning(f"Subscription not found for customer {customer_id}")
            return

        if subscription.status in ("cancelled", "expired"):
            logger.warning(f"Ignoring stale payment failure for {subscription.status} subscription {subscription.id}")
            return

        event_ts = self._extract_event_timestamp(event_data)

        # 1. Invoice-level check: if the invoice is already paid, this is an out-of-order failure event
        if invoice_id:
            inv_res = await db.execute(
                select(SubscriptionInvoice).where(
                    and_(
                        SubscriptionInvoice.invoice_number == invoice_id,
                        SubscriptionInvoice.company_id == subscription.company_id,
                    )
                )
            )
            invoice = inv_res.scalar_one_or_none()
            if invoice:
                if invoice.status == "paid":
                    logger.warning(
                        f"Ignoring payment failure for already-paid invoice {invoice_id} "
                        f"on subscription {subscription.id} (out-of-order event)."
                    )
                    return
                invoice.status = "failed"
            else:
                curr_plan = await db.get(Plan, subscription.plan_id)
                amount = curr_plan.price if curr_plan else 0
                currency = curr_plan.currency if curr_plan else "INR"
                new_inv = SubscriptionInvoice(
                    company_id=subscription.company_id,
                    subscription_id=subscription.id,
                    invoice_number=invoice_id,
                    status="failed",
                    total_amount=amount,
                    subtotal=amount,
                    currency=currency,
                    created_at=event_ts or datetime.utcnow()
                )
                db.add(new_inv)

        # 2. Subscription-level ordering check: if subscription is active, ensure this failure event
        # is not older than the most recent successful payment.
        if subscription.status == "active":
            latest_paid_ts = await db.scalar(
                select(func.max(SubscriptionInvoice.paid_at)).where(
                    SubscriptionInvoice.subscription_id == subscription.id,
                    SubscriptionInvoice.status == "paid"
                )
            )
            if latest_paid_ts and event_ts:
                paid_ts = latest_paid_ts.replace(tzinfo=None) if latest_paid_ts.tzinfo else latest_paid_ts
                if event_ts < paid_ts:
                    logger.warning(
                        f"Ignoring stale payment.failed event ({event_ts}) on active subscription {subscription.id} "
                        f"which is older than latest paid invoice ({paid_ts})"
                    )
                    return

        subscription.status = "past_due"
                
        log = ActivityLog(
            action="SUBSCRIPTION_PAYMENT_FAILED",
            entity="Subscription",
            entity_id=subscription.id,
            performed_by=None,
            details={"invoice_id": invoice_id, "provider": self.provider.provider_name}
        )
        db.add(log)

    async def _handle_subscription_deleted(self, db: AsyncSession, event_data: dict):
        data = event_data.get("data", {}).get("object", {})
        subscription_id = data.get("subscription")

        if not subscription_id:
            return

        result = await db.execute(
            select(Subscription).where(
                Subscription.external_subscription_id == subscription_id
            )
        )
        subscription = result.scalar_one_or_none()

        if not subscription:
            return

        if subscription.status in ("cancelled", "expired"):
            return

        event_ts = self._extract_event_timestamp(event_data)
        if event_ts and subscription.start_date:
            sub_start = subscription.start_date.replace(tzinfo=None) if subscription.start_date.tzinfo else subscription.start_date
            if event_ts < sub_start:
                logger.warning(
                    f"Ignoring stale subscription.deleted event ({event_ts}) for subscription {subscription.id} "
                    f"started at ({sub_start})"
                )
                return

        subscription.status = "cancelled"
        
        log = ActivityLog(
            action="SUBSCRIPTION_CANCELLED",
            entity="Subscription",
            entity_id=subscription.id,
            performed_by=None,
            details={"external_subscription_id": subscription_id, "provider": self.provider.provider_name}
        )
        db.add(log)

    async def _handle_refund_processed(self, db: AsyncSession, event_data: dict):
        data = event_data.get("data", {}).get("object", {})
        customer_id = data.get("customer")
        invoice_id = data.get("invoice_number")
        refund_id = data.get("refund_id")
        payment_id = data.get("payment_id")
        refund_status = data.get("refund_status", "full")
        amount = data.get("amount")

        subscription = None
        if customer_id:
            sub_res = await db.execute(
                select(Subscription).where(
                    Subscription.external_customer_id == customer_id
                )
            )
            subscription = sub_res.scalar_one_or_none()

        invoice = None
        if invoice_id:
            if subscription:
                inv_res = await db.execute(
                    select(SubscriptionInvoice).where(
                        and_(
                            SubscriptionInvoice.invoice_number == invoice_id,
                            SubscriptionInvoice.company_id == subscription.company_id,
                        )
                    )
                )
                invoice = inv_res.scalar_one_or_none()
            else:
                inv_res = await db.execute(
                    select(SubscriptionInvoice).where(
                        SubscriptionInvoice.invoice_number == invoice_id
                    )
                )
                invoice = inv_res.scalar_one_or_none()
                if invoice:
                    sub_res = await db.execute(
                        select(Subscription).where(
                            Subscription.id == invoice.subscription_id
                        )
                    )
                    subscription = sub_res.scalar_one_or_none()

        if not invoice:
            logger.warning(
                f"Refund ignored: invoice {invoice_id} not found or tenant mismatch."
            )
            return

        if subscription and invoice.company_id != subscription.company_id:
            logger.warning(
                f"Cross-tenant refund attempt blocked: invoice company {invoice.company_id} "
                f"does not match subscription company {subscription.company_id}."
            )
            return

        # Explicit Partial Refund limitation check
        if refund_status == "partial":
            logger.info(
                f"Partial refund received for invoice {invoice_id}. "
                f"Preserving existing invoice status '{invoice.status}' as partial refunds are not tracked in schema."
            )
            log = ActivityLog(
                action="REFUND_PARTIAL_RECEIVED",
                entity="SubscriptionInvoice",
                entity_id=invoice.id,
                performed_by=None,
                details={
                    "invoice_number": invoice.invoice_number,
                    "refund_id": refund_id,
                    "amount": amount,
                    "provider": self.provider.provider_name,
                },
            )
            db.add(log)
            return

        # Full Refund execution
        invoice.status = "refunded"

        log = ActivityLog(
            action="REFUND_PROCESSED",
            entity="SubscriptionInvoice",
            entity_id=invoice.id,
            performed_by=None,
            details={
                "invoice_number": invoice.invoice_number,
                "refund_id": refund_id,
                "payment_id": payment_id,
                "amount": amount,
                "provider": self.provider.provider_name,
            },
        )
        db.add(log)

