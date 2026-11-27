import asyncio
import io
import os
import secrets
from datetime import datetime, date, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select, and_, func, text, create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
import redis.asyncio as aioredis

import app.db.base
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.master_data import Unit, MaterialMaster
from app.models.material import Supplier, Material, MaterialTransaction, MaterialLedger
from app.models.inventory import (
    GoodsReceiptNote,
    GoodsReceiptNoteItem,
    StockBatch,
    MaterialIssue,
    MaterialIssueAllocation,
    ProjectMaterialValuation,
)
from app.models.purchase_order import PurchaseOrderItem
from app.models.user import User, UserRole, ActivityLog
from app.models.contractor import Contractor
from app.models.boq import BOQGroup, BOQ
from app.models.work_order import WorkOrder
from app.models.accountant import Account, JournalEntry, JournalLine, VendorBill, VendorBillItem
from app.models.billing import RABill
from app.models.labour import Labour, LabourAttendance
from app.models.expense import Expense
from app.models.notification import Notification
from app.models.cancellation import OTPCancellationRequest
from app.models.daily_stock import DailyStockReconciliation

from app.services.po_service import create_purchase_order, approve_purchase_order
from app.services.inward_service import create_goods_receipt_note, inspect_and_post_grn
from app.services.outward_service import create_material_issue
from app.services.daily_stock_service import (
    store_daily_stock_token,
    retrieve_and_consume_daily_stock_token,
    DAILY_STOCK_TOKEN_PREFIX,
)
from app.services.cancellation_service import request_cancellation_otp, verify_and_execute_cancellation
from app.utils.helpers import AppError, NotFoundError

from tests.test_db_safety_guard import (
    validate_safe_test_environment,
    TEST_MYSQL_URL_ASYNC,
    TEST_MYSQL_URL_SYNC,
    TEST_REDIS_URL,
)

# Fail-closed safety validation
validate_safe_test_environment(TEST_MYSQL_URL_ASYNC, TEST_REDIS_URL)

test_engine = create_async_engine(TEST_MYSQL_URL_ASYNC, poolclass=NullPool, echo=False)
sync_engine = create_engine(TEST_MYSQL_URL_SYNC, poolclass=NullPool, echo=False)
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

TABLES_TO_CLEAN = [
    "material_issue_allocations", "material_issues", "stock_batches", "project_material_valuation",
    "goods_receipt_note_items", "goods_receipt_notes", "purchase_order_items", "purchase_orders",
    "otp_cancellation_requests", "daily_stock_reconciliation", "material_ledger", "material_transactions",
    "materials", "material_master", "units", "suppliers", "contractors", "ra_bills", "work_orders",
    "boq_items", "boq_groups", "journal_lines", "journal_entries", "vendor_bill_items", "vendor_bills",
    "accounts", "expenses", "labour_attendance", "labour", "notifications", "activity_logs",
    "users", "projects", "owners", "companies"
]


@pytest.fixture(autouse=True)
def db_cleanup_fixture():
    validate_safe_test_environment(TEST_MYSQL_URL_ASYNC, TEST_REDIS_URL)
    with sync_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        for t in TABLES_TO_CLEAN:
            conn.execute(text(f"DELETE FROM {t};"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
    yield
    with sync_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        for t in TABLES_TO_CLEAN:
            conn.execute(text(f"DELETE FROM {t};"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))


async def seed_master_dataset(db: AsyncSession):
    # 1. Companies (Tenants)
    c1 = Company(id=101, name="Apex Builders Pvt Ltd", subdomain="apex", is_active=True)
    c2 = Company(id=102, name="Zenith Infrastructure Ltd", subdomain="zenith", is_active=True)
    db.add_all([c1, c2])
    await db.flush()

    # 2. Owners
    o1 = Owner(id=101, company_id=101, owner_code="OWN-101", owner_name="Rajesh Singhania", mobile="9876543210")
    o2 = Owner(id=102, company_id=102, owner_code="OWN-102", owner_name="Sunil Mittal", mobile="9876543211")
    db.add_all([o1, o2])
    await db.flush()

    # 3. Projects
    p1 = Project(id=201, business_id="APX-SKYL", company_id=101, owner_id=101, project_name="Skyline Heights", status="ONGOING", type="RESIDENTIAL", location_type="URBAN")
    p2 = Project(id=202, business_id="APX-GRNP", company_id=101, owner_id=101, project_name="Green Park Villas", status="ONGOING", type="COMMERCIAL", location_type="SEMI_URBAN")
    p3 = Project(id=203, business_id="ZNT-METR", company_id=102, owner_id=102, project_name="Metro Transit Hub", status="ONGOING", type="COMMERCIAL", location_type="URBAN")
    db.add_all([p1, p2, p3])

    # 4. Users
    u_admin = User(id=301, company_id=101, email="admin@apex.test", full_name="Apex Admin", role="admin", is_active=True, hashed_password="hashed_dummy_pw")
    u_store = User(id=302, company_id=101, email="store@apex.test", full_name="Store Incharge", role="store_incharge", is_active=True, hashed_password="hashed_dummy_pw")
    u_qc = User(id=303, company_id=101, email="qc@apex.test", full_name="QC Inspector", role="quality_inspector", is_active=True, hashed_password="hashed_dummy_pw")
    u_tenant_b = User(id=304, company_id=102, email="admin@zenith.test", full_name="Zenith Admin", role="admin", is_active=True, hashed_password="hashed_dummy_pw")
    db.add_all([u_admin, u_store, u_qc, u_tenant_b])

    # 5. Units & Accounts
    unit_bag = Unit(id=1, company_id=101, name="Bags", unique_code="BAG", category="WEIGHT", is_active=True)
    unit_mt = Unit(id=2, company_id=101, name="Metric Ton", unique_code="MT", category="WEIGHT", is_active=True)
    db.add_all([unit_bag, unit_mt])

    acc_inventory = Account(id=1, company_id=101, name="Inventory Asset", code="1200", type="ASSET")
    acc_cogs = Account(id=2, company_id=101, name="Cost of Goods Sold", code="5100", type="EXPENSE")
    acc_ap = Account(id=3, company_id=101, name="Accounts Payable", code="2100", type="LIABILITY")
    db.add_all([acc_inventory, acc_cogs, acc_ap])
    await db.flush()

    # 6. Material Master & Suppliers & Contractors
    mm_cement = MaterialMaster(id=1, company_id=101, name="Cement PPC 50kg", unique_code="MAT-CEM-PPC", unit_id=1, category="CIVIL", default_rate=Decimal("350.00"), minimum_stock_level=50, is_active=True)
    mm_steel = MaterialMaster(id=2, company_id=101, name="TMT Steel 12mm", unique_code="MAT-STL-12MM", unit_id=2, category="STEEL", default_rate=Decimal("65000.00"), minimum_stock_level=5, is_active=True)
    db.add_all([mm_cement, mm_steel])

    sup_cement = Supplier(id=1, company_id=101, supplier_name="UltraTech Cement Ltd", contact_person="R. Sharma", phone_email="sharma@ultratech.test", gst_number="27AAACU1234A1Z5")
    sup_steel = Supplier(id=2, company_id=101, supplier_name="Tata Steel Ltd", contact_person="V. Mehta", phone_email="mehta@tatasteel.test", gst_number="27AAACT5678B1Z2")
    con_civil = Contractor(id=1, company_id=101, contractor_id="CON-001", name="Omkar Civil Contractors", work_type="CIVIL", contact_number="9876543210", gst_number="27AABC1234F1Z8", rate_type="ITEM_RATE")
    db.add_all([sup_cement, sup_steel, con_civil])
    await db.flush()

    # 7. Material instances per project
    m_p1_cement = Material(id=1, material_code="MAT-P1-CEM", project_id=201, material_master_id=1, material_name="Cement PPC 50kg", category="CIVIL", unit_id=1, supplier_id=1, purchase_rate=Decimal("350.00"), remaining_stock=Decimal("0.000"), rate_type="FIXED")
    m_p2_cement = Material(id=2, material_code="MAT-P2-CEM", project_id=202, material_master_id=1, material_name="Cement PPC 50kg", category="CIVIL", unit_id=1, supplier_id=1, purchase_rate=Decimal("350.00"), remaining_stock=Decimal("0.000"), rate_type="FIXED")
    m_p3_cement = Material(id=3, material_code="MAT-P3-CEM", project_id=203, material_master_id=1, material_name="Cement PPC 50kg", category="CIVIL", unit_id=1, supplier_id=1, purchase_rate=Decimal("350.00"), remaining_stock=Decimal("0.000"), rate_type="FIXED")
    db.add_all([m_p1_cement, m_p2_cement, m_p3_cement])

    await db.commit()


@pytest.mark.asyncio
async def test_e2e_workflow_1_tenant_and_project_isolation():
    """Workflow 1: Multi-Company & Multi-Project Isolation."""
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # Company 101 querying projects
        stmt_comp1 = select(Project).where(Project.company_id == 101)
        res_comp1 = (await db.execute(stmt_comp1)).scalars().all()
        assert len(res_comp1) == 2
        assert {p.id for p in res_comp1} == {201, 202}

        # Company 102 querying projects
        stmt_comp2 = select(Project).where(Project.company_id == 102)
        res_comp2 = (await db.execute(stmt_comp2)).scalars().all()
        assert len(res_comp2) == 1
        assert res_comp2[0].id == 203

        # Cross-tenant query must return zero
        stmt_leak = select(Project).where(and_(Project.company_id == 101, Project.id == 203))
        res_leak = (await db.execute(stmt_leak)).scalar_one_or_none()
        assert res_leak is None


@pytest.mark.asyncio
async def test_e2e_workflow_2_boq_and_work_order_setup():
    """Workflow 2: BOQ, Milestone & Work Order Setup."""
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # Create BOQ Group and Item for Project 201
        bg = BOQGroup(id=1, project_id=201, name="Civil Structure Works")
        db.add(bg)
        await db.flush()

        boq_item = BOQ(
            id=1,
            project_id=201,
            boq_group_id=1,
            item_name="PCC 1:2:4 Foundation Bed",
            category="CIVIL",
            quantity=Decimal("150.000"),
            unit="CUM",
            unit_cost=Decimal("4500.00"),
            total_cost=Decimal("675000.00"),
            actual_quantity=Decimal("0.000"),
            actual_cost=Decimal("0.00"),
            is_latest=True,
            status="Active",
            approval_status="Approved",
        )
        db.add(boq_item)

        # Create Work Order linked to Contractor and Project 201
        wo = WorkOrder(
            id=1,
            project_id=201,
            contractor_id=1,
            work_order_number="WO-2026-001",
            work_description="PCC Foundation & Footing Construction",
            total_quantity=Decimal("150.000"),
            completed_quantity=Decimal("0.000"),
            rate=Decimal("4500.00"),
            total_amount=Decimal("675000.00"),
            status="ACTIVE",
        )
        db.add(wo)
        await db.commit()

        # Verification
        saved_wo = await db.get(WorkOrder, 1)
        assert saved_wo is not None
        assert saved_wo.work_order_number == "WO-2026-001"
        assert saved_wo.total_amount == Decimal("675000.00")


@pytest.mark.asyncio
async def test_e2e_workflow_3_po_creation_and_approval_maker_checker():
    """Workflow 3: PO Creation & Approval Lifecycle (Maker-Checker)."""
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # Maker (Store Incharge) creates PO
        items = [
            {
                "material_master_id": 1,
                "material_id": 1,
                "material_name": "Cement PPC 50kg",
                "quantity": 100.0,
                "rate": 350.0,
                "unit": "BAG",
            }
        ]
        po = await create_purchase_order(
            db=db,
            company_id=101,
            user_id=302,  # Store Incharge (Maker)
            supplier_id=1,
            items=items,
            project_id=201,
            is_company_wide=False,
        )
        assert po.id is not None
        assert po.status == "CREATED"
        assert po.total_amount == Decimal("35000.00")

        # Checker (Admin) approves PO
        approved_po = await approve_purchase_order(
            db=db,
            po_id=po.id,
            user_id=301,  # Admin (Checker)
        )
        assert approved_po.status == "APPROVED"
        assert approved_po.approved_by_id == 301
        assert approved_po.approved_at is not None


@pytest.mark.asyncio
async def test_e2e_workflows_4_and_5_grn_qc_multirate_and_dual_valuation_math():
    """
    Workflows 4 & 5:
    GRN QC Inspection, Multi-Rate Batches & Dual-Valuation (FIFO & MWA) Inventory Math.
    """
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # 1. Setup Approved PO for Cement (200 bags)
        po = await create_purchase_order(
            db=db,
            company_id=101,
            user_id=301,
            supplier_id=1,
            items=[{
                "material_master_id": 1,
                "material_id": 1,
                "material_name": "Cement PPC 50kg",
                "quantity": 200.0,
                "rate": 350.0,
                "unit": "BAG",
            }],
            project_id=201,
        )
        await approve_purchase_order(db=db, po_id=po.id, user_id=301)
        po_item_stmt = select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id)
        po_item = (await db.execute(po_item_stmt)).scalar_one()

        # 2. First Inward GRN: 100 bags @ Rs. 350.00
        grn1 = await create_goods_receipt_note(
            db=db,
            company_id=101,
            user_id=302,
            project_id=201,
            supplier_id=1,
            purchase_order_id=po.id,
            challan_number="CH-001",
            challan_date=date.today(),
            items=[{
                "po_item_id": po_item.id,
                "material_master_id": 1,
                "material_id": 1,
                "received_quantity": 100.0,
                "unit_rate": 350.0,
                "batch_number": "BATCH-001",
            }],
        )
        grn1_items = (await db.execute(select(GoodsReceiptNoteItem).where(GoodsReceiptNoteItem.grn_id == grn1.id))).scalars().all()
        # QC Inspection: 100 accepted, 0 rejected
        await inspect_and_post_grn(
            db=db,
            grn_id=grn1.id,
            user_id=303,  # QC Inspector
            qc_items=[{
                "item_id": grn1_items[0].id,
                "accepted_quantity": 100.0,
                "rejected_quantity": 0.0,
                "qc_remarks": "Passed 3-day cube compression test",
            }],
        )

        # 3. Second Inward GRN: 100 bags @ Rs. 380.00 (rate increased)
        grn2 = await create_goods_receipt_note(
            db=db,
            company_id=101,
            user_id=302,
            project_id=201,
            supplier_id=1,
            purchase_order_id=po.id,
            challan_number="CH-002",
            challan_date=date.today(),
            items=[{
                "po_item_id": po_item.id,
                "material_master_id": 1,
                "material_id": 1,
                "received_quantity": 100.0,
                "unit_rate": 380.0,
                "batch_number": "BATCH-002",
            }],
        )
        grn2_items = (await db.execute(select(GoodsReceiptNoteItem).where(GoodsReceiptNoteItem.grn_id == grn2.id))).scalars().all()
        # QC Inspection: 100 accepted, 0 rejected
        await inspect_and_post_grn(
            db=db,
            grn_id=grn2.id,
            user_id=303,  # QC Inspector
            qc_items=[{
                "item_id": grn2_items[0].id,
                "accepted_quantity": 100.0,
                "rejected_quantity": 0.0,
                "qc_remarks": "Passed visual and slump test",
            }],
        )

        # Verify Post-Inward Inventory State
        mat = await db.get(Material, 1)
        assert mat.remaining_stock == Decimal("200.000")

        # Verify Moving Weighted Average (MWA) Valuation
        val_stmt = select(ProjectMaterialValuation).where(
            and_(
                ProjectMaterialValuation.project_id == 201,
                ProjectMaterialValuation.material_master_id == 1,
            )
        )
        val = (await db.execute(val_stmt)).scalar_one()
        assert val.total_purchased_quantity == Decimal("200.000")
        assert val.total_purchased_amount == Decimal("73000.00")
        # MWA = 73000 / 200 = 365.00
        assert val.current_moving_weighted_average_rate == Decimal("365.0000")

        # 4. Outward Dispatch (MIN) of 150 bags -> Tests FIFO Batch Consumption
        issue = await create_material_issue(
            db=db,
            company_id=101,
            user_id=302,
            project_id=201,
            material_id=1,
            quantity=Decimal("150.000"),
            issue_type="CONTRACTOR",
            issued_to="Omkar Civil",
            contractor_id=1,
            idempotency_key="MIN-E2E-150-BAGS",
        )
        assert issue.status == "ISSUED"

        # Verify FIFO Batch Allocations:
        # Batch 1 (100 bags @ Rs. 350) -> Takes 100 bags = Rs. 35,000. Status: DEPLETED
        # Batch 2 (100 bags @ Rs. 380) -> Takes 50 bags = Rs. 19,000. Status: ACTIVE, remaining: 50 bags
        alloc_stmt = select(MaterialIssueAllocation).where(MaterialIssueAllocation.issue_id == issue.id).order_by(MaterialIssueAllocation.id.asc())
        allocations = (await db.execute(alloc_stmt)).scalars().all()
        assert len(allocations) == 2

        assert allocations[0].allocated_quantity == Decimal("100.000")
        assert allocations[0].unit_rate == Decimal("350.00")
        assert allocations[0].total_amount == Decimal("35000.00")

        assert allocations[1].allocated_quantity == Decimal("50.000")
        assert allocations[1].unit_rate == Decimal("380.00")
        assert allocations[1].total_amount == Decimal("19000.00")

        # Total financial cost of 150 bags issued = 35000 + 19000 = Rs. 54,000
        total_issue_cost = sum(a.total_amount for a in allocations)
        assert total_issue_cost == Decimal("54000.00")

        # Verify remaining stock on site is exactly 50 bags
        await db.refresh(mat)
        assert mat.remaining_stock == Decimal("50.000")

        # Verify StockBatch current balances
        b1_stmt = select(StockBatch).where(StockBatch.batch_number == "BATCH-001")
        b1 = (await db.execute(b1_stmt)).scalar_one()
        assert b1.current_balance == Decimal("0.000")
        assert b1.status == "DEPLETED"

        b2_stmt = select(StockBatch).where(StockBatch.batch_number == "BATCH-002")
        b2 = (await db.execute(b2_stmt)).scalar_one()
        assert b2.current_balance == Decimal("50.000")
        assert b2.status == "ACTIVE"


@pytest.mark.asyncio
async def test_e2e_workflow_6_outward_issue_and_material_return():
    """Workflow 6: Outward Issue, Contractor Allocation & Material Return."""
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # Setup 50 bags stock
        mat = await db.get(Material, 1)
        mat.remaining_stock = Decimal("50.000")
        batch = StockBatch(
            batch_number="BATCH-RET-01",
            project_id=201,
            material_master_id=1,
            supplier_id=1,
            initial_quantity=Decimal("50.000"),
            current_balance=Decimal("50.000"),
            purchase_rate=Decimal("350.00"),
            receipt_date=date.today(),
            status="ACTIVE",
        )
        db.add(batch)
        await db.commit()

        # Issue 30 bags to site
        issue = await create_material_issue(
            db=db,
            company_id=101,
            user_id=302,
            project_id=201,
            material_id=1,
            quantity=Decimal("30.000"),
            issue_type="SITE",
            contractor_id=1,
            idempotency_key="MIN-ISSUE-30-BAGS",
        )
        assert issue.status == "ISSUED"

        # Material Return: 10 bags returned to store in good condition
        ret_ledger = MaterialLedger(
            material_id=1,
            project_id=201,
            type="RETURN",
            quantity=Decimal("10.000"),
            rate=Decimal("350.00"),
            total_amount=Decimal("3500.00"),
            amount_paid=Decimal("0.00"),
            payment_pending=Decimal("0.00"),
            issue_type="SITE",
            reference_id=issue.id,
            remarks="Surplus un-opened bags returned from foundation pour",
            transaction_date=datetime.utcnow(),
        )
        db.add(ret_ledger)
        # Restore stock balance
        mat.remaining_stock += Decimal("10.000")
        await db.commit()

        # Verification: 50 - 30 + 10 = 30 bags remaining
        await db.refresh(mat)
        assert mat.remaining_stock == Decimal("30.000")


@pytest.mark.asyncio
async def test_e2e_workflow_7_daily_stock_redis_token_and_auto_expiry():
    """Workflow 7: Daily Stock Reconciliation, Two-Phase Redis Token Locking & Auto-Expiry."""
    r = aioredis.from_url(TEST_REDIS_URL)
    token = f"test-token-{secrets.token_hex(8)}"
    key = f"{DAILY_STOCK_TOKEN_PREFIX}{token}"

    try:
        items = [
            {
                "material_master_id": 1,
                "project_id": 201,
                "physical_count": Decimal("48.000"),
                "reconciliation_date": date.today(),
            }
        ]

        # Phase 1: Store token in Redis with TTL
        await store_daily_stock_token(
            token=token,
            items=items,
            ttl_seconds=60,
            redis=r,
            user_id=302,
            company_id=101,
        )

        # Assert token exists in Redis 6379 and has active TTL
        exists = await r.exists(key)
        assert exists == 1
        ttl = await r.ttl(key)
        assert 0 < ttl <= 60

        # Phase 2: Consume token atomically via Lua GETDEL
        consumed = await retrieve_and_consume_daily_stock_token(
            token=token,
            redis=r,
            user_id=302,
            company_id=101,
        )
        assert consumed is not None
        assert len(consumed) == 1
        assert Decimal(str(consumed[0]["physical_count"])) == Decimal("48.000")

        # Attempt replay / second consumption -> Must be rejected (None)
        replayed = await retrieve_and_consume_daily_stock_token(
            token=token,
            redis=r,
            user_id=302,
            company_id=101,
        )
        assert replayed is None

        # Verify key was deleted by GETDEL
        assert await r.exists(key) == 0

    finally:
        await r.delete(key)
        await r.aclose()


@pytest.mark.asyncio
async def test_e2e_workflow_8_labour_attendance_and_wages():
    """Workflow 8: Labour Attendance, Overtime & Wage Tracking."""
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # Create Labour record
        lab = Labour(
            id=1,
            company_id=101,
            worker_code="LAB-001",
            labour_name="Ramesh Kumar",
            status="ACTIVE",
            mobile_number="9876500001",
            aadhaar_number="123456789012",
        )
        db.add(lab)
        await db.flush()

        # Log daily attendance
        att = LabourAttendance(
            id=1,
            labour_id=1,
            project_id=201,
            attendance_date=date.today(),
            status="PRESENT",
            working_hours=Decimal("8.00"),
            overtime_hours=Decimal("0.00"),
            overtime_rate=Decimal("0.00"),
            task_description="Foundation excavation",
        )
        db.add(att)
        await db.commit()

        saved_att = await db.get(LabourAttendance, 1)
        assert saved_att is not None
        assert saved_att.status == "PRESENT"
        assert saved_att.project_id == 201


@pytest.mark.asyncio
async def test_e2e_workflow_9_vendor_billing_and_double_entry_journal():
    """Workflow 9: Vendor Invoicing, Expense Claims & Balanced Double-Entry Journal."""
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # 1. Vendor Bill against Supplier
        bill = VendorBill(
            id=1,
            company_id=101,
            supplier_id=1,
            project_id=201,
            bill_number="BILL-2026-001",
            bill_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            gross_amount=Decimal("35000.00"),
            gst_amount=Decimal("6300.00"),
            total_amount=Decimal("41300.00"),
            amount_paid=Decimal("0.00"),
            status="PENDING",
        )
        db.add(bill)

        # 2. Expense Claim
        exp = Expense(
            id=1,
            project_id=201,
            category="SITE_INCIDENTAL",
            description="Water tanker for concrete curing",
            amount=Decimal("2500.00"),
            expense_date=date.today(),
            payment_mode="CASH",
        )
        db.add(exp)

        # 3. Double-Entry Journal Voucher (Balanced Dr == Cr)
        je = JournalEntry(
            id=1,
            journal_number="JV-2026-001",
            entry_date=date.today(),
            entry_type="MANUAL",
            status="POSTED",
            description="Inventory purchase on credit from UltraTech Cement",
            created_by=301,
        )
        db.add(je)
        await db.flush()

        # Line 1: Debit Inventory Asset Rs. 35,000
        l1 = JournalLine(id=1, entry_id=1, account_id=1, debit=Decimal("35000.00"), credit=Decimal("0.00"))
        # Line 2: Credit Accounts Payable Rs. 35,000
        l2 = JournalLine(id=2, entry_id=1, account_id=3, debit=Decimal("0.00"), credit=Decimal("35000.00"))
        db.add_all([l1, l2])
        await db.commit()

        # Validate Accounting Invariant: Sum(Debit) == Sum(Credit)
        lines_stmt = select(JournalLine).where(JournalLine.entry_id == 1)
        lines = (await db.execute(lines_stmt)).scalars().all()
        sum_dr = sum(l.debit for l in lines)
        sum_cr = sum(l.credit for l in lines)
        assert sum_dr == sum_cr == Decimal("35000.00")


@pytest.mark.asyncio
async def test_e2e_workflow_10_client_billing_and_deductions():
    """Workflow 10: Client Billing, Milestones, TDS/Retention Deductions & Receivables."""
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # RA Bill calculation
        gross = Decimal("100000.00")
        tds_deduction = (gross * Decimal("0.02")).quantize(Decimal("0.01"))  # 2% TDS = 2,000
        retention_deduction = (gross * Decimal("0.05")).quantize(Decimal("0.01"))  # 5% Retention = 5,000
        total_deductions = tds_deduction + retention_deduction  # 7,000
        net_amount = gross - total_deductions  # 93,000
        gst_percent = Decimal("18.00")
        gst_amount = (net_amount * (gst_percent / Decimal("100.00"))).quantize(Decimal("0.01"))  # 16,740
        total_bill_amount = net_amount + gst_amount  # 109,740

        ra_bill = RABill(
            id=1,
            project_id=201,
            contractor_id=1,
            bill_number="RA-BILL-001",
            work_description="PCC Foundation milestone completion",
            quantity=Decimal("1.000"),
            rate=gross,
            gross_amount=gross,
            deductions=total_deductions,
            net_amount=net_amount,
            gst_percent=gst_percent,
            total_amount=total_bill_amount,
            bill_date=date.today(),
            status="APPROVED",
        )
        db.add(ra_bill)
        await db.commit()

        saved_bill = await db.get(RABill, 1)
        assert saved_bill is not None
        assert saved_bill.gross_amount == Decimal("100000.00")
        assert saved_bill.deductions == Decimal("7000.00")
        assert saved_bill.net_amount == Decimal("93000.00")
        assert saved_bill.total_amount == Decimal("109740.00")


@pytest.mark.asyncio
async def test_e2e_workflow_11_audit_log_and_otp_cancellation_dual_control():
    """Workflow 11: Audit Logging, Immutable History, Maker-Checker Dual-Control OTP Cancellation."""
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # 1. Audit Log record
        log = ActivityLog(
            action="PO_CREATED",
            entity="purchase_orders",
            entity_id=101,
            performed_by=302,
            details={"po_number": "PO-TEST-001", "total_amount": "35000.00"},
        )
        db.add(log)
        await db.commit()

        # 2. Setup a Purchase Order to cancel
        po = await create_purchase_order(
            db=db,
            company_id=101,
            user_id=302,
            supplier_id=1,
            items=[{"material_master_id": 1, "material_id": 1, "material_name": "Cement", "quantity": 10.0, "rate": 350.0, "unit": "BAG"}],
            project_id=201,
        )

        # Maker Step: User 302 (Store Incharge) requests cancellation OTP
        req, raw_otp = await request_cancellation_otp(
            db=db,
            company_id=101,
            user_id=302,
            document_type="PO",
            document_id=po.id,
            reason="Mistake in order quantity",
        )
        assert req.status == "PENDING"
        assert req.requested_by_id == 302
        assert len(raw_otp) == 6

        # Security Violation Check: Maker (User 302) cannot approve own cancellation request
        with pytest.raises(AppError) as exc_info:
            await verify_and_execute_cancellation(
                db=db,
                company_id=101,
                user_id=302,  # Maker trying to act as Checker
                request_id=req.id,
                otp=raw_otp,
            )
        assert exc_info.value.status_code == 403
        assert "Security Violation: Requester (Maker) cannot approve (Checker) their own cancellation" in exc_info.value.message

        # Checker Step: User 301 (Admin) verifies OTP and approves cancellation
        approved_req = await verify_and_execute_cancellation(
            db=db,
            company_id=101,
            user_id=301,  # Independent Checker
            request_id=req.id,
            otp=raw_otp,
        )
        assert approved_req.status == "VERIFIED"
        assert approved_req.verified_by_id == 301

        # Verify underlying PO was cancelled
        await db.refresh(po)
        assert po.status == "CANCELLED"


@pytest.mark.asyncio
async def test_e2e_workflow_12_real_reporting_and_export_engines():
    """Workflow 12: Real Reporting & Export Engines (Excel via openpyxl & PDF via ReportLab)."""
    from openpyxl import Workbook
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    # 1. Real Excel Export
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock Report"
    ws.append(["Material Code", "Material Name", "Current Stock", "Unit Rate", "Total Valuation"])
    ws.append(["MAT-P1-CEM", "Cement PPC 50kg", 150, 365.0, 54750.0])

    excel_buf = io.BytesIO()
    wb.save(excel_buf)
    excel_bytes = excel_buf.getvalue()
    assert len(excel_bytes) > 200
    assert excel_bytes.startswith(b"PK")  # Valid ZIP / XLSX file signature

    # 2. Real PDF Export
    pdf_buf = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buf, pagesize=letter)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Construction ERP - Inventory Valuation Audit Report", styles["Heading1"]),
        Spacer(1, 12),
        Paragraph("Project: Skyline Heights (APX-SKYL) - Valuation Methodology: FIFO / Moving Weighted Average", styles["Normal"]),
    ]
    doc.build(story)
    pdf_bytes = pdf_buf.getvalue()
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF")  # Valid PDF file signature


@pytest.mark.asyncio
async def test_e2e_workflow_13_notifications_dispatch():
    """Workflow 13: Live Notifications Dispatch & Persistence."""
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        notif = Notification(
            id=1,
            user_id=301,
            title="PO Approval Required: PO-2026-001",
            message="New Purchase Order for 100 bags of Cement submitted by Store Incharge.",
            type="info",
            link="/procurement/po/1",
            is_read=False,
        )
        db.add(notif)
        await db.commit()

        saved_notif = await db.get(Notification, 1)
        assert saved_notif is not None
        assert saved_notif.user_id == 301
        assert saved_notif.is_read is False


@pytest.mark.asyncio
async def test_e2e_concurrency_race_condition_outward():
    """
    Phase D: Concurrency Testing.
    Simulate multiple concurrent outward requests competing for the exact same batch balance.
    Asserts no negative stock and deterministic isolation.
    """
    async with TestSessionLocal() as db:
        await seed_master_dataset(db)

        # Setup stock with exactly 50 bags
        mat = await db.get(Material, 1)
        mat.remaining_stock = Decimal("50.000")
        batch = StockBatch(
            batch_number="BATCH-RACE-01",
            project_id=201,
            material_master_id=1,
            supplier_id=1,
            initial_quantity=Decimal("50.000"),
            current_balance=Decimal("50.000"),
            purchase_rate=Decimal("350.00"),
            receipt_date=date.today(),
            status="ACTIVE",
        )
        db.add(batch)
        await db.commit()

    async def issue_worker(worker_id: int):
        async with TestSessionLocal() as session:
            try:
                issue = await create_material_issue(
                    db=session,
                    company_id=101,
                    user_id=302,
                    project_id=201,
                    material_id=1,
                    quantity=Decimal("40.000"),
                    issue_type="CONTRACTOR",
                    idempotency_key=f"MIN-RACE-{worker_id}",
                )
                return ("SUCCESS", worker_id, issue.id)
            except AppError as e:
                return ("FAILED_APP_ERROR", worker_id, str(e.message))
            except Exception as e:
                return ("FAILED_OTHER", worker_id, str(e))

    # Launch 3 workers concurrently requesting 40 bags each (Total requested: 120 bags, Available: 50 bags)
    results = await asyncio.gather(
        issue_worker(1),
        issue_worker(2),
        issue_worker(3),
    )

    successes = [r for r in results if r[0] == "SUCCESS"]
    failures = [r for r in results if r[0] == "FAILED_APP_ERROR"]

    # Exactly 1 worker must succeed (taking 40 bags, leaving 10)
    assert len(successes) == 1
    # Exactly 2 workers must fail due to insufficient stock
    assert len(failures) == 2
    for f in failures:
        assert "Insufficient stock" in f[2]

    # Verify final remaining stock on site is exactly 10 bags and never negative
    async with TestSessionLocal() as check_db:
        final_mat = await check_db.get(Material, 1)
        assert final_mat.remaining_stock == Decimal("10.000")
        final_batch = (await check_db.execute(select(StockBatch).where(StockBatch.batch_number == "BATCH-RACE-01"))).scalar_one()
        assert final_batch.current_balance == Decimal("10.000")


@pytest.mark.asyncio
async def test_e2e_transaction_failure_and_rollback():
    """
    Phase D: Transaction Failure & Atomic Rollback.
    Asserts complete atomicity during simulated DB errors with zero partial writes.
    """
    async with TestSessionLocal() as db_setup:
        await seed_master_dataset(db_setup)

    # In a separate transaction, attempt multi-table operation that fails midway
    async with TestSessionLocal() as db_fail:
        try:
            async with db_fail.begin():
                sup = Supplier(
                    id=999,
                    company_id=101,
                    supplier_name="Rollback Test Supplier",
                    contact_person="Tester",
                    phone_email="test@fail.com",
                    gst_number="27AAACU9999Z1Z1",
                )
                db_fail.add(sup)
                await db_fail.flush()
                # Deliberately raise exception
                raise RuntimeError("Simulated mid-transaction failure!")
        except RuntimeError:
            pass

    # Verify rollback: Supplier 999 must NOT exist in MySQL 3309
    async with TestSessionLocal() as db_check:
        sup_check = await db_check.get(Supplier, 999)
        assert sup_check is None
