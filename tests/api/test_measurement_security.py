import pytest
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.final_measurement import FinalMeasurement
from app.models.approval import Approval
from app.models.invoice import Invoice
from app.models.billing import RABill
from app.models.rbac import UserPermissionOverride, Permission
from tests.api.test_rbac_phase2_batch_n import setup_batch_n_data


@pytest.mark.asyncio
async def test_create_measurement_without_status_assigns_draft():
    """1. Creating a Measurement without status defaults strictly to DRAFT on the server."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 10.0,
                    "approved_rate": 50.0,
                },
            )
            assert res.status_code == 200
            meas_id = res.json()["id"]
            assert res.json()["status"] == "DRAFT"

        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert obj is not None
            assert obj.status == "DRAFT"


@pytest.mark.asyncio
async def test_create_measurement_with_approved_rejected():
    """2. Attempting to create a Measurement with APPROVED is rejected (422) and not saved."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 10.0,
                    "approved_rate": 50.0,
                    "status": "APPROVED",
                },
            )
            assert res.status_code in (400, 422)

        async with AsyncSessionLocal() as db:
            objs = (
                await db.execute(
                    select(FinalMeasurement).where(
                        FinalMeasurement.project_id == proj_id,
                        FinalMeasurement.status == "APPROVED",
                    )
                )
            ).scalars().all()
            assert len(objs) == 0, "No APPROVED measurement should be created"


@pytest.mark.asyncio
async def test_create_measurement_with_other_privileged_statuses_rejected():
    """3. Attempting to create with REJECTED, BILLED, VERIFIED, or SUBMITTED is rejected (422)."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for st in ["REJECTED", "BILLED", "VERIFIED", "SUBMITTED"]:
                res = await ac.post(
                    "/api/v1/measurements",
                    headers=headers,
                    json={
                        "project_id": proj_id,
                        "final_area": 10.0,
                        "approved_rate": 50.0,
                        "status": st,
                    },
                )
                assert res.status_code in (400, 422)


@pytest.mark.asyncio
async def test_create_measurement_arbitrary_status_rejected():
    """4. Arbitrary status values such as HACKED_STATUS are rejected (422)."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 10.0,
                    "approved_rate": 50.0,
                    "status": "HACKED_STATUS",
                },
            )
            assert res.status_code in (400, 422)


@pytest.mark.asyncio
async def test_low_privilege_user_cannot_create_approved_measurement():
    """5. A user with only measurements.create cannot create an already-approved Measurement."""
    async with setup_batch_n_data() as data:
        user_custom = data["user_custom_a"]
        token_custom = data["tokens"]["user_custom_a"]
        perm_create = data["perms"]["measurements.create"]
        proj_id = data["proj_a1"].id

        async with AsyncSessionLocal() as db:
            db.add(
                UserPermissionOverride(
                    user_id=user_custom.id,
                    permission_id=perm_create.id,
                    is_granted=True,
                )
            )
            await db.commit()

        headers = {"Authorization": f"Bearer {token_custom}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_bad = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 10.0,
                    "approved_rate": 50.0,
                    "status": "APPROVED",
                },
            )
            assert res_bad.status_code in (400, 422)

            res_good = await ac.post(
                "/api/v1/measurements",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "final_area": 10.0,
                    "approved_rate": 50.0,
                },
            )
            assert res_good.status_code == 200
            assert res_good.json()["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_general_update_cannot_elevate_status():
    """6. A user with measurements.edit cannot elevate a DRAFT or REJECTED Measurement through general update."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        meas_id = data["meas_a1"].id  # DRAFT
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Try setting APPROVED on DRAFT
            res = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"status": "APPROVED"},
            )
            assert res.status_code in (400, 422)

            # Try setting BILLED on DRAFT
            res_billed = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"status": "BILLED"},
            )
            assert res_billed.status_code in (400, 422)

        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert obj.status == "DRAFT"


@pytest.mark.asyncio
async def test_general_update_allows_legitimate_non_status_edits():
    """7. The general update endpoint continues to allow legitimate non-status edits."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        meas_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.put(
                f"/api/v1/measurements/{meas_id}",
                headers=headers,
                json={"final_area": 45.0, "approved_rate": 120.0},
            )
            assert res.status_code == 200
            assert res.json()["final_area"] == 45.0
            assert res.json()["approved_rate"] == 120.0
            assert res.json()["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_direct_status_updates_cannot_bypass_permissions_or_manual_blocks():
    """8. Direct status updates cannot manually assign APPROVED, REJECTED, or BILLED."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        meas_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res1 = await ac.put(
                f"/api/v1/measurements/{meas_id}/status",
                headers=headers,
                json={"status": "APPROVED"},
            )
            assert res1.status_code in (400, 422)

            res2 = await ac.put(
                f"/api/v1/measurements/{meas_id}/status",
                headers=headers,
                json={"status": "REJECTED"},
            )
            assert res2.status_code in (400, 422)

            res3 = await ac.put(
                f"/api/v1/measurements/{meas_id}/status",
                headers=headers,
                json={"status": "BILLED"},
            )
            assert res3.status_code in (400, 422)


@pytest.mark.asyncio
async def test_invalid_transitions_and_unauthorized_rollbacks_rejected():
    """9. Invalid transitions and unauthorized rollback attempts are rejected."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        meas_draft_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Cannot jump directly DRAFT -> VERIFIED
            res_jump = await ac.put(
                f"/api/v1/measurements/{meas_draft_id}/status",
                headers=headers,
                json={"status": "VERIFIED"},
            )
            assert res_jump.status_code in (400, 422)

            # Cannot reset DRAFT -> DRAFT
            res_draft = await ac.put(
                f"/api/v1/measurements/{meas_draft_id}/status",
                headers=headers,
                json={"status": "DRAFT"},
            )
            assert res_draft.status_code in (400, 422)

        # Set measurement to APPROVED directly in DB to test rollback prevention
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_draft_id)
            obj.status = "APPROVED"
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Attempt to rollback APPROVED -> DRAFT
            res_rb1 = await ac.put(
                f"/api/v1/measurements/{meas_draft_id}/status",
                headers=headers,
                json={"status": "DRAFT"},
            )
            assert res_rb1.status_code in (400, 422)

            # Attempt to transition APPROVED -> SUBMITTED
            res_rb2 = await ac.put(
                f"/api/v1/measurements/{meas_draft_id}/status",
                headers=headers,
                json={"status": "SUBMITTED"},
            )
            assert res_rb2.status_code in (400, 422)


@pytest.mark.asyncio
async def test_submission_creates_approval_tracking_record():
    """10. Submission creates the appropriate Approval tracking record."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["admin_a"]
        user_id = data["admin_a"].id
        meas_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.put(
                f"/api/v1/measurements/{meas_id}/status",
                headers=headers,
                json={"status": "SUBMITTED"},
            )
            assert res.status_code == 200
            assert res.json()["status"] == "SUBMITTED"

        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert obj.status == "SUBMITTED"

            approval = await db.scalar(
                select(Approval).where(
                    Approval.entity_type == "measurement",
                    Approval.entity_id == meas_id,
                    Approval.status == "Pending",
                )
            )
            assert approval is not None
            assert approval.requested_by == user_id


@pytest.mark.asyncio
async def test_verification_requires_permission():
    """11. Verification requires measurements.approve or approvals.approve permission."""
    async with setup_batch_n_data() as data:
        user_custom = data["user_custom_a"]
        token_custom = data["tokens"]["user_custom_a"]
        perm_edit = data["perms"]["measurements.edit"]
        perm_approve = data["perms"]["measurements.approve"]
        meas_id = data["meas_a2_submitted"].id  # Already SUBMITTED

        # Grant ONLY measurements.edit
        async with AsyncSessionLocal() as db:
            db.add(
                UserPermissionOverride(
                    user_id=user_custom.id,
                    permission_id=perm_edit.id,
                    is_granted=True,
                )
            )
            await db.commit()

        headers_custom = {"Authorization": f"Bearer {token_custom}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Attempt to verify without approval permission -> 403
            res_denied = await ac.put(
                f"/api/v1/measurements/{meas_id}/status",
                headers=headers_custom,
                json={"status": "VERIFIED"},
            )
            assert res_denied.status_code == 403

        # Now grant measurements.approve as well
        async with AsyncSessionLocal() as db:
            db.add(
                UserPermissionOverride(
                    user_id=user_custom.id,
                    permission_id=perm_approve.id,
                    is_granted=True,
                )
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res_ok = await ac.put(
                f"/api/v1/measurements/{meas_id}/status",
                headers=headers_custom,
                json={"status": "VERIFIED"},
            )
            assert res_ok.status_code == 200
            assert res_ok.json()["status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_invoicing_rejects_ineligible_measurements_and_sets_billed():
    """12. Invoicing rejects DRAFT measurements, succeeds for APPROVED, and sets status to BILLED."""
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        meas_draft_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Attempt invoice from DRAFT measurement -> 422
            res_draft = await ac.post(
                f"/api/v1/invoices/from-measurement/{meas_draft_id}",
                headers=headers,
            )
            assert res_draft.status_code in (400, 422)

        # Set status to APPROVED in DB
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_draft_id)
            obj.status = "APPROVED"
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Invoice creation from APPROVED measurement succeeds
            res_app = await ac.post(
                f"/api/v1/invoices/from-measurement/{meas_draft_id}",
                headers=headers,
            )
            assert res_app.status_code == 200
            inv_id = res_app.json()["id"]
            assert inv_id is not None

            # Duplicate invoicing rejected
            res_dup = await ac.post(
                f"/api/v1/invoices/from-measurement/{meas_draft_id}",
                headers=headers,
            )
            assert res_dup.status_code in (400, 422)

        # Verify DB status transitioned to BILLED
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_draft_id)
            assert obj.status == "BILLED"


@pytest.mark.asyncio
async def test_tenant_isolation_maintained_on_measurement():
    """13. Tenant isolation prevents cross-company access or modification."""
    async with setup_batch_n_data() as data:
        token_a = data["tokens"]["admin_a"]
        meas_b = data["meas_b1"].id  # Belongs to Company B
        headers = {"Authorization": f"Bearer {token_a}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # View Company B measurement -> 404
            res_get = await ac.get(f"/api/v1/measurements/{meas_b}", headers=headers)
            assert res_get.status_code == 404

            # Edit Company B measurement -> 404
            res_edit = await ac.put(
                f"/api/v1/measurements/{meas_b}",
                headers=headers,
                json={"final_area": 99.0},
            )
            assert res_edit.status_code == 404

            # Update status of Company B measurement -> 404
            res_st = await ac.put(
                f"/api/v1/measurements/{meas_b}/status",
                headers=headers,
                json={"status": "SUBMITTED"},
            )
            assert res_st.status_code == 404


@pytest.mark.asyncio
async def test_ra_billing_rejects_ineligible_measurements_and_sets_billed():
    """14. RA billing rejects DRAFT measurements, succeeds for APPROVED, and sets status to BILLED."""
    from datetime import date
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        meas_draft_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Billing with DRAFT measurement is rejected (422)
            res_draft = await ac.post(
                "/api/v1/billing",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "measurement_id": meas_draft_id,
                    "bill_number": f"RA-SEC-TEST-DRAFT-{data['uid']}",
                    "work_description": "Civil work",
                    "quantity": 10.0,
                    "rate": 100.0,
                    "bill_date": str(date.today()),
                },
            )
            assert res_draft.status_code in (400, 422)

        # 2. Set measurement to APPROVED
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_draft_id)
            obj.status = "APPROVED"
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Billing with APPROVED measurement succeeds
            res_app = await ac.post(
                "/api/v1/billing",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "measurement_id": meas_draft_id,
                    "bill_number": f"RA-SEC-TEST-APP-{data['uid']}",
                    "work_description": "Civil work",
                    "quantity": 10.0,
                    "rate": 100.0,
                    "bill_date": str(date.today()),
                },
            )
            assert res_app.status_code == 200

            # 3. Duplicate RA billing with same measurement rejected
            res_dup = await ac.post(
                "/api/v1/billing",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "measurement_id": meas_draft_id,
                    "bill_number": f"RA-SEC-TEST-DUP-{data['uid']}",
                    "work_description": "Civil work",
                    "quantity": 10.0,
                    "rate": 100.0,
                    "bill_date": str(date.today()),
                },
            )
            assert res_dup.status_code in (400, 422)

        # 4. Verify measurement transitioned to BILLED in DB
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_draft_id)
            assert obj.status == "BILLED"


@pytest.mark.asyncio
async def test_central_approval_workflow_functional_with_sod_and_rejection():
    """15. Central approvals workflow maintains Segregation of Duties and rejection remarks for measurements."""
    async with setup_batch_n_data() as data:
        token_admin = data["tokens"]["admin_a"]
        token_sa = data["tokens"]["super_admin"]
        meas_id = data["meas_a1"].id

        # Grant approvals.create and approvals.approve to admin_a
        async with AsyncSessionLocal() as db:
            for p_code in ["approvals.create", "approvals.approve"]:
                p = await db.scalar(select(Permission).where(Permission.code == p_code))
                if not p:
                    p = Permission(code=p_code, module="approvals", action=p_code.split(".")[1], description=p_code)
                    db.add(p)
                    await db.flush()
                db.add(UserPermissionOverride(user_id=data["admin_a"].id, permission_id=p.id, is_granted=True))
            await db.commit()

        headers_admin = {"Authorization": f"Bearer {token_admin}"}
        headers_sa = {"Authorization": f"Bearer {token_sa}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Submit measurement for approval
            res_sub = await ac.post(
                "/api/v1/approvals",
                headers=headers_admin,
                json={
                    "entity_type": "measurement",
                    "entity_id": meas_id,
                    "remarks": "Submitting for approval",
                },
            )
            assert res_sub.status_code == 200
            approval_id = res_sub.json()["id"]

            # 2. Requester cannot approve own request (Segregation of Duties) -> 400
            res_self_app = await ac.put(
                f"/api/v1/approvals/{approval_id}/approve",
                headers=headers_admin,
                json={"remarks": "Self approve attempt"},
            )
            assert res_self_app.status_code in (400, 422)

            # 3. Super Admin approves
            res_app = await ac.put(
                f"/api/v1/approvals/{approval_id}/approve",
                headers=headers_sa,
                json={"remarks": "Approved by supervisor"},
            )
            assert res_app.status_code == 200

        # Check measurement status is now APPROVED
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert obj.status == "APPROVED"


@pytest.mark.asyncio
async def test_cross_billing_prevention_invoice_then_ra_bill():
    """16. Creating an invoice blocks subsequent RA billing for the same measurement."""
    from datetime import date
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        meas_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        # Set measurement status to APPROVED
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            obj.status = "APPROVED"
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Invoice creation succeeds
            res_inv = await ac.post(
                f"/api/v1/invoices/from-measurement/{meas_id}",
                headers=headers,
            )
            assert res_inv.status_code == 200

            # 2. Subsequent RA billing with same measurement is rejected
            res_ra = await ac.post(
                "/api/v1/billing",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "measurement_id": meas_id,
                    "bill_number": f"RA-CROSS-INV-{data['uid']}",
                    "work_description": "Civil work duplicate test",
                    "quantity": 10.0,
                    "rate": 100.0,
                    "bill_date": str(date.today()),
                },
            )
            assert res_ra.status_code in (400, 422)


@pytest.mark.asyncio
async def test_cross_billing_prevention_ra_bill_then_invoice():
    """17. Creating an RA bill blocks subsequent invoice generation for the same measurement."""
    from datetime import date
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        proj_id = data["proj_a1"].id
        meas_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        # Set measurement status to VERIFIED
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            obj.status = "VERIFIED"
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. RA bill creation succeeds
            res_ra = await ac.post(
                "/api/v1/billing",
                headers=headers,
                json={
                    "project_id": proj_id,
                    "measurement_id": meas_id,
                    "bill_number": f"RA-CROSS-RA-{data['uid']}",
                    "work_description": "Civil work",
                    "quantity": 10.0,
                    "rate": 100.0,
                    "bill_date": str(date.today()),
                },
            )
            assert res_ra.status_code == 200

            # 2. Subsequent Invoice generation with same measurement is rejected
            res_inv = await ac.post(
                f"/api/v1/invoices/from-measurement/{meas_id}",
                headers=headers,
            )
            assert res_inv.status_code in (400, 422)


@pytest.mark.asyncio
async def test_transaction_rollback_reverts_measurement_billed_status_on_failure():
    """18. Transaction rollback ensures measurement status is not committed as BILLED if financial record fails."""
    from unittest.mock import patch
    async with setup_batch_n_data() as data:
        token = data["tokens"]["super_admin"]
        meas_id = data["meas_a1"].id
        headers = {"Authorization": f"Bearer {token}"}

        # Set measurement status to APPROVED
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            obj.status = "APPROVED"
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Mock _post_invoice_journal to simulate an unhandled failure during invoice processing
            with patch("app.api.invoice._post_invoice_journal", side_effect=RuntimeError("Simulated ledger failure")):
                with pytest.raises(RuntimeError):
                    await ac.post(
                        f"/api/v1/invoices/from-measurement/{meas_id}",
                        headers=headers,
                    )

        # Verify in DB that measurement status was NOT committed as BILLED; it remains APPROVED
        async with AsyncSessionLocal() as db:
            obj = await db.get(FinalMeasurement, meas_id)
            assert obj.status == "APPROVED", f"Expected APPROVED but found {obj.status}"
