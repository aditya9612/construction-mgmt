"""
Focused Regression Tests for PO Cancellation Cross-Company Isolation.

Verifies that both `request_cancellation_otp` and `verify_and_execute_cancellation`
enforce `PurchaseOrder.company_id` ownership directly at the PurchaseOrder query level,
while preserving existing same-company PO cancellation and OTP notification behavior.

Runs strictly isolated from production/local MySQL databases.
"""

import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, BindParameter

from app.core.config import settings as app_settings
from app.core.dependencies import (
    get_current_active_user,
    get_current_user,
    get_db_session,
)
from app.main import app
from app.models.cancellation import OTPCancellationRequest
from app.models.material import PurchaseOrder
from app.models.notification import Notification
from app.models.purchase_order import PurchaseOrderItem
from app.models.user import User, UserRole
from app.services.cancellation_service import (
    _get_otp_secret,
    _hash_otp,
    request_cancellation_otp,
    verify_and_execute_cancellation,
)
from app.utils.helpers import NotFoundError
from tests.test_db_safety_guard import (
    TEST_MYSQL_URL_ASYNC,
    validate_safe_test_environment,
)


def _extract_eq_filters(expr) -> dict[str, object]:
    """Extracts column_key -> bound value equality predicates from a SQLAlchemy WHERE tree."""
    filters: dict[str, object] = {}
    if expr is None:
        return filters
    if isinstance(expr, BooleanClauseList):
        for clause in expr.clauses:
            filters.update(_extract_eq_filters(clause))
    elif isinstance(expr, BinaryExpression):
        left_key = getattr(expr.left, "key", None)
        if left_key and isinstance(expr.right, BindParameter):
            filters[left_key] = expr.right.value
    return filters


def _collect_where_filters(stmt) -> dict[str, object]:
    combined: dict[str, object] = {}
    for criterion in getattr(stmt, "_where_criteria", ()):
        combined.update(_extract_eq_filters(criterion))
    return combined


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _IsolatedCancellationSession:
    """
    In-memory AsyncSession double that evaluates real SQLAlchemy queries
    and records every PurchaseOrder statement executed for tenant-filter inspection.
    """

    def __init__(self, users: list[User], purchase_orders: list[PurchaseOrder]):
        self.users = {u.id: u for u in users}
        self.purchase_orders = {po.id: po for po in purchase_orders}
        self.requests: dict[int, OTPCancellationRequest] = {}
        self.notifications: list[Notification] = []
        self.po_queries: list[tuple[str, dict[str, object]]] = []
        self._next_req_id = 100
        self.committed_count = 0
        self.rolled_back_count = 0

    def add(self, obj):
        if isinstance(obj, OTPCancellationRequest):
            if not getattr(obj, "id", None):
                obj.id = self._next_req_id
                self._next_req_id += 1
            if not getattr(obj, "created_at", None):
                obj.created_at = datetime.utcnow()
            self.requests[obj.id] = obj
        elif isinstance(obj, Notification):
            self.notifications.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.committed_count += 1

    async def rollback(self):
        self.rolled_back_count += 1

    async def refresh(self, obj):
        return None

    async def get(self, model, pk):
        if model is User:
            return self.users.get(pk)
        if model is PurchaseOrder:
            return self.purchase_orders.get(pk)
        return None

    async def execute(self, stmt):
        from_tables = [f.name for f in stmt.get_final_froms()]
        filters = _collect_where_filters(stmt)

        if "purchase_orders" in from_tables:
            self.po_queries.append((str(stmt), dict(filters)))
            matched = []
            for po in self.purchase_orders.values():
                if "id" in filters and po.id != filters["id"]:
                    continue
                if "company_id" in filters and po.company_id != filters["company_id"]:
                    continue
                matched.append(po)
            return _FakeExecuteResult(matched)

        if "goods_receipt_notes" in from_tables:
            return _FakeExecuteResult([])

        if "otp_cancellation_requests" in from_tables:
            matched = []
            for req in self.requests.values():
                if "id" in filters and req.id != filters["id"]:
                    continue
                if "company_id" in filters and req.company_id != filters["company_id"]:
                    continue
                if "document_type" in filters and req.document_type != filters["document_type"]:
                    continue
                if "document_id" in filters and req.document_id != filters["document_id"]:
                    continue
                if "status" in filters and req.status != filters["status"]:
                    continue
                if "expires_at" in filters and not (req.expires_at > datetime.utcnow()):
                    continue
                matched.append(req)
            return _FakeExecuteResult(matched)

        if "users" in from_tables:
            matched = [
                u
                for u in self.users.values()
                if u.company_id == filters.get("company_id")
                and u.is_active is True
                and u.is_deleted is False
                and u.role in (UserRole.ADMIN.value, "admin", "Admin", UserRole.PROJECT_MANAGER.value, "project_manager", "ProjectManager")
            ]
            return _FakeExecuteResult(matched)

        return _FakeExecuteResult([])


@pytest.fixture(autouse=True)
def _enforce_isolated_guard():
    """Enforces test DB safety guard and preserves FastAPI overrides cleanup."""
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC)
    yield
    app.dependency_overrides.clear()


def _build_two_company_fixture():
    uid = uuid.uuid4().hex[:6]
    maker_a = User(
        id=11,
        email=f"maker_a_{uid}@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_deleted=False,
        is_super_admin=False,
        company_id=1,
    )
    checker_a = User(
        id=12,
        email=f"checker_a_{uid}@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_deleted=False,
        is_super_admin=False,
        company_id=1,
    )
    maker_b = User(
        id=21,
        email=f"maker_b_{uid}@test.com",
        role=UserRole.ADMIN.value,
        is_active=True,
        is_deleted=False,
        is_super_admin=False,
        company_id=2,
    )
    checker_b = User(
        id=22,
        email=f"checker_b_{uid}@test.com",
        role=UserRole.PROJECT_MANAGER.value,
        is_active=True,
        is_deleted=False,
        is_super_admin=False,
        company_id=2,
    )

    po_item_a = PurchaseOrderItem(
        id=1001,
        purchase_order_id=101,
        material_name="Cement",
        quantity=Decimal("10.000"),
        received_quantity=Decimal("0.000"),
        rate=Decimal("350.00"),
        total_amount=Decimal("3500.00"),
        status="PENDING",
        is_deleted=False,
    )
    po_a = PurchaseOrder(
        id=101,
        company_id=1,
        po_number=f"PO-A-{uid}",
        supplier_id=1,
        project_id=1,
        status="APPROVED",
        items=[po_item_a],
    )

    po_item_b = PurchaseOrderItem(
        id=2001,
        purchase_order_id=201,
        material_name="Steel",
        quantity=Decimal("5.000"),
        received_quantity=Decimal("0.000"),
        rate=Decimal("500.00"),
        total_amount=Decimal("2500.00"),
        status="PENDING",
        is_deleted=False,
    )
    po_b = PurchaseOrder(
        id=201,
        company_id=2,
        po_number=f"PO-B-{uid}",
        supplier_id=2,
        project_id=2,
        status="APPROVED",
        items=[po_item_b],
    )

    db = _IsolatedCancellationSession(
        users=[maker_a, checker_a, maker_b, checker_b],
        purchase_orders=[po_a, po_b],
    )
    return {
        "db": db,
        "maker_a": maker_a,
        "checker_a": checker_a,
        "maker_b": maker_b,
        "checker_b": checker_b,
        "po_a": po_a,
        "po_item_a": po_item_a,
        "po_b": po_b,
        "po_item_b": po_item_b,
    }


def test_po_isolation_suite_anchored_to_safety_guard():
    """Confirm test safety guard blocks protected local/prod DB and allows isolated container only."""
    with pytest.raises(RuntimeError, match="SAFETY VIOLATION"):
        validate_safe_test_environment(app_settings.DATABASE_URL_ASYNC)
    assert validate_safe_test_environment(TEST_MYSQL_URL_ASYNC) is True


@pytest.mark.asyncio
async def test_same_company_po_cancellation_and_otp_notification_preserved():
    """Scenario A: Same-company PO request & verification work end-to-end and preserve OTP notification format."""
    ctx = _build_two_company_fixture()
    db: _IsolatedCancellationSession = ctx["db"]

    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: ctx["maker_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["maker_a"]

    with patch(
        "app.core.dependencies.get_effective_user_permissions",
        new=AsyncMock(return_value={"transaction.cancel.request", "transaction.cancel.approve"}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res_req = await client.post(
                "/api/v1/cancellation/request-otp",
                json={
                    "company_id": 1,
                    "document_type": "PO",
                    "document_id": ctx["po_a"].id,
                    "reason": "Duplicate purchase order",
                },
            )
            assert res_req.status_code == 201
            body = res_req.json()
            assert "otp" not in body
            assert body["status"] == "PENDING"
            assert body["document_id"] == ctx["po_a"].id

            # Existing OTP notification behavior preserved verbatim:
            # checker receives "Authorization OTP: <6 digits>", maker receives sanitized notice without OTP,
            # foreign company users receive nothing.
            checker_notifs = [n for n in db.notifications if n.user_id == ctx["checker_a"].id]
            maker_notifs = [n for n in db.notifications if n.user_id == ctx["maker_a"].id]
            foreign_notifs = [n for n in db.notifications if n.user_id in (ctx["maker_b"].id, ctx["checker_b"].id)]

            assert len(checker_notifs) == 1
            otp_match = re.search(r"Authorization OTP:\s*(\d{6})", checker_notifs[0].message)
            assert otp_match is not None
            raw_otp = otp_match.group(1)

            assert len(maker_notifs) == 1
            assert raw_otp not in maker_notifs[0].message
            assert len(foreign_notifs) == 0

            # Checker verifies OTP and cancels own company PO
            app.dependency_overrides[get_current_user] = lambda: ctx["checker_a"]
            app.dependency_overrides[get_current_active_user] = lambda: ctx["checker_a"]

            res_verify = await client.post(
                "/api/v1/cancellation/verify-otp",
                json={
                    "company_id": 1,
                    "request_id": body["request_id"],
                    "otp": raw_otp,
                },
            )
            assert res_verify.status_code == 200
            verify_body = res_verify.json()
            assert verify_body["status"] == "VERIFIED"
            assert verify_body["verified_by_id"] == ctx["checker_a"].id

    assert ctx["po_a"].status == "CANCELLED"
    assert ctx["po_item_a"].status == "CANCELLED"
    assert ctx["po_b"].status == "APPROVED"


@pytest.mark.asyncio
async def test_cross_company_po_cancellation_request_rejected():
    """Scenario B: Company A user attempting to request OTP for Company B's PO ID is rejected with no request/OTP created."""
    ctx = _build_two_company_fixture()
    db: _IsolatedCancellationSession = ctx["db"]

    # 1. Service-layer rejection
    with pytest.raises(NotFoundError, match=f"Purchase Order {ctx['po_b'].id} not found"):
        await request_cancellation_otp(
            db=db,
            company_id=1,
            user_id=ctx["maker_a"].id,
            document_type="PO",
            document_id=ctx["po_b"].id,
            reason="Cross-tenant IDOR attempt",
        )

    assert len(db.requests) == 0
    assert len(db.notifications) == 0
    assert ctx["po_b"].status == "APPROVED"
    assert ctx["po_item_b"].status == "PENDING"

    # 2. HTTP endpoint rejection
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: ctx["maker_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["maker_a"]

    with patch(
        "app.core.dependencies.get_effective_user_permissions",
        new=AsyncMock(return_value={"transaction.cancel.request"}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/v1/cancellation/request-otp",
                json={
                    "company_id": 1,
                    "document_type": "PO",
                    "document_id": ctx["po_b"].id,
                    "reason": "Cross-tenant API IDOR attempt",
                },
            )
            assert res.status_code == 404

    assert len(db.requests) == 0
    assert len(db.notifications) == 0
    assert ctx["po_b"].status == "APPROVED"


@pytest.mark.asyncio
async def test_cross_company_po_cancellation_verification_rejected():
    """
    Scenario C:
    1) A Company A cancellation request pointing to Company B's PO ID (mismatched document/request)
       fails safely on verification even with a valid OTP; Company B's PO remains untouched.
    2) A Company B checker attempting to verify a valid Company A request is rejected; Company A/B POs unchanged.
    """
    ctx = _build_two_company_fixture()
    db: _IsolatedCancellationSession = ctx["db"]

    raw_otp = "654321"
    secret = _get_otp_secret()
    mismatched_req = OTPCancellationRequest(
        id=9001,
        company_id=1,
        document_type="PO",
        document_id=ctx["po_b"].id,  # Mismatched foreign PO ID belonging to Company 2
        document_number=ctx["po_b"].po_number,
        requested_by_id=ctx["maker_a"].id,
        reason="Mismatched cross-company PO check",
        otp_hash=_hash_otp(raw_otp, secret),
        expires_at=datetime.utcnow() + timedelta(minutes=15),
        attempts=0,
        max_attempts=3,
        status="PENDING",
    )
    db.requests[mismatched_req.id] = mismatched_req

    # 1. Service verification fails safely when request (Company A) references Company B's PO
    with pytest.raises(NotFoundError, match=f"Purchase Order {ctx['po_b'].id} not found"):
        await verify_and_execute_cancellation(
            db=db,
            company_id=1,
            user_id=ctx["checker_a"].id,
            request_id=mismatched_req.id,
            otp=raw_otp,
        )

    assert ctx["po_b"].status == "APPROVED"
    assert ctx["po_item_b"].status == "PENDING"
    assert mismatched_req.status != "VERIFIED"

    # 2. HTTP verification with mismatched foreign PO also returns 404 safely
    mismatched_req.attempts = 0
    mismatched_req.status = "PENDING"
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: ctx["checker_a"]
    app.dependency_overrides[get_current_active_user] = lambda: ctx["checker_a"]

    with patch(
        "app.core.dependencies.get_effective_user_permissions",
        new=AsyncMock(return_value={"transaction.cancel.approve"}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res_mismatch = await client.post(
                "/api/v1/cancellation/verify-otp",
                json={
                    "company_id": 1,
                    "request_id": mismatched_req.id,
                    "otp": raw_otp,
                },
            )
            assert res_mismatch.status_code == 404

    assert ctx["po_b"].status == "APPROVED"
    assert mismatched_req.status != "VERIFIED"

    # 3. Company B checker attempting to verify a valid Company A request is rejected
    valid_req_a, valid_otp_a = await request_cancellation_otp(
        db=db,
        company_id=1,
        user_id=ctx["maker_a"].id,
        document_type="PO",
        document_id=ctx["po_a"].id,
        reason="Valid Company A request",
    )
    with pytest.raises(NotFoundError):
        await verify_and_execute_cancellation(
            db=db,
            company_id=2,
            user_id=ctx["checker_b"].id,
            request_id=valid_req_a.id,
            otp=valid_otp_a,
        )

    assert ctx["po_a"].status == "APPROVED"
    assert valid_req_a.status == "PENDING"


@pytest.mark.asyncio
async def test_every_po_lookup_enforces_company_ownership_filter():
    """Scenario D: Every PurchaseOrder SQL lookup in request & verify paths explicitly filters company_id."""
    ctx = _build_two_company_fixture()
    db: _IsolatedCancellationSession = ctx["db"]

    # Cross-company request lookup
    with pytest.raises(NotFoundError):
        await request_cancellation_otp(
            db=db,
            company_id=1,
            user_id=ctx["maker_a"].id,
            document_type="PO",
            document_id=ctx["po_b"].id,
            reason="Inspect cross-company request SQL",
        )

    # Same-company full flow (request + verify)
    req, raw_otp = await request_cancellation_otp(
        db=db,
        company_id=1,
        user_id=ctx["maker_a"].id,
        document_type="PO",
        document_id=ctx["po_a"].id,
        reason="Inspect same-company request & verify SQL",
    )
    await verify_and_execute_cancellation(
        db=db,
        company_id=1,
        user_id=ctx["checker_a"].id,
        request_id=req.id,
        otp=raw_otp,
    )

    assert len(db.po_queries) == 3  # 1 cross-tenant request + 1 same-tenant request + 1 same-tenant verify
    for sql_text, filters in db.po_queries:
        assert "purchase_orders.id =" in sql_text
        assert "purchase_orders.company_id =" in sql_text
        assert "id" in filters
        assert filters.get("company_id") == 1
