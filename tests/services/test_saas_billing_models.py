import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.subscription import Plan, Subscription, SubscriptionInvoice, BillingWebhookEvent


@pytest.mark.asyncio
async def test_subscription_invoice_creation_and_scoping():
    async with AsyncSessionLocal() as db:
        # Get existing company and subscription
        company = await db.scalar(select(Company).where(Company.id == 1))
        sub = await db.scalar(select(Subscription).where(Subscription.company_id == 1))
        if not sub:
            plan = await db.scalar(select(Plan).where(Plan.is_active == True))
            sub = Subscription(
                company_id=1,
                plan_id=plan.id,
                status="active",
                start_date=datetime.utcnow(),
            )
            db.add(sub)
            await db.flush()

        inv_num = f"INV-TEST-{int(datetime.utcnow().timestamp())}"
        invoice = SubscriptionInvoice(
            company_id=1,
            subscription_id=sub.id,
            invoice_number=inv_num,
            billing_period_start=datetime.utcnow(),
            billing_period_end=datetime.utcnow() + timedelta(days=30),
            subtotal=Decimal("4999.00"),
            tax_amount=Decimal("899.82"),
            total_amount=Decimal("5898.82"),
            currency="INR",
            status="paid",
            issued_at=datetime.utcnow(),
            paid_at=datetime.utcnow(),
        )
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)

        assert invoice.id is not None
        assert invoice.company_id == 1
        assert invoice.subscription_id == sub.id
        assert invoice.total_amount == Decimal("5898.82")


@pytest.mark.asyncio
async def test_subscription_invoice_unique_number():
    async with AsyncSessionLocal() as db:
        sub = await db.scalar(select(Subscription).where(Subscription.company_id == 1))
        inv_num = f"INV-UNIQ-{int(datetime.utcnow().timestamp())}"
        
        inv1 = SubscriptionInvoice(
            company_id=1,
            subscription_id=sub.id,
            invoice_number=inv_num,
            total_amount=Decimal("100.00"),
        )
        db.add(inv1)
        await db.commit()

        # Duplicate invoice number should fail
        inv2 = SubscriptionInvoice(
            company_id=1,
            subscription_id=sub.id,
            invoice_number=inv_num,
            total_amount=Decimal("200.00"),
        )
        db.add(inv2)
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


@pytest.mark.asyncio
async def test_billing_webhook_event_idempotency_constraint():
    async with AsyncSessionLocal() as db:
        evt_id = f"evt_{int(datetime.utcnow().timestamp())}"
        
        event1 = BillingWebhookEvent(
            provider="razorpay",
            event_id=evt_id,
            event_type="payment.captured",
            payload_reference="pay_123456",
            status="processed",
            processed_at=datetime.utcnow(),
        )
        db.add(event1)
        await db.commit()

        # Duplicate (provider, event_id) must trigger IntegrityError
        event2 = BillingWebhookEvent(
            provider="razorpay",
            event_id=evt_id,
            event_type="payment.captured",
            status="pending",
        )
        db.add(event2)
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
