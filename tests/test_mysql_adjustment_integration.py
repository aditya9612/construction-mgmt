from datetime import datetime
from decimal import Decimal
import pytest
from sqlalchemy import select, and_, text, create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from fastapi import HTTPException

from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.master_data import Unit, MaterialMaster
from app.models.material import Supplier, Material, MaterialTransaction, MaterialLedger
from app.models.inventory import StockBatch, ProjectMaterialValuation
from app.models.user import User, UserRole
from app.core.enums import ProjectStatus, RateType
from app.api.material import adjust_inventory
from app.schemas.material import InventoryAdjustRequest
from tests.test_db_safety_guard import validate_safe_test_environment, TEST_MYSQL_URL_ASYNC, TEST_MYSQL_URL_SYNC

# Enforce fail-closed safety check at module load
validate_safe_test_environment(TEST_MYSQL_URL_ASYNC)

test_engine = create_async_engine(TEST_MYSQL_URL_ASYNC, poolclass=NullPool, echo=False)
sync_engine = create_engine(TEST_MYSQL_URL_SYNC, poolclass=NullPool, echo=False)
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


class MockRedis:
    async def delete(self, *keys):
        return 1
    async def get(self, key):
        return None
    async def set(self, key, value, *args, **kwargs):
        return True
    async def incr(self, key):
        return 1


@pytest.fixture(autouse=True)
def cleanup_db():
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC)
    with sync_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        tables = [
            "material_transfers", "stock_batches", "project_material_valuation",
            "material_ledger", "material_transactions", "materials",
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


async def seed_base_data(db: AsyncSession):
    # Companies
    c1 = Company(id=1, name="Company 1 Tenant", is_active=True)
    c2 = Company(id=2, name="Company 2 Tenant", is_active=True)
    db.add_all([c1, c2])

    # Owners
    o1 = Owner(id=1, company_id=1, owner_code="OWN-001", owner_name="Owner 1", mobile="9876543210")
    o2 = Owner(id=2, company_id=2, owner_code="OWN-002", owner_name="Owner 2", mobile="9876543211")
    db.add_all([o1, o2])

    # Projects
    p1 = Project(id=1, business_id="PRJ-001", company_id=1, owner_id=1, project_name="Site A", status=ProjectStatus.ONGOING)
    p2 = Project(id=2, business_id="PRJ-002", company_id=2, owner_id=2, project_name="Site B (Tenant 2)", status=ProjectStatus.ONGOING)
    db.add_all([p1, p2])

    # Unit & Supplier
    u1 = Unit(id=1, name="Kilogram", unique_code="KG", company_id=1)
    s1 = Supplier(id=1, supplier_name="Steel Supplier Ltd", company_id=1)
    db.add_all([u1, s1])

    # Material Master
    mm1 = MaterialMaster(id=1, name="TMT Steel 12mm", unit_id=1, company_id=1, unique_code="MAT-TMT-12")
    db.add(mm1)

    # Users
    u_admin1 = User(
        id=1, email="admin1@test.com", role=UserRole.ADMIN.value,
        company_id=1, is_active=True, is_deleted=False, is_super_admin=False
    )
    u_admin2 = User(
        id=2, email="admin2@test.com", role=UserRole.ADMIN.value,
        company_id=2, is_active=True, is_deleted=False, is_super_admin=False
    )
    db.add_all([u_admin1, u_admin2])

    await db.commit()
    return c1, c2, p1, p2, mm1, s1, u_admin1, u_admin2


# =========================================================================
# TEST 1: POSITIVE VARIANCE CREATES BATCH & SYNCS MWA WITHOUT POLLUTING AP
# =========================================================================
@pytest.mark.asyncio
async def test_positive_variance_creates_batch_and_updates_valuation():
    async with TestSessionLocal() as db:
        _, _, p1, _, mm1, s1, admin1, _ = await seed_base_data(db)

        # Initial Material: 100 units @ Rs 50
        mat = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("50.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"), total_amount=Decimal("5000.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("5000.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        val = ProjectMaterialValuation(
            project_id=p1.id, material_master_id=mm1.id,
            total_purchased_quantity=Decimal("100.000"), total_purchased_amount=Decimal("5000.00"),
            current_moving_weighted_average_rate=Decimal("50.00"),
        )
        db.add_all([mat, val])
        await db.commit()

        # Adjust from 100 to 150 (variance = +50)
        req = InventoryAdjustRequest(
            material_id=1,
            new_stock=150.0,
            reason="Found extra stock during audit",
        )
        res = await adjust_inventory(
            payload=req,
            db=db,
            current_user=admin1,
            redis=MockRedis(),
        )

        assert res.new_stock == 150.0
        assert res.difference == 50.0

    # Verify against fresh MySQL session
    async with TestSessionLocal() as db_fresh:
        m = await db_fresh.get(Material, 1)
        # Material stock updated
        assert m.remaining_stock == Decimal("150.000")
        # Accounts Payable protected: quantity_purchased and total_amount NOT inflated
        assert m.quantity_purchased == Decimal("100.000")
        assert m.total_amount == Decimal("5000.00")

        # Adjustment batch created in MySQL
        adj_batch = (await db_fresh.execute(
            select(StockBatch).where(
                and_(
                    StockBatch.material_master_id == mm1.id,
                    StockBatch.batch_source == "ADJUSTMENT_IN",
                )
            )
        )).scalar_one()

        assert adj_batch.current_balance == Decimal("50.000")
        assert adj_batch.initial_quantity == Decimal("50.000")
        assert adj_batch.purchase_rate == Decimal("50.00")
        assert adj_batch.status == "ACTIVE"

        # ProjectMaterialValuation updated with +50 units
        v = (await db_fresh.execute(
            select(ProjectMaterialValuation).where(
                and_(
                    ProjectMaterialValuation.project_id == p1.id,
                    ProjectMaterialValuation.material_master_id == mm1.id,
                )
            )
        )).scalar_one()

        assert v.total_purchased_quantity == Decimal("150.000")
        assert v.total_purchased_amount == Decimal("7500.00")
        assert v.current_moving_weighted_average_rate == Decimal("50.00")


# =========================================================================
# TEST 2: NEGATIVE VARIANCE DEPLETES BATCHES IN STRICT FIFO ORDER
# =========================================================================
@pytest.mark.asyncio
async def test_negative_variance_fifo_batch_depletion():
    async with TestSessionLocal() as db:
        _, _, p1, _, mm1, s1, admin1, _ = await seed_base_data(db)

        # Initial Material: 150 units total, 8250 amount
        mat = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("55.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("150.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("150.000"), total_amount=Decimal("8250.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("8250.00"),
            minimum_stock_level=Decimal("20.000"),
        )
        # 3 batches: 50 @ 50, 50 @ 60, 50 @ 55
        b1 = StockBatch(
            id=1, batch_number="BATCH-ADJ-001", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("50.000"), current_balance=Decimal("50.000"),
            purchase_rate=Decimal("50.00"), receipt_date=datetime(2026, 1, 1), status="ACTIVE",
        )
        b2 = StockBatch(
            id=2, batch_number="BATCH-ADJ-002", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("50.000"), current_balance=Decimal("50.000"),
            purchase_rate=Decimal("60.00"), receipt_date=datetime(2026, 1, 15), status="ACTIVE",
        )
        b3 = StockBatch(
            id=3, batch_number="BATCH-ADJ-003", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("50.000"), current_balance=Decimal("50.000"),
            purchase_rate=Decimal("55.00"), receipt_date=datetime(2026, 2, 1), status="ACTIVE",
        )
        val = ProjectMaterialValuation(
            project_id=p1.id, material_master_id=mm1.id,
            total_purchased_quantity=Decimal("150.000"), total_purchased_amount=Decimal("8250.00"),
            current_moving_weighted_average_rate=Decimal("55.00"),
        )
        db.add_all([mat, b1, b2, b3, val])
        await db.commit()

        # Physical stock = 90 (diff = -60 units).
        # FIFO depletion must consume:
        # B1: 50 units @ 50 (5000 / 2 = 2500)
        # B2: 10 units @ 60 (600)
        # Total depleted value = 3100.
        req = InventoryAdjustRequest(
            material_id=1,
            new_stock=90.0,
            reason="Shrinkage and damage write-off",
        )
        res = await adjust_inventory(
            payload=req,
            db=db,
            current_user=admin1,
            redis=MockRedis(),
        )

        assert res.new_stock == 90.0
        assert res.difference == -60.0

    # Verify against fresh MySQL session
    async with TestSessionLocal() as db_fresh:
        sb1 = await db_fresh.get(StockBatch, 1)
        sb2 = await db_fresh.get(StockBatch, 2)
        sb3 = await db_fresh.get(StockBatch, 3)

        assert sb1.current_balance == Decimal("0.000")
        assert sb1.status == "DEPLETED"

        assert sb2.current_balance == Decimal("40.000")
        assert sb2.status == "ACTIVE"

        assert sb3.current_balance == Decimal("50.000")
        assert sb3.status == "ACTIVE"

        m = await db_fresh.get(Material, 1)
        assert m.remaining_stock == Decimal("90.000")
        assert m.quantity_used == Decimal("60.000")

        # ProjectMaterialValuation in MySQL:
        # Remaining: 40 @ 60 = 2400, 50 @ 55 = 2750 -> total 5150 / 90 = 57.22
        v = (await db_fresh.execute(
            select(ProjectMaterialValuation).where(
                and_(
                    ProjectMaterialValuation.project_id == p1.id,
                    ProjectMaterialValuation.material_master_id == mm1.id,
                )
            )
        )).scalar_one()

        assert v.total_purchased_quantity == Decimal("90.000")
        assert v.total_purchased_amount == Decimal("5150.00")
        assert v.current_moving_weighted_average_rate == Decimal("57.22")


# =========================================================================
# TEST 3: ADJUSTMENT IDEMPOTENCY
# =========================================================================
@pytest.mark.asyncio
async def test_adjustment_idempotency():
    async with TestSessionLocal() as db:
        _, _, p1, _, mm1, s1, admin1, _ = await seed_base_data(db)

        mat = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("50.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"), total_amount=Decimal("5000.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("5000.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        db.add(mat)
        await db.commit()

        req = InventoryAdjustRequest(
            material_id=1,
            new_stock=120.0,
            reason="Idempotency verification",
        )

        # First request with Idempotency-Key
        res1 = await adjust_inventory(
            payload=req,
            db=db,
            current_user=admin1,
            redis=MockRedis(),
            idempotency_key="IDEM-ADJ-TEST-001",
        )
        assert res1.new_stock == 120.0

        # Second request with SAME Idempotency-Key: returns idempotent response
        res2 = await adjust_inventory(
            payload=req,
            db=db,
            current_user=admin1,
            redis=MockRedis(),
            idempotency_key="IDEM-ADJ-TEST-001",
        )
        assert res2.new_stock == 120.0

    # Verify stock was only changed ONCE in MySQL (120, not 140)
    async with TestSessionLocal() as db_fresh:
        m = await db_fresh.get(Material, 1)
        assert m.remaining_stock == Decimal("120.000")


# =========================================================================
# TEST 4: TENANT BOUNDARY ISOLATION
# =========================================================================
@pytest.mark.asyncio
async def test_adjustment_tenant_isolation():
    async with TestSessionLocal() as db:
        _, _, p1, _, mm1, s1, _, admin2 = await seed_base_data(db)

        mat = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("50.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"), total_amount=Decimal("5000.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("5000.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        db.add(mat)
        await db.commit()

        # admin2 from Company 2 attempts to adjust Company 1's material
        req = InventoryAdjustRequest(
            material_id=1,
            new_stock=150.0,
            reason="Unauthorized adjustment attempt",
        )
        with pytest.raises(Exception) as exc_info:
            await adjust_inventory(
                payload=req,
                db=db,
                current_user=admin2,
                redis=MockRedis(),
            )
        # Raises NotFoundError / HTTPException(404)
        assert "Material not found" in str(exc_info.value)
