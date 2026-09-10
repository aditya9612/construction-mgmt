import uuid
from decimal import Decimal
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, update

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, ActivityLog
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.contractor import Contractor
from app.models.labour import Labour
from app.models.material import Material, Supplier
from app.models.master_data import MaterialMaster, Unit
from app.models.equipment import Equipment
from app.models.settings import CompanySettings
from app.models.billing import RABill
from app.models.work_order import WorkOrder
from app.models.notification import Notification
from app.models.quotation import (
    QuotationMaster,
    QuotationItem,
    MeasurementDetail,
    QuotationLabour,
    QuotationMaterial,
    QuotationExtraCharge,
    QuotationStatus,
)
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import ProjectStatus, SkillType, RateType, LabourStatus


@asynccontextmanager
async def setup_batch_k_data():
    """Seed test companies, projects, contractors, labour, materials, equipment, quotations, and users for Batch K."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchK_CompA_{uid}")
        comp_b = Company(name=f"BatchK_CompB_{uid}")
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
            email=f"superadmin_k_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin K",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_ka_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin K",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_kb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin K",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"QuotationOfficer_{uid}"
        user_custom_a = User(
            email=f"custom_ka_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Quotation Officer K",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        user_legacy_a = User(
            email=f"legacy_ka_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy PM K",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Project Manager",
        )

        user_unassigned_a = User(
            email=f"unassigned_ka_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Unassigned Officer K",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        client_user_a = User(
            email=f"client_ka_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Client User A K",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Client",
        )

        client_user_b = User(
            email=f"client_kb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Client User B K",
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
            display_name="Custom Role K",
            company_id=comp_a.id,
            description="Custom Quotation Role K",
        )
        db.add(role_custom)
        await db.flush()

        # 5. Owners and Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-KA-{uid}",
            owner_name=f"Owner KA {uid}",
            email=f"ownerka_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-KB-{uid}",
            owner_name=f"Owner KB {uid}",
            email=f"ownerkb_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a = Project(
            business_id=f"PRJ-KA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_KA_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-KB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_KB_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 6. Contractors
        contractor_a = Contractor(
            company_id=comp_a.id,
            contractor_id=f"CON-KA-{uid}",
            name=f"Contractor KA {uid}",
            work_type="Civil",
            contact_number=f"96{uuid.uuid4().int % 100000000:08d}",
            rate_type="Fixed",
        )
        contractor_b = Contractor(
            company_id=comp_b.id,
            contractor_id=f"CON-KB-{uid}",
            name=f"Contractor KB {uid}",
            work_type="Civil",
            contact_number=f"95{uuid.uuid4().int % 100000000:08d}",
            rate_type="Fixed",
        )
        db.add_all([contractor_a, contractor_b])
        await db.flush()

        # 7. Labour workers
        labour_a = Labour(
            company_id=comp_a.id,
            worker_code=f"LAB-KA-{uid}",
            labour_name=f"Worker KA {uid}",
            status=LabourStatus.ACTIVE,
        )
        labour_b = Labour(
            company_id=comp_b.id,
            worker_code=f"LAB-KB-{uid}",
            labour_name=f"Worker KB {uid}",
            status=LabourStatus.ACTIVE,
        )
        db.add_all([labour_a, labour_b])
        await db.flush()

        # 8. Equipment
        eq_a = Equipment(
            project_id=proj_a.id,
            equipment_name=f"Excavator KA {uid}",
            equipment_code=f"EQ-KA-{uid}",
            rental_cost=Decimal("1500.00"),
        )
        eq_b = Equipment(
            project_id=proj_b.id,
            equipment_name=f"Excavator KB {uid}",
            equipment_code=f"EQ-KB-{uid}",
            rental_cost=Decimal("1600.00"),
        )
        db.add_all([eq_a, eq_b])
        await db.flush()

        # 9. Unit, Supplier, Material Master & Material
        unit_res = await db.execute(select(Unit).limit(1))
        unit = unit_res.scalar_one_or_none()
        if not unit:
            unit = Unit(name=f"Bags_{uid}")
            db.add(unit)
            await db.flush()

        sup_a = Supplier(
            supplier_name=f"Supplier KA {uid}",
            contact_person="Person A",
            phone_email=f"98{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_a.id,
        )
        sup_b = Supplier(
            supplier_name=f"Supplier KB {uid}",
            contact_person="Person B",
            phone_email=f"97{uuid.uuid4().int % 100000000:08d}",
            company_id=comp_b.id,
        )
        db.add_all([sup_a, sup_b])
        await db.flush()

        mm_a = MaterialMaster(
            name=f"Cement KA {uid}",
            unit_id=unit.id,
            company_id=comp_a.id,
        )
        mm_b = MaterialMaster(
            name=f"Cement KB {uid}",
            unit_id=unit.id,
            company_id=comp_b.id,
        )
        db.add_all([mm_a, mm_b])
        await db.flush()

        mat_a = Material(
            material_code=f"M-KA-{uid}",
            project_id=proj_a.id,
            material_master_id=mm_a.id,
            material_name=f"Cement KA {uid}",
            category="Structural",
            unit_id=unit.id,
            supplier_id=sup_a.id,
            rate_type=RateType.FIXED,
            purchase_rate=Decimal("350.00"),
        )
        mat_b = Material(
            material_code=f"M-KB-{uid}",
            project_id=proj_b.id,
            material_master_id=mm_b.id,
            material_name=f"Cement KB {uid}",
            category="Structural",
            unit_id=unit.id,
            supplier_id=sup_b.id,
            rate_type=RateType.FIXED,
            purchase_rate=Decimal("360.00"),
        )
        db.add_all([mat_a, mat_b])
        await db.flush()

        # 10. Quotations
        # Quotation A1: DRAFT with items, measurements, labour, material, extra charge
        qtn_a1 = QuotationMaster(
            quotation_no=f"QT/2026/A1-{uid}",
            company_id=comp_a.id,
            client_user_id=client_user_a.id,
            client_name="Client User A",
            mobile_number="9876543210",
            project_name=f"Quotation Project A1 {uid}",
            project_type="Residential",
            status=QuotationStatus.DRAFT,
            is_approved=False,
            grand_total=150000.0,
            subtotal=150000.0,
        )
        # Quotation A2: SENT (ready for approve/reject)
        qtn_a2 = QuotationMaster(
            quotation_no=f"QT/2026/A2-{uid}",
            company_id=comp_a.id,
            client_user_id=client_user_a.id,
            client_name="Client User A",
            mobile_number="9876543210",
            project_name=f"Quotation Project A2 {uid}",
            project_type="Commercial",
            status=QuotationStatus.SENT,
            is_approved=False,
            grand_total=250000.0,
            subtotal=250000.0,
        )
        # Quotation A3: APPROVED (ready for conversion to bill, work order, project)
        qtn_a3 = QuotationMaster(
            quotation_no=f"QT/2026/A3-{uid}",
            company_id=comp_a.id,
            client_user_id=client_user_a.id,
            client_name="Client User A",
            mobile_number="9876543210",
            project_name=f"Quotation Project A3 {uid}",
            project_type="Infrastructure",
            status=QuotationStatus.APPROVED,
            is_approved=True,
            approved_at=datetime.utcnow(),
            grand_total=500000.0,
            subtotal=500000.0,
        )

        # Quotation B1: DRAFT for company B
        qtn_b1 = QuotationMaster(
            quotation_no=f"QT/2026/B1-{uid}",
            company_id=comp_b.id,
            client_user_id=client_user_b.id,
            client_name="Client User B",
            mobile_number="9876543211",
            project_name=f"Quotation Project B1 {uid}",
            project_type="Residential",
            status=QuotationStatus.DRAFT,
            is_approved=False,
            grand_total=120000.0,
            subtotal=120000.0,
        )

        db.add_all([qtn_a1, qtn_a2, qtn_a3, qtn_b1])
        await db.flush()

        # 11. Sub-resources on Quotation A1
        item_a1 = QuotationItem(
            quotation_id=qtn_a1.id,
            item_type="soling",
            title="Stone Soling",
            unit="brass",
            rate=1200.0,
            quantity=10.0,
            amount=12000.0,
        )
        item_b1 = QuotationItem(
            quotation_id=qtn_b1.id,
            item_type="soling",
            title="Foreign Stone Soling",
            unit="brass",
            rate=1300.0,
            quantity=10.0,
            amount=13000.0,
        )
        db.add_all([item_a1, item_b1])
        await db.flush()

        meas_a1 = MeasurementDetail(
            quotation_item_id=item_a1.id,
            length=10.0,
            width=10.0,
            height=1.0,
            quantity=1.0,
        )
        db.add(meas_a1)

        labour_item_a1 = QuotationLabour(
            quotation_id=qtn_a1.id,
            labour_id=labour_a.id,
            skill_type="Skilled",
            labour_count=2,
            daily_wage=800.0,
            labour_days=5,
            amount=8000.0,
        )
        labour_item_b1 = QuotationLabour(
            quotation_id=qtn_b1.id,
            labour_id=labour_b.id,
            skill_type="Skilled",
            labour_count=2,
            daily_wage=850.0,
            labour_days=5,
            amount=8500.0,
        )
        db.add_all([labour_item_a1, labour_item_b1])

        mat_item_a1 = QuotationMaterial(
            quotation_id=qtn_a1.id,
            material_id=mat_a.id,
            material_name="Cement Bags",
            unit="Bags",
            estimated_quantity=50.0,
            estimated_rate=350.0,
            estimated_amount=17500.0,
        )
        mat_item_b1 = QuotationMaterial(
            quotation_id=qtn_b1.id,
            material_id=mat_b.id,
            material_name="Foreign Cement Bags",
            unit="Bags",
            estimated_quantity=50.0,
            estimated_rate=360.0,
            estimated_amount=18000.0,
        )
        db.add_all([mat_item_a1, mat_item_b1])

        extra_item_a1 = QuotationExtraCharge(
            quotation_id=qtn_a1.id,
            equipment_id=eq_a.id,
            expense_type="Equipment Rental",
            description="Excavator charges",
            quantity=2.0,
            rate=1500.0,
            amount=3000.0,
        )
        extra_item_b1 = QuotationExtraCharge(
            quotation_id=qtn_b1.id,
            equipment_id=eq_b.id,
            expense_type="Equipment Rental",
            description="Foreign Excavator charges",
            quantity=2.0,
            rate=1600.0,
            amount=3200.0,
        )
        db.add_all([extra_item_a1, extra_item_b1])
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
            "contractor_a": contractor_a,
            "contractor_b": contractor_b,
            "labour_a": labour_a,
            "labour_b": labour_b,
            "mat_a": mat_a,
            "mat_b": mat_b,
            "eq_a": eq_a,
            "eq_b": eq_b,
            "qtn_a1": qtn_a1,
            "qtn_a2": qtn_a2,
            "qtn_a3": qtn_a3,
            "qtn_b1": qtn_b1,
            "item_a1": item_a1,
            "item_b1": item_b1,
            "labour_item_a1": labour_item_a1,
            "labour_item_b1": labour_item_b1,
            "mat_item_a1": mat_item_a1,
            "mat_item_b1": mat_item_b1,
            "extra_item_a1": extra_item_a1,
            "extra_item_b1": extra_item_b1,
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
            await clean_db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_([
                admin_a.id, admin_b.id, user_custom_a.id, user_legacy_a.id,
                user_unassigned_a.id, super_admin.id, client_user_a.id, client_user_b.id
            ])))
            # Break circular reference from quotation_master to projects
            await clean_db.execute(update(QuotationMaster).where(QuotationMaster.company_id.in_([comp_a.id, comp_b.id])).values(project_id=None))
            # Delete bills, work orders
            await clean_db.execute(delete(RABill).where(RABill.quotation_id.in_([qtn_a1.id, qtn_a2.id, qtn_a3.id, qtn_b1.id])))
            await clean_db.execute(delete(WorkOrder).where(WorkOrder.quotation_id.in_([qtn_a1.id, qtn_a2.id, qtn_a3.id, qtn_b1.id])))
            # Delete quotation sub-resources
            await clean_db.execute(delete(MeasurementDetail))
            await clean_db.execute(delete(QuotationItem))
            await clean_db.execute(delete(QuotationLabour))
            await clean_db.execute(delete(QuotationMaterial))
            await clean_db.execute(delete(QuotationExtraCharge))
            # Delete ALL quotation master rows for comp_a and comp_b
            await clean_db.execute(delete(QuotationMaster).where(QuotationMaster.company_id.in_([comp_a.id, comp_b.id])))
            # Delete materials & master
            await clean_db.execute(delete(Material).where(Material.id.in_([mat_a.id, mat_b.id])))
            await clean_db.execute(delete(MaterialMaster).where(MaterialMaster.id.in_([mm_a.id, mm_b.id])))
            await clean_db.execute(delete(Supplier).where(Supplier.id.in_([sup_a.id, sup_b.id])))
            await clean_db.execute(delete(Equipment).where(Equipment.id.in_([eq_a.id, eq_b.id])))
            await clean_db.execute(delete(Labour).where(Labour.id.in_([labour_a.id, labour_b.id])))
            await clean_db.execute(delete(Contractor).where(Contractor.id.in_([contractor_a.id, contractor_b.id])))
            # Delete ALL projects for comp_a and comp_b (including converted ones)
            await clean_db.execute(delete(Project).where(Project.company_id.in_([comp_a.id, comp_b.id])))
            # Delete owners
            await clean_db.execute(delete(Owner).where(Owner.company_id.in_([comp_a.id, comp_b.id])))
            # Delete RBAC overrides & roles
            await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([
                user_custom_a.id, user_legacy_a.id, user_unassigned_a.id, client_user_a.id
            ])))
            await clean_db.execute(delete(RolePermission).where(RolePermission.role_id == role_custom.id))
            await clean_db.execute(delete(Role).where(Role.id == role_custom.id))
            # Delete users
            await clean_db.execute(delete(User).where(User.company_id.in_([comp_a.id, comp_b.id])))
            await clean_db.execute(delete(User).where(User.id == super_admin.id))
            # Delete company settings and companies
            await clean_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
            await clean_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await clean_db.commit()


# ==============================================================================
# TEST 1 — ALL 27 UNAUTHENTICATED ROUTES RETURN 401
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_unauthenticated_all_27_routes_401():
    """Verify that all 27 active routes in Batch K return HTTP 401 when accessed without credentials."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            q_id = d_data["qtn_a1"].id
            item_id = d_data["item_a1"].id
            labour_id = d_data["labour_item_a1"].id
            mat_id = d_data["mat_item_a1"].id
            extra_id = d_data["extra_item_a1"].id

            routes = [
                ("POST", "/api/v1/quotations/", {"json": {}}),
                ("GET", "/api/v1/quotations/", {}),
                ("GET", f"/api/v1/quotations/{q_id}", {}),
                ("PUT", f"/api/v1/quotations/{q_id}", {"json": {}}),
                ("DELETE", f"/api/v1/quotations/{q_id}", {}),
                ("POST", f"/api/v1/quotations/{q_id}/items", {"json": {}}),
                ("PUT", f"/api/v1/quotations/quotation-items/{item_id}", {"json": {}}),
                ("DELETE", f"/api/v1/quotations/quotation-items/{item_id}", {}),
                ("GET", f"/api/v1/quotations/{q_id}/preview", {}),
                ("PUT", f"/api/v1/quotations/{q_id}/approve", {}),
                ("PUT", f"/api/v1/quotations/{q_id}/reject", {"json": {"reason": "Test"}}),
                ("POST", f"/api/v1/quotations/{q_id}/convert-to-bill?project_id=1&contractor_id=1", {}),
                ("POST", f"/api/v1/quotations/{q_id}/convert-to-work-order?project_id=1&contractor_id=1", {}),
                ("POST", f"/api/v1/quotations/{q_id}/labour", {"json": {}}),
                ("PUT", f"/api/v1/quotations/labour/{labour_id}", {"json": {}}),
                ("DELETE", f"/api/v1/quotations/labour/{labour_id}", {}),
                ("POST", f"/api/v1/quotations/{q_id}/materials", {"json": {}}),
                ("PUT", f"/api/v1/quotations/quotation-materials/{mat_id}", {"json": {}}),
                ("DELETE", f"/api/v1/quotations/quotation-materials/{mat_id}", {}),
                ("GET", f"/api/v1/quotations/{q_id}/materials", {}),
                ("POST", f"/api/v1/quotations/{q_id}/extra-charges", {"json": {}}),
                ("PUT", f"/api/v1/quotations/quotation-extra-charges/{extra_id}", {"json": {}}),
                ("DELETE", f"/api/v1/quotations/quotation-extra-charges/{extra_id}", {}),
                ("GET", f"/api/v1/quotations/{q_id}/extra-charges", {}),
                ("GET", f"/api/v1/quotations/{q_id}/pdf", {}),
                ("POST", f"/api/v1/quotations/{q_id}/convert-to-project", {"json": {}}),
                ("POST", f"/api/v1/quotations/{q_id}/send", {}),
            ]

            assert len(routes) == 27, f"Expected 27 routes, found {len(routes)}"

            for method, path, kwargs in routes:
                resp = await ac.request(method, path, **kwargs)
                assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"


# ==============================================================================
# TEST 2 — AUTHENTICATED USER WITHOUT PERMISSION IS FORBIDDEN (403)
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_authenticated_without_permission_forbidden_403():
    """Verify that an authenticated user without the required permission gets 403 on all action types."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["unassigned_a"]
            headers = {"Authorization": f"Bearer {token}"}
            q_id = d_data["qtn_a1"].id
            item_id = d_data["item_a1"].id

            # Representative endpoints for each permission
            test_routes = [
                ("GET", "/api/v1/quotations/"),  # quotations.view
                ("GET", f"/api/v1/quotations/{q_id}"),  # quotations.view
                ("POST", "/api/v1/quotations/"),  # quotations.create
                ("PUT", f"/api/v1/quotations/{q_id}"),  # quotations.edit
                ("DELETE", f"/api/v1/quotations/{q_id}"),  # quotations.delete
                ("POST", f"/api/v1/quotations/{q_id}/items"),  # quotations.edit
                ("PUT", f"/api/v1/quotations/quotation-items/{item_id}"),  # quotations.edit
                ("DELETE", f"/api/v1/quotations/quotation-items/{item_id}"),  # quotations.edit
                ("PUT", f"/api/v1/quotations/{q_id}/approve"),  # quotations.approve
                ("PUT", f"/api/v1/quotations/{q_id}/reject"),  # quotations.approve
                ("POST", f"/api/v1/quotations/{q_id}/convert-to-bill?project_id=1&contractor_id=1"),  # quotations.manage
                ("POST", f"/api/v1/quotations/{q_id}/convert-to-work-order?project_id=1&contractor_id=1"),  # quotations.manage
                ("POST", f"/api/v1/quotations/{q_id}/convert-to-project"),  # quotations.manage
                ("GET", f"/api/v1/quotations/{q_id}/pdf"),  # quotations.export
                ("POST", f"/api/v1/quotations/{q_id}/send"),  # quotations.assign
            ]

            for method, path in test_routes:
                resp = await ac.request(method, path, headers=headers)
                assert resp.status_code == 403, f"{method} {path} returned {resp.status_code}, expected 403"


# ==============================================================================
# TEST 3 — PERMISSION GRANTED VIA ROLE SUCCEEDS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_permission_granted_via_role_succeeds():
    """Verify that granting a permission to a custom role allows access."""
    async with setup_batch_k_data() as d_data:
        # Grant quotations.view to custom role
        async with AsyncSessionLocal() as db:
            perm = await db.scalar(select(Permission).where(Permission.code == "quotations.view"))
            db.add(RolePermission(role_id=d_data["role_custom"].id, permission_id=perm.id, role=d_data["role_custom"].name))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            q_id = d_data["qtn_a1"].id

            # Viewing list and details should now succeed
            resp_list = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp_list.status_code == 200

            resp_detail = await ac.get(f"/api/v1/quotations/{q_id}", headers=headers)
            assert resp_detail.status_code == 200
            assert resp_detail.json()["id"] == q_id

            # But mutations without quotations.edit should still fail with 403
            resp_put = await ac.put(f"/api/v1/quotations/{q_id}", json={"project_name": "New Name"}, headers=headers)
            assert resp_put.status_code == 403


# ==============================================================================
# TEST 4 — DYNAMIC REVOCATION AND RE-GRANT WITHOUT SERVER RESTART
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_dynamic_revocation_and_regrant():
    """Verify that revoking and regranting DB permissions dynamically changes access without restart."""
    async with setup_batch_k_data() as d_data:
        async with AsyncSessionLocal() as db:
            perm = await db.scalar(select(Permission).where(Permission.code == "quotations.view"))
            rp = RolePermission(role_id=d_data["role_custom"].id, permission_id=perm.id, role=d_data["role_custom"].name)
            db.add(rp)
            await db.commit()
            rp_id = rp.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}

            # 1. Granted -> 200
            resp = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp.status_code == 200

            # 2. Revoke in DB
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(RolePermission.id == rp_id))
                await db.commit()

            # Revoked -> 403
            resp_revoked = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp_revoked.status_code == 403

            # 3. Re-grant in DB
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role_id=d_data["role_custom"].id, permission_id=perm.id, role=d_data["role_custom"].name))
                await db.commit()

            # Re-granted -> 200
            resp_regranted = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp_regranted.status_code == 200


# ==============================================================================
# TEST 5 — LEGACY ROLE ALONE FAILS (NO HARDCODED ROLE BYPASS)
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_legacy_role_alone_fails():
    """Verify that having a legacy role name (e.g. 'Project Manager') alone does NOT grant access without DB permission."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["legacy_a"]
            headers = {"Authorization": f"Bearer {token}"}
            q_id = d_data["qtn_a1"].id

            # Project Manager has no quotation permissions seeded -> 403
            resp = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp.status_code == 403

            resp_detail = await ac.get(f"/api/v1/quotations/{q_id}", headers=headers)
            assert resp_detail.status_code == 403


# ==============================================================================
# TEST 6 — USER PERMISSION OVERRIDE (GRANT & REVOKE)
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_user_permission_overrides():
    """Verify that UserPermissionOverride can explicitly grant or revoke individual permissions."""
    async with setup_batch_k_data() as d_data:
        user_id = d_data["user_unassigned_a"].id
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["unassigned_a"]
            headers = {"Authorization": f"Bearer {token}"}

            # 1. Unassigned user has no role permissions -> 403
            resp = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp.status_code == 403

            # 2. Add explicit grant override
            async with AsyncSessionLocal() as db:
                perm = await db.scalar(select(Permission).where(Permission.code == "quotations.view"))
                db.add(UserPermissionOverride(user_id=user_id, permission_id=perm.id, is_granted=True))
                await db.commit()

            # Now succeeds -> 200
            resp_override = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp_override.status_code == 200

            # 3. Change override to revoke (is_granted=False)
            async with AsyncSessionLocal() as db:
                override = await db.scalar(select(UserPermissionOverride).where(UserPermissionOverride.user_id == user_id))
                override.is_granted = False
                await db.commit()

            # Now forbidden -> 403
            resp_revoked_override = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp_revoked_override.status_code == 403


# ==============================================================================
# TEST 7 — WILDCARD PERMISSIONS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_wildcard_permission():
    """Verify that granting a module wildcard or global wildcard satisfies permissions."""
    async with setup_batch_k_data() as d_data:
        async with AsyncSessionLocal() as db:
            # Grant quotations.* or *
            perm_all = await db.scalar(select(Permission).where(Permission.code == "*"))
            if not perm_all:
                perm_all = Permission(module="all", action="*", code="*", description="Global wildcard")
                db.add(perm_all)
                await db.flush()
            db.add(RolePermission(role_id=d_data["role_custom"].id, permission_id=perm_all.id, role=d_data["role_custom"].name))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["custom_a"]
            headers = {"Authorization": f"Bearer {token}"}
            q_id = d_data["qtn_a1"].id

            # Wildcard user can view, list, etc.
            resp = await ac.get("/api/v1/quotations/", headers=headers)
            assert resp.status_code == 200

            resp_detail = await ac.get(f"/api/v1/quotations/{q_id}", headers=headers)
            assert resp_detail.status_code == 200


# ==============================================================================
# TEST 8 — TENANT ISOLATION: FOREIGN QUOTATION MASKED WITH 404
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_foreign_quotation_masked_404():
    """Verify that attempting to access a foreign quotation returns 404 (not 403) to prevent existence leakage."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            foreign_q_id = d_data["qtn_b1"].id

            # Admin A trying to access Tenant B's quotation
            resp_get = await ac.get(f"/api/v1/quotations/{foreign_q_id}", headers=headers_a)
            assert resp_get.status_code == 404, f"Expected 404, got {resp_get.status_code}"

            resp_put = await ac.put(f"/api/v1/quotations/{foreign_q_id}", json={"project_name": "Hacked"}, headers=headers_a)
            assert resp_put.status_code == 404, f"Expected 404, got {resp_put.status_code}"

            resp_del = await ac.delete(f"/api/v1/quotations/{foreign_q_id}", headers=headers_a)
            assert resp_del.status_code == 404, f"Expected 404, got {resp_del.status_code}"


# ==============================================================================
# TEST 9 — TENANT ISOLATION: FOREIGN SUB-RESOURCES MASKED WITH 404
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_foreign_subresources_masked_404():
    """Verify that attempting to mutate a foreign quotation sub-resource returns 404."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}

            foreign_item_id = d_data["item_b1"].id
            foreign_labour_id = d_data["labour_item_b1"].id
            foreign_mat_id = d_data["mat_item_b1"].id
            foreign_extra_id = d_data["extra_item_b1"].id

            # Update/delete foreign quotation item -> 404
            resp = await ac.put(f"/api/v1/quotations/quotation-items/{foreign_item_id}", json={"rate": 999.0}, headers=headers_a)
            assert resp.status_code == 404

            resp = await ac.delete(f"/api/v1/quotations/quotation-items/{foreign_item_id}", headers=headers_a)
            assert resp.status_code == 404

            # Update/delete foreign labour item -> 404
            resp = await ac.put(f"/api/v1/quotations/labour/{foreign_labour_id}", json={"daily_wage": 999.0}, headers=headers_a)
            assert resp.status_code == 404

            resp = await ac.delete(f"/api/v1/quotations/labour/{foreign_labour_id}", headers=headers_a)
            assert resp.status_code == 404

            # Update/delete foreign material item -> 404
            resp = await ac.put(f"/api/v1/quotations/quotation-materials/{foreign_mat_id}", json={"estimated_rate": 999.0}, headers=headers_a)
            assert resp.status_code == 404

            resp = await ac.delete(f"/api/v1/quotations/quotation-materials/{foreign_mat_id}", headers=headers_a)
            assert resp.status_code == 404

            # Update/delete foreign extra charge -> 404
            resp = await ac.put(f"/api/v1/quotations/quotation-extra-charges/{foreign_extra_id}", json={"rate": 999.0}, headers=headers_a)
            assert resp.status_code == 404

            resp = await ac.delete(f"/api/v1/quotations/quotation-extra-charges/{foreign_extra_id}", headers=headers_a)
            assert resp.status_code == 404


# ==============================================================================
# TEST 10 — P0 FIX: CONVERT TO BILL BLOCKS CROSS-TENANT PROJECT & CONTRACTOR
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_p0_convert_to_bill_blocks_foreign_resources():
    """Verify that convert-to-bill blocks foreign project_id or contractor_id with 404."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}

            q_id = d_data["qtn_a3"].id  # approved quotation
            valid_proj_id = d_data["proj_a"].id
            valid_contractor_id = d_data["contractor_a"].id
            foreign_proj_id = d_data["proj_b"].id
            foreign_contractor_id = d_data["contractor_b"].id

            # 1. Foreign project injection -> 404
            resp1 = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-bill?project_id={foreign_proj_id}&contractor_id={valid_contractor_id}",
                headers=headers_a,
            )
            assert resp1.status_code == 404, f"Expected 404, got {resp1.status_code}"

            # 2. Foreign contractor injection -> 404
            resp2 = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-bill?project_id={valid_proj_id}&contractor_id={foreign_contractor_id}",
                headers=headers_a,
            )
            assert resp2.status_code == 404, f"Expected 404, got {resp2.status_code}"

            # 3. Valid same-company resources -> 200
            resp3 = await ac.post(
                f"/api/v1/quotations/{q_id}/convert-to-bill?project_id={valid_proj_id}&contractor_id={valid_contractor_id}",
                headers=headers_a,
            )
            assert resp3.status_code == 200, f"Expected 200, got {resp3.status_code}: {resp3.text}"
            data = resp3.json()
            assert "bill_id" in data
            assert data["project_id"] == valid_proj_id
            assert data["contractor_id"] == valid_contractor_id


# ==============================================================================
# TEST 11 — P0 FIX: CONVERT TO WORK ORDER BLOCKS CROSS-TENANT PROJECT & CONTRACTOR
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_p0_convert_to_work_order_blocks_foreign_resources():
    """Verify that convert-to-work-order blocks foreign project_id or contractor_id with 404."""
    async with setup_batch_k_data() as d_data:
        # Use an approved quotation for this test
        async with AsyncSessionLocal() as db:
            qtn_wo = QuotationMaster(
                quotation_no=f"QT/2026/WO-{uuid.uuid4().hex[:6]}",
                company_id=d_data["comp_a"].id,
                client_user_id=d_data["client_user_a"].id,
                client_name="Client User A",
                mobile_number="9876543210",
                project_name="Work Order Quotation",
                project_type="Residential",
                status=QuotationStatus.APPROVED,
                is_approved=True,
                approved_at=datetime.utcnow(),
                grand_total=300000.0,
                subtotal=300000.0,
            )
            db.add(qtn_wo)
            await db.commit()
            await db.refresh(qtn_wo)
            wo_qtn_id = qtn_wo.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}

            valid_proj_id = d_data["proj_a"].id
            valid_contractor_id = d_data["contractor_a"].id
            foreign_proj_id = d_data["proj_b"].id
            foreign_contractor_id = d_data["contractor_b"].id

            # 1. Foreign project -> 404
            resp1 = await ac.post(
                f"/api/v1/quotations/{wo_qtn_id}/convert-to-work-order?project_id={foreign_proj_id}&contractor_id={valid_contractor_id}",
                headers=headers_a,
            )
            assert resp1.status_code == 404

            # 2. Foreign contractor -> 404
            resp2 = await ac.post(
                f"/api/v1/quotations/{wo_qtn_id}/convert-to-work-order?project_id={valid_proj_id}&contractor_id={foreign_contractor_id}",
                headers=headers_a,
            )
            assert resp2.status_code == 404

            # 3. Valid same-company resources -> 200
            resp3 = await ac.post(
                f"/api/v1/quotations/{wo_qtn_id}/convert-to-work-order?project_id={valid_proj_id}&contractor_id={valid_contractor_id}",
                headers=headers_a,
            )
            assert resp3.status_code == 200
            data = resp3.json()
            assert "work_order_id" in data
            assert data["project_id"] == valid_proj_id
            assert data["contractor_id"] == valid_contractor_id


# ==============================================================================
# TEST 12 — P0 FIX: CONVERT TO PROJECT BLOCKS FOREIGN OWNER
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_p0_convert_to_project_blocks_foreign_owner():
    """Verify that convert-to-project blocks foreign owner_id with 404."""
    async with setup_batch_k_data() as d_data:
        async with AsyncSessionLocal() as db:
            qtn_prj = QuotationMaster(
                quotation_no=f"QT/2026/PRJ-{uuid.uuid4().hex[:6]}",
                company_id=d_data["comp_a"].id,
                client_user_id=d_data["client_user_a"].id,
                client_name="Client User A",
                mobile_number="9876543210",
                project_name="Convert Project Quotation",
                project_type="Residential",
                status=QuotationStatus.APPROVED,
                is_approved=True,
                approved_at=datetime.utcnow(),
                grand_total=400000.0,
                subtotal=400000.0,
            )
            db.add(qtn_prj)
            await db.commit()
            await db.refresh(qtn_prj)
            prj_qtn_id = qtn_prj.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}

            valid_owner_id = d_data["owner_a"].id
            foreign_owner_id = d_data["owner_b"].id

            # 1. Foreign owner -> 404
            resp1 = await ac.post(
                f"/api/v1/quotations/{prj_qtn_id}/convert-to-project",
                json={"owner_id": foreign_owner_id},
                headers=headers_a,
            )
            assert resp1.status_code == 404

            # 2. Valid same-company owner -> 200
            resp2 = await ac.post(
                f"/api/v1/quotations/{prj_qtn_id}/convert-to-project",
                json={"owner_id": valid_owner_id},
                headers=headers_a,
            )
            assert resp2.status_code == 200
            data = resp2.json()
            assert "project_id" in data
            assert data["quotation_id"] == prj_qtn_id


# ==============================================================================
# TEST 13 — P1 FIX: CREATE QUOTATION BLOCKS FOREIGN CLIENT & RESOURCES
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_p1_create_quotation_blocks_foreign_references():
    """Verify that create_quotation rejects foreign client, labour, material, and equipment IDs."""
    async with setup_batch_k_data() as d_data:
        # Create a fresh client user for company A with no active quotations
        async with AsyncSessionLocal() as db:
            client_fresh = User(
                email=f"client_fresh_{uuid.uuid4().hex[:6]}@test.com",
                hashed_password=get_password_hash("Secret123!"),
                full_name="Fresh Client A",
                company_id=d_data["comp_a"].id,
                is_super_admin=False,
                is_active=True,
                role="Client",
            )
            db.add(client_fresh)
            await db.commit()
            await db.refresh(client_fresh)
            fresh_client_id = client_fresh.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}

            foreign_client_id = d_data["client_user_b"].id
            valid_labour_id = d_data["labour_a"].id
            foreign_labour_id = d_data["labour_b"].id
            valid_mat_id = d_data["mat_a"].id
            foreign_mat_id = d_data["mat_b"].id
            valid_eq_id = d_data["eq_a"].id
            foreign_eq_id = d_data["eq_b"].id

            base_payload = {
                "client_name": "Test Client",
                "mobile_number": "9876543210",
                "project_name": "Creation Test Project",
                "project_type": "Residential",
                "items": [
                    {
                        "item_type": "soling",
                        "title": "Item 1",
                        "unit": "brass",
                        "rate": 1000.0,
                        "measurements": [{"length": 10.0, "width": 10.0, "height": 1.0}],
                    }
                ],
            }

            # 1. Foreign client -> 404
            payload_bad_client = {**base_payload, "client_user_id": foreign_client_id}
            resp = await ac.post("/api/v1/quotations/", json=payload_bad_client, headers=headers_a)
            assert resp.status_code == 404

            # 2. Foreign labour -> 404
            payload_bad_labour = {
                **base_payload,
                "client_user_id": fresh_client_id,
                "labour_items": [
                    {
                        "labour_id": foreign_labour_id,
                        "skill_type": "Skilled",
                        "labour_count": 1,
                        "daily_wage": 500.0,
                        "labour_days": 1,
                    }
                ],
            }
            resp = await ac.post("/api/v1/quotations/", json=payload_bad_labour, headers=headers_a)
            assert resp.status_code == 404

            # 3. Foreign material -> 404
            payload_bad_mat = {
                **base_payload,
                "client_user_id": fresh_client_id,
                "material_items": [
                    {
                        "material_id": foreign_mat_id,
                        "material_name": "Cement",
                        "unit": "Bags",
                        "estimated_quantity": 10.0,
                        "estimated_rate": 300.0,
                    }
                ],
            }
            resp = await ac.post("/api/v1/quotations/", json=payload_bad_mat, headers=headers_a)
            assert resp.status_code == 404

            # 4. Foreign equipment -> 404
            payload_bad_eq = {
                **base_payload,
                "client_user_id": fresh_client_id,
                "extra_charge_items": [
                    {
                        "equipment_id": foreign_eq_id,
                        "expense_type": "Equipment",
                        "description": "Crane",
                        "quantity": 1.0,
                        "rate": 1000.0,
                    }
                ],
            }
            resp = await ac.post("/api/v1/quotations/", json=payload_bad_eq, headers=headers_a)
            assert resp.status_code == 404


# ==============================================================================
# TEST 14 — SUPER ADMIN CROSS-COMPANY ACCESS
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_super_admin_cross_company_access():
    """Verify that Super Admin can view quotations across all companies."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_super = d_data["tokens"]["super"]
            headers_super = {"Authorization": f"Bearer {token_super}"}

            # Super admin can list quotations and see both company A and company B quotations
            resp = await ac.get("/api/v1/quotations/", headers=headers_super)
            assert resp.status_code == 200
            q_ids = [q["id"] for q in resp.json()]
            assert d_data["qtn_a1"].id in q_ids
            assert d_data["qtn_b1"].id in q_ids

            # Super admin can get detail of Company A quotation
            resp_a = await ac.get(f"/api/v1/quotations/{d_data['qtn_a1'].id}", headers=headers_super)
            assert resp_a.status_code == 200

            # Super admin can get detail of Company B quotation
            resp_b = await ac.get(f"/api/v1/quotations/{d_data['qtn_b1'].id}", headers=headers_super)
            assert resp_b.status_code == 200


# ==============================================================================
# TEST 15 — CLIENT USER SELF-SERVICE ACCESS AND ISOLATION
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_client_user_access_and_isolation():
    """Verify that a Client user with quotations.view can view own quotation but is blocked from foreign quotations."""
    async with setup_batch_k_data() as d_data:
        # Grant quotations.view to Client role in DB
        async with AsyncSessionLocal() as db:
            perm = await db.scalar(select(Permission).where(Permission.code == "quotations.view"))
            db.add(RolePermission(role="Client", permission_id=perm.id))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_client_a = d_data["tokens"]["client_a"]
            headers_client_a = {"Authorization": f"Bearer {token_client_a}"}

            # Client A can list own quotations
            resp = await ac.get("/api/v1/quotations/", headers=headers_client_a)
            assert resp.status_code == 200
            items = resp.json()
            for item in items:
                assert item["client_user_id"] == d_data["client_user_a"].id

            # Client A can get own quotation
            resp_own = await ac.get(f"/api/v1/quotations/{d_data['qtn_a1'].id}", headers=headers_client_a)
            assert resp_own.status_code == 200

            # Client A trying to get Client B's quotation -> 404
            resp_foreign = await ac.get(f"/api/v1/quotations/{d_data['qtn_b1'].id}", headers=headers_client_a)
            assert resp_foreign.status_code == 404


# ==============================================================================
# TEST 16 — QUOTATION LIFECYCLE: SEND, APPROVE, REJECT, DELETE
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_quotation_lifecycle_and_business_rules():
    """Verify the business lifecycle: Send -> Approve / Reject, and deletion business rules."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            q_id = d_data["qtn_a1"].id

            # 1. Cannot approve DRAFT quotation directly (must be sent first)
            resp_app = await ac.put(f"/api/v1/quotations/{q_id}/approve", headers=headers_a)
            assert resp_app.status_code == 400

            # 2. Cannot reject DRAFT quotation directly (must be sent first)
            resp_rej = await ac.put(f"/api/v1/quotations/{q_id}/reject", json={"reason": "Too expensive"}, headers=headers_a)
            assert resp_rej.status_code == 400

            # 3. Send quotation -> 200
            resp_send = await ac.post(f"/api/v1/quotations/{q_id}/send", headers=headers_a)
            assert resp_send.status_code == 200

            # 4. Now approve the sent quotation -> 200
            resp_app2 = await ac.put(f"/api/v1/quotations/{q_id}/approve", headers=headers_a)
            assert resp_app2.status_code == 200

            # 5. Approved quotation cannot be deleted -> 400
            resp_del = await ac.delete(f"/api/v1/quotations/{q_id}", headers=headers_a)
            assert resp_del.status_code == 400

            # 6. Approved quotation cannot be edited -> 400
            resp_edit = await ac.put(f"/api/v1/quotations/{q_id}", json={"project_name": "Edited Name"}, headers=headers_a)
            assert resp_edit.status_code == 400


# ==============================================================================
# TEST 17 — PDF EXPORT USES TENANT BRANDING & RETURNS STREAM
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_pdf_export_tenant_scoped():
    """Verify that PDF generation endpoint returns application/pdf streaming response."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            q_id = d_data["qtn_a1"].id

            resp = await ac.get(f"/api/v1/quotations/{q_id}/pdf", headers=headers_a)
            assert resp.status_code == 200
            assert resp.headers.get("content-type") == "application/pdf"
            assert "attachment" in resp.headers.get("content-disposition", "")
            assert len(resp.content) > 0


# ==============================================================================
# TEST 18 — SUB-RESOURCE CRUD OPERATIONS (ITEMS, LABOUR, MATERIALS, EXTRA CHARGES)
# ==============================================================================


@pytest.mark.asyncio
async def test_batch_k_subresource_crud():
    """Verify that sub-resources can be created, updated, and deleted on draft quotations."""
    async with setup_batch_k_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token_a = d_data["tokens"]["admin_a"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            q_id = d_data["qtn_a1"].id

            # 1. Add Quotation Item
            item_payload = {
                "item_type": "soling",
                "title": "Added Item",
                "unit": "brass",
                "rate": 1500.0,
                "measurements": [{"length": 5.0, "width": 5.0, "height": 1.0}],
            }
            resp_item = await ac.post(f"/api/v1/quotations/{q_id}/items", json=item_payload, headers=headers_a)
            assert resp_item.status_code == 200

            # 2. Add Labour Item
            labour_payload = {
                "labour_id": d_data["labour_a"].id,
                "skill_type": "Semi-Skilled",
                "labour_count": 3,
                "daily_wage": 600.0,
                "labour_days": 4,
            }
            resp_lab = await ac.post(f"/api/v1/quotations/{q_id}/labour", json=labour_payload, headers=headers_a)
            assert resp_lab.status_code == 200

            # 3. Add Material Item
            mat_payload = {
                "material_id": d_data["mat_a"].id,
                "material_name": "Added Cement",
                "unit": "Bags",
                "estimated_quantity": 20.0,
                "estimated_rate": 350.0,
            }
            resp_mat = await ac.post(f"/api/v1/quotations/{q_id}/materials", json=mat_payload, headers=headers_a)
            assert resp_mat.status_code == 200

            # 4. Add Extra Charge
            extra_payload = {
                "equipment_id": d_data["eq_a"].id,
                "expense_type": "Transport",
                "description": "Truck Delivery",
                "quantity": 1.0,
                "rate": 2000.0,
            }
            resp_extra = await ac.post(f"/api/v1/quotations/{q_id}/extra-charges", json=extra_payload, headers=headers_a)
            assert resp_extra.status_code == 200

            # 5. List Materials & Extra Charges
            resp_mats = await ac.get(f"/api/v1/quotations/{q_id}/materials", headers=headers_a)
            assert resp_mats.status_code == 200

            resp_extras = await ac.get(f"/api/v1/quotations/{q_id}/extra-charges", headers=headers_a)
            assert resp_extras.status_code == 200

            # 6. Preview Quotation
            resp_prev = await ac.get(f"/api/v1/quotations/{q_id}/preview", headers=headers_a)
            assert resp_prev.status_code == 200
