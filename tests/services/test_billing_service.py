import pytest
import json
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
import app.db.base
from app.models.subscription import Subscription, Plan, SubscriptionInvoice, BillingWebhookEvent
from app.models.company import Company
from app.models.user import User, ActivityLog
from app.services.billing.mock_provider import MockPaymentProvider
from app.services.billing.billing_service import BillingService

pytestmark = pytest.mark.asyncio


async def _get_test_data(db):
    company = await db.scalar(select(Company).where(Company.name == "Billing Test Company"))
    if not company:
        company = Company(name="Billing Test Company", subdomain="billing-test", is_active=True)
        db.add(company)
        await db.commit()
        await db.refresh(company)

    user = await db.scalar(select(User).where(User.company_id == company.id).limit(1))
    if not user:
        user = User(
            email="billingadmin@billingtest.com",
            full_name="Billing Admin",
            company_id=company.id,
            hashed_password="test",
            role="admin",
            mobile="9999999998",
            is_active=True,
            is_super_admin=False
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
    plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
    
    # cleanup subs for this specific test company
    await db.execute(Subscription.__table__.delete().where(Subscription.company_id == company.id))
    await db.commit()
    
    return company, user, plan


async def test_billing_checkout_success():
    async with AsyncSessionLocal() as db:
        company, user, plan = await _get_test_data(db)
        
        sub = Subscription(
            company_id=company.id,
            plan_id=plan.id,
            status="trial"
        )
        db.add(sub)
        await db.commit()
        
        provider = MockPaymentProvider()
        service = BillingService(provider)
        
        checkout_url = await service.create_checkout(
            db=db,
            company_id=company.id,
            plan_id=plan.id,
            user=user,
            success_url="http://success",
            cancel_url="http://cancel"
        )
        
        assert checkout_url.startswith("https://mock-provider.local/checkout/")
        await db.refresh(sub)
        assert sub.external_customer_id is not None
        assert sub.external_customer_id.startswith("mock_cus_")


async def test_billing_webhook_payment_succeeded():
    import uuid
    async with AsyncSessionLocal() as db:
        company, user, plan = await _get_test_data(db)

        test_cus_id = f"mock_cus_{uuid.uuid4().hex[:8]}"
        test_sub_id = f"mock_sub_{uuid.uuid4().hex[:8]}"
        test_inv_id = f"INV-{uuid.uuid4().hex[:8]}"
        test_evt_id = f"evt_{uuid.uuid4().hex[:8]}"

        sub = Subscription(
            company_id=company.id,
            plan_id=plan.id,
            status="trial",
            external_customer_id=test_cus_id
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
        
        invoice = SubscriptionInvoice(
            company_id=company.id,
            subscription_id=sub.id,
            invoice_number=test_inv_id,
            status="pending"
        )
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)
        
        provider = MockPaymentProvider()
        service = BillingService(provider)
        
        payload = json.dumps({
            "id": test_evt_id,
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "customer": test_cus_id,
                    "subscription": test_sub_id,
                    "invoice_number": test_inv_id
                }
            }
        }).encode("utf-8")
        
        result = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert result["status"] == "success"
        
        await db.refresh(sub)
        assert sub.status == "active"
        assert sub.external_subscription_id == test_sub_id
        
        await db.refresh(invoice)
        assert invoice.status == "paid"
        assert invoice.paid_at is not None


async def test_billing_webhook_payment_failed():
    import uuid
    async with AsyncSessionLocal() as db:
        company, user, plan = await _get_test_data(db)

        test_cus_id = f"mock_cus_{uuid.uuid4().hex[:8]}"
        test_sub_id = f"mock_sub_{uuid.uuid4().hex[:8]}"
        test_inv_id = f"INV-{uuid.uuid4().hex[:8]}"
        test_evt_id = f"evt_{uuid.uuid4().hex[:8]}"

        sub = Subscription(
            company_id=company.id,
            plan_id=plan.id,
            status="active",
            external_customer_id=test_cus_id
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
        
        invoice = SubscriptionInvoice(
            company_id=company.id,
            subscription_id=sub.id,
            invoice_number=test_inv_id,
            status="pending"
        )
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)
        
        provider = MockPaymentProvider()
        service = BillingService(provider)
        
        payload = json.dumps({
            "id": test_evt_id,
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "customer": test_cus_id,
                    "subscription": test_sub_id,
                    "invoice_number": test_inv_id
                }
            }
        }).encode("utf-8")
        
        result = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert result["status"] == "success"
        
        await db.refresh(sub)
        assert sub.status == "past_due"
        
        await db.refresh(invoice)
        assert invoice.status == "failed"


async def test_billing_webhook_idempotency():
    async with AsyncSessionLocal() as db:
        # clear events for test
        await db.execute(BillingWebhookEvent.__table__.delete().where(BillingWebhookEvent.event_id == "evt_duplicate"))
        await db.commit()

        provider = MockPaymentProvider()
        service = BillingService(provider)
        
        payload = json.dumps({
            "id": "evt_duplicate",
            "type": "customer.subscription.deleted",
            "data": {"object": {"subscription": "sub_xyz"}}
        }).encode("utf-8")
        
        # First call
        res1 = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert res1["status"] == "success"
        
        # Second call
        res2 = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert res2.get("message") == "already processed"
        
        # Verify only one event in DB
        result = await db.execute(select(BillingWebhookEvent).where(BillingWebhookEvent.event_id == "evt_duplicate"))
        events = result.scalars().all()
        assert len(events) == 1


async def test_billing_webhook_invalid_signature():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        service = BillingService(provider)
        
        payload = json.dumps({"id": "1", "type": "test"}).encode("utf-8")
        
        with pytest.raises(HTTPException) as exc:
            await service.handle_webhook(db=db, payload=payload, signature="bad_sig")
            
        assert exc.value.status_code == 400


async def test_billing_webhook_plan_upgrade_with_retroactive_invoice():
    import uuid
    async with AsyncSessionLocal() as db:
        company, user, plan = await _get_test_data(db)
        
        # Create a newer plan with unique code
        new_plan = Plan(name="Pro", code=f"pro_{uuid.uuid4().hex[:8]}", price=100.0, billing_interval="monthly", currency="INR", is_active=True)
        db.add(new_plan)
        await db.commit()
        await db.refresh(new_plan)

        test_cus_id = f"mock_cus_{uuid.uuid4().hex[:8]}"
        test_sub_id = f"mock_sub_{uuid.uuid4().hex[:8]}"
        test_inv_id = f"INV-{uuid.uuid4().hex[:8]}"
        test_evt_id = f"evt_{uuid.uuid4().hex[:8]}"

        sub = Subscription(
            company_id=company.id,
            plan_id=plan.id,
            status="trial",
            external_customer_id=test_cus_id
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
        
        provider = MockPaymentProvider()
        service = BillingService(provider)
        
        payload = json.dumps({
            "id": test_evt_id,
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "customer": test_cus_id,
                    "subscription": test_sub_id,
                    "invoice_number": test_inv_id,
                    "plan_id": str(new_plan.id)
                }
            }
        }).encode("utf-8")
        
        result = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert result["status"] == "success"
        
        await db.refresh(sub)
        assert sub.status == "active"
        assert sub.plan_id == new_plan.id
        
        # Retroactive invoice creation test
        invoice = await db.scalar(select(SubscriptionInvoice).where(SubscriptionInvoice.invoice_number == test_inv_id))
        assert invoice is not None
        assert invoice.status == "paid"
        assert invoice.total_amount == new_plan.price
        assert invoice.company_id == company.id
        assert invoice.subscription_id == sub.id


async def test_billing_webhook_reject_stale_payment_for_cancelled():
    import uuid
    async with AsyncSessionLocal() as db:
        company, user, plan = await _get_test_data(db)

        test_cus_id = f"mock_cus_{uuid.uuid4().hex[:8]}"
        test_sub_id = f"mock_sub_{uuid.uuid4().hex[:8]}"
        test_inv_id = f"INV-{uuid.uuid4().hex[:8]}"
        test_evt_id = f"evt_{uuid.uuid4().hex[:8]}"

        sub = Subscription(
            company_id=company.id,
            plan_id=plan.id,
            status="cancelled",
            external_customer_id=test_cus_id
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
        
        provider = MockPaymentProvider()
        service = BillingService(provider)
        
        payload = json.dumps({
            "id": test_evt_id,
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "customer": test_cus_id,
                    "subscription": test_sub_id,
                    "invoice_number": test_inv_id
                }
            }
        }).encode("utf-8")
        
        result = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert result["status"] == "success"
        
        await db.refresh(sub)
        # Should remain cancelled
        assert sub.status == "cancelled"



async def test_billing_webhook_renewal_success():
    import uuid
    import json
    
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        
        test_company_name = f"Renew Test {uuid.uuid4().hex[:8]}"
        company = Company(name=test_company_name, subdomain=test_company_name.lower(), is_active=True)
        db.add(company)
        await db.commit()
        await db.refresh(company)
        
        test_cus_id = f"mock_cus_renew_{uuid.uuid4().hex[:8]}"
        test_sub_id = f"mock_sub_renew_{uuid.uuid4().hex[:8]}"
        test_inv_id = f"INV-RENEW-{uuid.uuid4().hex[:8]}"
        test_evt_id = f"evt_renew_{uuid.uuid4().hex[:8]}"

        sub = Subscription(
            company_id=company.id,
            plan_id=plan.id,
            status="active",
            external_customer_id=test_cus_id
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
        
        provider = MockPaymentProvider()
        service = BillingService(provider)
        
        payload = json.dumps({
            "id": test_evt_id,
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "customer": test_cus_id,
                    "subscription": test_sub_id,
                    "invoice_number": test_inv_id
                }
            }
        }).encode("utf-8")
        
        result = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert result["status"] == "success"
        
        # Check ActivityLog
        log = await db.scalar(select(ActivityLog).where(ActivityLog.action == "SUBSCRIPTION_RENEWED").order_by(ActivityLog.id.desc()))
        assert log is not None
        assert log.entity_id == sub.id


async def test_billing_webhook_newer_success_followed_by_older_failure():
    import uuid
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        test_comp_name = f"Ordering Test {uuid.uuid4().hex[:8]}"
        company = Company(name=test_comp_name, subdomain=test_comp_name.lower(), is_active=True)
        db.add(company)
        await db.commit()
        await db.refresh(company)

        test_cus_id = f"mock_cus_order_{uuid.uuid4().hex[:8]}"
        test_sub_id = f"mock_sub_order_{uuid.uuid4().hex[:8]}"
        test_inv_id = f"INV-ORDER-{uuid.uuid4().hex[:8]}"

        sub = Subscription(
            company_id=company.id,
            plan_id=plan.id,
            status="trial",
            external_customer_id=test_cus_id
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)

        provider = MockPaymentProvider()
        service = BillingService(provider)

        # 1. Newer Success arrives (timestamp: 1700000500)
        success_payload = json.dumps({
            "id": f"evt_success_{uuid.uuid4().hex[:8]}",
            "type": "invoice.payment_succeeded",
            "created_at": 1700000500,
            "data": {
                "object": {
                    "customer": test_cus_id,
                    "subscription": test_sub_id,
                    "invoice_number": test_inv_id,
                    "created_at": 1700000500
                }
            }
        }).encode("utf-8")
        res1 = await service.handle_webhook(db=db, payload=success_payload, signature="mock_valid_signature")
        assert res1["status"] == "success"

        await db.refresh(sub)
        assert sub.status == "active"
        inv = await db.scalar(select(SubscriptionInvoice).where(SubscriptionInvoice.invoice_number == test_inv_id))
        assert inv.status == "paid"

        # 2. Older Failure arrives (timestamp: 1700000100)
        failure_payload = json.dumps({
            "id": f"evt_fail_{uuid.uuid4().hex[:8]}",
            "type": "invoice.payment_failed",
            "created_at": 1700000100,
            "data": {
                "object": {
                    "customer": test_cus_id,
                    "subscription": test_sub_id,
                    "invoice_number": test_inv_id,
                    "created_at": 1700000100
                }
            }
        }).encode("utf-8")
        res2 = await service.handle_webhook(db=db, payload=failure_payload, signature="mock_valid_signature")
        assert res2["status"] == "success"

        # Subscription must REMAIN active and invoice must REMAIN paid
        await db.refresh(sub)
        assert sub.status == "active"
        await db.refresh(inv)
        assert inv.status == "paid"


async def test_billing_webhook_newer_failure_followed_by_older_success():
    import uuid
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        test_comp_name = f"OrderFail Test {uuid.uuid4().hex[:8]}"
        company = Company(name=test_comp_name, subdomain=test_comp_name.lower(), is_active=True)
        db.add(company)
        await db.commit()
        await db.refresh(company)

        test_cus_id = f"mock_cus_fail_{uuid.uuid4().hex[:8]}"
        test_sub_id = f"mock_sub_fail_{uuid.uuid4().hex[:8]}"
        test_inv_fail_id = f"INV-FAIL-{uuid.uuid4().hex[:8]}"
        test_inv_old_id = f"INV-OLD-{uuid.uuid4().hex[:8]}"

        sub = Subscription(
            company_id=company.id,
            plan_id=plan.id,
            status="active",
            external_customer_id=test_cus_id
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)

        provider = MockPaymentProvider()
        service = BillingService(provider)

        # 1. Newer Failure arrives at 1700000500
        failure_payload = json.dumps({
            "id": f"evt_fail_{uuid.uuid4().hex[:8]}",
            "type": "invoice.payment_failed",
            "created_at": 1700000500,
            "data": {
                "object": {
                    "customer": test_cus_id,
                    "subscription": test_sub_id,
                    "invoice_number": test_inv_fail_id,
                    "created_at": 1700000500
                }
            }
        }).encode("utf-8")
        res1 = await service.handle_webhook(db=db, payload=failure_payload, signature="mock_valid_signature")
        assert res1["status"] == "success"

        await db.refresh(sub)
        assert sub.status == "past_due"

        # 2. Older Success arrives with timestamp 1700000100
        old_success_payload = json.dumps({
            "id": f"evt_old_success_{uuid.uuid4().hex[:8]}",
            "type": "invoice.payment_succeeded",
            "created_at": 1700000100,
            "data": {
                "object": {
                    "customer": test_cus_id,
                    "subscription": test_sub_id,
                    "invoice_number": test_inv_old_id,
                    "created_at": 1700000100
                }
            }
        }).encode("utf-8")
        res2 = await service.handle_webhook(db=db, payload=old_success_payload, signature="mock_valid_signature")
        assert res2["status"] == "success"

        # Subscription must REMAIN past_due
        await db.refresh(sub)
        assert sub.status == "past_due"


async def test_billing_webhook_cross_tenant_injection_isolated():
    import uuid
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        
        # Company A
        comp_a = Company(name=f"CompA_{uuid.uuid4().hex[:6]}", is_active=True)
        db.add(comp_a)
        # Company B
        comp_b = Company(name=f"CompB_{uuid.uuid4().hex[:6]}", is_active=True)
        db.add(comp_b)
        await db.commit()
        await db.refresh(comp_a)
        await db.refresh(comp_b)

        cus_a = f"cus_a_{uuid.uuid4().hex[:6]}"
        cus_b = f"cus_b_{uuid.uuid4().hex[:6]}"

        sub_a = Subscription(company_id=comp_a.id, plan_id=plan.id, status="active", external_customer_id=cus_a)
        sub_b = Subscription(company_id=comp_b.id, plan_id=plan.id, status="active", external_customer_id=cus_b)
        db.add_all([sub_a, sub_b])
        await db.commit()
        await db.refresh(sub_a)
        await db.refresh(sub_b)

        # Invoice belonging to Company B
        inv_b_num = f"INV-B-{uuid.uuid4().hex[:6]}"
        inv_b = SubscriptionInvoice(
            company_id=comp_b.id,
            subscription_id=sub_b.id,
            invoice_number=inv_b_num,
            status="pending"
        )
        db.add(inv_b)
        await db.commit()
        await db.refresh(inv_b)

        provider = MockPaymentProvider()
        service = BillingService(provider)

        # Attack: Webhook comes with customer A's ID, but tries to reference Company B's invoice_number
        attack_payload = json.dumps({
            "id": f"evt_atk_{uuid.uuid4().hex[:8]}",
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "customer": cus_a,
                    "invoice_number": inv_b_num
                }
            }
        }).encode("utf-8")

        res = await service.handle_webhook(db=db, payload=attack_payload, signature="mock_valid_signature")
        assert res["status"] == "success"

        # Company B's invoice must NOT be marked paid by Company A's webhook
        await db.refresh(inv_b)
        assert inv_b.status == "pending"


async def test_billing_webhook_full_refund_success():
    import uuid
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        comp = Company(name=f"CompRefund_{uuid.uuid4().hex[:6]}", is_active=True)
        db.add(comp)
        await db.commit()
        await db.refresh(comp)

        cus_id = f"cus_ref_{uuid.uuid4().hex[:6]}"
        sub = Subscription(company_id=comp.id, plan_id=plan.id, status="active", external_customer_id=cus_id)
        db.add(sub)
        await db.commit()
        await db.refresh(sub)

        inv_num = f"INV-REF-{uuid.uuid4().hex[:6]}"
        inv = SubscriptionInvoice(
            company_id=comp.id,
            subscription_id=sub.id,
            invoice_number=inv_num,
            status="paid",
            total_amount=500.0,
            currency="INR"
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)

        provider = MockPaymentProvider()
        service = BillingService(provider)

        refund_payload = json.dumps({
            "id": f"evt_rfnd_{uuid.uuid4().hex[:8]}",
            "type": "refund.processed",
            "data": {
                "object": {
                    "customer": cus_id,
                    "invoice_number": inv_num,
                    "refund_id": f"rfnd_{uuid.uuid4().hex[:8]}",
                    "payment_id": f"pay_{uuid.uuid4().hex[:8]}",
                    "amount": 50000,
                    "refund_status": "full"
                }
            }
        }).encode("utf-8")

        res = await service.handle_webhook(db=db, payload=refund_payload, signature="mock_valid_signature")
        assert res["status"] == "success"

        await db.refresh(inv)
        assert inv.status == "refunded"

        # Check ActivityLog
        log = await db.scalar(
            select(ActivityLog)
            .where(
                ActivityLog.action == "REFUND_PROCESSED",
                ActivityLog.entity_id == inv.id
            )
            .order_by(ActivityLog.id.desc())
        )
        assert log is not None
        assert log.details["invoice_number"] == inv_num
        assert "secret" not in str(log.details)
        assert "signature" not in str(log.details)


async def test_billing_webhook_refund_duplicate_idempotency():
    import uuid
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        comp = Company(name=f"CompRefDup_{uuid.uuid4().hex[:6]}", is_active=True)
        db.add(comp)
        await db.commit()
        await db.refresh(comp)

        cus_id = f"cus_dup_{uuid.uuid4().hex[:6]}"
        sub = Subscription(company_id=comp.id, plan_id=plan.id, status="active", external_customer_id=cus_id)
        db.add(sub)
        await db.commit()
        await db.refresh(sub)

        inv_num = f"INV-DUP-{uuid.uuid4().hex[:6]}"
        inv = SubscriptionInvoice(
            company_id=comp.id,
            subscription_id=sub.id,
            invoice_number=inv_num,
            status="paid",
            total_amount=500.0,
            currency="INR"
        )
        db.add(inv)
        await db.commit()

        provider = MockPaymentProvider()
        service = BillingService(provider)

        evt_id = f"evt_dup_rfnd_{uuid.uuid4().hex[:8]}"
        payload = json.dumps({
            "id": evt_id,
            "type": "refund.processed",
            "data": {
                "object": {
                    "customer": cus_id,
                    "invoice_number": inv_num,
                    "refund_id": "rfnd_dup_1",
                    "refund_status": "full"
                }
            }
        }).encode("utf-8")

        res1 = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert res1["status"] == "success"

        res2 = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert res2.get("message") == "already processed"


async def test_billing_webhook_refund_cross_tenant_rejection():
    import uuid
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        comp_a = Company(name=f"CompA_Ref_{uuid.uuid4().hex[:6]}", is_active=True)
        comp_b = Company(name=f"CompB_Ref_{uuid.uuid4().hex[:6]}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.commit()
        await db.refresh(comp_a)
        await db.refresh(comp_b)

        cus_a = f"cus_a_{uuid.uuid4().hex[:6]}"
        cus_b = f"cus_b_{uuid.uuid4().hex[:6]}"
        sub_a = Subscription(company_id=comp_a.id, plan_id=plan.id, status="active", external_customer_id=cus_a)
        sub_b = Subscription(company_id=comp_b.id, plan_id=plan.id, status="active", external_customer_id=cus_b)
        db.add_all([sub_a, sub_b])
        await db.commit()
        await db.refresh(sub_a)
        await db.refresh(sub_b)

        inv_b_num = f"INV-B-REF-{uuid.uuid4().hex[:6]}"
        inv_b = SubscriptionInvoice(
            company_id=comp_b.id,
            subscription_id=sub_b.id,
            invoice_number=inv_b_num,
            status="paid",
            total_amount=500.0,
            currency="INR"
        )
        db.add(inv_b)
        await db.commit()
        await db.refresh(inv_b)

        provider = MockPaymentProvider()
        service = BillingService(provider)

        # Cross-tenant attack payload: Customer A tries to refund Company B invoice
        attack_payload = json.dumps({
            "id": f"evt_atk_rfnd_{uuid.uuid4().hex[:8]}",
            "type": "refund.processed",
            "data": {
                "object": {
                    "customer": cus_a,
                    "invoice_number": inv_b_num,
                    "refund_id": "rfnd_atk",
                    "refund_status": "full"
                }
            }
        }).encode("utf-8")

        res = await service.handle_webhook(db=db, payload=attack_payload, signature="mock_valid_signature")
        assert res["status"] == "success"

        # Company B invoice must NOT be refunded
        await db.refresh(inv_b)
        assert inv_b.status == "paid"


async def test_billing_webhook_refund_cancelled_and_expired_subscription_no_reactivation():
    import uuid
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        comp = Company(name=f"CompRefCanc_{uuid.uuid4().hex[:6]}", is_active=True)
        db.add(comp)
        await db.commit()
        await db.refresh(comp)

        cus_id = f"cus_canc_{uuid.uuid4().hex[:6]}"
        sub = Subscription(company_id=comp.id, plan_id=plan.id, status="cancelled", external_customer_id=cus_id)
        db.add(sub)
        await db.commit()
        await db.refresh(sub)

        inv_num = f"INV-CANC-{uuid.uuid4().hex[:6]}"
        inv = SubscriptionInvoice(
            company_id=comp.id,
            subscription_id=sub.id,
            invoice_number=inv_num,
            status="paid",
            total_amount=500.0,
            currency="INR"
        )
        db.add(inv)
        await db.commit()

        provider = MockPaymentProvider()
        service = BillingService(provider)

        payload = json.dumps({
            "id": f"evt_canc_rfnd_{uuid.uuid4().hex[:8]}",
            "type": "refund.processed",
            "data": {
                "object": {
                    "customer": cus_id,
                    "invoice_number": inv_num,
                    "refund_id": "rfnd_canc_1",
                    "refund_status": "full"
                }
            }
        }).encode("utf-8")

        res = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert res["status"] == "success"

        await db.refresh(inv)
        assert inv.status == "refunded"

        # Subscription must NEVER be reactivated
        await db.refresh(sub)
        assert sub.status == "cancelled"


async def test_billing_webhook_partial_refund_preserves_invoice_status():
    import uuid
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        comp = Company(name=f"CompPartRef_{uuid.uuid4().hex[:6]}", is_active=True)
        db.add(comp)
        await db.commit()
        await db.refresh(comp)

        cus_id = f"cus_part_{uuid.uuid4().hex[:6]}"
        sub = Subscription(company_id=comp.id, plan_id=plan.id, status="active", external_customer_id=cus_id)
        db.add(sub)
        await db.commit()
        await db.refresh(sub)

        inv_num = f"INV-PART-{uuid.uuid4().hex[:6]}"
        inv = SubscriptionInvoice(
            company_id=comp.id,
            subscription_id=sub.id,
            invoice_number=inv_num,
            status="paid",
            total_amount=1000.0,
            currency="INR"
        )
        db.add(inv)
        await db.commit()

        provider = MockPaymentProvider()
        service = BillingService(provider)

        payload = json.dumps({
            "id": f"evt_part_rfnd_{uuid.uuid4().hex[:8]}",
            "type": "refund.processed",
            "data": {
                "object": {
                    "customer": cus_id,
                    "invoice_number": inv_num,
                    "refund_id": "rfnd_partial_1",
                    "amount": 25000,
                    "refund_status": "partial"
                }
            }
        }).encode("utf-8")

        res = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert res["status"] == "success"

        # Invoice MUST remain 'paid' because schema does not support partial refund balances
        await db.refresh(inv)
        assert inv.status == "paid"

        # Verify activity log for partial refund
        log = await db.scalar(
            select(ActivityLog)
            .where(
                ActivityLog.action == "REFUND_PARTIAL_RECEIVED",
                ActivityLog.entity_id == inv.id
            )
            .order_by(ActivityLog.id.desc())
        )
        assert log is not None
        assert log.details["invoice_number"] == inv_num
        assert log.details["amount"] == 25000


async def test_billing_webhook_populates_authoritative_company_id():
    import uuid
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
        comp = Company(name=f"CompOwner_{uuid.uuid4().hex[:6]}", is_active=True)
        db.add(comp)
        await db.commit()
        await db.refresh(comp)

        cus_id = f"cus_own_{uuid.uuid4().hex[:6]}"
        sub = Subscription(company_id=comp.id, plan_id=plan.id, status="active", external_customer_id=cus_id)
        db.add(sub)
        await db.commit()

        provider = MockPaymentProvider()
        service = BillingService(provider)

        evt_id = f"evt_own_{uuid.uuid4().hex[:8]}"
        payload = json.dumps({
            "id": evt_id,
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "customer": cus_id,
                    "subscription": "sub_own_123"
                }
            }
        }).encode("utf-8")

        res = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert res["status"] == "success"

        # Verify BillingWebhookEvent has authoritative company_id populated
        webhook_evt = await db.scalar(
            select(BillingWebhookEvent).where(BillingWebhookEvent.event_id == evt_id)
        )
        assert webhook_evt is not None
        assert webhook_evt.company_id == comp.id


async def test_billing_webhook_unknown_company_writes_null():
    import uuid
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        service = BillingService(provider)

        evt_id = f"evt_unknown_{uuid.uuid4().hex[:8]}"
        payload = json.dumps({
            "id": evt_id,
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "customer": f"cus_nonexistent_{uuid.uuid4().hex[:6]}"
                }
            }
        }).encode("utf-8")

        res = await service.handle_webhook(db=db, payload=payload, signature="mock_valid_signature")
        assert res["status"] == "success"

        webhook_evt = await db.scalar(
            select(BillingWebhookEvent).where(BillingWebhookEvent.event_id == evt_id)
        )
        assert webhook_evt is not None
        assert webhook_evt.company_id is None


async def test_billing_webhook_historical_null_company_id_valid():
    import uuid
    async with AsyncSessionLocal() as db:
        evt_id = f"evt_hist_{uuid.uuid4().hex[:8]}"
        historical_evt = BillingWebhookEvent(
            provider="mock",
            event_id=evt_id,
            event_type="invoice.payment_succeeded",
            company_id=None,
            status="processed"
        )
        db.add(historical_evt)
        await db.commit()
        await db.refresh(historical_evt)

        assert historical_evt.company_id is None
        assert historical_evt.id is not None



