from decimal import Decimal
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.labour import LabourPayroll
from app.models.rbac import Permission, RolePermission
from app.core.enums import PayrollStatus
from tests.api.test_rbac_phase2_batch_h import setup_batch_h_data


@pytest.mark.asyncio
async def test_pay_salary_draft_payroll_raises_400():
    """Verify that paying a DRAFT payroll raises 400 with proper error message instead of UnboundLocalError."""
    async with setup_batch_h_data() as d_data:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            token = d_data["tokens"]["admin_a"]
            headers = {"Authorization": f"Bearer {token}"}

            proj_id = d_data["proj_a"].id
            labour_id = d_data["labour_a"].id
            payroll_id = d_data["payroll_a"].id
            month = d_data["payroll_a"].month
            year = d_data["payroll_a"].year

            # Ensure admin has labour.approve permission
            async with AsyncSessionLocal() as db:
                p_papp = (await db.execute(select(Permission).where(Permission.code == "labour.approve"))).scalar_one_or_none()
                if not p_papp:
                    p_papp = Permission(code="labour.approve", module="labour", description="Approve labour")
                    db.add(p_papp)
                    await db.flush()

                # Set payroll status to DRAFT
                p = await db.get(LabourPayroll, payroll_id)
                p.status = PayrollStatus.DRAFT
                await db.commit()

            pay_payload = {
                "project_id": proj_id,
                "labour_id": labour_id,
                "month": month,
                "year": year,
                "amount": 100.0,
            }

            # 1. DRAFT payroll payment attempt
            res = await ac.post("/api/v1/labour/payroll/pay", json=pay_payload, headers=headers)
            assert res.status_code == 400, f"Expected 400 for DRAFT, got {res.status_code}: {res.text}"
            assert "Cannot pay DRAFT payroll. Please lock it first." in res.json().get("detail", "")

            # 2. PAID payroll payment attempt
            async with AsyncSessionLocal() as db:
                p = await db.get(LabourPayroll, payroll_id)
                p.status = PayrollStatus.PAID
                p.remaining_amount = Decimal("0.00")
                p.paid_amount = Decimal("1200.00")
                await db.commit()

            res_paid = await ac.post("/api/v1/labour/payroll/pay", json=pay_payload, headers=headers)
            assert res_paid.status_code == 400, f"Expected 400 for PAID, got {res_paid.status_code}: {res_paid.text}"
            assert "Payroll is already PAID in full." in res_paid.json().get("detail", "")
