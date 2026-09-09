import uuid
from decimal import Decimal
from datetime import date
from contextlib import asynccontextmanager
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.user import User, ActivityLog, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.settings import CompanySettings
from app.models.boq import BOQ, BOQGroup
from app.models.invoice import Invoice
from app.models.final_measurement import FinalMeasurement
from app.models.notification import Notification
from app.models.rbac import Role, Permission, RolePermission, UserPermissionOverride
from app.core.security import get_password_hash, create_access_token
from app.core.enums import (
    InvoiceSourceType,
    InvoiceStatus,
    InvoiceType,
    ProjectStatus,
)


@asynccontextmanager
async def setup_batch_n_data():
    """Seed test companies, projects, BOQ items, measurements, invoices, and users for Batch N."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]

        # 1. Companies
        comp_a = Company(name=f"BatchN_CompA_{uid}")
        comp_b = Company(name=f"BatchN_CompB_{uid}")
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
            email=f"superadmin_n_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin N",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Super Admin",
        )
        admin_a = User(
            email=f"admin_na_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company A Admin N",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        admin_b = User(
            email=f"admin_nb_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Company B Admin N",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )

        custom_role_name = f"Surveyor_{uid}"
        user_custom_a = User(
            email=f"custom_na_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Custom Surveyor N",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=custom_role_name,
        )

        legacy_admin_no_perm = User(
            email=f"legacy_admin_n_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Legacy Admin No Perm N",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=f"EmptyAdminN_{uid}",
        )

        dummy_none_company_user = User(
            email=f"nonecomp_n_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="None Comp User N",
            company_id=None,
            is_super_admin=False,
            is_active=True,
            role="Staff",
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

        # 4. Owners & Projects
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-NA-{uid}",
            owner_name="Owner NA",
            mobile=f"98{uuid.uuid4().int % 100000000:08d}",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-NB-{uid}",
            owner_name="Owner NB",
            mobile=f"97{uuid.uuid4().int % 100000000:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        proj_a1 = Project(
            business_id=f"PRJ-NA1-{uid}",
            project_name=f"Project NA1 {uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_a2 = Project(
            business_id=f"PRJ-NA2-{uid}",
            project_name=f"Project NA2 {uid}",
            company_id=comp_a.id,
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b1 = Project(
            business_id=f"PRJ-NB1-{uid}",
            project_name=f"Project NB1 {uid}",
            company_id=comp_b.id,
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a1, proj_a2, proj_b1])
        await db.flush()

        # 5. BOQ Groups & BOQ Items
        bg_a1 = BOQGroup(project_id=proj_a1.id, name="Group NA1")
        bg_b1 = BOQGroup(project_id=proj_b1.id, name="Group NB1")
        db.add_all([bg_a1, bg_b1])
        await db.flush()

        boq_a1 = BOQ(
            project_id=proj_a1.id,
            boq_group_id=bg_a1.id,
            item_name="Excavation Work",
            category="Civil",
            quantity=Decimal("100.000"),
            unit="cum",
            unit_cost=Decimal("500.00"),
            total_cost=Decimal("50000.00"),
            status="Active",
        )
        boq_b1 = BOQ(
            project_id=proj_b1.id,
            boq_group_id=bg_b1.id,
            item_name="Foreign Masonry",
            category="Civil",
            quantity=Decimal("100.000"),
            unit="sqm",
            unit_cost=Decimal("700.00"),
            total_cost=Decimal("70000.00"),
            status="Active",
        )
        db.add_all([boq_a1, boq_b1])
        await db.flush()

        # 6. Final Measurements
        # Measurement A1: normal DRAFT
        meas_a1 = FinalMeasurement(
            project_id=proj_a1.id,
            boq_item_id=boq_a1.id,
            final_area=Decimal("10.00"),
            approved_rate=Decimal("50.00"),
            extra_area=Decimal("2.00"),
            extra_rate=Decimal("60.00"),
            total_area=Decimal("12.00"),
            total_amount=Decimal("620.00"),
            measured_qty=Decimal("10.000"),
            status="DRAFT",
        )
        # Measurement A2: SUBMITTED (status locked)
        meas_a2_submitted = FinalMeasurement(
            project_id=proj_a1.id,
            boq_item_id=boq_a1.id,
            final_area=Decimal("15.00"),
            approved_rate=Decimal("50.00"),
            extra_area=Decimal("0.00"),
            extra_rate=Decimal("0.00"),
            total_area=Decimal("15.00"),
            total_amount=Decimal("750.00"),
            measured_qty=Decimal("15.000"),
            status="SUBMITTED",
        )
        # Measurement A3: DRAFT but linked to an Invoice (invoice locked)
        meas_a3_invoiced = FinalMeasurement(
            project_id=proj_a1.id,
            boq_item_id=boq_a1.id,
            final_area=Decimal("20.00"),
            approved_rate=Decimal("50.00"),
            extra_area=Decimal("0.00"),
            extra_rate=Decimal("0.00"),
            total_area=Decimal("20.00"),
            total_amount=Decimal("1000.00"),
            measured_qty=Decimal("20.000"),
            status="DRAFT",
        )
        # Measurement B1: Company B measurement
        meas_b1 = FinalMeasurement(
            project_id=proj_b1.id,
            boq_item_id=boq_b1.id,
            final_area=Decimal("30.00"),
            approved_rate=Decimal("100.00"),
            extra_area=Decimal("0.00"),
            extra_rate=Decimal("0.00"),
            total_area=Decimal("30.00"),
            total_amount=Decimal("3000.00"),
            measured_qty=Decimal("30.000"),
            status="DRAFT",
        )
        db.add_all([meas_a1, meas_a2_submitted, meas_a3_invoiced, meas_b1])
        await db.flush()

        # 7. Invoice linked to meas_a3_invoiced
        inv_a = Invoice(
            company_id=comp_a.id,
            project_id=proj_a1.id,
            owner_id=owner_a.id,
            type=InvoiceType.OWNER,
            source_type=InvoiceSourceType.MEASUREMENT,
            reference_id=meas_a3_invoiced.id,
            amount=Decimal("1000.00"),
            total_amount=Decimal("1000.00"),
            pending_amount=Decimal("1000.00"),
            status=InvoiceStatus.PENDING,
        )
        db.add(inv_a)
        await db.flush()

        # 8. RBAC Roles & DB Permissions
        role_custom = Role(
            name=custom_role_name,
            display_name="Custom Surveyor",
            company_id=comp_a.id,
        )
        role_legacy_empty = Role(
            name=f"EmptyAdminN_{uid}",
            display_name="Empty Admin N",
            company_id=comp_a.id,
        )
        db.add_all([role_custom, role_legacy_empty])
        await db.flush()

        # Fetch measurement permissions from catalog
        perm_codes = [
            "measurements.view",
            "measurements.create",
            "measurements.edit",
            "measurements.delete",
            "measurements.approve",
            "measurements.manage",
        ]
        res = await db.execute(select(Permission).where(Permission.module == "measurements"))
        perms = {p.code: p for p in res.scalars().all()}

        # Fetch or create wildcard permission
        res_wc = await db.execute(select(Permission).where(Permission.code == "measurements.*"))
        perm_wc = res_wc.scalar_one_or_none()
        if not perm_wc:
            perm_wc = Permission(
                code="measurements.*",
                module="measurements",
                action="*",
                description="Wildcard measurement access",
            )
            db.add(perm_wc)
            await db.flush()
        perms["measurements.*"] = perm_wc

        # Ensure Admin roles have permissions bound in DB
        role_admin_a = (
            await db.execute(select(Role).where(Role.name == "Admin", Role.company_id == comp_a.id))
        ).scalar_one_or_none()
        if not role_admin_a:
            role_admin_a = Role(name=f"Admin_NA_{uid}", display_name="Admin NA", company_id=comp_a.id)
            db.add(role_admin_a)
            await db.flush()
            admin_a.role = role_admin_a.name
            await db.flush()
        for code in ["measurements.view", "measurements.create", "measurements.edit", "measurements.delete"]:
            if code in perms:
                db.add(RolePermission(role=role_admin_a.name, role_id=role_admin_a.id, permission_id=perms[code].id))

        role_admin_b = (
            await db.execute(select(Role).where(Role.name == "Admin", Role.company_id == comp_b.id))
        ).scalar_one_or_none()
        if not role_admin_b:
            role_admin_b = Role(name=f"Admin_NB_{uid}", display_name="Admin NB", company_id=comp_b.id)
            db.add(role_admin_b)
            await db.flush()
            admin_b.role = role_admin_b.name
            await db.flush()
        for code in ["measurements.view", "measurements.create", "measurements.edit", "measurements.delete"]:
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
            "user_custom_a": user_custom_a,
            "legacy_admin_no_perm": legacy_admin_no_perm,
            "dummy_none_company_user": dummy_none_company_user,
            "proj_a1": proj_a1,
            "proj_a2": proj_a2,
            "proj_b1": proj_b1,
            "boq_a1": boq_a1,
            "boq_b1": boq_b1,
            "meas_a1": meas_a1,
            "meas_a2_submitted": meas_a2_submitted,
            "meas_a3_invoiced": meas_a3_invoiced,
            "meas_b1": meas_b1,
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
                    super_admin.id, admin_a.id, admin_b.id,
                    user_custom_a.id, legacy_admin_no_perm.id, dummy_none_company_user.id,
                ]
                r_ids = [role_custom.id, role_legacy_empty.id, role_admin_a.id, role_admin_b.id]

                await clean_db.execute(delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(u_ids)))
                await clean_db.execute(delete(RolePermission).where(RolePermission.role_id.in_(r_ids)))
                await clean_db.execute(delete(Role).where(Role.id.in_(r_ids)))
                await clean_db.execute(delete(ActivityLog).where(ActivityLog.performed_by.in_(u_ids)))
                await clean_db.execute(delete(Notification).where(Notification.user_id.in_(u_ids)))
                await clean_db.execute(delete(Invoice).where(Invoice.project_id.in_([proj_a1.id, proj_a2.id, proj_b1.id])))
                await clean_db.execute(delete(FinalMeasurement).where(FinalMeasurement.project_id.in_([proj_a1.id, proj_a2.id, proj_b1.id])))
                await clean_db.execute(delete(BOQ).where(BOQ.project_id.in_([proj_a1.id, proj_a2.id, proj_b1.id])))
                await clean_db.execute(delete(BOQGroup).where(BOQGroup.project_id.in_([proj_a1.id, proj_a2.id, proj_b1.id])))
                await clean_db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_([proj_a1.id, proj_a2.id, proj_b1.id])))
                await clean_db.execute(delete(Project).where(Project.company_id.in_(c_ids)))
                await clean_db.execute(delete(Owner).where(Owner.company_id.in_(c_ids)))
                await clean_db.execute(delete(CompanySettings).where(CompanySettings.company_id.in_(c_ids)))
                await clean_db.execute(delete(User).where(User.id.in_(u_ids)))
                await clean_db.execute(delete(Company).where(Company.id.in_(c_ids)))
                await clean_db.commit()


@pytest.mark.asyncio
async def test_batch_n_authentication_required():
    """All 6 Batch N routes return 401 Unauthorized without auth token."""
    async with setup_batch_n_data() as data:
        meas_id = data["meas_a1"].id
        proj_id = data["proj_a1"].id
        routes = [
            ("POST", "/api/v1/measurements", {"project_id": proj_id, "final_area": 10.0, "approved_rate": 50.0}),
            ("GET", f"/api/v1/measurements/project/{proj_id}", None),
            ("GET", f"/api/v1/measurements/{meas_id}", None),
            ("PUT", f"/api/v1/measurements/{meas_id}", {"final_area": 20.0}),
            ("DELETE", f"/api/v1/measurements/{meas_id}", None),
            ("PUT", f"/api/v1/measurements/{meas_id}/status", {"status": "SUBMITTED"}),
        ]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for method, url, payload in routes:
                if method == "GET":
                    res = await ac.get(url)
                elif method == "POST":
                    res = await ac.post(url, json=payload)
                elif method == "PUT":
                    res = await ac.put(url, json=payload)
                elif method == "DELETE":
                    res = await ac.delete(url)
                assert res.status_code == 401, f"Route {method} {url} expected 401, got {res.status_code}"


@pytest.mark.asyncio
async def test_batch_n_permission_denial():
    """Users without required permission receive 403 Forbidden across all 6 routes."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["user_custom_a"]  # 0 DB permissions
        headers = {"Authorization": f"Bearer {token}"}
        meas_id = data["meas_a1"].id
        proj_id = data["proj_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. POST / -> measurements.create
            res = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={"project_id": proj_id, "final_area": 10.0, "approved_rate": 50.0},
            )
            assert res.status_code == 403

            # 2. GET /project/{id} -> measurements.view
            res = await ac.get(f"/api/v1/measurements/project/{proj_id}", headers=headers)
            assert res.status_code == 403

            # 3. GET /{id} -> measurements.view
            res = await ac.get(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code == 403

            # 4. PUT /{id} -> measurements.edit
            res = await ac.put(f"/api/v1/measurements/{meas_id}", headers=headers, json={"final_area": 20.0})
            assert res.status_code == 403

            # 5. DELETE /{id} -> measurements.delete
            res = await ac.delete(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code == 403

            # 6. PUT /{id}/status -> measurements.edit
            res = await ac.put(f"/api/v1/measurements/{meas_id}/status", headers=headers, json={"status": "SUBMITTED"})
            assert res.status_code == 403


@pytest.mark.asyncio
async def test_batch_n_dynamic_db_role_permission_lifecycle():
    """Verify DB-driven RBAC: 403 -> Grant DB perm -> 200/204 -> Revoke DB perm -> 403."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_id = data["role_custom"].id
        perms = data["perms"]
        meas_id = data["meas_a1"].id
        proj_id = data["proj_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Initially 403 on measurements.view
            res = await ac.get(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code == 403

            # Dynamically grant measurements.view
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["role_custom"].name, role_id=role_id, permission_id=perms["measurements.view"].id))
                await db.commit()

            # Now 200 on get and project listing
            res = await ac.get(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code == 200
            res_list = await ac.get(f"/api/v1/measurements/project/{proj_id}", headers=headers)
            assert res_list.status_code == 200

            # But still 403 on measurements.create
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={"project_id": proj_id, "final_area": 10.0, "approved_rate": 50.0},
            )
            assert res_create.status_code == 403

            # Dynamically revoke measurements.view
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(RolePermission).where(
                        RolePermission.role_id == role_id,
                        RolePermission.permission_id == perms["measurements.view"].id,
                    )
                )
                await db.commit()

            # Now 403 again
            res_after = await ac.get(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res_after.status_code == 403


@pytest.mark.asyncio
async def test_batch_n_user_permission_overrides():
    """Verify user overrides: positive override grants access; negative override denies access."""
    async with setup_batch_n_data() as data:
        user = data["user_custom_a"]
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        perms = data["perms"]
        meas_id = data["meas_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # User has no role permission for measurements.view -> 403
            res = await ac.get(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res.status_code == 403

            # 1. POSITIVE OVERRIDE: grant measurements.view directly to user
            async with AsyncSessionLocal() as db:
                db.add(UserPermissionOverride(user_id=user.id, permission_id=perms["measurements.view"].id, is_granted=True))
                await db.commit()

            res_granted = await ac.get(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res_granted.status_code == 200

            # 2. NEGATIVE OVERRIDE: give role permission, but explicitly deny user
            async with AsyncSessionLocal() as db:
                db.add(RolePermission(role=data["role_custom"].name, role_id=data["role_custom"].id, permission_id=perms["measurements.view"].id))
                await db.execute(
                    delete(UserPermissionOverride).where(
                        UserPermissionOverride.user_id == user.id,
                        UserPermissionOverride.permission_id == perms["measurements.view"].id,
                    )
                )
                db.add(UserPermissionOverride(user_id=user.id, permission_id=perms["measurements.view"].id, is_granted=False))
                await db.commit()

            # Role has permission, but user override denies -> 403
            res_denied = await ac.get(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res_denied.status_code == 403


@pytest.mark.asyncio
async def test_batch_n_wildcard_permission():
    """measurements.* grants full access across all 6 Final Measurement endpoints."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["user_custom_a"]
        headers = {"Authorization": f"Bearer {token}"}
        role_id = data["role_custom"].id
        perms = data["perms"]
        proj_id = data["proj_a1"].id

        # Grant wildcard measurements.* to custom role
        async with AsyncSessionLocal() as db:
            db.add(RolePermission(role=data["role_custom"].name, role_id=role_id, permission_id=perms["measurements.*"].id))
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={"project_id": proj_id, "final_area": 12.0, "approved_rate": 50.0},
            )
            assert res_create.status_code == 200
            new_id = res_create.json()["id"]

            # 2. List by project
            res_list = await ac.get(f"/api/v1/measurements/project/{proj_id}", headers=headers)
            assert res_list.status_code == 200

            # 3. Get Detail
            res_get = await ac.get(f"/api/v1/measurements/{new_id}", headers=headers)
            assert res_get.status_code == 200

            # 4. Update
            res_update = await ac.put(f"/api/v1/measurements/{new_id}", headers=headers, json={"final_area": 14.0})
            assert res_update.status_code == 200

            # 5. Update Status
            res_status = await ac.put(f"/api/v1/measurements/{new_id}/status", headers=headers, json={"status": "SUBMITTED"})
            assert res_status.status_code == 200

            # Reset status back to DRAFT to allow delete
            async with AsyncSessionLocal() as db:
                m = await db.get(FinalMeasurement, new_id)
                m.status = "DRAFT"
                await db.commit()

            # 6. Delete
            res_del = await ac.delete(f"/api/v1/measurements/{new_id}", headers=headers)
            assert res_del.status_code == 204


@pytest.mark.asyncio
async def test_batch_n_immunity_to_legacy_role_names():
    """A user with role='Admin' or 'Project Manager' but 0 DB permissions is denied (403)."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["legacy_admin_no_perm"]
        headers = {"Authorization": f"Bearer {token}"}
        meas_id = data["meas_a1"].id
        proj_id = data["proj_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res1 = await ac.get(f"/api/v1/measurements/{meas_id}", headers=headers)
            assert res1.status_code == 403

            res2 = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={"project_id": proj_id, "final_area": 10.0, "approved_rate": 50.0},
            )
            assert res2.status_code == 403


@pytest.mark.asyncio
async def test_batch_n_cross_tenant_idor_isolation():
    """Tenant A Admin cannot access, list, create, update, or delete Tenant B Measurement (masked 404)."""
    async with setup_batch_n_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        meas_b_id = data["meas_b1"].id
        proj_b_id = data["proj_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Detail: Tenant A querying Tenant B measurement -> 404
            res_get = await ac.get(f"/api/v1/measurements/{meas_b_id}", headers=headers_a)
            assert res_get.status_code == 404

            # 2. List by project: Tenant A querying Tenant B project -> 404
            res_list = await ac.get(f"/api/v1/measurements/project/{proj_b_id}", headers=headers_a)
            assert res_list.status_code == 404

            # 3. Create: Tenant A creating measurement in Tenant B project -> 404
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers_a,
                json={"project_id": proj_b_id, "final_area": 10.0, "approved_rate": 50.0},
            )
            assert res_create.status_code == 404

            # 4. Update: Tenant A updating Tenant B measurement -> 404
            res_update = await ac.put(
                f"/api/v1/measurements/{meas_b_id}",
                headers=headers_a,
                json={"final_area": 25.0},
            )
            assert res_update.status_code == 404

            # 5. Status: Tenant A updating status of Tenant B measurement -> 404
            res_status = await ac.put(
                f"/api/v1/measurements/{meas_b_id}/status",
                headers=headers_a,
                json={"status": "SUBMITTED"},
            )
            assert res_status.status_code == 404

            # 6. Delete: Tenant A deleting Tenant B measurement -> 404
            res_del = await ac.delete(f"/api/v1/measurements/{meas_b_id}", headers=headers_a)
            assert res_del.status_code == 404


@pytest.mark.asyncio
async def test_batch_n_cross_project_boq_item_injection():
    """Attaching a foreign project's BOQ item to a measurement returns masked 404."""
    async with setup_batch_n_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        proj_a1 = data["proj_a1"].id
        boq_b1_id = data["boq_b1"].id  # Belongs to Project B1
        meas_a1_id = data["meas_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. On Create
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers_a,
                json={
                    "project_id": proj_a1,
                    "boq_item_id": boq_b1_id,
                    "final_area": 10.0,
                    "approved_rate": 50.0,
                },
            )
            assert res_create.status_code == 404

            # 2. On Update
            res_update = await ac.put(
                f"/api/v1/measurements/{meas_a1_id}",
                headers=headers_a,
                json={"boq_item_id": boq_b1_id},
            )
            assert res_update.status_code == 404


@pytest.mark.asyncio
async def test_batch_n_super_admin_cross_company_access():
    """Super Admin can list, view, update, and manage measurements across all companies."""
    async with setup_batch_n_data() as data:
        token_sa = data["tokens"]["super_admin"]
        headers_sa = {"Authorization": f"Bearer {token_sa}"}
        meas_a_id = data["meas_a1"].id
        meas_b_id = data["meas_b1"].id
        proj_a_id = data["proj_a1"].id
        proj_b_id = data["proj_b1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # SA can view Company A measurement
            res_a = await ac.get(f"/api/v1/measurements/{meas_a_id}", headers=headers_sa)
            assert res_a.status_code == 200

            # SA can view Company B measurement
            res_b = await ac.get(f"/api/v1/measurements/{meas_b_id}", headers=headers_sa)
            assert res_b.status_code == 200

            # SA can list Company A project measurements
            res_list_a = await ac.get(f"/api/v1/measurements/project/{proj_a_id}", headers=headers_sa)
            assert res_list_a.status_code == 200

            # SA can list Company B project measurements
            res_list_b = await ac.get(f"/api/v1/measurements/project/{proj_b_id}", headers=headers_sa)
            assert res_list_b.status_code == 200

            # SA can create measurement in Company B project
            res_create = await ac.post(
                "/api/v1/measurements",
                headers=headers_sa,
                json={"project_id": proj_b_id, "final_area": 15.0, "approved_rate": 80.0},
            )
            assert res_create.status_code == 200
            new_sa_meas_id = res_create.json()["id"]

            # SA can delete created measurement
            res_del = await ac.delete(f"/api/v1/measurements/{new_sa_meas_id}", headers=headers_sa)
            assert res_del.status_code == 204


@pytest.mark.asyncio
async def test_batch_n_unassigned_and_none_company_user_isolation():
    """Users with company_id=None (non-super-admin) receive 403 / 404 isolation."""
    async with setup_batch_n_data() as data:
        token_none = data["tokens"]["dummy_none_company_user"]
        headers_none = {"Authorization": f"Bearer {token_none}"}
        meas_id = data["meas_a1"].id
        proj_id = data["proj_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res1 = await ac.get(f"/api/v1/measurements/{meas_id}", headers=headers_none)
            assert res1.status_code in [403, 404]

            res2 = await ac.get(f"/api/v1/measurements/project/{proj_id}", headers=headers_none)
            assert res2.status_code in [403, 404]


@pytest.mark.asyncio
async def test_batch_n_business_status_guards():
    """Measurements not in DRAFT or REJECTED status cannot be updated or deleted (400/422)."""
    async with setup_batch_n_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        meas_submitted = data["meas_a2_submitted"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Try to edit SUBMITTED measurement
            res_edit = await ac.put(
                f"/api/v1/measurements/{meas_submitted}",
                headers=headers_a,
                json={"final_area": 99.0},
            )
            assert res_edit.status_code in [400, 422]

            # Try to delete SUBMITTED measurement
            res_del = await ac.delete(f"/api/v1/measurements/{meas_submitted}", headers=headers_a)
            assert res_del.status_code in [400, 422]


@pytest.mark.asyncio
async def test_batch_n_invoice_lock():
    """Measurements linked to an invoice cannot be updated or deleted (400/422)."""
    async with setup_batch_n_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        meas_invoiced = data["meas_a3_invoiced"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Try to edit invoiced measurement
            res_edit = await ac.put(
                f"/api/v1/measurements/{meas_invoiced}",
                headers=headers_a,
                json={"final_area": 50.0},
            )
            assert res_edit.status_code in [400, 422]

            # Try to delete invoiced measurement
            res_del = await ac.delete(f"/api/v1/measurements/{meas_invoiced}", headers=headers_a)
            assert res_del.status_code in [400, 422]


@pytest.mark.asyncio
async def test_batch_n_boq_quantity_cap():
    """Measured quantity cannot exceed available BOQ quantity (400/422)."""
    async with setup_batch_n_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        proj_id = data["proj_a1"].id
        boq_id = data["boq_a1"].id  # Quantity is 100.000; meas_a1, meas_a2, meas_a3 already take 10+15+20=45

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Try to create with measured_qty=60.0 (45 + 60 = 105 > 100)
            res = await ac.post(
                "/api/v1/measurements",
                headers=headers_a,
                json={
                    "project_id": proj_id,
                    "boq_item_id": boq_id,
                    "measured_qty": 60.0,
                    "final_area": 60.0,
                    "approved_rate": 50.0,
                },
            )
            assert res.status_code in [400, 422]


@pytest.mark.asyncio
async def test_batch_n_direct_approved_rejected_status_transition_rejection():
    """Status endpoint blocks direct manual transition to APPROVED or REJECTED (400/422)."""
    async with setup_batch_n_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        meas_id = data["meas_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Try to set APPROVED directly
            res1 = await ac.put(f"/api/v1/measurements/{meas_id}/status", headers=headers_a, json={"status": "APPROVED"})
            assert res1.status_code in [400, 422]

            # Try to set REJECTED directly
            res2 = await ac.put(f"/api/v1/measurements/{meas_id}/status", headers=headers_a, json={"status": "REJECTED"})
            assert res2.status_code in [400, 422]

            # Try invalid status
            res3 = await ac.put(f"/api/v1/measurements/{meas_id}/status", headers=headers_a, json={"status": "INVALID_STATUS"})
            assert res3.status_code in [400, 422]

            # Valid transition to SUBMITTED succeeds
            res4 = await ac.put(f"/api/v1/measurements/{meas_id}/status", headers=headers_a, json={"status": "SUBMITTED"})
            assert res4.status_code == 200
            assert res4.json()["status"] == "SUBMITTED"


@pytest.mark.asyncio
async def test_batch_n_db_exception_masking():
    """Verify internal database exceptions do not leak raw SQL / trace details to clients."""
    from unittest.mock import patch
    from sqlalchemy.exc import OperationalError

    async with setup_batch_n_data() as data:
        token_a = data["tokens"]["admin_a"]
        headers_a = {"Authorization": f"Bearer {token_a}"}
        meas_id = data["meas_a1"].id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("sqlalchemy.ext.asyncio.AsyncSession.commit", side_effect=OperationalError("SELECT secret_col FROM table", params={}, orig=Exception("Fatal internal DB failure"))):
                res = await ac.put(f"/api/v1/measurements/{meas_id}", headers=headers_a, json={"final_area": 99.0})
                assert res.status_code == 500
                detail = res.json().get("detail", "")
                assert "SELECT" not in detail
                assert "secret_col" not in detail
                assert "Fatal internal DB failure" not in detail
                assert detail == "An internal error occurred while updating measurement"

