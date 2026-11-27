import pytest
from sqlalchemy.pool import NullPool
from app.core.db import async_engine

# Under pytest-asyncio with function-scoped loops, connections in QueuePool
# are tied to closed event loops, leaking MySQL connections across 390+ tests.
# NullPool ensures connections close cleanly before each test's event loop terminates.
_null_pool = NullPool(
    creator=async_engine.sync_engine.pool._creator,
    recycle=async_engine.sync_engine.pool._recycle,
    echo=async_engine.sync_engine.pool.echo,
    reset_on_return=async_engine.sync_engine.pool._reset_on_return,
    pre_ping=True,
)
_null_pool._is_asyncio = True
async_engine.sync_engine.pool = _null_pool

from app.main import app

@pytest.fixture(autouse=True)
def clean_dependency_overrides():
    """
    Safely isolate dependency overrides per test lifecycle:
    1. Capture a snapshot of app.dependency_overrides prior to test execution.
    2. Allow the test and its fixtures to register or modify overrides.
    3. On completion or failure, restore app.dependency_overrides to the pre-test snapshot,
       reverting test-specific mutations while preserving outer fixture overrides.
    """
    snapshot = dict(app.dependency_overrides)
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(snapshot)
