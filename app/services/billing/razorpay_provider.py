import hmac
import hashlib
import logging
import httpx
from typing import Dict, Any
from fastapi import HTTPException

from app.core.config import settings
from app.services.billing.provider_base import PaymentProviderInterface

logger = logging.getLogger(__name__)


class RazorpayPaymentProvider(PaymentProviderInterface):
    """
    Razorpay payment provider implementation using httpx.AsyncClient.
    Maps Razorpay API calls and Webhook events safely.
    """

    def __init__(self):
        self.key_id = settings.RAZORPAY_KEY_ID
        self.key_secret = settings.RAZORPAY_KEY_SECRET
        self.webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET
        self.base_url = "https://api.razorpay.com/v1"

    @property
    def provider_name(self) -> str:
        return "razorpay"

    def _get_client(self) -> httpx.AsyncClient:
        # Check credentials
        if not self.key_id or not self.key_secret:
            raise HTTPException(status_code=500, detail="Razorpay credentials not configured")
        
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=(self.key_id, self.key_secret),
            timeout=10.0
        )

    async def create_customer(
        self, company_id: int, company_name: str, user_email: str
    ) -> str:
        """
        Creates a Razorpay customer.
        Returns the external_customer_id (cust_XXX)
        """
        async with self._get_client() as client:
            payload = {
                "name": company_name,
                "email": user_email,
                "notes": {
                    "company_id": str(company_id)
                }
            }
            try:
                response = await client.post("/customers", json=payload)
                response.raise_for_status()
                data = response.json()
                return data["id"]
            except httpx.HTTPStatusError as e:
                logger.error(f"Razorpay HTTP error creating customer: {e.response.text}")
                raise HTTPException(status_code=502, detail="Payment gateway error")
            except httpx.RequestError as e:
                logger.error(f"Razorpay network error creating customer: {str(e)}")
                raise HTTPException(status_code=502, detail="Payment gateway network error")

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
        """
        Creates a Razorpay Payment Link, simulating a checkout session.
        Returns the short_url.
        """
        async with self._get_client() as client:
            # Razorpay amounts are in the smallest currency unit (e.g. paise for INR)
            amount_in_smallest_unit = int(price * 100)
            
            payload = {
                "amount": amount_in_smallest_unit,
                "currency": currency,
                "accept_partial": False,
                "description": f"Subscription for Plan ID {plan_id}",
                "customer": {
                    "id": customer_id
                },
                "notify": {
                    "email": False,
                    "sms": False
                },
                "reminder_enable": False,
                "notes": {
                    "company_id": str(company_id),
                    "plan_id": str(plan_id)
                },
                "callback_url": success_url,
                "callback_method": "get"
            }
            
            try:
                response = await client.post("/payment_links", json=payload)
                response.raise_for_status()
                data = response.json()
                return data["short_url"]
            except httpx.HTTPStatusError as e:
                logger.error(f"Razorpay HTTP error creating checkout: {e.response.text}")
                raise HTTPException(status_code=502, detail="Payment gateway error")
            except httpx.RequestError as e:
                logger.error(f"Razorpay network error creating checkout: {str(e)}")
                raise HTTPException(status_code=502, detail="Payment gateway network error")

    async def get_subscription_status(self, provider_subscription_id: str) -> Dict[str, Any]:
        """
        Fetch subscription details from Razorpay.
        """
        async with self._get_client() as client:
            try:
                response = await client.get(f"/subscriptions/{provider_subscription_id}")
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError:
                raise HTTPException(status_code=502, detail="Error fetching subscription status")

    async def cancel_subscription(self, provider_subscription_id: str) -> bool:
        """
        Cancel a Razorpay subscription.
        """
        async with self._get_client() as client:
            try:
                response = await client.post(f"/subscriptions/{provider_subscription_id}/cancel")
                response.raise_for_status()
                return True
            except httpx.HTTPError:
                return False

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify the Razorpay webhook HMAC SHA256 signature using raw bytes.
        """
        if not self.webhook_secret:
            logger.error("Razorpay webhook secret not configured")
            return False
            
        if not signature:
            return False

        try:
            expected_sig = hmac.new(
                key=self.webhook_secret.encode('utf-8'),
                msg=payload,
                digestmod=hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(expected_sig, signature)
        except Exception:
            return False

    def normalize_webhook_event(self, event_data: dict) -> dict:
        """
        Convert Razorpay webhook format into the normalized BillingService format.
        """
        # BillingService expects:
        # {
        #   "id": event_id,
        #   "type": normalized_type,
        #   "data": { "object": { "customer": ..., "subscription": ..., "invoice_number": ... } }
        # }
        
        rzp_event_name = event_data.get("event")
        rzp_event_id = event_data.get("account_id") or "rzp_" + event_data.get("event", "unknown")
        
        # If there's a specific Razorpay webhook ID header (X-Razorpay-Event-Id), that would be better,
        # but Razorpay webhooks don't send a unique event ID by default in the payload body (other than the entity ID).
        # We can use a hash of the payload or the entity ID to create an event ID.
        # However, the payload does often have an 'event' name. We will extract entity details.
        
        normalized_type = "unknown"
        customer = None
        subscription = None
        invoice = None
        
        payload = event_data.get("payload", {})
        
        # Determine mapping
        customer = None
        subscription = None
        invoice = None
        plan_id = None
        event_created_at = event_data.get("created_at")

        if rzp_event_name in ["payment_link.paid", "order.paid", "payment.captured"]:
            normalized_type = "invoice.payment_succeeded"
            
            # Extract customer/subscription/invoice
            if "payment_link" in payload:
                entity = payload["payment_link"]["entity"]
                rzp_event_id = entity.get("id", rzp_event_id)
                customer = entity.get("customer", {}).get("id")
                # Fallback to notes if customer not in entity directly
                if not customer and "notes" in entity:
                    customer = entity["notes"].get("customer_id")
                
                # Extract plan_id from notes
                plan_id = entity.get("notes", {}).get("plan_id")
                if not event_created_at and "created_at" in entity:
                    event_created_at = entity.get("created_at")
                
            elif "payment" in payload:
                entity = payload["payment"]["entity"]
                rzp_event_id = entity.get("id", rzp_event_id)
                customer = entity.get("customer_id")
                invoice = entity.get("invoice_id")
                plan_id = entity.get("notes", {}).get("plan_id")
                if not event_created_at and "created_at" in entity:
                    event_created_at = entity.get("created_at")
                
        elif rzp_event_name in ["payment.failed"]:
            normalized_type = "invoice.payment_failed"
            if "payment" in payload:
                entity = payload["payment"]["entity"]
                rzp_event_id = entity.get("id", rzp_event_id)
                customer = entity.get("customer_id")
                invoice = entity.get("invoice_id")
                if not event_created_at and "created_at" in entity:
                    event_created_at = entity.get("created_at")
                
        elif rzp_event_name in ["subscription.cancelled", "subscription.halted"]:
            normalized_type = "customer.subscription.deleted"
            if "subscription" in payload:
                entity = payload["subscription"]["entity"]
                rzp_event_id = entity.get("id", rzp_event_id)
                customer = entity.get("customer_id")
                subscription = entity.get("id")
                if not event_created_at and "created_at" in entity:
                    event_created_at = entity.get("created_at")

        elif rzp_event_name in ["refund.processed", "refund.created", "payment.refunded"]:
            normalized_type = "refund.processed"
            refund_id = None
            payment_id = None
            amount = None
            refund_status = "full"
            
            if "refund" in payload:
                entity = payload["refund"]["entity"]
                rzp_event_id = entity.get("id", rzp_event_id)
                refund_id = entity.get("id")
                payment_id = entity.get("payment_id")
                amount = entity.get("amount")
                if not customer and "notes" in entity:
                    customer = entity["notes"].get("customer_id")
                if not invoice and "notes" in entity:
                    invoice = entity["notes"].get("invoice_number") or entity["notes"].get("invoice_id")
                if not invoice and "receipt" in entity:
                    invoice = entity.get("receipt")
                if not event_created_at and "created_at" in entity:
                    event_created_at = entity.get("created_at")

            if "payment" in payload:
                p_entity = payload["payment"]["entity"]
                if not customer:
                    customer = p_entity.get("customer_id")
                if not invoice:
                    invoice = p_entity.get("invoice_id")
                if not payment_id:
                    payment_id = p_entity.get("id")
                refund_status = p_entity.get("refund_status", "full")
                if not event_created_at and "created_at" in p_entity:
                    event_created_at = p_entity.get("created_at")

            return {
                "id": f"rzp_evt_{rzp_event_id}",
                "type": normalized_type,
                "created_at": event_created_at,
                "data": {
                    "object": {
                        "customer": customer,
                        "subscription": subscription,
                        "invoice_number": invoice,
                        "refund_id": refund_id,
                        "payment_id": payment_id,
                        "amount": amount,
                        "refund_status": refund_status,
                        "plan_id": plan_id,
                        "created_at": event_created_at
                    }
                },
                "raw": event_data
            }

        return {
            "id": f"rzp_evt_{rzp_event_id}",
            "type": normalized_type,
            "created_at": event_created_at,
            "data": {
                "object": {
                    "customer": customer,
                    "subscription": subscription,
                    "invoice_number": invoice,
                    "plan_id": plan_id,
                    "created_at": event_created_at
                }
            },
            "raw": event_data
        }
