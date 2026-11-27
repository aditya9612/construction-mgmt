"""
Focused Isolation & Regression Tests for TDS Deduction Endpoints.

Proves multi-tenant scoping across:
- GET /api/v1/accountant/tds/deductions (listing, pagination, ordering, conflict fail-closed)
- POST /api/v1/accountant/tds/deductions (source-link validation, posted journal account scoping)
- GET / PATCH / DELETE /api/v1/accountant/tds/deductions/{id} (ownership & conflict fail-closed)

Runs strictly against an isolated in-memory SQLite database without altering
`construction_management` or modifying pre-existing TDS records.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.dependencies import (
    get_current_active_user,
    get_current_user,
    get_db_session,
)
from app.main import app
from app.models.accountant import (
    Account,
    JournalEntry,
    JournalLine,
    TDSDeduction,
    VendorBill,
)
from app.models.billing import RABill
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.settings import CompanySettings
from app.models.user import User, UserRole
from app.core.enums import AccountType
from tests.test_db_safety_guard import (
    TEST_MYSQL_URL_ASYNC,
    validate_safe_test_environment,
)


class _AsyncSQLiteSession:
    """AsyncSession-compatible wrapper backed strictly by an in-memory SQLite Session."""

    def __init__(self, sync_session: Session):
        self._sync = sync_session

    def add(self, instance):
        self._sync.add(instance)

    def add_all(self, instances):
        self._sync.add_all(instances)

    async def flush(self):
        self._sync.flush()

    async def commit(self):
        self._sync.commit()

    async def rollback(self):
        self._sync.rollback()

    async def refresh(self, instance):
        self._sync.refresh(instance)

    async def delete(self, instance):
        self._sync.delete(instance)

    async def scalars(self, stmt):
        return self._sync.scalars(stmt)

    async def scalar(self, stmt):
        return self._sync.scalar(stmt)

    async def execute(self, stmt):
        return self._sync.execute(stmt)


@pytest.fixture(autouse=True)
def _enforce_isolated_guard():
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC)
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def isolated_tds_db():
    """Creates an isolated in-memory SQLite schema for two companies and TDS fixtures."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def _register_mysql_collations(dbapi_conn, _):
        dbapi_conn.create_collation(
            "utf8mb4_0900_ai_ci",
            lambda a, b: ((a or "").lower() > (b or "").lower()) - ((a or "").lower() < (b or "").lower()),
        )

    tables = [
        Company.__table__,
        User.__table__,
        Account.__table__,
        CompanySettings.__table__,
        Owner.__table__,
        Project.__table__,
        VendorBill.__table__,
        RABill.__table__,
        JournalEntry.__table__,
        JournalLine.__table__,
        TDSDeduction.__table__,
    ]
    Company.metadata.create_all(engine, tables=tables)

    with Session(engine, expire_on_commit=False) as db:
        comp_a = Company(id=101, name="Comp A")
        comp_b = Company(id=202, name="Comp B")
        db.add_all([comp_a, comp_b])

        user_a_acct = User(
            id=11,
            email="acct_a@test.com",
            hashed_password="x",
            role=UserRole.ACCOUNTANT.value,
            is_active=True,
            is_deleted=False,
            is_super_admin=False,
            company_id=101,
        )
        user_a_pm = User(
            id=12,
            email="pm_a@test.com",
            hashed_password="x",
            role=UserRole.PROJECT_MANAGER.value,
            is_active=True,
            is_deleted=False,
            is_super_admin=False,
            company_id=101,
        )
        user_a_eng = User(
            id=13,
            email="eng_a@test.com",
            hashed_password="x",
            role=UserRole.SITE_ENGINEER.value,
            is_active=True,
            is_deleted=False,
            is_super_admin=False,
            company_id=101,
        )
        user_b_admin = User(
            id=21,
            email="admin_b@test.com",
            hashed_password="x",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_deleted=False,
            is_super_admin=False,
            company_id=202,
        )
        user_no_comp = User(
            id=99,
            email="nocomp@test.com",
            hashed_password="x",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_deleted=False,
            is_super_admin=True,
            company_id=None,
        )
        db.add_all([user_a_acct, user_a_pm, user_a_eng, user_b_admin, user_no_comp])

        # Tenant tax and payable accounts (Company B has accounts; Company A initially also has accounts; Company B != Company A)
        acc_a_tds = Account(id=501, company_id=101, name="TDS Payable A", code="TDS_PAYABLE", type=AccountType.LIABILITY)
        acc_a_vp = Account(id=502, company_id=101, name="Vendor Payable A", code="VENDOR_PAYABLE", type=AccountType.LIABILITY)
        acc_b_tds = Account(id=601, company_id=202, name="TDS Payable B", code="TDS_PAYABLE", type=AccountType.LIABILITY)
        acc_b_vp = Account(id=602, company_id=202, name="Vendor Payable B", code="VENDOR_PAYABLE", type=AccountType.LIABILITY)
        db.add_all([acc_a_tds, acc_a_vp, acc_b_tds, acc_b_vp])

        owner_a = Owner(
            id=31, company_id=101, owner_code="OWNA", owner_name="Owner A", mobile="9000000001"
        )
        owner_b = Owner(
            id=32, company_id=202, owner_code="OWNB", owner_name="Owner B", mobile="9000000002"
        )
        db.add_all([owner_a, owner_b])

        proj_a = Project(
            id=41,
            business_id="PRJA",
            project_name="Project A",
            company_id=101,
            owner_id=31,
            status="ONGOING",
        )
        proj_b = Project(
            id=42,
            business_id="PRJB",
            project_name="Project B",
            company_id=202,
            owner_id=32,
            status="ONGOING",
        )
        db.add_all([proj_a, proj_b])

        vb_a = VendorBill(
            id=51,
            company_id=101,
            supplier_id=1,
            project_id=41,
            bill_number="VB-A-1",
            bill_date=date(2026, 11, 1),
            due_date=date(2026, 11, 15),
            total_amount=Decimal("10000.00"),
            status="APPROVED",
        )
        vb_b = VendorBill(
            id=52,
            company_id=202,
            supplier_id=2,
            project_id=42,
            bill_number="VB-B-1",
            bill_date=date(2026, 11, 1),
            due_date=date(2026, 11, 15),
            total_amount=Decimal("20000.00"),
            status="APPROVED",
        )
        vb_a_extra = VendorBill(
            id=53,
            company_id=101,
            supplier_id=1,
            project_id=41,
            bill_number="VB-A-2",
            bill_date=date(2026, 11, 2),
            due_date=date(2026, 11, 16),
            total_amount=Decimal("15000.00"),
            status="APPROVED",
        )
        vb_unowned = VendorBill(
            id=54,
            company_id=None,
            supplier_id=1,
            project_id=41,
            bill_number="VB-NULL-1",
            bill_date=date(2026, 11, 2),
            due_date=date(2026, 11, 16),
            total_amount=Decimal("5000.00"),
            status="APPROVED",
        )
        db.add_all([vb_a, vb_b, vb_a_extra, vb_unowned])

        rab_a = RABill(
            id=61,
            project_id=41,
            bill_number="RAB-A-1",
            work_description="Work A",
            quantity=Decimal("10.00"),
            rate=Decimal("1000.00"),
            gross_amount=Decimal("10000.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("10000.00"),
            gst_percent=Decimal("18.00"),
            total_amount=Decimal("11800.00"),
            bill_date=date(2026, 11, 1),
            status="Approved",
        )
        rab_b = RABill(
            id=62,
            project_id=42,
            bill_number="RAB-B-1",
            work_description="Work B",
            quantity=Decimal("10.00"),
            rate=Decimal("2000.00"),
            gross_amount=Decimal("20000.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("20000.00"),
            gst_percent=Decimal("18.00"),
            total_amount=Decimal("23600.00"),
            bill_date=date(2026, 11, 1),
            status="Approved",
        )
        rab_a_extra = RABill(
            id=63,
            project_id=41,
            bill_number="RAB-A-2",
            work_description="Work A2",
            quantity=Decimal("5.00"),
            rate=Decimal("1000.00"),
            gross_amount=Decimal("5000.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("5000.00"),
            gst_percent=Decimal("18.00"),
            total_amount=Decimal("5900.00"),
            bill_date=date(2026, 11, 2),
            status="Approved",
        )
        db.add_all([rab_a, rab_b, rab_a_extra])

        base_ts = datetime(2026, 11, 20, 10, 0, 0)
        tds_rows = [
            # Company A rows via all three consistent ownership paths
            TDSDeduction(
                id=1001,
                party_name="CompA Standalone",
                payment_amount=Decimal("1000.00"),
                tds_section="194C",
                tds_rate=Decimal("1.00"),
                tds_amount=Decimal("10.00"),
                status="Pending",
                created_by=11,
                created_at=base_ts + timedelta(minutes=1),
                updated_at=base_ts + timedelta(minutes=1),
            ),
            TDSDeduction(
                id=1002,
                party_name="CompA VendorBill",
                payment_amount=Decimal("2000.00"),
                tds_section="194C",
                tds_rate=Decimal("2.00"),
                tds_amount=Decimal("40.00"),
                status="PENDING",
                vendor_bill_id=51,
                created_by=None,
                created_at=base_ts + timedelta(minutes=2),
                updated_at=base_ts + timedelta(minutes=2),
            ),
            TDSDeduction(
                id=1003,
                party_name="CompA RABill",
                payment_amount=Decimal("3000.00"),
                tds_section="194C",
                tds_rate=Decimal("1.00"),
                tds_amount=Decimal("30.00"),
                status="Pending",
                ra_bill_id=61,
                created_by=11,
                created_at=base_ts + timedelta(minutes=3),
                updated_at=base_ts + timedelta(minutes=3),
            ),
            # Company B rows
            TDSDeduction(
                id=2001,
                party_name="CompB Standalone",
                payment_amount=Decimal("4000.00"),
                tds_section="194J",
                tds_rate=Decimal("10.00"),
                tds_amount=Decimal("400.00"),
                status="Pending",
                created_by=21,
                created_at=base_ts + timedelta(minutes=4),
                updated_at=base_ts + timedelta(minutes=4),
            ),
            TDSDeduction(
                id=2002,
                party_name="CompB VendorBill",
                payment_amount=Decimal("5000.00"),
                tds_section="194C",
                tds_rate=Decimal("2.00"),
                tds_amount=Decimal("100.00"),
                status="PENDING",
                vendor_bill_id=52,
                created_by=21,
                created_at=base_ts + timedelta(minutes=5),
                updated_at=base_ts + timedelta(minutes=5),
            ),
            TDSDeduction(
                id=2003,
                party_name="CompB RABill",
                payment_amount=Decimal("6000.00"),
                tds_section="194C",
                tds_rate=Decimal("1.00"),
                tds_amount=Decimal("60.00"),
                status="Pending",
                ra_bill_id=62,
                created_by=21,
                created_at=base_ts + timedelta(minutes=6),
                updated_at=base_ts + timedelta(minutes=6),
            ),
            # Unowned / conflicting records -> MUST ALL FAIL CLOSED for every tenant
            TDSDeduction(
                id=9001,
                party_name="Orphan Record",
                payment_amount=Decimal("999.00"),
                tds_section="194C",
                tds_rate=Decimal("1.00"),
                tds_amount=Decimal("9.99"),
                status="Pending",
                vendor_bill_id=None,
                ra_bill_id=None,
                created_by=None,
                created_at=base_ts + timedelta(minutes=7),
                updated_at=base_ts + timedelta(minutes=7),
            ),
            TDSDeduction(
                id=9002,
                party_name="Conflict VB-B CreatedBy-A",
                payment_amount=Decimal("500.00"),
                tds_section="194C",
                tds_rate=Decimal("1.00"),
                tds_amount=Decimal("5.00"),
                status="Pending",
                vendor_bill_id=52,
                created_by=11,
                created_at=base_ts + timedelta(minutes=8),
                updated_at=base_ts + timedelta(minutes=8),
            ),
            TDSDeduction(
                id=9003,
                party_name="Conflict VB-A RAB-B",
                payment_amount=Decimal("500.00"),
                tds_section="194C",
                tds_rate=Decimal("1.00"),
                tds_amount=Decimal("5.00"),
                status="Pending",
                vendor_bill_id=51,
                ra_bill_id=62,
                created_by=None,
                created_at=base_ts + timedelta(minutes=9),
                updated_at=base_ts + timedelta(minutes=9),
            ),
            TDSDeduction(
                id=9004,
                party_name="SuperAdmin Unlinked Record",
                payment_amount=Decimal("500.00"),
                tds_section="194C",
                tds_rate=Decimal("1.00"),
                tds_amount=Decimal("5.00"),
                status="Pending",
                created_by=99,
                created_at=base_ts + timedelta(minutes=10),
                updated_at=base_ts + timedelta(minutes=10),
            ),
            TDSDeduction(
                id=9005,
                party_name="Unowned VendorBill With CompA User",
                payment_amount=Decimal("500.00"),
                tds_section="194C",
                tds_rate=Decimal("1.00"),
                tds_amount=Decimal("5.00"),
                status="Pending",
                vendor_bill_id=54,
                created_by=11,
                created_at=base_ts + timedelta(minutes=11),
                updated_at=base_ts + timedelta(minutes=11),
            ),
        ]
        db.add_all(tds_rows)
        db.commit()

        yield {
            "db": _AsyncSQLiteSession(db),
            "sync_db": db,
            "user_a_acct": user_a_acct,
            "user_a_pm": user_a_pm,
            "user_a_eng": user_a_eng,
            "user_b_admin": user_b_admin,
            "user_no_comp": user_no_comp,
        }


def _set_user(db_wrapper: _AsyncSQLiteSession, user: User) -> None:
    app.dependency_overrides[get_db_session] = lambda: db_wrapper
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user


@pytest.mark.asyncio
async def test_company_a_and_company_b_tds_deductions_isolated_and_conflicts_fail_closed(isolated_tds_db):
    db = isolated_tds_db["db"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Company A sees strictly its own 3 valid records; never Comp B, orphan, conflict, or unowned-bill rows
        _set_user(db, isolated_tds_db["user_a_acct"])
        res_a = await client.get("/api/v1/accountant/tds/deductions")
        assert res_a.status_code == 200, res_a.text
        data_a = res_a.json()
        assert [row["id"] for row in data_a] == [1003, 1002, 1001]
        assert {row["party_name"] for row in data_a} == {
            "CompA Standalone",
            "CompA VendorBill",
            "CompA RABill",
        }

        # Company B sees strictly its own 3 valid records; never Comp A or conflicting rows 9002/9003
        _set_user(db, isolated_tds_db["user_b_admin"])
        res_b = await client.get("/api/v1/accountant/tds/deductions")
        assert res_b.status_code == 200, res_b.text
        data_b = res_b.json()
        assert [row["id"] for row in data_b] == [2003, 2002, 2001]
        returned_b_ids = {row["id"] for row in data_b}
        assert returned_b_ids.isdisjoint({1001, 1002, 1003, 9001, 9002, 9003, 9004, 9005})


@pytest.mark.asyncio
async def test_tds_deductions_pagination_and_null_company_fail_closed(isolated_tds_db):
    db = isolated_tds_db["db"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _set_user(db, isolated_tds_db["user_a_pm"])
        res_page = await client.get("/api/v1/accountant/tds/deductions?skip=1&limit=1")
        assert res_page.status_code == 200, res_page.text
        page_rows = res_page.json()
        assert len(page_rows) == 1
        assert page_rows[0]["id"] == 1002
        assert page_rows[0]["tds_section"] == "194C"

        # User with company_id=None (including SuperAdmin) must fail closed on all TDS operations
        _set_user(db, isolated_tds_db["user_no_comp"])
        res_none = await client.get("/api/v1/accountant/tds/deductions")
        assert res_none.status_code == 200
        assert res_none.json() == []
        assert (await client.get("/api/v1/accountant/tds/deductions/1001")).status_code == 403
        assert (await client.get("/api/v1/accountant/tds/deductions/9001")).status_code == 403
        assert (
            await client.post(
                "/api/v1/accountant/tds/deductions",
                json={
                    "party_name": "Should Fail",
                    "payment_amount": 1000,
                    "tds_section": "194C",
                    "tds_rate": 1,
                    "tds_amount": 10,
                },
            )
        ).status_code == 403


@pytest.mark.asyncio
async def test_tds_single_record_idor_conflict_and_write_validation(isolated_tds_db):
    db = isolated_tds_db["db"]
    sync_db = isolated_tds_db["sync_db"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _set_user(db, isolated_tds_db["user_a_acct"])

        # Own record accessible
        assert (await client.get("/api/v1/accountant/tds/deductions/1001")).status_code == 200

        # Cross-tenant, orphan, and conflicting records all fail closed with 404
        for forbidden_id in (2001, 2002, 9001, 9002, 9003, 9004, 9005):
            assert (await client.get(f"/api/v1/accountant/tds/deductions/{forbidden_id}")).status_code == 404
            assert (
                await client.patch(
                    f"/api/v1/accountant/tds/deductions/{forbidden_id}",
                    json={"party_name": "Hijacked"},
                )
            ).status_code == 404
            assert (await client.delete(f"/api/v1/accountant/tds/deductions/{forbidden_id}")).status_code == 404

        # Cannot create TDS referencing another company's VendorBill or RABill (or unowned bill)
        res_bad_vb = await client.post(
            "/api/v1/accountant/tds/deductions",
            json={
                "party_name": "CrossTenantVB",
                "payment_amount": 1000,
                "tds_section": "194C",
                "tds_rate": 2,
                "tds_amount": 20,
                "vendor_bill_id": 52,
            },
        )
        assert res_bad_vb.status_code == 404

        res_bad_rab = await client.post(
            "/api/v1/accountant/tds/deductions",
            json={
                "party_name": "CrossTenantRAB",
                "payment_amount": 1000,
                "tds_section": "194C",
                "tds_rate": 2,
                "tds_amount": 20,
                "ra_bill_id": 62,
            },
        )
        assert res_bad_rab.status_code == 404

        res_null_vb = await client.post(
            "/api/v1/accountant/tds/deductions",
            json={
                "party_name": "UnownedVB",
                "payment_amount": 1000,
                "tds_section": "194C",
                "tds_rate": 2,
                "tds_amount": 20,
                "vendor_bill_id": 54,
            },
        )
        assert res_null_vb.status_code == 404

        # Cannot PATCH own TDS deduction to link another company's bill
        res_relink = await client.patch(
            "/api/v1/accountant/tds/deductions/1001",
            json={"vendor_bill_id": 52},
        )
        assert res_relink.status_code == 404

        # Posted TDS posts journal ONLY to caller's company accounts (501/502, never 601/602)
        res_posted = await client.post(
            "/api/v1/accountant/tds/deductions",
            json={
                "party_name": "CompA Posted TDS",
                "payment_amount": 5000,
                "tds_section": "194J",
                "tds_rate": 10,
                "tds_amount": 500,
                "status": "Posted",
                "vendor_bill_id": 53,
                "ra_bill_id": 63,
            },
        )
        assert res_posted.status_code == 200, res_posted.text
        created_id = res_posted.json()["id"]

        lines = sync_db.query(JournalLine).all()
        account_ids = {line.account_id for line in lines}
        assert account_ids == {501, 502}

        # Pre-existing Comp B row 2001 was never modified
        row_b = sync_db.get(TDSDeduction, 2001)
        assert row_b.party_name == "CompB Standalone"
        assert (await client.get(f"/api/v1/accountant/tds/deductions/{created_id}")).status_code == 200


@pytest.mark.asyncio
async def test_tds_deductions_preserves_rbac_and_unauthenticated_guards(isolated_tds_db):
    db = isolated_tds_db["db"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Unauthenticated request rejected 401
        app.dependency_overrides.clear()
        app.dependency_overrides[get_db_session] = lambda: db
        res_unauth = await client.get("/api/v1/accountant/tds/deductions")
        assert res_unauth.status_code == 401

        # Disallowed role (SiteEngineer) rejected 403
        _set_user(db, isolated_tds_db["user_a_eng"])
        assert (await client.get("/api/v1/accountant/tds/deductions")).status_code == 403
        assert (await client.get("/api/v1/accountant/tds/deductions/1001")).status_code == 403

        # ProjectManager allowed on reads, rejected 403 on writes
        _set_user(db, isolated_tds_db["user_a_pm"])
        assert (await client.get("/api/v1/accountant/tds/deductions/1001")).status_code == 200
        assert (
            await client.post(
                "/api/v1/accountant/tds/deductions",
                json={
                    "party_name": "PM Write",
                    "payment_amount": 1000,
                    "tds_section": "194C",
                    "tds_rate": 1,
                    "tds_amount": 10,
                },
            )
        ).status_code == 403
        assert (
            await client.patch(
                "/api/v1/accountant/tds/deductions/1001",
                json={"party_name": "PM Patch"},
            )
        ).status_code == 403
        assert (await client.delete("/api/v1/accountant/tds/deductions/1001")).status_code == 403
