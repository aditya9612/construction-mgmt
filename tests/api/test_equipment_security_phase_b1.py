import asyncio
import uuid
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select, text
import app.main
from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_current_active_user, require_roles
from app.models.user import User
from app.models.company import Company
from app.models.project import Project
from app.models.equipment import Equipment
from app.core.enums import EquipmentStatus, EquipmentCondition

client = TestClient(app)

# Tenant A User (Company 1)
user_company_a = User(
    id=8001,
    email="admin_a@tenant.com",
    role="Admin",
    is_active=True,
    is_super_admin=False,
    company_id=1,
)

# Tenant B User (Company 2)
user_company_b = User(
    id=8002,
    email="admin_b@tenant.com",
    role="Admin",
    is_active=True,
    is_super_admin=False,
    company_id=2,
)

# Platform SuperAdmin (company_id=None, is_super_admin=True)
user_superadmin = User(
    id=8999,
    email="superadmin@platform.com",
    role="Super Admin",
    is_active=True,
    is_super_admin=True,
    company_id=None,
)

def override_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    def mock_require_roles(roles):
        return lambda: user
    app.dependency_overrides[require_roles] = mock_require_roles

def clear_user_override():
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def cleanup_overrides():
    yield
    clear_user_override()


@pytest.fixture(scope="module")
def seed_security_equipment():
    """
    Seeds specific test equipment records for security testing:
    - eq_a1: Assigned to Project in Company 1
    - eq_a_central: Central equipment belonging to Company 1 (project_id=None)
    - eq_b1: Assigned to Project in Company 2
    - eq_b_central: Central equipment belonging to Company 2 (project_id=None)
    - eq_null: Equipment with company_id IS NULL (unmigrated/orphan test record)
    """
    async def _setup():
        async with AsyncSessionLocal() as db:
            proj_a = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
            if not proj_a:
                proj_a = Project(
                    company_id=1,
                    project_name=f"Project A Sec {uuid.uuid4().hex[:6]}",
                    status="IN_PROGRESS",
                )
                db.add(proj_a)
                await db.flush()

            proj_b = await db.scalar(select(Project).where(Project.company_id == 2).limit(1))
            if not proj_b:
                proj_b = Project(
                    company_id=2,
                    project_name=f"Project B Sec {uuid.uuid4().hex[:6]}",
                    status="IN_PROGRESS",
                )
                db.add(proj_b)
                await db.flush()

            # 1. Company 1 assigned equipment
            eq_a1 = Equipment(
                company_id=1,
                project_id=proj_a.id,
                equipment_name="Company A Assigned Excavator",
                equipment_code=f"EQ-A1-{uuid.uuid4().hex[:6]}",
                status=EquipmentStatus.IN_PROJECT,
                condition=EquipmentCondition.GOOD,
                rental_cost=Decimal("150.00"),
                working_hours=Decimal("10.0"),
                fuel_used=Decimal("5.0"),
            )
            db.add(eq_a1)

            # 2. Company 1 central equipment (project_id=None)
            eq_a_central = Equipment(
                company_id=1,
                project_id=None,
                equipment_name="Company A Central Bulldozer",
                equipment_code=f"EQ-AC-{uuid.uuid4().hex[:6]}",
                status=EquipmentStatus.AVAILABLE,
                condition=EquipmentCondition.GOOD,
                rental_cost=Decimal("200.00"),
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
            )
            db.add(eq_a_central)

            # 3. Company 2 assigned equipment
            eq_b1 = Equipment(
                company_id=2,
                project_id=proj_b.id,
                equipment_name="Company B Assigned Crane",
                equipment_code=f"EQ-B1-{uuid.uuid4().hex[:6]}",
                status=EquipmentStatus.IN_PROJECT,
                condition=EquipmentCondition.GOOD,
                rental_cost=Decimal("300.00"),
                working_hours=Decimal("20.0"),
                fuel_used=Decimal("10.0"),
            )
            db.add(eq_b1)

            # 4. Company 2 central equipment (project_id=None)
            eq_b_central = Equipment(
                company_id=2,
                project_id=None,
                equipment_name="Company B Central Loader",
                equipment_code=f"EQ-BC-{uuid.uuid4().hex[:6]}",
                status=EquipmentStatus.AVAILABLE,
                condition=EquipmentCondition.GOOD,
                rental_cost=Decimal("120.00"),
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
            )
            db.add(eq_b_central)

            # 5. Equipment with company_id IS NULL
            eq_null = Equipment(
                company_id=None,
                project_id=None,
                equipment_name="Orphan NULL Company Roller",
                equipment_code=f"EQ-NULL-{uuid.uuid4().hex[:6]}",
                status=EquipmentStatus.AVAILABLE,
                condition=EquipmentCondition.GOOD,
                rental_cost=Decimal("80.00"),
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
            )
            db.add(eq_null)

            await db.commit()
            await db.refresh(eq_a1)
            await db.refresh(eq_a_central)
            await db.refresh(eq_b1)
            await db.refresh(eq_b_central)
            await db.refresh(eq_null)

            data = {
                "proj_a_id": proj_a.id,
                "proj_b_id": proj_b.id,
                "eq_a1_id": eq_a1.id,
                "eq_a_central_id": eq_a_central.id,
                "eq_b1_id": eq_b1.id,
                "eq_b_central_id": eq_b_central.id,
                "eq_null_id": eq_null.id,
            }
            ids = [eq_a1.id, eq_a_central.id, eq_b1.id, eq_b_central.id, eq_null.id]
            return data, ids

    test_data, cleanup_ids = asyncio.run(_setup())

    yield test_data

    # Teardown
    async def _teardown():
        async with AsyncSessionLocal() as db:
            for eq_id in cleanup_ids:
                eq = await db.get(Equipment, eq_id)
                if eq:
                    await db.delete(eq)
            await db.commit()

    asyncio.run(_teardown())


# ==============================================================================
# TEST 1 — TENANT ISOLATION
# ==============================================================================
def test_1_tenant_isolation(seed_security_equipment):
    data = seed_security_equipment
    eq_a_id = data["eq_a1_id"]
    eq_b_id = data["eq_b1_id"]

    # Company A user: sees A equipment, cannot see B equipment
    override_user(user_company_a)
    resp_a = client.get(f"/api/v1/equipment/{eq_a_id}")
    assert resp_a.status_code == 200, f"Company A user should access Company A equipment: {resp_a.text}"

    resp_b_from_a = client.get(f"/api/v1/equipment/{eq_b_id}")
    assert resp_b_from_a.status_code == 404, "Company A user must NOT access Company B equipment (expected 404)"

    # Listing for Company A: Company A equipment is present, Company B equipment is excluded
    list_a = client.get("/api/v1/equipment")
    assert list_a.status_code == 200
    item_ids_a = [item["id"] for item in list_a.json().get("items", [])]
    assert eq_a_id in item_ids_a, "Company A listing must contain Company A equipment"
    assert eq_b_id not in item_ids_a, "Company A listing must NOT contain Company B equipment"

    # Company B user: sees B equipment, cannot see A equipment
    override_user(user_company_b)
    resp_b = client.get(f"/api/v1/equipment/{eq_b_id}")
    assert resp_b.status_code == 200, "Company B user should access Company B equipment"

    resp_a_from_b = client.get(f"/api/v1/equipment/{eq_a_id}")
    assert resp_a_from_b.status_code == 404, "Company B user must NOT access Company A equipment"


# ==============================================================================
# TEST 2 — CENTRAL EQUIPMENT ISOLATION
# ==============================================================================
def test_2_central_equipment_isolation(seed_security_equipment):
    data = seed_security_equipment
    eq_a_central_id = data["eq_a_central_id"]
    eq_b_central_id = data["eq_b_central_id"]

    # Company A user can see Company A central equipment
    override_user(user_company_a)
    resp_a = client.get(f"/api/v1/equipment/{eq_a_central_id}")
    assert resp_a.status_code == 200, f"Company A user should access Company A central equipment: {resp_a.text}"
    assert resp_a.json()["project_id"] is None, "Central equipment must have project_id=None"

    # Company B user cannot see Company A central equipment
    override_user(user_company_b)
    resp_a_from_b = client.get(f"/api/v1/equipment/{eq_a_central_id}")
    assert resp_a_from_b.status_code == 404, "Company B user must NOT see Company A central equipment"

    # Company B user can see Company B central equipment
    resp_b = client.get(f"/api/v1/equipment/{eq_b_central_id}")
    assert resp_b.status_code == 200, "Company B user should access Company B central equipment"


# ==============================================================================
# TEST 3 — CROSS-COMPANY PROJECT ASSIGNMENT & MUTATION SAFETY
# ==============================================================================
def test_3_cross_company_project_assignment(seed_security_equipment):
    data = seed_security_equipment
    eq_a_central_id = data["eq_a_central_id"]
    proj_b_id = data["proj_b_id"]

    override_user(user_company_a)

    # Company A user attempts: Company A equipment -> Company B project
    resp = client.post(
        "/api/v1/equipment/allocate",
        json={
            "project_id": proj_b_id,
            "equipment_ids": [eq_a_central_id],
        },
    )
    # Expected: 403 or 404
    assert resp.status_code in (400, 403, 404), f"Cross-company allocation must be rejected: {resp.status_code}"

    # Verify zero DB mutation
    async def _verify():
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, eq_a_central_id)
            assert eq.project_id is None, "Equipment project_id MUST remain unchanged after rejected allocation"
            assert eq.company_id == 1, "Equipment company_id MUST remain Company 1"
            assert eq.status == EquipmentStatus.AVAILABLE, "Equipment status MUST remain AVAILABLE"

    asyncio.run(_verify())


# ==============================================================================
# TEST 4 — CROSS-COMPANY UPDATE & MUTATION SAFETY
# ==============================================================================
def test_4_cross_company_update(seed_security_equipment):
    data = seed_security_equipment
    eq_b_id = data["eq_b1_id"]

    override_user(user_company_a)

    # Company A user attempts to update Company B equipment
    resp = client.put(
        f"/api/v1/equipment/{eq_b_id}",
        json={
            "equipment_name": "Hacked By Company A",
            "rental_cost": 99999.00,
        },
    )
    assert resp.status_code == 404, f"Cross-company update must return 404: {resp.status_code}"

    # Verify zero mutation in DB
    async def _verify():
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, eq_b_id)
            assert eq.equipment_name != "Hacked By Company A", "Equipment name must NOT be mutated by another tenant"
            assert eq.company_id == 2, "Equipment company_id must remain Company 2"

    asyncio.run(_verify())


# ==============================================================================
# TEST 5 — CROSS-COMPANY DELETE & MUTATION SAFETY
# ==============================================================================
def test_5_cross_company_delete(seed_security_equipment):
    data = seed_security_equipment
    eq_b_id = data["eq_b1_id"]

    override_user(user_company_a)

    # Company A user attempts to delete Company B equipment
    resp = client.delete(f"/api/v1/equipment/{eq_b_id}")
    assert resp.status_code in (400, 403, 404), f"Cross-company delete must be rejected: {resp.status_code}"

    # Verify equipment was NOT deleted in DB
    async def _verify():
        async with AsyncSessionLocal() as db:
            eq = await db.get(Equipment, eq_b_id)
            assert eq is not None, "Equipment must still exist in DB"
            assert eq.is_deleted is False, "Equipment is_deleted MUST remain False"

    asyncio.run(_verify())


# ==============================================================================
# TEST 6 — SUPERADMIN PLATFORM-WIDE ACCESS & KPI
# ==============================================================================
def test_6_superadmin_access(seed_security_equipment):
    data = seed_security_equipment
    eq_a_id = data["eq_a1_id"]
    eq_b_id = data["eq_b1_id"]
    eq_a_central_id = data["eq_a_central_id"]
    eq_b_central_id = data["eq_b_central_id"]

    override_user(user_superadmin)

    # 1. SuperAdmin can get equipment across companies
    resp_a = client.get(f"/api/v1/equipment/{eq_a_id}")
    assert resp_a.status_code == 200, f"SuperAdmin should access Company A equipment: {resp_a.text}"

    resp_b = client.get(f"/api/v1/equipment/{eq_b_id}")
    assert resp_b.status_code == 200, f"SuperAdmin should access Company B equipment: {resp_b.text}"

    # 2. SuperAdmin can get central equipment across companies
    resp_ac = client.get(f"/api/v1/equipment/{eq_a_central_id}")
    assert resp_ac.status_code == 200, "SuperAdmin should access Company A central equipment"

    resp_bc = client.get(f"/api/v1/equipment/{eq_b_central_id}")
    assert resp_bc.status_code == 200, "SuperAdmin should access Company B central equipment"

    # 3. SuperAdmin can list equipment across companies
    resp_list = client.get("/api/v1/equipment")
    assert resp_list.status_code == 200
    all_ids = [item["id"] for item in resp_list.json().get("items", [])]
    assert eq_a_id in all_ids, "SuperAdmin list should include Company A equipment"
    assert eq_b_id in all_ids, "SuperAdmin list should include Company B equipment"

    # 4. SuperAdmin can access platform-wide KPI
    resp_kpi = client.get("/api/v1/equipment/kpi")
    assert resp_kpi.status_code == 200, f"SuperAdmin must access KPI: {resp_kpi.text}"
    kpi_data = resp_kpi.json()
    assert kpi_data["total_equipment"] > 0, "Platform-wide KPI should count equipment across companies"

    # 5. SuperAdmin can access filtered company KPI
    resp_kpi_a = client.get("/api/v1/equipment/kpi?company_id=1")
    assert resp_kpi_a.status_code == 200
    assert resp_kpi_a.json()["total_equipment"] >= 2, "Company 1 KPI should count at least 2 seeded equipment"


# ==============================================================================
# TEST 7 — NULL COMPANY_ID PROTECTION
# ==============================================================================
def test_7_null_company_id_leak_protection(seed_security_equipment):
    data = seed_security_equipment
    eq_null_id = data["eq_null_id"]

    # Company A user cannot get NULL company equipment
    override_user(user_company_a)
    resp_a = client.get(f"/api/v1/equipment/{eq_null_id}")
    assert resp_a.status_code == 404, "Tenant user must receive 404 for NULL company equipment"

    # Company A listing must NOT contain NULL company equipment
    list_a = client.get("/api/v1/equipment")
    item_ids_a = [item["id"] for item in list_a.json().get("items", [])]
    assert eq_null_id not in item_ids_a, "Tenant listing must NEVER leak NULL company equipment"

    # Company B user cannot get NULL company equipment
    override_user(user_company_b)
    resp_b = client.get(f"/api/v1/equipment/{eq_null_id}")
    assert resp_b.status_code == 404, "Tenant B user must receive 404 for NULL company equipment"

    # SuperAdmin CAN see NULL company equipment
    override_user(user_superadmin)
    resp_sa = client.get(f"/api/v1/equipment/{eq_null_id}")
    assert resp_sa.status_code == 200, "SuperAdmin must be able to inspect NULL company equipment"


# ==============================================================================
# TEST 8 — DIRECT QUERY ENDPOINTS & ROUTE ALIASES (Issue #38)
# ==============================================================================
def test_8_route_aliases_and_direct_queries(seed_security_equipment):
    override_user(user_company_a)

    # 1. Route Aliases parity
    # Availability
    resp_old_avail = client.get("/api/v1/equipment/eq/availability")
    resp_new_avail = client.get("/api/v1/equipment/availability")
    assert resp_old_avail.status_code == 200, "Old availability route must remain functional"
    assert resp_new_avail.status_code == 200, "New availability route must be functional"
    assert len(resp_old_avail.json()) == len(resp_new_avail.json())

    # Utilization
    resp_old_util = client.get("/api/v1/equipment/report/utilization")
    resp_new_util = client.get("/api/v1/equipment/utilization")
    assert resp_old_util.status_code == 200, "Old utilization route must remain functional"
    assert resp_new_util.status_code == 200, "New utilization route must be functional"
    assert len(resp_old_util.json()) == len(resp_new_util.json())

    # Cost Report
    resp_old_cost = client.get("/api/v1/equipment/cost/report")
    resp_new_cost = client.get("/api/v1/equipment/cost-report")
    assert resp_old_cost.status_code == 200, "Old cost report route must remain functional"
    assert resp_new_cost.status_code == 200, "New cost report route must be functional"
    assert len(resp_old_cost.json()) == len(resp_new_cost.json())
