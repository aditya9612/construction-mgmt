import asyncio
import io
import uuid
import pytest
from datetime import date, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
import openpyxl
import fitz
from sqlalchemy import select

import app.main
from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_current_active_user
from app.models.user import User
from app.models.project import Project
from app.models.equipment import (
    Equipment,
    EquipmentUsage,
    EquipmentMaintenance,
    EquipmentRental,
    EquipmentPurchase,
)
from app.core.enums import EquipmentStatus, EquipmentCondition, PurchaseType

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
def seed_export_test_data():
    """
    Seeds isolated, deterministic test records for Company A (id=1) and Company B (id=2).
    """
    async def _setup():
        async with AsyncSessionLocal() as db:
            # Users
            user_a = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
            user_b = await db.scalar(select(User).where(User.company_id == 2, User.role == "Admin").limit(1))
            user_super = await db.scalar(select(User).where(User.is_super_admin == True).limit(1))
            user_no_perm = await db.scalar(select(User).where(User.company_id == 1, User.role == "Labour").limit(1))

            if not user_a or not user_b or not user_super:
                raise RuntimeError("Required test users (Company 1 Admin, Company 2 Admin, SuperAdmin) not found in DB")

            # Projects
            proj_a = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
            if not proj_a:
                proj_a = Project(company_id=1, project_name=f"Proj A Export {uuid.uuid4().hex[:6]}", status="IN_PROGRESS")
                db.add(proj_a)
                await db.flush()

            proj_b = await db.scalar(select(Project).where(Project.company_id == 2).limit(1))
            if not proj_b:
                proj_b = Project(company_id=2, project_name=f"Proj B Export {uuid.uuid4().hex[:6]}", status="IN_PROGRESS")
                db.add(proj_b)
                await db.flush()

            tag = uuid.uuid4().hex[:6]

            # Company A Equipment & Data
            eq_a = Equipment(
                company_id=1,
                project_id=proj_a.id,
                equipment_name=f"Company A Loader {tag}",
                equipment_code=f"EQ-A-{tag}",
                status=EquipmentStatus.IN_PROJECT,
                condition=EquipmentCondition.GOOD,
                rental_cost=Decimal("250.00"),
                working_hours=Decimal("15.0"),
                fuel_used=Decimal("30.0"),
            )
            db.add(eq_a)
            await db.flush()

            usage_a = EquipmentUsage(
                equipment_id=eq_a.id,
                working_hours=Decimal("8.0"),
                fuel_used=Decimal("12.0"),
                usage_date=date.today() - timedelta(days=2),
                notes=f"USAGE-CO-A-EXCLUSIVE-{tag}",
            )
            maint_a = EquipmentMaintenance(
                equipment_id=eq_a.id,
                project_id=proj_a.id,
                description=f"MAINT-CO-A-EXCLUSIVE-{tag}",
                maintenance_date=date.today() - timedelta(days=1),
                next_maintenance_date=date.today() + timedelta(days=30),
                cost=Decimal("750.00"),
                is_completed=False,
            )
            rental_a = EquipmentRental(
                equipment_id=eq_a.id,
                project_id=proj_a.id,
                client_name=f"CLIENT-A-EXCLUSIVE-{tag}",
                start_date=date.today() - timedelta(days=5),
                end_date=date.today() + timedelta(days=5),
                rental_cost=Decimal("1500.00"),
                notes=f"RENTAL-A-NOTES-{tag}",
            )
            purchase_a = EquipmentPurchase(
                project_id=proj_a.id,
                asset_id=eq_a.id,
                purchase_type=PurchaseType.NEW,
                purchase_date=date.today() - timedelta(days=20),
                vendor_name=f"VENDOR-A-EXCLUSIVE-{tag}",
                invoice_number=f"INV-A-EXCLUSIVE-{tag}",
                quantity=1,
                unit_price=Decimal("12000.00"),
                total_amount=Decimal("12000.00"),
            )

            # Company B Equipment & Data (LEAK PROBE DATA)
            eq_b = Equipment(
                company_id=2,
                project_id=proj_b.id,
                equipment_name=f"Company B Crane {tag}",
                equipment_code=f"EQ-B-{tag}",
                status=EquipmentStatus.IN_PROJECT,
                condition=EquipmentCondition.GOOD,
                rental_cost=Decimal("500.00"),
                working_hours=Decimal("25.0"),
                fuel_used=Decimal("50.0"),
            )
            db.add(eq_b)
            await db.flush()

            usage_b = EquipmentUsage(
                equipment_id=eq_b.id,
                working_hours=Decimal("10.0"),
                fuel_used=Decimal("20.0"),
                usage_date=date.today() - timedelta(days=1),
                notes=f"USAGE-CO-B-LEAK-PROBE-{tag}",
            )
            maint_b = EquipmentMaintenance(
                equipment_id=eq_b.id,
                project_id=proj_b.id,
                description=f"MAINT-CO-B-LEAK-PROBE-{tag}",
                maintenance_date=date.today() - timedelta(days=3),
                next_maintenance_date=date.today() + timedelta(days=60),
                cost=Decimal("9999.00"),
                is_completed=False,
            )
            rental_b = EquipmentRental(
                equipment_id=eq_b.id,
                project_id=proj_b.id,
                client_name=f"CLIENT-B-LEAK-PROBE-{tag}",
                start_date=date.today() - timedelta(days=4),
                end_date=date.today() + timedelta(days=4),
                rental_cost=Decimal("8888.00"),
                notes=f"RENTAL-B-NOTES-{tag}",
            )
            purchase_b = EquipmentPurchase(
                project_id=proj_b.id,
                asset_id=eq_b.id,
                purchase_type=PurchaseType.NEW,
                purchase_date=date.today() - timedelta(days=15),
                vendor_name=f"VENDOR-B-LEAK-PROBE-{tag}",
                invoice_number=f"INV-B-LEAK-PROBE-{tag}",
                quantity=2,
                unit_price=Decimal("45000.00"),
                total_amount=Decimal("90000.00"),
            )

            db.add_all([usage_a, maint_a, rental_a, purchase_a, usage_b, maint_b, rental_b, purchase_b])
            await db.commit()

            data = {
                "user_a": user_a,
                "user_b": user_b,
                "user_super": user_super,
                "user_no_perm": user_no_perm,
                "eq_a_id": eq_a.id,
                "eq_b_id": eq_b.id,
                "tag": tag,
                "maint_b_desc": f"MAINT-CO-B-LEAK-PROBE-{tag}",
                "rental_b_client": f"CLIENT-B-LEAK-PROBE-{tag}",
                "purchase_b_vendor": f"VENDOR-B-LEAK-PROBE-{tag}",
                "purchase_b_invoice": f"INV-B-LEAK-PROBE-{tag}",
                "usage_b_notes": f"USAGE-CO-B-LEAK-PROBE-{tag}",
                "maint_a_desc": f"MAINT-CO-A-EXCLUSIVE-{tag}",
                "rental_a_client": f"CLIENT-A-EXCLUSIVE-{tag}",
                "purchase_a_vendor": f"VENDOR-A-EXCLUSIVE-{tag}",
                "purchase_a_invoice": f"INV-A-EXCLUSIVE-{tag}",
            }
            cleanup_ids = {
                "eq_a": eq_a.id,
                "eq_b": eq_b.id,
                "usage_a": usage_a.id,
                "usage_b": usage_b.id,
                "maint_a": maint_a.id,
                "maint_b": maint_b.id,
                "rental_a": rental_a.id,
                "rental_b": rental_b.id,
                "purchase_a": purchase_a.id,
                "purchase_b": purchase_b.id,
            }
            return data, cleanup_ids

    test_data, cleanup_ids = asyncio.run(_setup())

    yield test_data

    # Teardown
    async def _teardown():
        async with AsyncSessionLocal() as db:
            for model, mid in [
                (EquipmentUsage, cleanup_ids["usage_a"]),
                (EquipmentUsage, cleanup_ids["usage_b"]),
                (EquipmentMaintenance, cleanup_ids["maint_a"]),
                (EquipmentMaintenance, cleanup_ids["maint_b"]),
                (EquipmentRental, cleanup_ids["rental_a"]),
                (EquipmentRental, cleanup_ids["rental_b"]),
                (EquipmentPurchase, cleanup_ids["purchase_a"]),
                (EquipmentPurchase, cleanup_ids["purchase_b"]),
                (Equipment, cleanup_ids["eq_a"]),
                (Equipment, cleanup_ids["eq_b"]),
            ]:
                obj = await db.get(model, mid)
                if obj:
                    await db.delete(obj)
            await db.commit()

    asyncio.run(_teardown())


# =============================================================================
# TEST 1: EXCEL CROSS-TENANT ISOLATION
# =============================================================================
def test_excel_cross_tenant_isolation(seed_export_test_data):
    """
    Company A requests full Excel export without equipment_id.
    Asserts Company A records exist and NO Company B values appear anywhere in workbook.
    """
    user_a = seed_export_test_data["user_a"]
    override_user(user_a)

    response = client.get("/api/v1/equipment/reports/excel")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    # Load workbook and inspect all cells
    wb = openpyxl.load_workbook(io.BytesIO(response.content))
    all_cell_texts = []
    for sheetname in wb.sheetnames:
        sheet = wb[sheetname]
        for row in sheet.iter_rows(values_only=True):
            for cell in row:
                if cell is not None:
                    all_cell_texts.append(str(cell))

    workbook_content = " ".join(all_cell_texts)

    # 1. Company A data MUST be present
    assert seed_export_test_data["maint_a_desc"] in workbook_content, "Company A maintenance missing from Excel"
    assert seed_export_test_data["rental_a_client"] in workbook_content, "Company A rental missing from Excel"
    assert seed_export_test_data["purchase_a_vendor"] in workbook_content, "Company A vendor missing from Excel"
    assert seed_export_test_data["purchase_a_invoice"] in workbook_content, "Company A invoice missing from Excel"

    # 2. Company B data MUST NOT appear anywhere
    assert seed_export_test_data["maint_b_desc"] not in workbook_content, "LEAK: Company B maintenance found in Excel!"
    assert seed_export_test_data["rental_b_client"] not in workbook_content, "LEAK: Company B rental client found in Excel!"
    assert seed_export_test_data["purchase_b_vendor"] not in workbook_content, "LEAK: Company B vendor found in Excel!"
    assert seed_export_test_data["purchase_b_invoice"] not in workbook_content, "LEAK: Company B invoice found in Excel!"
    assert seed_export_test_data["usage_b_notes"] not in workbook_content, "LEAK: Company B usage notes found in Excel!"


# =============================================================================
# TEST 2: PDF CROSS-TENANT ISOLATION
# =============================================================================
def test_pdf_cross_tenant_isolation(seed_export_test_data):
    """
    Company A requests full PDF export without equipment_id.
    Asserts Company A records exist and NO Company B values appear anywhere in PDF document.
    """
    user_a = seed_export_test_data["user_a"]
    override_user(user_a)

    response = client.get("/api/v1/equipment/reports/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    # Extract all text from PDF using PyMuPDF
    doc = fitz.open(stream=response.content, filetype="pdf")
    pdf_text = "\n".join(page.get_text() for page in doc)
    # ReportLab wraps long hyphenated strings across lines inside narrow table cells;
    # also check compact text with newlines removed
    pdf_text_compact = pdf_text.replace("\n", "")

    # 1. Company A data MUST be present
    assert (
        seed_export_test_data["maint_a_desc"] in pdf_text
        or seed_export_test_data["maint_a_desc"] in pdf_text_compact
    ), "Company A maintenance missing from PDF"
    assert (
        seed_export_test_data["rental_a_client"] in pdf_text
        or seed_export_test_data["rental_a_client"] in pdf_text_compact
    ), "Company A rental missing from PDF"
    assert (
        seed_export_test_data["purchase_a_vendor"] in pdf_text
        or seed_export_test_data["purchase_a_vendor"] in pdf_text_compact
    ), "Company A vendor missing from PDF"
    assert (
        seed_export_test_data["purchase_a_invoice"] in pdf_text
        or seed_export_test_data["purchase_a_invoice"] in pdf_text_compact
    ), "Company A invoice missing from PDF"

    # 2. Company B data MUST NOT appear anywhere
    assert seed_export_test_data["maint_b_desc"] not in pdf_text_compact, "LEAK: Company B maintenance found in PDF!"
    assert seed_export_test_data["rental_b_client"] not in pdf_text_compact, "LEAK: Company B rental client found in PDF!"
    assert seed_export_test_data["purchase_b_vendor"] not in pdf_text_compact, "LEAK: Company B vendor found in PDF!"
    assert seed_export_test_data["purchase_b_invoice"] not in pdf_text_compact, "LEAK: Company B invoice found in PDF!"


# =============================================================================
# TEST 3: CROSS-TENANT SINGLE EQUIPMENT IDOR
# =============================================================================
def test_cross_tenant_single_equipment_idor(seed_export_test_data):
    """
    Company A attempts to export single equipment belonging to Company B.
    Both Excel and PDF endpoints must reject with 404 Not Found.
    """
    user_a = seed_export_test_data["user_a"]
    eq_b_id = seed_export_test_data["eq_b_id"]
    override_user(user_a)

    # Excel export with foreign equipment_id
    res_excel = client.get(f"/api/v1/equipment/reports/excel?equipment_id={eq_b_id}")
    assert res_excel.status_code == 404, f"Expected 404 for cross-tenant Excel, got {res_excel.status_code}"

    # PDF export with foreign equipment_id
    res_pdf = client.get(f"/api/v1/equipment/reports/pdf?equipment_id={eq_b_id}")
    assert res_pdf.status_code == 404, f"Expected 404 for cross-tenant PDF, got {res_pdf.status_code}"


# =============================================================================
# TEST 4: SAME-TENANT EXPORT INTEGRITY
# =============================================================================
def test_same_tenant_export_integrity(seed_export_test_data):
    """
    Company A exports own single equipment.
    Verifies HTTP 200 and presence of all associated sub-records.
    """
    user_a = seed_export_test_data["user_a"]
    eq_a_id = seed_export_test_data["eq_a_id"]
    override_user(user_a)

    # Excel export for own equipment
    res_excel = client.get(f"/api/v1/equipment/reports/excel?equipment_id={eq_a_id}")
    assert res_excel.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(res_excel.content))
    content_excel = " ".join(
        str(cell)
        for s in wb.worksheets
        for row in s.iter_rows(values_only=True)
        for cell in row
        if cell is not None
    )
    assert seed_export_test_data["maint_a_desc"] in content_excel
    assert seed_export_test_data["rental_a_client"] in content_excel

    # PDF export for own equipment
    res_pdf = client.get(f"/api/v1/equipment/reports/pdf?equipment_id={eq_a_id}")
    assert res_pdf.status_code == 200
    doc = fitz.open(stream=res_pdf.content, filetype="pdf")
    pdf_text = "\n".join(page.get_text() for page in doc)
    assert seed_export_test_data["maint_a_desc"] in pdf_text
    assert seed_export_test_data["rental_a_client"] in pdf_text


# =============================================================================
# TEST 5: SUPERADMIN EXISTING BEHAVIOR REGRESSION (403 PRESERVED)
# =============================================================================
def test_superadmin_existing_behavior_regression(seed_export_test_data):
    """
    SuperAdmin without company_id must preserve existing audited 403 Forbidden behavior.
    """
    user_super = seed_export_test_data["user_super"]
    override_user(user_super)

    res_excel = client.get("/api/v1/equipment/reports/excel")
    assert res_excel.status_code == 403
    assert "Super Admin cannot export reports in standard equipment API" in res_excel.json()["detail"]

    res_pdf = client.get("/api/v1/equipment/reports/pdf")
    assert res_pdf.status_code == 403
    assert "Super Admin cannot export reports in standard equipment API" in res_pdf.json()["detail"]


# =============================================================================
# TEST 6: RBAC REGRESSION (401 AND 403)
# =============================================================================
def test_rbac_regression(seed_export_test_data):
    """
    Verify RBAC guards:
    - Unauthenticated request returns 401 Unauthorized.
    - Authenticated user without equipment.export returns 403 Forbidden.
    """
    # 1. Unauthenticated
    override_user(None)
    res_unauth_excel = client.get("/api/v1/equipment/reports/excel")
    assert res_unauth_excel.status_code == 401

    res_unauth_pdf = client.get("/api/v1/equipment/reports/pdf")
    assert res_unauth_pdf.status_code == 401

    # 2. Authenticated user without equipment.export (Labour)
    user_no_perm = seed_export_test_data["user_no_perm"]
    if user_no_perm:
        override_user(user_no_perm)
        res_no_perm_excel = client.get("/api/v1/equipment/reports/excel")
        assert res_no_perm_excel.status_code == 403

        res_no_perm_pdf = client.get("/api/v1/equipment/reports/pdf")
        assert res_no_perm_pdf.status_code == 403
