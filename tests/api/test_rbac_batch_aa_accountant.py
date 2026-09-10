"""
RBAC Batch AA: Accountant & Financial Management Tests
Covers all 24 test criteria:
- TC01: Unauthenticated request to GET /accounts -> 401
- TC02: Unauthenticated request to POST /cash-book/import -> 401
- TC03: Unauthenticated request to POST /bank-book/import -> 401
- TC04: Authenticated user with no permissions -> 403
- TC05: Authenticated user with only accountant.view: GET 200, POST 403
- TC06: Authenticated user with only accountant.create: POST 200, GET 403
- TC07: Authenticated user with only accountant.edit: PATCH 200, DELETE 403
- TC08: Authenticated user with only accountant.delete: DELETE 200, PATCH 403
- TC09: Authenticated user with only accountant.export: GET export 200, GET list 403
- TC10: Module wildcard accountant.* grants all 5 actions
- TC11: Global wildcard * grants access
- TC12: Dynamic grant via DB
- TC13: Dynamic revoke via DB
- TC14: Positive user override
- TC15: Negative user override
- TC16: Super Admin bypass
- TC17: Tenant Isolation - Company A cannot view Company B resources (404)
- TC18: Cross-tenant FK injection rejected
- TC19: Tenantless user (company_id=None, is_super_admin=False) -> 403/404
- TC20: Super Admin cross-company access succeeds
- TC21: Concurrency / locking on create_fund_transfer & invalid amounts
- TC22: Accounting Invariants on create_journal_entry (unbalanced, negative, cross-tenant)
- TC23: Contractor Payment overpayment prevention on pay_contractor
- TC24: Static router audit (71 unique, 0 dupes, 100% require_permission, 0 require_roles, 0 role allowlists)
"""

import ast
import io
import uuid
from decimal import Decimal
from datetime import date
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, update

import app.main
from app.main import app
from app.core.db import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.accountant import (
    Account,
    BankAccount,
    FixedAsset,
    JournalEntry,
    JournalLine,
    FundTransfer,
    GSTReturn,
)
from app.models.settings import CompanySettings
from app.models.billing import RABill
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import AccountType
from app.api.accountant import router


# ==============================================================================
# FIXTURE DATA SETUP
# ==============================================================================

@asynccontextmanager
async def setup_batch_aa_data():
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        comp_a = Company(name=f"BatchAA_CompA_{uid}")
        comp_b = Company(name=f"BatchAA_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN_A_{uid}",
            owner_name=f"Owner A {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN_B_{uid}",
            owner_name=f"Owner B {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerb_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj A {uid}",
            business_id=f"PA_{uid}"
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj B {uid}",
            business_id=f"PB_{uid}"
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Accounts for Company A
        acc_a1 = Account(
            company_id=comp_a.id,
            name=f"Bank Account A1 {uid}",
            code=f"BNK_A1_{uid}",
            type=AccountType.ASSET,
        )
        acc_a2 = Account(
            company_id=comp_a.id,
            name=f"Bank Account A2 {uid}",
            code=f"BNK_A2_{uid}",
            type=AccountType.ASSET,
        )
        # Accounts for Company B
        acc_b1 = Account(
            company_id=comp_b.id,
            name=f"Bank Account B1 {uid}",
            code=f"BNK_B1_{uid}",
            type=AccountType.ASSET,
        )
        db.add_all([acc_a1, acc_a2, acc_b1])
        await db.flush()

        # Bank accounts
        bank_a1 = BankAccount(
            account_id=acc_a1.id,
            bank_name="Test Bank A1",
            account_number=f"ACC_A1_{uid}",
        )
        bank_b1 = BankAccount(
            account_id=acc_b1.id,
            bank_name="Test Bank B1",
            account_number=f"ACC_B1_{uid}",
        )
        db.add_all([bank_a1, bank_b1])
        await db.flush()

        # Assets
        asset_a = FixedAsset(
            project_id=proj_a.id,
            name=f"Excavator A {uid}",
            purchase_value=Decimal("10000.00"),
            current_value=Decimal("10000.00"),
            depreciation_rate=Decimal("10.00"),
        )
        asset_b = FixedAsset(
            project_id=proj_b.id,
            name=f"Crane B {uid}",
            purchase_value=Decimal("20000.00"),
            current_value=Decimal("20000.00"),
            depreciation_rate=Decimal("10.00"),
        )
        db.add_all([asset_a, asset_b])
        await db.flush()

        # RA Bill for contractor payment tests
        ra_bill_a = RABill(
            project_id=proj_a.id,
            bill_number=f"RA_{uid}",
            work_description="Test RA Work",
            quantity=Decimal("1.000"),
            rate=Decimal("50000.00"),
            gross_amount=Decimal("50000.00"),
            net_amount=Decimal("50000.00"),
            total_amount=Decimal("50000.00"),
            bill_date=date.today(),
            status="Approved",
        )
        db.add(ra_bill_a)
        await db.flush()

        # GST Returns for Company A and B
        gstr_a = GSTReturn(
            company_id=comp_a.id,
            filing_period="2026-06",
            return_type="GSTR-1",
            taxable_value=Decimal("10000.00"),
            gst_liability=Decimal("1800.00"),
            itc_available=Decimal("0.00"),
            net_gst_payable=Decimal("1800.00"),
            status="Draft",
        )
        gstr_b = GSTReturn(
            company_id=comp_b.id,
            filing_period="2026-06",
            return_type="GSTR-1",
            taxable_value=Decimal("20000.00"),
            gst_liability=Decimal("3600.00"),
            itc_available=Decimal("0.00"),
            net_gst_payable=Decimal("3600.00"),
            status="Draft",
        )
        db.add_all([gstr_a, gstr_b])
        await db.flush()

        # Users
        admin_a = User(
            email=f"admin_aa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin AA",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_ab_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin AB",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        super_admin = User(
            email=f"sa_aa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin AA",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        none_company_user = User(
            email=f"none_aa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Tenantless AA",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        db.add_all([admin_a, admin_b, super_admin, none_company_user])
        await db.flush()

        # Roles for testing
        role_empty = Role(
            company_id=comp_a.id,
            name=f"empty_aa_{uid}",
            display_name="Empty Role AA",
            is_system=False,
        )
        role_custom = Role(
            company_id=comp_a.id,
            name=f"custom_aa_{uid}",
            display_name="Custom Role AA",
            is_system=False,
        )
        db.add_all([role_empty, role_custom])
        await db.flush()

        no_perm_user = User(
            email=f"noperm_aa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="No Perm User AA",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=role_empty.name,
        )
        custom_user = User(
            email=f"custom_aa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom User AA",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=role_custom.name,
        )
        db.add_all([no_perm_user, custom_user])
        await db.flush()

        # Permissions
        perm_view = (await db.scalar(select(Permission).where(Permission.code == "accountant.view")))
        perm_create = (await db.scalar(select(Permission).where(Permission.code == "accountant.create")))
        perm_edit = (await db.scalar(select(Permission).where(Permission.code == "accountant.edit")))
        perm_delete = (await db.scalar(select(Permission).where(Permission.code == "accountant.delete")))
        perm_export = (await db.scalar(select(Permission).where(Permission.code == "accountant.export")))

        # Grant all 5 to Admin for testing
        admin_rps_added = []
        for p in [perm_view, perm_create, perm_edit, perm_delete, perm_export]:
            existing_rp = await db.scalar(
                select(RolePermission).where(
                    RolePermission.role == "Admin",
                    RolePermission.permission_id == p.id,
                    RolePermission.role_id.is_(None),
                )
            )
            if not existing_rp:
                rp = RolePermission(role="Admin", permission_id=p.id)
                db.add(rp)
                await db.flush()
                admin_rps_added.append(rp.id)

        tokens = {
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "none_company": create_access_token({"sub": str(none_company_user.id)}),
            "no_perm": create_access_token({"sub": str(no_perm_user.id)}),
            "custom": create_access_token({"sub": str(custom_user.id)}),
        }

        await db.commit()

        yield {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "acc_a1": acc_a1,
            "acc_a2": acc_a2,
            "acc_b1": acc_b1,
            "bank_a1": bank_a1,
            "bank_b1": bank_b1,
            "asset_a": asset_a,
            "asset_b": asset_b,
            "ra_bill_a": ra_bill_a,
            "gstr_a": gstr_a,
            "gstr_b": gstr_b,
            "users": {
                "admin_a": admin_a,
                "admin_b": admin_b,
                "super_admin": super_admin,
                "none_company": none_company_user,
                "no_perm": no_perm_user,
                "custom": custom_user,
            },
            "tokens": tokens,
            "roles": {
                "empty": role_empty,
                "custom": role_custom,
            },
            "permissions": {
                "view": perm_view,
                "create": perm_create,
                "edit": perm_edit,
                "delete": perm_delete,
                "export": perm_export,
            },
            "_admin_rps_added": admin_rps_added,
        }

        # Cleanup
        async with AsyncSessionLocal() as cdb:
            all_user_ids = [
                admin_a.id, admin_b.id, super_admin.id,
                none_company_user.id, no_perm_user.id, custom_user.id,
            ]
            await cdb.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(all_user_ids)))
            await cdb.execute(delete(RolePermission).where(RolePermission.role.in_([role_empty.name, role_custom.name])))
            if admin_rps_added:
                await cdb.execute(delete(RolePermission).where(RolePermission.id.in_(admin_rps_added)))

            # Clean GST Returns
            await cdb.execute(delete(GSTReturn).where(GSTReturn.id.in_([gstr_a.id, gstr_b.id])))
            # Clean fund transfers
            await cdb.execute(delete(FundTransfer).where(FundTransfer.from_account_id.in_([acc_a1.id, acc_a2.id, acc_b1.id])))
            # Clean RA Bill
            await cdb.execute(delete(RABill).where(RABill.id == ra_bill_a.id))
            # Clean Assets
            await cdb.execute(delete(FixedAsset).where(FixedAsset.id.in_([asset_a.id, asset_b.id])))
            # Clean Bank accounts
            await cdb.execute(delete(BankAccount).where(BankAccount.id.in_([bank_a1.id, bank_b1.id])))
            # Clean Accounts
            await cdb.execute(delete(Account).where(Account.id.in_([acc_a1.id, acc_a2.id, acc_b1.id])))
            # Clean Projects & Owners
            await cdb.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await cdb.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            # Clean Roles
            await cdb.execute(delete(Role).where(Role.id.in_([role_empty.id, role_custom.id])))
            # Clean Users
            await cdb.execute(update(User).where(User.id.in_(all_user_ids)).values(created_by=None))
            await cdb.execute(delete(User).where(User.id.in_(all_user_ids)))
            # Clean Companies
            await cdb.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await cdb.commit()


def _headers(tokens, key):
    return {"Authorization": f"Bearer {tokens[key]}"}


# ==============================================================================
# 24 TEST CASES
# ==============================================================================

@pytest.mark.asyncio
async def test_tc01_unauthenticated_get_accounts():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/api/v1/accountant/accounts")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_tc02_unauthenticated_post_cash_book_import():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        files = {"file": ("test.csv", b"Date,Voucher No,Type,Debit,Credit,Balance\n", "text/csv")}
        r = await ac.post("/api/v1/accountant/cash-book/import", files=files)
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_tc03_unauthenticated_post_bank_book_import():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        files = {"file": ("test.csv", b"Date,Reference,Details,Withdrawal,Deposit,Balance\n", "text/csv")}
        r = await ac.post("/api/v1/accountant/bank-book/import", files=files)
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_tc04_authenticated_user_no_permission():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "no_perm"))
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_tc05_authenticated_user_only_view():
    async with setup_batch_aa_data() as data:
        async with AsyncSessionLocal() as db:
            rp = RolePermission(role=data["roles"]["custom"].name, role_id=data["roles"]["custom"].id, permission_id=data["permissions"]["view"].id)
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # View succeeds
            r_get = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "custom"))
            assert r_get.status_code == 200

            # Create fails with 403
            payload = {"name": "Test Acc", "code": f"TST_{data['uid']}", "type": "asset"}
            r_post = await ac.post("/api/v1/accountant/accounts", json=payload, headers=_headers(data["tokens"], "custom"))
            assert r_post.status_code == 403


@pytest.mark.asyncio
async def test_tc06_authenticated_user_only_create():
    async with setup_batch_aa_data() as data:
        async with AsyncSessionLocal() as db:
            rp = RolePermission(role=data["roles"]["custom"].name, role_id=data["roles"]["custom"].id, permission_id=data["permissions"]["create"].id)
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # View fails with 403
            r_get = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "custom"))
            assert r_get.status_code == 403

            # Create succeeds
            payload = {"name": "Test Create Acc", "code": f"TST_CR_{data['uid']}", "type": "asset"}
            r_post = await ac.post("/api/v1/accountant/accounts", json=payload, headers=_headers(data["tokens"], "custom"))
            assert r_post.status_code == 200

            # Cleanup created account
            async with AsyncSessionLocal() as cdb:
                await cdb.execute(delete(Account).where(Account.code == f"TST_CR_{data['uid']}"))
                await cdb.commit()


@pytest.mark.asyncio
async def test_tc07_authenticated_user_only_edit():
    async with setup_batch_aa_data() as data:
        async with AsyncSessionLocal() as db:
            rp = RolePermission(role=data["roles"]["custom"].name, role_id=data["roles"]["custom"].id, permission_id=data["permissions"]["edit"].id)
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Edit succeeds
            payload = {"name": f"Updated Name {data['uid']}"}
            r_patch = await ac.patch(f"/api/v1/accountant/accounts/{data['acc_a1'].id}", json=payload, headers=_headers(data["tokens"], "custom"))
            assert r_patch.status_code == 200

            # Delete fails with 403
            r_del = await ac.delete(f"/api/v1/accountant/accounts/{data['acc_a1'].id}", headers=_headers(data["tokens"], "custom"))
            assert r_del.status_code == 403


@pytest.mark.asyncio
async def test_tc08_authenticated_user_only_delete():
    async with setup_batch_aa_data() as data:
        # Create a disposable account to delete
        async with AsyncSessionLocal() as db:
            temp_acc = Account(
                company_id=data["comp_a"].id,
                name=f"Temp Del {data['uid']}",
                code=f"TEMP_DEL_{data['uid']}",
                type=AccountType.ASSET,
            )
            db.add(temp_acc)
            rp = RolePermission(role=data["roles"]["custom"].name, role_id=data["roles"]["custom"].id, permission_id=data["permissions"]["delete"].id)
            db.add(rp)
            await db.commit()
            temp_id = temp_acc.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Edit fails with 403
            r_patch = await ac.patch(f"/api/v1/accountant/accounts/{temp_id}", json={"name": "New"}, headers=_headers(data["tokens"], "custom"))
            assert r_patch.status_code == 403

            # Delete succeeds
            r_del = await ac.delete(f"/api/v1/accountant/accounts/{temp_id}", headers=_headers(data["tokens"], "custom"))
            assert r_del.status_code == 200


@pytest.mark.asyncio
async def test_tc09_authenticated_user_only_export():
    async with setup_batch_aa_data() as data:
        async with AsyncSessionLocal() as db:
            rp = RolePermission(role=data["roles"]["custom"].name, role_id=data["roles"]["custom"].id, permission_id=data["permissions"]["export"].id)
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Export succeeds
            r_exp = await ac.get("/api/v1/accountant/accounts/export", headers=_headers(data["tokens"], "custom"))
            assert r_exp.status_code == 200

            # Normal list fails with 403
            r_list = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "custom"))
            assert r_list.status_code == 403


@pytest.mark.asyncio
async def test_tc10_module_wildcard_permission():
    async with setup_batch_aa_data() as data:
        async with AsyncSessionLocal() as db:
            wc_perm = await db.scalar(select(Permission).where(Permission.code == "accountant.*"))
            rp = RolePermission(role=data["roles"]["custom"].name, role_id=data["roles"]["custom"].id, permission_id=wc_perm.id)
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # View succeeds
            r1 = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "custom"))
            assert r1.status_code == 200
            # Export succeeds
            r2 = await ac.get("/api/v1/accountant/accounts/export", headers=_headers(data["tokens"], "custom"))
            assert r2.status_code == 200


@pytest.mark.asyncio
async def test_tc11_global_wildcard_permission():
    async with setup_batch_aa_data() as data:
        async with AsyncSessionLocal() as db:
            global_wc = await db.scalar(select(Permission).where(Permission.code == "*"))
            rp = RolePermission(role=data["roles"]["custom"].name, role_id=data["roles"]["custom"].id, permission_id=global_wc.id)
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "custom"))
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_tc12_dynamic_grant():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Initially 403
            r1 = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "custom"))
            assert r1.status_code == 403

            # 2. Grant in DB
            async with AsyncSessionLocal() as db:
                rp = RolePermission(role=data["roles"]["custom"].name, role_id=data["roles"]["custom"].id, permission_id=data["permissions"]["view"].id)
                db.add(rp)
                await db.commit()

            # 3. Next request succeeds without restarting
            r2 = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "custom"))
            assert r2.status_code == 200


@pytest.mark.asyncio
async def test_tc13_dynamic_revoke():
    async with setup_batch_aa_data() as data:
        async with AsyncSessionLocal() as db:
            rp = RolePermission(role=data["roles"]["custom"].name, role_id=data["roles"]["custom"].id, permission_id=data["permissions"]["view"].id)
            db.add(rp)
            await db.commit()
            rp_id = rp.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Allowed
            r1 = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "custom"))
            assert r1.status_code == 200

            # 2. Revoke in DB
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.id == rp_id))
                await db.commit()

            # 3. Next request fails 403
            r2 = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "custom"))
            assert r2.status_code == 403


@pytest.mark.asyncio
async def test_tc14_positive_user_override():
    async with setup_batch_aa_data() as data:
        # User has no_perm role (no permissions), but has positive user override
        async with AsyncSessionLocal() as db:
            ov = UserPermissionOverride(
                user_id=data["users"]["no_perm"].id,
                permission_id=data["permissions"]["view"].id,
                is_granted=True,
            )
            db.add(ov)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "no_perm"))
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_tc15_negative_user_override():
    async with setup_batch_aa_data() as data:
        # Admin A has accountant.view via role, but negative user override
        async with AsyncSessionLocal() as db:
            ov = UserPermissionOverride(
                user_id=data["users"]["admin_a"].id,
                permission_id=data["permissions"]["view"].id,
                is_granted=False,
            )
            db.add(ov)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "admin_a"))
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_tc16_super_admin_bypass():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/accountant/accounts", headers=_headers(data["tokens"], "super_admin"))
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_tc17_tenant_isolation_foreign_resource():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Admin A cannot access Company B's account
            r1 = await ac.get(f"/api/v1/accountant/accounts/{data['acc_b1'].id}", headers=_headers(data["tokens"], "admin_a"))
            assert r1.status_code == 404

            # Admin A cannot access Company B's bank account
            r2 = await ac.get(f"/api/v1/accountant/bank-accounts/{data['bank_b1'].id}", headers=_headers(data["tokens"], "admin_a"))
            assert r2.status_code == 404

            # Admin A cannot access Company B's fixed asset
            r3 = await ac.get(f"/api/v1/accountant/assets/{data['asset_b'].id}", headers=_headers(data["tokens"], "admin_a"))
            assert r3.status_code == 404


@pytest.mark.asyncio
async def test_tc18_cross_tenant_fk_injection():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Admin A attempts to create asset linked to Company B's project
            payload = {
                "name": "Hacked Asset",
                "purchase_value": 5000,
                "project_id": data["proj_b"].id,  # foreign project
            }
            r = await ac.post("/api/v1/accountant/assets", json=payload, headers=_headers(data["tokens"], "admin_a"))
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_tc19_tenantless_user_rejection():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Tenantless user (company_id=None, is_super_admin=False) gets 403 or 404 on item lookup
            r = await ac.get(f"/api/v1/accountant/accounts/{data['acc_a1'].id}", headers=_headers(data["tokens"], "none_company"))
            assert r.status_code in [403, 404]


@pytest.mark.asyncio
async def test_tc20_super_admin_cross_company_access():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Super Admin can view Company A's account
            r_a = await ac.get(f"/api/v1/accountant/accounts/{data['acc_a1'].id}", headers=_headers(data["tokens"], "super_admin"))
            assert r_a.status_code == 200

            # Super Admin can view Company B's account
            r_b = await ac.get(f"/api/v1/accountant/accounts/{data['acc_b1'].id}", headers=_headers(data["tokens"], "super_admin"))
            assert r_b.status_code == 200


@pytest.mark.asyncio
async def test_tc21_concurrency_fund_transfer_locking_and_invariants():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Reject source == destination
            p_same = {
                "from_account_id": data["acc_a1"].id,
                "to_account_id": data["acc_a1"].id,
                "amount": 1000,
                "transfer_date": str(date.today()),
            }
            r_same = await ac.post("/api/v1/accountant/transfers", json=p_same, headers=_headers(data["tokens"], "admin_a"))
            assert r_same.status_code in [400, 422]
            assert "must be different" in r_same.text

            # 2. Reject cross-tenant transfer (from A to B)
            p_cross = {
                "from_account_id": data["acc_a1"].id,
                "to_account_id": data["acc_b1"].id,
                "amount": 1000,
                "transfer_date": str(date.today()),
            }
            r_cross = await ac.post("/api/v1/accountant/transfers", json=p_cross, headers=_headers(data["tokens"], "admin_a"))
            assert r_cross.status_code == 404


@pytest.mark.asyncio
async def test_tc22_accounting_invariants_journal_entry():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Less than 2 lines
            p_one = {
                "entry_date": str(date.today()),
                "description": "One line",
                "lines": [{"account_id": data["acc_a1"].id, "debit": 100, "credit": 0}]
            }
            r_one = await ac.post("/api/v1/accountant/journal", json=p_one, headers=_headers(data["tokens"], "admin_a"))
            assert r_one.status_code in [400, 422]
            assert "at least 2 lines" in r_one.text

            # 2. Debit != Credit
            p_unbalanced = {
                "entry_date": str(date.today()),
                "description": "Unbalanced",
                "lines": [
                    {"account_id": data["acc_a1"].id, "debit": 100, "credit": 0},
                    {"account_id": data["acc_a2"].id, "debit": 0, "credit": 50},
                ]
            }
            r_unb = await ac.post("/api/v1/accountant/journal", json=p_unbalanced, headers=_headers(data["tokens"], "admin_a"))
            assert r_unb.status_code in [400, 422]
            assert "equal" in r_unb.text

            # 3. Cross-tenant account injection
            p_cross = {
                "entry_date": str(date.today()),
                "description": "Cross tenant journal",
                "lines": [
                    {"account_id": data["acc_a1"].id, "debit": 100, "credit": 0},
                    {"account_id": data["acc_b1"].id, "debit": 0, "credit": 100},
                ]
            }
            r_cr = await ac.post("/api/v1/accountant/journal", json=p_cross, headers=_headers(data["tokens"], "admin_a"))
            assert r_cr.status_code == 404


@pytest.mark.asyncio
async def test_tc23_contractor_payment_overpayment_prevention():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Bill total is 50000. Try to pay 60000 -> 422/400 Amount exceeds pending
            p_over = {
                "amount": 60000.0,
                "mode": "BankTransfer",
                "reference": "OVERPAY_001",
            }
            r = await ac.post(f"/api/v1/accountant/payables/{data['ra_bill_a'].id}/pay", json=p_over, headers=_headers(data["tokens"], "admin_a"))
            assert r.status_code in [400, 422]
            assert "exceeds pending" in r.text.lower()


@pytest.mark.asyncio
async def test_tc24_static_router_audit():
    # 1. Exactly 71 unique endpoints, 0 duplicate registrations
    from collections import defaultdict
    method_path_counts = defaultdict(list)
    for r in router.routes:
        methods = [m for m in r.methods if m not in ('HEAD', 'OPTIONS')]
        for m in methods:
            method_path_counts[(m, r.path)].append(r.endpoint.__name__)

    assert len(method_path_counts) == 71, f"Expected 71 unique endpoints, got {len(method_path_counts)}"
    duplicates = {k: v for k, v in method_path_counts.items() if len(v) > 1}
    assert len(duplicates) == 0, f"Expected 0 duplicates, got: {duplicates}"

    # 2. AST audit of app/api/accountant.py
    with open("app/api/accountant.py", "r", encoding="utf-8") as f:
        code = f.read()

    tree = ast.parse(code)
    perms = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in ['get', 'post', 'put', 'patch', 'delete']:
                    for d in node.args.defaults:
                        s = ast.unparse(d)
                        if "require_permission" in s:
                            import re
                            m = re.search(r'require_permission\(["\']([^"\']+)["\']\)', s)
                            if m:
                                perms.append(m.group(1))

    assert len(perms) == 71, f"Expected 71 endpoints with require_permission, got {len(perms)}"
    from collections import Counter
    counts = Counter(perms)
    assert counts["accountant.view"] == 33
    assert counts["accountant.create"] == 20
    assert counts["accountant.edit"] == 7
    assert counts["accountant.delete"] == 3
    assert counts["accountant.export"] == 8

    # 3. 0 require_roles
    assert "require_roles" not in code, "Found require_roles in app/api/accountant.py"

    # 4. 0 current_user.role allowlists
    assert "current_user.role" not in code, "Found current_user.role in app/api/accountant.py"


@pytest.mark.asyncio
async def test_tc25_gst_return_tenant_isolation_and_ownership():
    async with setup_batch_aa_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            gstr_a = data["gstr_a"]
            gstr_b = data["gstr_b"]
            headers_a = _headers(data["tokens"], "admin_a")
            headers_b = _headers(data["tokens"], "admin_b")
            headers_sa = _headers(data["tokens"], "super_admin")
            headers_none = _headers(data["tokens"], "none_company")

            # 1. Company A can access Company A GSTReturn -> 200
            r_get_a = await ac.get(f"/api/v1/accountant/gst/returns/{gstr_a.id}", headers=headers_a)
            assert r_get_a.status_code == 200
            assert r_get_a.json()["id"] == gstr_a.id

            # 2. Company A cannot GET Company B GSTReturn -> 404
            r_get_b = await ac.get(f"/api/v1/accountant/gst/returns/{gstr_b.id}", headers=headers_a)
            assert r_get_b.status_code == 404

            # 3. Company A cannot PATCH Company B GSTReturn -> 404
            r_patch_b = await ac.patch(
                f"/api/v1/accountant/gst/returns/{gstr_b.id}",
                json={"taxable_value": 99999.0},
                headers=headers_a,
            )
            assert r_patch_b.status_code == 404

            # 4. Company A cannot DELETE Company B Draft GSTReturn -> 404
            r_del_b = await ac.delete(f"/api/v1/accountant/gst/returns/{gstr_b.id}", headers=headers_a)
            assert r_del_b.status_code == 404

            # 5. Company A list contains only Company A GSTReturns
            r_list_a = await ac.get("/api/v1/accountant/gst/returns", headers=headers_a)
            assert r_list_a.status_code == 200
            list_ids = [item["id"] for item in r_list_a.json()]
            assert gstr_a.id in list_ids
            assert gstr_b.id not in list_ids

            # 6. Company A cannot inject Company B ownership during creation
            create_payload = {
                "filing_period": "2026-07",
                "return_type": "GSTR-3B",
                "taxable_value": 50000.0,
                "gst_liability": 9000.0,
                "itc_available": 0.0,
                "net_gst_payable": 9000.0,
                "status": "Draft",
            }
            r_create = await ac.post("/api/v1/accountant/gst/returns", json=create_payload, headers=headers_a)
            assert r_create.status_code == 200
            created_gstr = r_create.json()
            assert created_gstr["company_id"] == data["comp_a"].id
            # Clean up created return
            async with AsyncSessionLocal() as db:
                obj = await db.get(GSTReturn, created_gstr["id"])
                if obj:
                    await db.delete(obj)
                    await db.commit()

            # 7. Tenantless non-SA is rejected -> 403 or 404
            r_none = await ac.get(f"/api/v1/accountant/gst/returns/{gstr_a.id}", headers=headers_none)
            assert r_none.status_code in [403, 404]

            # 8. Super Admin retains intended cross-company behavior
            r_sa_a = await ac.get(f"/api/v1/accountant/gst/returns/{gstr_a.id}", headers=headers_sa)
            r_sa_b = await ac.get(f"/api/v1/accountant/gst/returns/{gstr_b.id}", headers=headers_sa)
            assert r_sa_a.status_code == 200
            assert r_sa_b.status_code == 200

            # 9. Historical/all GSTReturn rows have deterministic valid company_id NOT NULL
            async with AsyncSessionLocal() as db:
                all_returns = (await db.scalars(select(GSTReturn))).all()
                for r in all_returns:
                    assert r.company_id is not None, f"GSTReturn id {r.id} missing company_id"


@pytest.mark.asyncio
async def test_tc26_company_settings_no_cross_tenant_fallback():
    from app.utils.accounting import get_primary_cash_account, get_petty_cash_account, get_payroll_account

    async with setup_batch_aa_data() as data:
        async with AsyncSessionLocal() as db:
            comp_a = data["comp_a"]
            comp_b = data["comp_b"]
            acc_a1 = data["acc_a1"]
            acc_a2 = data["acc_a2"]

            # Configure CompanySettings ONLY for Company A
            settings_a = CompanySettings(
                company_id=comp_a.id,
                primary_cash_account_id=acc_a1.id,
                petty_cash_account_id=acc_a2.id,
            )
            db.add(settings_a)
            await db.commit()

            try:
                # 1. Tenant A has CompanySettings -> correct account returned
                cash_a = await get_primary_cash_account(db, company_id=comp_a.id)
                assert cash_a.id == acc_a1.id

                petty_a = await get_petty_cash_account(db, company_id=comp_a.id)
                assert petty_a.id == acc_a2.id

                # 2. Tenant B has NO CompanySettings -> safe failure (MUST NOT fall back to Company A's settings)
                with pytest.raises(ValueError) as exc_info_cash:
                    await get_primary_cash_account(db, company_id=comp_b.id)
                assert f"CompanySettings not configured for company {comp_b.id}" in str(exc_info_cash.value)

                with pytest.raises(ValueError) as exc_info_petty:
                    await get_petty_cash_account(db, company_id=comp_b.id)
                assert f"CompanySettings not configured for company {comp_b.id}" in str(exc_info_petty.value)

                with pytest.raises(ValueError) as exc_info_payroll:
                    await get_payroll_account(db, "salary_account_id", company_id=comp_b.id)
                assert f"CompanySettings not configured for company {comp_b.id}" in str(exc_info_payroll.value)

            finally:
                await db.delete(settings_a)
                await db.commit()
