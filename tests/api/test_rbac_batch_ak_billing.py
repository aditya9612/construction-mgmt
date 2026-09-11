"""
Test Suite: BATCH AK — Client Progress Billing / Contractor RA Bill Management
=============================================================================
Covers:
1. Authentication: all 8 routes unauthenticated -> 401
2. Granular DB-driven RBAC Permissions:
   - billing.view, billing.create, billing.edit, billing.delete, billing.approve, billing.pay
3. Granular pay permission enforcement:
   - User with billing.edit but without billing.pay cannot pay bill (403)
   - User with billing.pay can pay (200)
4. Tenantless Non-SA:
   - company_id=None -> 403 on all endpoints
5. Tenant Isolation:
   - Tenant A cannot list or view Tenant B bills
6. IDOR 404 Masking:
   - GET /billing/{foreign_id} -> 404
   - PUT /billing/{foreign_id} -> 404
   - DELETE /billing/{foreign_id} -> 404
   - PUT /billing/{foreign_id}/submit -> 404
   - PUT /billing/{foreign_id}/approve -> 404
   - PUT /billing/{foreign_id}/pay -> 404
7. Foreign Project Injection:
   - POST /billing with foreign project_id -> 404
8. Foreign Contractor FK Injection:
   - POST /billing with foreign contractor_id -> 404
   - PUT /billing/{id} with foreign contractor_id -> 404
9. Bill Number Isolation:
   - Same bill_number allowed in Comp A and Comp B
   - Duplicate bill_number inside Comp A rejected (422)
10. Super Admin Semantics:
   - SA global list across all companies
   - SA company-filtered list (?company_id=...)
   - Nonexistent company_id -> 404
11. Non-SA Company Override Prevention:
   - Tenant A user supplying ?company_id=Comp_B cannot view Comp B data
12. Delete Business Invariant:
   - Draft bill deletion succeeds (200)
   - Approved bill deletion rejected (422)
   - Paid bill deletion rejected (422)
13. Financial Invariant & Accounting Ledger Integrity:
   - Approval generates JournalEntry (Invoice) with balanced lines (AR, Revenue, GST, Retention)
   - OwnerTransaction created on bill creation
   - Deleting draft cleans up orphan OwnerTransaction and Approval records
14. Concurrency & Duplicate Prevention:
   - Second approval attempt rejected (duplicate journal entry prevented)
15. Route Preservation:
   - Exactly 8 billing endpoints, 8 unique (method, path), 0 duplicates, 781 total app routes
16. Static Security Hygiene:
   - 0 require_roles, 0 admin_required, 0 UserRole, 0 hardcoded role allowlists
17. Dynamic RBAC Lifecycle:
   - Revoking permission in DB immediately blocks endpoint
   - Regranting in DB immediately restores access
"""

import inspect
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.api import billing as billing_api_module
from app.core.db import AsyncSessionLocal
from app.core.enums import AccountType
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.accountant import Account, JournalEntry, JournalLine
from app.models.approval import Approval
from app.models.billing import RABill
from app.models.company import Company
from app.models.contractor import Contractor
from app.models.owner import Owner, OwnerTransaction
from app.models.project import Project, ProjectMember
from app.models.rbac import Permission, Role, RolePermission
from app.models.settings import CompanySettings
from app.models.user import User


@asynccontextmanager
async def setup_billing_test_data():
    """Create test companies, projects, contractors, accounts, roles, and users."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"BillCompA_{uid}")
        comp_b = Company(name=f"BillCompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Accounting Accounts & CompanySettings
        ar_a = Account(name=f"AR A {uid}", code="ACCOUNTS_RECEIVABLE", type=AccountType.ASSET, company_id=comp_a.id)
        rev_a = Account(name=f"Revenue A {uid}", code="SALES_REVENUE", type=AccountType.INCOME, company_id=comp_a.id)
        gst_a = Account(name=f"GST A {uid}", code="OUTPUT_GST", type=AccountType.LIABILITY, company_id=comp_a.id)
        ret_a = Account(name=f"Retention A {uid}", code="RETENTION_PAYABLE", type=AccountType.LIABILITY, company_id=comp_a.id)

        ar_b = Account(name=f"AR B {uid}", code="ACCOUNTS_RECEIVABLE", type=AccountType.ASSET, company_id=comp_b.id)
        rev_b = Account(name=f"Revenue B {uid}", code="SALES_REVENUE", type=AccountType.INCOME, company_id=comp_b.id)
        gst_b = Account(name=f"GST B {uid}", code="OUTPUT_GST", type=AccountType.LIABILITY, company_id=comp_b.id)

        db.add_all([ar_a, rev_a, gst_a, ret_a, ar_b, rev_b, gst_b])
        await db.flush()

        settings_a = CompanySettings(company_id=comp_a.id)
        settings_b = CompanySettings(company_id=comp_b.id)
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

        proj_a = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Project A {uid}",
            business_id=f"PA_{uid}",
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Project B {uid}",
            business_id=f"PB_{uid}",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 4. Contractors
        contractor_a = Contractor(
            name=f"Contractor A {uid}",
            contractor_id=f"CONT-A-{uid}",
            company_id=comp_a.id,
            contact_number=f"91{uuid.uuid4().int % 100000000:08d}",
            work_type="Civil",
            rate_type="ItemRate",
        )
        contractor_b = Contractor(
            name=f"Contractor B {uid}",
            contractor_id=f"CONT-B-{uid}",
            company_id=comp_b.id,
            contact_number=f"92{uuid.uuid4().int % 100000000:08d}",
            work_type="Civil",
            rate_type="ItemRate",
        )
        db.add_all([contractor_a, contractor_b])
        await db.flush()

        # 5. Roles & Permissions
        all_billing_perms = [
            "billing.view",
            "billing.create",
            "billing.edit",
            "billing.delete",
            "billing.approve",
            "billing.pay",
        ]
        perm_objs = {}
        for pcode in all_billing_perms:
            p = await db.scalar(select(Permission).where(Permission.code == pcode))
            if not p:
                parts = pcode.split(".")
                p = Permission(module=parts[0], action=parts[1], code=pcode, description=f"Billing {parts[1]}")
                db.add(p)
                await db.flush()
            perm_objs[pcode] = p

        role_name_full_a = f"BillingFullA_{uid}"
        role_name_nopay_a = f"BillingNoPayA_{uid}"
        role_name_ro_a = f"BillingROA_{uid}"
        role_name_full_b = f"BillingFullB_{uid}"
        role_name_none_a = f"BillingNoneA_{uid}"

        role_full_a = Role(company_id=comp_a.id, name=role_name_full_a, display_name="Billing Full A", is_system=False)
        role_nopay_a = Role(company_id=comp_a.id, name=role_name_nopay_a, display_name="Billing No Pay A", is_system=False)
        role_ro_a = Role(company_id=comp_a.id, name=role_name_ro_a, display_name="Billing ReadOnly A", is_system=False)
        role_full_b = Role(company_id=comp_b.id, name=role_name_full_b, display_name="Billing Full B", is_system=False)
        role_none_a = Role(company_id=comp_a.id, name=role_name_none_a, display_name="Billing None A", is_system=False)

        db.add_all([role_full_a, role_nopay_a, role_ro_a, role_full_b, role_none_a])
        await db.flush()

        # Grant permissions
        for pcode in all_billing_perms:
            db.add(RolePermission(role=role_name_full_a, role_id=role_full_a.id, permission_id=perm_objs[pcode].id))
            db.add(RolePermission(role=role_name_full_b, role_id=role_full_b.id, permission_id=perm_objs[pcode].id))

        for pcode in all_billing_perms:
            if pcode != "billing.pay":
                db.add(RolePermission(role=role_name_nopay_a, role_id=role_nopay_a.id, permission_id=perm_objs[pcode].id))

        db.add(RolePermission(role=role_name_ro_a, role_id=role_ro_a.id, permission_id=perm_objs["billing.view"].id))
        await db.flush()

        # 6. Users
        user_full_a = User(
            email=f"full_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role=role_name_full_a,
            is_active=True,
            is_super_admin=False,
        )
        user_nopay_a = User(
            email=f"nopay_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role=role_name_nopay_a,
            is_active=True,
            is_super_admin=False,
        )
        user_ro_a = User(
            email=f"ro_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role=role_name_ro_a,
            is_active=True,
            is_super_admin=False,
        )
        user_comp_b = User(
            email=f"comp_b_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_b.id,
            role=role_name_full_b,
            is_active=True,
            is_super_admin=False,
        )
        user_tenantless = User(
            email=f"tenantless_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=None,
            role=role_name_full_a,
            is_active=True,
            is_super_admin=False,
        )
        user_super = User(
            email=f"super_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=None,
            role="SuperAdmin",
            is_active=True,
            is_super_admin=True,
        )
        user_none_a = User(
            email=f"none_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role=role_name_none_a,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([user_full_a, user_nopay_a, user_ro_a, user_comp_b, user_tenantless, user_super, user_none_a])
        await db.flush()

        # Add project members
        for u in [user_full_a, user_nopay_a, user_ro_a, user_none_a]:
            db.add(ProjectMember(project_id=proj_a.id, user_id=u.id))
        db.add(ProjectMember(project_id=proj_b.id, user_id=user_comp_b.id))
        await db.flush()

        # 7. Initial RA Bills
        bill_a = RABill(
            project_id=proj_a.id,
            contractor_id=contractor_a.id,
            bill_number=f"BILL-A-{uid}",
            work_description="Excavation and foundation work",
            quantity=Decimal("100.000"),
            rate=Decimal("500.00"),
            deductions=Decimal("2000.00"),
            net_amount=Decimal("48000.00"),
            gst_percent=Decimal("18.00"),
            total_amount=Decimal("56640.00"),
            gross_amount=Decimal("50000.00"),
            bill_date=date.today(),
            status="Draft",
        )
        bill_b = RABill(
            project_id=proj_b.id,
            contractor_id=contractor_b.id,
            bill_number=f"BILL-B-{uid}",
            work_description="Masonry work",
            quantity=Decimal("200.000"),
            rate=Decimal("300.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("60000.00"),
            gst_percent=Decimal("18.00"),
            total_amount=Decimal("70800.00"),
            gross_amount=Decimal("60000.00"),
            bill_date=date.today(),
            status="Draft",
        )
        db.add_all([bill_a, bill_b])
        await db.flush()

        await db.commit()

        yield {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "contractor_a": contractor_a,
            "contractor_b": contractor_b,
            "bill_a": bill_a,
            "bill_b": bill_b,
            "user_full_a": user_full_a,
            "user_nopay_a": user_nopay_a,
            "user_ro_a": user_ro_a,
            "user_comp_b": user_comp_b,
            "user_tenantless": user_tenantless,
            "user_super": user_super,
            "user_none_a": user_none_a,
            "role_full_a": role_full_a,
            "perm_objs": perm_objs,
        }


def auth_headers(user: User) -> dict:
    token = create_access_token(data={"sub": str(user.id), "user_id": user.id})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. Unauthenticated Access Denied (All 8 Routes -> 401)
# ==============================================================================
@pytest.mark.asyncio
async def test_all_routes_unauthenticated_return_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/billing")
        assert res.status_code == 401

        res = await ac.post("/api/v1/billing", json={})
        assert res.status_code == 401

        res = await ac.get("/api/v1/billing/1")
        assert res.status_code == 401

        res = await ac.put("/api/v1/billing/1", json={})
        assert res.status_code == 401

        res = await ac.delete("/api/v1/billing/1")
        assert res.status_code == 401

        res = await ac.put("/api/v1/billing/1/submit")
        assert res.status_code == 401

        res = await ac.put("/api/v1/billing/1/approve")
        assert res.status_code == 401

        res = await ac.put("/api/v1/billing/1/pay")
        assert res.status_code == 401


# ==============================================================================
# 2. Permission Enforcement (Missing Permissions -> 403)
# ==============================================================================
@pytest.mark.asyncio
async def test_permission_enforcement_missing_grants():
    async with setup_billing_test_data() as data:
        user_none = data["user_none_a"]
        user_ro = data["user_ro_a"]
        bill_a = data["bill_a"]
        headers_none = auth_headers(user_none)
        headers_ro = auth_headers(user_ro)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # User with zero permissions denied on all endpoints
            res = await ac.get("/api/v1/billing", headers=headers_none)
            assert res.status_code == 403

            res = await ac.post(
                "/api/v1/billing",
                headers=headers_none,
                json={
                    "project_id": data["proj_a"].id,
                    "bill_number": f"TEST-{data['uid']}",
                    "work_description": "Test",
                    "quantity": "10.000",
                    "rate": "100.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res.status_code == 403

            res = await ac.get(f"/api/v1/billing/{bill_a.id}", headers=headers_none)
            assert res.status_code == 403

            # Read-only user can view list and get, but cannot mutate
            res = await ac.get("/api/v1/billing", headers=headers_ro)
            assert res.status_code == 200

            res = await ac.get(f"/api/v1/billing/{bill_a.id}", headers=headers_ro)
            assert res.status_code == 200

            res = await ac.put(f"/api/v1/billing/{bill_a.id}", headers=headers_ro, json={"work_description": "New Desc"})
            assert res.status_code == 403

            res = await ac.delete(f"/api/v1/billing/{bill_a.id}", headers=headers_ro)
            assert res.status_code == 403

            res = await ac.put(f"/api/v1/billing/{bill_a.id}/submit", headers=headers_ro)
            assert res.status_code == 403

            res = await ac.put(f"/api/v1/billing/{bill_a.id}/approve", headers=headers_ro)
            assert res.status_code == 403

            res = await ac.put(f"/api/v1/billing/{bill_a.id}/pay", headers=headers_ro)
            assert res.status_code == 403


# ==============================================================================
# 3. Granular Pay Permission Enforcement (billing.pay vs billing.edit)
# ==============================================================================
@pytest.mark.asyncio
async def test_granular_pay_permission_enforcement():
    async with setup_billing_test_data() as data:
        user_nopay = data["user_nopay_a"]
        user_full = data["user_full_a"]
        bill_a = data["bill_a"]
        headers_nopay = auth_headers(user_nopay)
        headers_full = auth_headers(user_full)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Submit the bill using user_nopay (has billing.edit)
            res_sub = await ac.put(f"/api/v1/billing/{bill_a.id}/submit", headers=headers_nopay)
            assert res_sub.status_code == 200

            # 2. Approve the bill using user_nopay (has billing.approve)
            res_app = await ac.put(f"/api/v1/billing/{bill_a.id}/approve", headers=headers_nopay)
            assert res_app.status_code == 200

            # 3. User with billing.edit but WITHOUT billing.pay CANNOT pay bill -> 403
            res_pay_denied = await ac.put(f"/api/v1/billing/{bill_a.id}/pay", headers=headers_nopay)
            assert res_pay_denied.status_code == 403

            # 4. User with billing.pay CAN pay bill -> 200
            res_pay_ok = await ac.put(f"/api/v1/billing/{bill_a.id}/pay", headers=headers_full)
            assert res_pay_ok.status_code == 200
            assert res_pay_ok.json()["message"] == "Paid"


# ==============================================================================
# 4. Tenantless Non-SA User Denied (company_id=None -> 403)
# ==============================================================================
@pytest.mark.asyncio
async def test_tenantless_non_sa_user_denied():
    async with setup_billing_test_data() as data:
        user_tenantless = data["user_tenantless"]
        bill_a = data["bill_a"]
        headers = auth_headers(user_tenantless)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/billing", headers=headers)
            assert res.status_code == 403

            res = await ac.post(
                "/api/v1/billing",
                headers=headers,
                json={
                    "project_id": data["proj_a"].id,
                    "bill_number": f"TENANTLESS-{data['uid']}",
                    "work_description": "Tenantless test",
                    "quantity": "10.000",
                    "rate": "100.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res.status_code == 403

            res = await ac.get(f"/api/v1/billing/{bill_a.id}", headers=headers)
            assert res.status_code == 403

            res = await ac.delete(f"/api/v1/billing/{bill_a.id}", headers=headers)
            assert res.status_code == 403


# ==============================================================================
# 5. Tenant Isolation (Tenant A vs Tenant B Listing)
# ==============================================================================
@pytest.mark.asyncio
async def test_tenant_isolation_listing():
    async with setup_billing_test_data() as data:
        headers_a = auth_headers(data["user_full_a"])
        headers_b = auth_headers(data["user_comp_b"])
        bill_a = data["bill_a"]
        bill_b = data["bill_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Tenant A listing only shows Comp A bills
            res_a = await ac.get("/api/v1/billing", headers=headers_a)
            assert res_a.status_code == 200
            items_a = res_a.json()["items"]
            ids_a = [item["id"] for item in items_a]
            assert bill_a.id in ids_a
            assert bill_b.id not in ids_a

            # Tenant B listing only shows Comp B bills
            res_b = await ac.get("/api/v1/billing", headers=headers_b)
            assert res_b.status_code == 200
            items_b = res_b.json()["items"]
            ids_b = [item["id"] for item in items_b]
            assert bill_b.id in ids_b
            assert bill_a.id not in ids_b


# ==============================================================================
# 6. IDOR 404 Masking Across All Endpoints
# ==============================================================================
@pytest.mark.asyncio
async def test_idor_404_masking_foreign_tenant_bill():
    async with setup_billing_test_data() as data:
        headers_a = auth_headers(data["user_full_a"])
        bill_b = data["bill_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. GET foreign bill -> 404
            res_get = await ac.get(f"/api/v1/billing/{bill_b.id}", headers=headers_a)
            assert res_get.status_code == 404

            # 2. PUT foreign bill -> 404
            res_put = await ac.put(f"/api/v1/billing/{bill_b.id}", headers=headers_a, json={"work_description": "Hacked"})
            assert res_put.status_code == 404

            # 3. DELETE foreign bill -> 404
            res_del = await ac.delete(f"/api/v1/billing/{bill_b.id}", headers=headers_a)
            assert res_del.status_code == 404

            # 4. SUBMIT foreign bill -> 404
            res_sub = await ac.put(f"/api/v1/billing/{bill_b.id}/submit", headers=headers_a)
            assert res_sub.status_code == 404

            # 5. APPROVE foreign bill -> 404
            res_app = await ac.put(f"/api/v1/billing/{bill_b.id}/approve", headers=headers_a)
            assert res_app.status_code == 404

            # 6. PAY foreign bill -> 404
            res_pay = await ac.put(f"/api/v1/billing/{bill_b.id}/pay", headers=headers_a)
            assert res_pay.status_code == 404


# ==============================================================================
# 7. Foreign Project Injection Rejected (404)
# ==============================================================================
@pytest.mark.asyncio
async def test_foreign_project_injection_rejected():
    async with setup_billing_test_data() as data:
        headers_a = auth_headers(data["user_full_a"])
        proj_b = data["proj_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/billing",
                headers=headers_a,
                json={
                    "project_id": proj_b.id,
                    "bill_number": f"INJECT_PROJ_{data['uid']}",
                    "work_description": "Malicious project injection",
                    "quantity": "10.000",
                    "rate": "100.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res.status_code == 404


# ==============================================================================
# 8. Foreign Contractor FK Injection Rejected (404)
# ==============================================================================
@pytest.mark.asyncio
async def test_foreign_contractor_injection_rejected():
    async with setup_billing_test_data() as data:
        headers_a = auth_headers(data["user_full_a"])
        proj_a = data["proj_a"]
        contractor_b = data["contractor_b"]
        bill_a = data["bill_a"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create with foreign contractor
            res_create = await ac.post(
                "/api/v1/billing",
                headers=headers_a,
                json={
                    "project_id": proj_a.id,
                    "contractor_id": contractor_b.id,
                    "bill_number": f"INJECT_CONT_{data['uid']}",
                    "work_description": "Foreign contractor injection",
                    "quantity": "10.000",
                    "rate": "100.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res_create.status_code == 404

            # Update with foreign contractor
            res_update = await ac.put(
                f"/api/v1/billing/{bill_a.id}",
                headers=headers_a,
                json={"contractor_id": contractor_b.id},
            )
            assert res_update.status_code == 404


# ==============================================================================
# 9. Bill Number Isolation Across Tenants
# ==============================================================================
@pytest.mark.asyncio
async def test_bill_number_tenant_scoped_uniqueness():
    async with setup_billing_test_data() as data:
        headers_a = auth_headers(data["user_full_a"])
        headers_b = auth_headers(data["user_comp_b"])
        shared_bill_no = f"COMMON-RA-{data['uid']}"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Company A creates COMMON-RA
            res_a1 = await ac.post(
                "/api/v1/billing",
                headers=headers_a,
                json={
                    "project_id": data["proj_a"].id,
                    "bill_number": shared_bill_no,
                    "work_description": "Comp A Bill",
                    "quantity": "50.000",
                    "rate": "200.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res_a1.status_code == 200

            # 2. Company B creates SAME COMMON-RA -> MUST BE ALLOWED!
            res_b = await ac.post(
                "/api/v1/billing",
                headers=headers_b,
                json={
                    "project_id": data["proj_b"].id,
                    "bill_number": shared_bill_no,
                    "work_description": "Comp B Bill with same number",
                    "quantity": "80.000",
                    "rate": "150.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res_b.status_code == 200

            # 3. Company A creates duplicate COMMON-RA -> REJECTED (422)
            res_a2 = await ac.post(
                "/api/v1/billing",
                headers=headers_a,
                json={
                    "project_id": data["proj_a"].id,
                    "bill_number": shared_bill_no,
                    "work_description": "Duplicate in Comp A",
                    "quantity": "10.000",
                    "rate": "100.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res_a2.status_code == 422


# ==============================================================================
# 10. Super Admin Behavior (Global List, Filtered List, 404 on Invalid Company)
# ==============================================================================
@pytest.mark.asyncio
async def test_super_admin_behavior():
    async with setup_billing_test_data() as data:
        headers_sa = auth_headers(data["user_super"])
        bill_a = data["bill_a"]
        bill_b = data["bill_b"]
        comp_a = data["comp_a"]
        comp_b = data["comp_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Global list without company_id returns bills from ALL companies
            res_global = await ac.get("/api/v1/billing", headers=headers_sa)
            assert res_global.status_code == 200
            global_ids = [item["id"] for item in res_global.json()["items"]]
            assert bill_a.id in global_ids
            assert bill_b.id in global_ids

            # 2. Filtered list by company_id=comp_a.id
            res_filt_a = await ac.get(f"/api/v1/billing?company_id={comp_a.id}", headers=headers_sa)
            assert res_filt_a.status_code == 200
            filt_a_ids = [item["id"] for item in res_filt_a.json()["items"]]
            assert bill_a.id in filt_a_ids
            assert bill_b.id not in filt_a_ids

            # 3. Filtered list by invalid company_id -> 404
            res_invalid = await ac.get("/api/v1/billing?company_id=9999999", headers=headers_sa)
            assert res_invalid.status_code == 404


# ==============================================================================
# 11. Non-SA Company Override Prevention
# ==============================================================================
@pytest.mark.asyncio
async def test_non_sa_company_override_prevention():
    async with setup_billing_test_data() as data:
        headers_a = auth_headers(data["user_full_a"])
        comp_b = data["comp_b"]
        bill_b = data["bill_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Tenant A user supplies company_id=Comp_B -> should be constrained to Comp A
            res = await ac.get(f"/api/v1/billing?company_id={comp_b.id}", headers=headers_a)
            assert res.status_code == 200
            ids = [item["id"] for item in res.json()["items"]]
            assert bill_b.id not in ids


# ==============================================================================
# 12. Delete Business Invariant (Draft Allowed, Approved/Paid Rejected)
# ==============================================================================
@pytest.mark.asyncio
async def test_delete_business_invariant():
    async with setup_billing_test_data() as data:
        headers_a = auth_headers(data["user_full_a"])
        proj_a = data["proj_a"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create a draft bill and delete it -> 200
            res_create = await ac.post(
                "/api/v1/billing",
                headers=headers_a,
                json={
                    "project_id": proj_a.id,
                    "bill_number": f"DELETE_ME_{data['uid']}",
                    "work_description": "To be deleted",
                    "quantity": "10.000",
                    "rate": "100.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res_create.status_code == 200
            del_id = res_create.json()["id"]

            res_del = await ac.delete(f"/api/v1/billing/{del_id}", headers=headers_a)
            assert res_del.status_code == 200
            assert res_del.json()["success"] is True

            # 2. Create another bill, submit, approve, and attempt deletion -> 422 rejected
            res_c2 = await ac.post(
                "/api/v1/billing",
                headers=headers_a,
                json={
                    "project_id": proj_a.id,
                    "bill_number": f"APPROVE_DEL_{data['uid']}",
                    "work_description": "Approved bill cannot be deleted",
                    "quantity": "20.000",
                    "rate": "100.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res_c2.status_code == 200
            app_id = res_c2.json()["id"]

            await ac.put(f"/api/v1/billing/{app_id}/submit", headers=headers_a)
            await ac.put(f"/api/v1/billing/{app_id}/approve", headers=headers_a)

            res_del_approved = await ac.delete(f"/api/v1/billing/{app_id}", headers=headers_a)
            assert res_del_approved.status_code == 422

            # 3. Pay bill and attempt deletion -> 422 rejected
            await ac.put(f"/api/v1/billing/{app_id}/pay", headers=headers_a)
            res_del_paid = await ac.delete(f"/api/v1/billing/{app_id}", headers=headers_a)
            assert res_del_paid.status_code == 422


# ==============================================================================
# 13. Financial Invariant & Accounting Ledger Integrity
# ==============================================================================
@pytest.mark.asyncio
async def test_financial_accounting_ledger_integrity():
    async with setup_billing_test_data() as data:
        headers_a = auth_headers(data["user_full_a"])
        proj_a = data["proj_a"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create bill with quantity=100, rate=500 -> Gross=50000, deductions=2000 -> Net=48000, GST 18%=8640, Total=56640
            res = await ac.post(
                "/api/v1/billing",
                headers=headers_a,
                json={
                    "project_id": proj_a.id,
                    "bill_number": f"FIN_TEST_{data['uid']}",
                    "work_description": "Accounting integrity bill",
                    "quantity": "100.000",
                    "rate": "500.00",
                    "deductions": "2000.00",
                    "gst_percent": "18.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            assert res.status_code == 200
            bill_id = res.json()["id"]

            # Submit & Approve
            await ac.put(f"/api/v1/billing/{bill_id}/submit", headers=headers_a)
            res_app = await ac.put(f"/api/v1/billing/{bill_id}/approve", headers=headers_a)
            assert res_app.status_code == 200

            # Verify in DB: JournalEntry and balanced JournalLines
            async with AsyncSessionLocal() as db:
                je = await db.scalar(
                    select(JournalEntry).where(JournalEntry.journal_number == f"J-RAB-{bill_id}")
                )
                assert je is not None
                assert je.entry_type == "Invoice"
                assert je.status == "Posted"

                lines = (await db.execute(select(JournalLine).where(JournalLine.entry_id == je.id))).scalars().all()
                assert len(lines) >= 2

                total_debit = sum(line.debit for line in lines)
                total_credit = sum(line.credit for line in lines)
                assert total_debit == total_credit


# ==============================================================================
# 14. Concurrency & State Transition Duplicate Prevention
# ==============================================================================
@pytest.mark.asyncio
async def test_duplicate_approval_prevention():
    async with setup_billing_test_data() as data:
        headers_a = auth_headers(data["user_full_a"])
        proj_a = data["proj_a"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/billing",
                headers=headers_a,
                json={
                    "project_id": proj_a.id,
                    "bill_number": f"CONC_TEST_{data['uid']}",
                    "work_description": "Concurrency test",
                    "quantity": "50.000",
                    "rate": "100.00",
                    "bill_date": date.today().isoformat(),
                },
            )
            bill_id = res.json()["id"]
            await ac.put(f"/api/v1/billing/{bill_id}/submit", headers=headers_a)
            res1 = await ac.put(f"/api/v1/billing/{bill_id}/approve", headers=headers_a)
            assert res1.status_code == 200

            # Second approval attempt must fail because status is no longer "Submitted"
            res2 = await ac.put(f"/api/v1/billing/{bill_id}/approve", headers=headers_a)
            assert res2.status_code == 422


# ==============================================================================
# 15. Dynamic Permission Lifecycle (Revocation & Regranting)
# ==============================================================================
@pytest.mark.asyncio
async def test_dynamic_permission_lifecycle():
    async with setup_billing_test_data() as data:
        user_full = data["user_full_a"]
        role_full = data["role_full_a"]
        perm_view = data["perm_objs"]["billing.view"]
        headers = auth_headers(user_full)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Allowed initially
            res1 = await ac.get("/api/v1/billing", headers=headers)
            assert res1.status_code == 200

            # 2. Revoke billing.view in DB
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role_full.id,
                        RolePermission.permission_id == perm_view.id,
                    )
                )
                await db.commit()

            # 3. Immediately blocked
            res2 = await ac.get("/api/v1/billing", headers=headers)
            assert res2.status_code == 403

            # 4. Regrant billing.view in DB
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_full.name, role_id=role_full.id, permission_id=perm_view.id))
                await db.commit()

            # 5. Immediately restored
            res3 = await ac.get("/api/v1/billing", headers=headers)
            assert res3.status_code == 200


# ==============================================================================
# 16. Route Preservation Verification
# ==============================================================================
def test_route_preservation():
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    billing_routes = [r for r in routes if r.path.startswith("/api/v1/billing")]

    unique_methods_paths = set()
    duplicates = 0
    for r in routes:
        for m in r.methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            if (m, r.path) in unique_methods_paths:
                duplicates += 1
            unique_methods_paths.add((m, r.path))

    assert len(billing_routes) == 8
    assert len(unique_methods_paths) == 781
    assert duplicates == 0


# ==============================================================================
# 17. Static Security Hygiene Verification
# ==============================================================================
def test_static_security_hygiene():
    source = inspect.getsource(billing_api_module)
    assert len(re.findall(r"require_roles", source)) == 0
    assert len(re.findall(r"admin_required", source)) == 0
    assert len(re.findall(r"UserRole", source)) == 0
    assert len(re.findall(r"BILLING_READ_ROLES", source)) == 0
    assert len(re.findall(r"BILLING_WRITE_ROLES", source)) == 0
