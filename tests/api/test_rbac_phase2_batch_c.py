import uuid
from decimal import Decimal
from datetime import date, datetime, timezone
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.core.db import async_engine
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.expense import Expense
from app.models.billing import RABill
from app.models.settings import CompanySettings, UserSettings
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token


@asynccontextmanager
async def setup_batch_c_data():
    """Seed test companies, users, projects, expenses, and RA bills for Batch C test suite."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Create two test companies
        comp_a = Company(name=f"BatchC_CompA_{uid}")
        comp_b = Company(name=f"BatchC_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Create company settings
        cs_a = CompanySettings(company_id=comp_a.id)
        cs_b = CompanySettings(company_id=comp_b.id)
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Create owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-CA-{uid}",
            owner_name=f"Owner CA {uid}",
            email=f"ownera_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-CB-{uid}",
            owner_name=f"Owner CB {uid}",
            email=f"ownerb_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 4. Create projects
        proj_a = Project(
            business_id=f"PRJ-CA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_A_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-CB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_B_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 5. Create Expenses in Proj A and Proj B
        today = date.today()
        exp_a = Expense(
            project_id=proj_a.id,
            category="Materials",
            description=f"Comp A Cement {uid}",
            amount=Decimal("5000.00"),
            expense_date=today,
            payment_mode="Bank Transfer",
        )
        exp_b = Expense(
            project_id=proj_b.id,
            category="Materials",
            description=f"Comp B Steel {uid}",
            amount=Decimal("12000.00"),
            expense_date=today,
            payment_mode="Bank Transfer",
        )
        db.add_all([exp_a, exp_b])
        await db.flush()

        # 6. Create RA Bills in Proj A and Proj B
        rabill_a = RABill(
            project_id=proj_a.id,
            bill_number=f"RAB-A-{uid}",
            work_description="Excavation Work A",
            quantity=Decimal("100.000"),
            rate=Decimal("50.00"),
            gross_amount=Decimal("5000.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("5000.00"),
            gst_percent=Decimal("18.00"),
            total_amount=Decimal("5900.00"),
            bill_date=today,
            status="Draft",
        )
        rabill_b = RABill(
            project_id=proj_b.id,
            bill_number=f"RAB-B-{uid}",
            work_description="Excavation Work B",
            quantity=Decimal("200.000"),
            rate=Decimal("50.00"),
            gross_amount=Decimal("10000.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("10000.00"),
            gst_percent=Decimal("18.00"),
            total_amount=Decimal("11800.00"),
            bill_date=today,
            status="Draft",
        )
        db.add_all([rabill_a, rabill_b])
        await db.flush()

        # 7. Create custom roles in Company A
        role_name_expense = f"CostController_{uid}"
        role_name_billing = f"BillingSpecialist_{uid}"
        role_name_noperm = f"UnprivRole_{uid}"

        role_exp_a = Role(company_id=comp_a.id, name=role_name_expense, display_name="Cost Controller", is_system=False)
        role_bill_a = Role(company_id=comp_a.id, name=role_name_billing, display_name="Billing Specialist", is_system=False)
        role_noperm_a = Role(company_id=comp_a.id, name=role_name_noperm, display_name="Unprivileged", is_system=False)
        db.add_all([role_exp_a, role_bill_a, role_noperm_a])
        await db.flush()

        # 8. Create users
        user_noperm = User(
            email=f"c_noperm_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Unprivileged User C",
            company_id=comp_a.id,
            role=role_name_noperm,
            is_active=True,
            is_super_admin=False,
        )
        user_expense = User(
            email=f"c_expense_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Expense Officer C",
            company_id=comp_a.id,
            role=role_name_expense,
            is_active=True,
            is_super_admin=False,
        )
        user_billing = User(
            email=f"c_billing_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Billing Officer C",
            company_id=comp_a.id,
            role=role_name_billing,
            is_active=True,
            is_super_admin=False,
        )
        user_admin_a = User(
            email=f"c_admin_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Company A Admin",
            company_id=comp_a.id,
            role="Admin",
            is_active=True,
            is_super_admin=False,
        )
        user_comp_b = User(
            email=f"c_compb_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Comp B User",
            company_id=comp_b.id,
            role=role_name_expense,
            is_active=True,
            is_super_admin=False,
        )
        user_super = User(
            email=f"c_super_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Super Admin C",
            company_id=None,
            role="Admin",
            is_active=True,
            is_super_admin=True,
        )
        # Legacy named role without permissions in DB
        user_legacy_role = User(
            email=f"c_legacy_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Legacy Site Engineer",
            company_id=comp_a.id,
            role="Site Engineer",
            is_active=True,
            is_super_admin=False,
        )

        all_users = [user_noperm, user_expense, user_billing, user_admin_a, user_comp_b, user_super, user_legacy_role]
        db.add_all(all_users)
        await db.flush()

        # Project members
        pm_exp = ProjectMember(project_id=proj_a.id, user_id=user_expense.id)
        pm_bill = ProjectMember(project_id=proj_a.id, user_id=user_billing.id)
        pm_noperm = ProjectMember(project_id=proj_a.id, user_id=user_noperm.id)
        db.add_all([pm_exp, pm_bill, pm_noperm])
        await db.flush()

        # 9. Fetch permissions from DB
        p_exp_view = await db.scalar(select(Permission).where(Permission.code == "expenses.view"))
        p_exp_create = await db.scalar(select(Permission).where(Permission.code == "expenses.create"))
        p_exp_edit = await db.scalar(select(Permission).where(Permission.code == "expenses.edit"))
        p_exp_delete = await db.scalar(select(Permission).where(Permission.code == "expenses.delete"))
        p_exp_export = await db.scalar(select(Permission).where(Permission.code == "expenses.export"))
        p_exp_upload = await db.scalar(select(Permission).where(Permission.code == "expenses.upload"))

        p_bill_view = await db.scalar(select(Permission).where(Permission.code == "billing.view"))
        p_bill_create = await db.scalar(select(Permission).where(Permission.code == "billing.create"))
        p_bill_edit = await db.scalar(select(Permission).where(Permission.code == "billing.edit"))
        p_bill_delete = await db.scalar(select(Permission).where(Permission.code == "billing.delete"))
        p_bill_approve = await db.scalar(select(Permission).where(Permission.code == "billing.approve"))

        # 10. Assign permissions to role_exp_a
        for p in [p_exp_view, p_exp_create, p_exp_edit, p_exp_delete, p_exp_export, p_exp_upload]:
            db.add(RolePermission(role=role_name_expense, role_id=role_exp_a.id, permission_id=p.id))

        # 11. Assign permissions to role_bill_a
        for p in [p_bill_view, p_bill_create, p_bill_edit, p_bill_delete, p_bill_approve]:
            db.add(RolePermission(role=role_name_billing, role_id=role_bill_a.id, permission_id=p.id))

        await db.commit()

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "exp_a": exp_a,
            "exp_b": exp_b,
            "rabill_a": rabill_a,
            "rabill_b": rabill_b,
            "user_noperm": user_noperm,
            "user_expense": user_expense,
            "user_billing": user_billing,
            "user_admin_a": user_admin_a,
            "user_comp_b": user_comp_b,
            "user_super": user_super,
            "user_legacy_role": user_legacy_role,
            "role_exp_a": role_exp_a,
            "role_bill_a": role_bill_a,
            "role_noperm_a": role_noperm_a,
            "role_name_expense": role_name_expense,
            "role_name_billing": role_name_billing,
            "role_name_noperm": role_name_noperm,
            "perms": {
                "expenses.view": p_exp_view,
                "expenses.create": p_exp_create,
                "expenses.edit": p_exp_edit,
                "expenses.delete": p_exp_delete,
                "expenses.export": p_exp_export,
                "expenses.upload": p_exp_upload,
                "billing.view": p_bill_view,
                "billing.create": p_bill_create,
                "billing.edit": p_bill_edit,
                "billing.delete": p_bill_delete,
                "billing.approve": p_bill_approve,
            },
        }

    try:
        yield data
    finally:
        # Cleanup in reverse order
        async with AsyncSessionLocal() as db:
            all_user_ids = [u.id for u in all_users]
            await db.execute(delete(RolePermission).where(
                RolePermission.role.in_([role_name_expense, role_name_billing, role_name_noperm])
            ))
            await db.execute(delete(UserPermissionOverride).where(
                UserPermissionOverride.user_id.in_(all_user_ids)
            ))
            await db.execute(delete(Role).where(
                Role.id.in_([role_exp_a.id, role_bill_a.id, role_noperm_a.id])
            ))
            await db.execute(delete(RABill).where(RABill.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(Expense).where(Expense.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(UserSettings).where(UserSettings.user_id.in_(all_user_ids)))
            await db.execute(delete(User).where(User.id.in_(all_user_ids)))
            await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()
        await async_engine.dispose()


def get_auth_headers(user: User):
    token = create_access_token(data={"sub": str(user.id), "role": user.role, "company_id": user.company_id})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. Unauthenticated Requests Return 401
# ==============================================================================
@pytest.mark.asyncio
async def test_unauthenticated_requests_fail():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("GET", "/api/v1/expenses"),
            ("POST", "/api/v1/expenses"),
            ("GET", "/api/v1/expenses/export"),
            ("GET", "/api/v1/billing"),
            ("POST", "/api/v1/billing"),
        ]
        for method, path in endpoints:
            if method == "GET":
                res = await ac.get(path)
            else:
                res = await ac.post(path, json={})
            assert res.status_code == 401, f"{method} {path} should return 401 without token"


# ==============================================================================
# 2. Missing Permission Returns 403
# ==============================================================================
@pytest.mark.asyncio
async def test_missing_permission_forbidden():
    async with setup_batch_c_data() as data:
        headers = get_auth_headers(data["user_noperm"])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_exp = await ac.get("/api/v1/expenses", headers=headers)
            assert res_exp.status_code == 403

            res_export = await ac.get("/api/v1/expenses/export", headers=headers)
            assert res_export.status_code == 403

            res_bill = await ac.get("/api/v1/billing", headers=headers)
            assert res_bill.status_code == 403


# ==============================================================================
# 3. Custom Role Authorization & Runtime Lifecycle (Grant -> 200 -> Revoke -> 403 -> Regrant -> 200)
# ==============================================================================
@pytest.mark.asyncio
async def test_custom_expense_role_runtime_lifecycle():
    async with setup_batch_c_data() as data:
        user = data["user_expense"]
        headers = get_auth_headers(user)
        role_name = data["role_name_expense"]
        p_view = data["perms"]["expenses.view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Initially granted -> 200
            res = await ac.get("/api/v1/expenses", headers=headers)
            assert res.status_code == 200

            # 2. Revoke permission in DB without restart
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == role_name,
                    RolePermission.permission_id == p_view.id,
                ))
                await db.commit()

            # Immediate request without restart -> 403
            res_revoked = await ac.get("/api/v1/expenses", headers=headers)
            assert res_revoked.status_code == 403

            # 3. Regrant permission in DB without restart
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=data["role_exp_a"].id, permission_id=p_view.id))
                await db.commit()

            # Immediate request without restart -> 200
            res_regranted = await ac.get("/api/v1/expenses", headers=headers)
            assert res_regranted.status_code == 200


# ==============================================================================
# 4. Custom Billing Role Runtime Lifecycle
# ==============================================================================
@pytest.mark.asyncio
async def test_custom_billing_role_runtime_lifecycle():
    async with setup_batch_c_data() as data:
        user = data["user_billing"]
        headers = get_auth_headers(user)
        role_name = data["role_name_billing"]
        p_view = data["perms"]["billing.view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially granted -> 200
            res = await ac.get("/api/v1/billing", headers=headers)
            assert res.status_code == 200

            # Revoke in DB
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == role_name,
                    RolePermission.permission_id == p_view.id,
                ))
                await db.commit()

            res_revoked = await ac.get("/api/v1/billing", headers=headers)
            assert res_revoked.status_code == 403

            # Regrant in DB
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=data["role_bill_a"].id, permission_id=p_view.id))
                await db.commit()

            res_regranted = await ac.get("/api/v1/billing", headers=headers)
            assert res_regranted.status_code == 200


# ==============================================================================
# 5. User Permission Overrides (Positive Grant & Negative Revoke)
# ==============================================================================
@pytest.mark.asyncio
async def test_user_permission_overrides():
    async with setup_batch_c_data() as data:
        user_noperm = data["user_noperm"]
        user_expense = data["user_expense"]
        p_view = data["perms"]["expenses.view"]
        headers_noperm = get_auth_headers(user_noperm)
        headers_exp = get_auth_headers(user_expense)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Verify user_noperm is 403
            assert (await ac.get("/api/v1/expenses", headers=headers_noperm)).status_code == 403

            # Positive user override for user_noperm
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user_noperm.id, permission_id=p_view.id, is_granted=True))
                await db.commit()

            assert (await ac.get("/api/v1/expenses", headers=headers_noperm)).status_code == 200

            # Negative user override for user_expense (who already has role permission)
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user_expense.id, permission_id=p_view.id, is_granted=False))
                await db.commit()

            assert (await ac.get("/api/v1/expenses", headers=headers_exp)).status_code == 403


# ==============================================================================
# 6. Wildcard Permission & Explicit Negative Override
# ==============================================================================
@pytest.mark.asyncio
async def test_wildcard_and_negative_override():
    async with setup_batch_c_data() as data:
        user = data["user_expense"]
        role_id = data["role_exp_a"].id
        role_name = data["role_name_expense"]
        p_view = data["perms"]["expenses.view"]
        headers = get_auth_headers(user)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Assign all module permissions to role
            async with AsyncSessionLocal() as db:
                all_exp_perms = (await db.scalars(select(Permission).where(Permission.module == "expenses"))).all()
                for p in all_exp_perms:
                    db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            assert (await ac.get("/api/v1/expenses", headers=headers)).status_code == 200

            # Negative override denies access even with role-level grant
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user.id, permission_id=p_view.id, is_granted=False))
                await db.commit()

            assert (await ac.get("/api/v1/expenses", headers=headers)).status_code == 403


# ==============================================================================
# 7. Tenant Isolation — Company A Cannot Read Company B Expenses
# ==============================================================================
@pytest.mark.asyncio
async def test_expense_tenant_isolation_cross_company():
    async with setup_batch_c_data() as data:
        headers_a = get_auth_headers(data["user_expense"])
        exp_b = data["exp_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. List expenses in Comp A does NOT include Comp B expense
            res_list = await ac.get("/api/v1/expenses", headers=headers_a)
            assert res_list.status_code == 200
            items = res_list.json()
            returned_ids = [item["id"] for item in items]
            assert exp_b.id not in returned_ids

            # 2. Direct lookup of Comp B expense by ID returns 404
            res_get = await ac.get(f"/api/v1/expenses/{exp_b.id}", headers=headers_a)
            assert res_get.status_code == 404

            # 3. Category search does NOT return Comp B records
            res_cat = await ac.get("/api/v1/expenses/category/Materials", headers=headers_a)
            assert res_cat.status_code == 200
            cat_ids = [item["id"] for item in res_cat.json()]
            assert exp_b.id not in cat_ids

            # 4. Date range query does NOT return Comp B records
            today_str = date.today().isoformat()
            res_date = await ac.get(f"/api/v1/expenses/date-range?start={today_str}&end={today_str}", headers=headers_a)
            assert res_date.status_code == 200
            date_ids = [item["id"] for item in res_date.json()]
            assert exp_b.id not in date_ids


# ==============================================================================
# 8. Cross-Tenant IDOR Remediation on Write and Delete
# ==============================================================================
@pytest.mark.asyncio
async def test_expense_idor_remediation_on_write_delete():
    async with setup_batch_c_data() as data:
        headers_a = get_auth_headers(data["user_expense"])
        exp_b = data["exp_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Update Comp B expense -> 404
            res_put = await ac.put(
                f"/api/v1/expenses/{exp_b.id}",
                headers=headers_a,
                json={"amount": "99999.00", "description": "Hacked Amount"},
            )
            assert res_put.status_code == 404

            # 2. Delete Comp B expense -> 404
            res_del = await ac.delete(f"/api/v1/expenses/{exp_b.id}", headers=headers_a)
            assert res_del.status_code == 404


# ==============================================================================
# 9. Cross-Tenant Project Expense Injection Blocked
# ==============================================================================
@pytest.mark.asyncio
async def test_expense_cross_tenant_injection_prevention():
    async with setup_batch_c_data() as data:
        headers_a = get_auth_headers(data["user_expense"])
        proj_b = data["proj_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_post = await ac.post(
                "/api/v1/expenses",
                headers=headers_a,
                json={
                    "project_id": proj_b.id,
                    "category": "Labour",
                    "description": "Cross-tenant injected claim",
                    "amount": "8500.00",
                    "expense_date": date.today().isoformat(),
                    "payment_mode": "Cash",
                },
            )
            assert res_post.status_code == 404


# ==============================================================================
# 10. Project-Specific Queries Enforce Tenant Boundary
# ==============================================================================
@pytest.mark.asyncio
async def test_project_specific_expense_idor_remediation():
    async with setup_batch_c_data() as data:
        headers_a = get_auth_headers(data["user_expense"])
        proj_b = data["proj_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. /expenses/project/{proj_b.id} -> 404
            res_proj = await ac.get(f"/api/v1/expenses/project/{proj_b.id}", headers=headers_a)
            assert res_proj.status_code == 404

            # 2. /expenses/summary/{proj_b.id} -> 404
            res_sum = await ac.get(f"/api/v1/expenses/summary/{proj_b.id}", headers=headers_a)
            assert res_sum.status_code == 404

            # 3. /expenses/boq-comparison/{proj_b.id} -> 404
            res_boq = await ac.get(f"/api/v1/expenses/boq-comparison/{proj_b.id}", headers=headers_a)
            assert res_boq.status_code == 404


# ==============================================================================
# 11. Dashboard Aggregates & Allocations Scoped to Tenant
# ==============================================================================
@pytest.mark.asyncio
async def test_expense_dashboard_and_allocations_tenant_scoping():
    async with setup_batch_c_data() as data:
        headers_a = get_auth_headers(data["user_expense"])
        exp_a = data["exp_a"]
        exp_b = data["exp_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Dashboard aggregate should ONLY sum Comp A expenses
            res_dash = await ac.get("/api/v1/expenses/dashboard", headers=headers_a)
            assert res_dash.status_code == 200
            dash_data = res_dash.json()
            # Comp A expense is 5000, Comp B is 12000. Total must be 5000, not 17000!
            assert dash_data["total_expense"] == float(exp_a.amount)

            # 2. Project allocations should only show Comp A projects
            res_alloc = await ac.get("/api/v1/expenses/project-allocations", headers=headers_a)
            assert res_alloc.status_code == 200
            alloc_data = res_alloc.json()
            project_names = [p["project_name"] for p in alloc_data["projects"]]
            assert data["proj_a"].project_name in project_names
            assert data["proj_b"].project_name not in project_names


# ==============================================================================
# 12. Expenses Export Authentication & Tenant Scoping
# ==============================================================================
@pytest.mark.asyncio
async def test_expenses_export_security_and_tenant_scoping():
    async with setup_batch_c_data() as data:
        headers_a = get_auth_headers(data["user_expense"])
        headers_noperm = get_auth_headers(data["user_noperm"])
        proj_a = data["proj_a"]
        proj_b = data["proj_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Unauthenticated -> 401
            res_unauth = await ac.get("/api/v1/expenses/export")
            assert res_unauth.status_code == 401

            # 2. Authenticated without permission -> 403
            res_noperm = await ac.get("/api/v1/expenses/export", headers=headers_noperm)
            assert res_noperm.status_code == 403

            # 3. Authenticated with permission -> 200 CSV with only Comp A data
            res_auth = await ac.get("/api/v1/expenses/export", headers=headers_a)
            assert res_auth.status_code == 200
            assert "text/csv" in res_auth.headers.get("content-type", "")
            csv_text = res_auth.text
            assert proj_a.project_name in csv_text
            assert proj_b.project_name not in csv_text


# ==============================================================================
# 13. Construction RA Billing Tenant Isolation & Project Access
# ==============================================================================
@pytest.mark.asyncio
async def test_billing_ra_bills_tenant_isolation():
    async with setup_batch_c_data() as data:
        headers_a = get_auth_headers(data["user_billing"])
        rabill_a = data["rabill_a"]
        rabill_b = data["rabill_b"]
        proj_b = data["proj_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. List RA bills returns Comp A bill, never Comp B
            res_list = await ac.get("/api/v1/billing", headers=headers_a)
            assert res_list.status_code == 200
            items = res_list.json()["items"]
            returned_ids = [item["id"] for item in items]
            assert rabill_a.id in returned_ids
            assert rabill_b.id not in returned_ids

            # 2. Get Comp B RA bill -> 403 (assert_project_access denies)
            res_get = await ac.get(f"/api/v1/billing/{rabill_b.id}", headers=headers_a)
            assert res_get.status_code in [403, 404]

            # 3. Update Comp B RA bill -> 403 (assert_project_access denies)
            res_put = await ac.put(f"/api/v1/billing/{rabill_b.id}", headers=headers_a, json={"work_description": "Hacked"})
            assert res_put.status_code in [403, 404]

            # 4. Delete Comp B RA bill -> 403 (assert_project_access denies)
            res_del = await ac.delete(f"/api/v1/billing/{rabill_b.id}", headers=headers_a)
            assert res_del.status_code in [403, 404]

            # 5. Create RA bill in Comp B project -> 403 (assert_project_access denies)
            res_post = await ac.post(
                "/api/v1/billing",
                headers=headers_a,
                json={
                    "project_id": proj_b.id,
                    "bill_number": f"INJECTED-{uuid.uuid4().hex[:6]}",
                    "work_description": "Injected RA Bill",
                    "quantity": "50.000",
                    "rate": "100.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res_post.status_code in [403, 404]


# ==============================================================================
# 14. Tenant Admin Bypass & Super Admin Behavior
# ==============================================================================
@pytest.mark.asyncio
async def test_tenant_admin_and_super_admin_behavior():
    async with setup_batch_c_data() as data:
        headers_admin = get_auth_headers(data["user_admin_a"])
        exp_b = data["exp_b"]
        rabill_b = data["rabill_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Tenant Admin has access to Comp A endpoints
            res_exp = await ac.get("/api/v1/expenses", headers=headers_admin)
            assert res_exp.status_code == 200

            res_bill = await ac.get("/api/v1/billing", headers=headers_admin)
            assert res_bill.status_code == 200

            # 2. Tenant Admin CANNOT access Comp B expenses or RA bills
            res_get_exp = await ac.get(f"/api/v1/expenses/{exp_b.id}", headers=headers_admin)
            assert res_get_exp.status_code == 404

            res_get_bill = await ac.get(f"/api/v1/billing/{rabill_b.id}", headers=headers_admin)
            assert res_get_bill.status_code in [403, 404]


# ==============================================================================
# 15. Legacy Role-Only User Denied Without DB Permission
# ==============================================================================
@pytest.mark.asyncio
async def test_legacy_role_only_user_denied_without_db_permission():
    async with setup_batch_c_data() as data:
        # User has role="Site Engineer" but ZERO entries in role_permissions table
        user_legacy = data["user_legacy_role"]
        headers = get_auth_headers(user_legacy)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Under data-driven RBAC, having role="Site Engineer" does not grant access
            # without explicit DB permission records
            res_exp = await ac.get("/api/v1/expenses", headers=headers)
            assert res_exp.status_code == 403

            res_bill = await ac.get("/api/v1/billing", headers=headers)
            assert res_bill.status_code == 403
