import os
from dotenv import load_dotenv

load_dotenv()

# YooMoney настройки
YOOMONEY_SHOP_ID = os.getenv("YOOMONEY_SHOP_ID")
YOOMONEY_SECRET_KEY = os.getenv("YOOMONEY_SECRET_KEY")
YOOMONEY_PROVIDER_TOKEN = os.getenv("YOOMONEY_PROVIDER_TOKEN") 
YOOMONEY_RETURN_URL = os.getenv("YOOMONEY_RETURN_URL", "https://t.me/Faceit_teamBot")

# VIP тарифы (в рублях)
VIP_PRICES = {
    "month": 149,
    "3month": 399,
    "year": 999,
    "permanent": 4990
}

# ID администраторов
ADMINS = [1770909404]

# Настройки платежей
PAYMENT_CURRENCY = "RUB"
PAYMENT_PROVIDER_DATA = {
    "receipt": {
        "items": [
            {
                "description": "VIP подписка",
                "quantity": "1.00",
                "amount": {
                    "value": "0.00",  # Будет заменено динамически
                    "currency": PAYMENT_CURRENCY
                },
                "vat_code": 1
            }
        ]
    }
}