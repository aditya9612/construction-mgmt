"""
Test Suite: BATCH AI — AI Predictions Security/RBAC & Tenant Isolation
======================================================================
Covers:
A. Authentication (unauthenticated -> 401)
B. Feature entitlement (no AI feature -> 403)
C. Permission gates (missing ai.view, ai.create, ai.edit, ai.delete -> 403)
D. Valid tenant access (feature + permissions -> 200/204)
E. Tenantless non-SA (company_id=None -> 403)
F. IDOR / Tenant security (foreign record GET/PUT/DELETE -> 404 masking)
G. Super Admin semantics (feature bypass, global listing, scoped listing, explicit company create, 404 on invalid company)
H. Input validation (unknown fields -> 422, oversized prompt -> 422, invalid update -> 422)
I. Cache isolation (tenant-scoped version key, Tenant A mutation does not bump Tenant B version)
J. Route preservation (5 AI routes, 5 unique method+path, 0 duplicates, 781 total routes)
K. Static security hygiene (0 require_roles, 0 admin_required, 0 UserRole authorization checks, 0 role allowlists)
"""

import inspect
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api import ai as ai_api_module
from app.cache.redis import bump_cache_version, get_cache_version
from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.ai_prediction import AIPrediction
from app.models.company import Company
from app.models.rbac import Permission, Role, RolePermission
from app.models.subscription import Plan, Subscription
from app.models.user import User


class InMemoryAsyncRedis:
    """In-memory redis mock for tracking cache keys and atomic version increments."""

    def __init__(self):
        self._store: Dict[str, Any] = {}

    async def get(self, key: str) -> Optional[bytes]:
        val = self._store.get(key)
        if val is None:
            return None
        if isinstance(val, bytes):
            return val
        return str(val).encode("utf-8")

    async def set(self, key: str, val: Any, ex: Optional[int] = None) -> None:
        self._store[key] = val

    async def incr(self, key: str) -> int:
        cur_raw = self._store.get(key)
        cur = int(cur_raw) if cur_raw is not None else 1
        new_val = cur + 1
        self._store[key] = str(new_val).encode("utf-8")
        return new_val


@asynccontextmanager
async def setup_ai_test_data():
    """Create companies, plans, subscriptions, roles, permissions, users, and predictions."""
    fake_redis = InMemoryAsyncRedis()
    app.state.redis = fake_redis

    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"AI_CompA_{uid}")
        comp_b = Company(name=f"AI_CompB_{uid}")
        comp_no_ai = Company(name=f"AI_CompNoAI_{uid}")
        db.add_all([comp_a, comp_b, comp_no_ai])
        await db.flush()

        # 2. Plans & Subscriptions
        plan_ai = Plan(name=f"AI_Plan_{uid}", code=f"ai_plan_{uid}", features={"ai_features": True})
        plan_no_ai = Plan(name=f"NoAI_Plan_{uid}", code=f"noai_plan_{uid}", features={"ai_features": False})
        db.add_all([plan_ai, plan_no_ai])
        await db.flush()

        sub_a = Subscription(company_id=comp_a.id, plan_id=plan_ai.id, status="active")
        sub_b = Subscription(company_id=comp_b.id, plan_id=plan_ai.id, status="active")
        sub_no_ai = Subscription(company_id=comp_no_ai.id, plan_id=plan_no_ai.id, status="active")
        db.add_all([sub_a, sub_b, sub_no_ai])
        await db.flush()

        # 3. Permissions
        ai_perm_codes = ["ai.view", "ai.create", "ai.edit", "ai.delete"]
        perms = (await db.execute(
            select(Permission).where(Permission.code.in_(ai_perm_codes))
        )).scalars().all()
        perm_map = {p.code: p for p in perms}

        # 4. Roles in Company A
        role_all = Role(company_id=comp_a.id, name=f"role_all_{uid}", display_name="All AI Perms", is_system=False)
        role_no_view = Role(company_id=comp_a.id, name=f"role_noview_{uid}", display_name="No View", is_system=False)
        role_no_create = Role(company_id=comp_a.id, name=f"role_nocreate_{uid}", display_name="No Create", is_system=False)
        role_no_edit = Role(company_id=comp_a.id, name=f"role_noedit_{uid}", display_name="No Edit", is_system=False)
        role_no_delete = Role(company_id=comp_a.id, name=f"role_nodelete_{uid}", display_name="No Delete", is_system=False)

        # Role in Company B
        role_b = Role(company_id=comp_b.id, name=f"role_b_{uid}", display_name="B All AI Perms", is_system=False)
        # Role in Company No AI
        role_noai = Role(company_id=comp_no_ai.id, name=f"role_noai_{uid}", display_name="No AI All Perms", is_system=False)

        db.add_all([role_all, role_no_view, role_no_create, role_no_edit, role_no_delete, role_b, role_noai])
        await db.flush()

        # Assign permissions
        for code in ["ai.view", "ai.create", "ai.edit", "ai.delete"]:
            db.add(RolePermission(role=role_all.name, role_id=role_all.id, permission_id=perm_map[code].id))
            db.add(RolePermission(role=role_b.name, role_id=role_b.id, permission_id=perm_map[code].id))
            db.add(RolePermission(role=role_noai.name, role_id=role_noai.id, permission_id=perm_map[code].id))

        for code in ["ai.create", "ai.edit", "ai.delete"]:
            db.add(RolePermission(role=role_no_view.name, role_id=role_no_view.id, permission_id=perm_map[code].id))
        for code in ["ai.view", "ai.edit", "ai.delete"]:
            db.add(RolePermission(role=role_no_create.name, role_id=role_no_create.id, permission_id=perm_map[code].id))
        for code in ["ai.view", "ai.create", "ai.delete"]:
            db.add(RolePermission(role=role_no_edit.name, role_id=role_no_edit.id, permission_id=perm_map[code].id))
        for code in ["ai.view", "ai.create", "ai.edit"]:
            db.add(RolePermission(role=role_no_delete.name, role_id=role_no_delete.id, permission_id=perm_map[code].id))

        await db.flush()

        # 5. Users
        def _make_user(email: str, cid: Optional[int], rname: str, is_sa: bool = False) -> User:
            return User(
                company_id=cid,
                role=rname,
                email=email,
                hashed_password=pwd_hash,
                full_name=email.split("@")[0],
                is_active=True,
                is_super_admin=is_sa,
            )

        u_all = _make_user(f"u_all_{uid}@test.com", comp_a.id, role_all.name)
        u_no_view = _make_user(f"u_noview_{uid}@test.com", comp_a.id, role_no_view.name)
        u_no_create = _make_user(f"u_nocreate_{uid}@test.com", comp_a.id, role_no_create.name)
        u_no_edit = _make_user(f"u_noedit_{uid}@test.com", comp_a.id, role_no_edit.name)
        u_no_delete = _make_user(f"u_nodelete_{uid}@test.com", comp_a.id, role_no_delete.name)
        u_b = _make_user(f"u_b_{uid}@test.com", comp_b.id, role_b.name)
        u_no_ai = _make_user(f"u_noai_{uid}@test.com", comp_no_ai.id, role_noai.name)
        u_tl = _make_user(f"u_tl_{uid}@test.com", None, role_all.name, is_sa=False)
        sa_no_comp = _make_user(f"sa_nocomp_{uid}@test.com", None, "SuperAdmin", is_sa=True)
        sa_with_comp = _make_user(f"sa_comp_{uid}@test.com", comp_a.id, "SuperAdmin", is_sa=True)

        all_users = [u_all, u_no_view, u_no_create, u_no_edit, u_no_delete, u_b, u_no_ai, u_tl, sa_no_comp, sa_with_comp]
        db.add_all(all_users)
        await db.flush()

        # 6. Sample AIPrediction rows
        pred_a = AIPrediction(
            company_id=comp_a.id,
            module_name="cost_forecast",
            prompt="predict concrete costs for project alpha",
            prediction={"confidence": 0.85, "estimated_cost_impact": 12000.0},
            created_by_user_id=u_all.id,
        )
        pred_b = AIPrediction(
            company_id=comp_b.id,
            module_name="schedule_delay",
            prompt="predict steel delivery delay",
            prediction={"confidence": 0.90, "estimated_delay_days": 4},
            created_by_user_id=u_b.id,
        )
        db.add_all([pred_a, pred_b])
        await db.flush()
        await db.commit()

        yield {
            "fake_redis": fake_redis,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "comp_no_ai": comp_no_ai,
            "pred_a": pred_a,
            "pred_b": pred_b,
            "u_all": u_all,
            "u_no_view": u_no_view,
            "u_no_create": u_no_create,
            "u_no_edit": u_no_edit,
            "u_no_delete": u_no_delete,
            "u_b": u_b,
            "u_no_ai": u_no_ai,
            "u_tl": u_tl,
            "sa_no_comp": sa_no_comp,
            "sa_with_comp": sa_with_comp,
            "tok_all": create_access_token({"sub": str(u_all.id), "user_id": u_all.id}),
            "tok_no_view": create_access_token({"sub": str(u_no_view.id), "user_id": u_no_view.id}),
            "tok_no_create": create_access_token({"sub": str(u_no_create.id), "user_id": u_no_create.id}),
            "tok_no_edit": create_access_token({"sub": str(u_no_edit.id), "user_id": u_no_edit.id}),
            "tok_no_delete": create_access_token({"sub": str(u_no_delete.id), "user_id": u_no_delete.id}),
            "tok_b": create_access_token({"sub": str(u_b.id), "user_id": u_b.id}),
            "tok_no_ai": create_access_token({"sub": str(u_no_ai.id), "user_id": u_no_ai.id}),
            "tok_tl": create_access_token({"sub": str(u_tl.id), "user_id": u_tl.id}),
            "tok_sa_nocomp": create_access_token({"sub": str(sa_no_comp.id), "user_id": sa_no_comp.id}),
            "tok_sa_comp": create_access_token({"sub": str(sa_with_comp.id), "user_id": sa_with_comp.id}),
        }


def _auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


# ==============================================================================
# A. Authentication (401 on unauthenticated access across all 5 routes)
# ==============================================================================
@pytest.mark.asyncio
async def test_authentication_required():
    """All 5 AI endpoints must return HTTP 401 when called without credentials."""
    async with setup_ai_test_data() as d:
        async with _client() as ac:
            r1 = await ac.post("/api/v1/ai/predict", json={"module_name": "test", "prompt": "test"})
            assert r1.status_code == 401

            r2 = await ac.get("/api/v1/ai")
            assert r2.status_code == 401

            r3 = await ac.get(f"/api/v1/ai/{d['pred_a'].id}")
            assert r3.status_code == 401

            r4 = await ac.put(f"/api/v1/ai/{d['pred_a'].id}", json={"prompt": "updated"})
            assert r4.status_code == 401

            r5 = await ac.delete(f"/api/v1/ai/{d['pred_a'].id}")
            assert r5.status_code == 401


# ==============================================================================
# B. Feature Entitlement (403 on missing AI feature entitlement across all 5 routes)
# ==============================================================================
@pytest.mark.asyncio
async def test_feature_entitlement_gate():
    """Tenant without 'ai_features' plan entitlement receives HTTP 403 on all routes."""
    async with setup_ai_test_data() as d:
        headers = _auth(d["tok_no_ai"])
        async with _client() as ac:
            r1 = await ac.post("/api/v1/ai/predict", headers=headers, json={"module_name": "test", "prompt": "test"})
            assert r1.status_code == 403
            assert "ai" in r1.text.lower() or "feature" in r1.text.lower()

            r2 = await ac.get("/api/v1/ai", headers=headers)
            assert r2.status_code == 403

            r3 = await ac.get(f"/api/v1/ai/{d['pred_a'].id}", headers=headers)
            assert r3.status_code == 403

            r4 = await ac.put(f"/api/v1/ai/{d['pred_a'].id}", headers=headers, json={"prompt": "updated"})
            assert r4.status_code == 403

            r5 = await ac.delete(f"/api/v1/ai/{d['pred_a'].id}", headers=headers)
            assert r5.status_code == 403


# ==============================================================================
# C. Permission Gates (missing granular permission -> HTTP 403)
# ==============================================================================
@pytest.mark.asyncio
async def test_permission_gates_granular():
    """Granular permissions ai.view, ai.create, ai.edit, ai.delete are independently enforced."""
    async with setup_ai_test_data() as d:
        async with _client() as ac:
            # Missing ai.view -> 403 on list and get
            r_list = await ac.get("/api/v1/ai", headers=_auth(d["tok_no_view"]))
            assert r_list.status_code == 403

            r_get = await ac.get(f"/api/v1/ai/{d['pred_a'].id}", headers=_auth(d["tok_no_view"]))
            assert r_get.status_code == 403

            # Missing ai.create -> 403 on predict
            r_create = await ac.post(
                "/api/v1/ai/predict",
                headers=_auth(d["tok_no_create"]),
                json={"module_name": "test", "prompt": "test"},
            )
            assert r_create.status_code == 403

            # Missing ai.edit -> 403 on update
            r_edit = await ac.put(
                f"/api/v1/ai/{d['pred_a'].id}",
                headers=_auth(d["tok_no_edit"]),
                json={"prompt": "new prompt"},
            )
            assert r_edit.status_code == 403

            # Missing ai.delete -> 403 on delete
            r_del = await ac.delete(
                f"/api/v1/ai/{d['pred_a'].id}",
                headers=_auth(d["tok_no_delete"]),
            )
            assert r_del.status_code == 403


# ==============================================================================
# D. Valid Tenant Access (with entitlement + permissions -> reaches logic)
# ==============================================================================
@pytest.mark.asyncio
async def test_valid_tenant_access_lifecycle():
    """Tenant user with active entitlement and full permissions can perform full CRUD lifecycle."""
    async with setup_ai_test_data() as d:
        headers = _auth(d["tok_all"])
        async with _client() as ac:
            # 1. Create prediction
            r_create = await ac.post(
                "/api/v1/ai/predict",
                headers=headers,
                json={"module_name": "foundation_safety", "prompt": "check concrete foundation curing"},
            )
            assert r_create.status_code == 200
            data_create = r_create.json()
            assert data_create["module_name"] == "foundation_safety"
            assert "prediction" in data_create

            # 2. List predictions (Tenant A sees only its own)
            r_list = await ac.get("/api/v1/ai", headers=headers)
            assert r_list.status_code == 200
            items = r_list.json()["items"]
            item_ids = [it["id"] for it in items]
            assert d["pred_a"].id in item_ids
            assert d["pred_b"].id not in item_ids

            # 3. Get single prediction
            r_get = await ac.get(f"/api/v1/ai/{d['pred_a'].id}", headers=headers)
            assert r_get.status_code == 200
            assert r_get.json()["id"] == d["pred_a"].id

            # 4. Update prediction
            r_update = await ac.put(
                f"/api/v1/ai/{d['pred_a'].id}",
                headers=headers,
                json={"prompt": "updated analysis prompt", "module_name": "foundation_safety_v2"},
            )
            assert r_update.status_code == 200
            assert r_update.json()["prompt"] == "updated analysis prompt"
            assert r_update.json()["module_name"] == "foundation_safety_v2"

            # 5. Delete prediction
            r_delete = await ac.delete(f"/api/v1/ai/{d['pred_a'].id}", headers=headers)
            assert r_delete.status_code == 204

            # Verify deleted
            r_verify = await ac.get(f"/api/v1/ai/{d['pred_a'].id}", headers=headers)
            assert r_verify.status_code == 404


# ==============================================================================
# E. Tenantless Non-SA (company_id=None -> 403 across all endpoints)
# ==============================================================================
@pytest.mark.asyncio
async def test_tenantless_non_sa_forbidden():
    """Non-SuperAdmin with company_id=None receives HTTP 403 on all AI endpoints."""
    async with setup_ai_test_data() as d:
        headers = _auth(d["tok_tl"])
        async with _client() as ac:
            r1 = await ac.post("/api/v1/ai/predict", headers=headers, json={"module_name": "x", "prompt": "x"})
            assert r1.status_code == 403

            r2 = await ac.get("/api/v1/ai", headers=headers)
            assert r2.status_code == 403

            r3 = await ac.get(f"/api/v1/ai/{d['pred_a'].id}", headers=headers)
            assert r3.status_code == 403

            r4 = await ac.put(f"/api/v1/ai/{d['pred_a'].id}", headers=headers, json={"prompt": "x"})
            assert r4.status_code == 403

            r5 = await ac.delete(f"/api/v1/ai/{d['pred_a'].id}", headers=headers)
            assert r5.status_code == 403


# ==============================================================================
# F. IDOR & Tenant Isolation (foreign tenant record -> HTTP 404 masking)
# ==============================================================================
@pytest.mark.asyncio
async def test_idor_tenant_isolation_masking():
    """Attempts by Tenant A to access Tenant B's predictions return HTTP 404 without leaking existence."""
    async with setup_ai_test_data() as d:
        headers = _auth(d["tok_all"])
        b_id = d["pred_b"].id
        async with _client() as ac:
            # GET foreign
            r_get = await ac.get(f"/api/v1/ai/{b_id}", headers=headers)
            assert r_get.status_code == 404

            # PUT foreign
            r_put = await ac.put(f"/api/v1/ai/{b_id}", headers=headers, json={"prompt": "malicious edit"})
            assert r_put.status_code == 404

            # DELETE foreign
            r_del = await ac.delete(f"/api/v1/ai/{b_id}", headers=headers)
            assert r_del.status_code == 404

            # Nonexistent record -> also 404
            r_nonexist = await ac.get("/api/v1/ai/99999999", headers=headers)
            assert r_nonexist.status_code == 404


# ==============================================================================
# G. Super Admin Semantics (Bypass feature, global/scoped listing, explicit company create)
# ==============================================================================
@pytest.mark.asyncio
async def test_super_admin_operations():
    """Super Admin operations: feature bypass, global list, filter by company, and company-targeted creation."""
    async with setup_ai_test_data() as d:
        headers_sa = _auth(d["tok_sa_nocomp"])
        async with _client() as ac:
            # 1. Global list (no company filter -> returns records from both Tenant A and Tenant B)
            r_global = await ac.get("/api/v1/ai", headers=headers_sa)
            assert r_global.status_code == 200
            global_ids = [item["id"] for item in r_global.json()["items"]]
            assert d["pred_a"].id in global_ids
            assert d["pred_b"].id in global_ids

            # 2. Filtered list by company_id
            r_filtered = await ac.get(f"/api/v1/ai?company_id={d['comp_a'].id}", headers=headers_sa)
            assert r_filtered.status_code == 200
            filtered_ids = [item["id"] for item in r_filtered.json()["items"]]
            assert d["pred_a"].id in filtered_ids
            assert d["pred_b"].id not in filtered_ids

            # 3. Filtered list with nonexistent company_id -> 404
            r_bad_comp = await ac.get("/api/v1/ai?company_id=9999999", headers=headers_sa)
            assert r_bad_comp.status_code == 404

            # 4. SA creation without company_id when SA has no company -> 400
            r_create_nocomp = await ac.post(
                "/api/v1/ai/predict",
                headers=headers_sa,
                json={"module_name": "sa_test", "prompt": "no company provided"},
            )
            assert r_create_nocomp.status_code == 400

            # 5. SA creation targeting valid company -> 200
            r_create_target = await ac.post(
                "/api/v1/ai/predict",
                headers=headers_sa,
                json={"module_name": "sa_test", "prompt": "target comp_a", "company_id": d["comp_a"].id},
            )
            assert r_create_target.status_code == 200

            # 6. SA creation targeting nonexistent company -> 404
            r_create_bad_comp = await ac.post(
                "/api/v1/ai/predict",
                headers=headers_sa,
                json={"module_name": "sa_test", "prompt": "target bad comp", "company_id": 9999999},
            )
            assert r_create_bad_comp.status_code == 404

            # 7. Non-SA specifying foreign company_id in POST body -> forced to own company
            r_non_sa_override = await ac.post(
                "/api/v1/ai/predict",
                headers=_auth(d["tok_all"]),
                json={"module_name": "tenant_test", "prompt": "attempt cross tenant", "company_id": d["comp_b"].id},
            )
            assert r_non_sa_override.status_code == 200
            # Verify in DB that it was saved under comp_a, NOT comp_b
            async with AsyncSessionLocal() as db:
                created_row = (await db.execute(
                    select(AIPrediction).where(AIPrediction.prompt == "attempt cross tenant")
                )).scalar_one()
                assert created_row.company_id == d["comp_a"].id

            # 8. Non-SA query param `company_id` on GET is ignored/forced to current_user.company_id
            r_non_sa_get = await ac.get(f"/api/v1/ai?company_id={d['comp_b'].id}", headers=_auth(d["tok_all"]))
            assert r_non_sa_get.status_code == 200
            non_sa_items = r_non_sa_get.json()["items"]
            assert all(item["id"] != d["pred_b"].id for item in non_sa_items)


# ==============================================================================
# H. Input Validation (Schemas, extra fields forbidden, prompt lengths)
# ==============================================================================
@pytest.mark.asyncio
async def test_input_validation_and_schemas():
    """Strict Pydantic input validation on update and predict endpoints."""
    async with setup_ai_test_data() as d:
        headers = _auth(d["tok_all"])
        async with _client() as ac:
            # 1. Update with unknown extra field -> 422 (extra="forbid")
            r_extra = await ac.put(
                f"/api/v1/ai/{d['pred_a'].id}",
                headers=headers,
                json={"prompt": "valid prompt", "malicious_injected_field": "hacked"},
            )
            assert r_extra.status_code == 422

            # 2. Update with empty module_name -> 422
            r_empty_mod = await ac.put(
                f"/api/v1/ai/{d['pred_a'].id}",
                headers=headers,
                json={"module_name": ""},
            )
            assert r_empty_mod.status_code == 422

            # 3. Predict with unknown extra field -> 422 (extra="forbid")
            r_pred_extra = await ac.post(
                "/api/v1/ai/predict",
                headers=headers,
                json={"module_name": "test", "prompt": "ok", "unexpected": "value"},
            )
            assert r_pred_extra.status_code == 422

            # 4. Predict with oversized prompt (> 5000 characters) -> 422
            oversized_prompt = "A" * 5001
            r_oversized = await ac.post(
                "/api/v1/ai/predict",
                headers=headers,
                json={"module_name": "test", "prompt": oversized_prompt},
            )
            assert r_oversized.status_code == 422

            # 5. Update with oversized prompt (> 5000 characters) -> 422
            r_upd_oversized = await ac.put(
                f"/api/v1/ai/{d['pred_a'].id}",
                headers=headers,
                json={"prompt": oversized_prompt},
            )
            assert r_upd_oversized.status_code == 422


# ==============================================================================
# I. Cache Isolation (Tenant-scoped version keys and cache invalidation)
# ==============================================================================
@pytest.mark.asyncio
async def test_cache_version_isolation():
    """Tenant A mutations bump Tenant A cache version without altering Tenant B cache version."""
    async with setup_ai_test_data() as d:
        fake_redis = d["fake_redis"]
        headers_a = _auth(d["tok_all"])

        ver_key_a = f"cache_version:ai_predictions:{d['comp_a'].id}"
        ver_key_b = f"cache_version:ai_predictions:{d['comp_b'].id}"
        ver_key_sa = "cache_version:ai_predictions:sa"

        # Initialize versions
        ver_a_initial = await get_cache_version(fake_redis, ver_key_a)
        ver_b_initial = await get_cache_version(fake_redis, ver_key_b)
        ver_sa_initial = await get_cache_version(fake_redis, ver_key_sa)

        async with _client() as ac:
            # 1. Tenant A performs predict mutation
            r1 = await ac.post(
                "/api/v1/ai/predict",
                headers=headers_a,
                json={"module_name": "cache_test", "prompt": "test cache isolation"},
            )
            assert r1.status_code == 200

            ver_a_after_post = await get_cache_version(fake_redis, ver_key_a)
            ver_b_after_post = await get_cache_version(fake_redis, ver_key_b)

            # Tenant A version must have bumped
            assert ver_a_after_post > ver_a_initial
            # Tenant B version must NOT have changed
            assert ver_b_after_post == ver_b_initial

            # 2. Tenant A performs update mutation
            r2 = await ac.put(
                f"/api/v1/ai/{d['pred_a'].id}",
                headers=headers_a,
                json={"prompt": "cache update test"},
            )
            assert r2.status_code == 200

            ver_a_after_put = await get_cache_version(fake_redis, ver_key_a)
            ver_b_after_put = await get_cache_version(fake_redis, ver_key_b)

            assert ver_a_after_put > ver_a_after_post
            assert ver_b_after_put == ver_b_initial

            # 3. Tenant A performs delete mutation
            r3 = await ac.delete(
                f"/api/v1/ai/{d['pred_a'].id}",
                headers=headers_a,
            )
            assert r3.status_code == 204

            ver_a_after_del = await get_cache_version(fake_redis, ver_key_a)
            ver_b_after_del = await get_cache_version(fake_redis, ver_key_b)

            assert ver_a_after_del > ver_a_after_put
            assert ver_b_after_del == ver_b_initial

            # 4. Super Admin platform version was also bumped during mutations
            ver_sa_after = await get_cache_version(fake_redis, ver_key_sa)
            assert ver_sa_after > ver_sa_initial


# ==============================================================================
# J. Route Preservation & Contract Integrity
# ==============================================================================
def test_route_preservation():
    """Verify exactly 5 AI routes, 5 unique method+path, 0 duplicates, and 781 total APIRoutes."""
    ai_routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/v1/ai")]
    assert len(ai_routes) == 5, f"Expected 5 AI routes, got {len(ai_routes)}"

    unique_routes = set((list(r.methods)[0], r.path) for r in ai_routes)
    assert len(unique_routes) == 5, f"Expected 5 unique AI routes, got {len(unique_routes)}"

    expected_routes = {
        ("POST", "/api/v1/ai/predict"),
        ("GET", "/api/v1/ai"),
        ("GET", "/api/v1/ai/{prediction_id}"),
        ("PUT", "/api/v1/ai/{prediction_id}"),
        ("DELETE", "/api/v1/ai/{prediction_id}"),
    }
    assert unique_routes == expected_routes

    all_api_routes = [r for r in app.routes if isinstance(r, APIRoute)]
    assert len(all_api_routes) == 781, f"Expected total 781 APIRoutes, got {len(all_api_routes)}"


# ==============================================================================
# K. Static Security Hygiene
# ==============================================================================
def test_static_security_hygiene():
    """Verify app/api/ai.py contains 0 legacy role checks and uses canonical require_permission."""
    source = inspect.getsource(ai_api_module)
    assert "require_roles" not in source, "Must NOT contain require_roles"
    assert "admin_required" not in source, "Must NOT contain admin_required"
    assert "UserRole" not in source, "Must NOT use UserRole authorization checks"
    assert "current_user.role ==" not in source, "Must NOT check role string directly"
    assert 'require_permission("ai.create")' in source
    assert 'require_permission("ai.view")' in source
    assert 'require_permission("ai.edit")' in source
    assert 'require_permission("ai.delete")' in source
    assert 'require_feature("ai_features", "AI Predictions")' in source
