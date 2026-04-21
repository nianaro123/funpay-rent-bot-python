# handlers.py
import logging
import time
from urllib.parse import quote_plus

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
    create_lp_replacement_request,
    get_lp_replacement_request_by_buyer,
    delete_lp_replacement_request_by_buyer,
)
from settings import WELCOME_TEXT, HELP_TEXT
from order_handler import handle_paid_order_message, try_handle_account_selection_reply
from steam_guard import generate_steam_guard_code
from tg_notify import send_admin_notification

LOGGER = logging.getLogger(__name__)


class AutoReplyBot:
    ADMIN_REQUEST_COOLDOWN = 5 * 60
    # У FunPay есть ограничение на размер одного сообщения.
    # Делаем лимит ниже фактического, чтобы не упираться в URL-encoding / спецсимволы.
    MAX_FUNPAY_MESSAGE_LEN = 1500
    # Лимит для URL-encoded сообщения в request[data][content].
    # Эмодзи/спецсимволы резко увеличивают размер после quote_plus,
    # поэтому ограничиваем не только "чистую" длину строки.
    MAX_FUNPAY_ENCODED_MESSAGE_LEN = 3500

    def __init__(self, acc):
        self.acc = acc
        self.rm = RentalManager(acc)

    @staticmethod
    def _message_id_is_not_new(current_id: str, last_id: str) -> bool:
        # Updater может отдавать события не строго по возрастанию id.
        # Если фильтровать по <= last_id, можно пропустить валидные сообщения
        # (например, сначала отзыв с большим id, потом оплата с меньшим id).
        # Поэтому отсекаем только точные дубли по id.
        return str(current_id) == str(last_id)

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

    def _send_long_message(self, chat_id: str, lines: list[str]) -> None:
        def _encoded_len(value: str) -> int:
            return len(quote_plus(value, safe=""))

        def _split_long_line(line: str) -> list[str]:
            if not line:
                return [line]

            parts: list[str] = []
            current: list[str] = []

            for ch in line:
                current.append(ch)
                current_value = "".join(current)
                if (
                    len(current_value) > self.MAX_FUNPAY_MESSAGE_LEN
                    or _encoded_len(current_value) > self.MAX_FUNPAY_ENCODED_MESSAGE_LEN
                ):
                    # Отбрасываем символ в следующую часть, чтобы текущая точно влезала.
                    current.pop()
                    if current:
                        parts.append("".join(current))
                    current = [ch]

            if current:
                parts.append("".join(current))

            return parts

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0
        current_encoded_len = 0

        for line in lines:
            for line_part in _split_long_line(line):
                line_len = len(line_part)
                line_encoded_len = _encoded_len(line_part)
                separator_len = 1 if current_chunk else 0  # перевод строки при join
                separator_encoded_len = _encoded_len("\n") if current_chunk else 0

                if (
                    current_len + separator_len + line_len > self.MAX_FUNPAY_MESSAGE_LEN
                    or current_encoded_len + separator_encoded_len + line_encoded_len
                    > self.MAX_FUNPAY_ENCODED_MESSAGE_LEN
                ):
                    chunks.append("\n".join(current_chunk).strip())
                    current_chunk = [line_part]
                    current_len = line_len
                    current_encoded_len = line_encoded_len
                    continue

                current_chunk.append(line_part)
                current_len += separator_len + line_len
                current_encoded_len += separator_encoded_len + line_encoded_len

        if current_chunk:
            chunks.append("\n".join(current_chunk).strip())

        for chunk in chunks:
            if chunk:
                self.acc.send_message(chat_id, chunk)

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

        # В новом чате первое сообщение тоже должно обрабатываться:
        # это важно для команд (например, /free) и приветствия.

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

            if getattr(msg, "by_bot", False):
                LOGGER.debug("Пропуск сообщения бота chat_id=%s msg_id=%s", chat_id, msg_id)
                return

            if author_id == self.acc.id:
                LOGGER.debug("Пропуск собственного сообщения chat_id=%s msg_id=%s", chat_id, msg_id)
                return

            if author_id is not None:
                lp_handled = self.try_handle_lp_replacement_reply(
                    chat_id=chat_id,
                    buyer_id=author_id,
                    text=text,
                )
                if lp_handled:
                    return

                selection_handled = try_handle_account_selection_reply(
                    self.acc,
                    self.rm,
                    chat_id=chat_id,
                    buyer_id=author_id,
                    buyer_username=author,
                    text=text,
                )
                if selection_handled:
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

            self._send_long_message(chat_id, lines)
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

        if cmd == "/lp_zamena":
            if not rentals:
                self.acc.send_message(chat_id, "У вас нет активных аренд.")
                return

            rental = rentals[0]
            create_lp_replacement_request(
                buyer_id=int(author_id),
                chat_id=str(chat_id),
                order_id=str(rental["order_id"]),
                stage="await_games",
                lp_games=None,
                created_ts=int(time.time()),
            )
            self.acc.send_message(
                chat_id,
                "Напишите цифрой, сколько LP-игр на аккаунте."
            )
            return

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

    def try_handle_lp_replacement_reply(
        self,
        *,
        chat_id: int | str,
        buyer_id: int,
        text: str,
    ) -> bool:
        pending = get_lp_replacement_request_by_buyer(buyer_id)
        if not pending:
            return False

        value = text.strip()
        if not value.isdigit():
            self.acc.send_message(chat_id, "Пожалуйста, отправьте число.")
            return True

        if pending["stage"] == "await_games":
            lp_games = int(value)
            if lp_games < 1 or lp_games > 5:
                self.acc.send_message(chat_id, "Не удалось обработать это количество игр. Введите корректное число.")
                return True

            bonus_hours = lp_games * 2
            create_lp_replacement_request(
                buyer_id=buyer_id,
                chat_id=str(chat_id),
                order_id=str(pending["order_id"]),
                stage="await_decision",
                lp_games=lp_games,
                created_ts=int(time.time()),
            )
            self.acc.send_message(
                chat_id,
                "\n".join([
                    f"Зафиксировано: {lp_games} LP-игр.",
                    "Выберите вариант и отправьте цифру:",
                    "1 — заменить аккаунт на другой",
                    f"2 — отыграю LP и получу +{bonus_hours} ч. бонусного времени",
                ])
            )
            return True

        if pending["stage"] == "await_decision":
            lp_games = int(pending["lp_games"] or 0)
            if value == "1":
                ok, message = self.rm.replace_low_priority_account(
                    buyer_id=buyer_id,
                    chat_id=chat_id,
                    lp_games=lp_games,
                )
                if not ok:
                    self.acc.send_message(chat_id, f"❌ {message}")
                    return True
                delete_lp_replacement_request_by_buyer(buyer_id)
                return True

            if value == "2":
                bonus_hours = lp_games * 2
                if bonus_hours <= 0:
                    self.acc.send_message(chat_id, "Не удалось рассчитать бонус. Начните заново: /lp_zamena")
                    delete_lp_replacement_request_by_buyer(buyer_id)
                    return True
                ok = self.rm.extend_rental_by_order_id(
                    order_id=str(pending["order_id"]),
                    hours=bonus_hours,
                    source="lp_compensation",
                )
                if not ok:
                    self.acc.send_message(chat_id, "❌ Не удалось начислить бонусное время. Напишите /admin.")
                    return True
                self.acc.send_message(
                    chat_id,
                    f"✅ Отлично! После отыгрыша LP по заказу #{pending['order_id']} начислено +{bonus_hours} ч."
                )
                delete_lp_replacement_request_by_buyer(buyer_id)
                return True

            self.acc.send_message(chat_id, "Отправьте 1 или 2.")
            return True

        delete_lp_replacement_request_by_buyer(buyer_id)
        return False

    def tick(self):
        self.rm.tick()
