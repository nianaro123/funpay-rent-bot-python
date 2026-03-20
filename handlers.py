# handlers.py

from FunPayAPI.updater.events import NewMessageEvent
from rental_manager import RentalManager
from storage import (
    get_last_message_id,
    list_active_rentals_by_buyer,
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
            if author_id == 0:
                low = text.lower()

                if "оплатил" in low and "заказ" in low:
                    handle_paid_order_message(self.acc, self.rm, chat_id, text)
                    return

                if "написал отзыв" in low and "заказ" in low:
                    self.rm.handle_review_notice(chat_id, text)
                    return

                return

            if text.startswith("/"):
                self.handle_command(chat_id, text, author_id)
                return

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

        if author_id is None:
            self.acc.send_message(chat_id, "Не удалось определить пользователя.")
            return

        rentals = list_active_rentals_by_buyer(author_id)

        if cmd == "/acc":
            if not rentals:
                self.acc.send_message(chat_id, "У вас нет активных аренд")
                return

            lines = ["📄 Ваши активные аренды:"]
            for i, rental in enumerate(rentals, start=1):
                lines.append(
                    f"\n{i}. {rental['title']}\n"
                    f"Логин: {rental['login']}\n"
                    f"Пароль: {rental['password']}"
                )
                if rental["note"]:
                    lines.append(f"Примечание: {rental['note']}")
            self.acc.send_message(chat_id, "\n".join(lines))
            return

        if cmd == "/code":
            if not rentals:
                self.acc.send_message(chat_id, "У вас нет активных аренд")
                return

            lines = ["🔑 Ваши коды аренд:"]
            for i, rental in enumerate(rentals, start=1):
                lines.append(
                    f"{i}. {rental['login']} — код: {rental['code']}"
                )

            self.acc.send_message(chat_id, "\n".join(lines))
            return

        if cmd == "/time":
            if not rentals:
                self.acc.send_message(chat_id, "У вас нет активных аренд")
                return

            lines = ["⏱ Ваши активные аренды:"]
            for i, rental in enumerate(rentals, start=1):
                remaining = self.rm.get_remaining_time(rental)
                lines.append(
                    f"{i}. {rental['title']} — осталось: {remaining}"
                )
            self.acc.send_message(chat_id, "\n".join(lines))
            return

        self.acc.send_message(chat_id, "Неизвестная команда. Напишите /help")

    def tick(self):
        self.rm.tick()