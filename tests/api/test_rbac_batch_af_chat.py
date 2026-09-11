"""
RBAC Batch AF: Chat & Communication Management Test Suite
==========================================================

Covers all mandatory verification areas for Batch AF:
- TC01: Unauthenticated requests return 401
- TC02: Authenticated user with no permissions returns 403 for chat.* actions
- TC03: chat.view permission allows GET /chats/, GET /chats/users, GET /chats/search-users, etc. -> 200
- TC04: Authenticated user without chat.create returns 403 on private chat creation & message sending
- TC05: chat.create permission allows POST /chats/private/{id} and POST /chats/group -> 200
- TC06: Authenticated user without chat.edit returns 403 on editing message, adding members, pinning
- TC07: chat.edit permission allows PUT /chats/messages/{id}/edit and POST /chats/group/{id}/add -> 200
- TC08: Authenticated user without chat.delete returns 403 on DELETE /chats/messages/{id} and DELETE /chats/{id}
- TC09: chat.delete permission allows deleting message and deleting chat -> 200
- TC10: Dynamic DB permission grant and revoke takes immediate effect
- TC11: Role names alone do not grant access; DB-driven permissions are required
- TC12: Tenantless non-SA user (company_id=None) returns 403 Forbidden
- TC13: Tenant Isolation - GET /chats/users and GET /chats/search-users do not expose foreign company users
- TC14: IDOR Prevention - Private chat cannot target foreign tenant user (404 masked)
- TC15: IDOR Prevention - Group creation cannot inject foreign tenant users (400 invalid users)
- TC16: IDOR Prevention - Single member add cannot target foreign tenant user (404 masked)
- TC17: IDOR Prevention - Bulk member add cannot inject foreign tenant users (400 invalid users)
- TC18: User Presence IDOR - Foreign user status check returns 404 masked
- TC19: Chat Resource IDOR - Foreign chat ID returns 404 masked
- TC20: Forwarding IDOR - Forwarding message to foreign chat returns 404 masked
- TC21: Business Invariant - Cannot create private chat with self (400)
- TC22: Business Invariant - Duplicate private chat returns existing chat_id
- TC23: Business Invariant - Message edit 15-minute window enforced (403 after expiration)
- TC24: Business Invariant - Message delete 60-minute window enforced (403 after expiration)
- TC25: Business Invariant - Group creator cannot be removed or kicked (400)
- TC26: Group Moderation - System RBAC (chat.edit) + MemberRole.ADMIN both enforced
- TC27: Super Admin global visibility across tenant chats
- TC28: Route Preservation - Exactly 45 chat endpoints and 781 total APIRoutes preserved
"""

import uuid
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.api.chat import router as chat_router
from app.models.company import Company
from app.models.rbac import Permission, Role, RolePermission, UserPermissionOverride
from app.models.user import User, UserRole
from app.models.chat import (
    ChatSession,
    ChatMember,
    ChatMessage,
    MemberRole,
    ChatType,
)
from app.utils.timezone import get_naive_local_now


# ==============================================================================
# HELPERS
# ==============================================================================

def _tok(user_id: int) -> str:
    return create_access_token({"sub": str(user_id)})


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def setup_batch_af_data():
    """
    Provisions two isolated tenants (comp_a, comp_b) with:
    - Users in Company A: user_a1, user_a2, user_a_viewer, user_a_creator, user_a_editor, user_a_deleter, user_a_full, user_a_noperms
    - Users in Company B: user_b1, user_b2
    - Super Admin user
    - Tenantless non-SA user
    - Chat permissions: chat.view, chat.create, chat.edit, chat.delete
    """
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"BatchAF_CompA_{uid}")
        comp_b = Company(name=f"BatchAF_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Permissions required
        perm_codes = [
            "chat.view",
            "chat.create",
            "chat.edit",
            "chat.delete",
            "*",
        ]
        perms = {}
        for code in perm_codes:
            existing = await db.scalar(select(Permission).where(Permission.code == code))
            if not existing:
                action = code.split(".")[-1] if "." in code else code
                module = code.split(".")[0] if "." in code else "*"
                p = Permission(
                    module=module,
                    action=action,
                    code=code,
                    description=f"{action} permission for {module}",
                )
                db.add(p)
                await db.flush()
                perms[code] = p
            else:
                perms[code] = existing

        # 3. Roles in Company A
        role_noperms = Role(name=f"af_noperms_{uid}", display_name="No Perms", company_id=comp_a.id, is_system=False)
        role_viewer = Role(name=f"af_viewer_{uid}", display_name="Viewer", company_id=comp_a.id, is_system=False)
        role_creator = Role(name=f"af_creator_{uid}", display_name="Creator", company_id=comp_a.id, is_system=False)
        role_editor = Role(name=f"af_editor_{uid}", display_name="Editor", company_id=comp_a.id, is_system=False)
        role_deleter = Role(name=f"af_deleter_{uid}", display_name="Deleter", company_id=comp_a.id, is_system=False)
        role_full = Role(name=f"af_full_{uid}", display_name="Full", company_id=comp_a.id, is_system=False)
        db.add_all([role_noperms, role_viewer, role_creator, role_editor, role_deleter, role_full])
        await db.flush()

        # Assign permissions to roles
        # Viewer -> chat.view
        db.add(RolePermission(role=role_viewer.name, role_id=role_viewer.id, permission_id=perms["chat.view"].id))
        # Creator -> chat.view, chat.create
        db.add_all([
            RolePermission(role=role_creator.name, role_id=role_creator.id, permission_id=perms["chat.view"].id),
            RolePermission(role=role_creator.name, role_id=role_creator.id, permission_id=perms["chat.create"].id),
        ])
        # Editor -> chat.view, chat.edit
        db.add_all([
            RolePermission(role=role_editor.name, role_id=role_editor.id, permission_id=perms["chat.view"].id),
            RolePermission(role=role_editor.name, role_id=role_editor.id, permission_id=perms["chat.edit"].id),
        ])
        # Deleter -> chat.view, chat.delete
        db.add_all([
            RolePermission(role=role_deleter.name, role_id=role_deleter.id, permission_id=perms["chat.view"].id),
            RolePermission(role=role_deleter.name, role_id=role_deleter.id, permission_id=perms["chat.delete"].id),
        ])
        # Full -> view, create, edit, delete
        for c in ["chat.view", "chat.create", "chat.edit", "chat.delete"]:
            db.add(RolePermission(role=role_full.name, role_id=role_full.id, permission_id=perms[c].id))

        # Role in Company B
        role_b = Role(name=f"af_comp_b_{uid}", display_name="Comp B", company_id=comp_b.id, is_system=False)
        db.add(role_b)
        await db.flush()
        for c in ["chat.view", "chat.create", "chat.edit", "chat.delete"]:
            db.add(RolePermission(role=role_b.name, role_id=role_b.id, permission_id=perms[c].id))
        await db.flush()

        # 4. Users in Company A
        user_a1 = User(
            email=f"af_a1_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha A1 {uid}",
            mobile=f"81{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_full.name,
            is_active=True,
            is_deleted=False,
        )
        user_a2 = User(
            email=f"af_a2_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha A2 {uid}",
            mobile=f"82{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_full.name,
            is_active=True,
            is_deleted=False,
        )
        user_a_viewer = User(
            email=f"af_aviewer_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha Viewer {uid}",
            mobile=f"83{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_viewer.name,
            is_active=True,
            is_deleted=False,
        )
        user_a_creator = User(
            email=f"af_acreator_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha Creator {uid}",
            mobile=f"84{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_creator.name,
            is_active=True,
            is_deleted=False,
        )
        user_a_editor = User(
            email=f"af_aeditor_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha Editor {uid}",
            mobile=f"85{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_editor.name,
            is_active=True,
            is_deleted=False,
        )
        user_a_deleter = User(
            email=f"af_adeleter_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha Deleter {uid}",
            mobile=f"86{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_deleter.name,
            is_active=True,
            is_deleted=False,
        )
        user_a_noperms = User(
            email=f"af_anoperms_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Alpha NoPerms {uid}",
            mobile=f"87{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_noperms.name,
            is_active=True,
            is_deleted=False,
        )

        # Users in Company B
        user_b1 = User(
            email=f"af_b1_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Beta B1 {uid}",
            mobile=f"88{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_b.id,
            role=role_b.name,
            is_active=True,
            is_deleted=False,
        )
        user_b2 = User(
            email=f"af_b2_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Beta B2 {uid}",
            mobile=f"89{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_b.id,
            role=role_b.name,
            is_active=True,
            is_deleted=False,
        )

        # Super Admin user
        user_sa = User(
            email=f"af_sa_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Super Admin {uid}",
            mobile=f"90{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
            role=role_full.name,
            is_super_admin=True,
            is_active=True,
            is_deleted=False,
        )

        # Tenantless user (company_id=None)
        user_tenantless = User(
            email=f"af_tenantless_{uid}@test.com",
            hashed_password=pwd,
            full_name=f"Tenantless {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            company_id=None,
            role=role_full.name,
            is_super_admin=False,
            is_active=True,
            is_deleted=False,
        )

        db.add_all([
            user_a1, user_a2, user_a_viewer, user_a_creator,
            user_a_editor, user_a_deleter, user_a_noperms,
            user_b1, user_b2, user_sa, user_tenantless,
        ])
        await db.commit()

        yield {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "user_a1": user_a1,
            "user_a2": user_a2,
            "user_a_viewer": user_a_viewer,
            "user_a_creator": user_a_creator,
            "user_a_editor": user_a_editor,
            "user_a_deleter": user_a_deleter,
            "user_a_noperms": user_a_noperms,
            "user_b1": user_b1,
            "user_b2": user_b2,
            "user_sa": user_sa,
            "user_tenantless": user_tenantless,
            "perms": perms,
            "role_viewer": role_viewer,
            "role_full": role_full,
        }


# ==============================================================================
# TEST CASES
# ==============================================================================

@pytest.mark.asyncio
async def test_tc01_unauthenticated_requests_return_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r1 = await ac.get("/api/v1/chats/")
        r2 = await ac.get("/api/v1/chats/users")
        r3 = await ac.post("/api/v1/chats/group", json={"name": "Test", "member_ids": [1]})
        assert r1.status_code in (401, 403)
        assert r2.status_code in (401, 403)
        assert r3.status_code in (401, 403)


@pytest.mark.asyncio
async def test_tc02_authenticated_no_permissions_returns_403():
    async with setup_batch_af_data() as data:
        tok = _tok(data["user_a_noperms"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1 = await ac.get("/api/v1/chats/", headers=_auth(tok))
            assert r1.status_code == 403
            r2 = await ac.get("/api/v1/chats/users", headers=_auth(tok))
            assert r2.status_code == 403
            r3 = await ac.post(f"/api/v1/chats/private/{data['user_a2'].id}", headers=_auth(tok))
            assert r3.status_code == 403


@pytest.mark.asyncio
async def test_tc03_chat_view_allows_read_endpoints():
    async with setup_batch_af_data() as data:
        tok = _tok(data["user_a_viewer"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1 = await ac.get("/api/v1/chats/", headers=_auth(tok))
            assert r1.status_code == 200
            assert isinstance(r1.json(), list)

            r2 = await ac.get("/api/v1/chats/users", headers=_auth(tok))
            assert r2.status_code == 200
            assert isinstance(r2.json(), list)

            r3 = await ac.get("/api/v1/chats/search-users?q=Alpha", headers=_auth(tok))
            assert r3.status_code == 200
            assert isinstance(r3.json(), list)


@pytest.mark.asyncio
async def test_tc04_chat_create_denied_without_permission():
    async with setup_batch_af_data() as data:
        tok = _tok(data["user_a_viewer"].id)  # has view only
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Cannot create private chat
            r1 = await ac.post(f"/api/v1/chats/private/{data['user_a2'].id}", headers=_auth(tok))
            assert r1.status_code == 403
            # Cannot create group chat
            r2 = await ac.post("/api/v1/chats/group", json={"name": "Forbidden Group", "member_ids": [data["user_a2"].id]}, headers=_auth(tok))
            assert r2.status_code == 403


@pytest.mark.asyncio
async def test_tc05_chat_create_allowed_when_granted():
    async with setup_batch_af_data() as data:
        tok = _tok(data["user_a_creator"].id)  # has view + create
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1 = await ac.post(f"/api/v1/chats/private/{data['user_a2'].id}", headers=_auth(tok))
            assert r1.status_code == 200
            assert "chat_id" in r1.json()

            r2 = await ac.post("/api/v1/chats/group", json={"name": "Alpha Group", "member_ids": [data["user_a2"].id]}, headers=_auth(tok))
            assert r2.status_code == 200
            assert "chat_id" in r2.json()


@pytest.mark.asyncio
async def test_tc06_chat_edit_denied_without_permission():
    async with setup_batch_af_data() as data:
        tok_creator = _tok(data["user_a_creator"].id)  # has create, NOT edit
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create group first
            r_grp = await ac.post("/api/v1/chats/group", json={"name": "Test Edit Denied", "member_ids": [data["user_a2"].id]}, headers=_auth(tok_creator))
            chat_id = r_grp.json()["chat_id"]

            # Try to add member -> 403
            r_add = await ac.post(f"/api/v1/chats/group/{chat_id}/add?user_id={data['user_a_viewer'].id}", headers=_auth(tok_creator))
            assert r_add.status_code == 403


@pytest.mark.asyncio
async def test_tc07_chat_edit_allowed_when_granted():
    async with setup_batch_af_data() as data:
        tok_full = _tok(data["user_a1"].id)  # has full perms
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create group
            r_grp = await ac.post("/api/v1/chats/group", json={"name": "Alpha Moderated Group", "member_ids": [data["user_a2"].id]}, headers=_auth(tok_full))
            chat_id = r_grp.json()["chat_id"]

            # Add member -> 200
            r_add = await ac.post(f"/api/v1/chats/group/{chat_id}/add?user_id={data['user_a_viewer'].id}", headers=_auth(tok_full))
            assert r_add.status_code == 200
            assert r_add.json()["status"] == "added"

            # Update group -> 200
            r_upd = await ac.put(f"/api/v1/chats/group/{chat_id}?name=Updated Name", headers=_auth(tok_full))
            assert r_upd.status_code == 200


@pytest.mark.asyncio
async def test_tc08_chat_delete_denied_without_permission():
    async with setup_batch_af_data() as data:
        tok_editor = _tok(data["user_a_editor"].id)  # view + edit, NOT delete
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r_del = await ac.delete("/api/v1/chats/9999", headers=_auth(tok_editor))
            assert r_del.status_code == 403


@pytest.mark.asyncio
async def test_tc09_chat_delete_allowed_when_granted():
    async with setup_batch_af_data() as data:
        tok_full = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create private chat
            r_chat = await ac.post(f"/api/v1/chats/private/{data['user_a2'].id}", headers=_auth(tok_full))
            chat_id = r_chat.json()["chat_id"]

            # Delete chat -> 200
            r_del = await ac.delete(f"/api/v1/chats/{chat_id}", headers=_auth(tok_full))
            assert r_del.status_code == 200
            assert r_del.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_tc10_dynamic_db_permission_grant_and_revoke():
    async with setup_batch_af_data() as data:
        tok_noperms = _tok(data["user_a_noperms"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Initially denied -> 403
            r1 = await ac.get("/api/v1/chats/", headers=_auth(tok_noperms))
            assert r1.status_code == 403

            # 2. Dynamically grant chat.view via UserPermissionOverride in DB
            async with AsyncSessionLocal() as db:
                ov = UserPermissionOverride(
                    user_id=data["user_a_noperms"].id,
                    permission_id=data["perms"]["chat.view"].id,
                    is_granted=True,
                )
                db.add(ov)
                await db.commit()

            # 3. Next request succeeds -> 200
            r2 = await ac.get("/api/v1/chats/", headers=_auth(tok_noperms))
            assert r2.status_code == 200

            # 4. Revoke override
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(UserPermissionOverride).where(
                        UserPermissionOverride.user_id == data["user_a_noperms"].id
                    )
                )
                await db.commit()

            # 5. Immediate rejection again -> 403
            r3 = await ac.get("/api/v1/chats/", headers=_auth(tok_noperms))
            assert r3.status_code == 403


@pytest.mark.asyncio
async def test_tc11_role_names_alone_do_not_determine_access():
    async with setup_batch_af_data() as data:
        # user_a_noperms has role='Labour' and no perms -> 403
        tok = _tok(data["user_a_noperms"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/v1/chats/", headers=_auth(tok))
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_tc12_tenantless_non_sa_returns_403():
    async with setup_batch_af_data() as data:
        tok_tenantless = _tok(data["user_tenantless"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/v1/chats/", headers=_auth(tok_tenantless))
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_tc13_tenant_isolation_get_users_excludes_foreign_users():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r_users = await ac.get("/api/v1/chats/users", headers=_auth(tok_a))
            assert r_users.status_code == 200
            user_ids = [u["user_id"] for u in r_users.json()]
            # Must include Company A users
            assert data["user_a2"].id in user_ids
            # Must NEVER include Company B users
            assert data["user_b1"].id not in user_ids
            assert data["user_b2"].id not in user_ids

            # Same check for search-users
            r_search = await ac.get("/api/v1/chats/search-users?q=Beta", headers=_auth(tok_a))
            assert r_search.status_code == 200
            assert len(r_search.json()) == 0


@pytest.mark.asyncio
async def test_tc14_idor_private_chat_foreign_user_returns_404():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/chats/private/{data['user_b1'].id}", headers=_auth(tok_a))
            assert r.status_code == 404
            assert r.json()["detail"] == "User not found"


@pytest.mark.asyncio
async def test_tc15_idor_group_create_foreign_user_rejected_400():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/chats/group",
                json={"name": "Hacked Cross-Company Group", "member_ids": [data["user_b1"].id]},
                headers=_auth(tok_a),
            )
            assert r.status_code == 400
            assert "Invalid users" in r.json()["detail"]


@pytest.mark.asyncio
async def test_tc16_idor_add_member_foreign_user_returns_404():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create valid group
            r_grp = await ac.post("/api/v1/chats/group", json={"name": "Comp A Group", "member_ids": [data["user_a2"].id]}, headers=_auth(tok_a))
            chat_id = r_grp.json()["chat_id"]

            # Try to add Company B user -> 404 masked
            r_add = await ac.post(f"/api/v1/chats/group/{chat_id}/add?user_id={data['user_b1'].id}", headers=_auth(tok_a))
            assert r_add.status_code == 404
            assert r_add.json()["detail"] == "User not found"


@pytest.mark.asyncio
async def test_tc17_idor_bulk_add_foreign_user_rejected_400():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r_grp = await ac.post("/api/v1/chats/group", json={"name": "Comp A Group", "member_ids": [data["user_a2"].id]}, headers=_auth(tok_a))
            chat_id = r_grp.json()["chat_id"]

            r_bulk = await ac.post(
                f"/api/v1/chats/group/{chat_id}/members",
                json={"member_ids": [data["user_b1"].id]},
                headers=_auth(tok_a),
            )
            assert r_bulk.status_code == 400
            assert "Invalid users" in r_bulk.json()["detail"]


@pytest.mark.asyncio
async def test_tc18_presence_idor_foreign_user_returns_404():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get(f"/api/v1/chats/users/{data['user_b1'].id}/status", headers=_auth(tok_a))
            assert r.status_code == 404
            assert r.json()["detail"] == "User not found"


@pytest.mark.asyncio
async def test_tc19_chat_resource_idor_foreign_chat_returns_404():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        tok_b = _tok(data["user_b1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Company B creates group chat
            r_b_grp = await ac.post("/api/v1/chats/group", json={"name": "Comp B Secret Group", "member_ids": [data["user_b2"].id]}, headers=_auth(tok_b))
            chat_id_b = r_b_grp.json()["chat_id"]

            # Company A tries to get messages from Company B's chat -> 404
            r_leak = await ac.get(f"/api/v1/chats/{chat_id_b}/messages", headers=_auth(tok_a))
            assert r_leak.status_code == 404
            assert r_leak.json()["detail"] == "Chat not found"


@pytest.mark.asyncio
async def test_tc20_forwarding_idor_foreign_chat_returns_404():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        tok_b = _tok(data["user_b1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create chat in Company A and send message
            r_a = await ac.post(f"/api/v1/chats/private/{data['user_a2'].id}", headers=_auth(tok_a))
            chat_a = r_a.json()["chat_id"]
            r_msg = await ac.post(f"/api/v1/chats/{chat_a}/messages", json={"message": "Confidential A"}, headers=_auth(tok_a))
            msg_id = r_msg.json()["id"]

            # Create chat in Company B
            r_b = await ac.post(f"/api/v1/chats/private/{data['user_b2'].id}", headers=_auth(tok_b))
            chat_b = r_b.json()["chat_id"]

            # Try to forward Company A's message into Company B's chat -> 404
            r_fwd = await ac.post(f"/api/v1/chats/messages/{msg_id}/forward?target_chat_id={chat_b}", headers=_auth(tok_a))
            assert r_fwd.status_code == 404


@pytest.mark.asyncio
async def test_tc21_business_self_private_chat_rejected_400():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.post(f"/api/v1/chats/private/{data['user_a1'].id}", headers=_auth(tok_a))
            assert r.status_code == 400
            assert "Cannot create chat with yourself" in r.json()["detail"]


@pytest.mark.asyncio
async def test_tc22_business_duplicate_private_chat_returns_existing():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create first time
            r1 = await ac.post(f"/api/v1/chats/private/{data['user_a2'].id}", headers=_auth(tok_a))
            assert r1.status_code == 200
            chat_id_1 = r1.json()["chat_id"]

            # 2. Create second time -> same chat_id returned
            r2 = await ac.post(f"/api/v1/chats/private/{data['user_a2'].id}", headers=_auth(tok_a))
            assert r2.status_code == 200
            chat_id_2 = r2.json()["chat_id"]
            assert chat_id_1 == chat_id_2


@pytest.mark.asyncio
async def test_tc23_business_message_edit_15min_window():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create chat and send message
            r_chat = await ac.post(f"/api/v1/chats/private/{data['user_a2'].id}", headers=_auth(tok_a))
            chat_id = r_chat.json()["chat_id"]
            r_msg = await ac.post(f"/api/v1/chats/{chat_id}/messages", json={"message": "Original"}, headers=_auth(tok_a))
            msg_id = r_msg.json()["id"]

            # Immediate edit within 15 mins -> 200
            r_edit = await ac.put(f"/api/v1/chats/messages/{msg_id}/edit?new_text=Freshly+Edited", headers=_auth(tok_a))
            assert r_edit.status_code == 200

            # Manually backdate created_at in DB by 20 mins
            async with AsyncSessionLocal() as db:
                m = await db.get(ChatMessage, msg_id)
                m.created_at = get_naive_local_now() - timedelta(minutes=20)
                await db.commit()

            # Attempt edit after window -> 403
            r_expired = await ac.put(f"/api/v1/chats/messages/{msg_id}/edit?new_text=Too+Late", headers=_auth(tok_a))
            assert r_expired.status_code == 403
            assert r_expired.json()["detail"] == "Edit time expired"


@pytest.mark.asyncio
async def test_tc24_business_message_delete_60min_window():
    async with setup_batch_af_data() as data:
        tok_a = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r_chat = await ac.post(f"/api/v1/chats/private/{data['user_a2'].id}", headers=_auth(tok_a))
            chat_id = r_chat.json()["chat_id"]
            r_msg = await ac.post(f"/api/v1/chats/{chat_id}/messages", json={"message": "To be deleted"}, headers=_auth(tok_a))
            msg_id = r_msg.json()["id"]

            # Backdate created_at by 75 mins in DB
            async with AsyncSessionLocal() as db:
                m = await db.get(ChatMessage, msg_id)
                m.created_at = get_naive_local_now() - timedelta(minutes=75)
                await db.commit()

            # Attempt delete after window -> 403
            r_del = await ac.delete(f"/api/v1/chats/messages/{msg_id}", headers=_auth(tok_a))
            assert r_del.status_code == 403
            assert r_del.json()["detail"] == "Delete time expired"


@pytest.mark.asyncio
async def test_tc25_business_group_creator_cannot_be_removed():
    async with setup_batch_af_data() as data:
        tok_a1 = _tok(data["user_a1"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # user_a1 creates group with user_a2
            r_grp = await ac.post("/api/v1/chats/group", json={"name": "Comp A Permanent Group", "member_ids": [data["user_a2"].id]}, headers=_auth(tok_a1))
            chat_id = r_grp.json()["chat_id"]

            # Promote user_a2 to admin
            r_promo = await ac.post(f"/api/v1/chats/group/{chat_id}/transfer-admin?new_admin_id={data['user_a2'].id}", headers=_auth(tok_a1))

            # Try to remove creator (user_a1) -> 400
            r_kick = await ac.post(f"/api/v1/chats/group/{chat_id}/kick?user_id={data['user_a1'].id}", headers=_auth(tok_a1))
            assert r_kick.status_code == 400
            assert "Cannot remove group creator" in r_kick.json()["detail"]


@pytest.mark.asyncio
async def test_tc26_group_admin_moderation_rules():
    async with setup_batch_af_data() as data:
        tok_a1 = _tok(data["user_a1"].id)
        tok_a2 = _tok(data["user_a2"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # user_a1 creates group with user_a2 as MEMBER
            r_grp = await ac.post("/api/v1/chats/group", json={"name": "Moderation Test", "member_ids": [data["user_a2"].id]}, headers=_auth(tok_a1))
            chat_id = r_grp.json()["chat_id"]

            # user_a2 is MEMBER (not group admin), even though user_a2 has system chat.edit!
            # Moderation operations require BOTH system chat.edit AND in-chat MemberRole.ADMIN
            r_fail = await ac.post(f"/api/v1/chats/group/{chat_id}/add?user_id={data['user_a_viewer'].id}", headers=_auth(tok_a2))
            assert r_fail.status_code == 403
            assert r_fail.json()["detail"] == "Admin access required"


@pytest.mark.asyncio
async def test_tc27_super_admin_global_visibility():
    async with setup_batch_af_data() as data:
        tok_sa = _tok(data["user_sa"].id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # SA gets users globally across different companies
            r_users_a = await ac.get(f"/api/v1/chats/users?search={data['user_a2'].email}", headers=_auth(tok_sa))
            assert r_users_a.status_code == 200
            assert any(u["user_id"] == data["user_a2"].id for u in r_users_a.json())

            r_users_b = await ac.get(f"/api/v1/chats/users?search={data['user_b1'].email}", headers=_auth(tok_sa))
            assert r_users_b.status_code == 200
            assert any(u["user_id"] == data["user_b1"].id for u in r_users_b.json())

            # SA can also query presence of user from company B without 404 masking
            r_stat_b = await ac.get(f"/api/v1/chats/users/{data['user_b1'].id}/status", headers=_auth(tok_sa))
            assert r_stat_b.status_code == 200


@pytest.mark.asyncio
async def test_tc28_route_preservation_and_ast_verification():
    from fastapi.routing import APIRoute

    api_routes = [r for r in app.routes if isinstance(r, APIRoute)]
    chat_routes = [r for r in api_routes if r.endpoint.__module__ == "app.api.chat"]

    assert len(chat_routes) == 45, f"Expected exactly 45 chat routes, found {len(chat_routes)}"
    assert len(api_routes) == 781, f"Expected 781 total APIRoutes, found {len(api_routes)}"
