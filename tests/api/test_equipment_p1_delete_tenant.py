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
from app.models.equipment import Equipment
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
def seed_p1_delete_data():
    """
    Seeds isolated unallocated and allocated equipment for Company 1 and Company 2.
    Critically, central equipment is seeded WITHOUT any EquipmentAuditLog rows
    to test the EQ-P1-001 fix directly.
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
                proj_a = Project(company_id=1, project_name=f"Proj A Del {uuid.uuid4().hex[:6]}", status="IN_PROGRESS")
                db.add(proj_a)
                await db.flush()

            tag = uuid.uuid4().hex[:6]

            # 1. Company A Central Equipment WITHOUT audit logs
            eq_a_central = Equipment(
                company_id=1,
                project_id=None,
                equipment_name=f"Company A Central Crane {tag}",
                equipment_code=f"CRANE-A-{tag}",
                condition=EquipmentCondition.GOOD,
                status=EquipmentStatus.AVAILABLE,
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
                is_deleted=False,
            )

            # 2. Company B Central Equipment WITHOUT audit logs
            eq_b_central = Equipment(
                company_id=2,
                project_id=None,
                equipment_name=f"Company B Central Crane {tag}",
                equipment_code=f"CRANE-B-{tag}",
                condition=EquipmentCondition.GOOD,
                status=EquipmentStatus.AVAILABLE,
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
                is_deleted=False,
            )

            # 3. Company A Allocated Equipment
            eq_a_allocated = Equipment(
                company_id=1,
                project_id=proj_a.id,
                equipment_name=f"Company A Allocated Excavator {tag}",
                equipment_code=f"EXC-A-{tag}",
                condition=EquipmentCondition.GOOD,
                status=EquipmentStatus.AVAILABLE,
                working_hours=Decimal("0.0"),
                fuel_used=Decimal("0.0"),
                is_deleted=False,
            )

            db.add_all([eq_a_central, eq_b_central, eq_a_allocated])
            await db.commit()
            await db.refresh(eq_a_central)
            await db.refresh(eq_b_central)
            await db.refresh(eq_a_allocated)

            return {
                "user_a": user_a,
                "user_b": user_b,
                "user_super": user_super,
                "user_labour": user_labour,
                "eq_a_central_id": eq_a_central.id,
                "eq_b_central_id": eq_b_central.id,
                "eq_a_allocated_id": eq_a_allocated.id,
            }

    data = asyncio.run(_setup())
    yield data

    # Teardown
    async def _cleanup():
        async with AsyncSessionLocal() as db:
            for eid in [data["eq_a_central_id"], data["eq_b_central_id"], data["eq_a_allocated_id"]]:
                obj = await db.get(Equipment, eid)
                if obj:
                    await db.delete(obj)
            await db.commit()

    asyncio.run(_cleanup())


def test_delete_central_equipment_without_audit_logs_succeeds(seed_p1_delete_data):
    """
    EQ-P1-001 Fix: Company A admin can delete Company A unallocated equipment
    even when NO audit log exists.
    """
    data = seed_p1_delete_data
    override_user(data["user_a"])

    res = client.delete(f"/api/v1/equipment/{data['eq_a_central_id']}")
    assert res.status_code in [200, 204], f"Expected 200/204, got {res.status_code}: {res.text}"

    # Verify soft deleted
    async def _verify():
        async with AsyncSessionLocal() as db:
            obj = await db.get(Equipment, data["eq_a_central_id"])
            assert obj is not None
            assert obj.is_deleted is True

    asyncio.run(_verify())


def test_delete_cross_tenant_central_equipment_returns_404(seed_p1_delete_data):
    """
    Tenant isolation: Company A user cannot delete Company B central equipment -> 404.
    """
    data = seed_p1_delete_data
    override_user(data["user_a"])

    res = client.delete(f"/api/v1/equipment/{data['eq_b_central_id']}")
    assert res.status_code == 404
    assert res.json().get("detail") == "Equipment not found"


def test_delete_allocated_equipment_returns_400(seed_p1_delete_data):
    """
    Allocated equipment cannot be deleted -> 400.
    """
    data = seed_p1_delete_data
    override_user(data["user_a"])

    res = client.delete(f"/api/v1/equipment/{data['eq_a_allocated_id']}")
    assert res.status_code == 400
    assert "Cannot delete allocated equipment" in res.json().get("detail")


def test_delete_equipment_superadmin_forbidden(seed_p1_delete_data):
    """
    SuperAdmin cannot delete company equipment directly -> 403.
    """
    data = seed_p1_delete_data
    override_user(data["user_super"])

    res = client.delete(f"/api/v1/equipment/{data['eq_b_central_id']}")
    assert res.status_code == 403
    assert "Super Admin cannot delete company equipment directly" in res.json().get("detail")


def test_delete_equipment_without_permission_forbidden(seed_p1_delete_data):
    """
    User lacking equipment.delete permission -> 403.
    """
    data = seed_p1_delete_data
    if data["user_labour"]:
        override_user(data["user_labour"])
        res = client.delete(f"/api/v1/equipment/{data['eq_b_central_id']}")
        assert res.status_code == 403
