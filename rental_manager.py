# rental_manager.py

import logging
import re
import secrets
import sqlite3
import time

from storage import mark_order_confirmed, mark_order_refunded
from lot_manager import LotManager
from steam_guard import generate_steam_guard_code
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
    get_rental_with_good_by_order_id,
    get_active_rental_by_buyer,
    set_bonus_applied,
    add_extension,
)
from tg_notify import send_admin_notification
from steam_session_worker import trigger_steam_sign_out_async

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

    def _format_datetime(self, ts: int) -> str:
        return time.strftime("%d.%m %H:%M", time.localtime(ts))

    def _format_issue_message(self, good, order_id: str, hours: int, steam_guard_code: str | None) -> str:
        paid_end_ts = int(time.time()) + hours * 3600
        lot_id = good["lot_id"]
        lot_url = f"https://funpay.com/lots/offer?id={lot_id}" if lot_id else None

        lines = [
            "🎉 Заказ успешно оформлен!",
            f"Номер заказа: #{order_id}",
            "",
            "🔐 Данные для входа:",
            f"• Логин: {good['login']}",
            f"• Пароль: {good['password']}",
            f"• Steam Guard: {steam_guard_code if steam_guard_code else 'не задан'}",
            "",
            "⏳ Информация по аренде:",
            f"• Оплачено времени: {hours} ч.",
            f"• Аккаунт доступен до: {self._format_datetime(paid_end_ts)}",
            "• За 10 минут до окончания я пришлю напоминание.",
        ]

        if lot_url:
            lines.extend([
                "",
                "🔄 Хотите продлить аренду?",
                "Оплачивайте продление по этой ссылке:",
                lot_url,
            ])

        lines.extend([
            "",
            "💬 Если возникнут вопросы, напишите /help — я подскажу доступные команды.",
            "⭐ Новым клиентам предусмотрен дополнительный час времени за положительный отзыв! Если вы хотите им воспользоваться - оставьте отзыв до окончания времени аренды.",
        ])
        return "\n".join(lines)

    def _format_review_bonus_message(self, rental) -> str:
        return "\n".join([
            "⭐ Спасибо за отзыв! Вы получили 1 час бонусного времени!",
            f"Текущее время аренды заказа #{rental['order_id']}: {self.get_remaining_time(rental)}.",
            "",
            "Приятной игры! Если захотите продлить аренду, просто оплатите тот же лот.",
        ])

    def _format_warning_message(self, order_id: str, rental) -> str:
        lot_id = rental["lot_id"]
        lot_url = f"https://funpay.com/lots/offer?id={lot_id}" if lot_id else "ссылка недоступна"

        return "\n".join([
            f"⚠️ Напоминание по заказу #{order_id}",
            "До окончания оплаченного времени осталось 10 минут.",
            f"Если хотите продлить аренду — оплатите этот лот: {lot_url}.",
        ])

    def _format_end_message(self, order_id: str) -> str:
        return "\n".join([
            f"⛔ Оплаченное время аренды по заказу #{order_id} истекло.",
            "Пожалуйста, подтвердите лот и покиньте аккаунт.",
            "Через 15 минут ваша сессия в Steam будет автоматически завершена и вас выкинет из аккаунта!",
        ])

    def _format_refund_message(self, order_id: str) -> str:
        return "\n".join([
            f"ℹ️ Заказ #{order_id} был отменён / возвращён.",
            "Аренда закрыта, аккаунт возвращён в пул.",
        ])

    def issue_specific_good(
        self,
        order_id: str,
        good_marker: str,
        buyer_id: int | None,
        buyer_username: str | None,
        chat_id: int | str,
        hours: int,
    ):
        existing = get_rental_by_order_id(order_id)
        if existing:
            LOGGER.info("Заказ %s уже обработан", order_id)
            return None

        good = get_good_by_marker(good_marker)
        if not good:
            self.acc.send_message(chat_id, f"❌ В базе не найден товар для маркера {good_marker}")
            return None

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
            return None

        steam_guard_code = None
        shared_secret = (good["shared_secret"] or "").strip()
        if shared_secret:
            try:
                steam_guard_code = generate_steam_guard_code(shared_secret)
            except Exception as e:
                LOGGER.exception("Ошибка генерации Steam Guard кода для good_id=%s: %s", good["id"], e)

        self.acc.send_message(
            chat_id,
            self._format_issue_message(
                good=good,
                order_id=order_id,
                hours=hours,
                steam_guard_code=steam_guard_code,
            ),
        )

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
        return good

    def extend_rental_by_order_id(self, order_id: str, hours: int, source: str = "same_marker_rebuy") -> bool:
        rental = get_rental_by_order_id(order_id)
        if not rental or rental["closed"]:
            return False

        add_seconds = hours * 3600
        extend_rental(order_id, add_seconds)
        add_extension(rental["id"], source, hours, int(time.time()))
        LOGGER.info("Продлена аренда order_id=%s на %s ч. source=%s", order_id, hours, source)
        return True

    def handle_review_notice(self, chat_id: int | str, text: str) -> None:
        match = re.search(r"#([A-Z0-9]+)", text)
        if not match:
            LOGGER.warning("Не удалось извлечь order_id из сообщения об отзыве: %s", text)
            return

        order_id = match.group(1)
        rental = get_rental_by_order_id(order_id)
        if not rental or rental["closed"]:
            LOGGER.info("Для отзыва order_id=%s активная аренда не найдена", order_id)
            return

        if rental["bonus_applied"]:
            self.acc.send_message(
                chat_id,
                f"ℹ️ По заказу #{order_id} бонус за отзыв уже был начислен."
            )
            return

        extend_rental(order_id, self.REVIEW_BONUS_SECONDS)
        set_bonus_applied(order_id)
        add_extension(rental["id"], "review_bonus", 1, int(time.time()))

        rental = get_rental_by_order_id(order_id)
        self.acc.send_message(chat_id, self._format_review_bonus_message(rental))
        LOGGER.info("Бонус за отзыв применён для order_id=%s", order_id)

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

        rental_snapshot = get_rental_with_good_by_order_id(order_id)

        try:
            lot_id = int(rental["lot_id"]) if rental["lot_id"] else 0
            if lot_id:
                self.lot_manager.set_lot_free(lot_id)
                LOGGER.info(
                    "Лот %s переведён в статус 'Свободен!' после возврата по заказу %s",
                    lot_id,
                    order_id,
                )
        except Exception as e:
            LOGGER.exception(
                "Не удалось вернуть лот в статус 'Свободен!' после возврата order_id=%s: %s",
                order_id,
                e,
            )

        try:
            close_rental(order_id)
            mark_order_refunded(order_id, int(time.time()))
            LOGGER.info("Аренда закрыта после возврата средств, order_id=%s", order_id)

            if rental_snapshot:
                trigger_steam_sign_out_async(rental_snapshot, reason="refund")
        except Exception as e:
            LOGGER.exception("Не удалось закрыть аренду после возврата order_id=%s: %s", order_id, e)
            return

        try:
            self.acc.send_message(chat_id, self._format_refund_message(order_id))
        except Exception as e:
            LOGGER.exception("Не удалось отправить сообщение после возврата order_id=%s: %s", order_id, e)

    def handle_order_confirmed_notice(self, chat_id: int | str, text: str) -> None:
        match_order = re.search(r"#([A-Z0-9]+)", text)
        match_buyer = re.search(r"Покупатель\s+(.+?)\s+подтвердил успешное выполнение", text)

        order_id = match_order.group(1) if match_order else "UNKNOWN"
        buyer_name = match_buyer.group(1) if match_buyer else "Неизвестный клиент"
        chat_link = f"https://funpay.com/chat/?node={chat_id}"
        mark_order_confirmed(order_id, int(time.time()))

        send_admin_notification(
            "\n".join([
                "✅ Аренда подтверждена",
                f"Клиент: {buyer_name}",
                f"Заказ: #{order_id}",
                f"Чат: {chat_link}",
            ])
        )

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

        rental = get_rental_by_order_id(rental["order_id"])
        self.acc.send_message(chat_id, self._format_review_bonus_message(rental))
        LOGGER.info("Бонус за отзыв применён для order_id=%s", rental["order_id"])
        return True

    def tick(self) -> None:
        now = int(time.time())
        rentals = list_active_rentals()

        for rental in rentals:
            order_id = rental["order_id"]
            chat_id = rental["chat_id"]
            buyer_name = rental["buyer_username"] or rental["buyer_id"] or "Неизвестный клиент"

            time_left = rental["paid_end_ts"] - now
            if rental["warned_10m"] == 0 and 0 < time_left <= self.WARNING_SECONDS:
                try:
                    self.acc.send_message(
                        chat_id,
                        self._format_warning_message(order_id, rental),
                    )
                    mark_warned(order_id)
                except Exception as e:
                    LOGGER.exception("Ошибка предупреждения order_id=%s: %s", order_id, e)

            if rental["ended_msg_sent"] == 0 and now >= rental["paid_end_ts"]:
                try:
                    self.acc.send_message(
                        chat_id,
                        self._format_end_message(order_id),
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

                    trigger_steam_sign_out_async(rental, reason="grace_timeout")

                    chat_link = f"https://funpay.com/chat/?node={chat_id}"
                    send_admin_notification(
                        "\n".join([
                            "⛔ Аренда закрыта автоматически без подтверждения",
                            f"Клиент: {buyer_name}",
                            f"Заказ: #{order_id}",
                            f"good_id: {rental['good_id']}",
                            f"Маркер: {rental['marker']}",
                            f"Логин аккаунта: {rental['login']}",
                            "Статус: аккаунт возвращён в пул, лот переведён в 'Свободен!'",
                            f"Чат: {chat_link}",
                        ])
                    )
                except Exception as e:
                    LOGGER.exception("Ошибка закрытия аренды order_id=%s: %s", order_id, e)