import uuid
import os
from pathlib import Path
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, ActivityLog, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.settings import CompanySettings
from app.models.document import Document
from app.models.notification import Notification
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import (
    DocumentStatus,
    ProjectStatus,
)

UPLOAD_DIR = Path("uploads/documents")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def setup_batch_m_data():
    """Seed test companies, projects, folders, documents, and users for Batch M."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchM_CompA_{uid}")
        comp_b = Company(name=f"BatchM_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company settings
        cs_a = CompanySettings(company_id=comp_a.id, company_name=f"Brand_Company_A_{uid}")
        cs_b = CompanySettings(company_id=comp_b.id, company_name=f"Brand_Company_B_{uid}")
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_m_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin M",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_ma_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin M",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_mb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin M",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"DocOfficer_{uid}"
        user_custom_a = User(
            email=f"custom_ma_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Doc Officer M",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        user_unassigned_a = User(
            email=f"unassigned_ma_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Unassigned Officer M",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        legacy_admin_no_perm = User(
            email=f"legacy_admin_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy Admin No Perm",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=f"EmptyAdmin_{uid}",
        )

        dummy_none_company_user = User(
            email=f"nonecomp_m_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="None Comp User M",
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
            user_unassigned_a,
            legacy_admin_no_perm,
            dummy_none_company_user,
        ])
        await db.flush()

        # 4. Owners & Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-MA-{uid}",
            owner_name="Owner MA",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-MB-{uid}",
            owner_name="Owner MB",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a1 = Project(
            business_id=f"PRJ-MA1-{uid}",
            project_name=f"Project MA1 {uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_a2 = Project(
            business_id=f"PRJ-MA2-{uid}",
            project_name=f"Project MA2 {uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b1 = Project(
            business_id=f"PRJ-MB1-{uid}",
            project_name=f"Project MB1 {uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a1, proj_a2, proj_b1])
        await db.flush()

        # 5. Physical test files
        real_file_a = UPLOAD_DIR / f"test_doc_a_{uid}.pdf"
        real_file_a.write_bytes(b"%PDF-1.4 Company A Sample File Content")

        real_file_b = UPLOAD_DIR / f"test_doc_b_{uid}.pdf"
        real_file_b.write_bytes(b"%PDF-1.4 Company B Sample File Content")

        # 6. Folders and Documents
        folder_a1 = Document(
            project_id=proj_a1.id,
            title="Folder A1 Root",
            is_folder=True,
            parent_id=None,
            status=DocumentStatus.APPROVED,
            uploaded_by_user_id=admin_a.id,
            is_deleted=False,
        )
        folder_a2_deleted = Document(
            project_id=proj_a1.id,
            title="Folder A2 Deleted",
            is_folder=True,
            parent_id=None,
            status=DocumentStatus.APPROVED,
            uploaded_by_user_id=admin_a.id,
            is_deleted=True,
        )
        folder_b1 = Document(
            project_id=proj_b1.id,
            title="Folder B1 Root",
            is_folder=True,
            parent_id=None,
            status=DocumentStatus.APPROVED,
            uploaded_by_user_id=admin_b.id,
            is_deleted=False,
        )
        db.add_all([folder_a1, folder_a2_deleted, folder_b1])
        await db.flush()

        doc_a1_pending = Document(
            project_id=proj_a1.id,
            title="Doc A1 Pending",
            document_type="Contract",
            file_url=str(real_file_a),
            file_size=len(real_file_a.read_bytes()),
            parent_id=folder_a1.id,
            status=DocumentStatus.PENDING,
            uploaded_by_user_id=admin_a.id,
            is_folder=False,
            is_deleted=False,
        )
        doc_a2_approved = Document(
            project_id=proj_a1.id,
            title="Doc A2 Approved Locked",
            document_type="Drawing",
            file_url=str(real_file_a),
            file_size=1024,
            parent_id=folder_a1.id,
            status=DocumentStatus.APPROVED,
            uploaded_by_user_id=admin_a.id,
            is_folder=False,
            is_deleted=False,
        )
        doc_a3_under_review = Document(
            project_id=proj_a1.id,
            title="Doc A3 Under Review Locked",
            document_type="Report",
            file_url=str(real_file_a),
            file_size=2048,
            parent_id=folder_a1.id,
            status=DocumentStatus.UNDER_REVIEW,
            uploaded_by_user_id=admin_a.id,
            is_folder=False,
            is_deleted=False,
        )
        doc_b1 = Document(
            project_id=proj_b1.id,
            title="Doc B1 Confidential",
            document_type="Audit",
            file_url=str(real_file_b),
            file_size=len(real_file_b.read_bytes()),
            parent_id=folder_b1.id,
            status=DocumentStatus.PENDING,
            uploaded_by_user_id=admin_b.id,
            is_folder=False,
            is_deleted=False,
        )
        db.add_all([doc_a1_pending, doc_a2_approved, doc_a3_under_review, doc_b1])
        await db.flush()

        # 7. RBAC Roles
        role_custom = Role(
            name=custom_role_name,
            display_name="Document Officer",
            company_id=comp_a.id,
        )
        role_legacy_empty = Role(
            name=f"EmptyAdmin_{uid}",
            display_name="Empty Admin",
            company_id=comp_a.id,
        )
        db.add_all([role_custom, role_legacy_empty])
        await db.flush()

        # 8. Ensure RBAC Permissions in catalog
        perm_codes = [
            "documents.view",
            "documents.create",
            "documents.upload",
            "documents.edit",
            "documents.delete",
            "documents.download",
            "documents.*",
        ]
        perms = {}
        for code in perm_codes:
            p = (await db.execute(select(Permission).where(Permission.code == code))).scalar_one_or_none()
            if not p:
                parts = code.split(".")
                p = Permission(module=parts[0], action=parts[1], code=code, description=f"{code} permission")
                db.add(p)
                await db.flush()
            perms[code] = p

        # Pre-assign standard permissions to Admin A and Admin B roles if needed
        # Check Admin role in company A
        role_admin_a = (await db.execute(select(Role).where(Role.name == "Admin", Role.company_id == comp_a.id))).scalar_one_or_none()
        if not role_admin_a:
            role_admin_a = Role(name=f"Admin_{uid}", display_name="Admin", company_id=comp_a.id)
            db.add(role_admin_a)
            await db.flush()
            admin_a.role = role_admin_a.name
            await db.flush()
        # Give Admin A full document permissions
        for code in ["documents.view", "documents.create", "documents.upload", "documents.edit", "documents.delete", "documents.download"]:
            db.add(RolePermission(role=role_admin_a.name, role_id=role_admin_a.id, permission_id=perms[code].id))

        role_admin_b = (await db.execute(select(Role).where(Role.name == "Admin", Role.company_id == comp_b.id))).scalar_one_or_none()
        if not role_admin_b:
            role_admin_b = Role(name=f"Admin_B_{uid}", display_name="Admin B", company_id=comp_b.id)
            db.add(role_admin_b)
            await db.flush()
            admin_b.role = role_admin_b.name
            await db.flush()
        for code in ["documents.view", "documents.create", "documents.upload", "documents.edit", "documents.delete", "documents.download"]:
            db.add(RolePermission(role=role_admin_b.name, role_id=role_admin_b.id, permission_id=perms[code].id))

        await db.commit()

        # Auth tokens
        tokens = {
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "user_custom_a": create_access_token({"sub": str(user_custom_a.id)}),
            "user_unassigned_a": create_access_token({"sub": str(user_unassigned_a.id)}),
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
            "user_unassigned_a": user_unassigned_a,
            "legacy_admin_no_perm": legacy_admin_no_perm,
            "dummy_none_company_user": dummy_none_company_user,
            "proj_a1": proj_a1,
            "proj_a2": proj_a2,
            "proj_b1": proj_b1,
            "folder_a1": folder_a1,
            "folder_a2_deleted": folder_a2_deleted,
            "folder_b1": folder_b1,
            "doc_a1_pending": doc_a1_pending,
            "doc_a2_approved": doc_a2_approved,
            "doc_a3_under_review": doc_a3_under_review,
            "doc_b1": doc_b1,
            "role_custom": role_custom,
            "role_legacy_empty": role_legacy_empty,
            "role_admin_a": role_admin_a,
            "role_admin_b": role_admin_b,
            "perms": perms,
            "tokens": tokens,
            "real_file_a": real_file_a,
            "real_file_b": real_file_b,
        }

        try:
            yield data
        finally:
            # Physical file cleanup
            if real_file_a.exists():
                try:
                    real_file_a.unlink()
                except Exception:
                    pass
            if real_file_b.exists():
                try:
                    real_file_b.unlink()
                except Exception:
                    pass

            async with AsyncSessionLocal() as clean_db:
                c_ids = [comp_a.id, comp_b.id]
                u_ids = [
                    super_admin.id, admin_a.id, admin_b.id,
                    user_custom_a.id, user_unassigned_a.id,
                    legacy_admin_no_perm.id, dummy_none_company_user.id
                ]
                r_ids = [role_custom.id, role_legacy_empty.id, role_admin_a.id, role_admin_b.id]

                await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(u_ids)))
                await clean_db.execute(delete(RolePermission).where(RolePermission.role_id.in_(r_ids)))
                await clean_db.execute(delete(Role).where(Role.id.in_(r_ids)))
                await clean_db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(u_ids)))
                await clean_db.execute(delete(Notification).where(Notification.user_id.in_(u_ids)))
                await clean_db.execute(delete(Document).where(Document.project_id.in_([proj_a1.id, proj_a2.id, proj_b1.id])))
                await clean_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a1.id, proj_a2.id, proj_b1.id])))
                await clean_db.execute(delete(Project).where(Project.company_id.in_(c_ids)))
                await clean_db.execute(delete(Owner).where(Owner.company_id.in_(c_ids)))
                await clean_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_(c_ids)))
                await clean_db.execute(delete(User).where(User.id.in_(u_ids)))
                await clean_db.execute(delete(Company).where(Company.id.in_(c_ids)))
                await clean_db.commit()


@pytest.mark.asyncio
async def test_batch_m_authentication_required():
    """All 8 Batch M routes return 401 Unauthorized without auth token."""
    async with setup_batch_m_data() as data:
        doc_id = data["doc_a1_pending"].id
        routes = [
            ("GET", "/api/v1/documents/stats"),
            ("POST", "/api/v1/documents"),
            ("POST", "/api/v1/documents/folders"),
            ("GET", "/api/v1/documents"),
            ("GET", f"/api/v1/documents/{doc_id}"),
            ("PUT", f"/api/v1/documents/{doc_id}"),
            ("DELETE", f"/api/v1/documents/{doc_id}"),
            ("GET", f"/api/v1/documents/{doc_id}/download"),
        ]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for method, url in routes:
                if method == "GET":
                    res = await ac.get(url)
                elif method == "POST":
                    res = await ac.post(url, json={})
                elif method == "PUT":
                    res = await ac.put(url, json={})
                elif method == "DELETE":
                    res = await ac.delete(url)
                assert res.status_code == 401, f"Route {method} {url} expected 401, got {res.status_code}"


@pytest.mark.asyncio
async def test_batch_m_permission_denial():
    """Users without required permission receive 403 Forbidden across all 8 routes."""
    async with setup_batch_m_data() as data:
        token = data["tokens"]["user_custom_a"]  # Has custom role with 0 permissions
        headers = {"Authorization": f"Bearer {token}"}
        doc_id = data["doc_a1_pending"].id
        proj_id = data["proj_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. GET /stats -> documents.view
            res = await ac.get("/api/v1/documents/stats", headers=headers)
            assert res.status_code == 403

            # 2. POST / (upload) -> documents.upload
            res = await ac.post(
                "/api/v1/documents",
                headers=headers,
                data={"project_id": str(proj_id), "title": "Deny Upload"},
                files={"file": ("test.pdf", b"%PDF-1.4 test", "application/pdf")},
            )
            assert res.status_code == 403

            # 3. POST /folders -> documents.create
            res = await ac.post(
                "/api/v1/documents/folders",
                headers=headers,
                params={"project_id": proj_id, "title": "Deny Folder"},
            )
            assert res.status_code == 403

            # 4. GET / (list) -> documents.view
            res = await ac.get("/api/v1/documents", headers=headers)
            assert res.status_code == 403

            # 5. GET /{id} -> documents.view
            res = await ac.get(f"/api/v1/documents/{doc_id}", headers=headers)
            assert res.status_code == 403

            # 6. PUT /{id} -> documents.edit
            res = await ac.put(
                f"/api/v1/documents/{doc_id}",
                headers=headers,
                data={"title": "Updated Title"},
            )
            assert res.status_code == 403

            # 7. DELETE /{id} -> documents.delete
            res = await ac.delete(f"/api/v1/documents/{doc_id}", headers=headers)
            assert res.status_code == 403

            # 8. GET /{id}/download -> documents.download
            res = await ac.get(f"/api/v1/documents/{doc_id}/download", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_batch_m_dynamic_db_role_permission_lifecycle():
    """Verify DB-driven RBAC: 403 -> Grant DB perm -> 200/204 -> Revoke DB perm -> 403."""
    async with setup_batch_m_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_id = data["role_custom"].id
        perms = data["perms"]
        doc_id = data["doc_a1_pending"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially 403 on documents.view
            res = await ac.get("/api/v1/documents", headers=headers)
            assert res.status_code == 403

            # Dynamically grant documents.view
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["role_custom"].name, role_id=role_id, permission_id=perms["documents.view"].id))
                await db.commit()

            # Now 200 on list and get
            res = await ac.get("/api/v1/documents", headers=headers)
            assert res.status_code == 200
            res_detail = await ac.get(f"/api/v1/documents/{doc_id}", headers=headers)
            assert res_detail.status_code == 200

            # But still 403 on documents.delete
            res_del = await ac.delete(f"/api/v1/documents/{doc_id}", headers=headers)
            assert res_del.status_code == 403

            # Dynamically revoke documents.view
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role_id,
                        RolePermission.permission_id == perms["documents.view"].id,
                    )
                )
                await db.commit()

            # Now 403 again
            res_after = await ac.get("/api/v1/documents", headers=headers)
            assert res_after.status_code == 403


@pytest.mark.asyncio
async def test_batch_m_user_permission_overrides():
    """Verify user overrides: positive override grants access; negative override denies access."""
    async with setup_batch_m_data() as data:
        user = data["user_custom_a"]
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        perms = data["perms"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # User has no role permission for documents.view -> 403
            res = await ac.get("/api/v1/documents/stats", headers=headers)
            assert res.status_code == 403

            # 1. POSITIVE OVERRIDE: grant documents.view directly to user
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user.id, permission_id=perms["documents.view"].id, is_granted=True))
                await db.commit()

            res_granted = await ac.get("/api/v1/documents/stats", headers=headers)
            assert res_granted.status_code == 200

            # 2. NEGATIVE OVERRIDE: give role permission, but explicitly deny user
            async with AsyncSessionLocal() as db:
                # Add role permission
                db.add(RolePermission(role=data["role_custom"].name, role_id=data["role_custom"].id, permission_id=perms["documents.view"].id))
                # Update user override to is_granted=False
                await db.execute(
                    delete(UserPermissionOverride).where(
                        UserPermissionOverride.user_id == user.id,
                        UserPermissionOverride.permission_id == perms["documents.view"].id,
                    )
                )
                db.add(UserPermissionOverride(user_id=user.id, permission_id=perms["documents.view"].id, is_granted=False))
                await db.commit()

            # Role has permission, but user override denies -> 403
            res_denied = await ac.get("/api/v1/documents/stats", headers=headers)
            assert res_denied.status_code == 403


@pytest.mark.asyncio
async def test_batch_m_wildcard_permission():
    """documents.* grants full access across all 8 Document Management endpoints."""
    async with setup_batch_m_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_id = data["role_custom"].id
        perms = data["perms"]
        doc_id = data["doc_a1_pending"].id
        proj_id = data["proj_a1"].id

        # Grant wildcard documents.* to custom role
        async with AsyncSessionLocal() as db:
            db.add(RolePermission(role=data["role_custom"].name, role_id=role_id, permission_id=perms["documents.*"].id))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Stats
            res = await ac.get("/api/v1/documents/stats", headers=headers)
            assert res.status_code == 200

            # 2. List
            res = await ac.get("/api/v1/documents", headers=headers)
            assert res.status_code == 200

            # 3. Get Detail
            res = await ac.get(f"/api/v1/documents/{doc_id}", headers=headers)
            assert res.status_code == 200

            # 4. Create Folder
            res = await ac.post(
                "/api/v1/documents/folders",
                headers=headers,
                params={"project_id": proj_id, "title": "Wildcard Folder"},
            )
            assert res.status_code == 200
            new_folder_id = res.json()["id"]

            # 5. Upload Document
            res = await ac.post(
                "/api/v1/documents",
                headers=headers,
                data={"project_id": str(proj_id), "title": "Wildcard Doc", "parent_id": str(new_folder_id)},
                files={"file": ("wildcard.pdf", b"%PDF-1.4 Wildcard Content", "application/pdf")},
            )
            assert res.status_code == 200
            new_doc_id = res.json()["id"]

            # 6. Update Document
            res = await ac.put(
                f"/api/v1/documents/{new_doc_id}",
                headers=headers,
                data={"title": "Updated Wildcard Doc"},
            )
            assert res.status_code == 200
            assert res.json()["title"] == "Updated Wildcard Doc"

            # 7. Download Document
            res = await ac.get(f"/api/v1/documents/{new_doc_id}/download", headers=headers)
            assert res.status_code == 200

            # 8. Delete Document
            res = await ac.delete(f"/api/v1/documents/{new_doc_id}", headers=headers)
            assert res.status_code == 204


@pytest.mark.asyncio
async def test_batch_m_immunity_to_legacy_role_names():
    """A user with role='Admin' or 'Project Manager' but 0 DB permissions is denied (403)."""
    async with setup_batch_m_data() as data:
        token = data["tokens"]["legacy_admin_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}
        doc_id = data["doc_a1_pending"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_stats = await ac.get("/api/v1/documents/stats", headers=headers)
            assert res_stats.status_code == 403

            res_list = await ac.get("/api/v1/documents", headers=headers)
            assert res_list.status_code == 403

            res_get = await ac.get(f"/api/v1/documents/{doc_id}", headers=headers)
            assert res_get.status_code == 403

            res_delete = await ac.delete(f"/api/v1/documents/{doc_id}", headers=headers)
            assert res_delete.status_code == 403


@pytest.mark.asyncio
async def test_batch_m_cross_tenant_idor_isolation():
    """Tenant A Admin cannot access, download, update, or delete Tenant B Document (masked 404)."""
    async with setup_batch_m_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        doc_b_id = data["doc_b1"].id
        proj_b_id = data["proj_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Detail: Tenant A querying Tenant B doc -> 404
            res_get = await ac.get(f"/api/v1/documents/{doc_b_id}", headers=headers_a)
            assert res_get.status_code == 404

            # Download: Tenant A downloading Tenant B doc -> 404
            res_down = await ac.get(f"/api/v1/documents/{doc_b_id}/download", headers=headers_a)
            assert res_down.status_code == 404

            # Update: Tenant A updating Tenant B doc -> 404
            res_put = await ac.put(f"/api/v1/documents/{doc_b_id}", headers=headers_a, data={"title": "Hacked Title"})
            assert res_put.status_code == 404

            # Delete: Tenant A deleting Tenant B doc -> 404
            res_del = await ac.delete(f"/api/v1/documents/{doc_b_id}", headers=headers_a)
            assert res_del.status_code == 404

            # Upload into foreign project -> 404
            res_upload = await ac.post(
                "/api/v1/documents",
                headers=headers_a,
                data={"project_id": str(proj_b_id), "title": "Cross Tenant Upload"},
                files={"file": ("test.pdf", b"%PDF-1.4 Cross Tenant", "application/pdf")},
            )
            assert res_upload.status_code == 404

            # List: Tenant A listing docs -> only sees Company A documents
            res_list = await ac.get("/api/v1/documents", headers=headers_a)
            assert res_list.status_code == 200
            items = res_list.json()["items"]
            doc_ids = [d["id"] for d in items]
            assert doc_b_id not in doc_ids


@pytest.mark.asyncio
async def test_batch_m_parent_id_security():
    """parent_id validation: must exist, is_deleted==False, is_folder==True, project_id match (all return 404)."""
    async with setup_batch_m_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        proj_a1 = data["proj_a1"].id
        proj_a2 = data["proj_a2"].id
        folder_a1 = data["folder_a1"].id
        folder_a2_deleted = data["folder_a2_deleted"].id
        folder_b1 = data["folder_b1"].id
        doc_a1_file = data["doc_a1_pending"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Non-existent parent_id -> 404
            res = await ac.post(
                "/api/v1/documents/folders",
                headers=headers_a,
                params={"project_id": proj_a1, "title": "SubFolder", "parent_id": 999999},
            )
            assert res.status_code == 404

            # 2. Parent is deleted (is_deleted == True) -> 404
            res = await ac.post(
                "/api/v1/documents/folders",
                headers=headers_a,
                params={"project_id": proj_a1, "title": "SubFolder", "parent_id": folder_a2_deleted},
            )
            assert res.status_code == 404

            # 3. Parent is not a folder (is_folder == False) -> 404
            res = await ac.post(
                "/api/v1/documents/folders",
                headers=headers_a,
                params={"project_id": proj_a1, "title": "SubFolder", "parent_id": doc_a1_file},
            )
            assert res.status_code == 404

            # 4. Parent belongs to a different project in same company -> 404
            res = await ac.post(
                "/api/v1/documents/folders",
                headers=headers_a,
                params={"project_id": proj_a2, "title": "SubFolder", "parent_id": folder_a1},
            )
            assert res.status_code == 404

            # 5. Parent belongs to a foreign company (Cross-tenant parent injection) -> 404
            res = await ac.post(
                "/api/v1/documents/folders",
                headers=headers_a,
                params={"project_id": proj_a1, "title": "SubFolder", "parent_id": folder_b1},
            )
            assert res.status_code == 404

            # 6. Same checks for POST /documents (file upload)
            res_up = await ac.post(
                "/api/v1/documents",
                headers=headers_a,
                data={"project_id": str(proj_a1), "title": "SubDoc", "parent_id": str(folder_b1)},
                files={"file": ("sub.pdf", b"%PDF-1.4 Sub Doc", "application/pdf")},
            )
            assert res_up.status_code == 404

            # 7. Valid parent_id -> 200
            res_valid = await ac.post(
                "/api/v1/documents/folders",
                headers=headers_a,
                params={"project_id": proj_a1, "title": "Valid SubFolder", "parent_id": folder_a1},
            )
            assert res_valid.status_code == 200
            assert res_valid.json()["parent_id"] == folder_a1


@pytest.mark.asyncio
async def test_batch_m_super_admin_scoping():
    """Super Admin accesses documents across all tenants with platform-wide scoping."""
    async with setup_batch_m_data() as data:
        token_sa = data["tokens"]["super_admin"]
        headers_sa = {"Authorization": f"Bearer {token_sa}"}
        doc_a_id = data["doc_a1_pending"].id
        doc_b_id = data["doc_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Super Admin can view Company A doc
            res_a = await ac.get(f"/api/v1/documents/{doc_a_id}", headers=headers_sa)
            assert res_a.status_code == 200

            # Super Admin can view Company B doc
            res_b = await ac.get(f"/api/v1/documents/{doc_b_id}", headers=headers_sa)
            assert res_b.status_code == 200

            # Super Admin can list documents across projects of different companies
            res_list_a = await ac.get(f"/api/v1/documents?project_id={data['proj_a1'].id}", headers=headers_sa)
            assert res_list_a.status_code == 200
            assert doc_a_id in [d["id"] for d in res_list_a.json()["items"]]

            res_list_b = await ac.get(f"/api/v1/documents?project_id={data['proj_b1'].id}", headers=headers_sa)
            assert res_list_b.status_code == 200
            assert doc_b_id in [d["id"] for d in res_list_b.json()["items"]]

            # Super Admin stats aggregation across all companies
            res_stats = await ac.get("/api/v1/documents/stats", headers=headers_sa)
            assert res_stats.status_code == 200
            assert res_stats.json()["total_documents"] >= 2

            # Super Admin can download both
            res_down_a = await ac.get(f"/api/v1/documents/{doc_a_id}/download", headers=headers_sa)
            assert res_down_a.status_code == 200
            res_down_b = await ac.get(f"/api/v1/documents/{doc_b_id}/download", headers=headers_sa)
            assert res_down_b.status_code == 200


@pytest.mark.asyncio
async def test_batch_m_business_status_guards():
    """UNDER_REVIEW and APPROVED documents cannot be edited or deleted (422/400)."""
    async with setup_batch_m_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        doc_approved = data["doc_a2_approved"].id
        doc_review = data["doc_a3_under_review"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Try to edit APPROVED document
            res = await ac.put(f"/api/v1/documents/{doc_approved}", headers=headers_a, data={"title": "New Title"})
            assert res.status_code in [400, 422]

            # Try to delete APPROVED document
            res = await ac.delete(f"/api/v1/documents/{doc_approved}", headers=headers_a)
            assert res.status_code in [400, 422]

            # Try to edit UNDER_REVIEW document
            res = await ac.put(f"/api/v1/documents/{doc_review}", headers=headers_a, data={"title": "New Title"})
            assert res.status_code in [400, 422]

            # Try to delete UNDER_REVIEW document
            res = await ac.delete(f"/api/v1/documents/{doc_review}", headers=headers_a)
            assert res.status_code in [400, 422]


@pytest.mark.asyncio
async def test_batch_m_safe_physical_file_cleanup_and_path_traversal():
    """Physical file deletion is safe against path traversal and missing files."""
    async with setup_batch_m_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        proj_id = data["proj_a1"].id
        admin_id = data["admin_a"].id

        # 1. Document with missing physical file on disk -> deletion still succeeds in DB (204)
        missing_doc = Document(
            project_id=proj_id,
            title="Doc With Missing File",
            document_type="Other",
            file_url=str(UPLOAD_DIR / "non_existent_file_xyz123.pdf"),
            file_size=500,
            status=DocumentStatus.PENDING,
            uploaded_by_user_id=admin_id,
            is_folder=False,
            is_deleted=False,
        )
        # 2. Document with path traversal file_url -> should NOT delete outside file
        outside_file = Path("uploads/documents/../test_outside_canary.txt")
        outside_file.write_text("Canary secret content")

        traversal_doc = Document(
            project_id=proj_id,
            title="Doc With Traversal URL",
            document_type="Other",
            file_url=str(outside_file),
            file_size=500,
            status=DocumentStatus.PENDING,
            uploaded_by_user_id=admin_id,
            is_folder=False,
            is_deleted=False,
        )

        async with AsyncSessionLocal() as db:
            db.add_all([missing_doc, traversal_doc])
            await db.commit()
            await db.refresh(missing_doc)
            await db.refresh(traversal_doc)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Delete missing file document: succeeds with 204
            res1 = await ac.delete(f"/api/v1/documents/{missing_doc.id}", headers=headers_a)
            assert res1.status_code == 204

            # Delete traversal document: succeeds with 204 without deleting canary outside file
            res2 = await ac.delete(f"/api/v1/documents/{traversal_doc.id}", headers=headers_a)
            assert res2.status_code == 204
            assert outside_file.exists(), "Path traversal vulnerability: outside file was deleted!"

            # Clean up canary
            if outside_file.exists():
                outside_file.unlink()


@pytest.mark.asyncio
async def test_batch_m_unassigned_and_none_company_user_isolation():
    """Users with company_id=None (non-super-admin) receive 403 / 404 isolation."""
    async with setup_batch_m_data() as data:
        token_none = data["tokens"]["dummy_none_company_user"]
        headers_none = {"Authorization": f"Bearer {token_none}"}
        doc_id = data["doc_a1_pending"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_stats = await ac.get("/api/v1/documents/stats", headers=headers_none)
            assert res_stats.status_code == 403

            res_get = await ac.get(f"/api/v1/documents/{doc_id}", headers=headers_none)
            assert res_get.status_code == 403


@pytest.mark.asyncio
async def test_batch_m_recursive_folder_cleanup_nested_hierarchy():
    """Verify recursive physical file cleanup across arbitrary nesting depth:
    Folder A -> Folder B -> Folder C -> nested_file.pdf
    Deleting Folder A must physically delete nested_file.pdf, cascade DB deletion,
    and not touch files outside UPLOAD_DIR.
    """
    async with setup_batch_m_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        proj_id = data["proj_a1"].id
        admin_id = data["admin_a"].id
        uid = data["uid"]

        # Create physical test file inside UPLOAD_DIR
        nested_file_path = UPLOAD_DIR / f"nested_doc_{uid}.pdf"
        nested_file_path.write_bytes(b"%PDF-1.4 Deeply Nested Document Content")

        # Create canary file outside UPLOAD_DIR to verify it is NEVER deleted
        canary_outside_file = Path("uploads/documents/../test_canary_recursive.txt")
        canary_outside_file.write_text("Canary recursive secret content")

        async with AsyncSessionLocal() as db:
            # Level 1: Folder A
            folder_a = Document(
                project_id=proj_id,
                title=f"Folder A {uid}",
                is_folder=True,
                parent_id=None,
                status=DocumentStatus.PENDING,
                uploaded_by_user_id=admin_id,
                is_deleted=False,
            )
            db.add(folder_a)
            await db.flush()

            # Level 2: Folder B
            folder_b = Document(
                project_id=proj_id,
                title=f"Folder B {uid}",
                is_folder=True,
                parent_id=folder_a.id,
                status=DocumentStatus.PENDING,
                uploaded_by_user_id=admin_id,
                is_deleted=False,
            )
            db.add(folder_b)
            await db.flush()

            # Level 3: Folder C
            folder_c = Document(
                project_id=proj_id,
                title=f"Folder C {uid}",
                is_folder=True,
                parent_id=folder_b.id,
                status=DocumentStatus.PENDING,
                uploaded_by_user_id=admin_id,
                is_deleted=False,
            )
            db.add(folder_c)
            await db.flush()

            # Level 4: Document in Folder C with physical file
            nested_doc = Document(
                project_id=proj_id,
                title=f"Nested Doc {uid}",
                document_type="Specification",
                file_url=str(nested_file_path),
                file_size=len(nested_file_path.read_bytes()),
                parent_id=folder_c.id,
                status=DocumentStatus.PENDING,
                uploaded_by_user_id=admin_id,
                is_folder=False,
                is_deleted=False,
            )
            db.add(nested_doc)
            await db.commit()
            await db.refresh(folder_a)
            await db.refresh(folder_b)
            await db.refresh(folder_c)
            await db.refresh(nested_doc)

            folder_a_id = folder_a.id
            folder_b_id = folder_b.id
            folder_c_id = folder_c.id
            nested_doc_id = nested_doc.id

        assert nested_file_path.exists()
        assert canary_outside_file.exists()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Delete Folder A via DELETE /documents/{folder_a_id}
            res = await ac.delete(f"/api/v1/documents/{folder_a_id}", headers=headers_a)
            assert res.status_code == 204

            # Verify physical nested file is deleted
            assert not nested_file_path.exists(), "Nested physical file was not deleted upon root folder deletion!"

            # Verify canary outside file remains untouched
            assert canary_outside_file.exists(), "Outside file was improperly touched or deleted!"

            # Verify DB records for Folder A, Folder B, Folder C, and nested_doc are all deleted or not found
            res_a = await ac.get(f"/api/v1/documents/{folder_a_id}", headers=headers_a)
            assert res_a.status_code == 404

            res_b = await ac.get(f"/api/v1/documents/{folder_b_id}", headers=headers_a)
            assert res_b.status_code == 404

            res_c = await ac.get(f"/api/v1/documents/{folder_c_id}", headers=headers_a)
            assert res_c.status_code == 404

            res_doc = await ac.get(f"/api/v1/documents/{nested_doc_id}", headers=headers_a)
            assert res_doc.status_code == 404

        # Cleanup canary and file if any remains
        if canary_outside_file.exists():
            canary_outside_file.unlink()
        if nested_file_path.exists():
            nested_file_path.unlink()
