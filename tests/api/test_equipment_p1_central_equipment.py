import asyncio
import uuid
import pytest
from datetime import date, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main
from app.main import app
from app.core.db import AsyncSessionLocal
from app.core.dependencies import get_current_user, get_current_active_user
from app.models.user import User
from app.models.project import Project
from app.models.equipment import (
    Equipment,
    EquipmentUsage,
    EquipmentMaintenance,
    EquipmentAuditLog,
)
from app.core.enums import EquipmentStatus, EquipmentCondition

client = TestClient(app)


def override_user(user: User | None):
    if user is None:
        app.dependency_overrides.clear()
    else:
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_current_active_user] = lambda: user


@pytest.fixture(autouse=True)
def cleanup_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def seed_p1_test_data():
    """
    Seeds isolated test data for Company A (id=1) and Company B (id=2),
    including central unallocated equipment (project_id=None) and project-assigned equipment.
    """
    async def _setup():
        async with AsyncSessionLocal() as db:
            user_a = await db.scalar(select(User).where(User.company_id == 1, User.role == "Admin").limit(1))
            user_b = await db.scalar(select(User).where(User.company_id == 2, User.role == "Admin").limit(1))
            user_super = await db.scalar(select(User).where(User.is_super_admin == True).limit(1))
            user_labour = await db.scalar(select(User).where(User.company_id == 1, User.role == "Labour").limit(1))

            if not user_a or not user_b or not user_super:
                raise RuntimeError("Required test users not found in DB")

            # Projects for Company A and B
            proj_a = await db.scalar(select(Project).where(Project.company_id == 1).limit(1))
            if not proj_a:
                proj_a = Project(company_id=1, project_name=f"Proj A P1 {uuid.uuid4().hex[:6]}", status="IN_PROGRESS")
                db.add(proj_a)
                await db.flush()

            proj_b = await db.scalar(select(Project).where(Project.company_id == 2).limit(1))
            if not proj_b:
                proj_b = Project(company_id=2, project_name=f"Proj B P1 {uuid.uuid4().hex[:6]}", status="IN_PROGRESS")
                db.add(proj_b)
                await db.flush()

            tag_a = uuid.uuid4().hex[:6]
            tag_b = uuid.uuid4().hex[:6]

            # 1. Company A Central Equipment (project_id=None)
            central_eq_a = Equipment(
                company_id=1,
                project_id=None,
                equipment_name=f"Company A Central Crane {tag_a}",
                equipment_code=f"EQ-A-CENTRAL-{tag_a}",
                status=EquipmentStatus.AVAILABLE,
                condition=EquipmentCondition.GOOD,
                working_hours=Decimal("20.0"),
                fuel_used=Decimal("40.0"),
            )
            db.add(central_eq_a)

            # 2. Company A Project-Assigned Equipment (project_id=proj_a.id)
            project_eq_a = Equipment(
                company_id=1,
                project_id=proj_a.id,
                equipment_name=f"Company A Project Dozer {tag_a}",
                equipment_code=f"EQ-A-PROJ-{tag_a}",
                status=EquipmentStatus.IN_PROJECT,
                condition=EquipmentCondition.GOOD,
                working_hours=Decimal("50.0"),
                fuel_used=Decimal("100.0"),
            )
            db.add(project_eq_a)

            # 3. Company B Central Equipment (project_id=None)
            central_eq_b = Equipment(
                company_id=2,
                project_id=None,
                equipment_name=f"Company B Central Excavator {tag_b}",
                equipment_code=f"EQ-B-CENTRAL-{tag_b}",
                status=EquipmentStatus.AVAILABLE,
                condition=EquipmentCondition.GOOD,
                working_hours=Decimal("30.0"),
                fuel_used=Decimal("60.0"),
            )
            db.add(central_eq_b)

            # 4. Company B Project-Assigned Equipment (project_id=proj_b.id)
            project_eq_b = Equipment(
                company_id=2,
                project_id=proj_b.id,
                equipment_name=f"Company B Project Roller {tag_b}",
                equipment_code=f"EQ-B-PROJ-{tag_b}",
                status=EquipmentStatus.IN_PROJECT,
                condition=EquipmentCondition.GOOD,
                working_hours=Decimal("40.0"),
                fuel_used=Decimal("80.0"),
            )
            db.add(project_eq_b)
            await db.flush()

            # Usages
            usage_central_a = EquipmentUsage(
                equipment_id=central_eq_a.id,
                working_hours=Decimal("6.0"),
                fuel_used=Decimal("12.0"),
                usage_date=date.today() - timedelta(days=1),
                notes=f"PROBE-USAGE-A-CENTRAL-{tag_a}",
            )
            usage_proj_a = EquipmentUsage(
                equipment_id=project_eq_a.id,
                working_hours=Decimal("8.0"),
                fuel_used=Decimal("16.0"),
                usage_date=date.today() - timedelta(days=1),
                notes=f"PROBE-USAGE-A-PROJ-{tag_a}",
            )
            usage_central_b = EquipmentUsage(
                equipment_id=central_eq_b.id,
                working_hours=Decimal("10.0"),
                fuel_used=Decimal("20.0"),
                usage_date=date.today() - timedelta(days=1),
                notes=f"PROBE-USAGE-B-CENTRAL-{tag_b}",
            )
            usage_proj_b = EquipmentUsage(
                equipment_id=project_eq_b.id,
                working_hours=Decimal("12.0"),
                fuel_used=Decimal("24.0"),
                usage_date=date.today() - timedelta(days=1),
                notes=f"PROBE-USAGE-B-PROJ-{tag_b}",
            )
            db.add_all([usage_central_a, usage_proj_a, usage_central_b, usage_proj_b])

            # Maintenances (upcoming within 5 days)
            maint_central_a = EquipmentMaintenance(
                equipment_id=central_eq_a.id,
                project_id=proj_a.id,
                description=f"PROBE-MAINT-A-CENTRAL-{tag_a}",
                maintenance_date=date.today() - timedelta(days=10),
                next_maintenance_date=date.today() + timedelta(days=4),
                cost=Decimal("500.00"),
                is_completed=False,
            )
            maint_proj_a = EquipmentMaintenance(
                equipment_id=project_eq_a.id,
                project_id=proj_a.id,
                description=f"PROBE-MAINT-A-PROJ-{tag_a}",
                maintenance_date=date.today() - timedelta(days=10),
                next_maintenance_date=date.today() + timedelta(days=6),
                cost=Decimal("600.00"),
                is_completed=False,
            )
            maint_central_b = EquipmentMaintenance(
                equipment_id=central_eq_b.id,
                project_id=proj_b.id,
                description=f"PROBE-MAINT-B-CENTRAL-{tag_b}",
                maintenance_date=date.today() - timedelta(days=10),
                next_maintenance_date=date.today() + timedelta(days=3),
                cost=Decimal("700.00"),
                is_completed=False,
            )
            db.add_all([maint_central_a, maint_proj_a, maint_central_b])

            # Transfer Audit Logs
            # Historical transfer for Company A central equipment (previously transferred between projects, now deallocated project_id=None)
            transfer_log_a = EquipmentAuditLog(
                equipment_id=central_eq_a.id,
                action="TRANSFER",
                old_values={"project_id": proj_a.id},
                new_values={"project_id": None},
                user_id=user_a.id,
            )
            # Transfer log for Company A project equipment
            transfer_log_proj_a = EquipmentAuditLog(
                equipment_id=project_eq_a.id,
                action="TRANSFER",
                old_values={"project_id": None},
                new_values={"project_id": proj_a.id},
                user_id=user_a.id,
            )
            # Transfer log for Company B central equipment
            transfer_log_b = EquipmentAuditLog(
                equipment_id=central_eq_b.id,
                action="TRANSFER",
                old_values={"project_id": proj_b.id},
                new_values={"project_id": None},
                user_id=user_b.id,
            )
            db.add_all([transfer_log_a, transfer_log_proj_a, transfer_log_b])

            await db.commit()

            return {
                "user_a": user_a,
                "user_b": user_b,
                "user_super": user_super,
                "user_labour": user_labour,
                "proj_a": proj_a,
                "proj_b": proj_b,
                "central_eq_a": central_eq_a,
                "project_eq_a": project_eq_a,
                "central_eq_b": central_eq_b,
                "project_eq_b": project_eq_b,
                "transfer_log_a": transfer_log_a,
                "tag_a": tag_a,
                "tag_b": tag_b,
            }

    return asyncio.run(_setup())


def test_central_equipment_usage_report(seed_p1_test_data):
    """
    Test 1 — Central Equipment Usage Report:
    Verifies that Company A central equipment (project_id=None) usage appears in
    GET /api/v1/equipment/usage/report, while Company B usage is excluded.
    """
    data = seed_p1_test_data
    override_user(data["user_a"])

    # 1. Global usage report
    resp = client.get("/api/v1/equipment/usage/report")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    items = resp.json()
    assert isinstance(items, list)

    returned_eq_ids = {row["equipment_id"] for row in items}
    assert data["central_eq_a"].id in returned_eq_ids, (
        f"Central equipment {data['central_eq_a'].id} missing from usage report: {returned_eq_ids}"
    )
    assert data["central_eq_b"].id not in returned_eq_ids, (
        f"Cross-tenant leak: Company B central equipment {data['central_eq_b'].id} found in Company A report"
    )

    # 2. Filter by single central equipment_id
    resp_filtered = client.get(f"/api/v1/equipment/usage/report?equipment_id={data['central_eq_a'].id}")
    assert resp_filtered.status_code == 200
    filtered_items = resp_filtered.json()
    assert len(filtered_items) == 1
    assert filtered_items[0]["equipment_id"] == data["central_eq_a"].id

    # 3. Filter by Company B equipment_id -> must be 404
    resp_cross = client.get(f"/api/v1/equipment/usage/report?equipment_id={data['central_eq_b'].id}")
    assert resp_cross.status_code == 404, f"Expected 404 IDOR block, got {resp_cross.status_code}"


def test_central_equipment_maintenance_alert(seed_p1_test_data):
    """
    Test 2 — Central Equipment Maintenance Alert:
    Verifies that Company A central equipment (project_id=None) maintenance alert appears
    in GET /api/v1/equipment/alerts/maintenance, while Company B records remain excluded.
    """
    data = seed_p1_test_data
    override_user(data["user_a"])

    # 1. Global maintenance alerts
    resp = client.get("/api/v1/equipment/alerts/maintenance?days_ahead=30")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    alerts = resp.json()
    assert isinstance(alerts, list)

    alert_eq_ids = {a["equipment_id"] for a in alerts}
    assert data["central_eq_a"].id in alert_eq_ids, (
        f"Central equipment {data['central_eq_a'].id} missing from maintenance alerts: {alert_eq_ids}"
    )
    assert data["central_eq_b"].id not in alert_eq_ids, (
        f"Cross-tenant leak: Company B central equipment {data['central_eq_b'].id} found in Company A alerts"
    )

    # 2. Filter by single central equipment_id
    resp_filtered = client.get(f"/api/v1/equipment/alerts/maintenance?equipment_id={data['central_eq_a'].id}")
    assert resp_filtered.status_code == 200
    filtered_alerts = resp_filtered.json()
    assert len(filtered_alerts) >= 1
    assert filtered_alerts[0]["equipment_id"] == data["central_eq_a"].id

    # 3. Filter by Company B equipment_id -> 404 IDOR block
    resp_cross = client.get(f"/api/v1/equipment/alerts/maintenance?equipment_id={data['central_eq_b'].id}")
    assert resp_cross.status_code == 404, f"Expected 404 IDOR block, got {resp_cross.status_code}"


def test_historical_transfer_after_deallocation(seed_p1_test_data):
    """
    Test 3 — Historical Transfer After Deallocation:
    Proves that when an equipment is currently deallocated (project_id=None), its historical
    transfer records are returned in both:
      - GET /api/v1/equipment/transfer-history
      - GET /api/v1/equipment/{equipment_id}/transfer-history
    and does not return an incorrect 404.
    """
    data = seed_p1_test_data
    override_user(data["user_a"])

    # 1. Global transfer history
    resp_global = client.get("/api/v1/equipment/transfer-history")
    assert resp_global.status_code == 200, f"Expected 200, got {resp_global.status_code}: {resp_global.text}"
    items = resp_global.json().get("items", [])
    transfer_eq_ids = {t["equipment_id"] for t in items}
    assert data["central_eq_a"].id in transfer_eq_ids, (
        f"Historical transfer for deallocated equipment {data['central_eq_a'].id} missing from global transfer-history"
    )

    # 2. Query param: /transfer-history?equipment_id={id}
    resp_param = client.get(f"/api/v1/equipment/transfer-history?equipment_id={data['central_eq_a'].id}")
    assert resp_param.status_code == 200, f"Expected 200, got {resp_param.status_code}: {resp_param.text}"
    items_param = resp_param.json().get("items", [])
    assert len(items_param) >= 1
    assert items_param[0]["equipment_id"] == data["central_eq_a"].id

    # 3. Single equipment route: /{equipment_id}/transfer-history
    resp_single = client.get(f"/api/v1/equipment/{data['central_eq_a'].id}/transfer-history")
    assert resp_single.status_code == 200, (
        f"Expected 200 (not 404) for legitimate central equipment: {resp_single.status_code}: {resp_single.text}"
    )
    items_single = resp_single.json().get("items", [])
    assert len(items_single) >= 1
    assert items_single[0]["equipment_id"] == data["central_eq_a"].id


def test_cross_tenant_isolation(seed_p1_test_data):
    """
    Test 4 — Cross-Tenant Isolation:
    Proves that Company A sees its own central records, and Company B central records
    are completely ABSENT across all three endpoints. Also verifies foreign IDs are blocked.
    """
    data = seed_p1_test_data
    override_user(data["user_a"])

    # Usage report
    resp_usage = client.get("/api/v1/equipment/usage/report")
    assert resp_usage.status_code == 200
    usage_eq_ids = {u["equipment_id"] for u in resp_usage.json()}
    assert data["central_eq_b"].id not in usage_eq_ids
    assert data["project_eq_b"].id not in usage_eq_ids

    # Maintenance alerts
    resp_alerts = client.get("/api/v1/equipment/alerts/maintenance")
    assert resp_alerts.status_code == 200
    alert_eq_ids = {a["equipment_id"] for a in resp_alerts.json()}
    assert data["central_eq_b"].id not in alert_eq_ids
    assert data["project_eq_b"].id not in alert_eq_ids

    # Transfer history
    resp_transfer = client.get("/api/v1/equipment/transfer-history")
    assert resp_transfer.status_code == 200
    transfer_eq_ids = {t["equipment_id"] for t in resp_transfer.json().get("items", [])}
    assert data["central_eq_b"].id not in transfer_eq_ids
    assert data["project_eq_b"].id not in transfer_eq_ids

    # Single equipment foreign IDOR checks -> 404
    assert client.get(f"/api/v1/equipment/{data['central_eq_b'].id}/transfer-history").status_code == 404
    assert client.get(f"/api/v1/equipment/transfer-history?equipment_id={data['central_eq_b'].id}").status_code == 404
    assert client.get(f"/api/v1/equipment/usage/report?equipment_id={data['central_eq_b'].id}").status_code == 404
    assert client.get(f"/api/v1/equipment/alerts/maintenance?equipment_id={data['central_eq_b'].id}").status_code == 404


def test_existing_project_assigned_equipment_regression(seed_p1_test_data):
    """
    Test 5 — Existing Project-Assigned Equipment Regression:
    Verifies that project-assigned Company A equipment (project_id != None) continues
    to appear correctly in usage report, maintenance alerts, and transfer history.
    """
    data = seed_p1_test_data
    override_user(data["user_a"])

    # Usage report has project-assigned equipment
    resp_usage = client.get("/api/v1/equipment/usage/report")
    assert resp_usage.status_code == 200
    usage_eq_ids = {u["equipment_id"] for u in resp_usage.json()}
    assert data["project_eq_a"].id in usage_eq_ids, "Project-assigned equipment missing from usage report"

    # Maintenance alerts has project-assigned equipment
    resp_alerts = client.get("/api/v1/equipment/alerts/maintenance?days_ahead=30")
    assert resp_alerts.status_code == 200
    alert_eq_ids = {a["equipment_id"] for a in resp_alerts.json()}
    assert data["project_eq_a"].id in alert_eq_ids, "Project-assigned equipment missing from maintenance alerts"

    # Transfer history has project-assigned equipment
    resp_transfer = client.get("/api/v1/equipment/transfer-history")
    assert resp_transfer.status_code == 200
    transfer_eq_ids = {t["equipment_id"] for t in resp_transfer.json().get("items", [])}
    assert data["project_eq_a"].id in transfer_eq_ids, "Project-assigned equipment missing from transfer history"


def test_rbac_regression(seed_p1_test_data):
    """
    Test 6 — RBAC Regression:
    Verifies 401 for unauthenticated calls, 403 for authenticated users lacking
    equipment.view permission, and 200 for authorized users.
    """
    data = seed_p1_test_data

    endpoints = [
        "/api/v1/equipment/usage/report",
        "/api/v1/equipment/alerts/maintenance",
        "/api/v1/equipment/transfer-history",
    ]

    # 1. Unauthenticated -> 401
    override_user(None)
    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 401, f"Expected 401 for unauthenticated {ep}, got {resp.status_code}"

    # 2. Authenticated lacking equipment.view -> 403
    if data["user_labour"]:
        override_user(data["user_labour"])
        for ep in endpoints:
            resp = client.get(ep)
            assert resp.status_code == 403, f"Expected 403 for unauthorized {ep}, got {resp.status_code}"

    # 3. Authorized -> 200
    override_user(data["user_a"])
    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 200, f"Expected 200 for authorized {ep}, got {resp.status_code}"


def test_superadmin_regression(seed_p1_test_data):
    """
    Test 7 — SuperAdmin Regression:
    Verifies that SuperAdmin (current_user.company_id is None) preserves existing
    safe empty returns without crashing or leaking cross-tenant records.
    """
    data = seed_p1_test_data
    override_user(data["user_super"])

    # Usage report -> returns []
    resp_usage = client.get("/api/v1/equipment/usage/report")
    assert resp_usage.status_code == 200
    assert resp_usage.json() == []

    # Maintenance alerts -> returns []
    resp_alerts = client.get("/api/v1/equipment/alerts/maintenance")
    assert resp_alerts.status_code == 200
    assert resp_alerts.json() == []

    # Transfer history -> returns empty items
    resp_transfer = client.get("/api/v1/equipment/transfer-history")
    assert resp_transfer.status_code == 200
    data_transfer = resp_transfer.json()
    assert data_transfer.get("items") == []
    assert data_transfer.get("meta", {}).get("total") == 0
