import os
import uuid
from datetime import datetime
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, ActivityLog
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.agreement import Agreement
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import ProjectStatus

UPLOAD_DIR = "uploads/agreements"


@asynccontextmanager
async def setup_batch_q_data():
    """Seed test companies, owners, projects, agreements, users, roles, and RBAC permissions for Batch Q."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    created_disk_files = []

    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchQ_CompA_{uid}")
        comp_b = Company(name=f"BatchQ_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Owners
        owner_a1 = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-QA1-{uid}",
            owner_name=f"Owner QA1 {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera1_{uid}@test.com",
        )
        owner_a2 = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-QA2-{uid}",
            owner_name=f"Owner QA2 {uid}",
            mobile=f"93{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera2_{uid}@test.com",
        )
        owner_b1 = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-QB1-{uid}",
            owner_name=f"Owner QB1 {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerb1_{uid}@test.com",
        )
        db.add_all([owner_a1, owner_a2, owner_b1])
        await db.flush()

        # 3. Projects
        proj_a1 = Project(
            business_id=f"PRJ-QA1-{uid}",
            project_name=f"Project QA1 {uid}",
            company_id=comp_a.id,
            owner_id=owner_a1.id,
            status=ProjectStatus.ONGOING,
        )
        proj_a2 = Project(
            business_id=f"PRJ-QA2-{uid}",
            project_name=f"Project QA2 {uid}",
            company_id=comp_a.id,
            owner_id=owner_a2.id,  # Linked to owner_a2, not owner_a1
            status=ProjectStatus.ONGOING,
        )
        proj_b1 = Project(
            business_id=f"PRJ-QB1-{uid}",
            project_name=f"Project QB1 {uid}",
            company_id=comp_b.id,
            owner_id=owner_b1.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a1, proj_a2, proj_b1])
        await db.flush()

        # 4. Dummy files on disk for agreements
        file_a1_name = f"AGR-A1-{uid}.pdf"
        file_a1_path = os.path.join(UPLOAD_DIR, file_a1_name)
        file_a1_content = b"%PDF-1.4 Company A agreement test content"
        with open(file_a1_path, "wb") as f:
            f.write(file_a1_content)
        created_disk_files.append(file_a1_path)

        file_b1_name = f"AGR-B1-{uid}.pdf"
        file_b1_path = os.path.join(UPLOAD_DIR, file_b1_name)
        file_b1_content = b"%PDF-1.4 Company B agreement test content"
        with open(file_b1_path, "wb") as f:
            f.write(file_b1_content)
        created_disk_files.append(file_b1_path)

        # 5. Agreements
        aggr_a1 = Agreement(
            document_id=f"AGR-QA1-{uid[:4].upper()}",
            owner_id=owner_a1.id,
            project_id=proj_a1.id,
            type="Land Lease",
            file_url=f"/uploads/agreements/{file_a1_name}",
            status="Active",
        )
        aggr_b1 = Agreement(
            document_id=f"AGR-QB1-{uid[:4].upper()}",
            owner_id=owner_b1.id,
            project_id=proj_b1.id,
            type="Construction Contract",
            file_url=f"/uploads/agreements/{file_b1_name}",
            status="Active",
        )
        db.add_all([aggr_a1, aggr_b1])
        await db.flush()

        # 6. Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_q_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin Q",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_qa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin Q",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_qb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin Q",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"AgreementManager_{uid}"
        user_custom_a = User(
            email=f"custom_qa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Agreement Manager Q",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        legacy_admin_no_perm = User(
            email=f"legacy_admin_q_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy Admin No Perm Q",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=f"EmptyAdminQ_{uid}",
        )

        dummy_none_company_user = User(
            email=f"nonecomp_q_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="No Company User Q",
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

        # 7. Roles & Permissions
        role_custom_a = Role(
            company_id=comp_a.id,
            name=custom_role_name,
            display_name="Agreement Manager Role",
            is_system=False,
        )
        role_legacy = Role(
            company_id=comp_a.id,
            name=f"EmptyAdminQ_{uid}",
            display_name="Legacy Admin Empty Role",
            is_system=False,
        )
        db.add_all([role_custom_a, role_legacy])
        await db.flush()

        # Fetch canonical DB permissions
        res_perms = await db.scalars(
            select(Permission).where(Permission.module == "agreements")
        )
        perms_map = {p.action: p for p in res_perms.all()}

        # Fetch or create wildcard permission
        wildcard_perm = await db.scalar(
            select(Permission).where(Permission.code == "agreements.*")
        )
        if not wildcard_perm:
            wildcard_perm = Permission(
                module="agreements",
                action="*",
                code="agreements.*",
                description="agreements.* permission",
            )
            db.add(wildcard_perm)
            await db.flush()

        await db.commit()

        # Access tokens
        sa_token = create_access_token({"sub": str(super_admin.id), "company_id": None})
        admin_a_token = create_access_token({"sub": str(admin_a.id), "company_id": comp_a.id})
        admin_b_token = create_access_token({"sub": str(admin_b.id), "company_id": comp_b.id})
        custom_a_token = create_access_token({"sub": str(user_custom_a.id), "company_id": comp_a.id})
        legacy_token = create_access_token({"sub": str(legacy_admin_no_perm.id), "company_id": comp_a.id})
        none_comp_token = create_access_token({"sub": str(dummy_none_company_user.id), "company_id": None})

        data = {
            "uid": uid,
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "owner_a1_id": owner_a1.id,
            "owner_a2_id": owner_a2.id,
            "owner_b1_id": owner_b1.id,
            "proj_a1_id": proj_a1.id,
            "proj_a2_id": proj_a2.id,
            "proj_b1_id": proj_b1.id,
            "aggr_a1_id": aggr_a1.id,
            "aggr_b1_id": aggr_b1.id,
            "file_a1_content": file_a1_content,
            "file_b1_content": file_b1_content,
            "super_admin_id": super_admin.id,
            "admin_a_id": admin_a.id,
            "admin_b_id": admin_b.id,
            "user_custom_a_id": user_custom_a.id,
            "legacy_admin_id": legacy_admin_no_perm.id,
            "none_comp_user_id": dummy_none_company_user.id,
            "custom_role_name": custom_role_name,
            "role_custom_a_id": role_custom_a.id,
            "role_legacy_id": role_legacy.id,
            "perms_map": perms_map,
            "wildcard_perm": wildcard_perm,
            "sa_token": sa_token,
            "admin_a_token": admin_a_token,
            "admin_b_token": admin_b_token,
            "custom_a_token": custom_a_token,
            "legacy_token": legacy_token,
            "none_comp_token": none_comp_token,
            "created_disk_files": created_disk_files,
        }

    try:
        yield data
    finally:
        # Cleanup DB
        async with AsyncSessionLocal() as db:
            # Delete overrides & role_permissions
            await db.execute(
                delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id.in_([
                        data["super_admin_id"],
                        data["admin_a_id"],
                        data["admin_b_id"],
                        data["user_custom_a_id"],
                        data["legacy_admin_id"],
                        data["none_comp_user_id"],
                    ])
                )
            )
            await db.execute(
                delete(RolePermission).where(
                    RolePermission.role.in_([
                        data["custom_role_name"],
                        f"EmptyAdminQ_{data['uid']}",
                        "CompB_Viewer",
                    ])
                )
            )

            # Delete agreements
            aggr_res = await db.scalars(
                select(Agreement.id).where(
                    Agreement.owner_id.in_([
                        data["owner_a1_id"],
                        data["owner_a2_id"],
                        data["owner_b1_id"],
                    ])
                )
            )
            aggr_ids = aggr_res.all()
            if aggr_ids:
                # Also find any new files created during test
                extra_files_res = await db.scalars(
                    select(Agreement.file_url).where(Agreement.id.in_(aggr_ids))
                )
                for fu in extra_files_res.all():
                    if fu:
                        fp = os.path.join(UPLOAD_DIR, os.path.basename(fu))
                        if fp not in created_disk_files:
                            created_disk_files.append(fp)
                await db.execute(delete(Agreement).where(Agreement.id.in_(aggr_ids)))

            # Delete projects
            proj_res = await db.scalars(
                select(Project.id).where(
                    Project.company_id.in_([data["comp_a_id"], data["comp_b_id"]])
                )
            )
            proj_ids = proj_res.all()
            if proj_ids:
                await db.execute(delete(Project).where(Project.id.in_(proj_ids)))

            # Delete owners
            owner_res = await db.scalars(
                select(Owner.id).where(
                    Owner.company_id.in_([data["comp_a_id"], data["comp_b_id"]])
                )
            )
            owner_ids = owner_res.all()
            if owner_ids:
                await db.execute(delete(Owner).where(Owner.id.in_(owner_ids)))

            # Delete users & roles
            await db.execute(
                delete(ActivityLog).where(
                    ActivityLog.performed_by.in_([
                        data["super_admin_id"],
                        data["admin_a_id"],
                        data["admin_b_id"],
                        data["user_custom_a_id"],
                        data["legacy_admin_id"],
                        data["none_comp_user_id"],
                    ])
                )
            )
            await db.execute(
                delete(User).where(
                    User.id.in_([
                        data["super_admin_id"],
                        data["admin_a_id"],
                        data["admin_b_id"],
                        data["user_custom_a_id"],
                        data["legacy_admin_id"],
                        data["none_comp_user_id"],
                    ])
                )
            )
            await db.execute(
                delete(Role).where(
                    Role.company_id.in_([data["comp_a_id"], data["comp_b_id"]])
                )
            )

            # Delete companies
            await db.execute(
                delete(Company).where(
                    Company.id.in_([data["comp_a_id"], data["comp_b_id"]])
                )
            )
            await db.commit()

        # Cleanup physical files from disk
        for fp in created_disk_files:
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass


# ==============================================================================
# 1. 401 Unauthorized without Token Across All 4 Routes
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_authentication_required():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Route 1: GET /api/v1/agreements/
        r1 = await client.get("/api/v1/agreements/")
        assert r1.status_code == 401, f"Route 1 expected 401, got {r1.status_code}: {r1.text}"

        # Route 2: POST /api/v1/agreements/
        r2 = await client.post(
            "/api/v1/agreements/",
            data={"owner_id": 1, "type": "Land Lease"},
            files={"file": ("dummy.pdf", b"dummy", "application/pdf")},
        )
        assert r2.status_code == 401, f"Route 2 expected 401, got {r2.status_code}: {r2.text}"

        # Route 3: GET /api/v1/agreements/stats
        r3 = await client.get("/api/v1/agreements/stats")
        assert r3.status_code == 401, f"Route 3 expected 401, got {r3.status_code}: {r3.text}"

        # Route 4: GET /api/v1/agreements/{id}/download
        r4 = await client.get("/api/v1/agreements/1/download")
        assert r4.status_code == 401, f"Route 4 expected 401, got {r4.status_code}: {r4.text}"


# ==============================================================================
# 2. 403 Forbidden for Authenticated User with Zero DB Permissions
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_permission_denial():
    async with setup_batch_q_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # 1. GET /
            r1 = await client.get("/api/v1/agreements/", headers=headers)
            assert r1.status_code == 403, f"Expected 403, got {r1.status_code}: {r1.text}"

            # 2. POST /
            r2 = await client.post(
                "/api/v1/agreements/",
                headers=headers,
                data={"owner_id": d["owner_a1_id"], "type": "Land Lease"},
                files={"file": ("dummy.pdf", b"dummy", "application/pdf")},
            )
            assert r2.status_code == 403, f"Expected 403, got {r2.status_code}: {r2.text}"

            # 3. GET /stats
            r3 = await client.get("/api/v1/agreements/stats", headers=headers)
            assert r3.status_code == 403, f"Expected 403, got {r3.status_code}: {r3.text}"

            # 4. GET /{id}/download
            r4 = await client.get(f"/api/v1/agreements/{d['aggr_a1_id']}/download", headers=headers)
            assert r4.status_code == 403, f"Expected 403, got {r4.status_code}: {r4.text}"


# ==============================================================================
# 3. Dynamic DB Grant & Revoke Lifecycle
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_dynamic_grant_revoke_lifecycle():
    async with setup_batch_q_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # Baseline: 403
            r_init = await client.get("/api/v1/agreements/", headers=headers)
            assert r_init.status_code == 403

            # Grant agreements.view to custom role in DB
            perm_view = d["perms_map"]["view"]
            async with AsyncSessionLocal() as db:
                rp = RolePermission(role=d["custom_role_name"], role_id=d["role_custom_a_id"], permission_id=perm_view.id)
                db.add(rp)
                await db.commit()

            # Now success (200) without restart
            r_grant = await client.get("/api/v1/agreements/", headers=headers)
            assert r_grant.status_code == 200, f"Expected 200 after grant, got {r_grant.status_code}: {r_grant.text}"

            # Revoke agreements.view in DB
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == d["role_custom_a_id"],
                        RolePermission.permission_id == perm_view.id,
                    )
                )
                await db.commit()

            # Now 403 again
            r_revoke = await client.get("/api/v1/agreements/", headers=headers)
            assert r_revoke.status_code == 403, f"Expected 403 after revoke, got {r_revoke.status_code}"


# ==============================================================================
# 4. Positive User Permission Override
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_positive_user_override():
    async with setup_batch_q_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # Baseline: role has no permissions -> 403
            r_pre = await client.get("/api/v1/agreements/stats", headers=headers)
            assert r_pre.status_code == 403

            # Add positive override for user directly
            perm_view = d["perms_map"]["view"]
            async with AsyncSessionLocal() as db:
                upo = UserPermissionOverride(
                    user_id=d["user_custom_a_id"],
                    permission_id=perm_view.id,
                    is_granted=True,
                )
                db.add(upo)
                await db.commit()

            # Now succeeds via user-level override
            r_post = await client.get("/api/v1/agreements/stats", headers=headers)
            assert r_post.status_code == 200, f"Expected 200 via override, got {r_post.status_code}: {r_post.text}"


# ==============================================================================
# 5. Negative User Permission Override
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_negative_user_override():
    async with setup_batch_q_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # Grant agreements.download to role
            perm_dl = d["perms_map"]["download"]
            async with AsyncSessionLocal() as db:
                rp = RolePermission(role=d["custom_role_name"], role_id=d["role_custom_a_id"], permission_id=perm_dl.id)
                db.add(rp)
                await db.commit()

            # Verify download works
            r_role = await client.get(f"/api/v1/agreements/{d['aggr_a1_id']}/download", headers=headers)
            assert r_role.status_code == 200

            # Add negative override for this user (is_granted=False)
            async with AsyncSessionLocal() as db:
                upo = UserPermissionOverride(
                    user_id=d["user_custom_a_id"],
                    permission_id=perm_dl.id,
                    is_granted=False,
                )
                db.add(upo)
                await db.commit()

            # Now 403 even though role has permission
            r_denied = await client.get(f"/api/v1/agreements/{d['aggr_a1_id']}/download", headers=headers)
            assert r_denied.status_code == 403, f"Expected 403 via negative override, got {r_denied.status_code}"


# ==============================================================================
# 6. Wildcard Permission (agreements.*)
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_wildcard_permission():
    async with setup_batch_q_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # Grant agreements.* wildcard to role
            async with AsyncSessionLocal() as db:
                rp = RolePermission(role=d["custom_role_name"], role_id=d["role_custom_a_id"], permission_id=d["wildcard_perm"].id)
                db.add(rp)
                await db.commit()

            # Verify all endpoints authorized via wildcard
            # 1. GET /
            r1 = await client.get("/api/v1/agreements/", headers=headers)
            assert r1.status_code == 200, f"Wildcard GET / failed: {r1.status_code}"

            # 2. GET /stats
            r2 = await client.get("/api/v1/agreements/stats", headers=headers)
            assert r2.status_code == 200, f"Wildcard GET /stats failed: {r2.status_code}"

            # 3. GET /{id}/download
            r3 = await client.get(f"/api/v1/agreements/{d['aggr_a1_id']}/download", headers=headers)
            assert r3.status_code == 200, f"Wildcard GET /download failed: {r3.status_code}"

            # 4. POST /
            r4 = await client.post(
                "/api/v1/agreements/",
                headers=headers,
                data={
                    "owner_id": d["owner_a1_id"],
                    "project_id": d["proj_a1_id"],
                    "type": "Wildcard Test",
                },
                files={"file": ("wildcard.pdf", b"%PDF-1.4 Wildcard test", "application/pdf")},
            )
            assert r4.status_code == 200, f"Wildcard POST / failed: {r4.status_code}: {r4.text}"


# ==============================================================================
# 7. Legacy Role Immunity (Admin / PM with 0 DB permissions -> 403)
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_legacy_role_immunity():
    async with setup_batch_q_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['legacy_token']}"}

            r1 = await client.get("/api/v1/agreements/", headers=headers)
            assert r1.status_code == 403, "Legacy role without DB perms must get 403"

            r2 = await client.get("/api/v1/agreements/stats", headers=headers)
            assert r2.status_code == 403, "Legacy role without DB perms must get 403"

            r3 = await client.get(f"/api/v1/agreements/{d['aggr_a1_id']}/download", headers=headers)
            assert r3.status_code == 403, "Legacy role without DB perms must get 403"


# ==============================================================================
# 8. Own-Tenant CRUD Success & Response Formatting
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_own_tenant_operations():
    async with setup_batch_q_data() as d:
        # Grant view, create, download permissions to user_custom_a
        async with AsyncSessionLocal() as db:
            for act in ["view", "create", "download"]:
                db.add(
                    RolePermission(
                        role=d["custom_role_name"],
                        role_id=d["role_custom_a_id"],
                        permission_id=d["perms_map"][act].id,
                    )
                )
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # 1. GET /
            r_list = await client.get("/api/v1/agreements/", headers=headers)
            assert r_list.status_code == 200
            data_list = r_list.json()
            assert isinstance(data_list, list)
            assert len(data_list) >= 1
            item = data_list[0]
            assert "document_id" in item
            assert "owner_name" in item
            assert "project_name" in item
            assert item["owner_name"] is not None

            # 2. POST / (upload new agreement)
            upload_content = b"%PDF-1.4 Newly uploaded own tenant agreement"
            r_up = await client.post(
                "/api/v1/agreements/",
                headers=headers,
                data={
                    "owner_id": d["owner_a1_id"],
                    "project_id": d["proj_a1_id"],
                    "type": "Master Services Agreement",
                },
                files={"file": ("contract.pdf", upload_content, "application/pdf")},
            )
            assert r_up.status_code == 200, f"Upload failed: {r_up.text}"
            new_aggr = r_up.json()
            assert new_aggr["status"] == "Active"
            assert new_aggr["document_id"].startswith("AGR-")
            assert new_aggr["owner_id"] == d["owner_a1_id"]
            assert new_aggr["project_id"] == d["proj_a1_id"]
            new_aggr_id = new_aggr["id"]

            # 3. GET /{id}/download (download newly uploaded agreement)
            r_dl = await client.get(f"/api/v1/agreements/{new_aggr_id}/download", headers=headers)
            assert r_dl.status_code == 200
            assert r_dl.content == upload_content
            assert r_dl.headers["content-type"] == "application/octet-stream"

            # 4. GET /stats
            r_stats = await client.get("/api/v1/agreements/stats", headers=headers)
            assert r_stats.status_code == 200
            stats = r_stats.json()
            assert stats["total_agreements"] >= 2  # aggr_a1 + newly created
            assert stats["active_contracts"] >= 2
            assert "MB" in stats["storage_used"]
            assert "missing_docs" in stats


# ==============================================================================
# 9. Cross-Tenant IDOR Masked 404 (Download)
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_cross_tenant_download_idor_404():
    async with setup_batch_q_data() as d:
        # Grant download permission to Company A user
        async with AsyncSessionLocal() as db:
            db.add(
                RolePermission(
                    role=d["custom_role_name"],
                    role_id=d["role_custom_a_id"],
                    permission_id=d["perms_map"]["download"].id,
                )
            )
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # Attempt to download Company B's agreement
            r = await client.get(f"/api/v1/agreements/{d['aggr_b1_id']}/download", headers=headers)
            assert r.status_code == 404, f"Expected masked 404 on foreign download, got {r.status_code}: {r.text}"
            assert r.json().get("detail") == "Agreement not found"

            # Attempt to download non-existent agreement ID
            r_nonexist = await client.get("/api/v1/agreements/9999999/download", headers=headers)
            assert r_nonexist.status_code == 404
            assert r_nonexist.json().get("detail") == "Agreement not found"


# ==============================================================================
# 10. Cross-Tenant Listing Isolation (GET /)
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_cross_tenant_list_isolation():
    async with setup_batch_q_data() as d:
        # Grant view permission to Company A user
        async with AsyncSessionLocal() as db:
            db.add(
                RolePermission(
                    role=d["custom_role_name"],
                    role_id=d["role_custom_a_id"],
                    permission_id=d["perms_map"]["view"].id,
                )
            )
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # 1. Base list: only Comp A agreements
            r = await client.get("/api/v1/agreements/", headers=headers)
            assert r.status_code == 200
            items = r.json()
            returned_ids = [item["id"] for item in items]
            assert d["aggr_a1_id"] in returned_ids
            assert d["aggr_b1_id"] not in returned_ids, "Company B agreement leaked to Company A caller!"

            # 2. Filtering with foreign owner_id -> masked 404
            r_foreign_owner = await client.get(
                f"/api/v1/agreements/?owner_id={d['owner_b1_id']}",
                headers=headers,
            )
            assert r_foreign_owner.status_code == 404
            assert r_foreign_owner.json().get("detail") == "Owner not found"

            # 3. Filtering with foreign project_id -> masked 404
            r_foreign_proj = await client.get(
                f"/api/v1/agreements/?project_id={d['proj_b1_id']}",
                headers=headers,
            )
            assert r_foreign_proj.status_code == 404
            assert r_foreign_proj.json().get("detail") == "Project not found"

            # 4. Filtering with own owner_id -> 200 success
            r_own_owner = await client.get(
                f"/api/v1/agreements/?owner_id={d['owner_a1_id']}",
                headers=headers,
            )
            assert r_own_owner.status_code == 200
            assert len(r_own_owner.json()) >= 1


# ==============================================================================
# 11. Cross-Tenant Upload Resource Injection & Relational Integrity (POST /)
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_cross_tenant_upload_injection_404():
    async with setup_batch_q_data() as d:
        # Grant create permission to Company A user
        async with AsyncSessionLocal() as db:
            db.add(
                RolePermission(
                    role=d["custom_role_name"],
                    role_id=d["role_custom_a_id"],
                    permission_id=d["perms_map"]["create"].id,
                )
            )
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # 1. Foreign owner_id -> masked 404
            r_f_owner = await client.post(
                "/api/v1/agreements/",
                headers=headers,
                data={"owner_id": d["owner_b1_id"], "type": "Subcontract"},
                files={"file": ("test.pdf", b"content", "application/pdf")},
            )
            assert r_f_owner.status_code == 404
            assert r_f_owner.json().get("detail") == "Owner not found"

            # 2. Own owner_id but foreign project_id -> masked 404
            r_f_proj = await client.post(
                "/api/v1/agreements/",
                headers=headers,
                data={
                    "owner_id": d["owner_a1_id"],
                    "project_id": d["proj_b1_id"],
                    "type": "Subcontract",
                },
                files={"file": ("test.pdf", b"content", "application/pdf")},
            )
            assert r_f_proj.status_code == 404
            assert r_f_proj.json().get("detail") == "Project not found"

            # 3. Own owner_id (owner_a1) but mismatched project (proj_a2 belongs to owner_a2) -> masked 404
            r_mismatch = await client.post(
                "/api/v1/agreements/",
                headers=headers,
                data={
                    "owner_id": d["owner_a1_id"],
                    "project_id": d["proj_a2_id"],
                    "type": "Subcontract",
                },
                files={"file": ("test.pdf", b"content", "application/pdf")},
            )
            assert r_mismatch.status_code == 404
            assert r_mismatch.json().get("detail") == "Project not found"


# ==============================================================================
# 12. Agreement Stats Multi-Tenant Isolation (GET /stats)
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_stats_tenant_isolation():
    async with setup_batch_q_data() as d:
        # Grant view permission to Company A user and create token for Company B
        async with AsyncSessionLocal() as db:
            db.add(
                RolePermission(
                    role=d["custom_role_name"],
                    role_id=d["role_custom_a_id"],
                    permission_id=d["perms_map"]["view"].id,
                )
            )
            # Create role for Comp B and grant view
            role_b = Role(
                company_id=d["comp_b_id"],
                name="CompB_Viewer",
                display_name="Comp B Viewer",
                is_system=False,
            )
            db.add(role_b)
            await db.flush()
            db.add(
                RolePermission(
                    role=role_b.name,
                    role_id=role_b.id,
                    permission_id=d["perms_map"]["view"].id,
                )
            )
            # update admin_b role to CompB_Viewer
            user_b = await db.get(User, d["admin_b_id"])
            user_b.role = "CompB_Viewer"
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers_a = {"Authorization": f"Bearer {d['custom_a_token']}"}
            headers_b = {"Authorization": f"Bearer {d['admin_b_token']}"}

            # Comp A stats
            r_a = await client.get("/api/v1/agreements/stats", headers=headers_a)
            assert r_a.status_code == 200
            stats_a = r_a.json()
            assert stats_a["total_agreements"] == 1
            assert stats_a["active_contracts"] == 1
            # Comp A has 2 owners (owner_a1 with aggr, owner_a2 without aggr) -> missing_docs = 1
            assert stats_a["missing_docs"] == 1

            # Comp B stats
            r_b = await client.get("/api/v1/agreements/stats", headers=headers_b)
            assert r_b.status_code == 200
            stats_b = r_b.json()
            assert stats_b["total_agreements"] == 1
            assert stats_b["active_contracts"] == 1
            # Comp B has 1 owner (owner_b1 with aggr) -> missing_docs = 0
            assert stats_b["missing_docs"] == 0


# ==============================================================================
# 13. Super Admin Cross-Company Access
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_super_admin_cross_company_access():
    async with setup_batch_q_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sa_headers = {"Authorization": f"Bearer {d['sa_token']}"}

            # 1. SA can list all agreements globally (both Comp A and Comp B)
            r_list = await client.get("/api/v1/agreements/", headers=sa_headers)
            assert r_list.status_code == 200
            ids = [item["id"] for item in r_list.json()]
            assert d["aggr_a1_id"] in ids
            assert d["aggr_b1_id"] in ids

            # 2. SA can download agreements from Comp A and Comp B
            r_dl_a = await client.get(f"/api/v1/agreements/{d['aggr_a1_id']}/download", headers=sa_headers)
            assert r_dl_a.status_code == 200
            assert r_dl_a.content == d["file_a1_content"]

            r_dl_b = await client.get(f"/api/v1/agreements/{d['aggr_b1_id']}/download", headers=sa_headers)
            assert r_dl_b.status_code == 200
            assert r_dl_b.content == d["file_b1_content"]

            # 3. SA stats returns global totals
            r_stats = await client.get("/api/v1/agreements/stats", headers=sa_headers)
            assert r_stats.status_code == 200
            sa_stats = r_stats.json()
            assert sa_stats["total_agreements"] >= 2


# ==============================================================================
# 14. Non-SA with company_id=None Denied (403)
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_non_sa_company_id_none():
    async with setup_batch_q_data() as d:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['none_comp_token']}"}

            r1 = await client.get("/api/v1/agreements/", headers=headers)
            assert r1.status_code == 403, f"Expected 403, got {r1.status_code}: {r1.text}"
            assert "company" in r1.json().get("detail", "").lower()

            r2 = await client.post(
                "/api/v1/agreements/",
                headers=headers,
                data={"owner_id": d["owner_a1_id"], "type": "Land Lease"},
                files={"file": ("dummy.pdf", b"dummy", "application/pdf")},
            )
            assert r2.status_code == 403

            r3 = await client.get("/api/v1/agreements/stats", headers=headers)
            assert r3.status_code == 403

            r4 = await client.get(f"/api/v1/agreements/{d['aggr_a1_id']}/download", headers=headers)
            assert r4.status_code == 403


# ==============================================================================
# 15. File Safety, Missing Disk File, and Exception Hygiene
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_q_file_safety_and_exception_hygiene():
    async with setup_batch_q_data() as d:
        async with AsyncSessionLocal() as db:
            db.add(
                RolePermission(
                    role=d["custom_role_name"],
                    role_id=d["role_custom_a_id"],
                    permission_id=d["perms_map"]["download"].id,
                )
            )
            # Create an agreement whose file does not exist on disk
            aggr_missing = Agreement(
                document_id=f"AGR-MISS-{uuid.uuid4().hex[:4].upper()}",
                owner_id=d["owner_a1_id"],
                project_id=d["proj_a1_id"],
                type="Missing File Test",
                file_url="/uploads/agreements/non_existent_file_12345.pdf",
                status="Active",
            )
            db.add(aggr_missing)
            await db.commit()
            await db.refresh(aggr_missing)
            missing_aggr_id = aggr_missing.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {d['custom_a_token']}"}

            # Attempt to download agreement with missing file on disk
            r_miss = await client.get(f"/api/v1/agreements/{missing_aggr_id}/download", headers=headers)
            assert r_miss.status_code == 404
            assert r_miss.json().get("detail") == "Agreement file not found on disk"

            # Cleanup missing record
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Agreement).where(Agreement.id == missing_aggr_id))
                await db.commit()
