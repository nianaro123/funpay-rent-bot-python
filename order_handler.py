# order_handler.py

import logging
import re
import time

from storage import (
    get_rental_by_order_id,
    get_active_rental_by_buyer_and_marker,
    get_good_by_marker,
    log_order_event,
)
from tg_notify import send_admin_notification

LOGGER = logging.getLogger(__name__)


def extract_order_id(text: str) -> str | None:
    match = re.search(r"#([A-Z0-9]+)", text)
    return match.group(1) if match else None


def extract_hours(text: str) -> int | None:
    low = text.lower()

    patterns = [
        r"(\d+)\s*шт",
        r"аренда\s*[,:-]?\s*(\d+)\s*ч",
        r"на\s*(\d+)\s*ч(?:ас|\.)?",
        r"(\d+)\s*ч(?:ас|\.)",
        r"аренда\s*[,:-]?\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, low)
        if match:
            return int(match.group(1))

    # На FunPay покупка одной единицы часто приходит без "1 шт".
    # Для наших лотов 1 единица = 1 час аренды.
    if extract_marker(text):
        return 1

    return None


def extract_min_hours(title: str) -> int | None:
    if not title:
        return None

    patterns = [
        r"от\s*(\d+)\s*час",
        r"от\s*(\d+)\s*ч",
        r"min\s*(\d+)\s*h",
    ]

    low = title.lower()
    for pattern in patterns:
        match = re.search(pattern, low)
        if match:
            return int(match.group(1))

    return None


def build_min_hours_message(marker: str, good, min_hours: int) -> str:
    lines = [
        f"❌ Минимальная аренда для этого аккаунта — от {min_hours} ч.",
        f"Сейчас в заказе указано меньше: доплатите ещё минимум до {min_hours} ч. и оформите заказ заново.",
    ]

    lot_id = None
    try:
        lot_id = good["lot_id"]
    except Exception:
        lot_id = None

    if lot_id:
        lines.extend([
            "",
            "Оплатить можно по этому лоту:",
            f"https://funpay.com/lots/offer?id={lot_id}",
        ])
    elif marker:
        lines.append(f"\nМаркер товара: {marker}")

    return "\n".join(lines)


def extract_marker(text: str) -> str | None:
    match = re.search(r"(#\d+)", text)
    return match.group(1) if match else None


def get_order_amount_rub(acc, order_id: str) -> float:
    try:
        order = acc.get_order(order_id)
        return float(order.sum) if order and order.sum is not None else 0.0
    except Exception:
        return 0.0


def handle_paid_order_message(acc, rm, chat_id: int | str, text: str):
    order_id = extract_order_id(text)
    if not order_id:
        acc.send_message(chat_id, "❌ Не удалось определить номер заказа.")
        return

    if get_rental_by_order_id(order_id):
        LOGGER.info("Заказ %s уже обработан", order_id)
        return

    marker = extract_marker(text)
    if not marker:
        acc.send_message(chat_id, "❌ Не удалось определить маркер товара (#1, #2 и т.д.).")
        return

    good = get_good_by_marker(marker)
    if not good:
        acc.send_message(chat_id, f"❌ В базе не найден товар для маркера {marker}")
        return

    hours = extract_hours(text)
    if not hours:
        acc.send_message(chat_id, "❌ Не удалось определить количество часов по заказу.")
        return

    min_hours = extract_min_hours(good["title"])
    if min_hours and hours < min_hours:
        acc.send_message(chat_id, build_min_hours_message(marker, good, min_hours))
        return

    msg = acc.get_chat_history(chat_id, interlocutor_username=None, from_id=0)
    buyer_id = None
    buyer_username = None
    amount_rub = get_order_amount_rub(acc, order_id)

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

            log_order_event(
                order_id=order_id,
                good_id=active_same_marker_rental["good_id"],
                good_title_snapshot=active_same_marker_rental["title"],
                login_snapshot=active_same_marker_rental["login"],
                buyer_id=buyer_id,
                buyer_username=buyer_username,
                marker=marker,
                hours=hours,
                amount_rub=amount_rub,
                kind="extension",
                status="paid",
                created_ts=int(time.time()),
            )

            send_admin_notification(
                f"🔁 Продление аренды\n"
                f"Клиент: {buyer_username or buyer_id}\n"
                f"Новый заказ: #{order_id}\n"
                f"Сумма: {amount_rub:.2f} RUB\n"
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

    log_order_event(
        order_id=order_id,
        good_id=issued_good["id"],
        good_title_snapshot=issued_good["title"],
        login_snapshot=issued_good["login"],
        buyer_id=buyer_id,
        buyer_username=buyer_username,
        marker=marker,
        hours=hours,
        amount_rub=amount_rub,
        kind="new_rental",
        status="paid",
        created_ts=int(time.time()),
    )

    send_admin_notification(
        f"🟢 Новая аренда\n"
        f"Клиент: {buyer_username or buyer_id}\n"
        f"Заказ: #{order_id}\n"
        f"Сумма: {amount_rub:.2f} RUB\n"
        f"Время: {hours} ч.\n"
        f"Маркер: {marker}\n"
        f"good_id: {issued_good['id']}\n"
        f"Логин аккаунта: {issued_good['login']}\n"
        f"Чат: {chat_link}"
    )