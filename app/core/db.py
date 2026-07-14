from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


async_engine = create_async_engine(
    settings.DATABASE_URL_ASYNC,
    echo=settings.SQL_ECHO,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

import time
import logging
from sqlalchemy import event

logger = logging.getLogger("construction-mgmt")

@event.listens_for(async_engine.sync_engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start_time", []).append(time.perf_counter())

@event.listens_for(async_engine.sync_engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if settings.SQL_ECHO:
        return
        
    start_time = conn.info["query_start_time"].pop(-1)
    total_time = time.perf_counter() - start_time
    
    if total_time > settings.SLOW_SQL_THRESHOLD:
        ms_time = round(total_time * 1000, 2)
        logger.warning(f"SLOW SQL ({ms_time}ms): {statement}")

