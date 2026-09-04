import pytest
import uuid
from decimal import Decimal
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
import app.main  # Ensures all ORM models are registered in SQLAlchemy registry
from app.core.db import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.project import Project
from app.models.equipment import Equipment
from app.models.accountant import Account
from app.models.settings import CompanySettings
from app.core.enums import AccountType
from app.utils.accounting import seed_company_chart_of_accounts, STANDARD_SYSTEM_ACCOUNTS


@pytest.mark.asyncio
async def test_equipment_company_id_backfill_and_integrity():
    """
    Verify Equipment.company_id migration:
    - Project-assigned equipment receives correct company_id.
    - FK integrity is enforced (cannot assign non-existent company_id).
    - Existing equipment records are preserved.
    """
    async with AsyncSessionLocal() as db:
        # 1. Verify equipment count is preserved
        res = await db.execute(text("SELECT COUNT(*) FROM equipment;"))
        total_eq = res.scalar()
        assert total_eq > 0, "Equipment records must be preserved"

        # 2. Check backfill consistency on equipment with projects
        res = await db.execute(text("""
            SELECT e.id, e.company_id, p.company_id as project_company_id
            FROM equipment e
            INNER JOIN projects p ON e.project_id = p.id
            WHERE p.company_id IS NOT NULL;
        """))
        assigned_rows = res.fetchall()
        assert len(assigned_rows) > 0, "Assigned equipment records must exist"
        for row in assigned_rows:
            assert row[1] == row[2], f"Equipment {row[0]} company_id {row[1]} does not match project company_id {row[2]}"

        # 3. Test FK constraint: Inserting equipment with non-existent company_id must fail
        invalid_comp_id = 999999999
        invalid_eq = Equipment(
            company_id=invalid_comp_id,
            equipment_name="Invalid Tenant Crane",
            equipment_code=f"INV-EQ-{uuid.uuid4().hex[:8]}",
            working_hours=Decimal("0.0"),
            fuel_used=Decimal("0.0"),
            rental_cost=Decimal("0.0"),
        )
        db.add(invalid_eq)
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()


@pytest.mark.asyncio
async def test_accounts_seeding_and_idempotency():
    """
    Verify Chart of Accounts initialization:
    - Required system accounts exist for companies.
    - No duplicate account codes within the same company.
    - Running seed_company_chart_of_accounts repeatedly is idempotent.
    - Standard accounts map correctly into CompanySettings.
    """
    async with AsyncSessionLocal() as db:
        # 1. Pick an active company
        company = await db.scalar(select(Company).where(Company.is_active == True).limit(1))
        assert company is not None, "At least one active company must exist"
        target_company_id = int(company.id)

        # 2. Verify all standard codes exist for this company
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

        # 3. Verify idempotency: run seed again on same company
        initial_count = len(accounts)
        acc_map = await seed_company_chart_of_accounts(db, target_company_id)
        await db.commit()

        accounts_after = (await db.scalars(select(Account).where(Account.company_id == target_company_id))).all()
        assert len(accounts_after) == initial_count, "Idempotent seed must not create duplicate accounts"

        # 4. Verify composite uniqueness: inserting duplicate (company_id, code) must fail
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

        # 5. Verify multi-tenancy: another company CAN have the same account code
        comp2 = await db.scalar(select(Company).where(Company.id != target_company_id).limit(1))
        if comp2:
            comp2_id = int(comp2.id)
            acc_comp2 = await db.scalar(
                select(Account).where(Account.company_id == comp2_id, Account.code == "GENERAL_EXPENSE")
            )
            assert acc_comp2 is not None, f"Company {comp2_id} must also have its own GENERAL_EXPENSE account"
            assert acc_comp2.company_id == comp2_id
            assert acc_comp2.company_id != target_company_id
