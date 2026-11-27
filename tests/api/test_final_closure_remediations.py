"""
Comprehensive regression tests for Final Development Closure & Release Acceptance fixes:
1. PO cancellation blocking when POSTED GRN or received quantities exist (SEC / integrity).
2. GRN cancellation batch depletion guard (multi-supplier partial batch consumption).
3. OTP HMAC secret uses configured JWT_SECRET instead of missing SECRET_KEY fallback.
4. Tenant isolation on all inventory reporting & export endpoints.
5. Combined Material Movement Summary, Inventory Audit Trail, & PO fulfillment creator/approver metadata.
6. GRN validation: site-wise PO lock, over-receipt prevention, duplicate challan guard, CANCELLED QC-post block, low-stock recovery alert.
7. Outward low-stock alert cooldown deduplication.
8. Bulk Outward template / preview / commit / atomic replay prevention / stock & formula validation.
9. Formula/macro payload rejection across bulk material & daily stock uploads + future-date & locked-status DSR guards.
"""

import io
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
import pytest
from httpx import AsyncClient, ASGITransport
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader
from sqlalchemy import select, and_

from app.main import app
from app.core.config import settings
from app.core.dependencies import get_current_user, get_current_active_user, require_roles, get_request_redis
from app.core.default_role_permissions import assign_default_role_permissions
from app.core.enums import ProjectStatus, RateType
from app.core.rbac_seed import seed_permissions
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.daily_stock import DailyStockReconciliation
from app.models.inventory import (
    GoodsReceiptNote,
    StockBatch,
    MaterialIssue,
)
from app.models.master_data import MaterialMaster, Unit
from app.models.material import Material, Supplier, PurchaseOrder
from app.models.notification import Notification
from app.models.owner import Owner
from app.models.project import Project
from app.models.rbac import Role, Permission, RolePermission
from app.models.user import User, UserRole
from app.services.cancellation_service import _get_otp_secret


class FakeAsyncRedis:
    """In-memory async Redis stub supporting set + LUA_ATOMIC_GETDEL eval semantics."""

    def __init__(self):
        self.store = {}

    async def set(self, key: str, value: bytes, ex: int = None):
        self.store[key] = value
        return True

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, *keys):
        removed = 0
        for k in keys:
            if k in self.store:
                self.store.pop(k, None)
                removed += 1
        return removed

    async def incr(self, key: str):
        cur = int(self.store.get(key, 0) or 0) + 1
        self.store[key] = cur
        return cur

    async def eval(self, script: str, numkeys: int, key: str):
        return self.store.pop(key, None)


def _override_user(user: User, fake_redis=None):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[require_roles] = lambda roles: (lambda: user)
    if fake_redis is not None:
        app.dependency_overrides[get_request_redis] = lambda: fake_redis


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.clear()


async def _seed_company_fixture():
    """Creates isolated two-company test environment with unique IDs."""
    uid = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as db:
        has_rbac = await db.scalar(
            select(RolePermission.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                and_(
                    RolePermission.role == UserRole.ADMIN.value,
                    Permission.code == "po.create",
                )
            )
            .limit(1)
        )
        if not has_rbac:
            await seed_permissions(db)
            await assign_default_role_permissions(db)
            await db.commit()

        comp_a = Company(name=f"ClosureCo A {uid}", is_active=True)
        comp_b = Company(name=f"ClosureCo B {uid}", is_active=True)
        db.add_all([comp_a, comp_b])
        await db.flush()

        maker_a = User(
            email=f"maker_a_{uid}@test.com",
            hashed_password=get_password_hash("Pass123!"),
            full_name=f"Maker A {uid}",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
            company_id=comp_a.id,
        )
        checker_a = User(
            email=f"checker_a_{uid}@test.com",
            hashed_password=get_password_hash("Pass123!"),
            full_name=f"Checker A {uid}",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
            company_id=comp_a.id,
        )
        admin_b = User(
            email=f"admin_b_{uid}@test.com",
            hashed_password=get_password_hash("Pass123!"),
            full_name=f"Admin B {uid}",
            role=UserRole.ADMIN.value,
            is_active=True,
            is_super_admin=False,
            company_id=comp_b.id,
        )
        db.add_all([maker_a, checker_a, admin_b])
        await db.flush()

        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWA-{uid}",
            owner_name=f"Owner A {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"owna_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWB-{uid}",
            owner_name=f"Owner B {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownb_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a1 = Project(
            business_id=f"PA1-{uid}",
            company_id=comp_a.id,
            project_name=f"Project A1 {uid}",
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_a2 = Project(
            business_id=f"PA2-{uid}",
            company_id=comp_a.id,
            project_name=f"Project A2 {uid}",
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b1 = Project(
            business_id=f"PB1-{uid}",
            company_id=comp_b.id,
            project_name=f"Project B1 {uid}",
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a1, proj_a2, proj_b1])
        await db.flush()

        unit = await db.scalar(select(Unit).where(Unit.name == f"Unit_{uid}"))
        if not unit:
            unit = Unit(name=f"Unit_{uid}", unique_code=f"U_{uid}", company_id=comp_a.id)
            db.add(unit)
            await db.flush()

        mm = MaterialMaster(
            company_id=comp_a.id,
            name=f"Steel Rebar {uid}",
            unique_code=f"MM-STL-{uid}",
            category="Steel",
            unit_id=unit.id,
            is_active=True,
        )
        sup_a1 = Supplier(
            company_id=comp_a.id,
            supplier_name=f"Supplier A1 {uid}",
            contact_person="Rep A1",
            phone_email=f"supa1_{uid}@test.com",
        )
        sup_a2 = Supplier(
            company_id=comp_a.id,
            supplier_name=f"Supplier A2 {uid}",
            contact_person="Rep A2",
            phone_email=f"supa2_{uid}@test.com",
        )
        db.add_all([mm, sup_a1, sup_a2])
        await db.commit()
        for obj in (comp_a, comp_b, maker_a, checker_a, admin_b, proj_a1, proj_a2, proj_b1, unit, mm, sup_a1, sup_a2):
            await db.refresh(obj)

        return {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "maker_a": maker_a,
            "checker_a": checker_a,
            "admin_b": admin_b,
            "proj_a1": proj_a1,
            "proj_a2": proj_a2,
            "proj_b1": proj_b1,
            "unit": unit,
            "mm": mm,
            "sup_a1": sup_a1,
            "sup_a2": sup_a2,
        }


async def _fetch_otp_from_notification(checker_id: int, doc_number: str) -> str:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Notification)
                .where(
                    and_(
                        Notification.user_id == checker_id,
                        Notification.title.like(f"%{doc_number}%"),
                    )
                )
                .order_by(Notification.id.desc())
            )
        ).scalars().all()
        for n in rows:
            m = re.search(r"Authorization OTP:\s*(\d{6})", n.message or "")
            if m:
                return m.group(1)
    raise AssertionError(f"OTP not found for checker {checker_id} on {doc_number}")


def _make_xlsx_bytes(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_cancellation_secret_uses_configured_jwt_secret():
    """Verify OTP HMAC key derives from configured JWT_SECRET instead of missing SECRET_KEY literal fallback."""
    secret = _get_otp_secret()
    assert secret == settings.JWT_SECRET
    assert secret != "default-secret-key-construction-erp"


@pytest.mark.asyncio
async def test_po_cancellation_blocked_when_posted_grn_exists_then_unblocked():
    """PO with POSTED GRN cannot be cancelled until the GRN itself is reversed/cancelled."""
    ctx = await _seed_company_fixture()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _override_user(ctx["maker_a"])
        res_po = await client.post("/api/v1/purchase-orders/create", json={
            "company_id": ctx["comp_a"].id,
            "supplier_id": ctx["sup_a1"].id,
            "project_id": ctx["proj_a1"].id,
            "items": [{"material_master_id": ctx["mm"].id, "material_name": ctx["mm"].name, "quantity": "20.000", "rate": "100.00"}],
        })
        assert res_po.status_code == 201
        po = res_po.json()
        po_id = po["id"]
        po_item_id = po["items"][0]["id"]

        res_grn = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "purchase_order_id": po_id,
            "items": [{
                "po_item_id": po_item_id,
                "material_master_id": ctx["mm"].id,
                "received_quantity": "10.000",
                "unit_rate": "100.00",
            }],
        })
        assert res_grn.status_code == 201
        grn = res_grn.json()

        res_post = await client.post(f"/api/v1/inward/grn/{grn['id']}/qc-post", json={
            "qc_items": [{"item_id": grn["items"][0]["id"], "accepted_quantity": "10.000", "rejected_quantity": "0.000"}]
        })
        assert res_post.status_code == 200

        # PO is now PARTIALLY_RECEIVED with a POSTED GRN -> PO cancellation must fail 400
        res_bad_po_cancel = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": ctx["comp_a"].id,
            "document_type": "PO",
            "document_id": po_id,
            "reason": "Attempt PO cancel before reversing GRN",
        })
        assert res_bad_po_cancel.status_code == 400
        assert "grn" in res_bad_po_cancel.json()["detail"].lower() or "partially_received" in res_bad_po_cancel.json()["detail"].lower()

        # Reverse/cancel the GRN via maker -> checker OTP
        res_grn_otp = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": ctx["comp_a"].id,
            "document_type": "GRN",
            "document_id": grn["id"],
            "reason": "Reverse GRN before PO cancellation",
        })
        assert res_grn_otp.status_code == 201
        grn_otp = await _fetch_otp_from_notification(ctx["checker_a"].id, grn["grn_number"])

        _override_user(ctx["checker_a"])
        res_ver_grn = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": ctx["comp_a"].id,
            "request_id": res_grn_otp.json()["request_id"],
            "otp": grn_otp,
        })
        assert res_ver_grn.status_code == 200

        # After GRN cancellation reverts PO back to CREATED/PENDING with 0 received qty, PO cancellation succeeds
        _override_user(ctx["maker_a"])
        res_po_otp = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": ctx["comp_a"].id,
            "document_type": "PO",
            "document_id": po_id,
            "reason": "Cancel PO after GRN reversal",
        })
        assert res_po_otp.status_code == 201
        po_otp = await _fetch_otp_from_notification(ctx["checker_a"].id, po["po_number"])

        _override_user(ctx["checker_a"])
        res_ver_po = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": ctx["comp_a"].id,
            "request_id": res_po_otp.json()["request_id"],
            "otp": po_otp,
        })
        assert res_ver_po.status_code == 200
        async with AsyncSessionLocal() as db:
            po_db = await db.get(PurchaseOrder, po_id)
            assert po_db.status == "CANCELLED"


@pytest.mark.asyncio
async def test_grn_cancellation_blocks_partial_batch_consumption_multi_supplier():
    """
    Even if combined material remaining_stock >= GRN A quantity (due to Supplier B stock),
    GRN A cannot be cancelled once any portion of GRN A's own StockBatch was consumed.
    """
    ctx = await _seed_company_fixture()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _override_user(ctx["maker_a"])
        r1 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "remarks": "Batch A direct inward",
            "items": [{"material_master_id": ctx["mm"].id, "received_quantity": "25.000", "unit_rate": "50.00"}],
        })
        assert r1.status_code == 201
        g1 = r1.json()
        p1 = await client.post(f"/api/v1/inward/grn/{g1['id']}/qc-post", json={
            "qc_items": [{"item_id": g1["items"][0]["id"], "accepted_quantity": "25.000", "rejected_quantity": "0.000"}]
        })
        assert p1.status_code == 200
        mat_id = p1.json()["items"][0]["material_id"]

        r2 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a2"].id,
            "remarks": "Batch B direct inward",
            "items": [{"material_master_id": ctx["mm"].id, "received_quantity": "35.000", "unit_rate": "60.00"}],
        })
        assert r2.status_code == 201
        g2 = r2.json()
        p2 = await client.post(f"/api/v1/inward/grn/{g2['id']}/qc-post", json={
            "qc_items": [{"item_id": g2["items"][0]["id"], "accepted_quantity": "35.000", "rejected_quantity": "0.000"}]
        })
        assert p2.status_code == 200

        # Outward 10 units -> consumes 10 from GRN A's batch (leaving 15/25 in Batch A; 50 total remaining >= 25!)
        m1 = await client.post("/api/v1/outward/issue/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "material_id": mat_id,
            "quantity": "10.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Crew 1",
        })
        assert m1.status_code == 201

        res_bad_grn = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": ctx["comp_a"].id,
            "document_type": "GRN",
            "document_id": g1["id"],
            "reason": "Partial batch A consumed",
        })
        assert res_bad_grn.status_code == 400
        assert "stock has already been consumed" in res_bad_grn.json()["detail"].lower()


@pytest.mark.asyncio
async def test_grn_site_lock_over_receipt_duplicate_challan_and_cancelled_qc_guard():
    """Verify PO site-lock, over-receipt prevention, duplicate challan guard, and CANCELLED GRN qc-post block."""
    ctx = await _seed_company_fixture()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _override_user(ctx["maker_a"])
        res_po = await client.post("/api/v1/purchase-orders/create", json={
            "company_id": ctx["comp_a"].id,
            "supplier_id": ctx["sup_a1"].id,
            "project_id": ctx["proj_a1"].id,
            "is_company_wide": False,
            "items": [{"material_master_id": ctx["mm"].id, "material_name": ctx["mm"].name, "quantity": "10.000", "rate": "80.00"}],
        })
        assert res_po.status_code == 201
        po = res_po.json()
        po_id = po["id"]
        po_item_id = po["items"][0]["id"]

        # 1. Site-wise PO locked to proj_a1 must reject GRN against proj_a2
        res_wrong_site = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a2"].id,
            "supplier_id": ctx["sup_a1"].id,
            "purchase_order_id": po_id,
            "items": [{"po_item_id": po_item_id, "material_master_id": ctx["mm"].id, "received_quantity": "5.000", "unit_rate": "80.00"}],
        })
        assert res_wrong_site.status_code == 400
        assert "locked to project" in res_wrong_site.json()["detail"].lower()

        # 2. Over-receipt (> 10.000) must reject 400
        res_over = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "purchase_order_id": po_id,
            "items": [{"po_item_id": po_item_id, "material_master_id": ctx["mm"].id, "received_quantity": "15.000", "unit_rate": "80.00"}],
        })
        assert res_over.status_code == 400
        assert "over-receipt" in res_over.json()["detail"].lower()

        # 3. Valid GRN with challan + duplicate challan rejection
        challan = f"CH-{ctx['uid']}"
        res_ok = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "purchase_order_id": po_id,
            "challan_number": challan,
            "items": [{"po_item_id": po_item_id, "material_master_id": ctx["mm"].id, "received_quantity": "5.000", "unit_rate": "80.00"}],
        })
        assert res_ok.status_code == 201
        grn_ok = res_ok.json()

        res_dup_ch = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "purchase_order_id": po_id,
            "challan_number": challan,
            "items": [{"po_item_id": po_item_id, "material_master_id": ctx["mm"].id, "received_quantity": "2.000", "unit_rate": "80.00"}],
        })
        assert res_dup_ch.status_code == 400
        assert "duplicate challan" in res_dup_ch.json()["detail"].lower()

        # 4. Cancelled DRAFT GRN cannot be QC-posted
        res_otp = await client.post("/api/v1/cancellation/request-otp", json={
            "company_id": ctx["comp_a"].id,
            "document_type": "GRN",
            "document_id": grn_ok["id"],
            "reason": "Cancel draft GRN",
        })
        assert res_otp.status_code == 201
        otp_val = await _fetch_otp_from_notification(ctx["checker_a"].id, grn_ok["grn_number"])

        _override_user(ctx["checker_a"])
        res_ver = await client.post("/api/v1/cancellation/verify-otp", json={
            "company_id": ctx["comp_a"].id,
            "request_id": res_otp.json()["request_id"],
            "otp": otp_val,
        })
        assert res_ver.status_code == 200

        res_post_cancelled = await client.post(f"/api/v1/inward/grn/{grn_ok['id']}/qc-post", json={
            "qc_items": [{"item_id": grn_ok["items"][0]["id"], "accepted_quantity": "5.000", "rejected_quantity": "0.000"}]
        })
        assert res_post_cancelled.status_code == 400
        assert "cancelled" in res_post_cancelled.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reports_tenant_isolation_combined_movement_audit_trail_and_po_actors():
    """
    Verify:
    1. Cross-company isolation on stock-summary, low-stock-alerts, batch-inventory, supplier-stock, movement-register, combined-movement, audit-trail.
    2. PO fulfillment includes approver metadata, Combined Movement returns opening/inward/outward/closing + valuation, Audit Trail returns activity logs, and Excel/PDF exports succeed.
    """
    ctx = await _seed_company_fixture()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _override_user(ctx["maker_a"])
        res_po = await client.post("/api/v1/purchase-orders/create", json={
            "company_id": ctx["comp_a"].id,
            "supplier_id": ctx["sup_a1"].id,
            "project_id": ctx["proj_a1"].id,
            "items": [{"material_master_id": ctx["mm"].id, "material_name": ctx["mm"].name, "quantity": "15.000", "rate": "100.00"}],
        })
        assert res_po.status_code == 201
        po = res_po.json()

        _override_user(ctx["checker_a"])
        res_app = await client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
        assert res_app.status_code == 200

        _override_user(ctx["maker_a"])
        res_grn = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "purchase_order_id": po["id"],
            "items": [{"po_item_id": po["items"][0]["id"], "material_master_id": ctx["mm"].id, "received_quantity": "12.000", "unit_rate": "100.00"}],
        })
        assert res_grn.status_code == 201
        grn = res_grn.json()

        res_post = await client.post(f"/api/v1/inward/grn/{grn['id']}/qc-post", json={
            "qc_items": [{"item_id": grn["items"][0]["id"], "accepted_quantity": "12.000", "rejected_quantity": "0.000"}]
        })
        assert res_post.status_code == 200
        mat_id = res_post.json()["items"][0]["material_id"]

        res_min = await client.post("/api/v1/outward/issue/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "material_id": mat_id,
            "quantity": "4.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Slab Crew",
        })
        assert res_min.status_code == 201

        # Company A report verifications
        res_po_rep = await client.get(f"/api/v1/inventory/reports/po-fulfillment?project_id={ctx['proj_a1'].id}")
        assert res_po_rep.status_code == 200
        po_entry = next(r for r in res_po_rep.json() if r["po_id"] == po["id"])
        assert po_entry["approved_by_id"] == ctx["checker_a"].id
        assert po_entry["approved_by_name"] == ctx["checker_a"].full_name

        res_comb = await client.get(f"/api/v1/inventory/reports/combined-movement?project_id={ctx['proj_a1'].id}")
        assert res_comb.status_code == 200
        comb_row = next(r for r in res_comb.json() if r["material_id"] == mat_id)
        assert Decimal(str(comb_row["total_inward"])) == Decimal("12.000")
        assert Decimal(str(comb_row["total_outward"])) == Decimal("4.000")
        assert Decimal(str(comb_row["closing_stock"])) == Decimal("8.000")
        assert Decimal(str(comb_row["closing_valuation"])) == Decimal("800.00")

        res_audit = await client.get(f"/api/v1/inventory/reports/audit-trail?project_id={ctx['proj_a1'].id}")
        assert res_audit.status_code == 200
        actions = {e["action"] for e in res_audit.json()}
        assert {"PO_CREATED", "PO_APPROVED", "GRN_CREATED", "GRN_QC_POSTED", "MIN_ISSUED"}.issubset(actions)

        # Export combined_movement to Excel and audit_trail to PDF
        res_xlsx = await client.get(f"/api/v1/inventory/reports/export/excel?report_name=combined_movement&project_id={ctx['proj_a1'].id}")
        assert res_xlsx.status_code == 200
        wb = load_workbook(io.BytesIO(res_xlsx.content), data_only=True)
        assert wb.active.max_row >= 2

        res_pdf = await client.get(f"/api/v1/inventory/reports/export/pdf?report_name=audit_trail&project_id={ctx['proj_a1'].id}")
        assert res_pdf.status_code == 200
        assert res_pdf.content.startswith(b"%PDF")
        assert len(PdfReader(io.BytesIO(res_pdf.content)).pages) >= 1

        # Cross-tenant isolation verification as Company B admin
        _override_user(ctx["admin_b"])
        assert (await client.get(f"/api/v1/inventory/reports/low-stock-alerts?company_id={ctx['comp_a'].id}")).status_code == 403
        assert (await client.get(f"/api/v1/inventory/reports/batch-inventory?project_id={ctx['proj_a1'].id}&company_id={ctx['comp_a'].id}")).status_code == 403
        assert (await client.get(f"/api/v1/inventory/reports/stock-summary?project_id={ctx['proj_a1'].id}")).json() == []
        assert (await client.get(f"/api/v1/inventory/reports/low-stock-alerts?project_id={ctx['proj_a1'].id}")).json() == []
        assert (await client.get(f"/api/v1/inventory/reports/batch-inventory?project_id={ctx['proj_a1'].id}")).json() == []
        assert (await client.get(f"/api/v1/inventory/reports/supplier-stock?project_id={ctx['proj_a1'].id}")).json() == []
        assert (await client.get(f"/api/v1/inventory/reports/combined-movement?project_id={ctx['proj_a1'].id}")).json() == []
        assert (await client.get(f"/api/v1/inventory/reports/audit-trail?project_id={ctx['proj_a1'].id}")).json() == []
        res_mov_forbidden = await client.get(f"/api/v1/inventory/reports/movement-register?material_id={mat_id}")
        assert res_mov_forbidden.status_code == 403


@pytest.mark.asyncio
async def test_low_stock_cooldown_dedup_and_inward_recovery_alert():
    """Repeated outward below threshold within cooldown skips duplicate alerts; inward above threshold emits recovery."""
    ctx = await _seed_company_fixture()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _override_user(ctx["maker_a"])
        r1 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "remarks": "Seed 12 units",
            "items": [{"material_master_id": ctx["mm"].id, "received_quantity": "12.000", "unit_rate": "50.00"}],
        })
        assert r1.status_code == 201
        g1 = r1.json()
        p1 = await client.post(f"/api/v1/inward/grn/{g1['id']}/qc-post", json={
            "qc_items": [{"item_id": g1["items"][0]["id"], "accepted_quantity": "12.000", "rejected_quantity": "0.000"}]
        })
        mat_id = p1.json()["items"][0]["material_id"]

        # Drop to 9.000 (<= 10 threshold) -> emits 1 alert
        res_o1 = await client.post("/api/v1/outward/issue/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "material_id": mat_id,
            "quantity": "3.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Crew 1",
        })
        assert res_o1.status_code == 201

        # Drop to 7.000 within 60m cooldown -> suppressed
        res_o2 = await client.post("/api/v1/outward/issue/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "material_id": mat_id,
            "quantity": "2.000",
            "issue_type": "SITE_CONSUMPTION",
            "issued_to": "Crew 1",
        })
        assert res_o2.status_code == 201

        async with AsyncSessionLocal() as db:
            low_alerts = (
                await db.execute(
                    select(Notification).where(
                        and_(
                            Notification.user_id == ctx["maker_a"].id,
                            Notification.type == "alert",
                            Notification.link == f"/inventory/materials/{mat_id}",
                        )
                    )
                )
            ).scalars().all()
            assert len(low_alerts) == 1

        # Replenish +10 -> 17.000 (> 10 threshold) -> emits Low Stock Alert Resolved
        r2 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "remarks": "Replenish stock",
            "items": [{"material_master_id": ctx["mm"].id, "received_quantity": "10.000", "unit_rate": "50.00"}],
        })
        assert r2.status_code == 201
        g2 = r2.json()
        p2 = await client.post(f"/api/v1/inward/grn/{g2['id']}/qc-post", json={
            "qc_items": [{"item_id": g2["items"][0]["id"], "accepted_quantity": "10.000", "rejected_quantity": "0.000"}]
        })
        assert p2.status_code == 200

        async with AsyncSessionLocal() as db:
            res_notifs = (
                await db.execute(
                    select(Notification).where(
                        and_(
                            Notification.user_id == ctx["maker_a"].id,
                            Notification.link == f"/inventory/materials/{mat_id}",
                            Notification.title.like("Low Stock Alert Resolved:%"),
                        )
                    )
                )
            ).scalars().all()
            assert len(res_notifs) == 1


@pytest.mark.asyncio
async def test_bulk_outward_template_preview_commit_replay_and_formula_guard():
    """Verify Bulk Outward end-to-end template, preview, atomic Lua commit, replay block, stock guard, and formula block."""
    ctx = await _seed_company_fixture()
    fake_redis = FakeAsyncRedis()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _override_user(ctx["maker_a"], fake_redis=fake_redis)

        # Seed stock = 20 units
        r1 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "remarks": "Seed 20 for bulk outward",
            "items": [{"material_master_id": ctx["mm"].id, "received_quantity": "20.000", "unit_rate": "40.00"}],
        })
        g1 = r1.json()
        p1 = await client.post(f"/api/v1/inward/grn/{g1['id']}/qc-post", json={
            "qc_items": [{"item_id": g1["items"][0]["id"], "accepted_quantity": "20.000", "rejected_quantity": "0.000"}]
        })
        mat_id = p1.json()["items"][0]["material_id"]

        # 1. Template download
        tpl_res = await client.get("/api/v1/outward/bulk-upload/template")
        assert tpl_res.status_code == 200
        wb = load_workbook(io.BytesIO(tpl_res.content), data_only=True)
        assert [c.value for c in wb.active[1]][:4] == ["Project ID", "Material ID", "Quantity", "Issue Type"]

        # 2. Bad preview (formula injection + cumulative stock exceeded)
        bad_xlsx = _make_xlsx_bytes([
            ["Project ID", "Material ID", "Quantity", "Issue Type", "Issued To", "Contractor ID", "BOQ Item ID", "Remarks"],
            [ctx["proj_a1"].id, mat_id, 15, "SITE_CONSUMPTION", "Crew A", "", "", "=CMD|'/C calc'!A0"],
            [ctx["proj_a1"].id, mat_id, 25, "SITE_CONSUMPTION", "Crew B", "", "", "Over stock"],
        ])
        bad_prev = await client.post(
            "/api/v1/outward/bulk-upload/preview",
            files={"file": ("bad_out.xlsx", bad_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert bad_prev.status_code == 200
        bad_json = bad_prev.json()
        assert bad_json["invalid_rows"] == 2
        assert bad_json["import_token"] is None

        # 3. Valid preview -> commit -> FIFO depleted -> token replay rejected
        good_xlsx = _make_xlsx_bytes([
            ["Project ID", "Material ID", "Quantity", "Issue Type", "Issued To", "Contractor ID", "BOQ Item ID", "Remarks"],
            [ctx["proj_a1"].id, mat_id, 8, "SITE_CONSUMPTION", "Team 1", "", "", "First pour"],
            [ctx["proj_a1"].id, mat_id, 4, "SITE_CONSUMPTION", "Team 2", "", "", "Second pour"],
        ])
        good_prev = await client.post(
            "/api/v1/outward/bulk-upload/preview",
            files={"file": ("good_out.xlsx", good_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert good_prev.status_code == 200
        prev_json = good_prev.json()
        assert prev_json["valid_rows"] == 2
        token = prev_json["import_token"]
        assert token

        res_commit = await client.post("/api/v1/outward/bulk-upload/commit", json={
            "import_token": token,
            "company_id": ctx["comp_a"].id,
        })
        assert res_commit.status_code == 200
        assert res_commit.json()["committed_count"] == 2

        res_replay = await client.post("/api/v1/outward/bulk-upload/commit", json={
            "import_token": token,
            "company_id": ctx["comp_a"].id,
        })
        assert res_replay.status_code == 400

        async with AsyncSessionLocal() as db:
            mat_db = await db.get(Material, mat_id)
            assert mat_db.remaining_stock == Decimal("8.000")


@pytest.mark.asyncio
async def test_bulk_material_and_daily_stock_formula_and_date_locks():
    """Verify formula rejection in bulk material + daily stock uploads, future-date block, and locked DSR overwrite guard."""
    ctx = await _seed_company_fixture()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _override_user(ctx["maker_a"])

        # Seed a site material
        r1 = await client.post("/api/v1/inward/grn/create", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "supplier_id": ctx["sup_a1"].id,
            "remarks": "Seed for DSR lock check",
            "items": [{"material_master_id": ctx["mm"].id, "received_quantity": "15.000", "unit_rate": "50.00"}],
        })
        g1 = r1.json()
        p1 = await client.post(f"/api/v1/inward/grn/{g1['id']}/qc-post", json={
            "qc_items": [{"item_id": g1["items"][0]["id"], "accepted_quantity": "15.000", "rejected_quantity": "0.000"}]
        })
        mat_id = p1.json()["items"][0]["material_id"]

        # 1. Bulk Material formula injection rejected with HTTP 400
        bad_mat_xlsx = _make_xlsx_bytes([
            ["Project ID", "Material Master ID", "Supplier ID", "Purchase Rate", "Rate Type", "Quantity Purchased"],
            [ctx["proj_a1"].id, ctx["mm"].id, ctx["sup_a1"].id, "=SUM(1,2)", "FIXED", 10],
        ])
        res_bad_mat = await client.post(
            "/api/v1/materials/bulk-upload/preview",
            files={"file": ("formula_mat.xlsx", bad_mat_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert res_bad_mat.status_code == 400
        assert "formula" in res_bad_mat.json()["detail"].lower()

        # 2. Daily stock bulk upload rejects formula in remarks and future reconciliation dates
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        bad_dsr_xlsx = _make_xlsx_bytes([
            ["Project ID", "Material ID", "Physical Count", "Reconciliation Date (YYYY-MM-DD)", "Remarks"],
            [ctx["proj_a1"].id, mat_id, 14, date.today().isoformat(), "@SUM(1+1)"],
            [ctx["proj_a1"].id, mat_id, 15, tomorrow, "Future date test"],
        ])
        res_bad_dsr = await client.post(
            "/api/v1/daily-stock/bulk-upload/preview",
            files={"file": ("bad_dsr.xlsx", bad_dsr_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert res_bad_dsr.status_code == 200
        assert res_bad_dsr.json()["invalid_rows"] >= 1
        assert res_bad_dsr.json()["import_token"] is None

        # 3. Direct future date blocked with 400
        res_future = await client.post("/api/v1/daily-stock/record", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "material_id": mat_id,
            "physical_count": "15.000",
            "reconciliation_date": tomorrow,
        })
        assert res_future.status_code == 400

        # 4. Adjusted DSR locks same-day overwrite
        res_dsr = await client.post("/api/v1/daily-stock/record", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "material_id": mat_id,
            "physical_count": "14.000",
            "reconciliation_date": date.today().isoformat(),
        })
        assert res_dsr.status_code == 201
        dsr_id = res_dsr.json()["id"]

        _override_user(ctx["checker_a"])
        res_rev = await client.post(f"/api/v1/daily-stock/{dsr_id}/review", json={
            "review_outcome": "ADJUST_INVENTORY",
            "review_notes": "Finalized count",
        })
        assert res_rev.status_code == 200
        assert res_rev.json()["status"] == "ADJUSTED"

        _override_user(ctx["maker_a"])
        res_locked = await client.post("/api/v1/daily-stock/record", json={
            "company_id": ctx["comp_a"].id,
            "project_id": ctx["proj_a1"].id,
            "material_id": mat_id,
            "physical_count": "10.000",
            "reconciliation_date": date.today().isoformat(),
        })
        assert res_locked.status_code == 400
        assert "locked" in res_locked.json()["detail"].lower()
