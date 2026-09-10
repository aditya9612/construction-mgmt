import uuid
from decimal import Decimal
from datetime import date
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, update

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.notification import Notification
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.material import Supplier, PurchaseOrder
from app.models.accountant import (
    VendorBill,
    VendorBillItem,
    Account,
    JournalEntry,
    JournalLine,
    TDSDeduction,
)
from app.models.invoice import Transaction
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import ProjectStatus, VendorBillStatus, AccountType


@asynccontextmanager
async def setup_batch_s_data():
    """Seed test companies, projects, suppliers, accounts, vendor bills, users, roles, and permissions for Batch S."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchS_CompA_{uid}")
        comp_b = Company(name=f"BatchS_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-SA-{uid}",
            owner_name=f"Owner SA {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-SB-{uid}",
            owner_name=f"Owner SB {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerb_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 3. Projects
        proj_a = Project(
            business_id=f"PRJ-SA-{uid}",
            project_name=f"Project SA {uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b = Project(
            business_id=f"PRJ-SB-{uid}",
            project_name=f"Project SB {uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 4. Suppliers
        supp_a = Supplier(
            company_id=comp_a.id,
            supplier_name=f"Supplier SA {uid}",
            contact_person="Contact SA",
            phone_email="suppa@test.com",
            gst_number="27ABCDE1234F1Z5",
        )
        supp_b = Supplier(
            company_id=comp_b.id,
            supplier_name=f"Supplier SB {uid}",
            contact_person="Contact SB",
            phone_email="suppb@test.com",
            gst_number="27ABCDE5678F1Z5",
        )
        db.add_all([supp_a, supp_b])
        await db.flush()

        # 5. Purchase Orders
        po_a = PurchaseOrder(
            project_id=proj_a.id,
            supplier_id=supp_a.id,
            material_id=1,  # dummy reference
            material_name="Cement Grade 53",
            quantity=Decimal("100.000"),
            rate=Decimal("350.00"),
            total_amount=Decimal("35000.00"),
            status="APPROVED",
        )
        po_b = PurchaseOrder(
            project_id=proj_b.id,
            supplier_id=supp_b.id,
            material_id=1,
            material_name="Steel TMT",
            quantity=Decimal("50.000"),
            rate=Decimal("600.00"),
            total_amount=Decimal("30000.00"),
            status="APPROVED",
        )
        db.add_all([po_a, po_b])
        await db.flush()

        # 6. Chart of Accounts for Comp A and Comp B
        acc_payable_a = Account(
            company_id=comp_a.id,
            name="Vendor Payable A",
            code="VENDOR_PAYABLE",
            type=AccountType.LIABILITY,
        )
        acc_expense_a = Account(
            company_id=comp_a.id,
            name="Material Expense A",
            code="EXPENSE",
            type=AccountType.EXPENSE,
        )
        acc_gst_a = Account(
            company_id=comp_a.id,
            name="Input GST A",
            code="INPUT_GST",
            type=AccountType.ASSET,
        )
        acc_bank_a = Account(
            company_id=comp_a.id,
            name="Main Bank A",
            code="BANK",
            type=AccountType.ASSET,
        )

        acc_payable_b = Account(
            company_id=comp_b.id,
            name="Vendor Payable B",
            code="VENDOR_PAYABLE",
            type=AccountType.LIABILITY,
        )
        acc_expense_b = Account(
            company_id=comp_b.id,
            name="Material Expense B",
            code="EXPENSE",
            type=AccountType.EXPENSE,
        )
        acc_gst_b = Account(
            company_id=comp_b.id,
            name="Input GST B",
            code="INPUT_GST",
            type=AccountType.ASSET,
        )
        acc_bank_b = Account(
            company_id=comp_b.id,
            name="Main Bank B",
            code="BANK",
            type=AccountType.ASSET,
        )

        db.add_all([
            acc_payable_a, acc_expense_a, acc_gst_a, acc_bank_a,
            acc_payable_b, acc_expense_b, acc_gst_b, acc_bank_b,
        ])
        await db.flush()

        # 7. Vendor Bills
        bill_a = VendorBill(
            company_id=comp_a.id,
            supplier_id=supp_a.id,
            project_id=proj_a.id,
            purchase_order_id=po_a.id,
            bill_number=f"VB-A-{uid}",
            bill_date=date.today(),
            due_date=date.today(),
            gross_amount=Decimal("1000.00"),
            gst_percent=Decimal("18.00"),
            gst_amount=Decimal("180.00"),
            total_amount=Decimal("1180.00"),
            amount_paid=Decimal("0.00"),
            status=VendorBillStatus.PENDING.value,
        )
        bill_b = VendorBill(
            company_id=comp_b.id,
            supplier_id=supp_b.id,
            project_id=proj_b.id,
            purchase_order_id=po_b.id,
            bill_number=f"VB-B-{uid}",
            bill_date=date.today(),
            due_date=date.today(),
            gross_amount=Decimal("2000.00"),
            gst_percent=Decimal("18.00"),
            gst_amount=Decimal("360.00"),
            total_amount=Decimal("2360.00"),
            amount_paid=Decimal("0.00"),
            status=VendorBillStatus.PENDING.value,
        )
        # Bill A for direct payment (approved, no accrued journal)
        bill_a_payable = VendorBill(
            company_id=comp_a.id,
            supplier_id=supp_a.id,
            project_id=proj_a.id,
            bill_number=f"VB-APAY-{uid}",
            bill_date=date.today(),
            due_date=date.today(),
            gross_amount=Decimal("500.00"),
            total_amount=Decimal("500.00"),
            amount_paid=Decimal("0.00"),
            status=VendorBillStatus.APPROVED.value,
            accrued_journal_id=None,
        )
        db.add_all([bill_a, bill_b, bill_a_payable])
        await db.flush()

        # Items for bill_a
        item_a = VendorBillItem(
            vendor_bill_id=bill_a.id,
            material_name="Cement",
            category="Raw Materials",
            quantity=Decimal("10.00"),
            unit="Bags",
            rate=Decimal("100.00"),
            total=Decimal("1000.00"),
        )
        item_b = VendorBillItem(
            vendor_bill_id=bill_b.id,
            material_name="Steel",
            category="Metals",
            quantity=Decimal("5.00"),
            unit="Tons",
            rate=Decimal("400.00"),
            total=Decimal("2000.00"),
        )
        db.add_all([item_a, item_b])
        await db.flush()

        # Bill for payment reversal testing
        bill_a_rev = VendorBill(
            company_id=comp_a.id,
            supplier_id=supp_a.id,
            project_id=proj_a.id,
            bill_number=f"VB-AREV-{uid}",
            bill_date=date.today(),
            due_date=date.today(),
            gross_amount=Decimal("800.00"),
            total_amount=Decimal("800.00"),
            amount_paid=Decimal("400.00"),
            status=VendorBillStatus.PARTIAL.value,
        )
        db.add(bill_a_rev)
        await db.flush()

        rev_pay_je = JournalEntry(
            description=f"Payment for Vendor Bill {bill_a_rev.bill_number}",
            entry_date=date.today(),
            entry_type="Payment",
            status="Posted",
        )
        db.add(rev_pay_je)
        await db.flush()

        rev_jl1 = JournalLine(entry_id=rev_pay_je.id, account_id=acc_payable_a.id, debit=Decimal("400.00"), credit=Decimal("0.00"))
        rev_jl2 = JournalLine(entry_id=rev_pay_je.id, account_id=acc_bank_a.id, debit=Decimal("0.00"), credit=Decimal("400.00"))
        db.add_all([rev_jl1, rev_jl2])
        await db.flush()

        rev_txn = Transaction(
            project_id=proj_a.id,
            type="payment",
            amount=Decimal("400.00"),
            mode="bank",
            reference=f"TXN-REV-INIT-{uid}",
            linked_to=f"vendor_bill:{bill_a_rev.id}",
            journal_entry_id=rev_pay_je.id,
            created_by=1,
        )
        db.add(rev_txn)
        await db.flush()

        # 8. Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_s_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin S",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_sa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin S",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_sb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin S",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"VendorBillOfficer_{uid}"
        user_custom_a = User(
            email=f"custom_sa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Vendor Bill Officer S",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        legacy_empty_role_name = f"EmptyRoleS_{uid}"
        legacy_admin_no_perm = User(
            email=f"legacy_admin_s_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy Admin No Perm S",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=legacy_empty_role_name,
        )

        dummy_none_company_user = User(
            email=f"nonecomp_s_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="No Company User S",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Contractor",
        )

        db.add_all([
            super_admin,
            admin_a,
            admin_b,
            user_custom_a,
            legacy_admin_no_perm,
            dummy_none_company_user,
        ])
        await db.flush()

        # Project Member for comp_a
        pm_a = ProjectMember(project_id=proj_a.id, user_id=admin_a.id)
        db.add(pm_a)
        await db.flush()

        # 9. Custom Role
        role_custom_a = Role(
            company_id=comp_a.id,
            name=custom_role_name,
            display_name="Vendor Bill Officer Role",
            is_system=False,
        )
        role_legacy_empty = Role(
            company_id=comp_a.id,
            name=legacy_empty_role_name,
            display_name="Empty Role S",
            is_system=False,
        )
        db.add_all([role_custom_a, role_legacy_empty])
        await db.flush()

        # 10. Query DB permissions
        perm_view = (await db.execute(select(Permission).where(Permission.code == "vendor_bills.view"))).scalar_one()
        perm_create = (await db.execute(select(Permission).where(Permission.code == "vendor_bills.create"))).scalar_one()
        perm_edit = (await db.execute(select(Permission).where(Permission.code == "vendor_bills.edit"))).scalar_one()
        perm_delete = (await db.execute(select(Permission).where(Permission.code == "vendor_bills.delete"))).scalar_one()
        perm_approve = (await db.execute(select(Permission).where(Permission.code == "vendor_bills.approve"))).scalar_one()
        perm_pay = (await db.execute(select(Permission).where(Permission.code == "vendor_bills.pay"))).scalar_one()

        # 11. Tokens
        tokens = {
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "user_custom_a": create_access_token({"sub": str(user_custom_a.id)}),
            "legacy_admin_no_perm": create_access_token({"sub": str(legacy_admin_no_perm.id)}),
            "none_comp": create_access_token({"sub": str(dummy_none_company_user.id)}),
        }

        await db.commit()

        yield {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "supp_a": supp_a,
            "supp_b": supp_b,
            "po_a": po_a,
            "po_b": po_b,
            "accounts_a": {
                "payable": acc_payable_a,
                "expense": acc_expense_a,
                "gst": acc_gst_a,
                "bank": acc_bank_a,
            },
            "accounts_b": {
                "payable": acc_payable_b,
                "expense": acc_expense_b,
                "gst": acc_gst_b,
                "bank": acc_bank_b,
            },
            "bills": {
                "bill_a": bill_a,
                "bill_b": bill_b,
                "bill_a_payable": bill_a_payable,
                "bill_a_rev": bill_a_rev,
                "rev_txn": rev_txn,
            },
            "users": {
                "super_admin": super_admin,
                "admin_a": admin_a,
                "admin_b": admin_b,
                "user_custom_a": user_custom_a,
                "legacy_admin_no_perm": legacy_admin_no_perm,
                "none_comp": dummy_none_company_user,
            },
            "tokens": tokens,
            "roles": {
                "custom_a": role_custom_a,
                "legacy_empty": role_legacy_empty,
            },
            "permissions": {
                "view": perm_view,
                "create": perm_create,
                "edit": perm_edit,
                "delete": perm_delete,
                "approve": perm_approve,
                "pay": perm_pay,
            },
        }

        # Cleanup
        async with AsyncSessionLocal() as cleanup_db:
            await cleanup_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([
                super_admin.id, admin_a.id, admin_b.id, user_custom_a.id,
                legacy_admin_no_perm.id, dummy_none_company_user.id
            ])))
            await cleanup_db.execute(delete(RolePermission).where(RolePermission.role.in_([
                custom_role_name, legacy_empty_role_name
            ])))
            # Delete any notifications created during tests
            await cleanup_db.execute(delete(Notification).where(Notification.user_id.in_([
                super_admin.id, admin_a.id, admin_b.id, user_custom_a.id,
                legacy_admin_no_perm.id, dummy_none_company_user.id
            ])))
            await cleanup_db.execute(delete(Transaction).where(Transaction.linked_to.like("vendor_bill:%")))
            # Delete bill items first
            await cleanup_db.execute(delete(VendorBillItem).where(VendorBillItem.vendor_bill_id.in_(
                select(VendorBill.id).where(VendorBill.company_id.in_([comp_a.id, comp_b.id]))
            )))
            # Break FK constraint from vendor_bills.accrued_journal_id -> journal_entries.id
            await cleanup_db.execute(update(VendorBill).where(VendorBill.company_id.in_([comp_a.id, comp_b.id])).values(accrued_journal_id=None))
            # Delete vendor bills
            await cleanup_db.execute(delete(VendorBill).where(VendorBill.company_id.in_([comp_a.id, comp_b.id])))
            # Now safe to delete journal lines and entries
            await cleanup_db.execute(delete(JournalLine).where(JournalLine.entry_id.in_(
                select(JournalEntry.id).where(JournalEntry.description.like(f"%{uid}%"))
            )))
            await cleanup_db.execute(delete(JournalEntry).where(JournalEntry.description.like(f"%{uid}%")))
            await cleanup_db.execute(delete(PurchaseOrder).where(PurchaseOrder.id.in_([po_a.id, po_b.id])))
            await cleanup_db.execute(delete(Account).where(Account.company_id.in_([comp_a.id, comp_b.id])))
            await cleanup_db.execute(delete(Supplier).where(Supplier.id.in_([supp_a.id, supp_b.id])))
            await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
            await cleanup_db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await cleanup_db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await cleanup_db.execute(delete(Role).where(Role.id.in_([role_custom_a.id, role_legacy_empty.id])))
            await cleanup_db.execute(delete(User).where(User.id.in_([
                super_admin.id, admin_a.id, admin_b.id, user_custom_a.id,
                legacy_admin_no_perm.id, dummy_none_company_user.id
            ])))
            await cleanup_db.execute(delete(Permission).where(Permission.code == "vendor_bills.*"))
            await cleanup_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await cleanup_db.commit()


# ==============================================================================
# TEST 1 — 401 UNAUTHENTICATED
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_401_unauthenticated():
    """Verify that requests without token or with invalid token receive 401 Unauthorized across all 6 routes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("POST", "/api/v1/vendor-bills", {"supplier_id": 1, "bill_number": "VB-TEST", "bill_date": str(date.today()), "due_date": str(date.today()), "total_amount": 100}),
            ("GET", "/api/v1/vendor-bills", None),
            ("GET", "/api/v1/vendor-bills/1", None),
            ("POST", "/api/v1/vendor-bills/1/approve", {"status": "APPROVED"}),
            ("POST", "/api/v1/vendor-bills/1/pay", {"amount": 100, "mode": "BankTransfer"}),
            ("POST", "/api/v1/vendor-bills/1/reverse-payment/1", None),
        ]

        for method, url, body in endpoints:
            if method == "POST":
                res = await ac.post(url, json=body if body else {})
            else:
                res = await ac.get(url)

            assert res.status_code == 401, f"{method} {url} expected 401 without auth, got {res.status_code}: {res.text}"

            # Also with invalid bearer token
            headers = {"Authorization": "Bearer invalid.jwt.token"}
            if method == "POST":
                res_bad = await ac.post(url, json=body if body else {}, headers=headers)
            else:
                res_bad = await ac.get(url, headers=headers)

            assert res_bad.status_code == 401, f"{method} {url} expected 401 with bad token, got {res_bad.status_code}"


# ==============================================================================
# TEST 2 — 403 MISSING PERMISSION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_403_missing_permission():
    """Verify that authenticated user without specific permissions is rejected with 403 Forbidden."""
    async with setup_batch_s_data() as data:
        token = data["tokens"]["user_custom_a"]  # has custom role with NO assigned permissions
        headers = {"Authorization": f"Bearer {token}"}
        bill_a_id = data["bills"]["bill_a"].id
        bill_pay_id = data["bills"]["bill_a_payable"].id
        bill_rev_id = data["bills"]["bill_a_rev"].id
        txn_id = data["bills"]["rev_txn"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("POST", "/api/v1/vendor-bills", {"supplier_id": data["supp_a"].id, "bill_number": "VB-NOPERM", "bill_date": str(date.today()), "due_date": str(date.today()), "total_amount": 100}),
                ("GET", "/api/v1/vendor-bills", None),
                ("GET", f"/api/v1/vendor-bills/{bill_a_id}", None),
                ("POST", f"/api/v1/vendor-bills/{bill_a_id}/approve", {"status": "APPROVED"}),
                ("POST", f"/api/v1/vendor-bills/{bill_pay_id}/pay", {"amount": 100, "mode": "BankTransfer"}),
                ("POST", f"/api/v1/vendor-bills/{bill_rev_id}/reverse-payment/{txn_id}", None),
            ]

            for method, url, body in endpoints:
                if method == "POST":
                    res = await ac.post(url, json=body if body else {}, headers=headers)
                else:
                    res = await ac.get(url, headers=headers)

                assert res.status_code == 403, f"{method} {url} expected 403, got {res.status_code}: {res.text}"
                assert "Insufficient permissions" in res.text


# ==============================================================================
# TEST 3 — DB PERMISSION GRANT AND REVOKE
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_db_permission_grant_and_revoke():
    """Verify dynamic grant and revoke via DB role_permissions."""
    async with setup_batch_s_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        custom_role = data["roles"]["custom_a"]
        perm_view = data["permissions"]["view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Before grant: 403
            res_before = await ac.get("/api/v1/vendor-bills", headers=headers)
            assert res_before.status_code == 403

            # 2. Dynamically grant vendor_bills.view
            async with AsyncSessionLocal() as db:
                rp = RolePermission(role=custom_role.name, role_id=custom_role.id, permission_id=perm_view.id)
                db.add(rp)
                await db.commit()

            # 3. After grant: 200
            res_after = await ac.get("/api/v1/vendor-bills", headers=headers)
            assert res_after.status_code == 200, f"Expected 200 after grant, got {res_after.status_code}"

            # 4. Dynamically revoke vendor_bills.view
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role_id == custom_role.id,
                    RolePermission.permission_id == perm_view.id,
                ))
                await db.commit()

            # 5. After revoke: 403
            res_revoked = await ac.get("/api/v1/vendor-bills", headers=headers)
            assert res_revoked.status_code == 403


# ==============================================================================
# TEST 4 — POSITIVE USER PERMISSION OVERRIDE
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_positive_permission_override():
    """Verify that a positive override (is_granted=True) enables access even when role lacks permission."""
    async with setup_batch_s_data() as data:
        user_custom = data["users"]["user_custom_a"]
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        perm_view = data["permissions"]["view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_before = await ac.get("/api/v1/vendor-bills", headers=headers)
            assert res_before.status_code == 403

            # Add positive override
            async with AsyncSessionLocal() as db:
                upo = UserPermissionOverride(user_id=user_custom.id, permission_id=perm_view.id, is_granted=True)
                db.add(upo)
                await db.commit()

            res_after = await ac.get("/api/v1/vendor-bills", headers=headers)
            assert res_after.status_code == 200, f"Expected 200 with positive override, got {res_after.status_code}"


# ==============================================================================
# TEST 5 — NEGATIVE USER PERMISSION OVERRIDE
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_negative_permission_override():
    """Verify that a negative override (is_granted=False) blocks access even when role has permission."""
    async with setup_batch_s_data() as data:
        user_custom = data["users"]["user_custom_a"]
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        custom_role = data["roles"]["custom_a"]
        perm_view = data["permissions"]["view"]

        # Grant via role
        async with AsyncSessionLocal() as db:
            rp = RolePermission(role=custom_role.name, role_id=custom_role.id, permission_id=perm_view.id)
            db.add(rp)
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_role = await ac.get("/api/v1/vendor-bills", headers=headers)
            assert res_role.status_code == 200

            # Add negative override
            async with AsyncSessionLocal() as db:
                upo = UserPermissionOverride(user_id=user_custom.id, permission_id=perm_view.id, is_granted=False)
                db.add(upo)
                await db.commit()

            res_blocked = await ac.get("/api/v1/vendor-bills", headers=headers)
            assert res_blocked.status_code == 403


# ==============================================================================
# TEST 6 — WILDCARD PERMISSION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_wildcard_permission():
    """Verify that wildcard permission vendor_bills.* grants access to routes."""
    async with setup_batch_s_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        custom_role = data["roles"]["custom_a"]
        bill_a_id = data["bills"]["bill_a"].id

        # Insert wildcard permission if not present
        async with AsyncSessionLocal() as db:
            wildcard_perm = await db.scalar(select(Permission).where(Permission.code == "vendor_bills.*"))
            if not wildcard_perm:
                wildcard_perm = Permission(module="vendor_bills", action="*", code="vendor_bills.*", description="Wildcard for vendor bills")
                db.add(wildcard_perm)
                await db.flush()

            rp = RolePermission(role=custom_role.name, role_id=custom_role.id, permission_id=wildcard_perm.id)
            db.add(rp)
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_list = await ac.get("/api/v1/vendor-bills", headers=headers)
            assert res_list.status_code == 200

            res_get = await ac.get(f"/api/v1/vendor-bills/{bill_a_id}", headers=headers)
            assert res_get.status_code == 200


# ==============================================================================
# TEST 7 — LEGACY ROLE IMMUNITY
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_legacy_role_immunity():
    """Verify that role string name alone ('Admin', 'Accountant') without DB permissions is denied (403)."""
    async with setup_batch_s_data() as data:
        token = data["tokens"]["legacy_admin_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/vendor-bills", headers=headers)
            assert res.status_code == 403, f"Expected 403, got {res.status_code}"


# ==============================================================================
# TEST 8 — OWN TENANT ACCESS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_own_tenant_access():
    """Verify that tenant admin lists and views own company bills, excluding foreign bills."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        bill_a = data["bills"]["bill_a"]
        bill_b = data["bills"]["bill_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/vendor-bills", headers=headers_a)
            assert res.status_code == 200
            items = res.json()
            bill_numbers = [b["bill_number"] for b in items]
            assert bill_a.bill_number in bill_numbers
            assert bill_b.bill_number not in bill_numbers


# ==============================================================================
# TEST 9 — FOREIGN BILL MASKED 404
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_foreign_bill_masked_404():
    """Verify that accessing another tenant's bill returns masked 404 (not 403)."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        bill_b_id = data["bills"]["bill_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/vendor-bills/{bill_b_id}", headers=headers_a)
            assert res.status_code == 404
            assert res.json()["detail"] == "Vendor Bill not found"


# ==============================================================================
# TEST 10 — FOREIGN APPROVE / PAY / REVERSE MASKED 404
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_foreign_approve_pay_reverse_masked_404():
    """Verify that mutation operations on a foreign bill return masked 404."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        bill_b_id = data["bills"]["bill_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Foreign approve
            res_app = await ac.post(
                f"/api/v1/vendor-bills/{bill_b_id}/approve",
                json={"status": "APPROVED"},
                headers=headers_a,
            )
            assert res_app.status_code == 404

            # Foreign pay
            res_pay = await ac.post(
                f"/api/v1/vendor-bills/{bill_b_id}/pay",
                json={"amount": 100, "mode": "BankTransfer"},
                headers=headers_a,
            )
            assert res_pay.status_code == 404

            # Foreign reverse
            res_rev = await ac.post(
                f"/api/v1/vendor-bills/{bill_b_id}/reverse-payment/1",
                headers=headers_a,
            )
            assert res_rev.status_code == 404


# ==============================================================================
# TEST 11 — FOREIGN OR UNLINKED TRANSACTION REVERSE 404
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_foreign_or_unlinked_transaction_reverse_404():
    """Verify that reverse-payment with a foreign/unlinked transaction returns masked 404."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        bill_a_id = data["bills"]["bill_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Completely nonexistent transaction
            res_nonexistent = await ac.post(
                f"/api/v1/vendor-bills/{bill_a_id}/reverse-payment/999999",
                headers=headers_a,
            )
            assert res_nonexistent.status_code == 404
            assert res_nonexistent.json()["detail"] == "Transaction not found"

            # 2. Transaction that exists but is linked to another bill
            txn_other = data["bills"]["rev_txn"]
            res_unlinked = await ac.post(
                f"/api/v1/vendor-bills/{bill_a_id}/reverse-payment/{txn_other.id}",
                headers=headers_a,
            )
            assert res_unlinked.status_code == 404
            assert res_unlinked.json()["detail"] == "Transaction not found"


# ==============================================================================
# TEST 12 — CROSS-TENANT SUPPLIER INJECTION REJECTED
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_cross_tenant_supplier_injection_rejected():
    """Verify that attempting to create a bill with a foreign supplier is rejected (masked 404)."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        supp_b_id = data["supp_b"].id
        proj_a_id = data["proj_a"].id
        uid = data["uid"]

        payload = {
            "supplier_id": supp_b_id,
            "project_id": proj_a_id,
            "bill_number": f"VB-INJ-SUPP-{uid}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "total_amount": 500.0,
            "items": [],
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/v1/vendor-bills", json=payload, headers=headers_a)
            assert res.status_code == 404
            assert "Supplier not found" in res.json()["detail"]


# ==============================================================================
# TEST 13 — CROSS-TENANT PROJECT INJECTION REJECTED
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_cross_tenant_project_injection_rejected():
    """Verify that attempting to create a bill with a foreign project is rejected (masked 404)."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        supp_a_id = data["supp_a"].id
        proj_b_id = data["proj_b"].id
        uid = data["uid"]

        payload = {
            "supplier_id": supp_a_id,
            "project_id": proj_b_id,
            "bill_number": f"VB-INJ-PROJ-{uid}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "total_amount": 500.0,
            "items": [],
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/v1/vendor-bills", json=payload, headers=headers_a)
            assert res.status_code == 404
            assert "Project not found" in res.json()["detail"]


# ==============================================================================
# TEST 14 — FOREIGN / MISMATCHED PO REJECTED
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_foreign_mismatched_po_rejected():
    """Verify that linking a foreign or mismatched PO is rejected (masked 404)."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        supp_a_id = data["supp_a"].id
        proj_a_id = data["proj_a"].id
        po_b_id = data["po_b"].id
        uid = data["uid"]

        payload = {
            "supplier_id": supp_a_id,
            "project_id": proj_a_id,
            "purchase_order_id": po_b_id,
            "bill_number": f"VB-INJ-PO-{uid}",
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "total_amount": 500.0,
            "items": [],
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/v1/vendor-bills", json=payload, headers=headers_a)
            assert res.status_code == 404


# ==============================================================================
# TEST 15 — BILL NUMBER UNIQUENESS SCOPED PER COMPANY
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_bill_number_uniqueness_scoped_per_company():
    """Verify that duplicate bill numbers in the same company are rejected (400)."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        supp_a_id = data["supp_a"].id
        proj_a_id = data["proj_a"].id
        uid = data["uid"]
        bill_num = f"VB-DUP-{uid}"

        payload = {
            "supplier_id": supp_a_id,
            "project_id": proj_a_id,
            "bill_number": bill_num,
            "bill_date": str(date.today()),
            "due_date": str(date.today()),
            "total_amount": 500.0,
            "items": [],
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # First creation succeeds
            res1 = await ac.post("/api/v1/vendor-bills", json=payload, headers=headers_a)
            assert res1.status_code == 201

            # Duplicate creation in same company fails
            res2 = await ac.post("/api/v1/vendor-bills", json=payload, headers=headers_a)
            assert res2.status_code == 400
            assert "Bill number already exists" in res2.json()["detail"]


# ==============================================================================
# TEST 16 — SUPER ADMIN CROSS-COMPANY ACCESS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_super_admin_cross_company_access():
    """Verify that Super Admin can view bills across both companies."""
    async with setup_batch_s_data() as data:
        token_sa = data["tokens"]["super_admin"]
        headers_sa = {"Authorization": f"Bearer {token_sa}"}
        bill_a = data["bills"]["bill_a"]
        bill_b = data["bills"]["bill_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # SA list sees both bills
            res = await ac.get("/api/v1/vendor-bills", headers=headers_sa)
            assert res.status_code == 200
            bill_numbers = [b["bill_number"] for b in res.json()]
            assert bill_a.bill_number in bill_numbers
            assert bill_b.bill_number in bill_numbers

            # SA detail access
            res_a = await ac.get(f"/api/v1/vendor-bills/{bill_a.id}", headers=headers_sa)
            assert res_a.status_code == 200

            res_b = await ac.get(f"/api/v1/vendor-bills/{bill_b.id}", headers=headers_sa)
            assert res_b.status_code == 200


# ==============================================================================
# TEST 17 — TENANTLESS NON-SA 403
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_tenantless_non_sa_403():
    """Verify that non-SA users with company_id=None are blocked with 403 on all routes."""
    async with setup_batch_s_data() as data:
        token_none = data["tokens"]["none_comp"]
        headers_none = {"Authorization": f"Bearer {token_none}"}
        bill_a_id = data["bills"]["bill_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_list = await ac.get("/api/v1/vendor-bills", headers=headers_none)
            assert res_list.status_code == 403

            res_get = await ac.get(f"/api/v1/vendor-bills/{bill_a_id}", headers=headers_none)
            assert res_get.status_code == 403

            res_create = await ac.post(
                "/api/v1/vendor-bills",
                json={"supplier_id": 1, "bill_number": "NONE-COMP", "bill_date": str(date.today()), "due_date": str(date.today()), "total_amount": 100},
                headers=headers_none,
            )
            assert res_create.status_code == 403

            res_app = await ac.post(f"/api/v1/vendor-bills/{bill_a_id}/approve", json={"status": "APPROVED"}, headers=headers_none)
            assert res_app.status_code == 403

            res_pay = await ac.post(f"/api/v1/vendor-bills/{bill_a_id}/pay", json={"amount": 50, "mode": "BankTransfer"}, headers=headers_none)
            assert res_pay.status_code == 403

            res_rev = await ac.post(f"/api/v1/vendor-bills/{bill_a_id}/reverse-payment/1", headers=headers_none)
            assert res_rev.status_code == 403


# ==============================================================================
# TEST 18 — APPROVAL ACCOUNTING INVARIANTS & ISOLATION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_approval_accounting_invariants():
    """Verify that bill approval generates posted accrual JournalEntry with strictly tenant-scoped Accounts."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        bill_a = data["bills"]["bill_a"]
        acc_comp_a = data["accounts_a"]
        acc_comp_b = data["accounts_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                f"/api/v1/vendor-bills/{bill_a.id}/approve",
                json={"status": "APPROVED"},
                headers=headers_a,
            )
            assert res.status_code == 200
            assert res.json()["status"] == "APPROVED"

            # Verify in DB
            async with AsyncSessionLocal() as db:
                refreshed_bill = await db.get(VendorBill, bill_a.id)
                assert refreshed_bill.accrued_journal_id is not None

                je = await db.get(JournalEntry, refreshed_bill.accrued_journal_id)
                assert je is not None
                assert je.status == "Posted"
                assert je.entry_date == date.today()

                jl_res = await db.execute(select(JournalLine).where(JournalLine.entry_id == je.id))
                lines = jl_res.scalars().all()
                assert len(lines) >= 2

                # Verify all lines reference Company A's accounts, NEVER Company B's accounts!
                comp_a_acc_ids = {a.id for a in acc_comp_a.values()}
                comp_b_acc_ids = {a.id for a in acc_comp_b.values()}
                for line in lines:
                    assert line.account_id in comp_a_acc_ids, f"JournalLine referenced unexpected account {line.account_id}"
                    assert line.account_id not in comp_b_acc_ids, "Cross-tenant Chart of Accounts contamination detected!"

                # Total debits must equal total credits
                total_debits = sum(line.debit for line in lines)
                total_credits = sum(line.credit for line in lines)
                assert total_debits == total_credits, "Accrual entry debits and credits do not balance!"


# ==============================================================================
# TEST 19 — PAYMENT AND REVERSAL ACCOUNTING INVARIANTS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_s_payment_and_reversal_accounting_invariants():
    """Verify that pay and reverse-payment record correct transactions and maintain ledger balance."""
    async with setup_batch_s_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        bill_pay = data["bills"]["bill_a_payable"]
        acc_comp_a = data["accounts_a"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Pay 200 out of 500
            pay_payload = {
                "amount": 200.0,
                "mode": "BankTransfer",
                "reference": "TXN-TEST-P1",
            }
            res_pay = await ac.post(
                f"/api/v1/vendor-bills/{bill_pay.id}/pay",
                json=pay_payload,
                headers=headers_a,
            )
            assert res_pay.status_code == 200
            pay_data = res_pay.json()
            assert pay_data["status"] == VendorBillStatus.PARTIAL.value
            assert pay_data["paid"] == 200.0
            assert pay_data["pending"] == 300.0

            # Verify Transaction created
            async with AsyncSessionLocal() as db:
                txn = await db.scalar(select(Transaction).where(
                    Transaction.reference == "TXN-TEST-P1",
                    Transaction.linked_to == f"vendor_bill:{bill_pay.id}",
                ))
                assert txn is not None
                assert txn.amount == Decimal("200.00")
                assert txn.journal_entry_id is not None
                txn_id = txn.id

                # Verify journal lines
                jl_res = await db.execute(select(JournalLine).where(JournalLine.entry_id == txn.journal_entry_id))
                lines = jl_res.scalars().all()
                for line in lines:
                    assert line.account_id in {acc_comp_a["payable"].id, acc_comp_a["bank"].id}

            # 2. Reverse this payment
            res_rev = await ac.post(
                f"/api/v1/vendor-bills/{bill_pay.id}/reverse-payment/{txn_id}",
                headers=headers_a,
            )
            assert res_rev.status_code == 200
            rev_data = res_rev.json()
            assert rev_data["status"] == VendorBillStatus.PENDING.value
            assert rev_data["paid"] == 0.0
            assert rev_data["pending"] == 500.0

            # Duplicate reversal attempt must be rejected (400)
            res_rev_dup = await ac.post(
                f"/api/v1/vendor-bills/{bill_pay.id}/reverse-payment/{txn_id}",
                headers=headers_a,
            )
            assert res_rev_dup.status_code == 400
            assert "already been reversed" in res_rev_dup.json()["detail"]
