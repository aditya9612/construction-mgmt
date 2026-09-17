import os
import uuid
from datetime import date
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import (
    Project,
    ProjectMember,
    Task,
    Milestone,
    DrawingDocument,
    QCRecord,
    SafetyIncident,
    Checklist,
    ChecklistItem,
)
from app.models.master_data import ActivityType
from app.models.rbac import Role, Permission, RolePermission
from app.core.enums import ProjectStatus, TaskStatus, DocumentStatus, SafetyChecklistStatus
from app.core.security import get_password_hash, create_access_token


def _make_auth_header(user: User):
    token = create_access_token(
        data={"sub": str(user.id), "role": user.role, "company_id": user.company_id}
    )
    return {"Authorization": f"Bearer {token}"}


async def _create_project(db: AsyncSession, company_id: int, uid: str) -> Project:
    owner = Owner(
        company_id=company_id,
        owner_code=f"OWN-{uid.upper()[:8]}",
        owner_name=f"Owner {uid}",
        mobile=f"9{str(uuid.uuid4().int)[:9]}",
    )
    db.add(owner)
    await db.flush()

    proj = Project(
        project_name=f"Proj-{uid}",
        business_id=f"PRJ-{uid.upper()[:8]}",
        company_id=company_id,
        owner_id=owner.id,
        status=ProjectStatus.ONGOING,
    )
    db.add(proj)
    await db.flush()
    return proj


# ============================================================================
# 1. UNAUTHENTICATED REQUESTS (401) FOR PROJECT ENDPOINTS
# ============================================================================

@pytest.mark.asyncio
async def test_bd_01_unauthenticated_requests_401():
    """Verify that unauthenticated requests to representative endpoints strictly return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("GET", "/api/v1/projects"),
            ("POST", "/api/v1/projects"),
            ("GET", "/api/v1/projects/1"),
            ("PUT", "/api/v1/projects/1"),
            ("DELETE", "/api/v1/projects/1"),
            ("POST", "/api/v1/projects/1/members/2"),
            ("DELETE", "/api/v1/projects/1/members/2"),
            ("POST", "/api/v1/projects/1/tasks/1/comments"),
            ("GET", "/api/v1/drawings/documents/download/1"),
            ("GET", "/api/v1/drawings/documents/view/1"),
            ("GET", "/api/v1/qc/1"),
            ("GET", "/api/v1/safety/1"),
            ("GET", "/api/v1/checklists/1"),
        ]

        for method, path in endpoints:
            res = await ac.request(method, path)
            assert res.status_code == 401, f"{method} {path} returned {res.status_code}, expected 401"


# ============================================================================
# 2. TENANTLESS NON-SA REQUESTS (403 Company context required)
# ============================================================================

@pytest.mark.asyncio
async def test_bd_02_tenantless_non_sa_403():
    """Verify that a non-SA user with company_id=None gets 403 Company context required."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        user = User(
            email=f"bd_tenantless_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Tenantless User",
            company_id=None,
            role="User",
            is_active=True,
            is_super_admin=False,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        headers = _make_auth_header(user)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/projects", headers=headers)
            assert res.status_code == 403
            assert res.json().get("detail") in ("Company context required", "User does not belong to any company.")

            res_qc = await ac.get("/api/v1/qc", headers=headers)
            assert res_qc.status_code == 403
            assert res_qc.json().get("detail") in ("Company context required", "User does not belong to any company.")

            res_drawing = await ac.get("/api/v1/drawings/documents/download/1", headers=headers)
            assert res_drawing.status_code == 403
            assert res_drawing.json().get("detail") in ("Company context required", "User does not belong to any company.")


# ============================================================================
# 3. DYNAMIC RBAC: projects.assign ON MEMBER ASSIGNMENT ROUTES
# ============================================================================

@pytest.mark.asyncio
async def test_bd_03_projects_assign_dynamic_rbac():
    """Verify projects.assign is required to assign and remove project members."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp = Company(name=f"BD-Comp-{uid}", subdomain=f"bd{uid[:4]}")
        db.add(comp)
        await db.flush()

        role = Role(company_id=comp.id, name=f"BD_Role_{uid}", display_name="BD Role", is_system=False)
        user = User(
            email=f"bd_user_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="BD User",
            company_id=comp.id,
            role=role.name,
            is_active=True,
            is_super_admin=False,
        )
        target_member = User(
            email=f"bd_target_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Target Member",
            company_id=comp.id,
            role="User",
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role, user, target_member])
        await db.flush()

        proj = await _create_project(db, comp.id, uid)
        await db.commit()
        await db.refresh(proj)
        await db.refresh(target_member)
        await db.refresh(user)

        headers = _make_auth_header(user)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Without projects.assign -> 403
            res_post = await ac.post(f"/api/v1/projects/{proj.id}/members/{target_member.id}", headers=headers)
            assert res_post.status_code == 403

            res_del = await ac.delete(f"/api/v1/projects/{proj.id}/members/{target_member.id}", headers=headers)
            assert res_del.status_code == 403

            # 2. Grant projects.assign
            perm = await db.scalar(select(Permission).where(Permission.code == "projects.assign"))
            if not perm:
                perm = Permission(code="projects.assign", module="projects", action="assign", description="Assign project members")
                db.add(perm)
                await db.flush()

            rp = RolePermission(role=role.name, role_id=role.id, permission_id=perm.id)
            db.add(rp)
            await db.commit()

            # Now has permission (may return 200, 201 or business validation)
            res_post2 = await ac.post(f"/api/v1/projects/{proj.id}/members/{target_member.id}", headers=headers)
            assert res_post2.status_code != 403, f"Expected non-403 after granting projects.assign, got {res_post2.status_code}"


# ============================================================================
# 4. COMMENT CREATION: tasks.edit REQUIRED
# ============================================================================

@pytest.mark.asyncio
async def test_bd_04_create_comment_requires_tasks_edit():
    """Verify POST /api/v1/projects/{project_id}/tasks/{task_id}/comments requires tasks.edit."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp = Company(name=f"BD-Comp-{uid}", subdomain=f"bd{uid[:4]}")
        db.add(comp)
        await db.flush()

        role = Role(company_id=comp.id, name=f"BD_Role_{uid}", display_name="BD Role", is_system=False)
        user = User(
            email=f"bd_comm_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="BD Comm User",
            company_id=comp.id,
            role=role.name,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role, user])
        await db.flush()

        proj = await _create_project(db, comp.id, uid)

        task = Task(
            project_id=proj.id,
            title="Test Task",
            status=TaskStatus.PLANNED,
            created_by_user_id=user.id,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        headers = _make_auth_header(user)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Without tasks.edit -> 403
            res = await ac.post(
                f"/api/v1/projects/{proj.id}/tasks/{task.id}/comments",
                headers=headers,
                json={"content": "Hello comment"},
            )
            assert res.status_code == 403

            # 2. Grant tasks.edit
            perm = await db.scalar(select(Permission).where(Permission.code == "tasks.edit"))
            if not perm:
                perm = Permission(code="tasks.edit", module="tasks", action="edit", description="Edit tasks")
                db.add(perm)
                await db.flush()

            rp = RolePermission(role=role.name, role_id=role.id, permission_id=perm.id)
            db.add(rp)
            await db.commit()

            # 3. Add user as member to satisfy business rule
            pm = ProjectMember(project_id=proj.id, user_id=user.id)
            db.add(pm)
            await db.commit()

            res2 = await ac.post(
                f"/api/v1/projects/{proj.id}/tasks/{task.id}/comments",
                headers=headers,
                json={"content": "Authorized comment"},
            )
            assert res2.status_code != 403, f"Expected non-403, got {res2.status_code}: {res2.text}"


# ============================================================================
# 5. DRAWING DOWNLOAD & VIEW: TENANT / IDOR PROTECTION
# ============================================================================

@pytest.mark.asyncio
async def test_bd_05_drawing_document_download_view_idor_protection():
    """Verify that foreign-tenant drawing documents return 404 on download and view."""
    async with AsyncSessionLocal() as db:
        uid1 = uuid.uuid4().hex[:6]
        uid2 = uuid.uuid4().hex[:6]

        # Company 1
        comp1 = Company(name=f"Comp1-{uid1}", subdomain=f"c1{uid1[:4]}")
        comp2 = Company(name=f"Comp2-{uid2}", subdomain=f"c2{uid2[:4]}")
        db.add_all([comp1, comp2])
        await db.flush()

        role1 = Role(company_id=comp1.id, name=f"Role1_{uid1}", display_name="Role 1", is_system=False)
        user1 = User(
            email=f"user1_{uid1}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="User One",
            company_id=comp1.id,
            role=role1.name,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role1, user1])
        await db.flush()

        # Company 2 Project & Drawing
        proj2 = await _create_project(db, comp2.id, uid2)

        # Create dummy physical file
        os.makedirs("uploads/drawings", exist_ok=True)
        dummy_file = f"uploads/drawings/test_drawing_{uid2}.pdf"
        with open(dummy_file, "wb") as f:
            f.write(b"%PDF-1.4 test drawing content")

        drawing2 = DrawingDocument(
            project_id=proj2.id,
            drawing_name="Secret Drawing",
            version="1.0",
            file_url=dummy_file,
            is_folder=False,
            is_latest_version=True,
            approval_status=DocumentStatus.APPROVED,
        )
        db.add(drawing2)

        # Grant permissions to user1 for download and view
        for perm_name in ["drawings.download", "drawings.view"]:
            perm = await db.scalar(select(Permission).where(Permission.code == perm_name))
            if not perm:
                perm = Permission(code=perm_name, module="drawings", action=perm_name.split(".")[1], description=perm_name)
                db.add(perm)
                await db.flush()
            db.add(RolePermission(role=role1.name, role_id=role1.id, permission_id=perm.id))

        await db.commit()
        await db.refresh(drawing2)

        headers1 = _make_auth_header(user1)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # User 1 tries to download Company 2's drawing -> MUST be 404 (masked)
            res_dl = await ac.get(f"/api/v1/drawings/documents/download/{drawing2.id}", headers=headers1)
            assert res_dl.status_code == 404, f"Expected 404 for cross-tenant download, got {res_dl.status_code}"

            # User 1 tries to view Company 2's drawing -> MUST be 404 (masked)
            res_view = await ac.get(f"/api/v1/drawings/documents/view/{drawing2.id}", headers=headers1)
            assert res_view.status_code == 404, f"Expected 404 for cross-tenant view, got {res_view.status_code}"

        # Cleanup test file
        if os.path.exists(dummy_file):
            try:
                os.remove(dummy_file)
            except Exception:
                pass


# ============================================================================
# 6. QC / SAFETY / CHECKLISTS: CROSS-TENANT 404 IDOR MASKING
# ============================================================================

@pytest.mark.asyncio
async def test_bd_06_qc_safety_checklist_cross_tenant_idor_masking():
    """Verify that foreign-tenant QC, Safety, and Checklist records return 404."""
    async with AsyncSessionLocal() as db:
        uid1 = uuid.uuid4().hex[:6]
        uid2 = uuid.uuid4().hex[:6]

        comp1 = Company(name=f"Comp1-{uid1}", subdomain=f"c1{uid1[:4]}")
        comp2 = Company(name=f"Comp2-{uid2}", subdomain=f"c2{uid2[:4]}")
        db.add_all([comp1, comp2])
        await db.flush()

        role1 = Role(company_id=comp1.id, name=f"Role1_{uid1}", display_name="Role 1", is_system=False)
        user1 = User(
            email=f"user1_{uid1}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="User One",
            company_id=comp1.id,
            role=role1.name,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role1, user1])
        await db.flush()

        proj2 = await _create_project(db, comp2.id, uid2)

        # Company 2 resources
        qc2 = QCRecord(project_id=proj2.id, inspection_type="Concrete", test_type="Slump", status="Passed")
        safety2 = SafetyIncident(
            project_id=proj2.id,
            safety_checklist_status=SafetyChecklistStatus.PENDING,
            violation_type="No Helmet",
            description="Test incident",
        )
        checklist2 = Checklist(project_id=proj2.id, name="Foundation Checklist")
        db.add_all([qc2, safety2, checklist2])

        # Grant user1 permissions
        for perm_name in ["qc.view", "safety.view", "checklists.view"]:
            perm = await db.scalar(select(Permission).where(Permission.code == perm_name))
            if not perm:
                perm = Permission(code=perm_name, module=perm_name.split(".")[0], action="view", description=perm_name)
                db.add(perm)
                await db.flush()
            db.add(RolePermission(role=role1.name, role_id=role1.id, permission_id=perm.id))

        await db.commit()
        await db.refresh(qc2)
        await db.refresh(safety2)
        await db.refresh(checklist2)

        headers1 = _make_auth_header(user1)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Foreign QC -> 404
            res_qc = await ac.get(f"/api/v1/qc/{qc2.id}", headers=headers1)
            assert res_qc.status_code == 404

            # 2. Foreign Safety -> 404
            res_saf = await ac.get(f"/api/v1/safety/{safety2.id}", headers=headers1)
            assert res_saf.status_code == 404

            # 3. Foreign Checklist -> 404
            res_cl = await ac.get(f"/api/v1/checklists/{checklist2.id}", headers=headers1)
            assert res_cl.status_code == 404


# ============================================================================
# 7. ACTIVITY TYPE: CROSS-TENANT FK INJECTION PROTECTION
# ============================================================================

@pytest.mark.asyncio
async def test_bd_07_activity_type_cross_tenant_fk_injection_protection():
    """Verify that using another tenant's ActivityType ID in create_task fails with 404."""
    async with AsyncSessionLocal() as db:
        uid1 = uuid.uuid4().hex[:6]
        uid2 = uuid.uuid4().hex[:6]

        comp1 = Company(name=f"Comp1-{uid1}", subdomain=f"c1{uid1[:4]}")
        comp2 = Company(name=f"Comp2-{uid2}", subdomain=f"c2{uid2[:4]}")
        db.add_all([comp1, comp2])
        await db.flush()

        role1 = Role(company_id=comp1.id, name=f"Role1_{uid1}", display_name="Role 1", is_system=False)
        user1 = User(
            email=f"user1_{uid1}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="User One",
            company_id=comp1.id,
            role=role1.name,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([role1, user1])
        await db.flush()

        proj1 = await _create_project(db, comp1.id, uid1)
        # Foreign activity type belonging to Company 2
        act2 = ActivityType(name=f"Comp2 Masonry {uid2}", company_id=comp2.id)
        db.add(act2)

        # Grant tasks.create to user1
        perm = await db.scalar(select(Permission).where(Permission.code == "tasks.create"))
        if not perm:
            perm = Permission(code="tasks.create", module="tasks", action="create", description="Create tasks")
            db.add(perm)
            await db.flush()
        db.add(RolePermission(role=role1.name, role_id=role1.id, permission_id=perm.id))

        await db.commit()
        await db.refresh(proj1)
        await db.refresh(act2)

        headers1 = _make_auth_header(user1)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # User 1 creates task with Foreign ActivityType -> MUST FAIL with 404 (NotFoundError)
            payload = {
                "title": "Task with Foreign Activity",
                "priority": 1,
                "activity_type_id": act2.id,
            }
            res = await ac.post(f"/api/v1/projects/{proj1.id}/tasks", headers=headers1, data=payload)
            assert res.status_code in (400, 404), f"Expected 400 or 404, got {res.status_code}: {res.text}"
            assert "activity" in res.text.lower()
