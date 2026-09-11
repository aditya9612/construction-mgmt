"""
RBAC Batch AG: SaaS Billing & Subscription Management Test Suite
================================================================

Covers all mandatory verification areas for Batch AG:
- TC01: Unauthenticated requests to protected endpoints return 401
- TC02: Authenticated user without saas_billing.view returns 403
- TC03: saas_billing.view permission grants GET /me
- TC04: saas_billing.view permission grants GET /usage
- TC05: saas_billing.view permission grants GET /invoices
- TC06: saas_billing.view permission grants GET /history
- TC07: Authenticated user without saas_billing.create returns 403 on POST /checkout
- TC08: saas_billing.create permission allows POST /checkout
- TC09: saas_billing.view permission allows UPI QR and transaction listing/detail
- TC10: saas_billing.create permission allows POST /upi/submit
- TC11: Dynamic DB permission grant works without application restart
- TC12: Dynamic DB permission revoke works immediately on next request
- TC13: Role name alone does not grant access; DB-driven permission is mandatory
- TC14: Tenantless non-SA user (company_id=None) returns 403 Forbidden
- TC15: Foreign invoice IDOR returns 404 masked (generic not-found)
- TC16: Foreign UPI transaction IDOR returns 404 masked (generic not-found)
- TC17: Nonexistent invoice returns 404
- TC18: Nonexistent UPI transaction reference returns 404
- TC19: Global duplicate UTR returns 400 Bad Request
- TC20: Invalid/malformed UTR returns 400 Bad Request
- TC21: GET /plans remains public and returns active plans without authentication
- TC22: POST /webhook preserves signature verification (valid accepted, invalid rejected)
- TC23: Super Admin has global visibility across all tenants
- TC24: Route preservation: exactly 14 SaaS billing routes and 781 total APIRoutes
- TC25: Static code hygiene: 0 require_tenant_admin, 0 require_roles, 0 admin_required, 0 UserRole.ADMIN in saas_billing.py
"""

import uuid
from decimal import Decimal
from datetime import datetime
from contextlib import asynccontextmanager
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, delete

from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.api.saas_billing import router as saas_billing_router
from app.models.company import Company
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride
from app.models.user import User, ActivityLog
from app.models.subscription import (
    Plan,
    Subscription,
    SubscriptionInvoice,
    ManualPaymentTransaction,
)


def _tok(user_id: int) -> str:
    return create_access_token({"sub": str(user_id)})


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def setup_batch_ag_data():
    """
    Provisions two isolated tenants (comp_a, comp_b) with:
    - Users in Company A: user_a_viewer, user_a_creator, user_a_full, user_a_noperms
    - Users in Company B: user_b
    - Super Admin user: user_sa
    - Tenantless non-SA user: user_tenantless
    - Plans: plan_active, plan_inactive
    - Subscriptions, invoices, and manual UPI payment transactions
    """
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"BatchAG_CompA_{uid}")
        comp_b = Company(name=f"BatchAG_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Permissions required
        perm_codes = ["saas_billing.view", "saas_billing.create", "*"]
        perms = {}
        for code in perm_codes:
            p = (await db.execute(select(Permission).where(Permission.code == code))).scalar_one_or_none()
            if not p:
                p = Permission(
                    code=code,
                    description=code,
                    module="saas_billing" if code != "*" else "all",
                    action=code.split(".")[1] if "." in code else "*",
                )
                db.add(p)
                await db.flush()
            perms[code] = p

        # 3. Roles in Company A
        role_viewer = Role(name=f"ag_viewer_{uid}", display_name="Viewer", company_id=comp_a.id, is_system=False)
        role_creator = Role(name=f"ag_creator_{uid}", display_name="Creator", company_id=comp_a.id, is_system=False)
        role_full = Role(name=f"ag_full_{uid}", display_name="Full", company_id=comp_a.id, is_system=False)
        role_noperms = Role(name=f"ag_noperms_{uid}", display_name="NoPerms", company_id=comp_a.id, is_system=False)
        db.add_all([role_viewer, role_creator, role_full, role_noperms])
        await db.flush()

        db.add(RolePermission(role=role_viewer.name, role_id=role_viewer.id, permission_id=perms["saas_billing.view"].id))
        db.add(RolePermission(role=role_creator.name, role_id=role_creator.id, permission_id=perms["saas_billing.create"].id))
        db.add(RolePermission(role=role_full.name, role_id=role_full.id, permission_id=perms["saas_billing.view"].id))
        db.add(RolePermission(role=role_full.name, role_id=role_full.id, permission_id=perms["saas_billing.create"].id))
        await db.flush()

        # Role in Company B
        role_b = Role(name=f"ag_comp_b_{uid}", display_name="Comp B", company_id=comp_b.id, is_system=False)
        db.add(role_b)
        await db.flush()
        db.add(RolePermission(role=role_b.name, role_id=role_b.id, permission_id=perms["saas_billing.view"].id))
        db.add(RolePermission(role=role_b.name, role_id=role_b.id, permission_id=perms["saas_billing.create"].id))
        await db.flush()

        # 4. Users
        user_a_viewer = User(
            email=f"ag_viewer_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha Viewer {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_viewer.name,
            is_active=True,
            is_deleted=False,
        )
        user_a_creator = User(
            email=f"ag_creator_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha Creator {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_creator.name,
            is_active=True,
            is_deleted=False,
        )
        user_a_full = User(
            email=f"ag_full_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha Full {uid}",
            mobile=f"93{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_full.name,
            is_active=True,
            is_deleted=False,
        )
        user_a_noperms = User(
            email=f"ag_noperms_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha NoPerms {uid}",
            mobile=f"94{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_noperms.name,
            is_active=True,
            is_deleted=False,
        )
        user_b = User(
            email=f"ag_b_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Beta User {uid}",
            mobile=f"95{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_b.id,
            role=role_b.name,
            is_active=True,
            is_deleted=False,
        )
        user_sa = User(
            email=f"ag_sa_{uid}@infrapilot.com",
            hashed_password=pwd,
            full_name=f"Super Admin {uid}",
            mobile=f"96{uuid.uuid4().int % 100000000:08d}",
            company_id=None,
            role="Admin",
            is_super_admin=True,
            is_active=True,
            is_deleted=False,
        )
        user_tenantless = User(
            email=f"ag_tenantless_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Tenantless User {uid}",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
            company_id=None,
            role=role_full.name,
            is_super_admin=False,
            is_active=True,
            is_deleted=False,
        )
        db.add_all([user_a_viewer, user_a_creator, user_a_full, user_a_noperms, user_b, user_sa, user_tenantless])
        await db.flush()

        # 5. Plans
        plan_active = Plan(
            name=f"Pro Plan {uid}",
            code=f"pro_{uid}",
            price=1999.0,
            currency="INR",
            billing_interval="monthly",
            is_active=True,
        )
        plan_inactive = Plan(
            name=f"Old Plan {uid}",
            code=f"old_{uid}",
            price=999.0,
            currency="INR",
            billing_interval="monthly",
            is_active=False,
        )
        db.add_all([plan_active, plan_inactive])
        await db.flush()

        # 6. Subscriptions
        sub_a = Subscription(
            company_id=comp_a.id,
            plan_id=plan_active.id,
            status="active",
            external_customer_id=f"cus_a_{uid}",
        )
        sub_b = Subscription(
            company_id=comp_b.id,
            plan_id=plan_active.id,
            status="active",
            external_customer_id=f"cus_b_{uid}",
        )
        db.add_all([sub_a, sub_b])
        await db.flush()

        # 7. Subscription Invoices
        inv_a = SubscriptionInvoice(
            company_id=comp_a.id,
            subscription_id=sub_a.id,
            total_amount=Decimal("1999.00"),
            subtotal=Decimal("1999.00"),
            tax_amount=Decimal("0.00"),
            currency="INR",
            status="paid",
            invoice_number=f"INV-A-{uid}",
        )
        inv_b = SubscriptionInvoice(
            company_id=comp_b.id,
            subscription_id=sub_b.id,
            total_amount=Decimal("1999.00"),
            subtotal=Decimal("1999.00"),
            tax_amount=Decimal("0.00"),
            currency="INR",
            status="paid",
            invoice_number=f"INV-B-{uid}",
        )
        db.add_all([inv_a, inv_b])
        await db.flush()

        # 8. Manual Payment Transactions
        txn_ref_a = f"TXN-UPI-{comp_a.id}-{int(datetime.utcnow().timestamp())}-{uid[:4].upper()}"
        txn_ref_b = f"TXN-UPI-{comp_b.id}-{int(datetime.utcnow().timestamp())}-{uid[4:].upper()}"
        utr_a = f"UTR{uid[:8].upper()}0001"
        utr_b = f"UTR{uid[:8].upper()}0002"

        txn_a = ManualPaymentTransaction(
            company_id=comp_a.id,
            subscription_id=sub_a.id,
            plan_id=plan_active.id,
            amount=Decimal("1999.00"),
            currency="INR",
            payment_method="UPI",
            transaction_reference=txn_ref_a,
            utr_reference=utr_a,
            status="pending",
        )
        txn_b = ManualPaymentTransaction(
            company_id=comp_b.id,
            subscription_id=sub_b.id,
            plan_id=plan_active.id,
            amount=Decimal("1999.00"),
            currency="INR",
            payment_method="UPI",
            transaction_reference=txn_ref_b,
            utr_reference=utr_b,
            status="pending",
        )
        db.add_all([txn_a, txn_b])
        await db.commit()

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "perms": perms,
            "role_viewer": role_viewer,
            "role_creator": role_creator,
            "role_full": role_full,
            "role_noperms": role_noperms,
            "role_b": role_b,
            "user_a_viewer": user_a_viewer,
            "user_a_creator": user_a_creator,
            "user_a_full": user_a_full,
            "user_a_noperms": user_a_noperms,
            "user_b": user_b,
            "user_sa": user_sa,
            "user_tenantless": user_tenantless,
            "plan_active": plan_active,
            "plan_inactive": plan_inactive,
            "sub_a": sub_a,
            "sub_b": sub_b,
            "inv_a": inv_a,
            "inv_b": inv_b,
            "txn_a": txn_a,
            "txn_b": txn_b,
            "txn_ref_a": txn_ref_a,
            "txn_ref_b": txn_ref_b,
            "utr_a": utr_a,
            "utr_b": utr_b,
        }

    try:
        yield data
    finally:
        async with AsyncSessionLocal() as db:
            all_user_ids = [
                user_a_viewer.id, user_a_creator.id, user_a_full.id, user_a_noperms.id, user_b.id, user_sa.id, user_tenantless.id
            ]
            # Cleanup activity logs first to avoid FK constraint on users
            await db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(all_user_ids)))
            await db.execute(delete(ManualPaymentTransaction).where(ManualPaymentTransaction.id.in_([txn_a.id, txn_b.id])))
            await db.execute(delete(SubscriptionInvoice).where(SubscriptionInvoice.id.in_([inv_a.id, inv_b.id])))
            await db.execute(delete(Subscription).where(Subscription.id.in_([sub_a.id, sub_b.id])))
            await db.execute(delete(Plan).where(Plan.id.in_([plan_active.id, plan_inactive.id])))
            await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(all_user_ids)))
            await db.execute(delete(User).where(User.id.in_(all_user_ids)))
            await db.execute(delete(RolePermission).where(RolePermission.role_id.in_([
                role_viewer.id, role_creator.id, role_full.id, role_noperms.id, role_b.id
            ])))
            await db.execute(delete(Role).where(Role.id.in_([
                role_viewer.id, role_creator.id, role_full.id, role_noperms.id, role_b.id
            ])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()


# ==============================================================================
# TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_tc01_unauthenticated_protected_endpoints():
    """TC01: Verify unauthenticated requests to protected endpoints return 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/v1/saas-billing/me")
        assert res.status_code == 401

        res = await ac.get("/api/v1/saas-billing/usage")
        assert res.status_code == 401

        res = await ac.get("/api/v1/saas-billing/invoices")
        assert res.status_code == 401

        res = await ac.get("/api/v1/saas-billing/history")
        assert res.status_code == 401

        res = await ac.post("/api/v1/saas-billing/checkout", json={"plan_id": 1})
        assert res.status_code == 401

        res = await ac.get("/api/v1/saas-billing/upi/qr-code?plan_id=1")
        assert res.status_code == 401

        res = await ac.post("/api/v1/saas-billing/upi/submit", json={"transaction_reference": "TXN-1", "utr_reference": "UTR1234567890"})
        assert res.status_code == 401

        res = await ac.get("/api/v1/saas-billing/upi/transactions")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_tc02_missing_saas_billing_view_denied():
    """TC02: Authenticated user without saas_billing.view receives 403 Forbidden."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_noperms"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/me", headers=_auth(token))
            assert res.status_code == 403
            assert "saas_billing.view" in str(res.json().get("detail", ""))

            res = await ac.get("/api/v1/saas-billing/usage", headers=_auth(token))
            assert res.status_code == 403

            res = await ac.get("/api/v1/saas-billing/invoices", headers=_auth(token))
            assert res.status_code == 403

            res = await ac.get("/api/v1/saas-billing/history", headers=_auth(token))
            assert res.status_code == 403

            res = await ac.get("/api/v1/saas-billing/upi/transactions", headers=_auth(token))
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_tc03_saas_billing_view_grants_me():
    """TC03: saas_billing.view grants access to GET /me."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/me", headers=_auth(token))
            assert res.status_code == 200
            data = res.json()
            assert "plan_id" in data
            assert data["plan_id"] == d["plan_active"].id


@pytest.mark.asyncio
async def test_tc04_saas_billing_view_grants_usage():
    """TC04: saas_billing.view grants access to GET /usage."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/usage", headers=_auth(token))
            assert res.status_code == 200
            data = res.json()
            assert "entitlements" in data or "usage" in data


@pytest.mark.asyncio
async def test_tc05_saas_billing_view_grants_invoices():
    """TC05: saas_billing.view grants access to GET /invoices and GET /invoices/{id}."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/invoices", headers=_auth(token))
            assert res.status_code == 200
            invs = res.json()
            assert len(invs) >= 1
            assert any(i["id"] == d["inv_a"].id for i in invs)

            res_detail = await ac.get(f"/api/v1/saas-billing/invoices/{d['inv_a'].id}", headers=_auth(token))
            assert res_detail.status_code == 200
            assert res_detail.json()["id"] == d["inv_a"].id


@pytest.mark.asyncio
async def test_tc06_saas_billing_view_grants_history():
    """TC06: saas_billing.view grants access to GET /history."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/history", headers=_auth(token))
            assert res.status_code == 200
            assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_tc07_missing_saas_billing_create_denied_checkout():
    """TC07: Missing saas_billing.create returns 403 on POST /checkout."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        checkout_payload = {
            "plan_id": d["plan_active"].id,
            "success_url": "http://test/success",
            "cancel_url": "http://test/cancel",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post("/api/v1/saas-billing/checkout", json=checkout_payload, headers=_auth(token))
            assert res.status_code == 403
            assert "saas_billing.create" in str(res.json().get("detail", ""))


@pytest.mark.asyncio
async def test_tc08_saas_billing_create_allows_checkout():
    """TC08: saas_billing.create allows checkout initiation."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_creator"].id)
        checkout_payload = {
            "plan_id": d["plan_active"].id,
            "success_url": "http://test/success",
            "cancel_url": "http://test/cancel",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post("/api/v1/saas-billing/checkout", json=checkout_payload, headers=_auth(token))
            assert res.status_code == 200
            data = res.json()
            assert "checkout_url" in data


@pytest.mark.asyncio
async def test_tc09_saas_billing_view_allows_upi_qr_and_transactions():
    """TC09: saas_billing.view allows UPI QR generation and transaction inspection."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_qr = await ac.get(f"/api/v1/saas-billing/upi/qr-code?plan_id={d['plan_active'].id}", headers=_auth(token))
            assert res_qr.status_code == 200
            assert res_qr.json()["amount"] == float(d["plan_active"].price)

            res_list = await ac.get("/api/v1/saas-billing/upi/transactions", headers=_auth(token))
            assert res_list.status_code == 200
            assert any(t["transaction_reference"] == d["txn_ref_a"] for t in res_list.json())

            res_detail = await ac.get(f"/api/v1/saas-billing/upi/transactions/{d['txn_ref_a']}", headers=_auth(token))
            assert res_detail.status_code == 200
            assert res_detail.json()["transaction_reference"] == d["txn_ref_a"]


@pytest.mark.asyncio
async def test_tc10_saas_billing_create_allows_utr_submission():
    """TC10: saas_billing.create allows UTR submission and transaction remains pending."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_creator"].id)
        # Create a fresh pending transaction for submission test
        async with AsyncSessionLocal() as db:
            fresh_ref = f"TXN-FRESH-{uuid.uuid4().hex[:6].upper()}"
            fresh_txn = ManualPaymentTransaction(
                company_id=d["comp_a"].id,
                subscription_id=d["sub_a"].id,
                plan_id=d["plan_active"].id,
                amount=Decimal("1999.00"),
                currency="INR",
                payment_method="UPI",
                transaction_reference=fresh_ref,
                status="pending",
            )
            db.add(fresh_txn)
            await db.commit()

        new_utr = f"UTR{uuid.uuid4().hex[:8].upper()}9999"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/saas-billing/upi/submit",
                json={"transaction_reference": fresh_ref, "utr_reference": new_utr},
                headers=_auth(token),
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "pending"  # MUST remain pending
            assert data["utr_reference"] == new_utr


@pytest.mark.asyncio
async def test_tc11_dynamic_db_permission_grant():
    """TC11: Dynamic DB permission grant works without application restart."""
    async with setup_batch_ag_data() as d:
        user_id = d["user_a_noperms"].id
        token = _tok(user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Step 1: Initial call returns 403
            res1 = await ac.get("/api/v1/saas-billing/me", headers=_auth(token))
            assert res1.status_code == 403

            # Step 2: Grant permission in DB via override
            async with AsyncSessionLocal() as db:
                override = UserPermissionOverride(
                    user_id=user_id,
                    permission_id=d["perms"]["saas_billing.view"].id,
                    is_granted=True,
                )
                db.add(override)
                await db.commit()

            # Step 3: Next request succeeds without restart
            res2 = await ac.get("/api/v1/saas-billing/me", headers=_auth(token))
            assert res2.status_code == 200


@pytest.mark.asyncio
async def test_tc12_dynamic_db_permission_revoke():
    """TC12: Dynamic DB permission revoke works immediately on next request."""
    async with setup_batch_ag_data() as d:
        user_id = d["user_a_viewer"].id
        token = _tok(user_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Step 1: Initial call returns 200
            res1 = await ac.get("/api/v1/saas-billing/me", headers=_auth(token))
            assert res1.status_code == 200

            # Step 2: Revoke permission in DB via override
            async with AsyncSessionLocal() as db:
                override = UserPermissionOverride(
                    user_id=user_id,
                    permission_id=d["perms"]["saas_billing.view"].id,
                    is_granted=False,
                )
                db.add(override)
                await db.commit()

            # Step 3: Next request returns 403
            res2 = await ac.get("/api/v1/saas-billing/me", headers=_auth(token))
            assert res2.status_code == 403


@pytest.mark.asyncio
async def test_tc13_role_name_alone_does_not_grant_access():
    """TC13: Role name alone without DB permissions does not grant access."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_noperms"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/me", headers=_auth(token))
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_tc14_tenantless_non_sa_denied():
    """TC14: Tenantless non-SA user (company_id=None) returns 403 Forbidden."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_tenantless"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/me", headers=_auth(token))
            assert res.status_code == 403
            detail = res.json().get("detail", "")
            assert "belong to any company" in detail or "Tenant context" in detail


@pytest.mark.asyncio
async def test_tc15_foreign_invoice_idor_masked():
    """TC15: Foreign invoice IDOR returns 404 masked (generic not-found)."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/saas-billing/invoices/{d['inv_b'].id}", headers=_auth(token))
            assert res.status_code == 404
            assert "not found" in res.json().get("detail", "").lower()
            assert "tenant" not in res.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_tc16_foreign_upi_transaction_idor_masked():
    """TC16: Foreign UPI transaction IDOR returns 404 masked (generic not-found)."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/saas-billing/upi/transactions/{d['txn_ref_b']}", headers=_auth(token))
            assert res.status_code == 404
            assert "not found" in res.json().get("detail", "").lower()
            assert "tenant" not in res.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_tc17_nonexistent_invoice_404():
    """TC17: Nonexistent invoice returns 404."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/invoices/999999999", headers=_auth(token))
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_tc18_nonexistent_upi_reference_404():
    """TC18: Nonexistent UPI transaction reference returns 404."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_viewer"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/upi/transactions/TXN-NONEXISTENT", headers=_auth(token))
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_tc19_duplicate_utr_rejected():
    """TC19: Submitting existing UTR returns 400 Bad Request."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_creator"].id)
        second_ref = f"TXN-SECOND-{uuid.uuid4().hex[:6].upper()}"
        async with AsyncSessionLocal() as db:
            second_txn = ManualPaymentTransaction(
                company_id=d["comp_a"].id,
                subscription_id=d["sub_a"].id,
                plan_id=d["plan_active"].id,
                amount=Decimal("1999.00"),
                currency="INR",
                payment_method="UPI",
                transaction_reference=second_ref,
                status="pending",
            )
            db.add(second_txn)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Try to submit existing utr_a on second transaction
            res = await ac.post(
                "/api/v1/saas-billing/upi/submit",
                json={"transaction_reference": second_ref, "utr_reference": d["utr_a"]},
                headers=_auth(token),
            )
            assert res.status_code == 400
            assert "already been submitted" in res.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_tc20_invalid_utr_rejected():
    """TC20: Submitting malformed or too short UTR returns 400 Bad Request."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_a_creator"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Too short (< 6 chars)
            res1 = await ac.post(
                "/api/v1/saas-billing/upi/submit",
                json={"transaction_reference": d["txn_ref_a"], "utr_reference": "123"},
                headers=_auth(token),
            )
            assert res1.status_code == 400

            # Special characters
            res2 = await ac.post(
                "/api/v1/saas-billing/upi/submit",
                json={"transaction_reference": d["txn_ref_a"], "utr_reference": "UTR@#$%^&*()"},
                headers=_auth(token),
            )
            assert res2.status_code == 400


@pytest.mark.asyncio
async def test_tc21_plans_endpoint_remains_public():
    """TC21: GET /plans remains publicly accessible without authentication."""
    async with setup_batch_ag_data() as d:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/saas-billing/plans")
            assert res.status_code == 200
            plans = res.json()
            assert isinstance(plans, list)
            # Must return active plans
            plan_ids = [p["id"] for p in plans]
            assert d["plan_active"].id in plan_ids
            # Must not return inactive plans
            assert d["plan_inactive"].id not in plan_ids


@pytest.mark.asyncio
async def test_tc22_webhook_signature_validation():
    """TC22: Webhook rejects invalid signatures and accepts valid signatures."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        evt_bad = f"evt_bad_{uuid.uuid4().hex[:8]}"
        res_bad = await ac.post(
            "/api/v1/saas-billing/webhook",
            json={"id": evt_bad, "type": "test_event"},
            headers={"X-Mock-Signature": "invalid_signature"},
        )
        assert res_bad.status_code in (400, 401, 403)

        evt_good = f"evt_good_{uuid.uuid4().hex[:8]}"
        res_good = await ac.post(
            "/api/v1/saas-billing/webhook",
            json={"id": evt_good, "type": "test_event"},
            headers={"X-Mock-Signature": "mock_valid_signature"},
        )
        assert res_good.status_code == 200


@pytest.mark.asyncio
async def test_tc23_super_admin_global_access():
    """TC23: Super Admin has global visibility across tenants."""
    async with setup_batch_ag_data() as d:
        token = _tok(d["user_sa"].id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # SA can list all invoices across tenants
            res_inv = await ac.get("/api/v1/saas-billing/invoices", headers=_auth(token))
            assert res_inv.status_code == 200
            inv_ids = [i["id"] for i in res_inv.json()]
            assert d["inv_a"].id in inv_ids
            assert d["inv_b"].id in inv_ids

            # SA can inspect detail of any invoice
            res_inv_detail = await ac.get(f"/api/v1/saas-billing/invoices/{d['inv_b'].id}", headers=_auth(token))
            assert res_inv_detail.status_code == 200
            assert res_inv_detail.json()["id"] == d["inv_b"].id

            # SA can list transactions globally
            res_txn = await ac.get("/api/v1/saas-billing/upi/transactions", headers=_auth(token))
            assert res_txn.status_code == 200
            refs = [t["transaction_reference"] for t in res_txn.json()]
            assert d["txn_ref_a"] in refs
            assert d["txn_ref_b"] in refs

            # SA can inspect detail of any transaction
            res_txn_detail = await ac.get(f"/api/v1/saas-billing/upi/transactions/{d['txn_ref_b']}", headers=_auth(token))
            assert res_txn_detail.status_code == 200
            assert res_txn_detail.json()["transaction_reference"] == d["txn_ref_b"]


def test_tc24_route_preservation():
    """TC24: Verify exactly 14 SaaS billing routes and 781 total routes."""
    from fastapi.routing import APIRoute

    saas_routes = [
        r for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1/saas-billing")
    ]
    assert len(saas_routes) == 14, f"Expected 14 SaaS billing routes, got {len(saas_routes)}"

    all_routes = [r for r in app.routes if isinstance(r, APIRoute)]
    assert len(all_routes) == 781, f"Expected 781 total routes, got {len(all_routes)}"

    unique_routes = set((list(r.methods)[0], r.path) for r in all_routes)
    assert len(unique_routes) == 781, f"Expected 781 unique routes, got {len(unique_routes)}"


def test_tc25_static_code_hygiene():
    """TC25: Verify saas_billing.py has 0 obsolete auth dependencies and uses canonical checks."""
    import inspect
    from app.api import saas_billing

    source = inspect.getsource(saas_billing)
    assert "require_tenant_admin" not in source
    assert "require_roles" not in source
    assert "admin_required" not in source
    assert "UserRole.ADMIN" not in source
    assert "current_user.role ==" not in source
    assert 'getattr(current_user, "is_super_admin", False) is True' in source
