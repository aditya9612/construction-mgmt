import pytest
import uuid
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, get_db_session
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus
from app.models.master_data import ActivityType
from app.models.boq import BOQ
from unittest.mock import patch, AsyncMock


@pytest.fixture(autouse=True)
def clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


async def setup_boq_test_data():
    """Seed companies, projects, and activity type for BOQ HTTP E2E tests."""
    async with AsyncSessionLocal() as db:
        suffix = uuid.uuid4().hex[:6]
        c1 = await db.get(Company, 1)
        if not c1:
            c1 = Company(id=1, name="BOQ Co 1", is_active=True)
            db.add(c1)

        c2 = await db.get(Company, 2)
        if not c2:
            c2 = Company(id=2, name="BOQ Co 2", is_active=True)
            db.add(c2)

        owner = await db.scalar(select(Owner).where(Owner.company_id == 1))
        if not owner:
            owner = Owner(
                owner_code=f"OWN-BOQ-{suffix}",
                owner_name="BOQ Owner",
                mobile="9876543111",
                email="boq_owner@test.com",
                company_id=1,
            )
            db.add(owner)
            await db.flush()

        p1 = Project(
            business_id=f"PRJ-BOQ-1-{suffix}",
            company_id=1,
            project_name=f"BOQ Proj 1 {suffix}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        p2 = Project(
            business_id=f"PRJ-BOQ-2-{suffix}",
            company_id=2,  # Belongs to Company 2
            project_name=f"BOQ Proj 2 {suffix}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([p1, p2])
        await db.flush()

        act = await db.scalar(select(ActivityType).where(ActivityType.company_id == 1))
        if not act:
            act = ActivityType(name=f"Earthwork-{suffix}", company_id=1, is_active=True)
            db.add(act)
            await db.flush()

        await db.commit()
        await db.refresh(p1)
        await db.refresh(p2)
        await db.refresh(act)

        return {
            "company_id": 1,
            "project_id": p1.id,
            "comp2_project_id": p2.id,
            "activity_type_id": act.id,
        }


# ================= 1. UNAUTHENTICATED TEST (401) =================

@pytest.mark.asyncio
async def test_boq_unauthenticated_fails_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/boq", json={"project_id": 1, "item_name": "Test"})
        assert resp.status_code == 401


# ================= 2. UNAUTHORIZED TEST (403) =================

@pytest.mark.asyncio
async def test_boq_unauthorized_fails_403():
    user = User(id=9101, email="viewer_boq@test.com", role=UserRole.SITE_ENGINEER.value, is_active=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"boq.view"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/boq", json={
                "project_id": 1,
                "item_name": "Concrete 20mm",
                "quantity": 100.0,
                "unit_cost": 500.0,
                "activity_type_id": 1,
            })
            assert resp.status_code == 403


# ================= 3. INVALID PAYLOAD TEST (422) =================

@pytest.mark.asyncio
async def test_boq_invalid_payload_fails_422():
    user = User(id=9102, email="admin_boq@test.com", role=UserRole.ADMIN.value, is_active=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"boq.create", "boq.view"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Missing project_id and activity_type_id
            resp = await client.post("/api/v1/boq", json={"item_name": "Concrete"})
            assert resp.status_code == 422


# ================= 4. CREATE VALID BOQ RECORD (200 / 201) =================

@pytest.mark.asyncio
async def test_boq_create_valid_record_200():
    data = await setup_boq_test_data()
    user = User(id=9103, email="admin_boq@test.com", role=UserRole.ADMIN.value, is_active=True, is_super_admin=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"boq.create", "boq.view", "boq.edit", "boq.delete"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "project_id": data["project_id"],
                "item_name": "Excavation and Earthwork",
                "description": "Earthwork excavation in foundation trenches",
                "quantity": 250.0,
                "unit_cost": 120.0,
                "status": "APPROVED",
                "activity_type_id": data["activity_type_id"],
            }
            resp = await client.post("/api/v1/boq", json=payload)
            assert resp.status_code in [200, 201], resp.text
            res_data = resp.json()
            assert res_data["id"] is not None
            assert res_data["item_name"] == "Excavation and Earthwork"
            assert float(res_data["quantity"]) == 250.0
            assert float(res_data["unit_cost"]) == 120.0

            boq_id = res_data["id"]

            # DB persistence verification
            async with AsyncSessionLocal() as db:
                boq = await db.get(BOQ, boq_id)
                assert boq is not None
                assert boq.item_name == "Excavation and Earthwork"
                assert boq.project_id == data["project_id"]


# ================= 5. GET SINGLE, LIST, UPDATE, DELETE ENDPOINTS (200) =================

@pytest.mark.asyncio
async def test_boq_crud_lifecycle_200():
    data = await setup_boq_test_data()
    user = User(id=9104, email="admin_boq@test.com", role=UserRole.ADMIN.value, is_active=True, is_super_admin=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"boq.create", "boq.view", "boq.edit", "boq.delete"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Create
            resp = await client.post("/api/v1/boq", json={
                "project_id": data["project_id"],
                "item_name": "Brick Masonry 9 inch",
                "quantity": 100.0,
                "unit_cost": 85.0,
                "activity_type_id": data["activity_type_id"],
            })
            assert resp.status_code in [200, 201]
            boq_id = resp.json()["id"]

            # 2. Get Single
            get_resp = await client.get(f"/api/v1/boq/{boq_id}")
            assert get_resp.status_code == 200
            assert get_resp.json()["id"] == boq_id

            # 3. List
            list_resp = await client.get(f"/api/v1/boq?project_id={data['project_id']}")
            assert list_resp.status_code == 200

            # 4. Update
            update_resp = await client.put(f"/api/v1/boq/{boq_id}", json={
                "item_name": "Brick Masonry 9 inch (Updated)",
                "quantity": 120.0,
            })
            assert update_resp.status_code == 200
            assert update_resp.json()["item_name"] == "Brick Masonry 9 inch (Updated)"

            # 5. Delete
            del_resp = await client.delete(f"/api/v1/boq/{boq_id}")
            assert del_resp.status_code in [200, 204]

            # Verify deleted from DB (soft delete marks status as Deleted)
            async with AsyncSessionLocal() as db:
                deleted_obj = await db.get(BOQ, boq_id)
                assert deleted_obj is not None
                assert deleted_obj.status == "Deleted"


# ================= 6. NON-EXISTENT AND CROSS-TENANT ISOLATION (404/403) =================

@pytest.mark.asyncio
async def test_boq_cross_tenant_and_non_existent():
    data = await setup_boq_test_data()
    # User belongs strictly to Company 1 (NOT super admin)
    comp1_user = User(id=9105, email="user_c1@test.com", role=UserRole.ADMIN.value, is_active=True, is_super_admin=False, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: comp1_user
    app.dependency_overrides[get_current_active_user] = lambda: comp1_user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"boq.create", "boq.view", "boq.edit", "boq.delete"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Non-existent returns 404
            get_404 = await client.get("/api/v1/boq/999999")
            assert get_404.status_code == 404

            # 2. Attempting to create BOQ for Project 2 (Company 2) must be rejected
            resp_cross = await client.post("/api/v1/boq", json={
                "project_id": data["comp2_project_id"],
                "item_name": "Injected Item",
                "quantity": 10.0,
                "unit_cost": 10.0,
                "activity_type_id": data["activity_type_id"],
            })
            assert resp_cross.status_code in [403, 404]


# ================= 7. BOQ BULK ADD ITEMS (200 / 201) =================

@pytest.mark.asyncio
async def test_boq_bulk_add_items_200():
    data = await setup_boq_test_data()
    user = User(id=9106, email="admin_boq@test.com", role=UserRole.ADMIN.value, is_active=True, is_super_admin=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"boq.create", "boq.view", "boq.edit", "boq.delete", "boq.export"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # First create a parent BOQ item to establish the group
            create_resp = await client.post("/api/v1/boq", json={
                "project_id": data["project_id"],
                "item_name": "Parent BOQ Group Item",
                "quantity": 10.0,
                "unit_cost": 50.0,
                "activity_type_id": data["activity_type_id"],
            })
            assert create_resp.status_code in [200, 201]
            group_id = create_resp.json()["boq_group_id"]

            # Now bulk add items to this group
            bulk_payload = {
                "items": [
                    {
                        "project_id": data["project_id"],
                        "item_name": "Bulk Sub-Item 1",
                        "quantity": 20.0,
                        "unit_cost": 15.0,
                        "activity_type_id": data["activity_type_id"],
                    },
                    {
                        "project_id": data["project_id"],
                        "item_name": "Bulk Sub-Item 2",
                        "quantity": 30.0,
                        "unit_cost": 25.0,
                        "activity_type_id": data["activity_type_id"],
                    }
                ]
            }
            bulk_resp = await client.post(f"/api/v1/boq/groups/{group_id}/items/bulk", json=bulk_payload)
            assert bulk_resp.status_code in [200, 201], bulk_resp.text
            res_items = bulk_resp.json()
            assert len(res_items) == 2


# ================= 8. BOQ EXPORT EXCEL (200) =================

@pytest.mark.asyncio
async def test_boq_export_excel_200():
    data = await setup_boq_test_data()
    user = User(id=9107, email="admin_boq@test.com", role=UserRole.ADMIN.value, is_active=True, is_super_admin=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"boq.create", "boq.view", "boq.edit", "boq.delete", "boq.export"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/v1/boq", json={
                "project_id": data["project_id"],
                "item_name": "Export Excel BOQ Item",
                "quantity": 50.0,
                "unit_cost": 100.0,
                "activity_type_id": data["activity_type_id"],
            })
            assert create_resp.status_code in [200, 201]
            boq_id = create_resp.json()["id"]

            excel_resp = await client.get(f"/api/v1/boq/{boq_id}/export/excel")
            assert excel_resp.status_code == 200
            assert len(excel_resp.content) > 100
            # Openpyxl XLSX file starts with ZIP magic bytes PK
            assert excel_resp.content[:2] == b"PK"


# ================= 9. BOQ EXPORT PDF (200) =================

@pytest.mark.asyncio
async def test_boq_export_pdf_200():
    data = await setup_boq_test_data()
    user = User(id=9108, email="admin_boq@test.com", role=UserRole.ADMIN.value, is_active=True, is_super_admin=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"boq.create", "boq.view", "boq.edit", "boq.delete", "boq.export"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/v1/boq", json={
                "project_id": data["project_id"],
                "item_name": "Export PDF BOQ Item",
                "quantity": 50.0,
                "unit_cost": 100.0,
                "activity_type_id": data["activity_type_id"],
            })
            assert create_resp.status_code in [200, 201]
            boq_id = create_resp.json()["id"]

            pdf_resp = await client.get(f"/api/v1/boq/{boq_id}/export/pdf")
            assert pdf_resp.status_code == 200
            assert len(pdf_resp.content) > 100
            # PDF file starts with %PDF
            assert pdf_resp.content[:4] == b"%PDF"
