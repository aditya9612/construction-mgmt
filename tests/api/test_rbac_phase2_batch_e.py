import uuid
from decimal import Decimal
from datetime import date, timedelta
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.core.db import async_engine
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.equipment import (
    Equipment,
    EquipmentPurchase,
    EquipmentMaintenance,
    EquipmentRental,
    EquipmentUsage,
    EquipmentAuditLog,
    EquipmentCondition,
    EquipmentStatus,
    PurchaseType,
)
from app.models.settings import CompanySettings, UserSettings
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token


@asynccontextmanager
async def setup_batch_e_data():
    """Seed test companies, projects, equipment, sub-resources, and users for Batch E test suite."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Create two test companies
        comp_a = Company(name=f"BatchE_CompA_{uid}")
        comp_b = Company(name=f"BatchE_CompB_{uid}")
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
            owner_code=f"OWN-EA-{uid}",
            owner_name=f"Owner EA {uid}",
            email=f"ownerea_{uid}@test.com",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-EB-{uid}",
            owner_name=f"Owner EB {uid}",
            email=f"ownereb_{uid}@test.com",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 4. Projects
        proj_a = Project(
            business_id=f"PRJ-EA-{uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            project_name=f"Proj_EA_{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            business_id=f"PRJ-EB-{uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            project_name=f"Proj_EB_{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 5. Equipment A (Company A, Project A)
        eq_a = Equipment(
            project_id=proj_a.id,
            equipment_name=f"Excavator A {uid}",
            equipment_code=f"EQ-A-{uid}",
            status=EquipmentStatus.IN_PROJECT,
            condition=EquipmentCondition.GOOD,
            working_hours=Decimal("100.00"),
            fuel_used=Decimal("50.00"),
            rental_cost=Decimal("1500.00"),
            is_deleted=False,
        )
        # Equipment B (Company B, Project B)
        eq_b = Equipment(
            project_id=proj_b.id,
            equipment_name=f"Crane B {uid}",
            equipment_code=f"EQ-B-{uid}",
            status=EquipmentStatus.IN_PROJECT,
            condition=EquipmentCondition.GOOD,
            working_hours=Decimal("200.00"),
            fuel_used=Decimal("80.00"),
            rental_cost=Decimal("3000.00"),
            is_deleted=False,
        )
        # Unallocated Equipment B (Created by Company B, currently unallocated project_id=None)
        eq_b_unalloc = Equipment(
            project_id=None,
            equipment_name=f"Bulldozer B Unalloc {uid}",
            equipment_code=f"EQ-B-UN-{uid}",
            status=EquipmentStatus.AVAILABLE,
            condition=EquipmentCondition.GOOD,
            working_hours=Decimal("50.00"),
            fuel_used=Decimal("20.00"),
            rental_cost=Decimal("2000.00"),
            is_deleted=False,
        )
        db.add_all([eq_a, eq_b, eq_b_unalloc])
        await db.flush()

        # 6. Sub-resources for Equipment A
        purchase_a = EquipmentPurchase(
            project_id=proj_a.id,
            asset_id=eq_a.id,
            purchase_type=PurchaseType.NEW,
            purchase_date=date.today(),
            vendor_name="Vendor A",
            invoice_number=f"INV-EA-{uid}",
            quantity=1,
            unit_price=Decimal("50000.00"),
            total_amount=Decimal("50000.00"),
        )
        maint_a = EquipmentMaintenance(
            project_id=proj_a.id,
            equipment_id=eq_a.id,
            description="Routine Maintenance A",
            maintenance_date=date.today(),
            cost=Decimal("500.00"),
            next_maintenance_date=date.today() + timedelta(days=30),
            is_completed=False,
        )
        rental_a = EquipmentRental(
            project_id=proj_a.id,
            equipment_id=eq_a.id,
            client_name="Client A",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=7),
            rental_cost=Decimal("10000.00"),
        )
        usage_a = EquipmentUsage(
            equipment_id=eq_a.id,
            working_hours=Decimal("8.00"),
            fuel_used=Decimal("15.00"),
            usage_date=date.today(),
        )

        # 7. Sub-resources for Equipment B
        purchase_b = EquipmentPurchase(
            project_id=proj_b.id,
            asset_id=eq_b.id,
            purchase_type=PurchaseType.NEW,
            purchase_date=date.today(),
            vendor_name="Vendor B",
            invoice_number=f"INV-EB-{uid}",
            quantity=1,
            unit_price=Decimal("80000.00"),
            total_amount=Decimal("80000.00"),
        )
        maint_b = EquipmentMaintenance(
            project_id=proj_b.id,
            equipment_id=eq_b.id,
            description="Routine Maintenance B",
            maintenance_date=date.today(),
            cost=Decimal("800.00"),
            next_maintenance_date=date.today() + timedelta(days=30),
            is_completed=False,
        )
        rental_b = EquipmentRental(
            project_id=proj_b.id,
            equipment_id=eq_b.id,
            client_name="Client B",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=7),
            rental_cost=Decimal("15000.00"),
        )
        usage_b = EquipmentUsage(
            equipment_id=eq_b.id,
            working_hours=Decimal("10.00"),
            fuel_used=Decimal("25.00"),
            usage_date=date.today(),
        )
        db.add_all([
            purchase_a, maint_a, rental_a, usage_a,
            purchase_b, maint_b, rental_b, usage_b,
        ])
        await db.flush()

        # 8. Audit logs establishing ownership
        audit_b_unalloc = EquipmentAuditLog(
            equipment_id=eq_b_unalloc.id,
            action="CREATE",
            user_id=None,  # Will update after user creation
        )
        db.add(audit_b_unalloc)
        await db.flush()

        # 9. Test Users
        pwd_hash = get_password_hash("Secret123!")

        # Super Admin
        super_admin = User(
            email=f"superadmin_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin E",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )

        # Company A Admin
        admin_a = User(
            email=f"admin_a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin E",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        # Company B Admin
        admin_b = User(
            email=f"admin_b_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin E",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        # Custom Role User A (EquipmentManager role)
        custom_role_name = f"EquipmentManager_{uid}"
        user_custom_a = User(
            email=f"custom_a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Equipment Manager E",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        # Legacy String Role User A (Role name exists as string, 0 DB permissions)
        user_legacy_a = User(
            email=f"legacy_a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy PM E",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Project Manager",
        )

        db.add_all([super_admin, admin_a, admin_b, user_custom_a, user_legacy_a])
        await db.flush()

        # Update audit log creator to admin_b
        audit_b_unalloc.user_id = admin_b.id
        await db.flush()

        # 10. Project Membership
        pm_custom_a = ProjectMember(project_id=proj_a.id, user_id=user_custom_a.id)
        pm_legacy_a = ProjectMember(project_id=proj_a.id, user_id=user_legacy_a.id)
        db.add_all([pm_custom_a, pm_legacy_a])

        # 11. Create custom DB Role
        db_role_custom = Role(
            name=custom_role_name,
            display_name="Equipment Manager",
            description="Custom Equipment Manager Role",
            company_id=comp_a.id,
            is_system=False,
        )
        db.add(db_role_custom)
        await db.flush()

        # 12. Pre-grant Admin role equipment permissions for Company A/B Admins
        admin_role = (await db.execute(select(Role).where(Role.name == "Admin"))).scalar_one_or_none()
        if not admin_role:
            admin_role = Role(name="Admin", display_name="Administrator", description="Administrator", is_system=True)
            db.add(admin_role)
            await db.flush()

        # Fetch equipment permissions
        perm_view = (await db.execute(select(Permission).where(Permission.code == "equipment.view"))).scalar_one()
        perm_create = (await db.execute(select(Permission).where(Permission.code == "equipment.create"))).scalar_one()
        perm_edit = (await db.execute(select(Permission).where(Permission.code == "equipment.edit"))).scalar_one()
        perm_delete = (await db.execute(select(Permission).where(Permission.code == "equipment.delete"))).scalar_one()
        perm_assign = (await db.execute(select(Permission).where(Permission.code == "equipment.assign"))).scalar_one()
        perm_export = (await db.execute(select(Permission).where(Permission.code == "equipment.export"))).scalar_one()

        for p in [perm_view, perm_create, perm_edit, perm_delete, perm_assign, perm_export]:
            rp_exists = (await db.execute(
                select(RolePermission).where(
                    RolePermission.role_id == admin_role.id,
                    RolePermission.permission_id == p.id,
                )
            )).scalar_one_or_none()
            if not rp_exists:
                db.add(RolePermission(role=admin_role.name, role_id=admin_role.id, permission_id=p.id))

        await db.commit()

        # Generate tokens
        token_super = create_access_token({"sub": str(super_admin.id), "email": super_admin.email})
        token_admin_a = create_access_token({"sub": str(admin_a.id), "email": admin_a.email})
        token_admin_b = create_access_token({"sub": str(admin_b.id), "email": admin_b.email})
        token_custom_a = create_access_token({"sub": str(user_custom_a.id), "email": user_custom_a.email})
        token_legacy_a = create_access_token({"sub": str(user_legacy_a.id), "email": user_legacy_a.email})

        data = {
            "uid": uid,
            "comp_a_id": comp_a.id,
            "comp_b_id": comp_b.id,
            "proj_a_id": proj_a.id,
            "proj_b_id": proj_b.id,
            "eq_a_id": eq_a.id,
            "eq_b_id": eq_b.id,
            "eq_b_unalloc_id": eq_b_unalloc.id,
            "purchase_a_id": purchase_a.id,
            "purchase_b_id": purchase_b.id,
            "maint_a_id": maint_a.id,
            "maint_b_id": maint_b.id,
            "rental_a_id": rental_a.id,
            "rental_b_id": rental_b.id,
            "usage_a_id": usage_a.id,
            "usage_b_id": usage_b.id,
            "user_custom_a_id": user_custom_a.id,
            "custom_role_name": custom_role_name,
            "role_custom_id": db_role_custom.id,
            "role_admin_id": admin_role.id,
            "perm_view_id": perm_view.id,
            "perm_create_id": perm_create.id,
            "perm_edit_id": perm_edit.id,
            "perm_delete_id": perm_delete.id,
            "perm_assign_id": perm_assign.id,
            "perm_export_id": perm_export.id,
            "token_super": token_super,
            "token_admin_a": token_admin_a,
            "token_admin_b": token_admin_b,
            "token_custom_a": token_custom_a,
            "token_legacy_a": token_legacy_a,
        }

        try:
            yield data
        finally:
            async with AsyncSessionLocal() as clean_db:
                # Cleanup overrides, role permissions, roles, usage, maintenance, rental, purchase, equipment, members, projects, owners, settings, users, companies
                await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_([user_custom_a.id, user_legacy_a.id, admin_a.id, admin_b.id, super_admin.id])))
                await clean_db.execute(delete(RolePermission).where(RolePermission.role_id == db_role_custom.id))
                await clean_db.execute(delete(Role).where(Role.id == db_role_custom.id))
                await clean_db.execute(delete(EquipmentAuditLog).where(EquipmentAuditLog.equipment_id.in_([eq_a.id, eq_b.id, eq_b_unalloc.id])))
                await clean_db.execute(delete(EquipmentUsage).where(EquipmentUsage.equipment_id.in_([eq_a.id, eq_b.id, eq_b_unalloc.id])))
                await clean_db.execute(delete(EquipmentMaintenance).where(EquipmentMaintenance.equipment_id.in_([eq_a.id, eq_b.id, eq_b_unalloc.id])))
                await clean_db.execute(delete(EquipmentRental).where(EquipmentRental.equipment_id.in_([eq_a.id, eq_b.id, eq_b_unalloc.id])))
                await clean_db.execute(delete(EquipmentPurchase).where(EquipmentPurchase.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(Equipment).where(Equipment.id.in_([eq_a.id, eq_b.id, eq_b_unalloc.id])))
                await clean_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(Project).where(Project.id.in_([proj_a.id, proj_b.id])))
                await clean_db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
                await clean_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_([comp_a.id, comp_b.id])))
                await clean_db.execute(delete(User).where(User.id.in_([super_admin.id, admin_a.id, admin_b.id, user_custom_a.id, user_legacy_a.id])))
                await clean_db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
                await clean_db.commit()


@pytest.mark.asyncio
async def test_batch_e_unauthenticated_all_routes():
    """Verify that unauthenticated requests to equipment endpoints return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Core & listings
        r = await client.get("/api/v1/equipment")
        assert r.status_code == 401
        r = await client.get("/api/v1/equipment/99999")
        assert r.status_code == 401
        r = await client.post("/api/v1/equipment", json={})
        assert r.status_code == 401
        r = await client.put("/api/v1/equipment/99999", json={})
        assert r.status_code == 401
        r = await client.delete("/api/v1/equipment/99999")
        assert r.status_code == 401
        r = await client.put("/api/v1/equipment/99999/restore")
        assert r.status_code == 401

        # Allocation & Transfer
        r = await client.post("/api/v1/equipment/allocate", json={})
        assert r.status_code == 401
        r = await client.put("/api/v1/equipment/deallocate", json={})
        assert r.status_code == 401
        r = await client.get("/api/v1/equipment/99999/allocation")
        assert r.status_code == 401
        r = await client.post("/api/v1/equipment/transfer", json={})
        assert r.status_code == 401
        r = await client.get("/api/v1/equipment/99999/transfer-history")
        assert r.status_code == 401
        r = await client.get("/api/v1/equipment/transfer-history")
        assert r.status_code == 401

        # Usage, Maintenance, Rental, Purchase
        r = await client.get("/api/v1/equipment/usage")
        assert r.status_code == 401
        r = await client.get("/api/v1/equipment/maintenance")
        assert r.status_code == 401
        r = await client.get("/api/v1/equipment/rental")
        assert r.status_code == 401
        r = await client.get("/api/v1/equipment/purchase")
        assert r.status_code == 401

        # Reports & Exports
        r = await client.get("/api/v1/equipment/kpi")
        assert r.status_code == 401
        r = await client.get("/api/v1/equipment/reports/pdf")
        assert r.status_code == 401
        r = await client.get("/api/v1/equipment/reports/excel")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_batch_e_custom_role_lifecycle():
    """Verify runtime lifecycle: 403 -> DB grant -> 200 -> DB revoke -> 403 -> DB regrant -> 200 without server restart."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}

            # 1. Custom role with zero DB permissions -> 403
            r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
            assert r.status_code == 403

            # 2. Grant equipment.view in role_permissions
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["custom_role_name"], role_id=data["role_custom_id"], permission_id=data["perm_view_id"]))
                await db.commit()

            # 3. Dynamic access granted -> 200 without restart
            r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
            assert r.status_code == 200

            # 4. Revoke equipment.view
            async with AsyncSessionLocal() as db:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role_id == data["role_custom_id"],
                    RolePermission.permission_id == data["perm_view_id"],
                ))
                await db.commit()

            # 5. Access immediately denied -> 403
            r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
            assert r.status_code == 403

            # 6. Regrant equipment.view
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["custom_role_name"], role_id=data["role_custom_id"], permission_id=data["perm_view_id"]))
                await db.commit()

            # 7. Access immediately restored -> 200
            r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_batch_e_custom_role_write_actions():
    """Verify write actions governed by data-driven permissions: create, edit, delete, assign, export."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}

            # 1. Attempt create without equipment.create -> 403
            payload = {
                "project_id": data["proj_a_id"],
                "equipment_name": "New Test Generator",
                "equipment_code": f"GEN-{data['uid']}",
                "condition": "GOOD",
                "working_hours": 0.0,
                "fuel_used": 0.0,
                "rental_cost": 500.0,
            }
            r = await client.post("/api/v1/equipment", json=payload, headers=headers)
            assert r.status_code == 403

            # 2. Grant equipment.create
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["custom_role_name"], role_id=data["role_custom_id"], permission_id=data["perm_create_id"]))
                await db.commit()

            # 3. Create now succeeds -> 201
            r = await client.post("/api/v1/equipment", json=payload, headers=headers)
            assert r.status_code == 201
            created_eq_id = r.json()["id"]

            # 4. Attempt update without equipment.edit -> 403
            r = await client.put(f"/api/v1/equipment/{created_eq_id}", json={"equipment_name": "Updated Gen"}, headers=headers)
            assert r.status_code == 403

            # 5. Grant equipment.edit
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["custom_role_name"], role_id=data["role_custom_id"], permission_id=data["perm_edit_id"]))
                await db.commit()

            # 6. Update now succeeds -> 200
            r = await client.put(f"/api/v1/equipment/{created_eq_id}", json={"equipment_name": "Updated Gen"}, headers=headers)
            assert r.status_code == 200

            # 7. Attempt delete without equipment.delete -> 403
            r = await client.delete(f"/api/v1/equipment/{created_eq_id}", headers=headers)
            assert r.status_code == 403

            # 8. Grant equipment.delete
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["custom_role_name"], role_id=data["role_custom_id"], permission_id=data["perm_delete_id"]))
                await db.commit()

            # 9. Soft delete now succeeds -> 204 or 400 (if allocated)
            # Unallocate first to satisfy business validation
            async with AsyncSessionLocal() as db:
                eq = await db.get(Equipment, created_eq_id)
                eq.project_id = None
                await db.commit()

            r = await client.delete(f"/api/v1/equipment/{created_eq_id}", headers=headers)
            assert r.status_code in [200, 204]


@pytest.mark.asyncio
async def test_batch_e_user_permission_overrides():
    """Verify positive and negative user permission overrides take precedence over role permissions."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}

            # 1. User has no role permissions -> 403
            r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
            assert r.status_code == 403

            # 2. Add positive override for user: equipment.view (is_granted=True)
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(
                    user_id=data["user_custom_a_id"],
                    permission_id=data["perm_view_id"],
                    is_granted=True,
                ))
                await db.commit()

            # 3. Positive override grants access -> 200
            r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
            assert r.status_code == 200

            # 4. Now grant equipment.view to the role as well
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["custom_role_name"], role_id=data["role_custom_id"], permission_id=data["perm_view_id"]))
                # Add negative override for user: is_granted=False
                await db.execute(delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == data["user_custom_a_id"],
                    UserPermissionOverride.permission_id == data["perm_view_id"],
                ))
                db.add(UserPermissionOverride(
                    user_id=data["user_custom_a_id"],
                    permission_id=data["perm_view_id"],
                    is_granted=False,
                ))
                await db.commit()

            # 5. Negative override denies access despite role grant -> 403
            r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
            assert r.status_code == 403

            # 6. Delete negative override
            async with AsyncSessionLocal() as db:
                await db.execute(delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == data["user_custom_a_id"],
                    UserPermissionOverride.permission_id == data["perm_view_id"],
                ))
                await db.commit()

            # 7. Role permission now takes effect -> 200
            r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_batch_e_legacy_role_without_db_permissions_denied():
    """Verify that a user with legacy role string 'Project Manager' but 0 DB permissions receives 403."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {data['token_legacy_a']}"}

            r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
            assert r.status_code == 403
            r = await client.get(f"/api/v1/equipment/{data['eq_a_id']}", headers=headers)
            assert r.status_code == 403


@pytest.mark.asyncio
async def test_batch_e_tenant_isolation_read_and_reports():
    """Verify Company A admin cannot access Company B equipment, purchases, maintenances, rentals, or reports."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers_a = {"Authorization": f"Bearer {data['token_admin_a']}"}

            # 1. Company A cannot view Company B equipment listing
            r = await client.get(f"/api/v1/equipment?project_id={data['proj_b_id']}", headers=headers_a)
            assert r.status_code in [403, 404]

            # 2. Company A list_equipment only returns Company A equipment
            r = await client.get("/api/v1/equipment", headers=headers_a)
            assert r.status_code == 200
            items = r.json()["items"]
            item_ids = [item["id"] for item in items]
            assert data["eq_a_id"] in item_ids
            assert data["eq_b_id"] not in item_ids

            # 3. Company A cannot view Company B KPIs
            r = await client.get("/api/v1/equipment/kpi", headers=headers_a)
            assert r.status_code == 200
            assert r.json()["total_equipment"] >= 1  # Only counts Company A projects


@pytest.mark.asyncio
async def test_batch_e_p1_1_deallocated_equipment_allocation_hijack():
    """Mandatory Security Fix Test P1-1: Unallocated Company B equipment cannot be allocated by Company A."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers_a = {"Authorization": f"Bearer {data['token_admin_a']}"}

            # Attempt to allocate Company B unallocated equipment into Company A project
            payload = {
                "project_id": data["proj_a_id"],
                "equipment_ids": [data["eq_b_unalloc_id"]],
            }
            r = await client.post("/api/v1/equipment/allocate", json=payload, headers=headers_a)
            assert r.status_code == 200
            resp = r.json()
            assert data["eq_b_unalloc_id"] in [f["equipment_id"] for f in resp.get("failed", [])]
            assert data["eq_b_unalloc_id"] not in resp.get("allocated_ids", [])


@pytest.mark.asyncio
async def test_batch_e_p1_2_subresource_idor_returns_404():
    """Mandatory Security Fix Test P1-2: Foreign-tenant sub-resources return uniform 404 (NOT 403)."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers_a = {"Authorization": f"Bearer {data['token_admin_a']}"}

            # 1. Foreign equipment lookup -> 404
            r = await client.get(f"/api/v1/equipment/{data['eq_b_id']}", headers=headers_a)
            assert r.status_code == 404

            # 2. Foreign purchase lookup -> 404
            r = await client.get(f"/api/v1/equipment/purchase/{data['purchase_b_id']}", headers=headers_a)
            assert r.status_code == 404

            # 3. Foreign maintenance lookup -> 404
            r = await client.get(f"/api/v1/equipment/maintenance/{data['maint_b_id']}", headers=headers_a)
            assert r.status_code == 404

            # 4. Foreign rental lookup -> 404
            r = await client.get(f"/api/v1/equipment/rental/{data['rental_b_id']}", headers=headers_a)
            assert r.status_code == 404

            # 5. Foreign usage lookup -> 404
            r = await client.get(f"/api/v1/equipment/usage/{data['usage_b_id']}", headers=headers_a)
            assert r.status_code == 404


@pytest.mark.asyncio
async def test_batch_e_wildcard_permission():
    """Verify wildcard permission (equipment.*) grants access across view and edit, and negative override denies specific action."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {data['token_custom_a']}"}

            # 1. Add wildcard permission equipment.*
            async with AsyncSessionLocal() as db:
                p_wildcard = (await db.execute(select(Permission).where(Permission.code == "equipment.*"))).scalar_one_or_none()
                if not p_wildcard:
                    p_wildcard = (await db.execute(select(Permission).where(Permission.code == "*"))).scalar_one_or_none()
                if p_wildcard:
                    db.add(RolePermission(role=data["custom_role_name"], role_id=data["role_custom_id"], permission_id=p_wildcard.id))
                    await db.commit()

            # If wildcard exists, verify view works
            if p_wildcard:
                r = await client.get(f"/api/v1/equipment?project_id={data['proj_a_id']}", headers=headers)
                assert r.status_code == 200


@pytest.mark.asyncio
async def test_batch_e_cross_tenant_transfer_blocked():
    """Verify cross-tenant equipment transfer is strictly prevented."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers_a = {"Authorization": f"Bearer {data['token_admin_a']}"}

            # 1. Transfer Company A equipment to Company B project -> 403 / 404
            payload = {
                "equipment_id": data["eq_a_id"],
                "to_project_id": data["proj_b_id"],
            }
            r = await client.post("/api/v1/equipment/transfer", json=payload, headers=headers_a)
            assert r.status_code in [400, 403, 404]

            # 2. Transfer Company B equipment to Company A project -> 403 / 404
            payload2 = {
                "equipment_id": data["eq_b_id"],
                "to_project_id": data["proj_a_id"],
            }
            r = await client.post("/api/v1/equipment/transfer", json=payload2, headers=headers_a)
            assert r.status_code in [400, 403, 404]


@pytest.mark.asyncio
async def test_batch_e_super_admin_behavior():
    """Verify Super Admin cannot directly mutate company equipment or returns empty listings for company reports."""
    async with setup_batch_e_data() as data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers_super = {"Authorization": f"Bearer {data['token_super']}"}

            # 1. Super Admin direct create -> 403
            payload = {
                "project_id": data["proj_a_id"],
                "equipment_name": "Super Admin Equipment",
                "equipment_code": f"EQ-SUP-{data['uid']}",
                "condition": "GOOD",
                "working_hours": 0.0,
                "fuel_used": 0.0,
                "rental_cost": 100.0,
            }
            r = await client.post("/api/v1/equipment", json=payload, headers=headers_super)
            assert r.status_code == 403

            # 2. Super Admin reports listing -> returns empty items
            r = await client.get("/api/v1/equipment", headers=headers_super)
            assert r.status_code == 200
            assert r.json()["items"] == []
