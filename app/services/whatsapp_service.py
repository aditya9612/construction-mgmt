import logging
import httpx
from typing import List, Dict, Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

def normalize_mobile_number(mobile: str) -> Optional[str]:
    """
    Normalizes a mobile number for WhatsApp API.
    Assumes Indian (+91) if 10 digits and no country code.
    Strips spaces, dashes, and other non-digit characters (except leading +).
    """
    if not mobile:
        return None
        
    # Strip spaces and dashes
    cleaned = mobile.replace(" ", "").replace("-", "")
    
    if cleaned.startswith("+"):
        # If it has a country code, keep digits after +
        digits = "".join(filter(str.isdigit, cleaned))
        return digits if digits else None
        
    # Remove any non-digits
    digits = "".join(filter(str.isdigit, cleaned))
    
    if len(digits) == 10:
        # Assume Indian number
        return f"91{digits}"
    elif len(digits) > 10:
        # If it's more than 10 digits and didn't start with +, assume country code is already included
        return digits
    else:
        # Too short or invalid
        return None

async def send_whatsapp_template(mobile: str, template_name: str, parameters: List[Dict[str, Any]] = None) -> bool:
    """
    Sends a WhatsApp template message using Meta Cloud API.
    Safe for background task execution (does not raise on failure).
    """
    if not settings.WHATSAPP_ENABLED:
        logger.debug("WhatsApp is disabled. Skipping message to %s", mobile)
        return False
        
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.error("WhatsApp enabled but WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID is missing")
        return False
        
    if not template_name:
        logger.error("WhatsApp template name is missing")
        return False

    normalized_mobile = normalize_mobile_number(mobile)
    if not normalized_mobile:
        logger.warning(f"Invalid mobile number for WhatsApp: {mobile}")
        return False

    url = f"https://graph.facebook.com/v20.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    components = []
    if parameters:
        components.append({
            "type": "body",
            "parameters": parameters
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": normalized_mobile,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {
                "code": "en"
            },
            "components": components
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            
            if response.status_code in (200, 201):
                logger.info(f"WhatsApp message sent successfully to {normalized_mobile}")
                return True
            else:
                logger.error(f"WhatsApp API error for {normalized_mobile}: {response.status_code} - {response.text}")
                return False
                
    except httpx.TimeoutException:
        logger.error(f"WhatsApp API timeout for {normalized_mobile}")
        return False
    except Exception as e:
        logger.error(f"WhatsApp API unexpected error for {normalized_mobile}: {str(e)}")
        return False
