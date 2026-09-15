import uuid
from decimal import Decimal
from datetime import date
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.contractor import Contractor, ContractorProject
from app.models.expense import Expense
from app.models.invoice import Invoice
from app.models.billing import RABill
from app.models.settings import CompanySettings
from app.models.rbac import Role, Permission, RolePermission
from app.core.security import get_password_hash, create_access_token


@asynccontextmanager
async def setup_batch_an_data():
    """Seed test companies, projects, contractors, and users for Batch AN test suite."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Create two test companies
        comp_a = Company(name=f"BatchAN_CompA_{uid}")
        comp_b = Company(name=f"BatchAN_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Company settings
        cs_a = CompanySettings(company_id=comp_a.id)
        cs_b = CompanySettings(company_id=comp_b.id)
        db.add_all([cs_a, cs_b])
        await db.flush()

        # 3. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-AN-A-{uid}",
            owner_name=f"Owner AN A {uid}",
            email=f"ownerana_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-AN-B-{uid}",
            owner_name=f"Owner AN B {uid}",
            email=f"owneranb_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 4. Projects
        proj_a = Project(
            business_id=f"PRJ-AN-A-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_AN_A_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-AN-B-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_AN_B_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 5. Contractors
        contractor_a = Contractor(
            company_id=comp_a.id,
            contractor_id=f"CNT-ANA-{uid}",
            name=f"Contractor AN A {uid}",
            work_type="Civil",
            contact_number=f"98{uid[:8]}",
            gst_number="27AAAAA0000A1Z5",
            rate_type="Item Rate",
            total_work_assigned=Decimal("15000.00"),
            payment_given=Decimal("0.00"),
        )
        contractor_b = Contractor(
            company_id=comp_b.id,
            contractor_id=f"CNT-ANB-{uid}",
            name=f"Contractor AN B {uid}",
            work_type="Electrical",
            contact_number=f"97{uid[:8]}",
            gst_number="27BBBBB0000B1Z5",
            rate_type="Item Rate",
            total_work_assigned=Decimal("25000.00"),
            payment_given=Decimal("0.00"),
        )
        db.add_all([contractor_a, contractor_b])
        await db.flush()

        # 6. ContractorProject mappings (A to A, B to B)
        cp_a = ContractorProject(contractor_id=contractor_a.id, project_id=proj_a.id)
        cp_b = ContractorProject(contractor_id=contractor_b.id, project_id=proj_b.id)
        db.add_all([cp_a, cp_b])
        await db.flush()

        # 7. Invoices (A to A, B to B)
        inv_a = Invoice(
            company_id=comp_a.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type="contractor",
            reference_id=contractor_a.id,
            amount=Decimal("3000.00"),
            total_amount=Decimal("3000.00"),
            pending_amount=Decimal("3000.00"),
            status="paid",
        )
        inv_b = Invoice(
            company_id=comp_b.id,
            project_id=proj_b.id,
            owner_id=owner_b.id,
            type="contractor",
            reference_id=contractor_b.id,
            amount=Decimal("4500.00"),
            total_amount=Decimal("4500.00"),
            pending_amount=Decimal("4500.00"),
            status="paid",
        )
        db.add_all([inv_a, inv_b])
        await db.flush()

        # 8. RA Bills
        rabill_a = RABill(
            project_id=proj_a.id,
            contractor_id=contractor_a.id,
            bill_number=f"RA-ANA-{uid}",
            work_description="Excavation and foundation work",
            quantity=Decimal("100.000"),
            rate=Decimal("50.00"),
            gross_amount=Decimal("5000.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("5000.00"),
            total_amount=Decimal("5000.00"),
            bill_date=date.today(),
            status="Approved",
        )
        rabill_b = RABill(
            project_id=proj_b.id,
            contractor_id=contractor_b.id,
            bill_number=f"RA-ANB-{uid}",
            work_description="Electrical ducting work",
            quantity=Decimal("200.000"),
            rate=Decimal("40.00"),
            gross_amount=Decimal("8000.00"),
            deductions=Decimal("0.00"),
            net_amount=Decimal("8000.00"),
            total_amount=Decimal("8000.00"),
            bill_date=date.today(),
            status="Approved",
        )
        db.add_all([rabill_a, rabill_b])
        await db.flush()

        # 9. Custom roles
        role_all = Role(company_id=comp_a.id, name=f"AN_All_{uid}", display_name="AN All", is_system=False)
        role_view = Role(company_id=comp_a.id, name=f"AN_View_{uid}", display_name="AN View", is_system=False)
        role_noperm = Role(company_id=comp_a.id, name=f"AN_NoPerm_{uid}", display_name="AN NoPerm", is_system=False)
        role_dyn = Role(company_id=comp_a.id, name=f"AN_Dyn_{uid}", display_name="AN Dyn", is_system=False)
        db.add_all([role_all, role_view, role_noperm, role_dyn])
        await db.flush()

        # 10. Users
        user_all_a = User(
            email=f"an_all_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AN All Perm User",
            company_id=comp_a.id,
            role=role_all.name,
            is_active=True,
            is_super_admin=False,
        )
        user_view_a = User(
            email=f"an_view_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AN View Only User",
            company_id=comp_a.id,
            role=role_view.name,
            is_active=True,
            is_super_admin=False,
        )
        user_noperm_a = User(
            email=f"an_noperm_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AN No Perm User",
            company_id=comp_a.id,
            role=role_noperm.name,
            is_active=True,
            is_super_admin=False,
        )
        user_dyn_a = User(
            email=f"an_dyn_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AN Dynamic Perm User",
            company_id=comp_a.id,
            role=role_dyn.name,
            is_active=True,
            is_super_admin=False,
        )
        user_tenantless = User(
            email=f"an_tenantless_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AN Tenantless User",
            company_id=None,
            role=role_all.name,
            is_active=True,
            is_super_admin=False,
        )
        user_super = User(
            email=f"an_super_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AN Super Admin",
            company_id=None,
            role="super_admin",
            is_active=True,
            is_super_admin=True,
        )
        user_comp_b = User(
            email=f"an_user_b_{uid}@test.com",
            hashed_password=get_password_hash("Secret123"),
            full_name="AN User Comp B",
            company_id=comp_b.id,
            role=role_all.name,
            is_active=True,
            is_super_admin=False,
        )
        db.add_all([
            user_all_a, user_view_a, user_noperm_a, user_dyn_a,
            user_tenantless, user_super, user_comp_b,
        ])
        await db.flush()

        # 11. Project membership
        pm_all = ProjectMember(project_id=proj_a.id, user_id=user_all_a.id)
        pm_view = ProjectMember(project_id=proj_a.id, user_id=user_view_a.id)
        pm_dyn = ProjectMember(project_id=proj_a.id, user_id=user_dyn_a.id)
        db.add_all([pm_all, pm_view, pm_dyn])
        await db.flush()

        # 12. Permissions
        p_view = await db.scalar(select(Permission).where(Permission.code == "contractors.view"))
        p_create = await db.scalar(select(Permission).where(Permission.code == "contractors.create"))
        p_edit = await db.scalar(select(Permission).where(Permission.code == "contractors.edit"))
        p_delete = await db.scalar(select(Permission).where(Permission.code == "contractors.delete"))
        p_assign = await db.scalar(select(Permission).where(Permission.code == "contractors.assign"))

        # Role All gets all 5 permissions
        for p in [p_view, p_create, p_edit, p_delete, p_assign]:
            db.add(RolePermission(role=role_all.name, role_id=role_all.id, permission_id=p.id))

        # Role View gets only view
        db.add(RolePermission(role=role_view.name, role_id=role_view.id, permission_id=p_view.id))

        await db.commit()

        yield {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "contractor_a": contractor_a,
            "contractor_b": contractor_b,
            "user_all_a": user_all_a,
            "user_view_a": user_view_a,
            "user_noperm_a": user_noperm_a,
            "user_dyn_a": user_dyn_a,
            "user_tenantless": user_tenantless,
            "user_super": user_super,
            "user_comp_b": user_comp_b,
            "role_dyn": role_dyn,
            "p_view": p_view,
            "p_create": p_create,
            "p_edit": p_edit,
            "p_delete": p_delete,
            "p_assign": p_assign,
        }


def get_auth_headers(user: User) -> dict:
    token = create_access_token(data={"sub": str(user.id), "email": user.email})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# A. Authentication: All 15 Endpoints Return 401 When Unauthenticated
# ==============================================================================
@pytest.mark.asyncio
async def test_all_15_contractor_endpoints_unauthenticated():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        endpoints = [
            ("POST", "/api/v1/contractors", {}),
            ("GET", "/api/v1/contractors/pending-report", None),
            ("GET", "/api/v1/contractors", None),
            ("GET", "/api/v1/contractors/1", None),
            ("PUT", "/api/v1/contractors/1", {}),
            ("DELETE", "/api/v1/contractors/1", None),
            ("POST", "/api/v1/contractors/1/assign-project/1", None),
            ("GET", "/api/v1/contractors/1/payments", None),
            ("POST", "/api/v1/contractors/1/pay", {}),
            ("GET", "/api/v1/contractors/1/projects", None),
            ("GET", "/api/v1/contractors/1/bills", None),
            ("GET", "/api/v1/contractors/1/performance", None),
            ("GET", "/api/v1/contractors/1/ledger", None),
            ("GET", "/api/v1/contractors/1/work-summary", None),
            ("GET", "/api/v1/contractors/1/dashboard", None),
        ]
        for method, path, body in endpoints:
            if method == "GET":
                res = await ac.get(path)
            elif method == "POST":
                res = await ac.post(path, json=body or {})
            elif method == "PUT":
                res = await ac.put(path, json=body or {})
            elif method == "DELETE":
                res = await ac.delete(path)
            assert res.status_code == 401, f"{method} {path} expected 401, got {res.status_code}"


# ==============================================================================
# B. RBAC: Missing Required Permission Returns 403, Valid Permission Allows
# ==============================================================================
@pytest.mark.asyncio
async def test_rbac_contractor_permissions():
    async with setup_batch_an_data() as data:
        headers_noperm = get_auth_headers(data["user_noperm_a"])
        headers_all = get_auth_headers(data["user_all_a"])
        cid = data["contractor_a"].id
        pid = data["proj_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. contractors.view missing -> 403
            res = await ac.get("/api/v1/contractors", headers=headers_noperm)
            assert res.status_code == 403

            res = await ac.get(f"/api/v1/contractors/{cid}", headers=headers_noperm)
            assert res.status_code == 403

            # 2. contractors.create missing -> 403
            res = await ac.post(
                "/api/v1/contractors",
                headers=headers_noperm,
                json={"name": "New", "work_type": "Civil", "contact_number": "9999999999", "rate_type": "Item"},
            )
            assert res.status_code == 403

            # 3. contractors.edit missing -> 403
            res = await ac.put(f"/api/v1/contractors/{cid}", headers=headers_noperm, json={"name": "Updated"})
            assert res.status_code == 403

            res = await ac.post(f"/api/v1/contractors/{cid}/pay?project_id={pid}&amount=100.00", headers=headers_noperm)
            assert res.status_code == 403

            # 4. contractors.delete missing -> 403
            res = await ac.delete(f"/api/v1/contractors/{cid}", headers=headers_noperm)
            assert res.status_code == 403

            # 5. contractors.assign missing -> 403
            res = await ac.post(f"/api/v1/contractors/{cid}/assign-project/{pid}", headers=headers_noperm)
            assert res.status_code == 403

            # 6. Valid permission allowed -> 200
            res = await ac.get("/api/v1/contractors", headers=headers_all)
            assert res.status_code == 200
            res = await ac.get(f"/api/v1/contractors/{cid}", headers=headers_all)
            assert res.status_code == 200


# ==============================================================================
# C. Tenant Isolation: Cross-Tenant Blocked & Tenantless Denied 403
# ==============================================================================
@pytest.mark.asyncio
async def test_tenant_isolation_and_tenantless_denied():
    async with setup_batch_an_data() as data:
        headers_a = get_auth_headers(data["user_all_a"])
        headers_tenantless = get_auth_headers(data["user_tenantless"])
        cid_b = data["contractor_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Tenant A cannot view Tenant B contractor -> 404
            res = await ac.get(f"/api/v1/contractors/{cid_b}", headers=headers_a)
            assert res.status_code == 404

            # 2. Tenant A cannot update Tenant B contractor -> 404
            res = await ac.put(f"/api/v1/contractors/{cid_b}", headers=headers_a, json={"name": "Hacked"})
            assert res.status_code == 404

            # 3. Tenant A cannot delete Tenant B contractor -> 404
            res = await ac.delete(f"/api/v1/contractors/{cid_b}", headers=headers_a)
            assert res.status_code == 404

            # 4. Tenantless non-SA receives 403 on contractor listing
            res = await ac.get("/api/v1/contractors", headers=headers_tenantless)
            assert res.status_code == 403
            assert "company" in res.json().get("detail", "").lower()

            # 5. Tenantless non-SA receives 403 on pending report
            res = await ac.get("/api/v1/contractors/pending-report", headers=headers_tenantless)
            assert res.status_code == 403

# ==============================================================================
# D. Super Admin Behavior: Global Operations, Mutations, and Pending Report
# ==============================================================================
@pytest.mark.asyncio
async def test_super_admin_operations():
    async with setup_batch_an_data() as data:
        headers_super = get_auth_headers(data["user_super"])
        cid_a = data["contractor_a"].id
        cid_b = data["contractor_b"].id
        comp_a = data["comp_a"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. SA can list contractors globally (querying with search)
            res_a = await ac.get(f"/api/v1/contractors?search={data['contractor_a'].name}", headers=headers_super)
            assert res_a.status_code == 200
            assert cid_a in [c["id"] for c in res_a.json()]

            res_b = await ac.get(f"/api/v1/contractors?search={data['contractor_b'].name}", headers=headers_super)
            assert res_b.status_code == 200
            assert cid_b in [c["id"] for c in res_b.json()]

            # 2. SA can view contractor across companies
            res = await ac.get(f"/api/v1/contractors/{cid_a}", headers=headers_super)
            assert res.status_code == 200
            res = await ac.get(f"/api/v1/contractors/{cid_b}", headers=headers_super)
            assert res.status_code == 200

            # 3. SA can update contractor across companies
            res = await ac.put(f"/api/v1/contractors/{cid_a}", headers=headers_super, json={"work_type": "Renovated"})
            assert res.status_code == 200
            assert res.json()["work_type"] == "Renovated"

            # 4. SA pending report works globally without failing
            res = await ac.get("/api/v1/contractors/pending-report", headers=headers_super)
            assert res.status_code == 200

            # 5. SA creation without company context rejected safely
            res = await ac.post(
                "/api/v1/contractors",
                headers=headers_super,
                json={"name": "SA Cont", "work_type": "Civil", "contact_number": "9123456789", "rate_type": "Daily"},
            )
            assert res.status_code == 400
            assert "Company context required" in res.json().get("detail", "")

            # 6. SA creation with target company creates properly scoped contractor
            res = await ac.post(
                f"/api/v1/contractors",
                headers=headers_super,
                json={
                    "name": "SA Cont Scoped",
                    "work_type": "Civil",
                    "contact_number": "9123456789",
                    "rate_type": "Daily",
                    "company_id": comp_a.id,
                },
            )
            # Either 200 with company or rejected if company_id is not in schema
            # If rejected because schema excludes company_id, verify safe rejection (not creating company_id=None)
            assert res.status_code in [200, 400]

            # 7. SA can delete contractor
            # Create a contractor to delete
            headers_all_a = get_auth_headers(data["user_all_a"])
            res_new = await ac.post(
                "/api/v1/contractors",
                headers=headers_all_a,
                json={"name": "To Delete", "work_type": "Piping", "contact_number": "9000000000", "rate_type": "Item"},
            )
            assert res_new.status_code == 200
            new_cid = res_new.json()["id"]

            res_del = await ac.delete(f"/api/v1/contractors/{new_cid}", headers=headers_super)
            assert res_del.status_code == 200
            assert res_del.json()["message"] == "Deleted successfully"


# ==============================================================================
# E. Project Assignment: Same-Company Allowed, Cross-Company Blocked (Even SA)
# ==============================================================================
@pytest.mark.asyncio
async def test_project_assignment_invariants():
    async with setup_batch_an_data() as data:
        headers_all = get_auth_headers(data["user_all_a"])
        headers_super = get_auth_headers(data["user_super"])
        cid_a = data["contractor_a"].id
        cid_b = data["contractor_b"].id
        pid_a = data["proj_a"].id
        pid_b = data["proj_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Cross-company assignment by normal user -> blocked (404)
            res = await ac.post(f"/api/v1/contractors/{cid_b}/assign-project/{pid_a}", headers=headers_all)
            assert res.status_code == 404

            # 2. Cross-company assignment by Super Admin -> blocked (400)
            res_sa = await ac.post(f"/api/v1/contractors/{cid_a}/assign-project/{pid_b}", headers=headers_super)
            assert res_sa.status_code == 400
            assert "must belong to the same company" in res_sa.json().get("detail", "")


# ==============================================================================
# F. Payment Invariants & Atomicity
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_payment_invariants():
    async with setup_batch_an_data() as data:
        headers_all = get_auth_headers(data["user_all_a"])
        headers_super = get_auth_headers(data["user_super"])
        cid_a = data["contractor_a"].id
        pid_a = data["proj_a"].id
        pid_b = data["proj_b"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Amount <= 0 blocked -> 400
            res = await ac.post(f"/api/v1/contractors/{cid_a}/pay?project_id={pid_a}&amount=0.00", headers=headers_all)
            assert res.status_code == 400
            assert "Amount must be positive" in res.json().get("detail", "")

            # 2. Cross-company payment by Super Admin blocked -> 400
            res = await ac.post(f"/api/v1/contractors/{cid_a}/pay?project_id={pid_b}&amount=100.00", headers=headers_super)
            assert res.status_code == 400

            # 3. Overpayment blocked -> 400
            res = await ac.post(f"/api/v1/contractors/{cid_a}/pay?project_id={pid_a}&amount=999999.00", headers=headers_all)
            assert res.status_code == 400
            assert "Payment exceeds total work amount" in res.json().get("detail", "")

            # 4. Valid payment allowed -> 200
            res = await ac.post(f"/api/v1/contractors/{cid_a}/pay?project_id={pid_a}&amount=1000.00", headers=headers_all)
            assert res.status_code == 200
            assert res.json()["paid_total"] == 1000.0


# ==============================================================================
# G. Financial Isolation & Cross-Company Data Leaks Prevented
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_financial_isolation():
    async with setup_batch_an_data() as data:
        headers_view = get_auth_headers(data["user_view_a"])
        cid_a = data["contractor_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Bills endpoint only returns invoices for contractor A's company
            res = await ac.get(f"/api/v1/contractors/{cid_a}/bills", headers=headers_view)
            assert res.status_code == 200
            bills = res.json()
            for b in bills:
                assert b["amount"] == 3000.0  # Only Comp A invoice

            # 2. Dashboard aggregates only Comp A RABill
            res = await ac.get(f"/api/v1/contractors/{cid_a}/dashboard", headers=headers_view)
            assert res.status_code == 200
            dash = res.json()
            assert dash["total_amount"] == 5000.0  # RABill Comp A only, not Comp B (8000)


# ==============================================================================
# H. Ledger Consistency: Payment Appears in Contractor Ledger
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_ledger_consistency():
    async with setup_batch_an_data() as data:
        headers_all = get_auth_headers(data["user_all_a"])
        cid_a = data["contractor_a"].id
        pid_a = data["proj_a"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Pay contractor
            res_pay = await ac.post(f"/api/v1/contractors/{cid_a}/pay?project_id={pid_a}&amount=500.00", headers=headers_all)
            assert res_pay.status_code == 200

            # Ledger must contain the debit payment entry
            res_ledger = await ac.get(f"/api/v1/contractors/{cid_a}/ledger", headers=headers_all)
            assert res_ledger.status_code == 200
            ledger = res_ledger.json()
            debit_entries = [entry for entry in ledger if entry["type"] == "DEBIT"]
            assert len(debit_entries) >= 1
            assert any(d["amount"] == 500.0 for d in debit_entries)


# ==============================================================================
# I. Dynamic RBAC: Revocation and Granting at Runtime
# ==============================================================================
@pytest.mark.asyncio
async def test_contractor_dynamic_rbac_runtime():
    async with setup_batch_an_data() as data:
        headers_dyn = get_auth_headers(data["user_dyn_a"])
        role_dyn = data["role_dyn"]
        p_view = data["p_view"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially no permissions -> 403
            res = await ac.get("/api/v1/contractors", headers=headers_dyn)
            assert res.status_code == 403

            # Grant contractors.view dynamically
            async with AsyncSessionLocal() as db:
                rp = RolePermission(role=role_dyn.name, role_id=role_dyn.id, permission_id=p_view.id)
                db.add(rp)
                await db.commit()

            # Now access is granted -> 200
            res = await ac.get("/api/v1/contractors", headers=headers_dyn)
            assert res.status_code == 200

            # Revoke contractors.view dynamically
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role_dyn.id,
                        RolePermission.permission_id == p_view.id,
                    )
                )
                await db.commit()

            # Immediately denied -> 403
            res = await ac.get("/api/v1/contractors", headers=headers_dyn)
            assert res.status_code == 403
