import uuid
from decimal import Decimal
from datetime import date, datetime, timezone
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, ActivityLog, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.settings import CompanySettings
from app.models.invoice import Invoice, Transaction
from app.models.client_payment import ClientPayment
from app.models.notification import Notification
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import (
    InvoiceStatus,
    InvoiceType,
    PaymentMethod,
    PaymentStatus,
    ProjectStatus,
)


@asynccontextmanager
async def setup_batch_l_data():
    """Seed test companies, projects, invoices, client payments, and users for Batch L."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchL_CompA_{uid}")
        comp_b = Company(name=f"BatchL_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company settings
        cs_a = CompanySettings(company_id=comp_a.id, company_name=f"Brand_Company_A_{uid}")
        cs_b = CompanySettings(company_id=comp_b.id, company_name=f"Brand_Company_B_{uid}")
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_l_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin L",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_la_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin L",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_lb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin L",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        accountant_a = User(
            email=f"accountant_la_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Accountant L",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Accountant",
        )

        custom_role_name = f"PaymentOfficer_{uid}"
        user_custom_a = User(
            email=f"custom_la_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Payment Officer L",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        user_unassigned_a = User(
            email=f"unassigned_la_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Unassigned Officer L",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        client_user_a1 = User(
            email=f"client_la1_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Client User A1",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Client",
        )
        client_user_a2 = User(
            email=f"client_la2_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Client User A2",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Client",
        )
        client_user_b1 = User(
            email=f"client_lb1_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Client User B1",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Client",
        )

        dummy_none_company_user = User(
            email=f"nonecomp_l_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="None Comp User L",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Staff",
        )

        db.add_all([
            super_admin,
            admin_a,
            admin_b,
            accountant_a,
            user_custom_a,
            user_unassigned_a,
            client_user_a1,
            client_user_a2,
            client_user_b1,
            dummy_none_company_user,
        ])
        await db.flush()

        # 4. Owners & Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-LA-{uid}",
            owner_name="Owner A",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-LB-{uid}",
            owner_name="Owner B",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(
            business_id=f"PRJ-LA-{uid}",
            project_name=f"Project LA {uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b = Project(
            business_id=f"PRJ-LB-{uid}",
            project_name=f"Project LB {uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 5. Project Memberships
        pm_a1 = ProjectMember(project_id=proj_a.id, user_id=client_user_a1.id)
        pm_b1 = ProjectMember(project_id=proj_b.id, user_id=client_user_b1.id)
        pm_custom_a = ProjectMember(project_id=proj_a.id, user_id=user_custom_a.id)
        db.add_all([pm_a1, pm_b1, pm_custom_a])
        await db.flush()

        # 6. Invoices
        inv_a1 = Invoice(
            company_id=comp_a.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type=InvoiceType.OWNER,
            status=InvoiceStatus.PENDING,
            amount=Decimal("100000.00"),
            total_amount=Decimal("100000.00"),
            paid_amount=Decimal("0.00"),
            pending_amount=Decimal("100000.00"),
            description="Invoice A1 for Construction Milestone 1",
        )
        inv_a2 = Invoice(
            company_id=comp_a.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type=InvoiceType.OWNER,
            status=InvoiceStatus.PARTIAL,
            amount=Decimal("50000.00"),
            total_amount=Decimal("50000.00"),
            paid_amount=Decimal("20000.00"),
            pending_amount=Decimal("30000.00"),
            description="Invoice A2 for Electrical Milestone",
        )
        inv_b1 = Invoice(
            company_id=comp_b.id,
            project_id=proj_b.id,
            owner_id=owner_b.id,
            type=InvoiceType.OWNER,
            status=InvoiceStatus.PENDING,
            amount=Decimal("200000.00"),
            total_amount=Decimal("200000.00"),
            paid_amount=Decimal("0.00"),
            pending_amount=Decimal("200000.00"),
            description="Invoice B1 for Company B Infrastructure",
        )
        db.add_all([inv_a1, inv_a2, inv_b1])
        await db.flush()

        # 7. Client Payments
        # Payment A1: verification pending
        pay_a1 = ClientPayment(
            company_id=comp_a.id,
            payment_no=f"CP-A1-{uid}",
            client_user_id=client_user_a1.id,
            project_id=proj_a.id,
            invoice_id=inv_a1.id,
            amount=Decimal("25000.00"),
            payment_method=PaymentMethod.NEFT,
            payment_status=PaymentStatus.VERIFICATION_PENDING,
            reference_no=f"REF-A1-{uid}",
            remarks="Initial 25k payment towards milestone 1",
        )
        # Payment A2: pending (editable/cancellable)
        pay_a2 = ClientPayment(
            company_id=comp_a.id,
            payment_no=f"CP-A2-{uid}",
            client_user_id=client_user_a1.id,
            project_id=proj_a.id,
            invoice_id=inv_a1.id,
            amount=Decimal("15000.00"),
            payment_method=PaymentMethod.CHEQUE,
            payment_status=PaymentStatus.PENDING,
            cheque_no=f"CHQ-A2-{uid}",
            bank_name="HDFC Bank",
            remarks="Cheque payment 15k",
        )
        # Payment A3: verified/success (receipt ready)
        pay_a3 = ClientPayment(
            company_id=comp_a.id,
            payment_no=f"CP-A3-{uid}",
            client_user_id=client_user_a1.id,
            project_id=proj_a.id,
            invoice_id=inv_a2.id,
            amount=Decimal("20000.00"),
            payment_method=PaymentMethod.RTGS,
            payment_status=PaymentStatus.SUCCESS,
            reference_no=f"REF-A3-{uid}",
            remarks="Verified RTGS payment 20k",
            verified_by=admin_a.id,
            verified_at=datetime.now(timezone.utc),
            payment_date=datetime.now(timezone.utc),
        )
        # Payment B1: Company B payment
        pay_b1 = ClientPayment(
            company_id=comp_b.id,
            payment_no=f"CP-B1-{uid}",
            client_user_id=client_user_b1.id,
            project_id=proj_b.id,
            invoice_id=inv_b1.id,
            amount=Decimal("30000.00"),
            payment_method=PaymentMethod.UPI,
            payment_status=PaymentStatus.VERIFICATION_PENDING,
            reference_no=f"UPI-B1-{uid}",
            remarks="Company B UPI payment",
        )
        db.add_all([pay_a1, pay_a2, pay_a3, pay_b1])
        await db.flush()

        # 8. RBAC Role
        role_custom = Role(
            name=custom_role_name,
            display_name="Payment Officer",
            company_id=comp_a.id,
        )
        db.add(role_custom)
        await db.flush()

        # 9. Ensure RBAC Permissions in catalog
        perm_codes = [
            "invoices.view",
            "invoices.create",
            "invoices.edit",
            "invoices.delete",
            "invoices.approve",
            "invoices.export",
            "invoices.*",
        ]
        perms = {}
        for code in perm_codes:
            p = (await db.execute(select(Permission).where(Permission.code == code))).scalar_one_or_none()
            if not p:
                parts = code.split(".")
                p = Permission(module=parts[0], action=parts[1], code=code, description=f"{code} permission")
                db.add(p)
                await db.flush()
            perms[code] = p

        await db.commit()

        # Auth tokens
        tokens = {
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "accountant_a": create_access_token({"sub": str(accountant_a.id)}),
            "user_custom_a": create_access_token({"sub": str(user_custom_a.id)}),
            "user_unassigned_a": create_access_token({"sub": str(user_unassigned_a.id)}),
            "client_user_a1": create_access_token({"sub": str(client_user_a1.id)}),
            "client_user_a2": create_access_token({"sub": str(client_user_a2.id)}),
            "client_user_b1": create_access_token({"sub": str(client_user_b1.id)}),
            "dummy_none_company_user": create_access_token({"sub": str(dummy_none_company_user.id)}),
        }

        data = {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "super_admin": super_admin,
            "admin_a": admin_a,
            "admin_b": admin_b,
            "accountant_a": accountant_a,
            "user_custom_a": user_custom_a,
            "user_unassigned_a": user_unassigned_a,
            "client_user_a1": client_user_a1,
            "client_user_a2": client_user_a2,
            "client_user_b1": client_user_b1,
            "dummy_none_company_user": dummy_none_company_user,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "inv_a1": inv_a1,
            "inv_a2": inv_a2,
            "inv_b1": inv_b1,
            "pay_a1": pay_a1,
            "pay_a2": pay_a2,
            "pay_a3": pay_a3,
            "pay_b1": pay_b1,
            "role_custom": role_custom,
            "perms": perms,
            "tokens": tokens,
        }

        try:
            yield data
        finally:
            async with AsyncSessionLocal() as clean_db:
                # Cleanup test records
                c_ids = [comp_a.id, comp_b.id]
                u_ids = [
                    super_admin.id, admin_a.id, admin_b.id, accountant_a.id,
                    user_custom_a.id, user_unassigned_a.id, client_user_a1.id,
                    client_user_a2.id, client_user_b1.id, dummy_none_company_user.id
                ]
                await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(u_ids)))
                await clean_db.execute(delete(RolePermission).where(RolePermission.role_id == role_custom.id))
                await clean_db.execute(delete(Role).where(Role.id == role_custom.id))
                await clean_db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(u_ids)))
                await clean_db.execute(delete(Notification).where(Notification.user_id.in_(u_ids)))
                await clean_db.execute(delete(Transaction).where(Transaction.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(ClientPayment).where(ClientPayment.company_id.in_(c_ids)))
                await clean_db.execute(delete(Invoice).where(Invoice.company_id.in_(c_ids)))
                await clean_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(Project).where(Project.company_id.in_(c_ids)))
                await clean_db.execute(delete(Owner).where(Owner.company_id.in_(c_ids)))
                await clean_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_(c_ids)))
                await clean_db.execute(delete(User).where(User.id.in_(u_ids)))
                await clean_db.execute(delete(Company).where(Company.id.in_(c_ids)))
                await clean_db.commit()


@pytest.mark.asyncio
async def test_batch_l_authentication_required():
    """Requirement A: All 13 routes return 401 without authentication."""
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
                    res = await ac.post(url, json={})
                elif method == "PUT":
                    res = await ac.put(url, json={})
                elif method == "DELETE":
                    res = await ac.delete(url)
                assert res.status_code == 401, f"Route {method} {url} expected 401, got {res.status_code}"


@pytest.mark.asyncio
async def test_batch_l_permission_denial():
    """Requirement B: Users without required permission get 403."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["user_unassigned_a"]
        headers = {"Authorization": f"Bearer {token}"}
        pay_id = data["pay_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. View route
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 403

            # 2. Summary
            res = await ac.get(f"/api/v1/client-payments/invoice-summary?project_id={data['proj_a'].id}", headers=headers)
            assert res.status_code == 403

            # 3. Create
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "1000.00",
                    "payment_method": "neft",
                },
            )
            assert res.status_code == 403

            # 4. Edit
            res = await ac.put(
                f"/api/v1/client-payments/{pay_id}",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "2000.00",
                    "payment_method": "neft",
                },
            )
            assert res.status_code == 403

            # 5. Delete
            res = await ac.delete(f"/api/v1/client-payments/{pay_id}", headers=headers)
            assert res.status_code == 403

            # 6. Verify
            res = await ac.post(
                f"/api/v1/client-payments/{pay_id}/verify",
                headers=headers,
                json={"action": "approve"},
            )
            assert res.status_code == 403

            # 7. Export
            res = await ac.get("/api/v1/client-payments/export/excel", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_batch_l_role_permission_grant_and_dynamic_revoke():
    """Requirements C, D, E: Role grant -> 200; revoke -> 403 immediately; re-grant -> 200."""
    async with setup_batch_l_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_id = data["role_custom"].id
        perm_view_id = data["perms"]["invoices.view"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially 403
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 403

            # 1. Grant permission
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["role_custom"].name, role_id=role_id, permission_id=perm_view_id))
                await db.commit()

            # Now succeeds (200)
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 200

            # 2. Dynamic revoke
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role_id == role_id,
                    RolePermission.permission_id == perm_view_id,
                ))
                await db.commit()

            # Immediately denied (403) without server restart
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 403

            # 3. Dynamic re-grant
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["role_custom"].name, role_id=role_id, permission_id=perm_view_id))
                await db.commit()

            # Restored immediately (200)
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_batch_l_legacy_role_bypass_immunity():
    """Requirement F: Built-in role names (Accountant, Client) have zero bypass power without DB permission."""
    async with setup_batch_l_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Accountant (previously in CLIENT_PAYMENT_VERIFY_ROLES) without DB permission -> 403
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

            # 2. Client (previously in CLIENT_PAYMENT_CREATE_ROLES) without DB permission -> 403
            token_client = data["tokens"]["client_user_a1"]
            res = await ac.get("/api/v1/client-payments", headers={"Authorization": f"Bearer {token_client}"})
            assert res.status_code == 403
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
async def test_batch_l_user_permission_overrides():
    """Requirement G: User overrides explicitly grant or revoke permissions."""
    async with setup_batch_l_data() as data:
        user_id = data["user_unassigned_a"].id
        token = data["tokens"]["user_unassigned_a"]
        headers = {"Authorization": f"Bearer {token}"}
        perm_view_id = data["perms"]["invoices.view"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially 403
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 403

            # Explicit User Override GRANT
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user_id, permission_id=perm_view_id, is_granted=True))
                await db.commit()

            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 200

            # Explicit User Override REVOKE (is_granted=False)
            async with AsyncSessionLocal() as db:
                await db.execute(delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == user_id,
                    UserPermissionOverride.permission_id == perm_view_id,
                ))
                # Add role grant
                db.add(RolePermission(role=data["role_custom"].name, role_id=data["role_custom"].id, permission_id=perm_view_id))
                # Add explicit user REVOKE
                db.add(UserPermissionOverride(user_id=user_id, permission_id=perm_view_id, is_granted=False))
                await db.commit()

            # User override revoke overrides role permission grant -> 403
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_batch_l_wildcard_permission():
    """Requirement H: invoices.* grants access to Batch L operations."""
    async with setup_batch_l_data() as data:
        role_id = data["role_custom"].id
        wildcard_id = data["perms"]["invoices.*"].id
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}

        async with AsyncSessionLocal() as db:
            db.add(RolePermission(role=data["role_custom"].name, role_id=role_id, permission_id=wildcard_id))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # View list
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 200

            # Single payment view
            res = await ac.get(f"/api/v1/client-payments/{data['pay_a1'].id}", headers=headers)
            assert res.status_code == 200

            # Summary
            res = await ac.get(f"/api/v1/client-payments/invoice-summary?project_id={data['proj_a'].id}", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_batch_l_foreign_payment_access_masked_404():
    """Requirement I & P1-1: Accessing a foreign tenant's payment returns 404 (not 403)."""
    async with setup_batch_l_data() as data:
        # Give Admin A invoices.view
        perm_view_id = data["perms"]["invoices.view"].id
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=perm_view_id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        foreign_pay_id = data["pay_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Foreign payment retrieval -> 404
            res = await ac.get(f"/api/v1/client-payments/{foreign_pay_id}", headers=headers)
            assert res.status_code == 404
            assert res.json()["detail"] == "Payment not found."

            # Foreign payment receipt -> 404
            res = await ac.get(f"/api/v1/client-payments/{foreign_pay_id}/receipt", headers=headers)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_batch_l_foreign_resource_injection_blocks_404():
    """Requirement J & P0-3: Submitting foreign invoice or project returns 404."""
    async with setup_batch_l_data() as data:
        # Grant create and edit
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.create"].id, is_granted=True))
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.edit"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create with foreign project -> 404
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_a1"].id),
                    "project_id": str(data["proj_b"].id),  # foreign project
                    "amount": "5000.00",
                    "payment_method": "NEFT",
                    "bank_name": "HDFC Bank",
                    "reference_no": f"REF_INJ1_{data['uid']}",
                },
            )
            assert res.status_code == 404

            # 2. Create with foreign invoice -> 404
            res = await ac.post(
                "/api/v1/client-payments",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_b1"].id),  # foreign invoice
                    "project_id": str(data["proj_a"].id),
                    "amount": "5000.00",
                    "payment_method": "NEFT",
                    "bank_name": "HDFC Bank",
                    "reference_no": f"REF_INJ2_{data['uid']}",
                },
            )
            assert res.status_code == 404

            # 3. Update with foreign invoice -> 404
            res = await ac.put(
                f"/api/v1/client-payments/{data['pay_a2'].id}",
                headers=headers,
                data={
                    "invoice_id": str(data["inv_b1"].id),  # foreign invoice
                    "project_id": str(data["proj_a"].id),
                    "amount": "6000.00",
                    "payment_method": "CHEQUE",
                    "bank_name": "HDFC Bank",
                    "cheque_no": f"CHQ_INJ_{data['uid']}",
                },
            )
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_batch_l_foreign_client_reference_404():
    """Requirement K & P1-2: Foreign client reference returns 404."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.view"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Admin A filtering by Client B (from Company B) -> 404
            res = await ac.get(
                f"/api/v1/client-payments?user_id={data['client_user_b1'].id}&project_id={data['proj_a'].id}",
                headers=headers,
            )
            assert res.status_code == 404
            assert res.json()["detail"] == "Client not found."


@pytest.mark.asyncio
async def test_batch_l_pending_invoices_isolation():
    """Requirement L & P0-1: Company A cannot see Company B pending invoices."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.view"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/client-payments/pending-invoices", headers=headers)
            assert res.status_code == 200
            items = res.json()["items"]
            inv_ids = [item["invoice_id"] for item in items]

            # Invoices from Company A must be present
            assert data["inv_a1"].id in inv_ids
            assert data["inv_a2"].id in inv_ids
            # Invoice B1 from Company B must NEVER be present
            assert data["inv_b1"].id not in inv_ids


@pytest.mark.asyncio
async def test_batch_l_notification_isolation():
    """Requirement M & P0-2: Creating payment in Company A notifies only Company A admins, NOT Company B."""
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
                    "amount": "12345.00",
                    "payment_method": "UPI",
                    "reference_no": f"NOTIF_TEST_{data['uid']}",
                },
                files={"receipt": ("receipt.png", b"fake-receipt-png-bytes", "image/png")},
            )
            assert res.status_code == 201

        async with AsyncSessionLocal() as db:
            # Check notifications for Admin A
            notif_a = (await db.execute(
                select(Notification).where(
                    Notification.user_id == data["admin_a"].id,
                    Notification.message.contains("12345.00"),
                )
            )).scalars().all()
            assert len(notif_a) >= 1

            # Check notifications for Admin B (Company B)
            notif_b = (await db.execute(
                select(Notification).where(
                    Notification.user_id == data["admin_b"].id,
                    Notification.message.contains("12345.00"),
                )
            )).scalars().all()
            assert len(notif_b) == 0, "Security violation: Company B admin received Company A payment notification!"


@pytest.mark.asyncio
async def test_batch_l_critical_super_admin():
    """Requirement N & Section 12: Super Admin cross-company access and isolation."""
    async with setup_batch_l_data() as data:
        token_sa = data["tokens"]["super_admin"]
        sa_headers = {"Authorization": f"Bearer {token_sa}"}
        pay_a1_id = data["pay_a1"].id
        pay_b1_id = data["pay_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Super Admin retrieves Company A payment (200)
            res_a = await ac.get(f"/api/v1/client-payments/{pay_a1_id}", headers=sa_headers)
            assert res_a.status_code == 200
            assert res_a.json()["id"] == pay_a1_id

            # 2. Cross-company visibility: Super Admin can also view Company B payment (200)
            res_b = await ac.get(f"/api/v1/client-payments/{pay_b1_id}", headers=sa_headers)
            assert res_b.status_code == 200
            assert res_b.json()["id"] == pay_b1_id

            # 3. Dummy user with company_id=None and is_super_admin=False does NOT have super admin privileges
            token_dummy = data["tokens"]["dummy_none_company_user"]
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=data["dummy_none_company_user"].id, permission_id=data["perms"]["invoices.view"].id, is_granted=True))
                await db.commit()

            res_dummy = await ac.get(f"/api/v1/client-payments/{pay_a1_id}", headers={"Authorization": f"Bearer {token_dummy}"})
            # Under P0-1, company_id is None and not super admin -> rejected at auth dependency (403 Forbidden)
            assert res_dummy.status_code in (403, 404)


@pytest.mark.asyncio
async def test_batch_l_client_self_service_behavior():
    """Requirement O: Client users only see their own payments and project memberships."""
    async with setup_batch_l_data() as data:
        # Grant invoices.view to Client User A1
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["client_user_a1"].id, permission_id=data["perms"]["invoices.view"].id, is_granted=True))
            # Also create payment owned by Client A2
            pay_a_other = ClientPayment(
                company_id=data["comp_a"].id,
                payment_no=f"CP-A-OTHER-{data['uid']}",
                client_user_id=data["client_user_a2"].id,
                project_id=data["proj_a"].id,
                invoice_id=data["inv_a1"].id,
                amount=Decimal("8000.00"),
                payment_method=PaymentMethod.NEFT,
                payment_status=PaymentStatus.PENDING,
            )
            db.add(pay_a_other)
            await db.commit()
            pay_other_id = pay_a_other.id

        token_client = data["tokens"]["client_user_a1"]
        headers = {"Authorization": f"Bearer {token_client}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. List payments: Client only sees their own payments
            res = await ac.get("/api/v1/client-payments", headers=headers)
            assert res.status_code == 200
            items = res.json()
            item_ids = {p["id"] for p in items}
            assert data["pay_a1"].id in item_ids
            assert pay_other_id not in item_ids

            # 2. Accessing another client's payment returns masked 404
            res = await ac.get(f"/api/v1/client-payments/{pay_other_id}", headers=headers)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_batch_l_payment_lifecycle_and_accounting():
    """Requirement P: Payment creation, verification, invoice amount updates, auto journal posting, and edit blocks."""
    async with setup_batch_l_data() as data:
        # Give Admin A edit, delete, approve, view, create permissions
        async with AsyncSessionLocal() as db:
            for code in ["invoices.view", "invoices.create", "invoices.edit", "invoices.delete", "invoices.approve"]:
                db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"][code].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        pay_id = data["pay_a1"].id
        inv_id = data["inv_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Verify / Approve payment
            res = await ac.post(
                f"/api/v1/client-payments/{pay_id}/verify",
                headers=headers,
                json={"action": "approve", "remarks": "Approved by accounts"},
            )
            assert res.status_code == 200
            assert res.json()["payment_status"] == PaymentStatus.SUCCESS.value

            # 2. Verify invoice balance updated in DB
            async with AsyncSessionLocal() as db:
                inv = await db.get(Invoice, inv_id)
                # inv_a1 total was 100,000, pay_a1 was 25,000 -> paid: 25000, pending: 75000
                assert inv.paid_amount == Decimal("25000.00")
                assert inv.pending_amount == Decimal("75000.00")
                assert inv.status == InvoiceStatus.PARTIAL

                # Verify transaction recorded
                txn = (await db.execute(
                    select(Transaction).where(Transaction.invoice_id == inv_id)
                )).scalar_one_or_none()
                assert txn is not None
                assert txn.amount == Decimal("25000.00")
                assert txn.type == "receipt"

            # 3. Editing verified payment must be rejected (400)
            res = await ac.put(
                f"/api/v1/client-payments/{pay_id}",
                headers=headers,
                data={
                    "invoice_id": str(inv_id),
                    "project_id": str(data["proj_a"].id),
                    "amount": "26000.00",
                    "payment_method": "NEFT",
                    "bank_name": "HDFC Bank",
                    "reference_no": f"REF_UPD_{data['uid']}",
                },
            )
            assert res.status_code == 400
            assert "Payment cannot be updated after verification" in res.json()["detail"]

            # 4. Deleting verified payment must be rejected (400)
            res = await ac.delete(f"/api/v1/client-payments/{pay_id}", headers=headers)
            assert res.status_code == 400
            assert "Payment cannot be cancelled after verification" in res.json()["detail"]


@pytest.mark.asyncio
async def test_batch_l_receipt_and_exports():
    """Requirement Q: Receipt download and Excel/PDF export generation."""
    async with setup_batch_l_data() as data:
        async with AsyncSessionLocal() as db:
            db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=data["perms"]["invoices.export"].id, is_granted=True))
            await db.commit()

        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Download receipt for verified payment A3 (SUCCESS) -> 200 PDF
            res = await ac.get(f"/api/v1/client-payments/{data['pay_a3'].id}/receipt", headers=headers)
            assert res.status_code == 200
            assert res.headers["content-type"] == "application/pdf"
            assert len(res.content) > 100

            # 2. Download receipt for unverified payment A1 (VERIFICATION_PENDING) -> 400
            res = await ac.get(f"/api/v1/client-payments/{data['pay_a1'].id}/receipt", headers=headers)
            assert res.status_code == 400
            assert "Receipt is only available for verified payments" in res.json()["detail"]

            # 3. Excel export -> 200 spreadsheet
            res = await ac.get("/api/v1/client-payments/export/excel", headers=headers)
            assert res.status_code == 200
            assert "spreadsheetml" in res.headers["content-type"]
            assert len(res.content) > 100

            # 4. PDF export -> 200 PDF
            res = await ac.get("/api/v1/client-payments/export/pdf", headers=headers)
            assert res.status_code == 200
            assert res.headers["content-type"] == "application/pdf"
            assert len(res.content) > 100
