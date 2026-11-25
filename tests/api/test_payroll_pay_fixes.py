from decimal import Decimal
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, delete
from unittest.mock import patch

from app.core.db import AsyncSessionLocal
from app.core.enums import PayrollStatus, ProjectStatus
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.labour import Labour, LabourPayroll
from app.models.user import User
from app.models.accountant import Account, JournalEntry, JournalLine
from app.models.invoice import Transaction
from app.models.settings import CompanySettings
from app.models.rbac import Permission, Role, RolePermission
import app.schemas.labour as s


@pytest_asyncio.fixture
async def payroll_pay_test_data():
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # 1. Company
        comp = Company(name=f"PayFix_Comp_{uid}")
        db.add(comp)
        await db.flush()

        # 2. Owner
        owner = Owner(
            company_id=comp.id,
            owner_code=f"OWN-PF-{uid}",
            owner_name=f"Owner PF {uid}",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_pf_{uid}@test.com",
        )
        db.add(owner)
        await db.flush()

        # 3. Project
        proj = Project(
            business_id=f"PRJ-PF-{uid}",
            company_id=comp.id,
            project_name=f"Project PF {uid}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(proj)
        await db.flush()

        # 4. User with Admin role
        admin_user = User(
            email=f"admin_pf_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin PF",
            company_id=comp.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        db.add(admin_user)
        await db.flush()

        # 5. Accounts (Use global standard accounts)
        cash_acc = await db.scalar(select(Account).where(Account.code == "CASH"))
        if not cash_acc:
            cash_acc = Account(
                name="Cash Account",
                code="CASH",
                type="ASSET",
            )
            db.add(cash_acc)
            await db.flush()

        wages_acc = await db.scalar(select(Account).where(Account.code == "WAGES_PAYABLE"))
        if not wages_acc:
            wages_acc = Account(
                name="Wages Payable",
                code="WAGES_PAYABLE",
                type="LIABILITY",
            )
            db.add(wages_acc)
            await db.flush()

        # 6. Company Settings
        settings = CompanySettings(
            company_id=comp.id,
            primary_cash_account_id=cash_acc.id,
        )
        db.add(settings)
        await db.flush()

        # 7. Labour
        labour = Labour(
            labour_name=f"Labour PF {uid}",
            worker_code=f"WRK-PF-{uid}",
            company_id=comp.id,
        )
        db.add(labour)
        await db.flush()

        # 8. Permissions (payroll.approve)
        p_approve = await db.scalar(select(Permission).where(Permission.code == "payroll.approve"))
        if not p_approve:
            p_approve = Permission(code="payroll.approve", name="Approve Payroll", module="payroll")
            db.add(p_approve)
            await db.flush()

        rp = await db.scalar(
            select(RolePermission).where(
                RolePermission.role == "Admin",
                RolePermission.permission_id == p_approve.id,
                RolePermission.role_id.is_(None),
            )
        )
        if not rp:
            db.add(RolePermission(role="Admin", permission_id=p_approve.id))
            await db.flush()

        await db.commit()

        token = create_access_token({"sub": str(admin_user.id)})

        data = {
            "comp": comp,
            "proj": proj,
            "admin": admin_user,
            "labour": labour,
            "cash_acc": cash_acc,
            "wages_acc": wages_acc,
            "token": token,
        }

    yield data

    # Cleanup
    async with AsyncSessionLocal() as cleanup_db:
        # Delete transactions and journal records created during payment
        entry_ids = (await cleanup_db.scalars(
            select(JournalEntry.id).where(JournalEntry.created_by == admin_user.id)
        )).all()
        if entry_ids:
            await cleanup_db.execute(delete(JournalLine).where(JournalLine.entry_id.in_(entry_ids)))
            await cleanup_db.execute(delete(JournalEntry).where(JournalEntry.id.in_(entry_ids)))
        await cleanup_db.execute(delete(Transaction).where(Transaction.project_id == proj.id))
        await cleanup_db.execute(delete(LabourPayroll).where(LabourPayroll.project_id == proj.id))
        await cleanup_db.execute(delete(Labour).where(Labour.id == labour.id))
        await cleanup_db.execute(delete(Project).where(Project.id == proj.id))
        await cleanup_db.execute(delete(Owner).where(Owner.id == owner.id))
        await cleanup_db.execute(delete(CompanySettings).where(CompanySettings.company_id == comp.id))
        await cleanup_db.execute(delete(User).where(User.id == admin_user.id))
        await cleanup_db.execute(delete(Company).where(Company.id == comp.id))
        await cleanup_db.commit()


@pytest.mark.asyncio
async def test_payroll_pay_with_null_working_hours_does_not_throw_500(payroll_pay_test_data):
    """
    Issue Fix Verification:
    When a payroll has total_working_hours=None, response serialization should NOT fail with 500 ResponseValidationError.
    """
    d = payroll_pay_test_data
    proj = d["proj"]
    labour = d["labour"]

    async with AsyncSessionLocal() as db:
        payroll = LabourPayroll(
            labour_id=labour.id,
            project_id=proj.id,
            month=1,
            year=2028,
            total_working_hours=None,  # NULL in database
            total_overtime_hours=None,  # NULL in database
            total_wage=Decimal("1500.00"),
            paid_amount=Decimal("0.00"),
            remaining_amount=Decimal("1500.00"),
            status=PayrollStatus.LOCKED,
        )
        db.add(payroll)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/labour/payroll/pay",
            json={
                "project_id": proj.id,
                "labour_id": labour.id,
                "month": 1,
                "year": 2028,
                "amount": 500.0,
            },
            headers={"Authorization": f"Bearer {d['token']}"},
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        body = res.json()
        assert body["paid_amount"] == 500.0
        assert body["remaining_amount"] == 1000.0
        assert body["status"] == "Partial"
        assert body["total_working_hours"] == 0.0
        assert body["total_overtime_hours"] == 0.0


@pytest.mark.asyncio
async def test_payroll_pay_full_payment_sets_paid_status(payroll_pay_test_data):
    """
    Full payment should reduce remaining_amount to 0 and set status to PAID.
    """
    d = payroll_pay_test_data
    proj = d["proj"]
    labour = d["labour"]

    async with AsyncSessionLocal() as db:
        payroll = LabourPayroll(
            labour_id=labour.id,
            project_id=proj.id,
            month=2,
            year=2028,
            total_working_hours=Decimal("8.00"),
            total_overtime_hours=Decimal("0.00"),
            total_wage=Decimal("1000.00"),
            paid_amount=Decimal("0.00"),
            remaining_amount=Decimal("1000.00"),
            status=PayrollStatus.LOCKED,
        )
        db.add(payroll)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/labour/payroll/pay",
            json={
                "project_id": proj.id,
                "labour_id": labour.id,
                "month": 2,
                "year": 2028,
                "amount": 1000.0,
            },
            headers={"Authorization": f"Bearer {d['token']}"},
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        body = res.json()
        assert body["paid_amount"] == 1000.0
        assert body["remaining_amount"] == 0.0
        assert body["status"] == "Paid"


@pytest.mark.asyncio
async def test_payroll_pay_safe_against_null_paid_amount(payroll_pay_test_data):
    """
    Verifies that if paid_amount is None in DB, arithmetic doesn't fail with TypeError.
    """
    d = payroll_pay_test_data
    proj = d["proj"]
    labour = d["labour"]

    async with AsyncSessionLocal() as db:
        payroll = LabourPayroll(
            labour_id=labour.id,
            project_id=proj.id,
            month=3,
            year=2028,
            total_working_hours=Decimal("8.00"),
            total_overtime_hours=Decimal("0.00"),
            total_wage=Decimal("800.00"),
            paid_amount=None,  # NULL in database
            remaining_amount=Decimal("800.00"),
            status=PayrollStatus.LOCKED,
        )
        db.add(payroll)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/labour/payroll/pay",
            json={
                "project_id": proj.id,
                "labour_id": labour.id,
                "month": 3,
                "year": 2028,
                "amount": 200.0,
            },
            headers={"Authorization": f"Bearer {d['token']}"},
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        body = res.json()
        assert body["paid_amount"] == 200.0
        assert body["remaining_amount"] == 600.0


@pytest.mark.asyncio
async def test_payroll_pay_rejects_draft_payroll(payroll_pay_test_data):
    """
    Payroll with DRAFT status must be rejected with 400.
    """
    d = payroll_pay_test_data
    proj = d["proj"]
    labour = d["labour"]

    async with AsyncSessionLocal() as db:
        payroll = LabourPayroll(
            labour_id=labour.id,
            project_id=proj.id,
            month=4,
            year=2028,
            total_working_hours=Decimal("8.00"),
            total_overtime_hours=Decimal("0.00"),
            total_wage=Decimal("800.00"),
            paid_amount=Decimal("0.00"),
            remaining_amount=Decimal("800.00"),
            status=PayrollStatus.DRAFT,
        )
        db.add(payroll)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/labour/payroll/pay",
            json={
                "project_id": proj.id,
                "labour_id": labour.id,
                "month": 4,
                "year": 2028,
                "amount": 200.0,
            },
            headers={"Authorization": f"Bearer {d['token']}"},
        )
        assert res.status_code == 400
        assert "Cannot pay DRAFT payroll" in res.json()["detail"]


@pytest.mark.asyncio
async def test_payroll_pay_safe_when_redis_fails(payroll_pay_test_data):
    """
    Verifies that if bump_cache_version encounters an error (e.g. Redis network failure),
    the payment still commits cleanly and does not return HTTP 500.
    """
    d = payroll_pay_test_data
    proj = d["proj"]
    labour = d["labour"]

    async with AsyncSessionLocal() as db:
        payroll = LabourPayroll(
            labour_id=labour.id,
            project_id=proj.id,
            month=5,
            year=2028,
            total_working_hours=Decimal("8.00"),
            total_overtime_hours=Decimal("0.00"),
            total_wage=Decimal("800.00"),
            paid_amount=Decimal("0.00"),
            remaining_amount=Decimal("800.00"),
            status=PayrollStatus.LOCKED,
        )
        db.add(payroll)
        await db.commit()

    transport = ASGITransport(app=app)
    with patch("app.cache.redis.bump_cache_version", side_effect=Exception("Redis connection refused")):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/labour/payroll/pay",
                json={
                    "project_id": proj.id,
                    "labour_id": labour.id,
                    "month": 5,
                    "year": 2028,
                    "amount": 300.0,
                },
                headers={"Authorization": f"Bearer {d['token']}"},
            )
            assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
            assert res.json()["paid_amount"] == 300.0
