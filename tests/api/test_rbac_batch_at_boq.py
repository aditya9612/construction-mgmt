import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.rbac import Role, Permission, RolePermission
from app.models.master_data import ActivityType, Unit
from app.models.project import Project, Milestone, Task
from app.models.boq import BOQ, BOQGroup, BOQAudit
from app.core.security import get_password_hash, create_access_token
from tests.api.test_rbac_phase2_batch_i import setup_batch_i_data


# ============================================================================
# 1. UNAUTHENTICATED REQUESTS (401) FOR ALL 27 BOQ ENDPOINTS
# ============================================================================

@pytest.mark.asyncio
async def test_at_01_unauthenticated_requests_all_27_endpoints_401():
    """Verify that all 27 BOQ endpoints strictly return 401 when unauthenticated."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("POST", "/api/v1/boq", {"json": {"project_id": 1, "boq_group_id": 1, "item_name": "Test"}}),
            ("GET", "/api/v1/boq", {}),
            ("GET", "/api/v1/boq/template/excel", {}),
            ("POST", "/api/v1/boq/groups/1/import/excel", {}),
            ("GET", "/api/v1/boq/1", {}),
            ("PUT", "/api/v1/boq/1", {"json": {"item_name": "Updated"}}),
            ("DELETE", "/api/v1/boq/1", {}),
            ("POST", "/api/v1/boq/1/actuals", {"json": {"actual_quantity": 10}}),
            ("GET", "/api/v1/boq/summary/1", {}),
            ("GET", "/api/v1/boq/comparison/1", {}),
            ("GET", "/api/v1/boq/1/report", {}),
            ("GET", "/api/v1/boq/1/alerts", {}),
            ("GET", "/api/v1/boq/1/versions", {}),
            ("GET", "/api/v1/boq/project/1", {}),
            ("POST", "/api/v1/boq/groups/1/items", {"json": {"item_name": "Item"}}),
            ("GET", "/api/v1/boq/groups/1/items", {}),
            ("PUT", "/api/v1/boq/items/1", {"json": {"item_name": "Updated"}}),
            ("POST", "/api/v1/boq/groups/1/items/bulk", {"json": {"items": []}}),
            ("DELETE", "/api/v1/boq/items/1", {}),
            ("POST", "/api/v1/boq/groups/1/versions", {"json": {"version_name": "v2"}}),
            ("GET", "/api/v1/boq/1/export/json", {}),
            ("GET", "/api/v1/boq/1/export/excel", {}),
            ("GET", "/api/v1/boq/1/export/pdf", {}),
            ("GET", "/api/v1/boq/1/optimize", {}),
            ("GET", "/api/v1/boq/1/logs", {}),
            ("GET", "/api/v1/boq/1/logs/export/csv", {}),
            ("POST", "/api/v1/boq/1/generate-tasks", {"json": {}}),
        ]
        assert len(endpoints) == 27, f"Expected 27 endpoints, got {len(endpoints)}"

        for method, path, kwargs in endpoints:
            res = await ac.request(method, path, **kwargs)
            assert res.status_code == 401, f"{method} {path} returned {res.status_code}, expected 401"


# ============================================================================
# 2. DYNAMIC RBAC: boq.view, boq.create, boq.edit, boq.delete, boq.export
# ============================================================================

@pytest.mark.asyncio
async def test_at_02_dynamic_rbac_boq_view():
    """Verify dynamic grant/revoke lifecycle for boq.view."""
    async with setup_batch_i_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            boq_id = d["boq_a"].id
            proj_id = d["proj_a"].id
            group_id = d["group_a"].id

            # 1. Initially 403 Forbidden
            res = await ac.get(f"/api/v1/boq/{boq_id}", headers=headers)
            assert res.status_code == 403
            res = await ac.get(f"/api/v1/boq/summary/{proj_id}", headers=headers)
            assert res.status_code == 403
            res = await ac.get(f"/api/v1/boq/groups/{group_id}/items", headers=headers)
            assert res.status_code == 403

            # 2. Grant boq.view in DB
            async with AsyncSessionLocal() as db:
                p = (await db.execute(select(Permission).where(Permission.code == "boq.view"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # 3. Access immediately granted without restart
            res = await ac.get(f"/api/v1/boq/{boq_id}", headers=headers)
            assert res.status_code == 200

            res = await ac.get(f"/api/v1/boq/summary/{proj_id}", headers=headers)
            assert res.status_code == 200

            res = await ac.get(f"/api/v1/boq/groups/{group_id}/items", headers=headers)
            assert res.status_code == 200

            # 4. Revoke boq.view
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role_id,
                        RolePermission.permission_id == p.id,
                    )
                )
                await db.commit()

            # 5. Access immediately denied (403)
            res = await ac.get(f"/api/v1/boq/{boq_id}", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_at_03_dynamic_rbac_boq_create_and_edit_delete():
    """Verify dynamic grant/revoke for boq.create, boq.edit, boq.delete."""
    async with setup_batch_i_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            boq_id = d["boq_a"].id
            group_id = d["group_a"].id
            proj_id = d["proj_a"].id

            # Initially 403 for create, edit, delete
            res = await ac.post(
                f"/api/v1/boq/groups/{group_id}/items",
                headers=headers,
                json={"item_name": "Dynamic Item", "project_id": proj_id, "activity_type_id": d["act"].id, "quantity": 10, "unit_cost": 100},
            )
            assert res.status_code == 403

            res = await ac.put(
                f"/api/v1/boq/{boq_id}",
                headers=headers,
                json={"item_name": "Dynamic Rename"},
            )
            assert res.status_code == 403

            # Grant boq.create
            async with AsyncSessionLocal() as db:
                p_create = (await db.execute(select(Permission).where(Permission.code == "boq.create"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_create.id))
                await db.commit()

            # Now create succeeds (200)
            res = await ac.post(
                f"/api/v1/boq/groups/{group_id}/items",
                headers=headers,
                json={"item_name": "Dynamic Item", "project_id": proj_id, "activity_type_id": d["act"].id, "quantity": 10, "unit_cost": 100},
            )
            assert res.status_code == 200
            new_item_id = res.json()["id"]

            # Edit still 403
            res = await ac.put(
                f"/api/v1/boq/{boq_id}",
                headers=headers,
                json={"item_name": "Dynamic Rename"},
            )
            assert res.status_code == 403

            # Grant boq.edit
            async with AsyncSessionLocal() as db:
                p_edit = (await db.execute(select(Permission).where(Permission.code == "boq.edit"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_edit.id))
                await db.commit()

            # Edit succeeds
            res = await ac.put(
                f"/api/v1/boq/{boq_id}",
                headers=headers,
                json={"item_name": "Dynamic Rename"},
            )
            assert res.status_code == 200

            # Delete still 403
            res = await ac.delete(f"/api/v1/boq/items/{new_item_id}", headers=headers)
            assert res.status_code == 403

            # Grant boq.delete
            async with AsyncSessionLocal() as db:
                p_delete = (await db.execute(select(Permission).where(Permission.code == "boq.delete"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_delete.id))
                await db.commit()

            # Delete succeeds
            res = await ac.delete(f"/api/v1/boq/items/{new_item_id}", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_at_04_dynamic_rbac_boq_export():
    """Verify dynamic grant/revoke for boq.export."""
    async with setup_batch_i_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            boq_id = d["boq_a"].id

            # 1. 403 Forbidden initially
            res = await ac.get(f"/api/v1/boq/{boq_id}/export/json", headers=headers)
            assert res.status_code == 403

            # 2. Grant boq.export
            async with AsyncSessionLocal() as db:
                p = (await db.execute(select(Permission).where(Permission.code == "boq.export"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # 3. Export succeeds (200)
            res = await ac.get(f"/api/v1/boq/{boq_id}/export/json", headers=headers)
            assert res.status_code == 200

            # 4. Revoke boq.export
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role_id,
                        RolePermission.permission_id == p.id,
                    )
                )
                await db.commit()

            # 5. Export immediately denies (403)
            res = await ac.get(f"/api/v1/boq/{boq_id}/export/json", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 3. GENERATE TASKS: tasks.create ALONE DOES NOT AUTHORIZE, boq.create AUTHORIZES
# ============================================================================

@pytest.mark.asyncio
async def test_at_05_generate_tasks_strictly_requires_boq_create():
    """Verify that tasks.create alone does NOT authorize /boq/{boq_id}/generate-tasks,
    and boq.create DOES authorize it."""
    async with setup_batch_i_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {d['tokens']['custom_a']}"}
            role_id = d["role_custom"].id
            role_name = d["role_custom"].name
            boq_id = d["boq_approved_a"].id

            # 1. Grant tasks.create ONLY
            async with AsyncSessionLocal() as db:
                p_tasks = (await db.execute(select(Permission).where(Permission.code == "tasks.create"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_tasks.id))
                await db.commit()

            # 2. Must return 403 Forbidden!
            res = await ac.post(
                f"/api/v1/boq/{boq_id}/generate-tasks",
                headers=headers,
                json={},
            )
            assert res.status_code == 403, f"tasks.create alone must not authorize generate-tasks! Got {res.status_code}"

            # 3. Revoke tasks.create and grant canonical boq.create
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role_id,
                        RolePermission.permission_id == p_tasks.id,
                    )
                )
                p_boq = (await db.execute(select(Permission).where(Permission.code == "boq.create"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_boq.id))
                await db.commit()

            # 4. Now boq.create successfully authorizes (200)
            res = await ac.post(
                f"/api/v1/boq/{boq_id}/generate-tasks",
                headers=headers,
                json={},
            )
            assert res.status_code == 200, f"boq.create should authorize generate-tasks! Got {res.status_code}: {res.text}"
            data = res.json()
            assert "created_tasks" in data or "message" in data


# ============================================================================
# 4. TENANT CONTEXT: TENANTLESS NON-SA => 403 "Company context required"
# ============================================================================

@pytest.mark.asyncio
async def test_at_06_tenantless_non_sa_returns_403():
    """Verify that any authenticated non-SA caller without company_id receives 403."""
    uid = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        role = Role(
            name=f"TenantlessRole_{uid}",
            display_name=f"Tenantless Role {uid}",
            description="Role for tenantless user",
        )
        db.add(role)
        await db.flush()

        # Grant all boq permissions to this role
        perms = (await db.execute(select(Permission).where(Permission.code.like("boq.%")))).scalars().all()
        for p in perms:
            db.add(RolePermission(role=role.name, role_id=role.id, permission_id=p.id))

        tenantless_user = User(
            email=f"tenantless_{uid}@test.com",
            hashed_password=get_password_hash("Secret123!"),
            company_id=None,
            is_active=True,
            is_super_admin=False,
            role=role.name,
        )
        db.add(tenantless_user)
        await db.commit()

    tenantless_token = create_access_token({"sub": str(tenantless_user.id)})
    headers = {"Authorization": f"Bearer {tenantless_token}"}

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("GET", "/api/v1/boq"),
                ("GET", "/api/v1/boq/1"),
                ("GET", "/api/v1/boq/summary/1"),
                ("GET", "/api/v1/boq/comparison/1"),
                ("GET", "/api/v1/boq/template/excel"),
                ("POST", "/api/v1/boq", {"json": {"project_id": 1, "boq_group_id": 1, "item_name": "Test"}}),
                ("POST", "/api/v1/boq/1/generate-tasks", {"json": {}}),
            ]
            for ep in endpoints:
                method = ep[0]
                path = ep[1]
                kwargs = ep[2] if len(ep) > 2 else {}
                res = await ac.request(method, path, headers=headers, **kwargs)
                assert res.status_code == 403, f"{method} {path} returned {res.status_code}, expected 403"
                assert res.json().get("detail", "") in ("Company context required", "User does not belong to any company.")
    finally:
        async with AsyncSessionLocal() as clean_db:
            await clean_db.execute(delete(User).where(User.id == tenantless_user.id))
            await clean_db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
            await clean_db.execute(delete(Role).where(Role.id == role.id))
            await clean_db.commit()


# ============================================================================
# 5. CROSS-TENANT ISOLATION & 404 MASKING (BOQ, PROJECT, RESOURCES)
# ============================================================================

@pytest.mark.asyncio
async def test_at_07_cross_tenant_boq_and_project_access_returns_404():
    """Verify that accessing cross-tenant BOQ resources is masked as 404."""
    async with setup_batch_i_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # User in Company A trying to access Company B resources
            headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
            boq_b_id = d["boq_b"].id
            proj_b_id = d["proj_b"].id
            group_b_id = d["group_b"].id

            # Cross-tenant GET boq => 404
            res = await ac.get(f"/api/v1/boq/{boq_b_id}", headers=headers_a)
            assert res.status_code == 404

            # Cross-tenant GET project summary => 404
            res = await ac.get(f"/api/v1/boq/summary/{proj_b_id}", headers=headers_a)
            assert res.status_code == 404

            # Cross-tenant GET project comparison => 404
            res = await ac.get(f"/api/v1/boq/comparison/{proj_b_id}", headers=headers_a)
            assert res.status_code == 404

            # Cross-tenant GET group items => 404
            res = await ac.get(f"/api/v1/boq/groups/{group_b_id}/items", headers=headers_a)
            assert res.status_code == 404

            # Cross-tenant PUT boq => 404
            res = await ac.put(f"/api/v1/boq/{boq_b_id}", headers=headers_a, json={"item_name": "Tamper"})
            assert res.status_code == 404

            # Cross-tenant DELETE boq => 404
            res = await ac.delete(f"/api/v1/boq/{boq_b_id}", headers=headers_a)
            assert res.status_code == 404


# ============================================================================
# 6. CROSS-TENANT FK INJECTION (project_id, milestone_id, activity_type_id)
# ============================================================================

@pytest.mark.asyncio
async def test_at_08_cross_tenant_fk_injection_rejected():
    """Verify that foreign keys from another tenant (project_id, activity_type_id, milestone_id)
    are strictly rejected with 404."""
    async with setup_batch_i_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
            group_a_id = d["group_a"].id
            proj_b_id = d["proj_b"].id
            boq_a_id = d["boq_a"].id

            # 1. Foreign project_id injection in POST /boq
            res = await ac.post(
                "/api/v1/boq",
                headers=headers_a,
                json={
                    "project_id": proj_b_id,
                    "item_name": "Injected Item",
                    "activity_type_id": d["act"].id,
                    "quantity": 10,
                    "unit_cost": 100,
                },
            )
            assert res.status_code == 404, f"Foreign project_id must return 404, got {res.status_code}"

            # Create a custom activity type belonging to Company B
            uid = uuid.uuid4().hex[:8]
            async with AsyncSessionLocal() as db:
                act_b = ActivityType(
                    name=f"Foreign_Act_{uid}",
                    category="Civil",
                    company_id=d["comp_b"].id,
                    is_active=True,
                )
                db.add(act_b)

                # Create a milestone belonging to Company B
                ms_b = Milestone(
                    project_id=d["proj_b"].id,
                    title=f"Foreign_MS_{uid}",
                    status="PLANNED",
                )
                db.add(ms_b)
                await db.commit()
                act_b_id = act_b.id
                ms_b_id = ms_b.id

            try:
                # 2. Foreign activity_type_id in add item => 404
                res = await ac.post(
                    f"/api/v1/boq/groups/{group_a_id}/items",
                    headers=headers_a,
                    json={
                        "item_name": "Cross Activity Item",
                        "project_id": d["proj_a"].id,
                        "activity_type_id": act_b_id,
                        "quantity": 10,
                        "unit_cost": 50,
                    },
                )
                assert res.status_code == 404, f"Foreign activity_type_id must return 404, got {res.status_code}"

                # 3. Foreign milestone_id in generate tasks => 404
                res = await ac.post(
                    f"/api/v1/boq/{d['boq_approved_a'].id}/generate-tasks?milestone_id={ms_b_id}",
                    headers=headers_a,
                )
                assert res.status_code == 404, f"Foreign milestone_id must return 404, got {res.status_code}"
            finally:
                async with AsyncSessionLocal() as clean_db:
                    await clean_db.execute(delete(Milestone).where(Milestone.id == ms_b_id))
                    await clean_db.execute(delete(ActivityType).where(ActivityType.id == act_b_id))
                    await clean_db.commit()


# ============================================================================
# 7. SUPER ADMIN GLOBAL ACCESS
# ============================================================================

@pytest.mark.asyncio
async def test_at_09_super_admin_global_access():
    """Verify that Super Admin can access BOQ across different companies."""
    async with setup_batch_i_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_sa = {"Authorization": f"Bearer {d['tokens']['super']}"}
            boq_a_id = d["boq_a"].id
            boq_b_id = d["boq_b"].id
            proj_a_id = d["proj_a"].id
            proj_b_id = d["proj_b"].id

            # SA can access Company A BOQ
            res = await ac.get(f"/api/v1/boq/{boq_a_id}", headers=headers_sa)
            assert res.status_code == 200

            # SA can access Company B BOQ
            res = await ac.get(f"/api/v1/boq/{boq_b_id}", headers=headers_sa)
            assert res.status_code == 200

            # SA can access summaries for both projects
            res = await ac.get(f"/api/v1/boq/summary/{proj_a_id}", headers=headers_sa)
            assert res.status_code == 200
            res = await ac.get(f"/api/v1/boq/summary/{proj_b_id}", headers=headers_sa)
            assert res.status_code == 200


# ============================================================================
# 8. BUSINESS INVARIANTS: CALCULATIONS & PREVENTING DUPLICATE TASK GENERATION
# ============================================================================

@pytest.mark.asyncio
async def test_at_10_business_invariants_and_task_generation():
    """Verify business invariants: calculations, duplicate task prevention,
    and excel template generation."""
    async with setup_batch_i_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
            boq_app_id = d["boq_approved_a"].id

            # 1. Download template succeeds
            res = await ac.get("/api/v1/boq/template/excel", headers=headers_a)
            assert res.status_code == 200
            assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in res.headers.get("content-type", "")

            # 2. Generate task first time succeeds
            res1 = await ac.post(
                f"/api/v1/boq/{boq_app_id}/generate-tasks",
                headers=headers_a,
                json={},
            )
            assert res1.status_code == 200

            # 3. Second call returns idempotent message "Task already exists for this BOQ"
            res2 = await ac.post(
                f"/api/v1/boq/{boq_app_id}/generate-tasks",
                headers=headers_a,
                json={},
            )
            assert res2.status_code == 200
            assert "Task already exists for this BOQ" in res2.json().get("message", "")
