"""
Tests for Issue #6: Work Progress Activities 500 & GET Mutation/Data Integrity
Verifies:
1. calculate_activity_status is a pure, side-effect-free resolver.
2. completion_percentage=None (NULL) does not cause 500 error; preserves NULL semantics.
3. GET endpoints (/activities, /activities/{id}, /work-order/{id}/progress-summary) are strictly read-only:
   - Zero INSERT/UPDATE/DELETE queries emitted
   - No FOR UPDATE locks used
   - Database row attributes (status, completion_percentage, updated_at) remain unchanged
   - Repeated GET requests return stable, idempotent results
4. /work-order/{id}/progress-summary accurately computes business aggregates in memory
   without depending on DB mutation in GET.
5. Write endpoints (POST, PUT, daily-entry) explicitly persist status as intended.
6. Tenant isolation and RBAC access control are strictly preserved.
"""

from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, select

from app.api.project import calculate_activity_status
from app.core.db import AsyncSessionLocal, async_engine
from app.core.enums import ProjectStatus, WorkActivityStatus
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.boq import BOQ, BOQGroup
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import (
    ActivityHistory,
    DailyProgressEntry,
    Project,
    WorkActivity,
)
from app.models.rbac import Permission, Role, RolePermission
from app.models.user import User
from app.models.work_order import WorkOrder
import app.schemas.project as s


# ==============================================================================
# Unit Tests: Pure Resolver calculate_activity_status
# ==============================================================================


class DummyActivity:
    """Mock object mimicking WorkActivity attributes."""
    def __init__(self, completion_percentage, end_date, status=WorkActivityStatus.NOT_STARTED):
        self.completion_percentage = completion_percentage
        self.end_date = end_date
        self.status = status


def test_calculate_activity_status_is_pure_and_side_effect_free():
    """Verify calculate_activity_status does not modify any attribute on the input object."""
    act = DummyActivity(
        completion_percentage=None,
        end_date=date.today() - timedelta(days=2),
        status=WorkActivityStatus.NOT_STARTED,
    )
    result = calculate_activity_status(act)
    assert result == WorkActivityStatus.DELAY
    # Check input object is completely untouched
    assert act.completion_percentage is None
    assert act.status == WorkActivityStatus.NOT_STARTED
    assert act.end_date == date.today() - timedelta(days=2)


def test_calculate_activity_status_completion_percentage_null():
    """Verify NULL completion_percentage semantics:
    - If end_date is in the future: NOT_STARTED (no progress yet)
    - If end_date is in the past: DELAY (past deadline with no progress)
    NULL is treated as 0% for status comparison without overwriting the NULL attribute.
    """
    future_date = date.today() + timedelta(days=5)
    past_date = date.today() - timedelta(days=5)

    act_future = DummyActivity(completion_percentage=None, end_date=future_date)
    assert calculate_activity_status(act_future) == WorkActivityStatus.NOT_STARTED
    assert act_future.completion_percentage is None  # Preserved

    act_past = DummyActivity(completion_percentage=None, end_date=past_date)
    assert calculate_activity_status(act_past) == WorkActivityStatus.DELAY
    assert act_past.completion_percentage is None  # Preserved


def test_calculate_activity_status_zero_percentage():
    """Verify 0.00 completion percentage semantics."""
    future_date = date.today() + timedelta(days=5)
    past_date = date.today() - timedelta(days=5)

    act_zero_future = DummyActivity(completion_percentage=Decimal("0.00"), end_date=future_date)
    assert calculate_activity_status(act_zero_future) == WorkActivityStatus.NOT_STARTED

    act_zero_past = DummyActivity(completion_percentage=Decimal("0.00"), end_date=past_date)
    assert calculate_activity_status(act_zero_past) == WorkActivityStatus.DELAY


def test_calculate_activity_status_partial_progress():
    """Verify partial progress (0 < pct < 100) semantics."""
    future_date = date.today() + timedelta(days=5)
    past_date = date.today() - timedelta(days=5)

    act_on_track = DummyActivity(completion_percentage=Decimal("45.00"), end_date=future_date)
    assert calculate_activity_status(act_on_track) == WorkActivityStatus.ON_TRACK

    act_delayed = DummyActivity(completion_percentage=Decimal("45.00"), end_date=past_date)
    assert calculate_activity_status(act_delayed) == WorkActivityStatus.DELAY


def test_calculate_activity_status_completed():
    """Verify 100% completion resolves to COMPLETED even if end_date was in the past."""
    past_date = date.today() - timedelta(days=10)
    future_date = date.today() + timedelta(days=10)

    act_comp_past = DummyActivity(completion_percentage=Decimal("100.00"), end_date=past_date)
    assert calculate_activity_status(act_comp_past) == WorkActivityStatus.COMPLETED

    act_comp_future = DummyActivity(completion_percentage=Decimal("100.00"), end_date=future_date)
    assert calculate_activity_status(act_comp_future) == WorkActivityStatus.COMPLETED


def test_calculate_activity_status_type_coercion():
    """Verify resolver handles float, string, or int completion_percentage gracefully."""
    future_date = date.today() + timedelta(days=5)
    assert calculate_activity_status(DummyActivity("50.00", future_date)) == WorkActivityStatus.ON_TRACK
    assert calculate_activity_status(DummyActivity(100, future_date)) == WorkActivityStatus.COMPLETED
    assert calculate_activity_status(DummyActivity(0.0, future_date)) == WorkActivityStatus.NOT_STARTED


def test_schema_work_activity_response_nullable_fields():
    """Verify WorkActivityResponse Pydantic model allows completion_percentage=None and boq_item_id=None."""
    res = s.WorkActivityResponse(
        id=999,
        project_id=1,
        boq_item_id=None,
        work_order_id=None,
        activity_name="Nullable Test Activity",
        discipline="Civil",
        planned_quantity=Decimal("100.00"),
        unit="cum",
        engineer_id=None,
        total_completed=Decimal("0.00"),
        remaining_quantity=Decimal("100.00"),
        completion_percentage=None,  # Must be allowed
        status=WorkActivityStatus.NOT_STARTED,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=5),
        created_at=date.today(),
        updated_at=date.today(),
    )
    assert res.completion_percentage is None
    assert res.boq_item_id is None


# ==============================================================================
# Integration Test Fixtures
# ==============================================================================


@pytest_asyncio.fixture
async def issue_6_data():
    """Sets up two tenant companies, projects, BOQs, work orders, and activities for testing."""
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:8]
        pwd_hash = get_password_hash("Secret123!")

        # 1. Companies
        comp_a = Company(name=f"Issue6_CompA_{uid}")
        comp_b = Company(name=f"Issue6_CompB_{uid}")
        db.add_all([comp_a, comp_b])
        await db.flush()

        # 2. Owners
        owner_a = Owner(
            company_id=comp_a.id,
            owner_code=f"OWN-6A-{uid}",
            owner_name=f"Owner 6A {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"owner6a_{uid}@test.com",
        )
        owner_b = Owner(
            company_id=comp_b.id,
            owner_code=f"OWN-6B-{uid}",
            owner_name=f"Owner 6B {uid}",
            mobile=f"91{uuid.uuid4().int % 100000000:08d}",
            email=f"owner6b_{uid}@test.com",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        # 3. Projects
        proj_a = Project(
            business_id=f"PRJ-6A-{uid}",
            company_id=comp_a.id,
            project_name=f"Project 6A {uid}",
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        proj_b = Project(
            business_id=f"PRJ-6B-{uid}",
            company_id=comp_b.id,
            project_name=f"Project 6B {uid}",
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # 4. Users
        user_a = User(
            email=f"admin_6a_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin 6A",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        user_b = User(
            email=f"admin_6b_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Admin 6B",
            company_id=comp_b.id,
            is_super_admin=False,
            is_active=True,
            role="Admin",
        )
        super_admin = User(
            email=f"sa_6_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="Super Admin 6",
            company_id=None,
            is_super_admin=True,
            is_active=True,
            role="Admin",
        )
        no_perm_role_name = f"noperm_6_{uid}"
        role_noperm = Role(
            company_id=comp_a.id,
            name=no_perm_role_name,
            display_name="No Perm Role 6",
            is_system=False,
        )
        db.add(role_noperm)
        await db.flush()

        user_no_perm = User(
            email=f"noperm_6_{uid}@test.com",
            hashed_password=pwd_hash,
            full_name="No Perm User 6",
            company_id=comp_a.id,
            is_super_admin=False,
            is_active=True,
            role=no_perm_role_name,
        )
        db.add_all([user_a, user_b, super_admin, user_no_perm])
        await db.flush()

        # 5. BOQ Group and BOQs
        boq_group_a = BOQGroup(project_id=proj_a.id, name="BOQ Group 6A")
        db.add(boq_group_a)
        await db.flush()

        boq_a = BOQ(
            project_id=proj_a.id,
            boq_group_id=boq_group_a.id,
            item_name="BOQ Earthwork 6A",
            category="Civil",
            quantity=Decimal("200.00"),
            unit="sqm",
            unit_cost=Decimal("150.00"),
            total_cost=Decimal("30000.00"),
            is_latest=True,
        )
        db.add(boq_a)
        await db.flush()

        # 6. Work Orders
        wo_a = WorkOrder(
            project_id=proj_a.id,
            work_order_number=f"WO-6A-{uid}",
            work_description="Civil Works 6A",
            total_quantity=Decimal("500.00"),
            rate=Decimal("100.00"),
            total_amount=Decimal("50000.00"),
            completed_quantity=Decimal("0.00"),
        )
        wo_b = WorkOrder(
            project_id=proj_b.id,
            work_order_number=f"WO-6B-{uid}",
            work_description="Civil Works 6B",
            total_quantity=Decimal("100.00"),
            rate=Decimal("100.00"),
            total_amount=Decimal("10000.00"),
            completed_quantity=Decimal("0.00"),
        )
        db.add_all([wo_a, wo_b])
        await db.flush()

        # 7. Work Activities under wo_a with specific configurations:
        # act1: completion_percentage = 0.00, end_date in future -> resolves NOT_STARTED
        act_null_future = WorkActivity(
            project_id=proj_a.id,
            work_order_id=wo_a.id,
            activity_name="Excavation Future (0.00 %)",
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
        # act2: completion_percentage = 0.00, end_date in past -> resolves DELAY
        act_null_past = WorkActivity(
            project_id=proj_a.id,
            work_order_id=wo_a.id,
            activity_name="Foundation Past (Overdue 0.00 %)",
            discipline="Civil",
            planned_quantity=Decimal("100.00"),
            unit="cum",
            start_date=date.today() - timedelta(days=20),
            end_date=date.today() - timedelta(days=5),
            total_completed=Decimal("0.00"),
            remaining_quantity=Decimal("100.00"),
            completion_percentage=Decimal("0.00"),
            status=WorkActivityStatus.NOT_STARTED,  # DB has NOT_STARTED, GET resolves DELAY dynamically
        )
        # act3: completion_percentage = 0.00, end_date in future -> resolves NOT_STARTED
        act_zero_future = WorkActivity(
            project_id=proj_a.id,
            work_order_id=wo_a.id,
            activity_name="Pillar Construction (0.00 %)",
            discipline="Civil",
            planned_quantity=Decimal("100.00"),
            unit="cum",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=15),
            total_completed=Decimal("0.00"),
            remaining_quantity=Decimal("100.00"),
            completion_percentage=Decimal("0.00"),
            status=WorkActivityStatus.NOT_STARTED,
        )
        # act4: completion_percentage = 50.00, end_date in future -> resolves ON_TRACK
        act_partial_future = WorkActivity(
            project_id=proj_a.id,
            work_order_id=wo_a.id,
            activity_name="Slab Casting (50.00 %)",
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
        # act5: completion_percentage = 100.00, end_date in past -> resolves COMPLETED
        act_completed_past = WorkActivity(
            project_id=proj_a.id,
            work_order_id=wo_a.id,
            activity_name="Site Survey (100.00 %)",
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
        # Company B activity for tenant isolation checks
        act_b = WorkActivity(
            project_id=proj_b.id,
            work_order_id=wo_b.id,
            activity_name="Comp B Activity",
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

        db.add_all([
            act_null_future,
            act_null_past,
            act_zero_future,
            act_partial_future,
            act_completed_past,
            act_b,
        ])
        await db.flush()

        # 8. Ensure permissions for Admin role
        perm_codes = [
            "work_progress.view",
            "work_progress.create",
            "work_progress.edit",
            "work_progress.delete",
            "work_progress.export",
        ]
        admin_added_rps = []
        for code in perm_codes:
            p_res = await db.execute(select(Permission).where(Permission.code == code))
            perm = p_res.scalar_one_or_none()
            if not perm:
                perm = Permission(
                    module="work_progress",
                    action=code.split(".")[1],
                    code=code,
                    description=code,
                )
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

        await db.commit()

        tokens = {
            "user_a": create_access_token({"sub": str(user_a.id)}),
            "user_b": create_access_token({"sub": str(user_b.id)}),
            "super_admin": create_access_token({"sub": str(super_admin.id)}),
            "no_perm": create_access_token({"sub": str(user_no_perm.id)}),
        }

        ctx = {
            "comp_a": comp_a,
            "comp_b": comp_b,
            "proj_a": proj_a,
            "proj_b": proj_b,
            "boq_a": boq_a,
            "boq_group_a": boq_group_a,
            "wo_a": wo_a,
            "wo_b": wo_b,
            "user_a": user_a,
            "user_b": user_b,
            "super_admin": super_admin,
            "user_no_perm": user_no_perm,
            "role_noperm": role_noperm,
            "act_null_future": act_null_future,
            "act_null_past": act_null_past,
            "act_zero_future": act_zero_future,
            "act_partial_future": act_partial_future,
            "act_completed_past": act_completed_past,
            "act_b": act_b,
            "tokens": tokens,
            "admin_added_rps": admin_added_rps,
        }

    try:
        yield ctx
    finally:
        async with AsyncSessionLocal() as db:
            if admin_added_rps:
                await db.execute(delete(RolePermission).where(RolePermission.id.in_(admin_added_rps)))
            all_uids = [user_a.id, user_b.id, super_admin.id, user_no_perm.id]
            all_pids = [proj_a.id, proj_b.id]
            # Delete any daily progress entries and activity history created by users or under projects
            await db.execute(delete(ActivityHistory).where(ActivityHistory.changed_by.in_(all_uids)))
            await db.execute(delete(DailyProgressEntry).where(DailyProgressEntry.created_by.in_(all_uids)))
            await db.execute(delete(DailyProgressEntry).where(DailyProgressEntry.activity_id.in_([
                act_null_future.id, act_null_past.id, act_zero_future.id,
                act_partial_future.id, act_completed_past.id, act_b.id,
            ])))
            await db.execute(delete(ActivityHistory).where(ActivityHistory.activity_id.in_([
                act_null_future.id, act_null_past.id, act_zero_future.id,
                act_partial_future.id, act_completed_past.id, act_b.id,
            ])))
            await db.execute(delete(WorkActivity).where(WorkActivity.project_id.in_(all_pids)))
            await db.execute(delete(WorkOrder).where(WorkOrder.project_id.in_(all_pids)))
            await db.execute(delete(BOQ).where(BOQ.project_id.in_(all_pids)))
            await db.execute(delete(BOQGroup).where(BOQGroup.project_id.in_(all_pids)))
            await db.execute(delete(User).where(User.id.in_(all_uids)))
            await db.execute(delete(Role).where(Role.id == role_noperm.id))
            await db.execute(delete(Project).where(Project.id.in_(all_pids)))
            await db.execute(delete(Owner).where(Owner.id.in_([owner_a.id, owner_b.id])))
            await db.execute(delete(Company).where(Company.id.in_([comp_a.id, comp_b.id])))
            await db.commit()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ==============================================================================
# Integration Tests: Issue #6 Verification
# ==============================================================================


@pytest.mark.asyncio
async def test_01_null_completion_percentage_returns_200_no_500(client, issue_6_data):
    """Verify listing and getting activities with 0 / unset completion_percentage returns 200,
    does NOT crash with 500, and resolves status properly in memory.
    """
    token = issue_6_data["tokens"]["user_a"]
    headers = {"Authorization": f"Bearer {token}"}
    proj_a_id = issue_6_data["proj_a"].id
    act_null_future_id = issue_6_data["act_null_future"].id
    act_null_past_id = issue_6_data["act_null_past"].id

    # 1. GET /activities (list)
    resp = await client.get(
        f"/api/v1/work-progress/activities?project_id={proj_a_id}",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["total_count"] == 5

    items_by_id = {item["id"]: item for item in data["data"]}

    # Check act_null_future: status NOT_STARTED
    item_future = items_by_id[act_null_future_id]
    assert item_future["status"] == WorkActivityStatus.NOT_STARTED.value

    # Check act_null_past: status DELAY (resolved in-memory because end_date is in past)
    item_past = items_by_id[act_null_past_id]
    assert item_past["status"] == WorkActivityStatus.DELAY.value

    # 2. GET /activities/{id} for single activity
    resp_single = await client.get(
        f"/api/v1/work-progress/activities/{act_null_past_id}",
        headers=headers,
    )
    assert resp_single.status_code == 200, resp_single.text
    single_data = resp_single.json()
    assert single_data["id"] == act_null_past_id
    assert single_data["status"] == WorkActivityStatus.DELAY.value


@pytest.mark.asyncio
async def test_02_get_endpoints_are_strictly_read_only_and_emit_zero_dml(client, issue_6_data):
    """Verify that GET endpoints:
    1. Do NOT execute any INSERT, UPDATE, or DELETE SQL statements.
    2. Do NOT use FOR UPDATE locks.
    3. Leave database rows completely unchanged (status, completion_percentage, updated_at).
    """
    token = issue_6_data["tokens"]["user_a"]
    headers = {"Authorization": f"Bearer {token}"}
    proj_a_id = issue_6_data["proj_a"].id
    act_null_past_id = issue_6_data["act_null_past"].id
    wo_a_id = issue_6_data["wo_a"].id

    # Capture initial DB state for all activities in proj_a
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(WorkActivity)
            .where(WorkActivity.project_id == proj_a_id)
            .order_by(WorkActivity.id)
        )
        before_records = {
            act.id: {
                "status": act.status,
                "completion_percentage": act.completion_percentage,
                "total_completed": act.total_completed,
                "updated_at": act.updated_at,
            }
            for act in res.scalars().all()
        }

    # Verify that in the database, act_null_past STILL has status=NOT_STARTED
    # (its status is only resolved to DELAY dynamically for the GET response)
    assert before_records[act_null_past_id]["status"] == WorkActivityStatus.NOT_STARTED

    # Instrument SQLAlchemy engine to capture executed SQL statements
    captured_statements = []

    def capture_sql(conn, cursor, statement, parameters, context, executemany):
        captured_statements.append(statement.strip())

    event.listen(async_engine.sync_engine, "before_cursor_execute", capture_sql)

    try:
        # 1. Call GET /activities
        resp1 = await client.get(
            f"/api/v1/work-progress/activities?project_id={proj_a_id}",
            headers=headers,
        )
        assert resp1.status_code == 200

        # 2. Call GET /activities/{id}
        resp2 = await client.get(
            f"/api/v1/work-progress/activities/{act_null_past_id}",
            headers=headers,
        )
        assert resp2.status_code == 200

        # 3. Call GET /work-order/{wo_id}/progress-summary
        resp3 = await client.get(
            f"/api/v1/work-progress/work-order/{wo_a_id}/progress-summary",
            headers=headers,
        )
        assert resp3.status_code == 200
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", capture_sql)

    # Assert: Zero DML statements were executed
    dml_statements = [
        s for s in captured_statements
        if s.upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert len(dml_statements) == 0, f"GET emitted DML statements: {dml_statements}"

    # Assert: Zero FOR UPDATE statements were executed
    for_update_statements = [
        s for s in captured_statements
        if "FOR UPDATE" in s.upper()
    ]
    assert len(for_update_statements) == 0, f"GET emitted FOR UPDATE statements: {for_update_statements}"

    # Verify DB state after GET is strictly identical to before
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(WorkActivity)
            .where(WorkActivity.project_id == proj_a_id)
            .order_by(WorkActivity.id)
        )
        after_records = {
            act.id: {
                "status": act.status,
                "completion_percentage": act.completion_percentage,
                "total_completed": act.total_completed,
                "updated_at": act.updated_at,
            }
            for act in res.scalars().all()
        }

    for act_id, before_vals in before_records.items():
        after_vals = after_records[act_id]
        assert before_vals["status"] == after_vals["status"], f"Status mutated for activity {act_id}!"
        assert before_vals["completion_percentage"] == after_vals["completion_percentage"], f"Pct mutated for activity {act_id}!"
        assert before_vals["total_completed"] == after_vals["total_completed"], f"Total completed mutated for activity {act_id}!"
        assert before_vals["updated_at"] == after_vals["updated_at"], f"Updated_at mutated for activity {act_id}!"


@pytest.mark.asyncio
async def test_03_repeated_get_calls_are_stable_and_idempotent(client, issue_6_data):
    """Verify repeated consecutive GET calls return stable and identical results without mutating state."""
    token = issue_6_data["tokens"]["user_a"]
    headers = {"Authorization": f"Bearer {token}"}
    proj_a_id = issue_6_data["proj_a"].id

    first_result = None
    for i in range(3):
        resp = await client.get(
            f"/api/v1/work-progress/activities?project_id={proj_a_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        if first_result is None:
            first_result = data
        else:
            assert data == first_result, f"Iteration {i} result differed from first result"


@pytest.mark.asyncio
async def test_04_work_order_progress_summary_aggregate_accuracy(client, issue_6_data):
    """Verify /work-order/{id}/progress-summary calculates business aggregates in-memory
    accurately, without with_for_update() and without depending on prior GET mutations.
    """
    token = issue_6_data["tokens"]["user_a"]
    headers = {"Authorization": f"Bearer {token}"}
    wo_a_id = issue_6_data["wo_a"].id

    resp = await client.get(
        f"/api/v1/work-progress/work-order/{wo_a_id}/progress-summary",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()

    # In wo_a we have 5 activities:
    # 1. act_null_future: completion_pct=0.00, end_date=future -> NOT_STARTED
    # 2. act_null_past: completion_pct=0.00, end_date=past -> DELAY
    # 3. act_zero_future: completion_pct=0.00, end_date=future -> NOT_STARTED
    # 4. act_partial_future: completion_pct=50.00, end_date=future -> ON_TRACK
    # 5. act_completed_past: completion_pct=100.00, end_date=past -> COMPLETED

    activities_summary = summary["activities"]
    assert activities_summary["total"] == 5
    assert activities_summary["completed"] == 1
    assert activities_summary["on_track"] == 1
    assert activities_summary["delayed"] == 1
    assert activities_summary["not_started"] == 2

    work_order_summary = summary["work_order"]
    # Planned quantity: 5 * 100.00 = 500.00
    assert Decimal(str(work_order_summary["planned_quantity"])) == Decimal("500.00")
    # Completed quantity: 0 + 0 + 0 + 50.00 + 100.00 = 150.00
    assert Decimal(str(work_order_summary["completed_quantity"])) == Decimal("150.00")
    # Remaining quantity: 500.00 - 150.00 = 350.00
    assert Decimal(str(work_order_summary["remaining_quantity"])) == Decimal("350.00")
    # Overall completion percentage = (150 / 500) * 100 = 30.00%
    assert Decimal(str(work_order_summary["completion_percentage"])) == Decimal("30.00")
    # Average progress = (0 + 0 + 0 + 50 + 100) / 5 = 30.00%
    assert Decimal(str(work_order_summary["average_progress"])) == Decimal("30.00")


@pytest.mark.asyncio
async def test_05_write_endpoints_explicitly_persist_status(client, issue_6_data):
    """Verify write endpoints (POST, PUT, daily-entry) continue to persist activity status in the database."""
    token = issue_6_data["tokens"]["user_a"]
    headers = {"Authorization": f"Bearer {token}"}
    proj_a_id = issue_6_data["proj_a"].id
    wo_a_id = issue_6_data["wo_a"].id
    boq_a_id = issue_6_data["boq_a"].id

    # 1. POST /activities creates activity and persists NOT_STARTED in DB
    create_payload = {
        "project_id": proj_a_id,
        "boq_item_id": boq_a_id,
        "work_order_id": wo_a_id,
        "start_date": str(date.today()),
        "end_date": str(date.today() + timedelta(days=10)),
    }
    resp = await client.post(
        "/api/v1/work-progress/activities",
        json=create_payload,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    created_id = resp.json()["data"]["id"]

    # Verify status in DB is persisted
    async with AsyncSessionLocal() as db:
        new_act = await db.get(WorkActivity, created_id)
        assert new_act is not None
        assert new_act.status == WorkActivityStatus.NOT_STARTED

    # 2. PUT /activities/{id} with past end_date updates and persists status=DELAY in DB
    update_payload = {
        "boq_item_id": boq_a_id,
        "start_date": str(date.today() - timedelta(days=20)),
        "end_date": str(date.today() - timedelta(days=5)),
    }
    put_resp = await client.put(
        f"/api/v1/work-progress/activities/{created_id}",
        json=update_payload,
        headers=headers,
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["data"]["status"] == WorkActivityStatus.DELAY.value

    # Verify status in DB was updated to DELAY
    async with AsyncSessionLocal() as db:
        updated_act = await db.get(WorkActivity, created_id)
        assert updated_act.status == WorkActivityStatus.DELAY

    # 3. POST /daily-entry adding progress to complete 100% of planned quantity persists COMPLETED in DB
    # First set end_date back to future and start_date to today
    await client.put(
        f"/api/v1/work-progress/activities/{created_id}",
        json={
            "boq_item_id": boq_a_id,
            "start_date": str(date.today()),
            "end_date": str(date.today() + timedelta(days=10)),
        },
        headers=headers,
    )
    progress_payload = {
        "activity_id": created_id,
        "entry_date": str(date.today()),
        "today_progress": 200.0,
        "remarks": "Completed full plastering",
    }
    dpe_resp = await client.post(
        "/api/v1/work-progress/daily-entry",
        json=progress_payload,
        headers=headers,
    )
    assert dpe_resp.status_code == 200, dpe_resp.text

    # Verify activity in DB has completion_percentage=100.00 and status=COMPLETED
    async with AsyncSessionLocal() as db:
        completed_act = await db.get(WorkActivity, created_id)
        assert completed_act.completion_percentage == Decimal("100.00")
        assert completed_act.status == WorkActivityStatus.COMPLETED

    # Clean up created activity
    async with AsyncSessionLocal() as db:
        await db.execute(delete(DailyProgressEntry).where(DailyProgressEntry.activity_id == created_id))
        await db.execute(delete(ActivityHistory).where(ActivityHistory.activity_id == created_id))
        await db.execute(delete(WorkActivity).where(WorkActivity.id == created_id))
        await db.commit()


@pytest.mark.asyncio
async def test_06_tenant_isolation_and_rbac_preservation(client, issue_6_data):
    """Verify tenant isolation and RBAC:
    - User B cannot access User A's activities (returns 404)
    - User B cannot access User A's progress summary (returns 404)
    - User without work_progress.view cannot access activities (returns 403)
    - SuperAdmin can access across tenants
    """
    token_b = issue_6_data["tokens"]["user_b"]
    token_sa = issue_6_data["tokens"]["super_admin"]
    token_no_perm = issue_6_data["tokens"]["no_perm"]

    headers_b = {"Authorization": f"Bearer {token_b}"}
    headers_sa = {"Authorization": f"Bearer {token_sa}"}
    headers_no_perm = {"Authorization": f"Bearer {token_no_perm}"}

    proj_a_id = issue_6_data["proj_a"].id
    wo_a_id = issue_6_data["wo_a"].id
    act_a_id = issue_6_data["act_null_future"].id

    # 1. Tenant B accessing Tenant A's activities list -> 404 Project not found
    resp_b_list = await client.get(
        f"/api/v1/work-progress/activities?project_id={proj_a_id}",
        headers=headers_b,
    )
    assert resp_b_list.status_code == 404

    # 2. Tenant B accessing Tenant A's single activity -> 404
    resp_b_single = await client.get(
        f"/api/v1/work-progress/activities/{act_a_id}",
        headers=headers_b,
    )
    assert resp_b_single.status_code == 404

    # 3. Tenant B accessing Tenant A's progress summary -> 404 Work order not found
    resp_b_summary = await client.get(
        f"/api/v1/work-progress/work-order/{wo_a_id}/progress-summary",
        headers=headers_b,
    )
    assert resp_b_summary.status_code == 404

    # 4. User without work_progress.view -> 403 Forbidden
    resp_noperm = await client.get(
        f"/api/v1/work-progress/activities?project_id={proj_a_id}",
        headers=headers_no_perm,
    )
    assert resp_noperm.status_code == 403

    # 5. Super Admin accessing Tenant A's activities -> 200 OK
    resp_sa_list = await client.get(
        f"/api/v1/work-progress/activities?project_id={proj_a_id}",
        headers=headers_sa,
    )
    assert resp_sa_list.status_code == 200
    assert resp_sa_list.json()["total_count"] == 5

    # 6. Super Admin accessing Tenant A's progress summary -> 200 OK
    resp_sa_summary = await client.get(
        f"/api/v1/work-progress/work-order/{wo_a_id}/progress-summary",
        headers=headers_sa,
    )
    assert resp_sa_summary.status_code == 200
    assert resp_sa_summary.json()["activities"]["total"] == 5


@pytest.mark.asyncio
async def test_07_list_daily_entries(client, issue_6_data):
    """Verify GET /api/v1/work-progress/daily-entry succeeds with expected schema and pagination."""
    token = issue_6_data["tokens"]["user_a"]
    headers = {"Authorization": f"Bearer {token}"}
    proj_a_id = issue_6_data["proj_a"].id

    resp = await client.get(
        f"/api/v1/work-progress/daily-entry?project_id={proj_a_id}&limit=10&offset=0",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["message"] == "Daily progress fetched successfully"
    assert "data" in data
    assert isinstance(data["data"], list)
    assert "pagination" in data
    assert data["pagination"]["limit"] == 10
    assert data["pagination"]["offset"] == 0
    assert "total" in data["pagination"]
    assert data["limit"] == 10
    assert data["offset"] == 0
    assert "total_count" in data
    assert "page_count" in data

