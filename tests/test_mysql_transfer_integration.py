import asyncio
from datetime import datetime, date
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
from app.models.material import Supplier, Material, MaterialTransfer, MaterialTransaction, MaterialLedger
from app.models.inventory import StockBatch, ProjectMaterialValuation
from app.models.user import User, UserRole
from app.core.enums import ProjectStatus, RateType
from app.api.material import update_transfer_status
from app.schemas.material import TransferStatusUpdate
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
    p2 = Project(id=2, business_id="PRJ-002", company_id=1, owner_id=1, project_name="Site B", status=ProjectStatus.ONGOING)
    p3 = Project(id=3, business_id="PRJ-003", company_id=2, owner_id=2, project_name="Site C (Tenant 2)", status=ProjectStatus.ONGOING)
    db.add_all([p1, p2, p3])

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
    return c1, c2, p1, p2, p3, mm1, s1, u_admin1, u_admin2


# =========================================================================
# TEST A: MULTI-BATCH FIFO DEPLETION IN REAL MYSQL
# =========================================================================
@pytest.mark.asyncio
async def test_a_multi_batch_fifo_depletion():
    async with TestSessionLocal() as db:
        _, _, p1, p2, _, mm1, s1, admin1, _ = await seed_base_data(db)

        # Site A Material: total 450 units, 25250 total amount
        mat_a = Material(
            id=1,
            material_code="MAT-001",
            project_id=p1.id,
            material_master_id=mm1.id,
            material_name="TMT Steel 12mm",
            category="Steel",
            unit_id=1,
            supplier_id=s1.id,
            purchase_rate=Decimal("56.11"),
            rate_type=RateType.FIXED,
            quantity_purchased=Decimal("450.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("450.000"),
            total_amount=Decimal("25250.00"),
            payment_given=Decimal("0.00"),
            payment_pending=Decimal("25250.00"),
            minimum_stock_level=Decimal("50.000"),
        )
        db.add(mat_a)

        # 3 Batches at Site A
        b1 = StockBatch(
            id=1,
            batch_number="BATCH-001",
            project_id=p1.id,
            material_master_id=mm1.id,
            supplier_id=s1.id,
            initial_quantity=Decimal("100.000"),
            current_balance=Decimal("100.000"),
            purchase_rate=Decimal("50.00"),
            receipt_date=datetime(2026, 1, 1, 10, 0, 0),
            batch_source="GRN",
            status="ACTIVE",
        )
        b2 = StockBatch(
            id=2,
            batch_number="BATCH-002",
            project_id=p1.id,
            material_master_id=mm1.id,
            supplier_id=s1.id,
            initial_quantity=Decimal("200.000"),
            current_balance=Decimal("200.000"),
            purchase_rate=Decimal("60.00"),
            receipt_date=datetime(2026, 1, 15, 10, 0, 0),
            batch_source="GRN",
            status="ACTIVE",
        )
        b3 = StockBatch(
            id=3,
            batch_number="BATCH-003",
            project_id=p1.id,
            material_master_id=mm1.id,
            supplier_id=s1.id,
            initial_quantity=Decimal("150.000"),
            current_balance=Decimal("150.000"),
            purchase_rate=Decimal("55.00"),
            receipt_date=datetime(2026, 2, 1, 10, 0, 0),
            batch_source="GRN",
            status="ACTIVE",
        )
        db.add_all([b1, b2, b3])

        # Project Material Valuation at Site A
        val_a = ProjectMaterialValuation(
            project_id=p1.id,
            material_master_id=mm1.id,
            total_purchased_quantity=Decimal("450.000"),
            total_purchased_amount=Decimal("25250.00"),
            current_moving_weighted_average_rate=Decimal("56.11"),
        )
        db.add(val_a)

        # Transfer 250 units from Site A to Site B
        tr = MaterialTransfer(
            id=1,
            material_id=mat_a.id,
            from_project_id=p1.id,
            to_project_id=p2.id,
            quantity=Decimal("250.000"),
            status="PENDING",
            reference_id="TRF-001",
        )
        db.add(tr)
        await db.commit()

        # Execute complete transfer via endpoint logic against real MySQL
        res = await update_transfer_status(
            id=tr.id,
            payload=TransferStatusUpdate(status="COMPLETED"),
            current_user=admin1,
            db=db,
            redis=MockRedis(),
        )

        assert res.status == "COMPLETED"

    # Query with a FRESH session directly from MySQL
    async with TestSessionLocal() as db_fresh:
        # Check source batches
        sb1 = await db_fresh.get(StockBatch, 1)
        sb2 = await db_fresh.get(StockBatch, 2)
        sb3 = await db_fresh.get(StockBatch, 3)

        assert sb1.current_balance == Decimal("0.000")
        assert sb1.status == "DEPLETED"

        assert sb2.current_balance == Decimal("50.000")
        assert sb2.status == "ACTIVE"

        assert sb3.current_balance == Decimal("150.000")
        assert sb3.status == "ACTIVE"

        # Check Source Material
        mat_src = await db_fresh.get(Material, 1)
        assert mat_src.remaining_stock == Decimal("200.000")
        assert mat_src.quantity_used == Decimal("250.000")

        # Check Source Valuation:
        # Remaining: 50 @ 60 = 3000, 150 @ 55 = 8250 -> total 11250 / 200 = 56.25
        val_src = (await db_fresh.execute(
            select(ProjectMaterialValuation).where(
                and_(
                    ProjectMaterialValuation.project_id == p1.id,
                    ProjectMaterialValuation.material_master_id == mm1.id,
                )
            )
        )).scalar_one()

        assert val_src.total_purchased_quantity == Decimal("200.000")
        assert val_src.total_purchased_amount == Decimal("11250.00")
        assert val_src.current_moving_weighted_average_rate == Decimal("56.25")


# =========================================================================
# TEST B: DESTINATION PROVENANCE PRESERVING RATES AND BATCH SOURCE
# =========================================================================
@pytest.mark.asyncio
async def test_b_destination_provenance():
    async with TestSessionLocal() as db:
        _, _, p1, p2, _, mm1, s1, admin1, _ = await seed_base_data(db)

        mat_a = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("56.11"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("450.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("450.000"), total_amount=Decimal("25250.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("25250.00"),
            minimum_stock_level=Decimal("50.000"),
        )
        b1 = StockBatch(
            id=1, batch_number="BATCH-001", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("100.000"), current_balance=Decimal("100.000"),
            purchase_rate=Decimal("50.00"), receipt_date=datetime(2026, 1, 1), status="ACTIVE",
        )
        b2 = StockBatch(
            id=2, batch_number="BATCH-002", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("200.000"), current_balance=Decimal("200.000"),
            purchase_rate=Decimal("60.00"), receipt_date=datetime(2026, 1, 15), status="ACTIVE",
        )
        tr = MaterialTransfer(
            id=1, material_id=1, from_project_id=p1.id, to_project_id=p2.id,
            quantity=Decimal("250.000"), status="PENDING", reference_id="TRF-PROV-1",
        )
        db.add_all([mat_a, b1, b2, tr])
        await db.commit()

        await update_transfer_status(
            id=tr.id,
            payload=TransferStatusUpdate(status="COMPLETED"),
            current_user=admin1,
            db=db,
            redis=MockRedis(),
        )

    # Check batches created at Site B
    async with TestSessionLocal() as db_fresh:
        dest_batches = (await db_fresh.execute(
            select(StockBatch)
            .where(
                and_(
                    StockBatch.project_id == p2.id,
                    StockBatch.material_master_id == mm1.id,
                )
            )
            .order_by(StockBatch.id.asc())
        )).scalars().all()

        assert len(dest_batches) >= 2
        # First batch at destination from b1 (100 units @ Rs 50)
        assert dest_batches[0].current_balance == Decimal("100.000")
        assert dest_batches[0].purchase_rate == Decimal("50.00")
        assert dest_batches[0].batch_source == "TRANSFER_IN"
        assert dest_batches[0].status == "ACTIVE"

        # Second batch at destination from b2 (150 units @ Rs 60)
        assert dest_batches[1].current_balance == Decimal("150.000")
        assert dest_batches[1].purchase_rate == Decimal("60.00")
        assert dest_batches[1].batch_source == "TRANSFER_IN"
        assert dest_batches[1].status == "ACTIVE"


# =========================================================================
# TEST C: SOURCE & DESTINATION MWA MATH PERSISTED IN MYSQL
# =========================================================================
@pytest.mark.asyncio
async def test_c_source_and_destination_mwa_math():
    async with TestSessionLocal() as db:
        _, _, p1, p2, _, mm1, s1, admin1, _ = await seed_base_data(db)

        # Site A has 450 units, valuation 25250 (100@50, 200@60, 150@55)
        mat_a = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("56.11"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("450.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("450.000"), total_amount=Decimal("25250.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("25250.00"),
            minimum_stock_level=Decimal("50.000"),
        )
        b1 = StockBatch(
            id=1, batch_number="BATCH-001", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("100.000"), current_balance=Decimal("100.000"),
            purchase_rate=Decimal("50.00"), receipt_date=datetime(2026, 1, 1), status="ACTIVE",
        )
        b2 = StockBatch(
            id=2, batch_number="BATCH-002", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("200.000"), current_balance=Decimal("200.000"),
            purchase_rate=Decimal("60.00"), receipt_date=datetime(2026, 1, 15), status="ACTIVE",
        )
        val_a = ProjectMaterialValuation(
            project_id=p1.id, material_master_id=mm1.id,
            total_purchased_quantity=Decimal("450.000"), total_purchased_amount=Decimal("25250.00"),
            current_moving_weighted_average_rate=Decimal("56.11"),
        )

        # Site B already has existing stock: 100 units @ Rs 70 (valuation 7000.00)
        mat_b = Material(
            id=2, material_code="MAT-002", project_id=p2.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("70.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"), total_amount=Decimal("7000.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("7000.00"),
            minimum_stock_level=Decimal("20.000"),
        )
        b_dest_existing = StockBatch(
            id=10, batch_number="BATCH-DEST-001", project_id=p2.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("100.000"), current_balance=Decimal("100.000"),
            purchase_rate=Decimal("70.00"), receipt_date=datetime(2026, 1, 5), status="ACTIVE",
        )
        val_b = ProjectMaterialValuation(
            project_id=p2.id, material_master_id=mm1.id,
            total_purchased_quantity=Decimal("100.000"), total_purchased_amount=Decimal("7000.00"),
            current_moving_weighted_average_rate=Decimal("70.00"),
        )

        tr = MaterialTransfer(
            id=1, material_id=1, from_project_id=p1.id, to_project_id=p2.id,
            quantity=Decimal("250.000"), status="PENDING", reference_id="TRF-MWA-1",
        )
        db.add_all([mat_a, b1, b2, val_a, mat_b, b_dest_existing, val_b, tr])
        await db.commit()

        # Complete transfer of 250 units: 100 @ 50 (5000) + 150 @ 60 (9000) = 14000
        await update_transfer_status(
            id=tr.id,
            payload=TransferStatusUpdate(status="COMPLETED"),
            current_user=admin1,
            db=db,
            redis=MockRedis(),
        )

    # Verify Destination MWA Math in MySQL:
    # Existing: 100 @ 7000. Transferred: 250 @ 14000. Total Qty: 350. Total Val: 21000.
    # MWA = 21000 / 350 = 60.00!
    async with TestSessionLocal() as db_fresh:
        dest_val = (await db_fresh.execute(
            select(ProjectMaterialValuation).where(
                and_(
                    ProjectMaterialValuation.project_id == p2.id,
                    ProjectMaterialValuation.material_master_id == mm1.id,
                )
            )
        )).scalar_one()

        assert dest_val.total_purchased_quantity == Decimal("350.000")
        assert dest_val.total_purchased_amount == Decimal("21000.00")
        assert dest_val.current_moving_weighted_average_rate == Decimal("60.00")


# =========================================================================
# TEST D: INSUFFICIENT STOCK REJECTION WITHOUT MUTATION
# =========================================================================
@pytest.mark.asyncio
async def test_d_insufficient_stock_rejection():
    async with TestSessionLocal() as db:
        _, _, p1, p2, _, mm1, s1, admin1, _ = await seed_base_data(db)

        mat_a = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("50.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"), total_amount=Decimal("5000.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("5000.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        b1 = StockBatch(
            id=1, batch_number="BATCH-001", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("100.000"), current_balance=Decimal("100.000"),
            purchase_rate=Decimal("50.00"), receipt_date=datetime(2026, 1, 1), status="ACTIVE",
        )
        tr = MaterialTransfer(
            id=1, material_id=1, from_project_id=p1.id, to_project_id=p2.id,
            quantity=Decimal("200.000"),  # Attempt to transfer 200 when only 100 available
            status="PENDING", reference_id="TRF-FAIL-1",
        )
        db.add_all([mat_a, b1, tr])
        await db.commit()

        # Should raise 400 Insufficient stock
        with pytest.raises(HTTPException) as exc_info:
            await update_transfer_status(
                id=tr.id,
                payload=TransferStatusUpdate(status="COMPLETED"),
                current_user=admin1,
                db=db,
                redis=MockRedis(),
            )
        assert exc_info.value.status_code == 400
        assert "Insufficient stock" in exc_info.value.detail

    # Verify real MySQL state remains untouched
    async with TestSessionLocal() as db_fresh:
        sb = await db_fresh.get(StockBatch, 1)
        assert sb.current_balance == Decimal("100.000")
        assert sb.status == "ACTIVE"

        m = await db_fresh.get(Material, 1)
        assert m.remaining_stock == Decimal("100.000")

        tr_obj = await db_fresh.get(MaterialTransfer, 1)
        assert tr_obj.status == "PENDING"


# =========================================================================
# TEST E: TRANSACTION ROLLBACK INTEGRITY
# =========================================================================
@pytest.mark.asyncio
async def test_e_transaction_rollback():
    from unittest.mock import AsyncMock

    async with TestSessionLocal() as db:
        _, _, p1, p2, _, mm1, s1, admin1, _ = await seed_base_data(db)

        mat_a = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("50.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"), total_amount=Decimal("5000.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("5000.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        b1 = StockBatch(
            id=1, batch_number="BATCH-001", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("100.000"), current_balance=Decimal("100.000"),
            purchase_rate=Decimal("50.00"), receipt_date=datetime(2026, 1, 1), status="ACTIVE",
        )
        tr = MaterialTransfer(
            id=1, material_id=1, from_project_id=p1.id, to_project_id=p2.id,
            quantity=Decimal("50.000"), status="PENDING", reference_id="TRF-ROLLBACK",
        )
        db.add_all([mat_a, b1, tr])
        await db.commit()

        # Simulate unexpected failure right before final commit to test rollback
        original_commit = db.commit
        db.commit = AsyncMock(side_effect=RuntimeError("Simulated database failure during transfer commit"))

        with pytest.raises(RuntimeError, match="Simulated database failure"):
            try:
                await update_transfer_status(
                    id=tr.id,
                    payload=TransferStatusUpdate(status="COMPLETED"),
                    current_user=admin1,
                    db=db,
                    redis=MockRedis(),
                )
            except Exception:
                await db.rollback()
                raise

    # Verify with completely fresh session that rollback was atomic and MySQL state remains intact
    async with TestSessionLocal() as db_fresh:
        sb = await db_fresh.get(StockBatch, 1)
        assert sb.current_balance == Decimal("100.000")
        assert sb.status == "ACTIVE"

        m = await db_fresh.get(Material, 1)
        assert m.remaining_stock == Decimal("100.000")
        assert m.quantity_used == Decimal("0.000")

        # Destination batches must NOT exist
        dest_batches = (await db_fresh.execute(
            select(StockBatch).where(StockBatch.project_id == 2)
        )).scalars().all()
        assert len(dest_batches) == 0


# =========================================================================
# TEST F: TRANSFER IDEMPOTENCY PREVENTING DOUBLE DEPLETION
# =========================================================================
@pytest.mark.asyncio
async def test_f_transfer_idempotency():
    async with TestSessionLocal() as db:
        _, _, p1, p2, _, mm1, s1, admin1, _ = await seed_base_data(db)

        mat_a = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("50.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"), total_amount=Decimal("5000.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("5000.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        b1 = StockBatch(
            id=1, batch_number="BATCH-001", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("100.000"), current_balance=Decimal("100.000"),
            purchase_rate=Decimal("50.00"), receipt_date=datetime(2026, 1, 1), status="ACTIVE",
        )
        tr = MaterialTransfer(
            id=1, material_id=1, from_project_id=p1.id, to_project_id=p2.id,
            quantity=Decimal("50.000"), status="PENDING", reference_id="TRF-IDEMPOTENT",
        )
        db.add_all([mat_a, b1, tr])
        await db.commit()

        # First completion: succeeds
        await update_transfer_status(
            id=tr.id,
            payload=TransferStatusUpdate(status="COMPLETED"),
            current_user=admin1,
            db=db,
            redis=MockRedis(),
        )

        # Second completion: must raise 400 Transfer is already completed
        with pytest.raises(HTTPException) as exc_info:
            await update_transfer_status(
                id=tr.id,
                payload=TransferStatusUpdate(status="COMPLETED"),
                current_user=admin1,
                db=db,
                redis=MockRedis(),
            )
        assert exc_info.value.status_code == 400
        assert "Transfer is already completed" in exc_info.value.detail

    # Assert stock was only depleted ONCE (50 units remaining)
    async with TestSessionLocal() as db_fresh:
        sb = await db_fresh.get(StockBatch, 1)
        assert sb.current_balance == Decimal("50.000")


# =========================================================================
# TEST G: CONCURRENT TRANSFER COMPLETION ACROSS INDEPENDENT DB SESSIONS
# =========================================================================
@pytest.mark.asyncio
async def test_g_concurrent_transfer_completion():
    async with TestSessionLocal() as db_init:
        _, _, p1, p2, _, mm1, s1, admin1, _ = await seed_base_data(db_init)

        # Stock = 100 units total
        mat_a = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("50.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"), total_amount=Decimal("5000.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("5000.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        b1 = StockBatch(
            id=1, batch_number="BATCH-001", project_id=p1.id, material_master_id=mm1.id,
            supplier_id=s1.id, initial_quantity=Decimal("100.000"), current_balance=Decimal("100.000"),
            purchase_rate=Decimal("50.00"), receipt_date=datetime(2026, 1, 1), status="ACTIVE",
        )
        # Two transfers for 70 units each. Both cannot succeed because 70 + 70 = 140 > 100.
        tr1 = MaterialTransfer(
            id=1, material_id=1, from_project_id=p1.id, to_project_id=p2.id,
            quantity=Decimal("70.000"), status="PENDING", reference_id="TRF-CONC-1",
        )
        tr2 = MaterialTransfer(
            id=2, material_id=1, from_project_id=p1.id, to_project_id=p2.id,
            quantity=Decimal("70.000"), status="PENDING", reference_id="TRF-CONC-2",
        )
        db_init.add_all([mat_a, b1, tr1, tr2])
        await db_init.commit()

    # Run both concurrently in 2 independent DB sessions
    async def worker(transfer_id: int):
        async with TestSessionLocal() as db_worker:
            u = await db_worker.get(User, 1)
            try:
                res = await update_transfer_status(
                    id=transfer_id,
                    payload=TransferStatusUpdate(status="COMPLETED"),
                    current_user=u,
                    db=db_worker,
                    redis=MockRedis(),
                )
                return ("SUCCESS", transfer_id, res)
            except HTTPException as e:
                return ("ERROR", transfer_id, e.status_code, e.detail)

    results = await asyncio.gather(worker(1), worker(2))

    successes = [r for r in results if r[0] == "SUCCESS"]
    errors = [r for r in results if r[0] == "ERROR"]

    # Exactly ONE must succeed and ONE must fail with 400 Insufficient stock
    assert len(successes) == 1
    assert len(errors) == 1
    assert errors[0][2] == 400
    assert "Insufficient stock" in errors[0][3]

    # In MySQL, current_balance must be exactly 30 (100 - 70) and NEVER negative
    async with TestSessionLocal() as db_fresh:
        sb = await db_fresh.get(StockBatch, 1)
        assert sb.current_balance == Decimal("30.000")
        assert sb.current_balance >= Decimal("0.000")


# =========================================================================
# TEST H: TENANT BOUNDARY ISOLATION
# =========================================================================
@pytest.mark.asyncio
async def test_h_tenant_boundary_isolation():
    async with TestSessionLocal() as db:
        _, _, p1, p2, p3, mm1, s1, _, admin2 = await seed_base_data(db)

        # Company 1 transfer
        mat_a = Material(
            id=1, material_code="MAT-001", project_id=p1.id, material_master_id=mm1.id,
            material_name="TMT Steel 12mm", category="Steel", unit_id=1, supplier_id=s1.id,
            purchase_rate=Decimal("50.00"), rate_type=RateType.FIXED,
            quantity_purchased=Decimal("100.000"), quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"), total_amount=Decimal("5000.00"),
            payment_given=Decimal("0.00"), payment_pending=Decimal("5000.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        tr = MaterialTransfer(
            id=1, material_id=1, from_project_id=p1.id, to_project_id=p2.id,
            quantity=Decimal("50.000"), status="PENDING", reference_id="TRF-TENANT",
        )
        db.add_all([mat_a, tr])
        await db.commit()

        # admin2 belongs to Company 2. They attempt to complete Company 1's transfer
        with pytest.raises(HTTPException) as exc_info:
            await update_transfer_status(
                id=tr.id,
                payload=TransferStatusUpdate(status="COMPLETED"),
                current_user=admin2,
                db=db,
                redis=MockRedis(),
            )
        assert exc_info.value.status_code == 404
        assert "Transfer not found" in exc_info.value.detail
