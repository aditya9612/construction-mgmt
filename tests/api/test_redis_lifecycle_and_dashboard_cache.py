import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from redis.exceptions import ConnectionError as RedisConnectionError, RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

import app.db.base
from app.main import create_app
from app.core.db import AsyncSessionLocal
from app.models.user import User
from app.schemas.dashboard import AdminDashboardOut
from app.api.dashboard import admin_dashboard
from app.cache import redis as r
from app.cache.redis import _dumps, _loads


class InMemoryRedis:
    """Mock Redis client simulating redis.asyncio.Redis."""
    def __init__(self):
        self.store = {}
        self.closed = False

    async def get(self, key):
        if self.closed:
            raise RedisConnectionError("Connection closed")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.closed:
            raise RedisConnectionError("Connection closed")
        self.store[key] = value

    async def incr(self, key):
        if self.closed:
            raise RedisConnectionError("Connection closed")
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val).encode()
        return val

    async def ping(self):
        if self.closed:
            raise RedisConnectionError("Connection closed")
        return True

    async def aclose(self):
        self.closed = True

    async def close(self):
        self.closed = True


async def get_test_admin_user():
    async with AsyncSessionLocal() as db:
        user = await db.scalar(
            select(User)
            .where(User.role == "Admin", User.is_active == True, User.company_id.is_not(None))
            .limit(1)
        )
        assert user is not None, "Admin user must exist in test database"
        return user


# =============================================================================
# 1. SERIALIZATION & DESERIALIZATION UNIT TESTS
# =============================================================================

def test_dumps_handles_pydantic_model():
    """Verify _dumps serializes Pydantic model into JSON dict bytes, not a raw string."""
    from app.schemas.dashboard import AdminVitals
    vitals = AdminVitals(
        total_labour_today=5,
        pending_approvals=2,
        action_items=1,
        material_used_today=3,
        site_issues_open=0,
    )
    dumped = _dumps(vitals)
    assert isinstance(dumped, bytes)
    # Must NOT be a quoted string representation
    assert not dumped.startswith(b'"total_labour_today=')
    # Must be valid JSON object starting with {
    assert dumped.startswith(b'{"')

    loaded = _loads(dumped)
    assert isinstance(loaded, dict)
    assert loaded["total_labour_today"] == 5


# =============================================================================
# 2. DASHBOARD CACHE MISS, CACHE HIT & TYPE PARITY
# =============================================================================

@pytest.mark.asyncio
async def test_dashboard_cache_miss_and_hit_parity():
    """
    Verify both cache-miss and cache-hit paths:
    1. Miss: calculates, stores valid JSON in Redis, returns AdminDashboardOut.
    2. Hit: retrieves from Redis, validates into AdminDashboardOut with identical schema.
    """
    admin_user = await get_test_admin_user()
    fake_redis = InMemoryRedis()

    async with AsyncSessionLocal() as db:
        # 1. Cache Miss
        res_miss = await admin_dashboard(current_user=admin_user, db=db, redis=fake_redis)
        assert isinstance(res_miss, AdminDashboardOut)
        assert res_miss.project_overview is not None
        assert res_miss.financial is not None

        # Verify key exists in Redis and contains valid JSON object
        cache_key = f"admin_dashboard:v2:{admin_user.company_id}:1"
        assert cache_key in fake_redis.store
        raw_stored = fake_redis.store[cache_key]
        assert raw_stored.startswith(b'{"')

        # 2. Cache Hit
        res_hit = await admin_dashboard(current_user=admin_user, db=db, redis=fake_redis)
        assert isinstance(res_hit, AdminDashboardOut)

        # Parity check: data must match identically
        assert res_hit.model_dump() == res_miss.model_dump()


# =============================================================================
# 3. REPEATED & CONCURRENT DASHBOARD REQUESTS
# =============================================================================

@pytest.mark.asyncio
async def test_dashboard_repeated_requests():
    """Verify repeated sequential calls remain stable and idempotent."""
    admin_user = await get_test_admin_user()
    fake_redis = InMemoryRedis()

    async with AsyncSessionLocal() as db:
        first = await admin_dashboard(current_user=admin_user, db=db, redis=fake_redis)
        for _ in range(5):
            subsequent = await admin_dashboard(current_user=admin_user, db=db, redis=fake_redis)
            assert isinstance(subsequent, AdminDashboardOut)
            assert subsequent.model_dump() == first.model_dump()


@pytest.mark.asyncio
async def test_dashboard_concurrent_requests():
    """Verify concurrent requests executing simultaneously with Redis cache succeed cleanly."""
    admin_user = await get_test_admin_user()
    fake_redis = InMemoryRedis()

    async def fetch():
        async with AsyncSessionLocal() as db:
            return await admin_dashboard(current_user=admin_user, db=db, redis=fake_redis)

    results = await asyncio.gather(*(fetch() for _ in range(5)))
    for r in results:
        assert isinstance(r, AdminDashboardOut)
    # All concurrent requests must return identical data
    assert all(r.model_dump() == results[0].model_dump() for r in results)


# =============================================================================
# 4. REDIS UNAVAILABLE & RUNTIME FAILURE FALLBACK
# =============================================================================

@pytest.mark.asyncio
async def test_dashboard_redis_none_fallback():
    """Verify that when redis is None (disabled), dashboard succeeds via direct DB queries."""
    admin_user = await get_test_admin_user()
    async with AsyncSessionLocal() as db:
        res = await admin_dashboard(current_user=admin_user, db=db, redis=None)
        assert isinstance(res, AdminDashboardOut)
        assert res.project_overview is not None


@pytest.mark.asyncio
async def test_dashboard_redis_runtime_error_fallback():
    """
    Verify that when Redis raises network/connection errors at runtime,
    cache helper functions catch the error and gracefully degrade to DB.
    """
    class BrokenRedis:
        async def get(self, key):
            raise RedisConnectionError("Socket disconnected")
        async def set(self, key, value, ex=None):
            raise RedisConnectionError("Socket disconnected")
        async def incr(self, key):
            raise RedisConnectionError("Socket disconnected")

    admin_user = await get_test_admin_user()
    broken = BrokenRedis()
    async with AsyncSessionLocal() as db:
        # Must NOT raise HTTP 500 or ConnectionError
        res = await admin_dashboard(current_user=admin_user, db=db, redis=broken)
        assert isinstance(res, AdminDashboardOut)


# =============================================================================
# 5. FASTAPI LIFESPAN STARTUP AND SHUTDOWN
# =============================================================================

@pytest.mark.asyncio
async def test_fastapi_lifespan_lifecycle():
    """Verify startup and shutdown lifecycle: state reference management, idempotency, and closing."""
    app = create_app()

    fake_client = InMemoryRedis()

    with patch("app.main.create_redis_client", AsyncMock(return_value=fake_client)), \
         patch("app.main.init_rate_limiter", AsyncMock()), \
         patch.object(AsyncEngine, "dispose", AsyncMock()) as mock_dispose:

        async with app.router.lifespan_context(app):
            # During running state
            assert app.state.redis is fake_client
            assert not fake_client.closed

        # After shutdown
        assert app.state.redis is None
        assert fake_client.closed
        assert mock_dispose.called


# =============================================================================
# 6. GLOBAL EXCEPTION HANDLER & CORS PRESERVATION ON 500
# =============================================================================

def test_unhandled_exception_cors_headers_preserved():
    """
    Verify that an unhandled 500 exception caught by global_exception_handler
    returns JSON with proper Access-Control-Allow-Origin header matching Netlify.
    """
    from starlette.testclient import TestClient

    app = create_app()

    @app.get("/test-unhandled-crash")
    async def crash_endpoint():
        raise ValueError("Simulated unexpected crash")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/test-unhandled-crash",
        headers={"Origin": "https://infrapilot-testing.netlify.app"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    # CORS header must be preserved
    assert response.headers.get("access-control-allow-origin") == "https://infrapilot-testing.netlify.app"
    assert response.headers.get("access-control-allow-credentials") == "true"
    assert "x-request-id" in response.headers


# =============================================================================
# 7. SERIALIZATION FAILURE GRACEFUL DEGRADATION
# =============================================================================

@pytest.mark.asyncio
async def test_cache_set_serialization_failure_graceful():
    """Verify that serialization failure in cache_set_json does not raise an unhandled exception."""
    fake_redis = InMemoryRedis()
    # A circular reference or unhandled type cannot be JSON-serialized
    class UnserializableObject:
        def __init__(self):
            self.loop = self
    
    # Must log warning and return cleanly without raising
    await r.cache_set_json(fake_redis, "test:unserializable", UnserializableObject())


# =============================================================================
# 8. STARTUP LIFESPAN WITH NON-CONNECTION REDIS ERROR
# =============================================================================

@pytest.mark.asyncio
async def test_fastapi_lifespan_general_redis_error_degrades_gracefully():
    """Verify that any RedisError on startup rate limiter init is caught and degrades gracefully."""
    app = create_app()
    fake_client = InMemoryRedis()

    with patch("app.main.create_redis_client", AsyncMock(return_value=fake_client)), \
         patch("app.main.init_rate_limiter", AsyncMock(side_effect=RedisError("Cluster unreachable"))), \
         patch.object(AsyncEngine, "dispose", AsyncMock()):

        async with app.router.lifespan_context(app):
            # Degraded state
            assert app.state.redis is None
            assert app.state.rate_limiter is None
            assert fake_client.closed


# =============================================================================
# 9. /health/ready CONNECTIVITY AND DEGRADED STATES
# =============================================================================

def test_health_ready_active_and_degraded():
    """Verify /health/ready returns 'ok' when connected, and 'degraded' when redis is unavailable."""
    from starlette.testclient import TestClient

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    # Case A: Redis attached and healthy
    app.state.redis = InMemoryRedis()
    resp_ok = client.get("/health/ready")
    assert resp_ok.status_code == 200
    data_ok = resp_ok.json()
    assert data_ok["redis"] == "ok"
    assert data_ok["status"] == "ok"

    # Case B: Redis is None (degraded)
    app.state.redis = None
    resp_degraded = client.get("/health/ready")
    assert resp_degraded.status_code == 200
    data_degraded = resp_degraded.json()
    assert data_degraded["redis"] == "unavailable"
    assert data_degraded["status"] == "degraded"
