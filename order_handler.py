# order_handler.py

import re
import logging

from FunPayAPI import Account
from rental_manager import RentalManager
from order_utils import (
    get_order_html,
    extract_hours_from_order_html,
    extract_short_description_from_order_html,
    extract_good_marker,
)

LOGGER = logging.getLogger(__name__)


def extract_order_id(text: str) -> str | None:
    match = re.search(r"#([A-Z0-9]{8})", text)
    if match:
        return match.group(1)
    return None


def is_extension_order(order) -> bool:
    text = " ".join([
        order.short_description or "",
        order.full_description or "",
    ]).lower()

    return "лот для продления" in text or "продление" in text


def handle_paid_order_message(
    acc: Account,
    rental_manager: RentalManager,
    chat_id: int | str,
    message_text: str
):
    order_id = extract_order_id(message_text)
    if not order_id:
        LOGGER.warning("Не удалось извлечь order_id из сообщения: %s", message_text)
        return

    try:
        order = acc.get_order(order_id)
    except Exception as e:
        LOGGER.exception("Ошибка получения заказа %s: %s", order_id, e)
        acc.send_message(chat_id, "❌ Не удалось загрузить данные заказа.")
        return

    if is_extension_order(order):
        ok = rental_manager.extend_active_rental_for_buyer(
            buyer_id=order.buyer_id,
            hours=1,
            source="paid_extension"
        )

        if ok:
            acc.send_message(chat_id, "✅ Аренда продлена на 1 час.")
        else:
            acc.send_message(chat_id, "❌ Не удалось продлить аренду. У вас нет активной аренды.")
        return

    try:
        html = get_order_html(acc, order_id)
    except Exception as e:
        LOGGER.exception("Ошибка загрузки HTML заказа %s: %s", order_id, e)
        acc.send_message(chat_id, "❌ Не удалось открыть страницу заказа.")
        return

    hours = extract_hours_from_order_html(html)
    if hours is None:
        acc.send_message(chat_id, "❌ Не удалось определить количество часов по заказу.")
        return

    short_description = extract_short_description_from_order_html(html)
    good_marker = extract_good_marker(short_description)

    if not good_marker:
        acc.send_message(chat_id, "❌ Не удалось определить номер товара из краткого описания.")
        return

    ok = rental_manager.issue_specific_good(
        order_id=order_id,
        good_marker=good_marker,
        buyer_id=order.buyer_id,
        buyer_username=order.buyer_username,
        chat_id=chat_id,
        hours=hours
    )

    if not ok:
        acc.send_message(chat_id, "❌ Не удалось выдать аккаунт.")