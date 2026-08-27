import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException

from app.models.subscription import Subscription
from app.models.company import Company
from app.models.user import ActivityLog, User
from app.services.billing.provider_base import PaymentProviderInterface

logger = logging.getLogger(__name__)


class BillingReconciliationService:
    """
    Reconciliation service to detect drift between local Subscription state
    and payment provider subscription state.
    Read-only in V1 to avoid destructive mutations.
    """

    def __init__(self, provider: PaymentProviderInterface):
        self.provider = provider

    def _normalize_provider_status(self, raw_status: Optional[str]) -> str:
        if not raw_status:
            return "unknown"
        s = raw_status.lower().strip()
        if s in ("active", "authenticated"):
            return "active"
        if s in ("halted", "pending", "past_due"):
            return "past_due"
        if s in ("cancelled", "canceled"):
            return "cancelled"
        if s in ("expired", "completed"):
            return "expired"
        return s

    async def reconcile_tenant(
        self, db: AsyncSession, company_id: int, current_user: Optional[User] = None
    ) -> Dict[str, Any]:
        """
        Reconcile a specific tenant's subscription against the payment provider.
        """
        company = await db.get(Company, company_id)
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")

        sub_res = await db.execute(
            select(Subscription).where(Subscription.company_id == company_id)
        )
        subscription = sub_res.scalar_one_or_none()

        if not subscription:
            return {
                "company_id": company_id,
                "subscription_id": None,
                "local_status": None,
                "provider_name": self.provider.provider_name,
                "provider_subscription_id": None,
                "provider_status": None,
                "is_matched": False,
                "has_drift": True,
                "drift_type": "missing_local_subscription",
                "details": "No subscription record found for company.",
                "reconciled_at": datetime.utcnow(),
            }

        local_status = subscription.status
        ext_sub_id = subscription.external_subscription_id
        ext_cus_id = subscription.external_customer_id

        # If on trial and has no external subscription ID
        if not ext_sub_id and not ext_cus_id:
            if local_status == "trial":
                return {
                    "company_id": company_id,
                    "subscription_id": subscription.id,
                    "local_status": local_status,
                    "provider_name": self.provider.provider_name,
                    "provider_subscription_id": None,
                    "provider_status": None,
                    "is_matched": True,
                    "has_drift": False,
                    "drift_type": "none",
                    "details": "Tenant is on an internal trial with no external provider subscription.",
                    "reconciled_at": datetime.utcnow(),
                }
            else:
                return {
                    "company_id": company_id,
                    "subscription_id": subscription.id,
                    "local_status": local_status,
                    "provider_name": self.provider.provider_name,
                    "provider_subscription_id": None,
                    "provider_status": None,
                    "is_matched": False,
                    "has_drift": True,
                    "drift_type": "missing_external_identifiers",
                    "details": "Non-trial subscription lacks external customer and subscription identifiers.",
                    "reconciled_at": datetime.utcnow(),
                }

        if not ext_sub_id:
            return {
                "company_id": company_id,
                "subscription_id": subscription.id,
                "local_status": local_status,
                "provider_name": self.provider.provider_name,
                "provider_subscription_id": None,
                "provider_status": None,
                "is_matched": False,
                "has_drift": True,
                "drift_type": "missing_external_subscription_id",
                "details": "Subscription has customer reference but no provider subscription identifier.",
                "reconciled_at": datetime.utcnow(),
            }

        # Query provider
        provider_status_raw = None
        try:
            provider_raw_data = await self.provider.get_subscription_status(ext_sub_id)
            if isinstance(provider_raw_data, dict):
                provider_status_raw = provider_raw_data.get("status")
        except HTTPException as e:
            logger.warning(f"Reconciliation provider error for company {company_id}: {e.detail}")
            return {
                "company_id": company_id,
                "subscription_id": subscription.id,
                "local_status": local_status,
                "provider_name": self.provider.provider_name,
                "provider_subscription_id": ext_sub_id,
                "provider_status": None,
                "is_matched": False,
                "has_drift": True,
                "drift_type": "provider_unavailable",
                "details": f"Payment provider returned error: {e.detail}",
                "reconciled_at": datetime.utcnow(),
            }
        except Exception as e:
            logger.warning(f"Reconciliation unexpected error for company {company_id}: {str(e)}")
            return {
                "company_id": company_id,
                "subscription_id": subscription.id,
                "local_status": local_status,
                "provider_name": self.provider.provider_name,
                "provider_subscription_id": ext_sub_id,
                "provider_status": None,
                "is_matched": False,
                "has_drift": True,
                "drift_type": "provider_unavailable",
                "details": f"Could not reach payment provider: {str(e)}",
                "reconciled_at": datetime.utcnow(),
            }

        normalized_provider_status = self._normalize_provider_status(provider_status_raw)

        # Evaluate drift
        is_matched = False
        has_drift = False
        drift_type = "none"
        details = "Local state matches provider subscription state."

        if local_status == normalized_provider_status:
            is_matched = True
            has_drift = False
            drift_type = "none"
            details = f"State is aligned: both local and provider are '{local_status}'."
        elif local_status == "active" and normalized_provider_status in ("past_due", "cancelled", "expired", "halted"):
            is_matched = False
            has_drift = True
            drift_type = "local_active_provider_inactive"
            details = f"Drift detected: local is 'active' but provider is '{provider_status_raw}'."
        elif local_status == "past_due" and normalized_provider_status == "active":
            is_matched = False
            has_drift = True
            drift_type = "local_past_due_provider_active"
            details = f"Drift detected: local is 'past_due' but provider is '{provider_status_raw}'."
        elif local_status == "cancelled" and normalized_provider_status == "active":
            is_matched = False
            has_drift = True
            drift_type = "local_cancelled_provider_active"
            details = f"Drift detected: local is 'cancelled' but provider is '{provider_status_raw}'."
        else:
            is_matched = False
            has_drift = True
            drift_type = f"status_mismatch_{local_status}_{normalized_provider_status}"
            details = f"Status mismatch: local is '{local_status}', provider is '{provider_status_raw}'."

        result = {
            "company_id": company_id,
            "subscription_id": subscription.id,
            "local_status": local_status,
            "provider_name": self.provider.provider_name,
            "provider_subscription_id": ext_sub_id,
            "provider_status": provider_status_raw,
            "is_matched": is_matched,
            "has_drift": has_drift,
            "drift_type": drift_type,
            "details": details,
            "reconciled_at": datetime.utcnow(),
        }

        # Safe activity audit log if user initiated
        if current_user:
            log = ActivityLog(
                action="BILLING_RECONCILIATION_PERFORMED",
                entity="Company",
                entity_id=company_id,
                performed_by=current_user.id,
                details={
                    "has_drift": has_drift,
                    "drift_type": drift_type,
                    "local_status": local_status,
                    "provider_status": provider_status_raw,
                },
            )
            db.add(log)
            await db.commit()

        return result

    async def reconcile_all_tenants(
        self,
        db: AsyncSession,
        batch_size: int = 50,
        current_user: Optional[User] = None,
        stop_event: Optional[object] = None,
    ) -> Dict[str, Any]:
        """
        Reconciles all company subscriptions against the payment provider in batches.
        Read-only, non-destructive drift detection across the platform.
        """
        offset = 0
        total_reconciled = 0
        total_matched = 0
        total_drifted = 0
        total_unavailable = 0
        results = []

        while True:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                logger.info("Billing reconciliation aborted early by stop_event.")
                break

            stmt = (
                select(Company.id)
                .order_by(Company.id.asc())
                .limit(batch_size)
                .offset(offset)
            )
            company_ids = (await db.execute(stmt)).scalars().all()
            if not company_ids:
                break

            for company_id in company_ids:
                if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                    logger.info("Billing reconciliation batch loop aborted early by stop_event.")
                    break
                try:
                    res = await self.reconcile_tenant(db, company_id, current_user=None)
                    results.append(res)
                    total_reconciled += 1
                    if res["is_matched"]:
                        total_matched += 1
                    else:
                        total_drifted += 1
                    if res.get("drift_type") == "provider_unavailable":
                        total_unavailable += 1
                except Exception as e:
                    logger.warning(f"Error reconciling company {company_id}: {str(e)}")
                    total_reconciled += 1
                    total_drifted += 1
                    total_unavailable += 1
                    results.append({
                        "company_id": company_id,
                        "subscription_id": None,
                        "local_status": None,
                        "provider_name": self.provider.provider_name,
                        "provider_subscription_id": None,
                        "provider_status": None,
                        "is_matched": False,
                        "has_drift": True,
                        "drift_type": "provider_unavailable",
                        "details": f"Reconciliation exception: {str(e)}",
                        "reconciled_at": datetime.utcnow(),
                    })

            offset += batch_size

        summary = {
            "total_reconciled": total_reconciled,
            "total_matched": total_matched,
            "total_drifted": total_drifted,
            "total_unavailable": total_unavailable,
            "results": results,
            "reconciled_at": datetime.utcnow(),
        }

        if current_user:
            log = ActivityLog(
                action="PLATFORM_BILLING_RECONCILIATION_PERFORMED",
                entity="Platform",
                entity_id=None,
                performed_by=current_user.id,
                details={
                    "total_reconciled": total_reconciled,
                    "total_matched": total_matched,
                    "total_drifted": total_drifted,
                    "total_unavailable": total_unavailable,
                    "provider": self.provider.provider_name,
                },
            )
            db.add(log)
            await db.commit()

        return summary
