import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, delete

from app.core.dependencies import get_current_active_user, require_permission
from app.db.session import get_db_session
from app.main import app
from app.models.project import Project, SiteRequest
from app.models.user import User


def get_test_client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_site_request_mandatory_fields_and_validation():
    created_request_ids = []

    async for db in get_db_session():
        user_res = await db.execute(select(User).where(User.is_active == True).limit(1))
        test_user = user_res.scalar_one()

        res = await db.execute(select(Project).where(Project.company_id == test_user.company_id).limit(1))
        project = res.scalar_one_or_none()
        if not project:
            res = await db.execute(select(Project).limit(1))
            project = res.scalar_one_or_none()
        project_id = project.id
        break

    app.dependency_overrides[get_current_active_user] = lambda: test_user
    app.dependency_overrides[require_permission("site_requests.create")] = lambda: test_user
    app.dependency_overrides[require_permission("site_requests.view")] = lambda: test_user

    try:
        async with get_test_client() as client:
            # ==========================================
            # 1. Validation failures (Should return 422)
            # ==========================================

            # Case A: Missing project_id
            resp = await client.post(
                "/api/v1/site-requests",
                json={"request_type": "Material", "quantity": 10},
            )
            assert resp.status_code == 422, resp.text
            err = resp.json()
            assert any(e["loc"][-1] == "project_id" for e in err["detail"])

            # Case B: Missing quantity
            resp = await client.post(
                "/api/v1/site-requests",
                json={"project_id": project_id, "request_type": "Material"},
            )
            assert resp.status_code == 422, resp.text
            err = resp.json()
            assert any(e["loc"][-1] == "quantity" for e in err["detail"])

            # Case C: Invalid request_type
            resp = await client.post(
                "/api/v1/site-requests",
                json={"project_id": project_id, "request_type": "InvalidType", "quantity": 10},
            )
            assert resp.status_code == 422, resp.text

            # Case D: project_id = 0
            resp = await client.post(
                "/api/v1/site-requests",
                json={"project_id": 0, "request_type": "Material", "quantity": 10},
            )
            assert resp.status_code == 422, resp.text

            # Case E: Negative project_id
            resp = await client.post(
                "/api/v1/site-requests",
                json={"project_id": -1, "request_type": "Material", "quantity": 10},
            )
            assert resp.status_code == 422, resp.text

            # Case F: quantity = 0
            resp = await client.post(
                "/api/v1/site-requests",
                json={"project_id": project_id, "request_type": "Material", "quantity": 0},
            )
            assert resp.status_code == 422, resp.text

            # Case G: Negative quantity
            resp = await client.post(
                "/api/v1/site-requests",
                json={"project_id": project_id, "request_type": "Material", "quantity": -5},
            )
            assert resp.status_code == 422, resp.text

            # Case H: Missing request_type
            resp = await client.post(
                "/api/v1/site-requests",
                json={"project_id": project_id, "quantity": 10},
            )
            assert resp.status_code == 422, resp.text
            err = resp.json()
            assert any(e["loc"][-1] == "request_type" for e in err["detail"])

            # ==========================================
            # 2. Valid requests (Should return 200)
            # ==========================================

            # Payload 1: Only mandatory fields + lowercase request_type
            resp = await client.post(
                "/api/v1/site-requests",
                json={
                    "project_id": project_id,
                    "request_type": "material",
                    "quantity": 10,
                },
            )
            assert resp.status_code == 200, resp.text
            created1 = resp.json()
            assert created1["project_id"] == project_id
            assert created1["request_type"] == "Material"
            assert created1["quantity"] == 10.0
            assert created1["status"] == "Pending"
            assert created1["description"] is None
            created_request_ids.append(created1["id"])

            # Payload 2: Case-insensitive + description
            resp = await client.post(
                "/api/v1/site-requests",
                json={
                    "project_id": project_id,
                    "request_type": "labour",
                    "quantity": 5,
                    "description": "Workers required",
                },
            )
            assert resp.status_code == 200, resp.text
            created2 = resp.json()
            assert created2["project_id"] == project_id
            assert created2["request_type"] == "Labour"
            assert created2["quantity"] == 5.0
            assert created2["description"] == "Workers required"
            assert created2["status"] == "Pending"
            created_request_ids.append(created2["id"])

            # Payload 3: Whitespace padded request_type + whitespace description
            resp = await client.post(
                "/api/v1/site-requests",
                json={
                    "project_id": project_id,
                    "request_type": " MATERIAL ",
                    "quantity": 10,
                    "description": "   ",
                },
            )
            assert resp.status_code == 200, resp.text
            created3 = resp.json()
            assert created3["project_id"] == project_id
            assert created3["request_type"] == "Material"
            assert created3["quantity"] == 10.0
            assert created3["description"] is None
            assert created3["status"] == "Pending"
            created_request_ids.append(created3["id"])

            # Additional enums: Equipment, Work
            resp = await client.post(
                "/api/v1/site-requests",
                json={
                    "project_id": project_id,
                    "request_type": "equipment",
                    "quantity": 2.0,
                    "description": "Excavator needed",
                },
            )
            assert resp.status_code == 200, resp.text
            created4 = resp.json()
            assert created4["request_type"] == "Equipment"
            created_request_ids.append(created4["id"])

            resp = await client.post(
                "/api/v1/site-requests",
                json={
                    "project_id": project_id,
                    "request_type": "WORK",
                    "quantity": 1.0,
                },
            )
            assert resp.status_code == 200, resp.text
            created5 = resp.json()
            assert created5["request_type"] == "Work"
            created_request_ids.append(created5["id"])

            # ==========================================
            # 3. GET /api/v1/site-requests?project_id=<id>
            # ==========================================
            resp = await client.get(f"/api/v1/site-requests?project_id={project_id}")
            assert resp.status_code == 200, resp.text
            items = resp.json()
            item_ids = [i["id"] for i in items]
            for cr_id in created_request_ids:
                assert cr_id in item_ids

    finally:
        app.dependency_overrides.clear()
        if created_request_ids:
            async for db in get_db_session():
                await db.execute(delete(SiteRequest).where(SiteRequest.id.in_(created_request_ids)))
                await db.commit()
                break
