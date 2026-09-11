"""
Test Suite: BATCH AJ — Project Expense Management Security/RBAC & Tenant Isolation
==================================================================================
Covers:
A. Authentication (all 16 endpoints -> 401 unauthenticated)
B. Granular RBAC Permissions (expenses.view, expenses.create, expenses.edit,
   expenses.delete, expenses.upload, expenses.export -> 403 on missing grant)
C. Tenantless Non-SA (company_id=None -> 403 across all 16 endpoints)
D. Valid Tenant Lifecycle (create, list, get, update, delete, summary,
   dashboard, boq-comparison, allocations, ledger, export, import)
E. Direct IDOR Protection (Tenant A accessing Tenant B -> 404 masking)
F. Foreign FK Injection (Tenant A creating with Tenant B project/BOQ/supplier -> 404)
G. Update Project Injection (reassigning expense to foreign project/BOQ -> 404)
H. Super Admin Semantics (global vs company-filtered list/dashboard/allocations/ledger/export,
   404 on invalid company)
I. Non-SA Company Override Prevention (foreign company_id query parameter ignored)
J. Export Security (tenant-scoped CSV vs SA global vs SA filtered)
K. Import Security (foreign tenant references in CSV rejected as errors)
L. Financial Ledger Invariants (JournalEntry, JournalLine Dr/Cr balance, OwnerTransaction)
M. Idempotency (same key returns cached, changed payload -> 409)
N. Route Preservation (16 expense routes, 16 unique method+path, 0 duplicates, 781 total routes)
O. Static Security Hygiene (0 require_roles, 0 admin_required, 0 UserRole, 0 role allowlists)
"""

import csv
import inspect
import io
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api import expense as expense_api_module
from app.core.db import AsyncSessionLocal
from app.core.enums import AccountType
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.accountant import Account, JournalEntry, JournalLine
from app.models.boq import BOQ, BOQGroup
from app.models.company import Company
from app.models.expense import Expense
from app.models.material import Supplier
from app.models.owner import Owner, OwnerTransaction
from app.models.project import Project
from app.models.rbac import Permission, Role, RolePermission
from app.models.settings import CompanySettings
from app.models.user import User


@asynccontextmanager
async def setup_expense_test_data():
    """Create test companies, projects, BOQs, accounts, settings, roles, permissions, users."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"ExpCompA_{uid}")
        comp_b = Company(name=f"ExpCompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Chart of Accounts & Settings for Double-Entry Accounting
        cash_acc_a = Account(name=f"Cash A {uid}", code=f"CASH_A_{uid}", type=AccountType.ASSET, company_id=comp_a.id)
        gen_exp_a = Account(name=f"General Expense A {uid}", code="GENERAL_EXPENSE", type=AccountType.EXPENSE, company_id=comp_a.id)
        cash_acc_b = Account(name=f"Cash B {uid}", code=f"CASH_B_{uid}", type=AccountType.ASSET, company_id=comp_b.id)
        gen_exp_b = Account(name=f"General Expense B {uid}", code="GENERAL_EXPENSE", type=AccountType.EXPENSE, company_id=comp_b.id)
        db.add_all([cash_acc_a, gen_exp_a, cash_acc_b, gen_exp_b])
        await db.flush()

        settings_a = CompanySettings(company_id=comp_a.id, primary_cash_account_id=cash_acc_a.id)
        settings_b = CompanySettings(company_id=comp_b.id, primary_cash_account_id=cash_acc_b.id)
        db.add_all([settings_a, settings_b])
        await db.flush()

        # 3. Owners & Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OA_{uid}",
            owner_name=f"Owner A {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OB_{uid}",
            owner_name=f"Owner B {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerb_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(company_id=comp_a.id, owner_id=owner_a.id, project_name=f"Project A {uid}", business_id=f"PA_{uid}")
        proj_b = Project(company_id=comp_b.id, owner_id=owner_b.id, project_name=f"Project B {uid}", business_id=f"PB_{uid}")
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 4. BOQ items
        boq_group_a = BOQGroup(project_id=proj_a.id, name=f"Group A {uid}")
        boq_group_b = BOQGroup(project_id=proj_b.id, name=f"Group B {uid}")
        db.add_all([boq_group_a, boq_group_b])
        await db.flush()

        boq_a = BOQ(
            project_id=proj_a.id,
            boq_group_id=boq_group_a.id,
            category="Civil",
            item_name="Foundation Works",
            quantity=50.0,
            unit="cum",
            unit_cost=4000.0,
            total_cost=200000.0,
            is_latest=True,
        )
        boq_b = BOQ(
            project_id=proj_b.id,
            boq_group_id=boq_group_b.id,
            category="Civil",
            item_name="Roofing Works",
            quantity=50.0,
            unit="sqm",
            unit_cost=3000.0,
            total_cost=150000.0,
            is_latest=True,
        )
        db.add_all([boq_a, boq_b])
        await db.flush()

        # 5. Suppliers
        supplier_a = Supplier(company_id=comp_a.id, supplier_name=f"Supplier A {uid}")
        supplier_b = Supplier(company_id=comp_b.id, supplier_name=f"Supplier B {uid}")
        db.add_all([supplier_a, supplier_b])
        await db.flush()

        # 6. Permissions & Roles
        expense_perm_codes = [
            "expenses.view",
            "expenses.create",
            "expenses.edit",
            "expenses.delete",
            "expenses.upload",
            "expenses.export",
        ]
        perms = (await db.execute(
            select(Permission).where(Permission.code.in_(expense_perm_codes))
        )).scalars().all()
        perm_map = {p.code: p for p in perms}

        # Role with full permissions
        role_all = Role(company_id=comp_a.id, name=f"role_all_{uid}", display_name="All Expense Perms", is_system=False)
        role_b = Role(company_id=comp_b.id, name=f"role_b_{uid}", display_name="B Expense Perms", is_system=False)

        # Restricted roles (missing one specific permission)
        role_no_view = Role(company_id=comp_a.id, name=f"role_noview_{uid}", display_name="No View", is_system=False)
        role_no_create = Role(company_id=comp_a.id, name=f"role_nocreate_{uid}", display_name="No Create", is_system=False)
        role_no_edit = Role(company_id=comp_a.id, name=f"role_noedit_{uid}", display_name="No Edit", is_system=False)
        role_no_delete = Role(company_id=comp_a.id, name=f"role_nodelete_{uid}", display_name="No Delete", is_system=False)
        role_no_upload = Role(company_id=comp_a.id, name=f"role_noupload_{uid}", display_name="No Upload", is_system=False)
        role_no_export = Role(company_id=comp_a.id, name=f"role_noexport_{uid}", display_name="No Export", is_system=False)

        db.add_all([
            role_all, role_b, role_no_view, role_no_create,
            role_no_edit, role_no_delete, role_no_upload, role_no_export
        ])
        await db.flush()

        for code in expense_perm_codes:
            db.add(RolePermission(role=role_all.name, role_id=role_all.id, permission_id=perm_map[code].id))
            db.add(RolePermission(role=role_b.name, role_id=role_b.id, permission_id=perm_map[code].id))

        # Assign all except the omitted permission
        for code in expense_perm_codes:
            if code != "expenses.view":
                db.add(RolePermission(role=role_no_view.name, role_id=role_no_view.id, permission_id=perm_map[code].id))
            if code != "expenses.create":
                db.add(RolePermission(role=role_no_create.name, role_id=role_no_create.id, permission_id=perm_map[code].id))
            if code != "expenses.edit":
                db.add(RolePermission(role=role_no_edit.name, role_id=role_no_edit.id, permission_id=perm_map[code].id))
            if code != "expenses.delete":
                db.add(RolePermission(role=role_no_delete.name, role_id=role_no_delete.id, permission_id=perm_map[code].id))
            if code != "expenses.upload":
                db.add(RolePermission(role=role_no_upload.name, role_id=role_no_upload.id, permission_id=perm_map[code].id))
            if code != "expenses.export":
                db.add(RolePermission(role=role_no_export.name, role_id=role_no_export.id, permission_id=perm_map[code].id))

        await db.flush()

        # 7. Users
        user_a = User(
            company_id=comp_a.id,
            role=role_all.name,
            email=f"user_a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User A All",
            is_active=True,
            is_super_admin=False,
        )
        user_b = User(
            company_id=comp_b.id,
            role=role_b.name,
            email=f"user_b_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User B",
            is_active=True,
            is_super_admin=False,
        )
        user_no_view = User(
            company_id=comp_a.id,
            role=role_no_view.name,
            email=f"noview_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User No View",
            is_active=True,
            is_super_admin=False,
        )
        user_no_create = User(
            company_id=comp_a.id,
            role=role_no_create.name,
            email=f"nocreate_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User No Create",
            is_active=True,
            is_super_admin=False,
        )
        user_no_edit = User(
            company_id=comp_a.id,
            role=role_no_edit.name,
            email=f"noedit_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User No Edit",
            is_active=True,
            is_super_admin=False,
        )
        user_no_delete = User(
            company_id=comp_a.id,
            role=role_no_delete.name,
            email=f"nodelete_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User No Delete",
            is_active=True,
            is_super_admin=False,
        )
        user_no_upload = User(
            company_id=comp_a.id,
            role=role_no_upload.name,
            email=f"noupload_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User No Upload",
            is_active=True,
            is_super_admin=False,
        )
        user_no_export = User(
            company_id=comp_a.id,
            role=role_no_export.name,
            email=f"noexport_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User No Export",
            is_active=True,
            is_super_admin=False,
        )
        user_tenantless = User(
            company_id=None,
            role="Admin",
            email=f"tenantless_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User Tenantless",
            is_active=True,
            is_super_admin=False,
        )
        sa_global = User(
            company_id=None,
            role="SuperAdmin",
            email=f"saglobal_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="SA Global",
            is_active=True,
            is_super_admin=True,
        )
        db.add_all([
            user_a, user_b, user_no_view, user_no_create,
            user_no_edit, user_no_delete, user_no_upload, user_no_export,
            user_tenantless, sa_global
        ])
        await db.flush()

        # 8. Pre-existing Expenses
        exp_a = Expense(
            project_id=proj_a.id,
            category="Civil",
            description=f"Initial Cement A {uid}",
            amount=Decimal("5000.00"),
            expense_date=date.today(),
            payment_mode="cash",
            boq_item_id=boq_a.id,
        )
        exp_b = Expense(
            project_id=proj_b.id,
            category="Civil",
            description=f"Initial Bricks B {uid}",
            amount=Decimal("8000.00"),
            expense_date=date.today(),
            payment_mode="cash",
            boq_item_id=boq_b.id,
        )
        db.add_all([exp_a, exp_b])
        await db.flush()
        await db.commit()

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "boq_a": boq_a,
            "boq_b": boq_b,
            "supplier_a": supplier_a,
            "supplier_b": supplier_b,
            "exp_a": exp_a,
            "exp_b": exp_b,
            "token_a": create_access_token({"sub": str(user_a.id), "user_id": user_a.id}),
            "token_b": create_access_token({"sub": str(user_b.id), "user_id": user_b.id}),
            "token_noview": create_access_token({"sub": str(user_no_view.id), "user_id": user_no_view.id}),
            "token_nocreate": create_access_token({"sub": str(user_no_create.id), "user_id": user_no_create.id}),
            "token_noedit": create_access_token({"sub": str(user_no_edit.id), "user_id": user_no_edit.id}),
            "token_nodelete": create_access_token({"sub": str(user_no_delete.id), "user_id": user_no_delete.id}),
            "token_noupload": create_access_token({"sub": str(user_no_upload.id), "user_id": user_no_upload.id}),
            "token_noexport": create_access_token({"sub": str(user_no_export.id), "user_id": user_no_export.id}),
            "token_tl": create_access_token({"sub": str(user_tenantless.id), "user_id": user_tenantless.id}),
            "token_sa": create_access_token({"sub": str(sa_global.id), "user_id": sa_global.id}),
        }
        try:
            yield data
        finally:
            pass


# ==============================================================================
# GROUP A: Authentication (All 16 Endpoints -> 401 Unauthenticated)
# ==============================================================================
@pytest.mark.asyncio
async def test_authentication_all_endpoints():
    """Verify that all 16 expense routes reject unauthenticated requests with HTTP 401."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("GET", "/api/v1/expenses"),
                ("POST", "/api/v1/expenses"),
                ("GET", "/api/v1/expenses/date-range?start=2025-01-01&end=2025-01-31"),
                ("GET", f"/api/v1/expenses/{d['exp_a'].id}"),
                ("PUT", f"/api/v1/expenses/{d['exp_a'].id}"),
                ("DELETE", f"/api/v1/expenses/{d['exp_a'].id}"),
                ("GET", f"/api/v1/expenses/project/{d['proj_a'].id}"),
                ("GET", "/api/v1/expenses/category/Civil"),
                ("GET", "/api/v1/expenses/payment-mode/cash"),
                ("GET", f"/api/v1/expenses/summary/{d['proj_a'].id}"),
                ("GET", f"/api/v1/expenses/boq-comparison/{d['proj_a'].id}"),
                ("GET", "/api/v1/expenses/dashboard"),
                ("GET", "/api/v1/expenses/project-allocations"),
                ("GET", "/api/v1/expenses/ledger"),
                ("POST", "/api/v1/expenses/import"),
                ("GET", "/api/v1/expenses/export"),
            ]
            for method, path in endpoints:
                res = await ac.request(method, path)
                assert res.status_code == 401, f"{method} {path} returned {res.status_code}, expected 401"


# ==============================================================================
# GROUP B: Granular RBAC Permissions (Missing Permission -> 403)
# ==============================================================================
@pytest.mark.asyncio
async def test_rbac_permissions_enforcement():
    """Verify that users missing specific permissions receive HTTP 403."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Missing expenses.view -> all read routes 403
            read_routes = [
                ("GET", "/api/v1/expenses"),
                ("GET", "/api/v1/expenses/date-range?start=2025-01-01&end=2025-01-31"),
                ("GET", f"/api/v1/expenses/{d['exp_a'].id}"),
                ("GET", f"/api/v1/expenses/project/{d['proj_a'].id}"),
                ("GET", "/api/v1/expenses/category/Civil"),
                ("GET", "/api/v1/expenses/payment-mode/cash"),
                ("GET", f"/api/v1/expenses/summary/{d['proj_a'].id}"),
                ("GET", f"/api/v1/expenses/boq-comparison/{d['proj_a'].id}"),
                ("GET", "/api/v1/expenses/dashboard"),
                ("GET", "/api/v1/expenses/project-allocations"),
                ("GET", "/api/v1/expenses/ledger"),
            ]
            for method, path in read_routes:
                res = await ac.request(method, path, headers={"Authorization": f"Bearer {d['token_noview']}"})
                assert res.status_code == 403, f"Missing expenses.view: {method} {path} returned {res.status_code}"

            # 2. Missing expenses.create -> POST /api/v1/expenses 403
            res = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_nocreate']}"},
                json={
                    "project_id": d["proj_a"].id,
                    "category": "Civil",
                    "description": "Unauthorized cement",
                    "amount": 1000.0,
                    "expense_date": str(date.today()),
                    "payment_mode": "cash",
                },
            )
            assert res.status_code == 403

            # 3. Missing expenses.edit -> PUT /api/v1/expenses/{id} 403
            res = await ac.put(
                f"/api/v1/expenses/{d['exp_a'].id}",
                headers={"Authorization": f"Bearer {d['token_noedit']}"},
                json={"description": "Unauthorized edit"},
            )
            assert res.status_code == 403

            # 4. Missing expenses.delete -> DELETE /api/v1/expenses/{id} 403
            res = await ac.delete(
                f"/api/v1/expenses/{d['exp_a'].id}",
                headers={"Authorization": f"Bearer {d['token_nodelete']}"},
            )
            assert res.status_code == 403

            # 5. Missing expenses.upload -> POST /api/v1/expenses/import 403
            csv_file = io.BytesIO(b"project_id,amount\n1,100\n")
            res = await ac.post(
                "/api/v1/expenses/import",
                headers={"Authorization": f"Bearer {d['token_noupload']}"},
                files={"file": ("test.csv", csv_file, "text/csv")},
            )
            assert res.status_code == 403

            # 6. Missing expenses.export -> GET /api/v1/expenses/export 403
            res = await ac.get(
                "/api/v1/expenses/export",
                headers={"Authorization": f"Bearer {d['token_noexport']}"},
            )
            assert res.status_code == 403


# ==============================================================================
# GROUP C: Tenantless Non-SA (company_id=None -> 403 Across All 16)
# ==============================================================================
@pytest.mark.asyncio
async def test_tenantless_non_sa_rejected():
    """Verify that a non-SA user with company_id=None receives HTTP 403 on all 16 routes."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("GET", "/api/v1/expenses"),
                ("POST", "/api/v1/expenses"),
                ("GET", "/api/v1/expenses/date-range?start=2025-01-01&end=2025-01-31"),
                ("GET", f"/api/v1/expenses/{d['exp_a'].id}"),
                ("PUT", f"/api/v1/expenses/{d['exp_a'].id}"),
                ("DELETE", f"/api/v1/expenses/{d['exp_a'].id}"),
                ("GET", f"/api/v1/expenses/project/{d['proj_a'].id}"),
                ("GET", "/api/v1/expenses/category/Civil"),
                ("GET", "/api/v1/expenses/payment-mode/cash"),
                ("GET", f"/api/v1/expenses/summary/{d['proj_a'].id}"),
                ("GET", f"/api/v1/expenses/boq-comparison/{d['proj_a'].id}"),
                ("GET", "/api/v1/expenses/dashboard"),
                ("GET", "/api/v1/expenses/project-allocations"),
                ("GET", "/api/v1/expenses/ledger"),
                ("POST", "/api/v1/expenses/import"),
                ("GET", "/api/v1/expenses/export"),
            ]
            for method, path in endpoints:
                if method == "POST" and "import" in path:
                    csv_file = io.BytesIO(b"project_id,amount\n1,100\n")
                    res = await ac.post(
                        path,
                        headers={"Authorization": f"Bearer {d['token_tl']}"},
                        files={"file": ("test.csv", csv_file, "text/csv")},
                    )
                elif method == "POST":
                    res = await ac.post(
                        path,
                        headers={"Authorization": f"Bearer {d['token_tl']}"},
                        json={
                            "project_id": d["proj_a"].id,
                            "category": "Civil",
                            "description": "Tenantless creation",
                            "amount": 1000.0,
                            "expense_date": str(date.today()),
                            "payment_mode": "cash",
                        },
                    )
                elif method == "PUT":
                    res = await ac.put(
                        path,
                        headers={"Authorization": f"Bearer {d['token_tl']}"},
                        json={"description": "Tenantless update"},
                    )
                else:
                    res = await ac.request(method, path, headers={"Authorization": f"Bearer {d['token_tl']}"})
                assert res.status_code == 403, f"Tenantless user got {res.status_code} on {method} {path}"


# ==============================================================================
# GROUP D: Valid Tenant Lifecycle
# ==============================================================================
@pytest.mark.asyncio
async def test_valid_tenant_lifecycle():
    """Verify full CRUD and analytics lifecycle for a valid tenant."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create Expense
            create_res = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={
                    "project_id": d["proj_a"].id,
                    "category": "Civil",
                    "description": "Steel Rods",
                    "amount": 3000.0,
                    "expense_date": str(date.today()),
                    "payment_mode": "cash",
                    "boq_item_id": d["boq_a"].id,
                },
            )
            assert create_res.status_code == 200, f"Create failed: {create_res.text}"
            exp_id = create_res.json()["id"]

            # 2. List Expenses
            list_res = await ac.get("/api/v1/expenses", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert list_res.status_code == 200
            ids = [item["id"] for item in list_res.json()]
            assert exp_id in ids

            # 3. Get Expense
            get_res = await ac.get(f"/api/v1/expenses/{exp_id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert get_res.status_code == 200
            assert get_res.json()["description"] == "Steel Rods"

            # 4. Update Expense
            upd_res = await ac.put(
                f"/api/v1/expenses/{exp_id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={"description": "Updated Steel Rods", "amount": 3500.0},
            )
            assert upd_res.status_code == 200
            assert upd_res.json()["description"] == "Updated Steel Rods"
            assert float(upd_res.json()["amount"]) == 3500.0

            # 5. Queries: project, category, payment-mode, date-range
            proj_res = await ac.get(f"/api/v1/expenses/project/{d['proj_a'].id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert proj_res.status_code == 200

            cat_res = await ac.get("/api/v1/expenses/category/Civil", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert cat_res.status_code == 200

            mode_res = await ac.get("/api/v1/expenses/payment-mode/cash", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert mode_res.status_code == 200

            dr_res = await ac.get(f"/api/v1/expenses/date-range?start={date.today()}&end={date.today()}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert dr_res.status_code == 200

            # 6. Analytics: summary, boq-comparison, dashboard, project-allocations, ledger
            sum_res = await ac.get(f"/api/v1/expenses/summary/{d['proj_a'].id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert sum_res.status_code == 200
            assert float(sum_res.json()["total_expense"]) > 0

            boq_res = await ac.get(f"/api/v1/expenses/boq-comparison/{d['proj_a'].id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert boq_res.status_code == 200

            dash_res = await ac.get("/api/v1/expenses/dashboard", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert dash_res.status_code == 200
            assert dash_res.json()["total_expense"] > 0

            alloc_res = await ac.get("/api/v1/expenses/project-allocations", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert alloc_res.status_code == 200

            ledger_res = await ac.get("/api/v1/expenses/ledger", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert ledger_res.status_code == 200

            # 7. Export & Import
            exp_export = await ac.get("/api/v1/expenses/export", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert exp_export.status_code == 200
            assert "text/csv" in exp_export.headers.get("content-type", "")

            csv_data = f"project_id,category,amount\n{d['proj_a'].id},Civil,500\n".encode("utf-8")
            imp_res = await ac.post(
                "/api/v1/expenses/import",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                files={"file": ("import.csv", io.BytesIO(csv_data), "text/csv")},
            )
            assert imp_res.status_code == 200
            assert imp_res.json()["valid_records"] == 1

            # 8. Delete Expense
            del_res = await ac.delete(f"/api/v1/expenses/{exp_id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert del_res.status_code == 204

            # Verify 404 after deletion
            get_after_del = await ac.get(f"/api/v1/expenses/{exp_id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert get_after_del.status_code == 404


# ==============================================================================
# GROUP E: Direct IDOR Protection (Cross-Tenant Access Masked with 404)
# ==============================================================================
@pytest.mark.asyncio
async def test_direct_idor_protection():
    """Verify that Tenant A attempting to access Tenant B's expense receives 404 indistinguishable from non-existent."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            foreign_id = d["exp_b"].id
            non_existent_id = 99999999

            # 1. GET foreign vs non-existent -> both 404
            res_foreign = await ac.get(f"/api/v1/expenses/{foreign_id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            res_nonexistent = await ac.get(f"/api/v1/expenses/{non_existent_id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert res_foreign.status_code == 404
            assert res_nonexistent.status_code == 404

            # 2. PUT foreign vs non-existent -> both 404
            res_put_foreign = await ac.put(
                f"/api/v1/expenses/{foreign_id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={"description": "Hacked Expense"},
            )
            res_put_nonexistent = await ac.put(
                f"/api/v1/expenses/{non_existent_id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={"description": "Hacked Expense"},
            )
            assert res_put_foreign.status_code == 404
            assert res_put_nonexistent.status_code == 404

            # 3. DELETE foreign vs non-existent -> both 404
            res_del_foreign = await ac.delete(
                f"/api/v1/expenses/{foreign_id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
            )
            res_del_nonexistent = await ac.delete(
                f"/api/v1/expenses/{non_existent_id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
            )
            assert res_del_foreign.status_code == 404
            assert res_del_nonexistent.status_code == 404

            # 4. Project-scoped queries on foreign project -> 404
            assert (await ac.get(f"/api/v1/expenses/project/{d['proj_b'].id}", headers={"Authorization": f"Bearer {d['token_a']}"})).status_code == 404
            assert (await ac.get(f"/api/v1/expenses/summary/{d['proj_b'].id}", headers={"Authorization": f"Bearer {d['token_a']}"})).status_code == 404
            assert (await ac.get(f"/api/v1/expenses/boq-comparison/{d['proj_b'].id}", headers={"Authorization": f"Bearer {d['token_a']}"})).status_code == 404


# ==============================================================================
# GROUP F: Foreign FK Injection Protection
# ==============================================================================
@pytest.mark.asyncio
async def test_foreign_fk_injection():
    """Verify that Tenant A cannot inject Tenant B's foreign keys (project, supplier, BOQ)."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Tenant A uses Tenant B project_id -> 404
            res = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={
                    "project_id": d["proj_b"].id,
                    "category": "Civil",
                    "description": "Foreign Project Injection",
                    "amount": 1000.0,
                    "expense_date": str(date.today()),
                    "payment_mode": "cash",
                },
            )
            assert res.status_code == 404

            # 2. Tenant A uses valid proj_a but foreign boq_item_id -> 404
            res = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={
                    "project_id": d["proj_a"].id,
                    "category": "Civil",
                    "description": "Foreign BOQ Injection",
                    "amount": 1000.0,
                    "expense_date": str(date.today()),
                    "payment_mode": "cash",
                    "boq_item_id": d["boq_b"].id,
                },
            )
            assert res.status_code == 404

            # 3. List expenses with foreign vendor_id -> 404
            res = await ac.get(
                f"/api/v1/expenses?vendor_id={d['supplier_b'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
            )
            assert res.status_code == 404

            # 4. List expenses with foreign project_id -> 404
            res = await ac.get(
                f"/api/v1/expenses?project_id={d['proj_b'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
            )
            assert res.status_code == 404


# ==============================================================================
# GROUP G: Update Project Injection Protection
# ==============================================================================
@pytest.mark.asyncio
async def test_update_project_injection():
    """Verify that updating an expense cannot reassign it to a foreign project or BOQ item."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Tenant A attempts to reassign expense to foreign project -> 404
            res = await ac.put(
                f"/api/v1/expenses/{d['exp_a'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={"project_id": d["proj_b"].id},
            )
            assert res.status_code == 404

            # 2. Tenant A attempts to attach foreign BOQ item -> 404
            res = await ac.put(
                f"/api/v1/expenses/{d['exp_a'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={"boq_item_id": d["boq_b"].id},
            )
            assert res.status_code == 404


# ==============================================================================
# GROUP H: Super Admin Semantics (Global vs Company-Filtered)
# ==============================================================================
@pytest.mark.asyncio
async def test_super_admin_operations():
    """Verify Super Admin platform-wide operations and scoped company_id filtering."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Global list: SA sees expenses from both Company A and Company B
            res_global = await ac.get("/api/v1/expenses", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert res_global.status_code == 200
            all_ids = [item["id"] for item in res_global.json()]
            assert d["exp_a"].id in all_ids
            assert d["exp_b"].id in all_ids

            # 2. Filtered list: SA + company_id=comp_a -> sees only Company A
            res_a = await ac.get(f"/api/v1/expenses?company_id={d['comp_a'].id}", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert res_a.status_code == 200
            a_ids = [item["id"] for item in res_a.json()]
            assert d["exp_a"].id in a_ids
            assert d["exp_b"].id not in a_ids

            # 3. Filtered list: SA + invalid company_id -> 404
            res_invalid = await ac.get("/api/v1/expenses?company_id=99999999", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert res_invalid.status_code == 404

            # 4. Global vs Filtered Dashboard
            dash_global = await ac.get("/api/v1/expenses/dashboard", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert dash_global.status_code == 200
            tot_global = dash_global.json()["total_expense"]

            dash_a = await ac.get(f"/api/v1/expenses/dashboard?company_id={d['comp_a'].id}", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert dash_a.status_code == 200
            tot_a = dash_a.json()["total_expense"]
            assert tot_a < tot_global

            dash_invalid = await ac.get("/api/v1/expenses/dashboard?company_id=99999999", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert dash_invalid.status_code == 404

            # 5. Filtered Project Allocations & Ledger
            alloc_a = await ac.get(f"/api/v1/expenses/project-allocations?company_id={d['comp_a'].id}", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert alloc_a.status_code == 200

            ledger_a = await ac.get(f"/api/v1/expenses/ledger?company_id={d['comp_a'].id}", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert ledger_a.status_code == 200

            # 6. SA Expense Creation on Company B Project
            sa_create = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_sa']}"},
                json={
                    "project_id": d["proj_b"].id,
                    "category": "Civil",
                    "description": "SA Created on Proj B",
                    "amount": 2500.0,
                    "expense_date": str(date.today()),
                    "payment_mode": "cash",
                },
            )
            assert sa_create.status_code == 200


# ==============================================================================
# GROUP I: Non-SA Company Override Prevention
# ==============================================================================
@pytest.mark.asyncio
async def test_non_sa_company_override_prevention():
    """Verify that a non-SA user supplying ?company_id=foreign is ignored and remains tenant scoped."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get(
                f"/api/v1/expenses?company_id={d['comp_b'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
            )
            assert res.status_code == 200
            ids = [item["id"] for item in res.json()]
            assert d["exp_a"].id in ids
            assert d["exp_b"].id not in ids

            # Export with foreign company_id
            exp_res = await ac.get(
                f"/api/v1/expenses/export?company_id={d['comp_b'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
            )
            assert exp_res.status_code == 200
            csv_text = exp_res.text
            assert d["proj_a"].project_name in csv_text
            assert d["proj_b"].project_name not in csv_text
            assert str(d["exp_a"].id) in csv_text
            assert str(d["exp_b"].id) not in csv_text


# ==============================================================================
# GROUP J: Export Security
# ==============================================================================
@pytest.mark.asyncio
async def test_export_security():
    """Verify CSV export respects exact tenant isolation and SA filtering."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Tenant A export
            res_a = await ac.get("/api/v1/expenses/export", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert res_a.status_code == 200
            text_a = res_a.text
            assert d["proj_a"].project_name in text_a
            assert d["proj_b"].project_name not in text_a
            assert str(d["exp_a"].id) in text_a
            assert str(d["exp_b"].id) not in text_a

            # SA global export contains both
            res_sa_global = await ac.get("/api/v1/expenses/export", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert res_sa_global.status_code == 200
            text_sa = res_sa_global.text
            assert d["proj_a"].project_name in text_sa
            assert d["proj_b"].project_name in text_sa
            assert str(d["exp_a"].id) in text_sa
            assert str(d["exp_b"].id) in text_sa

            # SA filtered export for comp_b
            res_sa_b = await ac.get(f"/api/v1/expenses/export?company_id={d['comp_b'].id}", headers={"Authorization": f"Bearer {d['token_sa']}"})
            assert res_sa_b.status_code == 200
            text_b = res_sa_b.text
            assert d["proj_b"].project_name in text_b
            assert d["proj_a"].project_name not in text_b
            assert str(d["exp_b"].id) in text_b
            assert str(d["exp_a"].id) not in text_b


# ==============================================================================
# GROUP K: Import Security
# ==============================================================================
@pytest.mark.asyncio
async def test_import_security():
    """Verify that import validates foreign references and rejects cross-tenant injection."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Non-CSV file rejected -> 400
            txt_file = io.BytesIO(b"hello world")
            res_bad_file = await ac.post(
                "/api/v1/expenses/import",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                files={"file": ("test.txt", txt_file, "text/plain")},
            )
            assert res_bad_file.status_code == 400

            # 2. Import containing foreign project reference -> rejected as error
            csv_foreign = f"project_id,category,amount\n{d['proj_b'].id},Civil,1000\n".encode("utf-8")
            res_imp_foreign = await ac.post(
                "/api/v1/expenses/import",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                files={"file": ("import.csv", io.BytesIO(csv_foreign), "text/csv")},
            )
            assert res_imp_foreign.status_code == 200
            assert res_imp_foreign.json()["valid_records"] == 0
            assert res_imp_foreign.json()["errors"] == 1

            # 3. Import containing valid project but foreign BOQ item -> rejected as error
            csv_foreign_boq = f"project_id,boq_item_id,category,amount\n{d['proj_a'].id},{d['boq_b'].id},Civil,1000\n".encode("utf-8")
            res_imp_boq = await ac.post(
                "/api/v1/expenses/import",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                files={"file": ("import.csv", io.BytesIO(csv_foreign_boq), "text/csv")},
            )
            assert res_imp_boq.status_code == 200
            assert res_imp_boq.json()["valid_records"] == 0
            assert res_imp_boq.json()["errors"] == 1

            # 4. Import containing valid project and valid BOQ -> success
            csv_valid = f"project_id,boq_item_id,category,amount\n{d['proj_a'].id},{d['boq_a'].id},Civil,1000\n".encode("utf-8")
            res_imp_valid = await ac.post(
                "/api/v1/expenses/import",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                files={"file": ("import.csv", io.BytesIO(csv_valid), "text/csv")},
            )
            assert res_imp_valid.status_code == 200
            assert res_imp_valid.json()["valid_records"] == 1
            assert res_imp_valid.json()["errors"] == 0


# ==============================================================================
# GROUP L: Financial Ledger Invariants
# ==============================================================================
@pytest.mark.asyncio
async def test_financial_ledger_invariants():
    """Verify double-entry accounting integrity: JournalEntry, balanced debit/credit lines, and OwnerTransaction."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exp_amount = Decimal("4250.00")
            create_res = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={
                    "project_id": d["proj_a"].id,
                    "category": "Civil",
                    "description": "Ledger Invariant Test Expense",
                    "amount": float(exp_amount),
                    "expense_date": str(date.today()),
                    "payment_mode": "cash",
                },
            )
            assert create_res.status_code == 200
            exp_id = create_res.json()["id"]

            async with AsyncSessionLocal() as db:
                # 1. Verify JournalEntry
                je = await db.scalar(
                    select(JournalEntry).where(JournalEntry.journal_number == f"J-EXP-{exp_id}")
                )
                assert je is not None
                assert je.entry_type == "Expense"
                assert je.status == "Posted"

                # 2. Verify JournalLines (balanced debit and credit)
                lines = (await db.scalars(
                    select(JournalLine).where(JournalLine.entry_id == je.id)
                )).all()
                assert len(lines) == 2

                dr_lines = [line for line in lines if line.debit > 0]
                cr_lines = [line for line in lines if line.credit > 0]
                assert len(dr_lines) == 1
                assert len(cr_lines) == 1
                assert dr_lines[0].debit == exp_amount
                assert cr_lines[0].credit == exp_amount

                # Verify accounts belong to Company A
                dr_acc = await db.get(Account, dr_lines[0].account_id)
                cr_acc = await db.get(Account, cr_lines[0].account_id)
                assert dr_acc.company_id == d["comp_a"].id
                assert cr_acc.company_id == d["comp_a"].id
                assert dr_acc.code == "GENERAL_EXPENSE"

                # 3. Verify OwnerTransaction
                ot = await db.scalar(
                    select(OwnerTransaction).where(
                        OwnerTransaction.reference_type == "expense",
                        OwnerTransaction.reference_id == exp_id,
                    )
                )
                assert ot is not None
                assert ot.project_id == d["proj_a"].id
                assert ot.amount == exp_amount
                assert ot.type == "debit"


# ==============================================================================
# GROUP M: Idempotency
# ==============================================================================
@pytest.mark.asyncio
async def test_idempotency_behavior():
    """Verify Idempotency-Key guarantees duplicate prevention and payload consistency."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with setup_expense_test_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            idemp_key = f"idemp_{uuid.uuid4().hex}"
            payload = {
                "project_id": d["proj_a"].id,
                "category": "Civil",
                "description": "Idempotent Expense",
                "amount": 1200.0,
                "expense_date": str(date.today()),
                "payment_mode": "cash",
            }

            # First call -> 200 created
            res1 = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_a']}", "Idempotency-Key": idemp_key},
                json=payload,
            )
            assert res1.status_code == 200
            first_id = res1.json()["id"]

            # Second call with identical payload and key -> returns original expense
            res2 = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_a']}", "Idempotency-Key": idemp_key},
                json=payload,
            )
            assert res2.status_code == 200
            assert res2.json()["id"] == first_id

            # Third call with same key but different payload -> 409 Conflict
            diff_payload = dict(payload, amount=9999.0)
            res3 = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_a']}", "Idempotency-Key": idemp_key},
                json=diff_payload,
            )
            assert res3.status_code == 409


# ==============================================================================
# GROUP N: Route Preservation (16 Routes, 16 Unique, 0 Duplicates, 781 Total)
# ==============================================================================
def test_route_preservation():
    """Verify exact route counts and preservation of total application endpoints."""
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    exp_routes = [r for r in routes if r.path.startswith("/api/v1/expenses")]

    assert len(exp_routes) == 16, f"Expected 16 expense routes, got {len(exp_routes)}"
    unique_exp = set((list(r.methods)[0], r.path) for r in exp_routes)
    assert len(unique_exp) == 16, f"Expected 16 unique expense routes, got {len(unique_exp)}"
    assert len(routes) == 781, f"Expected 781 total routes, got {len(routes)}"


# ==============================================================================
# GROUP O: Static Security Hygiene
# ==============================================================================
def test_static_security_hygiene():
    """Verify zero UserRole, require_roles, admin_required, or hardcoded role allowlists."""
    source = inspect.getsource(expense_api_module)
    assert "require_roles" not in source, "Found require_roles in expense.py"
    assert "admin_required" not in source, "Found admin_required in expense.py"
    assert "UserRole" not in source, "Found UserRole in expense.py"
    assert "EXPENSE_READ_ROLES" not in source, "Found EXPENSE_READ_ROLES in expense.py"
    assert "EXPENSE_WRITE_ROLES" not in source, "Found EXPENSE_WRITE_ROLES in expense.py"
