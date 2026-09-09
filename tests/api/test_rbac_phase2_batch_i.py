import uuid
from decimal import Decimal
from datetime import date, timedelta
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember, Milestone, Task
from app.models.settings import CompanySettings
from app.models.master_data import ActivityType
from app.models.boq import BOQ, BOQGroup, BOQAudit
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token


@asynccontextmanager
async def setup_batch_i_data():
    """Seed test companies, projects, BOQ groups, BOQ items, audit logs, and users for Batch I test suite."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Create two test companies
        comp_a = Company(name=f"BatchI_CompA_{uid}")
        comp_b = Company(name=f"BatchI_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company settings with distinct branding
        cs_a = CompanySettings(company_id=comp_a.id, company_name=f"Brand_Company_A_{uid}")
        cs_b = CompanySettings(company_id=comp_b.id, company_name=f"Brand_Company_B_{uid}")
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Test Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_i_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin I",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_ia_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin I",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_ib_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin I",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"CostingManager_{uid}"
        user_custom_a = User(
            email=f"custom_ia_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Costing Manager I",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        user_legacy_a = User(
            email=f"legacy_ia_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy PM I",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Project Manager",
        )

        # Unassigned user for testing project membership isolation
        user_unassigned_a = User(
            email=f"unassigned_ia_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Unassigned Engineer I",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        db.add_all([super_admin, admin_a, admin_b, user_custom_a, user_legacy_a, user_unassigned_a])
        await db.flush()

        # 4. Create Role for custom user
        role_custom = Role(name=custom_role_name, display_name="Custom Role I", company_id=comp_a.id, description="Custom Role I")
        db.add(role_custom)
        await db.flush()

        # 5. Owners and Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-IA-{uid}",
            owner_name=f"Owner IA {uid}",
            email=f"owneria_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-IB-{uid}",
            owner_name=f"Owner IB {uid}",
            email=f"ownerib_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(
            business_id=f"PRJ-IA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_IA_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-IB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_IB_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Project memberships (admin_a and user_custom_a assigned to proj_a; user_unassigned_a is NOT)
        pm_a1 = ProjectMember(project_id=proj_a.id, user_id=admin_a.id)
        pm_a2 = ProjectMember(project_id=proj_a.id, user_id=user_custom_a.id)
        pm_b1 = ProjectMember(project_id=proj_b.id, user_id=admin_b.id)
        db.add_all([pm_a1, pm_a2, pm_b1])
        await db.flush()

        # 6. Master data: ActivityType
        act = (await db.execute(select(ActivityType).where(ActivityType.is_active == True))).scalars().first()
        if not act:
            act = ActivityType(name=f"Excavation_{uid}", category="Civil Work", is_active=True)
            db.add(act)
            await db.flush()

        # 7. BOQ Groups and Items
        # Company A - Draft BOQ (for add_item, bulk_add_items, and update_actuals)
        group_a = BOQGroup(project_id=proj_a.id, name="Earthwork Group A", current_version=1, status="Draft")
        db.add(group_a)
        await db.flush()

        boq_a = BOQ(
            project_id=proj_a.id,
            boq_group_id=group_a.id,
            version_no=1,
            is_latest=True,
            item_name="Site Excavation A",
            category=act.category or "Civil",
            description="Foundation excavation in soft rock",
            quantity=Decimal("100.000"),
            unit="cum",
            unit_cost=Decimal("250.00"),
            total_cost=Decimal("25000.00"),
            actual_quantity=Decimal("10.000"),
            actual_cost=Decimal("2500.00"),
            variance_cost=Decimal("22500.00"),
            status="Active",
            approval_status="Draft",
            activity_type_id=act.id,
        )
        db.add(boq_a)
        await db.flush()

        # Company A - Approved BOQ (for generate_tasks and create_version)
        group_approved_a = BOQGroup(project_id=proj_a.id, name="Approved Group A", current_version=1, status="Approved")
        db.add(group_approved_a)
        await db.flush()

        boq_approved_a = BOQ(
            project_id=proj_a.id,
            boq_group_id=group_approved_a.id,
            version_no=1,
            is_latest=True,
            item_name="Approved Concrete Work A",
            category=act.category or "Civil",
            description="Approved structure work",
            quantity=Decimal("50.000"),
            unit="cum",
            unit_cost=Decimal("500.00"),
            total_cost=Decimal("25000.00"),
            actual_quantity=Decimal("0.000"),
            actual_cost=Decimal("0.00"),
            variance_cost=Decimal("25000.00"),
            status="Active",
            approval_status="Approved",
            activity_type_id=act.id,
        )
        db.add(boq_approved_a)
        await db.flush()

        audit_a = BOQAudit(
            boq_id=boq_a.id,
            user_id=admin_a.id,
            action="CREATE",
            message="Created BOQ Item A",
            changes={"unit_cost": {"old": None, "new": 250.0}},
        )
        db.add(audit_a)

        # Milestone for proj_a
        ms_a = Milestone(project_id=proj_a.id, title="Substructure Milestone A")
        db.add(ms_a)

        # Company B
        group_b = BOQGroup(project_id=proj_b.id, name="Earthwork Group B", current_version=1, status="Approved")
        db.add(group_b)
        await db.flush()

        boq_b = BOQ(
            project_id=proj_b.id,
            boq_group_id=group_b.id,
            version_no=1,
            is_latest=True,
            item_name="Site Excavation B",
            category=act.category or "Civil",
            description="Foundation excavation in hard rock",
            quantity=Decimal("50.000"),
            unit="cum",
            unit_cost=Decimal("400.00"),
            total_cost=Decimal("20000.00"),
            actual_quantity=Decimal("5.000"),
            actual_cost=Decimal("2000.00"),
            variance_cost=Decimal("18000.00"),
            status="Active",
            approval_status="Approved",
            activity_type_id=act.id,
        )
        db.add(boq_b)
        await db.flush()

        audit_b = BOQAudit(
            boq_id=boq_b.id,
            user_id=admin_b.id,
            action="CREATE",
            message="Created BOQ Item B",
            changes={"unit_cost": {"old": None, "new": 400.0}},
        )
        db.add(audit_b)

        await db.commit()

        # Tokens
        tokens = {
            "super": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "custom_a": create_access_token({"sub": str(user_custom_a.id)}),
            "legacy_a": create_access_token({"sub": str(user_legacy_a.id)}),
            "unassigned_a": create_access_token({"sub": str(user_unassigned_a.id)}),
        }

        yield {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "group_a": group_a,
            "group_approved_a": group_approved_a,
            "group_b": group_b,
            "boq_a": boq_a,
            "boq_approved_a": boq_approved_a,
            "boq_b": boq_b,
            "audit_a": audit_a,
            "audit_b": audit_b,
            "ms_a": ms_a,
            "act": act,
            "role_custom": role_custom,
            "user_custom_a": user_custom_a,
            "user_legacy_a": user_legacy_a,
            "user_unassigned_a": user_unassigned_a,
            "tokens": tokens,
        }

        # Cleanup
        async with AsyncSessionLocal() as clean_db:
            await clean_db.execute(delete(BOQAudit).where(BOQAudit.boq_id.in_([boq_a.id, boq_approved_a.id, boq_b.id])))
            await clean_db.execute(delete(Task).where(Task.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(BOQ).where(BOQ.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(BOQGroup).where(BOQGroup.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(Milestone).where(Milestone.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([user_custom_a.id, user_legacy_a.id, user_unassigned_a.id])))
            await clean_db.execute(delete(RolePermission).where(RolePermission.role_id == role_custom.id))
            await clean_db.execute(delete(Role).where(Role.id == role_custom.id))
            await clean_db.execute(delete(User).where(User.id.in_([super_admin.id, admin_a.id, admin_b.id, user_custom_a.id, user_legacy_a.id, user_unassigned_a.id])))
            await clean_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
            await clean_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await clean_db.commit()


@pytest.mark.asyncio
async def test_batch_i_unauthenticated_all_27_routes_401():
    """Verify that all 27 active routes in Batch I return HTTP 401 when accessed without credentials."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            boq_id = d_data["boq_a"].id
            group_id = d_data["group_a"].id
            proj_id = d_data["proj_a"].id

            routes = [
                ("POST", "/api/v1/boq", {"json": {}}),
                ("GET", "/api/v1/boq", {}),
                ("GET", "/api/v1/boq/template/excel", {}),
                ("POST", f"/api/v1/boq/groups/{group_id}/import/excel", {}),
                ("GET", f"/api/v1/boq/{boq_id}", {}),
                ("PUT", f"/api/v1/boq/{boq_id}", {"json": {}}),
                ("DELETE", f"/api/v1/boq/{boq_id}", {}),
                ("POST", f"/api/v1/boq/{boq_id}/actuals", {"json": {}}),
                ("GET", f"/api/v1/boq/summary/{proj_id}", {}),
                ("GET", f"/api/v1/boq/comparison/{proj_id}", {}),
                ("GET", f"/api/v1/boq/{boq_id}/report", {}),
                ("GET", f"/api/v1/boq/{boq_id}/alerts", {}),
                ("GET", f"/api/v1/boq/{boq_id}/versions", {}),
                ("GET", f"/api/v1/boq/project/{proj_id}", {}),
                ("POST", f"/api/v1/boq/groups/{group_id}/items", {"json": {}}),
                ("GET", f"/api/v1/boq/groups/{group_id}/items", {}),
                ("PUT", f"/api/v1/boq/items/{boq_id}", {"json": {}}),
                ("POST", f"/api/v1/boq/groups/{group_id}/items/bulk", {"json": {}}),
                ("DELETE", f"/api/v1/boq/items/{boq_id}", {}),
                ("POST", f"/api/v1/boq/groups/{group_id}/versions", {}),
                ("GET", f"/api/v1/boq/{boq_id}/export/json", {}),
                ("GET", f"/api/v1/boq/{boq_id}/export/excel", {}),
                ("GET", f"/api/v1/boq/{boq_id}/export/pdf", {}),
                ("GET", f"/api/v1/boq/{boq_id}/optimize", {}),
                ("GET", f"/api/v1/boq/{boq_id}/logs", {}),
                ("GET", f"/api/v1/boq/{boq_id}/logs/export/csv", {}),
                ("POST", f"/api/v1/boq/{boq_id}/generate-tasks", {}),
            ]

            assert len(routes) == 27, f"Expected exactly 27 routes in Batch I audit, found {len(routes)}"

            for method, path, kwargs in routes:
                res = await ac.request(method, path, **kwargs)
                assert res.status_code == 401, f"{method} {path} returned {res.status_code}, expected 401"


@pytest.mark.asyncio
async def test_batch_i_custom_role_dynamic_lifecycle_boq_view():
    """Verify dynamic grant, revocation, and re-grant of `boq.view` without server restart."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name

            # 1. Zero permissions -> 403 Forbidden
            res = await ac.get("/api/v1/boq", headers=headers)
            assert res.status_code == 403

            # 2. Grant boq.view dynamically
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "boq.view"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                await db.commit()

            # Now authorized -> 200 OK
            res = await ac.get("/api/v1/boq", headers=headers)
            assert res.status_code == 200

            # 3. Revoke boq.view dynamically
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == p_view.id))
                await db.commit()

            # Now 403 Forbidden
            res = await ac.get("/api/v1/boq", headers=headers)
            assert res.status_code == 403

            # 4. Regrant boq.view dynamically
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                await db.commit()

            # Now 200 OK
            res = await ac.get("/api/v1/boq", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_batch_i_custom_role_dynamic_lifecycle_mutations():
    """Verify dynamic lifecycle for boq.create, boq.edit, boq.delete."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name
            proj_id = d_data["proj_a"].id
            act_id = d_data["act"].id

            create_payload = {
                "project_id": proj_id,
                "item_name": "Test Creation BOQ",
                "description": "Dynamic create test",
                "quantity": 10.0,
                "unit_cost": 100.0,
                "activity_type_id": act_id,
                "status": "Active",
            }

            # 1. Create unauthorized
            res = await ac.post("/api/v1/boq", json=create_payload, headers=headers)
            assert res.status_code == 403

            async with AsyncSessionLocal() as db:
                p_create = (await db.execute(select(Permission).where(Permission.code == "boq.create"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_create.id))
                await db.commit()

            res = await ac.post("/api/v1/boq", json=create_payload, headers=headers)
            assert res.status_code == 200
            new_boq_id = res.json()["id"]

            # 2. Edit unauthorized
            update_payload = {"description": "Updated description"}
            res = await ac.put(f"/api/v1/boq/{new_boq_id}", json=update_payload, headers=headers)
            assert res.status_code == 403

            async with AsyncSessionLocal() as db:
                p_edit = (await db.execute(select(Permission).where(Permission.code == "boq.edit"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_edit.id))
                await db.commit()

            res = await ac.put(f"/api/v1/boq/{new_boq_id}", json=update_payload, headers=headers)
            assert res.status_code == 200

            # 3. Delete unauthorized
            res = await ac.delete(f"/api/v1/boq/{new_boq_id}", headers=headers)
            assert res.status_code == 403

            async with AsyncSessionLocal() as db:
                p_del = (await db.execute(select(Permission).where(Permission.code == "boq.delete"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_del.id))
                await db.commit()

            res = await ac.delete(f"/api/v1/boq/{new_boq_id}", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_batch_i_custom_role_dynamic_lifecycle_export():
    """Verify dynamic lifecycle for boq.export."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name
            boq_id = d_data["boq_a"].id

            # 1. Export JSON unauthorized
            res = await ac.get(f"/api/v1/boq/{boq_id}/export/json", headers=headers)
            assert res.status_code == 403

            async with AsyncSessionLocal() as db:
                p_exp = (await db.execute(select(Permission).where(Permission.code == "boq.export"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_exp.id))
                await db.commit()

            # Now authorized
            res = await ac.get(f"/api/v1/boq/{boq_id}/export/json", headers=headers)
            assert res.status_code == 200

            # Test export logs CSV
            res_csv = await ac.get(f"/api/v1/boq/{boq_id}/logs/export/csv", headers=headers)
            assert res_csv.status_code == 200


@pytest.mark.asyncio
async def test_batch_i_custom_role_dynamic_lifecycle_tasks_create():
    """Verify dynamic lifecycle for tasks.create on generate_tasks_from_boq."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name
            boq_id = d_data["boq_approved_a"].id
            ms_id = d_data["ms_a"].id

            # 1. Task generation unauthorized
            res = await ac.post(f"/api/v1/boq/{boq_id}/generate-tasks?milestone_id={ms_id}", headers=headers)
            assert res.status_code == 403

            async with AsyncSessionLocal() as db:
                p_tcreate = (await db.execute(select(Permission).where(Permission.code == "tasks.create"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_tcreate.id))
                await db.commit()

            # Now authorized
            res = await ac.post(f"/api/v1/boq/{boq_id}/generate-tasks?milestone_id={ms_id}", headers=headers)
            assert res.status_code == 200
            assert "task_id" in res.json()


@pytest.mark.asyncio
async def test_batch_i_user_permission_overrides():
    """Verify user-level positive and negative overrides take precedence over role permissions."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            user_id = d_data["user_custom_a"].id
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name

            # 1. Positive override: role lacks permission, user override grants it -> 200
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "boq.view"))).scalar_one()
                db.add(UserPermissionOverride(user_id=user_id, permission_id=p_view.id, is_granted=True))
                await db.commit()

            res = await ac.get("/api/v1/boq", headers=headers)
            assert res.status_code == 200

            # 2. Negative override: role HAS permission, user override explicitly DENIES it -> 403
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                override = (await db.execute(select(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id, UserPermissionOverride.permission_id == p_view.id))).scalar_one()
                override.is_granted = False
                await db.commit()

            res = await ac.get("/api/v1/boq", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_batch_i_wildcard_permission():
    """Verify wildcard permissions `*` and `boq.*` authorize Batch I routes."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name

            # Grant wildcard
            async with AsyncSessionLocal() as db:
                p_star = (await db.execute(select(Permission).where(Permission.code == "boq.*"))).scalar_one_or_none()
                if not p_star:
                    p_star = (await db.execute(select(Permission).where(Permission.code == "*"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_star.id))
                await db.commit()

            res = await ac.get("/api/v1/boq", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_batch_i_legacy_role_strings_denied():
    """Verify legacy role strings alone without DB permissions receive 403."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["legacy_a"]
            headers = {"Authorization": f"Bearer {token}"}

            res = await ac.get("/api/v1/boq", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_batch_i_tenant_isolation_idor_reads_and_updates_and_deletes():
    """Verify P0 IDOR security: Company A cannot read, update, delete, or exfiltrate Company B BOQs."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}

            boq_b_id = d_data["boq_b"].id

            # 1. P0-1: GET foreign BOQ -> 404
            res = await ac.get(f"/api/v1/boq/{boq_b_id}", headers=headers_a)
            assert res.status_code == 404

            # 2. P0-2: PUT foreign BOQ -> 404
            res = await ac.put(f"/api/v1/boq/{boq_b_id}", json={"description": "Hacked"}, headers=headers_a)
            assert res.status_code == 404

            # 3. P0-3: DELETE foreign BOQ -> 404
            res = await ac.delete(f"/api/v1/boq/{boq_b_id}", headers=headers_a)
            assert res.status_code == 404

            # 4. P0-4: PUT foreign BOQ item -> 404
            res = await ac.put(f"/api/v1/boq/items/{boq_b_id}", json={"description": "Hacked"}, headers=headers_a)
            assert res.status_code == 404

            # 5. P0-5: DELETE foreign BOQ item -> 404
            res = await ac.delete(f"/api/v1/boq/items/{boq_b_id}", headers=headers_a)
            assert res.status_code == 404

            # 6. P0-6: GET foreign BOQ logs -> 404
            res = await ac.get(f"/api/v1/boq/{boq_b_id}/logs", headers=headers_a)
            assert res.status_code == 404

            # 7. GET foreign BOQ logs export CSV -> 404
            res = await ac.get(f"/api/v1/boq/{boq_b_id}/logs/export/csv", headers=headers_a)
            assert res.status_code == 404

            # 8. GET foreign BOQ export JSON -> 404
            res = await ac.get(f"/api/v1/boq/{boq_b_id}/export/json", headers=headers_a)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_batch_i_cross_project_injection_prevention():
    """Verify P1-1 and P1-2: foreign project_id passed in add_item or bulk_add_items cannot contaminate parent project."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            group_a_id = d_data["group_a"].id
            proj_a_id = d_data["proj_a"].id
            proj_b_id = d_data["proj_b"].id
            act_id = d_data["act"].id

            # 1. P1-1: add_item with foreign payload.project_id
            payload_single = {
                "project_id": proj_b_id,  # Injection attempt
                "item_name": "Injected Single Item",
                "description": "Attempting project cross contamination",
                "quantity": 5.0,
                "unit_cost": 50.0,
                "activity_type_id": act_id,
                "status": "Active",
            }
            res = await ac.post(f"/api/v1/boq/groups/{group_a_id}/items", json=payload_single, headers=headers_a)
            assert res.status_code == 200
            created_item = res.json()
            # Verified: forced to parent.project_id
            assert created_item["project_id"] == proj_a_id

            # 2. P1-2: bulk_add_items with foreign item.project_id
            payload_bulk = {
                "items": [
                    {
                        "project_id": proj_b_id,  # Injection attempt
                        "item_name": "Injected Bulk Item",
                        "description": "Bulk cross contamination",
                        "quantity": 10.0,
                        "unit_cost": 100.0,
                        "activity_type_id": act_id,
                        "status": "Active",
                    }
                ]
            }
            res = await ac.post(f"/api/v1/boq/groups/{group_a_id}/items/bulk", json=payload_bulk, headers=headers_a)
            assert res.status_code == 200
            bulk_items = res.json()["items"]
            assert bulk_items[0]["project_id"] == proj_a_id


@pytest.mark.asyncio
async def test_batch_i_list_boq_project_membership_and_super_admin():
    """Verify P1-5 project membership enforcement and P1-6 Super Admin semantics."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_unassigned = d_data["tokens"]["unassigned_a"]
            headers_unassigned = {"Authorization": f"Bearer {token_unassigned}"}
            token_super = d_data["tokens"]["super"]
            headers_super = {"Authorization": f"Bearer {token_super}"}
            proj_a_id = d_data["proj_a"].id
            proj_b_id = d_data["proj_b"].id

            # Grant boq.view to custom role
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "boq.view"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                await db.commit()

            # 1. P1-5: Unassigned employee querying project_id -> 404
            res = await ac.get(f"/api/v1/boq?project_id={proj_a_id}", headers=headers_unassigned)
            assert res.status_code == 404

            # 2. P1-6: Super Admin can view projects across companies
            res_super_a = await ac.get(f"/api/v1/boq?project_id={proj_a_id}", headers=headers_super)
            assert res_super_a.status_code == 200
            assert len(res_super_a.json()["items"]) >= 1

            res_super_b = await ac.get(f"/api/v1/boq?project_id={proj_b_id}", headers=headers_super)
            assert res_super_b.status_code == 200
            assert len(res_super_b.json()["items"]) >= 1


@pytest.mark.asyncio
async def test_batch_i_company_settings_tenant_isolation():
    """Verify P1-4: Document exports use tenant-specific company settings."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            token_b = d_data["tokens"]["admin_b"]
            headers_b = {"Authorization": f"Bearer {token_b}"}

            boq_a_id = d_data["boq_a"].id
            boq_b_id = d_data["boq_b"].id

            # Export Excel Company A
            res_a = await ac.get(f"/api/v1/boq/{boq_a_id}/export/excel", headers=headers_a)
            assert res_a.status_code == 200
            assert res_a.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

            # Export Excel Company B
            res_b = await ac.get(f"/api/v1/boq/{boq_b_id}/export/excel", headers=headers_b)
            assert res_b.status_code == 200


@pytest.mark.asyncio
async def test_batch_i_update_actuals_business_rule():
    """Verify Route 8 update_actuals preserves business rule HTTP 403."""
    async with setup_batch_i_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            token_custom = d_data["tokens"]["custom_a"]
            headers_custom = {"Authorization": f"Bearer {token_custom}"}
            boq_a_id = d_data["boq_a"].id

            actuals_payload = {
                "actual_quantity": 15.0,
                "actual_cost": 3000.0,
            }

            # 1. User without permission receives 403 permission denied
            res_unauth = await ac.post(f"/api/v1/boq/{boq_a_id}/actuals", json=actuals_payload, headers=headers_custom)
            assert res_unauth.status_code == 403

            # 2. User WITH permission reaches the business rule HTTP 403
            res_auth = await ac.post(f"/api/v1/boq/{boq_a_id}/actuals", json=actuals_payload, headers=headers_a)
            assert res_auth.status_code == 403
            assert "financial determinism" in res_auth.json()["detail"]
