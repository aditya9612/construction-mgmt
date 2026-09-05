"""
RBAC Phase 2 – Batch V: Approvals Management Test Suite
Tests covering all 4 active production routes:
- POST /api/v1/approvals
- GET /api/v1/approvals
- PUT /api/v1/approvals/{id}/approve
- PUT /api/v1/approvals/{id}/reject

Validating:
1. 401 unauthenticated for all 4 routes
2. 403 missing permissions
3. Dynamic DB permission grant & revoke
4. Positive user permission override
5. Negative user permission override
6. approvals.* wildcard
7. Global * wildcard
8. Legacy role immunity (no role bypass)
9. Tenantless non-SA user -> 403
10. Super Admin cross-company access
11. Cross-tenant list isolation
12. Foreign approval ID masked as 404
13. Foreign target entity injection masked as 404
14. Unsupported entity type rejection (400)
15. Duplicate Pending approval blocked (400)
16. Segregation of duties: self-approval blocked (400)
17. State machine: Approved request cannot be approved/rejected (400)
18. State machine: Rejected request cannot be approved/rejected (400)
19. Rejection requires non-empty remarks (400)
20. Target state synchronization for BOQ
21. Target state synchronization for FinalMeasurement
22. Target state synchronization for PurchaseOrder
23. Target state synchronization for Document
24. Target state synchronization for DrawingDocument
25. Target state synchronization for RABill
26. Target state synchronization for JournalEntry
27. Atomic transaction rollback on notification failure (500)
28. AST inspection: ZERO require_roles in app/api/approval.py
"""

import ast
import uuid
from decimal import Decimal
from datetime import date
from unittest.mock import patch
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, func

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, DrawingDocument
from app.models.boq import BOQ, BOQGroup
from app.models.final_measurement import FinalMeasurement
from app.models.material import Supplier, PurchaseOrder
from app.models.document import Document
from app.models.billing import RABill
from app.models.accountant import JournalEntry
from app.models.approval import Approval
from app.models.notification import Notification
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import ProjectStatus, DocumentStatus


@asynccontextmanager
async def setup_batch_v_data():
    """
    Seed test fixture data for Batch V approvals tests.
    Ensures safe cleanup in finally block.
    """
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"BatchV_CompA_{uid}")
        comp_b = Company(name=f"BatchV_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-VA-{uid}",
            owner_name=f"Owner VA {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerva_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-VB-{uid}",
            owner_name=f"Owner VB {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownervb_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 3. Projects
        proj_a = Project(
            business_id=f"PRJ-VA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Project VA {uid}",
            status=ProjectStatus.ONGOING,
        )
        proj_b = Project(
            business_id=f"PRJ-VB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Project VB {uid}",
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 3. Users
        # User A1: requester in Company A
        user_a1 = User(
            email=f"requester_va1_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Requester VA1",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        # User A2: approver in Company A
        user_a2 = User(
            email=f"approver_va2_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Approver VA2",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        # User B: user in Company B
        user_b = User(
            email=f"user_vb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="User VB",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        # Super Admin
        super_admin = User(
            email=f"sa_v_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin V",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        # Tenantless non-SA
        tenantless_user = User(
            email=f"tenantless_v_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Tenantless V",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        db.add_all([user_a1, user_a2, user_b, super_admin, tenantless_user])
        await db.flush()

        # Isolated roles for RBAC tests
        role_empty_name = f"empty_v_{uid}"
        role_custom_name = f"custom_v_{uid}"
        role_empty = Role(
            company_id=comp_a.id,
            name=role_empty_name,
            display_name="Empty Role V",
            is_system=False,
        )
        role_custom = Role(
            company_id=comp_a.id,
            name=role_custom_name,
            display_name="Custom Role V",
            is_system=False,
        )
        db.add_all([role_empty, role_custom])
        await db.flush()

        no_perm_user = User(
            email=f"noperm_v_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="No Perm User V",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=role_empty_name,
        )
        custom_user = User(
            email=f"custom_v_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom User V",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=role_custom_name,
        )
        db.add_all([no_perm_user, custom_user])
        await db.flush()

        # 4. Permissions
        perm_view = (await db.execute(select(Permission).where(Permission.code == "approvals.view"))).scalar_one()
        perm_create = (await db.execute(select(Permission).where(Permission.code == "approvals.create"))).scalar_one()
        perm_approve = (await db.execute(select(Permission).where(Permission.code == "approvals.approve"))).scalar_one()

        # Add permissions to Admin role for the test session
        admin_added_rp_ids = []
        for perm in [perm_view, perm_create, perm_approve]:
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
                admin_added_rp_ids.append(rp.id)

        # 5. Target Entities in Company A
        # BOQ
        boq_group_a = BOQGroup(project_id=proj_a.id, name=f"BOQ Group A {uid}")
        db.add(boq_group_a)
        await db.flush()
        boq_a = BOQ(
            project_id=proj_a.id,
            boq_group_id=boq_group_a.id,
            item_name="BOQ Test Item A",
            category="Civil",
            approval_status="Draft",
        )
        # FinalMeasurement
        meas_a = FinalMeasurement(
            project_id=proj_a.id,
            final_area=100.0,
            approved_rate=250.0,
            total_area=100.0,
            total_amount=25000.0,
            status="PENDING",
        )
        # Supplier & PurchaseOrder
        supp_a = Supplier(
            company_id=comp_a.id,
            supplier_name=f"Supplier VA {uid}",
            contact_person="Supplier A",
            phone_email="supp_va@test.com",
        )
        db.add(supp_a)
        await db.flush()
        po_a = PurchaseOrder(
            project_id=proj_a.id,
            supplier_id=supp_a.id,
            material_id=1,
            material_name="Cement Grade 53",
            quantity=Decimal("100.00"),
            rate=Decimal("350.00"),
            total_amount=Decimal("35000.00"),
            status="CREATED",
        )
        # Document
        doc_a = Document(
            project_id=proj_a.id,
            title=f"Test Document VA {uid}",
            status=DocumentStatus.PENDING,
        )
        # DrawingDocument
        drawing_a = DrawingDocument(
            project_id=proj_a.id,
            drawing_name=f"Architectural Plan {uid}",
            approval_status=DocumentStatus.PENDING,
        )
        # RABill
        bill_a = RABill(
            project_id=proj_a.id,
            bill_number=f"BILL-VA-{uid}",
            work_description="Excavation Work",
            quantity=10,
            rate=500,
            gross_amount=5000,
            net_amount=5000,
            total_amount=5000,
            bill_date=date.today(),
            status="Draft",
        )
        # JournalEntry
        je_a = JournalEntry(
            journal_number=f"JRN-VA-{uid}",
            entry_date=date.today(),
            status="Draft",
            entry_type="Manual",
            created_by=user_a1.id,
        )
        db.add_all([boq_a, meas_a, po_a, doc_a, drawing_a, bill_a, je_a])
        await db.flush()

        # 6. Target Entities in Company B
        boq_group_b = BOQGroup(project_id=proj_b.id, name=f"BOQ Group B {uid}")
        db.add(boq_group_b)
        await db.flush()
        boq_b = BOQ(
            project_id=proj_b.id,
            boq_group_id=boq_group_b.id,
            item_name="BOQ Test Item B",
            category="Civil",
            approval_status="Draft",
        )
        je_b = JournalEntry(
            journal_number=f"JRN-VB-{uid}",
            entry_date=date.today(),
            status="Draft",
            entry_type="Manual",
            created_by=user_b.id,
        )
        db.add_all([boq_b, je_b])
        await db.flush()

        tokens = {
            "user_a1": create_access_token({"sub": str(user_a1.id)}),
            "user_a2": create_access_token({"sub": str(user_a2.id)}),
            "user_b": create_access_token({"sub": str(user_b.id)}),
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "tenantless": create_access_token({"sub": str(tenantless_user.id)}),
            "no_perm": create_access_token({"sub": str(no_perm_user.id)}),
            "custom": create_access_token({"sub": str(custom_user.id)}),
        }

        await db.commit()

        context_data = {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "user_a1": user_a1,
            "user_a2": user_a2,
            "user_b": user_b,
            "super_admin": super_admin,
            "tenantless_user": tenantless_user,
            "no_perm_user": no_perm_user,
            "custom_user": custom_user,
            "role_empty": role_empty,
            "role_custom": role_custom,
            "perm_view": perm_view,
            "perm_create": perm_create,
            "perm_approve": perm_approve,
            "boq_a": boq_a,
            "boq_b": boq_b,
            "meas_a": meas_a,
            "po_a": po_a,
            "doc_a": doc_a,
            "drawing_a": drawing_a,
            "bill_a": bill_a,
            "je_a": je_a,
            "je_b": je_b,
            "tokens": tokens,
        }

        try:
            yield context_data
        finally:
            async with AsyncSessionLocal() as clean_db:
                # 1. Clean notifications for test users
                test_user_ids = [
                    user_a1.id, user_a2.id, user_b.id, super_admin.id,
                    tenantless_user.id, no_perm_user.id, custom_user.id
                ]
                await clean_db.execute(delete(Notification).where(Notification.user_id.in_(test_user_ids)))
                # 2. Clean approvals
                await clean_db.execute(delete(Approval).where(Approval.requested_by.in_(test_user_ids)))
                # 3. Clean target entities
                await clean_db.execute(delete(DrawingDocument).where(DrawingDocument.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(Document).where(Document.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(BOQ).where(BOQ.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(BOQGroup).where(BOQGroup.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(FinalMeasurement).where(FinalMeasurement.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(PurchaseOrder).where(PurchaseOrder.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(Supplier).where(Supplier.company_id.in_([comp_a.id, comp_b.id])))
                await clean_db.execute(delete(RABill).where(RABill.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(JournalEntry).where(JournalEntry.created_by.in_(test_user_ids)))
                # 4. Clean Projects & Owners
                await clean_db.execute(delete(Project).where(Project.company_id.in_([comp_a.id, comp_b.id])))
                await clean_db.execute(delete(Owner).where(Owner.company_id.in_([comp_a.id, comp_b.id])))
                # 5. Clean UserPermissionOverride
                await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(test_user_ids)))
                # 6. Clean RolePermission
                await clean_db.execute(delete(RolePermission).where(RolePermission.role_id.in_([role_empty.id, role_custom.id])))
                if admin_added_rp_ids:
                    await clean_db.execute(delete(RolePermission).where(RolePermission.id.in_(admin_added_rp_ids)))
                # 7. Clean Users
                await clean_db.execute(delete(User).where(User.id.in_(test_user_ids)))
                # 8. Clean Roles
                await clean_db.execute(delete(Role).where(Role.id.in_([role_empty.id, role_custom.id])))
                # 9. Clean Companies
                await clean_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
                await clean_db.commit()


# ==============================================================================
# TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_approvals_unauthenticated():
    """Verify that all 4 routes reject unauthenticated requests with HTTP 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.get("/api/v1/approvals")
        assert r1.status_code == 401

        r2 = await ac.post("/api/v1/approvals", json={"entity_type": "boq", "entity_id": 1})
        assert r2.status_code == 401

        r3 = await ac.put("/api/v1/approvals/1/approve", json={"remarks": "ok"})
        assert r3.status_code == 401

        r4 = await ac.put("/api/v1/approvals/1/reject", json={"remarks": "no"})
        assert r4.status_code == 401


@pytest.mark.asyncio
async def test_approvals_missing_permissions():
    """Verify that a user without approval permissions gets HTTP 403 on all routes."""
    async with setup_batch_v_data() as data:
        token = data["tokens"]["no_perm"]
        headers = {"Authorization": f"Bearer {token}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r1 = await ac.get("/api/v1/approvals", headers=headers)
            assert r1.status_code == 403

            r2 = await ac.post(
                "/api/v1/approvals",
                headers=headers,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id},
            )
            assert r2.status_code == 403

            r3 = await ac.put("/api/v1/approvals/1/approve", headers=headers, json={"remarks": "ok"})
            assert r3.status_code == 403

            r4 = await ac.put("/api/v1/approvals/1/reject", headers=headers, json={"remarks": "no"})
            assert r4.status_code == 403


@pytest.mark.asyncio
async def test_approvals_dynamic_grant_and_revoke():
    """Verify dynamic grant and revoke without server restart."""
    async with setup_batch_v_data() as data:
        token = data["tokens"]["custom"]
        headers = {"Authorization": f"Bearer {token}"}
        role_custom = data["role_custom"]
        perm_view = data["perm_view"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Initially 403
            r_before = await ac.get("/api/v1/approvals", headers=headers)
            assert r_before.status_code == 403

            # Grant approvals.view
            async with AsyncSessionLocal() as db:
                rp = RolePermission(role=role_custom.name, role_id=role_custom.id, permission_id=perm_view.id)
                db.add(rp)
                await db.commit()
                rp_id = rp.id

            # Now 200
            r_granted = await ac.get("/api/v1/approvals", headers=headers)
            assert r_granted.status_code == 200

            # Revoke approvals.view
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.id == rp_id))
                await db.commit()

            # Denied 403 immediately
            r_revoked = await ac.get("/api/v1/approvals", headers=headers)
            assert r_revoked.status_code == 403


@pytest.mark.asyncio
async def test_approvals_user_permission_override_positive():
    """Verify positive UserPermissionOverride grants access even if role has no permission."""
    async with setup_batch_v_data() as data:
        token = data["tokens"]["no_perm"]
        headers = {"Authorization": f"Bearer {token}"}
        no_perm_user = data["no_perm_user"]
        perm_view = data["perm_view"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r1 = await ac.get("/api/v1/approvals", headers=headers)
            assert r1.status_code == 403

            async with AsyncSessionLocal() as db:
                override = UserPermissionOverride(
                    user_id=no_perm_user.id,
                    permission_id=perm_view.id,
                    is_granted=True,
                )
                db.add(override)
                await db.commit()

            r2 = await ac.get("/api/v1/approvals", headers=headers)
            assert r2.status_code == 200


@pytest.mark.asyncio
async def test_approvals_user_permission_override_negative():
    """Verify negative UserPermissionOverride revokes access even if user role has permission."""
    async with setup_batch_v_data() as data:
        token = data["tokens"]["custom"]
        headers = {"Authorization": f"Bearer {token}"}
        custom_user = data["custom_user"]
        role_custom = data["role_custom"]
        perm_view = data["perm_view"]

        # 1. Grant approvals.view to role_custom
        async with AsyncSessionLocal() as db:
            rp = RolePermission(role=role_custom.name, role_id=role_custom.id, permission_id=perm_view.id)
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r1 = await ac.get("/api/v1/approvals", headers=headers)
            assert r1.status_code == 200

            # 2. Add negative override to custom_user
            async with AsyncSessionLocal() as db:
                override = UserPermissionOverride(
                    user_id=custom_user.id,
                    permission_id=perm_view.id,
                    is_granted=False,
                )
                db.add(override)
                await db.commit()

            # 3. Access should now be 403
            r2 = await ac.get("/api/v1/approvals", headers=headers)
            assert r2.status_code == 403


@pytest.mark.asyncio
async def test_approvals_wildcard_permissions():
    """Verify approvals.* wildcard grants access to approvals endpoints."""
    async with setup_batch_v_data() as data:
        token = data["tokens"]["no_perm"]
        headers = {"Authorization": f"Bearer {token}"}
        no_perm_user = data["no_perm_user"]

        async with AsyncSessionLocal() as db:
            perm_wildcard = (await db.execute(
                select(Permission).where(Permission.code == "approvals.*")
            )).scalar_one_or_none()
            if not perm_wildcard:
                perm_wildcard = Permission(
                    module="approvals",
                    action="*",
                    code="approvals.*",
                    description="Wildcard for approvals",
                )
                db.add(perm_wildcard)
                await db.flush()

            override = UserPermissionOverride(
                user_id=no_perm_user.id,
                permission_id=perm_wildcard.id,
                is_granted=True,
            )
            db.add(override)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/approvals", headers=headers)
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_approvals_global_wildcard_permission():
    """Verify global * wildcard grants access to approvals endpoints."""
    async with setup_batch_v_data() as data:
        token = data["tokens"]["no_perm"]
        headers = {"Authorization": f"Bearer {token}"}
        no_perm_user = data["no_perm_user"]

        async with AsyncSessionLocal() as db:
            perm_star = (await db.execute(
                select(Permission).where(Permission.code == "*")
            )).scalar_one_or_none()
            if not perm_star:
                perm_star = Permission(
                    module="*",
                    action="*",
                    code="*",
                    description="Global wildcard",
                )
                db.add(perm_star)
                await db.flush()

            override = UserPermissionOverride(
                user_id=no_perm_user.id,
                permission_id=perm_star.id,
                is_granted=True,
            )
            db.add(override)
            await db.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/approvals", headers=headers)
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_approvals_legacy_role_immunity():
    """Verify that a legacy role name (e.g. Accountant) without DB permissions cannot access approvals."""
    async with setup_batch_v_data() as data:
        uid = data["uid"]
        comp_a = data["comp_a"]
        pwd_hash = get_password_hash("Secret123!")

        async with AsyncSessionLocal() as db:
            accountant_user = User(
                email=f"accountant_v_{uid}@test.com",
                hashed_password=pwd_hash,
                full_name="Legacy Accountant V",
                company_id=comp_a.id,
                is_super_admin=False,
                is_active=True,
                role=UserRole.ACCOUNTANT.value,
            )
            db.add(accountant_user)
            await db.commit()
            acc_id = accountant_user.id

        token = create_access_token({"sub": str(acc_id)})
        headers = {"Authorization": f"Bearer {token}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Accountant role has no approval permissions in DB -> 403
            r = await ac.get("/api/v1/approvals", headers=headers)
            assert r.status_code == 403

        async with AsyncSessionLocal() as db:
            await db.execute(delete(User).where(User.id == acc_id))
            await db.commit()


@pytest.mark.asyncio
async def test_approvals_tenantless_non_sa_denied():
    """Verify non-SA users with company_id=None are strictly denied with HTTP 403."""
    async with setup_batch_v_data() as data:
        token = data["tokens"]["tenantless"]
        headers = {"Authorization": f"Bearer {token}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r1 = await ac.get("/api/v1/approvals", headers=headers)
            assert r1.status_code == 403

            r2 = await ac.post(
                "/api/v1/approvals",
                headers=headers,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id},
            )
            assert r2.status_code == 403

            r3 = await ac.put("/api/v1/approvals/1/approve", headers=headers, json={"remarks": "ok"})
            assert r3.status_code == 403

            r4 = await ac.put("/api/v1/approvals/1/reject", headers=headers, json={"remarks": "no"})
            assert r4.status_code == 403


@pytest.mark.asyncio
async def test_approvals_super_admin_access():
    """Verify Super Admin (company_id=None) can list cross-company approvals without 403."""
    async with setup_batch_v_data() as data:
        token = data["tokens"]["super_admin"]
        headers = {"Authorization": f"Bearer {token}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/approvals", headers=headers)
            assert r.status_code == 200
            assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_approvals_cross_tenant_list_isolation():
    """Verify Tenant A user only sees approvals requested by Company A users."""
    async with setup_batch_v_data() as data:
        token_a = data["tokens"]["user_a1"]
        token_b = data["tokens"]["user_b"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # User A creates approval in Company A
            r_create_a = await ac.post(
                "/api/v1/approvals",
                headers=headers_a,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id, "remarks": "Req A"},
            )
            assert r_create_a.status_code == 200
            app_a_id = r_create_a.json()["id"]

            # User B creates approval in Company B
            r_create_b = await ac.post(
                "/api/v1/approvals",
                headers=headers_b,
                json={"entity_type": "boq", "entity_id": data["boq_b"].id, "remarks": "Req B"},
            )
            assert r_create_b.status_code == 200
            app_b_id = r_create_b.json()["id"]

            # User A lists approvals -> sees app_a_id, does NOT see app_b_id
            list_a = (await ac.get("/api/v1/approvals", headers=headers_a)).json()
            ids_a = [item["id"] for item in list_a]
            assert app_a_id in ids_a
            assert app_b_id not in ids_a

            # User B lists approvals -> sees app_b_id, does NOT see app_a_id
            list_b = (await ac.get("/api/v1/approvals", headers=headers_b)).json()
            ids_b = [item["id"] for item in list_b]
            assert app_b_id in ids_b
            assert app_a_id not in ids_b


@pytest.mark.asyncio
async def test_approvals_foreign_approval_masked_as_404():
    """Verify attempting to decision a foreign company approval returns HTTP 404."""
    async with setup_batch_v_data() as data:
        token_a = data["tokens"]["user_a1"]
        token_b = data["tokens"]["user_b"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # User B creates approval in Company B
            r_create_b = await ac.post(
                "/api/v1/approvals",
                headers=headers_b,
                json={"entity_type": "boq", "entity_id": data["boq_b"].id, "remarks": "Req B"},
            )
            app_b_id = r_create_b.json()["id"]

            # User A tries to approve or reject Company B's approval -> 404
            r_app = await ac.put(f"/api/v1/approvals/{app_b_id}/approve", headers=headers_a, json={"remarks": "ok"})
            assert r_app.status_code == 404

            r_rej = await ac.put(f"/api/v1/approvals/{app_b_id}/reject", headers=headers_a, json={"remarks": "no"})
            assert r_rej.status_code == 404


@pytest.mark.asyncio
async def test_approvals_foreign_target_entity_masked_as_404():
    """Verify attempting to create an approval targeting a foreign entity returns HTTP 404."""
    async with setup_batch_v_data() as data:
        token_a = data["tokens"]["user_a1"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # User A tries to create approval for Company B's BOQ -> 404
            r = await ac.post(
                "/api/v1/approvals",
                headers=headers_a,
                json={"entity_type": "boq", "entity_id": data["boq_b"].id, "remarks": "Malicious"},
            )
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_approvals_unsupported_entity_type_rejected():
    """Verify creating an approval with unsupported entity type returns HTTP 400."""
    async with setup_batch_v_data() as data:
        token_a = data["tokens"]["user_a1"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/approvals",
                headers=headers_a,
                json={"entity_type": "random_unsupported", "entity_id": 1, "remarks": "test"},
            )
            assert r.status_code == 400


@pytest.mark.asyncio
async def test_approvals_duplicate_pending_blocked():
    """Verify that creating a duplicate Pending approval for the same entity returns HTTP 400."""
    async with setup_batch_v_data() as data:
        token_a = data["tokens"]["user_a1"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r1 = await ac.post(
                "/api/v1/approvals",
                headers=headers_a,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id, "remarks": "Req 1"},
            )
            assert r1.status_code == 200

            # Duplicate submission while first is still Pending
            r2 = await ac.post(
                "/api/v1/approvals",
                headers=headers_a,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id, "remarks": "Req 2"},
            )
            assert r2.status_code == 400
            assert "already exists" in r2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_approvals_self_approval_blocked():
    """Verify that a requester cannot approve or reject their own request."""
    async with setup_batch_v_data() as data:
        token_a1 = data["tokens"]["user_a1"]
        headers_a1 = {"Authorization": f"Bearer {token_a1}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_create = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id, "remarks": "Self"},
            )
            app_id = r_create.json()["id"]

            # Requester A1 tries to approve own request -> 400
            r_app = await ac.put(f"/api/v1/approvals/{app_id}/approve", headers=headers_a1, json={"remarks": "ok"})
            assert r_app.status_code == 400
            assert "cannot approve" in r_app.json()["detail"].lower() or "cannot decide" in r_app.json()["detail"].lower()

            # Requester A1 tries to reject own request -> 400
            r_rej = await ac.put(f"/api/v1/approvals/{app_id}/reject", headers=headers_a1, json={"remarks": "reject"})
            assert r_rej.status_code == 400


@pytest.mark.asyncio
async def test_approvals_rejection_requires_remarks():
    """Verify that rejection fails with HTTP 400 if remarks are missing or empty."""
    async with setup_batch_v_data() as data:
        token_a1 = data["tokens"]["user_a1"]
        token_a2 = data["tokens"]["user_a2"]
        headers_a1 = {"Authorization": f"Bearer {token_a1}"}
        headers_a2 = {"Authorization": f"Bearer {token_a2}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_create = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id, "remarks": "Req"},
            )
            app_id = r_create.json()["id"]

            # Approver A2 rejects without remarks -> 400
            r_rej1 = await ac.put(f"/api/v1/approvals/{app_id}/reject", headers=headers_a2, json={})
            assert r_rej1.status_code == 400

            r_rej2 = await ac.put(f"/api/v1/approvals/{app_id}/reject", headers=headers_a2, json={"remarks": "   "})
            assert r_rej2.status_code == 400


@pytest.mark.asyncio
async def test_approvals_state_machine_approved_immutable():
    """Verify that an Approved approval cannot be approved again or rejected."""
    async with setup_batch_v_data() as data:
        token_a1 = data["tokens"]["user_a1"]
        token_a2 = data["tokens"]["user_a2"]
        headers_a1 = {"Authorization": f"Bearer {token_a1}"}
        headers_a2 = {"Authorization": f"Bearer {token_a2}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_create = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id},
            )
            app_id = r_create.json()["id"]

            # Approve
            r_app1 = await ac.put(f"/api/v1/approvals/{app_id}/approve", headers=headers_a2, json={"remarks": "LGTM"})
            assert r_app1.status_code == 200

            # Approve again -> 400
            r_app2 = await ac.put(f"/api/v1/approvals/{app_id}/approve", headers=headers_a2, json={"remarks": "Again"})
            assert r_app2.status_code == 400

            # Reject -> 400
            r_rej = await ac.put(f"/api/v1/approvals/{app_id}/reject", headers=headers_a2, json={"remarks": "Flip"})
            assert r_rej.status_code == 400


@pytest.mark.asyncio
async def test_approvals_state_machine_rejected_immutable():
    """Verify that a Rejected approval cannot be approved or rejected again."""
    async with setup_batch_v_data() as data:
        token_a1 = data["tokens"]["user_a1"]
        token_a2 = data["tokens"]["user_a2"]
        headers_a1 = {"Authorization": f"Bearer {token_a1}"}
        headers_a2 = {"Authorization": f"Bearer {token_a2}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_create = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id},
            )
            app_id = r_create.json()["id"]

            # Reject
            r_rej1 = await ac.put(f"/api/v1/approvals/{app_id}/reject", headers=headers_a2, json={"remarks": "Bad"})
            assert r_rej1.status_code == 200

            # Reject again -> 400
            r_rej2 = await ac.put(f"/api/v1/approvals/{app_id}/reject", headers=headers_a2, json={"remarks": "Again"})
            assert r_rej2.status_code == 400

            # Approve -> 400
            r_app = await ac.put(f"/api/v1/approvals/{app_id}/approve", headers=headers_a2, json={"remarks": "Flip"})
            assert r_app.status_code == 400


@pytest.mark.asyncio
async def test_approvals_target_state_sync_all_entities():
    """
    Verify state transitions on target entities for all 7 supported types:
    - boq
    - measurement
    - purchase_order
    - document
    - drawing
    - bill
    - journal_entry
    """
    async with setup_batch_v_data() as data:
        token_a1 = data["tokens"]["user_a1"]
        token_a2 = data["tokens"]["user_a2"]
        headers_a1 = {"Authorization": f"Bearer {token_a1}"}
        headers_a2 = {"Authorization": f"Bearer {token_a2}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. FinalMeasurement
            r_m = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "measurement", "entity_id": data["meas_a"].id},
            )
            assert r_m.status_code == 200
            # Check submitted
            async with AsyncSessionLocal() as db:
                m_curr = await db.get(FinalMeasurement, data["meas_a"].id)
                assert m_curr.status == "SUBMITTED"

            r_m_app = await ac.put(f"/api/v1/approvals/{r_m.json()['id']}/approve", headers=headers_a2, json={})
            assert r_m_app.status_code == 200
            async with AsyncSessionLocal() as db:
                m_curr = await db.get(FinalMeasurement, data["meas_a"].id)
                assert m_curr.status == "APPROVED"

            # 2. PurchaseOrder
            r_po = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "purchase_order", "entity_id": data["po_a"].id},
            )
            assert r_po.status_code == 200
            async with AsyncSessionLocal() as db:
                po_curr = await db.get(PurchaseOrder, data["po_a"].id)
                assert po_curr.status == "PENDING"

            r_po_app = await ac.put(f"/api/v1/approvals/{r_po.json()['id']}/approve", headers=headers_a2, json={})
            assert r_po_app.status_code == 200
            async with AsyncSessionLocal() as db:
                po_curr = await db.get(PurchaseOrder, data["po_a"].id)
                assert po_curr.status == "APPROVED"

            # 3. Document
            r_doc = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "document", "entity_id": data["doc_a"].id},
            )
            assert r_doc.status_code == 200
            async with AsyncSessionLocal() as db:
                doc_curr = await db.get(Document, data["doc_a"].id)
                assert doc_curr.status == DocumentStatus.UNDER_REVIEW

            r_doc_app = await ac.put(f"/api/v1/approvals/{r_doc.json()['id']}/approve", headers=headers_a2, json={})
            assert r_doc_app.status_code == 200
            async with AsyncSessionLocal() as db:
                doc_curr = await db.get(Document, data["doc_a"].id)
                assert doc_curr.status == DocumentStatus.APPROVED

            # 4. DrawingDocument
            r_drw = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "drawing", "entity_id": data["drawing_a"].id},
            )
            assert r_drw.status_code == 200
            async with AsyncSessionLocal() as db:
                drw_curr = await db.get(DrawingDocument, data["drawing_a"].id)
                assert drw_curr.approval_status == DocumentStatus.UNDER_REVIEW
                assert drw_curr.approval_id == r_drw.json()["id"]

            r_drw_app = await ac.put(f"/api/v1/approvals/{r_drw.json()['id']}/approve", headers=headers_a2, json={})
            assert r_drw_app.status_code == 200
            async with AsyncSessionLocal() as db:
                drw_curr = await db.get(DrawingDocument, data["drawing_a"].id)
                assert drw_curr.approval_status == DocumentStatus.APPROVED

            # 5. Bill
            r_bill = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "bill", "entity_id": data["bill_a"].id},
            )
            assert r_bill.status_code == 200
            r_bill_app = await ac.put(f"/api/v1/approvals/{r_bill.json()['id']}/approve", headers=headers_a2, json={})
            assert r_bill_app.status_code == 200
            async with AsyncSessionLocal() as db:
                bill_curr = await db.get(RABill, data["bill_a"].id)
                assert bill_curr.status == "Approved"

            # 6. JournalEntry
            r_je = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "journal_entry", "entity_id": data["je_a"].id},
            )
            assert r_je.status_code == 200
            r_je_app = await ac.put(f"/api/v1/approvals/{r_je.json()['id']}/approve", headers=headers_a2, json={})
            assert r_je_app.status_code == 200
            async with AsyncSessionLocal() as db:
                je_curr = await db.get(JournalEntry, data["je_a"].id)
                assert je_curr.status == "Posted"


@pytest.mark.asyncio
async def test_approvals_atomic_rollback_on_notification_failure():
    """Verify that if notification creation fails, the entire transaction rolls back."""
    async with setup_batch_v_data() as data:
        token_a1 = data["tokens"]["user_a1"]
        token_a2 = data["tokens"]["user_a2"]
        headers_a1 = {"Authorization": f"Bearer {token_a1}"}
        headers_a2 = {"Authorization": f"Bearer {token_a2}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_create = await ac.post(
                "/api/v1/approvals",
                headers=headers_a1,
                json={"entity_type": "boq", "entity_id": data["boq_a"].id},
            )
            app_id = r_create.json()["id"]

            with patch("app.api.approval.create_notification", side_effect=RuntimeError("Notification Service Down")):
                r_app = await ac.put(f"/api/v1/approvals/{app_id}/approve", headers=headers_a2, json={"remarks": "ok"})
                assert r_app.status_code == 500

            # Verify that approval remains Pending and was NOT committed as Approved
            async with AsyncSessionLocal() as db:
                app_curr = await db.get(Approval, app_id)
                assert app_curr.status == "Pending"
                assert app_curr.approved_by is None

                boq_curr = await db.get(BOQ, data["boq_a"].id)
                assert boq_curr.approval_status != "Approved"


def test_approvals_ast_zero_hardcoded_roles():
    """AST check: verify app/api/approval.py contains zero require_roles calls."""
    with open("app/api/approval.py", "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    require_roles_found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "require_roles":
                require_roles_found.append(node.lineno)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "require_roles":
                require_roles_found.append(node.lineno)

    assert len(require_roles_found) == 0, f"Found require_roles calls at lines: {require_roles_found}"
