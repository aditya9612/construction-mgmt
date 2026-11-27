import pytest
import uuid
from decimal import Decimal
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.main import app
from app.db.session import AsyncSessionLocal, get_db_session
from app.core.dependencies import get_current_user, get_current_active_user
from app.core.enums import InvoiceStatus, InvoiceType
from app.models.user import User
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project
from app.models.material import Supplier
from app.models.accountant import VendorBill
from app.models.invoice import Invoice, Transaction
from app.models.billing import RABill

@asynccontextmanager
async def setup_report_data():
    async with AsyncSessionLocal() as db:
        uid = uuid.uuid4().hex[:6]
        
        comp = Company(name=f"ReportComp_{uid}")
        db.add(comp)
        await db.flush()
        
        owner = Owner(
            company_id=comp.id,
            owner_code=f"REP-{uid}",
            owner_name=f"Report Owner {uid}",
            mobile=f"99{uuid.uuid4().int % 100000000:08d}",
            email=f"repowner_{uid}@test.com",
        )
        db.add(owner)
        await db.flush()
        
        proj = Project(
            company_id=comp.id,
            owner_id=owner.id,
            business_id=f"PRJ-{uid}",
            project_name=f"Report Project {uid}",
            start_date=date.today(),
        )
        db.add(proj)
        await db.flush()
        
        supplier = Supplier(
            company_id=comp.id,
            supplier_name=f"Report Supplier {uid}",
        )
        db.add(supplier)
        await db.flush()
        
        # Test 1: Vendor Aging Data
        today = date(2026, 1, 15) # Fixed as_of_date
        
        # not_due: due tomorrow
        vb_not_due = VendorBill(
            company_id=comp.id, supplier_id=supplier.id,
            bill_number=f"VB1_{uid}", bill_date=today, due_date=today + timedelta(days=1),
            total_amount=Decimal("1000"), amount_paid=Decimal("0"), status="PENDING"
        )
        
        # 1-30 days: due 10 days ago (2026-01-05)
        vb_1_30 = VendorBill(
            company_id=comp.id, supplier_id=supplier.id,
            bill_number=f"VB2_{uid}", bill_date=today, due_date=today - timedelta(days=10),
            total_amount=Decimal("2000"), amount_paid=Decimal("500"), status="PENDING" # 1500 outstanding
        )
        
        # 31-60 days: due 45 days ago (2025-12-01)
        vb_31_60 = VendorBill(
            company_id=comp.id, supplier_id=supplier.id,
            bill_number=f"VB3_{uid}", bill_date=today, due_date=today - timedelta(days=45),
            total_amount=Decimal("3000"), amount_paid=Decimal("0"), status="PENDING"
        )
        
        # 61-90 days: due 75 days ago (2025-11-01)
        vb_61_90 = VendorBill(
            company_id=comp.id, supplier_id=supplier.id,
            bill_number=f"VB4_{uid}", bill_date=today, due_date=today - timedelta(days=75),
            total_amount=Decimal("4000"), amount_paid=Decimal("0"), status="PENDING"
        )
        
        # 90+ days: due 100 days ago (2025-10-07)
        vb_90_plus = VendorBill(
            company_id=comp.id, supplier_id=supplier.id,
            bill_number=f"VB5_{uid}", bill_date=today, due_date=today - timedelta(days=100),
            total_amount=Decimal("5000"), amount_paid=Decimal("0"), status="PENDING"
        )
        
        # Fully paid (should not appear in outstanding)
        vb_paid = VendorBill(
            company_id=comp.id, supplier_id=supplier.id,
            bill_number=f"VB6_{uid}", bill_date=today, due_date=today - timedelta(days=5),
            total_amount=Decimal("100"), amount_paid=Decimal("100"), status="PENDING"
        )
        
        db.add_all([vb_not_due, vb_1_30, vb_31_60, vb_61_90, vb_90_plus, vb_paid])
        
        # Test 2 & 3 & 4: Billing Reconciliation Data
        
        # Invoice 1: Paid inside date range (2026-02-01)
        inv_1 = Invoice(
            company_id=comp.id, project_id=proj.id, owner_id=owner.id,
            type=InvoiceType.OWNER, amount=Decimal("1000"), total_amount=Decimal("1000"),
            paid_amount=Decimal("1000"), pending_amount=Decimal("0"),
            status=InvoiceStatus.PAID, created_at=datetime(2026, 2, 1, 10, 0)
        )
        
        # Invoice 2: Unpaid inside date range (2026-02-15)
        inv_2 = Invoice(
            company_id=comp.id, project_id=proj.id, owner_id=owner.id,
            type=InvoiceType.OWNER, amount=Decimal("2000"), total_amount=Decimal("2000"),
            paid_amount=Decimal("500"), pending_amount=Decimal("1500"),
            status=InvoiceStatus.PENDING, created_at=datetime(2026, 2, 15, 10, 0)
        )
        
        # Invoice 4: Outside date range (2026-04-01)
        inv_outside = Invoice(
            company_id=comp.id, project_id=proj.id, owner_id=owner.id,
            type=InvoiceType.OWNER, amount=Decimal("5000"), total_amount=Decimal("5000"),
            paid_amount=Decimal("0"), pending_amount=Decimal("5000"),
            status=InvoiceStatus.PENDING, created_at=datetime(2026, 4, 1, 10, 0)
        )
        
        db.add_all([inv_1, inv_2, inv_outside])
        
        # RABill 1: Paid inside date range (2026-02-10)
        ra_1 = RABill(
            project_id=proj.id, bill_number=f"RA1_{uid}",
            work_description="Work 1", quantity=Decimal("1"), rate=Decimal("1000"),
            gross_amount=Decimal("1000"), net_amount=Decimal("1000"), total_amount=Decimal("1000"),
            bill_date=date(2026, 2, 10), status="Paid"
        )
        
        # RABill 2: Unpaid inside date range (2026-02-25)
        ra_2 = RABill(
            project_id=proj.id, bill_number=f"RA2_{uid}",
            work_description="Work 2", quantity=Decimal("1"), rate=Decimal("2000"),
            gross_amount=Decimal("2000"), net_amount=Decimal("2000"), total_amount=Decimal("2000"),
            bill_date=date(2026, 2, 25), status="Approved"
        )
        
        # RABill 3: Cancelled (Should be ignored entirely)
        ra_cancelled = RABill(
            project_id=proj.id, bill_number=f"RA3_{uid}",
            work_description="Work 3", quantity=Decimal("1"), rate=Decimal("9999"),
            gross_amount=Decimal("9999"), net_amount=Decimal("9999"), total_amount=Decimal("9999"),
            bill_date=date(2026, 2, 25), status="CANCELLED"
        )
        
        db.add_all([ra_1, ra_2, ra_cancelled])
        
        # Transaction (Unallocated inside date range)
        txn_1 = Transaction(
            project_id=proj.id, type="receipt", amount=Decimal("300"),
            mode="bank", created_by=1, created_at=datetime(2026, 2, 5, 10, 0)
        )
        db.add(txn_1)
        
        await db.commit()
        
        yield {
            "comp_id": comp.id,
            "proj_id": proj.id,
            "supplier_id": supplier.id,
            "inv_1_id": inv_1.id,
            "inv_2_id": inv_2.id,
            "inv_outside_id": inv_outside.id,
            "ra_1_id": ra_1.id,
            "ra_2_id": ra_2.id,
            "ra_cancelled_id": ra_cancelled.id
        }
        
        # Cleanup
        await db.execute(delete(Transaction).where(Transaction.project_id == proj.id))
        await db.execute(delete(RABill).where(RABill.project_id == proj.id))
        await db.execute(delete(Invoice).where(Invoice.project_id == proj.id))
        await db.execute(delete(VendorBill).where(VendorBill.supplier_id == supplier.id))
        await db.execute(delete(Supplier).where(Supplier.id == supplier.id))
        await db.execute(delete(Project).where(Project.id == proj.id))
        await db.execute(delete(Owner).where(Owner.id == owner.id))
        await db.execute(delete(Company).where(Company.id == comp.id))
        await db.commit()

def make_accountant(user_id: int = 1, company_id: int = 1) -> User:
    return User(
        id=user_id, email=f"acc_{user_id}@test.com", role="Accountant",
        is_active=True, company_id=company_id, is_super_admin=False,
    )

@pytest.mark.asyncio
async def test_vendor_aging_sql_aggregation():
    app.dependency_overrides[get_current_user] = lambda: make_accountant()
    app.dependency_overrides[get_current_active_user] = lambda: make_accountant()
    
    async with setup_report_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # Test 5: Vendor Aging Date Control (Fixed as_of_date)
            resp = await ac.get(f"/api/v1/accountant/reports/vendor-aging?supplier_id={data['supplier_id']}&as_of_date=2026-01-15")
            assert resp.status_code == 200, resp.text
            res = resp.json()
            
            # Mathematical Assertions (Test 1)
            # Total Billed = 1000 + 2000 + 3000 + 4000 + 5000 + 100 = 15100
            assert Decimal(str(res["total_billed"])) == Decimal("15100")
            assert Decimal(str(res["total_paid"])) == Decimal("600") # 500 + 100
            assert Decimal(str(res["total_outstanding"])) == Decimal("14500")
            
            # Buckets
            assert Decimal(str(res["not_due_total"])) == Decimal("1000")
            assert Decimal(str(res["days_1_30_total"])) == Decimal("1500")
            assert Decimal(str(res["days_31_60_total"])) == Decimal("3000")
            assert Decimal(str(res["days_61_90_total"])) == Decimal("4000")
            assert Decimal(str(res["days_90_plus_total"])) == Decimal("5000")

@pytest.mark.asyncio
async def test_billing_reconciliation_calculations():
    app.dependency_overrides[get_current_user] = lambda: make_accountant()
    app.dependency_overrides[get_current_active_user] = lambda: make_accountant()
    
    async with setup_report_data() as data:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            
            # Test 4: Date Filter (Inside range only: Feb 1 to Feb 28, 2026)
            url = f"/api/v1/accountant/reports/billing-reconciliation?project_id={data['proj_id']}&start_date=2026-02-01&end_date=2026-02-28"
            resp = await ac.get(url)
            assert resp.status_code == 200, resp.text
            res = resp.json()
            
            # Expected items inside date range (and NOT CANCELLED):
            # inv_1: tot=1000, paid=1000, pend=0
            # inv_2: tot=2000, paid=500, pend=1500
            # ra_1: tot=1000, paid=1000, pend=0 (Paid status)
            # ra_2: tot=2000, paid=0, pend=2000 (Approved status)
            # inv_cancelled is ignored. inv_outside is outside date range.
            
            # Total Billed = 1000 + 2000 + 1000 + 2000 = 6000
            assert Decimal(str(res["total_billed"])) == Decimal("6000")
            
            # Total Received = 1000 (inv_1) + 500 (inv_2) + 1000 (ra_1 'Paid') = 2500
            assert Decimal(str(res["total_received"])) == Decimal("2500")
            
            # Total Outstanding = 1500 (inv_2) + 2000 (ra_2) = 3500
            assert Decimal(str(res["total_outstanding"])) == Decimal("3500")
            
            # Unallocated = 300 (txn_1)
            assert Decimal(str(res["total_unallocated_advances"])) == Decimal("300")
            
            # Test 3: Pagination Independence
            # There are exactly 4 items in the date range. Let's get limit=2
            resp_p1 = await ac.get(url + "&skip=0&limit=2")
            res_p1 = resp_p1.json()
            
            assert len(res_p1["items"]) == 2
            # Totals should STILL equal the global full-dataset sums
            assert Decimal(str(res_p1["total_billed"])) == Decimal("6000")
            assert Decimal(str(res_p1["total_received"])) == Decimal("2500")
            assert Decimal(str(res_p1["total_outstanding"])) == Decimal("3500")
            
            resp_p2 = await ac.get(url + "&skip=2&limit=2")
            res_p2 = resp_p2.json()
            assert len(res_p2["items"]) == 2
            # The items on page 2 should be different (checking reference / IDs indirectly)
            item_ids_p1 = [item["billing_id"] for item in res_p1["items"]]
            item_ids_p2 = [item["billing_id"] for item in res_p2["items"]]
            assert set(item_ids_p1).isdisjoint(set(item_ids_p2))
            
            # Totals remain the same on page 2
            assert Decimal(str(res_p2["total_billed"])) == Decimal("6000")
