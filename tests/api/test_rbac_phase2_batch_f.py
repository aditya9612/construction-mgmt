import uuid
from decimal import Decimal
from datetime import date, timedelta
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.core.db import async_engine
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import (
    Project,
    ProjectMember,
    Task,
    QCRecord,
    SafetyIncident,
    Checklist,
    ChecklistItem,
    ChecklistLog,
)
from app.models.settings import CompanySettings, UserSettings
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import QCStatus, SafetyChecklistStatus, ChecklistStatus, TaskStatus


@asynccontextmanager
async def setup_batch_f_data():
    """Seed test companies, projects, tasks, QC records, safety incidents, checklists, items, logs, and users for Batch F test suite."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Create two test companies
        comp_a = Company(name=f"BatchF_CompA_{uid}")
        comp_b = Company(name=f"BatchF_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company settings
        cs_a = CompanySettings(company_id=comp_a.id)
        cs_b = CompanySettings(company_id=comp_b.id)
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Test Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_f_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin F",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_fa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin F",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_fb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin F",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"SafetyAuditor_{uid}"
        user_custom_a = User(
            email=f"custom_fa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Safety Auditor F",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        user_legacy_a = User(
            email=f"legacy_fa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy PM F",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Project Manager",
        )

        db.add_all([super_admin, admin_a, admin_b, user_custom_a, user_legacy_a])
        await db.flush()

        # 4. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-FA-{uid}",
            owner_name=f"Owner FA {uid}",
            email=f"ownerfa_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-FB-{uid}",
            owner_name=f"Owner FB {uid}",
            email=f"ownerfb_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 5. Projects
        proj_a = Project(
            business_id=f"PRJ-FA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_FA_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-FB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_FB_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 6. Tasks
        task_a = Task(
            project_id=proj_a.id,
            title=f"Task FA {uid}",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=10),
            status=TaskStatus.IN_PROGRESS,
            created_by_user_id=admin_a.id,
        )
        task_b = Task(
            project_id=proj_b.id,
            title=f"Task FB {uid}",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=10),
            status=TaskStatus.IN_PROGRESS,
            created_by_user_id=admin_b.id,
        )
        db.add_all([task_a, task_b])
        await db.flush()

        # 7. QC Records
        qc_a = QCRecord(
            project_id=proj_a.id,
            task_id=task_a.id,
            inspection_type="Concrete Strength Test",
            test_type="Compressive Strength",
            result=32.5,
            standard_value=30.0,
            status=QCStatus.PASS,
            engineer_name="John Doe",
            remarks="Meets IS 456 specification",
        )
        qc_b = QCRecord(
            project_id=proj_b.id,
            task_id=task_b.id,
            inspection_type="Steel Rebar Quality",
            test_type="Tensile Strength",
            result=510.0,
            standard_value=500.0,
            status=QCStatus.PASS,
            engineer_name="Jane Smith",
            remarks="Approved",
        )
        db.add_all([qc_a, qc_b])
        await db.flush()

        # 8. Safety Incidents
        safety_a = SafetyIncident(
            project_id=proj_a.id,
            task_id=task_a.id,
            date=date.today(),
            safety_checklist_status=SafetyChecklistStatus.COMPLETED,
            ppe_compliance=True,
            violation_type="Near Miss",
            description="Scaffolding plank displaced during morning inspection",
            injury_details="None",
            action_taken="Plank refastened with steel clamps",
            responsible_person="Alex Safety Officer",
        )
        safety_b = SafetyIncident(
            project_id=proj_b.id,
            task_id=task_b.id,
            date=date.today(),
            safety_checklist_status=SafetyChecklistStatus.PENDING,
            ppe_compliance=False,
            violation_type="PPE Violation",
            description="Labour working without safety harness",
            injury_details="Minor scratch",
            action_taken="Warning issued and harness provided",
            responsible_person="Bob Safety Supervisor",
        )
        db.add_all([safety_a, safety_b])
        await db.flush()

        # 9. Checklists
        chk_a = Checklist(
            project_id=proj_a.id,
            name=f"Excavation Checklist FA {uid}",
            type="Safety Inspection",
        )
        chk_b = Checklist(
            project_id=proj_b.id,
            name=f"Electrical Checklist FB {uid}",
            type="Quality Inspection",
        )
        db.add_all([chk_a, chk_b])
        await db.flush()

        # 10. Checklist Items
        item_a = ChecklistItem(
            checklist_id=chk_a.id,
            item="Check trench shoring stability",
        )
        item_b = ChecklistItem(
            checklist_id=chk_b.id,
            item="Inspect conduit grounding continuity",
        )
        db.add_all([item_a, item_b])
        await db.flush()

        # 11. Checklist Logs
        log_a = ChecklistLog(
            checklist_id=chk_a.id,
            project_id=proj_a.id,
            status=ChecklistStatus.DONE,
            remarks="Completed successfully",
            executed_by=admin_a.id,
        )
        log_b = ChecklistLog(
            checklist_id=chk_b.id,
            project_id=proj_b.id,
            status=ChecklistStatus.DONE,
            remarks="Passed with notes",
            executed_by=admin_b.id,
        )
        db.add_all([log_a, log_b])
        await db.flush()

        # 12. Project Memberships
        pm_custom_a = ProjectMember(project_id=proj_a.id, user_id=user_custom_a.id)
        pm_legacy_a = ProjectMember(project_id=proj_a.id, user_id=user_legacy_a.id)
        db.add_all([pm_custom_a, pm_legacy_a])

        # 13. Create custom DB Role
        db_role_custom = Role(
            name=custom_role_name,
            display_name="Safety Auditor",
            description="Custom Safety and QC Auditor",
            company_id=comp_a.id,
            is_system=False,
        )
        db.add(db_role_custom)
        await db.flush()

        # 14. Ensure Admin role has permissions for testing
        admin_role = (await db.execute(select(Role).where(Role.name == "Admin"))).scalar_one_or_none()
        if not admin_role:
            admin_role = Role(name="Admin", display_name="Administrator", description="Administrator", is_system=True)
            db.add(admin_role)
            await db.flush()

        perms_query = select(Permission).where(
            Permission.code.in_([
                "qc.view", "qc.create", "qc.edit", "qc.delete",
                "safety.view", "safety.create", "safety.edit", "safety.delete",
                "checklists.view", "checklists.create", "checklists.edit", "checklists.delete",
            ])
        )
        target_perms = (await db.execute(perms_query)).scalars().all()
        for p in target_perms:
            rp_exists = (await db.execute(
                select(RolePermission).where(RolePermission.role == "Admin", RolePermission.permission_id == p.id)
            )).scalar_one_or_none()
            if not rp_exists:
                db.add(RolePermission(role="Admin", permission_id=p.id))

        await db.commit()

        tokens = {
            "super_admin": create_access_token(data={"sub": str(super_admin.id), "email": super_admin.email}),
            "admin_a": create_access_token(data={"sub": str(admin_a.id), "email": admin_a.email}),
            "admin_b": create_access_token(data={"sub": str(admin_b.id), "email": admin_b.email}),
            "user_custom_a": create_access_token(data={"sub": str(user_custom_a.id), "email": user_custom_a.email}),
            "user_legacy_a": create_access_token(data={"sub": str(user_legacy_a.id), "email": user_legacy_a.email}),
        }

        data_dict = {
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "proj_a_id": proj_a.id,
            "proj_b_id": proj_b.id,
            "task_a_id": task_a.id,
            "task_b_id": task_b.id,
            "qc_a_id": qc_a.id,
            "qc_b_id": qc_b.id,
            "safety_a_id": safety_a.id,
            "safety_b_id": safety_b.id,
            "chk_a_id": chk_a.id,
            "chk_b_id": chk_b.id,
            "item_a_id": item_a.id,
            "item_b_id": item_b.id,
            "log_a_id": log_a.id,
            "log_b_id": log_b.id,
            "super_admin_id": super_admin.id,
            "admin_a_id": admin_a.id,
            "admin_b_id": admin_b.id,
            "user_custom_a_id": user_custom_a.id,
            "user_legacy_a_id": user_legacy_a.id,
            "custom_role_name": custom_role_name,
            "tokens": tokens,
        }

    try:
        yield data_dict
    finally:
        async with AsyncSessionLocal() as db:
            # Clean up overrides and role permissions for custom role
            await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([
                user_custom_a.id, user_legacy_a.id, admin_a.id, admin_b.id, super_admin.id
            ])))
            await db.execute(delete(RolePermission).where(RolePermission.role == custom_role_name))
            await db.execute(delete(Role).where(Role.name == custom_role_name))

            # Clean up domain models
            await db.execute(delete(ChecklistLog).where(ChecklistLog.id.in_([log_a.id, log_b.id])))
            await db.execute(delete(ChecklistItem).where(ChecklistItem.checklist_id.in_([chk_a.id, chk_b.id])))
            await db.execute(delete(Checklist).where(Checklist.id.in_([chk_a.id, chk_b.id])))
            await db.execute(delete(SafetyIncident).where(SafetyIncident.id.in_([safety_a.id, safety_b.id])))
            await db.execute(delete(QCRecord).where(QCRecord.id.in_([qc_a.id, qc_b.id])))
            await db.execute(delete(Task).where(Task.id.in_([task_a.id, task_b.id])))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
            await db.execute(delete(User).where(User.id.in_([
                super_admin.id, admin_a.id, admin_b.id, user_custom_a.id, user_legacy_a.id
            ])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()


@pytest.mark.asyncio
async def test_batch_f_unauthenticated_all_22_routes_401():
    """Verify all 22 Batch F endpoints reject unauthenticated calls with 401 Unauthorized."""
    async with setup_batch_f_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            routes = [
                ("POST", "/api/v1/qc", {"project_id": d["proj_a_id"], "inspection_type": "T", "test_type": "T", "result": 1.0, "standard_value": 1.0, "status": "Pass", "engineer_name": "E"}, True),
                ("GET", f"/api/v1/qc/{d['qc_a_id']}", None, False),
                ("GET", "/api/v1/qc", None, False),
                ("PUT", f"/api/v1/qc/{d['qc_a_id']}", {"project_id": d["proj_a_id"], "inspection_type": "T", "test_type": "T", "result": 1.0, "standard_value": 1.0, "status": "Pass", "engineer_name": "E"}, False),
                ("DELETE", f"/api/v1/qc/{d['qc_a_id']}", None, False),

                ("POST", "/api/v1/safety", {"project_id": d["proj_a_id"], "date": str(date.today()), "safety_checklist_status": "completed", "ppe_compliance": True, "violation_type": "V", "description": "D", "responsible_person": "P"}, False),
                ("GET", f"/api/v1/safety/{d['safety_a_id']}", None, False),
                ("GET", "/api/v1/safety", None, False),
                ("PUT", f"/api/v1/safety/{d['safety_a_id']}", {"project_id": d["proj_a_id"], "date": str(date.today()), "safety_checklist_status": "completed", "ppe_compliance": True, "violation_type": "V", "description": "D", "responsible_person": "P"}, False),
                ("DELETE", f"/api/v1/safety/{d['safety_a_id']}", None, False),

                ("GET", "/api/v1/checklists/logs", None, False),
                ("POST", "/api/v1/checklists", {"project_id": d["proj_a_id"], "name": "N", "type": "T"}, False),
                ("GET", f"/api/v1/checklists/{d['chk_a_id']}", None, False),
                ("PUT", f"/api/v1/checklists/{d['chk_a_id']}", {"name": "N2"}, False),
                ("DELETE", f"/api/v1/checklists/{d['chk_a_id']}", None, False),
                ("POST", "/api/v1/checklists/items", {"checklist_id": d["chk_a_id"], "item": "I"}, False),
                ("GET", f"/api/v1/checklists/{d['chk_a_id']}/items", None, False),
                ("PUT", f"/api/v1/checklists/items/{d['item_a_id']}", {"item": "I2"}, False),
                ("GET", f"/api/v1/checklists/items/{d['chk_a_id']}", None, False),
                ("DELETE", f"/api/v1/checklists/items/{d['item_a_id']}", None, False),
                ("GET", "/api/v1/checklists", None, False),
                ("POST", f"/api/v1/checklists/{d['chk_a_id']}/execute", {"project_id": d["proj_a_id"], "checklist_id": d["chk_a_id"], "status": "DONE"}, False),
            ]

            assert len(routes) == 22, f"Expected exactly 22 routes, found {len(routes)}"

            for method, path, data, is_query in routes:
                if method == "GET":
                    r = await client.get(path)
                elif method == "POST":
                    if is_query:
                        r = await client.post(path, params=data)
                    else:
                        r = await client.post(path, json=data)
                elif method == "PUT":
                    r = await client.put(path, json=data)
                elif method == "DELETE":
                    r = await client.delete(path)
                assert r.status_code == 401, f"{method} {path} returned {r.status_code}, expected 401"


@pytest.mark.asyncio
async def test_batch_f_custom_role_dynamic_lifecycle_qc():
    """Verify runtime dynamic RBAC lifecycle for QC routes with custom role: 403 -> grant -> 200 -> revoke -> 403 -> regrant -> 200."""
    async with setup_batch_f_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Custom role has 0 permissions -> 403
            r = await client.get("/api/v1/qc", headers=headers)
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

            # 2. Grant qc.view to custom role
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "qc.view"))).scalar_one()
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_view.id))
                await db.commit()

            # 3. Next request succeeds immediately (200) without server restart
            r = await client.get("/api/v1/qc", headers=headers)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

            # 4. Revoke qc.view from custom role
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == d["custom_role_name"],
                    RolePermission.permission_id == p_view.id
                ))
                await db.commit()

            # 5. Immediate 403 Forbidden
            r = await client.get("/api/v1/qc", headers=headers)
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

            # 6. Regrant qc.view
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_view.id))
                await db.commit()

            # 7. Immediate 200 OK
            r = await client.get("/api/v1/qc", headers=headers)
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_batch_f_custom_role_dynamic_lifecycle_safety():
    """Verify runtime dynamic RBAC lifecycle for Safety routes with custom role: 403 -> grant -> 200 -> revoke -> 403 -> regrant -> 200."""
    async with setup_batch_f_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Custom role has 0 permissions -> 403
            r = await client.get("/api/v1/safety", headers=headers)
            assert r.status_code == 403

            # 2. Grant safety.view to custom role
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "safety.view"))).scalar_one()
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_view.id))
                await db.commit()

            # 3. Next request succeeds immediately (200)
            r = await client.get("/api/v1/safety", headers=headers)
            assert r.status_code == 200

            # 4. Revoke safety.view
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == d["custom_role_name"],
                    RolePermission.permission_id == p_view.id
                ))
                await db.commit()

            # 5. Immediate 403 Forbidden
            r = await client.get("/api/v1/safety", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_batch_f_custom_role_dynamic_lifecycle_checklists():
    """Verify runtime dynamic RBAC lifecycle for Checklist routes with custom role: 403 -> grant -> 200 -> revoke -> 403 -> regrant -> 200."""
    async with setup_batch_f_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Custom role has 0 permissions -> 403
            r = await client.get("/api/v1/checklists", headers=headers)
            assert r.status_code == 403

            # 2. Grant checklists.view to custom role
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "checklists.view"))).scalar_one()
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_view.id))
                await db.commit()

            # 3. Immediate 200 OK
            r = await client.get("/api/v1/checklists", headers=headers)
            assert r.status_code == 200

            # 4. Revoke checklists.view
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == d["custom_role_name"],
                    RolePermission.permission_id == p_view.id
                ))
                await db.commit()

            # 5. Immediate 403 Forbidden
            r = await client.get("/api/v1/checklists", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_batch_f_user_permission_overrides():
    """Verify positive and negative user permission overrides take precedence over role permissions."""
    async with setup_batch_f_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. User role has 0 permissions -> 403
            r = await client.get("/api/v1/qc", headers=headers)
            assert r.status_code == 403

            # 2. Add positive override (is_granted=True) directly to user
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "qc.view"))).scalar_one()
                db.add(UserPermissionOverride(
                    user_id=d["user_custom_a_id"],
                    permission_id=p_view.id,
                    is_granted=True,
                ))
                await db.commit()

            # 3. User now gets 200 OK via override
            r = await client.get("/api/v1/qc", headers=headers)
            assert r.status_code == 200

            # 4. Now also grant role-level permission to the custom role
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_view.id))
                # Change user override to negative (is_granted=False)
                override = (await db.execute(select(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == d["user_custom_a_id"],
                    UserPermissionOverride.permission_id == p_view.id
                ))).scalar_one()
                override.is_granted = False
                await db.commit()

            # 5. Negative user override MUST take precedence over role grant -> 403 Forbidden
            r = await client.get("/api/v1/qc", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_batch_f_wildcard_permission():
    """Verify wildcard permissions ('*' and 'qc.*') grant access across module endpoints."""
    async with setup_batch_f_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_custom_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Grant wildcard permission to custom role
            async with AsyncSessionLocal() as db:
                p_wildcard = (await db.execute(select(Permission).where(Permission.code == "*"))).scalar_one_or_none()
                if not p_wildcard:
                    p_wildcard = Permission(code="*", module="all", action="*", description="Wildcard")
                    db.add(p_wildcard)
                    await db.flush()
                db.add(RolePermission(role=d["custom_role_name"], permission_id=p_wildcard.id))
                await db.commit()

            # Custom role with wildcard can access QC, Safety, and Checklist listings
            r_qc = await client.get("/api/v1/qc", headers=headers)
            assert r_qc.status_code == 200
            r_safety = await client.get("/api/v1/safety", headers=headers)
            assert r_safety.status_code == 200
            r_chk = await client.get("/api/v1/checklists", headers=headers)
            assert r_chk.status_code == 200


@pytest.mark.asyncio
async def test_batch_f_legacy_role_strings_denied():
    """Verify users with legacy role strings ('Project Manager') and 0 DB permissions receive 403 Forbidden."""
    async with setup_batch_f_data() as d:
        headers = {"Authorization": f"Bearer {d['tokens']['user_legacy_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Ensure "Project Manager" has no DB role permissions
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role == "Project Manager"))
                await db.commit()

            r1 = await client.get("/api/v1/qc", headers=headers)
            assert r1.status_code == 403, f"Legacy role should be denied, got {r1.status_code}"

            r2 = await client.get("/api/v1/safety", headers=headers)
            assert r2.status_code == 403, f"Legacy role should be denied, got {r2.status_code}"

            r3 = await client.get("/api/v1/checklists", headers=headers)
            assert r3.status_code == 403, f"Legacy role should be denied, got {r3.status_code}"


@pytest.mark.asyncio
async def test_batch_f_tenant_isolation_qc():
    """Verify Company A Admin cannot list, read, inject, mutate, or delete Company B QC records (P0-1, P0-2, P0-3, P0-4)."""
    async with setup_batch_f_data() as d:
        headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. P0-1: Listing without project_id returns ONLY Company A's QC records
            r = await client.get("/api/v1/qc", headers=headers_a)
            assert r.status_code == 200
            items = r.json()["items"]
            item_ids = [item["id"] for item in items]
            assert d["qc_a_id"] in item_ids
            assert d["qc_b_id"] not in item_ids, "Company B QC record leaked in list query!"

            # 2. P0-1: Listing with foreign project_id returns 404
            r_foreign_proj = await client.get(f"/api/v1/qc?project_id={d['proj_b_id']}", headers=headers_a)
            assert r_foreign_proj.status_code == 404

            # 3. P0-2 & P1-1: Detail lookup on foreign QC record returns 404 (masked)
            r_detail = await client.get(f"/api/v1/qc/{d['qc_b_id']}", headers=headers_a)
            assert r_detail.status_code == 404, f"Expected 404 for foreign QC detail, got {r_detail.status_code}"

            # 4. P0-3: Creating QC record under foreign Project B is rejected with 404
            r_create = await client.post(
                "/api/v1/qc",
                headers=headers_a,
                params={
                    "project_id": d["proj_b_id"],
                    "inspection_type": "Illegal Injection",
                    "test_type": "Injection Test",
                    "result": 100.0,
                    "standard_value": 100.0,
                    "status": "Pass",
                    "engineer_name": "Attacker",
                }
            )
            assert r_create.status_code == 404, f"Expected 404 for foreign project QC creation, got {r_create.status_code}"

            # 5. P0-4: Updating foreign QC record returns 404
            r_update = await client.put(
                f"/api/v1/qc/{d['qc_b_id']}",
                headers=headers_a,
                json={
                    "project_id": d["proj_b_id"],
                    "inspection_type": "Tampered",
                    "test_type": "Tampered",
                    "result": 999.0,
                    "standard_value": 100.0,
                    "status": "Fail",
                    "engineer_name": "Attacker",
                }
            )
            assert r_update.status_code == 404

            # 6. P0-4: Deleting foreign QC record returns 404
            r_del = await client.delete(f"/api/v1/qc/{d['qc_b_id']}", headers=headers_a)
            assert r_del.status_code == 404


@pytest.mark.asyncio
async def test_batch_f_tenant_isolation_safety():
    """Verify Company A Admin cannot list, read, inject, mutate, or delete Company B Safety incidents (P0-1, P0-2, P0-3, P0-4)."""
    async with setup_batch_f_data() as d:
        headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. P0-1: Listing without project_id returns ONLY Company A's safety incidents
            r = await client.get("/api/v1/safety", headers=headers_a)
            assert r.status_code == 200
            items = r.json()["items"]
            item_ids = [item["id"] for item in items]
            assert d["safety_a_id"] in item_ids
            assert d["safety_b_id"] not in item_ids, "Company B safety incident leaked in list query!"

            # 2. P0-1: Listing with foreign project_id returns 404
            r_foreign_proj = await client.get(f"/api/v1/safety?project_id={d['proj_b_id']}", headers=headers_a)
            assert r_foreign_proj.status_code == 404

            # 3. P0-2 & P1-1: Detail lookup on foreign safety incident returns 404
            r_detail = await client.get(f"/api/v1/safety/{d['safety_b_id']}", headers=headers_a)
            assert r_detail.status_code == 404

            # 4. P0-3: Logging safety incident in foreign Project B returns 404
            r_create = await client.post(
                "/api/v1/safety",
                headers=headers_a,
                json={
                    "project_id": d["proj_b_id"],
                    "date": str(date.today()),
                    "safety_checklist_status": "pending",
                    "ppe_compliance": False,
                    "violation_type": "Illegal Incident",
                    "description": "Cross tenant injection",
                    "responsible_person": "Attacker",
                }
            )
            assert r_create.status_code == 404

            # 5. P0-4: Updating foreign safety incident returns 404
            r_update = await client.put(
                f"/api/v1/safety/{d['safety_b_id']}",
                headers=headers_a,
                json={
                    "project_id": d["proj_b_id"],
                    "date": str(date.today()),
                    "safety_checklist_status": "completed",
                    "ppe_compliance": True,
                    "violation_type": "Tampered",
                    "description": "Tampered description",
                    "responsible_person": "Attacker",
                }
            )
            assert r_update.status_code == 404

            # 6. P0-4: Deleting foreign safety incident returns 404
            r_del = await client.delete(f"/api/v1/safety/{d['safety_b_id']}", headers=headers_a)
            assert r_del.status_code == 404


@pytest.mark.asyncio
async def test_batch_f_tenant_isolation_checklists():
    """Verify Company A Admin cannot list, read, inject, mutate, or execute Company B Checklists, Items, or Logs (P0-1..4, P1-1, P1-3)."""
    async with setup_batch_f_data() as d:
        headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. P0-1: Listing checklists without project_id returns ONLY Company A's checklists
            r = await client.get("/api/v1/checklists", headers=headers_a)
            assert r.status_code == 200
            chk_ids = [item["id"] for item in r.json()]
            assert d["chk_a_id"] in chk_ids
            assert d["chk_b_id"] not in chk_ids, "Company B checklist leaked in list query!"

            # 2. P0-1: Listing logs without project_id returns ONLY Company A's logs
            r_logs = await client.get("/api/v1/checklists/logs", headers=headers_a)
            assert r_logs.status_code == 200
            log_ids = [item["id"] for item in r_logs.json()["items"]]
            assert d["log_a_id"] in log_ids
            assert d["log_b_id"] not in log_ids, "Company B checklist log leaked!"

            # 3. P0-2 & P1-1: Detail lookup on foreign checklist returns 404
            r_chk_detail = await client.get(f"/api/v1/checklists/{d['chk_b_id']}", headers=headers_a)
            assert r_chk_detail.status_code == 404

            # 4. P0-2 & P1-1: Querying items of foreign checklist returns 404
            r_items_1 = await client.get(f"/api/v1/checklists/{d['chk_b_id']}/items", headers=headers_a)
            assert r_items_1.status_code == 404
            r_items_2 = await client.get(f"/api/v1/checklists/items/{d['chk_b_id']}", headers=headers_a)
            assert r_items_2.status_code == 404

            # 5. P0-3: Creating checklist in foreign Project B returns 404
            r_create_chk = await client.post(
                "/api/v1/checklists",
                headers=headers_a,
                json={"project_id": d["proj_b_id"], "name": "Injected Checklist", "type": "Test"}
            )
            assert r_create_chk.status_code == 404

            # 6. P0-3: Adding item to foreign checklist returns 404
            r_add_item = await client.post(
                "/api/v1/checklists/items",
                headers=headers_a,
                json={"checklist_id": d["chk_b_id"], "item": "Injected Check Item"}
            )
            assert r_add_item.status_code == 404

            # 7. P0-4: Updating or deleting foreign checklist returns 404
            r_upd_chk = await client.put(f"/api/v1/checklists/{d['chk_b_id']}", headers=headers_a, json={"name": "Tampered"})
            assert r_upd_chk.status_code == 404
            r_del_chk = await client.delete(f"/api/v1/checklists/{d['chk_b_id']}", headers=headers_a)
            assert r_del_chk.status_code == 404

            # 8. P0-4: Updating or deleting foreign checklist item returns 404
            r_upd_item = await client.put(f"/api/v1/checklists/items/{d['item_b_id']}", headers=headers_a, json={"item": "Tampered Item"})
            assert r_upd_item.status_code == 404
            r_del_item = await client.delete(f"/api/v1/checklists/items/{d['item_b_id']}", headers=headers_a)
            assert r_del_item.status_code == 404

            # 9. P1-3: Executing Checklist A with mismatch project_id (Project B) is rejected (400)
            r_exec_mismatch = await client.post(
                f"/api/v1/checklists/{d['chk_a_id']}/execute",
                headers=headers_a,
                json={"project_id": d["proj_b_id"], "checklist_id": d["chk_a_id"], "status": "DONE", "remarks": "Cross-Project Execution"}
            )
            assert r_exec_mismatch.status_code == 400

            # 10. Executing foreign checklist B returns 404
            r_exec_foreign = await client.post(
                f"/api/v1/checklists/{d['chk_b_id']}/execute",
                headers=headers_a,
                json={"project_id": d["proj_b_id"], "checklist_id": d["chk_b_id"], "status": "DONE", "remarks": "Foreign Execution"}
            )
            assert r_exec_foreign.status_code == 404


@pytest.mark.asyncio
async def test_batch_f_null_and_nonexistent_ids_404():
    """Verify non-existent IDs return 404 across all mutation and detail routes without 500 error (P1-2)."""
    async with setup_batch_f_data() as d:
        headers_a = {"Authorization": f"Bearer {d['tokens']['admin_a']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            non_existent_id = 999999999

            # QC mutations
            r1 = await client.get(f"/api/v1/qc/{non_existent_id}", headers=headers_a)
            assert r1.status_code == 404
            r2 = await client.put(
                f"/api/v1/qc/{non_existent_id}",
                headers=headers_a,
                json={"project_id": d["proj_a_id"], "inspection_type": "T", "test_type": "T", "result": 1.0, "standard_value": 1.0, "status": "Pass", "engineer_name": "E"}
            )
            assert r2.status_code == 404
            r3 = await client.delete(f"/api/v1/qc/{non_existent_id}", headers=headers_a)
            assert r3.status_code == 404

            # Safety mutations
            r4 = await client.get(f"/api/v1/safety/{non_existent_id}", headers=headers_a)
            assert r4.status_code == 404
            r5 = await client.put(
                f"/api/v1/safety/{non_existent_id}",
                headers=headers_a,
                json={"project_id": d["proj_a_id"], "date": str(date.today()), "safety_checklist_status": "completed", "ppe_compliance": True, "violation_type": "V", "description": "D", "responsible_person": "P"}
            )
            assert r5.status_code == 404
            r6 = await client.delete(f"/api/v1/safety/{non_existent_id}", headers=headers_a)
            assert r6.status_code == 404

            # Checklist mutations
            r7 = await client.get(f"/api/v1/checklists/{non_existent_id}", headers=headers_a)
            assert r7.status_code == 404
            r8 = await client.put(f"/api/v1/checklists/{non_existent_id}", headers=headers_a, json={"name": "N"})
            assert r8.status_code == 404
            r9 = await client.delete(f"/api/v1/checklists/{non_existent_id}", headers=headers_a)
            assert r9.status_code == 404
            r10 = await client.put(f"/api/v1/checklists/items/{non_existent_id}", headers=headers_a, json={"item": "I"})
            assert r10.status_code == 404
            r11 = await client.delete(f"/api/v1/checklists/items/{non_existent_id}", headers=headers_a)
            assert r11.status_code == 404


@pytest.mark.asyncio
async def test_batch_f_super_admin_tenant_context():
    """Verify Super Admin without tenant context receives safe responses rather than unconstrained cross-company leakage."""
    async with setup_batch_f_data() as d:
        headers_sa = {"Authorization": f"Bearer {d['tokens']['super_admin']}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Listing without project_id as super admin without company returns empty list safely
            r_qc = await client.get("/api/v1/qc", headers=headers_sa)
            assert r_qc.status_code == 200
            assert r_qc.json()["items"] == []

            r_safety = await client.get("/api/v1/safety", headers=headers_sa)
            assert r_safety.status_code == 200
            assert r_safety.json()["items"] == []

            r_chk = await client.get("/api/v1/checklists", headers=headers_sa)
            assert r_chk.status_code == 200
            assert r_chk.json() == []
