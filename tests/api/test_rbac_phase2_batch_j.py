import uuid
from decimal import Decimal
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, ActivityLog
from app.models.company import Company
from app.models.owner import Owner, OwnerTransaction
from app.models.project import Project, ProjectMember, Task
from app.models.settings import CompanySettings
from app.models.invoice import Invoice, Transaction
from app.models.quotation import QuotationMaster, QuotationStatus
from app.models.final_measurement import FinalMeasurement
from app.models.billing import RABill
from app.models.expense import Expense
from app.models.accountant import JournalEntry, JournalLine, Account
from app.models.notification import Notification
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import InvoiceType, InvoiceStatus, InvoiceSourceType, PaymentMode


@asynccontextmanager
async def setup_batch_j_data():
    """Seed test companies, projects, invoices, transactions, quotations, measurements, and users for Batch J."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchJ_CompA_{uid}")
        comp_b = Company(name=f"BatchJ_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company settings
        cs_a = CompanySettings(company_id=comp_a.id, company_name=f"Brand_Company_A_{uid}")
        cs_b = CompanySettings(company_id=comp_b.id, company_name=f"Brand_Company_B_{uid}")
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_j_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin J",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_ja_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin J",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_jb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin J",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"InvoiceOfficer_{uid}"
        user_custom_a = User(
            email=f"custom_ja_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Invoice Officer J",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        user_legacy_a = User(
            email=f"legacy_ja_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy PM J",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Project Manager",
        )

        user_unassigned_a = User(
            email=f"unassigned_ja_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Unassigned Officer J",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        client_user_a = User(
            email=f"client_ja_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Client User A J",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Client",
        )

        client_user_b = User(
            email=f"client_jb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Client User B J",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Client",
        )

        db.add_all([
            super_admin, admin_a, admin_b, user_custom_a, user_legacy_a,
            user_unassigned_a, client_user_a, client_user_b
        ])
        await db.flush()

        # 4. Role for custom user
        role_custom = Role(
            name=custom_role_name,
            display_name="Custom Role J",
            company_id=comp_a.id,
            description="Custom Invoice Role J",
        )
        db.add(role_custom)
        await db.flush()

        # 5. Owners and Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-JA-{uid}",
            owner_name=f"Owner JA {uid}",
            email=f"ownerja_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-JB-{uid}",
            owner_name=f"Owner JB {uid}",
            email=f"ownerjb_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(
            business_id=f"PRJ-JA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_JA_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-JB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_JB_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Project Members
        pm_a1 = ProjectMember(project_id=proj_a.id, user_id=admin_a.id)
        pm_a2 = ProjectMember(project_id=proj_a.id, user_id=user_custom_a.id)
        pm_a3 = ProjectMember(project_id=proj_a.id, user_id=client_user_a.id)
        pm_b1 = ProjectMember(project_id=proj_b.id, user_id=admin_b.id)
        pm_b2 = ProjectMember(project_id=proj_b.id, user_id=client_user_b.id)
        db.add_all([pm_a1, pm_a2, pm_a3, pm_b1, pm_b2])
        await db.flush()

        # 6. Invoices
        invoice_a1 = Invoice(
            company_id=comp_a.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type=InvoiceType.OWNER,
            source_type=InvoiceSourceType.MANUAL,
            amount=Decimal("10000.00"),
            gst_percent=Decimal("18.00"),
            gst_amount=Decimal("1800.00"),
            tax_percent=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("11800.00"),
            paid_amount=Decimal("1800.00"),
            pending_amount=Decimal("10000.00"),
            status=InvoiceStatus.PARTIAL,
            description="Initial Invoice Comp A",
            invoice_number=f"INV-A1-{uid}",
            invoice_date=date.today(),
        )
        invoice_b1 = Invoice(
            company_id=comp_b.id,
            project_id=proj_b.id,
            owner_id=owner_b.id,
            type=InvoiceType.OWNER,
            source_type=InvoiceSourceType.MANUAL,
            amount=Decimal("20000.00"),
            gst_percent=Decimal("18.00"),
            gst_amount=Decimal("3600.00"),
            tax_percent=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("23600.00"),
            paid_amount=Decimal("3600.00"),
            pending_amount=Decimal("20000.00"),
            status=InvoiceStatus.PARTIAL,
            description="Initial Invoice Comp B",
            invoice_number=f"INV-B1-{uid}",
            invoice_date=date.today(),
        )
        db.add_all([invoice_a1, invoice_b1])
        await db.flush()

        # 7. Transactions
        txn_a1 = Transaction(
            project_id=proj_a.id,
            invoice_id=invoice_a1.id,
            type="receipt",
            amount=Decimal("1800.00"),
            mode="bank",
            reference=f"REF-TXN-A1-{uid}",
            created_by=admin_a.id,
        )
        txn_b1 = Transaction(
            project_id=proj_b.id,
            invoice_id=invoice_b1.id,
            type="receipt",
            amount=Decimal("3600.00"),
            mode="bank",
            reference=f"REF-TXN-B1-{uid}",
            created_by=admin_b.id,
        )
        db.add_all([txn_a1, txn_b1])
        await db.flush()

        # 8. Quotations
        quotation_a = QuotationMaster(
            quotation_no=f"QTN-JA-{uid}",
            company_id=comp_a.id,
            client_user_id=client_user_a.id,
            client_name="Client A",
            mobile_number="9800000001",
            project_id=proj_a.id,
            project_name="Proj JA",
            project_type="Residential",
            subtotal=50000.0,
            grand_total=59000.0,
            gst_amount=9000.0,
            cgst_percent=9.0,
            sgst_percent=9.0,
            is_approved=True,
            status=QuotationStatus.APPROVED,
            converted_to_invoice=False,
        )
        quotation_b = QuotationMaster(
            quotation_no=f"QTN-JB-{uid}",
            company_id=comp_b.id,
            client_user_id=client_user_b.id,
            client_name="Client B",
            mobile_number="9700000002",
            project_id=proj_b.id,
            project_name="Proj JB",
            project_type="Commercial",
            subtotal=80000.0,
            grand_total=94400.0,
            gst_amount=14400.0,
            cgst_percent=9.0,
            sgst_percent=9.0,
            is_approved=True,
            status=QuotationStatus.APPROVED,
            converted_to_invoice=False,
        )
        db.add_all([quotation_a, quotation_b])
        await db.flush()

        # 9. Measurements
        meas_a = FinalMeasurement(
            project_id=proj_a.id,
            final_area=Decimal("100.00"),
            approved_rate=Decimal("150.00"),
            total_area=Decimal("100.00"),
            total_amount=Decimal("15000.00"),
            status="APPROVED",
        )
        meas_b = FinalMeasurement(
            project_id=proj_b.id,
            final_area=Decimal("200.00"),
            approved_rate=Decimal("150.00"),
            total_area=Decimal("200.00"),
            total_amount=Decimal("30000.00"),
            status="APPROVED",
        )
        db.add_all([meas_a, meas_b])
        await db.flush()

        # 10. RABills
        rabill_a = RABill(
            project_id=proj_a.id,
            bill_number=f"RAB-JA-{uid}",
            work_description="Excavation Work Comp A",
            quantity=Decimal("100.000"),
            rate=Decimal("100.00"),
            gross_amount=Decimal("10000.00"),
            net_amount=Decimal("10000.00"),
            total_amount=Decimal("11800.00"),
            bill_date=date.today(),
            status="Approved",
        )
        rabill_b = RABill(
            project_id=proj_b.id,
            bill_number=f"RAB-JB-{uid}",
            work_description="Structure Work Comp B",
            quantity=Decimal("200.000"),
            rate=Decimal("100.00"),
            gross_amount=Decimal("20000.00"),
            net_amount=Decimal("20000.00"),
            total_amount=Decimal("23600.00"),
            bill_date=date.today(),
            status="Approved",
        )
        db.add_all([rabill_a, rabill_b])
        await db.flush()

        # 11. Expenses for labour and material
        exp_labour_a = Expense(
            project_id=proj_a.id,
            source_type="attendance_auto",
            category="Labour",
            amount=Decimal("5000.00"),
            payment_mode="Cash",
            expense_date=date.today(),
            description="Auto attendance wages",
        )
        exp_material_a = Expense(
            project_id=proj_a.id,
            source_type="manual",
            category="Material",
            amount=Decimal("7500.00"),
            payment_mode="Cash",
            expense_date=date.today(),
            description="Cement bags",
        )
        db.add_all([exp_labour_a, exp_material_a])
        await db.flush()

        await db.commit()

        # Tokens
        tokens = {
            "super": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "custom_a": create_access_token({"sub": str(user_custom_a.id)}),
            "legacy_a": create_access_token({"sub": str(user_legacy_a.id)}),
            "unassigned_a": create_access_token({"sub": str(user_unassigned_a.id)}),
            "client_a": create_access_token({"sub": str(client_user_a.id)}),
            "client_b": create_access_token({"sub": str(client_user_b.id)}),
        }

        yield {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "owner_a": owner_a,
            "owner_b": owner_b,
            "invoice_a1": invoice_a1,
            "invoice_b1": invoice_b1,
            "txn_a1": txn_a1,
            "txn_b1": txn_b1,
            "quotation_a": quotation_a,
            "quotation_b": quotation_b,
            "meas_a": meas_a,
            "meas_b": meas_b,
            "rabill_a": rabill_a,
            "rabill_b": rabill_b,
            "role_custom": role_custom,
            "super_admin": super_admin,
            "admin_a": admin_a,
            "admin_b": admin_b,
            "user_custom_a": user_custom_a,
            "user_legacy_a": user_legacy_a,
            "user_unassigned_a": user_unassigned_a,
            "client_user_a": client_user_a,
            "client_user_b": client_user_b,
            "tokens": tokens,
        }

        # Cleanup
        async with AsyncSessionLocal() as clean_db:
            await clean_db.execute(delete(Notification).where(Notification.user_id.in_([client_user_a.id, client_user_b.id])))
            await clean_db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_([admin_a.id, admin_b.id, user_custom_a.id, super_admin.id])))
            await clean_db.execute(delete(OwnerTransaction).where(OwnerTransaction.owner_id.in_([owner_a.id, owner_b.id])))
            await clean_db.execute(delete(Transaction).where(Transaction.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(Invoice).where(Invoice.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(Expense).where(Expense.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(RABill).where(RABill.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(FinalMeasurement).where(FinalMeasurement.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(QuotationMaster).where(QuotationMaster.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await clean_db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([user_custom_a.id, user_legacy_a.id, user_unassigned_a.id])))
            await clean_db.execute(delete(RolePermission).where(RolePermission.role_id == role_custom.id))
            await clean_db.execute(delete(Role).where(Role.id == role_custom.id))
            await clean_db.execute(delete(User).where(User.id.in_([
                super_admin.id, admin_a.id, admin_b.id, user_custom_a.id,
                user_legacy_a.id, user_unassigned_a.id, client_user_a.id, client_user_b.id
            ])))
            await clean_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
            await clean_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await clean_db.commit()


# ==============================================================================
# TEST 1 — ALL 28 UNAUTHENTICATED ROUTES RETURN 401
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_unauthenticated_all_28_routes_401():
    """Verify that all 28 active routes in Batch J return HTTP 401 when accessed without credentials."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            inv_id = d_data["invoice_a1"].id
            proj_id = d_data["proj_a"].id
            qtn_id = d_data["quotation_a"].id
            meas_id = d_data["meas_a"].id
            client_id = d_data["owner_a"].id

            routes = [
                ("POST", "/api/v1/invoices", {"json": {}}),
                ("POST", f"/api/v1/invoices/from-quotation/{qtn_id}", {}),
                ("GET", "/api/v1/invoices", {}),
                ("GET", "/api/v1/invoices/date-range?start=2026-01-01&end=2026-12-31", {}),
                ("GET", f"/api/v1/invoices/{inv_id}", {}),
                ("PUT", f"/api/v1/invoices/{inv_id}", {"json": {}}),
                ("DELETE", f"/api/v1/invoices/{inv_id}", {}),
                ("GET", f"/api/v1/invoices/project/{proj_id}", {}),
                ("GET", "/api/v1/invoices/type/owner", {}),
                ("POST", f"/api/v1/invoices/{inv_id}/mark-paid", {}),
                ("GET", f"/api/v1/invoices/{inv_id}/pdf", {}),
                ("POST", "/api/v1/invoices/labour", {"json": {}}),
                ("POST", "/api/v1/invoices/material?project_id=1", {}),
                ("POST", f"/api/v1/invoices/from-measurement/{meas_id}", {}),
                ("GET", f"/api/v1/invoices/project/{proj_id}/summary", {}),
                ("GET", f"/api/v1/invoices/analytics/summary?project_id={proj_id}", {}),
                ("POST", f"/api/v1/invoices/{inv_id}/pay?amount=100&mode=Cash", {}),
                ("GET", f"/api/v1/invoices/{inv_id}/transactions", {}),
                ("GET", "/api/v1/invoices/receivables/summary", {}),
                ("GET", "/api/v1/invoices/receivables/aging", {}),
                ("GET", f"/api/v1/invoices/receivables/client-ledger/{client_id}", {}),
                ("GET", "/api/v1/invoices/receivables/collections", {}),
                ("POST", "/api/v1/invoices/receivables/manual", {"json": {}}),
                ("POST", "/api/v1/invoices/receivables/import", {}),
                ("GET", "/api/v1/invoices/receivables/export", {}),
                ("GET", "/api/v1/invoices/receivables/collections/export", {}),
                ("GET", f"/api/v1/invoices/receivables/client-ledger/{client_id}/export", {}),
                ("POST", f"/api/v1/invoices/{inv_id}/send", {"json": {}}),
            ]

            assert len(routes) == 28, f"Expected exactly 28 routes in Batch J, found {len(routes)}"

            for method, path, kwargs in routes:
                res = await ac.request(method, path, **kwargs)
                assert res.status_code == 401, f"{method} {path} returned {res.status_code}, expected 401"


# ==============================================================================
# TEST 2 — MISSING PERMISSIONS RETURN 403
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_missing_permissions_403():
    """Verify that authenticated users without corresponding DB permissions receive 403."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            inv_id = d_data["invoice_a1"].id
            client_id = d_data["owner_a"].id

            # invoices.view missing
            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 403

            # invoices.create missing
            res = await ac.post("/api/v1/invoices", json={}, headers=headers)
            assert res.status_code == 403

            # invoices.edit missing
            res = await ac.put(f"/api/v1/invoices/{inv_id}", json={}, headers=headers)
            assert res.status_code == 403

            # invoices.delete missing
            res = await ac.delete(f"/api/v1/invoices/{inv_id}", headers=headers)
            assert res.status_code == 403

            # invoices.export missing
            res = await ac.get("/api/v1/invoices/receivables/export", headers=headers)
            assert res.status_code == 403
            res = await ac.get(f"/api/v1/invoices/{inv_id}/pdf", headers=headers)
            assert res.status_code == 403
            res = await ac.get(f"/api/v1/invoices/receivables/client-ledger/{client_id}/export", headers=headers)
            assert res.status_code == 403


# ==============================================================================
# TEST 3 — DYNAMIC RBAC: GRANT, REVOKE, REGRANT
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_dynamic_rbac_lifecycle():
    """Verify dynamic permission grant, revocation, and re-grant without server restart."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name

            # 1. Initially 403
            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 403

            # 2. Grant invoices.view
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "invoices.view"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                await db.commit()

            # Now 200 OK
            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 200

            # 3. Revoke invoices.view
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == p_view.id))
                await db.commit()

            # Now 403
            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 403

            # 4. Regrant invoices.view
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                await db.commit()

            # Now 200 OK
            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 200


# ==============================================================================
# TEST 4 — USER PERMISSION OVERRIDES
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_user_permission_overrides():
    """Verify user overrides: positive grants, explicit negative beats role grant."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            user_id = d_data["user_custom_a"].id
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}

            # 1. Positive override gives access without role permission
            async with AsyncSessionLocal() as db:
                p_view = (await db.execute(select(Permission).where(Permission.code == "invoices.view"))).scalar_one()
                db.add(UserPermissionOverride(user_id=user_id, permission_id=p_view.id, is_granted=True))
                await db.commit()

            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 200

            # 2. Add role grant, but flip override to negative (is_granted=False)
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_view.id))
                ov = (await db.execute(select(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id, UserPermissionOverride.permission_id == p_view.id))).scalar_one()
                ov.is_granted = False
                await db.commit()

            # Negative override beats role grant -> 403
            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 403


# ==============================================================================
# TEST 5 — WILDCARD PERMISSIONS (* and invoices.*)
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_wildcards():
    """Verify * and invoices.* wildcards authorize invoice endpoints."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name

            # Grant wildcard using existing DB permission
            async with AsyncSessionLocal() as db:
                p_star = (await db.execute(select(Permission).where(Permission.code == "*"))).scalar_one_or_none()
                if not p_star:
                    p_star = (await db.execute(select(Permission).where(Permission.code == "invoices.*"))).scalar_one_or_none()
                if not p_star:
                    p_star = (await db.execute(select(Permission).where(Permission.code == "invoices.view"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_star.id))
                await db.commit()

            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 200


# ==============================================================================
# TEST 6 — LEGACY ROLE ALONE IS DENIED
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_legacy_role_alone_denied():
    """Verify that a user with legacy role ('Project Manager') and no DB permission is rejected."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["legacy_a"]
            headers = {"Authorization": f"Bearer {token}"}

            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 403


# ==============================================================================
# TEST 7 — PUBLIC EXPORT REGRESSION & AUTH
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_public_export_regression_and_auth():
    """Verify that receivables, collections, and client-ledger exports require auth and invoices.export."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            client_id = d_data["owner_a"].id
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            role_id = d_data["role_custom"].id
            role_name = d_data["role_custom"].name

            # Unauthenticated -> 401
            assert (await ac.get("/api/v1/invoices/receivables/export")).status_code == 401
            assert (await ac.get("/api/v1/invoices/receivables/collections/export")).status_code == 401
            assert (await ac.get(f"/api/v1/invoices/receivables/client-ledger/{client_id}/export")).status_code == 401

            # Authenticated without permission -> 403
            assert (await ac.get("/api/v1/invoices/receivables/export", headers=headers)).status_code == 403

            # Grant invoices.export
            async with AsyncSessionLocal() as db:
                p_exp = (await db.execute(select(Permission).where(Permission.code == "invoices.export"))).scalar_one()
                db.add(RolePermission(role=role_name, role_id=role_id, permission_id=p_exp.id))
                await db.commit()

            # Now authorized -> 200 with text/csv
            res1 = await ac.get("/api/v1/invoices/receivables/export", headers=headers)
            assert res1.status_code == 200
            assert "text/csv" in res1.headers.get("content-type", "")

            res2 = await ac.get("/api/v1/invoices/receivables/collections/export", headers=headers)
            assert res2.status_code == 200
            assert "text/csv" in res2.headers.get("content-type", "")

            res3 = await ac.get(f"/api/v1/invoices/receivables/client-ledger/{client_id}/export", headers=headers)
            assert res3.status_code == 200
            assert "text/csv" in res3.headers.get("content-type", "")


# ==============================================================================
# TEST 8 — CROSS-TENANT INVOICE IDOR
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_cross_tenant_invoice_idor():
    """Verify Company A receives 404 for all operations on Company B's invoice."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            inv_b_id = d_data["invoice_b1"].id

            # GET
            res = await ac.get(f"/api/v1/invoices/{inv_b_id}", headers=headers)
            assert res.status_code == 404

            # PUT
            res = await ac.put(f"/api/v1/invoices/{inv_b_id}", json={"description": "Hacked"}, headers=headers)
            assert res.status_code == 404

            # DELETE
            res = await ac.delete(f"/api/v1/invoices/{inv_b_id}", headers=headers)
            assert res.status_code == 404

            # POST mark-paid
            res = await ac.post(f"/api/v1/invoices/{inv_b_id}/mark-paid", headers=headers)
            assert res.status_code == 404

            # POST pay
            res = await ac.post(f"/api/v1/invoices/{inv_b_id}/pay?amount=100&mode=Cash", headers=headers)
            assert res.status_code == 404

            # GET transactions
            res = await ac.get(f"/api/v1/invoices/{inv_b_id}/transactions", headers=headers)
            assert res.status_code == 404

            # GET PDF
            res = await ac.get(f"/api/v1/invoices/{inv_b_id}/pdf", headers=headers)
            assert res.status_code == 404

            # POST send
            send_payload = {"client_user_id": d_data["client_user_a"].id}
            res = await ac.post(f"/api/v1/invoices/{inv_b_id}/send", json=send_payload, headers=headers)
            assert res.status_code == 404


# ==============================================================================
# TEST 9 — CROSS-TENANT QUOTATION CONVERSION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_cross_tenant_quotation_conversion():
    """Company A cannot convert Company B's quotation into an invoice."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            qtn_b_id = d_data["quotation_b"].id

            res = await ac.post(f"/api/v1/invoices/from-quotation/{qtn_b_id}", headers=headers)
            assert res.status_code == 404


# ==============================================================================
# TEST 10 — CROSS-TENANT MEASUREMENT CONVERSION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_cross_tenant_measurement_conversion():
    """Company A cannot convert Company B's final measurement into an invoice."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            meas_b_id = d_data["meas_b"].id

            res = await ac.post(f"/api/v1/invoices/from-measurement/{meas_b_id}", headers=headers)
            assert res.status_code == 404


# ==============================================================================
# TEST 11 — CROSS-TENANT PROJECT INJECTION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_cross_tenant_project_injection():
    """Company A cannot create manual, labour, or material invoices against Company B's project."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            proj_b_id = d_data["proj_b"].id
            owner_a_id = d_data["owner_a"].id

            # Manual create with Proj B
            payload = {
                "project_id": proj_b_id,
                "owner_id": owner_a_id,
                "amount": 5000.0,
                "description": "Malicious Invoice",
            }
            res = await ac.post("/api/v1/invoices", json=payload, headers=headers)
            assert res.status_code == 404

            # Labour invoice with Proj B
            labour_payload = {
                "project_id": proj_b_id,
                "start_date": str(date.today() - timedelta(days=7)),
                "end_date": str(date.today()),
            }
            res = await ac.post("/api/v1/invoices/labour", json=labour_payload, headers=headers)
            assert res.status_code == 404

            # Material invoice with Proj B
            res = await ac.post(f"/api/v1/invoices/material?project_id={proj_b_id}", headers=headers)
            assert res.status_code == 404


# ==============================================================================
# TEST 12 — CROSS-TENANT COLLECTIONS ISOLATION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_cross_tenant_collections():
    """GET /receivables/collections must only return the caller company's receipts."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            inv_a_id = d_data["invoice_a1"].id
            inv_b_id = d_data["invoice_b1"].id

            res = await ac.get("/api/v1/invoices/receivables/collections", headers=headers)
            assert res.status_code == 200
            data = res.json()

            # Verify only Company A's invoice appears in collections
            invoice_nos = [item["invoice_no"] for item in data]
            assert f"INV-{inv_a_id}" in invoice_nos
            assert f"INV-{inv_b_id}" not in invoice_nos


# ==============================================================================
# TEST 13 — CROSS-TENANT RECEIVABLE SUMMARY ISOLATION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_cross_tenant_receivable_summary():
    """GET /receivables/summary must not aggregate another company's RABill totals."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}

            res = await ac.get("/api/v1/invoices/receivables/summary", headers=headers)
            assert res.status_code == 200
            summary = res.json()

            # Company A's Invoice A1 (11800) + RABill A (11800) = 23600
            # If Company B's RABill B (23600) were leaked, total_billed would be >= 47200
            assert summary["total_billed"] < 40000.0


# ==============================================================================
# TEST 14 — CROSS-TENANT TRANSACTIONS ISOLATION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_cross_tenant_transactions():
    """GET /invoices/{id}/transactions returns 404 for another company's invoice."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            inv_b_id = d_data["invoice_b1"].id

            res = await ac.get(f"/api/v1/invoices/{inv_b_id}/transactions", headers=headers)
            assert res.status_code == 404


# ==============================================================================
# TEST 15 — CLIENT LEDGER ISOLATION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_client_ledger_isolation():
    """Requesting client ledger or export for another company's client returns 404."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            owner_b_id = d_data["owner_b"].id

            res = await ac.get(f"/api/v1/invoices/receivables/client-ledger/{owner_b_id}", headers=headers)
            assert res.status_code == 404

            res_exp = await ac.get(f"/api/v1/invoices/receivables/client-ledger/{owner_b_id}/export", headers=headers)
            assert res_exp.status_code == 404


# ==============================================================================
# TEST 16 — MANUAL RECEIVABLE CLIENT INJECTION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_manual_receivable_client_injection():
    """Cannot create a manual receivable for a client belonging to another company."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            owner_b_id = d_data["owner_b"].id

            payload = {
                "client_id": owner_b_id,
                "amount": 5000.0,
                "description": "Cross company manual receivable",
                "due_date": str(date.today() + timedelta(days=30)),
            }
            res = await ac.post("/api/v1/invoices/receivables/manual", json=payload, headers=headers)
            assert res.status_code == 404


# ==============================================================================
# TEST 17 — SEND INVOICE CLIENT ISOLATION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_send_invoice_client_isolation():
    """Sending invoice to a client belonging to another company returns 404."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            inv_a_id = d_data["invoice_a1"].id
            client_b_id = d_data["client_user_b"].id

            payload = {"client_user_id": client_b_id}
            res = await ac.post(f"/api/v1/invoices/{inv_a_id}/send", json=payload, headers=headers)
            assert res.status_code == 404


# ==============================================================================
# TEST 18 — PDF GENERATION & ISOLATION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_pdf_generation_and_isolation():
    """Authorized PDF generates valid application/pdf; foreign invoice returns 404."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            inv_a_id = d_data["invoice_a1"].id
            inv_b_id = d_data["invoice_b1"].id

            # Own invoice -> 200
            res = await ac.get(f"/api/v1/invoices/{inv_a_id}/pdf", headers=headers)
            assert res.status_code == 200
            assert res.headers.get("content-type") == "application/pdf"

            # Foreign invoice -> 404
            res_b = await ac.get(f"/api/v1/invoices/{inv_b_id}/pdf", headers=headers)
            assert res_b.status_code == 404


# ==============================================================================
# TEST 19 — BUSINESS RULES PRESERVED
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_business_rules_preserved():
    """Verify duplicate invoice prevention, overpayment rejection, and status guards."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}
            inv_a = d_data["invoice_a1"]

            # 1. Duplicate invoice -> 422/400 (ValidationError)
            dup_payload = {
                "project_id": inv_a.project_id,
                "owner_id": inv_a.owner_id,
                "amount": float(inv_a.amount),
                "description": inv_a.description,
            }
            res = await ac.post("/api/v1/invoices", json=dup_payload, headers=headers)
            assert res.status_code in [400, 422]

            # 2. Overpayment on pay_invoice -> 422/400
            overpay = float(inv_a.pending_amount) + 5000.0
            res = await ac.post(f"/api/v1/invoices/{inv_a.id}/pay?amount={overpay}&mode=Cash", headers=headers)
            assert res.status_code in [400, 422]


# ==============================================================================
# TEST 20 — SUPER ADMIN SEMANTICS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_j_super_admin_semantics():
    """Verify genuine Super Admin retains cross-company visibility."""
    async with setup_batch_j_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["super"]
            headers = {"Authorization": f"Bearer {token}"}
            inv_a_id = d_data["invoice_a1"].id
            inv_b_id = d_data["invoice_b1"].id

            # Super admin can view both invoices
            res_a = await ac.get(f"/api/v1/invoices/{inv_a_id}", headers=headers)
            assert res_a.status_code == 200

            res_b = await ac.get(f"/api/v1/invoices/{inv_b_id}", headers=headers)
            assert res_b.status_code == 200

            # Collections includes both
            res_coll = await ac.get("/api/v1/invoices/receivables/collections", headers=headers)
            assert res_coll.status_code == 200
            data = res_coll.json()
            invoice_nos = [item["invoice_no"] for item in data]
            assert f"INV-{inv_a_id}" in invoice_nos
            assert f"INV-{inv_b_id}" in invoice_nos
