import uuid
import pytest
from decimal import Decimal
from fastapi import HTTPException
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.api.quotation import calculate_item, validate_item_measurement_dimensions
from app.models.user import User, UserRole, ActivityLog
from app.models.company import Company
from app.models.quotation import QuotationMaster, QuotationItem, MeasurementDetail, QuotationStatus
from app.core.security import get_password_hash, create_access_token


# =============================================================================
# UNIT TESTS: calculate_item
# =============================================================================


def test_calculate_item_sqft():
    """Verify Sqft calculation: (length * width), height ignored, formula='sqft'."""
    res = calculate_item(unit="Sqft", length=10, width=20, height=0, rate=15.0)
    assert res["quantity"] == 200.0
    assert res["amount"] == 3000.0
    assert res["formula"] == "sqft"
    assert res["cubic_feet"] == 0.0


def test_calculate_item_sqm():
    """Verify Sqm calculation: (length * width * 0.092903), formula='sqm'."""
    res = calculate_item(unit="Sqm", length=10, width=20, height=None, rate=10.0)
    # 200 * 0.092903 = 18.5806 -> round to 18.58
    assert res["quantity"] == 18.58
    assert res["amount"] == 185.80
    assert res["formula"] == "sqm"


def test_calculate_item_rm():
    """Verify Rm (running meter) calculation: (length * 0.3048), formula='rm'."""
    res = calculate_item(unit="Rm", length=100, width=0, height=0, rate=10.0)
    # 100 * 0.3048 = 30.48
    assert res["quantity"] == 30.48
    assert res["amount"] == 304.80
    assert res["formula"] == "rm"


def test_calculate_item_rft():
    """Verify Rft (running feet) calculation: length, formula='rft'."""
    res = calculate_item(unit="Rft", length=50, width=0, height=0, rate=20.0)
    assert res["quantity"] == 50.0
    assert res["amount"] == 1000.0
    assert res["formula"] == "rft"


def test_calculate_item_nos():
    """Verify Nos (item count) calculation requires explicit positive quantity."""
    # Via explicit quantity field
    res = calculate_item(unit="Nos", length=0, width=0, height=0, rate=50.0, quantity=15)
    assert res["quantity"] == 15.0
    assert res["amount"] == 750.0
    assert res["formula"] == "nos"

    # Missing quantity must be rejected (cannot derive count from dimensions)
    with pytest.raises(HTTPException) as exc:
        calculate_item(unit="Nos", length=8, width=0, height=0, rate=50.0)
    assert exc.value.status_code == 400


def test_calculate_item_kg():
    """Verify Kg (kilogram weight) calculation requires explicit positive quantity."""
    res = calculate_item(unit="Kg", length=0, width=0, height=0, rate=60.0, quantity=250.5)
    assert res["quantity"] == 250.5
    assert res["amount"] == 15030.0
    assert res["formula"] == "kg"

    # Missing quantity must be rejected (cannot derive weight from dimensions)
    with pytest.raises(HTTPException) as exc:
        calculate_item(unit="Kg", length=100, width=0, height=0, rate=60.0)
    assert exc.value.status_code == 400


def test_calculate_item_ton():
    """Verify Ton (metric tonne weight) calculation requires explicit positive quantity."""
    res = calculate_item(unit="Ton", length=0, width=0, height=0, rate=65000.0, quantity=2.5)
    assert res["quantity"] == 2.5
    assert res["amount"] == 162500.0
    assert res["formula"] == "ton"

    # Missing quantity must be rejected (cannot derive weight from dimensions)
    with pytest.raises(HTTPException) as exc:
        calculate_item(unit="Ton", length=2, width=0, height=0, rate=65000.0)
    assert exc.value.status_code == 400


def test_calculate_item_ls():
    """Verify Ls (lump sum) calculation."""
    # With explicit quantity
    res = calculate_item(unit="Ls", length=0, width=0, height=0, rate=25000.0, quantity=1)
    assert res["quantity"] == 1.0
    assert res["amount"] == 25000.0
    assert res["formula"] == "ls"

    # Without quantity or length defaults to 1.0
    res_default = calculate_item(unit="Ls", length=0, width=0, height=0, rate=25000.0)
    assert res_default["quantity"] == 1.0
    assert res_default["amount"] == 25000.0
    assert res_default["formula"] == "ls"


def test_calculate_item_brass_preservation():
    """Verify brass calculation remains unchanged."""
    res = calculate_item(unit="brass", length=10, width=10, height=1, rate=1000.0)
    # 100 cu ft / 100 = 1.0 brass
    assert res["quantity"] == 1.0
    assert res["amount"] == 1000.0
    assert res["formula"] == "brass"
    assert res["brass"] == 1.0
    assert res["cubic_feet"] == 100.0


def test_calculate_item_cum_preservation():
    """Verify cum (m3) calculation remains unchanged."""
    res = calculate_item(unit="cum", length=10, width=10, height=1, rate=500.0)
    # 100 * 0.0283168 = 2.83168 -> 2.83
    assert res["quantity"] == 2.83
    assert res["amount"] == 1415.0
    assert res["formula"] == "cubic_meter"


def test_calculate_item_cft_preservation():
    """Verify cft (cubic feet) calculation remains unchanged."""
    res = calculate_item(unit="cft", length=10, width=10, height=1, rate=25.0)
    assert res["quantity"] == 100.0
    assert res["amount"] == 2500.0
    assert res["formula"] == "cubic_feet"


def test_calculate_item_aliases_and_casing():
    """Verify casing and common aliases for newly supported units."""
    for u in ["sqft", "SQFT", "Sq.Ft", "square feet", "sq ft"]:
        res = calculate_item(unit=u, length=5, width=10, height=0, rate=2.0)
        assert res["quantity"] == 50.0
        assert res["formula"] == "sqft"

    for u in ["sqm", "SQM", "sq.m", "square meter", "sq meter", "square meters"]:
        res = calculate_item(unit=u, length=10, width=10, height=0, rate=1.0)
        assert res["quantity"] == 9.29
        assert res["formula"] == "sqm"

    for u in ["rm", "RM", "running meter", "running meters", "r.m."]:
        res = calculate_item(unit=u, length=10, width=0, height=0, rate=1.0)
        assert res["quantity"] == 3.05
        assert res["formula"] == "rm"

    for u in ["nos", "NOS", "Nos", "no", "number", "numbers", "piece", "pieces", "pcs"]:
        res = calculate_item(unit=u, length=0, width=0, height=0, rate=10.0, quantity=5)
        assert res["quantity"] == 5.0
        assert res["formula"] == "nos"

    for u in ["kg", "KG", "Kg", "kgs", "kilogram", "kilograms"]:
        res = calculate_item(unit=u, length=0, width=0, height=0, rate=10.0, quantity=12.5)
        assert res["quantity"] == 12.5
        assert res["formula"] == "kg"

    for u in ["ton", "TON", "Ton", "tons", "tonne", "tonnes", "mt", "metric ton"]:
        res = calculate_item(unit=u, length=0, width=0, height=0, rate=10.0, quantity=2.0)
        assert res["quantity"] == 2.0
        assert res["formula"] == "ton"

    for u in ["ls", "LS", "Ls", "l.s.", "lump sum", "lumpsum", "lump-sum"]:
        res = calculate_item(unit=u, length=0, width=0, height=0, rate=10.0, quantity=1)
        assert res["quantity"] == 1.0
        assert res["formula"] == "ls"


def test_calculate_item_unsupported_unit_raises_400():
    """Verify genuinely unsupported units continue to raise HTTP 400."""
    with pytest.raises(HTTPException) as exc_info:
        calculate_item(unit="furlongs", length=10, width=10, height=10, rate=10.0)
    assert exc_info.value.status_code == 400
    assert "Unsupported unit: furlongs" in exc_info.value.detail


# =============================================================================
# UNIT TESTS: validate_item_measurement_dimensions
# =============================================================================


def test_dimension_validation_volumetric():
    """Volumetric units require length, width, and height > 0."""
    # Valid
    validate_item_measurement_dimensions("brass", 10, 10, 1, "Soling")
    validate_item_measurement_dimensions("cum", 5, 5, 2, "Plum Concrete")

    # Invalid: missing height
    with pytest.raises(HTTPException) as exc:
        validate_item_measurement_dimensions("brass", 10, 10, 0, "Soling")
    assert exc.value.status_code == 400


def test_dimension_validation_area():
    """Area units require length and width > 0; height may be 0 or None."""
    # Valid: height is 0 or None
    validate_item_measurement_dimensions("Sqft", 10, 20, 0, "Flooring")
    validate_item_measurement_dimensions("Sqm", 10, 20, None, "Plastering")

    # Invalid: width is 0
    with pytest.raises(HTTPException) as exc:
        validate_item_measurement_dimensions("Sqft", 10, 0, 0, "Flooring")
    assert exc.value.status_code == 400

    # Invalid: length is 0
    with pytest.raises(HTTPException) as exc:
        validate_item_measurement_dimensions("Sqm", 0, 20, 0, "Plastering")
    assert exc.value.status_code == 400


def test_dimension_validation_linear():
    """Linear units require length > 0; width and height may be 0 or None."""
    # Valid: width and height are 0 or None
    validate_item_measurement_dimensions("Rm", 50, 0, 0, "Piping")
    validate_item_measurement_dimensions("Rft", 50, None, None, "Skirting")

    # Invalid: length is 0
    with pytest.raises(HTTPException) as exc:
        validate_item_measurement_dimensions("Rm", 0, 0, 0, "Piping")
    assert exc.value.status_code == 400


def test_dimension_validation_count_and_weight():
    """Count and weight units strictly require quantity > 0 and reject dimensions without quantity."""
    # Valid via quantity
    validate_item_measurement_dimensions("Nos", 0, 0, 0, "Doors", quantity=5)
    validate_item_measurement_dimensions("Kg", None, None, None, "Steel", quantity=100.5)
    validate_item_measurement_dimensions("Ton", 0, 0, 0, "Cement", quantity=2)
    validate_item_measurement_dimensions("Ls", 0, 0, 0, "Painting", quantity=1)

    # Invalid: dimensions provided without quantity must raise 400
    with pytest.raises(HTTPException) as exc:
        validate_item_measurement_dimensions("Nos", 5, 0, 0, "Windows")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        validate_item_measurement_dimensions("Kg", 200, 0, 0, "Rebar")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        validate_item_measurement_dimensions("Ton", 2, 0, 0, "Steel")
    assert exc.value.status_code == 400

    # Invalid: quantity <= 0
    with pytest.raises(HTTPException) as exc:
        validate_item_measurement_dimensions("Nos", 0, 0, 0, "Doors", quantity=0)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        validate_item_measurement_dimensions("Kg", 0, 0, 0, "Steel", quantity=0)
    assert exc.value.status_code == 400


def test_measurement_create_schema_quantity():
    """Verify MeasurementCreate schema accepts quantity and rejects negative."""
    from app.schemas.quotation import MeasurementCreate
    from pydantic import ValidationError

    # Valid with quantity only
    m1 = MeasurementCreate(quantity=10.0)
    assert m1.quantity == 10.0

    # Valid with dimensions only
    m2 = MeasurementCreate(length=5.0, width=10.0)
    assert m2.length == 5.0
    assert m2.quantity is None

    # Invalid negative quantity
    with pytest.raises(ValidationError):
        MeasurementCreate(quantity=-1.0)


# =============================================================================
# INTEGRATION TESTS: Quotation API Endpoints
# =============================================================================


@pytest.mark.asyncio
async def test_quotation_creation_and_item_endpoints_with_units():
    """Test full cycle: create quotation with Sqft, add item with Sqm, update item with Rm."""
    comp_id = None
    admin_id = None
    client_id = None
    q_id = None

    try:
        async with AsyncSessionLocal() as db:
            uid = uuid.uuid4().hex[:8]

            # 1. Seed Company & Admin & Client
            comp = Company(name=f"UnitComp_{uid}")
            db.add(comp)
            await db.flush()
            comp_id = comp.id

            admin = User(
                full_name=f"Admin Tester {uid}",
                email=f"admintester_{uid}@test.com",
                hashed_password=get_password_hash("Secret123!"),
                company_id=comp.id,
                role=UserRole.ADMIN,
                is_active=True,
                is_super_admin=True,
            )
            client = User(
                full_name=f"Client Tester {uid}",
                email=f"clienttester_{uid}@test.com",
                hashed_password=get_password_hash("Secret123!"),
                company_id=comp.id,
                role=UserRole.CLIENT,
                is_active=True,
                is_super_admin=False,
            )
            db.add_all([admin, client])
            await db.commit()
            admin_id = admin.id
            client_id = client.id

        token = create_access_token({"sub": str(admin_id), "user_id": admin_id})
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 2. CREATE QUOTATION with Sqft item (height=0)
            create_payload = {
                "client_user_id": client_id,
                "client_name": "Unit Test Client",
                "mobile_number": "9876543210",
                "project_name": "Unit Test Project",
                "project_type": "Residential",
                "items": [
                    {
                        "item_type": "soling",
                        "title": "Flooring Item",
                        "unit": "Sqft",
                        "rate": 50.0,
                        "measurements": [
                            {"length": 20.0, "width": 10.0, "height": 0.0}
                        ],
                    }
                ],
            }

            resp = await ac.post("/api/v1/quotations/", json=create_payload, headers=headers)
            assert resp.status_code == 201, resp.text
            q_data = resp.json()
            q_id = q_data["id"]

            # Expected: 20 * 10 = 200 sqft, rate=50 -> 10,000 amount
            assert len(q_data["items"]) == 1
            item1 = q_data["items"][0]
            assert item1["unit"] == "Sqft"
            assert item1["quantity"] == 200.0
            assert item1["amount"] == 10000.0
            assert q_data["subtotal"] == 10000.0
            item1_id = item1["id"]

            # 3. ADD ITEM with Sqm (height=None / omitted)
            add_item_payload = {
                "item_type": "plum_concrete",
                "title": "Plaster Item",
                "unit": "Sqm",
                "rate": 100.0,
                "measurements": [
                    {"length": 20.0, "width": 10.0}  # height omitted
                ],
            }
            resp_add = await ac.post(f"/api/v1/quotations/{q_id}/items", json=add_item_payload, headers=headers)
            assert resp_add.status_code == 200, resp_add.text
            q_after_add = resp_add.json()
            assert len(q_after_add["items"]) == 2
            item2 = next(it for it in q_after_add["items"] if it["title"] == "Plaster Item")
            assert item2["unit"] == "Sqm"
            # 200 * 0.092903 = 18.58 sqm -> amount = 18.58 * 100 = 1858.0
            assert item2["quantity"] == 18.58
            assert item2["amount"] == 1858.0
            item2_id = item2["id"]

            # 4. UPDATE ITEM: change unit to Rm (linear, width & height omitted)
            update_item_payload = {
                "unit": "Rm",
                "rate": 200.0,
                "measurements": [
                    {"length": 100.0}
                ],
            }
            resp_upd = await ac.put(f"/api/v1/quotations/quotation-items/{item2_id}", json=update_item_payload, headers=headers)
            assert resp_upd.status_code == 200, resp_upd.text
            q_after_upd = resp_upd.json()
            updated_item = next(it for it in q_after_upd["items"] if it["id"] == item2_id)
            assert updated_item["unit"] == "Rm"
            # 100 * 0.3048 = 30.48 rm -> amount = 30.48 * 200 = 6096.0
            assert updated_item["quantity"] == 30.48
            assert updated_item["amount"] == 6096.0

            # 5. ADD ITEM with Nos (using quantity field)
            add_nos_payload = {
                "item_type": "soling",
                "title": "Door Item",
                "unit": "Nos",
                "rate": 150.0,
                "measurements": [
                    {"quantity": 10.0}
                ],
            }
            resp_nos = await ac.post(f"/api/v1/quotations/{q_id}/items", json=add_nos_payload, headers=headers)
            assert resp_nos.status_code == 200, resp_nos.text
            q_after_nos = resp_nos.json()
            item_nos = next(it for it in q_after_nos["items"] if it["title"] == "Door Item")
            assert item_nos["unit"] == "Nos"
            assert item_nos["quantity"] == 10.0
            assert item_nos["amount"] == 1500.0

            # 6. ADD ITEM with Kg (using quantity field)
            add_kg_payload = {
                "item_type": "soling",
                "title": "Steel Item",
                "unit": "Kg",
                "rate": 65.0,
                "measurements": [
                    {"quantity": 500.0}
                ],
            }
            resp_kg = await ac.post(f"/api/v1/quotations/{q_id}/items", json=add_kg_payload, headers=headers)
            assert resp_kg.status_code == 200, resp_kg.text
            q_after_kg = resp_kg.json()
            item_kg = next(it for it in q_after_kg["items"] if it["title"] == "Steel Item")
            assert item_kg["unit"] == "Kg"
            assert item_kg["quantity"] == 500.0
            assert item_kg["amount"] == 32500.0

            # 7. ADD ITEM with Ton (using quantity field)
            add_ton_payload = {
                "item_type": "soling",
                "title": "Cement Item",
                "unit": "Ton",
                "rate": 60000.0,
                "measurements": [
                    {"quantity": 2.5}
                ],
            }
            resp_ton = await ac.post(f"/api/v1/quotations/{q_id}/items", json=add_ton_payload, headers=headers)
            assert resp_ton.status_code == 200, resp_ton.text
            q_after_ton = resp_ton.json()
            item_ton = next(it for it in q_after_ton["items"] if it["title"] == "Cement Item")
            assert item_ton["unit"] == "Ton"
            assert item_ton["quantity"] == 2.5
            assert item_ton["amount"] == 150000.0

            # 8. ADD ITEM with Ls (lump sum)
            add_ls_payload = {
                "item_type": "soling",
                "title": "Electrical Lump Sum",
                "unit": "Ls",
                "rate": 25000.0,
                "measurements": [
                    {"quantity": 1.0}
                ],
            }
            resp_ls = await ac.post(f"/api/v1/quotations/{q_id}/items", json=add_ls_payload, headers=headers)
            assert resp_ls.status_code == 200, resp_ls.text
            q_after_ls = resp_ls.json()
            item_ls = next(it for it in q_after_ls["items"] if it["title"] == "Electrical Lump Sum")
            assert item_ls["unit"] == "Ls"
            assert item_ls["quantity"] == 1.0
            assert item_ls["amount"] == 25000.0

            # 9. TEST INVALID DIMENSIONS REJECTION:
            # Area item with length=0
            bad_area_payload = {
                "item_type": "soling",
                "title": "Bad Area Item",
                "unit": "Sqft",
                "rate": 10.0,
                "measurements": [{"length": 0.0, "width": 10.0}],
            }
            resp_bad = await ac.post(f"/api/v1/quotations/{q_id}/items", json=bad_area_payload, headers=headers)
            assert resp_bad.status_code == 400
            assert "Invalid measurement values" in resp_bad.text

            # 10. TEST INVALID COUNT REJECTION:
            bad_nos_payload = {
                "item_type": "soling",
                "title": "Bad Nos Item",
                "unit": "Nos",
                "rate": 10.0,
                "measurements": [{"quantity": 0.0}],
            }
            resp_bad_nos = await ac.post(f"/api/v1/quotations/{q_id}/items", json=bad_nos_payload, headers=headers)
            assert resp_bad_nos.status_code == 400
            assert "Invalid measurement values" in resp_bad_nos.text

            # 11. TEST UNSUPPORTED UNIT REJECTION:
            bad_unit_payload = {
                "item_type": "soling",
                "title": "Bad Unit Item",
                "unit": "Furlongs",
                "rate": 10.0,
                "measurements": [{"length": 10.0, "width": 10.0}],
            }
            resp_bad_unit = await ac.post(f"/api/v1/quotations/{q_id}/items", json=bad_unit_payload, headers=headers)
            assert resp_bad_unit.status_code == 400
            assert "Unsupported unit: Furlongs" in resp_bad_unit.text

    finally:
        async with AsyncSessionLocal() as db:
            if admin_id:
                await db.execute(delete(ActivityLog).where(ActivityLog.performed_by == admin_id))
            if q_id:
                q = await db.get(QuotationMaster, q_id)
                if q:
                    await db.delete(q)
            if client_id:
                cl = await db.get(User, client_id)
                if cl:
                    await db.delete(cl)
            if admin_id:
                u = await db.get(User, admin_id)
                if u:
                    await db.delete(u)
            if comp_id:
                c = await db.get(Company, comp_id)
                if c:
                    await db.delete(c)
            await db.commit()


@pytest.mark.asyncio
async def test_unit_only_update_without_measurements():
    """Verify PUT /api/v1/quotations/quotation-items/{id} with unit-only change omits measurements,
    recalculates quantity and grand total mathematically, and rejects invalid cross-family conversions."""
    comp_id = None
    admin_id = None
    client_id = None
    q_id = None

    try:
        async with AsyncSessionLocal() as db:
            uid = uuid.uuid4().hex[:8]

            comp = Company(name=f"UnitOnlyComp_{uid}")
            db.add(comp)
            await db.flush()
            comp_id = comp.id

            admin = User(
                full_name=f"Admin Tester {uid}",
                email=f"admin_uo_{uid}@test.com",
                hashed_password=get_password_hash("Secret123!"),
                company_id=comp.id,
                role=UserRole.ADMIN,
                is_active=True,
                is_super_admin=True,
            )
            client = User(
                full_name=f"Client Tester {uid}",
                email=f"client_uo_{uid}@test.com",
                hashed_password=get_password_hash("Secret123!"),
                company_id=comp.id,
                role=UserRole.CLIENT,
                is_active=True,
                is_super_admin=False,
            )
            db.add_all([admin, client])
            await db.commit()
            admin_id = admin.id
            client_id = client.id

        token = create_access_token({"sub": str(admin_id), "user_id": admin_id})
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create Quotation with Sqft item
            create_payload = {
                "client_user_id": client_id,
                "client_name": "Unit Only Client",
                "mobile_number": "9876543210",
                "project_name": "Unit Only Project",
                "project_type": "Residential",
                "items": [
                    {
                        "item_type": "soling",
                        "title": "Flooring Item",
                        "unit": "Sqft",
                        "rate": 50.0,
                        "measurements": [
                            {"length": 20.0, "width": 10.0, "height": 0.0}
                        ],
                    }
                ],
            }
            resp = await ac.post("/api/v1/quotations/", json=create_payload, headers=headers)
            assert resp.status_code == 201, resp.text
            q_data = resp.json()
            q_id = q_data["id"]
            item = q_data["items"][0]
            item_id = item["id"]
            assert item["quantity"] == 200.0
            assert item["amount"] == 10000.0
            assert q_data["subtotal"] == 10000.0

            # 2. Unit-only update: Sqft -> Sqm (valid intra-area conversion, measurements omitted)
            unit_update_payload = {"unit": "Sqm"}
            resp_upd = await ac.put(
                f"/api/v1/quotations/quotation-items/{item_id}",
                json=unit_update_payload,
                headers=headers,
            )
            assert resp_upd.status_code == 200, resp_upd.text
            q_after = resp_upd.json()
            upd_item = next(it for it in q_after["items"] if it["id"] == item_id)
            assert upd_item["unit"] == "Sqm"
            # 20 * 10 * 0.092903 = 18.5806 -> 18.58
            assert upd_item["quantity"] == 18.58
            # 18.58 * 50.0 = 929.0
            assert upd_item["amount"] == 929.0
            assert q_after["subtotal"] == 929.0
            assert q_after["grand_total"] == 929.0

            # 3. Invalid unit-only update: Sqm -> Kg (cross-family without measurements must be rejected)
            bad_family_payload = {"unit": "Kg"}
            resp_bad_fam = await ac.put(
                f"/api/v1/quotations/quotation-items/{item_id}",
                json=bad_family_payload,
                headers=headers,
            )
            assert resp_bad_fam.status_code == 400
            assert "Cannot change unit from 'Sqm' to 'Kg' without providing new measurements" in resp_bad_fam.text

            # 4. Invalid dimension unit-only update: Sqm -> cum (missing height must be rejected)
            bad_dim_payload = {"unit": "cum"}
            resp_bad_dim = await ac.put(
                f"/api/v1/quotations/quotation-items/{item_id}",
                json=bad_dim_payload,
                headers=headers,
            )
            assert resp_bad_dim.status_code == 400
            assert "Invalid measurement values" in resp_bad_dim.text

            # 5. Verify database integrity after failed updates
            resp_check = await ac.get(f"/api/v1/quotations/{q_id}", headers=headers)
            assert resp_check.status_code == 200
            item_check = next(it for it in resp_check.json()["items"] if it["id"] == item_id)
            assert item_check["unit"] == "Sqm"
            assert item_check["quantity"] == 18.58
            assert item_check["amount"] == 929.0

    finally:
        async with AsyncSessionLocal() as db:
            if admin_id:
                await db.execute(delete(ActivityLog).where(ActivityLog.performed_by == admin_id))
            if q_id:
                q = await db.get(QuotationMaster, q_id)
                if q:
                    await db.delete(q)
            if client_id:
                cl = await db.get(User, client_id)
                if cl:
                    await db.delete(cl)
            if admin_id:
                u = await db.get(User, admin_id)
                if u:
                    await db.delete(u)
            if comp_id:
                c = await db.get(Company, comp_id)
                if c:
                    await db.delete(c)
            await db.commit()


@pytest.mark.asyncio
async def test_legacy_quotation_with_null_client_user_id():
    """Verify that a legacy quotation with client_user_id=None serializes correctly
    via GET /api/v1/quotations/{id} and GET /api/v1/quotations/ without ResponseValidationError."""
    comp_id = None
    admin_id = None
    q_id = None

    try:
        async with AsyncSessionLocal() as db:
            uid = uuid.uuid4().hex[:8]

            comp = Company(name=f"NullClientComp_{uid}")
            db.add(comp)
            await db.flush()
            comp_id = comp.id

            admin = User(
                full_name=f"Admin Tester {uid}",
                email=f"admin_nc_{uid}@test.com",
                hashed_password=get_password_hash("Secret123!"),
                company_id=comp.id,
                role=UserRole.ADMIN,
                is_active=True,
                is_super_admin=True,
            )
            db.add(admin)
            await db.flush()
            admin_id = admin.id

            # Directly create legacy quotation record with client_user_id=None
            legacy_quote = QuotationMaster(
                quotation_no=f"QT-LEGACY-{uid}",
                company_id=comp.id,
                client_user_id=None,
                client_name="Legacy Client Without User ID",
                mobile_number="9876543210",
                project_name="Legacy Project",
                project_type="Residential",
                status=QuotationStatus.DRAFT,
                subtotal=0.0,
                grand_total=0.0,
                balance_due=0.0,
            )
            db.add(legacy_quote)
            await db.commit()
            q_id = legacy_quote.id

        token = create_access_token({"sub": str(admin_id), "user_id": admin_id})
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Test single quotation GET serialization with null client_user_id
            resp_single = await ac.get(f"/api/v1/quotations/{q_id}", headers=headers)
            assert resp_single.status_code == 200, resp_single.text
            data_single = resp_single.json()
            assert data_single["id"] == q_id
            assert data_single["client_user_id"] is None
            assert data_single["client_name"] == "Legacy Client Without User ID"

            # 2. Test list quotations GET serialization with null client_user_id
            resp_list = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp_list.status_code == 200, resp_list.text
            items_list = resp_list.json()
            legacy_item = next((it for it in items_list if it["id"] == q_id), None)
            assert legacy_item is not None
            assert legacy_item["client_user_id"] is None

    finally:
        async with AsyncSessionLocal() as db:
            if admin_id:
                await db.execute(delete(ActivityLog).where(ActivityLog.performed_by == admin_id))
            if q_id:
                q = await db.get(QuotationMaster, q_id)
                if q:
                    await db.delete(q)
            if admin_id:
                u = await db.get(User, admin_id)
                if u:
                    await db.delete(u)
            if comp_id:
                c = await db.get(Company, comp_id)
                if c:
                    await db.delete(c)
            await db.commit()
