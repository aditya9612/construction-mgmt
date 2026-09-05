import uuid
from decimal import Decimal
from datetime import date, datetime
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
from app.models.contractor import Contractor
from app.models.accountant import (
    VendorBill,
    VendorBillItem,
    Account,
    BankAccount,
    JournalEntry,
    JournalLine,
    TDSDeduction,
    PaymentVoucher,
)
from app.models.settings import CompanySettings
from app.models.invoice import Transaction
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import ProjectStatus, VendorBillStatus, AccountType


@asynccontextmanager
async def setup_batch_t_data():
    """Seed companies, projects, suppliers, contractors, accounts, bank accounts, bills, payment vouchers, users, and roles."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchT_CompA_{uid}")
        comp_b = Company(name=f"BatchT_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-TA-{uid}",
            owner_name=f"Owner TA {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerta_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-TB-{uid}",
            owner_name=f"Owner TB {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownertb_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 3. Projects
        proj_a = Project(
            business_id=f"PRJ-TA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Project TA {uid}",
            status=ProjectStatus.ONGOING,
        )
        proj_b = Project(
            business_id=f"PRJ-TB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Project TB {uid}",
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 4. Suppliers & Contractors
        supp_a = Supplier(
            company_id=comp_a.id,
            supplier_name=f"Supplier TA {uid}",
            contact_person="Supplier Contact A",
            phone_email=f"987654{uid[:4]}",
        )
        supp_b = Supplier(
            company_id=comp_b.id,
            supplier_name=f"Supplier TB {uid}",
            contact_person="Supplier Contact B",
            phone_email=f"987655{uid[:4]}",
        )
        cont_a = Contractor(
            company_id=comp_a.id,
            contractor_id=f"CNT-A-{uid}",
            name=f"Contractor TA {uid}",
            work_type="Civil",
            contact_number=f"98{uid[:8]}",
            rate_type="Daily",
        )
        cont_b = Contractor(
            company_id=comp_b.id,
            contractor_id=f"CNT-B-{uid}",
            name=f"Contractor TB {uid}",
            work_type="Electrical",
            contact_number=f"99{uid[:8]}",
            rate_type="Daily",
        )
        db.add_all([supp_a, supp_b, cont_a, cont_b])
        await db.flush()

        # 5. Accounts (strictly per tenant)
        acc_payable_a = Account(
            company_id=comp_a.id,
            name=f"Vendor Payable A {uid}",
            code="VENDOR_PAYABLE",
            type=AccountType.LIABILITY,
        )
        acc_cont_payable_a = Account(
            company_id=comp_a.id,
            name=f"Contractor Payable A {uid}",
            code="CONTRACTOR_PAYABLE",
            type=AccountType.LIABILITY,
        )
        acc_bank_a = Account(
            company_id=comp_a.id,
            name=f"Bank Ledger A {uid}",
            code="BANK",
            type=AccountType.ASSET,
        )
        acc_tds_a = Account(
            company_id=comp_a.id,
            name=f"TDS Payable A {uid}",
            code="TDS_PAYABLE",
            type=AccountType.LIABILITY,
        )
        acc_ret_a = Account(
            company_id=comp_a.id,
            name=f"Retention Payable A {uid}",
            code="RETENTION_PAYABLE",
            type=AccountType.LIABILITY,
        )

        acc_payable_b = Account(
            company_id=comp_b.id,
            name=f"Vendor Payable B {uid}",
            code="VENDOR_PAYABLE",
            type=AccountType.LIABILITY,
        )
        acc_bank_b = Account(
            company_id=comp_b.id,
            name=f"Bank Ledger B {uid}",
            code="BANK",
            type=AccountType.ASSET,
        )
        db.add_all([
            acc_payable_a, acc_cont_payable_a, acc_bank_a, acc_tds_a, acc_ret_a,
            acc_payable_b, acc_bank_b
        ])
        await db.flush()

        # 6. CompanySettings
        settings_a = CompanySettings(
            company_id=comp_a.id,
            tds_payable_account_id=acc_tds_a.id,
            retention_payable_account_id=acc_ret_a.id,
        )
        settings_b = CompanySettings(
            company_id=comp_b.id,
        )
        db.add_all([settings_a, settings_b])
        await db.flush()

        # 7. Bank Accounts
        bank_acc_a = BankAccount(
            account_id=acc_bank_a.id,
            bank_name=f"Bank A {uid}",
            account_number=f"ACC-A-{uid}",
        )
        bank_acc_b = BankAccount(
            account_id=acc_bank_b.id,
            bank_name=f"Bank B {uid}",
            account_number=f"ACC-B-{uid}",
        )
        db.add_all([bank_acc_a, bank_acc_b])
        await db.flush()

        # 8. Vendor Bills
        bill_a_approved = VendorBill(
            company_id=comp_a.id,
            supplier_id=supp_a.id,
            project_id=proj_a.id,
            bill_number=f"VB-TA-APP-{uid}",
            bill_date=date.today(),
            due_date=date.today(),
            gross_amount=Decimal("1000.00"),
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"),
            status=VendorBillStatus.APPROVED.value,
        )
        bill_a_partial = VendorBill(
            company_id=comp_a.id,
            supplier_id=supp_a.id,
            project_id=proj_a.id,
            bill_number=f"VB-TA-PART-{uid}",
            bill_date=date.today(),
            due_date=date.today(),
            gross_amount=Decimal("1000.00"),
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("200.00"),
            status=VendorBillStatus.PARTIAL.value,
        )
        bill_a_draft = VendorBill(
            company_id=comp_a.id,
            supplier_id=supp_a.id,
            project_id=proj_a.id,
            bill_number=f"VB-TA-DRAFT-{uid}",
            bill_date=date.today(),
            due_date=date.today(),
            gross_amount=Decimal("500.00"),
            total_amount=Decimal("500.00"),
            amount_paid=Decimal("0.00"),
            status=VendorBillStatus.PENDING.value,
        )
        bill_b_approved = VendorBill(
            company_id=comp_b.id,
            supplier_id=supp_b.id,
            project_id=proj_b.id,
            bill_number=f"VB-TB-APP-{uid}",
            bill_date=date.today(),
            due_date=date.today(),
            gross_amount=Decimal("2000.00"),
            total_amount=Decimal("2000.00"),
            amount_paid=Decimal("0.00"),
            status=VendorBillStatus.APPROVED.value,
        )
        db.add_all([bill_a_approved, bill_a_partial, bill_a_draft, bill_b_approved])
        await db.flush()

        # 9. Payment Vouchers
        pv_a_pending = PaymentVoucher(
            payment_voucher_number=f"VOUCHER-TA-PEND-{uid}",
            payment_date=datetime.now(),
            party_type="Vendor",
            supplier_id=supp_a.id,
            vendor_bill_id=bill_a_approved.id,
            base_amount=Decimal("400.00"),
            gst_amount=Decimal("0.00"),
            gross_amount=Decimal("400.00"),
            tds_amount=Decimal("0.00"),
            retention_amount=Decimal("0.00"),
            net_payable_amount=Decimal("400.00"),
            payment_method="BankTransfer",
            bank_account_id=bank_acc_a.id,
            reference_no=f"REF-P1-{uid}",
            status="PENDING",
        )
        pv_b_pending = PaymentVoucher(
            payment_voucher_number=f"VOUCHER-TB-PEND-{uid}",
            payment_date=datetime.now(),
            party_type="Vendor",
            supplier_id=supp_b.id,
            vendor_bill_id=bill_b_approved.id,
            base_amount=Decimal("500.00"),
            gst_amount=Decimal("0.00"),
            gross_amount=Decimal("500.00"),
            tds_amount=Decimal("0.00"),
            retention_amount=Decimal("0.00"),
            net_payable_amount=Decimal("500.00"),
            payment_method="BankTransfer",
            bank_account_id=bank_acc_b.id,
            status="PENDING",
        )
        db.add_all([pv_a_pending, pv_b_pending])
        await db.flush()

        # 10. Users & Roles
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"sa_t_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin T",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_ta_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin TA",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_tb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin TB",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        accountant_no_perm = User(
            email=f"accountant_t_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Accountant No Perm",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Accountant",
        )
        user_custom_a = User(
            email=f"user_custom_ta_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom User TA",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Contractor",
        )
        dummy_none_company_user = User(
            email=f"none_comp_t_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Tenantless T",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        db.add_all([
            super_admin, admin_a, admin_b, accountant_no_perm,
            user_custom_a, dummy_none_company_user
        ])
        await db.flush()

        custom_role_name = f"role_custom_t_{uid}"
        empty_role_name = f"role_empty_t_{uid}"
        role_custom_a = Role(
            company_id=comp_a.id,
            name=custom_role_name,
            display_name="Payment Voucher Officer",
            is_system=False,
        )
        role_empty = Role(
            company_id=comp_a.id,
            name=empty_role_name,
            display_name="Empty Role T",
            is_system=False,
        )
        db.add_all([role_custom_a, role_empty])
        await db.flush()

        # Connect accountant_no_perm to empty role
        accountant_no_perm.role = empty_role_name
        user_custom_a.role = custom_role_name
        await db.flush()

        # 11. Query DB Permissions
        perm_view = (await db.execute(select(Permission).where(Permission.code == "payment_vouchers.view"))).scalar_one()
        perm_create = (await db.execute(select(Permission).where(Permission.code == "payment_vouchers.create"))).scalar_one()
        perm_edit = (await db.execute(select(Permission).where(Permission.code == "payment_vouchers.edit"))).scalar_one()
        perm_delete = (await db.execute(select(Permission).where(Permission.code == "payment_vouchers.delete"))).scalar_one()
        perm_pay = (await db.execute(select(Permission).where(Permission.code == "payment_vouchers.pay"))).scalar_one()

        tokens = {
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "accountant_no_perm": create_access_token({"sub": str(accountant_no_perm.id)}),
            "user_custom_a": create_access_token({"sub": str(user_custom_a.id)}),
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
            "cont_a": cont_a,
            "cont_b": cont_b,
            "bank_a": bank_acc_a,
            "bank_b": bank_acc_b,
            "bills": {
                "bill_a_approved": bill_a_approved,
                "bill_a_partial": bill_a_partial,
                "bill_a_draft": bill_a_draft,
                "bill_b_approved": bill_b_approved,
            },
            "vouchers": {
                "pv_a_pending": pv_a_pending,
                "pv_b_pending": pv_b_pending,
            },
            "accounts_a": {
                "payable": acc_payable_a,
                "cont_payable": acc_cont_payable_a,
                "bank": acc_bank_a,
                "tds": acc_tds_a,
                "ret": acc_ret_a,
            },
            "users": {
                "super_admin": super_admin,
                "admin_a": admin_a,
                "admin_b": admin_b,
                "accountant_no_perm": accountant_no_perm,
                "user_custom_a": user_custom_a,
                "none_comp": dummy_none_company_user,
            },
            "tokens": tokens,
            "roles": {
                "custom_a": role_custom_a,
                "empty": role_empty,
            },
            "permissions": {
                "view": perm_view,
                "create": perm_create,
                "edit": perm_edit,
                "delete": perm_delete,
                "pay": perm_pay,
            },
        }

        # Cleanup
        async with AsyncSessionLocal() as cleanup_db:
            all_uids = [
                super_admin.id, admin_a.id, admin_b.id, accountant_no_perm.id,
                user_custom_a.id, dummy_none_company_user.id
            ]
            acc_ids = [
                acc_payable_a.id, acc_cont_payable_a.id, acc_bank_a.id, acc_tds_a.id, acc_ret_a.id,
                acc_payable_b.id, acc_bank_b.id
            ]
            all_bill_ids = [bill_a_approved.id, bill_a_partial.id, bill_a_draft.id, bill_b_approved.id]
            await cleanup_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(all_uids)))
            await cleanup_db.execute(delete(RolePermission).where(RolePermission.role.in_([custom_role_name, empty_role_name])))
            await cleanup_db.execute(delete(Notification).where(Notification.user_id.in_(all_uids)))
            await cleanup_db.execute(delete(Transaction).where(Transaction.linked_to.like("vendor_bill:%")))
            await cleanup_db.execute(delete(Transaction).where(Transaction.reference.like(f"%{uid}%")))
            await cleanup_db.execute(delete(TDSDeduction).where(TDSDeduction.vendor_bill_id.in_(all_bill_ids)))
            # Break FK cycle
            await cleanup_db.execute(update(PaymentVoucher).where(PaymentVoucher.vendor_bill_id.in_(all_bill_ids)).values(journal_entry_id=None))
            await cleanup_db.execute(delete(PaymentVoucher).where(PaymentVoucher.vendor_bill_id.in_(all_bill_ids)))
            je_ids = (await cleanup_db.scalars(
                select(JournalEntry.id).where(
                    (JournalEntry.description.like(f"%{uid}%")) |
                    (JournalEntry.description.like("%Payment Voucher%")) |
                    (JournalEntry.description.like("%Reversal of Payment%"))
                )
            )).all()
            if je_ids:
                await cleanup_db.execute(delete(JournalLine).where(JournalLine.entry_id.in_(je_ids)))
                await cleanup_db.execute(delete(JournalEntry).where(JournalEntry.id.in_(je_ids)))
            await cleanup_db.execute(delete(JournalLine).where(JournalLine.account_id.in_(acc_ids)))
            await cleanup_db.execute(delete(VendorBillItem).where(VendorBillItem.vendor_bill_id.in_(all_bill_ids)))
            await cleanup_db.execute(delete(VendorBill).where(VendorBill.company_id.in_([comp_a.id, comp_b.id])))
            await cleanup_db.execute(delete(BankAccount).where(BankAccount.account_id.in_(acc_ids)))
            await cleanup_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
            await cleanup_db.execute(delete(Account).where(Account.company_id.in_([comp_a.id, comp_b.id])))
            await cleanup_db.execute(delete(Supplier).where(Supplier.id.in_([supp_a.id, supp_b.id])))
            await cleanup_db.execute(delete(Contractor).where(Contractor.id.in_([cont_a.id, cont_b.id])))
            await cleanup_db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
            await cleanup_db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await cleanup_db.execute(delete(Role).where(Role.id.in_([role_custom_a.id, role_empty.id])))
            await cleanup_db.execute(update(User).where(User.id.in_(all_uids)).values(created_by=None))
            await cleanup_db.execute(delete(User).where(User.id.in_(all_uids)))
            await cleanup_db.execute(delete(Permission).where(Permission.code == "payment_vouchers.*"))
            await cleanup_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await cleanup_db.commit()


# ==============================================================================
# 1. 401 UNAUTHENTICATED
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_401_unauthenticated():
    """Verify that all 4 endpoints reject unauthenticated requests with HTTP 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("POST", "/api/v1/payments/vouchers", {"vendor_bill_id": 1, "party_type": "Vendor"}),
            ("GET", "/api/v1/payments/vouchers", None),
            ("POST", "/api/v1/payments/vouchers/1/mark-paid", None),
            ("POST", "/api/v1/payments/vouchers/1/cancel", None),
        ]
        for method, path, payload in endpoints:
            res = await ac.request(method, path, json=payload)
            assert res.status_code == 401, f"Expected 401 for {method} {path}, got {res.status_code}"


# ==============================================================================
# 2. 403 MISSING PERMISSION
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_403_missing_permission():
    """Verify that users without required permissions receive HTTP 403."""
    async with setup_batch_t_data() as data:
        token_no_perm = data["tokens"]["accountant_no_perm"]
        headers = {"Authorization": f"Bearer {token_no_perm}"}
        pv_id = data["vouchers"]["pv_a_pending"].id
        bill_id = data["bills"]["bill_a_approved"].id
        supp_id = data["supp_a"].id
        bank_id = data["bank_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("POST", "/api/v1/payments/vouchers", {
                    "payment_date": datetime.now().isoformat(),
                    "party_type": "Vendor",
                    "supplier_id": supp_id,
                    "vendor_bill_id": bill_id,
                    "gross_amount": 100.0,
                    "payment_method": "BankTransfer",
                    "bank_account_id": bank_id,
                }),
                ("GET", "/api/v1/payments/vouchers", None),
                ("POST", f"/api/v1/payments/vouchers/{pv_id}/mark-paid", None),
                ("POST", f"/api/v1/payments/vouchers/{pv_id}/cancel", None),
            ]
            for method, path, payload in endpoints:
                res = await ac.request(method, path, json=payload, headers=headers)
                assert res.status_code == 403, f"Expected 403 for {method} {path}, got {res.status_code}"


# ==============================================================================
# 3. DYNAMIC DB GRANT AND REVOKE
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_dynamic_db_grant_and_revoke():
    """Verify that granting a permission in DB immediately allows access, and revoking denies access."""
    async with setup_batch_t_data() as data:
        role_name = data["roles"]["custom_a"].name
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        perm_view = data["permissions"]["view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Initially 403
            res1 = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res1.status_code == 403

            # 2. Grant permission in DB
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=role_name, permission_id=perm_view.id))
                await db.commit()

            # 3. Now 200
            res2 = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res2.status_code == 200

            # 4. Revoke permission from DB
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role == role_name,
                    RolePermission.permission_id == perm_view.id,
                ))
                await db.commit()

            # 5. Immediately 403
            res3 = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res3.status_code == 403


# ==============================================================================
# 4. POSITIVE USER OVERRIDE
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_positive_user_override():
    """Verify positive user permission override grants access regardless of role."""
    async with setup_batch_t_data() as data:
        user = data["users"]["accountant_no_perm"]
        token = data["tokens"]["accountant_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}
        perm_view = data["permissions"]["view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_before = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res_before.status_code == 403

            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user.id, permission_id=perm_view.id, is_granted=True))
                await db.commit()

            res_after = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res_after.status_code == 200


# ==============================================================================
# 5. NEGATIVE USER OVERRIDE
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_negative_user_override():
    """Verify negative user permission override denies access even if role has permission."""
    async with setup_batch_t_data() as data:
        user_a = data["users"]["admin_a"]
        token_a = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token_a}"}
        perm_view = data["permissions"]["view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # admin_a has permission via Admin role
            res1 = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res1.status_code == 200

            # Apply negative override
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user_a.id, permission_id=perm_view.id, is_granted=False))
                await db.commit()

            # Now 403
            res2 = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res2.status_code == 403


# ==============================================================================
# 6. WILDCARD PERMISSION
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_wildcard_permission():
    """Verify payment_vouchers.* wildcard grants access across all 4 endpoints."""
    async with setup_batch_t_data() as data:
        user = data["users"]["user_custom_a"]
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        pv_id = data["vouchers"]["pv_a_pending"].id
        bill_id = data["bills"]["bill_a_approved"].id
        supp_id = data["supp_a"].id
        bank_id = data["bank_a"].id

        async with AsyncSessionLocal() as db:
            wildcard_perm = Permission(
                module="payment_vouchers",
                action="*",
                code="payment_vouchers.*",
                description="Wildcard for payment vouchers",
            )
            db.add(wildcard_perm)
            await db.flush()
            db.add(UserPermissionOverride(user_id=user.id, permission_id=wildcard_perm.id, is_granted=True))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. View (GET)
            res_get = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res_get.status_code == 200

            # 2. Create (POST)
            create_payload = {
                "payment_date": datetime.now().isoformat(),
                "party_type": "Vendor",
                "supplier_id": supp_id,
                "vendor_bill_id": bill_id,
                "gross_amount": 50.0,
                "payment_method": "BankTransfer",
                "bank_account_id": bank_id,
            }
            res_post = await ac.post("/api/v1/payments/vouchers", json=create_payload, headers=headers)
            assert res_post.status_code == 200


# ==============================================================================
# 7. LEGACY ROLE IMMUNITY (Admin and Accountant with no DB perm -> 403)
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_legacy_role_immunity():
    """Verify that legacy role names without DB permission cannot access any endpoint."""
    async with setup_batch_t_data() as data:
        token = data["tokens"]["accountant_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res.status_code == 403


# ==============================================================================
# 8. OWN-TENANT ACCESS (Create, List, Pay, Cancel)
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_own_tenant_lifecycle():
    """Verify full own-tenant lifecycle: create voucher, list it, pay it, and cancel it."""
    async with setup_batch_t_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token_a}"}
        bill = data["bills"]["bill_a_approved"]
        supp = data["supp_a"]
        bank = data["bank_a"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create Voucher
            create_payload = {
                "payment_date": datetime.now().isoformat(),
                "party_type": "Vendor",
                "supplier_id": supp.id,
                "vendor_bill_id": bill.id,
                "gross_amount": 250.0,
                "tds_amount": 0.0,
                "retention_amount": 0.0,
                "payment_method": "BankTransfer",
                "bank_account_id": bank.id,
            }
            res_create = await ac.post("/api/v1/payments/vouchers", json=create_payload, headers=headers)
            assert res_create.status_code == 200
            pv_data = res_create.json()
            pv_id = pv_data["id"]
            assert pv_data["status"] == "PENDING"
            assert Decimal(str(pv_data["gross_amount"])) == Decimal("250.00")

            # 2. List Vouchers
            res_list = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res_list.status_code == 200
            ids = [item["id"] for item in res_list.json()]
            assert pv_id in ids

            # 3. Pay Voucher
            res_pay = await ac.post(f"/api/v1/payments/vouchers/{pv_id}/mark-paid", headers=headers)
            assert res_pay.status_code == 200
            assert res_pay.json()["status"] == "PAID"

            # 4. Cancel Voucher
            res_cancel = await ac.post(f"/api/v1/payments/vouchers/{pv_id}/cancel", headers=headers)
            assert res_cancel.status_code == 200
            assert res_cancel.json()["status"] == "CANCELLED"


# ==============================================================================
# 9. FOREIGN RESOURCE INJECTIONS (Masked 404)
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_foreign_resource_injections_masked_404():
    """Verify that foreign bill, supplier, contractor, or bank account injection returns masked 404."""
    async with setup_batch_t_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token_a}"}
        bill_a = data["bills"]["bill_a_approved"]
        bill_b = data["bills"]["bill_b_approved"]
        supp_a = data["supp_a"]
        supp_b = data["supp_b"]
        cont_b = data["cont_b"]
        bank_a = data["bank_a"]
        bank_b = data["bank_b"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Foreign vendor_bill_id
            res1 = await ac.post("/api/v1/payments/vouchers", json={
                "payment_date": datetime.now().isoformat(),
                "party_type": "Vendor",
                "supplier_id": supp_a.id,
                "vendor_bill_id": bill_b.id,
                "gross_amount": 100.0,
                "payment_method": "BankTransfer",
                "bank_account_id": bank_a.id,
            }, headers=headers)
            assert res1.status_code == 404

            # 2. Foreign supplier_id
            res2 = await ac.post("/api/v1/payments/vouchers", json={
                "payment_date": datetime.now().isoformat(),
                "party_type": "Vendor",
                "supplier_id": supp_b.id,
                "vendor_bill_id": bill_a.id,
                "gross_amount": 100.0,
                "payment_method": "BankTransfer",
                "bank_account_id": bank_a.id,
            }, headers=headers)
            assert res2.status_code == 404

            # 3. Foreign contractor_id
            res3 = await ac.post("/api/v1/payments/vouchers", json={
                "payment_date": datetime.now().isoformat(),
                "party_type": "Contractor",
                "contractor_id": cont_b.id,
                "vendor_bill_id": bill_a.id,
                "gross_amount": 100.0,
                "payment_method": "BankTransfer",
                "bank_account_id": bank_a.id,
            }, headers=headers)
            assert res3.status_code == 404

            # 4. Foreign bank_account_id
            res4 = await ac.post("/api/v1/payments/vouchers", json={
                "payment_date": datetime.now().isoformat(),
                "party_type": "Vendor",
                "supplier_id": supp_a.id,
                "vendor_bill_id": bill_a.id,
                "gross_amount": 100.0,
                "payment_method": "BankTransfer",
                "bank_account_id": bank_b.id,
            }, headers=headers)
            assert res4.status_code == 404


# ==============================================================================
# 10. FOREIGN MUTATIONS RETURN MASKED 404
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_foreign_mutations_masked_404():
    """Verify that mark-paid and cancel on foreign vouchers return masked 404."""
    async with setup_batch_t_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token_a}"}
        pv_b_id = data["vouchers"]["pv_b_pending"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Foreign mark-paid
            res_pay = await ac.post(f"/api/v1/payments/vouchers/{pv_b_id}/mark-paid", headers=headers)
            assert res_pay.status_code == 404

            # Foreign cancel
            res_cancel = await ac.post(f"/api/v1/payments/vouchers/{pv_b_id}/cancel", headers=headers)
            assert res_cancel.status_code == 404


# ==============================================================================
# 11. TENANTLESS NON-SA DENIAL (403)
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_tenantless_non_sa_403():
    """Verify that non-SA users with company_id=None are blocked with 403 on all routes."""
    async with setup_batch_t_data() as data:
        token_none = data["tokens"]["none_comp"]
        headers = {"Authorization": f"Bearer {token_none}"}
        pv_id = data["vouchers"]["pv_a_pending"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("POST", "/api/v1/payments/vouchers", {"vendor_bill_id": 1, "party_type": "Vendor"}),
                ("GET", "/api/v1/payments/vouchers", None),
                ("POST", f"/api/v1/payments/vouchers/{pv_id}/mark-paid", None),
                ("POST", f"/api/v1/payments/vouchers/{pv_id}/cancel", None),
            ]
            for method, path, payload in endpoints:
                res = await ac.request(method, path, json=payload, headers=headers)
                assert res.status_code == 403


# ==============================================================================
# 12. SUPER ADMIN GLOBAL ACCESS
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_super_admin_global_access():
    """Verify that Super Admin can list cross-company vouchers and access tenant vouchers."""
    async with setup_batch_t_data() as data:
        token_sa = data["tokens"]["super_admin"]
        headers = {"Authorization": f"Bearer {token_sa}"}
        pv_a_id = data["vouchers"]["pv_a_pending"].id
        pv_b_id = data["vouchers"]["pv_b_pending"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_list = await ac.get("/api/v1/payments/vouchers", headers=headers)
            assert res_list.status_code == 200
            ids = [item["id"] for item in res_list.json()]
            assert pv_a_id in ids
            assert pv_b_id in ids


# ==============================================================================
# 13. BILL STATUS INVARIANT
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_bill_status_invariant():
    """Verify that payment voucher creation requires bill to be in APPROVED or PARTIAL status."""
    async with setup_batch_t_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token_a}"}
        bill_draft = data["bills"]["bill_a_draft"]
        supp = data["supp_a"]
        bank = data["bank_a"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            payload = {
                "payment_date": datetime.now().isoformat(),
                "party_type": "Vendor",
                "supplier_id": supp.id,
                "vendor_bill_id": bill_draft.id,
                "gross_amount": 100.0,
                "payment_method": "BankTransfer",
                "bank_account_id": bank.id,
            }
            res = await ac.post("/api/v1/payments/vouchers", json=payload, headers=headers)
            assert res.status_code == 400
            assert "must be APPROVED or PARTIAL" in res.json()["detail"]


# ==============================================================================
# 14. CHART OF ACCOUNTS & GENERAL LEDGER INVARIANTS
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_accounting_tenant_invariants():
    """Verify that pay posts JournalLines strictly with accounts belonging to the voucher's company."""
    async with setup_batch_t_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token_a}"}
        pv_id = data["vouchers"]["pv_a_pending"].id
        acc_a = data["accounts_a"]
        comp_a_id = data["comp_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_pay = await ac.post(f"/api/v1/payments/vouchers/{pv_id}/mark-paid", headers=headers)
            assert res_pay.status_code == 200

            # Verify JournalEntry & Lines
            async with AsyncSessionLocal() as db:
                pv = await db.get(PaymentVoucher, pv_id)
                assert pv.journal_entry_id is not None

                jl_res = await db.execute(select(JournalLine).where(JournalLine.entry_id == pv.journal_entry_id))
                lines = jl_res.scalars().all()
                assert len(lines) >= 2  # Debit payable, credit bank, plus TDS and Retention

                for line in lines:
                    acc = await db.get(Account, line.account_id)
                    assert acc is not None
                    assert acc.company_id == comp_a_id, f"JournalLine account {acc.id} leaked cross-company!"


# ==============================================================================
# 15. DETERMINISTIC TRANSACTION REVERSAL
# ==============================================================================
@pytest.mark.asyncio
async def test_batch_t_deterministic_transaction_reversal():
    """Verify cancellation creates an exact reversal transaction linked to the voucher's payment."""
    async with setup_batch_t_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token_a}"}
        pv_id = data["vouchers"]["pv_a_pending"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Mark paid
            res_pay = await ac.post(f"/api/v1/payments/vouchers/{pv_id}/mark-paid", headers=headers)
            assert res_pay.status_code == 200

            # 2. Cancel
            res_cancel = await ac.post(f"/api/v1/payments/vouchers/{pv_id}/cancel", headers=headers)
            assert res_cancel.status_code == 200

            # 3. Check reversal transaction
            async with AsyncSessionLocal() as db:
                pv = await db.get(PaymentVoucher, pv_id)
                expected_refs = [f"REV-PV-{pv.payment_voucher_number}"]
                if pv.reference_no:
                    expected_refs.append(f"REV-{pv.reference_no}")
                tx_rev = await db.scalar(select(Transaction).where(
                    Transaction.reference.in_(expected_refs)
                ))
                assert tx_rev is not None
                assert Decimal(str(tx_rev.amount)) == -Decimal(str(pv.net_payable_amount))
