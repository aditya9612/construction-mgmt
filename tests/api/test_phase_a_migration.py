import uuid
from decimal import Decimal
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.main  # Ensures all ORM models are registered in SQLAlchemy registry
from app.core.config import settings as app_settings
from app.core.enums import AccountType
from app.models.accountant import Account
from app.models.company import Company
from app.models.equipment import Equipment
from app.models.owner import Owner
from app.models.project import Project
from app.models.settings import CompanySettings
from app.utils.accounting import seed_company_chart_of_accounts
from tests.test_db_safety_guard import TEST_MYSQL_URL_ASYNC, validate_safe_test_environment


def _create_isolated_sessionmaker(db_url: str):
    """Fail closed on any non-test database before constructing an engine or session."""
    validate_safe_test_environment(db_url)
    engine = create_async_engine(db_url, poolclass=NullPool, echo=False)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, session_factory


# Enforce isolated port 3309 integration database at module load (never app.core.db / construction_management)
validate_safe_test_environment(TEST_MYSQL_URL_ASYNC)
test_engine, TestSessionLocal = _create_isolated_sessionmaker(TEST_MYSQL_URL_ASYNC)


@pytest.fixture(autouse=True)
def enforce_isolated_test_db():
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC)
    yield


def test_phase_a_migration_blocks_non_test_db():
    """Verify attempting to bind against configured construction_management or port 3306 fails closed before writes."""
    with pytest.raises(RuntimeError, match="SAFETY VIOLATION"):
        _create_isolated_sessionmaker(app_settings.DATABASE_URL_ASYNC)

    with pytest.raises(RuntimeError, match="SAFETY VIOLATION"):
        _create_isolated_sessionmaker(
            "mysql+asyncmy://user:pass@localhost:3306/construction_management"
        )


@pytest.mark.asyncio
async def test_equipment_company_id_backfill_and_integrity():
    """
    Verify Equipment.company_id migration behavior using isolated test-owned records:
    - Project-assigned equipment receives correct company_id on backfill.
    - FK integrity is enforced (cannot assign non-existent company_id).
    - Test-owned equipment records are preserved during test and cleaned up afterward.
    """
    uid = uuid.uuid4().hex[:8]
    comp_id = None
    owner_id = None
    proj_id = None
    eq_ids = []

    try:
        async with TestSessionLocal() as db:
            db_name, port = (await db.execute(text("SELECT DATABASE(), @@port"))).one()
            assert db_name == "construction_management_integration_test"
            assert port == 3309

            comp = Company(name=f"PhaseA Co {uid}", subdomain=f"phasea-{uid}", is_active=True)
            db.add(comp)
            await db.flush()
            comp_id = int(comp.id)

            owner = Owner(
                company_id=comp_id,
                owner_code=f"OWN-PHA-{uid}",
                owner_name=f"PhaseA Owner {uid}",
                mobile=f"98{uuid.uuid4().int % 100000000:08d}",
            )
            db.add(owner)
            await db.flush()
            owner_id = int(owner.id)

            proj = Project(
                business_id=f"PRJ-PHA-{uid}",
                company_id=comp_id,
                owner_id=owner_id,
                project_name=f"PhaseA Site {uid}",
                status="ONGOING",
                type="RESIDENTIAL",
                location_type="URBAN",
            )
            db.add(proj)
            await db.flush()
            proj_id = int(proj.id)

            eq_missing = Equipment(
                company_id=None,
                project_id=proj_id,
                equipment_name=f"PhaseA Unassigned Crane {uid}",
                equipment_code=f"PHA-NULL-{uid}",
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
                rental_cost=Decimal("0.0"),
            )
            eq_preset = Equipment(
                company_id=comp_id,
                project_id=proj_id,
                equipment_name=f"PhaseA Assigned Loader {uid}",
                equipment_code=f"PHA-SET-{uid}",
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
                rental_cost=Decimal("0.0"),
            )
            db.add_all([eq_missing, eq_preset])
            await db.commit()
            eq_ids = [int(eq_missing.id), int(eq_preset.id)]

            # 1. Verify test-owned equipment count is present
            total_eq = (
                await db.execute(
                    text("SELECT COUNT(*) FROM equipment WHERE id IN (:id1, :id2);"),
                    {"id1": eq_ids[0], "id2": eq_ids[1]},
                )
            ).scalar()
            assert total_eq == 2, "Test-owned equipment records must be preserved"

            # 2. Apply backfill strictly scoped to test-owned equipment rows
            await db.execute(
                text(
                    """
                    UPDATE equipment e
                    INNER JOIN projects p ON e.project_id = p.id
                    SET e.company_id = p.company_id
                    WHERE e.id IN (:id1, :id2)
                      AND e.company_id IS NULL
                      AND p.company_id IS NOT NULL;
                    """
                ),
                {"id1": eq_ids[0], "id2": eq_ids[1]},
            )
            await db.commit()

            res = await db.execute(
                text(
                    """
                    SELECT e.id, e.company_id, p.company_id as project_company_id
                    FROM equipment e
                    INNER JOIN projects p ON e.project_id = p.id
                    WHERE e.id IN (:id1, :id2);
                    """
                ),
                {"id1": eq_ids[0], "id2": eq_ids[1]},
            )
            assigned_rows = res.fetchall()
            assert len(assigned_rows) == 2, "Assigned test equipment records must exist"
            for row in assigned_rows:
                assert row[1] == comp_id
                assert row[1] == row[2], f"Equipment {row[0]} company_id {row[1]} does not match project company_id {row[2]}"

            # 3. Test FK constraint: inserting equipment with non-existent company_id must fail
            invalid_eq = Equipment(
                company_id=999999999,
                equipment_name="Invalid Tenant Crane",
                equipment_code=f"INV-EQ-{uid}",
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
                rental_cost=Decimal("0.0"),
            )
            db.add(invalid_eq)
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()
    finally:
        async with TestSessionLocal() as cleanup_db:
            if eq_ids:
                await cleanup_db.execute(delete(Equipment).where(Equipment.id.in_(eq_ids)))
            if proj_id is not None:
                await cleanup_db.execute(delete(Project).where(Project.id == proj_id))
            if owner_id is not None:
                await cleanup_db.execute(delete(Owner).where(Owner.id == owner_id))
            if comp_id is not None:
                await cleanup_db.execute(delete(Company).where(Company.id == comp_id))
            await cleanup_db.commit()

            # Verify zero leftover test-owned rows
            if eq_ids:
                assert (await cleanup_db.scalar(select(Equipment).where(Equipment.id.in_(eq_ids)))) is None
            if comp_id is not None:
                assert (await cleanup_db.scalar(select(Company).where(Company.id == comp_id))) is None


@pytest.mark.asyncio
async def test_accounts_seeding_and_idempotency():
    """
    Verify Chart of Accounts initialization on isolated test companies:
    - Required system accounts are created per company.
    - Running seed_company_chart_of_accounts repeatedly is idempotent.
    - Composite uniqueness (company_id, code) rejects duplicates while allowing cross-tenant reuse.
    - All test-created records are removed cleanly on teardown.
    """
    uid = uuid.uuid4().hex[:8]
    created_comp_ids = []

    try:
        async with TestSessionLocal() as db:
            db_name, port = (await db.execute(text("SELECT DATABASE(), @@port"))).one()
            assert db_name == "construction_management_integration_test"
            assert port == 3309

            c1 = Company(name=f"PhaseA Acct1 {uid}", subdomain=f"pha-a1-{uid}", is_active=True)
            c2 = Company(name=f"PhaseA Acct2 {uid}", subdomain=f"pha-a2-{uid}", is_active=True)
            db.add_all([c1, c2])
            await db.commit()
            target_company_id = int(c1.id)
            comp2_id = int(c2.id)
            created_comp_ids = [target_company_id, comp2_id]

            # 1. Seed initial Chart of Accounts for Company 1 and verify required codes
            await seed_company_chart_of_accounts(db, target_company_id)
            await db.commit()

            accounts = (await db.scalars(select(Account).where(Account.company_id == target_company_id))).all()
            account_codes = {a.code for a in accounts}
            required_codes = {
                "GENERAL_EXPENSE",
                "VENDOR_PAYABLE",
                "INPUT_GST",
                "ACCOUNTS_RECEIVABLE",
                "SALES_REVENUE",
                "WAGES_PAYABLE",
                "LABOUR_EXPENSE",
                "BANK",
                "CASH",
                "PETTY_CASH",
            }
            missing_codes = required_codes - account_codes
            assert not missing_codes, f"Company {target_company_id} is missing system accounts: {missing_codes}"

            # 2. Verify idempotency: run seed again on same company
            initial_count = len(accounts)
            await seed_company_chart_of_accounts(db, target_company_id)
            await db.commit()

            accounts_after = (await db.scalars(select(Account).where(Account.company_id == target_company_id))).all()
            assert len(accounts_after) == initial_count, "Idempotent seed must not create duplicate accounts"

            # 3. Verify composite uniqueness: inserting duplicate (company_id, code) must fail
            dup_acc = Account(
                company_id=target_company_id,
                name="Duplicate Expense",
                code="GENERAL_EXPENSE",
                type=AccountType.EXPENSE,
            )
            db.add(dup_acc)
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

            # 4. Verify multi-tenancy: Company 2 can also have its own GENERAL_EXPENSE account
            await seed_company_chart_of_accounts(db, comp2_id)
            await db.commit()

            acc_comp2 = await db.scalar(
                select(Account).where(Account.company_id == comp2_id, Account.code == "GENERAL_EXPENSE")
            )
            assert acc_comp2 is not None, f"Company {comp2_id} must also have its own GENERAL_EXPENSE account"
            assert acc_comp2.company_id == comp2_id
            assert acc_comp2.company_id != target_company_id
    finally:
        if created_comp_ids:
            async with TestSessionLocal() as cleanup_db:
                await cleanup_db.execute(
                    delete(CompanySettings).where(CompanySettings.company_id.in_(created_comp_ids))
                )
                await cleanup_db.execute(
                    delete(Account).where(Account.company_id.in_(created_comp_ids))
                )
                await cleanup_db.execute(
                    delete(Company).where(Company.id.in_(created_comp_ids))
                )
                await cleanup_db.commit()

                assert (
                    await cleanup_db.scalar(select(Account).where(Account.company_id.in_(created_comp_ids)))
                ) is None
                assert (
                    await cleanup_db.scalar(select(Company).where(Company.id.in_(created_comp_ids)))
                ) is None
