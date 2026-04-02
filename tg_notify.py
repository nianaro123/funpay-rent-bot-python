# tg_notify.py

import requests

from settings import TELEGRAM_ADMIN_BOT_TOKEN, TELEGRAM_ADMIN_USER_ID


def send_admin_notification(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_ADMIN_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_ADMIN_USER_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, data=payload, timeout=10)
        return response.status_code == 200
    except Exception:
        return False