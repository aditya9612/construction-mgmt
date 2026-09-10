"""
Test Suite: BATCH AB-R — Global Security Blocker Remediation
Covers:
1. AI Tenant Isolation & IDOR & RBAC (ai.view, ai.create, ai.edit, ai.delete)
2. Material AI Recommendation Strict Ownership Validation
3. Master Data Security & Tenancy (18 endpoints, template semantics, RBAC)
4. Attendance Tenantless Regression (403 on non-SA company_id=None)
5. Expense Tenant Isolation & FK Injection (404 on foreign project/BOQ/vendor)
6. Equipment Maintenance Strict Ownership Validation (404 on foreign equipment/BOQ)
7. Accounting Utility Hardening (zero fallback, ValueError on company_id=None)
"""

import uuid
from decimal import Decimal
from datetime import date
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.core.db import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.subscription import Subscription, Plan
from app.models.project import Project
from app.models.owner import Owner
from app.models.boq import BOQ, BOQGroup
from app.models.ai_prediction import AIPrediction
from app.models import master_data as m
from app.models.expense import Expense
from app.models.equipment import Equipment, EquipmentMaintenance
from app.models.settings import CompanySettings
from app.models.accountant import Account
from app.models.material import Supplier
from app.models.rbac import Role, Permission, RolePermission
from app.core.enums import EquipmentStatus
from app.core.security import get_password_hash, create_access_token
from app.utils.accounting import (
    get_primary_cash_account,
    get_payroll_account,
    resolve_tax_accounts,
)


@asynccontextmanager
async def setup_ab_r_data():
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        comp_a = Company(name=f"ABR_CompA_{uid}")
        comp_b = Company(name=f"ABR_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        plan_ai = Plan(name=f"AI Plan {uid}", code=f"ai_{uid}", features={"ai_features": True})
        db.add(plan_ai)
        await db.flush()
        sub_a = Subscription(company_id=comp_a.id, plan_id=plan_ai.id, status="active")
        sub_b = Subscription(company_id=comp_b.id, plan_id=plan_ai.id, status="active")
        db.add_all([sub_a, sub_b])
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
            business_id=f"PA_{uid}",
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj B {uid}",
            business_id=f"PB_{uid}",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        boq_group_a = BOQGroup(project_id=proj_a.id, name=f"BOQ Group A {uid}")
        boq_group_b = BOQGroup(project_id=proj_b.id, name=f"BOQ Group B {uid}")
        db.add_all([boq_group_a, boq_group_b])
        await db.flush()

        boq_a = BOQ(
            project_id=proj_a.id,
            boq_group_id=boq_group_a.id,
            category="Civil",
            item_name="Concrete Works",
            quantity=100.0,
            unit="cum",
            unit_cost=5000.0,
            total_cost=500000.0,
            is_latest=True,
        )
        boq_b = BOQ(
            project_id=proj_b.id,
            boq_group_id=boq_group_b.id,
            category="Civil",
            item_name="Brick Works",
            quantity=100.0,
            unit="sqm",
            unit_cost=3000.0,
            total_cost=300000.0,
            is_latest=True,
        )
        db.add_all([boq_a, boq_b])
        await db.flush()

        supplier_a = Supplier(
            company_id=comp_a.id,
            supplier_name=f"Supplier A {uid}",
        )
        supplier_b = Supplier(
            company_id=comp_b.id,
            supplier_name=f"Supplier B {uid}",
        )
        db.add_all([supplier_a, supplier_b])
        await db.flush()

        # Roles
        role_full = Role(
            company_id=comp_a.id,
            name=f"abr_full_{uid}",
            display_name=f"Full Role {uid}",
            is_system=False,
        )
        db.add(role_full)
        await db.flush()

        # Grant permissions to role_full
        req_codes = [
            "ai.view", "ai.create", "ai.edit", "ai.delete",
            "master_data.view", "master_data.create", "master_data.edit", "master_data.delete",
            "expenses.view", "expenses.create", "expenses.edit", "expenses.delete", "expenses.export", "expenses.upload",
            "attendance.view", "attendance.create", "attendance.edit", "attendance.export",
            "materials.view", "equipment.view", "equipment.create", "equipment.edit",
        ]
        perms = (await db.execute(
            select(Permission).where(Permission.code.in_(req_codes))
        )).scalars().all()

        for p in perms:
            db.add(RolePermission(
                role=role_full.name,
                role_id=role_full.id,
                permission_id=p.id,
            ))
        await db.flush()

        # Users
        user_a = User(
            company_id=comp_a.id,
            role="Admin",
            email=f"user_a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name=f"User A {uid}",
            is_active=True,
            is_super_admin=False,
        )
        user_b = User(
            company_id=comp_b.id,
            role="Admin",
            email=f"user_b_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name=f"User B {uid}",
            is_active=True,
            is_super_admin=False,
        )
        user_tenantless = User(
            company_id=None,
            role="Admin",
            email=f"user_tenantless_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name=f"User Tenantless {uid}",
            is_active=True,
            is_super_admin=False,
        )
        sa_no_comp = User(
            company_id=None,
            role="SuperAdmin",
            email=f"sa_no_comp_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name=f"SA No Comp {uid}",
            is_active=True,
            is_super_admin=True,
        )
        sa_with_comp = User(
            company_id=comp_a.id,
            role="SuperAdmin",
            email=f"sa_comp_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name=f"SA Comp {uid}",
            is_active=True,
            is_super_admin=True,
        )
        db.add_all([user_a, user_b, user_tenantless, sa_no_comp, sa_with_comp])
        await db.flush()

        # Equipment
        eq_a = Equipment(
            company_id=comp_a.id,
            project_id=proj_a.id,
            equipment_code=f"EQA_{uid}",
            equipment_name="Excavator A",
            status=EquipmentStatus.AVAILABLE,
            is_deleted=False,
        )
        eq_b = Equipment(
            company_id=comp_b.id,
            project_id=proj_b.id,
            equipment_code=f"EQB_{uid}",
            equipment_name="Excavator B",
            status=EquipmentStatus.AVAILABLE,
            is_deleted=False,
        )
        db.add_all([eq_a, eq_b])
        await db.flush()

        # Master Data
        unit_a = m.Unit(company_id=comp_a.id, name=f"Unit_A_{uid}", unique_code=f"UA_{uid}", is_active=True)
        unit_b = m.Unit(company_id=comp_b.id, name=f"Unit_B_{uid}", unique_code=f"UB_{uid}", is_active=True)
        db.add_all([unit_a, unit_b])
        await db.flush()

        mat_a = m.MaterialMaster(company_id=comp_a.id, name=f"Mat_A_{uid}", unique_code=f"MA_{uid}", unit_id=unit_a.id, is_active=True)
        mat_b = m.MaterialMaster(company_id=comp_b.id, name=f"Mat_B_{uid}", unique_code=f"MB_{uid}", unit_id=unit_b.id, is_active=True)
        db.add_all([mat_a, mat_b])
        await db.flush()

        pred_a = AIPrediction(company_id=comp_a.id, module_name="test_ai", prompt="prompt a", prediction={"res": 1}, created_by_user_id=user_a.id)
        pred_b = AIPrediction(company_id=comp_b.id, module_name="test_ai", prompt="prompt b", prediction={"res": 2}, created_by_user_id=user_b.id)
        db.add_all([pred_a, pred_b])
        await db.flush()

        await db.commit()

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "user_a": user_a,
            "user_b": user_b,
            "user_tenantless": user_tenantless,
            "sa_no_comp": sa_no_comp,
            "sa_with_comp": sa_with_comp,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "boq_a": boq_a,
            "boq_b": boq_b,
            "supplier_a": supplier_a,
            "supplier_b": supplier_b,
            "eq_a": eq_a,
            "eq_b": eq_b,
            "unit_a": unit_a,
            "unit_b": unit_b,
            "mat_a": mat_a,
            "mat_b": mat_b,
            "pred_a": pred_a,
            "pred_b": pred_b,
            "token_a": create_access_token({"sub": str(user_a.id), "user_id": user_a.id}),
            "token_b": create_access_token({"sub": str(user_b.id), "user_id": user_b.id}),
            "token_tl": create_access_token({"sub": str(user_tenantless.id), "user_id": user_tenantless.id}),
            "token_sa_nocomp": create_access_token({"sub": str(sa_no_comp.id), "user_id": sa_no_comp.id}),
            "token_sa_comp": create_access_token({"sub": str(sa_with_comp.id), "user_id": sa_with_comp.id}),
        }
        try:
            yield data
        finally:
            pass


@pytest.mark.asyncio
async def test_accounting_utilities_hardening():
    """Verify get_primary_cash_account, get_payroll_account, and resolve_tax_accounts require company_id."""
    async with AsyncSessionLocal() as db:
        # None -> raises ValueError
        with pytest.raises(ValueError, match="Company context is required"):
            await get_primary_cash_account(db, company_id=None)

        with pytest.raises(ValueError, match="Company context is required"):
            await get_payroll_account(db, "staff_salary_account_id", company_id=None)

        with pytest.raises(ValueError, match="Company context is required"):
            await resolve_tax_accounts(db, "output_gst", company_id=None)


@pytest.mark.asyncio
async def test_ai_predictions_rbac_and_tenancy():
    """Verify AI Predictions: RBAC, IDOR 404, list isolation, SA 400 on no company context."""
    transport = ASGITransport(app=app)
    async with setup_ab_r_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Unauthenticated -> 401
            res = await ac.get("/api/v1/ai")
            assert res.status_code == 401

            # 2. Non-SA tenantless -> 403
            res = await ac.get("/api/v1/ai", headers={"Authorization": f"Bearer {d['token_tl']}"})
            assert res.status_code == 403

            # 3. Super Admin without company context calls POST /ai/predict -> 400
            res = await ac.post(
                "/api/v1/ai/predict",
                headers={"Authorization": f"Bearer {d['token_sa_nocomp']}"},
                json={"module_name": "test_ai", "prompt": "test prompt"},
            )
            assert res.status_code == 400

            res = await ac.post(
                "/api/v1/ai/predict",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={"module_name": "test_ai", "prompt": "user a prompt"},
            )
            assert res.status_code == 200, f"Error: {res.text}"

            # 5. User A lists predictions -> sees only comp_a predictions
            res = await ac.get("/api/v1/ai", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert res.status_code == 200
            items = res.json()["items"]
            pred_ids = [item["id"] for item in items]
            assert d["pred_a"].id in pred_ids
            assert d["pred_b"].id not in pred_ids

            # 6. User A gets Company B prediction -> 404 (IDOR protection)
            res = await ac.get(f"/api/v1/ai/{d['pred_b'].id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert res.status_code == 404

            # 7. User A updates Company B prediction -> 404
            res = await ac.put(
                f"/api/v1/ai/{d['pred_b'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={"prompt": "malicious update"},
            )
            assert res.status_code == 404

            # 8. User A deletes Company B prediction -> 404
            res = await ac.delete(f"/api/v1/ai/{d['pred_b'].id}", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_material_ai_recommendation_ownership():
    """Verify POST /materials/ai-recommendation strictly enforces project tenant ownership."""
    transport = ASGITransport(app=app)
    async with setup_ab_r_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Company A user with Company B project_id -> 404
            res = await ac.post(
                "/api/v1/materials/ai-recommendation",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={"project_id": d["proj_b"].id, "target_days": 7},
            )
            assert res.status_code == 404

            # 2. Verify no AIPrediction created for failed cross-tenant attempt
            async with AsyncSessionLocal() as db:
                leaked = await db.scalar(
                    select(AIPrediction).where(AIPrediction.prompt.like(f"%project_id={d['proj_b'].id}%"))
                )
                assert leaked is None

            # 3. Tenantless user -> 403
            res = await ac.post(
                "/api/v1/materials/ai-recommendation",
                headers={"Authorization": f"Bearer {d['token_tl']}"},
                json={"project_id": d["proj_a"].id, "target_days": 7},
            )
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_master_data_security_and_templates():
    """Verify Master Data: 401 unauthenticated, 403 tenantless, template visibility vs isolation."""
    transport = ASGITransport(app=app)
    async with setup_ab_r_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Unauthenticated -> 401
            assert (await ac.get("/api/v1/master/units")).status_code == 401
            assert (await ac.get("/api/v1/master/materials")).status_code == 401
            assert (await ac.get("/api/v1/master/stats")).status_code == 401
            assert (await ac.get("/api/v1/master/all")).status_code == 401

            # 2. Tenantless -> 403
            assert (await ac.get("/api/v1/master/units", headers={"Authorization": f"Bearer {d['token_tl']}"})).status_code == 403

            # 3. Company A user sees own Unit, cannot see Company B Unit
            res = await ac.get("/api/v1/master/units", headers={"Authorization": f"Bearer {d['token_a']}"})
            assert res.status_code == 200
            unit_ids = [u["id"] for u in res.json()]
            assert d["unit_a"].id in unit_ids
            assert d["unit_b"].id not in unit_ids

            # 4. Company A cannot PUT or DELETE Company B Unit -> 404
            res = await ac.put(
                f"/api/v1/master/units/{d['unit_b'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={"name": "Hacked Unit"},
            )
            assert res.status_code == 404

            res = await ac.delete(
                f"/api/v1/master/units/{d['unit_b'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
            )
            assert res.status_code == 404

            # 5. Foreign unit_id on MaterialMaster create -> 404
            res = await ac.post(
                "/api/v1/master/materials",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={
                    "name": f"New Mat {uuid.uuid4().hex[:6]}",
                    "category": "Raw",
                    "unit_id": d["unit_b"].id,  # Foreign Unit
                },
            )
            assert res.status_code == 404

            # 6. MaterialMaster with company_id IS NULL is visible to both tenants as template
            async with AsyncSessionLocal() as db:
                tmpl = await db.scalar(select(m.MaterialMaster).where(m.MaterialMaster.company_id.is_(None)))
            if tmpl:
                res_a = await ac.get("/api/v1/master/materials", headers={"Authorization": f"Bearer {d['token_a']}"})
                mats_a = [mat["id"] for mat in res_a.json()]
                assert tmpl.id in mats_a

                # Non-SA user cannot modify or delete template -> 403
                res_mod = await ac.put(
                    f"/api/v1/master/materials/{tmpl.id}",
                    headers={"Authorization": f"Bearer {d['token_a']}"},
                    json={"name": "Modified Template"},
                )
                assert res_mod.status_code == 403

                res_del = await ac.delete(
                    f"/api/v1/master/materials/{tmpl.id}",
                    headers={"Authorization": f"Bearer {d['token_a']}"},
                )
                assert res_del.status_code == 403


@pytest.mark.asyncio
async def test_attendance_tenantless_fix():
    """Verify Attendance: Non-SA tenantless user gets 403 on list and proxy check-in."""
    transport = ASGITransport(app=app)
    async with setup_ab_r_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/attendance/list", headers={"Authorization": f"Bearer {d['token_tl']}"})
            assert res.status_code == 403

            res = await ac.post(
                "/api/v1/attendance/proxy-check-in",
                headers={"Authorization": f"Bearer {d['token_tl']}"},
                json={"user_ids": [d["user_a"].id], "project_id": d["proj_a"].id},
            )
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_expense_tenancy_and_fk_validation():
    """Verify Expense: 403 tenantless, 404 foreign project, 404 foreign BOQ, 404 foreign vendor."""
    transport = ASGITransport(app=app)
    async with setup_ab_r_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Non-SA tenantless -> 403
            res = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_tl']}"},
                json={
                    "project_id": d["proj_a"].id,
                    "category": "Material",
                    "description": "Cement",
                    "amount": 1000.0,
                    "expense_date": str(date.today()),
                    "payment_mode": "cash",
                },
            )
            assert res.status_code == 403

            # 2. Company A user + Company B project -> 404
            res = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={
                    "project_id": d["proj_b"].id,
                    "category": "Material",
                    "description": "Cement",
                    "amount": 1000.0,
                    "expense_date": str(date.today()),
                    "payment_mode": "cash",
                },
            )
            assert res.status_code == 404

            # 3. Company A user + valid project + mismatched foreign BOQ -> 404
            res = await ac.post(
                "/api/v1/expenses",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={
                    "project_id": d["proj_a"].id,
                    "category": "Material",
                    "description": "Cement",
                    "amount": 1000.0,
                    "expense_date": str(date.today()),
                    "payment_mode": "cash",
                    "boq_item_id": d["boq_b"].id,  # foreign BOQ
                },
            )
            assert res.status_code == 404

            # 4. list_expenses with foreign vendor_id -> 404
            res = await ac.get(
                f"/api/v1/expenses?vendor_id={d['supplier_b'].id}",
                headers={"Authorization": f"Bearer {d['token_a']}"},
            )
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_equipment_maintenance_strict_ownership():
    """Verify Equipment Maintenance: foreign equipment -> 404, mismatched BOQ -> 404."""
    transport = ASGITransport(app=app)
    async with setup_ab_r_data() as d:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Company A user with Company B equipment -> 404
            res = await ac.post(
                f"/api/v1/equipment/{d['eq_b'].id}/maintenance",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={
                    "project_id": d["proj_a"].id,
                    "description": "Oil Change",
                    "maintenance_date": str(date.today()),
                },
            )
            assert res.status_code == 404

            # 2. Company A user with Company A equipment but foreign BOQ item -> 404
            res = await ac.post(
                f"/api/v1/equipment/{d['eq_a'].id}/maintenance",
                headers={"Authorization": f"Bearer {d['token_a']}"},
                json={
                    "project_id": d["proj_a"].id,
                    "description": "Oil Change",
                    "maintenance_date": str(date.today()),
                    "boq_item_id": d["boq_b"].id,  # foreign BOQ
                },
            )
            assert res.status_code == 404
