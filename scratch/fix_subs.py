import asyncio
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def fix():
    async with AsyncSessionLocal() as db:
        await db.execute(text("UPDATE subscriptions SET status = 'active' WHERE company_id IN (1, 2)"))
        await db.commit()

asyncio.run(fix())
