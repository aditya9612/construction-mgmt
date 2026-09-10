import uuid
from decimal import Decimal
from datetime import date
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
from app.models.project import Project, ProjectMember
from app.models.contractor import Contractor, ContractorProject
from app.models.expense import Expense
from app.models.settings import CompanySettings, UserSettings
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token


@asynccontextmanager
async def setup_batch_d_data():
    """Seed test companies, projects, contractors, and users for Batch D test suite."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Create two test companies
        comp_a = Company(name=f"BatchD_CompA_{uid}")
        comp_b = Company(name=f"BatchD_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company settings
        cs_a = CompanySettings(company_id=comp_a.id)
        cs_b = CompanySettings(company_id=comp_b.id)
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-DA-{uid}",
            owner_name=f"Owner DA {uid}",
            email=f"ownerda_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-DB-{uid}",
            owner_name=f"Owner DB {uid}",
            email=f"ownerdb_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 4. Projects
        proj_a = Project(
            business_id=f"PRJ-DA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_DA_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-DB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_DB_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 5. Contractors
        contractor_a = Contractor(
            company_id=comp_a.id,
            contractor_id=f"CNT-DA-{uid}",
            name=f"Contractor A {uid}",
            work_type="Civil",
            contact_number=f"98{uid[:8]}",
            gst_number="27AAAAA0000A1Z5",
            rate_type="Item Rate",
            total_work_assigned=Decimal("10000.00"),
            payment_given=Decimal("0.00"),
        )
        contractor_b = Contractor(
            company_id=comp_b.id,
            contractor_id=f"CNT-DB-{uid}",
            name=f"Contractor B {uid}",
            work_type="Electrical",
            contact_number=f"97{uid[:8]}",
            gst_number="27BBBBB0000B1Z5",
            rate_type="Item Rate",
            total_work_assigned=Decimal("20000.00"),
            payment_given=Decimal("0.00"),
        )
        db.add_all([contractor_a, contractor_b])
        await db.flush()

        # 6. ContractorProject mappings
        cp_a = ContractorProject(contractor_id=contractor_a.id, project_id=proj_a.id)
        cp_b = ContractorProject(contractor_id=contractor_b.id, project_id=proj_b.id)
        db.add_all([cp_a, cp_b])
        await db.flush()

        # 7. Custom roles in Company A
        role_name_mgr = f"ContractorManager_{uid}"
        role_name_auditor = f"ContractorAuditor_{uid}"
        role_name_dyn = f"DynamicRole_{uid}"
        role_name_noperm = f"UnprivRole_{uid}"

        role_mgr_a = Role(company_id=comp_a.id, name=role_name_mgr, display_name="Contractor Manager", is_system=False)
        role_auditor_a = Role(company_id=comp_a.id, name=role_name_auditor, display_name="Contractor Auditor", is_system=False)
        role_dyn_a = Role(company_id=comp_a.id, name=role_name_dyn, display_name="Dynamic Role", is_system=False)
        role_noperm_a = Role(company_id=comp_a.id, name=role_name_noperm, display_name="Unprivileged", is_system=False)
        db.add_all([role_mgr_a, role_auditor_a, role_dyn_a, role_noperm_a])
        await db.flush()

        # 8. Users in Company A
        user_noperm = User(
            email=f"d_noperm_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Unprivileged User D",
            company_id=comp_a.id,
            role=role_name_noperm,
            is_active=True,
            is_super_admin=False,
        )
        user_contractor_view = User(
            email=f"d_auditor_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Contractor Auditor D",
            company_id=comp_a.id,
            role=role_name_auditor,
            is_active=True,
            is_super_admin=False,
        )
        user_contractor_mgr = User(
            email=f"d_mgr_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Contractor Manager D",
            company_id=comp_a.id,
            role=role_name_mgr,
            is_active=True,
            is_super_admin=False,
        )
        user_contractor_dyn = User(
            email=f"d_dyn_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Dynamic User D",
            company_id=comp_a.id,
            role=role_name_dyn,
            is_active=True,
            is_super_admin=False,
        )
        user_admin_a = User(
            email=f"d_admin_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Company A Admin D",
            company_id=comp_a.id,
            role="Admin",
            is_active=True,
            is_super_admin=False,
        )
        user_comp_b = User(
            email=f"d_compb_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Comp B User D",
            company_id=comp_b.id,
            role=role_name_mgr,
            is_active=True,
            is_super_admin=False,
        )
        user_super = User(
            email=f"d_super_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Super Admin D",
            company_id=None,
            role="Admin",
            is_active=True,
            is_super_admin=True,
        )
        user_legacy = User(
            email=f"d_legacy_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="Legacy Project Manager D",
            company_id=comp_a.id,
            role="Project Manager",
            is_active=True,
            is_super_admin=False,
        )

        all_users = [
            user_noperm, user_contractor_view, user_contractor_mgr,
            user_contractor_dyn, user_admin_a, user_comp_b, user_super, user_legacy,
        ]
        db.add_all(all_users)
        await db.flush()

        # 9. Project members (assign users to Project A)
        pm_auditor = ProjectMember(project_id=proj_a.id, user_id=user_contractor_view.id)
        pm_mgr = ProjectMember(project_id=proj_a.id, user_id=user_contractor_mgr.id)
        pm_dyn = ProjectMember(project_id=proj_a.id, user_id=user_contractor_dyn.id)
        pm_noperm = ProjectMember(project_id=proj_a.id, user_id=user_noperm.id)
        pm_legacy = ProjectMember(project_id=proj_a.id, user_id=user_legacy.id)
        db.add_all([pm_auditor, pm_mgr, pm_dyn, pm_noperm, pm_legacy])
        await db.flush()

        # 10. Fetch permissions
        p_view = await db.scalar(select(Permission).where(Permission.code == "contractors.view"))
        p_create = await db.scalar(select(Permission).where(Permission.code == "contractors.create"))
        p_edit = await db.scalar(select(Permission).where(Permission.code == "contractors.edit"))
        p_delete = await db.scalar(select(Permission).where(Permission.code == "contractors.delete"))
        p_assign = await db.scalar(select(Permission).where(Permission.code == "contractors.assign"))

        # 11. Assign permissions to role_auditor_a (view only)
        db.add(RolePermission(role=role_name_auditor, role_id=role_auditor_a.id, permission_id=p_view.id))

        # 12. Assign all permissions to role_mgr_a
        for p in [p_view, p_create, p_edit, p_delete, p_assign]:
            db.add(RolePermission(role=role_name_mgr, role_id=role_mgr_a.id, permission_id=p.id))

        await db.commit()

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "contractor_a": contractor_a,
            "contractor_b": contractor_b,
            "user_noperm": user_noperm,
            "user_contractor_view": user_contractor_view,
            "user_contractor_mgr": user_contractor_mgr,
            "user_contractor_dyn": user_contractor_dyn,
            "user_admin_a": user_admin_a,
            "user_comp_b": user_comp_b,
            "user_super": user_super,
            "user_legacy": user_legacy,
            "role_mgr_a": role_mgr_a,
            "role_auditor_a": role_auditor_a,
            "role_dyn_a": role_dyn_a,
            "role_noperm_a": role_noperm_a,
            "role_name_mgr": role_name_mgr,
            "role_name_auditor": role_name_auditor,
            "role_name_dyn": role_name_dyn,
            "role_name_noperm": role_name_noperm,
            "perms": {
                "contractors.view": p_view,
                "contractors.create": p_create,
                "contractors.edit": p_edit,
                "contractors.delete": p_delete,
                "contractors.assign": p_assign,
            },
        }

    try:
        yield data
    finally:
        async with AsyncSessionLocal() as db:
            all_user_ids = [u.id for u in all_users]
            roles_to_del = [role_name_mgr, role_name_auditor, role_name_dyn, role_name_noperm]
            await db.execute(delete(RolePermission).where(RolePermission.role.in_(roles_to_del)))
            await db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(all_user_ids)))
            await db.execute(delete(Role).where(Role.id.in_([role_mgr_a.id, role_auditor_a.id, role_dyn_a.id, role_noperm_a.id])))
            await db.execute(delete(Expense).where(Expense.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(ContractorProject).where(ContractorProject.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(Contractor).where(Contractor.id.in_([contractor_a.id, contractor_b.id])))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await db.execute(delete(UserSettings).where(UserSettings.user_id.in_(all_user_ids)))
            await db.execute(delete(User).where(User.id.in_(all_user_ids)))
            await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()
        await async_engine.dispose()


def get_auth_headers(user: User):
    token = create_access_token(data={"sub": str(user.id), "role": user.role, "company_id": user.company_id})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. All Contractor Endpoints Require Authentication (401)
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_unauthenticated_requests_fail():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("POST", "/api/v1/contractors", {}),
            ("GET", "/api/v1/contractors/pending-report", None),
            ("GET", "/api/v1/contractors", None),
            ("GET", "/api/v1/contractors/1", None),
            ("PUT", "/api/v1/contractors/1", {}),
            ("DELETE", "/api/v1/contractors/1", None),
            ("POST", "/api/v1/contractors/1/assign-project/1", None),
            ("GET", "/api/v1/contractors/1/payments", None),
            ("POST", "/api/v1/contractors/1/pay", {}),
            ("GET", "/api/v1/contractors/1/projects", None),
            ("GET", "/api/v1/contractors/1/bills", None),
            ("GET", "/api/v1/contractors/1/performance", None),
            ("GET", "/api/v1/contractors/1/ledger", None),
            ("GET", "/api/v1/contractors/1/work-summary", None),
            ("GET", "/api/v1/contractors/1/dashboard", None),
        ]
        for method, path, body in endpoints:
            if method == "GET":
                res = await ac.get(path)
            elif method == "POST":
                res = await ac.post(path, json=body or {})
            elif method == "PUT":
                res = await ac.put(path, json=body or {})
            elif method == "DELETE":
                res = await ac.delete(path)
            assert res.status_code == 401, f"{method} {path} should return 401 unauthenticated"


# ==============================================================================
# 2. User Without Required DB Permission Receives 403
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_missing_permission_forbidden():
    async with setup_batch_d_data() as data:
        headers = get_auth_headers(data["user_noperm"])
        cid = data["contractor_a"].id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_list = await ac.get("/api/v1/contractors", headers=headers)
            assert res_list.status_code == 403

            res_get = await ac.get(f"/api/v1/contractors/{cid}", headers=headers)
            assert res_get.status_code == 403

            res_post = await ac.post(
                "/api/v1/contractors",
                headers=headers,
                json={"name": "Test", "work_type": "Civil", "contact_number": "9999999999", "rate_type": "Daily"},
            )
            assert res_post.status_code == 403


# ==============================================================================
# 3. Custom Role + contractors.view -> 200 Where Resource Scope Permits
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_custom_role_view_authorized():
    async with setup_batch_d_data() as data:
        headers = get_auth_headers(data["user_contractor_view"])
        cid = data["contractor_a"].id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_list = await ac.get("/api/v1/contractors", headers=headers)
            assert res_list.status_code == 200

            res_get = await ac.get(f"/api/v1/contractors/{cid}", headers=headers)
            assert res_get.status_code == 200


# ==============================================================================
# 4. Custom Role Runtime Lifecycle: no perm -> 403, grant -> 200, revoke -> 403, regrant -> 200
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_custom_role_runtime_lifecycle():
    async with setup_batch_d_data() as data:
        user = data["user_contractor_dyn"]
        headers = get_auth_headers(user)
        role_name = data["role_name_dyn"]
        role_id = data["role_dyn_a"].id
        p_view = data["perms"]["contractors.view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Initially no permission -> 403
            res_init = await ac.get("/api/v1/contractors", headers=headers)
            assert res_init.status_code == 403

            # 2. Grant permission in DB without restart -> 200
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                await db.commit()

            res_grant = await ac.get("/api/v1/contractors", headers=headers)
            assert res_grant.status_code == 200

            # 3. Revoke permission in DB without restart -> 403
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == role_name,
                    RolePermission.permission_id == p_view.id,
                ))
                await db.commit()

            res_revoke = await ac.get("/api/v1/contractors", headers=headers)
            assert res_revoke.status_code == 403

            # 4. Regrant permission in DB without restart -> 200
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                await db.commit()

            res_regrant = await ac.get("/api/v1/contractors", headers=headers)
            assert res_regrant.status_code == 200


# ==============================================================================
# 5-8. Custom Role Write Permissions (create, edit, delete, assign)
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_custom_role_write_actions():
    async with setup_batch_d_data() as data:
        headers_mgr = get_auth_headers(data["user_contractor_mgr"])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 5. contractors.create
            contact = f"98{uuid.uuid4().int % 100000000:08d}"
            res_create = await ac.post(
                "/api/v1/contractors",
                headers=headers_mgr,
                json={
                    "name": f"New Contractor {uuid.uuid4().hex[:4]}",
                    "work_type": "Masonry",
                    "contact_number": contact,
                    "rate_type": "Item Rate",
                },
            )
            assert res_create.status_code == 200
            new_cid = res_create.json()["id"]

            # 6. contractors.edit
            res_edit = await ac.put(
                f"/api/v1/contractors/{new_cid}",
                headers=headers_mgr,
                json={"work_type": "Plumbing"},
            )
            assert res_edit.status_code == 200
            assert res_edit.json()["work_type"] == "Plumbing"

            # 7. contractors.assign
            res_assign = await ac.post(
                f"/api/v1/contractors/{new_cid}/assign-project/{data['proj_a'].id}",
                headers=headers_mgr,
            )
            assert res_assign.status_code == 200

            # 8. contractors.delete
            res_delete = await ac.delete(f"/api/v1/contractors/{new_cid}", headers=headers_mgr)
            assert res_delete.status_code == 200


# ==============================================================================
# 9-10. User Permission Overrides (Positive & Negative)
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_user_permission_overrides():
    async with setup_batch_d_data() as data:
        user_noperm = data["user_noperm"]
        user_mgr = data["user_contractor_mgr"]
        p_view = data["perms"]["contractors.view"]
        headers_noperm = get_auth_headers(user_noperm)
        headers_mgr = get_auth_headers(user_mgr)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 9. Positive override grants unprivileged user access
            assert (await ac.get("/api/v1/contractors", headers=headers_noperm)).status_code == 403

            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user_noperm.id, permission_id=p_view.id, is_granted=True))
                await db.commit()

            assert (await ac.get("/api/v1/contractors", headers=headers_noperm)).status_code == 200

            # 10. Negative override revokes access from role-granted user
            assert (await ac.get("/api/v1/contractors", headers=headers_mgr)).status_code == 200

            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user_mgr.id, permission_id=p_view.id, is_granted=False))
                await db.commit()

            assert (await ac.get("/api/v1/contractors", headers=headers_mgr)).status_code == 403


# ==============================================================================
# 11-12. Wildcard Permission & Negative Override
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_wildcard_and_negative_override():
    async with setup_batch_d_data() as data:
        user = data["user_contractor_dyn"]
        role_name = data["role_name_dyn"]
        role_id = data["role_dyn_a"].id
        p_view = data["perms"]["contractors.view"]
        headers = get_auth_headers(user)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Assign all module permissions to role
            async with AsyncSessionLocal() as db:
                all_contractor_perms = (await db.scalars(select(Permission).where(Permission.module == "contractors"))).all()
                for p in all_contractor_perms:
                    db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p.id))
                await db.commit()

            # 11. Full wildcard access -> 200
            assert (await ac.get("/api/v1/contractors", headers=headers)).status_code == 200

            # 12. Negative override against wildcard -> 403
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user.id, permission_id=p_view.id, is_granted=False))
                await db.commit()

            assert (await ac.get("/api/v1/contractors", headers=headers)).status_code == 403


# ==============================================================================
# 13. P0 Fix #1: Company A Tenant Admin List Does NOT See Company B Contractors
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_list_tenant_isolation_admin():
    async with setup_batch_d_data() as data:
        headers_admin_a = get_auth_headers(data["user_admin_a"])
        contractor_b = data["contractor_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/contractors", headers=headers_admin_a)
            assert res.status_code == 200
            contractor_ids = [c["id"] for c in res.json()]
            assert data["contractor_a"].id in contractor_ids
            # P0 Fix verification: Company B contractor MUST NOT appear for Company A Admin!
            assert contractor_b.id not in contractor_ids


# ==============================================================================
# 14-17. IDOR Isolation: Company A Cannot GET, UPDATE, DELETE, or PAY Company B Contractor
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_cross_tenant_crud_blocked():
    async with setup_batch_d_data() as data:
        headers_mgr_a = get_auth_headers(data["user_contractor_mgr"])
        cid_b = data["contractor_b"].id
        proj_a_id = data["proj_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 14. GET Company B contractor -> 404
            res_get = await ac.get(f"/api/v1/contractors/{cid_b}", headers=headers_mgr_a)
            assert res_get.status_code in [403, 404]

            # 15. UPDATE Company B contractor -> 404
            res_put = await ac.put(f"/api/v1/contractors/{cid_b}", headers=headers_mgr_a, json={"work_type": "Hacked"})
            assert res_put.status_code == 404

            # 16. DELETE Company B contractor -> 404
            res_del = await ac.delete(f"/api/v1/contractors/{cid_b}", headers=headers_mgr_a)
            assert res_del.status_code == 404

            # 17. PAY Company B contractor -> 403/404
            res_pay = await ac.post(
                f"/api/v1/contractors/{cid_b}/pay?project_id={proj_a_id}&amount=500.00",
                headers=headers_mgr_a,
            )
            assert res_pay.status_code in [403, 404]


# ==============================================================================
# 18-19. P0 Fix #2: Cross-Tenant Contractor Project Assignment Blocked
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_cross_tenant_assignment_blocked():
    async with setup_batch_d_data() as data:
        headers_mgr_a = get_auth_headers(data["user_contractor_mgr"])
        cid_a = data["contractor_a"].id
        cid_b = data["contractor_b"].id
        proj_a = data["proj_a"].id
        proj_b = data["proj_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 18. Cannot assign Company B contractor to Company A project -> 404
            res_b_to_a = await ac.post(
                f"/api/v1/contractors/{cid_b}/assign-project/{proj_a}",
                headers=headers_mgr_a,
            )
            assert res_b_to_a.status_code == 404

            # 19. Cannot assign Company A contractor to Company B project -> 403/404
            res_a_to_b = await ac.post(
                f"/api/v1/contractors/{cid_a}/assign-project/{proj_b}",
                headers=headers_mgr_a,
            )
            assert res_a_to_b.status_code in [403, 404]


# ==============================================================================
# 20. Reports and Dashboard Endpoints Remain Tenant Isolated
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_reports_and_dashboard_tenant_isolation():
    async with setup_batch_d_data() as data:
        headers_view_a = get_auth_headers(data["user_contractor_view"])
        cid_a = data["contractor_a"].id
        cid_b = data["contractor_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Pending report only shows Company A contractors
            res_rep = await ac.get("/api/v1/contractors/pending-report", headers=headers_view_a)
            assert res_rep.status_code == 200

            # Sub-resource endpoints on Company A contractor -> 200
            for endpoint in ["payments", "projects", "bills", "performance", "ledger", "work-summary", "dashboard"]:
                res_sub_a = await ac.get(f"/api/v1/contractors/{cid_a}/{endpoint}", headers=headers_view_a)
                assert res_sub_a.status_code == 200, f"Expected 200 on /contractors/{cid_a}/{endpoint}"

                # Sub-resource endpoints on Company B contractor -> 403 or 404
                res_sub_b = await ac.get(f"/api/v1/contractors/{cid_b}/{endpoint}", headers=headers_view_a)
                assert res_sub_b.status_code in [403, 404], f"Expected 403/404 on /contractors/{cid_b}/{endpoint}"


# ==============================================================================
# 21. Tenant Admin Works Inside Own Company But Scoped
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_tenant_admin_scoped():
    async with setup_batch_d_data() as data:
        headers_admin_a = get_auth_headers(data["user_admin_a"])
        cid_a = data["contractor_a"].id
        cid_b = data["contractor_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Tenant Admin gets 200 on own company contractor
            res_a = await ac.get(f"/api/v1/contractors/{cid_a}", headers=headers_admin_a)
            assert res_a.status_code == 200

            # Tenant Admin cannot read other company contractor -> 403 or 404
            res_b = await ac.get(f"/api/v1/contractors/{cid_b}", headers=headers_admin_a)
            assert res_b.status_code in [403, 404]


# ==============================================================================
# 22. Super Admin Behavior Correct
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_super_admin_behavior():
    async with setup_batch_d_data() as data:
        headers_super = get_auth_headers(data["user_super"])
        cid_a = data["contractor_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_super = await ac.get(f"/api/v1/contractors/{cid_a}", headers=headers_super)
            assert res_super.status_code == 200


# ==============================================================================
# 23. Legacy Role Without DB Permission Receives 403
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_legacy_role_without_permission_denied():
    async with setup_batch_d_data() as data:
        # User has role="Project Manager" but ZERO records in role_permissions table
        headers_legacy = get_auth_headers(data["user_legacy"])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_list = await ac.get("/api/v1/contractors", headers=headers_legacy)
            assert res_list.status_code == 403

            res_rep = await ac.get("/api/v1/contractors/pending-report", headers=headers_legacy)
            assert res_rep.status_code == 403
