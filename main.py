# main.py

import logging
import threading

from FunPayAPI import Account
from FunPayAPI.updater.runner import Runner
from FunPayAPI.updater.events import (
    NewMessageEvent,
    NewOrderEvent,
    OrderStatusChangedEvent,
)
from FunPayAPI.common.enums import OrderStatuses

from settings import GOLDEN_KEY, USER_AGENT, REQUESTS_DELAY
from handlers import AutoReplyBot
from storage import init_db
from order_handler import handle_paid_order_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

LOGGER = logging.getLogger(__name__)


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
            try:
                if isinstance(event, NewMessageEvent):
                    bot.handle_new_message(event)
                    continue

                if isinstance(event, NewOrderEvent):
                    LOGGER.info("Обнаружен новый заказ #%s status=%s", event.order.id, event.order.status)
                    if event.order.status == OrderStatuses.PAID:
                        handle_paid_order_event(acc, bot.rm, event.order)
                    continue

                if isinstance(event, OrderStatusChangedEvent):
                    LOGGER.info("Изменился статус заказа #%s status=%s", event.order.id, event.order.status)
                    if event.order.status == OrderStatuses.PAID:
                        handle_paid_order_event(acc, bot.rm, event.order)
                    continue

            except Exception:
                logging.exception("Ошибка при обработке события Runner")

    except KeyboardInterrupt:
        print("Остановлено")

    finally:
        stop_event.set()
        tick_thread.join(timeout=1)


if __name__ == "__main__":
    main()