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


def bootstrap_chat_state(acc) -> None:
    """
    При первом запуске/после деплоя помечаем текущее последнее сообщение
    в каждом уже существующем чате как "уже виденное", чтобы бот не начал
    обрабатывать старые системные сообщения после рестарта.
    """
    logging.info("Инициализация chat_state для существующих чатов...")

    initialized_count = 0
    skipped_count = 0

    for chat in acc.chats.values():
        chat_id = str(chat.id)

        # Если состояние чата уже есть в БД — ничего не трогаем.
        if get_last_message_id(chat_id) is not None:
            skipped_count += 1
            continue

        last_message = getattr(chat, "last_message", None)
        if last_message and getattr(last_message, "id", None) is not None:
            set_last_message_id(chat_id, str(last_message.id))
            initialized_count += 1

    logging.info(
        "Инициализация chat_state завершена: initialized=%s, skipped=%s",
        initialized_count,
        skipped_count,
    )


def main():
    init_db()

    acc = Account(GOLDEN_KEY, user_agent=USER_AGENT).get()

    try:
        chats = acc.request_chats()
        acc.add_chats(chats)
        print(f"Чаты загружены: {len(chats)}")

        # ВАЖНО:
        # при первом запуске на новом окружении сохраняем текущие последние
        # сообщения в чатах, чтобы не обрабатывать старый хвост истории.
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