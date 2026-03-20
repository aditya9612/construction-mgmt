"""SMS delivery for OTP - mock (log) and Twilio support."""

import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("construction-mgmt")


async def send_otp_sms(mobile: str, otp: str) -> bool:
    """
    Send OTP via configured provider.
    Returns True if sent (or mocked) successfully.
    """
    if settings.OTP_PROVIDER == "twilio" and settings.TWILIO_ACCOUNT_SID:
        return await _send_via_twilio(mobile, otp)
    # Default: mock - log OTP (useful for development)
    logger.info("OTP mock: mobile=%s otp=%s (set OTP_PROVIDER=twilio for real SMS)", mobile, otp)
    return True


async def _send_via_twilio(mobile: str, otp: str) -> bool:
    """Send OTP via Twilio API."""
    try:
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=f"Your construction management OTP is: {otp}. Valid for 5 minutes.",
            from_=settings.TWILIO_PHONE_NUMBER,
            to=mobile,
        )
        logger.info("OTP sent via Twilio to %s, sid=%s", mobile, message.sid)
        return True
    except Exception as e:
        logger.exception("Twilio SMS failed: %s", e)
        return False
