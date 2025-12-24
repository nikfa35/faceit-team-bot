import uuid
import httpx
import logging
from config import YOOMONEY_SHOP_ID, YOOMONEY_SECRET_KEY, YOOMONEY_RETURN_URL

logger = logging.getLogger(__name__)

async def create_yoomoney_payment(
    user_id: int, 
    amount: float, 
    description: str,
    subscription_type: str
):
    """Создает платеж в ЮKassa"""
    try:
        auth = (YOOMONEY_SHOP_ID, YOOMONEY_SECRET_KEY)
        headers = {
            "Idempotence-Key": str(uuid.uuid4()),
            "Content-Type": "application/json"
        }
        
        data = {
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": YOOMONEY_RETURN_URL
            },
            "capture": True,
            "description": description[:128],  # Макс. 128 символов
            "metadata": {
                "user_id": user_id,
                "subscription_type": subscription_type
            }
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://api.yookassa.ru/v3/payments",
                json=data,
                auth=auth,
                headers=headers
            )
            
            if response.status_code == 200:
                return response.json()
            
            logger.error(
                f"Payment creation failed: {response.status_code}, {response.text}"
            )
            return None
                
    except httpx.RequestError as e:
        logger.error(f"Payment connection error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected payment error: {e}")
        return None