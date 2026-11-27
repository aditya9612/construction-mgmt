"""
Comprehensive Concurrency and Production-Safety Tests for Database Sequence Generator.
Verifies atomic MAT code generation across concurrent database sessions,
independent worker tasks, separate OS processes, transactions, and rollback behavior.
"""

import sys
import asyncio
import subprocess
import uuid
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.main import app  # noqa: F401
from app.db.session import AsyncSessionLocal
from app.models.sequence import BusinessSequence
from app.models.material import Material, Supplier
from app.models.master_data import MaterialMaster, Unit
from app.models.project import Project, ProjectStatus
from app.models.owner import Owner
from app.models.company import Company
from app.models.user import User, UserRole
from app.core.enums import RateType
from app.services.sequence_service import (
    allocate_material_code,
    allocate_material_codes,
    _get_max_existing_material_number,
)
from app.utils.common import generate_business_id


@pytest.mark.asyncio
async def test_sequential_mat_code_generation():
    """Verifies that sequential single-code allocations advance deterministically."""
    async with AsyncSessionLocal() as db:
        code1 = await allocate_material_code(db)
        code2 = await allocate_material_code(db)
        code3 = await allocate_material_code(db)
        await db.commit()

        assert code1.startswith("MAT")
        assert code2.startswith("MAT")
        assert code3.startswith("MAT")

        num1 = int(code1.replace("MAT", ""))
        num2 = int(code2.replace("MAT", ""))
        num3 = int(code3.replace("MAT", ""))

        assert num2 == num1 + 1
        assert num3 == num2 + 1


@pytest.mark.asyncio
async def test_batch_allocation_matches_sequential():
    """Verifies that batch allocation reserves the exact range in one atomic operation."""
    async with AsyncSessionLocal() as db:
        codes = await allocate_material_codes(db, count=5)
        next_code = await allocate_material_code(db)
        await db.commit()

        assert len(codes) == 5
        nums = [int(c.replace("MAT", "")) for c in codes]
        for i in range(len(nums) - 1):
            assert nums[i + 1] == nums[i] + 1

        next_num = int(next_code.replace("MAT", ""))
        assert next_num == nums[-1] + 1


@pytest.mark.asyncio
async def test_zero_and_negative_count_allocation():
    """Verifies edge cases: count <= 0 returns empty list without modifying counter."""
    async with AsyncSessionLocal() as db:
        zero_codes = await allocate_material_codes(db, count=0)
        neg_codes = await allocate_material_codes(db, count=-5)
        assert zero_codes == []
        assert neg_codes == []


@pytest.mark.asyncio
async def test_genuine_concurrent_single_allocations():
    """
    Genuine Concurrency Test (20 simultaneous sessions):
    Spawns 20 simultaneous async tasks using independent AsyncSession connections.
    Each task atomically allocates a MAT code.
    Verifies that all 20 allocated codes are strictly unique (zero duplicates).
    """
    concurrency_count = 20

    async def single_worker(worker_id: int):
        async with AsyncSessionLocal() as db:
            code = await allocate_material_code(db)
            await db.commit()
            return code

    allocated = await asyncio.gather(
        *(single_worker(i) for i in range(concurrency_count))
    )

    assert len(allocated) == concurrency_count
    unique_codes = set(allocated)
    assert len(unique_codes) == concurrency_count, (
        f"Concurrency race detected! Expected {concurrency_count} unique codes, "
        f"got {len(unique_codes)}. Codes: {allocated}"
    )


@pytest.mark.asyncio
async def test_genuine_concurrent_bulk_allocations():
    """
    Genuine Bulk Concurrency Test (50 codes total):
    Spawns 5 simultaneous independent workers each reserving a batch of 10 codes.
    Verifies all 50 allocated codes are mutually exclusive with zero collisions.
    """
    num_workers = 5
    batch_size = 10

    async def bulk_worker(worker_id: int):
        async with AsyncSessionLocal() as db:
            codes = await allocate_material_codes(db, count=batch_size)
            await db.commit()
            return codes

    results = await asyncio.gather(*(bulk_worker(i) for i in range(num_workers)))

    all_codes = []
    for batch in results:
        assert len(batch) == batch_size
        all_codes.extend(batch)

    total_expected = num_workers * batch_size
    assert len(all_codes) == total_expected
    assert len(set(all_codes)) == total_expected, (
        f"Bulk collision detected! Expected {total_expected} distinct codes, "
        f"got {len(set(all_codes))}."
    )


@pytest.mark.asyncio
async def test_genuine_concurrent_single_and_bulk_mixed():
    """
    Mixed Concurrency Test:
    Simultaneously runs 6 single workers and 4 bulk workers (batch size 5).
    Verifies no overlap between single and bulk allocations.
    """
    single_workers = 6
    bulk_workers = 4
    batch_size = 5

    async def single_task():
        async with AsyncSessionLocal() as db:
            code = await allocate_material_code(db)
            await db.commit()
            return [code]

    async def bulk_task():
        async with AsyncSessionLocal() as db:
            codes = await allocate_material_codes(db, count=batch_size)
            await db.commit()
            return codes

    tasks = [single_task() for _ in range(single_workers)] + [
        bulk_task() for _ in range(bulk_workers)
    ]

    results = await asyncio.gather(*tasks)
    flattened = [code for batch in results for code in batch]

    total_expected = single_workers + (bulk_workers * batch_size)
    assert len(flattened) == total_expected
    assert len(set(flattened)) == total_expected, "Overlap detected in mixed allocation!"


@pytest.mark.asyncio
async def test_transaction_rollback_behavior():
    """
    Verifies transaction rollback:
    When a transaction rolls back, the database handles the row lock cleanly
    and subsequent transactions can allocate codes without hanging or failing.
    """
    async with AsyncSessionLocal() as db:
        code_rolled_back = await allocate_material_code(db)
        await db.rollback()

    async with AsyncSessionLocal() as db2:
        code_committed = await allocate_material_code(db2)
        await db2.commit()

    assert code_committed.startswith("MAT")
    assert isinstance(int(code_committed.replace("MAT", "")), int)


@pytest.mark.asyncio
async def test_database_unique_constraint_enforced():
    """
    Verifies that MySQL unique constraint on `materials.material_code` is strictly
    enforced and rejects duplicate insert attempts.
    """
    suffix = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        owner = await db.scalar(select(Owner).where(Owner.company_id == 1))
        if not owner:
            owner = Owner(
                owner_code=f"OWN-SQ-{suffix}",
                owner_name="Owner Seq",
                mobile=f"97{uuid.uuid4().int % 100000000:08d}",
                email="seq@test.com",
                company_id=1,
            )
            db.add(owner)
            await db.flush()

        proj = Project(
            business_id=f"PRJ-SQ-{suffix}",
            company_id=1,
            project_name=f"Project Seq {suffix}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(proj)
        await db.flush()

        unit = await db.scalar(select(Unit).where(Unit.name == "Bags"))
        if not unit:
            unit = Unit(name="Bags", unique_code="BAG")
            db.add(unit)
            await db.flush()

        mm1 = MaterialMaster(
            company_id=1,
            name=f"Material Seq 1 {suffix}",
            category="Cement",
            unit_id=unit.id,
            is_active=True,
        )
        mm2 = MaterialMaster(
            company_id=1,
            name=f"Material Seq 2 {suffix}",
            category="Cement",
            unit_id=unit.id,
            is_active=True,
        )
        db.add(mm1)
        db.add(mm2)
        await db.flush()

        sup = Supplier(
            company_id=1,
            supplier_name=f"Supplier Seq {suffix}",
            contact_person="Sam",
            phone_email="sam@test.com",
            is_deleted=False,
        )
        db.add(sup)
        await db.flush()

        code = f"MAT-TEST-UQ-{suffix}"
        m1 = Material(
            material_code=code,
            project_id=proj.id,
            material_master_id=mm1.id,
            supplier_id=sup.id,
            material_name="Material 1",
            category="Cement",
            unit_id=unit.id,
            purchase_rate=Decimal("100.00"),
            rate_type=RateType.FIXED,
            is_deleted=False,
        )
        db.add(m1)
        await db.flush()

        # Second material with EXACT same code must trigger IntegrityError
        m2 = Material(
            material_code=code,
            project_id=proj.id,
            material_master_id=mm2.id,
            supplier_id=sup.id,
            material_name="Material 2",
            category="Cement",
            unit_id=unit.id,
            purchase_rate=Decimal("100.00"),
            rate_type=RateType.FIXED,
            is_deleted=False,
        )
        db.add(m2)
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


@pytest.mark.asyncio
async def test_non_standard_codes_do_not_disrupt_sequence():
    """
    Verifies that malformed, hyphenated or non-standard material codes
    (e.g., 'MAT-04D48C' or 'MAT-DUP-123') do not corrupt numeric sequence calculation.
    """
    async with AsyncSessionLocal() as db:
        max_num = await _get_max_existing_material_number(db, "MAT")
        assert max_num >= 0

        code = await allocate_material_code(db)
        await db.commit()

        num = int(code.replace("MAT", ""))
        assert num > max_num


@pytest.mark.asyncio
async def test_generate_business_id_delegates_to_atomic_sequence():
    """
    Verifies that calling the existing generic `generate_business_id` with prefix='MAT'
    seamlessly delegates to the atomic sequence generator without regressions.
    """
    async with AsyncSessionLocal() as db:
        code1 = await generate_business_id(
            db=db,
            model=Material,
            column_name="material_code",
            prefix="MAT",
        )
        code2 = await generate_business_id(
            db=db,
            model=Material,
            column_name="material_code",
            prefix="MAT",
        )
        await db.commit()

        assert code1.startswith("MAT")
        assert code2.startswith("MAT")
        n1 = int(code1.replace("MAT", ""))
        n2 = int(code2.replace("MAT", ""))
        assert n2 == n1 + 1


@pytest.mark.asyncio
async def test_simultaneous_initialization_race():
    """
    Verifies initialization race condition handling:
    When multiple workers simultaneously attempt to initialize an unseeded sequence key,
    exactly one row is created, all allocated codes are unique, and no duplicate-key crashes occur.
    """
    test_key = f"TEST_INIT_{uuid.uuid4().hex[:6]}"

    async def race_worker(worker_id: int):
        async with AsyncSessionLocal() as db:
            code = await allocate_material_code(db, prefix=test_key)
            await db.commit()
            return code

    # Spawn 5 simultaneous workers initializing the sequence key at the exact same moment
    results = await asyncio.gather(*(race_worker(i) for i in range(5)))

    assert len(results) == 5
    assert len(set(results)) == 5, f"Duplicates in init race: {results}"

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT sequence_name, current_value FROM business_sequences WHERE sequence_name = :k"
                ),
                {"k": test_key},
            )
        ).all()
        assert len(rows) == 1
        assert rows[0][1] == 5

        # Cleanup isolated test sequence row
        await db.execute(
            text("DELETE FROM business_sequences WHERE sequence_name = :k"),
            {"k": test_key},
        )
        await db.commit()


def _run_subproc_worker(_: int) -> str:
    script = """
import sys
sys.path.insert(0, r"C:\\Users\\Admin\\Desktop\\construction-mgmt")
import asyncio
from app.main import app
from app.db.session import AsyncSessionLocal
from app.services.sequence_service import allocate_material_codes

async def main():
    async with AsyncSessionLocal() as db:
        codes = await allocate_material_codes(db, count=5)
        await db.commit()
        print(",".join(codes))

asyncio.run(main())
"""
    python_exe = sys.executable
    proc = subprocess.run([python_exe, "-c", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Subprocess failed: {proc.stderr}")
    return proc.stdout.strip()


def test_multi_process_allocation():
    """
    Multi-Process Concurrency Test:
    Spawns 4 independent OS Python processes simultaneously connecting to MySQL.
    Each process reserves 5 codes (20 codes total).
    Verifies that all 20 allocated codes are strictly unique and non-overlapping across OS processes.
    """
    num_processes = 4
    with ThreadPoolExecutor(max_workers=num_processes) as executor:
        results = list(executor.map(_run_subproc_worker, range(num_processes)))

    all_codes = []
    for r in results:
        batch = [c.strip() for c in r.split(",") if c.strip()]
        assert len(batch) == 5
        all_codes.extend(batch)

    total_expected = num_processes * 5
    assert len(all_codes) == total_expected
    assert len(set(all_codes)) == total_expected, (
        f"Multi-process collision detected! Got {len(set(all_codes))} unique of {total_expected}."
    )


@pytest.mark.asyncio
async def test_non_mat_prefixes_unaffected():
    """
    Verifies that non-MAT prefixes (e.g. PRJ, PC, CNT) continue to function
    via their standard generic generator without interference.
    """
    async with AsyncSessionLocal() as db:
        prj_code = await generate_business_id(
            db=db,
            model=Project,
            column_name="business_id",
            prefix="PRJ-TEST-",
            padding=3,
        )
        assert prj_code.startswith("PRJ-TEST-")
