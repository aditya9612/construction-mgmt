import pytest
import uuid
import asyncio
from sqlalchemy import select


from app.db.session import AsyncSessionLocal
import app.db.base
from app.models.subscription import Subscription, Plan
from app.models.company import Company
from app.models.user import User, ActivityLog
from app.services.billing.mock_provider import MockPaymentProvider
from app.services.billing.reconciliation_service import BillingReconciliationService

pytestmark = pytest.mark.asyncio


async def _create_test_company_and_sub(db, status="active", ext_sub_id=None, ext_cus_id=None):
    plan = await db.scalar(select(Plan).where(Plan.is_active == True).limit(1))
    comp_name = f"Recon Test {uuid.uuid4().hex[:8]}"
    company = Company(name=comp_name, subdomain=comp_name.lower(), is_active=True)
    db.add(company)
    await db.commit()
    await db.refresh(company)

    sub = Subscription(
        company_id=company.id,
        plan_id=plan.id,
        status=status,
        external_subscription_id=ext_sub_id,
        external_customer_id=ext_cus_id,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return company, sub


async def test_reconciliation_matching_active_state():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        company, sub = await _create_test_company_and_sub(
            db, status="active", ext_sub_id=sub_id, ext_cus_id=f"cus_{uuid.uuid4().hex[:8]}"
        )

        service = BillingReconciliationService(provider)
        result = await service.reconcile_tenant(db, company.id)

        assert result["is_matched"] is True
        assert result["has_drift"] is False
        assert result["drift_type"] == "none"
        assert result["local_status"] == "active"
        assert result["provider_status"] == "active"


async def test_reconciliation_matching_past_due_state():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        sub_id = f"sub_past_due_{uuid.uuid4().hex[:8]}"
        company, sub = await _create_test_company_and_sub(
            db, status="past_due", ext_sub_id=sub_id, ext_cus_id=f"cus_{uuid.uuid4().hex[:8]}"
        )

        service = BillingReconciliationService(provider)
        result = await service.reconcile_tenant(db, company.id)

        assert result["is_matched"] is True
        assert result["has_drift"] is False
        assert result["drift_type"] == "none"
        assert result["local_status"] == "past_due"
        assert result["provider_status"] == "halted"


async def test_reconciliation_local_active_provider_inactive_drift():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        sub_id = f"sub_inactive_{uuid.uuid4().hex[:8]}"
        company, sub = await _create_test_company_and_sub(
            db, status="active", ext_sub_id=sub_id, ext_cus_id=f"cus_{uuid.uuid4().hex[:8]}"
        )

        service = BillingReconciliationService(provider)
        result = await service.reconcile_tenant(db, company.id)

        assert result["is_matched"] is False
        assert result["has_drift"] is True
        assert result["drift_type"] == "local_active_provider_inactive"
        # Local subscription status must NOT be destructively mutated in V1 detection
        await db.refresh(sub)
        assert sub.status == "active"


async def test_reconciliation_local_past_due_provider_active_drift():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        sub_id = f"sub_active_{uuid.uuid4().hex[:8]}"
        company, sub = await _create_test_company_and_sub(
            db, status="past_due", ext_sub_id=sub_id, ext_cus_id=f"cus_{uuid.uuid4().hex[:8]}"
        )

        service = BillingReconciliationService(provider)
        result = await service.reconcile_tenant(db, company.id)

        assert result["is_matched"] is False
        assert result["has_drift"] is True
        assert result["drift_type"] == "local_past_due_provider_active"
        # Local subscription status remains unchanged
        await db.refresh(sub)
        assert sub.status == "past_due"


async def test_reconciliation_cancelled_subscription_never_auto_reactivated():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        sub_id = f"sub_active_{uuid.uuid4().hex[:8]}"
        company, sub = await _create_test_company_and_sub(
            db, status="cancelled", ext_sub_id=sub_id, ext_cus_id=f"cus_{uuid.uuid4().hex[:8]}"
        )

        service = BillingReconciliationService(provider)
        result = await service.reconcile_tenant(db, company.id)

        assert result["is_matched"] is False
        assert result["has_drift"] is True
        assert result["drift_type"] == "local_cancelled_provider_active"
        # Cancelled subscription must NEVER be automatically reactivated
        await db.refresh(sub)
        assert sub.status == "cancelled"


async def test_reconciliation_missing_external_reference():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        # Non-trial subscription with no external identifiers
        company, sub = await _create_test_company_and_sub(
            db, status="active", ext_sub_id=None, ext_cus_id=None
        )

        service = BillingReconciliationService(provider)
        result = await service.reconcile_tenant(db, company.id)

        assert result["is_matched"] is False
        assert result["has_drift"] is True
        assert result["drift_type"] == "missing_external_identifiers"


async def test_reconciliation_internal_trial_alignment():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        # Trial subscription with no external identifiers is aligned
        company, sub = await _create_test_company_and_sub(
            db, status="trial", ext_sub_id=None, ext_cus_id=None
        )

        service = BillingReconciliationService(provider)
        result = await service.reconcile_tenant(db, company.id)

        assert result["is_matched"] is True
        assert result["has_drift"] is False
        assert result["drift_type"] == "none"


async def test_reconciliation_provider_unavailable_graceful_handling():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        sub_id = f"sub_error_{uuid.uuid4().hex[:8]}"
        company, sub = await _create_test_company_and_sub(
            db, status="active", ext_sub_id=sub_id, ext_cus_id=f"cus_{uuid.uuid4().hex[:8]}"
        )

        service = BillingReconciliationService(provider)
        result = await service.reconcile_tenant(db, company.id)

        assert result["is_matched"] is False
        assert result["has_drift"] is True
        assert result["drift_type"] == "provider_unavailable"
        assert "Payment provider returned error" in result["details"]


async def test_reconciliation_no_secrets_in_result_or_audit():
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        company, sub = await _create_test_company_and_sub(
            db, status="active", ext_sub_id=sub_id, ext_cus_id=f"cus_{uuid.uuid4().hex[:8]}"
        )

        user = await db.scalar(select(User).limit(1))
        service = BillingReconciliationService(provider)
        result = await service.reconcile_tenant(db, company.id, current_user=user)

        # Ensure no secrets in dict
        for key in ["secret", "key", "signature", "token", "password", "raw"]:
            assert key not in result

        # Check ActivityLog
        if user:
            log = await db.scalar(
                select(ActivityLog)
                .where(
                    ActivityLog.action == "BILLING_RECONCILIATION_PERFORMED",
                    ActivityLog.entity_id == company.id,
                )
                .order_by(ActivityLog.id.desc())
            )
            assert log is not None
            assert "secret" not in str(log.details)
            assert "token" not in str(log.details)


async def test_multi_tenant_batch_reconciliation():
    """Verify batch processing across all companies without in-memory overflow."""
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        # Seed 3 companies: 1 matched, 1 drifted, 1 trial
        sub_id_1 = f"sub_matched_{uuid.uuid4().hex[:8]}"
        c1, s1 = await _create_test_company_and_sub(db, status="active", ext_sub_id=sub_id_1, ext_cus_id="cus_1")

        sub_id_2 = f"sub_drifted_{uuid.uuid4().hex[:8]}"
        provider.set_subscription_status(sub_id_2, {"status": "halted", "id": sub_id_2})
        c2, s2 = await _create_test_company_and_sub(db, status="active", ext_sub_id=sub_id_2, ext_cus_id="cus_2")

        c3, s3 = await _create_test_company_and_sub(db, status="trial", ext_sub_id=None, ext_cus_id=None)

        service = BillingReconciliationService(provider)
        summary = await service.reconcile_all_tenants(db, batch_size=2)

        assert summary["total_reconciled"] >= 3
        assert summary["total_matched"] >= 2  # c1 and c3
        assert summary["total_drifted"] >= 1  # c2
        assert "results" in summary
        assert len(summary["results"]) == summary["total_reconciled"]

        # Ensure no mutations occurred
        await db.refresh(s1)
        await db.refresh(s2)
        assert s1.status == "active"
        assert s2.status == "active"


async def test_multi_tenant_failure_isolation():
    """Verify that a provider failure for one tenant does not abort the batch."""
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        sub_err = f"sub_error_{uuid.uuid4().hex[:8]}"
        c_err, s_err = await _create_test_company_and_sub(db, status="active", ext_sub_id=sub_err, ext_cus_id="cus_err")

        sub_ok = f"sub_ok_{uuid.uuid4().hex[:8]}"
        c_ok, s_ok = await _create_test_company_and_sub(db, status="active", ext_sub_id=sub_ok, ext_cus_id="cus_ok")

        service = BillingReconciliationService(provider)
        summary = await service.reconcile_all_tenants(db, batch_size=10)

        # Batch completes successfully
        assert summary["total_reconciled"] >= 2
        assert summary["total_unavailable"] >= 1

        # Error details captured without crash
        err_res = next((r for r in summary["results"] if r["company_id"] == c_err.id), None)
        assert err_res is not None
        assert err_res["drift_type"] == "provider_unavailable"


async def test_reconciliation_concurrent_safety():
    """Verify concurrent read-only reconciliations execute safely without deadlocks."""
    async with AsyncSessionLocal() as db:
        provider = MockPaymentProvider()
        sub_id = f"sub_conc_{uuid.uuid4().hex[:8]}"
        comp, sub = await _create_test_company_and_sub(db, status="active", ext_sub_id=sub_id, ext_cus_id="cus_conc")
        service = BillingReconciliationService(provider)

        # Run two reconciliations concurrently
        res1, res2 = await asyncio.gather(
            service.reconcile_tenant(db, comp.id),
            service.reconcile_tenant(db, comp.id),
        )

        assert res1["is_matched"] is True
        assert res2["is_matched"] is True


def test_superadmin_platform_reconciliation_api():
    """Verify GET /api/v1/superadmin/billing/reconciliation RBAC and response structure."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.dependencies import get_current_user, get_current_active_user, require_super_admin
    from app.models.user import UserRole

    client = TestClient(app)

    super_user = User(id=1025, email="super@platform.com", is_super_admin=True, role=UserRole.ADMIN.value, company_id=None)
    tenant_user = User(id=2001, email="admin@tenant.com", is_super_admin=False, role=UserRole.ADMIN.value, company_id=1)

    # 1. Unauthenticated -> 401
    app.dependency_overrides.clear()
    assert client.get("/api/v1/superadmin/billing/reconciliation").status_code == 401

    # 2. Tenant Admin -> 403
    app.dependency_overrides[get_current_user] = lambda: tenant_user
    app.dependency_overrides[get_current_active_user] = lambda: tenant_user
    assert client.get("/api/v1/superadmin/billing/reconciliation").status_code == 403

    # 3. Super Admin -> 200
    app.dependency_overrides[get_current_user] = lambda: super_user
    app.dependency_overrides[get_current_active_user] = lambda: super_user
    app.dependency_overrides[require_super_admin] = lambda: super_user

    res = client.get("/api/v1/superadmin/billing/reconciliation?batch_size=10")
    assert res.status_code == 200
    data = res.json()
    assert "total_reconciled" in data
    assert "total_matched" in data
    assert "total_drifted" in data
    assert "total_unavailable" in data
    assert "results" in data

    app.dependency_overrides.clear()
