import uuid
import json
from typing import Dict, Any
from app.services.billing.provider_base import PaymentProviderInterface


class MockPaymentProvider(PaymentProviderInterface):
    def __init__(self):
        self._mock_statuses: Dict[str, Dict[str, Any]] = {}

    @property
    def provider_name(self) -> str:
        return "mock"

    def set_subscription_status(self, subscription_id: str, status_dict: Dict[str, Any]):
        """Helper for tests to simulate specific provider status."""
        self._mock_statuses[subscription_id] = status_dict

    async def create_customer(
        self, company_id: int, company_name: str, user_email: str
    ) -> str:
        """Generate a synthetic customer ID."""
        return f"mock_cus_{company_id}_{uuid.uuid4().hex[:8]}"

    async def create_checkout_session(
        self,
        company_id: int,
        plan_id: int,
        customer_id: str,
        price: float,
        currency: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        """Generate a synthetic session ID and URL."""
        session_id = f"mock_cs_{uuid.uuid4().hex[:16]}"
        return f"https://mock-provider.local/checkout/{session_id}"

    async def get_subscription_status(self, provider_subscription_id: str) -> Dict[str, Any]:
        """Return a simulated provider status."""
        from fastapi import HTTPException
        if provider_subscription_id in self._mock_statuses:
            return self._mock_statuses[provider_subscription_id]
        if "error" in provider_subscription_id:
            raise HTTPException(status_code=502, detail="Mock provider gateway unavailable")
        if "inactive" in provider_subscription_id or "halted" in provider_subscription_id:
            return {"status": "halted", "id": provider_subscription_id}
        if "cancelled" in provider_subscription_id:
            return {"status": "cancelled", "id": provider_subscription_id}
        if "past_due" in provider_subscription_id:
            return {"status": "halted", "id": provider_subscription_id}
        return {"status": "active", "id": provider_subscription_id}

    async def cancel_subscription(self, provider_subscription_id: str) -> bool:
        """Always succeed for mock."""
        return True

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Accept a static signature for testing."""
        return signature == "mock_valid_signature"

