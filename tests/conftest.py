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
