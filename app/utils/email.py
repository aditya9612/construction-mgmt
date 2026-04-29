import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

from app.core.config import settings
from app.core.logger import logger


def _send_sync_email(msg):
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
        server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(msg)


async def send_email(
    to_email: str,
    subject: str,
    body: str,
    attachment: bytes = None,
    filename: str = None,
) -> bool:

    if not settings.SHOULD_SEND_EMAIL:
        logger.info(f"[DEV MODE] Email to {to_email}")
        return True

    if not all([
        settings.SMTP_HOST,
        settings.SMTP_USERNAME,
        settings.SMTP_PASSWORD,
        settings.SMTP_FROM_EMAIL,
    ]):
        logger.error("SMTP not configured properly")
        return False

    try:
        msg = MIMEMultipart("alternative")

        msg["From"] = settings.SMTP_FROM_EMAIL
        msg["To"] = to_email
        msg["Subject"] = subject

        # Plain text fallback
        text_part = MIMEText("Daily report attached.", "plain")

        # HTML version
        html_part = MIMEText(body, "html")

        msg.attach(text_part)
        msg.attach(html_part)

        if attachment and filename:
            part = MIMEApplication(attachment, Name=filename)
            part["Content-Disposition"] = f'attachment; filename="{filename}"'
            msg.attach(part)

        await asyncio.to_thread(_send_sync_email, msg)

        logger.info(f"Email sent to {to_email}")
        return True

    except Exception as e:
        logger.exception(f"Email failed: {str(e)}")
        return False