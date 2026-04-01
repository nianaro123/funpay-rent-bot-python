#rental_service.py
import secrets
import time

from FunPayAPI import Account

GRACE_MINUTES = 15


def generate_unique_code(length: int = 8) -> str:
    return secrets.token_hex(8)[:length].upper()


def format_credentials(good) -> str:
    lines = [
        "✅ Данные для входа:",
        f"Логин: {good['login']}",
        f"Пароль: {good['password']}",
    ]
    note = (good["note"] or "").strip()
    if note:
        lines.append(f"Примечание: {note}")
    return "\n".join(lines)


def rent_good_for_order(
    acc: Account,
    order_id: str,
    lot_id: int,
    buyer_id: int | None,
    buyer_username: str | None,
    chat_id: int | str,
    rental_hours: int
) -> bool:
    """
    Выдаёт аккаунт по конкретному lot_id.
    Возвращает True если всё успешно.
    """

    # если заказ уже обработан — не выдаём повторно
    existing = get_rental_by_order_id(order_id)
    if existing:
        return False

    good = get_good_by_lot_id(lot_id)
    if not good:
        acc.send_message(chat_id, "❌ Товар не найден в базе.")
        return False

    if good["status"] != "free":
        acc.send_message(chat_id, "⚠️ Этот аккаунт уже занят. Напиши продавцу.")
        return False

    start_ts = int(time.time())
    end_ts = start_ts + rental_hours * 3600
    grace_end_ts = end_ts + GRACE_MINUTES * 60
    code = generate_unique_code()

    create_rental(
        order_id=order_id,
        lot_id=lot_id,
        good_id=good["id"],
        buyer_id=buyer_id,
        buyer_username=buyer_username,
        chat_id=str(chat_id),
        rental_hours=rental_hours,
        unique_code=code,
        start_ts=start_ts,
        end_ts=end_ts,
        grace_end_ts=grace_end_ts
    )

    set_good_status(good["id"], "rented")

    message = (
        f"{format_credentials(good)}\n\n"
        f"⏱ Время аренды: {rental_hours} ч.\n"
        f"⚠️ За 10 минут до завершения я отправлю предупреждение.\n"
    )

    acc.send_message(chat_id, message)
    return True