# handlers.py

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
from config import WELCOME_TEXT, HELP_TEXT
from order_handler import handle_paid_order_message
from steam_guard import generate_steam_guard_code
from tg_notify import send_admin_notification


class AutoReplyBot:
    ADMIN_REQUEST_COOLDOWN = 5 * 60  # 5 минут

    def __init__(self, acc):
        self.acc = acc
        self.rm = RentalManager(acc)

    def handle_new_message(self, event: NewMessageEvent):
        msg = event.message

        text = (msg.text or "").strip()
        if not text:
            return

        chat_id = msg.chat_id
        author_id = getattr(msg, "author_id", None)
        author = getattr(msg, "author", None)
        msg_id = str(getattr(msg, "id", ""))

        last_id = get_last_message_id(str(chat_id))
        if last_id is not None and msg_id and self._message_id_is_not_new(msg_id, last_id):
            return

        try:
            # Игнорируем сообщения, которые отправил сам бот
            if getattr(msg, "by_bot", False):
                return

            # Игнорируем свои собственные сообщения продавца
            if author_id == self.acc.id:
                return

            # Системные сообщения FunPay
            if author_id == 0:
                low = text.lower()

                if "оплатил" in low and "заказ" in low:
                    handle_paid_order_message(self.acc, self.rm, chat_id, text)
                    return

                if "подтвердил успешное выполнение" in low and "заказ" in low:
                    self.rm.handle_order_confirmed_notice(chat_id, text)
                    return

                if "написал отзыв" in low and "заказ" in low:
                    self.rm.handle_review_notice(chat_id, text)
                    return

                if "вернул деньги покупателю" in low and "заказ" in low:
                    self.rm.handle_refund_notice(chat_id, text)
                    return

                return

            # Команды клиента
            if text.startswith("/"):
                self.handle_command(chat_id, text, author_id, author)
                return

            # Приветствие клиента только один раз даже после рестарта
            if not is_chat_welcomed(str(chat_id)):
                self.acc.send_message(chat_id, WELCOME_TEXT)
                mark_chat_welcomed(str(chat_id))

        finally:
            if msg_id:
                set_last_message_id(str(chat_id), msg_id)

    @staticmethod
    def _message_id_is_not_new(current_id: str, last_id: str) -> bool:
        try:
            return int(current_id) <= int(last_id)
        except (TypeError, ValueError):
            return current_id <= str(last_id)

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
                f"Клиент {username} отправил запрос на диалог с вами."
                f"Ссылка на чат:{chat_link}"
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
                self.acc.send_message(chat_id, "У вас нет активных аренд")
                return

            lines = ["📄 Ваши активные аренды:"]
            for i, rental in enumerate(rentals, start=1):
                lines.append(
                    f"{i}. {rental['title']}"
                    f"Логин: {rental['login']}"
                    f"Пароль: {rental['password']}"
                )
            self.acc.send_message(chat_id, "".join(lines))
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
            self.acc.send_message(chat_id, "".join(lines))
            return

        if cmd == "/time":
            if not rentals:
                self.acc.send_message(chat_id, "У вас нет активных аренд")
                return

            lines = ["⏱ Ваши активные аренды:"]
            for i, rental in enumerate(rentals, start=1):
                remaining = self.rm.get_remaining_time(rental)
                lines.append(f"{i}. {rental['title']} — осталось: {remaining}")
            self.acc.send_message(chat_id, "".join(lines))
            return

        self.acc.send_message(chat_id, "Неизвестная команда. Напишите /help")

    def tick(self):
        self.rm.tick()
