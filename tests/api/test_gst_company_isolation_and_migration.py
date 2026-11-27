"""
Focused Multi-Tenant Isolation, Dashboard Cache, Route Deduplication, and Migration
Tests for GST Endpoints and `gst_returns` Legacy Compatibility.

Covers every GST touchpoint against an isolated in-memory SQLite database
plus Alembic offline SQL verification — zero mutations to `construction_management`.
"""

import contextlib
import importlib.util
from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import json
from pathlib import Path
from unittest.mock import patch

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session

from app.core.dependencies import (
    get_current_active_user,
    get_current_user,
    get_db_session,
)
from app.core.enums import AccountType, InvoiceStatus, InvoiceType
from app.main import app
from app.models.accountant import (
    Account,
    GSTReturn,
    JournalEntry,
    JournalLine,
    TDSDeduction,
    VendorBill,
)
from app.models.approval import Approval
from app.models.billing import RABill
from app.models.boq import BOQ
from app.models.company import Company
from app.models.expense import Expense
from app.models.invoice import Invoice, Transaction
from app.models.material import Supplier
from app.models.owner import Owner
from app.models.project import Milestone, Project, ProjectMember, Task
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride
from app.models.settings import CompanySettings
from app.models.user import ActivityLog, User, UserRole
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

    async def get(self, model, ident):
        return self._sync.get(model, ident)

    async def scalars(self, stmt):
        return self._sync.scalars(stmt)

    async def scalar(self, stmt):
        return self._sync.scalar(stmt)

    async def execute(self, stmt):
        return self._sync.execute(stmt)


class _FakeAsyncRedis:
    """In-memory async Redis stub for testing dashboard cache versioning and invalidation."""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.counters: dict[str, int] = {}

    async def get(self, key: str):
        if key in self.counters:
            return str(self.counters[key]).encode("utf-8")
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None):
        self.store[key] = value
        return True

    async def incr(self, key: str) -> int:
        val = self.counters.get(key, 1) + 1
        self.counters[key] = val
        return val


@pytest.fixture(autouse=True)
def _enforce_isolated_guard():
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC)
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def isolated_gst_db():
    """Creates an isolated in-memory SQLite DB with legacy NULL and two companies' GST data."""
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
        Permission.__table__,
        Role.__table__,
        RolePermission.__table__,
        UserPermissionOverride.__table__,
        Account.__table__,
        CompanySettings.__table__,
        Owner.__table__,
        Project.__table__,
        ProjectMember.__table__,
        Milestone.__table__,
        Task.__table__,
        Supplier.__table__,
        BOQ.__table__,
        Expense.__table__,
        Invoice.__table__,
        Transaction.__table__,
        VendorBill.__table__,
        RABill.__table__,
        JournalEntry.__table__,
        JournalLine.__table__,
        TDSDeduction.__table__,
        GSTReturn.__table__,
        Approval.__table__,
        ActivityLog.__table__,
    ]
    Company.metadata.create_all(engine, tables=tables)

    with Session(engine, expire_on_commit=False) as db:
        comp_a = Company(id=101, name="Comp A")
        comp_b = Company(id=202, name="Comp B")
        comp_c = Company(id=303, name="Comp C NoTaxAccounts")
        db.add_all([comp_a, comp_b, comp_c])

        perm_view = Permission(id=1, module="dashboard", action="view", code="dashboard.view")
        perm_export = Permission(id=2, module="dashboard", action="export", code="dashboard.export")
        db.add_all([
            perm_view,
            perm_export,
            RolePermission(role=UserRole.ADMIN.value, permission_id=1),
            RolePermission(role=UserRole.ADMIN.value, permission_id=2),
            RolePermission(role=UserRole.ACCOUNTANT.value, permission_id=1),
            RolePermission(role=UserRole.ACCOUNTANT.value, permission_id=2),
        ])

        user_a_admin = User(
            id=10, email="admin_a@test.com", hashed_password="x",
            role=UserRole.ADMIN.value, is_active=True, is_deleted=False,
            is_super_admin=False, company_id=101,
        )
        user_a_pm = User(
            id=11, email="pm_a@test.com", hashed_password="x",
            role=UserRole.PROJECT_MANAGER.value, is_active=True, is_deleted=False,
            is_super_admin=False, company_id=101,
        )
        user_a_eng = User(
            id=12, email="eng_a@test.com", hashed_password="x",
            role=UserRole.SITE_ENGINEER.value, is_active=True, is_deleted=False,
            is_super_admin=False, company_id=101,
        )
        user_b_acct = User(
            id=20, email="acct_b@test.com", hashed_password="x",
            role=UserRole.ACCOUNTANT.value, is_active=True, is_deleted=False,
            is_super_admin=False, company_id=202,
        )
        user_c_acct = User(
            id=30, email="acct_c@test.com", hashed_password="x",
            role=UserRole.ACCOUNTANT.value, is_active=True, is_deleted=False,
            is_super_admin=False, company_id=303,
        )
        user_super = User(
            id=99, email="super@test.com", hashed_password="x",
            role=UserRole.ADMIN.value, is_active=True, is_deleted=False,
            is_super_admin=True, company_id=None,
        )
        db.add_all([user_a_admin, user_a_pm, user_a_eng, user_b_acct, user_c_acct, user_super])

        # Tax ledger accounts ONLY for Comp A and Comp B (Comp C intentionally has none!)
        acc_a_in = Account(id=501, company_id=101, name="Input GST A", code="INPUT_GST", type=AccountType.ASSET)
        acc_a_out = Account(id=502, company_id=101, name="Output GST A", code="OUTPUT_GST", type=AccountType.LIABILITY)
        acc_b_in = Account(id=601, company_id=202, name="Input GST B", code="INPUT_GST", type=AccountType.ASSET)
        acc_b_out = Account(id=602, company_id=202, name="Output GST B", code="OUTPUT_GST", type=AccountType.LIABILITY)
        db.add_all([acc_a_in, acc_a_out, acc_b_in, acc_b_out])

        je_a = JournalEntry(id=701, description="Comp A GST", entry_date=date(2026, 7, 10), status="Posted", entry_type="Auto")
        je_b = JournalEntry(id=702, description="Comp B GST", entry_date=date(2026, 7, 10), status="Posted", entry_type="Auto")
        je_a_draft = JournalEntry(id=703, description="Comp A Unposted", entry_date=date(2026, 7, 10), status="Draft", entry_type="Auto")
        db.add_all([je_a, je_b, je_a_draft])
        db.add_all([
            JournalLine(id=801, entry_id=701, account_id=501, debit=Decimal("180.00"), credit=Decimal("0.00")),
            JournalLine(id=802, entry_id=701, account_id=502, debit=Decimal("0.00"), credit=Decimal("900.00")),
            JournalLine(id=803, entry_id=702, account_id=601, debit=Decimal("5400.00"), credit=Decimal("0.00")),
            JournalLine(id=804, entry_id=702, account_id=602, debit=Decimal("0.00"), credit=Decimal("9900.00")),
            JournalLine(id=805, entry_id=703, account_id=501, debit=Decimal("9999.00"), credit=Decimal("0.00")),
        ])

        owner_a = Owner(id=31, company_id=101, owner_code="OA", owner_name="Owner A", mobile="9000000011")
        owner_b = Owner(id=32, company_id=202, owner_code="OB", owner_name="Owner B", mobile="9000000022")
        db.add_all([owner_a, owner_b])

        proj_a = Project(id=41, business_id="PA", project_name="Proj A", company_id=101, owner_id=31, status="ONGOING")
        proj_b = Project(id=42, business_id="PB", project_name="Proj B", company_id=202, owner_id=32, status="ONGOING")
        db.add_all([proj_a, proj_b])

        sup_a = Supplier(id=1, company_id=101, supplier_name="Supplier A")
        sup_b = Supplier(id=2, company_id=202, supplier_name="Supplier B")
        db.add_all([sup_a, sup_b])

        # Invoices for 2026-07 (Comp A vs Comp B)
        inv_a = Invoice(
            id=111, company_id=101, project_id=41, owner_id=31,
            type=InvoiceType.OWNER, invoice_number="INV-A-2026-07",
            invoice_date=date(2026, 7, 12), amount=Decimal("10000.00"),
            gst_percent=Decimal("18.00"), gst_amount=Decimal("1800.00"),
            total_amount=Decimal("11800.00"), paid_amount=Decimal("0.00"),
            pending_amount=Decimal("11800.00"), status=InvoiceStatus.PENDING,
            created_at=datetime(2026, 7, 12, 10, 0, 0),
        )
        inv_b = Invoice(
            id=222, company_id=202, project_id=42, owner_id=32,
            type=InvoiceType.OWNER, invoice_number="INV-B-2026-07",
            invoice_date=date(2026, 7, 15), amount=Decimal("50000.00"),
            gst_percent=Decimal("18.00"), gst_amount=Decimal("9000.00"),
            total_amount=Decimal("59000.00"), paid_amount=Decimal("0.00"),
            pending_amount=Decimal("59000.00"), status=InvoiceStatus.PENDING,
            created_at=datetime(2026, 7, 15, 10, 0, 0),
        )
        db.add_all([inv_a, inv_b])

        # VendorBills for 2026-07 (Comp A vs Comp B)
        vb_a = VendorBill(
            id=51, company_id=101, supplier_id=1, project_id=41,
            bill_number="VB-A-2026-07", bill_date=date(2026, 7, 20),
            due_date=date(2026, 7, 30), gross_amount=Decimal("5000.00"),
            gst_percent=Decimal("18.00"), gst_amount=Decimal("900.00"),
            total_amount=Decimal("5900.00"), status="APPROVED",
        )
        vb_b = VendorBill(
            id=52, company_id=202, supplier_id=2, project_id=42,
            bill_number="VB-B-2026-07", bill_date=date(2026, 7, 20),
            due_date=date(2026, 7, 30), gross_amount=Decimal("20000.00"),
            gst_percent=Decimal("18.00"), gst_amount=Decimal("3600.00"),
            total_amount=Decimal("23600.00"), status="APPROVED",
        )
        db.add_all([vb_a, vb_b])

        # Posted TDS records (Comp A, Comp B, and conflicting/orphan that MUST be excluded from summary)
        db.add_all([
            TDSDeduction(
                id=301, party_name="TDS A", payment_amount=Decimal("1000.00"),
                tds_section="194C", tds_rate=Decimal("10.00"), tds_amount=Decimal("100.00"),
                status="Posted", created_by=10,
            ),
            TDSDeduction(
                id=302, party_name="TDS B", payment_amount=Decimal("2000.00"),
                tds_section="194J", tds_rate=Decimal("10.00"), tds_amount=Decimal("250.00"),
                status="Posted", created_by=20,
            ),
            TDSDeduction(
                id=303, party_name="TDS Conflict", payment_amount=Decimal("5000.00"),
                tds_section="194C", tds_rate=Decimal("10.00"), tds_amount=Decimal("500.00"),
                status="Posted", vendor_bill_id=52, created_by=10,
            ),
            TDSDeduction(
                id=304, party_name="TDS Orphan", payment_amount=Decimal("5000.00"),
                tds_section="194C", tds_rate=Decimal("10.00"), tds_amount=Decimal("777.00"),
                status="Posted", created_by=None,
            ),
        ])

        base_ts = datetime(2026, 7, 10, 15, 0, 0)
        # Legacy NULL-ownership rows mirroring real DB records (IDs 1, 26, 27) + owned rows for Comp A & B
        db.add_all([
            GSTReturn(
                id=1, company_id=None, filing_period="2025-01", return_type="GSTR-1",
                taxable_value=Decimal("10000.00"), gst_liability=Decimal("1800.00"),
                itc_available=Decimal("0.00"), net_gst_payable=Decimal("0.00"),
                status="Draft", created_at=base_ts, updated_at=base_ts,
            ),
            GSTReturn(
                id=26, company_id=None, filing_period="2026-08", return_type="GSTR-1",
                taxable_value=Decimal("1000.00"), gst_liability=Decimal("0.00"),
                itc_available=Decimal("459046.80"), net_gst_payable=Decimal("-459046.80"),
                status="Draft", created_at=datetime(2026, 8, 18, 19, 18, 42),
                updated_at=datetime(2026, 8, 18, 19, 18, 42),
            ),
            GSTReturn(
                id=27, company_id=None, filing_period="11-2026-67c", return_type="GSTR-1",
                taxable_value=Decimal("10000.00"), gst_liability=Decimal("1800.00"),
                itc_available=Decimal("500.00"), net_gst_payable=Decimal("1300.00"),
                status="Draft", created_at=datetime(2026, 11, 27, 11, 25, 35),
                updated_at=datetime(2026, 11, 27, 11, 25, 35),
            ),
            GSTReturn(
                id=101, company_id=101, filing_period="2026-06", return_type="GSTR-3B",
                taxable_value=Decimal("20000.00"), gst_liability=Decimal("3600.00"),
                itc_available=Decimal("600.00"), net_gst_payable=Decimal("3000.00"),
                status="Draft", created_at=base_ts + timedelta(days=1), updated_at=base_ts + timedelta(days=1),
            ),
            GSTReturn(
                id=102, company_id=101, filing_period="2026-05", return_type="GSTR-1",
                taxable_value=Decimal("15000.00"), gst_liability=Decimal("2700.00"),
                itc_available=Decimal("200.00"), net_gst_payable=Decimal("2500.00"),
                status="Filed", filing_date=date(2026, 6, 11),
                created_at=base_ts + timedelta(days=2), updated_at=base_ts + timedelta(days=2),
            ),
            GSTReturn(
                id=201, company_id=202, filing_period="2026-06", return_type="GSTR-3B",
                taxable_value=Decimal("90000.00"), gst_liability=Decimal("16200.00"),
                itc_available=Decimal("1200.00"), net_gst_payable=Decimal("15000.00"),
                status="Draft", created_at=base_ts + timedelta(days=3), updated_at=base_ts + timedelta(days=3),
            ),
        ])
        db.commit()

        yield {
            "db": _AsyncSQLiteSession(db),
            "sync_db": db,
            "user_a_admin": user_a_admin,
            "user_a_pm": user_a_pm,
            "user_a_eng": user_a_eng,
            "user_b_acct": user_b_acct,
            "user_c_acct": user_c_acct,
            "user_super": user_super,
        }


def _set_user(db_wrapper: _AsyncSQLiteSession, user: User) -> None:
    app.dependency_overrides[get_db_session] = lambda: db_wrapper
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user


def test_gst_routes_have_no_duplicates_and_unique_operation_ids():
    seen_routes: dict[tuple[str, str], list[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if "/accountant/gst" in path:
            for method in getattr(route, "methods", set()) or set():
                key = (method, path)
                seen_routes.setdefault(key, []).append(route.endpoint.__name__)

    expected = {
        ("GET", "/api/v1/accountant/gst/summary"),
        ("POST", "/api/v1/accountant/gst/returns"),
        ("GET", "/api/v1/accountant/gst/returns"),
        ("GET", "/api/v1/accountant/gst/invoice-register"),
        ("GET", "/api/v1/accountant/gst/returns/generate"),
        ("POST", "/api/v1/accountant/gst/reconciliation/match"),
        ("GET", "/api/v1/accountant/gst/returns/{id}"),
        ("PATCH", "/api/v1/accountant/gst/returns/{id}"),
        ("DELETE", "/api/v1/accountant/gst/returns/{id}"),
        ("GET", "/api/v1/accountant/gst/export"),
        ("POST", "/api/v1/accountant/gst/import"),
    }
    assert set(seen_routes.keys()) == expected
    duplicates = {k: v for k, v in seen_routes.items() if len(v) != 1}
    assert duplicates == {}, f"Duplicate GST routes still registered: {duplicates}"


@pytest.mark.asyncio
async def test_gst_returns_crud_legacy_isolation_and_spoof_prevention(isolated_gst_db):
    db = isolated_gst_db["db"]
    sync_db = isolated_gst_db["sync_db"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Company A listing excludes Comp B (201) and ALL legacy NULL rows (1, 26, 27)
        _set_user(db, isolated_gst_db["user_a_admin"])
        res_a = await client.get("/api/v1/accountant/gst/returns")
        assert res_a.status_code == 200
        assert {r["id"] for r in res_a.json()} == {101, 102}

        # 2. Company B sees strictly its own row (201)
        _set_user(db, isolated_gst_db["user_b_acct"])
        res_b = await client.get("/api/v1/accountant/gst/returns")
        assert res_b.status_code == 200
        assert [r["id"] for r in res_b.json()] == [201]

        # 3. Cross-tenant AND legacy NULL IDs return 404 on GET / PATCH / DELETE (legacy Draft rows untouched!)
        _set_user(db, isolated_gst_db["user_a_admin"])
        for forbidden_id in (1, 26, 27, 201):
            assert (await client.get(f"/api/v1/accountant/gst/returns/{forbidden_id}")).status_code == 404
            assert (
                await client.patch(
                    f"/api/v1/accountant/gst/returns/{forbidden_id}",
                    json={"taxable_value": 123.45},
                )
            ).status_code == 404
            assert (await client.delete(f"/api/v1/accountant/gst/returns/{forbidden_id}")).status_code == 404

        # 4. POST /gst/returns assigns server-side company_id and ignores client-supplied company_id spoof
        res_create = await client.post(
            "/api/v1/accountant/gst/returns",
            json={
                "filing_period": "2026-09",
                "return_type": "GSTR-3B",
                "taxable_value": 5000.0,
                "gst_liability": 900.0,
                "itc_available": 100.0,
                "net_gst_payable": 800.0,
                "company_id": 202,
            },
        )
        assert res_create.status_code == 200, res_create.text
        created_id = res_create.json()["id"]
        created_row = sync_db.get(GSTReturn, created_id)
        assert created_row.company_id == 101

        # 5. Update and delete work cleanly on own Draft row; Filed row (102) remains protected
        assert (
            await client.patch(
                f"/api/v1/accountant/gst/returns/{created_id}",
                json={"taxable_value": 6000.0, "company_id": 202},
            )
        ).status_code == 200
        sync_db.refresh(created_row)
        assert created_row.company_id == 101
        assert (
            await client.patch(
                "/api/v1/accountant/gst/returns/102",
                json={"taxable_value": 1.0},
            )
        ).status_code == 400
        assert (await client.delete(f"/api/v1/accountant/gst/returns/{created_id}")).status_code == 204

    # Legacy NULL records remain completely preserved
    for legacy_id, expected_period in ((1, "2025-01"), (26, "2026-08"), (27, "11-2026-67c")):
        row = sync_db.get(GSTReturn, legacy_id)
        assert row is not None
        assert row.company_id is None
        assert row.filing_period == expected_period
        assert row.status == "Draft"
    assert sync_db.get(GSTReturn, 26).net_gst_payable == Decimal("-459046.80")


@pytest.mark.asyncio
async def test_gst_generation_register_export_and_reconciliation_isolated(isolated_gst_db):
    db = isolated_gst_db["db"]
    sync_db = isolated_gst_db["sync_db"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _set_user(db, isolated_gst_db["user_a_admin"])

        # 1. Generate computes ONLY Comp A's 2026-07 invoices (10000 / 1800) & bills (5900 * 0.18 = 1062.0)
        res_gen = await client.get("/api/v1/accountant/gst/returns/generate?filing_period=2026-07&return_type=GSTR-3B")
        assert res_gen.status_code == 200, res_gen.text
        gen_data = res_gen.json()
        assert gen_data["taxable_value"] == pytest.approx(10000.0)
        assert gen_data["gst_liability"] == pytest.approx(1800.0)
        assert gen_data["itc_available"] == pytest.approx(5900.0 * 0.18)
        gen_row = sync_db.get(GSTReturn, gen_data["id"])
        assert gen_row.company_id == 101

        # 2. Invoice register returns only Comp A's INV-A-2026-07 & VB-A-2026-07
        res_reg = await client.get("/api/v1/accountant/gst/invoice-register")
        assert res_reg.status_code == 200
        reg_nos = {item["invoice_no"] for item in res_reg.json()}
        assert reg_nos == {"INV-A-2026-07", "VB-A-2026-07"}

        # 3. Export CSV contains only Comp A records
        res_exp = await client.get("/api/v1/accountant/gst/export")
        assert res_exp.status_code == 200
        csv_text = res_exp.text
        assert "INV-A-2026-07" in csv_text
        assert "VB-A-2026-07" in csv_text
        assert "INV-B-2026-07" not in csv_text
        assert "VB-B-2026-07" not in csv_text

        # 4. Reconciliation JSON & CSV cannot match Comp B's VB-B-2026-07 (must report MISSING_IN_ERP)
        json_payload = json.dumps([
            {"invoice_no": "VB-A-2026-07", "vendor": "SupA", "gst_amount": 5900.0 * 0.18},
            {"invoice_no": "VB-B-2026-07", "vendor": "SupB", "gst_amount": 23600.0 * 0.18},
        ]).encode("utf-8")
        res_rec_json = await client.post(
            "/api/v1/accountant/gst/reconciliation/match",
            files={"file": ("portal.json", io.BytesIO(json_payload), "application/json")},
        )
        assert res_rec_json.status_code == 200
        mismatches_json = res_rec_json.json()
        assert len(mismatches_json) == 1
        assert mismatches_json[0]["invoice_no"] == "VB-B-2026-07"
        assert mismatches_json[0]["status"] == "MISSING_IN_ERP"

        csv_payload = (
            "invoice_no,vendor,gst_amount\n"
            "VB-A-2026-07,SupA,1062.0\n"
            "VB-B-2026-07,SupB,4248.0\n"
        ).encode("utf-8")
        res_rec_csv = await client.post(
            "/api/v1/accountant/gst/reconciliation/match",
            files={"file": ("portal.csv", io.BytesIO(csv_payload), "text/csv")},
        )
        assert res_rec_csv.status_code == 200
        mismatches_csv = res_rec_csv.json()
        assert len(mismatches_csv) == 1
        assert mismatches_csv[0]["invoice_no"] == "VB-B-2026-07"
        assert mismatches_csv[0]["status"] == "MISSING_IN_ERP"


@pytest.mark.asyncio
async def test_gst_summary_accounts_tds_and_filings_isolated(isolated_gst_db):
    db = isolated_gst_db["db"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Comp A: Posted accounts (180 input, 900 output), Posted TDS (100), filings (2026-06, 2026-05 only)
        _set_user(db, isolated_gst_db["user_a_admin"])
        res_a = await client.get("/api/v1/accountant/gst/summary")
        assert res_a.status_code == 200, res_a.text
        body_a = res_a.json()
        assert body_a["input_gst"] == pytest.approx(180.0)
        assert body_a["output_gst"] == pytest.approx(900.0)
        assert body_a["net_gst"] == pytest.approx(720.0)
        assert body_a["tds_collected"] == pytest.approx(100.0)
        assert {f["filing_period"] for f in body_a["return_status"]} == {"2026-06", "2026-05"}

        # Comp C has ZERO tax accounts -> MUST return 0.0 (never fallback globally to Comp A's accounts!)
        _set_user(db, isolated_gst_db["user_c_acct"])
        res_c = await client.get("/api/v1/accountant/gst/summary")
        assert res_c.status_code == 200, res_c.text
        body_c = res_c.json()
        assert body_c["input_gst"] == 0.0
        assert body_c["output_gst"] == 0.0
        assert body_c["net_gst"] == 0.0
        assert body_c["tds_collected"] == 0.0
        assert body_c["return_status"] == []
        assert body_c["recent_filings"] == []


@pytest.mark.asyncio
async def test_accountant_dashboard_gst_kpis_export_and_cache_invalidation(isolated_gst_db):
    db = isolated_gst_db["db"]
    fake_redis = _FakeAsyncRedis()
    # Pre-seed an old unscoped cache key to verify v2 key versioning ignores stale cache
    fake_redis.store["accountant_dashboard:101:1"] = b'{"kpi_cards":{"gst_due":999999}}'

    prev_redis = getattr(app.state, "redis", None)
    app.state.redis = fake_redis
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Comp A accountant dashboard: gst_due = 3000.0 (row 101 Draft only), notification = 1 period
            _set_user(db, isolated_gst_db["user_a_admin"])
            res_dash_a = await client.get("/api/v1/dashboard/accountant")
            assert res_dash_a.status_code == 200, res_dash_a.text
            dash_a = res_dash_a.json()
            assert dash_a["kpi_cards"]["gst_due"] == pytest.approx(3000.0)
            assert "GST Return filing due for 1 periods." in dash_a["notifications"]
            assert "accountant_dashboard:v2:101:1" in fake_redis.store

            # 2. Comp B accountant dashboard: gst_due = 15000.0 (row 201 Draft only), never gets Comp A cache
            _set_user(db, isolated_gst_db["user_b_acct"])
            res_dash_b = await client.get("/api/v1/dashboard/accountant")
            assert res_dash_b.status_code == 200
            dash_b = res_dash_b.json()
            assert dash_b["kpi_cards"]["gst_due"] == pytest.approx(15000.0)
            assert "accountant_dashboard:v2:202:1" in fake_redis.store

            # 3. Export CSV also reflects scoped gst_due (3000.0 for Comp A)
            _set_user(db, isolated_gst_db["user_a_admin"])
            res_export_a = await client.get("/api/v1/dashboard/accountant/export")
            assert res_export_a.status_code == 200
            assert "3000.0" in res_export_a.text

            # 4. Creating a Draft GSTReturn for Comp A invalidates dashboard_version and updates cached KPI
            res_new = await client.post(
                "/api/v1/accountant/gst/returns",
                json={
                    "filing_period": "2026-10",
                    "return_type": "GSTR-3B",
                    "taxable_value": 2000.0,
                    "gst_liability": 360.0,
                    "itc_available": 0.0,
                    "net_gst_payable": 500.0,
                    "status": "Draft",
                },
            )
            assert res_new.status_code == 200
            assert fake_redis.counters.get("dashboard_version") == 2

            res_dash_a2 = await client.get("/api/v1/dashboard/accountant")
            assert res_dash_a2.status_code == 200
            assert res_dash_a2.json()["kpi_cards"]["gst_due"] == pytest.approx(3500.0)
            assert "GST Return filing due for 2 periods." in res_dash_a2.json()["notifications"]

            # Warm-cache hit on updated version returns valid serialized AccountantDashboardOut
            res_dash_a2_cached = await client.get("/api/v1/dashboard/accountant")
            assert res_dash_a2_cached.status_code == 200
            assert res_dash_a2_cached.json()["kpi_cards"]["gst_due"] == pytest.approx(3500.0)
    finally:
        app.state.redis = prev_redis


@pytest.mark.asyncio
async def test_gst_rbac_and_company_less_rejections(isolated_gst_db):
    db = isolated_gst_db["db"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Unauthenticated -> 401
        app.dependency_overrides.clear()
        app.dependency_overrides[get_db_session] = lambda: db
        assert (await client.get("/api/v1/accountant/gst/returns")).status_code == 401
        assert (await client.get("/api/v1/accountant/gst/summary")).status_code == 401

        # 2. Company-less SuperAdmin -> rejected 403 across ALL GST routes (no legacy/global exposure!)
        _set_user(db, isolated_gst_db["user_super"])
        assert (await client.get("/api/v1/accountant/gst/returns")).status_code == 403
        assert (await client.get("/api/v1/accountant/gst/returns/1")).status_code == 403
        assert (await client.get("/api/v1/accountant/gst/returns/generate?filing_period=2026-07&return_type=GSTR-3B")).status_code == 403
        assert (await client.get("/api/v1/accountant/gst/summary")).status_code == 403
        assert (await client.get("/api/v1/accountant/gst/invoice-register")).status_code == 403
        assert (await client.get("/api/v1/accountant/gst/export")).status_code == 403
        assert (
            await client.post(
                "/api/v1/accountant/gst/returns",
                json={"filing_period": "2026-09", "return_type": "GSTR-1"},
            )
        ).status_code == 403
        assert (
            await client.patch(
                "/api/v1/accountant/gst/returns/1",
                json={"taxable_value": 100.0},
            )
        ).status_code == 403
        assert (await client.delete("/api/v1/accountant/gst/returns/1")).status_code == 403
        assert (
            await client.post(
                "/api/v1/accountant/gst/reconciliation/match",
                files={"file": ("portal.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/v1/accountant/gst/import",
                files={"file": ("imp.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
            )
        ).status_code == 403

        # 3. SiteEngineer disallowed -> 403
        _set_user(db, isolated_gst_db["user_a_eng"])
        assert (await client.get("/api/v1/accountant/gst/returns")).status_code == 403
        assert (await client.get("/api/v1/accountant/gst/summary")).status_code == 403

        # 4. ProjectManager allowed on read routes, rejected 403 on write routes
        _set_user(db, isolated_gst_db["user_a_pm"])
        assert (await client.get("/api/v1/accountant/gst/returns")).status_code == 200
        assert (await client.get("/api/v1/accountant/gst/returns/101")).status_code == 200
        assert (await client.get("/api/v1/accountant/gst/summary")).status_code == 200
        assert (
            await client.post(
                "/api/v1/accountant/gst/returns",
                json={"filing_period": "2026-11", "return_type": "GSTR-1"},
            )
        ).status_code == 403
        assert (
            await client.patch(
                "/api/v1/accountant/gst/returns/101",
                json={"taxable_value": 1.0},
            )
        ).status_code == 403
        assert (await client.delete("/api/v1/accountant/gst/returns/101")).status_code == 403


def test_alembic_gst_migration_offline_sql_and_sqlite_legacy_preservation():
    repo_root = Path(__file__).resolve().parents[2]
    cfg = AlembicConfig(str(repo_root / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["c3d4e5f6a7b8"]
    rev = script.get_revision("b2c3d4e5f6a7")
    assert rev.down_revision == "a1b2c3d4e5f6"
    rev_rbac = script.get_revision("c3d4e5f6a7b8")
    assert rev_rbac.down_revision == "b2c3d4e5f6a7"

    # 1. Generate offline MySQL DDL SQL (touches ZERO live databases)
    sql_buf = io.StringIO()
    with contextlib.redirect_stdout(sql_buf):
        alembic_command.upgrade(cfg, "a1b2c3d4e5f6:c3d4e5f6a7b8", sql=True)
    offline_sql = sql_buf.getvalue()
    assert "ALTER TABLE gst_returns ADD COLUMN company_id INTEGER" in offline_sql
    assert "FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE RESTRICT" in offline_sql
    assert "ix_gst_returns_company_id" in offline_sql
    assert "ix_gst_returns_company_created" in offline_sql
    assert "ix_rbac_audit_logs_company_id" in offline_sql
    assert "ix_rbac_audit_logs_action" in offline_sql
    assert "ix_rbac_audit_logs_created_at" in offline_sql
    assert "UPDATE gst_returns" not in offline_sql
    assert "DELETE FROM gst_returns" not in offline_sql

    # Verify InnoDB safe ordering: explicit indexes created BEFORE FK constraint
    idx_pos = offline_sql.index("ix_gst_returns_company_id")
    idx_created_pos = offline_sql.index("ix_gst_returns_company_created")
    fk_pos = offline_sql.index("fk_gst_returns_company_id")
    assert idx_pos < fk_pos
    assert idx_created_pos < fk_pos

    # Verify offline MySQL downgrade drops FK BEFORE dropping indexes (avoids MySQL 1553)
    down_buf = io.StringIO()
    with contextlib.redirect_stdout(down_buf):
        alembic_command.downgrade(cfg, "b2c3d4e5f6a7:a1b2c3d4e5f6", sql=True)
    down_sql = down_buf.getvalue()
    assert "DROP FOREIGN KEY fk_gst_returns_company_id" in down_sql
    assert down_sql.index("DROP FOREIGN KEY fk_gst_returns_company_id") < down_sql.index("ix_gst_returns_company_created")
    assert down_sql.index("DROP FOREIGN KEY fk_gst_returns_company_id") < down_sql.index("ix_gst_returns_company_id")

    # 2. Run actual migration upgrade & downgrade against an isolated pre-migration SQLite DB
    mig_path = repo_root / "alembic" / "versions" / "b2c3d4e5f6a7_add_company_id_to_gst_returns.py"
    spec = importlib.util.spec_from_file_location("b2c3d4e5f6a7_mod", str(mig_path))
    mig_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig_mod)

    sqlite_engine = create_engine("sqlite:///:memory:", echo=False)
    with sqlite_engine.begin() as conn:
        conn.execute(text("CREATE TABLE companies (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL)"))
        conn.execute(text("INSERT INTO companies (id, name) VALUES (1, 'Default Company'), (101, 'Comp A')"))
        conn.execute(
            text(
                """
                CREATE TABLE gst_returns (
                    id INTEGER PRIMARY KEY,
                    filing_period VARCHAR(20) NOT NULL,
                    return_type VARCHAR(50) NOT NULL,
                    taxable_value DECIMAL(18,2),
                    gst_liability DECIMAL(18,2),
                    itc_available DECIMAL(18,2),
                    net_gst_payable DECIMAL(18,2),
                    status VARCHAR(50),
                    filing_date DATE,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO gst_returns
                    (id, filing_period, return_type, taxable_value, gst_liability, itc_available, net_gst_payable, status, created_at, updated_at)
                VALUES
                    (1, '2025-01', 'GSTR-1', 10000.00, 1800.00, 0.00, 0.00, 'Draft', '2026-07-10 15:58:33', '2026-07-10 15:58:33'),
                    (26, '2026-08', 'GSTR-1', 1000.00, 0.00, 459046.80, -459046.80, 'Draft', '2026-08-18 19:18:42', '2026-08-18 19:18:42'),
                    (27, '11-2026-67c', 'GSTR-1', 10000.00, 1800.00, 500.00, 1300.00, 'Draft', '2026-11-27 11:25:35', '2026-11-27 11:25:35')
                """
            )
        )

        mc = MigrationContext.configure(conn)
        ops = Operations(mc)
        with patch.object(mig_mod, "op", ops), patch.object(mig_mod.context, "is_offline_mode", return_value=False):
            mig_mod.upgrade()

            insp = inspect(conn)
            cols = {c["name"]: c for c in insp.get_columns("gst_returns")}
            assert "company_id" in cols
            assert cols["company_id"]["nullable"] is True
            idx_names = {idx["name"] for idx in insp.get_indexes("gst_returns")}
            assert "ix_gst_returns_company_id" in idx_names
            assert "ix_gst_returns_company_created" in idx_names
            fks = insp.get_foreign_keys("gst_returns")
            assert any(
                fk["referred_table"] == "companies"
                and fk["constrained_columns"] == ["company_id"]
                and fk["options"].get("ondelete") == "RESTRICT"
                for fk in fks
            )

            # Verify legacy rows untouched & NULL
            rows = conn.execute(
                text("SELECT id, filing_period, net_gst_payable, status, company_id FROM gst_returns ORDER BY id")
            ).fetchall()
            assert len(rows) == 3
            assert all(r[4] is None for r in rows)
            assert rows[1][2] == -459046.80

            # Clean downgrade succeeds when all rows remain NULL
            mig_mod.downgrade()
            assert "company_id" not in {c["name"] for c in inspect(conn).get_columns("gst_returns")}

            # Re-upgrade, add tenant-owned row -> downgrade MUST fail closed with safety guard
            mig_mod.upgrade()
            conn.execute(
                text(
                    """
                    INSERT INTO gst_returns
                        (id, company_id, filing_period, return_type, taxable_value, gst_liability, itc_available, net_gst_payable, status, created_at, updated_at)
                    VALUES
                        (200, 101, '2026-11', 'GSTR-3B', 100.00, 18.00, 0.00, 18.00, 'Draft', '2026-11-27 12:00:00', '2026-11-27 12:00:00')
                    """
                )
            )
            with pytest.raises(RuntimeError, match="IrreversibleMigrationError"):
                mig_mod.downgrade()



@pytest.mark.asyncio
async def test_invoice_output_gst_journal_strictly_company_scoped(isolated_gst_db):
    from app.api.invoice import _post_invoice_journal

    db = isolated_gst_db["db"]
    sync_db = isolated_gst_db["sync_db"]

    # Company B has OUTPUT_GST (602); Company C has NO tax accounts yet; Company A has OUTPUT_GST (502).
    inv_b = Invoice(
        id=9101,
        company_id=202,
        project_id=42,
        owner_id=32,
        type=InvoiceType.OWNER,
        invoice_number="INV-B-JRN-1",
        amount=Decimal("10000.00"),
        gst_percent=Decimal("18.00"),
        gst_amount=Decimal("1800.00"),
        total_amount=Decimal("11800.00"),
        paid_amount=Decimal("0.00"),
        pending_amount=Decimal("11800.00"),
        status=InvoiceStatus.PENDING,
        description="Comp B Tax Invoice",
    )
    inv_c = Invoice(
        id=9102,
        company_id=303,
        project_id=None,
        owner_id=None,
        type=InvoiceType.OWNER,
        invoice_number="INV-C-JRN-1",
        amount=Decimal("20000.00"),
        gst_percent=Decimal("18.00"),
        gst_amount=Decimal("3600.00"),
        total_amount=Decimal("23600.00"),
        paid_amount=Decimal("0.00"),
        pending_amount=Decimal("23600.00"),
        status=InvoiceStatus.PENDING,
        description="Comp C Tax Invoice",
    )
    inv_none = Invoice(
        id=9103,
        company_id=None,
        project_id=None,
        owner_id=None,
        type=InvoiceType.OWNER,
        invoice_number="INV-NULL-JRN-1",
        amount=Decimal("5000.00"),
        gst_percent=Decimal("18.00"),
        gst_amount=Decimal("900.00"),
        total_amount=Decimal("5900.00"),
        paid_amount=Decimal("0.00"),
        pending_amount=Decimal("5900.00"),
        status=InvoiceStatus.PENDING,
        description="Company-less Tax Invoice",
    )
    sync_db.add_all([inv_b, inv_c, inv_none])
    sync_db.flush()

    await _post_invoice_journal(db, inv_b)
    await _post_invoice_journal(db, inv_c)
    await _post_invoice_journal(db, inv_none)
    await db.commit()

    # Verify Comp C got its own OUTPUT_GST account (company_id=303) rather than falling back to Comp A (502)
    comp_c_out = sync_db.query(Account).filter(
        Account.code == "OUTPUT_GST", Account.company_id == 303
    ).one_or_none()
    assert comp_c_out is not None
    assert comp_c_out.id not in (502, 602)

    # Verify HTTP /gst/summary shows strict per-tenant Output GST without cross-company leakage
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _set_user(db, isolated_gst_db["user_a_admin"])
        sum_a = (await client.get("/api/v1/accountant/gst/summary")).json()
        assert sum_a["output_gst"] == pytest.approx(900.0)

        _set_user(db, isolated_gst_db["user_b_acct"])
        sum_b = (await client.get("/api/v1/accountant/gst/summary")).json()
        assert sum_b["output_gst"] == pytest.approx(9900.0 + 1800.0)

        _set_user(db, isolated_gst_db["user_c_acct"])
        sum_c = (await client.get("/api/v1/accountant/gst/summary")).json()
        assert sum_c["output_gst"] == pytest.approx(3600.0)


def test_alembic_rbac_reconcile_migration_sqlite():
    repo_root = Path(__file__).resolve().parents[2]
    mig_path = repo_root / "alembic" / "versions" / "c3d4e5f6a7b8_reconcile_rbac_audit_logs_schema.py"
    spec = importlib.util.spec_from_file_location("c3d4e5f6a7b8_mod", str(mig_path))
    mig_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig_mod)

    sqlite_engine = create_engine("sqlite:///:memory:", echo=False)
    with sqlite_engine.begin() as conn:
        conn.execute(text("CREATE TABLE companies (id INTEGER PRIMARY KEY, name VARCHAR(255))"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        conn.execute(
            text(
                """
                CREATE TABLE rbac_audit_logs (
                    id INTEGER PRIMARY KEY,
                    company_id INTEGER,
                    actor_id INTEGER,
                    action VARCHAR(100) NOT NULL,
                    target_type VARCHAR(50),
                    target_id VARCHAR(100),
                    permission VARCHAR(150),
                    old_value TEXT,
                    new_value TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO rbac_audit_logs (id, company_id, actor_id, action, target_type)
                VALUES (1, 101, 10, 'ROLE_UPDATED', NULL)
                """
            )
        )

        mc = MigrationContext.configure(conn)
        ops = Operations(mc)
        with patch.object(mig_mod, "op", ops), patch.object(mig_mod.context, "is_offline_mode", return_value=False):
            mig_mod.upgrade()

            insp = inspect(conn)
            cols = {c["name"] for c in insp.get_columns("rbac_audit_logs")}
            assert {"entity_type", "entity_id", "performed_by", "details"}.issubset(cols)
            idx_names = {idx["name"] for idx in insp.get_indexes("rbac_audit_logs")}
            assert {
                "ix_rbac_audit_logs_company_id",
                "ix_rbac_audit_logs_action",
                "ix_rbac_audit_logs_created_at",
            }.issubset(idx_names)

            row = conn.execute(
                text("SELECT id, action, target_type FROM rbac_audit_logs WHERE id = 1")
            ).fetchone()
            assert row == (1, "ROLE_UPDATED", None)

            # Downgrade removes only added indexes when legacy columns are all NULL
            mig_mod.downgrade()
            after_idx = {idx["name"] for idx in inspect(conn).get_indexes("rbac_audit_logs")}
            assert "ix_rbac_audit_logs_created_at" not in after_idx

            # Re-upgrade, insert legacy column data -> downgrade MUST fail closed
            mig_mod.upgrade()
            conn.execute(
                text(
                    "UPDATE rbac_audit_logs SET entity_type = 'role', details = '{\"k\": 1}' WHERE id = 1"
                )
            )
            with pytest.raises(RuntimeError, match="IrreversibleMigrationError"):
                mig_mod.downgrade()


@pytest.mark.asyncio
async def test_accounting_helpers_strictly_tenant_scoped_and_fail_closed(isolated_gst_db):
    from app.utils.accounting import (
        get_accounts_receivable,
        get_primary_cash_account,
        get_revenue_account,
        resolve_tax_accounts,
    )

    db = isolated_gst_db["db"]
    sync_db = isolated_gst_db["sync_db"]

    # Seed Company A's AR, Revenue, TDS_PAYABLE, CASH + CompanySettings pointing to A's TDS_PAYABLE
    acc_a_tds = Account(id=503, company_id=101, name="TDS Payable A", code="TDS_PAYABLE", type=AccountType.LIABILITY)
    acc_a_ar = Account(id=504, company_id=101, name="AR A", code="ACCOUNTS_RECEIVABLE", type=AccountType.ASSET)
    acc_a_rev = Account(id=505, company_id=101, name="Rev A", code="SALES_REVENUE", type=AccountType.INCOME)
    acc_a_cash = Account(id=506, company_id=101, name="Cash A", code="CASH", type=AccountType.ASSET)
    sync_db.add_all([acc_a_tds, acc_a_ar, acc_a_rev, acc_a_cash])
    sync_db.flush()
    sync_db.add(CompanySettings(id=1, company_id=101, tds_payable_account_id=503, primary_cash_account_id=506))
    sync_db.commit()

    # 1. Missing company_id MUST fail closed (never leak/mutate another tenant's accounts)
    for fn, args in [
        (resolve_tax_accounts, ("output_gst",)),
        (resolve_tax_accounts, ("input_gst",)),
        (resolve_tax_accounts, ("tds_payable",)),
        (get_accounts_receivable, ()),
        (get_revenue_account, ()),
    ]:
        with pytest.raises(ValueError):
            await fn(db, *args, company_id=None)

    # 2. Unseeded Company C (303) MUST create its own accounts rather than falling back to Company A
    out_c = await resolve_tax_accounts(db, "output_gst", company_id=303)
    in_c = await resolve_tax_accounts(db, "input_gst", company_id=303)
    tds_c = await resolve_tax_accounts(db, "tds_payable", company_id=303)
    ar_c = await get_accounts_receivable(db, company_id=303)
    rev_c = await get_revenue_account(db, company_id=303)
    await db.commit()

    assert out_c.company_id == 303 and out_c.id not in (502, 602)
    assert in_c.company_id == 303 and in_c.id not in (501, 601)
    assert tds_c.company_id == 303 and tds_c.id != 503
    assert ar_c.company_id == 303 and ar_c.id != 504
    assert rev_c.company_id == 303 and rev_c.id != 505

    # Re-lookup returns same Comp C accounts cleanly
    assert (await resolve_tax_accounts(db, "output_gst", company_id=303)).id == out_c.id
    assert (await get_accounts_receivable(db, company_id=303)).id == ar_c.id

    # get_primary_cash_account with unseeded company_id=303 raises ValueError (no fallback to 506)
    with pytest.raises(ValueError):
        await get_primary_cash_account(db, company_id=303)
    assert (await get_primary_cash_account(db, company_id=101)).id == 506


@pytest.mark.asyncio
async def test_ra_bill_approval_and_manual_receivable_tenant_isolation(isolated_gst_db):
    db = isolated_gst_db["db"]
    sync_db = isolated_gst_db["sync_db"]

    perm_bill = Permission(id=3, module="billing", action="approve", code="billing.approve")
    perm_inv = Permission(id=4, module="invoices", action="create", code="invoices.create")
    owner_c = Owner(id=33, company_id=303, owner_code="OC", owner_name="Owner C", mobile="9000000033")
    owner_orphan = Owner(id=34, company_id=None, owner_code="OO", owner_name="Owner Orphan", mobile="9000000034")
    proj_c = Project(id=43, business_id="PC", project_name="Proj C", company_id=303, owner_id=33, status="ONGOING")
    proj_orphan = Project(id=44, business_id="PO", project_name="Proj Orphan", company_id=None, owner_id=34, status="ONGOING")
    sync_db.add_all([
        perm_bill,
        perm_inv,
        RolePermission(role=UserRole.ACCOUNTANT.value, permission_id=3),
        RolePermission(role=UserRole.ACCOUNTANT.value, permission_id=4),
        owner_c,
        owner_orphan,
        proj_c,
        proj_orphan,
        RABill(
            id=9201,
            project_id=43,
            bill_number="RAB-C-1",
            work_description="Comp C RA Work",
            quantity=Decimal("1.000"),
            rate=Decimal("10000.00"),
            gross_amount=Decimal("10000.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("10000.00"),
            gst_percent=Decimal("18.00"),
            total_amount=Decimal("11800.00"),
            bill_date=date(2026, 7, 25),
            status="Submitted",
        ),
        RABill(
            id=9202,
            project_id=44,
            bill_number="RAB-ORPHAN-1",
            work_description="Orphan RA Work",
            quantity=Decimal("1.000"),
            rate=Decimal("5000.00"),
            gross_amount=Decimal("5000.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("5000.00"),
            gst_percent=Decimal("18.00"),
            total_amount=Decimal("5900.00"),
            bill_date=date(2026, 7, 25),
            status="Submitted",
        ),
    ])
    sync_db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Approve Comp C RA Bill -> creates Comp C's own AR, Revenue, Output GST accounts
        _set_user(db, isolated_gst_db["user_c_acct"])
        resp_ok = await client.put("/api/v1/billing/9201/approve")
        assert resp_ok.status_code == 200

        comp_c_out = sync_db.query(Account).filter(
            Account.code == "OUTPUT_GST", Account.company_id == 303
        ).one_or_none()
        assert comp_c_out is not None and comp_c_out.id not in (502, 602)

        sum_c = (await client.get("/api/v1/accountant/gst/summary")).json()
        assert sum_c["output_gst"] == pytest.approx(1800.0)

        # Comp A summary must NOT include Comp C's RA Bill GST
        _set_user(db, isolated_gst_db["user_a_admin"])
        sum_a = (await client.get("/api/v1/accountant/gst/summary")).json()
        assert sum_a["output_gst"] == pytest.approx(900.0)

        # 2. Company-less RA Bill fails closed 403 WITHOUT mutating RABill.status
        _set_user(db, isolated_gst_db["user_super"])
        resp_deny = await client.put("/api/v1/billing/9202/approve")
        assert resp_deny.status_code == 403
        sync_db.expire_all()
        assert sync_db.get(RABill, 9202).status == "Submitted"

        # 3. Manual receivable without company context fails closed 403
        resp_rec_deny = await client.post(
            "/api/v1/invoices/receivables/manual",
            json={"client_id": 34, "amount": 1000.0, "due_date": "2026-08-01", "description": "Orphan"},
        )
        assert resp_rec_deny.status_code == 403


@pytest.mark.asyncio
async def test_rbac_audit_log_model_and_api_handle_legacy_null_target_type(isolated_gst_db):
    from app.models.rbac import RBACAuditLog

    db = isolated_gst_db["db"]
    sync_db = isolated_gst_db["sync_db"]
    RBACAuditLog.__table__.create(sync_db.bind, checkfirst=True)

    sync_db.add_all([
        RBACAuditLog(
            id=1,
            company_id=101,
            actor_id=10,
            action="LEGACY_ROLE_SYNC",
            target_type=None,
            entity_type="role",
            entity_id=1,
            performed_by=10,
            details={"source": "legacy"},
        ),
        RBACAuditLog(
            id=2,
            company_id=202,
            actor_id=20,
            action="OTHER_COMPANY_LOG",
            target_type="role",
        ),
    ])
    sync_db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _set_user(db, isolated_gst_db["user_a_admin"])
        resp = await client.get("/api/v1/rbac/audit-logs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["action"] == "LEGACY_ROLE_SYNC"
        assert body["items"][0]["target_type"] is None
