import uuid
import inspect
from datetime import date, time, datetime, timezone
from decimal import Decimal
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.base
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember, Task
from app.models.master_data import ActivityType
from app.models.work_order import WorkOrder
from app.models.work_update import WorkUpdate, WorkUpdateStatus
from app.models.cad_conversion import CADConversion
from app.models.project_visualization import ProjectVisualization
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.enums import ProjectStatus, TaskStatus
from app.core.security import get_password_hash, create_access_token
import app.core.rbac_seed as rbac_seed
from app.core.dependencies import get_effective_user_permissions, has_permission


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
# TARGET 1: BILLING.PAY IN RBAC SEED & CATALOG
# ============================================================================

@pytest.mark.asyncio
async def test_target_1_billing_pay_in_seed_and_catalog():
    """Verify billing.pay is present in MODULE_ACTIONS, seedable, and resolves correctly."""
    # 1. Check MODULE_ACTIONS contains pay for billing
    assert "billing" in rbac_seed.MODULE_ACTIONS, "billing must be in MODULE_ACTIONS"
    billing_actions = rbac_seed.MODULE_ACTIONS["billing"]
    expected_actions = ["view", "create", "edit", "delete", "approve", "export", "pay"]
    for act in expected_actions:
        assert act in billing_actions, f"billing must include {act}"
    
    # 2. Verify no duplicates in billing namespace
    assert len(billing_actions) == len(set(billing_actions)), "billing actions must have no duplicates"

    # 3. Seed permissions and check billing.pay exists in Permission table
    async with AsyncSessionLocal() as db:
        res = await rbac_seed.seed_permissions(db)
        assert res["message"] == "Permissions seeded successfully"

        perm = await db.scalar(select(Permission).where(Permission.code == "billing.pay"))
        assert perm is not None, "billing.pay must be seeded in database"
        assert perm.module == "billing"
        assert perm.action == "pay"

        # 4. Canonical resolution checks
        assert has_permission({"billing.pay"}, "billing.pay") is True
        assert has_permission({"billing.*"}, "billing.pay") is True
        assert has_permission({"*"}, "billing.pay") is True
        assert has_permission({"billing.view"}, "billing.pay") is False


# ============================================================================
# TARGET 2: WORK ORDER MANAGEMENT AUTHORIZATION & TENANCY
# ============================================================================

@pytest.mark.asyncio
async def test_target_2_work_order_db_driven_auth_and_tenancy():
    """Verify work order listing uses DB permissions without Admin role bypass."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp1 = Company(name=f"Comp1-{uid}", subdomain=f"c1{uid}")
        comp2 = Company(name=f"Comp2-{uid}", subdomain=f"c2{uid}")
        db.add_all([comp1, comp2])
        await db.flush()

        proj1 = await _create_project(db, comp1.id, f"1_{uid}")
        proj2 = await _create_project(db, comp2.id, f"2_{uid}")

        wo1 = WorkOrder(
            work_order_number=f"WO-1-{uid}",
            work_description="Work 1",
            total_quantity=Decimal("10.00"),
            rate=Decimal("100.00"),
            total_amount=Decimal("1000.00"),
            project_id=proj1.id,
            status="Assigned",
        )
        wo2 = WorkOrder(
            work_order_number=f"WO-2-{uid}",
            work_description="Work 2",
            total_quantity=Decimal("20.00"),
            rate=Decimal("100.00"),
            total_amount=Decimal("2000.00"),
            project_id=proj2.id,
            status="Assigned",
        )
        db.add_all([wo1, wo2])

        perm_view = await db.scalar(select(Permission).where(Permission.code == "work_orders.view"))
        if not perm_view:
            perm_view = Permission(module="work_orders", action="view", code="work_orders.view", description="view")
            db.add(perm_view)
            await db.flush()

        perm_manage = await db.scalar(select(Permission).where(Permission.code == "work_orders.manage"))
        if not perm_manage:
            perm_manage = Permission(module="work_orders", action="manage", code="work_orders.manage", description="manage")
            db.add(perm_manage)
            await db.flush()

        # User A has role="Admin", but has an explicit DENY override on work_orders.manage.
        # Under the old code, `current_user.role == "Admin"` bypassed DB permissions.
        # Under the new code, `work_orders.manage` is evaluated from DB perms and denies unassigned projects.
        user_admin = User(
            email=f"wo_admin_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="WO Admin User",
            company_id=comp1.id,
            role="Admin",
            is_active=True,
            is_super_admin=False,
        )

        # User B: custom role with work_orders.view, no work_orders.manage, not project member
        role_b = Role(company_id=comp1.id, name=f"RoleB_{uid}", display_name="Role B", is_system=False)
        db.add(role_b)
        await db.flush()
        db.add(RolePermission(role=role_b.name, role_id=role_b.id, permission_id=perm_view.id))

        user_b = User(
            email=f"wo_b_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="WO User B",
            company_id=comp1.id,
            role=role_b.name,
            is_active=True,
            is_super_admin=False,
        )

        user_sa = User(
            email=f"wo_sa_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Super Admin",
            company_id=None,
            role="SuperAdmin",
            is_active=True,
            is_super_admin=True,
        )
        db.add_all([user_admin, user_b, user_sa])
        await db.commit()
        await db.refresh(user_admin)
        await db.refresh(user_b)
        await db.refresh(user_sa)

        # 1. Deny work_orders.manage for user_admin
        deny_override = UserPermissionOverride(user_id=user_admin.id, permission_id=perm_manage.id, is_granted=False)
        db.add(deny_override)
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 2. User Admin without management permission cannot receive Admin role bypass -> 0 work orders
            res_admin_denied = await ac.get("/api/v1/work-orders", headers=_make_auth_header(user_admin))
            assert res_admin_denied.status_code == 200
            assert len(res_admin_denied.json()) == 0, "Admin role without work_orders.manage must not receive bypass"

            # 3. User B initially has no work_orders.manage -> 0 work orders
            res_b_initial = await ac.get("/api/v1/work-orders", headers=_make_auth_header(user_b))
            assert res_b_initial.status_code == 200
            assert len(res_b_initial.json()) == 0

            # 4. Grant work_orders.manage to User B via UserPermissionOverride
            grant_b = UserPermissionOverride(user_id=user_b.id, permission_id=perm_manage.id, is_granted=True)
            db.add(grant_b)
            await db.commit()

            res_b_granted = await ac.get("/api/v1/work-orders", headers=_make_auth_header(user_b))
            assert res_b_granted.status_code == 200
            data_b = res_b_granted.json()
            assert len(data_b) == 1, "User with work_orders.manage should now see company work orders"
            assert data_b[0]["id"] == wo1.id

            # 5. Revoke work_orders.manage immediately removes management access
            await db.delete(grant_b)
            await db.commit()

            res_b_revoked = await ac.get("/api/v1/work-orders", headers=_make_auth_header(user_b))
            assert res_b_revoked.status_code == 200
            assert len(res_b_revoked.json()) == 0, "Revoking work_orders.manage immediately drops access"

            # 6. Tenant isolation: User B never sees Company 2's work order even with manage
            grant_b2 = UserPermissionOverride(user_id=user_b.id, permission_id=perm_manage.id, is_granted=True)
            db.add(grant_b2)
            await db.commit()
            res_b_check = await ac.get("/api/v1/work-orders", headers=_make_auth_header(user_b))
            ids_b = [w["id"] for w in res_b_check.json()]
            assert wo1.id in ids_b
            assert wo2.id not in ids_b, "Tenant isolation: User B must never see foreign company work orders"

            # 7. Super Admin sees work orders across all companies
            res_sa = await ac.get("/api/v1/work-orders", headers=_make_auth_header(user_sa))
            assert res_sa.status_code == 200
            data_sa = res_sa.json()
            wo_ids = [w["id"] for w in data_sa]
            assert wo1.id in wo_ids and wo2.id in wo_ids, "Super Admin sees all work orders"


# ============================================================================
# TARGET 3: WORK UPDATE DB-DRIVEN EDIT AUTHORIZATION & TENANCY
# ============================================================================

@pytest.mark.asyncio
async def test_target_3_work_update_db_driven_edit_auth():
    """Verify work update edits respect creator ownership and DB permissions without role bypass."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp1 = Company(name=f"WUComp1-{uid}", subdomain=f"wuc1{uid}")
        comp2 = Company(name=f"WUComp2-{uid}", subdomain=f"wuc2{uid}")
        db.add_all([comp1, comp2])
        await db.flush()

        proj1 = await _create_project(db, comp1.id, f"wu_{uid}")

        perm_view = await db.scalar(select(Permission).where(Permission.code == "work_updates.view"))
        if not perm_view:
            perm_view = Permission(module="work_updates", action="view", code="work_updates.view", description="view")
            db.add(perm_view)

        perm_edit = await db.scalar(select(Permission).where(Permission.code == "work_updates.edit"))
        if not perm_edit:
            perm_edit = Permission(module="work_updates", action="edit", code="work_updates.edit", description="edit")
            db.add(perm_edit)
        await db.flush()

        # Creator User
        role_creator = Role(company_id=comp1.id, name=f"WUCreator_{uid}", display_name="Creator", is_system=False)
        db.add(role_creator)
        await db.flush()
        db.add_all([
            RolePermission(role=role_creator.name, role_id=role_creator.id, permission_id=perm_view.id),
            RolePermission(role=role_creator.name, role_id=role_creator.id, permission_id=perm_edit.id),
        ])

        creator = User(
            email=f"wu_creator_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="WU Creator",
            company_id=comp1.id,
            role=role_creator.name,
            is_active=True,
            is_super_admin=False,
        )
        db.add(creator)
        await db.flush()

        task1 = Task(
            title=f"Task1-{uid}",
            project_id=proj1.id,
            created_by_user_id=creator.id,
            status=TaskStatus.IN_PROGRESS,
        )
        db.add(task1)

        act_type = await db.scalar(select(ActivityType))
        if not act_type:
            act_type = ActivityType(name=f"Act-{uid}", code=f"ACT-{uid.upper()[:4]}")
            db.add(act_type)
        await db.flush()

        # Non-creator with role="Admin" but with work_updates.edit explicitly DENIED via override
        role_admin = Role(company_id=comp1.id, name=f"Admin_{uid}", display_name="Admin", is_system=False)
        db.add(role_admin)
        await db.flush()
        db.add(RolePermission(role=role_admin.name, role_id=role_admin.id, permission_id=perm_view.id))

        admin_user_no_edit = User(
            email=f"wu_admin_no_edit_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Admin No Edit",
            company_id=comp1.id,
            role="Admin",
            is_active=True,
            is_super_admin=False,
        )

        # Non-creator Engineer WITH work_updates.edit
        role_eng = Role(company_id=comp1.id, name=f"WUEng_{uid}", display_name="Engineer", is_system=False)
        db.add(role_eng)
        await db.flush()
        db.add_all([
            RolePermission(role=role_eng.name, role_id=role_eng.id, permission_id=perm_view.id),
            RolePermission(role=role_eng.name, role_id=role_eng.id, permission_id=perm_edit.id),
        ])

        eng_user = User(
            email=f"wu_eng_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Engineer With Edit",
            company_id=comp1.id,
            role=role_eng.name,
            is_active=True,
            is_super_admin=False,
        )

        # Foreign user in Comp 2 with work_updates.edit in Comp 2
        role_foreign = Role(company_id=comp2.id, name=f"WUForeign_{uid}", display_name="Foreign", is_system=False)
        db.add(role_foreign)
        await db.flush()
        db.add_all([
            RolePermission(role=role_foreign.name, role_id=role_foreign.id, permission_id=perm_view.id),
            RolePermission(role=role_foreign.name, role_id=role_foreign.id, permission_id=perm_edit.id),
        ])

        foreign_user = User(
            email=f"wu_foreign_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Foreign User",
            company_id=comp2.id,
            role=role_foreign.name,
            is_active=True,
            is_super_admin=False,
        )

        db.add_all([admin_user_no_edit, eng_user, foreign_user])
        await db.flush()

        # Deny work_updates.edit for admin_user_no_edit to ensure no role bypass
        db.add(UserPermissionOverride(user_id=admin_user_no_edit.id, permission_id=perm_edit.id, is_granted=False))

        # Add project memberships
        db.add_all([
            ProjectMember(project_id=proj1.id, user_id=creator.id),
            ProjectMember(project_id=proj1.id, user_id=admin_user_no_edit.id),
            ProjectMember(project_id=proj1.id, user_id=eng_user.id),
        ])

        # Create WorkUpdate by creator
        wu = WorkUpdate(
            business_id=f"WU-{uid.upper()}",
            project_id=proj1.id,
            task_id=task1.id,
            activity_type_id=act_type.id,
            created_by_id=creator.id,
            work_description="Initial description of progress",
            work_date=date.today(),
            start_time=time(9, 0),
            status=WorkUpdateStatus.DRAFT.value,
        )
        db.add(wu)
        await db.commit()
        await db.refresh(wu)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Creator can update draft
            res_creator = await ac.put(
                f"/api/v1/work-updates/{wu.id}",
                headers=_make_auth_header(creator),
                json={"work_description": "Creator updated work description"},
            )
            assert res_creator.status_code == 200, f"Creator should be able to update draft: {res_creator.text}"

            # 2. Non-creator Admin user without work_updates.edit denied (HTTP 403, no role bypass)
            res_admin = await ac.put(
                f"/api/v1/work-updates/{wu.id}",
                headers=_make_auth_header(admin_user_no_edit),
                json={"work_description": "Admin hijacked description"},
            )
            assert res_admin.status_code == 403, "Admin without work_updates.edit must be denied"

            # 3. Non-creator user WITH work_updates.edit allowed
            res_eng = await ac.put(
                f"/api/v1/work-updates/{wu.id}",
                headers=_make_auth_header(eng_user),
                json={"work_description": "Engineer valid update description"},
            )
            assert res_eng.status_code == 200, f"Authorized user with work_updates.edit should be allowed: {res_eng.text}"

            # 4. Foreign tenant user returns 404 (scoped work update not found)
            res_foreign = await ac.put(
                f"/api/v1/work-updates/{wu.id}",
                headers=_make_auth_header(foreign_user),
                json={"work_description": "Foreign update description"},
            )
            assert res_foreign.status_code == 404, "Foreign tenant user should receive 404"


# ============================================================================
# TARGET 4: CAD CONVERSION SA & TENANCY VALIDATION
# ============================================================================

@pytest.mark.asyncio
async def test_target_4_cad_sa_and_tenant_context():
    """Verify CAD endpoints enforce company context for non-SA and canonical SA checks."""
    # 1. Static check: verify no bare is_super_admin in app/api/cad.py
    import app.api.cad as cad_module
    src = inspect.getsource(cad_module)
    assert "current_user.is_super_admin" not in src, "Bare current_user.is_super_admin must be remediated in cad.py"

    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp1 = Company(name=f"CADComp1-{uid}", subdomain=f"cad1{uid}")
        comp2 = Company(name=f"CADComp2-{uid}", subdomain=f"cad2{uid}")
        db.add_all([comp1, comp2])
        await db.flush()

        perm_view = await db.scalar(select(Permission).where(Permission.code == "drawings.view"))
        if not perm_view:
            perm_view = Permission(module="drawings", action="view", code="drawings.view", description="view")
            db.add(perm_view)

        perm_create = await db.scalar(select(Permission).where(Permission.code == "drawings.create"))
        if not perm_create:
            perm_create = Permission(module="drawings", action="create", code="drawings.create", description="create")
            db.add(perm_create)
        await db.flush()

        role = Role(company_id=comp1.id, name=f"CADRole_{uid}", display_name="CAD Role", is_system=False)
        db.add(role)
        await db.flush()
        db.add_all([
            RolePermission(role=role.name, role_id=role.id, permission_id=perm_view.id),
            RolePermission(role=role.name, role_id=role.id, permission_id=perm_create.id),
        ])

        # Tenant-scoped user
        user_c1 = User(
            email=f"cad_c1_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="CAD User 1",
            company_id=comp1.id,
            role=role.name,
            is_active=True,
            is_super_admin=False,
        )
        # Tenantless non-SA user
        user_tenantless = User(
            email=f"cad_tenantless_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="CAD Tenantless",
            company_id=None,
            role=role.name,
            is_active=True,
            is_super_admin=False,
        )
        # Super Admin
        user_sa = User(
            email=f"cad_sa_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="CAD SA",
            company_id=None,
            role="SuperAdmin",
            is_active=True,
            is_super_admin=True,
        )

        cad1 = CADConversion(project_name="CAD 1", file_path="uploads/cad1.dxf", area=100.0, company_id=comp1.id)
        cad2 = CADConversion(project_name="CAD 2", file_path="uploads/cad2.dxf", area=200.0, company_id=comp2.id)

        db.add_all([user_c1, user_tenantless, user_sa, cad1, cad2])
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 2. Tenantless non-SA gets 403 Company context required on logs
            res_tl_logs = await ac.get("/api/v1/cad/logs", headers=_make_auth_header(user_tenantless))
            assert res_tl_logs.status_code == 403
            assert any(msg in res_tl_logs.json()["detail"] for msg in ["Company context required", "User does not belong to any company"])

            # 3. Tenantless non-SA gets 403 on csv-to-dxf conversion
            files = {"file": ("test.csv", b"x,y\n0,0\n1,1", "text/csv")}
            res_tl_conv = await ac.post("/api/v1/cad/csv-to-dxf", headers=_make_auth_header(user_tenantless), files=files)
            assert res_tl_conv.status_code == 403
            assert any(msg in res_tl_conv.json()["detail"] for msg in ["Company context required", "User does not belong to any company"])

            # 4. Tenant-scoped non-SA only sees their company's conversions
            res_c1 = await ac.get("/api/v1/cad/logs", headers=_make_auth_header(user_c1))
            assert res_c1.status_code == 200
            data_c1 = res_c1.json()
            ids = [c["id"] for c in data_c1]
            assert cad1.id in ids
            assert cad2.id not in ids, "Non-SA user must not see foreign company CAD conversions"

            # 5. Super Admin sees all conversions
            res_sa = await ac.get("/api/v1/cad/logs", headers=_make_auth_header(user_sa))
            assert res_sa.status_code == 200
            data_sa = res_sa.json()
            ids_sa = [c["id"] for c in data_sa]
            assert cad1.id in ids_sa and cad2.id in ids_sa


# ============================================================================
# TARGET 5: PROJECT VISUALIZATION SCOPED LOOKUP & TENANT ISOLATION
# ============================================================================

@pytest.mark.asyncio
async def test_target_5_project_visualization_scoped_lookup():
    """Verify project visualization endpoints enforce tenant isolation and canonical project scoping."""
    # 1. Static check: verify no direct db.get(Project, id) in project_visualization.py
    import app.api.project_visualization as viz_module
    src = inspect.getsource(viz_module)
    assert "db.get(Project" not in src, "Direct db.get(Project, id) must be eliminated"
    assert "_get_scoped_project" in src, "Must use canonical _get_scoped_project helper"

    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        comp1 = Company(name=f"VizComp1-{uid}", subdomain=f"vc1{uid}")
        comp2 = Company(name=f"VizComp2-{uid}", subdomain=f"vc2{uid}")
        db.add_all([comp1, comp2])
        await db.flush()

        proj1 = await _create_project(db, comp1.id, f"v1_{uid}")
        proj2 = await _create_project(db, comp2.id, f"v2_{uid}")

        perm_view = await db.scalar(select(Permission).where(Permission.code == "projects.view"))
        if not perm_view:
            perm_view = Permission(module="projects", action="view", code="projects.view", description="view")
            db.add(perm_view)

        perm_upload = await db.scalar(select(Permission).where(Permission.code == "projects.upload"))
        if not perm_upload:
            perm_upload = Permission(module="projects", action="upload", code="projects.upload", description="upload")
            db.add(perm_upload)
        await db.flush()

        role = Role(company_id=comp1.id, name=f"VizRole_{uid}", display_name="Viz Role", is_system=False)
        db.add(role)
        await db.flush()
        db.add_all([
            RolePermission(role=role.name, role_id=role.id, permission_id=perm_view.id),
            RolePermission(role=role.name, role_id=role.id, permission_id=perm_upload.id),
        ])

        # User in Comp 1
        user_c1 = User(
            email=f"viz_c1_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Viz User 1",
            company_id=comp1.id,
            role=role.name,
            is_active=True,
            is_super_admin=False,
        )
        # Tenantless non-SA user
        user_tenantless = User(
            email=f"viz_tenantless_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Viz Tenantless",
            company_id=None,
            role=role.name,
            is_active=True,
            is_super_admin=False,
        )
        # Super Admin
        user_sa = User(
            email=f"viz_sa_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Viz SA",
            company_id=None,
            role="SuperAdmin",
            is_active=True,
            is_super_admin=True,
        )

        viz1 = ProjectVisualization(
            visualization_id=f"VIZ-{uid[:4].upper()}",
            project_id=proj1.id,
            title="Foundation",
            points=10,
            image_url="/uploads/visualizations/viz1.png",
        )
        db.add_all([user_c1, user_tenantless, user_sa, viz1])
        await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 2. Same-tenant project returns 200 and visualization list
            res_same = await ac.get(f"/api/v1/projects/{proj1.id}/visualizations", headers=_make_auth_header(user_c1))
            assert res_same.status_code == 200
            data_same = res_same.json()
            assert len(data_same) == 1
            assert data_same[0]["id"] == viz1.id

            # 3. Foreign project masked as 404
            res_foreign = await ac.get(f"/api/v1/projects/{proj2.id}/visualizations", headers=_make_auth_header(user_c1))
            assert res_foreign.status_code == 404
            assert "Project not found" in res_foreign.json()["detail"]

            # 4. Tenantless non-SA rejected with 403 Company context required
            res_tl = await ac.get(f"/api/v1/projects/{proj1.id}/visualizations", headers=_make_auth_header(user_tenantless))
            assert res_tl.status_code == 403
            assert any(msg in res_tl.json()["detail"] for msg in ["Company context required", "User does not belong to any company"])

            # 5. SA retains legitimate cross-company access
            res_sa = await ac.get(f"/api/v1/projects/{proj2.id}/visualizations", headers=_make_auth_header(user_sa))
            assert res_sa.status_code == 200
