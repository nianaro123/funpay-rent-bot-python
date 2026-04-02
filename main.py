# main.py

import logging
import threading

from FunPayAPI import Account
from FunPayAPI.updater.runner import Runner
from FunPayAPI.updater.events import NewMessageEvent

from settings import GOLDEN_KEY, USER_AGENT, REQUESTS_DELAY
from handlers import AutoReplyBot
from storage import init_db, get_last_message_id, set_last_message_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


def start_tick_loop(bot, stop_event: threading.Event, interval: int = 5):
    while not stop_event.is_set():
        try:
            bot.tick()
        except Exception:
            logging.exception("Ошибка в tick()")
        stop_event.wait(interval)


def _is_newer_message_id(current_id: str, stored_id: str | None) -> bool:
    if stored_id is None:
        return True

    try:
        return int(current_id) > int(stored_id)
    except (TypeError, ValueError):
        return str(current_id) > str(stored_id)


def bootstrap_chat_state(acc) -> None:
    """
    На старте синхронизируем chat_state с текущим состоянием чатов.

    ВАЖНО:
    - если записи для чата нет -> создаём
    - если запись есть, но last_message_id отстаёт -> обновляем
    - если запись актуальна -> не трогаем

    Это защищает от повторной обработки старых системных сообщений
    после рестарта/deploy на Railway.
    """
    logging.info("Инициализация chat_state для существующих чатов...")

    inserted_count = 0
    updated_count = 0
    skipped_count = 0

    for chat in acc.chats.values():
        chat_id = str(chat.id)
        last_message = getattr(chat, "last_message", None)

        if not last_message or getattr(last_message, "id", None) is None:
            skipped_count += 1
            continue

        current_msg_id = str(last_message.id)
        stored_msg_id = get_last_message_id(chat_id)

        if stored_msg_id is None:
            set_last_message_id(chat_id, current_msg_id)
            inserted_count += 1
            continue

        if _is_newer_message_id(current_msg_id, stored_msg_id):
            set_last_message_id(chat_id, current_msg_id)
            updated_count += 1
            continue

        skipped_count += 1

    logging.info(
        "Инициализация chat_state завершена: inserted=%s, updated=%s, skipped=%s",
        inserted_count,
        updated_count,
        skipped_count,
    )


def main():
    init_db()

    acc = Account(GOLDEN_KEY, user_agent=USER_AGENT).get()

    try:
        chats = acc.request_chats()
        acc.add_chats(chats)
        print(f"Чаты загружены: {len(chats)}")

        # Синхронизируем chat_state с текущими последними сообщениями,
        # чтобы не обрабатывать старый хвост истории после рестарта.
        bootstrap_chat_state(acc)

    except Exception as e:
        print("Не удалось загрузить чаты:", e)

    runner = Runner(acc)
    bot = AutoReplyBot(acc)

    stop_event = threading.Event()
    tick_thread = threading.Thread(
        target=start_tick_loop,
        args=(bot, stop_event, 5),
        daemon=True
    )
    tick_thread.start()

    print("Бот запущен. Ctrl+C чтобы остановить")

    try:
        for event in runner.listen(requests_delay=REQUESTS_DELAY):
            if isinstance(event, NewMessageEvent):
                try:
                    bot.handle_new_message(event)
                except Exception:
                    logging.exception("Ошибка при обработке нового сообщения")

    except KeyboardInterrupt:
        print("Остановлено")

    finally:
        stop_event.set()
        tick_thread.join(timeout=1)


if __name__ == "__main__":
    main()