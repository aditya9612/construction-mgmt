import io
import uuid
import pytest
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_roles
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus
from app.models.master_data import MaterialMaster, Unit
from app.models.material import (
    Material,
    Supplier,
    MaterialTransaction,
    MaterialLedger,
)
from app.api.material import VERSION_KEY
from app.cache.redis import get_cache_version
from app.core.default_role_permissions import assign_default_role_permissions
from app.core.enums import RateType, TransactionType
from app.core.rbac_seed import seed_permissions
from app.models.rbac import Permission, RolePermission

admin_user = User(
    id=9101,
    email="admin_bulk_mat@test.com",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)

unauthorized_user = User(
    id=9102,
    email="unauth_bulk_mat@test.com",
    role=UserRole.CLIENT.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
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
def clean_overrides():
    yield
    clear_user_override()


async def setup_test_data():
    async with AsyncSessionLocal() as db:
        has_rbac = await db.scalar(
            select(RolePermission.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(Permission.code == "materials.create")
            .limit(1)
        )
        if not has_rbac:
            await seed_permissions(db)
            await assign_default_role_permissions(db)
            await db.commit()

        c1 = await db.get(Company, 1)
        if not c1:
            c1 = Company(id=1, name="Bulk Test Company", is_active=True)
            db.add(c1)

        owner = await db.scalar(select(Owner).where(Owner.company_id == 1))
        if not owner:
            owner = Owner(
                owner_code=f"OWN-BLK-{uuid.uuid4().hex[:6]}",
                owner_name="Owner Bulk",
                mobile=f"98{uuid.uuid4().int % 100000000:08d}",
                email="owner_bulk@test.com",
                company_id=1,
            )
            db.add(owner)
            await db.flush()

        usr = await db.get(User, admin_user.id)
        if not usr:
            from app.core.security import get_password_hash
            usr = User(
                id=admin_user.id,
                email=admin_user.email,
                hashed_password=get_password_hash("TestPassword123!"),
                full_name="Admin Bulk",
                role=UserRole.ADMIN.value,
                is_active=True,
                is_super_admin=False,
                company_id=1,
            )
            db.add(usr)
            await db.flush()

        suffix = uuid.uuid4().hex[:6]
        proj = Project(
            business_id=f"PRJ-BLK-{suffix}",
            company_id=1,
            project_name=f"Project Bulk {suffix}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(proj)
        await db.flush()

        unit = await db.scalar(select(Unit).where(Unit.name == "Bags"))
        if not unit:
            unit = Unit(name="Bags", unique_code="BAG")
            db.add(unit)
            await db.flush()

        mm1 = MaterialMaster(
            company_id=1,
            name=f"Cement Grade A {suffix}",
            category="Cement",
            unit_id=unit.id,
            hsn_code="25232930",
            is_active=True,
        )
        mm2 = MaterialMaster(
            company_id=1,
            name=f"Steel TMT Bar {suffix}",
            category="Steel",
            unit_id=unit.id,
            hsn_code="72142090",
            is_active=True,
        )
        db.add(mm1)
        db.add(mm2)
        await db.flush()

        supplier1 = Supplier(
            company_id=1,
            supplier_name=f"Supplier Alpha {suffix}",
            contact_person="Alice",
            phone_email="alpha@test.com",
            gst_number="27AAAAA0000A1Z5",
            address="Zone 1",
            is_deleted=False,
        )
        supplier2 = Supplier(
            company_id=1,
            supplier_name=f"Supplier Beta {suffix}",
            contact_person="Bob",
            phone_email="beta@test.com",
            gst_number="27BBBBB0000B1Z5",
            address="Zone 2",
            is_deleted=False,
        )
        db.add(supplier1)
        db.add(supplier2)
        await db.flush()

        await db.commit()
        return {
            "project_id": proj.id,
            "unit_id": unit.id,
            "mm1_id": mm1.id,
            "mm2_id": mm2.id,
            "supplier1_id": supplier1.id,
            "supplier2_id": supplier2.id,
        }


def make_excel_bytes(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Materials"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_csv_bytes(rows: list[list]) -> bytes:
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    for r in rows:
        writer.writerow(r)
    return output.getvalue().encode("utf-8")


# ==============================================================================
# TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_download_bulk_template_xlsx():
    override_user(admin_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/materials/bulk-upload/template?format=xlsx")
        assert res.status_code == 200
        assert "application/vnd.openxmlformats" in res.headers["content-type"]
        assert "attachment; filename=material_bulk_upload_template.xlsx" in res.headers["content-disposition"]

        wb = load_workbook(filename=io.BytesIO(res.content))
        assert "Materials" in wb.sheetnames
        assert "Instructions" in wb.sheetnames
        ws = wb["Materials"]
        assert ws.cell(row=1, column=1).value == "Project ID"
        assert ws.cell(row=1, column=2).value == "Material Master ID"


@pytest.mark.asyncio
async def test_download_bulk_template_csv():
    override_user(admin_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/materials/bulk-upload/template?format=csv")
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        text = res.content.decode("utf-8")
        assert "Project ID,Material Master ID,Supplier ID" in text


@pytest.mark.asyncio
async def test_preview_and_commit_valid_xlsx():
    override_user(admin_user)
    data = await setup_test_data()

    headers = [
        "Project ID",
        "Material Master ID",
        "Supplier ID",
        "Purchase Rate",
        "Rate Type",
        "Quantity Purchased",
        "Payment Given",
        "Minimum Stock Level",
    ]
    row1 = [data["project_id"], data["mm1_id"], data["supplier1_id"], 380.00, "FIXED", 100, 15000.00, 10]
    row2 = [data["project_id"], data["mm2_id"], data["supplier2_id"], 65.50, "PER_KG", 500, 20000.00, 25]

    xlsx_bytes = make_excel_bytes([headers, row1, row2])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Step 1: Preview
        files = {"file": ("materials.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        assert res.status_code == 200
        body = res.json()
        assert body["total_rows"] == 2
        assert body["valid_rows"] == 2
        assert body["error_rows"] == 0
        assert body["can_commit"] is True
        assert body["import_token"] is not None
        assert len(body["preview_data"]) == 2

        token = body["import_token"]

        # Step 2: Commit
        commit_res = await ac.post("/api/v1/materials/bulk-upload/commit", json={"import_token": token})
        assert commit_res.status_code == 201
        commit_body = commit_res.json()
        assert commit_body["imported_count"] == 2
        assert len(commit_body["materials"]) == 2

        mat1_code = commit_body["materials"][0]["material_code"]
        mat2_code = commit_body["materials"][1]["material_code"]
        assert mat1_code.startswith("MAT")
        assert mat2_code.startswith("MAT")
        assert mat1_code != mat2_code

    # Verify DB records
    async with AsyncSessionLocal() as db:
        m1 = await db.scalar(select(Material).where(Material.material_code == mat1_code))
        assert m1 is not None
        assert m1.project_id == data["project_id"]
        assert m1.purchase_rate == Decimal("380.00")
        assert m1.quantity_purchased == Decimal("100.000")
        assert m1.total_amount == Decimal("38000.00")
        assert m1.remaining_stock == Decimal("100.000")
        assert m1.payment_given == Decimal("15000.00")
        assert m1.payment_pending == Decimal("23000.00")

        # Verify transaction and ledger
        tx = await db.scalar(select(MaterialTransaction).where(MaterialTransaction.material_id == m1.id))
        assert tx is not None
        assert tx.type == TransactionType.PURCHASE
        assert tx.quantity == Decimal("100.000")

        ledger = await db.scalar(select(MaterialLedger).where(MaterialLedger.material_id == m1.id))
        assert ledger is not None
        assert ledger.total_amount == Decimal("38000.00")


@pytest.mark.asyncio
async def test_token_replay_rejected():
    override_user(admin_user)
    data = await setup_test_data()

    headers = ["Project ID", "Material Master ID", "Supplier ID", "Purchase Rate", "Rate Type", "Quantity Purchased"]
    row = [data["project_id"], data["mm1_id"], data["supplier1_id"], 250.00, "FIXED", 10]
    xlsx_bytes = make_excel_bytes([headers, row])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("materials.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        token = res.json()["import_token"]

        # First commit succeeds
        commit1 = await ac.post("/api/v1/materials/bulk-upload/commit", json={"import_token": token})
        assert commit1.status_code == 201

        # Second commit with same token must fail
        commit2 = await ac.post("/api/v1/materials/bulk-upload/commit", json={"import_token": token})
        assert commit2.status_code == 400
        assert "Invalid or expired import token" in commit2.json()["detail"]


@pytest.mark.asyncio
async def test_direct_bulk_upload_csv():
    override_user(admin_user)
    data = await setup_test_data()

    headers = ["Project ID", "Material Master ID", "Supplier ID", "Purchase Rate", "Rate Type", "Quantity Purchased", "Payment Given"]
    row1 = [data["project_id"], data["mm1_id"], data["supplier1_id"], "100.00", "FIXED", "50", "2000.00"]
    row2 = [data["project_id"], data["mm2_id"], data["supplier2_id"], "200.00", "FIXED", "20", "1000.00"]
    csv_bytes = make_csv_bytes([headers, row1, row2])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("materials.csv", csv_bytes, "text/csv")}
        res = await ac.post("/api/v1/materials/bulk-upload", files=files)
        assert res.status_code == 201
        body = res.json()
        assert body["imported_count"] == 2


@pytest.mark.asyncio
async def test_preview_validation_errors_detection():
    override_user(admin_user)
    data = await setup_test_data()

    headers = [
        "Project ID",
        "Material Master ID",
        "Supplier ID",
        "Purchase Rate",
        "Rate Type",
        "Quantity Purchased",
        "Payment Given",
    ]
    # Row 2: Non-existent Project ID
    row2 = [9999999, data["mm1_id"], data["supplier1_id"], 100.00, "FIXED", 10, 0]
    # Row 3: Non-existent Material Master ID
    row3 = [data["project_id"], 9999999, data["supplier1_id"], 100.00, "FIXED", 10, 0]
    # Row 4: Non-existent Supplier ID
    row4 = [data["project_id"], data["mm1_id"], 9999999, 100.00, "FIXED", 10, 0]
    # Row 5: Negative rate
    row5 = [data["project_id"], data["mm1_id"], data["supplier1_id"], -50.00, "FIXED", 10, 0]
    # Row 6: Payment exceeds total amount
    row6 = [data["project_id"], data["mm1_id"], data["supplier1_id"], 100.00, "FIXED", 10, 5000.00]
    # Row 7: Invalid rate type
    row7 = [data["project_id"], data["mm1_id"], data["supplier1_id"], 100.00, "UNKNOWN_TYPE", 10, 0]

    xlsx_bytes = make_excel_bytes([headers, row2, row3, row4, row5, row6, row7])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("invalid_materials.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        assert res.status_code == 200
        body = res.json()
        assert body["can_commit"] is False
        assert body["import_token"] is None
        assert body["error_rows"] == 6
        assert len(body["errors"]) >= 6


@pytest.mark.asyncio
async def test_intra_file_duplicate_detection():
    override_user(admin_user)
    data = await setup_test_data()

    headers = ["Project ID", "Material Master ID", "Supplier ID", "Purchase Rate", "Rate Type"]
    # Same project, master, supplier in both rows!
    row1 = [data["project_id"], data["mm1_id"], data["supplier1_id"], 100.00, "FIXED"]
    row2 = [data["project_id"], data["mm1_id"], data["supplier1_id"], 120.00, "FIXED"]

    xlsx_bytes = make_excel_bytes([headers, row1, row2])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("dup.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        assert res.status_code == 200
        body = res.json()
        assert body["can_commit"] is False
        dup_errors = [e for e in body["errors"] if "Duplicate material row in file" in e["message"]]
        assert len(dup_errors) >= 1


@pytest.mark.asyncio
async def test_database_duplicate_detection():
    override_user(admin_user)
    data = await setup_test_data()

    # Create one material in DB first
    async with AsyncSessionLocal() as db:
        m = Material(
            material_code=f"MAT-DUP-{uuid.uuid4().hex[:4]}",
            project_id=data["project_id"],
            material_master_id=data["mm1_id"],
            supplier_id=data["supplier1_id"],
            purchase_rate=Decimal("100.00"),
            rate_type=RateType.FIXED,
            material_name="Existing Material",
            category="Test",
            unit_id=data["unit_id"],
            is_deleted=False,
        )
        db.add(m)
        await db.commit()

    headers = ["Project ID", "Material Master ID", "Supplier ID", "Purchase Rate"]
    row = [data["project_id"], data["mm1_id"], data["supplier1_id"], 150.00]
    xlsx_bytes = make_excel_bytes([headers, row])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("db_dup.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        assert res.status_code == 200
        body = res.json()
        assert body["can_commit"] is False
        db_dup_errors = [e for e in body["errors"] if "Material already exists in Project" in e["message"]]
        assert len(db_dup_errors) >= 1


@pytest.mark.asyncio
async def test_direct_upload_fails_atomically_on_error():
    override_user(admin_user)
    data = await setup_test_data()

    headers = ["Project ID", "Material Master ID", "Supplier ID", "Purchase Rate"]
    # Row 1 is valid, Row 2 is invalid (rate <= 0)
    row1 = [data["project_id"], data["mm1_id"], data["supplier1_id"], 100.00]
    row2 = [data["project_id"], data["mm2_id"], data["supplier2_id"], -50.00]
    csv_bytes = make_csv_bytes([headers, row1, row2])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("atomic_fail.csv", csv_bytes, "text/csv")}
        res = await ac.post("/api/v1/materials/bulk-upload", files=files)
        assert res.status_code == 400

    # Verify that NEITHER row was created in DB
    async with AsyncSessionLocal() as db:
        m1 = await db.scalar(
            select(Material).where(
                Material.project_id == data["project_id"],
                Material.material_master_id == data["mm1_id"],
                Material.supplier_id == data["supplier1_id"],
            )
        )
        assert m1 is None


@pytest.mark.asyncio
async def test_empty_file_rejected():
    override_user(admin_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("empty.xlsx", b"", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        assert res.status_code == 400
        assert "The uploaded file is empty" in res.json()["detail"]


@pytest.mark.asyncio
async def test_unsupported_extension_rejected():
    override_user(admin_user)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("malicious.exe", b"MZ...", "application/octet-stream")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        assert res.status_code == 400
        assert "Unsupported file format" in res.json()["detail"]


@pytest.mark.asyncio
async def test_missing_required_headers_rejected():
    override_user(admin_user)
    csv_bytes = make_csv_bytes([["ColA", "ColB"], [1, 2]])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("invalid_headers.csv", csv_bytes, "text/csv")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        assert res.status_code == 400
        assert "Missing required columns" in res.json()["detail"]


@pytest.mark.asyncio
async def test_unauthorized_user_rejected():
    override_user(unauthorized_user)
    csv_bytes = make_csv_bytes([["Project ID", "Material Master ID", "Supplier ID", "Purchase Rate"], [1, 1, 1, 100]])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("test.csv", csv_bytes, "text/csv")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_user_without_project_access_rejected():
    data = await setup_test_data()
    restricted_user = User(
        id=9103,
        email="restricted_user@test.com",
        role=UserRole.SITE_ENGINEER.value,
        is_active=True,
        is_super_admin=False,
        company_id=1,
        project_memberships=[],
    )
    override_user(restricted_user)

    headers = ["Project ID", "Material Master ID", "Supplier ID", "Purchase Rate"]
    row = [data["project_id"], data["mm1_id"], data["supplier1_id"], 100.00]
    xlsx_bytes = make_excel_bytes([headers, row])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("restricted.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        # Should be forbidden either at dependency or reported in preview errors
        if res.status_code == 200:
            body = res.json()
            assert body["can_commit"] is False
            assert any("Access denied" in e["message"] or "not assigned" in e["message"] for e in body["errors"])
        else:
            assert res.status_code in (403, 404)


@pytest.mark.asyncio
async def test_empty_rows_gracefully_skipped():
    override_user(admin_user)
    data = await setup_test_data()

    headers = ["Project ID", "Material Master ID", "Supplier ID", "Purchase Rate"]
    row1 = [data["project_id"], data["mm1_id"], data["supplier1_id"], 100.00]
    empty_row = [None, None, None, None]
    row2 = [data["project_id"], data["mm2_id"], data["supplier2_id"], 200.00]

    xlsx_bytes = make_excel_bytes([headers, row1, empty_row, row2])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("empty_rows.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        res = await ac.post("/api/v1/materials/bulk-upload/preview", files=files)
        assert res.status_code == 200
        body = res.json()
        assert body["total_rows"] == 2
        assert body["valid_rows"] == 2
        assert body["can_commit"] is True
