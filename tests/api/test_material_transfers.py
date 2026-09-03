import pytest
import uuid
from decimal import Decimal
from datetime import datetime
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_roles
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus
from app.models.master_data import MaterialMaster, Unit
from app.models.material import (
    Material,
    Supplier,
    MaterialTransfer,
    MaterialTransaction,
    MaterialLedger,
)
from app.core.enums import TransactionType, IssueType, RateType

# Test users
company_a_admin = User(
    id=8001,
    email="admin_a_trf@test.com",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)

company_b_admin = User(
    id=8002,
    email="admin_b_trf@test.com",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=2,
)


def override_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    def mock_require_roles(roles):
        return lambda: user

    app.dependency_overrides[require_roles] = mock_require_roles


def clear_user_override():
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_overrides():
    yield
    clear_user_override()


async def setup_test_data():
    """Seed companies, owners, projects, suppliers, unit, master data, and source material."""
    async with AsyncSessionLocal() as db:
        # Ensure companies 1 & 2
        c1 = await db.get(Company, 1)
        if not c1:
            c1 = Company(id=1, name="Company A Transfers", is_active=True)
            db.add(c1)

        c2 = await db.get(Company, 2)
        if not c2:
            c2 = Company(id=2, name="Company B Transfers", is_active=True)
            db.add(c2)

        # Ensure Owner
        owner_a = await db.scalar(select(Owner).where(Owner.company_id == 1))
        if not owner_a:
            owner_a = Owner(
                name="Owner A",
                phone="9876543210",
                email="owner_a@test.com",
                company_id=1,
            )
            db.add(owner_a)
            await db.flush()

        owner_b = await db.scalar(select(Owner).where(Owner.company_id == 2))
        if not owner_b:
            owner_b = Owner(
                name="Owner B",
                phone="9876543211",
                email="owner_b@test.com",
                company_id=2,
            )
            db.add(owner_b)
            await db.flush()

        # Company A Projects: Proj 1 (Source) & Proj 2 (Destination)
        suffix = uuid.uuid4().hex[:6]
        p_a1 = Project(
            business_id=f"PRJ-A1-{suffix}",
            company_id=1,
            project_name=f"Project A Source {suffix}",
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        p_a2 = Project(
            business_id=f"PRJ-A2-{suffix}",
            company_id=1,
            project_name=f"Project A Dest {suffix}",
            owner_id=owner_a.id,
            status=ProjectStatus.ONGOING,
        )
        # Company B Project
        p_b1 = Project(
            business_id=f"PRJ-B1-{suffix}",
            company_id=2,
            project_name=f"Project B Foreign {suffix}",
            owner_id=owner_b.id,
            status=ProjectStatus.ONGOING,
        )
        db.add_all([p_a1, p_a2, p_b1])
        await db.flush()

        # Unit
        unit = await db.scalar(select(Unit))
        if not unit:
            unit = Unit(name="Bags", code="BAG")
            db.add(unit)
            await db.flush()

        # Material Master
        mm = await db.scalar(select(MaterialMaster))
        if not mm:
            mm = MaterialMaster(
                name="Portland Cement 53",
                category="Civil",
                default_unit_id=unit.id,
                is_active=True,
            )
            db.add(mm)
            await db.flush()

        # Supplier
        supp = Supplier(
            company_id=1,
            supplier_name=f"Cement Supp {suffix}",
            contact_person="Ramesh",
        )
        db.add(supp)
        await db.flush()

        # Source Material in Proj A1 with 100 qty purchased, 0 used => 100 remaining
        mat_code = f"MAT-{suffix.upper()}"
        source_mat = Material(
            material_code=mat_code,
            project_id=p_a1.id,
            material_master_id=mm.id,
            material_name="Portland Cement 53",
            category="Civil",
            unit_id=unit.id,
            supplier_id=supp.id,
            rate_type=RateType.FIXED,
            purchase_rate=Decimal("350.00"),
            quantity_purchased=Decimal("100.000"),
            quantity_used=Decimal("0.000"),
            remaining_stock=Decimal("100.000"),
            total_amount=Decimal("35000.00"),
            payment_given=Decimal("0.00"),
            payment_pending=Decimal("35000.00"),
            minimum_stock_level=Decimal("10.000"),
        )
        db.add(source_mat)
        await db.commit()
        await db.refresh(source_mat)
        await db.refresh(p_a1)
        await db.refresh(p_a2)
        await db.refresh(p_b1)

        return {
            "proj_a1_id": p_a1.id,
            "proj_a2_id": p_a2.id,
            "proj_b1_id": p_b1.id,
            "material_id": source_mat.id,
            "supplier_id": supp.id,
            "master_id": mm.id,
        }


@pytest.mark.asyncio
async def test_material_transfer_2step_workflow():
    data = await setup_test_data()
    p1 = data["proj_a1_id"]
    p2 = data["proj_a2_id"]
    mat_id = data["material_id"]

    override_user(company_a_admin)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. CREATE TRANSFER => Status must be PENDING, created_at non-null
        create_payload = {
            "material_id": mat_id,
            "from_project_id": p1,
            "to_project_id": p2,
            "quantity": 25.0,
        }
        resp = await ac.post("/api/v1/materials/transfers", json=create_payload)
        assert resp.status_code == 200, resp.text
        tr_data = resp.json()
        transfer_id = tr_data["id"]
        assert tr_data["status"] == "PENDING"
        assert tr_data["created_at"] is not None
        assert tr_data["quantity"] == 25.0

        # 2. Check stock NOT moved on POST
        async with AsyncSessionLocal() as db:
            src = await db.get(Material, mat_id)
            assert src.remaining_stock == Decimal("100.000")
            assert src.quantity_used == Decimal("0.000")

            # Check no destination material created yet
            dest = await db.scalar(
                select(Material).where(
                    Material.project_id == p2,
                    Material.material_master_id == data["master_id"],
                )
            )
            assert dest is None

        # 3. GET /transfers lists the transfer with created_at non-null
        list_resp = await ac.get("/api/v1/materials/transfers")
        assert list_resp.status_code == 200
        list_json = list_resp.json()
        assert list_json["total"] >= 1
        found = [t for t in list_json["data"] if t["id"] == transfer_id]
        assert len(found) == 1
        assert found[0]["created_at"] is not None
        assert found[0]["status"] == "PENDING"

        # 4. GET /transfers/{id} returns details with created_at non-null
        get_resp = await ac.get(f"/api/v1/materials/transfers/{transfer_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["created_at"] is not None
        assert get_resp.json()["status"] == "PENDING"

        # 5. COMPLETE TRANSFER (PUT /api/v1/materials/transfers/{id}?status=COMPLETED)
        complete_resp = await ac.put(f"/api/v1/materials/transfers/{transfer_id}?status=COMPLETED")
        assert complete_resp.status_code == 200, complete_resp.text
        completed_data = complete_resp.json()
        assert completed_data["status"] == "COMPLETED"
        assert completed_data["created_at"] is not None

        # 6. Verify atomic stock movement
        async with AsyncSessionLocal() as db:
            src = await db.get(Material, mat_id)
            assert src.quantity_used == Decimal("25.000")
            assert src.remaining_stock == Decimal("75.000")

            dest = await db.scalar(
                select(Material).where(
                    Material.project_id == p2,
                    Material.material_master_id == data["master_id"],
                )
            )
            assert dest is not None
            assert dest.quantity_purchased == Decimal("25.000")
            assert dest.remaining_stock == Decimal("25.000")

            # Verify OUT and IN transactions
            txs = (
                await db.execute(
                    select(MaterialTransaction).where(
                        MaterialTransaction.material_id.in_([src.id, dest.id])
                    )
                )
            ).scalars().all()
            assert len(txs) == 2
            tx_out = next(t for t in txs if t.type == TransactionType.TRANSFER_OUT)
            tx_in = next(t for t in txs if t.type == TransactionType.TRANSFER_IN)
            assert tx_out.quantity == Decimal("-25.000")
            assert tx_in.quantity == Decimal("25.000")
            assert tx_out.project_id == p1
            assert tx_in.project_id == p2

            # Verify OUT and IN ledgers
            ledgers = (
                await db.execute(
                    select(MaterialLedger).where(
                        MaterialLedger.material_id.in_([src.id, dest.id])
                    )
                )
            ).scalars().all()
            assert len(ledgers) == 2
            l_out = next(l for l in ledgers if l.type == TransactionType.TRANSFER_OUT)
            l_in = next(l for l in ledgers if l.type == TransactionType.TRANSFER_IN)
            assert l_out.quantity == Decimal("-25.000")
            assert l_in.quantity == Decimal("25.000")

        # 7. Reject duplicate completion (COMPLETED -> COMPLETED)
        dup_resp = await ac.put(f"/api/v1/materials/transfers/{transfer_id}?status=COMPLETED")
        assert dup_resp.status_code == 400
        assert "already completed" in dup_resp.json()["detail"].lower()

        # Verify stock NOT deducted a second time
        async with AsyncSessionLocal() as db:
            src = await db.get(Material, mat_id)
            assert src.remaining_stock == Decimal("75.000")


@pytest.mark.asyncio
async def test_material_transfer_cancellation():
    data = await setup_test_data()
    p1 = data["proj_a1_id"]
    p2 = data["proj_a2_id"]
    mat_id = data["material_id"]

    override_user(company_a_admin)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Create transfer
        resp = await ac.post(
            "/api/v1/materials/transfers",
            json={
                "material_id": mat_id,
                "from_project_id": p1,
                "to_project_id": p2,
                "quantity": 10.0,
            },
        )
        assert resp.status_code == 200
        tr_id = resp.json()["id"]

        # 2. Cancel transfer
        cancel_resp = await ac.put(f"/api/v1/materials/transfers/{tr_id}?status=CANCELLED")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "CANCELLED"
        assert cancel_resp.json()["created_at"] is not None

        # 3. Verify stock NOT modified
        async with AsyncSessionLocal() as db:
            src = await db.get(Material, mat_id)
            assert src.remaining_stock == Decimal("100.000")
            assert src.quantity_used == Decimal("0.000")

        # 4. Reject completion after cancellation (CANCELLED -> COMPLETED)
        comp_resp = await ac.put(f"/api/v1/materials/transfers/{tr_id}?status=COMPLETED")
        assert comp_resp.status_code == 400
        assert "already cancelled" in comp_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_material_transfer_invalid_transitions():
    data = await setup_test_data()
    p1 = data["proj_a1_id"]
    p2 = data["proj_a2_id"]
    mat_id = data["material_id"]

    override_user(company_a_admin)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/materials/transfers",
            json={
                "material_id": mat_id,
                "from_project_id": p1,
                "to_project_id": p2,
                "quantity": 5.0,
            },
        )
        assert resp.status_code == 200
        tr_id = resp.json()["id"]

        # Reject PENDING -> PENDING
        p_resp = await ac.put(f"/api/v1/materials/transfers/{tr_id}?status=PENDING")
        assert p_resp.status_code == 400

        # Reject PENDING -> INVALID
        inv_resp = await ac.put(f"/api/v1/materials/transfers/{tr_id}?status=IN_TRANSIT")
        assert inv_resp.status_code == 400


@pytest.mark.asyncio
async def test_material_transfer_validation_rules():
    data = await setup_test_data()
    p1 = data["proj_a1_id"]
    p2 = data["proj_a2_id"]
    mat_id = data["material_id"]

    override_user(company_a_admin)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Zero quantity
        resp = await ac.post(
            "/api/v1/materials/transfers",
            json={
                "material_id": mat_id,
                "from_project_id": p1,
                "to_project_id": p2,
                "quantity": 0.0,
            },
        )
        assert resp.status_code in (400, 422)

        # 2. Negative quantity
        resp = await ac.post(
            "/api/v1/materials/transfers",
            json={
                "material_id": mat_id,
                "from_project_id": p1,
                "to_project_id": p2,
                "quantity": -5.0,
            },
        )
        assert resp.status_code in (400, 422)

        # 3. Same source and destination project
        resp = await ac.post(
            "/api/v1/materials/transfers",
            json={
                "material_id": mat_id,
                "from_project_id": p1,
                "to_project_id": p1,
                "quantity": 5.0,
            },
        )
        assert resp.status_code in (400, 422)

        # 4. Insufficient stock on create
        resp = await ac.post(
            "/api/v1/materials/transfers",
            json={
                "material_id": mat_id,
                "from_project_id": p1,
                "to_project_id": p2,
                "quantity": 500.0,
            },
        )
        assert resp.status_code == 400
        assert "insufficient stock" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_material_transfer_tenant_isolation_idor():
    data = await setup_test_data()
    p1 = data["proj_a1_id"]
    p2 = data["proj_a2_id"]
    pb1 = data["proj_b1_id"]
    mat_id = data["material_id"]

    # 1. Company A creates a transfer between Company A projects
    override_user(company_a_admin)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/materials/transfers",
            json={
                "material_id": mat_id,
                "from_project_id": p1,
                "to_project_id": p2,
                "quantity": 5.0,
            },
        )
        assert resp.status_code == 200
        tr_id = resp.json()["id"]

        # 2. Company A tries to transfer to Company B project -> Blocked
        cross_resp = await ac.post(
            "/api/v1/materials/transfers",
            json={
                "material_id": mat_id,
                "from_project_id": p1,
                "to_project_id": pb1,
                "quantity": 5.0,
            },
        )
        assert cross_resp.status_code in (403, 404)

        # 3. Company B tries to view Company A's transfer -> 404
        override_user(company_b_admin)
        get_b = await ac.get(f"/api/v1/materials/transfers/{tr_id}")
        assert get_b.status_code == 404

        # 4. Company B tries to complete Company A's transfer -> 404
        put_b = await ac.put(f"/api/v1/materials/transfers/{tr_id}?status=COMPLETED")
        assert put_b.status_code == 404

        # 5. Company B lists transfers -> does not see Company A's transfer
        list_b = await ac.get("/api/v1/materials/transfers")
        assert list_b.status_code == 200
        assert not any(t["id"] == tr_id for t in list_b.json()["data"])
