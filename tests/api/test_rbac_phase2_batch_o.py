import uuid
from decimal import Decimal
from datetime import date
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, ActivityLog
from app.models.company import Company
from app.models.owner import Owner, OwnerTransaction, OwnerPaymentSchedule
from app.models.project import Project, ProjectMember
from app.models.settings import CompanySettings
from app.models.invoice import Invoice
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import (
    InvoiceSourceType,
    InvoiceStatus,
    InvoiceType,
    ProjectStatus,
    OwnerTransactionType,
)


@asynccontextmanager
async def setup_batch_o_data():
    """Seed test companies, users, owners, projects, schedules, transactions, and RBAC data for Batch O."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchO_CompA_{uid}")
        comp_b = Company(name=f"BatchO_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company Settings
        cs_a = CompanySettings(company_id=comp_a.id, company_name=f"Brand_CompA_{uid}")
        cs_b = CompanySettings(company_id=comp_b.id, company_name=f"Brand_CompB_{uid}")
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_o_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin O",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_oa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin O",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_ob_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin O",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"OwnerManager_{uid}"
        user_custom_a = User(
            email=f"custom_oa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Owner Manager O",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        legacy_admin_no_perm = User(
            email=f"legacy_admin_o_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy Admin No Perm O",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=f"EmptyAdminO_{uid}",
        )

        dummy_none_company_user = User(
            email=f"nonecomp_o_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="None Comp User O",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Staff",
        )

        db.add_all([
            super_admin,
            admin_a,
            admin_b,
            user_custom_a,
            legacy_admin_no_perm,
            dummy_none_company_user,
        ])
        await db.flush()

        # 4. Owners
        # Owner A1: has projects, payment schedules, and transactions
        owner_a1 = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-OA1-{uid}",
            owner_name=f"Owner OA1 {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera1_{uid}@test.com",
            address="123 Main St",
        )
        # Owner A2: clean unlinked owner for deletion and mobile collision tests
        owner_a2 = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-OA2-{uid}",
            owner_name=f"Owner OA2 Clean {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera2_{uid}@test.com",
            address="456 Clean Ave",
        )
        # Owner A_Linked: has linked project (guards deletion)
        owner_a_linked = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-OAL-{uid}",
            owner_name=f"Owner OA Linked {uid}",
            mobile=f"93{uuid.uuid4().int % 100000000:08d}",
            email=f"owneral_{uid}@test.com",
        )
        # Owner A_Fin: has payment schedule / financial records (guards deletion)
        owner_a_fin = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-OAF-{uid}",
            owner_name=f"Owner OA Fin {uid}",
            mobile=f"94{uuid.uuid4().int % 100000000:08d}",
            email=f"owneraf_{uid}@test.com",
        )
        # Owner B1: belongs to Company B
        owner_b1 = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-OB1-{uid}",
            owner_name=f"Owner OB1 {uid}",
            mobile=f"95{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerb1_{uid}@test.com",
        )
        db.add_all([owner_a1, owner_a2, owner_a_linked, owner_a_fin, owner_b1])
        await db.flush()

        # 5. Projects
        proj_a1 = Project(
            business_id=f"PRJ-OA1-{uid}",
            project_name=f"Project OA1 {uid}",
            company_id=comp_a.id,
            owner_id=owner_a1.id,
            status=ProjectStatus.ONGOING,
            start_date=date(2025, 1, 1),
            end_date=date(2027, 1, 1),
        )
        proj_a_linked = Project(
            business_id=f"PRJ-OAL-{uid}",
            project_name=f"Project OA Linked {uid}",
            company_id=comp_a.id,
            owner_id=owner_a_linked.id,
            status=ProjectStatus.ONGOING,
            start_date=date(2025, 1, 1),
            end_date=date(2027, 1, 1),
        )
        proj_b1 = Project(
            business_id=f"PRJ-OB1-{uid}",
            project_name=f"Project OB1 {uid}",
            company_id=comp_b.id,
            owner_id=owner_b1.id,
            status=ProjectStatus.ONGOING,
            start_date=date(2025, 1, 1),
            end_date=date(2027, 1, 1),
        )
        db.add_all([proj_a1, proj_a_linked, proj_b1])
        await db.flush()

        # 6. Payment Schedules
        sched_a1 = OwnerPaymentSchedule(
            owner_id=owner_a1.id,
            project_id=proj_a1.id,
            milestone_name="Initial Milestone A1",
            due_date=date(2026, 12, 1),
            amount=Decimal("50000.00"),
            paid_amount=Decimal("10000.00"),
            status="Partially Paid",
            reference_code=f"SCHED-OA1-{uid}",
        )
        sched_a_fin = OwnerPaymentSchedule(
            owner_id=owner_a_fin.id,
            project_id=proj_a1.id,
            milestone_name="Milestone Fin Guard",
            due_date=date(2026, 11, 1),
            amount=Decimal("20000.00"),
            paid_amount=Decimal("0.00"),
            status="Unpaid",
            reference_code=f"SCHED-OAF-{uid}",
        )
        sched_b1 = OwnerPaymentSchedule(
            owner_id=owner_b1.id,
            project_id=proj_b1.id,
            milestone_name="Milestone B1",
            due_date=date(2026, 10, 1),
            amount=Decimal("30000.00"),
            paid_amount=Decimal("0.00"),
            status="Unpaid",
            reference_code=f"SCHED-OB1-{uid}",
        )
        db.add_all([sched_a1, sched_a_fin, sched_b1])
        await db.flush()

        # 7. Owner Transactions
        tx_a1_credit = OwnerTransaction(
            owner_id=owner_a1.id,
            project_id=proj_a1.id,
            type=OwnerTransactionType.CREDIT.value,
            amount=Decimal("50000.00"),
            reference_type="INVOICE",
            reference_id=1,
            description="Milestone billing",
        )
        tx_a1_debit = OwnerTransaction(
            owner_id=owner_a1.id,
            project_id=proj_a1.id,
            type=OwnerTransactionType.DEBIT.value,
            amount=Decimal("20000.00"),
            reference_type="PAYMENT",
            reference_id=1,
            description="Client receipt",
        )
        tx_b1_credit = OwnerTransaction(
            owner_id=owner_b1.id,
            project_id=proj_b1.id,
            type=OwnerTransactionType.CREDIT.value,
            amount=Decimal("30000.00"),
            reference_type="INVOICE",
            reference_id=2,
            description="Company B billing",
        )
        db.add_all([tx_a1_credit, tx_a1_debit, tx_b1_credit])
        await db.flush()

        # 8. RBAC Roles & DB Permissions
        role_custom = Role(
            name=custom_role_name,
            display_name="Custom Owner Manager",
            company_id=comp_a.id,
        )
        role_legacy_empty = Role(
            name=f"EmptyAdminO_{uid}",
            display_name="Empty Admin O",
            company_id=comp_a.id,
        )
        db.add_all([role_custom, role_legacy_empty])
        await db.flush()

        # Fetch existing owners permissions from database catalog
        res_perms = await db.execute(select(Permission).where(Permission.module == "owners"))
        perms = {p.code: p for p in res_perms.scalars().all()}

        # Fetch or create wildcard permission
        res_wc = await db.execute(select(Permission).where(Permission.code == "owners.*"))
        perm_wc = res_wc.scalar_one_or_none()
        if not perm_wc:
            perm_wc = Permission(
                code="owners.*",
                module="owners",
                action="*",
                description="Wildcard owner management",
            )
            db.add(perm_wc)
            await db.flush()
        perms["owners.*"] = perm_wc

        # Ensure Admin roles have permissions bound in DB
        role_admin_a = (
            await db.execute(select(Role).where(Role.name == "Admin", Role.company_id == comp_a.id))
        ).scalar_one_or_none()
        if not role_admin_a:
            role_admin_a = Role(name=f"Admin_OA_{uid}", display_name="Admin OA", company_id=comp_a.id)
            db.add(role_admin_a)
            await db.flush()
            admin_a.role = role_admin_a.name
            await db.flush()

        for code in ["owners.view", "owners.create", "owners.edit", "owners.delete", "owners.export"]:
            if code in perms:
                db.add(RolePermission(role=role_admin_a.name, role_id=role_admin_a.id, permission_id=perms[code].id))

        role_admin_b = (
            await db.execute(select(Role).where(Role.name == "Admin", Role.company_id == comp_b.id))
        ).scalar_one_or_none()
        if not role_admin_b:
            role_admin_b = Role(name=f"Admin_OB_{uid}", display_name="Admin OB", company_id=comp_b.id)
            db.add(role_admin_b)
            await db.flush()
            admin_b.role = role_admin_b.name
            await db.flush()

        for code in ["owners.view", "owners.create", "owners.edit", "owners.delete", "owners.export"]:
            if code in perms:
                db.add(RolePermission(role=role_admin_b.name, role_id=role_admin_b.id, permission_id=perms[code].id))

        await db.commit()

        # Auth tokens
        tokens = {
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "user_custom_a": create_access_token({"sub": str(user_custom_a.id)}),
            "legacy_admin_no_perm": create_access_token({"sub": str(legacy_admin_no_perm.id)}),
            "dummy_none_company_user": create_access_token({"sub": str(dummy_none_company_user.id)}),
        }

        data = {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "super_admin": super_admin,
            "admin_a": admin_a,
            "admin_b": admin_b,
            "user_custom_a": user_custom_a,
            "legacy_admin_no_perm": legacy_admin_no_perm,
            "dummy_none_company_user": dummy_none_company_user,
            "owner_a1": owner_a1,
            "owner_a2": owner_a2,
            "owner_a_linked": owner_a_linked,
            "owner_a_fin": owner_a_fin,
            "owner_b1": owner_b1,
            "proj_a1": proj_a1,
            "proj_a_linked": proj_a_linked,
            "proj_b1": proj_b1,
            "sched_a1": sched_a1,
            "sched_a_fin": sched_a_fin,
            "sched_b1": sched_b1,
            "tx_a1_credit": tx_a1_credit,
            "tx_a1_debit": tx_a1_debit,
            "tx_b1_credit": tx_b1_credit,
            "role_custom": role_custom,
            "role_legacy_empty": role_legacy_empty,
            "role_admin_a": role_admin_a,
            "role_admin_b": role_admin_b,
            "perms": perms,
            "tokens": tokens,
        }

        try:
            yield data
        finally:
            async with AsyncSessionLocal() as clean_db:
                c_ids = [comp_a.id, comp_b.id]
                u_ids = [
                    super_admin.id, admin_a.id, admin_b.id,
                    user_custom_a.id, legacy_admin_no_perm.id, dummy_none_company_user.id,
                ]
                r_ids = [role_custom.id, role_legacy_empty.id, role_admin_a.id, role_admin_b.id]
                o_sub = select(Owner.id).where(Owner.company_id.in_(c_ids))
                p_sub = select(Project.id).where(Project.company_id.in_(c_ids))

                await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(u_ids)))
                await clean_db.execute(delete(RolePermission).where(RolePermission.role_id.in_(r_ids)))
                await clean_db.execute(delete(Role).where(Role.id.in_(r_ids)))
                await clean_db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(u_ids)))
                await clean_db.execute(delete(OwnerTransaction).where(OwnerTransaction.owner_id.in_(o_sub)))
                await clean_db.execute(delete(OwnerPaymentSchedule).where(OwnerPaymentSchedule.owner_id.in_(o_sub)))
                await clean_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_(p_sub)))
                await clean_db.execute(delete(Project).where(Project.company_id.in_(c_ids)))
                await clean_db.execute(delete(Owner).where(Owner.company_id.in_(c_ids)))
                await clean_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_(c_ids)))
                await clean_db.execute(delete(User).where(User.id.in_(u_ids)))
                await clean_db.execute(delete(Company).where(Company.id.in_(c_ids)))
                await clean_db.commit()


# ============================================================================
# 1. AUTHENTICATION REQUIRED (401 across all 12 routes)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_authentication_required():
    """All 12 Batch O routes return 401 Unauthorized without auth token."""
    async with setup_batch_o_data() as data:
        owner_id = data["owner_a1"].id
        proj_id = data["proj_a1"].id
        routes = [
            ("POST", "/api/v1/owners", {"owner_name": "New Owner", "mobile": "9876543210"}),
            ("GET", "/api/v1/owners", None),
            ("GET", "/api/v1/owners/portfolio", None),
            ("GET", "/api/v1/owners/payment-tracker", None),
            ("POST", "/api/v1/owners/payment-tracker", {
                "owner_id": owner_id,
                "project_id": proj_id,
                "milestone_name": "Milestone Test",
                "amount": "1000.00",
            }),
            ("GET", f"/api/v1/owners/{owner_id}", None),
            ("PUT", f"/api/v1/owners/{owner_id}", {"owner_name": "Updated Name"}),
            ("DELETE", f"/api/v1/owners/{owner_id}", None),
            ("GET", f"/api/v1/owners/{owner_id}/payments", None),
            ("GET", f"/api/v1/owners/{owner_id}/ledger", None),
            ("GET", f"/api/v1/owners/{owner_id}/ledger/pdf", None),
            ("GET", f"/api/v1/owners/{owner_id}/ledger/excel", None),
        ]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for method, url, payload in routes:
                if method == "GET":
                    res = await ac.get(url)
                elif method == "POST":
                    res = await ac.post(url, json=payload)
                elif method == "PUT":
                    res = await ac.put(url, json=payload)
                elif method == "DELETE":
                    res = await ac.delete(url)
                assert res.status_code == 401, f"Route {method} {url} expected 401, got {res.status_code}"


# ============================================================================
# 2. PERMISSION DENIAL (403 across all 12 routes)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_permission_denial():
    """Users without required permission receive 403 Forbidden across all 12 routes."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["user_custom_a"]  # 0 DB permissions
        headers = {"Authorization": f"Bearer {token}"}
        owner_id = data["owner_a1"].id
        proj_id = data["proj_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. POST /api/v1/owners -> owners.create
            res = await ac.post(
                "/api/v1/owners",
                headers=headers,
                json={"owner_name": "Denied Owner", "mobile": "9876543210"},
            )
            assert res.status_code == 403

            # 2. GET /api/v1/owners -> owners.view
            res = await ac.get("/api/v1/owners", headers=headers)
            assert res.status_code == 403

            # 3. GET /api/v1/owners/portfolio -> owners.view
            res = await ac.get("/api/v1/owners/portfolio", headers=headers)
            assert res.status_code == 403

            # 4. GET /api/v1/owners/payment-tracker -> owners.view
            res = await ac.get("/api/v1/owners/payment-tracker", headers=headers)
            assert res.status_code == 403

            # 5. POST /api/v1/owners/payment-tracker -> owners.create
            res = await ac.post(
                "/api/v1/owners/payment-tracker",
                headers=headers,
                json={
                    "owner_id": owner_id,
                    "project_id": proj_id,
                    "milestone_name": "Milestone Denied",
                    "amount": "1000.00",
                },
            )
            assert res.status_code == 403

            # 6. GET /api/v1/owners/{owner_id} -> owners.view
            res = await ac.get(f"/api/v1/owners/{owner_id}", headers=headers)
            assert res.status_code == 403

            # 7. PUT /api/v1/owners/{owner_id} -> owners.edit
            res = await ac.put(f"/api/v1/owners/{owner_id}", headers=headers, json={"owner_name": "Denied Edit"})
            assert res.status_code == 403

            # 8. DELETE /api/v1/owners/{owner_id} -> owners.delete
            res = await ac.delete(f"/api/v1/owners/{owner_id}", headers=headers)
            assert res.status_code == 403

            # 9. GET /api/v1/owners/{owner_id}/payments -> owners.view
            res = await ac.get(f"/api/v1/owners/{owner_id}/payments", headers=headers)
            assert res.status_code == 403

            # 10. GET /api/v1/owners/{owner_id}/ledger -> owners.view
            res = await ac.get(f"/api/v1/owners/{owner_id}/ledger", headers=headers)
            assert res.status_code == 403

            # 11. GET /api/v1/owners/{owner_id}/ledger/pdf -> owners.export
            res = await ac.get(f"/api/v1/owners/{owner_id}/ledger/pdf", headers=headers)
            assert res.status_code == 403

            # 12. GET /api/v1/owners/{owner_id}/ledger/excel -> owners.export
            res = await ac.get(f"/api/v1/owners/{owner_id}/ledger/excel", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 3. DYNAMIC DB GRANT -> SUCCESS -> REVOKE -> 403 LIFECYCLE
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_dynamic_grant_revoke_lifecycle():
    """Runtime DB permission grant yields immediate access, and revoking yields immediate 403."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_id = data["role_custom"].id
        role_name = data["role_custom"].name
        perm_view = data["perms"]["owners.view"]
        perm_edit = data["perms"]["owners.edit"]
        owner_id = data["owner_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially denied
            res = await ac.get(f"/api/v1/owners/{owner_id}", headers=headers)
            assert res.status_code == 403

            # 1. Grant owners.view
            async with AsyncSessionLocal() as db:
                rp_view = RolePermission(role=role_name, role_id=role_id, permission_id=perm_view.id)
                db.add(rp_view)
                await db.commit()

            # Now succeeds
            res = await ac.get(f"/api/v1/owners/{owner_id}", headers=headers)
            assert res.status_code == 200
            assert res.json()["id"] == owner_id

            # But edit is still 403
            res = await ac.put(f"/api/v1/owners/{owner_id}", headers=headers, json={"owner_name": "Updated OA1"})
            assert res.status_code == 403

            # 2. Grant owners.edit
            async with AsyncSessionLocal() as db:
                rp_edit = RolePermission(role=role_name, role_id=role_id, permission_id=perm_edit.id)
                db.add(rp_edit)
                await db.commit()

            # Now edit succeeds
            res = await ac.put(f"/api/v1/owners/{owner_id}", headers=headers, json={"owner_name": "Updated OA1"})
            assert res.status_code == 200
            assert res.json()["owner_name"] == "Updated OA1"

            # 3. Revoke both permissions
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
                await db.commit()

            # Now both are immediately 403
            res = await ac.get(f"/api/v1/owners/{owner_id}", headers=headers)
            assert res.status_code == 403
            res = await ac.put(f"/api/v1/owners/{owner_id}", headers=headers, json={"owner_name": "Blocked OA1"})
            assert res.status_code == 403


# ============================================================================
# 4. POSITIVE USER PERMISSION OVERRIDE
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_positive_user_override():
    """User without role permission can access route when positive user override is granted."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["user_custom_a"]
        user_id = data["user_custom_a"].id
        headers = {"Authorization": f"Bearer {token}"}
        perm_view = data["perms"]["owners.view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Baseline: 403
            res = await ac.get("/api/v1/owners", headers=headers)
            assert res.status_code == 403

            # Add positive override
            async with AsyncSessionLocal() as db:
                override = UserPermissionOverride(
                    user_id=user_id,
                    permission_id=perm_view.id,
                    is_granted=True,
                )
                db.add(override)
                await db.commit()

            # Now succeeds
            res = await ac.get("/api/v1/owners", headers=headers)
            assert res.status_code == 200
            assert isinstance(res.json(), list)


# ============================================================================
# 5. NEGATIVE USER PERMISSION OVERRIDE
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_negative_user_override():
    """User with role permission is blocked when negative user override is configured."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        user_id = data["admin_a"].id
        headers = {"Authorization": f"Bearer {token}"}
        perm_view = data["perms"]["owners.view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Baseline: 200
            res = await ac.get("/api/v1/owners", headers=headers)
            assert res.status_code == 200

            # Add negative override
            async with AsyncSessionLocal() as db:
                override = UserPermissionOverride(
                    user_id=user_id,
                    permission_id=perm_view.id,
                    is_granted=False,
                )
                db.add(override)
                await db.commit()

            # Now explicitly denied 403
            res = await ac.get("/api/v1/owners", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 6. WILDCARD PERMISSION (owners.*)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_wildcard_permission():
    """Wildcard permission owners.* grants full access across owner endpoints."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_id = data["role_custom"].id
        role_name = data["role_custom"].name
        perm_wc = data["perms"]["owners.*"]
        owner_id = data["owner_a1"].id

        # Grant wildcard
        async with AsyncSessionLocal() as db:
            rp = RolePermission(role=role_name, role_id=role_id, permission_id=perm_wc.id)
            db.add(rp)
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. View allowed
            res = await ac.get(f"/api/v1/owners/{owner_id}", headers=headers)
            assert res.status_code == 200

            # 2. Portfolio allowed
            res = await ac.get("/api/v1/owners/portfolio", headers=headers)
            assert res.status_code == 200

            # 3. Edit allowed
            res = await ac.put(f"/api/v1/owners/{owner_id}", headers=headers, json={"owner_name": "Wildcard OA1"})
            assert res.status_code == 200

            # 4. Payments view allowed
            res = await ac.get(f"/api/v1/owners/{owner_id}/payments", headers=headers)
            assert res.status_code == 200

            # 5. Ledger view allowed
            res = await ac.get(f"/api/v1/owners/{owner_id}/ledger", headers=headers)
            assert res.status_code == 200


# ============================================================================
# 7. LEGACY ROLE IMMUNITY
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_legacy_role_immunity():
    """Legacy role name with zero DB permissions receives 403 Forbidden."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["legacy_admin_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/owners", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 8. CROSS-TENANT OWNER ACCESS (404)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_cross_tenant_owner_access_404():
    """Company A admin cannot access Company B owner (masked 404)."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_b_id = data["owner_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/owners/{owner_b_id}", headers=headers)
            assert res.status_code == 404
            assert res.json()["detail"] == "Owner not found"


# ============================================================================
# 9. CROSS-TENANT OWNER UPDATE / DELETE (404)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_cross_tenant_owner_mutation_404():
    """Company A admin cannot update or delete Company B owner (masked 404)."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_b_id = data["owner_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # PUT
            res = await ac.put(f"/api/v1/owners/{owner_b_id}", headers=headers, json={"owner_name": "Hacked OB1"})
            assert res.status_code == 404
            assert res.json()["detail"] == "Owner not found"

            # DELETE
            res = await ac.delete(f"/api/v1/owners/{owner_b_id}", headers=headers)
            assert res.status_code == 404
            assert res.json()["detail"] == "Owner not found"


# ============================================================================
# 10. CROSS-TENANT FINANCIAL SUB-RESOURCES (404)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_cross_tenant_financial_subresources_404():
    """Company A admin cannot access Company B financial sub-resources (payments, ledger, pdf, excel)."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_b_id = data["owner_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Payments
            res = await ac.get(f"/api/v1/owners/{owner_b_id}/payments", headers=headers)
            assert res.status_code == 404

            # Ledger
            res = await ac.get(f"/api/v1/owners/{owner_b_id}/ledger", headers=headers)
            assert res.status_code == 404

            # PDF
            res = await ac.get(f"/api/v1/owners/{owner_b_id}/ledger/pdf", headers=headers)
            assert res.status_code == 404

            # Excel
            res = await ac.get(f"/api/v1/owners/{owner_b_id}/ledger/excel", headers=headers)
            assert res.status_code == 404


# ============================================================================
# 11. CROSS-TENANT PAYMENT TRACKER LISTING ISOLATION
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_cross_tenant_payment_tracker_listing():
    """Payment tracker listing is strictly isolated by tenant, masking foreign owner/project with 404."""
    async with setup_batch_o_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        owner_b_id = data["owner_b1"].id
        proj_b_id = data["proj_b1"].id
        sched_a_id = data["sched_a1"].id
        sched_b_id = data["sched_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Unfiltered listing returns Company A's schedules, never Company B's
            res = await ac.get("/api/v1/owners/payment-tracker", headers=headers_a)
            assert res.status_code == 200
            ids = [r["id"] for r in res.json()]
            assert sched_a_id in ids
            assert sched_b_id not in ids

            # 2. Filter by foreign owner_id -> 404
            res = await ac.get(f"/api/v1/owners/payment-tracker?owner_id={owner_b_id}", headers=headers_a)
            assert res.status_code == 404

            # 3. Filter by foreign project_id -> 404
            res = await ac.get(f"/api/v1/owners/payment-tracker?project_id={proj_b_id}", headers=headers_a)
            assert res.status_code == 404


# ============================================================================
# 12. CROSS-TENANT PAYMENT MILESTONE CREATION (404)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_cross_tenant_milestone_creation():
    """Attempting to create a payment milestone for foreign owner or project returns masked 404."""
    async with setup_batch_o_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        owner_a_id = data["owner_a1"].id
        owner_b_id = data["owner_b1"].id
        proj_a_id = data["proj_a1"].id
        proj_b_id = data["proj_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Foreign owner + own project -> 404
            res = await ac.post(
                "/api/v1/owners/payment-tracker",
                headers=headers_a,
                json={
                    "owner_id": owner_b_id,
                    "project_id": proj_a_id,
                    "milestone_name": "Cross Owner Milestone",
                    "amount": "5000.00",
                },
            )
            assert res.status_code == 404
            assert res.json()["detail"] == "Owner not found"

            # Own owner + foreign project -> 404
            res = await ac.post(
                "/api/v1/owners/payment-tracker",
                headers=headers_a,
                json={
                    "owner_id": owner_a_id,
                    "project_id": proj_b_id,
                    "milestone_name": "Cross Project Milestone",
                    "amount": "5000.00",
                },
            )
            assert res.status_code == 404
            assert res.json()["detail"] == "Project not found"


# ============================================================================
# 13. CROSS-PROJECT OWNER MISMATCH (404)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_cross_project_owner_mismatch():
    """Attempting to create a payment milestone where project belongs to another owner returns 404."""
    async with setup_batch_o_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        owner_a1_id = data["owner_a1"].id
        # proj_a_linked belongs to owner_a_linked, NOT owner_a1
        proj_a_linked_id = data["proj_a_linked"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/owners/payment-tracker",
                headers=headers_a,
                json={
                    "owner_id": owner_a1_id,
                    "project_id": proj_a_linked_id,
                    "milestone_name": "Mismatched Milestone",
                    "amount": "5000.00",
                },
            )
            assert res.status_code == 404
            assert res.json()["detail"] == "Project not found"


# ============================================================================
# 14. SUPER ADMIN CROSS-COMPANY ACCESS
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_super_admin_cross_company_access():
    """Super Admin can access owners and payment schedules across both companies."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["super_admin"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_a_id = data["owner_a1"].id
        owner_b_id = data["owner_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. List owners shows both companies
            res = await ac.get("/api/v1/owners", headers=headers)
            assert res.status_code == 200
            owner_ids = [o["id"] for o in res.json()]
            assert owner_a_id in owner_ids
            assert owner_b_id in owner_ids

            # 2. Get specific owner from Company A
            res = await ac.get(f"/api/v1/owners/{owner_a_id}", headers=headers)
            assert res.status_code == 200

            # 3. Get specific owner from Company B
            res = await ac.get(f"/api/v1/owners/{owner_b_id}", headers=headers)
            assert res.status_code == 200

            # 4. Payment tracker listing shows both
            res = await ac.get("/api/v1/owners/payment-tracker", headers=headers)
            assert res.status_code == 200
            sched_ids = [s["id"] for s in res.json()]
            assert data["sched_a1"].id in sched_ids
            assert data["sched_b1"].id in sched_ids


# ============================================================================
# 15. NON-SA COMPANY_ID=NONE ISOLATION
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_non_sa_company_id_none_isolation():
    """Non-Super-Admin user with company_id=None is denied and isolated across all operations."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["dummy_none_company_user"]
        user_id = data["dummy_none_company_user"].id
        headers = {"Authorization": f"Bearer {token}"}
        owner_a_id = data["owner_a1"].id
        proj_a_id = data["proj_a1"].id

        # Grant permissions via override to verify company_id=None isolation independently
        async with AsyncSessionLocal() as db:
            for code in ["owners.view", "owners.create", "owners.edit", "owners.delete", "owners.export"]:
                db.add(UserPermissionOverride(user_id=user_id, permission_id=data["perms"][code].id, is_granted=True))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create owner -> 403 Forbidden
            res = await ac.post(
                "/api/v1/owners",
                headers=headers,
                json={"owner_name": "No Comp Owner", "mobile": "9998887776"},
            )
            assert res.status_code == 403
            assert "User does not belong to any company" in res.json().get("detail", "")

            # 2. List owners -> 403 Forbidden
            res = await ac.get("/api/v1/owners", headers=headers)
            assert res.status_code == 403
            assert "User does not belong to any company" in res.json().get("detail", "")

            # 3. Portfolio -> 403 Forbidden
            res = await ac.get("/api/v1/owners/portfolio", headers=headers)
            assert res.status_code == 403
            assert "User does not belong to any company" in res.json().get("detail", "")

            # 4. Payment tracker -> 403 Forbidden
            res = await ac.get("/api/v1/owners/payment-tracker", headers=headers)
            assert res.status_code == 403
            assert "User does not belong to any company" in res.json().get("detail", "")

            # 5. Get owner -> 403 Forbidden
            res = await ac.get(f"/api/v1/owners/{owner_a_id}", headers=headers)
            assert res.status_code == 403
            assert "User does not belong to any company" in res.json().get("detail", "")

            # 6. Post payment tracker milestone -> 403 Forbidden
            res = await ac.post(
                "/api/v1/owners/payment-tracker",
                headers=headers,
                json={
                    "owner_id": owner_a_id,
                    "project_id": proj_a_id,
                    "milestone_name": "No Comp Milestone",
                    "amount": "100.00",
                },
            )
            assert res.status_code == 403
            assert "User does not belong to any company" in res.json().get("detail", "")


# ============================================================================
# 16. OWNER DELETION BLOCKED BY LINKED PROJECTS
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_delete_blocked_by_linked_projects():
    """Deleting an owner with assigned projects returns 422 ValidationError."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_linked_id = data["owner_a_linked"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/owners/{owner_linked_id}", headers=headers)
            assert res.status_code == 422
            assert "are assigned to this owner" in res.json()["detail"]


# ============================================================================
# 17. OWNER DELETION BLOCKED BY FINANCIAL RECORDS
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_delete_blocked_by_financial_records():
    """Deleting an owner with related financial records (schedules/transactions/invoices) returns 422."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_fin_id = data["owner_a_fin"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/owners/{owner_fin_id}", headers=headers)
            assert res.status_code == 422
            assert "related financial records exist" in res.json()["detail"]


# ============================================================================
# 18. OWNER DELETION SUCCESS FOR CLEAN UNLINKED OWNER
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_delete_success_clean_owner():
    """Deleting a clean unlinked owner without projects or financial records returns 204."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_clean_id = data["owner_a2"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/owners/{owner_clean_id}", headers=headers)
            assert res.status_code == 204

            # Verify no longer exists
            res_check = await ac.get(f"/api/v1/owners/{owner_clean_id}", headers=headers)
            assert res_check.status_code == 404


# ============================================================================
# 19. MOBILE UNIQUENESS COLLISION ON UPDATE
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_mobile_uniqueness_collision():
    """Updating an owner with an existing mobile number returns 422 ValidationError."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_a1 = data["owner_a1"]
        owner_a2 = data["owner_a2"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.put(
                f"/api/v1/owners/{owner_a2.id}",
                headers=headers,
                json={"mobile": owner_a1.mobile},
            )
            assert res.status_code == 422
            assert "Mobile number already exists" in res.json()["detail"]


# ============================================================================
# 20. OWNER CODE GENERATION AND CREATION
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_owner_code_generation_and_creation():
    """Creating an owner automatically generates unique business owner_code starting with OWN."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        unique_mobile = f"96{uuid.uuid4().int % 100000000:08d}"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/owners",
                headers=headers,
                json={
                    "owner_name": "Generated Code Owner",
                    "mobile": unique_mobile,
                    "email": "gencodetest@owner.com",
                    "address": "789 Code Blvd",
                },
            )
            assert res.status_code == 200
            resp_data = res.json()
            assert resp_data["owner_code"].startswith("OWN")
            assert resp_data["owner_name"] == "Generated Code Owner"


# ============================================================================
# 21. PORTFOLIO SATISFACTION CALCULATION
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_portfolio_satisfaction_calculation():
    """Portfolio endpoint computes satisfaction scores and aggregated summaries correctly."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/owners/portfolio", headers=headers)
            assert res.status_code == 200
            resp = res.json()
            summary = resp["summary"]
            items = resp["items"]

            assert summary["total_clients"] >= 1
            assert "average_satisfaction_score" in summary
            assert len(items) >= 1

            # Check individual item attributes
            first_item = items[0]
            assert "satisfaction_score" in first_item
            assert 0 <= first_item["satisfaction_score"] <= 100


# ============================================================================
# 22. LEDGER BALANCE CALCULATION
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_ledger_balance_calculation():
    """Owner ledger computes credit, debit, and balance accurately from transactions."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_id = data["owner_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/owners/{owner_id}/ledger", headers=headers)
            assert res.status_code == 200
            ledger = res.json()

            # Credit = 50000.00, Debit = 20000.00, Balance = 30000.00
            assert Decimal(str(ledger["total_credit"])) == Decimal("50000.00")
            assert Decimal(str(ledger["total_debit"])) == Decimal("20000.00")
            assert Decimal(str(ledger["balance"])) == Decimal("30000.00")
            assert len(ledger["transactions"]) == 2


# ============================================================================
# 23. PDF AND EXCEL EXPORT SUCCESS & VALIDATION
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_pdf_and_excel_exports():
    """PDF and Excel exports stream successfully when transactions exist, and return 422 if empty."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_with_tx = data["owner_a1"].id
        owner_empty = data["owner_a2"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. PDF Export with transactions
            res_pdf = await ac.get(f"/api/v1/owners/{owner_with_tx}/ledger/pdf", headers=headers)
            assert res_pdf.status_code == 200
            assert "application/pdf" in res_pdf.headers.get("content-type", "")
            assert len(res_pdf.content) > 0

            # 2. Excel (CSV) Export with transactions
            res_excel = await ac.get(f"/api/v1/owners/{owner_with_tx}/ledger/excel", headers=headers)
            assert res_excel.status_code == 200
            assert "text/csv" in res_excel.headers.get("content-type", "")
            assert b"Date,Type,Amount" in res_excel.content

            # 3. PDF Export without transactions -> 422
            res_empty_pdf = await ac.get(f"/api/v1/owners/{owner_empty}/ledger/pdf", headers=headers)
            assert res_empty_pdf.status_code == 422
            assert "No ledger data available to export" in res_empty_pdf.json()["detail"]

            # 4. Excel Export without transactions -> 422
            res_empty_excel = await ac.get(f"/api/v1/owners/{owner_empty}/ledger/excel", headers=headers)
            assert res_empty_excel.status_code == 422
            assert "No ledger data available to export" in res_empty_excel.json()["detail"]


# ============================================================================
# 24. EXCEPTION DETAILS ARE NOT LEAKED
# ============================================================================

@pytest.mark.asyncio
async def test_batch_o_exception_details_not_leaked(monkeypatch):
    """Internal exceptions logged securely and generic application error returned without leaking."""
    async with setup_batch_o_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        owner_id = data["owner_a1"].id

        # Monkeypatch db.flush or commit to simulate unexpected internal failure
        from sqlalchemy.ext.asyncio import AsyncSession

        async def mock_flush_fail(*args, **kwargs):
            raise RuntimeError("CRITICAL_DATABASE_INTERNAL_SECRET_LEAK")

        monkeypatch.setattr(AsyncSession, "flush", mock_flush_fail)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.put(
                f"/api/v1/owners/{owner_id}",
                headers=headers,
                json={"owner_name": "Trigger Error"},
            )
            assert res.status_code == 500
            detail = res.json().get("detail", "")
            assert "CRITICAL_DATABASE_INTERNAL_SECRET_LEAK" not in detail
            assert "An internal error occurred" in detail
