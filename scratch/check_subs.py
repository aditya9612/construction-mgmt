import asyncio
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as db:
        res = await db.execute(text("SELECT id, status, plan_id FROM subscriptions WHERE company_id = 1"))
        print("Subscriptions for company 1:")
        for r in res.fetchall():
            print(r)
        
        res = await db.execute(text("SELECT id, name, features FROM plans"))
        print("\nPlans:")
        for r in res.fetchall():
            print(r)

asyncio.run(check())
