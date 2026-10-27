import uuid
import pytest
from decimal import Decimal
from pydantic import ValidationError
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

import app.models.material  # Ensure relationship mapper resolves Material
from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.master_data import MaterialMaster, Unit, LabourType, ActivityType
from app.core.dependencies import get_current_user, get_current_active_user, require_roles
from app.schemas.master_data import (
    MaterialMasterCreate,
    MaterialMasterUpdate,
    UnitCreate,
    UnitUpdate,
    LabourTypeCreate,
    LabourTypeUpdate,
    ActivityTypeCreate,
    ActivityTypeUpdate,
)


@pytest.fixture
def admin_user():
    return User(
        id=9991,
        email="admin_master_audit@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
    )


@pytest.fixture(autouse=True)
def override_auth(admin_user):
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_current_active_user] = lambda: admin_user

    def mock_require_roles(roles):
        return lambda: admin_user

    app.dependency_overrides[require_roles] = mock_require_roles
    yield
    app.dependency_overrides.clear()


async def get_or_create_test_unit():
    async with AsyncSessionLocal() as db:
        unit = await db.scalar(select(Unit).limit(1))
        if not unit:
            unit = Unit(name="TestUnitMasterAudit", unique_code="UOM-TMA-001", is_active=True)
            db.add(unit)
            await db.commit()
            await db.refresh(unit)
        return unit.id


# ==============================================================================
# 1. PURE PYDANTIC SCHEMA VALIDATION TESTS
# ==============================================================================

def test_material_master_update_schema_validation():
    # Valid name with trimming
    m1 = MaterialMasterUpdate(name="  Cement  ")
    assert m1.name == "Cement"

    # Valid name internal spaces
    m2 = MaterialMasterUpdate(name="Ready Mix Concrete")
    assert m2.name == "Ready Mix Concrete"

    # Valid Unicode name
    m3 = MaterialMasterUpdate(name="Ciment Spécial Été 52.5N")
    assert m3.name == "Ciment Spécial Été 52.5N"

    # Omitted name (None by default)
    m4 = MaterialMasterUpdate(unit_id=5)
    assert m4.name is None
    dump4 = m4.model_dump(exclude_unset=True)
    assert "name" not in dump4
    assert dump4 == {"unit_id": 5}

    # Explicit None
    m5 = MaterialMasterUpdate(name=None)
    assert m5.name is None

    # Whitespace only -> Rejected with ValueError
    with pytest.raises(ValidationError) as exc:
        MaterialMasterUpdate(name="   ")
    assert "Name cannot be empty" in str(exc.value)

    # Tabs and newlines -> Rejected
    with pytest.raises(ValidationError) as exc:
        MaterialMasterUpdate(name="\t\n  ")
    assert "Name cannot be empty" in str(exc.value)

    # Empty string -> Rejected
    with pytest.raises(ValidationError) as exc:
        MaterialMasterUpdate(name="")
    assert "Name cannot be empty" in str(exc.value)


def test_other_update_schemas_validation():
    for schema_cls in [UnitUpdate, LabourTypeUpdate, ActivityTypeUpdate]:
        # Valid trimmed
        obj = schema_cls(name="  Valid Name  ")
        assert obj.name == "Valid Name"

        # Whitespace only -> Rejected
        with pytest.raises(ValidationError) as exc:
            schema_cls(name="   ")
        assert "Name cannot be empty" in str(exc.value)

        # Empty string -> Rejected
        with pytest.raises(ValidationError) as exc:
            schema_cls(name="")
        assert "Name cannot be empty" in str(exc.value)

        # Omitted name preserved
        partial = schema_cls()
        assert partial.name is None
        assert "name" not in partial.model_dump(exclude_unset=True)


# ==============================================================================
# 2. REQUIRED 18 MATERIAL MASTER ENDPOINT SCENARIOS
# ==============================================================================

@pytest.mark.asyncio
async def test_material_master_18_scenarios():
    unit_id = await get_or_create_test_unit()
    created_material_ids = []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        try:
            # 1. Create with "Cement": accepted
            uid1 = uuid.uuid4().hex[:6]
            res1 = await ac.post("/api/v1/master/materials", json={
                "name": f"Cement_{uid1}",
                "unit_id": unit_id
            })
            assert res1.status_code == 200, res1.text
            mat1_id = res1.json()["id"]
            created_material_ids.append(mat1_id)
            assert res1.json()["name"] == f"Cement_{uid1}"

            # 2. Create with "  Test Cement  ": accepted and normalized to "Test Cement"
            uid2 = uuid.uuid4().hex[:6]
            res2 = await ac.post("/api/v1/master/materials", json={
                "name": f"  Test Cement {uid2}  ",
                "unit_id": unit_id
            })
            assert res2.status_code == 200, res2.text
            mat2_id = res2.json()["id"]
            created_material_ids.append(mat2_id)
            assert res2.json()["name"] == f"Test Cement {uid2}"

            # 3. Create with "   ": HTTP 422; no record created
            res3 = await ac.post("/api/v1/master/materials", json={
                "name": "   ",
                "unit_id": unit_id
            })
            assert res3.status_code == 422
            assert "Name cannot be empty" in str(res3.json())

            # 4. Create with tabs/newlines only: HTTP 422; no record created
            res4 = await ac.post("/api/v1/master/materials", json={
                "name": "\t\r\n  ",
                "unit_id": unit_id
            })
            assert res4.status_code == 422
            assert "Name cannot be empty" in str(res4.json())

            # 5. Create with "": HTTP 422; no record created
            res5 = await ac.post("/api/v1/master/materials", json={
                "name": "",
                "unit_id": unit_id
            })
            assert res5.status_code == 422
            assert "Name cannot be empty" in str(res5.json())

            # 6. Create with missing name: retain existing required-field behavior (HTTP 422 Field required)
            res6 = await ac.post("/api/v1/master/materials", json={
                "unit_id": unit_id
            })
            assert res6.status_code == 422
            assert "Field required" in str(res6.json())

            # 7. Update with "Cement High Grade": accepted and persisted correctly
            res7 = await ac.put(f"/api/v1/master/materials/{mat1_id}", json={
                "name": f"Cement High Grade {uid1}"
            })
            assert res7.status_code == 200, res7.text
            assert res7.json()["name"] == f"Cement High Grade {uid1}"
            async with AsyncSessionLocal() as db:
                mat1_db = await db.get(MaterialMaster, mat1_id)
                assert mat1_db.name == f"Cement High Grade {uid1}"

            # 8. Update with "   ": rejected before commit; existing database name unchanged
            res8 = await ac.put(f"/api/v1/master/materials/{mat1_id}", json={
                "name": "   "
            })
            assert res8.status_code == 422
            assert "Name cannot be empty" in str(res8.json())
            async with AsyncSessionLocal() as db:
                mat1_db = await db.get(MaterialMaster, mat1_id)
                assert mat1_db.name == f"Cement High Grade {uid1}"

            # 9. Update with "\t\n  ": rejected before commit; existing database name unchanged
            res9 = await ac.put(f"/api/v1/master/materials/{mat1_id}", json={
                "name": "\t\n  "
            })
            assert res9.status_code == 422
            assert "Name cannot be empty" in str(res9.json())
            async with AsyncSessionLocal() as db:
                mat1_db = await db.get(MaterialMaster, mat1_id)
                assert mat1_db.name == f"Cement High Grade {uid1}"

            # 10. Update with "": rejected before commit; existing database name unchanged
            res10 = await ac.put(f"/api/v1/master/materials/{mat1_id}", json={
                "name": ""
            })
            assert res10.status_code == 422
            assert "Name cannot be empty" in str(res10.json())
            async with AsyncSessionLocal() as db:
                mat1_db = await db.get(MaterialMaster, mat1_id)
                assert mat1_db.name == f"Cement High Grade {uid1}"

            # 11. Update with "  Cement High Grade  ": accepted and stored trimmed
            res11 = await ac.put(f"/api/v1/master/materials/{mat1_id}", json={
                "name": f"  Trimmed Grade {uid1}  "
            })
            assert res11.status_code == 200, res11.text
            assert res11.json()["name"] == f"Trimmed Grade {uid1}"
            async with AsyncSessionLocal() as db:
                mat1_db = await db.get(MaterialMaster, mat1_id)
                assert mat1_db.name == f"Trimmed Grade {uid1}"

            # 12. Update a different field while omitting name: preserve existing update behavior
            res12 = await ac.put(f"/api/v1/master/materials/{mat1_id}", json={
                "brand": "UltraTech Prime"
            })
            assert res12.status_code == 200, res12.text
            assert res12.json()["brand"] == "UltraTech Prime"
            assert res12.json()["name"] == f"Trimmed Grade {uid1}"
            async with AsyncSessionLocal() as db:
                mat1_db = await db.get(MaterialMaster, mat1_id)
                assert mat1_db.name == f"Trimmed Grade {uid1}"
                assert mat1_db.brand == "UltraTech Prime"

            # 13. Update with name=None: rejected before commit; existing database name unchanged
            res13 = await ac.put(f"/api/v1/master/materials/{mat1_id}", json={
                "name": None
            })
            assert res13.status_code == 422
            assert "Material Name is required" in str(res13.json())
            async with AsyncSessionLocal() as db:
                mat1_db = await db.get(MaterialMaster, mat1_id)
                assert mat1_db.name == f"Trimmed Grade {uid1}"

            # 14. Update with "Ready Mix Concrete": accepted
            res14 = await ac.put(f"/api/v1/master/materials/{mat1_id}", json={
                "name": f"Ready Mix Concrete {uid1}"
            })
            assert res14.status_code == 200, res14.text
            assert res14.json()["name"] == f"Ready Mix Concrete {uid1}"

            # 15. Create and update a valid Unicode name: accepted
            uid_u = uuid.uuid4().hex[:6]
            unicode_name = f"Ciment Spécial Été {uid_u}"
            res15_create = await ac.post("/api/v1/master/materials", json={
                "name": unicode_name,
                "unit_id": unit_id
            })
            assert res15_create.status_code == 200
            mat_u_id = res15_create.json()["id"]
            created_material_ids.append(mat_u_id)
            assert res15_create.json()["name"] == unicode_name

            res15_update = await ac.put(f"/api/v1/master/materials/{mat_u_id}", json={
                "name": f"  {unicode_name} v2  "
            })
            assert res15_update.status_code == 200
            assert res15_update.json()["name"] == f"{unicode_name} v2"

            # 16. Update a record with its existing name: duplicate checking does not reject self
            res16 = await ac.put(f"/api/v1/master/materials/{mat1_id}", json={
                "name": f"Ready Mix Concrete {uid1}"
            })
            assert res16.status_code == 200, res16.text
            assert res16.json()["name"] == f"Ready Mix Concrete {uid1}"

            # 17. Attempt to rename a record to another record's existing name: returns 422 duplicate error
            res17 = await ac.put(f"/api/v1/master/materials/{mat2_id}", json={
                "name": f"Ready Mix Concrete {uid1}"
            })
            assert res17.status_code == 422
            assert "Material already exists" in str(res17.json())

            # 18. Force an update validation failure and verify no database commit or persisted mutation occurs
            res18 = await ac.put(f"/api/v1/master/materials/{mat2_id}", json={
                "name": "     "
            })
            assert res18.status_code == 422
            async with AsyncSessionLocal() as db:
                mat2_db = await db.get(MaterialMaster, mat2_id)
                assert mat2_db.name == f"Test Cement {uid2}"  # unchanged

        finally:
            # Teardown created materials
            async with AsyncSessionLocal() as db:
                for mid in created_material_ids:
                    obj = await db.get(MaterialMaster, mid)
                    if obj:
                        await db.delete(obj)
                await db.commit()


# ==============================================================================
# 3. UNIT, LABOUR TYPE & ACTIVITY TYPE ENDPOINT TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_unit_whitespace_validation():
    uid = uuid.uuid4().hex[:6]
    created_unit_ids = []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        try:
            # Create valid
            res_c = await ac.post("/api/v1/master/units", json={"name": f"  Metric Ton {uid}  "})
            assert res_c.status_code == 200
            assert res_c.json()["name"] == f"Metric Ton {uid}"
            unit_id = res_c.json()["id"]
            created_unit_ids.append(unit_id)

            # Create whitespace only -> 422
            res_c_ws = await ac.post("/api/v1/master/units", json={"name": "   "})
            assert res_c_ws.status_code == 422

            # Update whitespace only -> 422
            res_u_ws = await ac.put(f"/api/v1/master/units/{unit_id}", json={"name": "   "})
            assert res_u_ws.status_code == 422
            async with AsyncSessionLocal() as db:
                u_db = await db.get(Unit, unit_id)
                assert u_db.name == f"Metric Ton {uid}"

            # Update valid trimmed
            res_u_valid = await ac.put(f"/api/v1/master/units/{unit_id}", json={"name": f"  Kilo Ton {uid}  "})
            assert res_u_valid.status_code == 200
            assert res_u_valid.json()["name"] == f"Kilo Ton {uid}"

            # Partial update omitting name
            res_u_partial = await ac.put(f"/api/v1/master/units/{unit_id}", json={"category": "Weight"})
            assert res_u_partial.status_code == 200
            assert res_u_partial.json()["name"] == f"Kilo Ton {uid}"
            assert res_u_partial.json()["category"] == "Weight"

        finally:
            async with AsyncSessionLocal() as db:
                for uid_val in created_unit_ids:
                    obj = await db.get(Unit, uid_val)
                    if obj:
                        await db.delete(obj)
                await db.commit()


@pytest.mark.asyncio
async def test_labour_type_whitespace_validation():
    uid = uuid.uuid4().hex[:6]
    created_ids = []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        try:
            # Create valid
            res_c = await ac.post("/api/v1/master/labour-types", json={
                "name": f"  Mason Expert {uid}  ",
                "skill_category": "Skilled",
                "default_daily_wage": "850.00"
            })
            assert res_c.status_code == 200
            assert res_c.json()["name"] == f"Mason Expert {uid}"
            lab_id = res_c.json()["id"]
            created_ids.append(lab_id)

            # Create whitespace only -> 422
            res_c_ws = await ac.post("/api/v1/master/labour-types", json={
                "name": "   ",
                "skill_category": "Skilled",
                "default_daily_wage": "850.00"
            })
            assert res_c_ws.status_code == 422

            # Update whitespace only -> 422, DB unchanged
            res_u_ws = await ac.put(f"/api/v1/master/labour-types/{lab_id}", json={"name": "   "})
            assert res_u_ws.status_code == 422
            async with AsyncSessionLocal() as db:
                l_db = await db.get(LabourType, lab_id)
                assert l_db.name == f"Mason Expert {uid}"

            # Update valid trimmed
            res_u_valid = await ac.put(f"/api/v1/master/labour-types/{lab_id}", json={"name": f"  Senior Mason {uid}  "})
            assert res_u_valid.status_code == 200
            assert res_u_valid.json()["name"] == f"Senior Mason {uid}"

            # Partial update omitting name
            res_u_partial = await ac.put(f"/api/v1/master/labour-types/{lab_id}", json={"default_daily_wage": "900.00"})
            assert res_u_partial.status_code == 200
            assert res_u_partial.json()["name"] == f"Senior Mason {uid}"
            assert Decimal(str(res_u_partial.json()["default_daily_wage"])) == Decimal("900.00")

        finally:
            async with AsyncSessionLocal() as db:
                for lid in created_ids:
                    obj = await db.get(LabourType, lid)
                    if obj:
                        await db.delete(obj)
                await db.commit()


@pytest.mark.asyncio
async def test_activity_type_whitespace_validation():
    uid = uuid.uuid4().hex[:6]
    created_ids = []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        try:
            # Create valid
            res_c = await ac.post("/api/v1/master/activity-types", json={
                "name": f"  Excavation Deep {uid}  "
            })
            assert res_c.status_code == 200
            assert res_c.json()["name"] == f"Excavation Deep {uid}"
            act_id = res_c.json()["id"]
            created_ids.append(act_id)

            # Create whitespace only -> 422
            res_c_ws = await ac.post("/api/v1/master/activity-types", json={"name": "   "})
            assert res_c_ws.status_code == 422

            # Update whitespace only -> 422, DB unchanged
            res_u_ws = await ac.put(f"/api/v1/master/activity-types/{act_id}", json={"name": "   "})
            assert res_u_ws.status_code == 422
            async with AsyncSessionLocal() as db:
                a_db = await db.get(ActivityType, act_id)
                assert a_db.name == f"Excavation Deep {uid}"

            # Update valid trimmed
            res_u_valid = await ac.put(f"/api/v1/master/activity-types/{act_id}", json={"name": f"  Deep Excavation {uid}  "})
            assert res_u_valid.status_code == 200
            assert res_u_valid.json()["name"] == f"Deep Excavation {uid}"

            # Partial update omitting name
            res_u_partial = await ac.put(f"/api/v1/master/activity-types/{act_id}", json={"category": "Civil"})
            assert res_u_partial.status_code == 200
            assert res_u_partial.json()["name"] == f"Deep Excavation {uid}"
            assert res_u_partial.json()["category"] == "Civil"

        finally:
            async with AsyncSessionLocal() as db:
                for aid in created_ids:
                    obj = await db.get(ActivityType, aid)
                    if obj:
                        await db.delete(obj)
                await db.commit()
