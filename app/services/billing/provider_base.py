from abc import ABC, abstractmethod
from typing import Dict, Any


class PaymentProviderInterface(ABC):
    """
    Abstract base class for all payment providers (e.g. Stripe, Razorpay, Mock).
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the string identifier for this provider (e.g., 'stripe', 'mock')."""
        pass

    @abstractmethod
    async def create_customer(
        self, company_id: int, company_name: str, user_email: str
    ) -> str:
        """Create a customer in the provider and return the provider's external_customer_id."""
        pass

    @abstractmethod
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
        """Create a checkout session and return the checkout URL."""
        pass

    @abstractmethod
    async def get_subscription_status(self, provider_subscription_id: str) -> Dict[str, Any]:
        """Fetch the current status of a subscription from the provider."""
        pass

    @abstractmethod
    async def cancel_subscription(self, provider_subscription_id: str) -> bool:
        """Cancel a subscription on the provider."""
        pass

    @abstractmethod
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify the cryptographic signature of a webhook payload."""
        pass

    def normalize_webhook_event(self, event_data: dict) -> dict:
        """
        Normalize a provider-specific webhook event payload into the standard internal format.
        Default implementation returns the event unchanged.
        """
        return event_data
