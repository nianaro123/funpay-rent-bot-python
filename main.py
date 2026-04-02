# main.py

import logging
import threading

from FunPayAPI import Account
from FunPayAPI.updater.runner import Runner
from FunPayAPI.updater.events import NewMessageEvent

from settings import GOLDEN_KEY, USER_AGENT, REQUESTS_DELAY
from handlers import AutoReplyBot
from storage import init_db

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


def main():
    init_db()

    acc = Account(GOLDEN_KEY, user_agent=USER_AGENT).get()

    try:
        chats = acc.request_chats()
        acc.add_chats(chats)
        print(f"Чаты загружены: {len(chats)}")
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
