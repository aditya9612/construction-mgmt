import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import async_engine
from app.db.base import *  # noqa: F403


async def init_db(engine: AsyncEngine = async_engine) -> None:
    """
    Dev helper to verify DB connectivity.
    For schema changes, prefer Alembic migrations.
    """
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))


if __name__ == "__main__":
    asyncio.run(init_db())

