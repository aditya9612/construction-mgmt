import pytest
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.final_measurement import FinalMeasurement
from tests.api.test_rbac_phase2_batch_n import setup_batch_n_data


@pytest.mark.asyncio
async def test_create_measurement_valid_total_below_measured_quantity():
    """1. Create measurement succeeds when certified + rejected < measured."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 100.0,
                    "approved_rate": 50.0,
                    "measured_qty": 100.0,
                    "certified_qty": 60.0,
                    "rejected_qty": 20.0,
                },
            )
            assert res.status_code == 200
            meas_id = res.json()["id"]

        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert obj is not None
            assert Decimal(str(obj.measured_qty)) == Decimal("100.000")
            assert Decimal(str(obj.certified_qty)) == Decimal("60.000")
            assert Decimal(str(obj.rejected_qty)) == Decimal("20.000")


@pytest.mark.asyncio
async def test_create_measurement_valid_total_equal_measured_quantity():
    """2. Create measurement succeeds when certified + rejected == measured."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 100.0,
                    "approved_rate": 50.0,
                    "measured_qty": 100.0,
                    "certified_qty": 70.0,
                    "rejected_qty": 30.0,
                },
            )
            assert res.status_code == 200
            meas_id = res.json()["id"]

        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert obj is not None
            assert Decimal(str(obj.certified_qty)) + Decimal(str(obj.rejected_qty)) == Decimal(str(obj.measured_qty))


@pytest.mark.asyncio
async def test_create_measurement_zero_quantities():
    """3. Create measurement succeeds with zero quantities."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 10.0,
                    "approved_rate": 50.0,
                    "measured_qty": 0.0,
                    "certified_qty": 0.0,
                    "rejected_qty": 0.0,
                },
            )
            assert res.status_code == 200
            assert res.json()["measured_qty"] == 0.0


@pytest.mark.asyncio
async def test_create_measurement_invalid_total_exceeding_measured_rejected():
    """4. Create measurement fails (422) with exact message when certified + rejected > measured."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 100.0,
                    "approved_rate": 50.0,
                    "measured_qty": 100.0,
                    "certified_qty": 80.0,
                    "rejected_qty": 30.0,
                },
            )
            assert res.status_code == 422
            assert "Certified Qty and Rejected Qty cannot exceed Measured Qty" in res.text

        # Verify no measurement record was created with these invalid quantities
        async with AsyncSessionLocal() as db:
            objs = (
                await db.execute(
                    select(FinalMeasurement).where(
                        FinalMeasurement.project_id == proj_id,
                        FinalMeasurement.certified_qty == 80.0,
                        FinalMeasurement.rejected_qty == 30.0,
                    )
                )
            ).scalars().all()
            assert len(objs) == 0, "Invalid measurement must not be persisted in database"


@pytest.mark.asyncio
async def test_create_measurement_negative_quantities_rejected():
    """5. Create measurement fails (422) when negative quantities are supplied."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for field in ["measured_qty", "certified_qty", "rejected_qty"]:
                payload = {
                    "project_id": proj_id,
                    "final_area": 100.0,
                    "approved_rate": 50.0,
                    "measured_qty": 100.0,
                    "certified_qty": 50.0,
                    "rejected_qty": 10.0,
                }
                payload[field] = -5.0
                res = await ac.post("/api/v1/measurements", headers=headers, json=payload)
                assert res.status_code == 422
                assert "cannot be negative" in res.text.lower()


@pytest.mark.asyncio
async def test_update_measurement_invalid_certified_quantity_on_partial_update():
    """6. Updating only certified_qty fails (422) if new_certified + existing_rejected > existing_measured."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        # Create initial valid measurement: measured=100, certified=40, rejected=20
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 50.0,
                    "approved_rate": 50.0,
                    "measured_qty": 100.0,
                    "certified_qty": 40.0,
                    "rejected_qty": 20.0,
                },
            )
            assert res_create.status_code == 200
            meas_id = res_create.json()["id"]

            # Partial update certified_qty=90 (90 + 20 = 110 > 100) -> 422
            res_update = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"certified_qty": 90.0},
            )
            assert res_update.status_code == 422
            assert "Certified Qty and Rejected Qty cannot exceed Measured Qty" in res_update.text

        # Verify DB remained unchanged
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert Decimal(str(obj.certified_qty)) == Decimal("40.000")
            assert Decimal(str(obj.rejected_qty)) == Decimal("20.000")
            assert Decimal(str(obj.measured_qty)) == Decimal("100.000")


@pytest.mark.asyncio
async def test_update_measurement_invalid_rejected_quantity_on_partial_update():
    """7. Updating only rejected_qty fails (422) if existing_certified + new_rejected > existing_measured."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 50.0,
                    "approved_rate": 50.0,
                    "measured_qty": 100.0,
                    "certified_qty": 40.0,
                    "rejected_qty": 20.0,
                },
            )
            assert res_create.status_code == 200
            meas_id = res_create.json()["id"]

            # Partial update rejected_qty=70 (40 + 70 = 110 > 100) -> 422
            res_update = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"rejected_qty": 70.0},
            )
            assert res_update.status_code == 422
            assert "Certified Qty and Rejected Qty cannot exceed Measured Qty" in res_update.text

        # Verify DB remained unchanged
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert Decimal(str(obj.rejected_qty)) == Decimal("20.000")


@pytest.mark.asyncio
async def test_update_measurement_lowering_measured_quantity_below_sum_rejected():
    """8. Reducing measured_qty below existing certified + rejected fails (422)."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 50.0,
                    "approved_rate": 50.0,
                    "measured_qty": 100.0,
                    "certified_qty": 40.0,
                    "rejected_qty": 20.0,
                },
            )
            assert res_create.status_code == 200
            meas_id = res_create.json()["id"]

            # Partial update reducing measured_qty=50 (40 + 20 = 60 > 50) -> 422
            res_update = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"measured_qty": 50.0},
            )
            assert res_update.status_code == 422
            assert "Certified Qty and Rejected Qty cannot exceed Measured Qty" in res_update.text

        # Verify DB remained unchanged
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert Decimal(str(obj.measured_qty)) == Decimal("100.000")


@pytest.mark.asyncio
async def test_update_measurement_valid_partial_update_succeeds():
    """9. Valid partial update modifying certified_qty succeeds and commits changes."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 50.0,
                    "approved_rate": 50.0,
                    "measured_qty": 100.0,
                    "certified_qty": 40.0,
                    "rejected_qty": 20.0,
                },
            )
            assert res_create.status_code == 200
            meas_id = res_create.json()["id"]

            # Partial update certified_qty=70 (70 + 20 = 90 <= 100) -> 200
            res_update = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"certified_qty": 70.0},
            )
            assert res_update.status_code == 200
            assert res_update.json()["certified_qty"] == 70.0

        # Verify DB persisted the update
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert Decimal(str(obj.certified_qty)) == Decimal("70.000")
            assert Decimal(str(obj.rejected_qty)) == Decimal("20.000")
            assert Decimal(str(obj.measured_qty)) == Decimal("100.000")


@pytest.mark.asyncio
async def test_update_measurement_negative_quantity_rejected():
    """10. Updating quantity to a negative value fails (422) and leaves database unchanged."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 50.0,
                    "approved_rate": 50.0,
                    "measured_qty": 100.0,
                    "certified_qty": 40.0,
                    "rejected_qty": 20.0,
                },
            )
            assert res_create.status_code == 200
            meas_id = res_create.json()["id"]

            # Attempt negative update
            res_neg = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"certified_qty": -10.0},
            )
            assert res_neg.status_code == 422

        # Verify DB remained unchanged
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert Decimal(str(obj.certified_qty)) == Decimal("40.000")


@pytest.mark.asyncio
async def test_existing_status_restrictions_remain_intact():
    """11. Measurement in SUBMITTED status blocks general updates with status error before quantity check."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        meas_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        # Set measurement status to SUBMITTED
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            obj.status = "SUBMITTED"
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"certified_qty": 10.0},
            )
            assert res.status_code in (400, 422)
            assert "Cannot modify a measurement once it has been submitted" in res.text


@pytest.mark.asyncio
async def test_create_measurement_negative_measurement_and_certification_fields_rejected():
    """12. Creating measurement with negative area, rate, or retention fails (422) and persists nothing."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for field in ["final_area", "approved_rate", "extra_area", "extra_rate", "retention_amount"]:
                payload = {
                    "project_id": proj_id,
                    "final_area": 100.0,
                    "approved_rate": 50.0,
                    "extra_area": 10.0,
                    "extra_rate": 20.0,
                    "measured_qty": 100.0,
                    "certified_qty": 50.0,
                    "rejected_qty": 10.0,
                    "retention_amount": 50.0,
                }
                payload[field] = -15.0

                res = await ac.post("/api/v1/measurements", headers=headers, json=payload)
                assert res.status_code == 422, f"Expected 422 for negative {field}, got {res.status_code}"
                assert "cannot be negative" in res.text.lower(), f"Expected clear message for {field}: {res.text}"

        # Verify no measurement record was created with negative values
        async with AsyncSessionLocal() as db:
            objs = (
                await db.execute(
                    select(FinalMeasurement).where(
                        FinalMeasurement.project_id == proj_id,
                        (FinalMeasurement.final_area < 0)
                        | (FinalMeasurement.approved_rate < 0)
                        | (FinalMeasurement.extra_area < 0)
                        | (FinalMeasurement.extra_rate < 0)
                        | (FinalMeasurement.retention_amount < 0),
                    )
                )
            ).scalars().all()
            assert len(objs) == 0, "Invalid measurement with negative values must not be persisted in database"


@pytest.mark.asyncio
async def test_update_measurement_negative_measurement_and_certification_fields_rejected():
    """13. Updating measurement with negative area, rate, or retention fails (422) and leaves database unchanged."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 100.0,
                    "approved_rate": 50.0,
                    "extra_area": 10.0,
                    "extra_rate": 20.0,
                    "measured_qty": 100.0,
                    "certified_qty": 50.0,
                    "rejected_qty": 10.0,
                    "retention_amount": 100.0,
                },
            )
            assert res_create.status_code == 200
            meas_id = res_create.json()["id"]

            for field in ["final_area", "approved_rate", "extra_area", "extra_rate", "retention_amount"]:
                res_update = await ac.put(
                    f"/api/v1/measurements/{meas_id}",
                    headers=headers,
                    json={field: -25.0},
                )
                assert res_update.status_code == 422, f"Expected 422 on update for negative {field}, got {res_update.status_code}"
                assert "cannot be negative" in res_update.text.lower(), f"Expected clear message for {field}: {res_update.text}"

        # Verify DB remained completely unchanged with original positive values
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert Decimal(str(obj.final_area)) == Decimal("100.00")
            assert Decimal(str(obj.approved_rate)) == Decimal("50.00")
            assert Decimal(str(obj.extra_area)) == Decimal("10.00")
            assert Decimal(str(obj.extra_rate)) == Decimal("20.00")
            assert Decimal(str(obj.retention_amount)) == Decimal("100.00")
            assert Decimal(str(obj.total_area)) == Decimal("110.00")
            assert Decimal(str(obj.total_amount)) == Decimal("5200.00")


@pytest.mark.asyncio
async def test_measurement_zero_boundary_values_and_totals():
    """14. Zero values are accepted for non-negative fields and derived totals calculate correctly."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 50.0,
                    "approved_rate": 10.0,
                    "extra_area": 0.0,
                    "extra_rate": 0.0,
                    "measured_qty": 50.0,
                    "certified_qty": 0.0,
                    "rejected_qty": 0.0,
                    "retention_amount": 0.0,
                },
            )
            assert res_create.status_code == 200
            meas_id = res_create.json()["id"]
            assert res_create.json()["total_area"] == 50.0
            assert res_create.json()["total_amount"] == 500.0

            # Update extra_area to 0.0 explicitly succeeds
            res_update = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"extra_area": 0.0, "retention_amount": 0.0},
            )
            assert res_update.status_code == 200
            assert res_update.json()["total_area"] == 50.0
