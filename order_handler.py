import logging
import re
import time

from storage import (
    get_connection,
    get_rental_by_order_id,
    get_active_rental_by_buyer_and_marker,
    log_order_event,
)
from tg_notify import send_admin_notification

LOGGER = logging.getLogger(__name__)


UNDER_MIN_KIND = "under_minimum"
UNDER_MIN_PENDING = "pending"
UNDER_MIN_APPLIED = "applied"


def extract_order_id(text: str) -> str | None:
    match = re.search(r"#([A-Z0-9]+)", text)
    return match.group(1) if match else None



def extract_hours(text: str) -> int | None:
    lowered = text.lower()
    match = re.search(r"(\d+)\s*шт", lowered)
    if match:
        return int(match.group(1))

    # FunPay нередко не пишет "1 шт" для единичной покупки аренды.
    # В таком случае считаем это заказом на 1 час.
    if "аренда" in lowered:
        return 1

    return None



def extract_marker(text: str) -> str | None:
    match = re.search(r"(#\d+)", text)
    return match.group(1) if match else None



def extract_min_hours(title: str | None) -> int:
    if not title:
        return 1

    patterns = [
        r"от\s*(\d+)\s*час",
        r"от\s*(\d+)\s*ч\b",
        r"min\s*(\d+)\s*h",
    ]
    lowered = title.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            try:
                value = int(match.group(1))
                if value > 0:
                    return value
            except ValueError:
                pass
    return 1



def get_order_amount_rub(acc, order_id: str) -> float:
    try:
        order = acc.get_order(order_id)
        return float(order.sum) if order and order.sum is not None else 0.0
    except Exception:
        return 0.0



def get_good_snapshot_by_marker(marker: str):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM goods
            WHERE marker = ?
              AND is_active = 1
            ORDER BY id ASC
            LIMIT 1
            """,
            (marker,),
        ).fetchone()
        return row
    finally:
        conn.close()



def get_order_event(order_id: str):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM order_events
            WHERE order_id = ?
            LIMIT 1
            """,
            (order_id,),
        ).fetchone()
        return row
    finally:
        conn.close()



def get_pending_under_minimum_hours(buyer_id: int, marker: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(hours), 0) AS total_hours
            FROM order_events
            WHERE buyer_id = ?
              AND marker = ?
              AND kind = ?
              AND status = ?
            """,
            (buyer_id, marker, UNDER_MIN_KIND, UNDER_MIN_PENDING),
        ).fetchone()
        return int(row["total_hours"] or 0)
    finally:
        conn.close()



def mark_pending_under_minimum_applied(buyer_id: int, marker: str, applied_ts: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE order_events
            SET status = ?, confirmed_ts = COALESCE(confirmed_ts, ?)
            WHERE buyer_id = ?
              AND marker = ?
              AND kind = ?
              AND status = ?
            """,
            (UNDER_MIN_APPLIED, applied_ts, buyer_id, marker, UNDER_MIN_KIND, UNDER_MIN_PENDING),
        )
        conn.commit()
    finally:
        conn.close()



def build_min_hours_message(min_hours: int, total_hours: int, lot_id: int | None) -> str:
    remaining = max(0, min_hours - total_hours)
    lines = [
        f"❌ Минимальная аренда для этого аккаунта — от {min_hours} ч.",
        f"Сейчас у вас оплачено: {total_hours} ч.",
    ]

    if remaining > 0:
        lines.append(f"Нужно доплатить ещё минимум: {remaining} ч.")

    if lot_id:
        lines.extend([
            "",
            "Оплатить можно по этому лоту:",
            f"https://funpay.com/lots/offer?id={lot_id}",
        ])

    lines.append("")
    lines.append("После оплаты недостающего времени бот автоматически суммирует часы и выдаст аккаунт.")
    return "\n".join(lines)



def handle_paid_order_message(acc, rm, chat_id: int | str, text: str):
    order_id = extract_order_id(text)
    if not order_id:
        acc.send_message(chat_id, "❌ Не удалось определить номер заказа.")
        return

    if get_rental_by_order_id(order_id):
        LOGGER.info("Заказ %s уже обработан как аренда", order_id)
        return

    existing_event = get_order_event(order_id)
    if existing_event:
        LOGGER.info("Заказ %s уже обработан как событие kind=%s status=%s", order_id, existing_event["kind"], existing_event["status"])
        return

    marker = extract_marker(text)
    if not marker:
        acc.send_message(chat_id, "❌ Не удалось определить маркер товара (#1, #2 и т.д.).")
        return

    hours = extract_hours(text)
    if not hours:
        acc.send_message(chat_id, "❌ Не удалось определить количество часов по заказу.")
        return

    good_snapshot = get_good_snapshot_by_marker(marker)
    if not good_snapshot:
        acc.send_message(chat_id, f"❌ В базе не найден товар для маркера {marker}.")
        return

    min_hours = extract_min_hours(good_snapshot["title"])
    lot_id = good_snapshot["lot_id"]

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

    pending_hours = get_pending_under_minimum_hours(buyer_id, marker)
    total_hours = pending_hours + hours

    if total_hours < min_hours:
        log_order_event(
            order_id=order_id,
            good_id=good_snapshot["id"],
            good_title_snapshot=good_snapshot["title"],
            login_snapshot=good_snapshot["login"],
            buyer_id=buyer_id,
            buyer_username=buyer_username,
            marker=marker,
            hours=hours,
            amount_rub=amount_rub,
            kind=UNDER_MIN_KIND,
            status=UNDER_MIN_PENDING,
            created_ts=int(time.time()),
        )

        acc.send_message(chat_id, build_min_hours_message(min_hours, total_hours, lot_id))

        send_admin_notification(
            f"⚠️ Недостаточная оплата аренды\n"
            f"Клиент: {buyer_username or buyer_id}\n"
            f"Заказ: #{order_id}\n"
            f"Маркер: {marker}\n"
            f"Оплачено в этом заказе: {hours} ч.\n"
            f"Накоплено всего: {total_hours}/{min_hours} ч.\n"
            f"Сумма: {amount_rub:.2f} RUB\n"
            f"Чат: {chat_link}"
        )
        return

    issued_good = rm.issue_specific_good(
        order_id=order_id,
        good_marker=marker,
        buyer_id=buyer_id,
        buyer_username=buyer_username,
        chat_id=chat_id,
        hours=total_hours,
    )

    if not issued_good:
        acc.send_message(chat_id, "❌ Не удалось выдать аккаунт.")
        return

    if pending_hours > 0:
        mark_pending_under_minimum_applied(buyer_id, marker, int(time.time()))
        acc.send_message(
            chat_id,
            f"✅ Ранее оплаченные часы учтены автоматически.\n"
            f"Суммарное время аренды: {total_hours} ч."
        )

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
        f"Время в текущем заказе: {hours} ч.\n"
        f"Суммарно выдано: {total_hours} ч.\n"
        f"Маркер: {marker}\n"
        f"good_id: {issued_good['id']}\n"
        f"Логин аккаунта: {issued_good['login']}\n"
        f"Чат: {chat_link}"
    )
