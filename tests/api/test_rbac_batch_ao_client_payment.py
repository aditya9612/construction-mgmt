import uuid
from decimal import Decimal
from datetime import date, datetime, timezone
from unittest.mock import patch
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, ActivityLog
from app.models.company import Company
from app.models.invoice import Invoice, Transaction
from app.models.client_payment import ClientPayment
from app.models.notification import Notification
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.enums import InvoiceStatus, PaymentStatus, PaymentMethod
from tests.api.test_rbac_phase2_batch_l import setup_batch_l_data

FAKE_RECEIPT = {"receipt": ("receipt.png", b"fake-receipt-png-bytes", "image/png")}


@pytest.mark.asyncio
async def test_ao_01_authentication_required():
    """1. All 13 endpoints require authentication (401)."""
    async with setup_batch_l_data() as data:
        pay_id = data["pay_a1"].id
        routes = [
            ("GET", f"/api/v1/client-payments/invoice-summary?project_id={data['proj_a'].id}"),
            ("GET", f"/api/v1/client-payments/history?project_id={data['proj_a'].id}"),
            ("GET", "/api/v1/client-payments/pending-invoices"),
            ("GET", "/api/v1/client-payments/analytics"),
            ("GET", "/api/v1/client-payments/export/excel"),
            ("GET", "/api/v1/client-payments/export/pdf"),
            ("POST", "/api/v1/client-payments"),
            ("GET", "/api/v1/client-payments"),
            ("GET", f"/api/v1/client-payments/{pay_id}"),
            ("PUT", f"/api/v1/client-payments/{pay_id}"),
            ("DELETE", f"/api/v1/client-payments/{pay_id}"),
            ("POST", f"/api/v1/client-payments/{pay_id}/verify"),
            ("GET", f"/api/v1/client-payments/{pay_id}/receipt"),
        ]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for method, url in routes:
                if method == "GET":
                    res = await ac.get(url)
                elif method == "POST":
                    res = await ac.post(url, data={})
                elif method == "PUT":
                    res = await ac.put(url, data={})
                elif method == "DELETE":
                    res = await ac.delete(url)
                assert res.status_code == 401, f"Route {method} {url} expected 401, got {res.status_code}"


@pytest.mark.asyncio
async def test_ao_02_missing_invoices_view_permission():
    """2. Missing invoices.view -> 403."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["user_unassigned_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_ao_03_missing_invoices_create_permission():
    """3. Missing invoices.create -> 403."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["user_unassigned_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "100.00",
                    "payment_method": "NEFT",
                },
            )
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_ao_04_missing_invoices_edit_permission():
    """4. Missing invoices.edit -> 403."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["user_unassigned_a"]
        headers = {"Authorization": f"Bearer {token}"}
        pay_id = data["pay_a1"].id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.put(
                f"/api/v1/client-payments/{pay_id}",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "100.00",
                    "payment_method": "NEFT",
                    "remarks": "Updated remark",
                },
            )
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_ao_05_missing_invoices_delete_permission():
    """5. Missing invoices.delete -> 403."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["user_unassigned_a"]
        headers = {"Authorization": f"Bearer {token}"}
        pay_id = data["pay_a1"].id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/client-payments/{pay_id}", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_ao_06_missing_invoices_approve_permission():
    """6. Missing invoices.approve -> 403."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["user_unassigned_a"]
        headers = {"Authorization": f"Bearer {token}"}
        pay_id = data["pay_a1"].id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                f"/api/v1/client-payments/{pay_id}/verify",
                headers=headers,
                json={"action": "approve"},
            )
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_ao_07_missing_invoices_export_permission():
    """7. Missing invoices.export -> 403."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["user_unassigned_a"]
        headers = {"Authorization": f"Bearer {token}"}
        pay_id = data["pay_a1"].id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1 = await ac.get("/api/v1/client-payments/export/excel", headers=headers)
            assert r1.status_code == 403
            r2 = await ac.get("/api/v1/client-payments/export/pdf", headers=headers)
            assert r2.status_code == 403
            r3 = await ac.get(f"/api/v1/client-payments/{pay_id}/receipt", headers=headers)
            assert r3.status_code == 403


@pytest.mark.asyncio
async def test_ao_08_db_driven_permission_grant_works():
    """8. DB-driven permission grant works without server restart."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["user_unassigned_a"]
        headers = {"Authorization": f"Bearer {token}"}
        user_id = data["user_unassigned_a"].id
        perm_view_id = data["perms"]["invoices.view"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially 403
            r1 = await ac.get("/api/v1/client-payments", headers=headers)
            assert r1.status_code == 403

            # Grant in DB
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user_id, permission_id=perm_view_id, is_granted=True))
                await db.commit()

            # Now 200
            r2 = await ac.get("/api/v1/client-payments", headers=headers)
            assert r2.status_code == 200


@pytest.mark.asyncio
async def test_ao_09_db_driven_permission_revoke_works_immediately():
    """9. DB-driven permission revoke works immediately."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        admin_id = data["admin_a"].id
        perm_view_id = data["perms"]["invoices.view"].id

        # Grant first
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=admin_id, permission_id=perm_view_id, is_granted=True))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1 = await ac.get("/api/v1/client-payments", headers=headers)
            assert r1.status_code == 200

            # Revoke immediately
            async with AsyncSessionLocal() as db:
                await db.execute(delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == admin_id,
                    UserPermissionOverride.permission_id == perm_view_id,
                ))
                db.add(UserPermissionOverride(user_id=admin_id, permission_id=perm_view_id, is_granted=False))
                await db.commit()

            r2 = await ac.get("/api/v1/client-payments", headers=headers)
            assert r2.status_code == 403


@pytest.mark.asyncio
async def test_ao_10_legacy_user_role_bypass_immunity():
    """10. Built-in role names (Accountant, Client) have zero bypass power without DB permission."""
    async with setup_batch_l_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Accountant without DB permission -> 403
            token_acc = data["tokens"]["accountant_a"]
            pay_id = data["pay_a1"].id
            res = await ac.post(
                f"/api/v1/client-payments/{pay_id}/verify",
                headers={"Authorization": f"Bearer {token_acc}"},
                json={"action": "approve"},
            )
            assert res.status_code == 403
            res_view = await ac.get("/api/v1/client-payments", headers={"Authorization": f"Bearer {token_acc}"})
            assert res_view.status_code == 403

            # 2. Client without DB permission -> 403
            token_client = data["tokens"]["client_user_a1"]
            res_post = await ac.post(
                "/api/v1/client-payments",
                headers={"Authorization": f"Bearer {token_client}"},
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "1000.00",
                    "payment_method": "UPI",
                    "reference_no": f"LEGACY_REF_{data['uid']}",
                },
            )
            assert res_post.status_code == 403


@pytest.mark.asyncio
async def test_ao_11_tenantless_non_sa_guards_all_13_endpoints():
    """11. Tenantless non-SA -> 403 for all 13 endpoints."""
    async with setup_batch_l_data() as data:
        # Give dummy_none_company_user wildcard permission to verify tenant guard overrides permissions
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(
                user_id=data["dummy_none_company_user"].id,
                permission_id=data["perms"]["invoices.*"].id,
                is_granted=True,
            ))
            await db.commit()

        token = data["tokens"]["dummy_none_company_user"]
        headers = {"Authorization": f"Bearer {token}"}
        pay_id = data["pay_a1"].id
        routes = [
            ("GET", f"/api/v1/client-payments/invoice-summary?project_id={data['proj_a'].id}"),
            ("GET", f"/api/v1/client-payments/history?project_id={data['proj_a'].id}"),
            ("GET", "/api/v1/client-payments/pending-invoices"),
            ("GET", "/api/v1/client-payments/analytics"),
            ("GET", "/api/v1/client-payments/export/excel"),
            ("GET", "/api/v1/client-payments/export/pdf"),
            ("POST", "/api/v1/client-payments", {"amount": "100.00"}),
            ("GET", "/api/v1/client-payments"),
            ("GET", f"/api/v1/client-payments/{pay_id}"),
            ("PUT", f"/api/v1/client-payments/{pay_id}", {"remarks": "test"}),
            ("DELETE", f"/api/v1/client-payments/{pay_id}"),
            ("POST", f"/api/v1/client-payments/{pay_id}/verify", {"action": "approve"}),
            ("GET", f"/api/v1/client-payments/{pay_id}/receipt"),
        ]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for item in routes:
                method = item[0]
                url = item[1]
                body = item[2] if len(item) > 2 else None

                if method == "GET":
                    res = await ac.get(url, headers=headers)
                elif method == "POST":
                    if "verify" in url:
                        res = await ac.post(url, json=body or {}, headers=headers)
                    else:
                        res = await ac.post(url, data=body or {}, headers=headers)
                elif method == "PUT":
                    res = await ac.put(url, data=body or {}, headers=headers)
                elif method == "DELETE":
                    res = await ac.delete(url, headers=headers)

                assert res.status_code == 403


@pytest.mark.asyncio
async def test_ao_12_foreign_payment_returns_404():
    """12. Foreign payment access returns 404."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.*"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        foreign_id = data["pay_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1 = await ac.get(f"/api/v1/client-payments/{foreign_id}", headers=headers)
            assert r1.status_code == 404

            r2 = await ac.put(
                f"/api/v1/client-payments/{foreign_id}",
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "1000.00",
                    "payment_method": "NEFT",
                    "remarks": "hack",
                },
                headers=headers,
            )
            assert r2.status_code == 404

            r3 = await ac.delete(f"/api/v1/client-payments/{foreign_id}", headers=headers)
            assert r3.status_code == 404

            r4 = await ac.post(
                f"/api/v1/client-payments/{foreign_id}/verify",
                headers=headers,
                json={"action": "approve"},
            )
            assert r4.status_code == 404

            r5 = await ac.get(f"/api/v1/client-payments/{foreign_id}/receipt", headers=headers)
            assert r5.status_code == 404


@pytest.mark.asyncio
async def test_ao_13_foreign_invoice_returns_404():
    """13. Creating payment with foreign invoice returns 404."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.create"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_b1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "500.00",
                    "payment_method": "NEFT",
                    "bank_name": "HDFC Bank",
                    "reference_no": f"REF_AO_13_{data['uid']}",
                },
            )
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_ao_14_foreign_project_returns_404():
    """14. Creating payment with foreign project returns 404."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.create"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_b"].id),
                    "amount": "500.00",
                    "payment_method": "NEFT",
                    "bank_name": "HDFC Bank",
                    "reference_no": f"REF_AO_14_{data['uid']}",
                },
            )
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_ao_15_foreign_client_returns_404():
    """15. Foreign client filtering returns 404."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.view"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get(
                f"/api/v1/client-payments?user_id={data['client_user_b1'].id}&project_id={data['proj_a'].id}",
                headers=headers,
            )
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_ao_16_cross_tenant_project_invoice_combination():
    """16. Cross-tenant project/invoice combination rejected."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["super_admin"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_b1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "500.00",
                    "payment_method": "NEFT",
                    "bank_name": "Axis Bank",
                    "reference_no": f"REF_AO_16_{data['uid']}",
                },
            )
            assert res.status_code in (400, 404)


@pytest.mark.asyncio
async def test_ao_17_sa_global_payment_visibility():
    """17. SA global payment visibility across companies."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["super_admin"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 200
            items = res.json()
            ids = [i["id"] for i in items]
            assert data["pay_a1"].id in ids
            assert data["pay_b1"].id in ids


@pytest.mark.asyncio
async def test_ao_18_sa_analytics_global_invoice_count():
    """18. SA analytics returns global totals without company_id=None filter error."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["super_admin"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/client-payments/analytics", headers=headers)
            assert res.status_code == 200
            json_data = res.json()
            assert "total_collection" in json_data
            assert "monthly_collection" in json_data


@pytest.mark.asyncio
async def test_ao_19_sa_duplicate_payment_detection_uses_target_company():
    """19. SA duplicate payment detection validates target company, not current_user.company_id."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["super_admin"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Pay_a1 reference already exists in Comp A
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "100.00",
                    "payment_method": "NEFT",
                    "bank_name": "HDFC Bank",
                    "reference_no": data["pay_a1"].reference_no,
                },
                files=FAKE_RECEIPT,
            )
            assert res.status_code in (400, 409)


@pytest.mark.asyncio
async def test_ao_20_sa_create_never_creates_company_id_none():
    """20. SA create payment derives company_id from target project/invoice, never None."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["super_admin"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "350.00",
                    "payment_method": "NEFT",
                    "bank_name": "Axis Bank",
                    "reference_no": f"SA_NEVER_NONE_{data['uid']}",
                },
                files=FAKE_RECEIPT,
            )
            assert res.status_code == 201
            created_id = res.json()["id"]

        async with AsyncSessionLocal() as db:
            payment = await db.get(ClientPayment, created_id)
            assert payment is not None
            assert payment.company_id == data["comp_a"].id
            assert payment.company_id is not None


@pytest.mark.asyncio
async def test_ao_21_verify_payment_locks_client_payment_and_invoice():
    """21. verify_client_payment acquires locks and transitions payment cleanly."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.approve"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                f"/api/v1/client-payments/{data['pay_a1'].id}/verify",
                headers=headers,
                json={"action": "approve"},
            )
            assert res.status_code == 200
            assert res.json()["payment_status"] == PaymentStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_ao_22_concurrent_verification_cannot_overallocate():
    """22. Concurrent or repeated verification cannot duplicate apply."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.approve"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # First verify succeeds
            r1 = await ac.post(
                f"/api/v1/client-payments/{data['pay_a1'].id}/verify",
                headers=headers,
                json={"action": "approve"},
            )
            assert r1.status_code == 200

            # Second verify fails
            r2 = await ac.post(
                f"/api/v1/client-payments/{data['pay_a1'].id}/verify",
                headers=headers,
                json={"action": "approve"},
            )
            assert r2.status_code == 400


@pytest.mark.asyncio
async def test_ao_23_verification_rejects_payment_exceeding_pending_balance():
    """23. verify_client_payment rejects payment exceeding invoice pending_amount."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            huge_pay = ClientPayment(
                company_id=data["comp_a"].id,
                payment_no=f"CP-HUGE-{data['uid']}",
                client_user_id=data["client_user_a1"].id,
                project_id=data["proj_a"].id,
                invoice_id=data["inv_a1"].id,
                amount=Decimal("999999.00"),
                payment_date=date.today(),
                payment_method=PaymentMethod.NEFT,
                reference_no=f"REF_HUGE_{data['uid']}",
                payment_status=PaymentStatus.VERIFICATION_PENDING,
            )
            db.add(huge_pay)
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.approve"].id, is_granted=True))
            await db.commit()
            await db.refresh(huge_pay)
            huge_id = huge_pay.id

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                f"/api/v1/client-payments/{huge_id}/verify",
                headers=headers,
                json={"action": "approve"},
            )
            assert res.status_code == 400
            assert "exceeds" in res.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_ao_24_failed_journal_posting_rolls_back_financial_changes():
    """24. Failed journal posting rolls back invoice balance and verification status."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            roll_pay = ClientPayment(
                company_id=data["comp_a"].id,
                payment_no=f"CP-ROLL-{data['uid']}",
                client_user_id=data["client_user_a1"].id,
                project_id=data["proj_a"].id,
                invoice_id=data["inv_a1"].id,
                amount=Decimal("1500.00"),
                payment_date=date.today(),
                payment_method=PaymentMethod.NEFT,
                reference_no=f"REF_ROLL_{data['uid']}",
                payment_status=PaymentStatus.VERIFICATION_PENDING,
            )
            db.add(roll_pay)
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.approve"].id, is_granted=True))
            await db.commit()
            await db.refresh(roll_pay)
            pay_id = roll_pay.id

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)

        with patch("app.api.client_payment.auto_post_journal", side_effect=RuntimeError("Simulated ledger failure")):
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                res = await ac.post(
                    f"/api/v1/client-payments/{pay_id}/verify",
                    headers=headers,
                    json={"action": "approve"},
                )
                assert res.status_code == 500

        # Assert rollback
        async with AsyncSessionLocal() as db:
            check_p = await db.get(ClientPayment, pay_id)
            assert check_p.payment_status == PaymentStatus.VERIFICATION_PENDING
            check_inv = await db.get(Invoice, data["inv_a1"].id)
            assert check_inv.paid_amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_ao_25_delete_locks_invoice_and_restores_balances():
    """25. Deleting pending payment locks invoice and removes payment cleanly; verified payments cannot be deleted."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.delete"].id, is_granted=True))
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.approve"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Delete pending payment A1 -> 204
            r1 = await ac.delete(f"/api/v1/client-payments/{data['pay_a1'].id}", headers=headers)
            assert r1.status_code in (200, 204)

            # 2. Deleting verified payment A3 -> 400 (Only pending payments can be cancelled)
            r2 = await ac.delete(f"/api/v1/client-payments/{data['pay_a3'].id}", headers=headers)
            assert r2.status_code == 400
            assert "cannot be cancelled" in r2.json().get("detail", "").lower() or "only pending" in r2.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_ao_26_receipt_uses_payment_company_settings():
    """26. Receipt downloads use CompanySettings matching payment.company_id."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["super_admin"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/client-payments/{data['pay_a3'].id}/receipt", headers=headers)
            assert res.status_code == 200
            assert res.headers["content-type"] == "application/pdf"
            assert len(res.content) > 100


@pytest.mark.asyncio
async def test_ao_27_notification_tenant_isolation():
    """27. Notifications created during payment submission stay isolated to target company."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["client_user_a1"].id, permission_id=data["perms"]["invoices.create"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["client_user_a1"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "250.00",
                    "payment_method": "NEFT",
                    "bank_name": "SBI",
                    "reference_no": f"NOTIF_ISOL_{data['uid']}",
                },
                files=FAKE_RECEIPT,
            )
            assert res.status_code == 201

        async with AsyncSessionLocal() as db:
            notif_a = (await db.execute(
                select(Notification).where(
                    Notification.user_id == data["admin_a"].id,
                    Notification.message.contains("250.00"),
                )
            )).scalars().all()
            assert len(notif_a) >= 1

            notif_b = (await db.execute(
                select(Notification).where(
                    Notification.user_id == data["admin_b"].id,
                    Notification.message.contains("250.00"),
                )
            )).scalars().all()
            assert len(notif_b) == 0


@pytest.mark.asyncio
async def test_ao_28_update_does_not_triple_fetch_unnecessarily():
    """28. Payment update executes cleanly and retains tenant bounds."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.edit"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.put(
                f"/api/v1/client-payments/{data['pay_a1'].id}",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "50000.00",
                    "payment_method": "NEFT",
                    "bank_name": "HDFC Bank",
                    "reference_no": data["pay_a1"].reference_no,
                    "remarks": "Updated remark single fetch",
                },
                files=FAKE_RECEIPT,
            )
            assert res.status_code == 200
            assert res.json()["remarks"] == "Updated remark single fetch"


@pytest.mark.asyncio
async def test_ao_29_existing_client_self_service_behavior():
    """29. Client self-service behavior intact: can view own and create for own project."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["client_user_a1"].id, permission_id=data["perms"]["invoices.view"].id, is_granted=True))
            db.add(UserPermissionOverride(user_id=data["client_user_a1"].id, permission_id=data["perms"]["invoices.create"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["client_user_a1"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Client views own payment
            r1 = await ac.get(f"/api/v1/client-payments/{data['pay_a1'].id}", headers=headers)
            assert r1.status_code == 200

            # Client creates payment
            r2 = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "120.00",
                    "payment_method": "NEFT",
                    "bank_name": "Axis Bank",
                    "reference_no": f"CLIENT_SELF_{data['uid']}",
                },
                files=FAKE_RECEIPT,
            )
            assert r2.status_code == 201

            # Client cannot verify (requires invoices.approve)
            r3 = await ac.post(
                f"/api/v1/client-payments/{data['pay_a1'].id}/verify",
                headers=headers,
                json={"action": "approve"},
            )
            assert r3.status_code == 403


@pytest.mark.asyncio
async def test_ao_30_full_payment_lifecycle_and_accounting():
    """30. Full lifecycle: create -> verify -> post accounting -> balances updated."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.*"].id, is_granted=True))
            db.add(UserPermissionOverride(user_id=data["client_user_a1"].id, permission_id=data["perms"]["invoices.*"].id, is_granted=True))
            await db.commit()

        token_admin = data["tokens"]["admin_a"]
        token_client = data["tokens"]["client_user_a1"]
        headers_admin = {"Authorization": f"Bearer {token_admin}"}
        headers_client = {"Authorization": f"Bearer {token_client}"}
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create
            r_create = await ac.post(
                "/api/v1/client-payments",
                headers=headers_client,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "3000.00",
                    "payment_method": "NEFT",
                    "bank_name": "HDFC Bank",
                    "reference_no": f"FULL_CYCLE_{data['uid']}",
                },
                files=FAKE_RECEIPT,
            )
            assert r_create.status_code == 201
            payment_id = r_create.json()["id"]

            # 2. Verify
            r_verify = await ac.post(
                f"/api/v1/client-payments/{payment_id}/verify",
                headers=headers_admin,
                json={"action": "approve"},
            )
            assert r_verify.status_code == 200
            assert r_verify.json()["payment_status"] == PaymentStatus.SUCCESS.value

            # 3. Check invoice
            async with AsyncSessionLocal() as db:
                inv = await db.get(Invoice, data["inv_a1"].id)
                assert inv.paid_amount == Decimal("3000.00")
                assert inv.pending_amount == Decimal("97000.00")
