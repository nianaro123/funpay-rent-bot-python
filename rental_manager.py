# rental_manager.py

import logging
import re
import secrets
import sqlite3
import time

from lot_manager import LotManager
from storage import (
    get_good_by_marker,
    count_free_goods,
    create_rental,
    list_active_rentals,
    close_rental,
    extend_rental,
    mark_warned,
    mark_ended_msg,
    get_rental_by_order_id,
    get_active_rental_by_buyer,
    set_bonus_applied,
    add_extension,
)

LOGGER = logging.getLogger(__name__)


class RentalManager:
    WARNING_SECONDS = 10 * 60
    GRACE_SECONDS = 15 * 60
    REVIEW_BONUS_SECONDS = 60 * 60

    def __init__(self, acc):
        self.acc = acc
        self.lot_manager = LotManager(acc)

    def generate_code(self, length: int = 8) -> str:
        return secrets.token_hex(8)[:length].upper()

    def get_free_accounts(self) -> int:
        return count_free_goods()

    def get_remaining_time(self, rental) -> str:
        now = int(time.time())
        remaining = rental["paid_end_ts"] - now

        if remaining <= 0:
            return "время истекло"

        hours = remaining // 3600
        minutes = (remaining % 3600) // 60

        if hours > 0:
            return f"{hours} ч. {minutes} мин."
        return f"{minutes} мин."

    def issue_specific_good(
        self,
        order_id: str,
        good_marker: str,
        buyer_id: int | None,
        buyer_username: str | None,
        chat_id: int | str,
        hours: int,
    ) -> bool:
        existing = get_rental_by_order_id(order_id)
        if existing:
            LOGGER.info("Заказ %s уже обработан", order_id)
            return False

        good = get_good_by_marker(good_marker)
        if not good:
            self.acc.send_message(chat_id, f"❌ В базе не найден товар для маркера {good_marker}")
            return False

        start_ts = int(time.time())
        paid_end_ts = start_ts + hours * 3600
        grace_end_ts = paid_end_ts + self.GRACE_SECONDS
        code = self.generate_code()

        try:
            create_rental(
                order_id=order_id,
                lot_id=good["lot_id"],
                chat_id=str(chat_id),
                buyer_id=buyer_id,
                buyer_username=buyer_username,
                good_id=good["id"],
                code=code,
                start_ts=start_ts,
                paid_end_ts=paid_end_ts,
                grace_end_ts=grace_end_ts,
            )
        except sqlite3.IntegrityError:
            LOGGER.warning(
                "Попытка двойной выдачи good_id=%s для order_id=%s",
                good["id"],
                order_id,
            )
            self.acc.send_message(
                chat_id,
                "❌ Этот аккаунт уже занят или только что был выдан другому покупателю."
            )
            return False

        lines = [
            "✅ Данные для входа:",
            f"Логин: {good['login']}",
            f"Пароль: {good['password']}",
        ]

        if good["note"]:
            lines.append(f"Примечание: {good['note']}")

        lines.extend([
            "",
            f"🧾 Ваш уникальный код: {code}",
            f"⏱ Время аренды: {hours} ч.",
            "⚠️ За 10 минут до окончания аренды я отправлю предупреждение.",
            "⌛ После завершения аренды действует буфер 15 минут.",
        ])

        self.acc.send_message(chat_id, "\n".join(lines))

        try:
            if good["lot_id"]:
                self.lot_manager.set_lot_busy(int(good["lot_id"]))
                LOGGER.info(
                    "Лот %s переведён в статус 'Занят!' после выдачи заказа %s",
                    good["lot_id"],
                    order_id,
                )
        except Exception as e:
            LOGGER.exception(
                "Не удалось сменить название лота %s на 'Занят!': %s",
                good["lot_id"],
                e,
            )

        LOGGER.info(
            "Выдан аккаунт good_id=%s, marker=%s, order_id=%s",
            good["id"], good_marker, order_id
        )
        return True

    def handle_review_notice(self, chat_id: int | str, text: str) -> None:
        self.acc.send_message(
            chat_id,
            "⭐ Спасибо за отзыв! Функция бонусного продления будет подключена следующим шагом."
        )

    def handle_refund_notice(self, chat_id: int | str, text: str) -> None:
        match = re.search(r"#([A-Z0-9]+)", text)
        if not match:
            LOGGER.warning("Не удалось извлечь order_id из сообщения о возврате: %s", text)
            return

        order_id = match.group(1)
        rental = get_rental_by_order_id(order_id)

        if not rental:
            LOGGER.info("Для возврата order_id=%s активная аренда не найдена", order_id)
            return

        if rental["closed"]:
            LOGGER.info("Аренда order_id=%s уже закрыта к моменту обработки возврата", order_id)
            return

        try:
            lot_id = int(rental["lot_id"]) if rental["lot_id"] else 0
            if lot_id:
                self.lot_manager.set_lot_free(lot_id)
                LOGGER.info("Лот %s переведён в статус 'Свободен!' после возврата по заказу %s", lot_id, order_id)
        except Exception as e:
            LOGGER.exception(
                "Не удалось вернуть лот в статус 'Свободен!' после возврата order_id=%s: %s",
                order_id,
                e,
            )

        try:
            close_rental(order_id)
            LOGGER.info("Аренда закрыта после возврата средств, order_id=%s", order_id)
        except Exception as e:
            LOGGER.exception("Не удалось закрыть аренду после возврата order_id=%s: %s", order_id, e)
            return

        try:
            self.acc.send_message(
                chat_id,
                f"ℹ️ Заказ #{order_id}: возврат средств зафиксирован. "
                "Аренда закрыта."
            )
        except Exception as e:
            LOGGER.exception("Не удалось отправить сообщение после возврата order_id=%s: %s", order_id, e)

    def extend_active_rental_for_buyer(
        self,
        buyer_id: int,
        hours: int,
        source: str = "manual"
    ) -> bool:
        rental = get_active_rental_by_buyer(buyer_id)
        if not rental:
            return False

        add_seconds = hours * 3600
        extend_rental(rental["order_id"], add_seconds)
        add_extension(rental["id"], source, hours, int(time.time()))

        LOGGER.info(
            "Продлена аренда order_id=%s на %s ч. source=%s",
            rental["order_id"], hours, source,
        )
        return True

    def apply_review_bonus(self, buyer_id: int, chat_id: int | str) -> bool:
        rental = get_active_rental_by_buyer(buyer_id)
        if not rental:
            return False

        if rental["bonus_applied"]:
            self.acc.send_message(chat_id, "ℹ️ Бонус за отзыв уже был использован.")
            return False

        extend_rental(rental["order_id"], self.REVIEW_BONUS_SECONDS)
        set_bonus_applied(rental["order_id"])
        add_extension(rental["id"], "review_bonus", 1, int(time.time()))

        self.acc.send_message(chat_id, "⭐ Спасибо за отзыв! Аренда продлена на 1 час.")
        LOGGER.info("Бонус за отзыв применён для order_id=%s", rental["order_id"])
        return True

    def tick(self) -> None:
        now = int(time.time())
        rentals = list_active_rentals()

        for rental in rentals:
            order_id = rental["order_id"]
            chat_id = rental["chat_id"]

            time_left = rental["paid_end_ts"] - now
            if rental["warned_10m"] == 0 and 0 < time_left <= self.WARNING_SECONDS:
                try:
                    self.acc.send_message(
                        chat_id,
                        f"⚠️ Заказ #{order_id}: до окончания аренды осталось 10 минут."
                    )
                    mark_warned(order_id)
                except Exception as e:
                    LOGGER.exception("Ошибка предупреждения order_id=%s: %s", order_id, e)

            if rental["ended_msg_sent"] == 0 and now >= rental["paid_end_ts"]:
                try:
                    self.acc.send_message(
                        chat_id,
                        f"⛔ Заказ #{order_id}: время аренды завершено.\n"
                        "Пожалуйста, покиньте аккаунт и подтвердите лот."
                    )
                    mark_ended_msg(order_id)
                except Exception as e:
                    LOGGER.exception("Ошибка сообщения о завершении order_id=%s: %s", order_id, e)

            if now >= rental["grace_end_ts"]:
                try:
                    try:
                        lot_id = int(rental["good_lot_id"]) if rental["good_lot_id"] else 0
                        if lot_id:
                            self.lot_manager.set_lot_free(lot_id)
                            LOGGER.info("Лот %s переведён в статус 'Свободен!'", lot_id)
                    except Exception as e:
                        LOGGER.exception(
                            "Не удалось сменить название лота обратно на 'Свободен!' для order_id=%s: %s",
                            order_id,
                            e,
                        )

                    close_rental(order_id)
                    LOGGER.info("Аренда закрыта order_id=%s", order_id)
                except Exception as e:
                    LOGGER.exception("Ошибка закрытия аренды order_id=%s: %s", order_id, e)