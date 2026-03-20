# handlers.py

from FunPayAPI.updater.events import NewMessageEvent
from rental_manager import RentalManager
from storage import (
    get_active_rental_by_buyer,
    get_last_message_id,
    set_last_message_id,
)
from config import WELCOME_TEXT, HELP_TEXT
from order_handler import handle_paid_order_message


class AutoReplyBot:
    def __init__(self, acc):
        self.acc = acc
        self.rm = RentalManager(acc)
        self.welcomed_chats = set()

    def handle_new_message(self, event: NewMessageEvent):
        msg = event.message

        if getattr(msg, "by_bot", False):
            return

        text = (msg.text or "").strip()
        if not text:
            return

        chat_id = msg.chat_id
        author_id = getattr(msg, "author_id", None)
        msg_id = str(getattr(msg, "id", ""))

        last_id = get_last_message_id(str(chat_id))
        if last_id is not None and msg_id and msg_id <= str(last_id):
            return

        try:
            # 1. системные сообщения FunPay
            if author_id == 0:
                low = text.lower()

                if "оплатил" in low and "заказ" in low:
                    handle_paid_order_message(self.acc, self.rm, chat_id, text)
                    return

                if "написал отзыв" in low and "заказ" in low:
                    self.rm.handle_review_notice(chat_id, text)
                    return

                return

            # 2. команды
            if text.startswith("/"):
                self.handle_command(chat_id, text, author_id)
                return

            # 3. обычное сообщение -> приветствие только 1 раз на чат
            if chat_id not in self.welcomed_chats:
                self.acc.send_message(chat_id, WELCOME_TEXT)
                self.welcomed_chats.add(chat_id)

        finally:
            if msg_id:
                set_last_message_id(str(chat_id), msg_id)

    def handle_command(self, chat_id, text, author_id=None):
        cmd = text.split()[0].lower()

        if cmd == "/help":
            self.acc.send_message(chat_id, HELP_TEXT)
            return

        if cmd == "/free":
            free = self.rm.get_free_accounts()
            self.acc.send_message(chat_id, f"Свободных аккаунтов: {free}")
            return

        if cmd == "/time":
            if author_id is None:
                self.acc.send_message(chat_id, "Не удалось определить пользователя.")
                return

            rental = get_active_rental_by_buyer(author_id)

            if not rental:
                self.acc.send_message(chat_id, "У вас нет активной аренды")
                return

            remaining = self.rm.get_remaining_time(rental)
            self.acc.send_message(chat_id, f"Осталось времени: {remaining}")
            return

        if cmd == "/code":
            if author_id is None:
                self.acc.send_message(chat_id, "Не удалось определить пользователя.")
                return

            rental = get_active_rental_by_buyer(author_id)

            if not rental:
                self.acc.send_message(chat_id, "У вас нет активной аренды")
                return

            self.acc.send_message(chat_id, f"Ваш код аренды: {rental['code']}")
            return

        if cmd == "/acc":
            if author_id is None:
                self.acc.send_message(chat_id, "Не удалось определить пользователя.")
                return

            rental = get_active_rental_by_buyer(author_id)

            if not rental:
                self.acc.send_message(chat_id, "У вас нет активной аренды")
                return

            self.acc.send_message(
                chat_id,
                f"Логин: {rental['login']}\nПароль: {rental['password']}"
            )
            return

        self.acc.send_message(chat_id, "Неизвестная команда. Напишите /help")

    def tick(self):
        self.rm.tick()