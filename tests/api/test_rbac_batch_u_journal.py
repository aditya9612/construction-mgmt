"""
RBAC Phase 2 – Batch U: Journal Entries Management
Tests for all 14 routes against:
- 401 unauthenticated
- 403 missing permission
- Dynamic grant/revoke
- User overrides (positive/negative)
- Wildcard permissions
- Tenant isolation (own-company vs foreign-company)
- SA cross-company access
- Account injection attacks
- Double-entry accounting rules
- CSV import/export security
- Recurring journal management
- Exception hygiene
"""

import io
import uuid
import re
from decimal import Decimal
from datetime import date, timedelta
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, update

from app.main import app
from app.core.db import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.accountant import (
    Account,
    JournalEntry,
    JournalLine,
    RecurringJournal,
)
from app.models.approval import Approval
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import AccountType


# ==============================================================================
# FIXTURE DATA SETUP
# ==============================================================================

@asynccontextmanager
async def setup_batch_u_data():
    """
    Seed two companies, accounts, users, and roles for Batch U journal tests.
    Also explicitly grants journal.* permissions to Admin role for the duration
    of the tests, cleaning them up afterward. Keeps the global '*' wildcard safe.
    """
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # --- Companies ---
        comp_a = Company(name=f"BatchU_CompA_{uid}")
        comp_b = Company(name=f"BatchU_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # --- Accounts (strictly per tenant) ---
        acc_a_debit = Account(
            company_id=comp_a.id,
            name=f"Cash A {uid}",
            code=f"CASH_A_{uid}",
            type=AccountType.ASSET,
        )
        acc_a_credit = Account(
            company_id=comp_a.id,
            name=f"Revenue A {uid}",
            code=f"REV_A_{uid}",
            type=AccountType.LIABILITY,
        )
        acc_b_debit = Account(
            company_id=comp_b.id,
            name=f"Cash B {uid}",
            code=f"CASH_B_{uid}",
            type=AccountType.ASSET,
        )
        acc_b_credit = Account(
            company_id=comp_b.id,
            name=f"Revenue B {uid}",
            code=f"REV_B_{uid}",
            type=AccountType.LIABILITY,
        )
        db.add_all([acc_a_debit, acc_a_credit, acc_b_debit, acc_b_credit])
        await db.flush()

        # --- Users ---
        admin_a = User(
            email=f"admin_ua_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin UA",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_ub_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin UB",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        super_admin = User(
            email=f"sa_u_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin U",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        none_company_user = User(
            email=f"none_u_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Tenantless U",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        db.add_all([admin_a, admin_b, super_admin, none_company_user])
        await db.flush()

        # --- Isolated roles for RBAC tests ---
        empty_role_name = f"empty_u_{uid}"
        custom_role_name = f"custom_u_{uid}"
        role_empty = Role(
            company_id=comp_a.id,
            name=empty_role_name,
            display_name="Empty Role U",
            is_system=False,
        )
        role_custom = Role(
            company_id=comp_a.id,
            name=custom_role_name,
            display_name="Custom Role U",
            is_system=False,
        )
        db.add_all([role_empty, role_custom])
        await db.flush()

        no_perm_user = User(
            email=f"noperm_u_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="No Perm User U",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=empty_role_name,
        )
        custom_user = User(
            email=f"custom_u_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom User U",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )
        db.add_all([no_perm_user, custom_user])
        await db.flush()

        # --- Fetch journal permissions ---
        perm_view = (await db.execute(select(Permission).where(Permission.code == "journal.view"))).scalar_one()
        perm_create = (await db.execute(select(Permission).where(Permission.code == "journal.create"))).scalar_one()
        perm_edit = (await db.execute(select(Permission).where(Permission.code == "journal.edit"))).scalar_one()
        perm_delete = (await db.execute(select(Permission).where(Permission.code == "journal.delete"))).scalar_one()
        perm_export = (await db.execute(select(Permission).where(Permission.code == "journal.export"))).scalar_one()

        # --- SELF-CONTAINED: Ensure Admin role has ALL journal permissions for this test session ---
        # Check which Admin→journal RolePermissions already exist, add the missing ones
        admin_journal_rp_ids_added = []  # track only the ones we add
        for perm in [perm_view, perm_create, perm_edit, perm_delete, perm_export]:
            existing_rp = (await db.execute(
                select(RolePermission).where(
                    RolePermission.role == "Admin",
                    RolePermission.permission_id == perm.id,
                    RolePermission.role_id.is_(None),
                )
            )).scalar_one_or_none()
            if not existing_rp:
                rp = RolePermission(role="Admin", permission_id=perm.id)
                db.add(rp)
                await db.flush()
                admin_journal_rp_ids_added.append(rp.id)

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
            "accounts_a": {"debit": acc_a_debit, "credit": acc_a_credit},
            "accounts_b": {"debit": acc_b_debit, "credit": acc_b_credit},
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
            "_admin_rp_ids_added": admin_journal_rp_ids_added,
        }

        # --- CLEANUP ---
        async with AsyncSessionLocal() as cdb:
            all_user_ids = [
                admin_a.id, admin_b.id, super_admin.id,
                none_company_user.id, no_perm_user.id, custom_user.id,
            ]
            acc_ids = [acc_a_debit.id, acc_a_credit.id, acc_b_debit.id, acc_b_credit.id]
            comp_ids = [comp_a.id, comp_b.id]
            journal_perm_ids = [perm_view.id, perm_create.id, perm_edit.id, perm_delete.id, perm_export.id]

            # Remove user permission overrides
            await cdb.execute(delete(UserPermissionOverride).where(
                UserPermissionOverride.user_id.in_(all_user_ids)
            ))

            # Remove journal.* wildcard permission (code="journal.*") created in tests
            wildcard_perm = (await cdb.execute(
                select(Permission).where(Permission.code == "journal.*")
            )).scalar_one_or_none()
            if wildcard_perm:
                await cdb.execute(delete(UserPermissionOverride).where(
                    UserPermissionOverride.permission_id == wildcard_perm.id
                ))
                await cdb.execute(delete(RolePermission).where(
                    RolePermission.permission_id == wildcard_perm.id
                ))
                await cdb.execute(delete(Permission).where(Permission.id == wildcard_perm.id))

            # Remove custom role permissions
            await cdb.execute(delete(RolePermission).where(
                RolePermission.role.in_([empty_role_name, custom_role_name])
            ))

            # SAFELY remove only the Admin→journal RolePermissions WE ADDED
            if admin_journal_rp_ids_added:
                await cdb.execute(delete(RolePermission).where(
                    RolePermission.id.in_(admin_journal_rp_ids_added)
                ))

            # Clean recurring journals created by test users
            await cdb.execute(delete(RecurringJournal).where(
                RecurringJournal.created_by.in_(all_user_ids)
            ))

            # Clean approvals and journal entries
            je_ids_res = await cdb.scalars(
                select(JournalEntry.id).where(JournalEntry.created_by.in_(all_user_ids))
            )
            je_ids = list(je_ids_res.all())
            if je_ids:
                await cdb.execute(delete(Approval).where(
                    Approval.entity_id.in_(je_ids),
                    Approval.entity_type == "journal_entry",
                ))
                await cdb.execute(delete(JournalLine).where(JournalLine.entry_id.in_(je_ids)))
                await cdb.execute(delete(JournalEntry).where(JournalEntry.id.in_(je_ids)))

            # Clean accounts
            await cdb.execute(delete(Account).where(Account.id.in_(acc_ids)))

            # Clean roles
            await cdb.execute(delete(Role).where(Role.id.in_([role_empty.id, role_custom.id])))

            # Clean users
            await cdb.execute(
                update(User).where(User.id.in_(all_user_ids)).values(created_by=None)
            )
            await cdb.execute(delete(User).where(User.id.in_(all_user_ids)))

            # Clean companies
            await cdb.execute(delete(Company).where(Company.id.in_(comp_ids)))

            await cdb.commit()


def _headers(tokens, key):
    return {"Authorization": f"Bearer {tokens[key]}"}


def _journal_payload(acc_debit_id, acc_credit_id, amount="100.00"):
    return {
        "entry_date": str(date.today()),
        "description": "Test journal entry",
        "lines": [
            {"account_id": acc_debit_id, "debit": amount, "credit": "0"},
            {"account_id": acc_credit_id, "debit": "0", "credit": amount},
        ],
    }


def _recurring_payload(acc_debit_id, acc_credit_id, frequency="Monthly"):
    yesterday = str(date.today() - timedelta(days=1))
    return {
        "template_name": "Test Recurring",
        "frequency": frequency,
        "next_run_date": yesterday,
        "template_data": {
            "description": "Test recurring entry",
            "lines": [
                {"account_id": acc_debit_id, "debit": 100, "credit": 0},
                {"account_id": acc_credit_id, "debit": 0, "credit": 100},
            ],
        },
    }


# ==============================================================================
# TEST 1: 401 UNAUTHENTICATED — All 14 endpoints
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_401_all_14_endpoints():
    """All 14 journal endpoints must return 401 without authentication."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("POST", "/api/v1/journal/manual"),
            ("GET",  "/api/v1/journal/manual"),
            ("GET",  "/api/v1/journal/manual/1"),
            ("POST", "/api/v1/journal/adjustment"),
            ("GET",  "/api/v1/journal/adjustment"),
            ("GET",  "/api/v1/journal/adjustment/export"),
            ("POST", "/api/v1/journal/adjustment/import"),
            ("GET",  "/api/v1/journal/adjustment/1"),
            ("POST", "/api/v1/journal/recurring"),
            ("GET",  "/api/v1/journal/recurring"),
            ("GET",  "/api/v1/journal/recurring/export"),
            ("POST", "/api/v1/journal/recurring/run-due"),
            ("POST", "/api/v1/journal/recurring/1/toggle"),
            ("GET",  "/api/v1/journal/export"),
        ]
        for method, path in endpoints:
            res = await ac.request(method, path)
            assert res.status_code == 401, (
                f"Expected 401 for {method} {path}, got {res.status_code}"
            )


# ==============================================================================
# TEST 2: 403 MISSING PERMISSION — All 14 endpoints
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_403_missing_permission():
    """Users without journal permissions receive 403 on all 14 endpoints."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "no_perm")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("POST", "/api/v1/journal/manual"),
                ("GET",  "/api/v1/journal/manual"),
                ("GET",  "/api/v1/journal/manual/1"),
                ("POST", "/api/v1/journal/adjustment"),
                ("GET",  "/api/v1/journal/adjustment"),
                ("GET",  "/api/v1/journal/adjustment/export"),
                ("POST", "/api/v1/journal/adjustment/import"),
                ("GET",  "/api/v1/journal/adjustment/1"),
                ("POST", "/api/v1/journal/recurring"),
                ("GET",  "/api/v1/journal/recurring"),
                ("GET",  "/api/v1/journal/recurring/export"),
                ("POST", "/api/v1/journal/recurring/run-due"),
                ("POST", "/api/v1/journal/recurring/1/toggle"),
                ("GET",  "/api/v1/journal/export"),
            ]
            for method, path in endpoints:
                res = await ac.request(method, path, headers=headers)
                assert res.status_code == 403, (
                    f"Expected 403 for {method} {path}, got {res.status_code}: {res.text}"
                )


# ==============================================================================
# TEST 3: DYNAMIC DB GRANT AND REVOKE
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_dynamic_db_grant_and_revoke():
    """Granting journal.view in DB immediately allows access; revoking denies it."""
    async with setup_batch_u_data() as data:
        role_name = data["roles"]["custom"].name
        headers = _headers(data["tokens"], "custom")
        perm_view = data["permissions"]["view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially 403
            res1 = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res1.status_code == 403

            # Grant permission in DB
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, permission_id=perm_view.id))
                await db.commit()

            # Immediately 200
            res2 = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res2.status_code == 200

            # Revoke permission
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == role_name,
                    RolePermission.permission_id == perm_view.id,
                ))
                await db.commit()

            # Immediately 403 again
            res3 = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res3.status_code == 403


# ==============================================================================
# TEST 4: POSITIVE USER OVERRIDE
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_positive_user_override():
    """Positive user permission override grants access regardless of role."""
    async with setup_batch_u_data() as data:
        user = data["users"]["no_perm"]
        headers = _headers(data["tokens"], "no_perm")
        perm_view = data["permissions"]["view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_before = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res_before.status_code == 403

            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user.id, permission_id=perm_view.id, is_granted=True))
                await db.commit()

            res_after = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res_after.status_code == 200


# ==============================================================================
# TEST 5: NEGATIVE USER OVERRIDE
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_negative_user_override():
    """Negative user override denies access even when role has the permission (Admin with journal.view)."""
    async with setup_batch_u_data() as data:
        user = data["users"]["admin_a"]
        headers = _headers(data["tokens"], "admin_a")
        perm_view = data["permissions"]["view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Admin has journal.view via role → should get 200
            res1 = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res1.status_code == 200, f"Admin should have journal.view, got {res1.status_code}: {res1.text}"

            # Apply negative override for journal.view
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user.id, permission_id=perm_view.id, is_granted=False))
                await db.commit()

            # Should now get 403
            res2 = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res2.status_code == 403, f"Expected 403 after negative override, got {res2.status_code}"


# ==============================================================================
# TEST 6: journal.* MODULE WILDCARD (via UserPermissionOverride)
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_journal_module_wildcard():
    """journal.* module wildcard grants access to all journal endpoints for that user."""
    async with setup_batch_u_data() as data:
        user = data["users"]["no_perm"]
        headers = _headers(data["tokens"], "no_perm")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        async with AsyncSessionLocal() as db:
            wildcard_perm = Permission(
                module="journal",
                action="*",
                code="journal.*",
                description="Wildcard for journal tests",
            )
            db.add(wildcard_perm)
            await db.flush()
            db.add(UserPermissionOverride(
                user_id=user.id,
                permission_id=wildcard_perm.id,
                is_granted=True,
            ))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_get = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res_get.status_code == 200, f"journal.* should grant view: {res_get.text}"

            res_post = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_d.id, acc_c.id),
                headers=headers,
            )
            assert res_post.status_code == 200, f"journal.* should grant create: {res_post.text}"

            res_export = await ac.get("/api/v1/journal/export", headers=headers)
            assert res_export.status_code == 200, f"journal.* should grant export: {res_export.text}"


# ==============================================================================
# TEST 7: GLOBAL * WILDCARD (via UserPermissionOverride on custom_user)
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_global_wildcard():
    """Global * wildcard grants access to all journal endpoints."""
    async with setup_batch_u_data() as data:
        user = data["users"]["custom"]
        headers = _headers(data["tokens"], "custom")

        # Fetch the global * permission
        async with AsyncSessionLocal() as db:
            star_perm = (await db.execute(
                select(Permission).where(Permission.code == "*")
            )).scalar_one_or_none()
            if star_perm is None:
                pytest.skip("Global '*' permission not seeded in DB — run restore_admin_perms.py")

            db.add(UserPermissionOverride(
                user_id=user.id,
                permission_id=star_perm.id,
                is_granted=True,
            ))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_view = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res_view.status_code == 200, f"* wildcard should grant journal.view: {res_view.text}"

            res_recur = await ac.get("/api/v1/journal/recurring", headers=headers)
            assert res_recur.status_code == 200, f"* wildcard should grant journal.view (recurring): {res_recur.text}"

            res_export = await ac.get("/api/v1/journal/export", headers=headers)
            assert res_export.status_code == 200, f"* wildcard should grant journal.export: {res_export.text}"


# ==============================================================================
# TEST 8: TENANTLESS NON-SA → 403 ON ALL 14 ROUTES
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_tenantless_user_403_all_routes():
    """Non-SA user with company_id=None receives HTTP 403 on all 14 endpoints."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "none_company")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("POST", "/api/v1/journal/manual"),
                ("GET",  "/api/v1/journal/manual"),
                ("GET",  "/api/v1/journal/manual/1"),
                ("POST", "/api/v1/journal/adjustment"),
                ("GET",  "/api/v1/journal/adjustment"),
                ("GET",  "/api/v1/journal/adjustment/export"),
                ("POST", "/api/v1/journal/adjustment/import"),
                ("GET",  "/api/v1/journal/adjustment/1"),
                ("POST", "/api/v1/journal/recurring"),
                ("GET",  "/api/v1/journal/recurring"),
                ("GET",  "/api/v1/journal/recurring/export"),
                ("POST", "/api/v1/journal/recurring/run-due"),
                ("POST", "/api/v1/journal/recurring/1/toggle"),
                ("GET",  "/api/v1/journal/export"),
            ]
            for method, path in endpoints:
                res = await ac.request(method, path, headers=headers)
                assert res.status_code == 403, (
                    f"Expected 403 for {method} {path}, got {res.status_code}"
                )


# ==============================================================================
# TEST 9: ADMIN ROLE ALONE WITHOUT JOURNAL PERMISSION → 403
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_admin_role_without_journal_perm_403():
    """User with isolated role (no journal perms) gets 403 regardless of role name."""
    async with setup_batch_u_data() as data:
        # no_perm user has empty_role which has no permissions → should get 403
        headers = _headers(data["tokens"], "no_perm")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res.status_code == 403, (
                f"Role without journal perm should get 403, got {res.status_code}"
            )


# ==============================================================================
# TEST 10: ACCOUNTANT ROLE ALONE WITHOUT JOURNAL PERMISSION → 403
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_accountant_role_without_journal_perm_403():
    """User with custom role (no journal perms) gets 403 on journal endpoints."""
    async with setup_batch_u_data() as data:
        # custom user has custom_role with no permissions
        headers = _headers(data["tokens"], "custom")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/journal/adjustment", headers=headers)
            assert res.status_code == 403, (
                f"Role without journal perm should get 403, got {res.status_code}"
            )


# ==============================================================================
# TEST 11-12: OWN-TENANT MANUAL JOURNAL — Create, List, Detail
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_own_tenant_manual_journal_lifecycle():
    """Own-tenant manual journal: create, list, detail all succeed."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create
            res_create = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_d.id, acc_c.id, "250.00"),
                headers=headers,
            )
            assert res_create.status_code == 200, res_create.text
            j = res_create.json()
            assert j["entry_type"] == "Manual"
            assert j["status"] == "Pending"
            assert len(j["lines"]) == 2
            j_id = j["id"]

            # List
            res_list = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res_list.status_code == 200
            ids = [item["id"] for item in res_list.json()]
            assert j_id in ids

            # Detail
            res_detail = await ac.get(f"/api/v1/journal/manual/{j_id}", headers=headers)
            assert res_detail.status_code == 200
            assert res_detail.json()["id"] == j_id


# ==============================================================================
# TEST 13-14: OWN-TENANT ADJUSTMENT — Create, List, Detail
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_own_tenant_adjustment_lifecycle():
    """Own-tenant adjustment journal: create, list, detail all succeed."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/journal/adjustment",
                json=_journal_payload(acc_d.id, acc_c.id, "75.00"),
                headers=headers,
            )
            assert res_create.status_code == 200, res_create.text
            j = res_create.json()
            assert j["entry_type"] == "Adjustment"
            j_id = j["id"]

            res_list = await ac.get("/api/v1/journal/adjustment", headers=headers)
            assert res_list.status_code == 200
            ids = [item["id"] for item in res_list.json()]
            assert j_id in ids

            res_detail = await ac.get(f"/api/v1/journal/adjustment/{j_id}", headers=headers)
            assert res_detail.status_code == 200
            assert res_detail.json()["id"] == j_id


# ==============================================================================
# TEST 15: OWN-TENANT ADJUSTMENT EXPORT
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_own_tenant_adjustment_export():
    """Own-tenant adjustment journal CSV export returns CSV content."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/api/v1/journal/adjustment",
                json=_journal_payload(acc_d.id, acc_c.id),
                headers=headers,
            )

            res = await ac.get("/api/v1/journal/adjustment/export", headers=headers)
            assert res.status_code == 200
            assert "text/csv" in res.headers.get("content-type", "")


# ==============================================================================
# TEST 16: OWN-TENANT ADJUSTMENT CSV IMPORT
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_own_tenant_adjustment_import():
    """Own-tenant adjustment journal CSV import with valid balanced data succeeds."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]
        today = str(date.today())

        csv_content = (
            f"Date,Account_ID,Debit,Credit\n"
            f"{today},{acc_d.id},500.00,0\n"
            f"{today},{acc_c.id},0,500.00\n"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/adjustment/import",
                headers=headers,
                files={"file": ("test_import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["valid_records"] == 2
            assert body["errors"] == []


# ==============================================================================
# TEST 17-19: OWN-TENANT RECURRING — Create, List, Export
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_own_tenant_recurring_lifecycle():
    """Own-tenant recurring journal: create, list, export all succeed."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/journal/recurring",
                json=_recurring_payload(acc_d.id, acc_c.id, "Monthly"),
                headers=headers,
            )
            assert res_create.status_code == 200, res_create.text
            r = res_create.json()
            r_id = r["id"]
            assert r["frequency"] == "Monthly"

            res_list = await ac.get("/api/v1/journal/recurring", headers=headers)
            assert res_list.status_code == 200
            ids = [item["id"] for item in res_list.json()]
            assert r_id in ids

            res_export = await ac.get("/api/v1/journal/recurring/export", headers=headers)
            assert res_export.status_code == 200
            assert "text/csv" in res_export.headers.get("content-type", "")


# ==============================================================================
# TEST 20: OWN-TENANT RECURRING TOGGLE
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_own_tenant_recurring_toggle():
    """Own-tenant recurring journal toggle changes Active ↔ Paused."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/journal/recurring",
                json=_recurring_payload(acc_d.id, acc_c.id, "Weekly"),
                headers=headers,
            )
            assert res_create.status_code == 200, res_create.text
            r_id = res_create.json()["id"]

            # Toggle to Paused
            res1 = await ac.post(f"/api/v1/journal/recurring/{r_id}/toggle", headers=headers)
            assert res1.status_code == 200
            assert res1.json()["status"] == "Paused"

            # Toggle back to Active
            res2 = await ac.post(f"/api/v1/journal/recurring/{r_id}/toggle", headers=headers)
            assert res2.status_code == 200
            assert res2.json()["status"] == "Active"


# ==============================================================================
# TEST 21: OWN-TENANT RUN-DUE
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_own_tenant_run_due():
    """Own-tenant run-due generates journal entries from due recurring templates."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/journal/recurring",
                json=_recurring_payload(acc_d.id, acc_c.id, "Monthly"),
                headers=headers,
            )
            assert res_create.status_code == 200, res_create.text

            res_run = await ac.post("/api/v1/journal/recurring/run-due", headers=headers)
            assert res_run.status_code == 200
            body = res_run.json()
            assert "generated" in body["message"]


# ==============================================================================
# TEST 22: OWN-TENANT GENERAL JOURNAL EXPORT
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_own_tenant_general_export():
    """Own-tenant general journal export returns CSV."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_d.id, acc_c.id),
                headers=headers,
            )
            res = await ac.get("/api/v1/journal/export", headers=headers)
            assert res.status_code == 200
            assert "text/csv" in res.headers.get("content-type", "")


# ==============================================================================
# TEST 23: FOREIGN MANUAL JOURNAL DETAIL → 404
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_foreign_manual_journal_404():
    """Admin A cannot view Admin B's manual journal entries (masked 404)."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        headers_b = _headers(data["tokens"], "admin_b")
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_b,
            )
            assert res_create.status_code == 200, res_create.text
            j_id = res_create.json()["id"]

            res = await ac.get(f"/api/v1/journal/manual/{j_id}", headers=headers_a)
            assert res.status_code == 404, f"Expected 404, got {res.status_code}: {res.text}"


# ==============================================================================
# TEST 24: FOREIGN ADJUSTMENT DETAIL → 404
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_foreign_adjustment_404():
    """Admin A cannot view Admin B's adjustment journal entries (masked 404)."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        headers_b = _headers(data["tokens"], "admin_b")
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/journal/adjustment",
                json=_journal_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_b,
            )
            assert res_create.status_code == 200, res_create.text
            j_id = res_create.json()["id"]

            res = await ac.get(f"/api/v1/journal/adjustment/{j_id}", headers=headers_a)
            assert res.status_code == 404, f"Expected 404, got {res.status_code}"


# ==============================================================================
# TEST 25: FOREIGN RECURRING TOGGLE → 404
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_foreign_recurring_toggle_404():
    """Admin A cannot toggle Admin B's recurring journal (masked 404)."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        headers_b = _headers(data["tokens"], "admin_b")
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/journal/recurring",
                json=_recurring_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_b,
            )
            assert res_create.status_code == 200, res_create.text
            r_id = res_create.json()["id"]

            res = await ac.post(f"/api/v1/journal/recurring/{r_id}/toggle", headers=headers_a)
            assert res.status_code == 404, f"Expected 404, got {res.status_code}"


# ==============================================================================
# TEST 26: FOREIGN ACCOUNT INJECTION IN MANUAL JOURNAL → REJECTED
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_foreign_account_injection_manual():
    """Admin A using Company B account IDs in manual journal creation is rejected."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_a,
            )
            assert res.status_code in (400, 404), (
                f"Expected 400/404, got {res.status_code}: {res.text}"
            )


# ==============================================================================
# TEST 27: FOREIGN ACCOUNT INJECTION IN ADJUSTMENT JOURNAL → REJECTED
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_foreign_account_injection_adjustment():
    """Admin A using Company B account IDs in adjustment journal creation is rejected."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/adjustment",
                json=_journal_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_a,
            )
            assert res.status_code in (400, 404), (
                f"Expected 400/404, got {res.status_code}: {res.text}"
            )


# ==============================================================================
# TEST 28: FOREIGN ACCOUNT INJECTION IN CSV IMPORT → ERRORS RETURNED
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_foreign_account_injection_csv_import():
    """Admin A importing CSV with Company B account IDs returns errors, no valid records."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]
        today = str(date.today())

        csv_content = (
            f"Date,Account_ID,Debit,Credit\n"
            f"{today},{acc_b_d.id},500.00,0\n"
            f"{today},{acc_b_c.id},0,500.00\n"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/adjustment/import",
                headers=headers_a,
                files={"file": ("import.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["valid_records"] == 0 or body["errors"], (
                "Foreign account injection should produce errors or zero valid records"
            )


# ==============================================================================
# TEST 29: FOREIGN ACCOUNT INJECTION IN RECURRING TEMPLATE → REJECTED
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_foreign_account_injection_recurring():
    """Admin A using Company B account IDs in recurring template creation is rejected."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/recurring",
                json=_recurring_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_a,
            )
            assert res.status_code in (400, 404), (
                f"Expected 400/404, got {res.status_code}: {res.text}"
            )


# ==============================================================================
# TEST 30: MIXED-COMPANY JOURNAL LINES → 400 (even for SA)
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_mixed_company_journal_rejected():
    """A journal entry with accounts from different companies is rejected 400."""
    async with setup_batch_u_data() as data:
        headers_sa = _headers(data["tokens"], "super_admin")
        acc_a_d = data["accounts_a"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_a_d.id, acc_b_c.id),
                headers=headers_sa,
            )
            assert res.status_code == 400, (
                f"Expected 400 for mixed-company journal, got {res.status_code}: {res.text}"
            )


# ==============================================================================
# TEST 31: CROSS-TENANT LIST ISOLATION
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_cross_tenant_list_isolation():
    """Admin A's manual journal list does not include Admin B's journals."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        headers_b = _headers(data["tokens"], "admin_b")
        acc_a_d = data["accounts_a"]["debit"]
        acc_a_c = data["accounts_a"]["credit"]
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_a = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_a_d.id, acc_a_c.id),
                headers=headers_a,
            )
            assert res_a.status_code == 200, res_a.text
            j_a_id = res_a.json()["id"]

            res_b = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_b,
            )
            assert res_b.status_code == 200, res_b.text
            j_b_id = res_b.json()["id"]

            res_list_a = await ac.get("/api/v1/journal/manual", headers=headers_a)
            ids_a = [item["id"] for item in res_list_a.json()]
            assert j_a_id in ids_a, "Admin A's own journal should appear in their list"
            assert j_b_id not in ids_a, "Admin B's journal should NOT appear in Admin A's list"


# ==============================================================================
# TEST 32: CROSS-TENANT CSV EXPORT ISOLATION
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_cross_tenant_csv_export_isolation():
    """Admin A's general export does not include Admin B's journal numbers."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        headers_b = _headers(data["tokens"], "admin_b")
        acc_a_d = data["accounts_a"]["debit"]
        acc_a_c = data["accounts_a"]["credit"]
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_a = await ac.post(
                "/api/v1/journal/adjustment",
                json=_journal_payload(acc_a_d.id, acc_a_c.id),
                headers=headers_a,
            )
            assert res_a.status_code == 200
            j_a_num = res_a.json().get("journal_number", "")

            res_b = await ac.post(
                "/api/v1/journal/adjustment",
                json=_journal_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_b,
            )
            assert res_b.status_code == 200
            j_b_num = res_b.json().get("journal_number", "")

            res_export = await ac.get("/api/v1/journal/adjustment/export", headers=headers_a)
            assert res_export.status_code == 200
            csv_content = res_export.text

            if j_b_num:
                assert j_b_num not in csv_content, (
                    "Admin B's journal number should NOT appear in Admin A's export"
                )


# ==============================================================================
# TEST 33: CROSS-TENANT RECURRING RUN ISOLATION
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_cross_tenant_recurring_run_isolation():
    """Admin A's run-due does NOT execute Company B's recurring journals."""
    async with setup_batch_u_data() as data:
        headers_a = _headers(data["tokens"], "admin_a")
        headers_b = _headers(data["tokens"], "admin_b")
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_b = await ac.post(
                "/api/v1/journal/recurring",
                json=_recurring_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_b,
            )
            assert res_b.status_code == 200, res_b.text
            r_b_id = res_b.json()["id"]

            # Admin A runs due journals (only Company A's)
            res_run = await ac.post("/api/v1/journal/recurring/run-due", headers=headers_a)
            assert res_run.status_code == 200

            # Company B's recurring template should NOT have been processed by Admin A
            async with AsyncSessionLocal() as db:
                je = await db.scalar(
                    select(JournalEntry).where(
                        JournalEntry.journal_number.like(f"REC-{r_b_id}-%"),
                        JournalEntry.created_by == data["users"]["admin_a"].id,
                    )
                )
                assert je is None, (
                    "Company B's recurring template was incorrectly executed by Admin A's run-due"
                )


# ==============================================================================
# TEST 34: SUPER ADMIN CROSS-COMPANY READ
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_sa_cross_company_read():
    """Super Admin can view journals from any company."""
    async with setup_batch_u_data() as data:
        headers_a   = _headers(data["tokens"], "admin_a")
        headers_b   = _headers(data["tokens"], "admin_b")
        headers_sa  = _headers(data["tokens"], "super_admin")
        acc_a_d = data["accounts_a"]["debit"]
        acc_a_c = data["accounts_a"]["credit"]
        acc_b_d = data["accounts_b"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_a = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_a_d.id, acc_a_c.id),
                headers=headers_a,
            )
            assert res_a.status_code == 200
            j_a_id = res_a.json()["id"]

            res_b = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_b_d.id, acc_b_c.id),
                headers=headers_b,
            )
            assert res_b.status_code == 200
            j_b_id = res_b.json()["id"]

            # SA can see both detail
            res_sa_a = await ac.get(f"/api/v1/journal/manual/{j_a_id}", headers=headers_sa)
            assert res_sa_a.status_code == 200, res_sa_a.text

            res_sa_b = await ac.get(f"/api/v1/journal/manual/{j_b_id}", headers=headers_sa)
            assert res_sa_b.status_code == 200, res_sa_b.text

            # SA list includes both companies
            res_list = await ac.get("/api/v1/journal/manual", headers=headers_sa)
            ids = [item["id"] for item in res_list.json()]
            assert j_a_id in ids
            assert j_b_id in ids


# ==============================================================================
# TEST 35: SA CREATE WITH SINGLE-COMPANY ACCOUNTS
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_sa_create_single_company_journal():
    """Super Admin can create a journal using accounts all from ONE company."""
    async with setup_batch_u_data() as data:
        headers_sa = _headers(data["tokens"], "super_admin")
        acc_a_d = data["accounts_a"]["debit"]
        acc_a_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_a_d.id, acc_a_c.id),
                headers=headers_sa,
            )
            assert res.status_code == 200, (
                f"SA should create journal with single-company accounts, got {res.status_code}: {res.text}"
            )
            assert res.json()["entry_type"] == "Manual"


# ==============================================================================
# TEST 36: SA MIXED-COMPANY JOURNAL → 400
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_sa_mixed_company_journal_rejected():
    """Super Admin cannot create a journal with accounts from different companies."""
    async with setup_batch_u_data() as data:
        headers_sa = _headers(data["tokens"], "super_admin")
        acc_a_d = data["accounts_a"]["debit"]
        acc_b_c = data["accounts_b"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_a_d.id, acc_b_c.id),
                headers=headers_sa,
            )
            assert res.status_code == 400, (
                f"Expected 400 for mixed-company SA journal, got {res.status_code}: {res.text}"
            )


# ==============================================================================
# TEST 37: DOUBLE-ENTRY IMBALANCE → 400
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_double_entry_imbalance_rejected():
    """Journal with unbalanced debit/credit totals is rejected with 400."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        payload = {
            "entry_date": str(date.today()),
            "description": "Imbalanced entry",
            "lines": [
                {"account_id": acc_d.id, "debit": "300.00", "credit": "0"},
                {"account_id": acc_c.id, "debit": "0", "credit": "200.00"},  # mismatch
            ],
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/v1/journal/manual", json=payload, headers=headers)
            assert res.status_code == 400, (
                f"Expected 400 for imbalanced journal, got {res.status_code}: {res.text}"
            )


# ==============================================================================
# TEST 38: ZERO/ZERO LINE → 400
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_zero_zero_line_rejected():
    """Journal line with both debit=0 and credit=0 is rejected with 400."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        payload = {
            "entry_date": str(date.today()),
            "description": "Zero line entry",
            "lines": [
                {"account_id": acc_d.id, "debit": "0", "credit": "0"},  # invalid
                {"account_id": acc_c.id, "debit": "100", "credit": "0"},
            ],
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/v1/journal/manual", json=payload, headers=headers)
            assert res.status_code == 400, (
                f"Expected 400 for zero/zero line, got {res.status_code}"
            )


# ==============================================================================
# TEST 39: NEGATIVE DEBIT/CREDIT → 400
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_negative_amounts_rejected():
    """Journal with negative debit or credit is rejected with 400."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        payload = {
            "entry_date": str(date.today()),
            "description": "Negative amounts",
            "lines": [
                {"account_id": acc_d.id, "debit": "-100.00", "credit": "0"},
                {"account_id": acc_c.id, "debit": "0", "credit": "-100.00"},
            ],
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/v1/journal/manual", json=payload, headers=headers)
            assert res.status_code == 400, (
                f"Expected 400 for negative amounts, got {res.status_code}"
            )


# ==============================================================================
# TEST 40: APPROVAL LINKAGE CREATED
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_approval_linkage_created():
    """Creating a manual journal entry creates an associated Approval record."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_d.id, acc_c.id),
                headers=headers,
            )
            assert res.status_code == 200
            j_id = res.json()["id"]

        async with AsyncSessionLocal() as db:
            approval = await db.scalar(
                select(Approval).where(
                    Approval.entity_type == "journal_entry",
                    Approval.entity_id == j_id,
                )
            )
            assert approval is not None, "Approval should be created for manual journal"
            assert approval.status == "Pending"
            assert approval.requested_by == data["users"]["admin_a"].id


# ==============================================================================
# TEST 41: RECURRING FREQUENCY ADVANCEMENT
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_recurring_frequency_advancement():
    """Running due recurring journals advances next_run_date by the correct frequency."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/journal/recurring",
                json=_recurring_payload(acc_d.id, acc_c.id, "Weekly"),
                headers=headers,
            )
            assert res_create.status_code == 200, res_create.text
            r_id = res_create.json()["id"]
            original_next_run = date.fromisoformat(res_create.json()["next_run_date"])

            await ac.post("/api/v1/journal/recurring/run-due", headers=headers)

            async with AsyncSessionLocal() as db:
                r = await db.get(RecurringJournal, r_id)
                expected = original_next_run + timedelta(days=7)
                assert r.next_run_date == expected, (
                    f"Expected next_run_date={expected}, got {r.next_run_date}"
                )


# ==============================================================================
# TEST 42: RECURRING JOURNAL IDEMPOTENCY
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_recurring_idempotency():
    """Running due journals with the same journal_number key does not create duplicates."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/journal/recurring",
                json=_recurring_payload(acc_d.id, acc_c.id, "Daily"),
                headers=headers,
            )
            assert res_create.status_code == 200
            r_id = res_create.json()["id"]

            # Run once — creates journal, advances next_run_date
            res_run1 = await ac.post("/api/v1/journal/recurring/run-due", headers=headers)
            assert res_run1.status_code == 200

            # Get current state
            async with AsyncSessionLocal() as db:
                r = await db.get(RecurringJournal, r_id)
                advanced_next_run = r.next_run_date
                # Reset next_run_date to yesterday (original due date)
                r.next_run_date = advanced_next_run - timedelta(days=1)
                await db.commit()

            # Run again — same journal_number already exists, should skip
            res_run2 = await ac.post("/api/v1/journal/recurring/run-due", headers=headers)
            assert res_run2.status_code == 200

            async with AsyncSessionLocal() as db:
                count_entries = len((await db.scalars(
                    select(JournalEntry).where(
                        JournalEntry.journal_number.like(f"REC-{r_id}-%")
                    )
                )).all())
                assert count_entries >= 1, "At least one recurring journal should have been generated"
                # Due to idempotency (same journal_number), second run should not double up
                # Count should remain at 1 for the original due date
                original_j_num = f"REC-{r_id}-{(advanced_next_run - timedelta(days=1)).strftime('%Y%m%d')}"
                dup = (await db.scalars(
                    select(JournalEntry).where(JournalEntry.journal_number == original_j_num)
                )).all()
                assert len(dup) == 1, f"Expected exactly 1 journal for {original_j_num}, got {len(dup)}"


# ==============================================================================
# TEST 43: CSV > 5MB REJECTION
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_csv_too_large_rejected():
    """CSV import files larger than 5MB are rejected with 400."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")

        # ~6MB CSV
        big_csv = b"Date,Account_ID,Debit,Credit\n" + b"2026-01-01,1,100.00,0\n" * 360000

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/adjustment/import",
                headers=headers,
                files={"file": ("big.csv", io.BytesIO(big_csv), "text/csv")},
            )
            assert res.status_code == 400, (
                f"Expected 400 for >5MB CSV, got {res.status_code}"
            )


# ==============================================================================
# TEST 44: CSV INVALID ROW → FULL ROLLBACK (no partial commit)
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_csv_invalid_row_full_rollback():
    """CSV import with any invalid row causes full rollback - no partial commit."""
    async with setup_batch_u_data() as data:
        headers = _headers(data["tokens"], "admin_a")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]
        today = str(date.today())

        csv_content = (
            f"Date,Account_ID,Debit,Credit\n"
            f"{today},{acc_d.id},100.00,0\n"
            f"{today},{acc_c.id},0,100.00\n"
            f"{today},99999999,50.00,0\n"  # invalid account → triggers error
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/journal/adjustment/import",
                headers=headers,
                files={"file": ("partial.csv", io.BytesIO(csv_content.encode()), "text/csv")},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["valid_records"] == 0, (
                f"Expected 0 valid records due to error, got {body['valid_records']}"
            )
            assert len(body["errors"]) > 0, "Expected errors for invalid account"


# ==============================================================================
# TEST 45: NO require_roles IN JOURNAL MODULE
# ==============================================================================
def test_batch_u_no_require_roles_in_journal_py():
    """Verify journal.py contains no require_roles calls."""
    import os
    journal_path = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "api", "journal.py"
    ))
    with open(journal_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "require_roles" not in content, "journal.py must not use require_roles"
    assert "ACCOUNTANT_READ_ROLES" not in content, "journal.py must not use ACCOUNTANT_READ_ROLES"
    assert "ACCOUNTANT_WRITE_ROLES" not in content, "journal.py must not use ACCOUNTANT_WRITE_ROLES"


# ==============================================================================
# TEST 46: NO HARDCODED ROLE AUTHORIZATION IN JOURNAL MODULE
# ==============================================================================
def test_batch_u_no_hardcoded_roles_in_journal_py():
    """Verify journal.py uses require_permission, not hardcoded role checks."""
    import os
    journal_path = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "api", "journal.py"
    ))
    with open(journal_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "require_permission" in content, "journal.py must use require_permission"
    assert "require_roles(" not in content, "journal.py must not call require_roles()"


# ==============================================================================
# ADDITIONAL: PERMISSION GRANULARITY VERIFICATION
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_u_permission_mapping_verification():
    """Verify that view-only does NOT grant create/export, and export perm enables export."""
    async with setup_batch_u_data() as data:
        user = data["users"]["no_perm"]
        headers = _headers(data["tokens"], "no_perm")
        acc_d = data["accounts_a"]["debit"]
        acc_c = data["accounts_a"]["credit"]
        perm_view = data["permissions"]["view"]
        perm_export = data["permissions"]["export"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Grant only view
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user.id, permission_id=perm_view.id, is_granted=True))
                await db.commit()

            # View should work
            res_view = await ac.get("/api/v1/journal/manual", headers=headers)
            assert res_view.status_code == 200

            # Create should be 403 (requires journal.create)
            res_create = await ac.post(
                "/api/v1/journal/manual",
                json=_journal_payload(acc_d.id, acc_c.id),
                headers=headers,
            )
            assert res_create.status_code == 403, (
                "create should require journal.create, not journal.view"
            )

            # Export should be 403 (requires journal.export)
            res_export = await ac.get("/api/v1/journal/export", headers=headers)
            assert res_export.status_code == 403, (
                "export should require journal.export, not journal.view"
            )

            # Add export permission
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user.id, permission_id=perm_export.id, is_granted=True))
                await db.commit()

            res_export2 = await ac.get("/api/v1/journal/export", headers=headers)
            assert res_export2.status_code == 200, "export should work with journal.export"
