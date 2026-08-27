import pytest
import asyncio
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.workers.billing_reconciliation_worker import BillingReconciliationWorker
from app.services.billing.mock_provider import MockPaymentProvider
import app.db.base
from app.models.subscription import Subscription, Plan, SubscriptionInvoice, ManualPaymentTransaction
from app.models.company import Company
from app.db.session import AsyncSessionLocal

from app.core.config import settings

pytestmark = pytest.mark.asyncio


class MockRedis:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    async def get(self, key):
        return self.store.get(key)

    async def eval(self, script, numkeys, key, val):
        if self.store.get(key) == val:
            del self.store[key]
            return 1
        return 0

    async def close(self):
        pass


async def _create_test_data(db):
    plan = await db.scalar(select_plan := select_stmt())
    comp_name = f"Worker Test {uuid.uuid4().hex[:8]}"
    company = Company(name=comp_name, subdomain=comp_name.lower(), is_active=True)
    db.add(company)
    await db.commit()
    await db.refresh(company)

    sub = Subscription(
        company_id=company.id,
        plan_id=plan.id if plan else 1,
        status="active",
        external_subscription_id=f"sub_{uuid.uuid4().hex[:8]}",
        external_customer_id=f"cus_{uuid.uuid4().hex[:8]}",
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)

    # Manual payment transaction linked to sub.id
    txn = ManualPaymentTransaction(
        company_id=company.id,
        subscription_id=sub.id,
        plan_id=plan.id if plan else 1,
        amount=Decimal("4999.00"),
        currency="INR",
        payment_method="UPI",
        transaction_reference=f"TXN-{uuid.uuid4().hex[:8]}",
        utr_reference=f"UTR{uuid.uuid4().hex[:10]}",
        status="pending",
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return company, sub, txn



def select_stmt():
    from sqlalchemy import select
    return select(Plan).where(Plan.is_active == True).limit(1)


# =============================================================================
# 1. WORKER EXECUTION & SCHEDULING TESTS
# =============================================================================

async def test_worker_run_once_success():
    """Verify single execution runs reconciliation and returns metrics."""
    async with AsyncSessionLocal() as db:
        company, sub, txn = await _create_test_data(db)

        provider = MockPaymentProvider()
        mock_redis = MockRedis()
        worker = BillingReconciliationWorker(
            provider=provider,
            redis_client=mock_redis,
            batch_size=10,
            interval_seconds=60,
        )

        summary = await worker.run_once()
        assert summary is not None
        assert summary["total_reconciled"] >= 1
        assert summary["total_matched"] >= 1
        assert summary["total_drifted"] >= 0

        # Lock was cleanly released after run
        assert BillingReconciliationWorker.LOCK_KEY not in mock_redis.store


async def test_worker_lock_prevents_concurrent_execution():
    """Verify second worker is locked out if first worker holds the distributed lock."""
    mock_redis = MockRedis()
    provider = MockPaymentProvider()

    worker_1 = BillingReconciliationWorker(provider=provider, redis_client=mock_redis)
    worker_2 = BillingReconciliationWorker(provider=provider, redis_client=mock_redis)

    # Worker 1 acquires lock manually to simulate running job
    await mock_redis.set(BillingReconciliationWorker.LOCK_KEY, worker_1.worker_id, nx=True, ex=600)

    # Worker 2 attempts run_once
    summary_2 = await worker_2.run_once()
    assert summary_2 is None  # Locked out!


async def test_worker_lock_release_on_failure():
    """Verify lock is released even if reconciliation raises an unhandled exception."""
    mock_redis = MockRedis()
    provider = MockPaymentProvider()

    worker = BillingReconciliationWorker(provider=provider, redis_client=mock_redis)

    with patch("app.services.billing.reconciliation_service.BillingReconciliationService.reconcile_all_tenants", side_effect=RuntimeError("Database down")):
        summary = await worker.run_once()
        assert summary is None

    # Lock must be released
    assert BillingReconciliationWorker.LOCK_KEY not in mock_redis.store


async def test_worker_graceful_shutdown():
    """Verify start and stop lifecycle cleanly terminates loop."""
    provider = MockPaymentProvider()
    mock_redis = MockRedis()
    worker = BillingReconciliationWorker(
        provider=provider,
        redis_client=mock_redis,
        interval_seconds=1,  # Short interval for test
        batch_size=5,
    )

    task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.1)  # Let worker start and run
    assert worker._is_running is True

    await worker.stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert worker._is_running is False
    assert BillingReconciliationWorker.LOCK_KEY not in mock_redis.store


# =============================================================================
# 2. FINANCIAL & STATE IMMUTABILITY INVARIANTS
# =============================================================================

async def test_worker_does_not_mutate_subscription_or_manual_upi():
    """HARD INVARIANT: Background worker must NOT modify subscriptions or manual payments."""
    async with AsyncSessionLocal() as db:
        company, sub, txn = await _create_test_data(db)
        original_sub_status = sub.status
        original_txn_status = txn.status
        original_utr = txn.utr_reference

        provider = MockPaymentProvider()
        mock_redis = MockRedis()
        worker = BillingReconciliationWorker(provider=provider, redis_client=mock_redis)

        await worker.run_once()

        # Refresh from DB
        await db.refresh(sub)
        await db.refresh(txn)

        assert sub.status == original_sub_status
        assert txn.status == original_txn_status
        assert txn.utr_reference == original_utr
        assert txn.verified_by is None
        assert txn.verified_at is None


async def test_worker_provider_failure_does_not_crash_loop():
    """Verify that network/provider exceptions are caught and the loop continues."""
    mock_redis = MockRedis()
    provider = MockPaymentProvider()
    worker = BillingReconciliationWorker(provider=provider, redis_client=mock_redis, interval_seconds=1)

    with patch("app.services.billing.reconciliation_service.BillingReconciliationService.reconcile_all_tenants", side_effect=Exception("Gateway network timeout")):
        summary = await worker.run_once()
        assert summary is None
        # Worker state remains healthy
        assert worker.LOCK_KEY not in mock_redis.store
