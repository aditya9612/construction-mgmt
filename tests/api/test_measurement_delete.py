import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.final_measurement import FinalMeasurement
from tests.api.test_rbac_phase2_batch_n import setup_batch_n_data


@pytest.mark.asyncio
async def test_delete_measurement_success_returns_200_and_message():
    """
    1. Successful deletion of a DRAFT measurement returns HTTP 200.
    2. Response body is exactly {"message": "Measurement deleted successfully"}.
    3. The measurement record is physically deleted from the database.
    """
    async with setup_batch_n_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        meas_id = data["meas_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code == 200
            assert res.json() == {"message": "Measurement deleted successfully"}

        # Verify physical deletion in database
        async with AsyncSessionLocal() as db:
            obj_in_db = await db.get(FinalMeasurement, meas_id)
            assert obj_in_db is None, "Measurement row must be deleted from database"


@pytest.mark.asyncio
async def test_delete_measurement_rejected_status_succeeds():
    """
    Measurements in REJECTED status can also be deleted.
    Returns HTTP 200 with confirmation message and deletes DB row.
    """
    async with setup_batch_n_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        meas_id = data["meas_a1"].id

        # Update status to REJECTED
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            obj.status = "REJECTED"
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code == 200
            assert res.json() == {"message": "Measurement deleted successfully"}

        # Verify physical deletion
        async with AsyncSessionLocal() as db:
            obj_in_db = await db.get(FinalMeasurement, meas_id)
            assert obj_in_db is None


@pytest.mark.asyncio
async def test_delete_measurement_submitted_status_blocked_400():
    """
    Measurements in SUBMITTED status cannot be deleted (ValidationError -> 400).
    Database record remains untouched.
    """
    async with setup_batch_n_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        meas_id = data["meas_a2_submitted"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code in (400, 422)
            assert "Cannot modify a measurement once it has been submitted" in res.json().get("detail", "")

        # Verify record still exists in DB
        async with AsyncSessionLocal() as db:
            obj_in_db = await db.get(FinalMeasurement, meas_id)
            assert obj_in_db is not None
            assert obj_in_db.status == "SUBMITTED"


@pytest.mark.asyncio
async def test_delete_measurement_invoiced_blocked_400():
    """
    Measurements linked to an Invoice cannot be deleted.
    Returns HTTP 400 with 'Cannot delete measurement. Invoice already exists.'
    Database record remains untouched.
    """
    async with setup_batch_n_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        meas_id = data["meas_a3_invoiced"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code in (400, 422)
            assert "Invoice already exists" in res.json().get("detail", "")

        # Verify record still exists in DB
        async with AsyncSessionLocal() as db:
            obj_in_db = await db.get(FinalMeasurement, meas_id)
            assert obj_in_db is not None


@pytest.mark.asyncio
async def test_delete_measurement_cross_tenant_returns_404():
    """
    Tenant Isolation: Admin from Company A attempting to delete Company B measurement returns 404.
    Company B measurement remains intact in the database.
    """
    async with setup_batch_n_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        meas_b_id = data["meas_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/measurements/{meas_b_id}", headers=headers_a)
            assert res.status_code in (403, 404)

        # Verify Company B measurement still exists in DB
        async with AsyncSessionLocal() as db:
            obj_in_db = await db.get(FinalMeasurement, meas_b_id)
            assert obj_in_db is not None


@pytest.mark.asyncio
async def test_delete_measurement_nonexistent_returns_404():
    """
    Deleting a non-existent measurement ID returns HTTP 404 Not Found.
    """
    async with setup_batch_n_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete("/api/v1/measurements/99999999", headers=headers)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_measurement_forbidden_without_permission():
    """
    RBAC: User lacking 'measurements.delete' receives HTTP 403 Forbidden.
    """
    async with setup_batch_n_data() as data:
        token_legacy = data["tokens"]["legacy_admin_no_perm"]
        headers = {"Authorization": f"Bearer {token_legacy}"}
        meas_id = data["meas_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code == 403

        # Verify measurement not deleted
        async with AsyncSessionLocal() as db:
            obj_in_db = await db.get(FinalMeasurement, meas_id)
            assert obj_in_db is not None


def test_delete_measurement_openapi_schema():
    """
    OpenAPI Documentation: Verifies that DELETE /api/v1/measurements/{id}
    declares a 200 OK response with schema referencing MeasurementDeleteOut
    and does NOT declare 204.
    """
    openapi = app.openapi()
    meas_del = openapi.get("paths", {}).get("/api/v1/measurements/{id}", {}).get("delete", {})
    assert meas_del, "DELETE /api/v1/measurements/{id} must be registered in OpenAPI"

    responses = meas_del.get("responses", {})
    assert "200" in responses, "OpenAPI must declare 200 response for measurement deletion"
    assert "204" not in responses, "OpenAPI must NOT declare 204 for measurement deletion"

    schema = responses["200"].get("content", {}).get("application/json", {}).get("schema", {})
    ref = schema.get("$ref", "")
    assert "MeasurementDeleteOut" in ref, f"Expected MeasurementDeleteOut in OpenAPI schema, got {schema}"
