import asyncio
import uuid
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main
from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_current_active_user
from app.models.user import User
from app.models.project import Project
from app.models.equipment import Equipment, EquipmentAuditLog
from app.core.enums import EquipmentStatus, EquipmentCondition

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
def seed_central_creation_data():
    """
    Seeds isolated test projects and users for Company 1 and Company 2.
    """
    async def _setup():
        async with AsyncSessionLocal() as db:
            user_a = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
            user_b = await db.scalar(select(User).where(User.company_id == 2, User.role == "Admin").limit(1))
            user_super = await db.scalar(select(User).where(User.is_super_admin == True).limit(1))
            user_labour = await db.scalar(select(User).where(User.company_id == 1, User.role == "Labour").limit(1))

            if not user_a or not user_b or not user_super:
                raise RuntimeError("Required test users not found in DB")

            proj_a = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
            if not proj_a:
                proj_a = Project(company_id=1, project_name=f"Proj A Central {uuid.uuid4().hex[:6]}", status="IN_PROGRESS")
                db.add(proj_a)
                await db.flush()

            proj_b = await db.scalar(select(Project).where(Project.company_id == 2).limit(1))
            if not proj_b:
                proj_b = Project(company_id=2, project_name=f"Proj B Central {uuid.uuid4().hex[:6]}", status="IN_PROGRESS")
                db.add(proj_b)
                await db.flush()

            await db.commit()

            return {
                "user_a": user_a,
                "user_b": user_b,
                "user_super": user_super,
                "user_labour": user_labour,
                "proj_a_id": proj_a.id,
                "proj_b_id": proj_b.id,
                "created_equipment_ids": [],
            }

    data = asyncio.run(_setup())
    yield data

    # Teardown
    async def _cleanup():
        async with AsyncSessionLocal() as db:
            for eq_id in data["created_equipment_ids"]:
                audit_logs = (await db.scalars(select(EquipmentAuditLog).where(EquipmentAuditLog.equipment_id == eq_id))).all()
                for log in audit_logs:
                    await db.delete(log)
                eq = await db.get(Equipment, eq_id)
                if eq:
                    await db.delete(eq)
            await db.commit()

    asyncio.run(_cleanup())


def test_authorized_user_creates_central_equipment(seed_central_creation_data):
    """
    Test 1: Authorized Company A user creates with project_id=None
    → 201 Created
    → company_id = Company A (persisted in DB)
    → project_id = NULL
    → status = AVAILABLE
    """
    data = seed_central_creation_data
    override_user(data["user_a"])

    tag = uuid.uuid4().hex[:6].upper()
    payload = {
        "equipment_name": f"Central Test Loader {tag}",
        "equipment_code": f"EQ-DIR-CEN-{tag}",
        "project_id": None,
        "condition": EquipmentCondition.GOOD.value,
        "rental_cost": "0.00",
    }

    res = client.post("/api/v1/equipment", json=payload)
    assert res.status_code == 201, f"Expected 201, got {res.status_code}: {res.text}"

    res_data = res.json()
    assert res_data["project_id"] is None
    assert res_data["status"] == "AVAILABLE"
    assert res_data["equipment_code"] == payload["equipment_code"]

    created_id = res_data["id"]
    data["created_equipment_ids"].append(created_id)

    # Verify persisted in database directly
    async def _verify():
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, created_id)
            assert eq is not None
            assert eq.company_id == 1
            assert eq.project_id is None
            assert eq.status == EquipmentStatus.AVAILABLE
            assert eq.is_deleted is False

    asyncio.run(_verify())


def test_authorized_user_creates_project_equipment(seed_central_creation_data):
    """
    Test 2: Authorized Company A user creates with valid Company A project
    → existing behavior remains unchanged (status=IN_PROJECT, project_id=proj_a_id)
    """
    data = seed_central_creation_data
    override_user(data["user_a"])

    tag = uuid.uuid4().hex[:6].upper()
    payload = {
        "equipment_name": f"Project Assigned Loader {tag}",
        "equipment_code": f"EQ-DIR-PRJ-{tag}",
        "project_id": data["proj_a_id"],
        "condition": EquipmentCondition.GOOD.value,
        "rental_cost": "0.00",
    }

    res = client.post("/api/v1/equipment", json=payload)
    assert res.status_code == 201, f"Expected 201, got {res.status_code}: {res.text}"

    res_data = res.json()
    assert res_data["project_id"] == data["proj_a_id"]
    assert res_data["status"] == "IN_PROJECT"
    assert res_data["equipment_code"] == payload["equipment_code"]

    created_id = res_data["id"]
    data["created_equipment_ids"].append(created_id)

    async def _verify():
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, created_id)
            assert eq is not None
            assert eq.company_id == 1
            assert eq.project_id == data["proj_a_id"]
            assert eq.status == EquipmentStatus.IN_PROJECT

    asyncio.run(_verify())


def test_cross_tenant_project_creation_denied(seed_central_creation_data):
    """
    Test 3: Company A user attempts to create equipment on Company B project
    → existing access denial remains (403 or 404)
    """
    data = seed_central_creation_data
    override_user(data["user_a"])

    tag = uuid.uuid4().hex[:6]
    payload = {
        "equipment_name": f"Cross Tenant Attempt {tag}",
        "equipment_code": f"EQ-X-TENANT-{tag}",
        "project_id": data["proj_b_id"],
        "condition": EquipmentCondition.GOOD.value,
        "rental_cost": "0.00",
    }

    res = client.post("/api/v1/equipment", json=payload)
    assert res.status_code in (403, 404), f"Expected 403/404, got {res.status_code}: {res.text}"


def test_client_cannot_manipulate_company_id(seed_central_creation_data):
    """
    Test 4: Client attempts to supply or manipulate company_id
    → persisted company remains current_user.company_id (Company A = 1)
    """
    data = seed_central_creation_data
    override_user(data["user_a"])

    tag = uuid.uuid4().hex[:6]
    payload = {
        "equipment_name": f"Manipulate Company Test {tag}",
        "equipment_code": f"EQ-MANIP-CO-{tag}",
        "project_id": None,
        "company_id": 2,  # Client tries to assign to Company 2
        "condition": EquipmentCondition.GOOD.value,
        "rental_cost": "0.00",
    }

    res = client.post("/api/v1/equipment", json=payload)
    assert res.status_code == 201, f"Expected 201, got {res.status_code}: {res.text}"

    created_id = res.json()["id"]
    data["created_equipment_ids"].append(created_id)

    async def _verify():
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, created_id)
            assert eq.company_id == 1, "Persisted company_id must remain 1 (ignoring client payload)"

    asyncio.run(_verify())


def test_unauthenticated_request_rejected():
    """
    Test 5: Unauthenticated request → 401
    """
    override_user(None)

    payload = {
        "equipment_name": "Unauth Equipment",
        "equipment_code": "EQ-UNAUTH-001",
        "project_id": None,
    }

    res = client.post("/api/v1/equipment", json=payload)
    assert res.status_code == 401, f"Expected 401, got {res.status_code}: {res.text}"


def test_user_without_equipment_create_rejected(seed_central_creation_data):
    """
    Test 6: User without equipment.create permission → 403
    """
    data = seed_central_creation_data
    if data["user_labour"]:
        override_user(data["user_labour"])

        tag = uuid.uuid4().hex[:6]
        payload = {
            "equipment_name": f"Labour Create Attempt {tag}",
            "equipment_code": f"EQ-LABOUR-{tag}",
            "project_id": None,
        }

        res = client.post("/api/v1/equipment", json=payload)
        assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"


def test_superadmin_forbidden(seed_central_creation_data):
    """
    Test 7: SuperAdmin → preserve existing 403
    """
    data = seed_central_creation_data
    override_user(data["user_super"])

    tag = uuid.uuid4().hex[:6]
    payload = {
        "equipment_name": f"SuperAdmin Equipment {tag}",
        "equipment_code": f"EQ-SA-CENTRAL-{tag}",
        "project_id": None,
    }

    res = client.post("/api/v1/equipment", json=payload)
    assert res.status_code == 403, f"Expected 403, got {res.status_code}: {res.text}"
    assert "Super Admin cannot create company equipment directly" in res.json().get("detail", "")


def test_created_central_equipment_lifecycle_flows(seed_central_creation_data):
    """
    Test 8: Verify created central equipment can subsequently be:
    - retrieved individually (GET /{id})
    - listed (GET /)
    - allocated to project (POST /allocate)
    - deallocated back to central pool (PUT /deallocate)
    """
    data = seed_central_creation_data
    override_user(data["user_a"])

    tag = uuid.uuid4().hex[:6]
    create_payload = {
        "equipment_name": f"Lifecycle Central Equipment {tag}",
        "equipment_code": f"EQ-LIFECYCLE-{tag}",
        "project_id": None,
        "condition": EquipmentCondition.GOOD.value,
        "rental_cost": "0.00",
    }

    # 1. Create central equipment
    res = client.post("/api/v1/equipment", json=create_payload)
    assert res.status_code == 201
    eq_id = res.json()["id"]
    data["created_equipment_ids"].append(eq_id)

    # 2. Retrieve individually
    res = client.get(f"/api/v1/equipment/{eq_id}")
    assert res.status_code == 200
    retrieved = res.json()
    assert retrieved["id"] == eq_id
    assert retrieved["project_id"] is None
    assert retrieved["status"] == "AVAILABLE"

    # Verify DB ownership
    async def _verify_ownership():
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, eq_id)
            assert eq.company_id == 1

    asyncio.run(_verify_ownership())

    # 3. List equipment
    res = client.get("/api/v1/equipment?limit=100")
    assert res.status_code == 200
    list_items = res.json().get("items", [])
    found = any(item["id"] == eq_id for item in list_items)
    assert found, "Created central equipment must appear in listing"

    # 4. Cross-tenant retrieval blocked
    override_user(data["user_b"])
    res = client.get(f"/api/v1/equipment/{eq_id}")
    assert res.status_code in (403, 404)

    # 5. Allocate to Company A project
    override_user(data["user_a"])
    res = client.post(
        "/api/v1/equipment/allocate",
        json={
            "equipment_ids": [eq_id],
            "project_id": data["proj_a_id"],
        },
    )
    assert res.status_code == 200
    alloc_data = res.json()
    assert eq_id in alloc_data.get("equipment_ids", [])

    # Verify project_id updated and status is IN_PROJECT
    res = client.get(f"/api/v1/equipment/{eq_id}")
    assert res.status_code == 200
    assert res.json()["project_id"] == data["proj_a_id"]
    assert res.json()["status"] == "IN_PROJECT"

    # 6. Deallocate back to central pool
    res = client.put(
        "/api/v1/equipment/deallocate",
        json={
            "equipment_ids": [eq_id],
            "project_id": data["proj_a_id"],
        },
    )
    assert res.status_code == 200
    dealloc_data = res.json()
    assert eq_id in dealloc_data.get("deallocated_ids", [])

    # Verify project_id is None again and status returned to AVAILABLE
    res = client.get(f"/api/v1/equipment/{eq_id}")
    assert res.status_code == 200
    assert res.json()["project_id"] is None
    assert res.json()["status"] == "AVAILABLE"
