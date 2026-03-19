# main.py

import logging

from FunPayAPI import Account
from FunPayAPI.updater.runner import Runner
from FunPayAPI.updater.events import NewMessageEvent

from config import GOLDEN_KEY, USER_AGENT, REQUESTS_DELAY
from handlers import AutoReplyBot
from storage import init_db


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


def main():
    # инициализация базы
    init_db()

    acc = Account(GOLDEN_KEY, user_agent=USER_AGENT).get()

    # загрузка чатов
    try:
        chats = acc.request_chats()
        acc.add_chats(chats)
        print(f"Чаты загружены: {len(chats)}")
    except Exception as e:
        print("Не удалось загрузить чаты:", e)

    runner = Runner(acc)
    bot = AutoReplyBot(acc)

    print("Бот запущен. Ctrl+C чтобы остановить")

    try:
        for event in runner.listen(requests_delay=REQUESTS_DELAY):

            if isinstance(event, NewMessageEvent):
                bot.handle_new_message(event)

            # периодические задачи
            bot.tick()

    except KeyboardInterrupt:
        print("Остановлено")


if __name__ == "__main__":
    main()