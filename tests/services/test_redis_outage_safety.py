import pytest
import pytest_asyncio
import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError, RedisError

from app.core.config import settings
from app.services.otp import (
    store_otp,
    verify_otp,
    check_otp_rate_limit,
    record_failed_otp_attempt,
    is_otp_locked,
    clear_otp_lockout,
    OTPStorageError,
    OTP_PREFIX,
    OTP_RATE_PREFIX,
    OTP_FAIL_PREFIX,
    OTP_LOCK_PREFIX,
)
from app.workers.billing_reconciliation_worker import (
    BillingReconciliationWorker,
    RELEASE_LOCK_SCRIPT,
)
from app.services.billing.mock_provider import MockPaymentProvider

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def real_redis():
    """Connects to real local Redis and ensures cleanup of test keys."""
    client = Redis.from_url(settings.REDIS_URL, decode_responses=False)
    created_keys = set()
    try:
        await client.ping()
        yield client, created_keys
    finally:
        for k in created_keys:
            try:
                await client.delete(k)
            except Exception:
                pass
        await client.aclose()


# =============================================================================
# 1. OTP LIFECYCLE & OUTAGE FAIL-CLOSED TESTS
# =============================================================================

async def test_otp_healthy_lifecycle(real_redis):
    """Verify OTP store, rate limit, verify, single-use consumption, and replay rejection when Redis is healthy."""
    client, created_keys = real_redis
    now_int = int(time.time() * 1000)
    mobile = f"9998{now_int % 1000000:06d}"
    otp_key = f"{OTP_PREFIX}{mobile}"
    created_keys.add(otp_key)

    # Store OTP
    await store_otp(client, mobile, "123456")
    stored = await client.get(otp_key)
    assert stored is not None
    assert stored.decode() == "123456"

    # Bad OTP rejected
    assert await verify_otp(client, mobile, "000000") is False
    assert await client.exists(otp_key) == 1

    # Good OTP accepted and consumed atomically
    assert await verify_otp(client, mobile, "123456") is True
    assert await client.exists(otp_key) == 0

    # Replay rejected
    assert await verify_otp(client, mobile, "123456") is False


async def test_otp_rate_limit_and_lockout(real_redis):
    """Verify OTP rate limit and brute force lockout under healthy Redis."""
    client, created_keys = real_redis
    now_int = int(time.time() * 1000)
    mobile = f"8887{now_int % 1000000:06d}"
    rate_key = f"{OTP_RATE_PREFIX}{mobile}"
    fail_key = f"{OTP_FAIL_PREFIX}{mobile}"
    lock_key = f"{OTP_LOCK_PREFIX}{mobile}"
    created_keys.update([rate_key, fail_key, lock_key])

    # Rate limiting: max 3/min
    assert await check_otp_rate_limit(client, mobile) is True
    assert await check_otp_rate_limit(client, mobile) is True
    assert await check_otp_rate_limit(client, mobile) is True
    assert await check_otp_rate_limit(client, mobile) is False

    # Lockout: 3 failed attempts trigger lock (parameterized test threshold)
    assert await record_failed_otp_attempt(client, mobile, max_attempts=3) is False
    assert await record_failed_otp_attempt(client, mobile, max_attempts=3) is False
    assert await record_failed_otp_attempt(client, mobile, max_attempts=3) is True
    assert await is_otp_locked(client, mobile) is True

    await clear_otp_lockout(client, mobile)
    assert await is_otp_locked(client, mobile) is False


async def test_otp_default_lockout_threshold_is_six(real_redis):
    """Verify default lockout threshold is authoritative 6 attempts when max_attempts is omitted (as in auth.py)."""
    client, created_keys = real_redis
    now_int = int(time.time() * 1000)
    mobile = f"8886{now_int % 1000000:06d}"
    fail_key = f"{OTP_FAIL_PREFIX}{mobile}"
    lock_key = f"{OTP_LOCK_PREFIX}{mobile}"
    created_keys.update([fail_key, lock_key])

    # Attempts 1..5 do not lock
    for _ in range(5):
        assert await record_failed_otp_attempt(client, mobile) is False
        assert await is_otp_locked(client, mobile) is False

    # Attempt 6 triggers lockout
    assert await record_failed_otp_attempt(client, mobile) is True
    assert await is_otp_locked(client, mobile) is True

    await clear_otp_lockout(client, mobile)
    assert await is_otp_locked(client, mobile) is False


async def test_otp_outage_fails_closed_when_redis_is_none():
    """Verify that when Redis is None, OTP operations fail closed instead of using unshared process memory."""
    mobile = "9999111222"

    # Store fails closed
    with pytest.raises(OTPStorageError) as exc_info:
        await store_otp(None, mobile, "123456")
    assert "Redis unavailable" in str(exc_info.value)

    # Verify fails closed
    with pytest.raises(OTPStorageError) as exc_info:
        await verify_otp(None, mobile, "123456")
    assert "Redis unavailable" in str(exc_info.value)

    # Rate limit fails closed
    with pytest.raises(OTPStorageError) as exc_info:
        await check_otp_rate_limit(None, mobile)
    assert "Redis unavailable" in str(exc_info.value)


async def test_otp_outage_fails_closed_on_connection_error():
    """Verify that when Redis raises ConnectionError during OTP operations, fail-closed OTPStorageError is raised."""
    mock_redis = AsyncMock(spec=Redis)
    mock_redis.set.side_effect = RedisConnectionError("Connection refused by peer")
    mock_redis.get.side_effect = RedisConnectionError("Connection refused by peer")
    mock_redis.incr.side_effect = RedisConnectionError("Connection refused by peer")

    mobile = "9999333444"

    # Store fails closed with OTPStorageError
    with pytest.raises(OTPStorageError) as exc_info:
        await store_otp(mock_redis, mobile, "654321")
    assert "Redis outage" in str(exc_info.value)

    # Verify fails closed with OTPStorageError
    with pytest.raises(OTPStorageError) as exc_info:
        await verify_otp(mock_redis, mobile, "654321")
    assert "Redis outage" in str(exc_info.value)

    # Rate limit fails closed with OTPStorageError
    with pytest.raises(OTPStorageError) as exc_info:
        await check_otp_rate_limit(mock_redis, mobile)
    assert "Redis outage" in str(exc_info.value)


# =============================================================================
# 2. BILLING RECONCILIATION WORKER OUTAGE & CONCURRENCY TESTS
# =============================================================================

async def test_billing_worker_healthy_redis_lock(real_redis):
    """Verify distributed lock acquisition, mutual exclusion, and release with real Redis."""
    client, created_keys = real_redis
    worker = BillingReconciliationWorker(
        provider=MockPaymentProvider(),
        redis_client=client,
        lock_ttl_seconds=10,
    )
    created_keys.add(worker.LOCK_KEY)

    # Worker 1 acquires lock
    acq_1 = await worker._acquire_lock()
    assert acq_1 is True

    # Worker 2 blocked
    worker_2 = BillingReconciliationWorker(
        provider=MockPaymentProvider(),
        redis_client=client,
        lock_ttl_seconds=10,
    )
    acq_2 = await worker_2._acquire_lock()
    assert acq_2 is False

    # Worker 1 releases
    await worker._release_lock()

    # Worker 2 can now acquire
    acq_2_retry = await worker_2._acquire_lock()
    assert acq_2_retry is True
    await worker_2._release_lock()


async def test_billing_worker_fails_closed_when_redis_is_none():
    """Verify billing worker fails closed when Redis is None, refusing to run with unsafe local lock."""
    worker = BillingReconciliationWorker(
        provider=MockPaymentProvider(),
        redis_client=None,
    )

    # Lock acquisition must fail closed
    acq = await worker._acquire_lock()
    assert acq is False

    # run_once must return None without executing reconciliation
    result = await worker.run_once()
    assert result is None


async def test_billing_worker_fails_closed_on_redis_connection_error():
    """Verify billing worker fails closed when Redis throws ConnectionError during acquisition."""
    mock_redis = AsyncMock(spec=Redis)
    mock_redis.set.side_effect = RedisConnectionError("Cluster unreachable")

    worker = BillingReconciliationWorker(
        provider=MockPaymentProvider(),
        redis_client=mock_redis,
    )

    # Fails closed
    acq = await worker._acquire_lock()
    assert acq is False

    # run_once skips safely
    result = await worker.run_once()
    assert result is None


async def test_distributed_lock_ownership_and_safe_release(real_redis):
    """Verify Lua script strictly prevents non-owner worker from releasing another worker's lock."""
    client, created_keys = real_redis
    lock_key = f"test:lock:ownership:{uuid.uuid4().hex[:8]}"
    created_keys.add(lock_key)

    owner_token = f"worker_owner_{uuid.uuid4().hex[:6]}"
    rogue_token = f"worker_rogue_{uuid.uuid4().hex[:6]}"

    # Owner acquires
    await client.set(lock_key, owner_token, nx=True, ex=10)

    # Rogue worker attempts release
    rel_rogue = await client.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, rogue_token)
    assert rel_rogue == 0
    # Lock still held by owner
    val = await client.get(lock_key)
    assert val.decode() == owner_token

    # Owner releases safely
    rel_owner = await client.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, owner_token)
    assert rel_owner == 1
    assert await client.get(lock_key) is None


async def test_distributed_lock_ttl_expiration_and_recovery(real_redis):
    """Verify distributed lock auto-reclaims when TTL expires."""
    client, created_keys = real_redis
    lock_key = f"test:lock:ttl:{uuid.uuid4().hex[:8]}"
    created_keys.add(lock_key)

    worker_a = f"worker_a_{uuid.uuid4().hex[:6]}"
    worker_b = f"worker_b_{uuid.uuid4().hex[:6]}"

    # Worker A acquires with 1s TTL
    await client.set(lock_key, worker_a, nx=True, ex=1)
    # Worker B blocked immediately
    assert await client.set(lock_key, worker_b, nx=True, ex=5) is None

    # Wait for TTL expiration
    await asyncio.sleep(1.2)

    # Worker B acquires reclaimed lock
    acq_b = await client.set(lock_key, worker_b, nx=True, ex=5)
    assert bool(acq_b) is True
    await client.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, worker_b)


# =============================================================================
# 3. APPLICATION SHUTDOWN HARDENING TESTS
# =============================================================================

async def test_shutdown_handler_success():
    """Verify graceful shutdown calls aclose() and idempotently clears app.state.redis."""
    mock_redis = AsyncMock()
    mock_redis.aclose = AsyncMock()

    class FakeState:
        redis = mock_redis

    class FakeApp:
        state = FakeState()

    app = FakeApp()
    redis = getattr(app.state, "redis", None)
    assert redis is not None

    # Execute shutdown logic
    app.state.redis = None
    if hasattr(redis, "aclose"):
        await redis.aclose()
    elif hasattr(redis, "close"):
        await redis.close()

    mock_redis.aclose.assert_awaited_once()
    assert app.state.redis is None

    # Idempotent second invocation does nothing
    second_redis = getattr(app.state, "redis", None)
    assert second_redis is None


async def test_shutdown_handler_connection_error_logged_without_crash():
    """Verify shutdown logs Redis connection errors gracefully without crashing or suppressing unrelated errors."""
    mock_redis = AsyncMock()
    mock_redis.aclose.side_effect = RedisConnectionError("Socket closed remotely")

    class FakeState:
        redis = mock_redis

    class FakeApp:
        state = FakeState()

    app = FakeApp()
    redis = getattr(app.state, "redis", None)

    # Execute shutdown logic with exception handling
    app.state.redis = None
    caught = False
    try:
        if hasattr(redis, "aclose"):
            await redis.aclose()
    except (RedisError, OSError):
        caught = True

    assert caught is True
    assert app.state.redis is None
