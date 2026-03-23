# handlers.py

import time

from FunPayAPI.updater.events import NewMessageEvent
from rental_manager import RentalManager
from storage import (
    get_last_message_id,
    list_active_rentals_by_buyer,
    set_last_message_id,
)
from config import WELCOME_TEXT, HELP_TEXT
from order_handler import handle_paid_order_message
from steam_guard import generate_steam_guard_code
from tg_notify import send_admin_notification


class AutoReplyBot:
    ADMIN_REQUEST_COOLDOWN = 5 * 60  # 5 минут

    def __init__(self, acc):
        self.acc = acc
        self.rm = RentalManager(acc)
        self.welcomed_chats = set()
        self.admin_request_last_ts = {}  # chat_id -> timestamp

    def handle_new_message(self, event: NewMessageEvent):
        msg = event.message

        if getattr(msg, "by_bot", False):
            return

        text = (msg.text or "").strip()
        if not text:
            return

        chat_id = msg.chat_id
        author_id = getattr(msg, "author_id", None)
        author = getattr(msg, "author", None)
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

                if "вернул деньги покупателю" in low and "заказ" in low:
                    self.rm.handle_refund_notice(chat_id, text)
                    return

                return

            if text.startswith("/"):
                self.handle_command(chat_id, text, author_id, author)
                return

            if chat_id not in self.welcomed_chats:
                self.acc.send_message(chat_id, WELCOME_TEXT)
                self.welcomed_chats.add(chat_id)

        finally:
            if msg_id:
                set_last_message_id(str(chat_id), msg_id)

    def handle_command(self, chat_id, text, author_id=None, author=None):
        cmd = text.split()[0].lower()

        if cmd == "/help":
            self.acc.send_message(chat_id, HELP_TEXT)
            return

        if cmd == "/free":
            free = self.rm.get_free_accounts()
            self.acc.send_message(chat_id, f"Свободных аккаунтов: {free}")
            return

        if cmd == "/admin":
            now = int(time.time())
            last_ts = self.admin_request_last_ts.get(chat_id, 0)
            cooldown_left = self.ADMIN_REQUEST_COOLDOWN - (now - last_ts)

            if cooldown_left > 0:
                minutes = cooldown_left // 60
                seconds = cooldown_left % 60

                if minutes > 0:
                    wait_text = f"{minutes} мин. {seconds} сек."
                else:
                    wait_text = f"{seconds} сек."

                self.acc.send_message(
                    chat_id,
                    f"⏳ Вы уже отправляли запрос продавцу недавно. "
                    f"Повторно можно вызвать через {wait_text}"
                )
                return

            username = author or "Неизвестный клиент"
            chat_link = f"https://funpay.com/chat/?node={chat_id}"

            notify_text = (
                f"Клиент {username} отправил запрос на диалог с вами.\n\n"
                f"Ссылка на чат:\n{chat_link}"
            )

            ok = send_admin_notification(notify_text)

            if ok:
                self.admin_request_last_ts[chat_id] = now
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
                self.acc.send_message(chat_id, "У вас нет активных аренд")
                return

            lines = ["📄 Ваши активные аренды:"]
            for i, rental in enumerate(rentals, start=1):
                lines.append(
                    f"\n{i}. {rental['title']}\n"
                    f"Логин: {rental['login']}\n"
                    f"Пароль: {rental['password']}"
                )
            self.acc.send_message(chat_id, "\n".join(lines))
            return

        if cmd == "/code":
            if not rentals:
                self.acc.send_message(chat_id, "У вас нет активных аренд")
                return

            lines = ["🔑 Ваши Steam Guard коды:"]
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
                self.acc.send_message(chat_id, "У вас нет активных аренд")
                return

            lines = ["⏱ Ваши активные аренды:"]
            for i, rental in enumerate(rentals, start=1):
                remaining = self.rm.get_remaining_time(rental)
                lines.append(f"{i}. {rental['title']} — осталось: {remaining}")
            self.acc.send_message(chat_id, "\n".join(lines))
            return

        self.acc.send_message(chat_id, "Неизвестная команда. Напишите /help")

    def tick(self):
        self.rm.tick()