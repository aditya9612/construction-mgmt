import httpx
from app.core.config import settings


async def send_report_template(to: str, name: str, report_url: str):
    headers = {
        "D360-API-KEY": settings.WHATSAPP_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "to": to,
        "type": "template",
        "template": {
            "namespace": settings.WHATSAPP_NAMESPACE,
            "name": "report_ready",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": name},
                        {"type": "text", "text": report_url},
                    ],
                }
            ],
        },
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(settings.WHATSAPP_BASE_URL, json=payload, headers=headers)

    return res.json()