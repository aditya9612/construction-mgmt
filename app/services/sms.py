"""SMS delivery for OTP - mock (log) and Twilio support."""

import logging
from typing import Optional

from app.core.config import settings

from app.core.logger import logger


async def send_otp_sms(mobile: str, otp: str) -> bool:
    if settings.OTP_PROVIDER == "twilio" and settings.TWILIO_ACCOUNT_SID:
        return await _send_via_twilio(mobile, otp)

    logger.info(
        "OTP mock: mobile=%s otp=%s (set OTP_PROVIDER=twilio for real SMS)",
        mobile,
        otp
    )
    return True


async def _send_via_twilio(mobile: str, otp: str) -> bool:
    try:
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=f"Your construction management OTP is: {otp}. Valid for 5 minutes.",
            from_=settings.TWILIO_PHONE_NUMBER,
            to=mobile,
        )

        logger.info(f"OTP sent via Twilio mobile={mobile} sid={message.sid}")
        return True

    except Exception:
        logger.exception(f"Twilio SMS failed mobile={mobile}")
        return False
