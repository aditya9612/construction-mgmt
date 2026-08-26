import pytest
import hmac
import hashlib
import json
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import HTTPException
import httpx

from app.core.config import settings
from app.services.billing.razorpay_provider import RazorpayPaymentProvider
from app.services.billing.billing_service import BillingService

pytestmark = pytest.mark.asyncio

@pytest.fixture
def razorpay_provider():
    settings.PAYMENT_PROVIDER = "razorpay"
    settings.RAZORPAY_KEY_ID = "test_key_id"
    settings.RAZORPAY_KEY_SECRET = "test_key_secret"
    settings.RAZORPAY_WEBHOOK_SECRET = "test_webhook_secret"
    return RazorpayPaymentProvider()


async def test_razorpay_create_customer(razorpay_provider):
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "cust_123"}
    mock_response.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        customer_id = await razorpay_provider.create_customer(
            company_id=1, company_name="Test Company", user_email="test@test.com"
        )
        
        assert customer_id == "cust_123"
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["name"] == "Test Company"
        assert kwargs["json"]["notes"]["company_id"] == "1"


async def test_razorpay_create_checkout_session(razorpay_provider):
    mock_response = MagicMock()
    mock_response.json.return_value = {"short_url": "https://rzp.io/i/test"}
    mock_response.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        url = await razorpay_provider.create_checkout_session(
            company_id=1,
            plan_id=1,
            customer_id="cust_123",
            price=10.0,
            currency="USD",
            success_url="http://success",
            cancel_url="http://cancel"
        )
        
        assert url == "https://rzp.io/i/test"
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["amount"] == 1000  # 10.0 * 100
        assert kwargs["json"]["customer"]["id"] == "cust_123"


def test_razorpay_verify_webhook_signature(razorpay_provider):
    payload = b'{"event":"payment.captured"}'
    secret = "test_webhook_secret"
    
    valid_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload,
        digestmod=hashlib.sha256
    ).hexdigest()
    
    assert razorpay_provider.verify_webhook_signature(payload, valid_signature) is True
    assert razorpay_provider.verify_webhook_signature(payload, "invalid") is False


def test_razorpay_normalize_webhook_event(razorpay_provider):
    raw_event = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_123",
                    "customer": {"id": "cust_123"}
                }
            }
        }
    }
    
    normalized = razorpay_provider.normalize_webhook_event(raw_event)
    assert normalized["type"] == "invoice.payment_succeeded"
    assert normalized["data"]["object"]["customer"] == "cust_123"
    assert normalized["id"] == "rzp_evt_plink_123"
    
    raw_event_failed = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_123",
                    "customer_id": "cust_456"
                }
            }
        }
    }
    
    normalized_failed = razorpay_provider.normalize_webhook_event(raw_event_failed)
    assert normalized_failed["type"] == "invoice.payment_failed"
    assert normalized_failed["data"]["object"]["customer"] == "cust_456"
    assert normalized_failed["id"] == "rzp_evt_pay_123"


def test_razorpay_normalize_refund_event(razorpay_provider):
    raw_event_refund = {
        "event": "refund.processed",
        "payload": {
            "refund": {
                "entity": {
                    "id": "rfnd_999",
                    "payment_id": "pay_888",
                    "amount": 50000,
                    "notes": {
                        "customer_id": "cust_777",
                        "invoice_number": "INV-2026-0001"
                    },
                    "created_at": 1690000000
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_888",
                    "customer_id": "cust_777",
                    "invoice_id": "INV-2026-0001",
                    "refund_status": "full"
                }
            }
        },
        "created_at": 1690000000
    }

    normalized = razorpay_provider.normalize_webhook_event(raw_event_refund)
    assert normalized["type"] == "refund.processed"
    assert normalized["data"]["object"]["refund_id"] == "rfnd_999"
    assert normalized["data"]["object"]["payment_id"] == "pay_888"
    assert normalized["data"]["object"]["customer"] == "cust_777"
    assert normalized["data"]["object"]["invoice_number"] == "INV-2026-0001"
    assert normalized["data"]["object"]["amount"] == 50000
    assert normalized["data"]["object"]["refund_status"] == "full"
    assert normalized["created_at"] == 1690000000



async def test_razorpay_missing_credentials():
    settings.RAZORPAY_KEY_ID = ""
    provider = RazorpayPaymentProvider()
    
    with pytest.raises(HTTPException) as exc:
        await provider.create_customer(1, "Test", "test@test.com")
    
    assert exc.value.status_code == 500


async def test_razorpay_http_error(razorpay_provider):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.RequestError("Network error", request=MagicMock())
        
        with pytest.raises(HTTPException) as exc:
            await razorpay_provider.create_customer(1, "Test", "test@test.com")
            
        assert exc.value.status_code == 502
