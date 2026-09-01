import asyncio
import logging
import uuid
import time
from typing import Optional, Dict, Any
from redis.asyncio import Redis

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.billing.provider_base import PaymentProviderInterface
from app.services.billing.mock_provider import MockPaymentProvider
from app.services.billing.razorpay_provider import RazorpayPaymentProvider
from app.services.billing.reconciliation_service import BillingReconciliationService

logger = logging.getLogger(__name__)

# Atomic Lua release script to ensure a worker only releases its own lock
RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class BillingReconciliationWorker:
    """
    Background orchestrator for periodic billing reconciliation.
    Strictly coordinates scheduling and distributed locking.
    Does NOT contain billing business logic and does NOT mutate subscriptions or payments.
    """

    LOCK_KEY = "infrapilot:lock:billing_reconciliation"

    def __init__(
        self,
        provider: Optional[PaymentProviderInterface] = None,
        redis_client: Optional[Redis] = None,
        interval_seconds: Optional[int] = None,
        batch_size: Optional[int] = None,
        lock_ttl_seconds: Optional[int] = None,
    ):
        self.worker_id = f"worker_{uuid.uuid4().hex[:8]}"
        self.provider = provider or self._resolve_default_provider()
        self.redis = redis_client
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.BILLING_RECONCILIATION_INTERVAL_MINUTES * 60
        )
        self.batch_size = (
            batch_size
            if batch_size is not None
            else settings.BILLING_RECONCILIATION_BATCH_SIZE
        )
        self.lock_ttl_seconds = (
            lock_ttl_seconds
            if lock_ttl_seconds is not None
            else settings.BILLING_RECONCILIATION_LOCK_TTL_SECONDS
        )
        self._stop_event = asyncio.Event()
        self._is_running = False
        self._in_process_lock = asyncio.Lock()
        self._current_task: Optional[asyncio.Task] = None

    def _resolve_default_provider(self) -> PaymentProviderInterface:
        if settings.PAYMENT_PROVIDER == "razorpay":
            return RazorpayPaymentProvider()
        return MockPaymentProvider()

    async def _acquire_lock(self) -> bool:
        if self.redis is not None:
            try:
                # Atomic SET key val NX EX ttl
                acquired = await self.redis.set(
                    self.LOCK_KEY,
                    self.worker_id,
                    nx=True,
                    ex=self.lock_ttl_seconds,
                )
                return bool(acquired)
            except Exception as e:
                logger.warning(
                    f"[{self.worker_id}] Redis lock acquire failed: {e}. Falling back to in-process lock."
                )

        # Fallback to local in-process lock
        if self._in_process_lock.locked():
            return False
        await self._in_process_lock.acquire()
        return True

    async def _release_lock(self) -> None:
        if self.redis is not None:
            try:
                await self.redis.eval(
                    RELEASE_LOCK_SCRIPT, 1, self.LOCK_KEY, self.worker_id
                )
            except Exception as e:
                logger.warning(
                    f"[{self.worker_id}] Redis lock release failed: {e}"
                )

        if self._in_process_lock.locked():
            self._in_process_lock.release()

    async def run_once(self) -> Optional[Dict[str, Any]]:
        """
        Executes a single reconciliation cycle with distributed lock protection.
        """
        if self._stop_event.is_set():
            return None

        lock_acquired = await self._acquire_lock()
        if not lock_acquired:
            logger.info(
                f"[{self.worker_id}] BILLING_RECONCILIATION_LOCKED: Another worker is already executing reconciliation."
            )
            return None

        start_time = time.time()
        logger.info(
            f"[{self.worker_id}] BILLING_RECONCILIATION_STARTED: Initiating multi-tenant billing scan (batch_size={self.batch_size})."
        )

        try:
            service = BillingReconciliationService(self.provider)
            async with AsyncSessionLocal() as db:
                summary = await service.reconcile_all_tenants(
                    db=db,
                    batch_size=self.batch_size,
                    current_user=None,
                    stop_event=self._stop_event,
                )

            duration = round(time.time() - start_time, 2)
            logger.info(
                f"[{self.worker_id}] BILLING_RECONCILIATION_COMPLETED: duration={duration}s "
                f"total={summary.get('total_reconciled', 0)} matched={summary.get('total_matched', 0)} "
                f"drifted={summary.get('total_drifted', 0)} unavailable={summary.get('total_unavailable', 0)}"
            )
            return summary
        except asyncio.CancelledError:
            duration = round(time.time() - start_time, 2)
            logger.info(
                f"[{self.worker_id}] BILLING_RECONCILIATION_CANCELLED: duration={duration}s"
            )
            raise
        except Exception as e:
            duration = round(time.time() - start_time, 2)
            logger.error(
                f"[{self.worker_id}] BILLING_RECONCILIATION_FAILED: duration={duration}s error={str(e)}"
            )
            return None
        finally:
            await self._release_lock()

    async def start(self) -> None:
        """
        Starts the background worker loop. Runs until stop() is invoked.
        """
        self._is_running = True
        self._stop_event.clear()
        logger.info(
            f"[{self.worker_id}] BillingReconciliationWorker started (interval={self.interval_seconds}s, batch_size={self.batch_size})."
        )

        try:
            while not self._stop_event.is_set():
                try:
                    self._current_task = asyncio.create_task(self.run_once())
                    await self._current_task
                except asyncio.CancelledError:
                    if self._stop_event.is_set():
                        break
                    raise
                except Exception as e:
                    logger.error(
                        f"[{self.worker_id}] Unexpected error in worker cycle: {e}"
                    )
                finally:
                    self._current_task = None

                if self._stop_event.is_set():
                    break

                try:
                    # Wait for interval or until stop signal
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            logger.info(f"[{self.worker_id}] Worker loop received cancellation signal.")
            self._stop_event.set()
        finally:
            self._is_running = False
            await self._release_lock()
            logger.info(
                f"[{self.worker_id}] BillingReconciliationWorker stopped cleanly."
            )

    async def stop(self) -> None:
        """
        Gracefully stops the worker loop and releases any held lock.
        """
        logger.info(
            f"[{self.worker_id}] Initiating graceful shutdown of BillingReconciliationWorker."
        )
        self._stop_event.set()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[{self.worker_id}] Error awaiting cancelled current task: {e}")
        await self._release_lock()


async def run_worker_cli():
    from app.cache.redis import create_redis_client

    redis_client = None
    try:
        redis_client = await create_redis_client(settings.REDIS_URL)
    except Exception as e:
        logger.warning(
            f"Redis unavailable for CLI worker ({e}); running with local locking."
        )

    worker = BillingReconciliationWorker(redis_client=redis_client)
    try:
        await worker.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        await worker.stop()
    finally:
        if redis_client is not None:
            await redis_client.close()


if __name__ == "__main__":
    asyncio.run(run_worker_cli())
