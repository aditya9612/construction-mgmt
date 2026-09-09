import uuid
import pytest
from datetime import date, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select, delete

from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_current_active_user
from app.models.user import User
from app.models.project import Project
from app.models.equipment import Equipment, EquipmentRental, EquipmentStatus, EquipmentCondition

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


@pytest.mark.asyncio
async def test_complete_rental_and_allocate():
    uid = uuid.uuid4().hex[:6]
    today = date.today()

    async with AsyncSessionLocal() as db:
        user_admin = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
        assert user_admin is not None

        proj = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
        assert proj is not None

        # Create equipment
        eq = Equipment(
            company_id=1,
            project_id=None,
            equipment_name=f"Comp Test Eq {uid}",
            equipment_code=f"EQ-{uid}",
            status=EquipmentStatus.RENTED,
            condition=EquipmentCondition.GOOD,
            working_hours=Decimal("0"),
            fuel_used=Decimal("0"),
            rental_cost=Decimal("0"),
        )
        db.add(eq)
        await db.flush()

        # Create rental ending today
        rental = EquipmentRental(
            equipment_id=eq.id,
            start_date=today - timedelta(days=2),
            end_date=today,
            rental_cost=Decimal("5000.00"),
            client_name="Test Client",
            is_completed=False,
        )
        db.add(rental)
        await db.commit()
        await db.refresh(eq)
        await db.refresh(rental)

        eq_id = eq.id
        rental_id = rental.id
        proj_id = proj.id

    try:
        override_user(user_admin)

        # 1. Complete rental on day of end_date (should succeed with 200, not 400)
        resp_comp = client.put(f"/api/v1/equipment/rental/{rental_id}/complete")
        assert resp_comp.status_code == 200, resp_comp.text
        data = resp_comp.json()
        assert data["status"] == "COMPLETED"
        assert data["is_completed"] is True

        # 2. Complete again should fail with 400 "Rental already completed"
        resp_comp_again = client.put(f"/api/v1/equipment/rental/{rental_id}/complete")
        assert resp_comp_again.status_code == 400
        assert "already completed" in resp_comp_again.json()["detail"].lower()

        # 3. Equipment should now be AVAILABLE
        resp_eq = client.get(f"/api/v1/equipment/{eq_id}")
        assert resp_eq.status_code == 200
        assert resp_eq.json()["status"] == "AVAILABLE"

        # 4. Equipment can now be allocated to project immediately!
        resp_alloc = client.post(
            "/api/v1/equipment/allocate",
            json={"equipment_ids": [eq_id], "project_id": proj_id},
        )
        assert resp_alloc.status_code == 200
        alloc_data = resp_alloc.json()
        assert eq_id in alloc_data["allocated_ids"]
        assert alloc_data["success_count"] == 1
        assert alloc_data["failed_count"] == 0

    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(EquipmentRental).where(EquipmentRental.id == rental_id))
            await db.execute(delete(Equipment).where(Equipment.id == eq_id))
            await db.commit()
