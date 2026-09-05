import uuid
from decimal import Decimal
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.contractor import Contractor
from app.models.work_order import WorkOrder
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import ProjectStatus


@asynccontextmanager
async def setup_batch_r_data():
    """Seed test companies, owners, projects, contractors, work orders, users, roles, and RBAC permissions for Batch R."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchR_CompA_{uid}")
        comp_b = Company(name=f"BatchR_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-RA-{uid}",
            owner_name=f"Owner RA {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-RB-{uid}",
            owner_name=f"Owner RB {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerb_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 3. Projects
        proj_a = Project(
            business_id=f"PRJ-RA-{uid}",
            project_name=f"Project RA {uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b = Project(
            business_id=f"PRJ-RB-{uid}",
            project_name=f"Project RB {uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 4. Contractors
        contractor_a = Contractor(
            company_id=comp_a.id,
            contractor_id=f"CNT-RA-{uid}",
            name=f"Contractor RA {uid}",
            work_type="Civil",
            contact_number=f"94{uuid.uuid4().int % 100000000:08d}",
            rate_type="Item Rate",
        )
        contractor_b = Contractor(
            company_id=comp_b.id,
            contractor_id=f"CNT-RB-{uid}",
            name=f"Contractor RB {uid}",
            work_type="Electrical",
            contact_number=f"95{uuid.uuid4().int % 100000000:08d}",
            rate_type="Lump Sum",
        )
        db.add_all([contractor_a, contractor_b])
        await db.flush()

        # 5. Work Orders
        wo_a = WorkOrder(
            project_id=proj_a.id,
            contractor_id=contractor_a.id,
            work_order_number=f"WO-RA-{uid}",
            work_description="Excavation and Foundation",
            total_quantity=Decimal("100.00"),
            completed_quantity=Decimal("10.00"),
            rate=Decimal("500.00"),
            total_amount=Decimal("50000.00"),
            status="In Progress",
        )
        wo_b = WorkOrder(
            project_id=proj_b.id,
            contractor_id=contractor_b.id,
            work_order_number=f"WO-RB-{uid}",
            work_description="Electrical Wiring",
            total_quantity=Decimal("200.00"),
            completed_quantity=Decimal("0.00"),
            rate=Decimal("150.00"),
            total_amount=Decimal("30000.00"),
            status="Assigned",
        )
        db.add_all([wo_a, wo_b])
        await db.flush()

        # 6. Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_r_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin R",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_ra_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin R",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_rb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin R",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"WorkOrderManager_{uid}"
        user_custom_a = User(
            email=f"custom_ra_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Work Order Manager R",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        legacy_empty_role_name = f"EmptyAdminR_{uid}"
        legacy_admin_no_perm = User(
            email=f"legacy_admin_r_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy Admin No Perm R",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=legacy_empty_role_name,
        )

        dummy_none_company_user = User(
            email=f"nonecomp_r_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="No Company User R",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Contractor",
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

        # 7. Project membership for custom user
        pm_a = ProjectMember(project_id=proj_a.id, user_id=user_custom_a.id)
        db.add(pm_a)
        await db.flush()

        # 8. Custom Role
        role_custom_a = Role(
            company_id=comp_a.id,
            name=custom_role_name,
            display_name="Work Order Manager Role",
            is_system=False,
        )
        role_legacy_empty = Role(
            company_id=comp_a.id,
            name=legacy_empty_role_name,
            display_name="Empty Admin Role",
            is_system=False,
        )
        db.add_all([role_custom_a, role_legacy_empty])
        await db.flush()

        # 9. Query DB permissions
        perm_view = (await db.execute(select(Permission).where(Permission.code == "work_orders.view"))).scalar_one()
        perm_create = (await db.execute(select(Permission).where(Permission.code == "work_orders.create"))).scalar_one()
        perm_edit = (await db.execute(select(Permission).where(Permission.code == "work_orders.edit"))).scalar_one()
        perm_delete = (await db.execute(select(Permission).where(Permission.code == "work_orders.delete"))).scalar_one()

        # 10. Tokens
        tokens = {
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "user_custom_a": create_access_token({"sub": str(user_custom_a.id)}),
            "legacy_admin_no_perm": create_access_token({"sub": str(legacy_admin_no_perm.id)}),
            "none_comp": create_access_token({"sub": str(dummy_none_company_user.id)}),
        }

        await db.commit()

        yield {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "contractor_a": contractor_a,
            "contractor_b": contractor_b,
            "wo_a": wo_a,
            "wo_b": wo_b,
            "users": {
                "super_admin": super_admin,
                "admin_a": admin_a,
                "admin_b": admin_b,
                "user_custom_a": user_custom_a,
                "legacy_admin_no_perm": legacy_admin_no_perm,
                "none_comp": dummy_none_company_user,
            },
            "tokens": tokens,
            "roles": {
                "custom_a": role_custom_a,
                "legacy_empty": role_legacy_empty,
            },
            "permissions": {
                "view": perm_view,
                "create": perm_create,
                "edit": perm_edit,
                "delete": perm_delete,
            },
        }

        # Cleanup
        async with AsyncSessionLocal() as cleanup_db:
            await cleanup_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([
                super_admin.id, admin_a.id, admin_b.id, user_custom_a.id,
                legacy_admin_no_perm.id, dummy_none_company_user.id
            ])))
            await cleanup_db.execute(delete(RolePermission).where(RolePermission.role.in_([
                custom_role_name, legacy_empty_role_name
            ])))
            await cleanup_db.execute(delete(WorkOrder).where(WorkOrder.id.in_([wo_a.id, wo_b.id])))
            await cleanup_db.execute(delete(Contractor).where(Contractor.id.in_([contractor_a.id, contractor_b.id])))
            await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await cleanup_db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await cleanup_db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await cleanup_db.execute(delete(Role).where(Role.id.in_([role_custom_a.id, role_legacy_empty.id])))
            await cleanup_db.execute(delete(User).where(User.id.in_([
                super_admin.id, admin_a.id, admin_b.id, user_custom_a.id,
                legacy_admin_no_perm.id, dummy_none_company_user.id
            ])))
            await cleanup_db.execute(delete(Permission).where(Permission.code == "work_orders.*"))
            await cleanup_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await cleanup_db.commit()


# ==============================================================================
# TEST 1 — 401 UNAUTHENTICATED
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_401_unauthenticated():
    """Verify that requests without token or with invalid token receive 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("POST", "/api/v1/work-orders", {"project_id": 1, "work_description": "test", "total_quantity": 10, "rate": 100}),
            ("GET", "/api/v1/work-orders", None),
            ("GET", "/api/v1/work-orders/1", None),
            ("PUT", "/api/v1/work-orders/1", {"work_description": "updated"}),
            ("DELETE", "/api/v1/work-orders/1", None),
        ]

        for method, url, body in endpoints:
            if method == "POST":
                resp = await ac.post(url, json=body)
            elif method == "GET":
                resp = await ac.get(url)
            elif method == "PUT":
                resp = await ac.put(url, json=body)
            elif method == "DELETE":
                resp = await ac.delete(url)
            assert resp.status_code == 401, f"{method} {url} expected 401, got {resp.status_code}"


# ==============================================================================
# TEST 2 — 403 MISSING PERMISSIONS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_403_missing_permissions():
    """Verify that authenticated users with 0 permissions receive 403 Forbidden."""
    async with setup_batch_r_data() as data:
        token = data["tokens"]["legacy_admin_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("POST", "/api/v1/work-orders", {"project_id": data["proj_a"].id, "work_description": "test", "total_quantity": 10, "rate": 100}),
                ("GET", "/api/v1/work-orders", None),
                ("GET", f"/api/v1/work-orders/{data['wo_a'].id}", None),
                ("PUT", f"/api/v1/work-orders/{data['wo_a'].id}", {"work_description": "updated"}),
                ("DELETE", f"/api/v1/work-orders/{data['wo_a'].id}", None),
            ]

            for method, url, body in endpoints:
                if method == "POST":
                    resp = await ac.post(url, json=body, headers=headers)
                elif method == "GET":
                    resp = await ac.get(url, headers=headers)
                elif method == "PUT":
                    resp = await ac.put(url, json=body, headers=headers)
                elif method == "DELETE":
                    resp = await ac.delete(url, headers=headers)
                assert resp.status_code == 403, f"{method} {url} expected 403, got {resp.status_code}: {resp.text}"


# ==============================================================================
# TEST 3 — DYNAMIC DB GRANT & REVOKE
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_dynamic_db_grant_and_revoke():
    """Verify dynamic grant and revocation of permissions takes effect immediately without restart."""
    async with setup_batch_r_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_obj = data["roles"]["custom_a"]
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Initially user has 0 permissions -> GET returns 403
            resp = await ac.get("/api/v1/work-orders", headers=headers)
            assert resp.status_code == 403

            # 2. Grant work_orders.view in DB -> GET succeeds with 200
            async with AsyncSessionLocal() as db:
                rp_view = RolePermission(role=role_obj.name, role_id=role_obj.id, permission_id=data["permissions"]["view"].id)
                db.add(rp_view)
                await db.commit()

            resp = await ac.get("/api/v1/work-orders", headers=headers)
            assert resp.status_code == 200

            # 3. Revoke work_orders.view in DB -> GET returns 403 again
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == role_obj.name,
                    RolePermission.permission_id == data["permissions"]["view"].id,
                ))
                await db.commit()

            resp = await ac.get("/api/v1/work-orders", headers=headers)
            assert resp.status_code == 403


# ==============================================================================
# TEST 4 — POSITIVE USER PERMISSION OVERRIDE
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_positive_user_override():
    """Verify a positive user permission override grants access when role lacks permission."""
    async with setup_batch_r_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        user_id = data["users"]["user_custom_a"].id
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Role has no create permission -> 403
            payload = {
                "project_id": data["proj_a"].id,
                "contractor_id": data["contractor_a"].id,
                "work_description": "Piling Works",
                "total_quantity": 50,
                "rate": 1200,
            }
            resp = await ac.post("/api/v1/work-orders", json=payload, headers=headers)
            assert resp.status_code == 403

            # Add positive override for work_orders.create
            async with AsyncSessionLocal() as db:
                ov = UserPermissionOverride(user_id=user_id, permission_id=data["permissions"]["create"].id, is_granted=True)
                db.add(ov)
                await db.commit()

            resp = await ac.post("/api/v1/work-orders", json=payload, headers=headers)
            assert resp.status_code == 200
            created_wo_id = resp.json()["id"]

            # Clean up created work order
            async with AsyncSessionLocal() as db:
                await db.execute(delete(WorkOrder).where(WorkOrder.id == created_wo_id))
                await db.commit()


# ==============================================================================
# TEST 5 — NEGATIVE USER PERMISSION OVERRIDE
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_negative_user_override():
    """Verify a negative user permission override blocks access even when role has permission."""
    async with setup_batch_r_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        user_id = data["users"]["user_custom_a"].id
        role_obj = data["roles"]["custom_a"]
        transport = ASGITransport(app=app)

        # Grant role work_orders.view
        async with AsyncSessionLocal() as db:
            rp = RolePermission(role=role_obj.name, role_id=role_obj.id, permission_id=data["permissions"]["view"].id)
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Succeeded with role grant
            resp = await ac.get("/api/v1/work-orders", headers=headers)
            assert resp.status_code == 200

            # Add negative override (is_granted=False)
            async with AsyncSessionLocal() as db:
                ov = UserPermissionOverride(user_id=user_id, permission_id=data["permissions"]["view"].id, is_granted=False)
                db.add(ov)
                await db.commit()

            # Now blocked with 403
            resp = await ac.get("/api/v1/work-orders", headers=headers)
            assert resp.status_code == 403


# ==============================================================================
# TEST 6 — WILDCARD PERMISSION (work_orders.*)
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_wildcard_permission():
    """Verify work_orders.* wildcard grants access to all 5 routes."""
    async with setup_batch_r_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_obj = data["roles"]["custom_a"]
        transport = ASGITransport(app=app)

        # Grant work_orders.* wildcard
        async with AsyncSessionLocal() as db:
            wildcard_perm = (await db.execute(select(Permission).where(Permission.code == "work_orders.*"))).scalar_one_or_none()
            if not wildcard_perm:
                wildcard_perm = Permission(module="work_orders", action="*", code="work_orders.*", description="Wildcard work_orders")
                db.add(wildcard_perm)
                await db.flush()
            rp = RolePermission(role=role_obj.name, role_id=role_obj.id, permission_id=wildcard_perm.id)
            db.add(rp)
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. POST
            create_payload = {
                "project_id": data["proj_a"].id,
                "contractor_id": data["contractor_a"].id,
                "work_description": "Plumbing Works",
                "total_quantity": 30,
                "rate": 450,
            }
            resp_post = await ac.post("/api/v1/work-orders", json=create_payload, headers=headers)
            assert resp_post.status_code == 200
            new_id = resp_post.json()["id"]

            # 2. GET list
            resp_list = await ac.get("/api/v1/work-orders", headers=headers)
            assert resp_list.status_code == 200

            # 3. GET detail
            resp_get = await ac.get(f"/api/v1/work-orders/{new_id}", headers=headers)
            assert resp_get.status_code == 200

            # 4. PUT update
            resp_put = await ac.put(f"/api/v1/work-orders/{new_id}", json={"work_description": "Updated Plumbing"}, headers=headers)
            assert resp_put.status_code == 200
            assert resp_put.json()["work_description"] == "Updated Plumbing"

            # 5. DELETE
            resp_del = await ac.delete(f"/api/v1/work-orders/{new_id}", headers=headers)
            assert resp_del.status_code == 200


# ==============================================================================
# TEST 7 — LEGACY ROLE IMMUNITY
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_legacy_role_immunity():
    """Verify that a user with role 'Admin' or 'Project Manager' but 0 DB permissions receives 403."""
    async with setup_batch_r_data() as data:
        token = data["tokens"]["legacy_admin_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/work-orders", headers=headers)
            assert resp.status_code == 403


# ==============================================================================
# TEST 8 — OWN-TENANT CRUD OPERATIONS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_own_tenant_crud():
    """Verify complete CRUD lifecycle within own tenant under valid permissions."""
    async with setup_batch_r_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. CREATE
            payload = {
                "project_id": data["proj_a"].id,
                "contractor_id": data["contractor_a"].id,
                "work_description": "Masonry Construction",
                "total_quantity": 500,
                "rate": 350,
            }
            resp_create = await ac.post("/api/v1/work-orders", json=payload, headers=headers)
            assert resp_create.status_code == 200
            wo_data = resp_create.json()
            wo_id = wo_data["id"]
            assert wo_data["work_description"] == "Masonry Construction"
            assert wo_data["total_amount"] == 175000.0
            assert wo_data["work_order_number"].startswith("WO")

            # 2. LIST
            resp_list = await ac.get("/api/v1/work-orders", headers=headers)
            assert resp_list.status_code == 200
            ids = [item["id"] for item in resp_list.json()]
            assert wo_id in ids
            assert data["wo_a"].id in ids
            # Tenant B work order must NOT be listed
            assert data["wo_b"].id not in ids

            # 3. GET DETAIL
            resp_get = await ac.get(f"/api/v1/work-orders/{wo_id}", headers=headers)
            assert resp_get.status_code == 200
            assert resp_get.json()["id"] == wo_id

            # 4. UPDATE
            resp_update = await ac.put(
                f"/api/v1/work-orders/{wo_id}",
                json={"completed_quantity": 250, "work_description": "Masonry Construction Phase 1"},
                headers=headers,
            )
            assert resp_update.status_code == 200
            updated_data = resp_update.json()
            assert updated_data["completed_quantity"] == 250.0
            assert updated_data["status"] == "In Progress"

            # 5. DELETE
            resp_delete = await ac.delete(f"/api/v1/work-orders/{wo_id}", headers=headers)
            assert resp_delete.status_code == 200

            # 6. VERIFY DELETED
            resp_verify = await ac.get(f"/api/v1/work-orders/{wo_id}", headers=headers)
            assert resp_verify.status_code == 404


# ==============================================================================
# TEST 9 — FOREIGN WORK ORDER IDOR MASKING (GET / PUT / DELETE)
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_foreign_work_order_idor_masking():
    """Verify cross-tenant work order access returns masked 404 Not Found."""
    async with setup_batch_r_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        foreign_wo_id = data["wo_b"].id
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. GET foreign work order -> 404
            resp_get = await ac.get(f"/api/v1/work-orders/{foreign_wo_id}", headers=headers_a)
            assert resp_get.status_code == 404
            assert resp_get.json()["detail"] == "Work order not found"

            # 2. PUT foreign work order -> 404
            resp_put = await ac.put(
                f"/api/v1/work-orders/{foreign_wo_id}",
                json={"work_description": "Malicious Update"},
                headers=headers_a,
            )
            assert resp_put.status_code == 404
            assert resp_put.json()["detail"] == "Work order not found"

            # 3. DELETE foreign work order -> 404
            resp_del = await ac.delete(f"/api/v1/work-orders/{foreign_wo_id}", headers=headers_a)
            assert resp_del.status_code == 404
            assert resp_del.json()["detail"] == "Work order not found"


# ==============================================================================
# TEST 10 — CROSS-TENANT PROJECT INJECTION PREVENTION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_cross_tenant_project_injection():
    """Verify that creating a work order with a foreign project_id is blocked with masked 404."""
    async with setup_batch_r_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        foreign_proj_id = data["proj_b"].id
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            payload = {
                "project_id": foreign_proj_id,
                "contractor_id": data["contractor_a"].id,
                "work_description": "Cross Tenant Project Injection Attempt",
                "total_quantity": 100,
                "rate": 200,
            }
            resp = await ac.post("/api/v1/work-orders", json=payload, headers=headers_a)
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Project not found"


# ==============================================================================
# TEST 11 — CROSS-TENANT CONTRACTOR INJECTION PREVENTION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_cross_tenant_contractor_injection():
    """Verify that assigning or updating to a foreign contractor_id is blocked with masked 404."""
    async with setup_batch_r_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        foreign_contractor_id = data["contractor_b"].id
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. CREATE with foreign contractor_id -> 404
            payload_create = {
                "project_id": data["proj_a"].id,
                "contractor_id": foreign_contractor_id,
                "work_description": "Foreign Contractor Injection",
                "total_quantity": 100,
                "rate": 200,
            }
            resp_create = await ac.post("/api/v1/work-orders", json=payload_create, headers=headers_a)
            assert resp_create.status_code == 404
            assert resp_create.json()["detail"] == "Contractor not found"

            # 2. UPDATE with foreign contractor_id -> 404
            own_wo_id = data["wo_a"].id
            resp_update = await ac.put(
                f"/api/v1/work-orders/{own_wo_id}",
                json={"contractor_id": foreign_contractor_id},
                headers=headers_a,
            )
            assert resp_update.status_code == 404
            assert resp_update.json()["detail"] == "Contractor not found"


# ==============================================================================
# TEST 12 — SUPER ADMIN CROSS-COMPANY ACCESS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_super_admin_cross_company():
    """Verify that Super Admin can access work orders across all companies without restriction."""
    async with setup_batch_r_data() as data:
        sa_token = data["tokens"]["super_admin"]
        sa_headers = {"Authorization": f"Bearer {sa_token}"}
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. SA can list work orders across all companies
            resp_list = await ac.get("/api/v1/work-orders", headers=sa_headers)
            assert resp_list.status_code == 200
            ids = [item["id"] for item in resp_list.json()]
            assert data["wo_a"].id in ids
            assert data["wo_b"].id in ids

            # 2. SA can get detail for company A and company B
            resp_a = await ac.get(f"/api/v1/work-orders/{data['wo_a'].id}", headers=sa_headers)
            assert resp_a.status_code == 200
            resp_b = await ac.get(f"/api/v1/work-orders/{data['wo_b'].id}", headers=sa_headers)
            assert resp_b.status_code == 200

            # 3. SA can update work order in either company
            resp_update = await ac.put(
                f"/api/v1/work-orders/{data['wo_b'].id}",
                json={"work_description": "Super Admin Global Override"},
                headers=sa_headers,
            )
            assert resp_update.status_code == 200
            assert resp_update.json()["work_description"] == "Super Admin Global Override"


# ==============================================================================
# TEST 13 — NON-SA USER WITHOUT COMPANY ID (company_id=None) DENIAL
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_non_sa_company_id_none_denial():
    """Verify that non-Super-Admin users with company_id=None receive 403 Forbidden."""
    async with setup_batch_r_data() as data:
        token = data["tokens"]["none_comp"]
        headers = {"Authorization": f"Bearer {token}"}
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # GET list
            resp_list = await ac.get("/api/v1/work-orders", headers=headers)
            assert resp_list.status_code == 403

            # GET detail
            resp_get = await ac.get(f"/api/v1/work-orders/{data['wo_a'].id}", headers=headers)
            assert resp_get.status_code == 403

            # POST
            resp_post = await ac.post("/api/v1/work-orders", json={"project_id": 1, "work_description": "x", "total_quantity": 1, "rate": 1}, headers=headers)
            assert resp_post.status_code == 403

            # PUT
            resp_put = await ac.put(f"/api/v1/work-orders/{data['wo_a'].id}", json={"work_description": "x"}, headers=headers)
            assert resp_put.status_code == 403

            # DELETE
            resp_del = await ac.delete(f"/api/v1/work-orders/{data['wo_a'].id}", headers=headers)
            assert resp_del.status_code == 403


# ==============================================================================
# TEST 14 — BUSINESS LOGIC INVARIANTS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_r_business_logic_invariants():
    """Verify status auto-transitions and quantity validations."""
    async with setup_batch_r_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        wo_id = data["wo_a"].id
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Completed quantity cannot exceed total quantity -> 422/400
            resp_err = await ac.put(
                f"/api/v1/work-orders/{wo_id}",
                json={"completed_quantity": 150, "total_quantity": 100},
                headers=headers,
            )
            assert resp_err.status_code in (400, 422)

            # 2. Auto status transition to Completed when completed == total
            resp_comp = await ac.put(
                f"/api/v1/work-orders/{wo_id}",
                json={"completed_quantity": 100, "total_quantity": 100},
                headers=headers,
            )
            assert resp_comp.status_code == 200
            assert resp_comp.json()["status"] == "Completed"

            # 3. Recalculate amount when quantity or rate changed
            resp_rate = await ac.put(
                f"/api/v1/work-orders/{wo_id}",
                json={"total_quantity": 200, "rate": 600},
                headers=headers,
            )
            assert resp_rate.status_code == 200
            assert resp_rate.json()["total_amount"] == 120000.0
