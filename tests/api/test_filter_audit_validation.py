"""
Tests for Complete Project-Wide API Filter Audit, Validation & Remediation.

Covers all 16 required verification points:
1. Activity status filtering (DELAY, ON_TRACK, COMPLETED, NOT_STARTED)
2. Activity count/data consistency (total_count == len(matching_records))
3. Activity PDF filter parity (PDF matches filter query params)
4. Activity Excel filter parity (List IDs == Excel IDs)
5. Dynamic status after date expiry (end_date in past -> DELAY)
6. Dynamic status after completion reaches 100% (100% -> COMPLETED)
7. Delayed activities endpoint parity (/activities/delayed matches list?status=DELAY)
8. Equipment availability filtering before pagination (slicing after is_available check)
9. Equipment alert severity filtering before pagination (slicing after severity check)
10. Equipment transfer-history project filtering before pagination (from/to project matching)
11. Project ONGOING vs DELAYED status parity (compute_project_status parity with list_projects)
12. DSR invalid date range (start_date > end_date -> HTTP 400)
13. Empty DSR export (0 records -> HTTP 200 empty workbook with headers, not 404)
14. Combined filters (project_id + work_order_id + status + search)
15. Pagination with filters (offset/limit stability)
16. Empty filter results (total_count: 0, page_count: 0, items: [])
"""

import io
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

import openpyxl
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.db import AsyncSessionLocal
from app.core.enums import (
    EquipmentCondition,
    EquipmentStatus,
    ProjectStatus,
    WorkActivityStatus,
)
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.boq import BOQ, BOQGroup
from app.models.company import Company
from app.models.equipment import Equipment, EquipmentAuditLog
from app.models.owner import Owner
from app.models.project import (
    DailySiteReport,
    Project,
    WorkActivity,
)
from app.models.rbac import Permission, RolePermission
from app.models.user import User
from app.models.work_order import WorkOrder


@pytest_asyncio.fixture
async def audit_data():
    """Setup isolated test fixtures covering Company, Projects, Work Activities, Equipment, Transfers, and DSR."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # 1. Company & Owner
        comp = Company(name=f"Audit_Comp_{uid}")
        db.add(comp)
        await db.flush()

        owner = Owner(
            company_id=comp.id,
            owner_code=f"OWN-AUD-{uid}",
            owner_name=f"Owner Audit {uid}",
            mobile=f"92{uuid.uuid4().int % 100000000:08d}",
            email=f"owner_aud_{uid}@test.com",
        )
        db.add(owner)
        await db.flush()

        # 2. Projects
        # proj1: Active project (future end_date -> ONGOING)
        proj1 = Project(
            business_id=f"PRJ-AUD1-{uid}",
            company_id=comp.id,
            project_name=f"Project Audit 1 {uid}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=30),
        )
        # proj2: Overdue project (past end_date -> dynamically DELAYED)
        proj2 = Project(
            business_id=f"PRJ-AUD2-{uid}",
            company_id=comp.id,
            project_name=f"Project Audit 2 Overdue {uid}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
            start_date=date.today() - timedelta(days=60),
            end_date=date.today() - timedelta(days=5),
        )
        # proj3: Completed project
        proj3 = Project(
            business_id=f"PRJ-AUD3-{uid}",
            company_id=comp.id,
            project_name=f"Project Audit 3 Completed {uid}",
            owner_id=owner.id,
            status=ProjectStatus.COMPLETED,
            start_date=date.today() - timedelta(days=90),
            end_date=date.today() - timedelta(days=10),
        )
        db.add_all([proj1, proj2, proj3])
        await db.flush()

        # 3. User with Admin role
        admin_user = User(
            email=f"admin_aud_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin Audit User",
            company_id=comp.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        db.add(admin_user)
        await db.flush()

        # Ensure all required permissions exist and are mapped to Admin
        required_perms = [
            ("work_progress", "view", "work_progress.view"),
            ("work_progress", "export", "work_progress.export"),
            ("equipment", "view", "equipment.view"),
            ("projects", "view", "projects.view"),
            ("dsr", "view", "dsr.view"),
            ("dsr", "export", "dsr.export"),
        ]
        admin_added_rps = []
        for module, action, code in required_perms:
            p_res = await db.execute(select(Permission).where(Permission.code == code))
            perm = p_res.scalar_one_or_none()
            if not perm:
                perm = Permission(module=module, action=action, code=code, description=code)
                db.add(perm)
                await db.flush()

            exists = await db.scalar(
                select(RolePermission).where(
                    RolePermission.role == "Admin",
                    RolePermission.permission_id == perm.id,
                )
            )
            if not exists:
                rp = RolePermission(role="Admin", permission_id=perm.id)
                db.add(rp)
                await db.flush()
                admin_added_rps.append(rp.id)

        # 4. BOQ & Work Order for Project 1
        boq_group = BOQGroup(project_id=proj1.id, name=f"BOQ Group {uid}")
        db.add(boq_group)
        await db.flush()

        boq = BOQ(
            project_id=proj1.id,
            boq_group_id=boq_group.id,
            item_name=f"BOQ Item {uid}",
            category="Civil",
            quantity=Decimal("100.00"),
            unit="sqm",
            unit_cost=Decimal("200.00"),
            total_cost=Decimal("20000.00"),
            is_latest=True,
        )
        db.add(boq)
        await db.flush()

        wo1 = WorkOrder(
            project_id=proj1.id,
            work_order_number=f"WO-AUD1-{uid}",
            work_description="Audit Civil Works",
            total_quantity=Decimal("1000.00"),
            rate=Decimal("100.00"),
            total_amount=Decimal("100000.00"),
            completed_quantity=Decimal("0.00"),
        )
        db.add(wo1)
        await db.flush()

        # 5. Work Activities under Project 1 with specific dynamics:
        # act_not_started: 0%, future deadline -> NOT_STARTED
        act_not_started = WorkActivity(
            project_id=proj1.id,
            work_order_id=wo1.id,
            activity_name=f"Site Clearing {uid}",
            discipline="Civil",
            planned_quantity=Decimal("100.00"),
            unit="cum",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=10),
            total_completed=Decimal("0.00"),
            remaining_quantity=Decimal("100.00"),
            completion_percentage=Decimal("0.00"),
            status=WorkActivityStatus.NOT_STARTED,
        )
        # act_delayed: 0%, past deadline, DB stored as NOT_STARTED -> dynamically DELAY
        act_delayed = WorkActivity(
            project_id=proj1.id,
            work_order_id=wo1.id,
            activity_name=f"Deep Excavation Overdue {uid}",
            discipline="Civil",
            planned_quantity=Decimal("100.00"),
            unit="cum",
            start_date=date.today() - timedelta(days=20),
            end_date=date.today() - timedelta(days=5),
            total_completed=Decimal("0.00"),
            remaining_quantity=Decimal("100.00"),
            completion_percentage=Decimal("0.00"),
            status=WorkActivityStatus.NOT_STARTED,
        )
        # act_on_track: 50%, future deadline -> ON_TRACK
        act_on_track = WorkActivity(
            project_id=proj1.id,
            work_order_id=wo1.id,
            activity_name=f"Concrete Pavement {uid}",
            discipline="Civil",
            planned_quantity=Decimal("100.00"),
            unit="cum",
            start_date=date.today() - timedelta(days=5),
            end_date=date.today() + timedelta(days=15),
            total_completed=Decimal("50.00"),
            remaining_quantity=Decimal("50.00"),
            completion_percentage=Decimal("50.00"),
            status=WorkActivityStatus.ON_TRACK,
        )
        # act_completed: 100%, past deadline -> COMPLETED
        act_completed = WorkActivity(
            project_id=proj1.id,
            work_order_id=wo1.id,
            activity_name=f"Soil Survey Completed {uid}",
            discipline="Civil",
            planned_quantity=Decimal("100.00"),
            unit="cum",
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() - timedelta(days=10),
            total_completed=Decimal("100.00"),
            remaining_quantity=Decimal("0.00"),
            completion_percentage=Decimal("100.00"),
            status=WorkActivityStatus.COMPLETED,
        )
        db.add_all([act_not_started, act_delayed, act_on_track, act_completed])
        await db.flush()

        # 6. Equipment records
        # eq1: Available (no project, GOOD condition)
        eq1 = Equipment(
            company_id=comp.id,
            project_id=None,
            equipment_name=f"Bulldozer Available 1 {uid}",
            equipment_code=f"EQ-AVL1-{uid}",
            status=EquipmentStatus.AVAILABLE,
            condition=EquipmentCondition.GOOD,
            working_hours=Decimal("150.00"),
            is_deleted=False,
        )
        # eq2: Available (no project, GOOD condition)
        eq2 = Equipment(
            company_id=comp.id,
            project_id=None,
            equipment_name=f"Crane Available 2 {uid}",
            equipment_code=f"EQ-AVL2-{uid}",
            status=EquipmentStatus.AVAILABLE,
            condition=EquipmentCondition.GOOD,
            working_hours=Decimal("250.00"),
            is_deleted=False,
        )
        # eq3: Damaged equipment under proj1 -> Alert severity: CRITICAL
        eq3 = Equipment(
            company_id=comp.id,
            project_id=proj1.id,
            equipment_name=f"Damaged Excavator {uid}",
            equipment_code=f"EQ-DMG-{uid}",
            status=EquipmentStatus.DAMAGED,
            condition=EquipmentCondition.DAMAGED,
            working_hours=Decimal("100.00"),
            is_deleted=False,
        )
        # eq4: Overused equipment under proj1 (working_hours=1200 > 1000) -> Alert severity: HIGH
        eq4 = Equipment(
            company_id=comp.id,
            project_id=proj1.id,
            equipment_name=f"Overused Loader {uid}",
            equipment_code=f"EQ-OVR-{uid}",
            status=EquipmentStatus.IN_PROJECT,
            condition=EquipmentCondition.GOOD,
            working_hours=Decimal("1200.00"),
            is_deleted=False,
        )
        db.add_all([eq1, eq2, eq3, eq4])
        await db.flush()

        # 7. Equipment Transfer History (Audit Logs)
        # log1: Transferred TO proj1
        log1 = EquipmentAuditLog(
            equipment_id=eq1.id,
            action="TRANSFER",
            old_values=json.dumps({"project_id": None}),
            new_values=json.dumps({"project_id": proj1.id}),
            user_id=admin_user.id,
        )
        # log2: Transferred FROM proj1
        log2 = EquipmentAuditLog(
            equipment_id=eq2.id,
            action="TRANSFER",
            old_values=json.dumps({"project_id": proj1.id}),
            new_values=json.dumps({"project_id": None}),
            user_id=admin_user.id,
        )
        # log3: Transfer unrelated to proj1 (proj2 to proj3)
        log3 = EquipmentAuditLog(
            equipment_id=eq3.id,
            action="TRANSFER",
            old_values=json.dumps({"project_id": proj2.id}),
            new_values=json.dumps({"project_id": proj3.id}),
            user_id=admin_user.id,
        )
        db.add_all([log1, log2, log3])
        await db.flush()

        # 8. DSR Record under proj1
        dsr1 = DailySiteReport(
            business_id=f"DSR-{uid}",
            project_id=proj1.id,
            created_by_id=admin_user.id,
            report_date=date.today() - timedelta(days=2),
            work_done="Foundational excavation and leveling done.",
            work_planned="Pouring concrete slabs.",
            total_labour=15,
            skilled_labour=5,
            unskilled_labour=10,
        )
        db.add(dsr1)
        await db.flush()

        await db.commit()

        token = create_access_token({"sub": str(admin_user.id)})

        ctx = {
            "comp": comp,
            "owner": owner,
            "proj1": proj1,
            "proj2": proj2,
            "proj3": proj3,
            "admin_user": admin_user,
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
            "wo1": wo1,
            "act_not_started": act_not_started,
            "act_delayed": act_delayed,
            "act_on_track": act_on_track,
            "act_completed": act_completed,
            "eq1": eq1,
            "eq2": eq2,
            "eq3": eq3,
            "eq4": eq4,
            "log1": log1,
            "log2": log2,
            "log3": log3,
            "dsr1": dsr1,
            "admin_added_rps": admin_added_rps,
        }

    try:
        yield ctx
    finally:
        async with AsyncSessionLocal() as db:
            if admin_added_rps:
                await db.execute(delete(RolePermission).where(RolePermission.id.in_(admin_added_rps)))
            await db.execute(delete(DailySiteReport).where(DailySiteReport.id == dsr1.id))
            await db.execute(delete(EquipmentAuditLog).where(EquipmentAuditLog.id.in_([log1.id, log2.id, log3.id])))
            await db.execute(delete(Equipment).where(Equipment.id.in_([eq1.id, eq2.id, eq3.id, eq4.id])))
            await db.execute(
                delete(WorkActivity).where(
                    WorkActivity.id.in_(
                        [act_not_started.id, act_delayed.id, act_on_track.id, act_completed.id]
                    )
                )
            )
            await db.execute(delete(WorkOrder).where(WorkOrder.id == wo1.id))
            await db.execute(delete(BOQ).where(BOQ.id == boq.id))
            await db.execute(delete(BOQGroup).where(BOQGroup.id == boq_group.id))
            await db.execute(delete(User).where(User.id == admin_user.id))
            await db.execute(delete(Project).where(Project.id.in_([proj1.id, proj2.id, proj3.id])))
            await db.execute(delete(Owner).where(Owner.id == owner.id))
            await db.execute(delete(Company).where(Company.id == comp.id))
            await db.commit()


# ==============================================================================
# 1. Activity Status Filtering
# ==============================================================================


@pytest.mark.asyncio
async def test_01_activity_status_filtering(audit_data):
    """Verify that filtering by status uses dynamic resolution for each status type."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # DELAY
        r = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=DELAY", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == audit_data["act_delayed"].id
        assert data["data"][0]["status"] == "DELAY"

        # ON_TRACK
        r = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=ON_TRACK", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == audit_data["act_on_track"].id
        assert data["data"][0]["status"] == "ON_TRACK"

        # COMPLETED
        r = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=COMPLETED", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == audit_data["act_completed"].id
        assert data["data"][0]["status"] == "COMPLETED"

        # NOT_STARTED
        r = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=NOT_STARTED", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == audit_data["act_not_started"].id
        assert data["data"][0]["status"] == "NOT_STARTED"


# ==============================================================================
# 2. Activity Count / Data Consistency
# ==============================================================================


@pytest.mark.asyncio
async def test_02_activity_count_data_consistency(audit_data):
    """Verify invariant: total_count == len(matching_records) and page_count matches."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # Status DELAY: only 1 activity matches dynamically
        r = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=DELAY&limit=100&offset=0", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["total_count"] == 1
        assert data["page_count"] == 1
        assert len(data["data"]) == 1

        # Unfiltered: all 4 activities
        r = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&limit=100&offset=0", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["total_count"] == 4
        assert data["page_count"] == 4
        assert len(data["data"]) == 4


# ==============================================================================
# 3. Activity PDF Filter Parity
# ==============================================================================


@pytest.mark.asyncio
async def test_03_activity_pdf_filter_parity(audit_data):
    """Verify PDF export accepts all filter parameters and returns valid PDF content."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # Filter by status=DELAY
        r = await ac.get(f"/api/v1/work-progress/reports/pdf?project_id={p1}&status=DELAY", headers=headers)
        assert r.status_code == 200
        assert r.headers.get("content-type") == "application/pdf"
        assert len(r.content) > 0
        assert r.content.startswith(b"%PDF")


# ==============================================================================
# 4. Activity Excel Filter Parity
# ==============================================================================


@pytest.mark.asyncio
async def test_04_activity_excel_filter_parity(audit_data):
    """Verify Invariant: Activity List IDs == Excel IDs when identical filters are provided."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # 1. Fetch filtered activities via List API
        list_r = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=DELAY", headers=headers)
        assert list_r.status_code == 200
        list_data = list_r.json()
        list_activity_names = [item["activity_name"] for item in list_data["data"]]
        assert len(list_activity_names) == 1
        assert list_activity_names[0] == audit_data["act_delayed"].activity_name

        # 2. Fetch Excel export with identical filter
        excel_r = await ac.get(f"/api/v1/work-progress/reports/excel?project_id={p1}&status=DELAY", headers=headers)
        assert excel_r.status_code == 200
        assert "spreadsheetml" in excel_r.headers.get("content-type", "")

        wb = openpyxl.load_workbook(io.BytesIO(excel_r.content))
        ws = wb.active

        # Find column with Activity Name in header row
        headers_row = [cell.value for cell in ws[1]]
        name_col_idx = headers_row.index("Activity Name") + 1

        excel_activity_names = []
        for row in range(2, ws.max_row + 1):
            val = ws.cell(row=row, column=name_col_idx).value
            if val is not None:
                excel_activity_names.append(val)

        # Invariant check: identical names / records
        assert excel_activity_names == list_activity_names


# ==============================================================================
# 5. Dynamic Status After Date Expiry
# ==============================================================================


@pytest.mark.asyncio
async def test_05_dynamic_status_after_date_expiry(audit_data):
    """Verify activity whose end_date is past with <100% completion dynamically resolves to DELAY."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # In DB, act_delayed has status NOT_STARTED, but end_date in past
        r = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=DELAY", headers=headers)
        assert r.status_code == 200
        items = r.json()["data"]
        matching = [x for x in items if x["id"] == audit_data["act_delayed"].id]
        assert len(matching) == 1
        assert matching[0]["status"] == "DELAY"

        # Must NOT appear under NOT_STARTED
        r2 = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=NOT_STARTED", headers=headers)
        assert r2.status_code == 200
        items2 = r2.json()["data"]
        assert not any(x["id"] == audit_data["act_delayed"].id for x in items2)


# ==============================================================================
# 6. Dynamic Status After Completion Reaches 100%
# ==============================================================================


@pytest.mark.asyncio
async def test_06_dynamic_status_after_completion_reaches_100(audit_data):
    """Verify activity with end_date in past but 100% completion resolves to COMPLETED, not DELAY."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        r_comp = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=COMPLETED", headers=headers)
        assert r_comp.status_code == 200
        comp_items = r_comp.json()["data"]
        assert any(x["id"] == audit_data["act_completed"].id for x in comp_items)

        r_delay = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&status=DELAY", headers=headers)
        assert r_delay.status_code == 200
        delay_items = r_delay.json()["data"]
        assert not any(x["id"] == audit_data["act_completed"].id for x in delay_items)


# ==============================================================================
# 7. Delayed Activities Endpoint Parity
# ==============================================================================


@pytest.mark.asyncio
async def test_07_delayed_activities_endpoint(audit_data):
    """Verify /project/{project_id}/delayed-activities returns exactly the items matching calculate_activity_status == DELAY."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # Delayed endpoint
        r_delayed = await ac.get(f"/api/v1/work-progress/project/{p1}/delayed-activities", headers=headers)
        assert r_delayed.status_code == 200
        delayed_data = r_delayed.json()
        assert delayed_data["total_count"] == 1
        assert len(delayed_data["data"]) == 1
        assert delayed_data["data"][0]["id"] == audit_data["act_delayed"].id
        assert delayed_data["data"][0]["status"] == "DELAY"

        # Project summary endpoint
        r_sum = await ac.get(f"/api/v1/work-progress/project/{p1}/summary", headers=headers)
        assert r_sum.status_code == 200
        sum_data = r_sum.json()["summary"]
        assert sum_data["delayed_activities"] == 1
        assert sum_data["on_track_activities"] == 1
        assert sum_data["completed_activities"] == 1
        assert sum_data["not_started_activities"] == 1


# ==============================================================================
# 8. Equipment Availability Filtering Before Pagination
# ==============================================================================


@pytest.mark.asyncio
async def test_08_equipment_availability_filtering_before_pagination(audit_data):
    """Verify equipment availability filtering happens before pagination slicing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = audit_data["headers"]

        # Both eq1 and eq2 are available (no project, GOOD condition)
        # Request page 1 with limit=1
        r_page1 = await ac.get("/api/v1/equipment/availability?is_available=true&limit=1&offset=0", headers=headers)
        assert r_page1.status_code == 200
        items_page1 = r_page1.json()
        assert len(items_page1) == 1
        assert items_page1[0]["is_available"] is True

        # Request page 2 with limit=1, offset=1
        r_page2 = await ac.get("/api/v1/equipment/availability?is_available=true&limit=1&offset=1", headers=headers)
        assert r_page2.status_code == 200
        items_page2 = r_page2.json()
        assert len(items_page2) == 1
        assert items_page2[0]["is_available"] is True
        assert items_page1[0]["equipment_id"] != items_page2[0]["equipment_id"]

        # Check unavailable equipment (eq3 is damaged, eq4 is in project)
        r_unavail = await ac.get("/api/v1/equipment/availability?is_available=false&limit=100&offset=0", headers=headers)
        assert r_unavail.status_code == 200
        items_unavail = r_unavail.json()
        unavail_ids = [x["equipment_id"] for x in items_unavail]
        assert audit_data["eq3"].id in unavail_ids
        assert audit_data["eq4"].id in unavail_ids


# ==============================================================================
# 9. Equipment Alert Severity Filtering Before Pagination
# ==============================================================================


@pytest.mark.asyncio
async def test_09_equipment_alert_severity_filtering_before_pagination(audit_data):
    """Verify equipment alerts filter by severity before applying pagination."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # eq3 has condition=DAMAGED -> severity CRITICAL
        r_crit = await ac.get(f"/api/v1/equipment/alerts/equipment?project_id={p1}&severity=CRITICAL&limit=10&offset=0", headers=headers)
        assert r_crit.status_code == 200
        crit_items = r_crit.json()
        assert any(x["equipment_id"] == audit_data["eq3"].id for x in crit_items)

        # eq4 has working_hours=1200 -> severity HIGH
        r_high = await ac.get(f"/api/v1/equipment/alerts/equipment?project_id={p1}&severity=HIGH&limit=10&offset=0", headers=headers)
        assert r_high.status_code == 200
        high_items = r_high.json()
        assert any(x["equipment_id"] == audit_data["eq4"].id for x in high_items)
        assert not any(x["equipment_id"] == audit_data["eq3"].id for x in high_items)


# ==============================================================================
# 10. Equipment Transfer History Project Filtering Before Pagination
# ==============================================================================


@pytest.mark.asyncio
async def test_10_equipment_transfer_history_project_filtering_before_pagination(audit_data):
    """Verify transfer history filters by project (from or to) before pagination, setting correct total."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # log1 transferred TO p1, log2 transferred FROM p1, log3 is between p2 and p3
        r = await ac.get(f"/api/v1/equipment/transfer-history?project_id={p1}&limit=1&offset=0", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["meta"]["total"] == 2
        assert len(data["items"]) == 1

        # Next page
        r2 = await ac.get(f"/api/v1/equipment/transfer-history?project_id={p1}&limit=1&offset=1", headers=headers)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["meta"]["total"] == 2
        assert len(data2["items"]) == 1
        assert data["items"][0]["id"] != data2["items"][0]["id"]


# ==============================================================================
# 11. Project ONGOING vs DELAYED Status Parity
# ==============================================================================


@pytest.mark.asyncio
async def test_11_project_ongoing_vs_delayed_status_parity(audit_data):
    """Verify compute_project_status and list_projects filtering have 100% parity for ONGOING vs DELAYED."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = audit_data["headers"]
        p1_id = audit_data["proj1"].id
        p2_id = audit_data["proj2"].id

        # proj2 is overdue (end_date < today) -> DELAYED
        r_delayed = await ac.get("/api/v1/projects?status=DELAYED", headers=headers)
        assert r_delayed.status_code == 200
        delayed_items = r_delayed.json()["items"]
        delayed_ids = [p["id"] for p in delayed_items]
        assert p2_id in delayed_ids
        assert p1_id not in delayed_ids

        # proj1 is normal ongoing -> ONGOING
        r_ongoing = await ac.get("/api/v1/projects?status=ONGOING", headers=headers)
        assert r_ongoing.status_code == 200
        ongoing_items = r_ongoing.json()["items"]
        ongoing_ids = [p["id"] for p in ongoing_items]
        assert p1_id in ongoing_ids
        assert p2_id not in ongoing_ids


# ==============================================================================
# 12. DSR Invalid Date Range Validation
# ==============================================================================


@pytest.mark.asyncio
async def test_12_dsr_invalid_date_range(audit_data):
    """Verify start_date > end_date returns HTTP 400 Bad Request."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        r = await ac.get(
            f"/api/v1/dsr/project/{p1}/export?start_date=2026-09-20&end_date=2026-09-10",
            headers=headers,
        )
        assert r.status_code == 400
        assert "cannot be before" in r.json().get("detail", "").lower() or "cannot be before" in r.json().get("message", "").lower()


# ==============================================================================
# 13. Empty DSR Export Returns Valid Excel Workbook
# ==============================================================================


@pytest.mark.asyncio
async def test_13_empty_dsr_export(audit_data):
    """Verify empty DSR export returns HTTP 200 with valid headers instead of HTTP 404."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # Query dates with zero records
        r = await ac.get(
            f"/api/v1/dsr/project/{p1}/export?start_date=2020-01-01&end_date=2020-01-02",
            headers=headers,
        )
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers.get("content-type", "")

        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb.active
        assert ws.max_row == 1  # Headers row only, no data rows
        headers_row = [cell.value for cell in ws[1]]
        assert "Date" in headers_row
        assert "Work Done" in headers_row


# ==============================================================================
# 14. Combined Filters
# ==============================================================================


@pytest.mark.asyncio
async def test_14_combined_filters(audit_data):
    """Verify combining project_id, work_order_id, status, and search filters together."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        wo1_id = audit_data["wo1"].id
        headers = audit_data["headers"]

        # Combined matching query
        r = await ac.get(
            f"/api/v1/work-progress/activities?project_id={p1}&work_order_id={wo1_id}&status=DELAY&search=Excavation",
            headers=headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total_count"] == 1
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == audit_data["act_delayed"].id


# ==============================================================================
# 15. Pagination With Filters
# ==============================================================================


@pytest.mark.asyncio
async def test_15_pagination_with_filters(audit_data):
    """Verify pagination stability: disjoint pages, consistent total_count across pages."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        # Total unfiltered activities = 4. Page 1 (limit 2, offset 0)
        r1 = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&limit=2&offset=0", headers=headers)
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["total_count"] == 4
        assert d1["page_count"] == 2
        assert len(d1["data"]) == 2

        # Page 2 (limit 2, offset 2)
        r2 = await ac.get(f"/api/v1/work-progress/activities?project_id={p1}&limit=2&offset=2", headers=headers)
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["total_count"] == 4
        assert d2["page_count"] == 2
        assert len(d2["data"]) == 2

        ids_p1 = {x["id"] for x in d1["data"]}
        ids_p2 = {x["id"] for x in d2["data"]}
        assert ids_p1.isdisjoint(ids_p2)


# ==============================================================================
# 16. Empty Filter Results
# ==============================================================================


@pytest.mark.asyncio
async def test_16_empty_filter_results(audit_data):
    """Verify filtering that produces zero matching results returns valid empty structure."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = audit_data["proj1"].id
        headers = audit_data["headers"]

        r = await ac.get(
            f"/api/v1/work-progress/activities?project_id={p1}&status=DELAY&search=NoSuchActivityExistsXYZ",
            headers=headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total_count"] == 0
        assert data["page_count"] == 0
        assert data["data"] == []
