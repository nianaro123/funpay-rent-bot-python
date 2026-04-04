# handlers.py
import logging
import time

from FunPayAPI.updater.events import NewMessageEvent

from rental_manager import RentalManager
from storage import (
    get_last_message_id,
    list_active_rentals_by_buyer,
    list_goods,
    set_last_message_id,
    get_admin_request_ts,
    set_admin_request_ts,
    is_chat_welcomed,
    mark_chat_welcomed,
)
from settings import WELCOME_TEXT, HELP_TEXT
from order_handler import handle_paid_order_message
from steam_guard import generate_steam_guard_code
from tg_notify import send_admin_notification

LOGGER = logging.getLogger(__name__)


class AutoReplyBot:
    ADMIN_REQUEST_COOLDOWN = 5 * 60

    def __init__(self, acc):
        self.acc = acc
        self.rm = RentalManager(acc)

    @staticmethod
    def _message_id_is_not_new(current_id: str, last_id: str) -> bool:
        try:
            return int(current_id) <= int(last_id)
        except (TypeError, ValueError):
            return current_id <= str(last_id)

    @staticmethod
    def _is_paid_order_notice(text_lower: str) -> bool:
        return "оплатил заказ" in text_lower and "заказ" in text_lower

    @staticmethod
    def _is_order_confirmed_notice(text_lower: str) -> bool:
        return "подтвердил успешное выполнение" in text_lower and "заказ" in text_lower

    @staticmethod
    def _is_review_notice(text_lower: str) -> bool:
        return "написал отзыв" in text_lower and "заказ" in text_lower

    @staticmethod
    def _is_refund_notice(text_lower: str) -> bool:
        return "вернул деньги покупателю" in text_lower and "заказ" in text_lower

    def handle_new_message(self, event: NewMessageEvent):
        msg = event.message

        text = (msg.text or "").strip()
        if not text:
            return

        chat_id = str(msg.chat_id)
        author_id = getattr(msg, "author_id", None)
        author = getattr(msg, "author", None)
        msg_id = str(getattr(msg, "id", "")).strip()

        if not msg_id:
            LOGGER.warning("Пропуск сообщения без id в chat_id=%s", chat_id)
            return

        low = text.lower()
        is_system_notice = (
            self._is_paid_order_notice(low)
            or self._is_order_confirmed_notice(low)
            or self._is_review_notice(low)
            or self._is_refund_notice(low)
        )

        last_id = get_last_message_id(chat_id)

        # Если для чата ещё нет chat_state:
        # - первое обычное сообщение просто запоминаем и не обрабатываем
        # - первое системное сообщение ОБРАБАТЫВАЕМ
        if last_id is None and not is_system_notice:
            LOGGER.info(
                "Инициализация chat_state без обработки первого обычного сообщения chat_id=%s msg_id=%s",
                chat_id,
                msg_id,
            )
            set_last_message_id(chat_id, msg_id)
            return

        # Защита от дублей / старых сообщений
        if last_id is not None and self._message_id_is_not_new(msg_id, last_id):
            LOGGER.debug(
                "Пропуск старого/дублирующего сообщения chat_id=%s msg_id=%s last_id=%s",
                chat_id,
                msg_id,
                last_id,
            )
            return

        try:
            if getattr(msg, "by_bot", False):
                LOGGER.debug("Пропуск сообщения бота chat_id=%s msg_id=%s", chat_id, msg_id)
                return

            if author_id == self.acc.id:
                LOGGER.debug("Пропуск собственного сообщения chat_id=%s msg_id=%s", chat_id, msg_id)
                return

            # Системные сообщения определяем по тексту, а не по author_id == 0
            if self._is_paid_order_notice(low):
                LOGGER.info("Обработка уведомления об оплате chat_id=%s msg_id=%s", chat_id, msg_id)
                handle_paid_order_message(self.acc, self.rm, chat_id, text)
                return

            if self._is_order_confirmed_notice(low):
                LOGGER.info("Обработка уведомления о подтверждении chat_id=%s msg_id=%s", chat_id, msg_id)
                self.rm.handle_order_confirmed_notice(chat_id, text)
                return

            if self._is_review_notice(low):
                LOGGER.info("Обработка уведомления об отзыве chat_id=%s msg_id=%s", chat_id, msg_id)
                self.rm.handle_review_notice(chat_id, text)
                return

            if self._is_refund_notice(low):
                LOGGER.info("Обработка уведомления о возврате chat_id=%s msg_id=%s", chat_id, msg_id)
                self.rm.handle_refund_notice(chat_id, text)
                return

            # Команды клиента
            if text.startswith("/"):
                LOGGER.info("Обработка команды chat_id=%s msg_id=%s text=%s", chat_id, msg_id, text)
                self.handle_command(chat_id, text, author_id, author)
                return

            # Обычное сообщение клиента: только приветствие один раз
            if not is_chat_welcomed(chat_id):
                LOGGER.info("Отправка приветствия chat_id=%s msg_id=%s", chat_id, msg_id)
                self.acc.send_message(chat_id, WELCOME_TEXT)
                mark_chat_welcomed(chat_id)

        finally:
            set_last_message_id(chat_id, msg_id)

    def handle_command(self, chat_id, text, author_id=None, author=None):
        cmd = text.split()[0].lower()

        if cmd == "/help":
            self.acc.send_message(chat_id, HELP_TEXT)
            return

        if cmd == "/free":
            goods = list_goods()
            free_goods = [g for g in goods if g["is_active"] and not g["is_busy"]]

            if not free_goods:
                self.acc.send_message(chat_id, "❌ Сейчас свободных аккаунтов нет.")
                return

            lines = ["🟢 Свободные аккаунты:", ""]
            for i, g in enumerate(free_goods, start=1):
                lot_id = g["lot_id"]
                lot_link = f"https://funpay.com/lots/offer?id={lot_id}" if lot_id else "не указана"

                lines.extend([
                    f"{i}. {g['title']}",
                    f"Ссылка на лот: {lot_link}",
                    "",
                ])

            self.acc.send_message(chat_id, "\n".join(lines).strip())
            return

        if cmd == "/admin":
            now = int(time.time())
            last_ts = get_admin_request_ts(str(chat_id)) or 0
            cooldown_left = self.ADMIN_REQUEST_COOLDOWN - (now - last_ts)

            if cooldown_left > 0:
                minutes = cooldown_left // 60
                seconds = cooldown_left % 60
                wait_text = f"{minutes} мин. {seconds} сек." if minutes > 0 else f"{seconds} сек."

                self.acc.send_message(
                    chat_id,
                    f"⏳ Вы уже отправляли запрос продавцу недавно. Повторно можно вызвать через {wait_text}"
                )
                return

            username = author or "Неизвестный клиент"
            chat_link = f"https://funpay.com/chat/?node={chat_id}"

            notify_text = (
                f"Клиент {username} отправил запрос на диалог с вами.\n"
                f"Ссылка на чат: {chat_link}"
            )

            ok = send_admin_notification(notify_text)

            if ok:
                set_admin_request_ts(str(chat_id), now)
                self.acc.send_message(
                    chat_id,
                    "✅ Продавцу отправлено уведомление. Пожалуйста, ожидайте ответа."
                )
            else:
                self.acc.send_message(
                    chat_id,
                    "⚠️ Не удалось отправить уведомление продавцу. Попробуйте позже."
                )
            return

        if author_id is None:
            self.acc.send_message(chat_id, "Не удалось определить пользователя.")
            return

        rentals = list_active_rentals_by_buyer(author_id)

        if cmd == "/acc":
            if not rentals:
                self.acc.send_message(chat_id, "У вас нет активных аренд.")
                return

            lines = ["📄 Ваши активные аренды:", ""]
            for i, rental in enumerate(rentals, start=1):
                lines.extend([
                    f"{i}. {rental['title']}",
                    f"Логин: {rental['login']}",
                    f"Пароль: {rental['password']}",
                    "",
                ])
            self.acc.send_message(chat_id, "\n".join(lines).strip())
            return

        if cmd == "/code":
            if not rentals:
                self.acc.send_message(chat_id, "У вас нет активных аренд.")
                return

            lines = ["🔑 Ваши Steam Guard коды:", ""]
            for i, rental in enumerate(rentals, start=1):
                code = generate_steam_guard_code(rental["shared_secret"])
                if code:
                    lines.append(f"{i}. {rental['login']} — код: {code}")
                else:
                    lines.append(f"{i}. {rental['login']} — shared_secret не задан")
            self.acc.send_message(chat_id, "\n".join(lines))
            return

        if cmd == "/time":
            if not rentals:
                self.acc.send_message(chat_id, "У вас нет активных аренд.")
                return

            lines = ["⏱ Ваши активные аренды:", ""]
            for i, rental in enumerate(rentals, start=1):
                remaining = self.rm.get_remaining_time(rental)
                lines.append(f"{i}. {rental['title']} — осталось: {remaining}")
            self.acc.send_message(chat_id, "\n".join(lines))
            return

        self.acc.send_message(chat_id, "Неизвестная команда. Напишите /help")

    def tick(self):
        self.rm.tick()