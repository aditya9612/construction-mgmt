import pytest
import uuid
from decimal import Decimal
from datetime import datetime, date
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, and_

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, get_db_session
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus
from app.models.master_data import MaterialMaster, Unit
from app.models.material import Material, Supplier, RateType, MaterialLedger
from app.models.inventory import StockBatch, MaterialIssue, MaterialIssueAllocation
from app.core.enums import TransactionType
from unittest.mock import patch, AsyncMock


@pytest.fixture(autouse=True)
def clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


async def setup_outward_test_data():
    """Seed companies, project, material, and stock batches for outward issue tests."""
    async with AsyncSessionLocal() as db:
        suffix = uuid.uuid4().hex[:6]
        # Company 1 (Target) & Company 2 (Attacker)
        c1 = await db.get(Company, 1)
        if not c1:
            c1 = Company(id=1, name="Outward Test Co 1", is_active=True)
            db.add(c1)

        c2 = await db.get(Company, 2)
        if not c2:
            c2 = Company(id=2, name="Outward Test Co 2", is_active=True)
            db.add(c2)

        user_db = await db.scalar(select(User).where(User.company_id == 1, User.is_active == True))
        if not user_db:
            user_db = User(
                email=f"eng_{suffix}@test.com",
                role=UserRole.SITE_ENGINEER.value,
                company_id=1,
                is_active=True,
            )
            db.add(user_db)
            await db.flush()

        owner = await db.scalar(select(Owner).where(Owner.company_id == 1))
        if not owner:
            owner = Owner(
                owner_code=f"OWN-OUT-{suffix}",
                owner_name="Outward Owner",
                mobile="9876543299",
                email="outward_owner@test.com",
                company_id=1,
            )
            db.add(owner)
            await db.flush()

        proj = Project(
            business_id=f"PRJ-OUT-{suffix}",
            company_id=1,
            project_name=f"Outward Project {suffix}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(proj)
        await db.flush()

        unit = await db.scalar(select(Unit).where(Unit.name == "Bags"))
        if not unit:
            unit = Unit(name=f"Bags-{suffix}", unique_code=f"BG-{suffix}")
            db.add(unit)
            await db.flush()

        mm = MaterialMaster(
            company_id=1,
            name=f"Steel TMT-{suffix}",
            category="Steel",
            unit_id=unit.id,
        )
        db.add(mm)
        await db.flush()

        supp = Supplier(
            company_id=1,
            supplier_name=f"Steel Supplier {suffix}",
            contact_person="Ramesh",
        )
        db.add(supp)
        await db.flush()

        # Material with 50 bags initial stock
        mat = Material(
            material_code=f"MAT-OUT-{suffix.upper()}",
            project_id=proj.id,
            material_master_id=mm.id,
            material_name=f"Steel TMT-{suffix}",
            category="Steel",
            unit_id=unit.id,
            supplier_id=supp.id,
            rate_type=RateType.FIXED,
            purchase_rate=Decimal("450.00"),
            quantity_purchased=Decimal("50.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("50.000"),
            total_amount=Decimal("22500.00"),
            payment_given=Decimal("0.00"),
            payment_pending=Decimal("22500.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        db.add(mat)
        await db.flush()

        # Create two stock batches: 30 @ 440.00 and 20 @ 465.00
        batch1 = StockBatch(
            project_id=proj.id,
            material_master_id=mm.id,
            batch_number=f"BAT-1-{suffix}",
            initial_quantity=Decimal("30.000"),
            current_balance=Decimal("30.000"),
            purchase_rate=Decimal("440.00"),
            receipt_date=datetime.utcnow(),
            status="ACTIVE",
        )
        batch2 = StockBatch(
            project_id=proj.id,
            material_master_id=mm.id,
            batch_number=f"BAT-2-{suffix}",
            initial_quantity=Decimal("20.000"),
            current_balance=Decimal("20.000"),
            purchase_rate=Decimal("465.00"),
            receipt_date=datetime.utcnow(),
            status="ACTIVE",
        )
        db.add_all([batch1, batch2])
        await db.commit()
        await db.refresh(proj)
        await db.refresh(mat)

        return {
            "company_id": 1,
            "project_id": proj.id,
            "material_id": mat.id,
            "batch1_id": batch1.id,
            "batch2_id": batch2.id,
            "user_id": user_db.id,
        }


# ================= 1. UNAUTHENTICATED REQUEST TEST (401) =================

@pytest.mark.asyncio
async def test_outward_issue_unauthenticated_fails_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/outward/issue/create", json={"company_id": 1, "quantity": 10.0})
        assert resp.status_code == 401


# ================= 2. UNAUTHORIZED REQUEST TEST (403) =================

@pytest.mark.asyncio
async def test_outward_issue_unauthorized_user_fails_403():
    user = User(id=9001, email="viewer@test.com", role=UserRole.SITE_ENGINEER.value, is_active=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"outward.view"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/outward/issue/create", json={
                "company_id": 1,
                "project_id": 1,
                "material_id": 1,
                "quantity": 10.0,
                "issue_type": "SITE_CONSUMPTION"
            })
            assert resp.status_code == 403
            assert "Insufficient permissions" in resp.text or "Permission denied" in resp.text


# ================= 3. CROSS-COMPANY TENANT ISOLATION TEST (403) =================

@pytest.mark.asyncio
async def test_outward_issue_cross_company_access_fails_403():
    data = await setup_outward_test_data()
    attacker = User(id=9002, email="attacker@comp2.com", role=UserRole.ADMIN.value, is_active=True, company_id=2)
    app.dependency_overrides[get_current_user] = lambda: attacker
    app.dependency_overrides[get_current_active_user] = lambda: attacker

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"outward.create", "outward.view"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/outward/issue/create", json={
                "company_id": 1,  # Attacking Company 1
                "project_id": data["project_id"],
                "material_id": data["material_id"],
                "quantity": 10.0,
                "issue_type": "SITE_CONSUMPTION"
            })
            assert resp.status_code == 403
            assert "Cross-company access is strictly prohibited" in resp.text


# ================= 4. INVALID PAYLOAD VALIDATION (422) =================

@pytest.mark.asyncio
async def test_outward_issue_invalid_payload_fails_422():
    data = await setup_outward_test_data()
    user = User(id=9003, email="engineer@comp1.com", role=UserRole.SITE_ENGINEER.value, is_active=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"outward.create", "outward.view"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Missing material_id and quantity
            resp = await client.post("/api/v1/outward/issue/create", json={
                "company_id": 1,
                "project_id": data["project_id"],
            })
            assert resp.status_code == 422


# ================= 5. NON-EXISTENT MATERIAL TEST (404) =================

@pytest.mark.asyncio
async def test_outward_issue_non_existent_material_fails_404():
    data = await setup_outward_test_data()
    user = User(id=9004, email="engineer@comp1.com", role=UserRole.SITE_ENGINEER.value, is_active=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"outward.create", "outward.view"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/outward/issue/create", json={
                "company_id": 1,
                "project_id": data["project_id"],
                "material_id": 999999,  # Non-existent
                "quantity": 5.0,
                "issue_type": "SITE_CONSUMPTION"
            })
            assert resp.status_code in [404, 400]


# ================= 6. INSUFFICIENT STOCK VALIDATION (400) =================

@pytest.mark.asyncio
async def test_outward_issue_insufficient_stock_fails_400():
    data = await setup_outward_test_data()
    user = User(id=9005, email="engineer@comp1.com", role=UserRole.SITE_ENGINEER.value, is_active=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"outward.create", "outward.view"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 50 bags available; attempt to issue 150 bags
            resp = await client.post("/api/v1/outward/issue/create", json={
                "company_id": 1,
                "project_id": data["project_id"],
                "material_id": data["material_id"],
                "quantity": 150.0,
                "issue_type": "SITE_CONSUMPTION"
            })
            assert resp.status_code == 400
            assert "Insufficient" in resp.text or "stock" in resp.text.lower()


# ================= 7. VALID OUTWARD ISSUE WITH FIFO DEPLETION (201) =================

@pytest.mark.asyncio
async def test_outward_issue_valid_atomic_creation_and_fifo_depletion_201():
    data = await setup_outward_test_data()
    user = User(id=data["user_id"], email="engineer@comp1.com", role=UserRole.SITE_ENGINEER.value, is_active=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"outward.create", "outward.view"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Issue 35 bags (exhausts 30 bags from Batch 1 @ 440, and 5 bags from Batch 2 @ 465)
            # Valuation = (30 * 440) + (5 * 465) = 13200 + 2325 = 15525.00
            issue_payload = {
                "company_id": 1,
                "project_id": data["project_id"],
                "material_id": data["material_id"],
                "quantity": 35.0,
                "issue_type": "SITE_CONSUMPTION",
                "issued_to": "Foundation Casting Team",
                "remarks": "Pouring basement slab",
            }
            resp = await client.post("/api/v1/outward/issue/create", json=issue_payload)
            assert resp.status_code == 201, resp.text
            res_data = resp.json()

            issue_id = res_data["id"]
            assert res_data["issue_number"] is not None
            assert float(res_data["quantity"]) == 35.0
            total_valuation = sum(float(a["total_amount"]) for a in res_data["allocations"])
            assert total_valuation == 15525.00
            assert len(res_data["allocations"]) == 2

            # Database persistence verification
            async with AsyncSessionLocal() as db:
                mat = await db.get(Material, data["material_id"])
                assert mat.remaining_stock == Decimal("15.000")  # 50 - 35 = 15
                assert mat.quantity_used == Decimal("35.000")

                b1 = await db.get(StockBatch, data["batch1_id"])
                b2 = await db.get(StockBatch, data["batch2_id"])
                assert b1.current_balance == Decimal("0.000")  # Fully depleted
                assert b1.status == "DEPLETED"
                assert b2.current_balance == Decimal("15.000")  # 20 - 5 = 15
                assert b2.status == "ACTIVE"

                # Verify ledger record
                ledgers = (await db.execute(
                    select(MaterialLedger).where(MaterialLedger.material_id == data["material_id"])
                )).scalars().all()
                assert len(ledgers) >= 1
                issue_ledger = next((l for l in ledgers if l.type == TransactionType.USAGE), None)
                assert issue_ledger is not None
                assert issue_ledger.quantity == Decimal("35.000")


# ================= 8. GET SINGLE AND LIST ENDPOINTS (200) =================

@pytest.mark.asyncio
async def test_outward_issue_get_single_and_list_endpoints_200():
    data = await setup_outward_test_data()
    user = User(id=data["user_id"], email="engineer@comp1.com", role=UserRole.SITE_ENGINEER.value, is_active=True, company_id=1)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    with patch("app.core.dependencies.get_effective_user_permissions", new=AsyncMock(return_value={"outward.create", "outward.view"})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Create issue
            resp = await client.post("/api/v1/outward/issue/create", json={
                "company_id": 1,
                "project_id": data["project_id"],
                "material_id": data["material_id"],
                "quantity": 10.0,
                "issue_type": "SITE_CONSUMPTION",
            })
            assert resp.status_code == 201
            created = resp.json()
            issue_id = created["id"]

            # 2. Get single
            get_resp = await client.get(f"/api/v1/outward/issue/{issue_id}")
            assert get_resp.status_code == 200
            single_data = get_resp.json()
            assert single_data["id"] == issue_id
            assert float(single_data["quantity"]) == 10.0

            # 3. List
            list_resp = await client.get(f"/api/v1/outward/issue/list?project_id={data['project_id']}")
            assert list_resp.status_code == 200, list_resp.text
            items = list_resp.json()
            assert any(i["id"] == issue_id for i in items)

            # 4. Non-existent returns 404
            missing_resp = await client.get("/api/v1/outward/issue/999999")
            assert missing_resp.status_code == 404
