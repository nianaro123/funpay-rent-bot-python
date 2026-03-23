# order_handler.py

import logging
import re

from storage import (
    get_rental_by_order_id,
    get_active_rental_by_buyer_and_marker,
)
from tg_notify import send_admin_notification

LOGGER = logging.getLogger(__name__)


def extract_order_id(text: str) -> str | None:
    match = re.search(r"#([A-Z0-9]+)", text)
    return match.group(1) if match else None


def extract_hours(text: str) -> int | None:
    match = re.search(r"(\d+)\s*шт", text.lower())
    return int(match.group(1)) if match else None


def extract_marker(text: str) -> str | None:
    match = re.search(r"(#\d+)", text)
    return match.group(1) if match else None


def handle_paid_order_message(acc, rm, chat_id: int | str, text: str):
    order_id = extract_order_id(text)
    if not order_id:
        acc.send_message(chat_id, "❌ Не удалось определить номер заказа.")
        return

    if get_rental_by_order_id(order_id):
        LOGGER.info("Заказ %s уже обработан", order_id)
        return

    hours = extract_hours(text)
    if not hours:
        acc.send_message(chat_id, "❌ Не удалось определить количество часов по заказу.")
        return

    marker = extract_marker(text)
    if not marker:
        acc.send_message(chat_id, "❌ Не удалось определить маркер товара (#1, #2 и т.д.).")
        return

    msg = acc.get_chat_history(chat_id, interlocutor_username=None, from_id=0)
    buyer_id = None
    buyer_username = None

    for m in reversed(msg):
        if m.author_id not in (0, acc.id):
            buyer_id = m.author_id
            buyer_username = m.author
            break

    if buyer_id is None:
        acc.send_message(chat_id, "❌ Не удалось определить покупателя заказа.")
        return

    chat_link = f"https://funpay.com/chat/?node={chat_id}"

    active_same_marker_rental = get_active_rental_by_buyer_and_marker(buyer_id, marker)
    if active_same_marker_rental:
        ok = rm.extend_rental_by_order_id(
            active_same_marker_rental["order_id"],
            hours,
            source="same_marker_rebuy",
        )
        if ok:
            acc.send_message(
                chat_id,
                f"✅ Заказ #{order_id}: аренда продлена на {hours} ч.\n"
                "⏱ Обновлённое время можно посмотреть командой /time"
            )

            send_admin_notification(
                f"🔁 Продление аренды\n"
                f"Клиент: {buyer_username or buyer_id}\n"
                f"Новый заказ: #{order_id}\n"
                f"Продлено на: {hours} ч.\n"
                f"Маркер: {marker}\n"
                f"good_id: {active_same_marker_rental['good_id']}\n"
                f"Логин аккаунта: {active_same_marker_rental['login']}\n"
                f"Чат: {chat_link}"
            )
        else:
            acc.send_message(chat_id, "❌ Не удалось продлить текущую аренду.")
        return

    issued_good = rm.issue_specific_good(
        order_id=order_id,
        good_marker=marker,
        buyer_id=buyer_id,
        buyer_username=buyer_username,
        chat_id=chat_id,
        hours=hours,
    )

    if not issued_good:
        acc.send_message(chat_id, "❌ Не удалось выдать аккаунт.")
        return

    send_admin_notification(
        f"🟢 Новая аренда\n"
        f"Клиент: {buyer_username or buyer_id}\n"
        f"Заказ: #{order_id}\n"
        f"Время: {hours} ч.\n"
        f"Маркер: {marker}\n"
        f"good_id: {issued_good['id']}\n"
        f"Логин аккаунта: {issued_good['login']}\n"
        f"Чат: {chat_link}"
    )