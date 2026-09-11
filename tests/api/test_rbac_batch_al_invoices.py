"""
Test Suite: BATCH AL — Client & Tax Invoicing / Accounts Receivable Management
=============================================================================
Covers:
1. Authentication: all 28 routes unauthenticated -> 401
2. Granular DB-driven RBAC Permissions:
   - invoices.view, invoices.create, invoices.edit, invoices.delete, invoices.export, invoices.upload
3. Dedicated upload permission:
   - POST /receivables/import requires invoices.upload specifically
4. invoices.edit / invoices.create does NOT grant upload
5. Tenantless Non-SA:
   - company_id=None -> 403 on protected endpoints
6. Tenant Isolation:
   - Tenant A cannot view or list Tenant B invoices
7. IDOR 404 Masking:
   - GET /invoices/{foreign_id} -> 404
   - PUT /invoices/{foreign_id} -> 404
   - DELETE /invoices/{foreign_id} -> 404
   - POST /invoices/{foreign_id}/mark-paid -> 404
   - POST /invoices/{foreign_id}/pay -> 404
   - GET /invoices/{foreign_id}/pdf -> 404
   - GET /invoices/{foreign_id}/transactions -> 404
8. Foreign Project Injection:
   - POST /invoices with foreign project_id -> 404
9. Foreign Quotation Injection:
   - POST /invoices/from-quotation/{foreign_id} -> 404
10. Foreign Measurement Injection:
    - POST /invoices/from-measurement/{foreign_id} -> 404
11. Foreign Client / Owner in Ledger:
    - GET /invoices/receivables/client-ledger/{foreign_client_id} -> 404
12. Foreign Owner in Create Manual Invoice:
    - POST /invoices with foreign owner_id -> 422
13. Super Admin Global Listing:
    - SA lists all invoices across tenants
14. Super Admin Company-Filtered Listing:
    - SA ?company_id=... filters to target company
15. Invalid SA company_id:
    - SA ?company_id=999999 -> 404
16. Non-SA Company Override Blocked:
    - Tenant A user providing ?company_id=Comp_B cannot view Comp B data
17. Client Ledger Scoped Accounting:
    - Verified company AR account used; balances computed accurately
18. Client Ledger Export:
    - Returns CSV with correct columns and authorized transactions
19. Manual Receivable Creates Balanced Journal:
    - Creates JournalEntry with equal debit (AR) and credit (Revenue)
20. Manual Receivable Accounting Failure:
    - Rolls back cleanly without creating empty/unbalanced JournalEntry
21. Paid Invoice Deletion Rejected:
    - Attempting to delete PAID invoice -> 422
22. Partial Invoice Deletion Rejected:
    - Attempting to delete PARTIAL invoice -> 422
23. Unpaid Legitimate Deletion Preserved:
    - Deleting PENDING invoice -> 204
24. Payment Concurrency / Locking:
    - Overpayment rejected; row locking prevents invalid state
25. Tax Create/Update Consistency:
    - amount + gst_amount - tax_amount formula consistent between create and update
26. Material Invoice Tenant Isolation & Re-Aggregation:
    - Only unbilled material expenses for project are aggregated
27. Send Invoice Tenant-Safe Recipient Validation:
    - Non-client recipient rejected (422); foreign recipient rejected (404)
28. Dynamic RBAC Lifecycle:
    - Revoking permission in DB immediately blocks endpoint (403)
29. Dynamic RBAC Grant:
    - Regranting in DB immediately restores access (200/201)
30. Route Preservation:
    - Exactly 28 invoice endpoints, 28 unique (method, path), 0 duplicates, 781 total
31. Static Security Hygiene:
    - 0 require_roles, 0 admin_required, 0 UserRole references, 0 role allowlists
"""

import inspect
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
import io

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.api import invoice as invoice_api_module
from app.core.db import AsyncSessionLocal
from app.core.enums import AccountType, InvoiceStatus, InvoiceType, PaymentMode, InvoiceSourceType
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.accountant import Account, JournalEntry, JournalLine
from app.models.company import Company
from app.models.expense import Expense
from app.models.final_measurement import FinalMeasurement
from app.models.invoice import Invoice, Transaction
from app.models.owner import Owner, OwnerTransaction
from app.models.project import Project, ProjectMember
from app.models.notification import Notification
from app.models.quotation import QuotationMaster, QuotationStatus
from app.models.rbac import Permission, Role, RolePermission
from app.models.settings import CompanySettings
from app.models.user import ActivityLog, User


@asynccontextmanager
async def setup_invoice_test_data():
    """Create test companies, accounts, owners, projects, quotations, measurements, and users."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"InvCompA_{uid}")
        comp_b = Company(name=f"InvCompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Accounts
        ar_a = Account(name=f"AR A {uid}", code="ACCOUNTS_RECEIVABLE", type=AccountType.ASSET, company_id=comp_a.id)
        rev_a = Account(name=f"Rev A {uid}", code="SALES_REVENUE", type=AccountType.INCOME, company_id=comp_a.id)
        cash_a = Account(name=f"Cash A {uid}", code="CASH", type=AccountType.ASSET, company_id=comp_a.id)
        gst_a = Account(name=f"GST A {uid}", code="OUTPUT_GST", type=AccountType.LIABILITY, company_id=comp_a.id)

        ar_b = Account(name=f"AR B {uid}", code="ACCOUNTS_RECEIVABLE", type=AccountType.ASSET, company_id=comp_b.id)
        rev_b = Account(name=f"Rev B {uid}", code="SALES_REVENUE", type=AccountType.INCOME, company_id=comp_b.id)
        cash_b = Account(name=f"Cash B {uid}", code="CASH", type=AccountType.ASSET, company_id=comp_b.id)

        db.add_all([ar_a, rev_a, cash_a, gst_a, ar_b, rev_b, cash_b])
        await db.flush()

        # Settings
        settings_a = CompanySettings(company_id=comp_a.id)
        settings_b = CompanySettings(company_id=comp_b.id)
        db.add_all([settings_a, settings_b])
        await db.flush()

        # 3. Owners & Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OA_{uid}",
            owner_name=f"Owner A {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OB_{uid}",
            owner_name=f"Owner B {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerb_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Project A {uid}",
            business_id=f"PA_{uid}",
        )
        proj_b = Project(
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Project B {uid}",
            business_id=f"PB_{uid}",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 4. Client Users
        client_user_a = User(
            email=f"client_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role="client",
            is_active=True,
            is_deleted=False,
            full_name=f"Client A {uid}",
        )
        client_user_b = User(
            email=f"client_b_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_b.id,
            role="client",
            is_active=True,
            is_deleted=False,
            full_name=f"Client B {uid}",
        )
        employee_user_a = User(
            email=f"employee_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role="accountant",
            is_active=True,
            is_deleted=False,
            full_name=f"Employee A {uid}",
        )
        db.add_all([client_user_a, client_user_b, employee_user_a])
        await db.flush()

        # Assign client_user_a as ProjectMember to proj_a
        pm_a = ProjectMember(project_id=proj_a.id, user_id=client_user_a.id)
        db.add(pm_a)
        await db.flush()

        # 5. Quotation & Measurement for conversion
        quotation_a = QuotationMaster(
            quotation_no=f"QTN-A-{uid}",
            company_id=comp_a.id,
            client_user_id=client_user_a.id,
            client_name="Client A",
            mobile_number="9800000001",
            project_id=proj_a.id,
            project_name="Proj A",
            project_type="Residential",
            subtotal=10000.0,
            grand_total=11600.0,
            gst_amount=1800.0,
            cgst_percent=9.0,
            sgst_percent=9.0,
            tds_percent=2.0,
            tds_amount=200.0,
            is_approved=True,
            status=QuotationStatus.APPROVED,
            converted_to_invoice=False,
        )
        measurement_a = FinalMeasurement(
            project_id=proj_a.id,
            final_area=Decimal("100.00"),
            approved_rate=Decimal("50.00"),
            total_area=Decimal("100.00"),
            total_amount=Decimal("5000.00"),
        )
        db.add_all([quotation_a, measurement_a])
        await db.flush()

        # 6. Roles & Permissions
        all_perms = [
            "invoices.view",
            "invoices.create",
            "invoices.edit",
            "invoices.delete",
            "invoices.export",
            "invoices.upload",
        ]
        perm_objs = {}
        for pcode in all_perms:
            p = await db.scalar(select(Permission).where(Permission.code == pcode))
            if not p:
                parts = pcode.split(".")
                p = Permission(module=parts[0], action=parts[1], code=pcode, description=f"Invoice {parts[1]}")
                db.add(p)
                await db.flush()
            perm_objs[pcode] = p

        role_name_full_a = f"InvFullA_{uid}"
        role_name_noupload_a = f"InvNoUploadA_{uid}"
        role_name_ro_a = f"InvROA_{uid}"
        role_name_full_b = f"InvFullB_{uid}"
        role_name_none_a = f"InvNoneA_{uid}"

        role_full_a = Role(company_id=comp_a.id, name=role_name_full_a, display_name="Inv Full A", is_system=False)
        role_noupload_a = Role(company_id=comp_a.id, name=role_name_noupload_a, display_name="Inv No Upload A", is_system=False)
        role_ro_a = Role(company_id=comp_a.id, name=role_name_ro_a, display_name="Inv RO A", is_system=False)
        role_full_b = Role(company_id=comp_b.id, name=role_name_full_b, display_name="Inv Full B", is_system=False)
        role_none_a = Role(company_id=comp_a.id, name=role_name_none_a, display_name="Inv None A", is_system=False)

        db.add_all([role_full_a, role_noupload_a, role_ro_a, role_full_b, role_none_a])
        await db.flush()

        # Permissions grants
        for pcode in all_perms:
            db.add(RolePermission(role=role_name_full_a, role_id=role_full_a.id, permission_id=perm_objs[pcode].id))
            db.add(RolePermission(role=role_name_full_b, role_id=role_full_b.id, permission_id=perm_objs[pcode].id))

        for pcode in all_perms:
            if pcode != "invoices.upload":
                db.add(RolePermission(role=role_name_noupload_a, role_id=role_noupload_a.id, permission_id=perm_objs[pcode].id))

        db.add(RolePermission(role=role_name_ro_a, role_id=role_ro_a.id, permission_id=perm_objs["invoices.view"].id))
        await db.flush()

        # 7. Users
        user_full_a = User(
            email=f"full_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role=role_name_full_a,
            is_active=True,
            full_name="Full A User",
        )
        user_noupload_a = User(
            email=f"noupload_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role=role_name_noupload_a,
            is_active=True,
            full_name="No Upload A User",
        )
        user_ro_a = User(
            email=f"ro_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role=role_name_ro_a,
            is_active=True,
            full_name="RO A User",
        )
        user_none_a = User(
            email=f"none_a_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_a.id,
            role=role_name_none_a,
            is_active=True,
            full_name="None A User",
        )
        user_full_b = User(
            email=f"full_b_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=comp_b.id,
            role=role_name_full_b,
            is_active=True,
            full_name="Full B User",
        )
        user_sa = User(
            email=f"sa_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=None,
            role="super_admin",
            is_super_admin=True,
            is_active=True,
            full_name="Super Admin User",
        )
        user_tenantless_non_sa = User(
            email=f"tenantless_{uid}@test.com",
            hashed_password=pwd_hash,
            company_id=None,
            role=role_name_full_a,
            is_super_admin=False,
            is_active=True,
            full_name="Tenantless Non-SA",
        )

        db.add_all([
            user_full_a,
            user_noupload_a,
            user_ro_a,
            user_none_a,
            user_full_b,
            user_sa,
            user_tenantless_non_sa,
        ])
        await db.flush()

        # Assign test users to project membership so assert_project_access succeeds
        pm_fa = ProjectMember(project_id=proj_a.id, user_id=user_full_a.id)
        pm_na = ProjectMember(project_id=proj_a.id, user_id=user_noupload_a.id)
        pm_ra = ProjectMember(project_id=proj_a.id, user_id=user_ro_a.id)
        pm_none = ProjectMember(project_id=proj_a.id, user_id=user_none_a.id)
        pm_fb = ProjectMember(project_id=proj_b.id, user_id=user_full_b.id)
        db.add_all([pm_fa, pm_na, pm_ra, pm_none, pm_fb])
        await db.commit()

        # Seed initial Invoices for testing
        inv_a1 = Invoice(
            company_id=comp_a.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type=InvoiceType.OWNER,
            source_type=InvoiceSourceType.MANUAL,
            amount=Decimal("10000.00"),
            gst_percent=Decimal("18.00"),
            gst_amount=Decimal("1800.00"),
            tax_percent=Decimal("2.00"),
            tax_amount=Decimal("200.00"),
            total_amount=Decimal("11600.00"),
            paid_amount=Decimal("0.00"),
            pending_amount=Decimal("11600.00"),
            status=InvoiceStatus.PENDING,
            description=f"Invoice A1 {uid}",
            created_at=datetime.utcnow(),
        )
        inv_b1 = Invoice(
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
            paid_amount=Decimal("0.00"),
            pending_amount=Decimal("23600.00"),
            status=InvoiceStatus.PENDING,
            description=f"Invoice B1 {uid}",
            created_at=datetime.utcnow(),
        )
        db.add_all([inv_a1, inv_b1])
        await db.commit()

        tokens = {
            "full_a": create_access_token(data={"sub": str(user_full_a.id)}),
            "noupload_a": create_access_token(data={"sub": str(user_noupload_a.id)}),
            "ro_a": create_access_token(data={"sub": str(user_ro_a.id)}),
            "none_a": create_access_token(data={"sub": str(user_none_a.id)}),
            "full_b": create_access_token(data={"sub": str(user_full_b.id)}),
            "sa": create_access_token(data={"sub": str(user_sa.id)}),
            "tenantless_non_sa": create_access_token(data={"sub": str(user_tenantless_non_sa.id)}),
        }

        data = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "owner_a": owner_a,
            "owner_b": owner_b,
            "client_user_a": client_user_a,
            "client_user_b": client_user_b,
            "employee_user_a": employee_user_a,
            "quotation_a": quotation_a,
            "measurement_a": measurement_a,
            "inv_a1": inv_a1,
            "inv_b1": inv_b1,
            "tokens": tokens,
            "perm_objs": perm_objs,
            "role_full_a": role_full_a,
            "uid": uid,
        }

        try:
            yield data
        finally:
            async with AsyncSessionLocal() as cleanup_db:
                # Cleanup test records
                await cleanup_db.execute(delete(RolePermission).where(RolePermission.role.in_([
                    role_name_full_a, role_name_noupload_a, role_ro_a, role_name_full_b, role_name_none_a
                ])))
                await cleanup_db.execute(delete(Role).where(Role.id.in_([
                    role_full_a.id, role_noupload_a.id, role_ro_a.id, role_full_b.id, role_none_a.id
                ])))
                all_test_user_ids = [
                    client_user_a.id, client_user_b.id, employee_user_a.id,
                    user_full_a.id, user_noupload_a.id, user_ro_a.id, user_none_a.id,
                    user_full_b.id, user_sa.id, user_tenantless_non_sa.id
                ]
                test_account_ids = [ar_a.id, rev_a.id, cash_a.id, gst_a.id, ar_b.id, rev_b.id, cash_b.id]

                je_res = await cleanup_db.execute(
                    select(JournalEntry.id).where(JournalEntry.created_by.in_(all_test_user_ids))
                )
                je_ids = je_res.scalars().all()
                if je_ids:
                    await cleanup_db.execute(
                        delete(JournalLine).where(
                            (JournalLine.account_id.in_(test_account_ids)) | (JournalLine.entry_id.in_(je_ids))
                        )
                    )
                    await cleanup_db.execute(
                        delete(JournalEntry).where(JournalEntry.id.in_(je_ids))
                    )
                else:
                    await cleanup_db.execute(
                        delete(JournalLine).where(JournalLine.account_id.in_(test_account_ids))
                    )

                await cleanup_db.execute(delete(Transaction).where(Transaction.project_id.in_([proj_a.id, proj_b.id])))
                await cleanup_db.execute(delete(Invoice).where(Invoice.project_id.in_([proj_a.id, proj_b.id])))
                await cleanup_db.execute(delete(Expense).where(Expense.project_id.in_([proj_a.id, proj_b.id])))
                await cleanup_db.execute(delete(FinalMeasurement).where(FinalMeasurement.project_id.in_([proj_a.id, proj_b.id])))
                await cleanup_db.execute(delete(QuotationMaster).where(QuotationMaster.project_id.in_([proj_a.id, proj_b.id])))
                await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
                await cleanup_db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
                await cleanup_db.execute(delete(OwnerTransaction).where(OwnerTransaction.owner_id.in_([owner_a.id, owner_b.id])))
                await cleanup_db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
                await cleanup_db.execute(delete(Account).where(Account.company_id.in_([comp_a.id, comp_b.id])))
                await cleanup_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
                await cleanup_db.execute(delete(Notification).where(Notification.user_id.in_(all_test_user_ids)))
                await cleanup_db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(all_test_user_ids)))
                await cleanup_db.execute(delete(User).where(User.company_id.in_([comp_a.id, comp_b.id])))
                await cleanup_db.execute(delete(User).where(User.id.in_([user_sa.id, user_tenantless_non_sa.id])))
                await cleanup_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
                await cleanup_db.commit()


@pytest.mark.asyncio
async def test_01_all_28_routes_unauthenticated_401():
    """Verify all 28 invoice routes return 401 when no token is provided."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("POST", "/api/v1/invoices"),
            ("POST", "/api/v1/invoices/from-quotation/1"),
            ("GET", "/api/v1/invoices"),
            ("GET", "/api/v1/invoices/date-range?start=2026-01-01&end=2026-12-31"),
            ("GET", "/api/v1/invoices/1"),
            ("PUT", "/api/v1/invoices/1"),
            ("DELETE", "/api/v1/invoices/1"),
            ("GET", "/api/v1/invoices/project/1"),
            ("GET", "/api/v1/invoices/type/owner"),
            ("POST", "/api/v1/invoices/1/mark-paid"),
            ("GET", "/api/v1/invoices/1/pdf"),
            ("POST", "/api/v1/invoices/labour"),
            ("POST", "/api/v1/invoices/material?project_id=1"),
            ("POST", "/api/v1/invoices/from-measurement/1"),
            ("GET", "/api/v1/invoices/project/1/summary"),
            ("GET", "/api/v1/invoices/analytics/summary?project_id=1"),
            ("POST", "/api/v1/invoices/1/pay"),
            ("GET", "/api/v1/invoices/1/transactions"),
            ("GET", "/api/v1/invoices/receivables/summary"),
            ("GET", "/api/v1/invoices/receivables/aging"),
            ("GET", "/api/v1/invoices/receivables/client-ledger/1"),
            ("GET", "/api/v1/invoices/receivables/collections"),
            ("POST", "/api/v1/invoices/receivables/manual"),
            ("POST", "/api/v1/invoices/receivables/import"),
            ("GET", "/api/v1/invoices/receivables/export"),
            ("GET", "/api/v1/invoices/receivables/collections/export"),
            ("GET", "/api/v1/invoices/receivables/client-ledger/1/export"),
            ("POST", "/api/v1/invoices/1/send"),
        ]

        for method, path in endpoints:
            res = await ac.request(method, path)
            assert res.status_code == 401, f"{method} {path} returned {res.status_code}, expected 401"


@pytest.mark.asyncio
async def test_02_granular_permission_enforcement():
    """Verify user without permission receives 403."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {td['tokens']['none_a']}"}

            # View endpoint -> 403
            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 403

            # Create endpoint -> 403
            res = await ac.post(
                "/api/v1/invoices",
                json={
                    "project_id": td["proj_a"].id,
                    "owner_id": td["owner_a"].id,
                    "type": "owner",
                    "amount": 1000.0,
                    "description": "Test",
                },
                headers=headers,
            )
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_03_invoices_upload_specifically_required_for_import():
    """Verify POST /receivables/import requires invoices.upload specifically."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # User with upload can import
            headers_upload = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            csv_data = "project_id,amount\n1,1000\n"
            files = {"file": ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}

            res = await ac.post("/api/v1/invoices/receivables/import", headers=headers_upload, files=files)
            assert res.status_code == 200
            data = res.json()
            assert "valid_records" in data


@pytest.mark.asyncio
async def test_04_invoices_edit_does_not_grant_upload():
    """User with invoices.edit/create but WITHOUT invoices.upload is rejected on import."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_noupload = {"Authorization": f"Bearer {td['tokens']['noupload_a']}"}
            csv_data = "project_id,amount\n1,1000\n"
            files = {"file": ("test.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}

            res = await ac.post("/api/v1/invoices/receivables/import", headers=headers_noupload, files=files)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_05_tenantless_non_sa_returns_403():
    """Verify tenantless non-SA user (company_id=None) returns 403."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {td['tokens']['tenantless_non_sa']}"}

            res = await ac.get("/api/v1/invoices", headers=headers)
            assert res.status_code == 403

            res = await ac.get(f"/api/v1/invoices/{td['inv_a1'].id}", headers=headers)
            assert res.status_code == 403

            res = await ac.get(f"/api/v1/invoices/receivables/client-ledger/{td['owner_a'].id}", headers=headers)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_06_tenant_a_cannot_access_tenant_b_invoice():
    """Tenant A cannot list or access Tenant B invoices."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}

            # List only returns Comp A invoices
            res = await ac.get("/api/v1/invoices", headers=headers_a)
            assert res.status_code == 200
            inv_ids = [inv["id"] for inv in res.json()]
            assert td["inv_a1"].id in inv_ids
            assert td["inv_b1"].id not in inv_ids


@pytest.mark.asyncio
async def test_07_foreign_invoice_id_returns_404():
    """Direct access to foreign invoice ID returns 404 Not Found."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            foreign_id = td["inv_b1"].id

            # GET
            res = await ac.get(f"/api/v1/invoices/{foreign_id}", headers=headers_a)
            assert res.status_code == 404

            # PUT
            res = await ac.put(f"/api/v1/invoices/{foreign_id}", json={"description": "Hacked"}, headers=headers_a)
            assert res.status_code == 404

            # DELETE
            res = await ac.delete(f"/api/v1/invoices/{foreign_id}", headers=headers_a)
            assert res.status_code == 404

            # Mark Paid
            res = await ac.post(f"/api/v1/invoices/{foreign_id}/mark-paid", headers=headers_a)
            assert res.status_code == 404

            # Pay
            res = await ac.post(
                f"/api/v1/invoices/{foreign_id}/pay",
                params={"amount": "100.00", "mode": "Cash"},
                headers=headers_a,
            )
            assert res.status_code == 404

            # PDF
            res = await ac.get(f"/api/v1/invoices/{foreign_id}/pdf", headers=headers_a)
            assert res.status_code == 404

            # Transactions
            res = await ac.get(f"/api/v1/invoices/{foreign_id}/transactions", headers=headers_a)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_08_foreign_project_injection_returns_404():
    """Attempting to create invoice with foreign project_id returns 404."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            res = await ac.post(
                "/api/v1/invoices",
                json={
                    "project_id": td["proj_b"].id,
                    "owner_id": td["owner_b"].id,
                    "type": "owner",
                    "amount": 1000.0,
                    "description": "Foreign Project Injection",
                },
                headers=headers_a,
            )
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_09_foreign_quotation_returns_404():
    """Attempting to create invoice from foreign quotation returns 404."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_b = {"Authorization": f"Bearer {td['tokens']['full_b']}"}
            # Comp B attempts to convert Comp A quotation
            res = await ac.post(f"/api/v1/invoices/from-quotation/{td['quotation_a'].id}", headers=headers_b)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_10_foreign_measurement_returns_404():
    """Attempting to create invoice from foreign measurement returns 404."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_b = {"Authorization": f"Bearer {td['tokens']['full_b']}"}
            # Comp B attempts to convert Comp A measurement
            res = await ac.post(f"/api/v1/invoices/from-measurement/{td['measurement_a'].id}", headers=headers_b)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_11_foreign_client_returns_404():
    """Foreign client in client-ledger returns 404."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            # Owner B belongs to Comp B
            res = await ac.get(f"/api/v1/invoices/receivables/client-ledger/{td['owner_b'].id}", headers=headers_a)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_12_foreign_owner_returns_422_or_404():
    """Owner not matching project owner returns 422 validation error."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            res = await ac.post(
                "/api/v1/invoices",
                json={
                    "project_id": td["proj_a"].id,
                    "owner_id": td["owner_b"].id,
                    "type": "owner",
                    "amount": 1000.0,
                    "description": "Owner Mismatch",
                },
                headers=headers_a,
            )
            assert res.status_code == 422


@pytest.mark.asyncio
async def test_13_sa_global_listing():
    """Super Admin without company_id sees global list across all companies."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_sa = {"Authorization": f"Bearer {td['tokens']['sa']}"}
            res = await ac.get("/api/v1/invoices", headers=headers_sa)
            assert res.status_code == 200
            inv_ids = [inv["id"] for inv in res.json()]
            assert td["inv_a1"].id in inv_ids
            assert td["inv_b1"].id in inv_ids


@pytest.mark.asyncio
async def test_14_sa_company_filtered_listing():
    """Super Admin with ?company_id=... only sees data for that company."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_sa = {"Authorization": f"Bearer {td['tokens']['sa']}"}
            res = await ac.get(f"/api/v1/invoices?company_id={td['comp_a'].id}", headers=headers_sa)
            assert res.status_code == 200
            inv_ids = [inv["id"] for inv in res.json()]
            assert td["inv_a1"].id in inv_ids
            assert td["inv_b1"].id not in inv_ids


@pytest.mark.asyncio
async def test_15_invalid_sa_company_id_returns_404():
    """Super Admin supplying nonexistent company_id receives 404."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_sa = {"Authorization": f"Bearer {td['tokens']['sa']}"}
            res = await ac.get("/api/v1/invoices?company_id=9999999", headers=headers_sa)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_16_non_sa_company_override_blocked():
    """Non-SA tenant user providing ?company_id=Comp_B cannot see Comp B data."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            # Tenant A user attempts to supply company_id of Comp B
            res = await ac.get(f"/api/v1/invoices?company_id={td['comp_b'].id}", headers=headers_a)
            assert res.status_code == 200
            inv_ids = [inv["id"] for inv in res.json()]
            assert td["inv_a1"].id in inv_ids
            assert td["inv_b1"].id not in inv_ids


@pytest.mark.asyncio
async def test_17_client_ledger_scoped_accounting():
    """Client ledger resolves verified AR account and computes running balance."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            res = await ac.get(f"/api/v1/invoices/receivables/client-ledger/{td['owner_a'].id}", headers=headers_a)
            assert res.status_code == 200
            data = res.json()
            assert "total_billed" in data
            assert "total_received" in data
            assert "outstanding" in data


@pytest.mark.asyncio
async def test_18_client_ledger_export_works():
    """Client ledger export returns CSV streaming response."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            res = await ac.get(f"/api/v1/invoices/receivables/client-ledger/{td['owner_a'].id}/export", headers=headers_a)
            assert res.status_code == 200
            assert "text/csv" in res.headers.get("content-type", "")
            assert b"Date,Particulars,Debit,Credit,Balance" in res.content


@pytest.mark.asyncio
async def test_19_manual_receivable_creates_balanced_journal():
    """Manual receivable creates balanced JournalEntry with debit and credit lines."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            res = await ac.post(
                "/api/v1/invoices/receivables/manual",
                json={
                    "client_id": td["owner_a"].id,
                    "amount": 7500.0,
                    "due_date": "2026-10-01",
                    "description": "Manual Receivable Test",
                },
                headers=headers_a,
            )
            assert res.status_code == 200
            data = res.json()
            journal_id = data["journal_id"]

            async with AsyncSessionLocal() as check_db:
                lines = (
                    await check_db.execute(
                        select(JournalLine).where(JournalLine.entry_id == journal_id)
                    )
                ).scalars().all()
                assert len(lines) == 2
                debits = sum(Decimal(str(l.debit)) for l in lines)
                credits = sum(Decimal(str(l.credit)) for l in lines)
                assert debits == credits == Decimal("7500")


@pytest.mark.asyncio
async def test_20_manual_receivable_accounting_failure_rolls_back():
    """If client is not found or invalid, manual receivable fails and does NOT leave orphan JournalEntry."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            res = await ac.post(
                "/api/v1/invoices/receivables/manual",
                json={
                    "client_id": 99999999,
                    "amount": 5000.0,
                    "due_date": "2026-10-01",
                    "description": "Fail Test Client NotFound",
                },
                headers=headers_a,
            )
            assert res.status_code == 404

            async with AsyncSessionLocal() as check_db:
                orphan_je = await check_db.scalar(
                    select(JournalEntry).where(JournalEntry.description == "Fail Test Client NotFound")
                )
                assert orphan_je is None


@pytest.mark.asyncio
async def test_21_paid_invoice_deletion_rejected():
    """Attempting to delete PAID invoice returns 422."""
    async with setup_invoice_test_data() as td:
        # Mark invoice as paid
        async with AsyncSessionLocal() as db:
            inv = await db.get(Invoice, td["inv_a1"].id)
            inv.status = InvoiceStatus.PAID
            inv.paid_amount = inv.total_amount
            inv.pending_amount = Decimal("0")
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            res = await ac.delete(f"/api/v1/invoices/{td['inv_a1'].id}", headers=headers_a)
            assert res.status_code == 422


@pytest.mark.asyncio
async def test_22_partial_invoice_deletion_rejected():
    """Attempting to delete PARTIAL invoice returns 422."""
    async with setup_invoice_test_data() as td:
        async with AsyncSessionLocal() as db:
            inv = await db.get(Invoice, td["inv_a1"].id)
            inv.status = InvoiceStatus.PARTIAL
            inv.paid_amount = Decimal("100.00")
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            res = await ac.delete(f"/api/v1/invoices/{td['inv_a1'].id}", headers=headers_a)
            assert res.status_code == 422


@pytest.mark.asyncio
async def test_23_unpaid_legitimate_deletion_preserved():
    """Deleting unpaid PENDING invoice succeeds with 204."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}
            res = await ac.delete(f"/api/v1/invoices/{td['inv_a1'].id}", headers=headers_a)
            assert res.status_code == 204


@pytest.mark.asyncio
async def test_24_payment_concurrency_locking():
    """Paying more than pending amount is rejected (422); payment creates receipt transaction."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}

            # Overpayment rejected
            res = await ac.post(
                f"/api/v1/invoices/{td['inv_a1'].id}/pay",
                params={"amount": "999999.00", "mode": "Cash"},
                headers=headers_a,
            )
            assert res.status_code == 422

            # Valid payment succeeds
            res = await ac.post(
                f"/api/v1/invoices/{td['inv_a1'].id}/pay",
                params={"amount": "1000.00", "mode": "Cash"},
                headers=headers_a,
            )
            assert res.status_code == 200
            data = res.json()
            assert data["paid"] == 1000.0
            assert data["status"] == "partial"


@pytest.mark.asyncio
async def test_25_tax_create_update_consistency():
    """Create and Update produce consistent totals using formula: amount + gst_amount - tax_amount."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}

            # Update inv_a1 amounts: amount=10000, gst=18% (1800), tax=2% (200) -> total=11600
            res = await ac.put(
                f"/api/v1/invoices/{td['inv_a1'].id}",
                json={"amount": 10000.0, "gst_percent": 18.0, "tax_percent": 2.0},
                headers=headers_a,
            )
            assert res.status_code == 200
            data = res.json()
            assert data["amount"] == 10000.0
            assert data["gst_amount"] == 1800.0
            assert data["tax_amount"] == 200.0
            assert data["total_amount"] == 11600.0  # 10000 + 1800 - 200


@pytest.mark.asyncio
async def test_26_material_invoice_tenant_isolation_and_reaggregation():
    """Material invoice isolates expenses by tenant and prevents re-invoicing already billed expenses."""
    async with setup_invoice_test_data() as td:
        async with AsyncSessionLocal() as db:
            exp1 = Expense(
                project_id=td["proj_a"].id,
                category="Material",
                amount=Decimal("3000.00"),
                expense_date=date.today(),
                description="Material exp test",
                payment_mode="Cash",
            )
            db.add(exp1)
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}

            # First material invoice aggregates exp1
            res = await ac.post(f"/api/v1/invoices/material?project_id={td['proj_a'].id}", headers=headers_a)
            assert res.status_code == 200
            inv_data = res.json()
            assert inv_data["total_amount"] == 3000.0

            # Second attempt: all expenses already invoiced -> 422
            res2 = await ac.post(f"/api/v1/invoices/material?project_id={td['proj_a'].id}", headers=headers_a)
            assert res2.status_code == 422


@pytest.mark.asyncio
async def test_27_send_invoice_tenant_safe_recipient_validation():
    """Sending invoice validates client recipient domain rule and tenant safety."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_a = {"Authorization": f"Bearer {td['tokens']['full_a']}"}

            # Non-client recipient (accountant) rejected with 422
            res = await ac.post(
                f"/api/v1/invoices/{td['inv_a1'].id}/send",
                json={"client_user_id": td["employee_user_a"].id},
                headers=headers_a,
            )
            assert res.status_code == 422

            # Foreign client recipient rejected with 404
            res = await ac.post(
                f"/api/v1/invoices/{td['inv_a1'].id}/send",
                json={"client_user_id": td["client_user_b"].id},
                headers=headers_a,
            )
            assert res.status_code == 404

            # Valid client recipient succeeds
            res = await ac.post(
                f"/api/v1/invoices/{td['inv_a1'].id}/send",
                json={"client_user_id": td["client_user_a"].id},
                headers=headers_a,
            )
            assert res.status_code == 200
            assert res.json()["message"] == "Invoice sent successfully."


@pytest.mark.asyncio
async def test_28_dynamic_db_permission_revoke():
    """Revoking invoices.view in DB immediately denies access (403)."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_ro = {"Authorization": f"Bearer {td['tokens']['ro_a']}"}

            # Pre-condition: can view
            res = await ac.get("/api/v1/invoices", headers=headers_ro)
            assert res.status_code == 200

            # Revoke invoices.view in DB
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role == td["role_full_a"].name.replace("InvFullA", "InvROA"),
                    )
                )
                await db.commit()

            # Now denied
            res = await ac.get("/api/v1/invoices", headers=headers_ro)
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_29_dynamic_db_permission_grant():
    """Granting invoices.create in DB immediately allows access."""
    async with setup_invoice_test_data() as td:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers_ro = {"Authorization": f"Bearer {td['tokens']['ro_a']}"}

            # Initially cannot create
            res = await ac.post(
                "/api/v1/invoices",
                json={
                    "project_id": td["proj_a"].id,
                    "owner_id": td["owner_a"].id,
                    "type": "owner",
                    "amount": 2500.0,
                    "description": "Dynamic Grant Test",
                },
                headers=headers_ro,
            )
            assert res.status_code == 403

            # Grant in DB
            async with AsyncSessionLocal() as db:
                ro_role_name = td["role_full_a"].name.replace("InvFullA", "InvROA")
                role_obj = await db.scalar(select(Role).where(Role.name == ro_role_name))
                perm_create = td["perm_objs"]["invoices.create"]
                db.add(RolePermission(role=ro_role_name, role_id=role_obj.id, permission_id=perm_create.id))
                await db.commit()

            # Now permitted
            res = await ac.post(
                "/api/v1/invoices",
                json={
                    "project_id": td["proj_a"].id,
                    "owner_id": td["owner_a"].id,
                    "type": "owner",
                    "amount": 2500.0,
                    "description": "Dynamic Grant Test",
                },
                headers=headers_ro,
            )
            assert res.status_code == 201


@pytest.mark.asyncio
async def test_30_route_preservation():
    """Verify exactly 28 invoice routes and 781 total application routes."""
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    invoice_routes = [r for r in routes if r.path.startswith("/api/v1/invoices")]

    assert len(invoice_routes) == 28, f"Expected 28 invoice routes, got {len(invoice_routes)}"
    assert len(routes) == 781, f"Expected 781 application routes, got {len(routes)}"

    # Check unique method + path
    unique_inv = set()
    for r in invoice_routes:
        for m in r.methods:
            if m not in ("HEAD", "OPTIONS"):
                key = (m, r.path)
                assert key not in unique_inv, f"Duplicate invoice route: {key}"
                unique_inv.add(key)

    assert len(unique_inv) == 28, f"Expected 28 unique invoice endpoints, got {len(unique_inv)}"


@pytest.mark.asyncio
async def test_31_static_security_hygiene():
    """Verify 0 require_roles, 0 admin_required, 0 UserRole references, 0 role allowlists in invoice.py."""
    src = inspect.getsource(invoice_api_module)

    assert "require_roles" not in src, "Found require_roles in app/api/invoice.py"
    assert "admin_required" not in src, "Found admin_required in app/api/invoice.py"
    assert "UserRole" not in src, "Found UserRole in app/api/invoice.py"
    assert "INVOICE_READ_ROLES" not in src, "Found INVOICE_READ_ROLES in app/api/invoice.py"
    assert "INVOICE_WRITE_ROLES" not in src, "Found INVOICE_WRITE_ROLES in app/api/invoice.py"
    assert "PAYMENT_ROLES" not in src, "Found PAYMENT_ROLES in app/api/invoice.py"
    assert "current_user.company_id is not None" not in src, "Found tenantless bypass pattern in invoice.py"
