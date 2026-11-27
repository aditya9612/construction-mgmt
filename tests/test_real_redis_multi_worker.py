import asyncio
import subprocess
import sys
import os
import json
import uuid
import time
from datetime import datetime, date
from decimal import Decimal
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select, and_, text, create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.master_data import Unit, MaterialMaster
from app.models.material import Supplier, Material
from app.models.inventory import StockBatch
from app.models.daily_stock import DailyStockReconciliation
from app.models.user import User, UserRole
from app.core.enums import ProjectStatus, RateType
from app.services.daily_stock_service import (
    store_daily_stock_token,
    retrieve_and_consume_daily_stock_token,
    DAILY_STOCK_TOKEN_PREFIX,
    LUA_ATOMIC_GETDEL,
)
from app.api.daily_stock import commit_daily_stock_bulk_endpoint
from app.schemas.inventory_workflow import DailyStockBulkCommitRequest
from tests.test_db_safety_guard import (
    validate_safe_test_environment,
    TEST_MYSQL_URL_ASYNC,
    TEST_MYSQL_URL_SYNC,
    TEST_REDIS_URL,
)

# Fail-closed safety check
validate_safe_test_environment(TEST_MYSQL_URL_ASYNC, TEST_REDIS_URL)

test_engine = create_async_engine(TEST_MYSQL_URL_ASYNC, poolclass=NullPool, echo=False)
sync_engine = create_engine(TEST_MYSQL_URL_SYNC, poolclass=NullPool, echo=False)
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def get_test_redis():
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC, TEST_REDIS_URL)
    return aioredis.from_url(TEST_REDIS_URL, decode_responses=False)


@pytest.fixture(autouse=True)
def cleanup_all():
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC, TEST_REDIS_URL)
    with sync_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        tables = [
            "daily_stock_reconciliation", "stock_batches", "materials",
            "material_master", "units", "suppliers", "users", "projects", "owners", "companies"
        ]
        for t in tables:
            conn.execute(text(f"DELETE FROM {t};"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
    yield
    with sync_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        for t in tables:
            conn.execute(text(f"DELETE FROM {t};"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))


# =========================================================================
# PHASE E: REAL REDIS SERVER INTEGRATION TESTS (PORT 6389)
# =========================================================================

@pytest.mark.asyncio
async def test_redis_token_store_and_atomic_consume():
    redis_client = await get_test_redis()
    try:
        token = f"test-tok-{uuid.uuid4()}"
        items = [
            {"material_id": 101, "physical_count": Decimal("45.500"), "reconciliation_date": date(2026, 11, 26)},
            {"material_id": 102, "physical_count": Decimal("100.000"), "reconciliation_date": date(2026, 11, 26)},
        ]

        # 1. Store in real Redis
        await store_daily_stock_token(
            token=token,
            items=items,
            ttl_seconds=900,
            redis=redis_client,
            user_id=42,
            company_id=7,
        )

        # Verify key exists in real Redis
        raw = await redis_client.get(f"{DAILY_STOCK_TOKEN_PREFIX}{token}")
        assert raw is not None

        # Verify TTL is near 900
        ttl = await redis_client.ttl(f"{DAILY_STOCK_TOKEN_PREFIX}{token}")
        assert 800 <= ttl <= 900

        # 2. Consume atomically via Lua script
        consumed = await retrieve_and_consume_daily_stock_token(
            token=token,
            redis=redis_client,
            user_id=42,
            company_id=7,
        )

        assert consumed is not None
        assert len(consumed) == 2
        assert consumed[0]["material_id"] == 101
        assert consumed[0]["physical_count"] == Decimal("45.500")
        assert consumed[0]["reconciliation_date"] == date(2026, 11, 26)

        # 3. Anti-replay: second consumption MUST return None
        consumed_again = await retrieve_and_consume_daily_stock_token(
            token=token,
            redis=redis_client,
            user_id=42,
            company_id=7,
        )
        assert consumed_again is None

        # Key in Redis must be deleted
        raw_after = await redis_client.get(f"{DAILY_STOCK_TOKEN_PREFIX}{token}")
        assert raw_after is None
    finally:
        await redis_client.delete(f"{DAILY_STOCK_TOKEN_PREFIX}{token}")
        await redis_client.aclose()


@pytest.mark.asyncio
async def test_redis_token_tenant_and_user_isolation():
    redis_client = await get_test_redis()
    try:
        token = f"iso-tok-{uuid.uuid4()}"
        items = [{"material_id": 1, "physical_count": Decimal("10.0")}]

        await store_daily_stock_token(
            token=token,
            items=items,
            ttl_seconds=900,
            redis=redis_client,
            user_id=10,
            company_id=5,
        )

        # Wrong tenant attempts consumption -> Rejected
        bad_tenant = await retrieve_and_consume_daily_stock_token(
            token=token,
            redis=redis_client,
            user_id=10,
            company_id=999,  # Wrong company
        )
        assert bad_tenant is None

        # Wrong user attempts consumption -> Rejected
        bad_user = await retrieve_and_consume_daily_stock_token(
            token=token,
            redis=redis_client,
            user_id=999,  # Wrong user
            company_id=5,
        )
        assert bad_user is None
    finally:
        await redis_client.delete(f"{DAILY_STOCK_TOKEN_PREFIX}{token}")
        await redis_client.aclose()


@pytest.mark.asyncio
async def test_redis_token_ttl_expiry():
    redis_client = await get_test_redis()
    try:
        token = f"exp-tok-{uuid.uuid4()}"
        items = [{"material_id": 1, "physical_count": Decimal("5.0")}]

        # Store with 1 second TTL
        await store_daily_stock_token(
            token=token,
            items=items,
            ttl_seconds=1,
            redis=redis_client,
            user_id=1,
            company_id=1,
        )

        # Wait for expiration in real Redis
        await asyncio.sleep(1.2)

        consumed = await retrieve_and_consume_daily_stock_token(
            token=token,
            redis=redis_client,
            user_id=1,
            company_id=1,
        )
        assert consumed is None
    finally:
        await redis_client.delete(f"{DAILY_STOCK_TOKEN_PREFIX}{token}")
        await redis_client.aclose()


# =========================================================================
# PHASE F: MULTI-WORKER INDEPENDENT OS PROCESS INTEGRATION
# =========================================================================

def test_multi_worker_cross_process_token_sharing():
    """
    Spawns two genuinely independent Python processes.
    Worker A stores a token in real Redis (port 6389).
    Worker B retrieves and consumes it from real Redis.
    """
    token = f"cross-proc-{uuid.uuid4()}"

    # Worker A script: Stores token in Redis
    worker_a_code = f"""
import asyncio
import redis.asyncio as aioredis
from app.services.daily_stock_service import store_daily_stock_token

async def main():
    r = aioredis.from_url("{TEST_REDIS_URL}")
    items = [{{"material_id": 200, "physical_count": "75.5"}}]
    await store_daily_stock_token("{token}", items, ttl_seconds=60, redis=r, user_id=1, company_id=1)
    await r.aclose()
    print("WORKER_A_DONE")

asyncio.run(main())
"""
    res_a = subprocess.run([sys.executable, "-c", worker_a_code], capture_output=True, text=True, check=True)
    assert "WORKER_A_DONE" in res_a.stdout

    # Worker B script: Consumes token from Redis
    worker_b_code = f"""
import asyncio
import redis.asyncio as aioredis
from app.services.daily_stock_service import retrieve_and_consume_daily_stock_token

async def main():
    r = aioredis.from_url("{TEST_REDIS_URL}")
    consumed = await retrieve_and_consume_daily_stock_token("{token}", redis=r, user_id=1, company_id=1)
    await r.aclose()
    if consumed and len(consumed) == 1 and consumed[0]["material_id"] == 200:
        print("WORKER_B_SUCCESS")
    else:
        print("WORKER_B_FAILED")

asyncio.run(main())
"""
    res_b = subprocess.run([sys.executable, "-c", worker_b_code], capture_output=True, text=True, check=True)
    assert "WORKER_B_SUCCESS" in res_b.stdout


def test_concurrent_multi_process_race_condition():
    """
    Spawns two simultaneous independent processes competing for the EXACT same token.
    Exactly ONE must succeed, and exactly ONE must fail.
    """
    token = f"race-tok-{uuid.uuid4()}"

    # Setup token
    setup_code = f"""
import asyncio
import redis.asyncio as aioredis
from app.services.daily_stock_service import store_daily_stock_token

async def main():
    r = aioredis.from_url("{TEST_REDIS_URL}")
    items = [{{"material_id": 300, "physical_count": "100.0"}}]
    await store_daily_stock_token("{token}", items, ttl_seconds=60, redis=r, user_id=1, company_id=1)
    await r.aclose()

asyncio.run(main())
"""
    subprocess.run([sys.executable, "-c", setup_code], check=True)

    # Worker competing code
    worker_compete_code = f"""
import asyncio
import redis.asyncio as aioredis
from app.services.daily_stock_service import retrieve_and_consume_daily_stock_token

async def main():
    r = aioredis.from_url("{TEST_REDIS_URL}")
    consumed = await retrieve_and_consume_daily_stock_token("{token}", redis=r, user_id=1, company_id=1)
    await r.aclose()
    if consumed is not None:
        print("OUTCOME:WIN")
    else:
        print("OUTCOME:LOSE")

asyncio.run(main())
"""
    # Launch two simultaneous processes
    p1 = subprocess.Popen([sys.executable, "-c", worker_compete_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p2 = subprocess.Popen([sys.executable, "-c", worker_compete_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    out1, _ = p1.communicate()
    out2, _ = p2.communicate()

    outcomes = [out1.strip(), out2.strip()]
    assert "OUTCOME:WIN" in outcomes
    assert "OUTCOME:LOSE" in outcomes


# =========================================================================
# PHASE F: END-TO-END PREVIEW -> COMMIT (REAL MYSQL + REAL REDIS)
# =========================================================================

@pytest.mark.asyncio
async def test_daily_stock_end_to_end_preview_to_commit():
    redis_client = await get_test_redis()
    try:
        async with TestSessionLocal() as db:
            # Seed Base Data
            c = Company(id=1, name="Test Company", is_active=True)
            o = Owner(id=1, company_id=1, owner_code="OWN-1", owner_name="Owner 1", mobile="9876543210")
            p = Project(id=1, business_id="PRJ-1", company_id=1, owner_id=1, project_name="Site 1", status=ProjectStatus.ONGOING)
            u = Unit(id=1, name="Bags", unique_code="BAG", company_id=1)
            s = Supplier(id=1, supplier_name="Cement Supplier", company_id=1)
            mm = MaterialMaster(id=1, name="OPC Cement 50kg", unit_id=1, company_id=1, unique_code="MAT-CEM-50")
            user = User(id=1, email="engineer@test.com", role=UserRole.ADMIN.value, company_id=1, is_active=True)
            db.add_all([c, o, p, u, s, mm, user])

            mat = Material(
                id=1, material_code="MAT-001", project_id=1, material_master_id=1,
                material_name="OPC Cement 50kg", category="Cement", unit_id=1, supplier_id=1,
                purchase_rate=Decimal("350.00"), rate_type=RateType.FIXED,
                quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
                remaining_stock=Decimal("100.000"), total_amount=Decimal("35000.00"),
                payment_given=Decimal("0.00"), payment_pending=Decimal("35000.00"),
                minimum_stock_level=Decimal("20.000"),
            )
            db.add(mat)
            await db.commit()

            # Step 1: Store preview token in REAL Redis
            token = f"commit-tok-{uuid.uuid4()}"
            items_to_commit = [
                {
                    "project_id": 1,
                    "material_id": 1,
                    "physical_count": Decimal("95.000"),
                    "reconciliation_date": date(2026, 11, 26),
                    "remarks": "End-of-day cement physical tally",
                }
            ]
            await store_daily_stock_token(
                token=token,
                items=items_to_commit,
                ttl_seconds=900,
                redis=redis_client,
                user_id=user.id,
                company_id=c.id,
            )

            # Step 2: Call commit_daily_stock_bulk_endpoint API endpoint against REAL Redis and REAL MySQL
            req = DailyStockBulkCommitRequest(import_token=token)
            commit_res = await commit_daily_stock_bulk_endpoint(
                payload=req,
                current_user=user,
                db=db,
                redis=redis_client,
            )

            assert commit_res.success is True
            assert commit_res.committed_count == 1

        # Step 3: Verify MySQL state in a fresh independent session
        async with TestSessionLocal() as db_fresh:
            dsr_record = (await db_fresh.execute(
                select(DailyStockReconciliation).where(
                    and_(
                        DailyStockReconciliation.project_id == 1,
                        DailyStockReconciliation.material_id == 1,
                    )
                )
            )).scalar_one()

            assert dsr_record.physical_count == Decimal("95.000")
            assert dsr_record.variance == Decimal("-5.000")
            assert dsr_record.status == "VARIANCE_FLAGGED"

        # Step 4: Verify anti-replay: Attempting to commit the same token a second time fails
        async with TestSessionLocal() as db:
            user = await db.get(User, 1)
            with pytest.raises(Exception) as exc_info:
                await commit_daily_stock_bulk_endpoint(
                    payload=req,
                    current_user=user,
                    db=db,
                    redis=redis_client,
                )
            assert "Invalid, expired, or already consumed import token" in str(exc_info.value)
    finally:
        await redis_client.delete(f"{DAILY_STOCK_TOKEN_PREFIX}{token}")
        await redis_client.aclose()
