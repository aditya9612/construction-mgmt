import uuid
from decimal import Decimal
from datetime import date, datetime
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus, Checklist
from app.models.invoice import Transaction
from app.models.accountant import VendorBill, VendorBillStatus
from app.models.material import Supplier, PurchaseOrder
from app.models.quotation import QuotationMaster
from app.models.labour import Labour, LabourPayroll, PayrollStatus
from app.models.equipment import Equipment, EquipmentRental, EquipmentCondition
from app.models.rbac import Role, Permission, RolePermission
from app.core.security import get_password_hash, create_access_token


@asynccontextmanager
async def setup_project_delete_test_data():
    """
    Sets up isolated test data for project deletion dependency verification:
    - Company A & Company B
    - Admin A & Admin B (with projects.delete permission)
    - Owners A & B
    - Dedicated test projects
    """
    uid = uuid.uuid4().hex[:8]
    created_entities = {
        "transactions": [],
        "vendor_bills": [],
        "quotations": [],
        "payrolls": [],
        "labour": [],
        "purchase_orders": [],
        "rentals": [],
        "equipment": [],
        "checklists": [],
        "suppliers": [],
        "projects": [],
        "owners": [],
        "users": [],
        "companies": [],
        "roles": [],
    }

    async with AsyncSessionLocal() as db:
        # 1. Companies
        comp_a = Company(name=f"CompA_PrjDel_{uid}")
        comp_b = Company(name=f"CompB_PrjDel_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()
        created_entities["companies"].extend([comp_a.id, comp_b.id])

        # 2. Permissions & Roles
        perm_del = await db.scalar(select(Permission).where(Permission.code == "projects.delete").limit(1))
        perm_view = await db.scalar(select(Permission).where(Permission.code == "projects.view").limit(1))

        role_admin_a = Role(name=f"AdminA_Del_{uid}", display_name="Admin A", company_id=comp_a.id)
        role_admin_b = Role(name=f"AdminB_Del_{uid}", display_name="Admin B", company_id=comp_b.id)
        db.add_all([role_admin_a, role_admin_b])
        await db.flush()
        created_entities["roles"].extend([role_admin_a.id, role_admin_b.id])

        if perm_del:
            db.add(RolePermission(role=role_admin_a.name, role_id=role_admin_a.id, permission_id=perm_del.id))
            db.add(RolePermission(role=role_admin_b.name, role_id=role_admin_b.id, permission_id=perm_del.id))
        if perm_view:
            db.add(RolePermission(role=role_admin_a.name, role_id=role_admin_a.id, permission_id=perm_view.id))
            db.add(RolePermission(role=role_admin_b.name, role_id=role_admin_b.id, permission_id=perm_view.id))
        await db.flush()

        # 3. Users
        pwd_hash = get_password_hash("Secret123!")
        admin_a = User(
            email=f"admin_a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin User A",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=role_admin_a.name,
        )
        admin_b = User(
            email=f"admin_b_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin User B",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role=role_admin_b.name,
        )
        db.add_all([admin_a, admin_b])
        await db.flush()
        created_entities["users"].extend([admin_a.id, admin_b.id])

        # 4. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-A-{uid}",
            owner_name="Owner A",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-B-{uid}",
            owner_name="Owner B",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()
        created_entities["owners"].extend([owner_a.id, owner_b.id])

        # 5. Base Projects
        proj_clean = Project(
            business_id=f"PRJ-CLN-{uid}",
            project_name=f"Clean Project {uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status=ProjectStatus.PLANNED,
            budget_amount=Decimal("1000.00"),
        )
        proj_b = Project(
            business_id=f"PRJ-B-{uid}",
            project_name=f"Company B Project {uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            status=ProjectStatus.PLANNED,
            budget_amount=Decimal("1000.00"),
        )
        db.add_all([proj_clean, proj_b])
        await db.flush()
        created_entities["projects"].extend([proj_clean.id, proj_b.id])

        await db.commit()

        tokens = {
            "admin_a": create_access_token({"sub": str(admin_a.id)}),
            "admin_b": create_access_token({"sub": str(admin_b.id)}),
        }

        context = {
            "uid": uid,
            "comp_a": comp_a,
            "comp_b": comp_b,
            "admin_a": admin_a,
            "admin_b": admin_b,
            "owner_a": owner_a,
            "owner_b": owner_b,
            "proj_clean": proj_clean,
            "proj_b": proj_b,
            "tokens": tokens,
            "created": created_entities,
        }

    try:
        yield context
    finally:
        # Robust Fixture Teardown: reverse dependency order
        async with AsyncSessionLocal() as clean_db:
            p_ids = created_entities["projects"]
            if p_ids:
                await clean_db.execute(delete(Transaction).where(Transaction.project_id.in_(p_ids)))
                await clean_db.execute(delete(VendorBill).where(VendorBill.project_id.in_(p_ids)))
                await clean_db.execute(delete(QuotationMaster).where(QuotationMaster.project_id.in_(p_ids)))
                await clean_db.execute(delete(LabourPayroll).where(LabourPayroll.project_id.in_(p_ids)))
                await clean_db.execute(delete(PurchaseOrder).where(PurchaseOrder.project_id.in_(p_ids)))
                await clean_db.execute(delete(EquipmentRental).where(EquipmentRental.project_id.in_(p_ids)))
                await clean_db.execute(delete(Checklist).where(Checklist.project_id.in_(p_ids)))
                await clean_db.execute(delete(Project).where(Project.id.in_(p_ids)))

            if created_entities["equipment"]:
                await clean_db.execute(delete(Equipment).where(Equipment.id.in_(created_entities["equipment"])))
            if created_entities["labour"]:
                await clean_db.execute(delete(Labour).where(Labour.id.in_(created_entities["labour"])))
            if created_entities["suppliers"]:
                await clean_db.execute(delete(Supplier).where(Supplier.id.in_(created_entities["suppliers"])))
            if created_entities["owners"]:
                await clean_db.execute(delete(Owner).where(Owner.id.in_(created_entities["owners"])))
            if created_entities["users"]:
                await clean_db.execute(delete(User).where(User.id.in_(created_entities["users"])))
            if created_entities["roles"]:
                await clean_db.execute(delete(RolePermission).where(RolePermission.role_id.in_(created_entities["roles"])))
                await clean_db.execute(delete(Role).where(Role.id.in_(created_entities["roles"])))
            if created_entities["companies"]:
                await clean_db.execute(delete(Company).where(Company.id.in_(created_entities["companies"])))
            await clean_db.commit()


@pytest.mark.asyncio
async def test_delete_project_clean_succeeds():
    """Verify that a project with no blocking dependencies is deleted successfully (HTTP 200)."""
    async with setup_project_delete_test_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        proj_id = data["proj_clean"].id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/projects/{proj_id}", headers=headers)
            assert res.status_code == 200
            json_body = res.json()
            assert json_body.get("success") is True
            assert f"Project_id {proj_id} deleted successfully" in json_body.get("message", "")

        # Verify row physically deleted from database
        async with AsyncSessionLocal() as db:
            p_in_db = await db.scalar(select(Project).where(Project.id == proj_id))
            assert p_in_db is None


@pytest.mark.asyncio
async def test_delete_project_with_transaction_rejected_409():
    """Verify that a project with a linked Transaction is rejected with HTTP 409 Conflict."""
    async with setup_project_delete_test_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        uid = data["uid"]

        async with AsyncSessionLocal() as db:
            p = Project(
                business_id=f"PRJ-TX-{uid}",
                project_name=f"Project TX {uid}",
                company_id=data["comp_a"].id,
                owner_id=data["owner_a"].id,
                status=ProjectStatus.PLANNED,
                budget_amount=Decimal("1000.00"),
            )
            db.add(p)
            await db.flush()
            data["created"]["projects"].append(p.id)

            tx = Transaction(
                project_id=p.id,
                type="Expense",
                amount=Decimal("250.00"),
                mode="cash",
                reference=f"REF-TX-{uid}",
                created_by=data["admin_a"].id,
                created_at=datetime.utcnow(),
            )
            db.add(tx)
            await db.commit()
            pid = p.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/projects/{pid}", headers=headers)
            assert res.status_code == 409
            detail = res.json().get("detail", "")
            assert "financial transactions" in detail
            assert "Cannot delete project" in detail

        # Verify project is NOT deleted
        async with AsyncSessionLocal() as db:
            p_in_db = await db.scalar(select(Project).where(Project.id == pid))
            assert p_in_db is not None


@pytest.mark.asyncio
async def test_delete_project_with_vendor_bill_rejected_409():
    """Verify that a project with a linked VendorBill is rejected with HTTP 409 Conflict."""
    async with setup_project_delete_test_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        uid = data["uid"]

        async with AsyncSessionLocal() as db:
            p = Project(
                business_id=f"PRJ-VB-{uid}",
                project_name=f"Project VB {uid}",
                company_id=data["comp_a"].id,
                owner_id=data["owner_a"].id,
                status=ProjectStatus.PLANNED,
                budget_amount=Decimal("1000.00"),
            )
            db.add(p)
            await db.flush()
            data["created"]["projects"].append(p.id)

            sup = Supplier(
                company_id=data["comp_a"].id,
                supplier_name=f"Supplier VB {uid}",
                contact_person="Vendor Person",
                phone_email="9876543210",
            )
            db.add(sup)
            await db.flush()
            data["created"]["suppliers"].append(sup.id)

            vb = VendorBill(
                company_id=data["comp_a"].id,
                supplier_id=sup.id,
                project_id=p.id,
                bill_number=f"BILL-{uid}",
                bill_date=date.today(),
                due_date=date.today(),
                total_amount=Decimal("1500.00"),
                status=VendorBillStatus.PENDING.value,
            )
            db.add(vb)
            await db.commit()
            pid = p.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/projects/{pid}", headers=headers)
            assert res.status_code == 409
            detail = res.json().get("detail", "")
            assert "vendor bills" in detail


@pytest.mark.asyncio
async def test_delete_project_with_quotation_rejected_409():
    """Verify that a project linked to QuotationMaster is rejected with HTTP 409 Conflict."""
    async with setup_project_delete_test_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        uid = data["uid"]

        async with AsyncSessionLocal() as db:
            p = Project(
                business_id=f"PRJ-QM-{uid}",
                project_name=f"Project QM {uid}",
                company_id=data["comp_a"].id,
                owner_id=data["owner_a"].id,
                status=ProjectStatus.PLANNED,
                budget_amount=Decimal("5000.00"),
            )
            db.add(p)
            await db.flush()
            data["created"]["projects"].append(p.id)

            qm = QuotationMaster(
                quotation_no=f"QT-DEL-{uid}",
                company_id=data["comp_a"].id,
                client_name="Quotation Client",
                mobile_number="9876543210",
                project_id=p.id,
                project_name=p.project_name,
                project_type="Residential",
                subtotal=Decimal("5000.00"),
            )
            db.add(qm)
            await db.commit()
            pid = p.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/projects/{pid}", headers=headers)
            assert res.status_code == 409
            detail = res.json().get("detail", "")
            assert "originating quotations" in detail


@pytest.mark.asyncio
async def test_delete_project_with_labour_payroll_rejected_409():
    """Verify that a project with LabourPayroll records is rejected with HTTP 409 Conflict."""
    async with setup_project_delete_test_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        uid = data["uid"]

        async with AsyncSessionLocal() as db:
            p = Project(
                business_id=f"PRJ-LP-{uid}",
                project_name=f"Project LP {uid}",
                company_id=data["comp_a"].id,
                owner_id=data["owner_a"].id,
                status=ProjectStatus.PLANNED,
                budget_amount=Decimal("3000.00"),
            )
            db.add(p)
            await db.flush()
            data["created"]["projects"].append(p.id)

            labour = Labour(
                company_id=data["comp_a"].id,
                worker_code=f"W-{uid}",
                labour_name=f"Worker LP {uid}",
                mobile_number=f"93{uuid.uuid4().int % 100000000:08d}",
            )
            db.add(labour)
            await db.flush()
            data["created"]["labour"].append(labour.id)

            payroll = LabourPayroll(
                project_id=p.id,
                labour_id=labour.id,
                month=date.today().month,
                year=date.today().year,
                total_wage=Decimal("5000.00"),
                paid_amount=Decimal("0.00"),
                remaining_amount=Decimal("5000.00"),
                status=PayrollStatus.PENDING,
            )
            db.add(payroll)
            await db.commit()
            pid = p.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/projects/{pid}", headers=headers)
            assert res.status_code == 409
            detail = res.json().get("detail", "")
            assert "labour payroll records" in detail


@pytest.mark.asyncio
async def test_delete_project_with_checklist_rejected_409():
    """Verify that a project with Checklist records is rejected with HTTP 409 Conflict."""
    async with setup_project_delete_test_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        uid = data["uid"]

        async with AsyncSessionLocal() as db:
            p = Project(
                business_id=f"PRJ-CK-{uid}",
                project_name=f"Project CK {uid}",
                company_id=data["comp_a"].id,
                owner_id=data["owner_a"].id,
                status=ProjectStatus.PLANNED,
                budget_amount=Decimal("1000.00"),
            )
            db.add(p)
            await db.flush()
            data["created"]["projects"].append(p.id)

            chk = Checklist(
                project_id=p.id,
                name=f"Site Safety Inspection {uid}",
                type="Safety",
            )
            db.add(chk)
            await db.commit()
            pid = p.id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/projects/{pid}", headers=headers)
            assert res.status_code == 409
            detail = res.json().get("detail", "")
            assert "quality and safety records" in detail


@pytest.mark.asyncio
async def test_delete_project_cross_tenant_rejected():
    """Verify tenant isolation: Admin A cannot delete Company B's project (returns 404)."""
    async with setup_project_delete_test_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        proj_b_id = data["proj_b"].id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/projects/{proj_b_id}", headers=headers_a)
            assert res.status_code in (403, 404)

        # Confirm Company B's project still exists
        async with AsyncSessionLocal() as db:
            p_in_db = await db.scalar(select(Project).where(Project.id == proj_b_id))
            assert p_in_db is not None


@pytest.mark.asyncio
async def test_delete_project_nonexistent_returns_404():
    """Verify that deleting a non-existent project returns HTTP 404."""
    async with setup_project_delete_test_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete("/api/v1/projects/99999999", headers=headers_a)
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_unexpected_database_error_returns_500(monkeypatch):
    """
    Verify requirement 7: Unexpected database failures are NOT masked as 409,
    and bubble up to HTTP 500 Database error.
    """
    async with setup_project_delete_test_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        proj_id = data["proj_clean"].id

        from app.api.project import ProjectsRepository

        async def _mock_failing_delete(self, db, obj):
            raise SQLAlchemyError("Simulated unexpected database failure")

        monkeypatch.setattr(ProjectsRepository, "delete_project", _mock_failing_delete)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/projects/{proj_id}", headers=headers)
            assert res.status_code == 500
            assert res.json().get("detail") == "Database error"


@pytest.mark.asyncio
async def test_delete_project_foreign_key_race_condition_caught_as_409(monkeypatch):
    """
    Verify requirement 8: Race condition between dependency validation and deletion.
    If pre-validation passes but the database foreign key constraint fails (MySQL 1451),
    the IntegrityError is defensively caught and converted to HTTP 409 Conflict.
    """
    async with setup_project_delete_test_data() as data:
        token = data["tokens"]["admin_a"]
        headers = {"Authorization": f"Bearer {token}"}
        proj_id = data["proj_clean"].id

        from app.api.project import ProjectsRepository

        async def _mock_fk_violation(self, db, obj):
            raise IntegrityError(
                statement="DELETE FROM projects WHERE projects.id = %s",
                params=(proj_id,),
                orig=Exception("1451, 'Cannot delete or update a parent row: a foreign key constraint fails (`transactions`)'"),
            )

        monkeypatch.setattr(ProjectsRepository, "delete_project", _mock_fk_violation)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.delete(f"/api/v1/projects/{proj_id}", headers=headers)
            assert res.status_code == 409
            detail = res.json().get("detail", "")
            assert "associated records referencing it" in detail

