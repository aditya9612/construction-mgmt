import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main
from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_current_active_user
from app.models.user import User

client = TestClient(app)


def override_user(user: User | None):
    if user is None:
        app.dependency_overrides.clear()
    else:
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_current_active_user] = lambda: user


@pytest.fixture(autouse=True)
def cleanup_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def seed_cost_report_users():
    async def _setup():
        async with AsyncSessionLocal() as db:
            user_a = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
            user_b = await db.scalar(select(User).where(User.company_id == 2, User.role == "Admin").limit(1))
            user_super = await db.scalar(select(User).where(User.is_super_admin == True).limit(1))
            user_no_view = await db.scalar(select(User).where(User.company_id == 1, User.role == "Labour").limit(1))
            return {
                "user_a": user_a,
                "user_b": user_b,
                "user_super": user_super,
                "user_no_view": user_no_view,
            }

    return asyncio.run(_setup())


def test_equipment_cost_report_openapi_operation_ids_unique():
    """
    EQ-P2-004: Proves /api/v1/equipment/cost/report and /api/v1/equipment/cost-report
    have distinct, deterministic, non-empty OpenAPI operation IDs.
    """
    openapi_schema = app.openapi()
    paths = openapi_schema.get("paths", {})

    slash_path = paths.get("/api/v1/equipment/cost/report", {})
    dash_path = paths.get("/api/v1/equipment/cost-report", {})

    assert slash_path, "Route /api/v1/equipment/cost/report not found in OpenAPI schema"
    assert dash_path, "Route /api/v1/equipment/cost-report not found in OpenAPI schema"

    op1 = slash_path.get("get", {}).get("operationId")
    op2 = dash_path.get("get", {}).get("operationId")

    assert op1 is not None, "operationId missing for /api/v1/equipment/cost/report"
    assert op2 is not None, "operationId missing for /api/v1/equipment/cost-report"
    assert op1 != op2, f"operation IDs must be distinct! Both were: {op1}"

    assert op1 == "equipment_cost_report"
    assert op2 == "equipment_cost_report_alias"

    # Scan all /api/v1/equipment routes in OpenAPI for any duplicate operation IDs
    equipment_op_ids = []
    for path, methods in paths.items():
        if path.startswith("/api/v1/equipment"):
            for method, spec in methods.items():
                if method.lower() in ("get", "post", "put", "patch", "delete"):
                    op_id = spec.get("operationId")
                    if op_id:
                        equipment_op_ids.append(op_id)

    duplicates = [op for op in equipment_op_ids if equipment_op_ids.count(op) > 1]
    assert not duplicates, f"Duplicate operation IDs found in equipment routes: {set(duplicates)}"


def test_both_cost_report_endpoints_callable_and_consistent(seed_cost_report_users):
    """
    Verifies that both /cost/report and /cost-report remain fully functional,
    with identical behavior, auth, RBAC, and response structure.
    """
    data = seed_cost_report_users

    # 1. Unauthenticated -> 401 on both
    override_user(None)
    r1 = client.get("/api/v1/equipment/cost/report")
    r2 = client.get("/api/v1/equipment/cost-report")
    assert r1.status_code == 401
    assert r2.status_code == 401

    # 2. User without equipment.view -> 403 on both
    if data["user_no_view"]:
        override_user(data["user_no_view"])
        r1 = client.get("/api/v1/equipment/cost/report")
        r2 = client.get("/api/v1/equipment/cost-report")
        assert r1.status_code == 403
        assert r2.status_code == 403

    # 3. Authorized tenant user -> 200 and identical responses
    override_user(data["user_a"])
    res_slash = client.get("/api/v1/equipment/cost/report")
    res_dash = client.get("/api/v1/equipment/cost-report")
    assert res_slash.status_code == 200
    assert res_dash.status_code == 200
    assert res_slash.json() == res_dash.json()

    # 4. Filters work identically on both
    res_slash_f = client.get("/api/v1/equipment/cost/report?limit=10&offset=0")
    res_dash_f = client.get("/api/v1/equipment/cost-report?limit=10&offset=0")
    assert res_slash_f.status_code == 200
    assert res_dash_f.status_code == 200
    assert res_slash_f.json() == res_dash_f.json()
