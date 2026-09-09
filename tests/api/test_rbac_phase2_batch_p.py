import uuid
from decimal import Decimal
from datetime import date, datetime
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, func

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, ActivityLog, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.settings import CompanySettings
from app.models.invoice import Transaction
from app.models.accountant import JournalEntry, JournalLine, Account, BankAccount
from app.models.billing import RABill
from app.models.labour import Labour, LabourAttendance, LabourPayroll, PayrollStatus, AttendanceStatus
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import AccountType, ProjectStatus


@asynccontextmanager
async def setup_batch_p_data():
    """Seed test companies, users, accounts, projects, labours, payrolls, bills, transactions, and RBAC data for Batch P."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchP_CompA_{uid}")
        comp_b = Company(name=f"BatchP_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # Owners
        owner_a1 = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-PA1-{uid}",
            owner_name=f"Owner PA1 {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"ownera1_{uid}@test.com",
        )
        owner_b1 = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-PB1-{uid}",
            owner_name=f"Owner PB1 {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"ownerb1_{uid}@test.com",
        )
        db.add_all([owner_a1, owner_b1])
        await db.flush()

        # 2. Accounts for Comp A and Comp B
        acc_salary_a = Account(
            company_id=comp_a.id,
            name=f"Salary Expense A {uid}",
            code=f"SAL-A-{uid}",
            type=AccountType.EXPENSE,
        )
        acc_cash_a = Account(
            company_id=comp_a.id,
            name=f"Main Cash A {uid}",
            code=f"CSH-A-{uid}",
            type=AccountType.ASSET,
        )
        acc_bank_a = Account(
            company_id=comp_a.id,
            name=f"HDFC Bank A {uid}",
            code=f"BNK-A-{uid}",
            type=AccountType.ASSET,
        )
        acc_bank_b = Account(
            company_id=comp_b.id,
            name=f"ICICI Bank B {uid}",
            code=f"BNK-B-{uid}",
            type=AccountType.ASSET,
        )
        db.add_all([acc_salary_a, acc_cash_a, acc_bank_a, acc_bank_b])
        await db.flush()

        # Bank Accounts
        bank_a = BankAccount(
            account_id=acc_bank_a.id,
            bank_name="HDFC Bank",
            account_number=f"ACC-A-{uid}",
            ifsc_code="HDFC0001",
        )
        bank_b = BankAccount(
            account_id=acc_bank_b.id,
            bank_name="ICICI Bank",
            account_number=f"ACC-B-{uid}",
            ifsc_code="ICIC0001",
        )
        db.add_all([bank_a, bank_b])
        await db.flush()

        # 3. Company Settings
        # Ensure company settings exists and points to our test salary & cash accounts
        first_cs = await db.scalar(select(CompanySettings))
        orig_salary_id = None
        orig_cash_id = None
        if first_cs:
            orig_salary_id = first_cs.staff_salary_account_id
            orig_cash_id = first_cs.primary_cash_account_id
            first_cs.staff_salary_account_id = acc_salary_a.id
            first_cs.primary_cash_account_id = acc_cash_a.id
        else:
            first_cs = CompanySettings(
                company_id=comp_a.id,
                company_name=f"Brand_CompA_{uid}",
                staff_salary_account_id=acc_salary_a.id,
                primary_cash_account_id=acc_cash_a.id,
            )
            db.add(first_cs)
        await db.flush()

        # 4. Users
        pwd_hash = get_password_hash("Secret123!")

        super_admin = User(
            email=f"superadmin_p_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin P",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_pa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin P",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_pb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin P",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        staff_a = User(
            email=f"staff_pa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Staff Engineer A",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=UserRole.SITE_ENGINEER.value,
            designation="Site Engineer",
            department="Civil",
        )
        staff_b = User(
            email=f"staff_pb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Staff Engineer B",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role=UserRole.SITE_ENGINEER.value,
            designation="Site Engineer",
            department="Civil",
        )
        ineligible_user_a = User(
            email=f"contractor_pa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Ineligible User A",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Contractor",
        )
        custom_role_name = f"PayrollManager_{uid}"
        user_custom_a = User(
            email=f"custom_pa_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Payroll Manager P",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )
        legacy_admin_no_perm = User(
            email=f"legacy_admin_p_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy Admin No Perm P",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=f"EmptyAdminP_{uid}",
        )
        dummy_none_company_user = User(
            email=f"nonecomp_p_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="None Comp User P",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Staff",
        )

        db.add_all([
            super_admin,
            admin_a,
            admin_b,
            staff_a,
            staff_b,
            ineligible_user_a,
            user_custom_a,
            legacy_admin_no_perm,
            dummy_none_company_user,
        ])
        await db.flush()

        # 5. Projects
        proj_a1 = Project(
            business_id=f"PRJ-PA1-{uid}",
            project_name=f"Project PA1 {uid}",
            company_id=comp_a.id,
            owner_id=owner_a1.id,
            status=ProjectStatus.ONGOING,
            start_date=date(2025, 1, 1),
            end_date=date(2027, 1, 1),
        )
        proj_b1 = Project(
            business_id=f"PRJ-PB1-{uid}",
            project_name=f"Project PB1 {uid}",
            company_id=comp_b.id,
            owner_id=owner_b1.id,
            status=ProjectStatus.ONGOING,
            start_date=date(2025, 1, 1),
            end_date=date(2027, 1, 1),
        )
        db.add_all([proj_a1, proj_b1])
        await db.flush()

        # 6. Labour & Attendance
        labour_a = Labour(
            company_id=comp_a.id,
            worker_code=f"LAB-PA-{uid}",
            labour_name=f"Labour PA {uid}",
        )
        labour_b = Labour(
            company_id=comp_b.id,
            worker_code=f"LAB-PB-{uid}",
            labour_name=f"Labour PB {uid}",
        )
        db.add_all([labour_a, labour_b])
        await db.flush()

        att_a = LabourAttendance(
            labour_id=labour_a.id,
            project_id=proj_a1.id,
            attendance_date=date(2026, 1, 15),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("8.00"),
            task_description="Foundation masonry",
        )
        att_b = LabourAttendance(
            labour_id=labour_b.id,
            project_id=proj_b1.id,
            attendance_date=date(2026, 1, 15),
            status=AttendanceStatus.PRESENT,
            working_hours=Decimal("10.00"),
            task_description="Excavation",
        )
        db.add_all([att_a, att_b])
        await db.flush()

        # 7. LabourPayroll records
        payroll_a_pending = LabourPayroll(
            labour_id=labour_a.id,
            project_id=proj_a1.id,
            month=1,
            year=2026,
            total_working_hours=Decimal("160.00"),
            total_wage=Decimal("5000.00"),
            paid_amount=Decimal("2000.00"),
            remaining_amount=Decimal("3000.00"),
            advance_adjusted=Decimal("500.00"),
            status=PayrollStatus.PENDING,
        )
        payroll_a_paid = LabourPayroll(
            labour_id=labour_a.id,
            project_id=proj_a1.id,
            month=2,
            year=2026,
            total_working_hours=Decimal("160.00"),
            total_wage=Decimal("2000.00"),
            paid_amount=Decimal("2000.00"),
            remaining_amount=Decimal("0.00"),
            advance_adjusted=Decimal("0.00"),
            status=PayrollStatus.PAID,
        )
        payroll_b_pending = LabourPayroll(
            labour_id=labour_b.id,
            project_id=proj_b1.id,
            month=1,
            year=2026,
            total_working_hours=Decimal("180.00"),
            total_wage=Decimal("9000.00"),
            paid_amount=Decimal("4000.00"),
            remaining_amount=Decimal("5000.00"),
            advance_adjusted=Decimal("1000.00"),
            status=PayrollStatus.PENDING,
        )
        payroll_b_paid = LabourPayroll(
            labour_id=labour_b.id,
            project_id=proj_b1.id,
            month=2,
            year=2026,
            total_working_hours=Decimal("180.00"),
            total_wage=Decimal("4000.00"),
            paid_amount=Decimal("4000.00"),
            remaining_amount=Decimal("0.00"),
            advance_adjusted=Decimal("0.00"),
            status=PayrollStatus.PAID,
        )
        db.add_all([payroll_a_pending, payroll_a_paid, payroll_b_pending, payroll_b_paid])
        await db.flush()

        # 8. RABills (Contractor Bills)
        ra_bill_a = RABill(
            project_id=proj_a1.id,
            bill_number=f"RA-PA-{uid}",
            work_description="Civil Work Part A",
            quantity=Decimal("100.000"),
            rate=Decimal("250.00"),
            gross_amount=Decimal("25000.00"),
            deductions=Decimal("1000.00"),
            net_amount=Decimal("24000.00"),
            total_amount=Decimal("24000.00"),
            bill_date=date(2026, 1, 20),
            status="Submitted",
        )
        ra_bill_b = RABill(
            project_id=proj_b1.id,
            bill_number=f"RA-PB-{uid}",
            work_description="Civil Work Part B",
            quantity=Decimal("200.000"),
            rate=Decimal("300.00"),
            gross_amount=Decimal("60000.00"),
            deductions=Decimal("2000.00"),
            net_amount=Decimal("58000.00"),
            total_amount=Decimal("58000.00"),
            bill_date=date(2026, 1, 22),
            status="Submitted",
        )
        db.add_all([ra_bill_a, ra_bill_b])
        await db.flush()

        # 9. Existing Transactions
        txn_staff_a = Transaction(
            project_id=proj_a1.id,
            type="payment",
            amount=Decimal("40000.00"),
            mode="bank",
            reference="gross:45000.00|deduct:5000.00",
            linked_to=f"STAFF-SALARY:{staff_a.id}:2026-05",
            created_by=admin_a.id,
        )
        txn_staff_b = Transaction(
            project_id=proj_b1.id,
            type="payment",
            amount=Decimal("42000.00"),
            mode="bank",
            reference="gross:48000.00|deduct:6000.00",
            linked_to=f"STAFF-SALARY:{staff_b.id}:2026-05",
            created_by=admin_b.id,
        )
        txn_labour_a = Transaction(
            project_id=proj_a1.id,
            type="payment",
            amount=Decimal("2000.00"),
            mode="cash",
            reference="gross:2500.00|deduct:500.00",
            linked_to=f"LABOUR-WAGE:{labour_a.id}:2026-01",
            created_by=admin_a.id,
        )
        db.add_all([txn_staff_a, txn_staff_b, txn_labour_a])
        await db.flush()

        # 10. RBAC Roles & DB Permissions
        role_custom = Role(
            name=custom_role_name,
            display_name="Custom Payroll Manager",
            company_id=comp_a.id,
        )
        role_legacy_empty = Role(
            name=f"EmptyAdminP_{uid}",
            display_name="Empty Admin P",
            company_id=comp_a.id,
        )
        db.add_all([role_custom, role_legacy_empty])
        await db.flush()

        # Existing payroll permissions from database catalog
        res_perms = await db.execute(select(Permission).where(Permission.module == "payroll"))
        perms = {p.code: p for p in res_perms.scalars().all()}

        # Fetch or create wildcard permission
        res_wc = await db.execute(select(Permission).where(Permission.code == "payroll.*"))
        perm_wc = res_wc.scalar_one_or_none()
        if not perm_wc:
            perm_wc = Permission(
                code="payroll.*",
                module="payroll",
                action="*",
                description="Wildcard payroll management",
            )
            db.add(perm_wc)
            await db.flush()
        perms["payroll.*"] = perm_wc

        # Ensure Admin roles have permissions bound in DB
        role_admin_a = (
            await db.execute(select(Role).where(Role.name == "Admin", Role.company_id == comp_a.id))
        ).scalar_one_or_none()
        if not role_admin_a:
            role_admin_a = Role(name=f"Admin_PA_{uid}", display_name="Admin PA", company_id=comp_a.id)
            db.add(role_admin_a)
            await db.flush()
            admin_a.role = role_admin_a.name
            await db.flush()

        for code in ["payroll.view", "payroll.create", "payroll.export"]:
            if code in perms:
                db.add(RolePermission(role=role_admin_a.name, role_id=role_admin_a.id, permission_id=perms[code].id))

        role_admin_b = (
            await db.execute(select(Role).where(Role.name == "Admin", Role.company_id == comp_b.id))
        ).scalar_one_or_none()
        if not role_admin_b:
            role_admin_b = Role(name=f"Admin_PB_{uid}", display_name="Admin PB", company_id=comp_b.id)
            db.add(role_admin_b)
            await db.flush()
            admin_b.role = role_admin_b.name
            await db.flush()

        for code in ["payroll.view", "payroll.create", "payroll.export"]:
            if code in perms:
                db.add(RolePermission(role=role_admin_b.name, role_id=role_admin_b.id, permission_id=perms[code].id))

        await db.commit()

        # Auth tokens
        tokens = {
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
            "user_custom_a": create_access_token({"sub": str(user_custom_a.id)}),
            "legacy_admin_no_perm": create_access_token({"sub": str(legacy_admin_no_perm.id)}),
            "dummy_none_company_user": create_access_token({"sub": str(dummy_none_company_user.id)}),
        }

        data = {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "super_admin": super_admin,
            "admin_a": admin_a,
            "admin_b": admin_b,
            "staff_a": staff_a,
            "staff_b": staff_b,
            "ineligible_user_a": ineligible_user_a,
            "user_custom_a": user_custom_a,
            "legacy_admin_no_perm": legacy_admin_no_perm,
            "dummy_none_company_user": dummy_none_company_user,
            "proj_a1": proj_a1,
            "proj_b1": proj_b1,
            "labour_a": labour_a,
            "labour_b": labour_b,
            "payroll_a": payroll_a_pending,
            "payroll_b": payroll_b_pending,
            "ra_bill_a": ra_bill_a,
            "ra_bill_b": ra_bill_b,
            "bank_a": bank_a,
            "bank_b": bank_b,
            "acc_salary_a": acc_salary_a,
            "acc_cash_a": acc_cash_a,
            "acc_bank_a": acc_bank_a,
            "acc_bank_b": acc_bank_b,
            "role_custom": role_custom,
            "role_legacy_empty": role_legacy_empty,
            "role_admin_a": role_admin_a,
            "role_admin_b": role_admin_b,
            "perms": perms,
            "tokens": tokens,
        }

        try:
            yield data
        finally:
            async with AsyncSessionLocal() as clean_db:
                c_ids = [comp_a.id, comp_b.id]
                u_ids = [
                    super_admin.id, admin_a.id, admin_b.id, staff_a.id, staff_b.id,
                    ineligible_user_a.id, user_custom_a.id, legacy_admin_no_perm.id, dummy_none_company_user.id,
                ]
                r_ids = [role_custom.id, role_legacy_empty.id, role_admin_a.id, role_admin_b.id]
                p_ids = [proj_a1.id, proj_b1.id]
                l_ids = [labour_a.id, labour_b.id]
                o_ids = [owner_a1.id, owner_b1.id]

                # Restore settings
                if first_cs:
                    db_cs = await clean_db.get(CompanySettings, first_cs.id)
                    if db_cs:
                        db_cs.staff_salary_account_id = orig_salary_id
                        db_cs.primary_cash_account_id = orig_cash_id

                await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(u_ids)))
                await clean_db.execute(delete(RolePermission).where(RolePermission.role_id.in_(r_ids)))
                await clean_db.execute(delete(Role).where(Role.id.in_(r_ids)))
                await clean_db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(u_ids)))
                await clean_db.execute(delete(Transaction).where(Transaction.project_id.in_(p_ids)))
                await clean_db.execute(delete(JournalLine).where(JournalLine.account_id.in_([acc_salary_a.id, acc_cash_a.id, acc_bank_a.id, acc_bank_b.id])))
                await clean_db.execute(delete(JournalEntry).where(JournalEntry.created_by.in_(u_ids)))
                await clean_db.execute(delete(LabourPayroll).where(LabourPayroll.project_id.in_(p_ids)))
                await clean_db.execute(delete(LabourAttendance).where(LabourAttendance.project_id.in_(p_ids)))
                await clean_db.execute(delete(Labour).where(Labour.id.in_(l_ids)))
                await clean_db.execute(delete(RABill).where(RABill.project_id.in_(p_ids)))
                await clean_db.execute(delete(BankAccount).where(BankAccount.id.in_([bank_a.id, bank_b.id])))
                await clean_db.execute(delete(Account).where(Account.id.in_([acc_salary_a.id, acc_cash_a.id, acc_bank_a.id, acc_bank_b.id])))
                await clean_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_(p_ids)))
                await clean_db.execute(delete(Project).where(Project.id.in_(p_ids)))
                await clean_db.execute(delete(Owner).where(Owner.id.in_(o_ids)))
                await clean_db.execute(delete(User).where(User.id.in_(u_ids)))
                await clean_db.execute(delete(Company).where(Company.id.in_(c_ids)))
                await clean_db.commit()


# ============================================================================
# 1. 401 UNAUTHORIZED ACROSS ALL 11 ROUTES (NO TOKEN)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_401_no_token_all_routes():
    """All 11 Batch P endpoints must return 401 when accessed without an authorization token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("GET", "/api/v1/accountant/payroll/summary"),
            ("GET", "/api/v1/accountant/payroll/payslip/export"),
            ("GET", "/api/v1/accountant/payroll/staff/register"),
            ("POST", "/api/v1/accountant/payroll/staff/process"),
            ("GET", "/api/v1/accountant/payroll/staff/history"),
            ("GET", "/api/v1/accountant/payroll/labour/wages?start_date=2026-01-01&end_date=2026-01-31"),
            ("GET", "/api/v1/accountant/payroll/contractor/bills"),
            ("GET", "/api/v1/accountant/payroll/staff/export"),
            ("GET", "/api/v1/accountant/payroll/contractor/export"),
            ("GET", "/api/v1/accountant/payroll/register/export"),
            ("GET", "/api/v1/accountant/payroll/register"),
        ]

        for method, path in endpoints:
            if method == "GET":
                res = await ac.get(path)
            else:
                res = await ac.post(path, json={})
            assert res.status_code == 401, f"{method} {path} returned {res.status_code}, expected 401"


# ============================================================================
# 2. 403 FORBIDDEN ACROSS ALL 11 ROUTES (AUTHENTICATED, ZERO PERMISSIONS)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_403_authenticated_zero_permissions_all_routes():
    """Authenticated user with 0 DB permissions must receive 403 Forbidden across all 11 endpoints."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["legacy_admin_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}
        staff_a_id = data["staff_a"].id
        proj_a_id = data["proj_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            endpoints = [
                ("GET", "/api/v1/accountant/payroll/summary", None),
                ("GET", "/api/v1/accountant/payroll/payslip/export", None),
                ("GET", "/api/v1/accountant/payroll/staff/register", None),
                ("POST", "/api/v1/accountant/payroll/staff/process", {
                    "user_id": staff_a_id,
                    "project_id": proj_a_id,
                    "month_year": "2026-06",
                    "gross_salary": "50000.00",
                    "deductions": "5000.00",
                    "net_salary": "45000.00",
                    "payment_mode": "cash",
                }),
                ("GET", "/api/v1/accountant/payroll/staff/history", None),
                ("GET", "/api/v1/accountant/payroll/labour/wages?start_date=2026-01-01&end_date=2026-01-31", None),
                ("GET", "/api/v1/accountant/payroll/contractor/bills", None),
                ("GET", "/api/v1/accountant/payroll/staff/export", None),
                ("GET", "/api/v1/accountant/payroll/contractor/export", None),
                ("GET", "/api/v1/accountant/payroll/register/export", None),
                ("GET", "/api/v1/accountant/payroll/register", None),
            ]

            for method, path, payload in endpoints:
                if method == "GET":
                    res = await ac.get(path, headers=headers)
                else:
                    res = await ac.post(path, headers=headers, json=payload)
                assert res.status_code == 403, f"{method} {path} returned {res.status_code}, expected 403"


# ============================================================================
# 3. DYNAMIC DB GRANT AND REVOKE
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_dynamic_db_grant_and_revoke():
    """Runtime DB permission grant allows access; runtime DB revoke removes access immediately."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_custom = data["role_custom"]
        perm_view = data["perms"]["payroll.view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Initially 403
            res1 = await ac.get("/api/v1/accountant/payroll/summary", headers=headers)
            assert res1.status_code == 403

            # 2. Grant permission in DB
            async with AsyncSessionLocal() as db:
                rp = RolePermission(role=role_custom.name, role_id=role_custom.id, permission_id=perm_view.id)
                db.add(rp)
                await db.commit()

            # 3. Immediate 200 without restart
            res2 = await ac.get("/api/v1/accountant/payroll/summary", headers=headers)
            assert res2.status_code == 200

            # 4. Revoke permission from DB
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role_custom.id,
                        RolePermission.permission_id == perm_view.id,
                    )
                )
                await db.commit()

            # 5. Immediate 403
            res3 = await ac.get("/api/v1/accountant/payroll/summary", headers=headers)
            assert res3.status_code == 403


# ============================================================================
# 4. POSITIVE AND NEGATIVE USER PERMISSION OVERRIDES
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_positive_and_negative_user_overrides():
    """Positive user override grants access when role lacks it; negative user override blocks access when role has it."""
    async with setup_batch_p_data() as data:
        token_custom = data["tokens"]["user_custom_a"]
        token_admin = data["tokens"]["admin_a"]
        user_custom_id = data["user_custom_a"].id
        admin_a_id = data["admin_a"].id
        perm_view_id = data["perms"]["payroll.view"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # A. Positive Override: user_custom_a lacks role permission -> override True -> 200
            res_before = await ac.get("/api/v1/accountant/payroll/summary", headers={"Authorization": f"Bearer {token_custom}"})
            assert res_before.status_code == 403

            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user_custom_id, permission_id=perm_view_id, is_granted=True))
                await db.commit()

            res_after_grant = await ac.get("/api/v1/accountant/payroll/summary", headers={"Authorization": f"Bearer {token_custom}"})
            assert res_after_grant.status_code == 200

            # B. Negative Override: admin_a has role permission -> override False -> 403
            res_admin_before = await ac.get("/api/v1/accountant/payroll/summary", headers={"Authorization": f"Bearer {token_admin}"})
            assert res_admin_before.status_code == 200

            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=admin_a_id, permission_id=perm_view_id, is_granted=False))
                await db.commit()

            res_admin_after_deny = await ac.get("/api/v1/accountant/payroll/summary", headers={"Authorization": f"Bearer {token_admin}"})
            assert res_admin_after_deny.status_code == 403


# ============================================================================
# 5. WILDCARD PERMISSION (payroll.*)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_wildcard_permission():
    """Granting 'payroll.*' enables view, create, and export actions."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_custom = data["role_custom"]
        perm_wc = data["perms"]["payroll.*"]
        staff_a_id = data["staff_a"].id
        proj_a_id = data["proj_a1"].id

        async with AsyncSessionLocal() as db:
            db.add(RolePermission(role=role_custom.name, role_id=role_custom.id, permission_id=perm_wc.id))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. View route
            res_view = await ac.get("/api/v1/accountant/payroll/summary", headers=headers)
            assert res_view.status_code == 200

            # 2. Export route
            res_export = await ac.get("/api/v1/accountant/payroll/payslip/export", headers=headers)
            assert res_export.status_code == 200
            assert "Employee/Labour Name" in res_export.text

            # 3. Create route
            res_create = await ac.post(
                "/api/v1/accountant/payroll/staff/process",
                headers=headers,
                json={
                    "user_id": staff_a_id,
                    "project_id": proj_a_id,
                    "month_year": "2026-07",
                    "gross_salary": "50000.00",
                    "deductions": "5000.00",
                    "net_salary": "45000.00",
                    "payment_mode": "cash",
                },
            )
            assert res_create.status_code == 200
            assert res_create.json()["message"] == "Staff salary processed successfully"


# ============================================================================
# 6. LEGACY ROLE IMMUNITY (Role name 'Admin' with zero DB permissions -> 403)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_legacy_role_immunity():
    """Role name 'Admin' or 'Accountant' alone grants zero access when no permissions exist in DB."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["legacy_admin_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/accountant/payroll/summary", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 7. OWN-TENANT SUCCESS ACROSS ALL 11 ROUTES
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_own_tenant_success_all_routes():
    """Company A admin with valid DB permissions succeeds across all 11 endpoints."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        staff_a_id = data["staff_a"].id
        proj_a_id = data["proj_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Summary
            res1 = await ac.get("/api/v1/accountant/payroll/summary", headers=headers)
            assert res1.status_code == 200
            assert "pending_payroll" in res1.json()

            # 2. Payslip export
            res2 = await ac.get("/api/v1/accountant/payroll/payslip/export", headers=headers)
            assert res2.status_code == 200
            assert res2.headers["content-type"].startswith("text/csv")
            assert "Employee/Labour Name" in res2.text

            # 3. Staff register
            res3 = await ac.get("/api/v1/accountant/payroll/staff/register", headers=headers)
            assert res3.status_code == 200
            assert any(u["user_id"] == staff_a_id for u in res3.json())

            # 4. Staff process
            res4 = await ac.post(
                "/api/v1/accountant/payroll/staff/process",
                headers=headers,
                json={
                    "user_id": staff_a_id,
                    "project_id": proj_a_id,
                    "month_year": "2026-08",
                    "gross_salary": "60000.00",
                    "deductions": "6000.00",
                    "net_salary": "54000.00",
                    "payment_mode": "cash",
                },
            )
            assert res4.status_code == 200
            assert res4.json()["message"] == "Staff salary processed successfully"

            # 5. Staff history
            res5 = await ac.get("/api/v1/accountant/payroll/staff/history", headers=headers)
            assert res5.status_code == 200
            assert isinstance(res5.json(), list)

            # 6. Labour wages
            res6 = await ac.get("/api/v1/accountant/payroll/labour/wages?start_date=2026-01-01&end_date=2026-01-31", headers=headers)
            assert res6.status_code == 200
            assert isinstance(res6.json(), list)

            # 7. Contractor bills
            res7 = await ac.get("/api/v1/accountant/payroll/contractor/bills", headers=headers)
            assert res7.status_code == 200
            assert any(b["bill_number"] == data["ra_bill_a"].bill_number for b in res7.json())

            # 8. Staff export
            res8 = await ac.get("/api/v1/accountant/payroll/staff/export", headers=headers)
            assert res8.status_code == 200
            assert "Staff Name" in res8.text

            # 9. Contractor export
            res9 = await ac.get("/api/v1/accountant/payroll/contractor/export", headers=headers)
            assert res9.status_code == 200
            assert "Contractor" in res9.text

            # 10. Register export
            res10 = await ac.get("/api/v1/accountant/payroll/register/export", headers=headers)
            assert res10.status_code == 200
            assert "Payroll Type" in res10.text

            # 11. Register
            res11 = await ac.get("/api/v1/accountant/payroll/register", headers=headers)
            assert res11.status_code == 200
            assert isinstance(res11.json(), list)


# ============================================================================
# 8. CROSS-TENANT IDOR GUARDS (STAFF, PROJECT, BANK ACCOUNT -> 404)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_cross_tenant_idor_staff_project_bank():
    """Processing staff salary with Company B's staff, project, or bank account returns masked 404."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        staff_a_id = data["staff_a"].id
        staff_b_id = data["staff_b"].id
        proj_a_id = data["proj_a1"].id
        proj_b_id = data["proj_b1"].id
        bank_b_id = data["bank_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Foreign staff user -> 404
            res_staff = await ac.post(
                "/api/v1/accountant/payroll/staff/process",
                headers=headers,
                json={
                    "user_id": staff_b_id,
                    "project_id": proj_a_id,
                    "month_year": "2026-09",
                    "gross_salary": "50000.00",
                    "deductions": "5000.00",
                    "net_salary": "45000.00",
                    "payment_mode": "cash",
                },
            )
            assert res_staff.status_code == 404
            assert "Staff user not found" in res_staff.json()["detail"]

            # 2. Foreign project -> 404
            res_proj = await ac.post(
                "/api/v1/accountant/payroll/staff/process",
                headers=headers,
                json={
                    "user_id": staff_a_id,
                    "project_id": proj_b_id,
                    "month_year": "2026-09",
                    "gross_salary": "50000.00",
                    "deductions": "5000.00",
                    "net_salary": "45000.00",
                    "payment_mode": "cash",
                },
            )
            assert res_proj.status_code == 404
            assert "Project not found" in res_proj.json()["detail"]

            # 3. Foreign bank account -> 404
            res_bank = await ac.post(
                "/api/v1/accountant/payroll/staff/process",
                headers=headers,
                json={
                    "user_id": staff_a_id,
                    "project_id": proj_a_id,
                    "month_year": "2026-09",
                    "gross_salary": "50000.00",
                    "deductions": "5000.00",
                    "net_salary": "45000.00",
                    "payment_mode": "bank",
                    "bank_account_id": bank_b_id,
                },
            )
            assert res_bank.status_code == 404
            assert "Bank account not found" in res_bank.json()["detail"]


# ============================================================================
# 9. TENANT DATA ISOLATION (SUMMARY, REGISTERS, HISTORY, WAGES, BILLS, EXPORTS)
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_tenant_data_isolation():
    """All payroll data queries and exports strictly isolate records by caller company."""
    async with setup_batch_p_data() as data:
        token_a = data["tokens"]["admin_a"]
        token_b = data["tokens"]["admin_b"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Summary Isolation
            res_sum_a = await ac.get("/api/v1/accountant/payroll/summary", headers=headers_a)
            assert res_sum_a.status_code == 200
            sum_a = res_sum_a.json()
            assert sum_a["pending_payroll"] == 3000.0  # Company A (5000 - 2000)
            assert sum_a["paid_payroll"] == 2000.0
            assert sum_a["advance_given"] == 500.0

            res_sum_b = await ac.get("/api/v1/accountant/payroll/summary", headers=headers_b)
            assert res_sum_b.status_code == 200
            sum_b = res_sum_b.json()
            assert sum_b["pending_payroll"] == 5000.0  # Company B (9000 - 4000)
            assert sum_b["paid_payroll"] == 4000.0
            assert sum_b["advance_given"] == 1000.0

            # 2. Staff Register Isolation
            res_reg_a = await ac.get("/api/v1/accountant/payroll/staff/register", headers=headers_a)
            assert res_reg_a.status_code == 200
            user_ids_a = [u["user_id"] for u in res_reg_a.json()]
            assert data["staff_a"].id in user_ids_a
            assert data["staff_b"].id not in user_ids_a

            # 3. Staff History Isolation
            res_hist_a = await ac.get("/api/v1/accountant/payroll/staff/history", headers=headers_a)
            assert res_hist_a.status_code == 200
            hist_a = res_hist_a.json()
            assert all(t["project_id"] == data["proj_a1"].id for t in hist_a)

            # 4. Labour Wages Isolation
            res_wages_a = await ac.get("/api/v1/accountant/payroll/labour/wages?start_date=2026-01-01&end_date=2026-01-31", headers=headers_a)
            assert res_wages_a.status_code == 200
            wages_a = res_wages_a.json()
            assert any(w["labour_id"] == data["labour_a"].id for w in wages_a)
            assert not any(w["labour_id"] == data["labour_b"].id for w in wages_a)

            # 5. Contractor Bills Isolation
            res_bills_a = await ac.get("/api/v1/accountant/payroll/contractor/bills", headers=headers_a)
            assert res_bills_a.status_code == 200
            bills_a = res_bills_a.json()
            assert any(b["bill_number"] == data["ra_bill_a"].bill_number for b in bills_a)
            assert not any(b["bill_number"] == data["ra_bill_b"].bill_number for b in bills_a)

            # 6. CSV Exports Isolation
            res_exp_staff = await ac.get("/api/v1/accountant/payroll/staff/export", headers=headers_a)
            assert res_exp_staff.status_code == 200
            assert data["staff_a"].full_name in res_exp_staff.text
            assert data["staff_b"].full_name not in res_exp_staff.text

            res_exp_payslip = await ac.get("/api/v1/accountant/payroll/payslip/export", headers=headers_a)
            assert res_exp_payslip.status_code == 200
            assert data["labour_a"].labour_name in res_exp_payslip.text
            assert data["labour_b"].labour_name not in res_exp_payslip.text

            res_exp_contractor = await ac.get("/api/v1/accountant/payroll/contractor/export", headers=headers_a)
            assert res_exp_contractor.status_code == 200
            assert data["ra_bill_a"].bill_number in res_exp_contractor.text
            assert data["ra_bill_b"].bill_number not in res_exp_contractor.text


# ============================================================================
# 10. SUPER ADMIN GLOBAL ACCESS SEMANTICS
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_super_admin_cross_company_access():
    """Super Admin can access cross-company payroll metrics and staff register."""
    async with setup_batch_p_data() as data:
        token_sa = data["tokens"]["super_admin"]
        headers_sa = {"Authorization": f"Bearer {token_sa}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Summary aggregates cross-company data (3000 from A + 5000 from B = 8000.0)
            res_sum = await ac.get("/api/v1/accountant/payroll/summary", headers=headers_sa)
            assert res_sum.status_code == 200
            assert res_sum.json()["pending_payroll"] >= 8000.0

            # 2. Staff register returns users from multiple companies
            res_reg = await ac.get("/api/v1/accountant/payroll/staff/register", headers=headers_sa)
            assert res_reg.status_code == 200
            user_ids = [u["user_id"] for u in res_reg.json()]
            assert data["staff_a"].id in user_ids
            assert data["staff_b"].id in user_ids


# ============================================================================
# 11. NON-SA COMPANY_ID=NONE ISOLATION
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_non_sa_company_id_none():
    """Non-SA user with company_id=None is denied with 403."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["dummy_none_company_user"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/accountant/payroll/summary", headers=headers)
            assert res.status_code == 403


# ============================================================================
# 12. BUSINESS INVARIANTS: DUPLICATE PROTECTION & STAFF ROLE ELIGIBILITY
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_business_invariants_duplicate_and_eligibility():
    """Duplicate salary processing and ineligible staff roles are rejected with 400."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        staff_a_id = data["staff_a"].id
        ineligible_user_id = data["ineligible_user_a"].id
        proj_a_id = data["proj_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Ineligible staff role -> 400
            res_ineligible = await ac.post(
                "/api/v1/accountant/payroll/staff/process",
                headers=headers,
                json={
                    "user_id": ineligible_user_id,
                    "project_id": proj_a_id,
                    "month_year": "2026-10",
                    "gross_salary": "50000.00",
                    "deductions": "5000.00",
                    "net_salary": "45000.00",
                    "payment_mode": "cash",
                },
            )
            assert res_ineligible.status_code == 400
            assert "Invalid staff user" in res_ineligible.json()["detail"]

            # 2. Duplicate salary protection
            # First attempt -> 200
            res1 = await ac.post(
                "/api/v1/accountant/payroll/staff/process",
                headers=headers,
                json={
                    "user_id": staff_a_id,
                    "project_id": proj_a_id,
                    "month_year": "2026-10",
                    "gross_salary": "50000.00",
                    "deductions": "5000.00",
                    "net_salary": "45000.00",
                    "payment_mode": "cash",
                },
            )
            assert res1.status_code == 200

            # Second attempt with same user_id and month_year -> 400
            res2 = await ac.post(
                "/api/v1/accountant/payroll/staff/process",
                headers=headers,
                json={
                    "user_id": staff_a_id,
                    "project_id": proj_a_id,
                    "month_year": "2026-10",
                    "gross_salary": "50000.00",
                    "deductions": "5000.00",
                    "net_salary": "45000.00",
                    "payment_mode": "cash",
                },
            )
            assert res2.status_code == 400
            assert "Salary already processed for this month" in res2.json()["detail"]


# ============================================================================
# 13. BALANCED JOURNAL ENTRY & TRANSACTION CREATION VERIFICATION
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_balanced_journal_and_transaction_verification():
    """Verify that processing staff salary generates balanced JournalLines and a linked Transaction."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        staff_a_id = data["staff_a"].id
        proj_a_id = data["proj_a1"].id
        bank_a_id = data["bank_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/accountant/payroll/staff/process",
                headers=headers,
                json={
                    "user_id": staff_a_id,
                    "project_id": proj_a_id,
                    "month_year": "2026-11",
                    "gross_salary": "80000.00",
                    "deductions": "8000.00",
                    "net_salary": "72000.00",
                    "payment_mode": "bank",
                    "bank_account_id": bank_a_id,
                },
            )
            assert res.status_code == 200

        async with AsyncSessionLocal() as db:
            linked_id = f"STAFF-SALARY:{staff_a_id}:2026-11"
            txn = await db.scalar(select(Transaction).where(Transaction.linked_to == linked_id))
            assert txn is not None
            assert txn.project_id == proj_a_id
            assert txn.amount == Decimal("72000.00")
            assert txn.mode == "bank"
            assert txn.reference == "gross:80000.00|deduct:8000.00"
            assert txn.journal_entry_id is not None

            # Verify Journal Entry & Balanced Lines
            je = await db.get(JournalEntry, txn.journal_entry_id)
            assert je is not None

            lines = (await db.execute(select(JournalLine).where(JournalLine.entry_id == je.id))).scalars().all()
            assert len(lines) == 2

            total_debit = sum(l.debit for l in lines)
            total_credit = sum(l.credit for l in lines)
            assert total_debit == Decimal("72000.00")
            assert total_credit == Decimal("72000.00")
            assert total_debit == total_credit  # Balanced double-entry!


# ============================================================================
# 14. CSV HEADERS, CONTENT-TYPE, AND INTEGRITY
# ============================================================================

@pytest.mark.asyncio
async def test_batch_p_csv_headers_and_integrity():
    """Verify all 4 CSV export streaming responses provide proper headers and content."""
    async with setup_batch_p_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exports = [
                ("/api/v1/accountant/payroll/payslip/export", "payslips_export.csv", "Employee/Labour Name,Period,Gross Pay,Deduction,Net Pay,Status,Payment Date"),
                ("/api/v1/accountant/payroll/staff/export", "staff_salary.csv", "Staff Name,Role,Department,Designation,Month,Gross Salary,Deductions,Net Salary,Payment Status,Payment Date"),
                ("/api/v1/accountant/payroll/contractor/export", "contractor_payments.csv", "Contractor,Project,Bill Number,Gross Amount,Deductions,Net Payable,Payment Status,Payment Date"),
                ("/api/v1/accountant/payroll/register/export", "payroll_register.csv", "Name,Payroll Type,Period,Gross,Deduction,Net Amount,Status,Payment Date"),
            ]

            for url, filename, expected_header in exports:
                res = await ac.get(url, headers=headers)
                assert res.status_code == 200
                assert res.headers["content-type"].startswith("text/csv")
                assert f"filename={filename}" in res.headers["content-disposition"]
                assert expected_header in res.text
