import os
import pytest
from urllib.parse import urlparse
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import redis.asyncio as aioredis

ALLOW_EXISTING_REDIS_6379 = os.getenv("ALLOW_EXISTING_REDIS_6379", "").strip().lower() in ("1", "true", "yes")

TEST_MYSQL_URL_ASYNC = "mysql+asyncmy://root:test_secret_pass_2026@127.0.0.1:3309/construction_management_integration_test?charset=utf8mb4"
TEST_MYSQL_URL_SYNC = "mysql+pymysql://root:test_secret_pass_2026@127.0.0.1:3309/construction_management_integration_test?charset=utf8mb4"
TEST_REDIS_URL = "redis://127.0.0.1:6379/0" if ALLOW_EXISTING_REDIS_6379 else "redis://127.0.0.1:6389/0"

FORBIDDEN_DB_NAMES = {"construction_management", "kinoventa", "nexacare", "production", "staging"}
FORBIDDEN_DB_PORTS = {3306, 3307}
FORBIDDEN_REDIS_PORTS = {6379, 6380}


def validate_safe_test_environment(
    db_url: str,
    redis_url: str | None = None,
    allow_existing_redis: bool | None = None,
) -> bool:
    """
    Fail-closed validator asserting test operations run ONLY against disposable
    test containers on dedicated non-standard ports.
    Raises RuntimeError immediately if any non-test or protected resource is targeted.
    """
    u = make_url(db_url)

    # Check Database Name
    db_name = (u.database or "").strip().lower()
    if not db_name or db_name in FORBIDDEN_DB_NAMES or not db_name.endswith("_integration_test"):
        raise RuntimeError(
            f"SAFETY VIOLATION: Refusing to connect to non-test database '{db_name}'. "
            f"Target database must explicitly end with '_integration_test'."
        )

    # Check Database Port
    port = u.port or 3306
    if port in FORBIDDEN_DB_PORTS:
        raise RuntimeError(
            f"SAFETY VIOLATION: Refusing to connect to protected database port {port}. "
            f"Integration tests must use isolated port 3309."
        )

    if port != 3309:
        raise RuntimeError(
            f"SAFETY VIOLATION: Database port is {port}, expected 3309."
        )

    # Check Host
    host = (u.host or "").lower()
    if host not in ("127.0.0.1", "localhost"):
        raise RuntimeError(
            f"SAFETY VIOLATION: Refusing to connect to remote host '{host}'."
        )

    # Check Redis URL if provided
    if redis_url:
        ru = urlparse(redis_url)
        r_port = ru.port or 6379
        use_existing_redis = ALLOW_EXISTING_REDIS_6379 if allow_existing_redis is None else allow_existing_redis
        if not use_existing_redis:
            if r_port in FORBIDDEN_REDIS_PORTS:
                raise RuntimeError(
                    f"SAFETY VIOLATION: Refusing to connect to protected Redis port {r_port}. "
                    f"Integration tests must use isolated port 6389."
                )
            if r_port != 6389:
                raise RuntimeError(
                    f"SAFETY VIOLATION: Redis port is {r_port}, expected 6389."
                )
        else:
            if r_port != 6379:
                raise RuntimeError(
                    f"SAFETY VIOLATION: Redis port is {r_port}, expected 6379 under ALLOW_EXISTING_REDIS_6379."
                )
        r_host = (ru.hostname or "").lower()
        if r_host not in ("127.0.0.1", "localhost"):
            raise RuntimeError(
                f"SAFETY VIOLATION: Refusing to connect to remote Redis host '{r_host}'."
            )

    return True


# =========================================================================
# SAFETY GUARD TESTS
# =========================================================================

def test_safety_guard_blocks_production_db_name():
    with pytest.raises(RuntimeError, match="SAFETY VIOLATION: Refusing to connect to non-test database"):
        validate_safe_test_environment(
            "mysql+asyncmy://root:pass@127.0.0.1:3309/construction_management"
        )


def test_safety_guard_blocks_default_port_3306():
    with pytest.raises(RuntimeError, match="SAFETY VIOLATION: Refusing to connect to protected database port 3306"):
        validate_safe_test_environment(
            "mysql+asyncmy://root:pass@127.0.0.1:3306/construction_management_integration_test"
        )


def test_safety_guard_blocks_default_redis_6379():
    with pytest.raises(RuntimeError, match="SAFETY VIOLATION: Refusing to connect to protected Redis port 6379"):
        validate_safe_test_environment(
            TEST_MYSQL_URL_ASYNC,
            redis_url="redis://127.0.0.1:6379/0",
            allow_existing_redis=False,
        )


def test_safety_guard_allows_integration_container():
    assert validate_safe_test_environment(TEST_MYSQL_URL_ASYNC, TEST_REDIS_URL) is True


@pytest.mark.asyncio
async def test_live_test_database_connection():
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC)
    engine = create_async_engine(TEST_MYSQL_URL_ASYNC)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT DATABASE()"))
            current_db = result.scalar()
            assert current_db == "construction_management_integration_test"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_live_test_redis_connection():
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC, TEST_REDIS_URL)
    r = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        pong = await r.ping()
        assert pong is True
    finally:
        await r.aclose()
